from __future__ import annotations

import hashlib
import io
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from PIL import Image

import factory.pet_sitcom_audio_probe as audio_probe
import factory.pet_sitcom_generation as generation
from factory.gateway_video import (
    GatewayVideoClient,
    GatewayVideoConfig,
    GatewayVideoResult,
    GatewayVideoTask,
)
from factory.pet_sitcom import VIDEO_MODEL, build_pet_sitcom_plan
from factory.pet_sitcom_generation import (
    PET_CONTINUITY_SCHEMA,
    PET_SELECTION_SCHEMA,
    PET_SHOT_GENERATION_SCHEMA,
    PetSitcomGenerationError,
    extract_pet_continuity_frame,
    generate_pet_sitcom_shots,
    sanitize_pet_sitcom_report,
    select_pet_shot_candidate,
)
from tests.media_fixtures import VALID_VIDEO_MP4


REAL_REQUIRE_APPROVED_PET_AUDIO_PROBE = (
    audio_probe.require_approved_pet_audio_probe
)


CAT_SHOT_IDS = {
    "shot_03",
    "shot_04",
    "shot_05",
    "shot_08",
    "shot_09",
    "shot_10",
}


@dataclass
class FakeImageClient:
    api_key: str = "image-secret"
    model: str = "doubao-seedream-4-5"

    def __post_init__(self) -> None:
        self.config = SimpleNamespace(
            api_key=self.api_key,
            model=self.model,
        )
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, output_path: Path, **kwargs: object):
        self.calls.append(
            {"prompt": prompt, "output_path": output_path, **kwargs}
        )
        Image.new("RGB", (8, 8), (230, 180, 120)).save(
            output_path, format="JPEG"
        )
        return SimpleNamespace(output_path=str(output_path))


class FakeGatewayResponse:
    def __init__(self, data, *, status=200, headers=None):
        self.body = (
            data
            if isinstance(data, bytes)
            else json.dumps(data).encode("utf-8")
        )
        self.stream = io.BytesIO(self.body)
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self.stream.read(-1 if size is None else size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class LockInterleavingVideoClient:
    def __init__(self) -> None:
        self.config = GatewayVideoConfig(
            api_key="transport-secret",
            base_url="https://gateway.test/v1",
            model=VIDEO_MODEL,
        )
        self.submit_count = 0
        self.complete_count = 0
        self.completed_task_ids: list[str] = []
        self.validated_images: list[tuple[str, ...]] = []
        self.validated_audio: list[Path] = []

    def validate_reference_images(self, images) -> None:
        self.validated_images.append(tuple(str(image) for image in images))

    def validate_reference_audio(self, audio) -> None:
        self.validated_audio.append(Path(audio))

    def prepare_submission(self, prompt, **kwargs):
        return prompt, kwargs

    def submit_prepared(self, submission, *, allow_network=False):
        self.submit_count += 1
        return GatewayVideoTask(
            task_id=f"task-new-{self.submit_count}",
            status="queued",
        )

    def complete_task(self, task, output_path, **kwargs):
        self.complete_count += 1
        self.completed_task_ids.append(task.task_id)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return GatewayVideoResult(
            output_path=str(output),
            model=self.config.model,
            task_id=task.task_id,
            status="completed",
            poll_count=1,
            output_size_bytes=len(VALID_VIDEO_MP4),
            duration_seconds=1.0,
            source_host="cdn.test",
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _gateway_recovery_inputs(
    plan,
    video_client,
    *,
    shot_id: str = "shot_01",
    candidate_number: int = 1,
    retry_reason: str = "",
    selections: dict[str, dict[str, object]] | None = None,
    drive_audio: Path | None = None,
    source_tts_sha256: str = "",
):
    shot = _shot(plan, shot_id)
    current_selections = {} if selections is None else selections
    candidate = (
        shot.candidate_dir / f"candidate_{candidate_number:03d}.mp4"
    )
    references = generation._pet_shot_references(
        plan,
        shot,
        current_selections,
    )
    prompt = generation._pet_shot_prompt(
        shot,
        candidate_number,
        retry_reason,
    )
    job = generation._gateway_video_batch.GatewayVideoJob(
        shot_id="single",
        index=1,
        prompt=prompt,
        images=tuple(str(path) for path in references),
        duration=shot.generation_duration_seconds,
        ratio="9:16",
        resolution="1080p",
        output_path=str(candidate),
    )
    endpoint_fingerprint = (
        generation._gateway_video_batch.gateway_endpoint_fingerprint(
            video_client.config.base_url
        )
    )
    signature = generation._gateway_video_batch._job_signature(
        job,
        model=str(video_client.config.model),
        generate_audio=drive_audio is not None,
        endpoint_fingerprint=endpoint_fingerprint,
        reference_audio=(
            generation._gateway_video_batch._reference_audio_evidence(
                drive_audio
            )
        ),
    )
    provenance = generation._pet_candidate_provenance(
        shot,
        candidate_number,
        prompt,
        retry_reason,
        references,
        current_selections,
        drive_audio,
        source_tts_sha256,
    )
    return (
        shot,
        candidate,
        references,
        prompt,
        signature,
        endpoint_fingerprint,
        provenance,
    )


def _write_gateway_recovery_state(
    candidate: Path,
    signature: str,
    *,
    endpoint_fingerprint: str,
    status: str,
    task_id: object = "task-safe_01",
    shot_id: str = "single",
    output_path: str | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": generation._gateway_video_batch.CLIP_STATE_SCHEMA,
        "signature": signature,
        "endpoint_fingerprint_sha256": endpoint_fingerprint,
        "status": status,
        "model": VIDEO_MODEL,
        "shot_id": shot_id,
        "output_path": output_path or str(candidate.resolve()),
    }
    if status in {"submitted", "completed"}:
        payload["task_id"] = task_id
    if status == "submitted":
        payload["task_status"] = "queued"
    if status == "completed":
        payload["output_size_bytes"] = (
            candidate.stat().st_size if candidate.is_file() else 0
        )
    path = generation._gateway_video_batch._clip_state_path(candidate)
    _write_json(path, payload)
    return path


def _write_png(path: Path, color: tuple[int, int, int] = (90, 100, 110)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, format="PNG")


def _write_valid_av_mp4(path: Path, *, duration_seconds: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=16x16:rate=24",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            str(duration_seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _shot(plan, shot_id: str):
    return next(item for item in plan.shots if item.shot_id == shot_id)


def _continuity_path(plan, shot_id: str) -> Path:
    return plan.output_dir / "continuity" / f"{shot_id}_last.png"


def _write_continuity_state(
    plan,
    shot_id: str,
    source: Path,
    *,
    timestamp: float | None = None,
) -> Path:
    shot = _shot(plan, shot_id)
    frame = _continuity_path(plan, shot_id)
    _write_png(frame, (shot.index * 10, 80, 120))
    source_duration = float(shot.generation_duration_seconds)
    endpoint = (
        min(shot.duration_seconds - 0.08, source_duration - 0.08)
        if timestamp is None
        else timestamp
    )
    _write_json(
        frame.with_suffix(".png.state.json"),
        {
            "schema_version": PET_CONTINUITY_SCHEMA,
            "source_video_path": str(source.resolve()),
            "source_video_sha256": _sha256(source),
            "source_video_duration_seconds": source_duration,
            "edit_duration_seconds": shot.duration_seconds,
            "timestamp_seconds": endpoint,
            "extracted_at": "2026-07-27T00:00:00+00:00",
            "frame_sha256": _sha256(frame),
        },
    )
    return frame


def _seed_selected(plan, *shot_ids: str) -> dict[str, dict[str, object]]:
    document = (
        json.loads(plan.selection_path.read_text(encoding="utf-8"))
        if plan.selection_path.is_file()
        else {
            "schema_version": PET_SELECTION_SCHEMA,
            "shots": {},
            "history": {},
        }
    )
    selections = document.setdefault("shots", {})
    for shot_id in shot_ids:
        shot = _shot(plan, shot_id)
        video = shot.candidate_dir / "candidate_001.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(VALID_VIDEO_MP4)
        frame = _write_continuity_state(plan, shot_id, video)
        state = json.loads(
            frame.with_suffix(".png.state.json").read_text(encoding="utf-8")
        )
        selections[shot_id] = {
            "candidate_number": 1,
            "status": "selected",
            "video_path": str(video.resolve()),
            "video_sha256": _sha256(video),
            "continuity_frame_path": str(frame.resolve()),
            "continuity_sidecar_path": str(
                frame.with_suffix(".png.state.json").resolve()
            ),
            "continuity_frame_sha256": _sha256(frame),
            "continuity_timestamp_seconds": state["timestamp_seconds"],
            "selected_at": "2026-07-27T00:00:00+00:00",
        }
    _write_json(plan.selection_path, document)
    return selections


def _approve_anchors(plan) -> None:
    review_path = plan.output_dir / "anchor_review_template.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review.update(
        {
            "naitang_consistent_across_panels": True,
            "doubao_consistent_across_panels": True,
            "cats_remain_clearly_distinct": True,
            "scenes_are_empty_and_clean": True,
            "scenes_share_home_design": True,
            "completed": True,
        }
    )
    _write_json(review_path, review)
    generation.approve_pet_anchors(plan)


def _install_real_approved_audio_probe(
    plan,
    prepared_audio,
    monkeypatch,
) -> list[object]:
    source_tts = prepared_audio.tts["shot_04"]
    drive_audio = prepared_audio.drive["shot_04"]

    def load_assets(current_plan):
        assert current_plan.output_dir == plan.output_dir
        return (
            SimpleNamespace(
                shot_id="shot_04",
                speaker="doubao",
                output_path=source_tts,
                output_sha256=_sha256(source_tts),
            ),
        )

    def build_drive(current_plan, shot_id, **kwargs):
        assert current_plan.output_dir == plan.output_dir
        assert shot_id == "shot_04"
        return drive_audio

    monkeypatch.setattr(audio_probe, "load_pet_speech_assets", load_assets)
    monkeypatch.setattr(audio_probe, "build_pet_drive_audio", build_drive)
    _write_json(
        plan.audio_manifest_path,
        {
            "schema_version": "test.pet-audio-manifest.v1",
            "source_shot_id": "shot_04",
        },
    )

    probe_video = audio_probe._probe_video_path(plan)
    probe_video.parent.mkdir(parents=True, exist_ok=True)
    probe_video.write_bytes(VALID_VIDEO_MP4)
    gateway_report = audio_probe._probe_gateway_report_path(plan)
    _write_json(
        gateway_report,
        {"success": True, "task_id": "probe-task-completed"},
    )
    frame_evidence = []
    for index, timestamp in enumerate(
        audio_probe.PROBE_FRAME_TIMESTAMPS,
        start=1,
    ):
        frame = audio_probe._probe_frame_dir(plan) / f"frame_{index:02d}.png"
        _write_png(frame, (index, 20, 30))
        frame_evidence.append(
            {
                "timestamp_seconds": timestamp,
                "path": str(frame.resolve()),
                "sha256": _sha256(frame),
            }
        )

    bindings = audio_probe._current_source_bindings(plan)
    report = {
        "schema_version": audio_probe.PROBE_SCHEMA,
        "capability": "supported",
        "success": True,
        "executed": True,
        "source_shot_id": audio_probe.PROBE_SOURCE_SHOT_ID,
        **bindings,
        "gateway_report_path": str(gateway_report.resolve()),
        "gateway_report_sha256": _sha256(gateway_report),
        "probe_mp4_path": str(probe_video.resolve()),
        "probe_mp4_sha256": _sha256(probe_video),
        "frame_evidence": frame_evidence,
    }
    _write_json(plan.audio_probe_path, report)
    review = {
        "schema_version": audio_probe.PROBE_REVIEW_SCHEMA,
        **audio_probe._review_bindings(plan, report),
        "completed": True,
        "approved": True,
        "audio_onset_seconds": 0.65,
        "mouth_onset_seconds": 0.80,
        "audio_offset_seconds": 1.65,
        "mouth_offset_seconds": 1.45,
        **{gate: True for gate in audio_probe.PROBE_REVIEW_GATES},
        "notes": "Approved fixture bound to the current probe artifacts.",
    }
    _write_json(plan.audio_probe_review_path, review)

    calls: list[object] = []

    def require_approved(current_plan):
        result = REAL_REQUIRE_APPROVED_PET_AUDIO_PROBE(current_plan)
        calls.append(current_plan)
        return result

    monkeypatch.setattr(
        audio_probe,
        "require_approved_pet_audio_probe",
        require_approved,
    )
    return calls


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, tmp_path / "pet-sitcom")


@pytest.fixture(autouse=True)
def approved_probe(monkeypatch):
    monkeypatch.setattr(
        audio_probe,
        "require_approved_pet_audio_probe",
        lambda plan: {"approved": True},
    )


@pytest.fixture
def prepared_references(plan):
    paths = [
        *(item.reference_path for item in plan.characters),
        *(item.anchor_path for item in plan.scenes),
    ]
    for index, path in enumerate(paths, start=1):
        _write_png(path, (20 * index, 40, 60))
    return paths


@pytest.fixture
def prepared_audio(plan, monkeypatch):
    tts_paths: dict[str, Path] = {}
    drive_paths: dict[str, Path] = {}
    for shot_id in CAT_SHOT_IDS:
        tts = plan.output_dir / "audio" / "tts" / f"{shot_id}.wav"
        drive = plan.output_dir / "audio" / "drive" / f"{shot_id}_drive.wav"
        tts.parent.mkdir(parents=True, exist_ok=True)
        drive.parent.mkdir(parents=True, exist_ok=True)
        tts.write_bytes(f"tts-{shot_id}".encode())
        drive.write_bytes(f"drive-{shot_id}".encode())
        tts_paths[shot_id] = tts
        drive_paths[shot_id] = drive

    def build_drive(current_plan, shot_id, **kwargs):
        assert current_plan.output_dir == plan.output_dir
        return drive_paths[shot_id]

    def load_assets(current_plan):
        assert current_plan.output_dir == plan.output_dir
        return tuple(
            SimpleNamespace(
                shot_id=shot_id,
                output_path=tts_paths[shot_id],
                output_sha256=_sha256(tts_paths[shot_id]),
            )
            for shot_id in sorted(CAT_SHOT_IDS)
        )

    monkeypatch.setattr(
        generation, "build_pet_drive_audio", build_drive, raising=False
    )
    monkeypatch.setattr(
        generation, "load_pet_speech_assets", load_assets, raising=False
    )
    return SimpleNamespace(tts=tts_paths, drive=drive_paths)


@pytest.fixture
def fake_video_client():
    return SimpleNamespace(
        config=SimpleNamespace(
            api_key="gateway-secret",
            base_url="https://gateway.example/v1",
            model=VIDEO_MODEL,
        ),
        calls=[],
    )


@pytest.fixture
def fake_image_client():
    return FakeImageClient()


@pytest.fixture
def provider_fakes(plan, fake_video_client, monkeypatch):
    durations = {
        shot.shot_id: float(shot.generation_duration_seconds)
        for shot in plan.shots
    }
    audio_stream_counts = {
        shot.shot_id: (
            1 if shot.speaker in {"naitang", "doubao"} else 0
        )
        for shot in plan.shots
    }

    def render(prompt, output_path, video_client, report_path, **kwargs):
        video_client.calls.append(
            {
                "prompt": prompt,
                "output_path": Path(output_path),
                "report_path": Path(report_path),
                **kwargs,
            }
        )
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        _write_json(
            Path(report_path),
            {
                "schema_version": "motion-comic-factory.gateway-video.v2",
                "success": True,
                "task_id": "safe-task-id",
            },
        )
        return {"success": True, "executed": True, "task_id": "safe-task-id"}

    def probe(path, *, required_stream):
        shot_id = Path(path).parent.name
        return SimpleNamespace(
            valid=True,
            duration_seconds=durations.get(shot_id, 5.0),
            video_stream_count=1,
            audio_stream_count=audio_stream_counts.get(shot_id, 0),
        )

    def extract(
        source_video,
        output_path,
        *,
        edit_duration_seconds=None,
        command_runner=subprocess.run,
        output_dir=None,
    ):
        source = Path(source_video)
        output = Path(output_path)
        shot_id = source.parent.name
        shot = _shot(plan, shot_id)
        source_duration = durations[shot_id]
        edit_duration = (
            shot.duration_seconds
            if edit_duration_seconds is None
            else float(edit_duration_seconds)
        )
        _write_png(output, (shot.index * 10, 100, 130))
        _write_json(
            output.with_suffix(".png.state.json"),
            {
                "schema_version": PET_CONTINUITY_SCHEMA,
                "source_video_path": str(source.resolve()),
                "source_video_sha256": _sha256(source),
                "source_video_duration_seconds": source_duration,
                "edit_duration_seconds": edit_duration,
                "timestamp_seconds": min(
                    edit_duration - 0.08, source_duration - 0.08
                ),
                "extracted_at": "2026-07-27T00:00:00+00:00",
                "frame_sha256": _sha256(output),
            },
        )
        return output

    monkeypatch.setattr(generation, "render_gateway_video_single", render)
    monkeypatch.setattr(generation, "probe_media", probe)
    monkeypatch.setattr(generation, "extract_pet_continuity_frame", extract)
    return fake_video_client


def test_audio_probe_gate_is_first_and_blocks_every_side_effect(
    plan, fake_video_client, monkeypatch
):
    events: list[str] = []

    def reject_probe(current_plan):
        events.append("probe")
        raise PetSitcomGenerationError("approved audio-drive probe required")

    monkeypatch.setattr(
        audio_probe, "require_approved_pet_audio_probe", reject_probe
    )
    monkeypatch.setattr(
        generation,
        "render_gateway_video_single",
        lambda *args, **kwargs: events.append("provider"),
    )

    with pytest.raises(PetSitcomGenerationError, match="audio-drive probe"):
        generate_pet_sitcom_shots(
            plan,
            video_client=fake_video_client,
            allow_network=True,
            shot_id="shot_01",
        )

    assert events == ["probe"]
    assert fake_video_client.calls == []
    assert not plan.generation_report_path.exists()


def test_cat_shot_uses_exact_drive_audio_and_generation_duration(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    _seed_selected(plan, "shot_02")

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is True
    call = provider_fakes.calls[0]
    assert call["audio"] == prepared_audio.drive["shot_03"]
    assert call["duration"] == 7
    assert call["resolution"] == "1080p"
    assert call["generate_audio"] is True


@pytest.mark.parametrize(
    ("shot_id", "dependencies", "duration"),
    [
        ("shot_01", (), 6),
        ("shot_06", ("shot_05",), 7),
        ("shot_07", ("shot_05", "shot_06"), 5),
    ],
)
def test_owner_and_silent_shots_do_not_send_reference_audio(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
    shot_id,
    dependencies,
    duration,
):
    _seed_selected(plan, *dependencies)
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=float(duration),
            video_stream_count=1,
            audio_stream_count=0,
        ),
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id=shot_id,
    )

    assert report["success"] is True
    call = provider_fakes.calls[0]
    assert call["audio"] is None
    assert call["duration"] == duration
    assert call["generate_audio"] is False


