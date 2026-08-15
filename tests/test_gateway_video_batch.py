import json
import fcntl
import hashlib
import os
import re
import wave
from pathlib import Path

import pytest

import factory.gateway_video_batch as gateway_batch
from factory.gateway_video import (
    GatewayVideoClient,
    GatewayVideoConfig,
    GatewayVideoError,
    GatewayVideoHTTPError,
    GatewayVideoResult,
    GatewayVideoTask,
)
from factory.gateway_video_batch import (
    GatewayVideoBatchError,
    GatewayVideoJob,
    _job_signature,
    build_gateway_video_jobs,
    render_gateway_video_batch,
    render_gateway_video_single,
)
from tests.media_fixtures import VALID_VIDEO_MP4


MINIMAL_MP4 = VALID_VIDEO_MP4


def _write_wav(path: Path, *, duration: float = 1.0) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\x01\x00\x02\x00" * int(duration * 48000))


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    role_a = tmp_path / "roles/a.png"
    role_b = tmp_path / "roles/b.png"
    role_a.parent.mkdir()
    role_a.write_bytes(b"\x89PNG\r\n\x1a\nrole-a")
    role_b.write_bytes(b"\x89PNG\r\n\x1a\nrole-b")
    handoff = tmp_path / "run/lumenx_handoff.json"
    package = tmp_path / "run/openmontage_package.json"
    handoff.parent.mkdir()
    handoff.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.lumenx-handoff.v1",
                "project_id": "sample",
                "script_like": {
                    "characters": [
                        {
                            "id": "char_a",
                            "reference_image_path": str(role_a),
                            "reference_image_exists": True,
                        },
                        {
                            "id": "char_b",
                            "reference_image_path": str(role_b),
                            "reference_image_exists": True,
                        },
                    ],
                    "frames": [
                        {
                            "id": "frame_001",
                            "character_ids": ["char_a", "char_b"],
                            "video_prompt": "two characters enter the station",
                            "duration": 6,
                        },
                        {
                            "id": "frame_002",
                            "character_ids": ["char_b"],
                            "video_prompt": "close-up of the ticket",
                            "duration": 5,
                        },
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    package.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.openmontage.v1",
                "project_id": "sample",
                "character_assets": {
                    "production_ready": True,
                    "characters": [
                        {
                            "character_id": "char_a",
                            "reference_image_path": str(role_a),
                            "production_ready": True,
                        },
                        {
                            "character_id": "char_b",
                            "reference_image_path": str(role_b),
                            "production_ready": True,
                        },
                    ],
                },
                "target": {"aspect_ratio": "9:16"},
                "timeline": [
                    {
                        "shot_id": "shot_001",
                        "index": 1,
                        "expected_assets": {
                            "video_clip": str(tmp_path / "run/clips/shot_001.mp4")
                        },
                    },
                    {
                        "shot_id": "shot_002",
                        "index": 2,
                        "expected_assets": {
                            "video_clip": str(tmp_path / "run/clips/shot_002.mp4")
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return handoff, package, role_a, role_b


def test_build_jobs_accepts_provider_neutral_video_handoff(tmp_path: Path) -> None:
    handoff, package, _role_a, _role_b = _write_inputs(tmp_path)
    legacy = json.loads(handoff.read_text(encoding="utf-8"))
    neutral = {
        "schema_version": "motion-comic-factory.video-handoff.v1",
        "project_id": legacy["project_id"],
        "characters": legacy["script_like"]["characters"],
        "shots": [
            {
                **frame,
                "index": index,
                "duration_seconds": frame["duration"],
            }
            for index, frame in enumerate(legacy["script_like"]["frames"], start=1)
        ],
    }
    handoff.write_text(json.dumps(neutral), encoding="utf-8")

    jobs = build_gateway_video_jobs(handoff, package)

    assert [job.shot_id for job in jobs] == ["shot_001", "shot_002"]
    assert jobs[0].prompt == "two characters enter the station"


class FakeClient:
    def __init__(self, *, fail=False, base_url="https://gateway.test/v1"):
        self.config = GatewayVideoConfig(
            api_key="batch-secret",
            base_url=base_url,
            model="doubao-seedance-2-0-fast",
        )
        self.calls = []
        self.fail = fail
        self.pending = {}
        self.submit_count = 0
        self.complete_count = 0
        self.completed_task_ids = []

    def validate_reference_images(self, images):
        return None

    def validate_reference_audio(self, audio):
        return None

    def prepare_submission(self, prompt, **kwargs):
        return prompt, kwargs

    def submit_prepared(self, submission, *, allow_network=False):
        prompt, kwargs = submission
        return self.submit(prompt, **kwargs)

    def submit(self, prompt, **kwargs):
        self.submit_count += 1
        task_id = f"task-{self.submit_count}"
        self.calls.append((prompt, None, kwargs))
        if self.fail:
            raise GatewayVideoError("batch-secret was rejected")
        self.pending[task_id] = (prompt, kwargs)
        return GatewayVideoTask(task_id=task_id, status="queued")

    def complete_task(self, task, output_path, **kwargs):
        self.complete_count += 1
        self.completed_task_ids.append(task.task_id)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(MINIMAL_MP4)
        return GatewayVideoResult(
            output_path=str(output),
            model=self.config.model,
            task_id=task.task_id,
            status="completed",
            poll_count=1,
            output_size_bytes=len(MINIMAL_MP4),
            duration_seconds=1.0,
            source_host="cdn.example",
        )


def _single_job(output: Path) -> GatewayVideoJob:
    return GatewayVideoJob(
        shot_id="single",
        index=1,
        prompt="animate one character",
        images=(),
        duration=5,
        ratio="9:16",
        resolution="720p",
        output_path=str(output),
    )


def _submitted_state(
    output: Path,
    client: FakeClient,
    *,
    shot_id: str = "single",
    output_path: str | None = None,
    task_id: object = "task-endpoint-a",
) -> dict[str, object]:
    endpoint_fingerprint = gateway_batch.gateway_endpoint_fingerprint(
        client.config.base_url
    )
    signature = _job_signature(
        _single_job(output),
        model=client.config.model,
        generate_audio=False,
        endpoint_fingerprint=endpoint_fingerprint,
    )
    return {
        "schema_version": gateway_batch.CLIP_STATE_SCHEMA,
        "signature": signature,
        "endpoint_fingerprint_sha256": endpoint_fingerprint,
        "status": "submitted",
        "model": client.config.model,
        "shot_id": shot_id,
        "output_path": output_path or str(output.resolve()),
        "task_id": task_id,
        "task_status": "queued",
    }


    def generate(self, prompt, output_path, **kwargs):
        self.calls.append((prompt, Path(output_path), kwargs))
        if self.fail:
            raise GatewayVideoError("batch-secret was rejected")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(MINIMAL_MP4)
        return GatewayVideoResult(
            output_path=str(output),
            model=self.config.model,
            task_id=f"task-{len(self.calls)}",
            status="completed",
            poll_count=1,
            output_size_bytes=len(MINIMAL_MP4),
            duration_seconds=1.0,
            source_host="cdn.example",
        )


def _completed_state(
    output: Path,
    client: FakeClient,
    *,
    task_id: object = "task-current",
) -> dict[str, object]:
    state = _submitted_state(output, client, task_id=task_id)
    state["status"] = "completed"
    state["output_size_bytes"] = len(MINIMAL_MP4)
    state.pop("task_status")
    return state


def test_build_gateway_video_jobs_maps_frames_roles_and_openmontage_outputs(tmp_path):
    handoff, package, role_a, role_b = _write_inputs(tmp_path)

    jobs = build_gateway_video_jobs(handoff, package, resolution="720p")

    assert len(jobs) == 2
    assert jobs[0].shot_id == "shot_001"
    assert jobs[0].prompt == "two characters enter the station"
    assert jobs[0].images == (str(role_a), str(role_b))
    assert jobs[0].duration == 6
    assert jobs[0].ratio == "9:16"
    assert jobs[0].resolution == "720p"
    assert jobs[0].output_path.endswith("run/clips/shot_001.mp4")
    assert jobs[1].images == (str(role_b),)


def test_build_gateway_video_jobs_preserves_keyframe_and_reference_roles(tmp_path):
    handoff, package, role_a, _role_b = _write_inputs(tmp_path)
    first = tmp_path / "run/frames/shot_001_first.png"
    last = tmp_path / "run/frames/shot_001_last.png"
    first.parent.mkdir(exist_ok=True)
    first.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    last.write_bytes(b"\x89PNG\r\n\x1a\nlast")
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["timeline"][0]["expected_assets"].update(
        {"first_frame": str(first), "last_frame": str(last)}
    )
    package.write_text(json.dumps(payload), encoding="utf-8")

    job = build_gateway_video_jobs(handoff, package)[0]

    assert job.images == (str(first), str(last), str(role_a), str(tmp_path / "roles/b.png"))
    assert job.image_roles == (
        "first_frame",
        "last_frame",
        "reference_image",
        "reference_image",
    )


def test_gateway_video_batch_passes_image_roles_to_provider(tmp_path):
    handoff, package, _role_a, _role_b = _write_inputs(tmp_path)
    first = tmp_path / "run/frames/shot_001_first.png"
    first.parent.mkdir(exist_ok=True)
    first.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["timeline"][0]["expected_assets"]["first_frame"] = str(first)
    package.write_text(json.dumps(payload), encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "report.json",
        limit=1,
        allow_network=True,
    )

    assert report["success"] is True
    assert client.calls[0][2]["image_roles"] == [
        "first_frame",
        "reference_image",
        "reference_image",
    ]


def test_gateway_video_batch_binds_each_job_to_its_shot_audio(tmp_path):
    handoff, package, _role_a, _role_b = _write_inputs(tmp_path)
    audio = tmp_path / "run/audio/shot_001.wav"
    audio.parent.mkdir(exist_ok=True)
    _write_wav(audio)
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["timeline"][0]["expected_assets"]["voice_audio"] = str(audio)
    package.write_text(json.dumps(payload), encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "report.json",
        limit=1,
        allow_network=True,
    )

    assert report["success"] is True
    assert client.calls[0][2]["audio"] == str(audio)
    state = json.loads(
        (tmp_path / "run/clips/shot_001.mp4.gateway.json").read_text(encoding="utf-8")
    )
    assert state["reference_audio_sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()


def test_build_gateway_video_jobs_rejects_duplicate_output_paths(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][1]["expected_assets"]["video_clip"] = data["timeline"][0][
        "expected_assets"
    ]["video_clip"]
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Duplicate OpenMontage video output path"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_non_mp4_output_paths(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][0]["expected_assets"]["video_clip"] = str(
        tmp_path / "run/clips/shot_001.json"
    )
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="must use an .mp4 path"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_reports_invalid_timeline_index(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][0]["index"] = "not-a-number"
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Invalid OpenMontage timeline index"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_duplicate_timeline_indexes(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][1]["index"] = 1
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Duplicate OpenMontage timeline index: 1"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_duplicate_shot_ids(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][1]["shot_id"] = data["timeline"][0]["shot_id"]
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Duplicate OpenMontage shot ID"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_duplicate_handoff_character_ids(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(handoff.read_text(encoding="utf-8"))
    data["script_like"]["characters"].append(
        dict(data["script_like"]["characters"][0])
    )
    handoff.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Duplicate LumenX character ID"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_duplicate_package_character_ids(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["character_assets"]["characters"].append(
        dict(data["character_assets"]["characters"][0])
    )
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Duplicate OpenMontage character ID"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_invalid_character_reference_data(tmp_path):
    handoff, package, role_a, _ = _write_inputs(tmp_path)
    role_a.write_bytes(b"not-a-real-png")

    with pytest.raises(
        GatewayVideoBatchError,
        match="Invalid production character reference.*char_a",
    ):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_reports_infinite_duration(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(handoff.read_text(encoding="utf-8"))
    data["script_like"]["frames"][0]["duration"] = "inf"
    handoff.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Invalid gateway video duration"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_does_not_replace_explicit_zero_duration(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(handoff.read_text(encoding="utf-8"))
    data["script_like"]["frames"][0]["duration"] = 0
    handoff.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="between 1 and 3600"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_fractional_timeline_index(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"][0]["index"] = 1.5
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Invalid OpenMontage timeline index"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_non_object_timeline_item(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["timeline"].append("not-a-shot")
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="timeline item 3 must be an object"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_empty_aspect_ratio(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["target"]["aspect_ratio"] = " "
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="aspect ratio is empty"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_non_list_character_ids(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(handoff.read_text(encoding="utf-8"))
    data["script_like"]["frames"][0]["character_ids"] = "char_a"
    handoff.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="character_ids must be a list"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_mismatched_projects(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["project_id"] = "different-project"
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="project IDs do not match"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_requires_production_ready_package_assets(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(package.read_text(encoding="utf-8"))
    data["character_assets"]["production_ready"] = False
    package.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="not production-ready"):
        build_gateway_video_jobs(handoff, package)


def test_build_gateway_video_jobs_rejects_unsupported_input_schema(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    data = json.loads(handoff.read_text(encoding="utf-8"))
    data["schema_version"] = "unexpected.v9"
    handoff.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GatewayVideoBatchError, match="Unsupported video handoff schema"):
        build_gateway_video_jobs(handoff, package)


def test_gateway_video_batch_dry_run_plans_without_calling_provider(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    client = FakeClient()
    report_path = tmp_path / "run/gateway_video_batch.json"

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        report_path,
        limit=1,
        allow_network=False,
    )

    assert client.calls == []
    assert report["executed"] is False
    assert report["success"] is False
    assert report["plan_ready"] is True
    assert report["planned_count"] == 1
    assert report["blocked_reasons"] == ["Live gateway video generation is disabled."]
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_gateway_video_batch_dry_run_rejects_unsupported_model_settings(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    client = FakeClient()

    with pytest.raises(GatewayVideoBatchError, match="Fast supports 480p or 720p"):
        render_gateway_video_batch(
            handoff,
            package,
            client,
            tmp_path / "run/gateway_video_batch.json",
            limit=1,
            resolution="1080p",
            allow_network=False,
        )

    assert client.submit_count == 0


def test_gateway_video_batch_generates_clips_for_openmontage(tmp_path):
    handoff, package, role_a, role_b = _write_inputs(tmp_path)
    client = FakeClient()
    report_path = tmp_path / "run/gateway_video_batch.json"

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        report_path,
        allow_network=True,
    )

    assert report["success"] is True
    assert report["executed"] is True
    assert report["completed_count"] == 2
    assert [call[0] for call in client.calls] == [
        "two characters enter the station",
        "close-up of the ticket",
    ]
    assert client.calls[0][2]["images"] == [str(role_a), str(role_b)]
    assert client.calls[0][2]["allow_network"] is True
    assert (tmp_path / "run/clips/shot_001.mp4").read_bytes() == MINIMAL_MP4
    assert (tmp_path / "run/clips/shot_002.mp4").read_bytes() == MINIMAL_MP4
    assert "batch-secret" not in report_path.read_text(encoding="utf-8")


def test_gateway_video_batch_stops_on_first_failure_and_sanitizes_report(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    client = FakeClient(fail=True)
    report_path = tmp_path / "run/gateway_video_batch.json"

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        report_path,
        allow_network=True,
    )

    assert report["success"] is False
    assert report["failed_count"] == 1
    assert len(client.calls) == 1
    assert "batch-secret" not in report_path.read_text(encoding="utf-8")


def test_gateway_video_batch_resumes_without_regenerating_existing_clips(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    existing = tmp_path / "run/clips/shot_001.mp4"
    initial_client = FakeClient()
    initial_report = render_gateway_video_batch(
        handoff,
        package,
        initial_client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
    )
    assert initial_report["success"] is True
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
    )

    assert report["success"] is True
    assert report["skipped_count"] == 1
    assert report["completed_count"] == 1
    assert [call[0] for call in client.calls] == ["close-up of the ticket"]
    assert existing.read_bytes() == MINIMAL_MP4
    assert report["results"][0]["status"] == "skipped_existing"


def test_gateway_video_batch_repair_submits_only_changed_shot(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    first_client = FakeClient()
    first = render_gateway_video_batch(
        handoff,
        package,
        first_client,
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
        replace_stale=True,
    )
    unchanged = tmp_path / "run/clips/shot_001.mp4"
    unchanged_state = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    unchanged_bytes = unchanged.read_bytes()
    unchanged_state_bytes = unchanged_state.read_bytes()
    handoff_payload = json.loads(handoff.read_text(encoding="utf-8"))
    handoff_payload["script_like"]["frames"][1][
        "video_prompt"
    ] = "revised ticket close-up"
    handoff.write_text(json.dumps(handoff_payload), encoding="utf-8")
    repair_client = FakeClient()

    repaired = render_gateway_video_batch(
        handoff,
        package,
        repair_client,
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
        replace_stale=True,
        repair_shot_ids=("shot_002",),
    )

    assert first["completed_count"] == 2
    assert [call[0] for call in repair_client.calls] == ["revised ticket close-up"]
    assert repaired["skipped_count"] == 1
    assert repaired["completed_count"] == 1
    assert unchanged.read_bytes() == unchanged_bytes
    assert unchanged_state.read_bytes() == unchanged_state_bytes


def test_gateway_video_batch_repair_rejects_unusable_preserved_job_before_submit(
    tmp_path,
):
    handoff, package, _, _ = _write_inputs(tmp_path)
    first = render_gateway_video_batch(
        handoff,
        package,
        FakeClient(),
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
        replace_stale=True,
    )
    assert first["success"] is True
    preserved_state = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    preserved_state.write_text('{"status":"corrupt"}', encoding="utf-8")
    handoff_payload = json.loads(handoff.read_text(encoding="utf-8"))
    handoff_payload["script_like"]["frames"][1][
        "video_prompt"
    ] = "revised ticket close-up"
    handoff.write_text(json.dumps(handoff_payload), encoding="utf-8")
    repair_client = FakeClient()

    repaired = render_gateway_video_batch(
        handoff,
        package,
        repair_client,
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
        replace_stale=True,
        repair_shot_ids=("shot_002",),
    )

    assert repaired["success"] is False
    assert repaired["failed_count"] == 1
    assert repaired["errors"][0]["shot_id"] == "shot_001"
    assert repair_client.calls == []


def test_gateway_video_batch_overwrite_regenerates_existing_clips(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    existing = tmp_path / "run/clips/shot_001.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing-video")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        allow_network=True,
        overwrite=True,
    )

    assert report["success"] is True
    assert report["skipped_count"] == 0
    assert report["completed_count"] == 2
    assert len(client.calls) == 2
    assert existing.read_bytes() == MINIMAL_MP4


def test_gateway_video_batch_replace_stale_rebuilds_stale_state(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"status": "rejected"}', encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
        replace_stale=True,
    )

    assert report["success"] is True
    assert report["replace_stale"] is True
    assert client.submit_count == 1
    assert client.complete_count == 1


def test_gateway_video_batch_rejects_overwrite_with_replace_stale(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"

    with pytest.raises(GatewayVideoBatchError, match="mutually exclusive"):
        render_gateway_video_batch(
            handoff,
            package,
            FakeClient(),
            report_path,
            limit=1,
            allow_network=True,
            overwrite=True,
            replace_stale=True,
        )

    assert not report_path.exists()


def test_gateway_video_single_rejects_overwrite_with_replace_stale(tmp_path):
    report_path = tmp_path / "single-report.json"

    with pytest.raises(GatewayVideoBatchError, match="mutually exclusive"):
        render_gateway_video_single(
            "animate one character",
            tmp_path / "single.mp4",
            FakeClient(),
            report_path,
            allow_network=True,
            overwrite=True,
            replace_stale=True,
        )

    assert not report_path.exists()


def test_gateway_video_batch_rejects_invalid_output_parent_before_paid_submit(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_bytes(b"occupied")
    package_data = json.loads(package.read_text(encoding="utf-8"))
    package_data["timeline"][0]["expected_assets"]["video_clip"] = str(
        blocked_parent / "shot_001.mp4"
    )
    package.write_text(json.dumps(package_data), encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
    )

    assert report["success"] is False
    assert report["executed"] is False
    assert client.submit_count == 0
    assert "output directory" in report["errors"][0]["error"]


def test_gateway_video_batch_rejects_report_path_that_would_overwrite_an_input(
    tmp_path,
):
    handoff, package, _, _ = _write_inputs(tmp_path)
    original_handoff = handoff.read_bytes()

    with pytest.raises(GatewayVideoBatchError, match="report path conflicts"):
        render_gateway_video_batch(
            handoff,
            package,
            FakeClient(),
            handoff,
            limit=1,
        )

    assert handoff.read_bytes() == original_handoff


def test_gateway_video_batch_rejects_invalid_report_parent_before_paid_submit(
    tmp_path,
):
    handoff, package, _, _ = _write_inputs(tmp_path)
    blocked_parent = tmp_path / "report-parent"
    blocked_parent.write_bytes(b"occupied")
    client = FakeClient()

    with pytest.raises(GatewayVideoBatchError, match="report directory"):
        render_gateway_video_batch(
            handoff,
            package,
            client,
            blocked_parent / "gateway-video.json",
            limit=1,
            allow_network=True,
        )

    assert client.submit_count == 0


def test_gateway_video_batch_rejects_directory_output_before_paid_submit(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    output_directory = tmp_path / "run/directory-output.mp4"
    output_directory.mkdir()
    package_data = json.loads(package.read_text(encoding="utf-8"))
    package_data["timeline"][0]["expected_assets"]["video_clip"] = str(
        output_directory
    )
    package.write_text(json.dumps(package_data), encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
        overwrite=True,
    )

    assert report["success"] is False
    assert report["executed"] is False
    assert client.submit_count == 0
    assert "output path is not a file" in report["errors"][0]["error"]


def test_gateway_video_batch_does_not_reuse_clips_from_a_different_model(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"
    fast_client = FakeClient()
    first_report = render_gateway_video_batch(
        handoff,
        package,
        fast_client,
        report_path,
        limit=1,
        allow_network=True,
    )
    assert first_report["success"] is True

    standard_client = FakeClient()
    standard_client.config = GatewayVideoConfig(
        api_key="batch-secret",
        base_url="https://gateway.test/v1",
        model="doubao-seedance-2-0",
    )
    second_report = render_gateway_video_batch(
        handoff,
        package,
        standard_client,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert second_report["success"] is False
    assert standard_client.calls == []
    assert "--overwrite" in second_report["errors"][0]["error"]


def test_gateway_endpoint_fingerprint_excludes_sensitive_url_components():
    clean = gateway_batch.gateway_endpoint_fingerprint(
        "https://gateway.example/v1"
    )
    sensitive = gateway_batch.gateway_endpoint_fingerprint(
        "https://user:password@GATEWAY.EXAMPLE:443/v1/"
        "?token=query-secret#fragment-secret"
    )

    assert sensitive == clean
    assert sensitive != gateway_batch.gateway_endpoint_fingerprint(
        "https://gateway.example/v2"
    )
    assert sensitive != gateway_batch.gateway_endpoint_fingerprint(
        "https://other.example/v1"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", sensitive)
    assert all(
        value not in sensitive
        for value in (
            "gateway.example",
            "user",
            "password",
            "query-secret",
            "fragment-secret",
        )
    )


def test_gateway_job_signature_is_bound_to_endpoint_fingerprint(tmp_path):
    job = _single_job(tmp_path / "single.mp4")

    endpoint_a = _job_signature(
        job,
        model="doubao-seedance-2-0-fast",
        generate_audio=False,
        endpoint_fingerprint=gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway-a.example/v1"
        ),
    )
    endpoint_b = _job_signature(
        job,
        model="doubao-seedance-2-0-fast",
        generate_audio=False,
        endpoint_fingerprint=gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway-b.example/v1"
        ),
    )

    assert endpoint_a != endpoint_b


def test_gateway_video_batch_never_resumes_task_from_different_endpoint(
    tmp_path,
):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"

    class InterruptedClient(FakeClient):
        def complete_task(self, task, output_path, **kwargs):
            self.complete_count += 1
            self.completed_task_ids.append(task.task_id)
            raise GatewayVideoError("poll interrupted")

    endpoint_a = InterruptedClient(
        base_url="https://gateway-a.example/v1"
    )
    first = render_gateway_video_batch(
        handoff,
        package,
        endpoint_a,
        report_path,
        limit=1,
        allow_network=True,
    )
    assert first["success"] is False
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    endpoint_a_task_id = json.loads(
        state_path.read_text(encoding="utf-8")
    )["task_id"]

    endpoint_b = FakeClient(
        base_url="https://gateway-b.example/v1"
    )
    second = render_gateway_video_batch(
        handoff,
        package,
        endpoint_b,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert second["success"] is False
    assert endpoint_b.submit_count == 0
    assert endpoint_b.complete_count == 0
    assert endpoint_a_task_id not in endpoint_b.completed_task_ids
    assert "--overwrite" in second["errors"][0]["error"]


def test_lock_time_state_from_other_endpoint_is_replaced_not_resumed(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"
    endpoint_a = FakeClient(
        base_url="https://gateway-a.example/v1"
    )
    endpoint_b = FakeClient(
        base_url="https://gateway-b.example/v1"
    )
    endpoint_a_state = _submitted_state(output, endpoint_a)
    original_acquire = gateway_batch._acquire_clip_lock
    injected = False

    def acquire_and_inject_state(current_output):
        nonlocal injected
        descriptor = original_acquire(current_output)
        if not injected:
            injected = True
            gateway_batch.write_atomic_json(
                gateway_batch._clip_state_path(Path(current_output)),
                endpoint_a_state,
            )
        return descriptor

    monkeypatch.setattr(
        gateway_batch,
        "_acquire_clip_lock",
        acquire_and_inject_state,
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        endpoint_b,
        report_path,
        allow_network=True,
        overwrite=True,
    )

    assert report["success"] is True
    assert endpoint_b.submit_count == 1
    assert endpoint_b.completed_task_ids == ["task-1"]
    assert "task-endpoint-a" not in endpoint_b.completed_task_ids
    state = json.loads(
        gateway_batch._clip_state_path(output).read_text(encoding="utf-8")
    )
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(endpoint_b.config.base_url)
    )


def test_replace_stale_resumes_current_submitted_state_seen_at_lock(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "single.mp4"
    state_path = gateway_batch._clip_state_path(output)
    state_path.write_text('{"status": "rejected"}', encoding="utf-8")
    client = FakeClient()
    current_state = _submitted_state(
        output,
        client,
        task_id="task-current",
    )
    original_acquire = gateway_batch._acquire_clip_lock
    injected = False

    def acquire_and_inject_state(current_output):
        nonlocal injected
        descriptor = original_acquire(current_output)
        if not injected:
            injected = True
            gateway_batch.write_atomic_json(state_path, current_state)
        return descriptor

    monkeypatch.setattr(
        gateway_batch,
        "_acquire_clip_lock",
        acquire_and_inject_state,
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        replace_stale=True,
    )

    assert report["success"] is True
    assert report["resumed_count"] == 1
    assert client.submit_count == 0
    assert client.completed_task_ids == ["task-current"]


def test_replace_stale_skips_current_completed_state_seen_at_lock(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "single.mp4"
    state_path = gateway_batch._clip_state_path(output)
    state_path.write_text('{"status": "rejected"}', encoding="utf-8")
    client = FakeClient()
    current_state = _completed_state(output, client)
    original_acquire = gateway_batch._acquire_clip_lock
    injected = False

    def acquire_and_inject_state(current_output):
        nonlocal injected
        descriptor = original_acquire(current_output)
        if not injected:
            injected = True
            output.write_bytes(MINIMAL_MP4)
            gateway_batch.write_atomic_json(state_path, current_state)
        return descriptor

    monkeypatch.setattr(
        gateway_batch,
        "_acquire_clip_lock",
        acquire_and_inject_state,
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        replace_stale=True,
    )

    assert report["success"] is True
    assert report["skipped_count"] == 1
    assert report["executed"] is False
    assert client.submit_count == 0
    assert client.complete_count == 0


def test_replace_stale_submits_when_state_is_still_stale_at_lock(tmp_path):
    output = tmp_path / "single.mp4"
    state_path = gateway_batch._clip_state_path(output)
    state_path.write_text('{"status": "rejected"}', encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        replace_stale=True,
    )

    assert report["success"] is True
    assert report["replace_stale"] is True
    assert report["resumed_count"] == 0
    assert client.submit_count == 1
    assert client.complete_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("shot_id", "other-shot"),
        ("output_path", "/tmp/outside-candidate.mp4"),
        ("task_id", "../malicious\nid"),
    ),
)
def test_gateway_video_single_rejects_unsafe_submitted_state_identity(
    tmp_path,
    field,
    value,
):
    output = tmp_path / "single.mp4"
    client = FakeClient()
    state = _submitted_state(output, client)
    state[field] = value
    state_path = gateway_batch._clip_state_path(output)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert client.complete_count == 0
    assert "--overwrite" in report["errors"][0]["error"]


def test_gateway_video_single_treats_legacy_state_without_endpoint_as_stale(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    client = FakeClient()
    state = _submitted_state(output, client)
    state.pop("endpoint_fingerprint_sha256")
    gateway_batch._clip_state_path(output).write_text(
        json.dumps(state),
        encoding="utf-8",
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert client.complete_count == 0
    assert "--overwrite" in report["errors"][0]["error"]


def test_gateway_video_single_submitting_state_is_fail_closed_with_overwrite(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    client = FakeClient()
    state = _submitted_state(output, client)
    state["status"] = "submitting"
    state.pop("task_id")
    state.pop("task_status")
    gateway_batch._clip_state_path(output).write_text(
        json.dumps(state),
        encoding="utf-8",
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        overwrite=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert "ambiguous" in report["errors"][0]["error"]


def test_gateway_video_single_submitting_state_is_fail_closed_with_replace_stale(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    client = FakeClient()
    state = _submitted_state(output, client)
    state["status"] = "submitting"
    state.pop("task_id")
    state.pop("task_status")
    gateway_batch._clip_state_path(output).write_text(
        json.dumps(state),
        encoding="utf-8",
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        replace_stale=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert "ambiguous" in report["errors"][0]["error"]


def test_gateway_video_single_rejects_completed_state_with_wrong_size(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"
    first = render_gateway_video_single(
        "animate one character",
        output,
        FakeClient(),
        report_path,
        allow_network=True,
    )
    assert first["success"] is True
    state_path = gateway_batch._clip_state_path(output)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["output_size_bytes"] += 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    retry = FakeClient()

    second = render_gateway_video_single(
        "animate one character",
        output,
        retry,
        report_path,
        allow_network=True,
    )

    assert second["success"] is False
    assert second["skipped_count"] == 0
    assert retry.submit_count == 0
    assert "--overwrite" in second["errors"][0]["error"]


def test_gateway_video_single_overwrite_rebuilds_corrupt_completed_output(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"
    first = render_gateway_video_single(
        "animate one character",
        output,
        FakeClient(),
        report_path,
        allow_network=True,
    )
    assert first["success"] is True
    output.write_bytes(b"corrupt")
    replacement = FakeClient()

    second = render_gateway_video_single(
        "animate one character",
        output,
        replacement,
        report_path,
        allow_network=True,
        overwrite=True,
    )

    assert second["success"] is True
    assert replacement.submit_count == 1
    assert output.read_bytes() == MINIMAL_MP4


def test_gateway_video_single_rejects_symlink_output_without_touching_target(
    tmp_path,
):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside-sentinel")
    output = tmp_path / "single.mp4"
    output.symlink_to(outside)
    client = FakeClient()

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
        overwrite=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert outside.read_bytes() == b"outside-sentinel"


def test_gateway_state_persists_only_safe_endpoint_fingerprint(tmp_path):
    output = tmp_path / "single.mp4"
    sensitive_url = (
        "https://url-user:url-password@gateway.example:443/v1/"
        "?token=query-secret#fragment-secret"
    )

    class InterruptedClient(FakeClient):
        def complete_task(self, task, output_path, **kwargs):
            raise GatewayVideoError("poll interrupted")

    client = InterruptedClient(base_url=sensitive_url)
    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        allow_network=True,
    )

    assert report["success"] is False
    state_text = gateway_batch._clip_state_path(output).read_text(
        encoding="utf-8"
    )
    state = json.loads(state_text)
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway.example/v1"
        )
    )
    assert all(
        secret not in state_text
        for secret in (
            "batch-secret",
            "url-user",
            "url-password",
            "query-secret",
            "fragment-secret",
            sensitive_url,
        )
    )


def test_gateway_video_batch_does_not_skip_a_corrupted_completed_clip(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"
    first_report = render_gateway_video_batch(
        handoff,
        package,
        FakeClient(),
        report_path,
        limit=1,
        allow_network=True,
    )
    assert first_report["success"] is True
    clip = tmp_path / "run/clips/shot_001.mp4"
    clip.write_bytes(b"truncated")

    client = FakeClient()
    second_report = render_gateway_video_batch(
        handoff,
        package,
        client,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert second_report["success"] is False
    assert client.calls == []
    assert "valid MP4" in second_report["errors"][0]["error"]


def test_gateway_video_batch_resumes_submitted_task_without_duplicate_charge(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"

    class InterruptedClient(FakeClient):
        def generate(self, *args, **kwargs):
            raise GatewayVideoError("legacy generate path must not be used")

        def complete_task(self, task, output_path, **kwargs):
            self.complete_count += 1
            raise GatewayVideoError("poll interrupted")

    interrupted = InterruptedClient()
    first_report = render_gateway_video_batch(
        handoff,
        package,
        interrupted,
        report_path,
        limit=1,
        allow_network=True,
    )
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert first_report["success"] is False
    assert interrupted.submit_count == 1
    assert state["status"] == "submitted"
    assert state["task_id"] == "task-1"
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(
            interrupted.config.base_url
        )
    )

    resumed = FakeClient()
    second_report = render_gateway_video_batch(
        handoff,
        package,
        resumed,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert second_report["success"] is True
    assert second_report["resumed_count"] == 1
    assert resumed.submit_count == 0
    assert resumed.complete_count == 1


def test_gateway_video_batch_overwrite_submits_a_fresh_task_instead_of_resuming(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"

    class InterruptedClient(FakeClient):
        def complete_task(self, task, output_path, **kwargs):
            self.complete_count += 1
            raise GatewayVideoError("poll interrupted")

    interrupted = InterruptedClient()
    first_report = render_gateway_video_batch(
        handoff,
        package,
        interrupted,
        report_path,
        limit=1,
        allow_network=True,
    )
    assert first_report["success"] is False
    assert interrupted.submit_count == 1

    replacement = FakeClient()
    second_report = render_gateway_video_batch(
        handoff,
        package,
        replacement,
        report_path,
        limit=1,
        allow_network=True,
        overwrite=True,
    )

    assert second_report["success"] is True
    assert second_report["resumed_count"] == 0
    assert replacement.submit_count == 1
    assert replacement.complete_count == 1


def test_gateway_video_batch_blocks_corrupt_state_before_paid_submit(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"status":"submitted",', encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
    )

    assert report["success"] is False
    assert report["failed_count"] == 1
    assert client.submit_count == 0
    assert "state" in report["errors"][0]["error"].lower()
    assert "--overwrite" in report["errors"][0]["error"]


def test_gateway_video_batch_blocks_empty_state_before_paid_submit(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{}", encoding="utf-8")
    client = FakeClient()

    report = render_gateway_video_batch(
        handoff,
        package,
        client,
        tmp_path / "run/gateway_video_batch.json",
        limit=1,
        allow_network=True,
    )

    assert report["success"] is False
    assert client.submit_count == 0
    assert "--overwrite" in report["errors"][0]["error"]


def test_gateway_video_batch_does_not_persist_or_resume_signed_immediate_url(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"
    signed_url = "https://cdn.example/clip.mp4?signature=private"

    class ImmediateInterruptedClient(FakeClient):
        def submit(self, prompt, **kwargs):
            self.submit_count += 1
            self.calls.append((prompt, None, kwargs))
            return GatewayVideoTask(
                task_id="",
                status="completed",
                video_url=signed_url,
            )

        def complete_task(self, task, output_path, **kwargs):
            self.complete_count += 1
            raise GatewayVideoError("download interrupted")

    interrupted = ImmediateInterruptedClient()
    first_report = render_gateway_video_batch(
        handoff,
        package,
        interrupted,
        report_path,
        limit=1,
        allow_network=True,
    )
    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first_report["success"] is False
    assert state["status"] == "submitted"
    assert "video_url" not in state
    assert signed_url not in json.dumps(state)

    resumed = FakeClient()
    second_report = render_gateway_video_batch(
        handoff,
        package,
        resumed,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert second_report["success"] is False
    assert second_report["resumed_count"] == 0
    assert resumed.submit_count == 0
    assert resumed.complete_count == 0
    assert "--overwrite" in second_report["errors"][0]["error"]


def test_gateway_video_batch_blocks_when_another_process_holds_clip_lock(tmp_path):
    handoff, package, _, _ = _write_inputs(tmp_path)
    lock_path = tmp_path / "run/clips/shot_001.mp4.gateway.lock"
    lock_path.parent.mkdir(parents=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = FakeClient()
    try:
        report = render_gateway_video_batch(
            handoff,
            package,
            client,
            tmp_path / "run/gateway_video_batch.json",
            limit=1,
            allow_network=True,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    assert report["success"] is False
    assert report["executed"] is False
    assert client.submit_count == 0
    assert "another process" in report["errors"][0]["error"]


def test_gateway_video_batch_leaves_submitting_claim_after_ambiguous_interrupt(
    tmp_path,
):
    handoff, package, _, _ = _write_inputs(tmp_path)
    report_path = tmp_path / "run/gateway_video_batch.json"

    class AmbiguousSubmitClient(FakeClient):
        def submit(self, prompt, **kwargs):
            self.submit_count += 1
            raise KeyboardInterrupt

    interrupted = AmbiguousSubmitClient()
    with pytest.raises(KeyboardInterrupt):
        render_gateway_video_batch(
            handoff,
            package,
            interrupted,
            report_path,
            limit=1,
            allow_network=True,
        )

    state_path = tmp_path / "run/clips/shot_001.mp4.gateway.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "submitting"
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(
            interrupted.config.base_url
        )
    )

    retry = FakeClient()
    retry_report = render_gateway_video_batch(
        handoff,
        package,
        retry,
        report_path,
        limit=1,
        allow_network=True,
    )

    assert retry_report["success"] is False
    assert retry.submit_count == 0
    assert "ambiguous" in retry_report["errors"][0]["error"]
    assert "manual resolution" in retry_report["errors"][0]["error"]


def test_gateway_video_single_marks_explicit_http_rejection_as_non_ambiguous(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"

    class RejectedClient(FakeClient):
        def submit(self, prompt, **kwargs):
            self.submit_count += 1
            raise GatewayVideoHTTPError(
                "Gateway video submit failed with HTTP 400.",
                status_code=400,
            )

    report = render_gateway_video_single(
        "animate one character",
        output,
        RejectedClient(),
        report_path,
        allow_network=True,
    )

    state = json.loads(
        output.with_suffix(".mp4.gateway.json").read_text(encoding="utf-8")
    )
    assert report["success"] is False
    assert report["failed_count"] == 1
    assert state["status"] == "rejected"
    assert state["http_status_code"] == 400
    assert state["task_id"] == ""
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway.test/v1"
        )
    )


def test_gateway_video_single_resumes_submitted_task_without_duplicate_charge(tmp_path):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"

    class InterruptedClient(FakeClient):
        def complete_task(self, task, output_path, **kwargs):
            self.complete_count += 1
            raise GatewayVideoError("poll interrupted")

    interrupted = InterruptedClient()
    first_report = render_gateway_video_single(
        "animate one character",
        output,
        interrupted,
        report_path,
        images=[reference],
        allow_network=True,
    )

    assert first_report["success"] is False
    assert interrupted.submit_count == 1
    assert json.loads(
        output.with_suffix(".mp4.gateway.json").read_text(encoding="utf-8")
    )["task_id"] == "task-1"

    resumed = FakeClient()
    second_report = render_gateway_video_single(
        "animate one character",
        output,
        resumed,
        report_path,
        images=[reference],
        allow_network=True,
    )

    assert second_report["success"] is True
    assert second_report["resumed_count"] == 1
    assert resumed.submit_count == 0
    assert resumed.complete_count == 1


def test_gateway_video_resume_restores_persisted_job_settings(tmp_path):
    output = tmp_path / "single.mp4"
    report_path = tmp_path / "single-report.json"

    class InterruptedClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.config = GatewayVideoConfig(
                api_key="batch-secret",
                base_url="https://gateway.test/v1",
                model="MiniMax-H3",
            )

        def complete_task(self, task, output_path, **kwargs):
            raise GatewayVideoError("poll interrupted")

    first = InterruptedClient()
    render_gateway_video_single(
        "animate one character",
        output,
        first,
        report_path,
        duration=4,
        resolution="2K",
        allow_network=True,
    )

    state = json.loads(output.with_suffix(".mp4.gateway.json").read_text("utf-8"))
    assert state["duration"] == 4
    assert state["resolution"] == "2K"
    assert state["reference_image_count"] == 0

    class ResumeClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.config = GatewayVideoConfig(
                api_key="batch-secret",
                base_url="https://gateway.test/v1",
                model="MiniMax-H3",
            )
            self.restored = None

        def restore_task_settings(self, task_id, **settings):
            self.restored = (task_id, settings)

    resumed = ResumeClient()
    report = render_gateway_video_single(
        "animate one character",
        output,
        resumed,
        report_path,
        duration=4,
        resolution="2K",
        allow_network=True,
    )

    assert report["success"] is True
    assert resumed.restored == (
        "task-1",
        {"resolution": "2K", "duration": 4, "image_count": 0},
    )


def test_gateway_video_single_persists_only_local_reference_audio_evidence(tmp_path):
    audio = tmp_path / "drive.wav"
    _write_wav(audio)
    output = tmp_path / "single.mp4"

    report = render_gateway_video_single(
        "animate one character speaking",
        output,
        FakeClient(),
        tmp_path / "single-report.json",
        audio=audio,
        allow_network=True,
    )

    state = json.loads(output.with_suffix(".mp4.gateway.json").read_text("utf-8"))
    state_text = json.dumps(state)
    assert report["success"] is True
    assert state["reference_audio_path"] == str(audio.resolve())
    assert state["reference_audio_sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert state["endpoint_fingerprint_sha256"] == (
        gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway.test/v1"
        )
    )
    assert "data:audio/" not in state_text


def test_gateway_video_single_validates_audio_before_hashing_evidence(
    tmp_path, monkeypatch
):
    audio = tmp_path / "oversized.wav"
    audio.write_bytes(b"x" * 32)
    client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0-fast",
            max_reference_audio_bytes=1,
        )
    )

    def fail_if_hashed(path):
        raise AssertionError("audio evidence must not be hashed before validation")

    monkeypatch.setattr("factory.gateway_video_batch._sha256_file", fail_if_hashed)

    with pytest.raises(GatewayVideoBatchError, match="exceeds the maximum"):
        render_gateway_video_single(
            "animate one character speaking",
            tmp_path / "single.mp4",
            client,
            tmp_path / "single-report.json",
            audio=audio,
        )


def test_gateway_video_job_signature_changes_with_reference_audio_hash(tmp_path):
    job = GatewayVideoJob(
        shot_id="single",
        index=1,
        prompt="animate one character speaking",
        images=(),
        duration=5,
        ratio="9:16",
        resolution="720p",
        output_path=str(tmp_path / "single.mp4"),
    )
    first = _job_signature(
        job,
        model="doubao-seedance-2-0-fast",
        generate_audio=True,
        endpoint_fingerprint=gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway.test/v1"
        ),
        reference_audio={
            "reference_audio_path": str(tmp_path / "drive.wav"),
            "reference_audio_sha256": "1" * 64,
        },
    )
    second = _job_signature(
        job,
        model="doubao-seedance-2-0-fast",
        generate_audio=True,
        endpoint_fingerprint=gateway_batch.gateway_endpoint_fingerprint(
            "https://gateway.test/v1"
        ),
        reference_audio={
            "reference_audio_path": str(tmp_path / "drive.wav"),
            "reference_audio_sha256": "2" * 64,
        },
    )

    assert first != second


def test_gateway_video_single_state_does_not_persist_signed_video_url(tmp_path):
    output = tmp_path / "single.mp4"

    class SignedUrlClient(FakeClient):
        def submit(self, prompt, **kwargs):
            self.submit_count += 1
            return GatewayVideoTask(
                task_id="",
                status="queued",
                video_url="https://cdn.example/video.mp4?signature=private",
            )

        def complete_task(self, task, output_path, **kwargs):
            raise GatewayVideoError("download interrupted")

    render_gateway_video_single(
        "animate one character",
        output,
        SignedUrlClient(),
        tmp_path / "single-report.json",
        allow_network=True,
    )

    state_text = output.with_suffix(".mp4.gateway.json").read_text("utf-8")
    assert "signature=private" not in state_text


def test_gateway_video_single_rejects_report_path_that_conflicts_with_resume_state(
    tmp_path,
):
    output = tmp_path / "single.mp4"
    state_path = output.with_suffix(".mp4.gateway.json")

    with pytest.raises(GatewayVideoBatchError, match="report path conflicts"):
        render_gateway_video_single(
            "animate one character",
            output,
            FakeClient(),
            state_path,
        )

    assert state_path.exists() is False


def test_gateway_video_single_preflights_request_before_claiming_ambiguous_submit(
    tmp_path,
):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 256)
    output = tmp_path / "single.mp4"
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="secret",
            base_url="https://gateway.test/v1",
            model="doubao-seedance-2-0-fast",
            max_request_body_bytes=256,
        ),
        urlopen_fn=fake_urlopen,
    )

    report = render_gateway_video_single(
        "animate one character",
        output,
        client,
        tmp_path / "single-report.json",
        images=[reference],
        allow_network=True,
    )

    assert report["success"] is False
    assert "request body exceeded" in report["errors"][0]["error"]
    assert contacted is False
    assert output.with_suffix(".mp4.gateway.json").exists() is False


def test_gateway_video_single_report_does_not_expose_remote_reference_url_path(
    tmp_path,
):
    report = render_gateway_video_single(
        "animate one character",
        tmp_path / "single.mp4",
        FakeClient(),
        tmp_path / "single-report.json",
        images=[
            "https://cdn.example/private-path-token/role.png?signature=private-query"
        ],
    )

    report_text = json.dumps(report)
    assert report["jobs"][0]["reference_images"] == ["cdn.example"]
    assert "private-path-token" not in report_text
    assert "private-query" not in report_text
