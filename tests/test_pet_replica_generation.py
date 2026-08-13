from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import wave
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

import factory.pet_replica_generation as generation
from factory.gateway_video import GatewayVideoConfig, GatewayVideoResult, GatewayVideoTask
from factory.gateway_video_batch import render_gateway_video_single
from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_assets import ReplicaAssetManifest, ReplicaAssetRecord
from factory.pet_replica_audio import ReplicaAudioAsset, ReplicaAudioManifest, ReplicaAacTimeline, ReplicaPayloadEvidence
from factory.pet_replica_generation import PetReplicaGenerationError, build_replica_shot_jobs, generate_replica_candidates, select_replica_candidate
from factory.pet_replica_reference import ReplicaShotAnnotation


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"reference")
    return build_pet_replica_plan(source, tmp_path / "output")


def _inputs(plan):
    root = plan.output_root
    records = []
    asset_paths = {
        "woman_front": "assets/characters/woman_front.png",
        "woman_half_body": "assets/characters/woman_half_body.png",
        "naitang_reference": "assets/characters/奶糖_reference.png",
        "doubao_reference": "assets/characters/豆包_reference.png",
        "scene_sofa": "assets/scenes/scene_sofa.png",
        "scene_table": "assets/scenes/scene_table.png",
        "scene_phone": "assets/scenes/scene_phone.png",
    }
    for asset_id in ("woman_front", "woman_half_body", "naitang_reference", "doubao_reference", "scene_sofa", "scene_table", "scene_phone"):
        path = root / asset_paths[asset_id]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(asset_id.encode())
        records.append(ReplicaAssetRecord(asset_id, "anchor", path, _sha(path), 1, 1, "test", None, None, "gateway", "test", "test", "generated"))
    for shot in plan.shots:
        path = root / "reference" / "shots" / shot.shot_id / "start.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("frame" + shot.shot_id).encode())
    assets = ReplicaAssetManifest("test", root, root / "assets/asset_manifest.json", _sha(plan.source_video), tuple(records), (), True)
    audio_assets = {}
    for shot in plan.shots:
        path = root / "audio/drive" / f"{shot.shot_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(b"\x00\x00\x00\x00")
        audio_assets[shot.shot_id] = ReplicaAudioAsset(shot.shot_id, path, _sha(path), shot.duration_s, 48000, 2, "pcm_s16le", shot.start_s, shot.end_s)
    timeline = ReplicaAacTimeline(44100, 1, 0, 1, 0, 1, 1, 1, None, None, None)
    full = ReplicaAudioAsset(None, root / "audio/source.aac", "x", 1, 44100, 2, "aac", 0, plan.duration_s)
    audio = ReplicaAudioManifest(root / "audio/audio_manifest.json", _sha(plan.source_video), full, MappingProxyType(audio_assets), timeline, timeline, ReplicaPayloadEvidence("x", 1), "local_evaluation_only", False, "Replace or license the source audio.")
    annotations = []
    for shot in plan.shots:
        characters = ("source_woman", "source_orange_cat", "source_tabby_cat")
        speaker = "source_woman" if shot.index != 9 else ""
        scene_anchor_id = "scene_sofa"
        if shot.shot_id == "R004":
            characters = ("source_orange_cat", "source_tabby_cat")
            scene_anchor_id = "scene_phone"
        elif shot.shot_id == "R007":
            characters = ("source_woman", "source_tabby_cat")
            scene_anchor_id = "scene_phone"
        elif shot.shot_id == "R010":
            scene_anchor_id = "scene_table"
        annotations.append(
            ReplicaShotAnnotation(
                shot_id=shot.shot_id,
                characters=characters,
                speaker=speaker,
                scene_anchor_id=scene_anchor_id,
                location="客厅木桌" if scene_anchor_id == "scene_table" else "客厅",
                framing="eye-level medium framing, woman on sofa left, cats right",
                action="woman gestures toward the cats while the cats look at her",
                subtitle="",
                source_audio=True,
                manual_review_required=False,
            )
        )
    return tuple(annotations), assets, audio