@pytest.mark.parametrize(
    ("shot_id", "dependencies"),
    [
        ("shot_01", ()),
        ("shot_07", ("shot_05", "shot_06")),
    ],
)
def test_owner_and_silent_zero_audio_candidates_are_selected(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
    shot_id,
    dependencies,
):
    _seed_selected(plan, *dependencies)
    duration = float(_shot(plan, shot_id).generation_duration_seconds)
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=0,
        ),
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id=shot_id,
    )

    assert report["success"] is True
    selection = json.loads(
        plan.selection_path.read_text(encoding="utf-8")
    )["shots"][shot_id]
    assert selection["status"] == "selected"


@pytest.mark.parametrize(
    ("shot_id", "dependencies"),
    [
        ("shot_01", ()),
        ("shot_07", ("shot_05", "shot_06")),
    ],
)
def test_owner_and_silent_candidates_reject_unexpected_audio_track(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
    shot_id,
    dependencies,
):
    _seed_selected(plan, *dependencies)
    duration = float(_shot(plan, shot_id).generation_duration_seconds)
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=1,
        ),
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id=shot_id,
    )

    assert report["success"] is False
    assert "provenance" in report["errors"][0]["error"]
    selections = (
        json.loads(plan.selection_path.read_text(encoding="utf-8"))["shots"]
        if plan.selection_path.is_file()
        else {}
    )
    assert shot_id not in selections


def test_shot_07_uses_only_declared_replay_frame(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    _seed_selected(plan, "shot_05", "shot_06")

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_07",
    )

    assert report["success"] is True
    call = provider_fakes.calls[0]
    assert [path.name for path in call["images"][-1:]] == [
        "shot_06_last.png",
    ]
    assert "shot_05_last.png" not in [path.name for path in call["images"]]
    assert "Reference 4 is the exact low security-camera composition from shot_06." in call[
        "prompt"
    ]
    assert "Continue Reference 4 without changing its camera axis" in call["prompt"]
    assert "do not return to the two-cat composition" in call["prompt"]


