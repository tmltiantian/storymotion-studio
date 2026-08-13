from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from PIL import Image

import factory.pet_sitcom_audio_first as audio_first
import factory.pet_sitcom_audio_probe as audio_probe
from factory.gateway_video import GatewayVideoHTTPError
from factory.pet_sitcom import build_pet_sitcom_plan
from factory.pet_sitcom_audio_first import (
    build_pet_drive_audio,
    generate_pet_speech_assets,
    load_pet_speech_assets,
)
from factory.pet_sitcom_audio_probe import (
    PROBE_FRAME_TIMESTAMPS,
    PROBE_MODEL,
    PROBE_REVIEW_GATES,
    require_approved_pet_audio_probe,
    run_pet_audio_drive_probe,
)
from factory.pet_sitcom_generation import PetSitcomGenerationError
from tests.media_fixtures import VALID_VIDEO_MP4


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x01\x00" * 2 * int(seconds * 48000))


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, tmp_path / "pet-sitcom")


@pytest.fixture
def prepared_audio_manifest(plan, monkeypatch):
    class FakeTTS:
        config = SimpleNamespace(resource_id="seed-tts-2.0")

        def synthesize(self, text, output_path, **kwargs):
            _write_wav(Path(output_path), 1.0)
            return SimpleNamespace(output_path=Path(output_path))

    def one_second_probe(path, *, required_stream):
        return SimpleNamespace(
            valid=True,
            duration_seconds=1.0,
            audio_stream_count=1,
            video_stream_count=0,
        )

    def trim_runner(command, **kwargs):
        _write_wav(Path(command[-1]), 1.0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(audio_first, "probe_media", one_second_probe)
    generate_pet_speech_assets(
        plan,
        tts_client=FakeTTS(),
        allow_network=True,
        command_runner=trim_runner,
    )

    def drive_runner(command, **kwargs):
        _write_wav(Path(command[-1]), 5.0)
        return subprocess.CompletedProcess(command, 0, "", "")

    drive = build_pet_drive_audio(plan, "shot_04", command_runner=drive_runner)
    assert load_pet_speech_assets(plan)
    return drive


@pytest.fixture
def prepared_references(plan):
    doubao = next(item for item in plan.characters if item.slug == "doubao")
    kitchen = next(item for item in plan.scenes if item.slug == "kitchen")
    for path, color in (
        (doubao.reference_path, (80, 70, 60)),
        (kitchen.anchor_path, (210, 200, 180)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), color).save(path, format="PNG")
    return doubao.reference_path, kitchen.anchor_path


@pytest.fixture
def fake_video_client():
    return SimpleNamespace(
        config=SimpleNamespace(api_key="video-secret", model=PROBE_MODEL),
        calls=[],
    )


@pytest.fixture
def successful_renderer(monkeypatch):
    def install(client):
        def render(prompt, output_path, video_client, report_path, **kwargs):
            client.calls.append(
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
            Path(report_path).write_text(
                json.dumps(
                    {
                        "success": True,
                        "task_id": "safe-task-id",
                        "video_url": "https://private.example/video.mp4?sig=secret",
                    }
                ),
                encoding="utf-8",
            )
            return {"success": True, "task_id": "safe-task-id"}

        def extract(video_path, evidence_dir, **kwargs):
            evidence_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            for index, timestamp in enumerate(PROBE_FRAME_TIMESTAMPS, start=1):
                path = evidence_dir / f"frame_{index:02d}.png"
                Image.new("RGB", (8, 8), (index, 20, 30)).save(path, format="PNG")
                frames.append(
                    {
                        "timestamp_seconds": timestamp,
                        "path": str(path.resolve()),
                        "sha256": _sha256(path),
                    }
                )
            return frames

        monkeypatch.setattr(audio_probe, "render_gateway_video_single", render)
        monkeypatch.setattr(audio_probe, "_extract_probe_frames", extract)

    return install


@pytest.fixture
def successful_probe(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
):
    successful_renderer(fake_video_client)
    run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    return plan.audio_probe_path


def _approve_review(plan) -> dict[str, object]:
    review = json.loads(plan.audio_probe_review_path.read_text(encoding="utf-8"))
    review.update(
        {
            "completed": True,
            "approved": True,
            "audio_onset_seconds": 0.65,
            "mouth_onset_seconds": 0.80,
            "audio_offset_seconds": 1.65,
            "mouth_offset_seconds": 1.45,
            "notes": "Manually reviewed all fixed evidence frames and the probe MP4.",
        }
    )
    review.update({gate: True for gate in PROBE_REVIEW_GATES})
    plan.audio_probe_review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return review


def test_probe_uses_final_doubao_audio_and_one_live_request(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
):
    successful_renderer(fake_video_client)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    call = fake_video_client.calls[0]

    assert report["success"] is True
    assert report["capability"] == "supported"
    assert len(fake_video_client.calls) == 1
    assert call["audio"].name == "shot_04_drive.wav"
    assert call["duration"] == 5
    assert call["generate_audio"] is True
    assert call["images"] == list(prepared_references)
    assert report["source_shot_id"] == "shot_04"
    assert report["model"] == "doubao-seedance-2-0"
    assert report["drive_audio_sha256"] == _sha256(prepared_audio_manifest)
    doubao_asset = next(
        item for item in load_pet_speech_assets(plan) if item.shot_id == "shot_04"
    )
    assert report["source_tts_sha256"] == doubao_asset.output_sha256
    assert report["gateway_report_sha256"]
    assert report["probe_mp4_sha256"]
    assert [item["timestamp_seconds"] for item in report["frame_evidence"]] == list(
        PROBE_FRAME_TIMESTAMPS
    )
    assert all(item["sha256"] for item in report["frame_evidence"])

    repeated = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    assert repeated == report
    assert len(fake_video_client.calls) == 1


def test_probe_dry_run_makes_zero_provider_calls(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
):
    successful_renderer(fake_video_client)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=False
    )

    assert report["capability"] == "inconclusive"
    assert report["success"] is False
    assert report["executed"] is False
    assert fake_video_client.calls == []
    assert not plan.audio_probe_path.exists()


def test_corrupt_existing_probe_report_fails_closed_without_resubmission(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
):
    successful_renderer(fake_video_client)
    plan.audio_probe_path.parent.mkdir(parents=True, exist_ok=True)
    plan.audio_probe_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(PetSitcomGenerationError, match="probe report"):
        run_pet_audio_drive_probe(
            plan, video_client=fake_video_client, allow_network=True
        )

    assert fake_video_client.calls == []


def test_probe_rejects_symlinked_tests_parent_before_provider_submission(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
    tmp_path,
):
    successful_renderer(fake_video_client)
    escaped = tmp_path / "escaped-probe-artifacts"
    escaped.mkdir()
    (plan.output_dir / "tests").symlink_to(escaped, target_is_directory=True)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        run_pet_audio_drive_probe(
            plan, video_client=fake_video_client, allow_network=True
        )

    assert fake_video_client.calls == []
    assert list(escaped.iterdir()) == []


def test_probe_atomic_json_uses_unique_temp_names_without_following_fixed_symlinks(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
    tmp_path,
):
    successful_renderer(fake_video_client)
    probe_dir = plan.output_dir / "tests" / "audio_drive_probe"
    probe_dir.mkdir(parents=True)
    fixed_temps = (
        plan.audio_probe_path.with_name(".audio_probe.json.tmp"),
        plan.audio_probe_review_path.with_name(".audio_probe_review.json.tmp"),
        probe_dir / ".shot_04.gateway.json.tmp",
    )
    sentinels = []
    for index, fixed_temp in enumerate(fixed_temps, start=1):
        sentinel = tmp_path / f"outside-sentinel-{index}.json"
        sentinel.write_text(f"sentinel-{index}", encoding="utf-8")
        fixed_temp.symlink_to(sentinel)
        sentinels.append(sentinel)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )

    assert report["capability"] == "supported"
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "sentinel-1",
        "sentinel-2",
        "sentinel-3",
    ]
    assert all(path.is_symlink() for path in fixed_temps)