def test_pilot_includes_full_r009_and_keeps_source_pixels_out_of_provider_refs(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    jobs = build_replica_shot_jobs(plan, annotations, assets, audio, pilot_only=True)
    assert [job.shot_id for job in jobs] == [f"R{index:03d}" for index in range(1, 10)]
    assert jobs[-1].end_s == pytest.approx(13.333333)
    assert all(job.model == "doubao-seedance-2-0" and job.resolution == "720p" and job.ratio == "9:16" for job in jobs)
    assert all(job.composition_path not in job.reference_images for job in jobs)
    assert all(
        "woman" not in image.name
        for job in jobs
        for image in job.reference_images
    )
    assert all("source woman face" in job.negative_contract and "platform UI" in job.negative_contract for job in jobs)
    by_id = {job.shot_id: job for job in jobs}
    assert [path.name for path in by_id["R001"].reference_images] == [
        "奶糖_reference.png",
        "豆包_reference.png",
        "scene_sofa.png",
    ]
    assert by_id["R004"].reference_images[-1].name == "scene_phone.png"
    assert by_id["R007"].reference_images[-1].name == "scene_phone.png"
    assert "No input image provides a human identity" in by_id["R001"].prompt
    assert "white muzzle, chest, belly, and paws" in by_id["R001"].prompt
    assert "white facial blaze, chest bib, and four white paws" in by_id["R001"].prompt
    assert "must already match the reviewed shot setup" in by_id["R001"].prompt
    table_job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R010",))[0]
    assert table_job.reference_images[-1].name == "scene_table.png"


def test_speaking_audio_mouth_and_silent_closed_mouth_contract(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    jobs = build_replica_shot_jobs(plan, annotations, assets, audio, pilot_only=False, shot_ids=("R001", "R009"))
    assert jobs[0].audio_path.name == "R001.wav"
    assert jobs[0].generate_audio is True
    assert "mouth begins" in jobs[0].prompt and "mouth closes" in jobs[0].prompt
    assert "mouth stays closed before the first audible syllable" in jobs[0].prompt
    assert "large and unobstructed enough for lip-sync review" in jobs[0].prompt
    assert jobs[1].audio_path is None and "silent closed mouth" in jobs[1].prompt
    offscreen = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R004",))[0]
    assert offscreen.speaker_visible is False and offscreen.audio_path.name == "R004.wav"
    assert "off-screen voice" in offscreen.prompt and "no visible speaking mouth" in offscreen.prompt
    assert "only one hand and forearm may enter" in offscreen.prompt
    assert "no face or complete body" in offscreen.prompt


def test_postprocess_lipsync_keeps_drive_audio_binding_out_of_gateway_request(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)

    job = build_replica_shot_jobs(
        plan,
        annotations,
        assets,
        audio,
        pilot_only=False,
        shot_ids=("R003",),
        postprocess_lipsync=True,
    )[0]

    assert job.audio_path is not None
    assert job.generate_audio is False
    assert "post-production lip-sync" in job.prompt
    assert generation._prepare_gateway_drive_audio(plan.output_root, job) is None

    cat_annotations = tuple(
        replace(
            annotation,
            characters=("source_orange_cat",),
            speaker="source_orange_cat",
            action="cat opens and closes its mouth during the reviewed line",
        )
        if annotation.shot_id == "R006"
        else annotation
        for annotation in annotations
    )
    cat_job = build_replica_shot_jobs(
        plan,
        cat_annotations,
        assets,
        audio,
        pilot_only=False,
        shot_ids=("R006",),
        postprocess_lipsync=True,
    )[0]

    assert cat_job.generate_audio is False
    assert "animate only the cat's lower jaw" in cat_job.prompt
    assert "mouth closed at frame 0" in cat_job.prompt
    assert "post-production lip-sync" not in cat_job.prompt


def test_gateway_drive_audio_matches_provider_duration_without_retiming(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(
        plan,
        annotations,
        assets,
        audio,
        False,
        shot_ids=("R001",),
    )[0]
    source = plan.output_root / "audio" / "drive" / "R001_valid.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source_frames = b"\x01\x00\x02\x00" * 48_000
    with wave.open(str(source), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(source_frames)
    job = replace(job, audio_path=source)

    transport = generation._prepare_gateway_drive_audio(plan.output_root, job)

    assert transport == (
        plan.output_root / "audio" / "transport" / "R001_4s.wav"
    )
    with wave.open(str(transport), "rb") as opened:
        assert opened.getparams()[:4] == (2, 2, 48_000, 192_000)
        rendered = opened.readframes(opened.getnframes())
    assert rendered[: len(source_frames)] == source_frames
    assert rendered[len(source_frames) :] == bytes(3 * 48_000 * 4)
    assert generation._prepare_gateway_drive_audio(plan.output_root, job) == transport


def test_candidate_validation_accepts_native_24fps_and_rejects_unknown_rate(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(
        plan,
        annotations,
        assets,
        audio,
        False,
        shot_ids=("R001",),
    )[0]
    candidate = plan.output_root / "shots/R001/provider.mp4"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"provider video")
    monkeypatch.setattr(generation, "is_valid_mp4_file", lambda _path: True)
    monkeypatch.setattr(
        generation,
        "_probe_candidate_video",
        lambda _path: generation.ReplicaCandidateVideoProbe(
            4.041667, 720, 1280, 24.0
        ),
    )

    generation._validate_staged_candidate(plan.output_root, candidate, job)

    monkeypatch.setattr(
        generation,
        "_probe_candidate_video",
        lambda _path: generation.ReplicaCandidateVideoProbe(4.1, 720, 1280, 20.0),
    )
    with pytest.raises(PetReplicaGenerationError, match="supported frame rate"):
        generation._validate_staged_candidate(plan.output_root, candidate, job)


def test_duration_candidate_limit_and_dry_run_are_locked(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    jobs = build_replica_shot_jobs(plan, annotations, assets, audio, pilot_only=False, shot_ids=("R001",))
    assert jobs[0].generation_duration_s == 4
    with pytest.raises(PetReplicaGenerationError, match="1 and 3"):
        build_replica_shot_jobs(plan, annotations, assets, audio, False, candidate_number=4)
    assert generate_replica_candidates(plan, jobs, object(), enable_live=False, replace_stale=False) == ()


def test_live_renderer_stale_replace_and_selection(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    jobs = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))
    calls = []
    class FakeClient:
        config = GatewayVideoConfig("sk-secret", "https://gateway.example/v1?token=nope", "doubao-seedance-2-0")
        def __init__(self, _config):
            self.config = _config
    def fake_render(prompt, output, client, report, **kwargs):
        calls.append(kwargs)
        Path(output).write_bytes(b"new video")
        payload = {
            "success": True,
            "planned_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "error": "",
            "errors": [],
            "results": [{"status": "completed", "url": "https://x/?token=nope"}],
            "token": "nope",
        }
        Path(report).write_text(json.dumps(payload), encoding="utf-8")
        return payload
    monkeypatch.setattr(generation, "render_gateway_video_single", fake_render)
    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "_validate_staged_candidate", lambda *_args: None)
    candidate = generate_replica_candidates(plan, jobs, FakeClient.config, True, False)[0]
    assert calls[0]["images"] == jobs[0].reference_images
    assert calls[0]["audio"] == plan.output_root / "audio/transport/R001_4s.wav"
    assert calls[0]["generate_audio"] is True
    payload = json.loads(candidate.provenance_path.read_text(encoding="utf-8"))
    assert "sk-secret" not in json.dumps(payload) and "token=nope" not in json.dumps(payload)
    assert "token=nope" not in candidate.gateway_report_path.read_text(encoding="utf-8")
    assert generate_replica_candidates(plan, jobs, FakeClient.config, True, False)[0] == candidate
    changed = replace(jobs[0], prompt=jobs[0].prompt + " changed")
    with pytest.raises(PetReplicaGenerationError, match="replace_stale"):
        generate_replica_candidates(plan, (changed,), FakeClient.config, True, False)
    regenerated = generate_replica_candidates(plan, (changed,), FakeClient.config, True, True)[0]
    assert len(calls) == 2 and regenerated.video_path.exists()
    selection = select_replica_candidate(plan, "R001", 1, "framing checked; Task 6 quality gate pending")
    assert json.loads(selection.read_text())["quality_approved"] is False


def test_path_escape_is_rejected(tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")
    (plan.output_root / "reference/shots/R001/start.jpg").unlink()
    (plan.output_root / "reference/shots/R001/start.jpg").symlink_to(outside)
    with pytest.raises(PetReplicaGenerationError, match="symlinks"):
        build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))


def test_staging_names_keep_real_renderer_extensions(tmp_path):
    class RendererClient:
        config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

        def validate_reference_images(self, _images):
            return None

        def validate_reference_audio(self, _audio):
            return None

        def prepare_submission(self, prompt, **kwargs):
            return prompt, kwargs

        def submit_prepared(self, _submission, *, allow_network):
            assert allow_network is True
            return GatewayVideoTask("task-1", "queued")

        def complete_task(self, _task, output, **_kwargs):
            from tests.media_fixtures import VALID_VIDEO_MP4

            Path(output).write_bytes(VALID_VIDEO_MP4)
            return GatewayVideoResult(
                output_path=str(output),
                model=self.config.model,
                task_id="task-1",
                status="completed",
                poll_count=1,
                output_size_bytes=Path(output).stat().st_size,
                duration_seconds=1.0,
                source_host="gateway.example",
            )

    output = tmp_path / ".candidate_01.stage.mp4"
    report = tmp_path / ".candidate_01.gateway.stage.json"
    result = render_gateway_video_single(
        "one calm shot",
        output,
        RendererClient(),
        report,
        duration=4,
        ratio="9:16",
        resolution="720p",
        allow_network=True,
    )
    assert output.suffix == ".mp4" and report.suffix == ".json"
    assert result["success"] is True


def test_failed_report_invalid_bytes_and_probe_failure_do_not_promote(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")
    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)

    def render_failed(_prompt, output, _client, report, **_kwargs):
        Path(output).write_bytes(b"not an mp4")
        payload = _completed_report()
        payload["success"] = False
        payload["failed_count"] = 1
        payload["errors"] = [{"error": "provider failed"}]
        Path(report).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(generation, "render_gateway_video_single", render_failed)
    with pytest.raises(PetReplicaGenerationError, match="report"):
        generate_replica_candidates(plan, (job,), config, True, False)
    assert not job.output_path.exists() and not job.gateway_report_path.exists()

    def render_invalid(_prompt, output, _client, report, **_kwargs):
        Path(output).write_bytes(b"not an mp4")
        Path(report).write_text(json.dumps(_completed_report()), encoding="utf-8")
        return _completed_report()

    monkeypatch.setattr(generation, "render_gateway_video_single", render_invalid)
    with pytest.raises(PetReplicaGenerationError, match="valid MP4"):
        generate_replica_candidates(plan, (job,), config, True, False)
    assert not job.output_path.exists() and not job.gateway_report_path.exists()

    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(b"old candidate")
    job.gateway_report_path.write_text("{}", encoding="utf-8")
    provenance = job.output_path.with_suffix(".provenance.json")
    provenance.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(generation, "is_valid_mp4_file", lambda _path: True)
    monkeypatch.setattr(
        generation,
        "_probe_candidate_video",
        lambda _path: generation.ReplicaCandidateVideoProbe(1.0, 720, 1280, 30.0),
    )
    with pytest.raises(PetReplicaGenerationError, match="shorter"):
        generate_replica_candidates(plan, (job,), config, True, True)
    assert job.output_path.read_bytes() == b"old candidate"
    assert not any(job.output_path.parent.glob("*.stage.*"))

    def render_raises(*_args, **_kwargs):
        raise RuntimeError("gateway transport failure")

    monkeypatch.setattr(generation, "render_gateway_video_single", render_raises)
    with pytest.raises(PetReplicaGenerationError, match="generation failed"):
        generate_replica_candidates(plan, (job,), config, True, True)
    assert job.output_path.read_bytes() == b"old candidate"
    assert not any(job.output_path.parent.glob("*.stage.*"))

    monkeypatch.setattr(generation, "render_gateway_video_single", render_invalid)
    monkeypatch.setattr(
        generation,
        "_probe_candidate_video",
        lambda _path: (_ for _ in ()).throw(PetReplicaGenerationError("probe failed")),
    )
    with pytest.raises(PetReplicaGenerationError, match="probe failed"):
        generate_replica_candidates(plan, (job,), config, True, True)
    assert job.output_path.read_bytes() == b"old candidate"
    assert not any(job.output_path.parent.glob("*.stage.*"))


def test_failed_attempt_archives_sanitized_gateway_state_without_promoting(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(
        plan, annotations, assets, audio, False, shot_ids=("R001",)
    )[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_ambiguous(_prompt, output, _client, report, **_kwargs):
        Path(report).write_text(
            json.dumps(
                {
                    "success": False,
                    "error": "submit failed with sk-secret",
                    "results": [],
                }
            ),
            encoding="utf-8",
        )
        Path(output).with_suffix(Path(output).suffix + ".gateway.json").write_text(
            json.dumps(
                {
                    "status": "submitting",
                    "task_id": "task-uncertain",
                    "api_key": "sk-secret",
                    "url": "https://gateway.example/result?token=nope",
                }
            ),
            encoding="utf-8",
        )
        raise RuntimeError("gateway transport failed with sk-secret")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_ambiguous)
    config = GatewayVideoConfig(
        "sk-secret", "https://gateway.example/v1", "doubao-seedance-2-0"
    )

    with pytest.raises(PetReplicaGenerationError, match="generation failed"):
        generate_replica_candidates(plan, (job,), config, True, False)

    attempt = (
        plan.output_root
        / "rejected"
        / "generation_attempts"
        / "R001"
        / "candidate_01"
        / "attempt_001"
    )
    assert not job.output_path.exists() and not job.gateway_report_path.exists()
    assert (attempt / "gateway_report.json").is_file()
    assert (attempt / "gateway_state.json").is_file()
    assert (attempt / "failure.json").is_file()
    archived = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(attempt.glob("*.json"))
    )
    assert "task-uncertain" in archived
    assert "submitting" in archived
    assert "sk-secret" not in archived
    assert "token=nope" not in archived
    assert not any(job.output_path.parent.glob("*.stage.*"))


def test_post_download_validation_failure_archives_gateway_video(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(
        plan, annotations, assets, audio, False, shot_ids=("R001",)
    )[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_completed(_prompt, output, _client, report, **_kwargs):
        Path(output).write_bytes(b"paid provider video")
        payload = _completed_report()
        Path(report).write_text(json.dumps(payload), encoding="utf-8")
        Path(output).with_suffix(Path(output).suffix + ".gateway.json").write_text(
            json.dumps({"status": "completed", "task_id": "task-paid"}),
            encoding="utf-8",
        )
        return payload

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_completed)
    monkeypatch.setattr(generation, "is_valid_mp4_file", lambda _path: True)
    monkeypatch.setattr(
        generation,
        "_probe_candidate_video",
        lambda _path: generation.ReplicaCandidateVideoProbe(4.1, 720, 1280, 20.0),
    )
    config = GatewayVideoConfig(
        "test", "https://gateway.example/v1", "doubao-seedance-2-0"
    )

    with pytest.raises(PetReplicaGenerationError, match="supported frame rate"):
        generate_replica_candidates(plan, (job,), config, True, False)

    attempt = (
        plan.output_root
        / "rejected/generation_attempts/R001/candidate_01/attempt_001"
    )
    assert (attempt / "gateway_output.mp4").read_bytes() == b"paid provider video"
    failure = json.loads((attempt / "failure.json").read_text(encoding="utf-8"))
    assert "gateway_output.mp4" in failure["diagnostics"]
    assert not any(job.output_path.parent.glob("*.stage.*"))


def test_post_promotion_cleanup_failure_rolls_back_to_verified_backups(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_completed(_prompt, output, _client, report, **_kwargs):
        Path(output).write_bytes(b"new candidate")
        Path(report).write_text(json.dumps(_completed_report()), encoding="utf-8")
        return _completed_report()

    provenance = job.output_path.with_suffix(".provenance.json")
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(b"old candidate")
    job.gateway_report_path.write_text('{"old": true}', encoding="utf-8")
    provenance.write_text('{"old": true}', encoding="utf-8")
    remove_staging_files = generation._remove_staging_files
    calls = 0

    def cleanup_fails_only_after_promotion(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("cleanup disk failure")
        return remove_staging_files(*args, **kwargs)

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_completed)
    monkeypatch.setattr(generation, "_validate_staged_candidate", lambda *_args: None)
    monkeypatch.setattr(generation, "_remove_staging_files", cleanup_fails_only_after_promotion)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError, match="cleanup disk failure"):
        generate_replica_candidates(plan, (job,), config, True, True)

    assert job.output_path.read_bytes() == b"old candidate"
    assert json.loads(job.gateway_report_path.read_text(encoding="utf-8")) == {"old": True}
    assert json.loads(provenance.read_text(encoding="utf-8")) == {"old": True}

    monkeypatch.setattr(generation, "_remove_staging_files", remove_staging_files)
    monkeypatch.setattr(
        generation,
        "_cleanup_backups_best_effort",
        lambda *_args: ("backup cleanup disk failure",),
    )
    candidate = generate_replica_candidates(plan, (job,), config, True, True)[0]
    assert candidate.video_path.read_bytes() == b"new candidate"


def test_malformed_gateway_bytes_are_archived_without_credentials(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]
    raw = b'{"task_id":"task-recoverable","access_token":"access-secret","blob":"\xff'

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_malformed(_prompt, output, _client, report, **_kwargs):
        Path(report).write_bytes(raw)
        raise RuntimeError("transport failed")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_malformed)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError, match="transport failed"):
        generate_replica_candidates(plan, (job,), config, True, False)

    attempt = plan.output_root / "rejected/generation_attempts/R001/candidate_01/attempt_001"
    record = attempt / "gateway_report.invalid.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    archived = "\n".join(path.read_text(encoding="utf-8") for path in attempt.glob("*.json"))
    assert payload["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert payload["original_byte_length"] == len(raw)
    assert payload["recoverable_task_ids"] == ["task-recoverable"]
    assert "access-secret" not in archived
    assert record.stat().st_mode & 0o077 == 0
    assert not job.output_path.exists() and not job.gateway_report_path.exists()


def test_malformed_gateway_unicode_escaped_credential_key_is_redacted(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]
    raw = (
        b'{"task_id":"task-escaped","note":"literal \\u0022authorization\\u0022 text",'
        b'"auth\\u006frization":"Bearer escaped-credential","broken":'
    )

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_malformed(_prompt, output, _client, report, **_kwargs):
        Path(report).write_bytes(raw)
        raise RuntimeError("transport failed")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_malformed)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError, match="transport failed"):
        generate_replica_candidates(plan, (job,), config, True, False)

    record = (
        plan.output_root
        / "rejected/generation_attempts/R001/candidate_01/attempt_001/gateway_report.invalid.json"
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    decoded = base64.b64decode(payload["sanitized_utf8_base64"]).decode("utf-8")
    assert payload["recoverable_task_ids"] == ["task-escaped"]
    assert "escaped-credential" not in decoded
    assert "task-escaped" in decoded
    assert "literal" in decoded


@pytest.mark.parametrize(
    ("raw", "task_id", "credential"),
    (
        (
            b'{"task_id":"task-single",\'auth\\u006frization\':\'Bearer single-secret\',"broken":',
            "task-single",
            "single-secret",
        ),
        (
            b'{"task_id":"task-js",\'auth\\x6frization\':\'Bearer js-secret\',"broken":',
            "task-js",
            "js-secret",
        ),
        (
            b'{"task_id":"task-double",\'auth\\\\u006frization\':\'Bearer nested-secret\',"broken":',
            "task-double",
            "nested-secret",
        ),
        (
            b'{"task_id":"task-percent","url":"https://gateway.example/?%61%63%63%65%73%73%5F%74%6F%6B%65%6E=percent-secret","broken":',
            "task-percent",
            "percent-secret",
        ),
    ),
)
def test_malformed_gateway_escape_variants_do_not_expose_credentials(
    monkeypatch, tmp_path, raw, task_id, credential
):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_malformed(_prompt, output, _client, report, **_kwargs):
        Path(report).write_bytes(raw)
        raise RuntimeError("transport failed")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_malformed)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError, match="transport failed"):
        generate_replica_candidates(plan, (job,), config, True, False)

    record = (
        plan.output_root
        / "rejected/generation_attempts/R001/candidate_01/attempt_001/gateway_report.invalid.json"
    )
    payload = json.loads(record.read_text(encoding="utf-8"))
    decoded = base64.b64decode(payload["sanitized_utf8_base64"]).decode("utf-8")
    assert payload["recoverable_task_ids"] == [task_id]
    assert task_id in decoded
    assert credential not in decoded