def test_declared_dependency_must_be_selected_before_provider_submission(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is False
    assert "shot_02" in report["errors"][0]["error"]
    assert provider_fakes.calls == []


def test_stale_dependency_is_rejected_before_provider_submission(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    selections = _seed_selected(plan, "shot_02")
    selections["shot_02"]["status"] = "stale_upstream"
    document = json.loads(plan.selection_path.read_text(encoding="utf-8"))
    document["shots"] = selections
    _write_json(plan.selection_path, document)

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is False
    assert "shot_02" in report["errors"][0]["error"]
    assert provider_fakes.calls == []


def test_dependency_video_hash_change_is_rejected_before_provider_submission(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    selections = _seed_selected(plan, "shot_02")
    Path(selections["shot_02"]["video_path"]).write_bytes(
        VALID_VIDEO_MP4 + b"changed"
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is False
    assert "hash" in report["errors"][0]["error"]
    assert provider_fakes.calls == []


def test_candidate_provenance_binds_dependencies_tts_and_drive_audio(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    selections = _seed_selected(plan, "shot_02")

    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    state_path = (
        _shot(plan, "shot_03").candidate_dir / "candidate_001.provenance.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == PET_SHOT_GENERATION_SCHEMA
    assert state["generation_duration_seconds"] == 7
    assert state["dependency_video_sha256"] == {
        "shot_02": selections["shot_02"]["video_sha256"]
    }
    assert state["source_tts_sha256"] == _sha256(
        prepared_audio.tts["shot_03"]
    )
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_03"]
    )
    assert state["reference_audio_path"] == str(
        prepared_audio.drive["shot_03"].resolve()
    )
    assert state["generate_audio"] is True


def test_owner_provenance_records_no_reference_audio(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    state = json.loads(
        (
            _shot(plan, "shot_01").candidate_dir
            / "candidate_001.provenance.json"
        ).read_text(encoding="utf-8")
    )
    assert state["source_tts_sha256"] == ""
    assert state["reference_audio_path"] == ""
    assert state["reference_audio_sha256"] == ""
    assert state["generate_audio"] is False


def test_current_candidate_is_reused_without_provider_call(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    first = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )
    provider_fakes.calls.clear()

    second = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    assert first["success"] is True
    assert second["success"] is True, json.dumps(
        second, ensure_ascii=False, indent=2
    )
    assert second["reused_count"] == 1
    assert second["shots"][0]["status"] == "reused"
    assert provider_fakes.calls == []


def test_task5_replace_stale_resumes_current_submitted_state_seen_at_lock(
    plan,
    prepared_references,
    monkeypatch,
):
    video_client = LockInterleavingVideoClient()
    (
        _shot_value,
        candidate,
        _references,
        _prompt,
        signature,
        endpoint_fingerprint,
        _provenance,
    ) = _gateway_recovery_inputs(
        plan,
        video_client,
        candidate_number=2,
        retry_reason="identity",
    )
    state_path = _write_gateway_recovery_state(
        candidate,
        signature,
        endpoint_fingerprint=endpoint_fingerprint,
        status="submitted",
        task_id="task-current",
    )
    current_state = json.loads(state_path.read_text(encoding="utf-8"))
    _write_json(state_path, {"status": "rejected"})
    original_acquire = generation._gateway_video_batch._acquire_clip_lock
    injected = False

    def acquire_and_inject_state(current_output):
        nonlocal injected
        descriptor = original_acquire(current_output)
        if not injected:
            injected = True
            generation._gateway_video_batch.write_atomic_json(
                state_path,
                current_state,
            )
        return descriptor

    monkeypatch.setattr(
        generation._gateway_video_batch,
        "_acquire_clip_lock",
        acquire_and_inject_state,
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=video_client,
        allow_network=True,
        shot_id="shot_01",
        candidate_number=2,
        retry_reason="identity",
    )

    assert report["success"] is True
    assert video_client.submit_count == 0
    assert video_client.completed_task_ids == ["task-current"]


def test_task5_replace_stale_skips_current_completed_state_seen_at_lock(
    plan,
    prepared_references,
    monkeypatch,
):
    video_client = LockInterleavingVideoClient()
    (
        _shot_value,
        candidate,
        _references,
        _prompt,
        signature,
        endpoint_fingerprint,
        _provenance,
    ) = _gateway_recovery_inputs(
        plan,
        video_client,
        candidate_number=2,
        retry_reason="identity",
    )
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(VALID_VIDEO_MP4)
    state_path = _write_gateway_recovery_state(
        candidate,
        signature,
        endpoint_fingerprint=endpoint_fingerprint,
        status="completed",
        task_id="task-current",
    )
    current_state = json.loads(state_path.read_text(encoding="utf-8"))
    current_video_hash = _sha256(candidate)
    candidate.unlink()
    _write_json(state_path, {"status": "rejected"})
    original_acquire = generation._gateway_video_batch._acquire_clip_lock
    injected = False

    def acquire_and_inject_state(current_output):
        nonlocal injected
        descriptor = original_acquire(current_output)
        if not injected:
            injected = True
            candidate.write_bytes(VALID_VIDEO_MP4)
            generation._gateway_video_batch.write_atomic_json(
                state_path,
                current_state,
            )
        return descriptor

    monkeypatch.setattr(
        generation._gateway_video_batch,
        "_acquire_clip_lock",
        acquire_and_inject_state,
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=video_client,
        allow_network=True,
        shot_id="shot_01",
        candidate_number=2,
        retry_reason="identity",
    )

    assert report["success"] is True
    assert video_client.submit_count == 0
    assert video_client.complete_count == 0
    assert _sha256(candidate) == current_video_hash


def test_completed_skip_repairs_stale_task5_provenance_on_real_generation_chain(
    plan,
    prepared_references,
    prepared_audio,
    monkeypatch,
):
    approved_probe_calls = _install_real_approved_audio_probe(
        plan,
        prepared_audio,
        monkeypatch,
    )
    task5_drive_calls: list[str] = []
    task5_asset_load_calls: list[object] = []
    build_drive_audio = generation.build_pet_drive_audio
    load_speech_assets = generation.load_pet_speech_assets

    def observed_build_drive(current_plan, shot_id, **kwargs):
        task5_drive_calls.append(shot_id)
        return build_drive_audio(current_plan, shot_id, **kwargs)

    def observed_load_assets(current_plan):
        task5_asset_load_calls.append(current_plan)
        return load_speech_assets(current_plan)

    monkeypatch.setattr(
        generation,
        "build_pet_drive_audio",
        observed_build_drive,
    )
    monkeypatch.setattr(
        generation,
        "load_pet_speech_assets",
        observed_load_assets,
    )

    selections = _seed_selected(plan, "shot_02")
    video_client = LockInterleavingVideoClient()
    drive_audio = prepared_audio.drive["shot_03"]
    source_tts = prepared_audio.tts["shot_03"]
    (
        shot,
        candidate,
        references,
        prompt,
        signature,
        endpoint_fingerprint,
        current_provenance,
    ) = _gateway_recovery_inputs(
        plan,
        video_client,
        shot_id="shot_03",
        candidate_number=2,
        retry_reason="identity",
        selections=selections,
        drive_audio=drive_audio,
        source_tts_sha256=_sha256(source_tts),
    )
    expected_anchor_references = [
        plan.characters[0].reference_path,
        plan.characters[1].reference_path,
        next(scene.anchor_path for scene in plan.scenes if scene.slug == "kitchen"),
    ]
    assert (
        generation.render_gateway_video_single
        is generation._gateway_video_batch.render_gateway_video_single
    )
    assert references[:3] == expected_anchor_references
    assert all(
        reference in prepared_references
        for reference in expected_anchor_references
    )
    _write_valid_av_mp4(
        candidate,
        duration_seconds=shot.generation_duration_seconds,
    )
    candidate_sha256 = _sha256(candidate)
    assert generation.is_valid_mp4_file(candidate)
    _write_gateway_recovery_state(
        candidate,
        signature,
        endpoint_fingerprint=endpoint_fingerprint,
        status="completed",
        task_id="task-current",
    )

    forged_sha256 = "f" * 64
    stale_provenance = {
        **current_provenance,
        "prompt_sha256": forged_sha256,
        "reference_sha256": [forged_sha256] * len(references),
        "dependency_video_sha256": {"shot_02": forged_sha256},
        "source_tts_sha256": forged_sha256,
        "reference_audio_sha256": forged_sha256,
        "video_sha256": candidate_sha256,
        "provider_success": True,
    }
    provenance_path = candidate.with_suffix(".provenance.json")
    gateway_report_path = candidate.with_suffix(".report.json")
    _write_json(provenance_path, stale_provenance)
    _write_json(
        gateway_report_path,
        {
            "schema_version": "motion-comic-factory.gateway-video.v2",
            "success": True,
            "pet_sitcom_provenance": stale_provenance,
        },
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=video_client,
        allow_network=True,
        shot_id="shot_03",
        candidate_number=2,
        retry_reason="identity",
    )

    assert report["success"] is True, json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    )
    assert report["shots"][0]["status"] == "generated"
    assert approved_probe_calls == [plan]
    assert task5_drive_calls == ["shot_03"]
    assert task5_asset_load_calls == [plan]
    assert video_client.submit_count == 0
    assert video_client.complete_count == 0
    assert video_client.validated_images == [
        tuple(str(reference) for reference in references)
    ]
    assert video_client.validated_audio == [drive_audio]
    assert _sha256(candidate) == candidate_sha256

    repaired_provenance = json.loads(
        provenance_path.read_text(encoding="utf-8")
    )
    gateway_report = json.loads(
        gateway_report_path.read_text(encoding="utf-8")
    )
    expected_provenance = {
        "schema_version": PET_SHOT_GENERATION_SCHEMA,
        "shot_id": "shot_03",
        "candidate_number": 2,
        "provider": "gateway",
        "model": VIDEO_MODEL,
        "base_prompt_sha256": _json_sha256(
            {"prompt": shot.base_prompt}
        ),
        "prompt_sha256": _json_sha256({"prompt": prompt}),
        "retry_reason": "identity",
        "retry_suffix": generation.PET_RETRY_SUFFIXES["identity"],
        "reference_paths": [
            str(reference.resolve()) for reference in references
        ],
        "reference_sha256": [
            _sha256(reference) for reference in references
        ],
        "dependency_video_sha256": {
            "shot_02": selections["shot_02"]["video_sha256"]
        },
        "source_tts_sha256": _sha256(source_tts),
        "reference_audio_path": str(drive_audio.resolve()),
        "reference_audio_sha256": _sha256(drive_audio),
        "generation_duration_seconds": 7,
        "generate_audio": True,
        "gateway_report_path": str(gateway_report_path.resolve()),
        "video_sha256": candidate_sha256,
        "provider_success": True,
    }
    assert repaired_provenance == expected_provenance
    assert gateway_report["executed"] is False
    assert gateway_report["skipped_count"] == 1
    assert gateway_report["completed_count"] == 0
    assert gateway_report["results"][0]["status"] == "skipped_existing"
    assert (
        gateway_report["pet_sitcom_provenance"]
        == repaired_provenance
    )
    assert forged_sha256 not in json.dumps(
        repaired_provenance,
        sort_keys=True,
    )
    assert forged_sha256 not in json.dumps(
        gateway_report,
        sort_keys=True,
    )

    selection = select_pet_shot_candidate(plan, "shot_03", 2)
    selected = selection["shots"]["shot_03"]

    assert approved_probe_calls == [plan, plan]
    assert task5_drive_calls == ["shot_03", "shot_03"]
    assert task5_asset_load_calls == [plan, plan]
    assert selected["candidate_number"] == 2
    assert selected["status"] == "selected"
    assert selected["video_path"] == str(candidate.resolve())
    assert selected["video_sha256"] == candidate_sha256
    assert selected["prompt_sha256"] == repaired_provenance["prompt_sha256"]
    assert selected["reference_paths"] == repaired_provenance[
        "reference_paths"
    ]
    assert selected["reference_sha256"] == repaired_provenance[
        "reference_sha256"
    ]
    assert selected["dependency_video_sha256"] == repaired_provenance[
        "dependency_video_sha256"
    ]
    assert selected["source_tts_sha256"] == repaired_provenance[
        "source_tts_sha256"
    ]
    assert selected["reference_audio_sha256"] == repaired_provenance[
        "reference_audio_sha256"
    ]
    assert _sha256(candidate) == candidate_sha256
    assert video_client.submit_count == 0
    assert video_client.complete_count == 0


def test_task5_replace_stale_submits_when_state_is_stale_at_lock(
    plan,
    prepared_references,
    monkeypatch,
):
    video_client = LockInterleavingVideoClient()
    (
        _shot_value,
        candidate,
        _references,
        _prompt,
        _signature,
        _endpoint_fingerprint,
        _provenance,
    ) = _gateway_recovery_inputs(
        plan,
        video_client,
        candidate_number=2,
        retry_reason="identity",
    )
    state_path = generation._gateway_video_batch._clip_state_path(candidate)
    _write_json(state_path, {"status": "rejected"})
    original_acquire = generation._gateway_video_batch._acquire_clip_lock
    lock_time_statuses: list[str] = []

    def acquire_and_observe_state(current_output):
        descriptor = original_acquire(current_output)
        lock_time_statuses.append(
            json.loads(state_path.read_text(encoding="utf-8"))["status"]
        )
        return descriptor

    monkeypatch.setattr(
        generation._gateway_video_batch,
        "_acquire_clip_lock",
        acquire_and_observe_state,
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=video_client,
        allow_network=True,
        shot_id="shot_01",
        candidate_number=2,
        retry_reason="identity",
    )

    gateway_report = json.loads(
        generation._pet_gateway_report_path(candidate).read_text(
            encoding="utf-8"
        )
    )
    assert report["success"] is True
    assert lock_time_statuses == ["rejected"]
    assert video_client.submit_count == 1
    assert video_client.completed_task_ids == ["task-new-1"]
    assert gateway_report["replace_stale"] is True


def test_gateway_state_file_symlink_is_rejected_before_provider(
    plan,
    prepared_references,
    provider_fakes,
    tmp_path,
):
    candidate = _shot(plan, "shot_01").candidate_dir / "candidate_001.mp4"
    state_path = generation._gateway_video_batch._clip_state_path(candidate)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-state.json"
    outside.write_text('{"sentinel": true}\n', encoding="utf-8")
    state_path.symlink_to(outside)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        generate_pet_sitcom_shots(
            plan,
            video_client=provider_fakes,
            allow_network=True,
            shot_id="shot_01",
        )

    assert provider_fakes.calls == []
    assert outside.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_pet_generation_does_not_create_redundant_recovery_companion(
    plan,
    prepared_references,
    provider_fakes,
):
    provider_fakes.config.api_key = "api-key-must-not-persist"
    provider_fakes.config.base_url = (
        "https://url-user:url-password@gateway.example:443/v1/"
        "?token=query-secret#fragment-secret"
    )

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    assert report["success"] is True
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in plan.output_dir.rglob("*.json")
    )
    assert list(plan.output_dir.rglob("*.pet-recovery.json")) == []
    assert all(
        secret not in persisted
        for secret in (
            "api-key-must-not-persist",
            "url-user",
            "url-password",
            "query-secret",
            "fragment-secret",
        )
    )


def test_submitted_gateway_task_resumes_without_duplicate_post(
    plan,
    prepared_references,
    monkeypatch,
):
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=6.0,
            video_stream_count=1,
            audio_stream_count=0,
        ),
    )

    def extract(source_video, output_path, **kwargs):
        source = Path(source_video)
        output = Path(output_path)
        _write_png(output)
        _write_json(
            output.with_suffix(".png.state.json"),
            {
                "schema_version": PET_CONTINUITY_SCHEMA,
                "source_video_path": str(source.resolve()),
                "source_video_sha256": _sha256(source),
                "source_video_duration_seconds": 6.0,
                "edit_duration_seconds": 5.2,
                "timestamp_seconds": 5.12,
                "extracted_at": "2026-07-27T00:00:00+00:00",
                "frame_sha256": _sha256(output),
            },
        )
        return output

    monkeypatch.setattr(generation, "extract_pet_continuity_frame", extract)
    first_requests: list[object] = []

    def interrupted_urlopen(request, timeout):
        first_requests.append(request)
        if request.get_method() == "POST":
            return FakeGatewayResponse(
                {
                    "id": "task-resume",
                    "status": "completed",
                    "video_url": "https://cdn.test/resume.mp4",
                }
            )
        raise URLError("download interrupted")

    interrupted_client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="transport-secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0",
            poll_interval_seconds=0,
            max_wait_seconds=10,
        ),
        urlopen_fn=interrupted_urlopen,
        sleep_fn=lambda _: None,
    )
    first = generate_pet_sitcom_shots(
        plan,
        video_client=interrupted_client,
        allow_network=True,
        shot_id="shot_01",
    )
    gateway_state = (
        _shot(plan, "shot_01").candidate_dir
        / "candidate_001.mp4.gateway.json"
    )
    assert first["success"] is False
    assert json.loads(gateway_state.read_text(encoding="utf-8"))[
        "status"
    ] == "submitted"
    assert sum(
        request.get_method() == "POST" for request in first_requests
    ) == 1

    resumed_requests: list[object] = []

    def resumed_urlopen(request, timeout):
        resumed_requests.append(request)
        if request.get_method() == "POST":
            raise AssertionError(
                "same-signature task must not be submitted twice"
            )
        if "gateway.test" in request.full_url:
            return FakeGatewayResponse(
                {
                    "id": "task-resume",
                    "status": "completed",
                    "video_url": "https://cdn.test/resume.mp4",
                }
            )
        return FakeGatewayResponse(
            VALID_VIDEO_MP4,
            headers={"Content-Length": str(len(VALID_VIDEO_MP4))},
        )

    resumed_client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="transport-secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0",
            poll_interval_seconds=0,
            max_wait_seconds=10,
        ),
        urlopen_fn=resumed_urlopen,
        sleep_fn=lambda _: None,
    )
    second = generate_pet_sitcom_shots(
        plan,
        video_client=resumed_client,
        allow_network=True,
        shot_id="shot_01",
    )

    assert second["success"] is True, json.dumps(
        second, ensure_ascii=False, indent=2
    )
    assert all(
        request.get_method() != "POST" for request in resumed_requests
    )
    assert json.loads(gateway_state.read_text(encoding="utf-8"))[
        "status"
    ] == "completed"