def test_probe_rejects_symlinked_frame_destination_before_provider_submission(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    successful_renderer,
    tmp_path,
):
    successful_renderer(fake_video_client)
    frame_dir = plan.output_dir / "tests" / "audio_drive_probe" / "frames"
    frame_dir.mkdir(parents=True)
    escaped_frame = tmp_path / "escaped-frame.png"
    Image.new("RGB", (8, 8), (200, 10, 10)).save(
        escaped_frame, format="PNG"
    )
    original_hash = _sha256(escaped_frame)
    (frame_dir / "frame_01.png").symlink_to(escaped_frame)

    with pytest.raises(PetSitcomGenerationError, match="symlink"):
        run_pet_audio_drive_probe(
            plan, video_client=fake_video_client, allow_network=True
        )

    assert fake_video_client.calls == []
    assert _sha256(escaped_frame) == original_hash


def test_production_gate_rejects_provider_success_without_manual_review(
    plan, successful_probe
):
    with pytest.raises(PetSitcomGenerationError, match="approved audio-drive probe"):
        require_approved_pet_audio_probe(plan)


def test_probe_review_is_hash_bound(plan, successful_probe):
    _approve_review(plan)
    approved = require_approved_pet_audio_probe(plan)
    assert approved["approved"] is True

    probe_video = Path(
        json.loads(successful_probe.read_text(encoding="utf-8"))[
            "probe_mp4_path"
        ]
    )
    probe_video.write_bytes(probe_video.read_bytes() + b"changed")
    with pytest.raises(PetSitcomGenerationError, match="hash"):
        require_approved_pet_audio_probe(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("reference_audio_accepted", False),
        ("mouth_onset_seconds", 0.91),
        ("mouth_offset_seconds", 1.39),
    ),
)
def test_probe_review_requires_all_gates_and_timing_within_quarter_second(
    plan, successful_probe, field, value
):
    review = _approve_review(plan)
    review[field] = value
    plan.audio_probe_review_path.write_text(
        json.dumps(review), encoding="utf-8"
    )

    with pytest.raises(PetSitcomGenerationError, match="approved audio-drive probe"):
        require_approved_pet_audio_probe(plan)