def test_backup_creation_restore_failure_is_retried_and_reported(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]
    provenance = job.output_path.with_suffix(".provenance.json")
    video_backup = generation._backup_path(job.output_path)
    stale_report_backup = generation._backup_path(job.gateway_report_path)

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_completed(_prompt, output, _client, report, **_kwargs):
        Path(output).write_bytes(b"new candidate")
        Path(report).write_text(json.dumps(_completed_report()), encoding="utf-8")
        return _completed_report()

    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(b"old candidate")
    job.gateway_report_path.write_text('{"old": "report"}', encoding="utf-8")
    provenance.write_text('{"old": "provenance"}', encoding="utf-8")
    stale_report_backup.write_bytes(b"stale report backup")
    real_replace = generation.os.replace
    restore_attempts = 0

    def fail_first_video_restore(source, target):
        nonlocal restore_attempts
        if Path(source) == video_backup and Path(target) == job.output_path:
            restore_attempts += 1
            if restore_attempts == 1:
                raise OSError("injected restore failure")
        return real_replace(source, target)

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_completed)
    monkeypatch.setattr(generation, "_validate_staged_candidate", lambda *_args: None)
    monkeypatch.setattr(generation.os, "replace", fail_first_video_restore)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError) as raised:
        generate_replica_candidates(plan, (job,), config, True, True)

    assert "Replica promotion backup already exists" in str(raised.value)
    assert "injected restore failure" in str(raised.value)
    assert restore_attempts == 2
    assert job.output_path.read_bytes() == b"old candidate"
    assert json.loads(job.gateway_report_path.read_text(encoding="utf-8")) == {"old": "report"}
    assert json.loads(provenance.read_text(encoding="utf-8")) == {"old": "provenance"}
    assert not video_backup.exists()
    assert stale_report_backup.read_bytes() == b"stale report backup"