def test_completed_gateway_candidate_recovers_missing_provenance_without_post(
    plan,
    prepared_references,
    monkeypatch,
):
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=6.0,
            video_stream_count=1,
            audio_stream_count=0,
        ),
    )

    def extract(source_video, output_path, **kwargs):
        source = Path(source_video)
        output = Path(output_path)
        _write_png(output)
        _write_json(
            output.with_suffix(".png.state.json"),
            {
                "schema_version": PET_CONTINUITY_SCHEMA,
                "source_video_path": str(source.resolve()),
                "source_video_sha256": _sha256(source),
                "source_video_duration_seconds": 6.0,
                "edit_duration_seconds": 5.2,
                "timestamp_seconds": 5.12,
                "extracted_at": "2026-07-27T00:00:00+00:00",
                "frame_sha256": _sha256(output),
            },
        )
        return output

    monkeypatch.setattr(generation, "extract_pet_continuity_frame", extract)
    first_requests: list[object] = []

    def successful_urlopen(request, timeout):
        first_requests.append(request)
        if request.get_method() == "POST":
            return FakeGatewayResponse(
                {
                    "id": "task-completed",
                    "status": "completed",
                    "video_url": "https://cdn.test/completed.mp4",
                }
            )
        return FakeGatewayResponse(
            VALID_VIDEO_MP4,
            headers={"Content-Length": str(len(VALID_VIDEO_MP4))},
        )

    first_client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="transport-secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0",
            poll_interval_seconds=0,
            max_wait_seconds=10,
        ),
        urlopen_fn=successful_urlopen,
        sleep_fn=lambda _: None,
    )
    first = generate_pet_sitcom_shots(
        plan,
        video_client=first_client,
        allow_network=True,
        shot_id="shot_01",
    )
    assert first["success"] is True
    candidate = _shot(plan, "shot_01").candidate_dir / "candidate_001.mp4"
    state_path = candidate.with_suffix(".provenance.json")
    original_candidate_hash = _sha256(candidate)
    state_path.unlink()

    recovery_requests: list[object] = []

    def recovery_urlopen(request, timeout):
        recovery_requests.append(request)
        if request.get_method() == "POST":
            raise AssertionError(
                "completed matching candidate must not be submitted twice"
            )
        raise AssertionError("completed matching candidate needs no network")

    recovery_client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="transport-secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0",
            poll_interval_seconds=0,
            max_wait_seconds=10,
        ),
        urlopen_fn=recovery_urlopen,
        sleep_fn=lambda _: None,
    )
    second = generate_pet_sitcom_shots(
        plan,
        video_client=recovery_client,
        allow_network=True,
        shot_id="shot_01",
    )

    assert second["success"] is True
    assert recovery_requests == []
    assert _sha256(candidate) == original_candidate_hash
    assert state_path.is_file()


@pytest.mark.parametrize("changed_binding", ["drive", "tts", "reference", "prompt"])
def test_changed_source_binding_rejects_candidate_reuse(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    changed_binding,
):
    _seed_selected(plan, "shot_02")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )
    provider_fakes.calls.clear()
    current_plan = plan

    if changed_binding == "drive":
        prepared_audio.drive["shot_03"].write_bytes(b"changed-drive")
    elif changed_binding == "tts":
        prepared_audio.tts["shot_03"].write_bytes(b"changed-tts")
    elif changed_binding == "reference":
        _write_png(plan.characters[0].reference_path, (255, 0, 0))
    else:
        shots = tuple(
            replace(shot, base_prompt=f"{shot.base_prompt} revised")
            if shot.shot_id == "shot_03"
            else shot
            for shot in plan.shots
        )
        current_plan = replace(plan, shots=shots)

    report = generate_pet_sitcom_shots(
        current_plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is True
    assert report["reused_count"] == 0
    assert len(provider_fakes.calls) == 1
    assert provider_fakes.calls[0]["overwrite"] is False
    assert provider_fakes.calls[0]["replace_stale"] is True


def test_extra_candidate_provenance_field_prevents_reuse(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )
    provider_fakes.calls.clear()
    state_path = (
        _shot(plan, "shot_01").candidate_dir / "candidate_001.provenance.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["untrusted_extra"] = True
    _write_json(state_path, state)

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    assert report["success"] is True
    assert report["reused_count"] == 0
    assert len(provider_fakes.calls) == 1


@pytest.mark.parametrize(
    "tamper",
    ("gateway_embedding", "state_and_gateway_model", "gateway_success"),
)
def test_selection_revalidates_current_gateway_and_provenance_state(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    tamper,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )
    candidate = _shot(plan, "shot_01").candidate_dir / "candidate_001.mp4"
    state_path = candidate.with_suffix(".provenance.json")
    gateway_path = candidate.with_suffix(".report.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
    if tamper == "gateway_embedding":
        gateway["pet_sitcom_provenance"]["prompt_sha256"] = "forged"
    elif tamper == "state_and_gateway_model":
        state["model"] = "forged-model"
        gateway["pet_sitcom_provenance"] = dict(state)
        _write_json(state_path, state)
    else:
        gateway["success"] = False
    _write_json(gateway_path, gateway)

    with pytest.raises(
        PetSitcomGenerationError, match="provenance"
    ):
        select_pet_shot_candidate(plan, "shot_01", 1)


def test_selection_rejects_candidate_replaced_after_generation(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )
    candidate = _shot(plan, "shot_01").candidate_dir / "candidate_001.mp4"
    changed = bytearray(candidate.read_bytes())
    changed[-1] ^= 1
    candidate.write_bytes(changed)

    with pytest.raises(
        PetSitcomGenerationError, match="provenance"
    ):
        select_pet_shot_candidate(plan, "shot_01", 1)


@pytest.mark.parametrize(
    ("changed_shot_id", "expected"),
    [
        ("shot_06", ("shot_07", "shot_08", "shot_09", "shot_10")),
        (
            "shot_05",
            ("shot_06", "shot_07", "shot_08", "shot_09", "shot_10"),
        ),
        ("shot_10", ()),
    ],
)
def test_dependent_shot_ids_follow_declared_transitive_graph(
    plan, changed_shot_id, expected
):
    assert generation.dependent_shot_ids(plan, changed_shot_id) == expected


def test_shot_one_third_continuity_retry_requires_continuous_cat_motion(plan):
    shot = _shot(plan, "shot_01")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "treat bag stays completely motionless",
        "continuous natural micro-motion",
        "staggered blinks",
        "one natural head turn",
        "Never freeze or repeat identical frames for 0.50 seconds",
    ):
        assert phrase in prompt


def test_shot_one_fourth_continuity_retry_schedules_observable_motion(plan):
    shot = _shot(plan, "shot_01")

    prompt = generation._pet_shot_prompt(shot, 4, "continuity")

    for phrase in (
        "one continuous six-second reaction",
        "0.00-1.20 seconds",
        "one short grounded half-step",
        "slightly different moments",
        "No cat may hold an identical pose longer than 0.25 seconds",
        "never snap between poses",
    ):
        assert phrase in prompt


def test_shot_one_fifth_continuity_retry_avoids_static_final_pose(plan):
    shot = _shot(plan, "shot_01")

    prompt = generation._pet_shot_prompt(shot, 5, "continuity")

    for phrase in (
        "never settle into a static camera-facing portrait",
        "2.40-4.20 seconds",
        "shifts weight gradually between his front paws",
        "turns briefly toward Naitang and then back toward camera",
        "4.20-6.00 seconds",
        "independent motion remains visible through the last frame",
    ):
        assert phrase in prompt


def test_shot_two_continuity_retry_replaces_silent_hold_with_action(plan):
    shot = _shot(plan, "shot_02")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "continuous silent reaction exchange",
        "Naitang slow-blinks once",
        "turns his eyes and chin toward Doubao",
        "Doubao answers with one ear swivel",
        "small grounded weight shift",
        "No identical pose may last 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_three_continuity_retry_binds_mouth_to_canonical_timing_and_motion(
    plan,
):
    shot = _shot(plan, "shot_03")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "begin restrained mouth motion at 0.55 seconds",
        "finish by 5.14 seconds",
        "no added lead-in",
        "Doubao keeps his mouth fully closed",
        "continuous natural body motion",
        "raises one forepaw gradually during the second sentence",
        "No identical pose may last 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_three_third_retry_schedules_each_speech_and_pause_window(plan):
    shot = _shot(plan, "shot_03")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "0.55-2.05 seconds: Naitang speaks",
        "2.05-2.63 seconds: Naitang closes his mouth",
        "2.63-4.09 seconds: Naitang speaks",
        "4.09-4.45 seconds: Naitang closes his mouth",
        "4.45-5.14 seconds: Naitang speaks",
        "After 5.14 seconds his mouth remains fully closed",
        "No speaking window may contain a closed-mouth freeze",
        "breathing, eye focus, and independent ear motion",
    ):
        assert phrase in prompt