def test_ambiguous_submission_is_inconclusive_and_never_retried(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    monkeypatch,
):
    def ambiguous(prompt, output_path, video_client, report_path, **kwargs):
        fake_video_client.calls.append({"audio": kwargs["audio"]})
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(
            json.dumps(
                {
                    "task_id": "safe-task-id",
                    "video_url": "https://private.example/a?token=secret",
                }
            ),
            encoding="utf-8",
        )
        raise URLError("ambiguous https://private.example/a?token=secret")

    monkeypatch.setattr(audio_probe, "render_gateway_video_single", ambiguous)

    first = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    second = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )

    assert first["capability"] == "inconclusive"
    assert first["success"] is False
    assert first["task_id"] == "safe-task-id"
    assert second == first
    assert len(fake_video_client.calls) == 1
    serialized = plan.audio_probe_path.read_text(encoding="utf-8")
    assert "private.example" not in serialized
    assert "video-secret" not in serialized


def test_ambiguous_submission_without_task_id_uses_explicit_unavailable_state(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    monkeypatch,
):
    def ambiguous(*args, **kwargs):
        fake_video_client.calls.append({})
        raise URLError("submission outcome unknown")

    monkeypatch.setattr(audio_probe, "render_gateway_video_single", ambiguous)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )

    assert report["capability"] == "inconclusive"
    assert report["executed"] is True
    assert report["success"] is False
    assert report["task_id"] == "no-durable-task-id"
    assert report["task_id_status"] == "unavailable"
    assert len(fake_video_client.calls) == 1