def test_failure_archives_redact_common_credential_keys_and_query_params(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_with_credentials(_prompt, output, _client, report, **_kwargs):
        Path(report).write_text(
            json.dumps(
                {
                    "access_token": "access-secret",
                    "refresh-token": "refresh-secret",
                    "headers": {
                        "X-API-Key": "api-secret",
                        "Cookie": "cookie-secret",
                        "Set-Cookie": "set-cookie-secret",
                        "Authorization": "Bearer authorization-secret",
                    },
                    "signature": "signature-secret",
                    "credential": "credential-secret",
                    "secret": "secret-secret",
                    "password": "password-secret",
                    "url": "https://gateway.example/?access_token=query-access&refresh_token=query-refresh&key=query-key&credential=query-credential",
                    "task_id": "task-safe",
                }
            ),
            encoding="utf-8",
        )
        raise RuntimeError("provider failure")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_with_credentials)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError, match="provider failure"):
        generate_replica_candidates(plan, (job,), config, True, False)

    attempt = plan.output_root / "rejected/generation_attempts/R001/candidate_01/attempt_001"
    archived = "\n".join(path.read_text(encoding="utf-8") for path in attempt.glob("*.json"))
    for secret in (
        "access-secret", "refresh-secret", "api-secret", "cookie-secret", "set-cookie-secret",
        "authorization-secret", "signature-secret", "credential-secret", "secret-secret",
        "password-secret", "query-access", "query-refresh", "query-key", "query-credential",
    ):
        assert secret not in archived
    assert "task-safe" in archived