def test_shot_four_continuity_retry_preserves_closeup_and_audio_window(plan):
    shot = _shot(plan, "shot_04")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "one continuous close reaction shot of Doubao",
        "Do not cut to a wide kitchen",
        "Keep the bag completely offscreen",
        "begin restrained mouth motion at 0.65 seconds",
        "finish by 1.61 seconds",
        "mouth fully closed after 1.61 seconds",
        "Naitang remains offscreen",
        "glances down-left toward the offscreen bag",
        "No identical pose may last 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_five_continuity_retry_schedules_speaker_reaction_and_motion(plan):
    shot = _shot(plan, "shot_05")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "one continuous locked two-cat medium shot",
        "same living-room axis",
        "do not replace the room with an empty kitchen doorway",
        "mouth closed before 0.55 seconds",
        "from 0.55 through 4.65 seconds",
        "mouth fully closed after 4.65 seconds",
        "keeps his mouth closed through 4.65 seconds",
        "exactly one quick natural lip lick between 5.10 and 5.40 seconds",
        "no pose may remain perceptually identical for 0.25 seconds",
        "No camera movement",
    ):
        assert phrase in prompt


def test_shot_six_continuity_retry_preserves_empty_bag_and_replay_motion(plan):
    shot = _shot(plan, "shot_06")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "fixed floor-level security-camera view",
        "same kitchen wood floor",
        "same beige freeze-dried treat pouch with a silver inner lining",
        "already torn open and completely empty",
        "no pellets, intact treats, crumbs",
        "No cat, person, paw, or tail may enter",
        "Between 0.40 and 1.60 seconds",
        "skids about fifteen centimeters",
        "loose top flap keeps fluttering irregularly",
        "no frame is perceptually identical for 0.25 seconds",
        "generate no speech",
    ):
        assert phrase in prompt


def test_shot_six_third_retry_removes_food_and_prevents_early_settle(plan):
    shot = _shot(plan, "shot_06")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "pouch opening folded down toward the floor",
        "zero pellets, kibble, cubes, treats, crumbs, or powder",
        "From 0.20-2.00 seconds",
        "from 2.00-4.80 seconds",
        "From 4.80-6.10 seconds",
        "never becomes a still photograph before the edit",
        "cats, tails, paws, people, text, and audio completely absent",
    ):
        assert phrase in prompt


def test_shot_seven_continuity_retry_preserves_replay_and_tail_physics(plan):
    shot = _shot(plan, "shot_07")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "exact same fixed floor-level security-camera frame as shot_06",
        "opened empty pouch remains beside the cabinet",
        "no full cat, head, torso, leg, or paw",
        "one orange striped furry tail enters from frame right",
        "between 0.80 and 1.10 seconds",
        "single continuous right-to-left arc",
        "exits frame left by 3.80 seconds",
        "connected to one cat whose entire body remains offscreen",
        "tail root stays clipped by the right or lower frame edge",
        "Never show both tail ends inside the image",
        "does not touch, drag, or move the pouch",
        "no second tail",
        "zero food, pellets, crumbs, text, or audio",
        "subtle changing sensor grain",
    ):
        assert phrase in prompt
    assert "Do not introduce the orange tail before the next shot" not in prompt
    assert "with no cat and no tail yet" not in prompt


def test_shot_seven_third_retry_keeps_tail_root_connected_below_frame(plan):
    shot = _shot(plan, "shot_07")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "Naitang walks just below the lower frame edge",
        "tail root stays continuously clipped by the lower frame edge",
        "attachment point travels steadily from lower-right toward lower-left",
        "Never show both ends of the tail inside the image",
        "never render the whole tail as a detached object",
        "tail arches upward from that hidden attachment point",
        "does not crawl, slither, coil, or lead with both ends",
        "pouch remains completely stationary",
        "changing luminance sensor noise",
    ):
        assert phrase in prompt


def test_shot_eight_continuity_retry_binds_tiny_crumbs_and_mouth_window(plan):
    shot = _shot(plan, "shot_08")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "one continuous locked medium-close shot",
        "exactly three to five tiny irregular beige flakes",
        "right mouth corner and adjacent whisker roots",
        "smaller than the width of one whisker root",
        "remain attached in identical positions from first frame to last",
        "zero cubes, pellets, treats, or crumbs on the floor",
        "mouth fully closed before 0.59 seconds",
        "restrained syllable-level jaw motion from 0.59 through 4.91 seconds",
        "mouth fully closed after 4.91 seconds",
        "Doubao's mouth stays closed for the entire shot",
        "no identical pose may last 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_eight_third_retry_uses_dust_specks_and_continuous_acting(plan):
    shot = _shot(plan, "shot_08")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "tight chest-up close-up",
        "crop the floor completely out of frame",
        "exactly two to four flat light-brown dust specks",
        "size of grains of sand",
        "no three-dimensional cube, pellet, dangling piece, or treat shape",
        "fixed to the right whisker roots",
        "0.00-0.59 seconds",
        "0.59-4.91 seconds",
        "After 4.91 seconds",
        "slow blink, one ear swivel, a small chin lift",
        "Doubao performs one side-eye and independent ear turn",
        "Never hold an identical pose for 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_eight_fourth_retry_confines_evidence_to_mouth_corner(plan):
    shot = _shot(plan, "shot_08")

    prompt = generation._pet_shot_prompt(shot, 4, "continuity")

    for phrase in (
        "preserve the successful continuous acting and dialogue timing",
        "remove every round disk, pellet, cube, or raised food piece",
        "forehead, brow, eyelids, cheeks, nose bridge, nose leather, chin, chest",
        "exactly two tiny matte-brown flecks",
        "only in the fur immediately beside Naitang's right mouth corner",
        "each fleck smaller than one quarter of his visible pupil diameter",
        "flat irregular discolorations between hairs",
        "not separate objects pasted onto the face",
        "remain fixed to the same hairs through every jaw movement",
        "mouth closed before 0.59 seconds",
        "speaks from 0.59 through 4.91 seconds",
        "mouth fully closed after 4.91 seconds",
        "Doubao remains silent with a closed mouth",
        "one continuous locked shot",
    ):
        assert phrase in prompt


def test_shot_eight_fifth_retry_is_clean_performance_layer(plan):
    shot = _shot(plan, "shot_08")

    prompt = generation._pet_shot_prompt(shot, 5, "continuity")

    for phrase in (
        "clean performance layer for deterministic tracked evidence compositing",
        "tight chest-up two-cat composition",
        "Naitang dominant on frame left",
        "Doubao reacting farther back on frame right",
        "Naitang keeps his mouth closed from 0.00 through 0.59 seconds",
        "speaks from 0.59 through 4.91 seconds",
        "mouth remains fully closed from 4.91 seconds through the final frame",
        "continuous breathing, one slow blink, one small chin lift",
        "Doubao remains silent with his mouth fully closed",
        "no perceptually identical pose longer than 0.25 seconds",
        "mechanically locked camera",
        "exactly \"橘色尾巴那么多，不能因为颜色就怀疑一只无辜的小猫。\"",
    ):
        assert phrase in prompt
    for forbidden in (
        "crumb",
        "flake",
        "fleck",
        "pellet",
        "food",
        "treat",
        "dust speck",
    ):
        assert forbidden not in prompt.lower()


def test_shot_nine_continuity_retry_stages_mirror_push_and_doubao_line(plan):
    shot = _shot(plan, "shot_09")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "one continuous locked two-cat shot",
        "small round mirror mounted on a low stable stand",
        "0.00-0.40 seconds",
        "both mouths remain fully closed",
        "0.40-2.20 seconds",
        "Doubao's grounded left forepaw",
        "pushes the mirror and stand about fifteen centimeters",
        "never floats, teleports, flips, or moves before paw contact",
        "2.20-2.55 seconds",
        "mirror settles completely",
        "2.55-4.17 seconds",
        "only Doubao speaks",
        "restrained syllable-level feline jaw motion",
        "Naitang's mouth remains closed",
        "After 4.17 seconds both mouths remain fully closed",
        "Naitang lowers his eyes and chin toward the mirror",
        "exactly two tiny matte-brown flecks",
        "right mouth-corner fur",
        "no ring, beard, cluster, cube, pellet, or raised chunk",
        "no perceptually identical pose longer than 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_nine_third_retry_preserves_mirror_and_removes_dangling_chunk(plan):
    shot = _shot(plan, "shot_09")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "retain the successful upright round mirror",
        "low metal stand",
        "physically grounded paw push",
        "do not redesign, flatten, remove, or teleport the mirror",
        "Remove the single dangling brown chunk",
        "exactly two flat matte-brown flecks",
        "smaller than one quarter of Naitang's visible pupil diameter",
        "same right mouth-corner whisker-root fur",
        "no raised volume, hanging edge, pellet, cube, beard, or cluster",
        "Doubao keeps his mouth closed through 2.55 seconds",
        "speaks from 2.55 through 4.17 seconds",
        "mouth fully closed after 4.17 seconds",
        "Naitang's mouth remains closed for the entire shot",
        "mirror remains stationary after 2.20 seconds",
        "Naitang lowers his eyes and chin after Doubao finishes",
        "no identical pose longer than 0.25 seconds",
    ):
        assert phrase in prompt


def test_shot_ten_first_candidate_uses_exact_drive_window_and_reverse_angle(plan):
    shot = _shot(plan, "shot_10")

    prompt = generation._pet_shot_prompt(shot, 1, "")

    for phrase in (
        "clean performance layer for the final comedy button",
        "Reference 4 defines only the preceding kitchen axis",
        "do not copy its tight Doubao reaction crop",
        "cut exactly once at this shot boundary",
        "front three-quarter medium close-up of Naitang",
        "small round mirror on its low metal stand remains visible",
        "exactly two tiny flat matte-brown flecks",
        "right mouth-corner whisker-root fur",
        "Naitang keeps his mouth fully closed from 0.00 through 0.75 seconds",
        "speaks from 0.75 through 3.35 seconds",
        "exactly \"证据也可能是后来粘上去的。\"",
        "mouth remains fully closed from 3.35 seconds through 5.00 seconds",
        "Doubao remains silent with his mouth fully closed",
        "one slow blink after Naitang finishes",
        "owner laugh will be added only in post-production",
        "no perceptually identical pose longer than 0.25 seconds",
        "mechanically locked camera",
    ):
        assert phrase in prompt
    for forbidden in (
        "0.35 and 1.65",
        "0.40 and 1.70",
        "generated owner laugh",
        "second audible region",
    ):
        assert forbidden not in prompt


def test_shot_ten_continuity_retry_shrinks_flecks_and_moves_blink_into_edit(plan):
    shot = _shot(plan, "shot_10")

    prompt = generation._pet_shot_prompt(shot, 2, "continuity")

    for phrase in (
        "clean performance layer for the final comedy button",
        "Retain the successful locked front three-quarter composition",
        "same stationary round mirror and low metal stand",
        "Correct only the evidence scale and final reaction timing",
        "Replace both round brown disks",
        "exactly two tiny flat irregular matte-brown flecks",
        "five to eight image pixels wide at 1080p",
        "smaller than one quarter of Naitang's visible pupil diameter",
        "Naitang keeps his mouth fully closed through 0.75 seconds",
        "speaks only from 0.75 through 3.35 seconds",
        "mouth fully closed after 3.35 seconds",
        "Doubao keeps his mouth fully closed for the entire clip",
        "between 3.55 and 3.95 seconds",
        "open-to-closed-to-open slow blink",
        "inside the 4.10-second final edit window",
        "mirror remains completely stationary",
        "no identical pose longer than 0.25 seconds",
    ):
        assert phrase in prompt
    for forbidden in (
        "0.35 and 1.65",
        "0.40 and 1.70",
        "three to five tiny beige",
    ):
        assert forbidden not in prompt