@pytest.mark.parametrize(
    "malformation",
    (
        "capability_wrong_type",
        "supported_not_executed",
        "unsupported_non_400",
        "unsupported_with_success_artifacts",
        "inconclusive_unsafe_task_id",
        "extra_url",
        "extra_credential",
        "extra_data",
    ),
)
def test_malformed_persisted_outcome_fails_closed_without_resubmission(
    plan,
    fake_video_client,
    successful_probe,
    malformation,
):
    report = json.loads(successful_probe.read_text(encoding="utf-8"))
    success_artifact_keys = (
        "probe_mp4_path",
        "probe_mp4_sha256",
        "frame_evidence",
    )
    if malformation == "capability_wrong_type":
        report["capability"] = ["supported"]
    elif malformation == "supported_not_executed":
        report["executed"] = False
    elif malformation == "unsupported_non_400":
        for key in success_artifact_keys:
            report.pop(key)
        report.update(
            {
                "capability": "unsupported",
                "success": False,
                "http_status_code": 401,
            }
        )
    elif malformation == "unsupported_with_success_artifacts":
        report.update(
            {
                "capability": "unsupported",
                "success": False,
                "http_status_code": 400,
            }
        )
    elif malformation == "inconclusive_unsafe_task_id":
        for key in success_artifact_keys:
            report.pop(key)
        report.update(
            {
                "capability": "inconclusive",
                "success": False,
                "task_id": "https://unsafe.example/task?credential=secret",
                "task_id_status": "durable",
            }
        )
    elif malformation == "extra_url":
        report["video_url"] = "https://unsafe.example/probe.mp4"
    elif malformation == "extra_credential":
        report["credential"] = "secret-value"
    else:
        report["data"] = "data:video/mp4;base64,private"
    successful_probe.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    call_count = len(fake_video_client.calls)

    with pytest.raises(PetSitcomGenerationError, match="probe capability state"):
        run_pet_audio_drive_probe(
            plan, video_client=fake_video_client, allow_network=True
        )
    assert len(fake_video_client.calls) == call_count

    with pytest.raises(PetSitcomGenerationError, match="approved audio-drive probe"):
        require_approved_pet_audio_probe(plan)


def test_http_400_is_persisted_as_unsupported_without_retry(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    monkeypatch,
):
    def unsupported(*args, **kwargs):
        fake_video_client.calls.append({})
        raise GatewayVideoHTTPError("reference audio rejected", status_code=400)

    monkeypatch.setattr(audio_probe, "render_gateway_video_single", unsupported)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    repeated = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )

    assert report == {
        **report,
        "capability": "unsupported",
        "success": False,
        "http_status_code": 400,
    }
    assert repeated == report
    assert len(fake_video_client.calls) == 1


def test_http_400_returned_by_gateway_helper_is_read_from_clip_state(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
    monkeypatch,
):
    def rejected(prompt, output_path, video_client, report_path, **kwargs):
        fake_video_client.calls.append({})
        state_path = Path(output_path).with_suffix(".state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "status": "rejected",
                    "http_status_code": 400,
                    "task_id": "",
                }
            ),
            encoding="utf-8",
        )
        Path(report_path).write_text(
            json.dumps(
                {
                    "success": False,
                    "state_path": str(state_path),
                    "errors": [{"error": "reference audio rejected"}],
                }
            ),
            encoding="utf-8",
        )
        return {"success": False}

    monkeypatch.setattr(audio_probe, "render_gateway_video_single", rejected)

    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )

    assert report["capability"] == "unsupported"
    assert report["success"] is False
    assert report["http_status_code"] == 400
    assert len(fake_video_client.calls) == 1


def test_probe_rejects_wrong_model_before_provider_call(
    plan,
    fake_video_client,
    prepared_audio_manifest,
    prepared_references,
):
    fake_video_client.config.model = "wrong-model"

    with pytest.raises(PetSitcomGenerationError, match="doubao-seedance-2-0"):
        run_pet_audio_drive_probe(
            plan, video_client=fake_video_client, allow_network=True
        )
    assert fake_video_client.calls == []