def test_symlinked_diagnostic_preserves_primary_error_and_failure_metadata(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]
    outside = tmp_path / "outside.json"
    outside.write_text('{"task_id": "outside-task"}', encoding="utf-8")

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def render_symlinked_diagnostic(_prompt, output, _client, report, **_kwargs):
        Path(report).symlink_to(outside)
        raise RuntimeError("original transport failure")

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", render_symlinked_diagnostic)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")

    with pytest.raises(PetReplicaGenerationError) as raised:
        generate_replica_candidates(plan, (job,), config, True, False)

    assert str(raised.value).startswith("Replica candidate generation failed: original transport failure")
    assert "diagnostic archive errors" in str(raised.value)
    attempt = plan.output_root / "rejected/generation_attempts/R001/candidate_01/attempt_001"
    failure = json.loads((attempt / "failure.json").read_text(encoding="utf-8"))
    assert failure["diagnostics"] == []
    assert any("symlink" in error.lower() for error in failure["diagnostic_errors"])
    assert outside.read_text(encoding="utf-8") == '{"task_id": "outside-task"}'
    assert not job.output_path.exists() and not job.gateway_report_path.exists()
    assert not any(job.output_path.parent.glob("*.stage.*"))


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for candidate-media integration",
)
def test_short_valid_mp4_is_not_promoted(monkeypatch, tmp_path):
    plan = _plan(tmp_path)
    annotations, assets, audio = _inputs(plan)
    job = build_replica_shot_jobs(plan, annotations, assets, audio, False, shot_ids=("R001",))[0]

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def short_render(_prompt, output, _client, report, **_kwargs):
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=black:s=720x1280:r=30:d=1", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(output),
            ],
            check=True,
        )
        payload = _completed_report()
        Path(report).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(generation, "GatewayVideoClient", FakeClient)
    monkeypatch.setattr(generation, "render_gateway_video_single", short_render)
    config = GatewayVideoConfig("test", "https://gateway.example/v1", "doubao-seedance-2-0")
    with pytest.raises(PetReplicaGenerationError, match="shorter"):
        generate_replica_candidates(plan, (job,), config, True, False)
    assert not job.output_path.exists() and not job.gateway_report_path.exists()


def _completed_report():
    return {
        "success": True,
        "planned_count": 1,
        "completed_count": 1,
        "failed_count": 0,
        "error": "",
        "errors": [],
        "results": [{"status": "completed"}],
    }