def test_shot_ten_third_candidate_is_clean_tracking_plate(plan):
    shot = _shot(plan, "shot_10")

    prompt = generation._pet_shot_prompt(shot, 3, "continuity")

    for phrase in (
        "clean performance plate for deterministic tracked evidence compositing",
        "front three-quarter medium close-up of Naitang",
        "Doubao visible farther back on frame right",
        "small round mirror on its low metal stand",
        "mirror glass remains completely clean",
        "Naitang's entire face and muzzle remain completely clean",
        "zero crumbs, flecks, pellets, cubes, spots, stains, or food",
        "Naitang keeps his mouth fully closed from 0.00 through 0.75 seconds",
        "speaks from 0.75 through 3.35 seconds",
        "exactly \"证据也可能是后来粘上去的。\"",
        "mouth remains fully closed from 3.35 seconds through 5.00 seconds",
        "Doubao remains silent with his mouth fully closed",
        "open-to-closed-to-open slow blink between 3.55 and 3.95 seconds",
        "inside the 4.10-second final edit window",
        "owner laugh will be added only in post-production",
        "no perceptually identical pose longer than 0.25 seconds",
        "mechanically locked camera",
    ):
        assert phrase in prompt
    for forbidden in (
        "Replace both round brown disks",
        "exactly two tiny flat irregular matte-brown flecks",
        "three to five tiny beige",
    ):
        assert forbidden not in prompt


def test_shot_one_local_recut_is_hash_bound_and_selectable(
    plan,
    prepared_references,
    provider_fakes,
):
    for candidate_number in (4, 5):
        report = generate_pet_sitcom_shots(
            plan,
            video_client=provider_fakes,
            allow_network=True,
            shot_id="shot_01",
            candidate_number=candidate_number,
            retry_reason="continuity",
        )
        assert report["success"] is True

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_one_reaction_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 6
    assert len(commands) == 1
    command = " ".join(commands[0])
    for phrase in (
        "trim=start=0:end=3.4",
        "trim=start=3.4:end=6",
        "scale=1404:2496",
        "crop=1080:1920:162:0",
        "concat=n=2:v=1:a=0",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_01")
    candidate = shot.candidate_dir / "candidate_006.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["provider"] == "local_ffmpeg_recut"
    assert state["model"] == "ffmpeg"
    assert state["video_sha256"] == _sha256(candidate)
    assert [
        item["candidate_number"] for item in state["source_candidates"]
    ] == [4, 5]
    assert [
        item["video_sha256"] for item in state["source_candidates"]
    ] == [
        _sha256(shot.candidate_dir / "candidate_004.mp4"),
        _sha256(shot.candidate_dir / "candidate_005.mp4"),
    ]

    selection = select_pet_shot_candidate(plan, "shot_01", 6)
    assert selection["shots"]["shot_01"]["candidate_number"] == 6
    assert (
        selection["shots"]["shot_01"]["video_sha256"]
        == state["video_sha256"]
    )
    from factory.pet_sitcom_review import _validate_selection_source

    reviewed_source = _validate_selection_source(
        plan,
        shot,
        selection["shots"],
    )
    assert reviewed_source["candidate_number"] == 6
    assert reviewed_source["sha256"] == state["video_sha256"]

    source = shot.candidate_dir / "candidate_004.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_01", 6)


def test_shot_three_dialogue_recut_is_hash_bound_audio_exact_and_selectable(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    _seed_selected(plan, "shot_01")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_02",
        candidate_number=2,
        retry_reason="continuity",
    )
    select_pet_shot_candidate(plan, "shot_02", 2)
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
        candidate_number=2,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_three_dialogue_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 4
    assert len(commands) == 1
    command = " ".join(commands[0])
    for phrase in (
        "trim=start=0:end=0.55",
        "trim=start=0.75:end=2.05",
        "trim=start=0.6:end=3.2",
        "trim=start=4.45:end=5.35",
        "trim=start=2.35:end=4",
        "concat=n=5:v=1:a=0",
        str(prepared_audio.drive["shot_03"]),
        "-map 5:a:0",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_03")
    candidate = shot.candidate_dir / "candidate_004.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 4
    assert state["provider"] == "local_ffmpeg_recut"
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_03"]
    )
    assert state["video_sha256"] == _sha256(candidate)
    assert [
        Path(item["video_path"]).parent.name
        for item in state["source_candidates"]
    ] == [
        "shot_02",
        "shot_03",
        "shot_02",
        "shot_03",
        "shot_02",
    ]

    selection = select_pet_shot_candidate(plan, "shot_03", 4)
    assert selection["shots"]["shot_03"]["candidate_number"] == 4
    assert selection["shots"]["shot_03"]["video_sha256"] == state["video_sha256"]

    source = _shot(plan, "shot_02").candidate_dir / "candidate_002.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_03", 4)


def test_shot_four_dialogue_recut_retimes_good_motion_to_exact_drive_audio(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_01")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_02",
        candidate_number=2,
        retry_reason="continuity",
    )
    select_pet_shot_candidate(plan, "shot_02", 2)
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
        candidate_number=2,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    generation.build_pet_shot_three_dialogue_recut(
        plan,
        command_runner=render,
    )
    select_pet_shot_candidate(plan, "shot_03", 4)
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_04",
        candidate_number=2,
        retry_reason="continuity",
    )

    report = generation.build_pet_shot_four_dialogue_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 3
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=5.39:end=6.04",
        "trim=start=1.585:end=4.175",
        "trim=start=6.04:end=7",
        "concat=n=3:v=1:a=0",
        str(prepared_audio.drive["shot_04"]),
        "-map 3:a:0",
        "-t 4.2",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_04")
    candidate = shot.candidate_dir / "candidate_003.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 3
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_04"]
    )
    assert [
        Path(item["video_path"]).parent.name
        for item in state["source_candidates"]
    ] == ["shot_03", "shot_04", "shot_03"]

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }
    audio_stream_counts = {
        item.shot_id: (
            1 if item.speaker in {"naitang", "doubao"} else 0
        )
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_04" and source.name == "candidate_003.mp4":
            duration = 4.2
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=audio_stream_counts[shot_id],
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_04", 3)
    assert selection["shots"]["shot_04"]["candidate_number"] == 3
    assert selection["shots"]["shot_04"]["video_sha256"] == state["video_sha256"]


def test_shot_five_dialogue_recut_covers_speaker_and_reaction_with_exact_audio(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_04")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_05",
        candidate_number=2,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_five_dialogue_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 3
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=0.4:end=0.95",
        "trim=start=3.5:end=7.596",
        "crop=540:960:540:480,scale=1080:1920",
        "trim=start=4.6:end=7.254",
        "crop=540:960:0:480,scale=1080:1920",
        "concat=n=3:v=1:a=0",
        str(prepared_audio.drive["shot_05"]),
        "-map 3:a:0",
        "-t 7.3",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_05")
    candidate = shot.candidate_dir / "candidate_003.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 3
    assert state["retry_reason"] == "mouth_anatomy"
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_05"]
    )
    assert [
        item["candidate_number"] for item in state["source_candidates"]
    ] == [2, 2, 2]

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_05" and source.name == "candidate_003.mp4":
            duration = 7.3
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=1,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_05", 3)
    assert selection["shots"]["shot_05"]["candidate_number"] == 3
    assert selection["shots"]["shot_05"]["video_sha256"] == state["video_sha256"]

    source = shot.candidate_dir / "candidate_002.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_05", 3)


def test_shot_six_security_recut_adds_only_bound_luma_sensor_grain(
    plan,
    prepared_references,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_05")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_06",
        candidate_number=3,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_six_security_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 4
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=0:end=6.1",
        "noise=c0s=12:c0f=t+u",
        "format=yuv420p",
        "-an",
        "-t 6.1",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_06")
    candidate = shot.candidate_dir / "candidate_004.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 4
    assert state["retry_reason"] == "continuity"
    assert state["generate_audio"] is False
    assert state["reference_audio_sha256"] == ""
    assert state["source_candidates"][0]["candidate_number"] == 3

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_06" and source.name == "candidate_004.mp4":
            duration = 6.1
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=0,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_06", 4)
    assert selection["shots"]["shot_06"]["candidate_number"] == 4
    assert selection["shots"]["shot_06"]["video_sha256"] == state["video_sha256"]

    source = shot.candidate_dir / "candidate_003.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_06", 4)


def test_shot_seven_tail_recut_preserves_bound_motion_and_adds_sensor_grain(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_05", "shot_06")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_07",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_seven_tail_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 3
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=0:end=4.8",
        "noise=c0s=12:c0f=t+u",
        "format=yuv420p",
        "-an",
        "-t 4.8",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_07")
    candidate = shot.candidate_dir / "candidate_003.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 3
    assert state["retry_reason"] == "continuity"
    assert state["generate_audio"] is False
    assert state["reference_audio_sha256"] == ""
    assert state["source_candidates"][0]["candidate_number"] == 1

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_07" and source.name == "candidate_003.mp4":
            duration = 4.8
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=0,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_07", 3)
    assert selection["shots"]["shot_07"]["candidate_number"] == 3
    assert selection["shots"]["shot_07"]["video_sha256"] == state["video_sha256"]

    source = shot.candidate_dir / "candidate_001.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_07", 3)


def test_shot_eight_evidence_recut_retimes_dialogue_and_tracks_tiny_flecks(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_07")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_08",
        candidate_number=1,
    )
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_08",
        candidate_number=5,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_eight_evidence_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 6
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=1.1:end=1.69",
        "trim=start=1.78:end=7.25",
        "setpts=0.789762340036563*PTS",
        "trim=start=3:end=5.09",
        "crop=400:711:680:300,scale=1080:1920",
        "concat=n=3:v=1:a=0",
        "-loop 1",
        "overlay=x=",
        "eval=frame",
        "enable='lt(t,4.91)'",
        "-map [v]",
        "-map 4:a:0",
        "-t 7",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_08")
    candidate = shot.candidate_dir / "candidate_006.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 6
    assert state["retry_reason"] == "continuity"
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_08"]
    )
    assert {
        item["candidate_number"] for item in state["source_candidates"]
    } == {1, 5}
    assert state["recipe"]["mouth_window_seconds"] == [0.59, 4.91]
    assert state["recipe"]["fleck_count"] == 2
    assert state["recipe"]["tracking_keyframes"] == [
        {"time_seconds": 0.0, "x": 270, "y": 935},
        {"time_seconds": 0.59, "x": 275, "y": 935},
        {"time_seconds": 4.91, "x": 259, "y": 925},
    ]

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        audio_stream_count = 1
        if shot_id == "shot_08" and source.name == "candidate_006.mp4":
            duration = 7.0
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=audio_stream_count,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_08", 6)
    assert selection["shots"]["shot_08"]["candidate_number"] == 6
    assert selection["shots"]["shot_08"]["video_sha256"] == state["video_sha256"]

    source = shot.candidate_dir / "candidate_005.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_08", 6)


def test_shot_nine_evidence_recut_preserves_mirror_and_binds_doubao_line(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_08")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_09",
        candidate_number=2,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_nine_evidence_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 6
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=0:end=2.9",
        "setpts=0.879310344827586*PTS",
        "trim=start=2.9:end=4.45",
        "setpts=1.04193548387097*PTS",
        "trim=start=4.625:end=5.96",
        "crop=500:889:580:300,scale=1080:1920",
        "removelogo=f=",
        "concat=n=3:v=1:a=0",
        "-loop 1",
        "overlay=x=",
        "eval=frame",
        "enable='lt(t,4.165)'",
        "-map [v]",
        "-map 4:a:0",
        "-t 5.5",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_09")
    candidate = shot.candidate_dir / "candidate_006.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 6
    assert state["retry_reason"] == "continuity"
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_09"]
    )
    assert {
        item["candidate_number"] for item in state["source_candidates"]
    } == {2}
    assert state["recipe"]["mouth_window_seconds"] == [2.55, 4.165]
    assert state["recipe"]["fleck_count"] == 2
    assert state["recipe"]["removal_segment_indexes"] == [0, 1]
    assert state["recipe"]["removal_mask"] == {
        "width": 1080,
        "height": 1920,
        "center_x": 386,
        "center_y": 706,
        "radius_x": 30,
        "radius_y": 50,
    }
    assert state["recipe"]["tracking_keyframes"] == [
        {"time_seconds": 0.0, "x": 402, "y": 686},
        {"time_seconds": 2.55, "x": 403, "y": 687},
        {"time_seconds": 4.165, "x": 403, "y": 688},
    ]

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_09" and source.name == "candidate_006.mp4":
            duration = 5.5
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=1,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_09", 6)
    assert selection["shots"]["shot_09"]["candidate_number"] == 6
    assert (
        selection["shots"]["shot_09"]["video_sha256"]
        == state["video_sha256"]
    )

    source = shot.candidate_dir / "candidate_002.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_09", 6)


def test_shot_ten_evidence_recut_binds_line_blink_and_clean_mirror(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
    monkeypatch,
):
    _seed_selected(plan, "shot_09")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_10",
        candidate_number=3,
        retry_reason="continuity",
    )

    commands: list[list[str]] = []

    def render(command, **kwargs):
        normalized = [str(item) for item in command]
        commands.append(normalized)
        output = Path(normalized[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(VALID_VIDEO_MP4)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    report = generation.build_pet_shot_ten_evidence_recut(
        plan,
        command_runner=render,
    )

    assert report["success"] is True
    assert report["candidate_number"] == 6
    command = " ".join(commands[-1])
    for phrase in (
        "trim=start=0:end=0.416667",
        "setpts=1.68*PTS,framerate=fps=24",
        "trim=start=0.416667:end=2.83333",
        "setpts=1.09620703448276*PTS,framerate=fps=24",
        "trim=start=2.83333:end=3.41667",
        "setpts=1.28714228571429*PTS,framerate=fps=24",
        "concat=n=3:v=1:a=0",
        "-loop 1",
        "overlay=x=",
        "eval=frame",
        "enable='lt(t,4.1)'",
        "-map [v]",
        "-map 4:a:0",
        "-t 4.1",
    ):
        assert phrase in command

    shot = _shot(plan, "shot_10")
    candidate = shot.candidate_dir / "candidate_006.mp4"
    state = json.loads(
        candidate.with_suffix(".provenance.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["schema_version"] == generation.PET_LOCAL_RECUT_SCHEMA
    assert state["candidate_number"] == 6
    assert state["retry_reason"] == "continuity"
    assert state["generate_audio"] is True
    assert state["reference_audio_sha256"] == _sha256(
        prepared_audio.drive["shot_10"]
    )
    assert {
        item["candidate_number"] for item in state["source_candidates"]
    } == {3}
    assert state["recipe"]["mouth_window_seconds"] == [0.7, 3.349167]
    assert state["recipe"]["blink_window_seconds"] == [3.55, 4.1]
    assert state["recipe"]["fleck_count"] == 2
    assert state["recipe"]["tracking_keyframes"] == [
        {"time_seconds": 0.0, "x": 360, "y": 994},
        {"time_seconds": 0.7, "x": 330, "y": 947},
        {"time_seconds": 3.349167, "x": 302, "y": 920},
        {"time_seconds": 4.1, "x": 302, "y": 920},
    ]

    generated_durations = {
        item.shot_id: float(item.generation_duration_seconds)
        for item in plan.shots
    }

    def probe_local_edit_duration(path, *, required_stream):
        source = Path(path)
        shot_id = source.parent.name
        duration = generated_durations[shot_id]
        if shot_id == "shot_10" and source.name == "candidate_006.mp4":
            duration = 4.1
        return SimpleNamespace(
            valid=True,
            duration_seconds=duration,
            video_stream_count=1,
            audio_stream_count=1,
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe_local_edit_duration,
    )
    selection = select_pet_shot_candidate(plan, "shot_10", 6)
    assert selection["shots"]["shot_10"]["candidate_number"] == 6
    assert (
        selection["shots"]["shot_10"]["video_sha256"]
        == state["video_sha256"]
    )

    source = shot.candidate_dir / "candidate_003.mp4"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(
        PetSitcomGenerationError,
        match="hash|local recut provenance",
    ):
        select_pet_shot_candidate(plan, "shot_10", 6)


def test_selecting_changed_shot_invalidates_only_transitive_dependents(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    _seed_selected(plan, "shot_05")
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_06",
    )
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_06",
        candidate_number=2,
        retry_reason="continuity",
    )
    document = json.loads(plan.selection_path.read_text(encoding="utf-8"))
    for downstream in ("shot_07", "shot_08", "shot_09", "shot_10"):
        document["shots"][downstream] = {
            "candidate_number": 1,
            "status": "selected",
            "video_path": f"/placeholder/{downstream}.mp4",
            "video_sha256": downstream,
        }
    document["shots"]["shot_05"]["marker"] = "unchanged"
    prior_shot_six = dict(document["shots"]["shot_06"])
    _write_json(plan.selection_path, document)

    selection = select_pet_shot_candidate(plan, "shot_06", 2)

    assert selection["shots"]["shot_05"]["status"] == "selected"
    assert selection["shots"]["shot_05"]["marker"] == "unchanged"
    assert selection["shots"]["shot_06"]["status"] == "selected"
    assert selection["history"]["shot_06"] == [prior_shot_six]
    for downstream in ("shot_07", "shot_08", "shot_09", "shot_10"):
        assert selection["shots"][downstream]["status"] == "stale_upstream"
        assert selection["shots"][downstream]["stale_from_shot"] == "shot_06"


def test_normal_resume_preserves_selected_retry_candidate_and_history(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )
    first_selection = json.loads(
        plan.selection_path.read_text(encoding="utf-8")
    )["shots"]["shot_01"]
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
        candidate_number=2,
        retry_reason="continuity",
    )
    selected_retry = select_pet_shot_candidate(plan, "shot_01", 2)
    retry_entry = dict(selected_retry["shots"]["shot_01"])
    provider_fakes.calls.clear()

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    document = json.loads(
        plan.selection_path.read_text(encoding="utf-8")
    )
    assert report["success"] is True
    assert report["reused_count"] == 1
    assert provider_fakes.calls == []
    assert document["shots"]["shot_01"] == retry_entry
    assert document["history"]["shot_01"] == [first_selection]


def test_selected_continuity_frame_records_edit_endpoint_and_hashes(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_01",
    )

    selection = json.loads(plan.selection_path.read_text(encoding="utf-8"))[
        "shots"
    ]["shot_01"]
    sidecar = json.loads(
        Path(selection["continuity_sidecar_path"]).read_text(encoding="utf-8")
    )
    assert selection["continuity_timestamp_seconds"] == pytest.approx(5.12)
    assert sidecar["timestamp_seconds"] == pytest.approx(5.12)
    assert sidecar["edit_duration_seconds"] == pytest.approx(5.2)
    assert sidecar["source_video_duration_seconds"] == pytest.approx(6.0)
    assert sidecar["source_video_sha256"] == selection["video_sha256"]
    assert sidecar["frame_sha256"] == selection["continuity_frame_sha256"]


def test_extract_continuity_uses_edit_endpoint_when_source_is_longer(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    output = tmp_path / "continuity" / "shot_10_last.png"
    source.write_bytes(VALID_VIDEO_MP4)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=5.0,
            video_stream_count=1,
            audio_stream_count=1,
        ),
    )

    def runner(command, **kwargs):
        calls.append(command)
        _write_png(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    result = extract_pet_continuity_frame(
        source,
        output,
        edit_duration_seconds=4.1,
        command_runner=runner,
        output_dir=tmp_path,
    )

    assert result == output.resolve()
    assert calls[0][calls[0].index("-ss") + 1] == "4.020"
    state = json.loads(
        output.with_suffix(".png.state.json").read_text(encoding="utf-8")
    )
    assert state["timestamp_seconds"] == pytest.approx(4.02)
    assert state["edit_duration_seconds"] == pytest.approx(4.1)
    assert state["source_video_duration_seconds"] == pytest.approx(5.0)
    assert state["source_video_sha256"] == _sha256(source)
    assert state["frame_sha256"] == _sha256(output)


def test_extract_continuity_uses_source_endpoint_when_source_is_shorter(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    output = tmp_path / "continuity.png"
    source.write_bytes(VALID_VIDEO_MP4)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=3.5,
            video_stream_count=1,
            audio_stream_count=1,
        ),
    )

    def runner(command, **kwargs):
        calls.append(command)
        _write_png(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    extract_pet_continuity_frame(
        source,
        output,
        edit_duration_seconds=4.1,
        command_runner=runner,
        output_dir=tmp_path,
    )

    assert calls[0][calls[0].index("-ss") + 1] == "3.420"


def test_extract_continuity_reuses_only_exact_bound_sidecar(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    output = tmp_path / "continuity.png"
    source.write_bytes(VALID_VIDEO_MP4)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=5.0,
            video_stream_count=1,
            audio_stream_count=1,
        ),
    )

    def runner(command, **kwargs):
        calls.append(command)
        _write_png(Path(command[-1]), (len(calls), 2, 3))
        return subprocess.CompletedProcess(command, 0, "", "")

    for _ in range(2):
        extract_pet_continuity_frame(
            source,
            output,
            edit_duration_seconds=4.1,
            command_runner=runner,
            output_dir=tmp_path,
        )
    assert len(calls) == 1

    sidecar = output.with_suffix(".png.state.json")
    state = json.loads(sidecar.read_text(encoding="utf-8"))
    state["untrusted_extra"] = True
    _write_json(sidecar, state)
    extract_pet_continuity_frame(
        source,
        output,
        edit_duration_seconds=4.1,
        command_runner=runner,
        output_dir=tmp_path,
    )
    assert len(calls) == 2

    changed_source = bytearray(VALID_VIDEO_MP4)
    changed_source[-1] ^= 1
    source.write_bytes(changed_source)
    extract_pet_continuity_frame(
        source,
        output,
        edit_duration_seconds=4.1,
        command_runner=runner,
        output_dir=tmp_path,
    )
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("source_duration", "edit_duration", "timestamp"),
    [
        (-1.0, 4.1, -1.08),
        (0.0, 4.1, -0.08),
        (0.07, 4.1, -0.01),
        (0.08, 4.1, 0.0),
        (float("nan"), 4.1, 0.0),
        (float("inf"), 4.1, 4.02),
        (5.0, -1.0, -1.08),
        (5.0, 0.0, -0.08),
        (5.0, 0.08, 0.0),
        (5.0, float("nan"), 0.0),
        (5.0, float("inf"), 4.92),
        (5.0, 4.1, -0.01),
        (5.0, 4.1, float("nan")),
        (5.0, 4.1, float("inf")),
        (True, 4.1, 0.92),
        (5.0, True, 0.92),
        (1.08, 4.1, True),
    ],
)
def test_continuity_state_rejects_unsafe_duration_and_timestamp_domain(
    tmp_path,
    source_duration,
    edit_duration,
    timestamp,
):
    source = tmp_path / "source.mp4"
    frame = tmp_path / "frame.png"
    source.write_bytes(VALID_VIDEO_MP4)
    _write_png(frame)
    _write_json(
        frame.with_suffix(".png.state.json"),
        {
            "schema_version": PET_CONTINUITY_SCHEMA,
            "source_video_path": str(source.resolve()),
            "source_video_sha256": _sha256(source),
            "source_video_duration_seconds": source_duration,
            "edit_duration_seconds": edit_duration,
            "timestamp_seconds": timestamp,
            "extracted_at": "2026-07-27T00:00:00+00:00",
            "frame_sha256": _sha256(frame),
        },
    )

    assert generation._pet_continuity_matches(source, frame) is False


def test_continuity_state_accepts_smallest_positive_endpoint(tmp_path):
    source = tmp_path / "source.mp4"
    frame = tmp_path / "frame.png"
    source.write_bytes(VALID_VIDEO_MP4)
    _write_png(frame)
    duration = 0.080001
    endpoint = duration - 0.08
    _write_json(
        frame.with_suffix(".png.state.json"),
        {
            "schema_version": PET_CONTINUITY_SCHEMA,
            "source_video_path": str(source.resolve()),
            "source_video_sha256": _sha256(source),
            "source_video_duration_seconds": duration,
            "edit_duration_seconds": duration,
            "timestamp_seconds": endpoint,
            "extracted_at": "2026-07-27T00:00:00+00:00",
            "frame_sha256": _sha256(frame),
        },
    )

    assert generation._pet_continuity_matches(source, frame) is True


def test_continuity_symlink_is_rejected_before_external_write(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(VALID_VIDEO_MP4)
    outside = tmp_path / "outside"
    outside.mkdir()
    continuity = tmp_path / "safe" / "continuity"
    continuity.parent.mkdir()
    continuity.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        generation,
        "probe_media",
        lambda path, *, required_stream: SimpleNamespace(
            valid=True,
            duration_seconds=5.0,
            video_stream_count=1,
            audio_stream_count=1,
        ),
    )

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        extract_pet_continuity_frame(
            source,
            continuity / "frame.png",
            edit_duration_seconds=4.1,
            output_dir=tmp_path,
        )

    assert list(outside.iterdir()) == []


def test_generation_rejects_symlinked_continuity_directory_before_provider(
    plan,
    prepared_references,
    prepared_audio,
    fake_video_client,
    tmp_path,
):
    outside = tmp_path / "outside-continuity"
    outside.mkdir()
    continuity = plan.output_dir / "continuity"
    continuity.parent.mkdir(parents=True, exist_ok=True)
    continuity.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        generate_pet_sitcom_shots(
            plan,
            video_client=fake_video_client,
            allow_network=True,
            shot_id="shot_01",
        )

    assert fake_video_client.calls == []
    assert list(outside.iterdir()) == []


def test_forged_candidate_path_escape_is_rejected_before_provider_or_report(
    plan,
    fake_video_client,
    tmp_path,
):
    escaped = tmp_path / "escaped"
    first = replace(plan.shots[0], candidate_dir=escaped)
    forged = replace(plan, shots=(first, *plan.shots[1:]))

    with pytest.raises(
        PetSitcomGenerationError, match="escapes"
    ):
        generate_pet_sitcom_shots(
            forged,
            video_client=fake_video_client,
            allow_network=False,
            shot_id="shot_01",
        )

    assert fake_video_client.calls == []
    assert not escaped.exists()
    assert not plan.generation_report_path.exists()


def test_candidate_directory_symlink_is_rejected_before_provider(
    plan,
    fake_video_client,
    tmp_path,
):
    outside = tmp_path / "outside-candidate"
    outside.mkdir()
    candidate_dir = _shot(plan, "shot_01").candidate_dir
    candidate_dir.parent.mkdir(parents=True, exist_ok=True)
    candidate_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        generate_pet_sitcom_shots(
            plan,
            video_client=fake_video_client,
            allow_network=False,
            shot_id="shot_01",
        )

    assert fake_video_client.calls == []
    assert list(outside.iterdir()) == []


def test_generation_report_symlink_is_rejected_without_touching_target(
    plan,
    fake_video_client,
    tmp_path,
):
    outside = tmp_path / "outside-report.json"
    outside.write_text("untouched", encoding="utf-8")
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    plan.generation_report_path.symlink_to(outside)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        generate_pet_sitcom_shots(
            plan,
            video_client=fake_video_client,
            allow_network=False,
            shot_id="shot_01",
        )

    assert outside.read_text(encoding="utf-8") == "untouched"
    assert fake_video_client.calls == []


def test_atomic_json_replace_failure_preserves_existing_document(
    tmp_path,
    monkeypatch,
):
    destination = tmp_path / "state.json"
    destination.write_text('{"stable": true}\n', encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(generation.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        generation._write_atomic_json(
            destination,
            {"stable": False},
            output_dir=tmp_path,
        )

    assert destination.read_text(encoding="utf-8") == '{"stable": true}\n'
    assert list(tmp_path.glob(".state.json.*.json")) == []


def test_generation_rejects_symlinked_drive_audio_before_provider(
    plan,
    prepared_references,
    prepared_audio,
    provider_fakes,
):
    _seed_selected(plan, "shot_02")
    drive = prepared_audio.drive["shot_03"]
    real_drive = drive.with_name("real-shot_03-drive.wav")
    drive.replace(real_drive)
    drive.symlink_to(real_drive)

    report = generate_pet_sitcom_shots(
        plan,
        video_client=provider_fakes,
        allow_network=True,
        shot_id="shot_03",
    )

    assert report["success"] is False
    assert "symlink" in report["errors"][0]["error"]
    assert provider_fakes.calls == []


def test_anchor_dry_run_makes_no_provider_calls(plan, fake_image_client):
    report = generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=False,
    )

    assert report["planned_count"] == 4
    assert report["executed"] is False
    assert fake_image_client.calls == []


def test_single_anchor_target_limits_generation_and_review_template(
    plan,
    fake_image_client,
):
    report = generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
        anchor_names=("naitang",),
    )

    assert report["success"] is True
    assert report["planned_count"] == 1
    assert [item["name"] for item in report["anchors"]] == ["naitang"]
    assert len(fake_image_client.calls) == 1
    assert plan.characters[0].reference_path.is_file()
    assert not plan.characters[1].reference_path.exists()
    assert not (plan.output_dir / "anchor_review_template.json").exists()


@pytest.mark.parametrize(
    "anchor_names",
    (
        (),
        ("naitang", "naitang"),
        ("unknown",),
        ("naitang", 1),
        ["naitang"],
    ),
)
def test_anchor_targets_reject_invalid_requests_before_provider(
    plan,
    fake_image_client,
    anchor_names,
):
    with pytest.raises(PetSitcomGenerationError):
        generation.generate_pet_sitcom_anchors(
            plan,
            image_client=fake_image_client,
            allow_network=True,
            anchor_names=anchor_names,
        )

    assert fake_image_client.calls == []
    assert not plan.output_dir.exists()


def test_anchor_review_template_waits_for_all_current_anchors(
    plan,
    fake_image_client,
):
    for name in ("naitang", "doubao", "living_room"):
        generation.generate_pet_sitcom_anchors(
            plan,
            image_client=fake_image_client,
            allow_network=True,
            anchor_names=(name,),
        )
        assert not (
            plan.output_dir / "anchor_review_template.json"
        ).exists()

    generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
        anchor_names=("kitchen",),
    )

    assert (plan.output_dir / "anchor_review_template.json").is_file()


def test_anchor_generation_normalizes_provider_images_to_png(
    plan,
    fake_image_client,
):
    report = generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
    )

    assert report["success"] is True
    for path in [
        *(item.reference_path for item in plan.characters),
        *(item.anchor_path for item in plan.scenes),
    ]:
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert all(
        call["size"] == "1440x2560" and call["n"] == 1
        for call in fake_image_client.calls
    )


def test_matching_anchors_reuse_without_provider_and_preserve_approval(
    plan,
    fake_image_client,
):
    generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
    )
    _approve_anchors(plan)
    fake_image_client.calls.clear()

    report = generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
    )

    assert report["reused_count"] == 4
    assert fake_image_client.calls == []
    review = json.loads(
        (
            plan.output_dir / "anchor_review_template.json"
        ).read_text(encoding="utf-8")
    )
    assert review["approved"] is True


def test_anchor_approval_revalidates_current_hashes_and_checks(
    plan,
    fake_image_client,
):
    generation.generate_pet_sitcom_anchors(
        plan,
        image_client=fake_image_client,
        allow_network=True,
    )
    review_path = plan.output_dir / "anchor_review_template.json"
    incomplete = json.loads(review_path.read_text(encoding="utf-8"))
    incomplete["completed"] = True
    _write_json(review_path, incomplete)
    with pytest.raises(
        PetSitcomGenerationError, match="boolean"
    ):
        generation.approve_pet_anchors(plan)

    _approve_anchors(plan)
    assert generation.approve_pet_anchors(plan)["approved"] is True
    plan.scenes[0].anchor_path.write_bytes(b"changed")
    with pytest.raises(PetSitcomGenerationError, match="hash"):
        generation.approve_pet_anchors(plan)


def test_anchor_output_symlink_is_rejected_before_provider(
    plan,
    fake_image_client,
    tmp_path,
):
    outside = tmp_path / "outside-anchor.png"
    _write_png(outside)
    outside_hash = _sha256(outside)
    target = plan.characters[0].reference_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        generation.generate_pet_sitcom_anchors(
            plan,
            image_client=fake_image_client,
            allow_network=True,
        )

    assert fake_image_client.calls == []
    assert _sha256(outside) == outside_hash


def test_gateway_failure_is_sanitized_and_stops_sequence(
    plan,
    prepared_references,
    prepared_audio,
    fake_video_client,
    monkeypatch,
):
    def fail(*args, **kwargs):
        raise RuntimeError(
            "gateway-secret https://private.example/video?signature=secret"
        )

    monkeypatch.setattr(generation, "render_gateway_video_single", fail)

    report = generate_pet_sitcom_shots(
        plan,
        video_client=fake_video_client,
        allow_network=True,
        shot_id="shot_01",
    )

    assert report["success"] is False
    serialized = json.dumps(report)
    assert "gateway-secret" not in serialized
    assert "private.example" not in serialized
    assert "[redacted]" in serialized or "[remote-url]" in serialized


def test_sanitizer_redacts_nested_secrets_inline_data_and_urls():
    result = sanitize_pet_sitcom_report(
        {
            "authorization": "Bearer secret",
            "nested": [
                "key-value https://private.example/file?token=x",
                "data:audio/wav;base64,AAAA",
            ],
        },
        ("key-value",),
    )

    assert result["authorization"] == "[redacted]"
    assert result["nested"] == ["[redacted] [remote-url]", "[inline-data]"]


@pytest.mark.parametrize(
    "label",
    (
        "client_secret",
        "private_key",
        "password",
        "passwd",
        "response",
        "signature",
        "credential",
        "session_token",
        "x-api-key",
        "token",
        "secret",
    ),
)
def test_sanitizer_redacts_ambiguous_values_to_line_end(label):
    raw = (
        f"gateway failed; {label}=two words that must not leak\n"
        "next line remains useful"
    )

    persisted = json.dumps(
        sanitize_pet_sitcom_report({"error": raw})
    )

    for fragment in ("two", "words", "that", "must", "not", "leak"):
        assert fragment not in persisted
    assert "next line remains useful" in persisted


def test_sanitizer_redacts_arbitrary_authorization_to_line_end():
    raw = (
        "gateway failed; Authorization=Digest username=cat, realm=home\n"
        "next line remains useful"
    )

    persisted = json.dumps(
        sanitize_pet_sitcom_report({"error": raw})
    )

    for fragment in ("Digest", "username", "cat", "realm", "home"):
        assert fragment not in persisted
    assert "Authorization: [redacted]" in persisted
    assert "next line remains useful" in persisted


def test_legacy_mouth_test_apis_fail_closed_without_side_effects(
    plan,
    fake_video_client,
):
    calls = (
        lambda: generation.generate_pet_mouth_test(
            plan,
            video_client=fake_video_client,
            allow_network=True,
        ),
        lambda: generation.approve_pet_mouth_test(plan),
        lambda: generation.require_approved_mouth_test(plan),
    )

    for call in calls:
        with pytest.raises(
            PetSitcomGenerationError, match="audio-drive probe"
        ):
            call()

    assert fake_video_client.calls == []
    assert not plan.output_dir.exists()
