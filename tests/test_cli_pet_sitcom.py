from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import factory_cli
from factory.pet_sitcom_review import PetSitcomReviewError
from factory.pet_sitcom_sound import PetSoundError


class _FakePlan(SimpleNamespace):
    def to_report(self):
        return {"project_id": "pet", "shot_count": len(self.shots)}


def _plan(tmp_path: Path) -> _FakePlan:
    root = tmp_path / "pet-output"
    shots = tuple(
        SimpleNamespace(
            shot_id=f"shot_{number:02d}",
            dialogue="对白" if number <= 8 else "",
            candidate_dir=root / "shots" / f"shot_{number:02d}",
        )
        for number in range(1, 11)
    )
    return _FakePlan(
        output_dir=root,
        plan_path=root / "pet_sitcom_plan.json",
        audio_manifest_path=root / "audio_manifest.json",
        audio_probe_path=root / "audio_probe.json",
        audio_probe_review_path=root / "audio_probe_review.json",
        selection_path=root / "selected_candidates.json",
        shot_review_path=root / "shot_review.json",
        shots=shots,
        clean_output=root / "final" / "clean.mp4",
        release_output=root / "final" / "release.mp4",
        review_markdown_path=root / "review.md",
    )


def _run(monkeypatch, capsys, *arguments: str):
    monkeypatch.setattr(
        sys,
        "argv",
        ["factory_cli.py", "--config", "fixture.json", "pet-sitcom", *arguments],
    )
    status = factory_cli.main()
    return status, json.loads(capsys.readouterr().out)


def _install_plan(monkeypatch, tmp_path: Path):
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        factory_cli, "load_config", lambda _: {"workspace": str(tmp_path)}
    )
    monkeypatch.setattr(
        factory_cli,
        "build_pet_sitcom_plan",
        lambda *_args, **_kwargs: plan,
    )
    monkeypatch.setattr(
        factory_cli,
        "write_pet_sitcom_plan",
        lambda value: value.plan_path,
    )
    return plan


def _profile():
    return SimpleNamespace(
        image=SimpleNamespace(
            provider="gateway",
            ready=True,
            api_key="secret-key",
            base_url="https://gateway.test/v1",
            blockers=(),
        ),
        video=SimpleNamespace(
            provider="gateway",
            ready=True,
            api_key="secret-key",
            base_url="https://gateway.test/v1",
            blockers=(),
        ),
        audio=SimpleNamespace(
            provider="doubao",
            ready=True,
            api_key="tts-secret",
            blockers=(),
        ),
    )


def _allow_probe(monkeypatch):
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "approved",
            "approved": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "require_approved_pet_audio_probe",
        lambda _plan: {"approved": True},
    )


def _allow_review_inputs(monkeypatch):
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_selections",
        lambda _plan, _audio: {"count": 10, "sources": {}},
    )
    monkeypatch.setattr(
        factory_cli,
        "_refresh_pet_incremental_evidence",
        lambda _plan: (),
    )


def _allow_compose_gates(monkeypatch, calls):
    _allow_review_inputs(monkeypatch)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_reviews",
        lambda _plan, _selections: {
            "passed_count": 10,
            "owner_verified": True,
            "source_valid": True,
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_sound",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_source_evidence",
        lambda _plan: calls.append("source") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_pet_shot_reviews",
        lambda _plan: calls.append("reviews") or {"passed": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_owner_native_audio_review",
        lambda _plan: calls.append("owner") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "compose_pet_sitcom",
        lambda _plan: calls.append("compose") or {"success": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "build_final_evidence",
        lambda _plan: calls.append("final-build") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_final_evidence",
        lambda _plan: calls.append("final-validate") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "write_pet_sitcom_review_markdown",
        lambda plan: calls.append("markdown") or plan.review_markdown_path,
    )


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_drive_state(plan, shot, asset) -> Path:
    import factory.pet_sitcom_audio_first as audio_first

    output = (
        plan.output_dir / "audio" / "drive" / f"{shot.shot_id}_drive.wav"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(audio_first.OUTPUT_CHANNELS)
        audio.setsampwidth(2)
        audio.setframerate(audio_first.OUTPUT_SAMPLE_RATE)
        audio.writeframes(
            b"\x01\x00"
            * audio_first.OUTPUT_CHANNELS
            * audio_first.OUTPUT_SAMPLE_RATE
            * shot.generation_duration_seconds
        )
    _write_json(
        output.with_suffix(".state.json"),
        {
            "schema_version": audio_first.DRIVE_AUDIO_STATE_SCHEMA,
            "status": "completed",
            "signature": audio_first._drive_signature(shot, asset),
            "shot_id": shot.shot_id,
            "dialogue_offset_seconds": shot.dialogue_offset_seconds,
            "generation_duration_seconds": shot.generation_duration_seconds,
            "source_path": str(asset.output_path),
            "source_sha256": asset.output_sha256,
            "source_duration_seconds": asset.duration_seconds,
            "absolute_start_seconds": asset.absolute_start_seconds,
            "absolute_end_seconds": asset.absolute_end_seconds,
            "output_path": str(output),
            "output_sha256": factory_cli._pet_file_sha256(output),
        },
    )
    return output


def _build_complete_persistent_status_fixture(
    tmp_path,
    monkeypatch,
    *,
    qc_duration_overrides=None,
):
    import factory.pet_sitcom_audio_probe as audio_probe
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review
    import factory.pet_sitcom_sound as sound
    from factory.pet_sitcom import build_pet_sitcom_plan, write_pet_sitcom_plan
    from tests.test_pet_sitcom_generation import FakeImageClient
    from tests.test_pet_sitcom_review import (
        FakeMediaRunner,
        _build_final,
        _build_source,
        _complete_owner_review,
        _complete_shot_reviews,
        _write_current_task2_manifest,
        _write_finals,
    )
    from tests.test_pet_sitcom_sound import (
        FakeSoundRunner,
        _write_approval,
        _write_pcm_wav,
    )

    plan = build_pet_sitcom_plan({}, output_dir=tmp_path / "complete")
    write_pet_sitcom_plan(plan)
    generation.generate_pet_sitcom_anchors(
        plan,
        image_client=FakeImageClient(),
        allow_network=True,
    )
    anchor_review = json.loads(
        (plan.output_dir / "anchor_review_template.json").read_text()
    )
    anchor_review.update(
        {
            field: True
            for field in generation._ANCHOR_REVIEW_FIELDS
        }
    )
    anchor_review["completed"] = True
    _write_json(
        plan.output_dir / "anchor_review_template.json",
        anchor_review,
    )
    generation.approve_pet_anchors(plan)

    assets = _write_current_task2_manifest(plan, monkeypatch)
    assets_by_shot = {asset.shot_id: asset for asset in assets}
    drives = {
        shot.shot_id: _write_drive_state(
            plan,
            shot,
            assets_by_shot[shot.shot_id],
        )
        for shot in plan.shots
        if shot.speaker in {"naitang", "doubao"}
    }

    source_shot = next(
        shot
        for shot in plan.shots
        if shot.shot_id == audio_probe.PROBE_SOURCE_SHOT_ID
    )
    source_asset = assets_by_shot[source_shot.shot_id]
    references = []
    for role, path in (
        ("doubao_character", plan.characters[1].reference_path),
        (
            "kitchen_scene",
            next(
                scene.anchor_path
                for scene in plan.scenes
                if scene.slug == "kitchen"
            ),
        ),
    ):
        references.append(
            {
                "role": role,
                "path": str(path),
                "sha256": factory_cli._pet_file_sha256(path),
            }
        )
    gateway = audio_probe._probe_gateway_report_path(plan)
    _write_json(gateway, {"success": True, "task_id": "persistent-probe"})
    probe_video = audio_probe._probe_video_path(plan)
    probe_video.parent.mkdir(parents=True, exist_ok=True)
    probe_video.write_bytes(b"persisted-probe-video")
    frame_evidence = []
    for index, timestamp in enumerate(
        audio_probe.PROBE_FRAME_TIMESTAMPS,
        start=1,
    ):
        frame = audio_probe._probe_frame_dir(plan) / f"frame_{index:02d}.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (index, 20, 40)).save(frame)
        frame_evidence.append(
            {
                "timestamp_seconds": timestamp,
                "path": str(frame),
                "sha256": factory_cli._pet_file_sha256(frame),
            }
        )
    probe_report = {
        "schema_version": audio_probe.PROBE_SCHEMA,
        "capability": "supported",
        "success": True,
        "executed": True,
        "source_shot_id": audio_probe.PROBE_SOURCE_SHOT_ID,
        "model": audio_probe.PROBE_MODEL,
        "prompt_sha256": audio_probe._hash_text(
            audio_probe._probe_prompt(plan)
        ),
        "references": references,
        "source_tts_path": str(source_asset.output_path),
        "source_tts_sha256": source_asset.output_sha256,
        "audio_manifest_path": str(plan.audio_manifest_path),
        "audio_manifest_sha256": factory_cli._pet_file_sha256(
            plan.audio_manifest_path
        ),
        "drive_audio_path": str(drives[source_shot.shot_id]),
        "drive_audio_sha256": factory_cli._pet_file_sha256(
            drives[source_shot.shot_id]
        ),
        "gateway_report_path": str(gateway),
        "gateway_report_sha256": factory_cli._pet_file_sha256(gateway),
        "probe_mp4_path": str(probe_video),
        "probe_mp4_sha256": factory_cli._pet_file_sha256(probe_video),
        "frame_evidence": frame_evidence,
    }
    _write_json(plan.audio_probe_path, probe_report)
    review_bindings = audio_probe._review_bindings(plan, probe_report)
    _write_json(
        plan.audio_probe_review_path,
        {
            "schema_version": audio_probe.PROBE_REVIEW_SCHEMA,
            **review_bindings,
            "completed": True,
            "approved": True,
            "audio_onset_seconds": source_shot.dialogue_offset_seconds,
            "mouth_onset_seconds": (
                source_shot.dialogue_offset_seconds + 0.05
            ),
            "audio_offset_seconds": (
                source_shot.dialogue_offset_seconds
                + source_asset.duration_seconds
            ),
            "mouth_offset_seconds": (
                source_shot.dialogue_offset_seconds
                + source_asset.duration_seconds
                + 0.05
            ),
            **{gate: True for gate in audio_probe.PROBE_REVIEW_GATES},
            "notes": "Persistent probe reviewed.",
        },
    )

    selections = {}
    for shot in plan.shots:
        candidate = generation._pet_candidate_path(shot, 1)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"candidate-{shot.shot_id}".encode())
        refs = generation._pet_shot_references(
            plan,
            shot,
            selections,
        )
        drive = drives.get(shot.shot_id)
        source_tts_sha256 = (
            assets_by_shot[shot.shot_id].output_sha256
            if drive is not None
            else ""
        )
        provenance = generation._pet_candidate_provenance(
            shot,
            1,
            generation._pet_shot_prompt(shot, 1, ""),
            "",
            refs,
            selections,
            drive,
            source_tts_sha256,
        )
        provenance.update(
            {
                "provider_success": True,
                "video_sha256": factory_cli._pet_file_sha256(candidate),
            }
        )
        _write_json(
            generation._pet_candidate_state_path(candidate),
            provenance,
        )
        _write_json(
            generation._pet_gateway_report_path(candidate),
            {"success": True, "pet_sitcom_provenance": provenance},
        )
        continuity = generation._pet_continuity_frame_path(
            plan,
            shot.shot_id,
        )
        continuity.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "white").save(continuity)
        source_video_duration = (qc_duration_overrides or {}).get(
            shot.shot_id,
            shot.generation_duration_seconds,
        )
        timestamp = min(
            shot.duration_seconds - 0.08,
            source_video_duration - 0.08,
        )
        sidecar = generation._pet_continuity_state_path(continuity)
        _write_json(
            sidecar,
            {
                "schema_version": generation.PET_CONTINUITY_SCHEMA,
                "source_video_path": str(candidate),
                "source_video_sha256": factory_cli._pet_file_sha256(
                    candidate
                ),
                "source_video_duration_seconds": (
                    source_video_duration
                ),
                "edit_duration_seconds": shot.duration_seconds,
                "timestamp_seconds": timestamp,
                "extracted_at": "2026-07-27T08:00:00+00:00",
                "frame_sha256": factory_cli._pet_file_sha256(continuity),
            },
        )
        selections[shot.shot_id] = {
            "candidate_number": 1,
            "status": "selected",
            "video_path": str(candidate),
            "video_sha256": provenance["video_sha256"],
            "prompt_sha256": provenance["prompt_sha256"],
            "reference_paths": provenance["reference_paths"],
            "reference_sha256": provenance["reference_sha256"],
            "dependency_video_sha256": provenance[
                "dependency_video_sha256"
            ],
            "source_tts_sha256": provenance["source_tts_sha256"],
            "reference_audio_sha256": provenance[
                "reference_audio_sha256"
            ],
            "selected_at": "2026-07-27T08:00:00+00:00",
            "continuity_frame_path": str(continuity),
            "continuity_sidecar_path": str(sidecar),
            "continuity_frame_sha256": factory_cli._pet_file_sha256(
                continuity
            ),
            "continuity_timestamp_seconds": timestamp,
        }
    _write_json(
        plan.selection_path,
        {
            "schema_version": generation.PET_SELECTION_SCHEMA,
            "shots": selections,
            "history": {},
        },
    )
    audio_state = factory_cli._pet_inspect_audio(plan)
    selection_state = factory_cli._pet_inspect_selections(
        plan,
        audio_state,
    )
    assert selection_state["count"] == 10
    monkeypatch.setattr(
        review,
        "_selected_sources",
        lambda _plan: selection_state["sources"],
    )
    base_media_runner = FakeMediaRunner()

    def media_runner(command, **kwargs):
        result = base_media_runner(command, **kwargs)
        if Path(command[0]).name != "ffprobe":
            return result
        path = Path(command[-1])
        shot_id = (
            path.stem
            if path.stem.startswith("shot_")
            else path.parent.name
        )
        duration = (qc_duration_overrides or {}).get(shot_id)
        if duration is None:
            return result
        payload = json.loads(result.stdout)
        payload["format"]["duration"] = str(duration)
        for stream in payload["streams"]:
            stream["duration"] = str(duration)
        return SimpleNamespace(stdout=json.dumps(payload), stderr="")

    _build_source(plan, media_runner, monkeypatch)
    source_manifest = json.loads(
        review._source_manifest_path(plan).read_text()
    )
    source_qc = json.loads(
        (review._evidence_root(plan) / "source_technical_qc.json").read_text()
    )
    qc_by_shot = {
        record["name"]: record for record in source_qc["records"]
    }
    sheets_by_shot = {
        item["shot_id"]: item for item in source_manifest["shot_sheets"]
    }
    continuity_by_current = {
        shot.shot_id: [
            item
            for item in source_manifest["continuity_comparisons"]
            if item["current_shot_id"] == shot.shot_id
        ]
        for shot in plan.shots
    }
    for shot in plan.shots:
        props = {
            label: group[shot.shot_id]
            for label, group in source_manifest["prop_sequences"].items()
            if shot.shot_id in group
        }
        _write_json(
            review._evidence_root(plan)
            / "incremental"
            / f"{shot.shot_id}.json",
            {
                "schema_version": review.SHOT_EVIDENCE_SCHEMA,
                "generated_at": source_manifest["generated_at"],
                "shot_id": shot.shot_id,
                "source_technical_qc": qc_by_shot[shot.shot_id],
                "shot_sheet": sheets_by_shot[shot.shot_id],
                "mouth_sequence": source_manifest["mouth_sequences"].get(
                    shot.shot_id
                ),
                "paw_sequence": source_manifest["paw_sequences"].get(
                    shot.shot_id
                ),
                "prop_sequences": props,
                "continuity_comparison": continuity_by_current[
                    shot.shot_id
                ],
                "manual_review_path": str(plan.shot_review_path),
                "automation_limitations": review._AUTOMATION_LIMITATIONS,
            },
        )
    shot_reviews = _complete_shot_reviews(plan)
    for shot_id, record in shot_reviews["mouth_timing"].items():
        source = selection_state["sources"][shot_id]
        record["audio_onset_seconds"] = source["audio_onset_seconds"]
        record["mouth_onset_seconds"] = source["audio_onset_seconds"] + 0.05
        record["audio_offset_seconds"] = source["audio_offset_seconds"]
        record["mouth_offset_seconds"] = source["audio_offset_seconds"] + 0.05
        record["onset_error_seconds"] = 0.05
        record["offset_error_seconds"] = 0.05
    _write_json(plan.shot_review_path, shot_reviews)
    _complete_owner_review(plan)

    music = tmp_path / "approved-music.wav"
    _write_pcm_wav(music, seconds=54.1)
    _write_approval(music)
    sound.prepare_pet_sound_design(
        plan,
        music_source=music,
        command_runner=FakeSoundRunner(),
    )
    _write_finals(plan)
    _build_final(plan, media_runner, monkeypatch)
    return plan


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, int, str]]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        stat_result = path.lstat()
        if path.is_file() and not path.is_symlink():
            digest = factory_cli._pet_file_sha256(path)
            kind = "file"
        elif path.is_symlink():
            digest = os.readlink(path)
            kind = "symlink"
        else:
            digest = ""
            kind = "directory"
        result[relative] = (
            kind,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            digest,
        )
    return result


def test_pet_stage_order_and_shot_choices_are_exact():
    assert factory_cli.PET_STAGE_ORDER == (
        "plan",
        "anchors",
        "audio",
        "audio-probe",
        "shots",
        "review",
        "compose",
        "status",
    )
    parser = factory_cli.build_parser()
    for stage in factory_cli.PET_STAGE_ORDER:
        assert parser.parse_args(["pet-sitcom", "--stage", stage]).stage == stage
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["pet-sitcom", "--stage", "shots", "--shot", "shot_11"]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["pet-sitcom", "--stage", "mouth-test"])


def test_pet_sitcom_defaults_to_audio_first_plan(
    tmp_path, monkeypatch, capsys
):
    plan = _install_plan(monkeypatch, tmp_path)

    status, payload = _run(monkeypatch, capsys)

    assert status == 0
    assert payload["stage"] == "plan"
    assert payload["executed"] is False
    assert payload["output_dir"] == str(plan.output_dir)
    assert payload["planned_count"] == 23
    assert payload["production_plan"] == {
        "anchor_count": 4,
        "audio_count": 8,
        "audio_probe_count": 1,
        "shot_count": 10,
        "total_count": 23,
    }
    assert "--stage anchors" in payload["next_command"]


def test_only_plan_stage_writes_plan(tmp_path, monkeypatch, capsys):
    plan = _install_plan(monkeypatch, tmp_path)
    writes = []
    monkeypatch.setattr(
        factory_cli,
        "write_pet_sitcom_plan",
        lambda value: writes.append(value) or value.plan_path,
    )

    _run(monkeypatch, capsys)
    assert writes == [plan]

    monkeypatch.setattr(
        factory_cli,
        "_pet_status",
        lambda _plan: {
            "plan_ready": False,
            "anchors_approved": False,
            "audio_ready": False,
            "audio_probe_approved": False,
            "selected_shot_count": 0,
            "shot_review_passed_count": 0,
            "sound_design_approved": False,
            "composition_ready": False,
            "next_stage": "plan",
        },
    )
    _run(monkeypatch, capsys, "--stage", "status")
    assert writes == [plan]


def test_plan_construction_error_uses_common_json_contract(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        factory_cli, "load_config", lambda _path: {"workspace": str(tmp_path)}
    )
    monkeypatch.setattr(
        factory_cli,
        "build_pet_sitcom_plan",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("invalid plan")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "status")

    assert status == 1
    assert payload["stage"] == "status"
    assert payload["executed"] is False
    assert payload["success"] is False
    assert payload["blocked_reasons"] == ["invalid plan"]
    assert payload["artifacts"] == {}
    assert "--stage status" in payload["next_command"]


@pytest.mark.parametrize("stage", ["anchors", "audio", "audio-probe"])
def test_non_live_media_stages_do_not_resolve_providers(
    stage, tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    if stage in {"audio", "audio-probe"}:
        monkeypatch.setattr(
            factory_cli,
            "_pet_inspect_anchors",
            lambda _plan: {"approved": True},
        )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_doubao_tts_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("TTS")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", stage)

    assert status == 0
    assert payload["executed"] is False


def test_live_anchors_passes_only_requested_anchor(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli, "resolve_provider_profile", lambda _config: _profile()
    )
    monkeypatch.setattr(
        factory_cli, "_pet_image_client", lambda *_args: object()
    )
    calls = []
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_sitcom_anchors",
        lambda _plan, **kwargs: calls.append(kwargs)
        or {
            "success": True,
            "executed": True,
            "planned_count": 1,
            "completed_count": 1,
            "reused_count": 0,
            "errors": [],
        },
    )

    status, payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "anchors",
        "--anchor",
        "doubao",
        "--enable-live",
    )

    assert status == 0
    assert payload["targets"] == ["doubao"]
    assert calls[0]["anchor_names"] == ("doubao",)


def test_anchors_approves_completed_review_without_provider(
    tmp_path, monkeypatch, capsys
):
    plan = _install_plan(monkeypatch, tmp_path)
    review = plan.output_dir / "anchor_review_template.json"
    review.parent.mkdir(parents=True)
    review.write_text(json.dumps({"completed": True}), encoding="utf-8")
    monkeypatch.setattr(
        factory_cli,
        "approve_pet_anchors",
        lambda _plan: {"approved": True},
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "anchors")

    assert status == 0
    assert payload["approved"] is True
    assert "--stage audio" in payload["next_command"]


def test_audio_stage_uses_task2_generator(
    tmp_path, monkeypatch, capsys
):
    plan = _install_plan(monkeypatch, tmp_path)
    generated = []
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli, "_require_approved_anchors", lambda _plan: None
    )
    config = SimpleNamespace(resource_id="seed-tts-2.0")
    monkeypatch.setattr(
        factory_cli, "resolve_doubao_tts_config", lambda _config: config
    )
    monkeypatch.setattr(
        factory_cli,
        "DoubaoTTSClient",
        lambda value: SimpleNamespace(config=value),
    )
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_speech_assets",
        lambda current, **kwargs: generated.append((current, kwargs))
        or {
            "success": True,
            "executed": True,
            "planned_count": 8,
            "completed_count": 8,
            "reused_count": 0,
            "errors": [],
        },
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "audio", "--enable-live"
    )

    assert status == 0
    assert payload["completed_count"] == 8
    assert payload["artifacts"]["audio_manifest"] == str(
        plan.audio_manifest_path
    )
    assert generated[0][1]["allow_network"] is True
    assert "--stage audio-probe" in payload["next_command"]


def test_audio_stage_requires_approved_anchors_before_tts(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": False},
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_doubao_tts_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("TTS")),
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "audio", "--enable-live"
    )

    assert status == 1
    assert "anchor evidence" in payload["blocked_reasons"][0]


def test_audio_stage_requires_ready_doubao_config(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli, "_require_approved_anchors", lambda _plan: None
    )
    monkeypatch.setattr(
        factory_cli, "resolve_doubao_tts_config", lambda _config: None
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "audio", "--enable-live"
    )

    assert status == 1
    assert "Doubao Seed-TTS" in payload["blocked_reasons"][0]


def test_audio_probe_dry_run_makes_no_provider_call(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "audio-probe")

    assert status == 0
    assert payload["executed"] is False


def test_audio_probe_live_uses_task4_entry_after_current_inputs(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "missing",
            "approved": False,
            "reason": "audio-drive probe evidence is missing",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_require_approved_anchors",
        lambda _plan: calls.append("anchors"),
    )
    monkeypatch.setattr(
        factory_cli,
        "load_pet_speech_assets",
        lambda _plan: calls.append("audio") or tuple(range(8)),
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: calls.append("profile") or _profile(),
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_video_client",
        lambda *_args: calls.append("client") or object(),
    )
    monkeypatch.setattr(
        factory_cli,
        "run_pet_audio_drive_probe",
        lambda _plan, **kwargs: calls.append(("probe", kwargs))
        or {"success": True, "executed": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "require_approved_pet_audio_probe",
        lambda _plan: calls.append("approval") or {"approved": True},
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "audio-probe", "--enable-live"
    )

    assert status == 0
    assert payload["approved"] is True
    assert calls[:4] == ["anchors", "audio", "profile", "client"]
    assert calls[4][0] == "probe"
    assert calls[4][1]["allow_network"] is True
    assert calls[5] == "approval"


def test_shots_checks_probe_before_profile_submission_or_write(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    writes = []
    monkeypatch.setattr(
        factory_cli,
        "write_pet_sitcom_plan",
        lambda plan: writes.append(plan) or plan.plan_path,
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "missing",
            "approved": False,
            "reason": "approved audio-drive probe evidence is missing",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_sitcom_shots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("submission")
        ),
    )

    status, payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--shot",
        "shot_03",
        "--enable-live",
    )

    assert status == 1
    assert writes == []
    assert "approved audio-drive probe" in payload["blocked_reasons"][0]


def test_shots_single_target_uses_task5_and_builds_review_evidence(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    _allow_probe(monkeypatch)
    monkeypatch.setattr(
        factory_cli, "resolve_provider_profile", lambda _config: _profile()
    )
    monkeypatch.setattr(factory_cli, "_pet_video_client", lambda *_args: object())
    calls = []
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_sitcom_shots",
        lambda _plan, **kwargs: calls.append(("generate", kwargs))
        or {
            "success": True,
            "executed": True,
            "planned_count": 1,
            "completed_count": 1,
            "reused_count": 0,
            "errors": [],
            "shots": [{"shot_id": "shot_03"}],
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "build_pet_shot_evidence",
        lambda _plan, shot: calls.append(("evidence", shot)) or {},
    )

    status, payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--shot",
        "shot_03",
        "--enable-live",
    )

    assert status == 0
    assert payload["targets"] == ["shot_03"]
    assert calls[0][1]["shot_id"] == "shot_03"
    assert calls[1] == ("evidence", "shot_03")


def test_shots_all_routes_exactly_ten_targets(
    tmp_path, monkeypatch, capsys
):
    plan = _install_plan(monkeypatch, tmp_path)
    _allow_probe(monkeypatch)
    monkeypatch.setattr(
        factory_cli, "resolve_provider_profile", lambda _config: _profile()
    )
    monkeypatch.setattr(factory_cli, "_pet_video_client", lambda *_args: object())
    generated = []

    def generate(_plan, **kwargs):
        generated.append(kwargs["shot_id"])
        return {
            "success": True,
            "executed": True,
            "planned_count": 1,
            "completed_count": 1,
            "reused_count": 0,
            "errors": [],
            "shots": [{"shot_id": kwargs["shot_id"]}],
        }

    monkeypatch.setattr(factory_cli, "generate_pet_sitcom_shots", generate)
    monkeypatch.setattr(
        factory_cli, "build_pet_shot_evidence", lambda *_args: {}
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "shots", "--enable-live"
    )

    assert status == 0
    assert generated == [shot.shot_id for shot in plan.shots]
    assert payload["completed_count"] == 10


def test_retry_candidate_requires_exactly_one_shot(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)

    status, payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--candidate",
        "2",
        "--retry-reason",
        "continuity",
        "--enable-live",
    )

    assert status == 1
    assert "exactly one --shot" in payload["blocked_reasons"][0]


def test_later_retry_candidate_still_requires_one_shot_and_reason(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)

    status, payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--candidate",
        "5",
        "--enable-live",
    )

    assert status == 1
    assert "allowed retry reason" in payload["blocked_reasons"][0]


@pytest.mark.parametrize("candidate", [2, 3, 4, 5])
def test_retry_candidate_uses_failed_review_gate_and_selection(
    candidate, tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    _allow_probe(monkeypatch)
    monkeypatch.setattr(
        factory_cli, "resolve_provider_profile", lambda _config: _profile()
    )
    monkeypatch.setattr(factory_cli, "_pet_video_client", lambda *_args: object())
    gates = []
    selected = []
    monkeypatch.setattr(
        factory_cli,
        "_require_failed_previous_pet_shot_review",
        lambda _plan, shot, number, reason: gates.append(
            (shot, number, reason)
        ),
    )
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_sitcom_shots",
        lambda _plan, **kwargs: {
            "success": True,
            "executed": True,
            "planned_count": 1,
            "completed_count": 1,
            "reused_count": 0,
            "errors": [],
            "shots": [{"shot_id": kwargs["shot_id"]}],
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "select_pet_shot_candidate",
        lambda _plan, shot, number: selected.append((shot, number)) or {},
    )
    monkeypatch.setattr(
        factory_cli, "build_pet_shot_evidence", lambda *_args: {}
    )

    status, _payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--shot",
        "shot_10",
        "--candidate",
        str(candidate),
        "--retry-reason",
        "continuity",
        "--enable-live",
    )

    assert status == 0
    assert gates == [("shot_10", candidate, "continuity")]
    assert selected == [("shot_10", candidate)]


def test_failed_previous_review_gate_uses_single_shot_validator(monkeypatch):
    plan = SimpleNamespace()
    monkeypatch.setattr(
        factory_cli,
        "validate_pet_shot_review",
        lambda _plan, shot: {
            "candidate": 1,
            "passed": False,
            "retry_reason": "continuity",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_pet_shot_reviews",
        lambda _plan: (_ for _ in ()).throw(AssertionError("full validator")),
    )

    factory_cli._require_failed_previous_pet_shot_review(
        plan, "shot_03", 2, "continuity"
    )


def test_review_uses_task6_source_and_review_gates(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    _allow_review_inputs(monkeypatch)
    calls = []
    monkeypatch.setattr(
        factory_cli,
        "build_source_evidence",
        lambda _plan: calls.append("build") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "_refresh_pet_incremental_evidence",
        lambda _plan: calls.append("incremental") or (),
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_source_evidence",
        lambda _plan: calls.append("source") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_pet_shot_reviews",
        lambda _plan: calls.append("reviews") or {"passed": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_owner_native_audio_review",
        lambda _plan: calls.append("owner") or {},
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "review")

    assert status == 0
    assert payload["executed"] is True
    assert calls == ["build", "incremental", "source", "reviews", "owner"]


def test_incremental_evidence_refresh_rebuilds_only_stale_shot(monkeypatch):
    plan = SimpleNamespace(
        shots=(
            SimpleNamespace(shot_id="shot_01"),
            SimpleNamespace(shot_id="shot_02"),
            SimpleNamespace(shot_id="shot_03"),
        )
    )
    repaired = set()
    validations = []
    builds = []

    def validate(_plan, shot_id):
        validations.append(shot_id)
        if shot_id == "shot_02" and shot_id not in repaired:
            raise PetSitcomReviewError("Continuity evidence is stale.")
        return {"passed": True}

    def build(_plan, shot_id):
        builds.append(shot_id)
        repaired.add(shot_id)
        return {}

    monkeypatch.setattr(factory_cli, "validate_pet_shot_review", validate)
    monkeypatch.setattr(factory_cli, "build_pet_shot_evidence", build)

    refreshed = factory_cli._refresh_pet_incremental_evidence(plan)

    assert refreshed == ("shot_02",)
    assert builds == ["shot_02"]
    assert validations == ["shot_01", "shot_02", "shot_02", "shot_03"]


def test_review_pending_is_blocked_without_compose(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    _allow_review_inputs(monkeypatch)
    monkeypatch.setattr(factory_cli, "build_source_evidence", lambda _plan: {})
    monkeypatch.setattr(factory_cli, "validate_source_evidence", lambda _plan: {})
    monkeypatch.setattr(
        factory_cli,
        "validate_pet_shot_reviews",
        lambda _plan: (_ for _ in ()).throw(
            PetSitcomReviewError("pending human review")
        ),
    )
    monkeypatch.setattr(
        factory_cli,
        "compose_pet_sitcom",
        lambda *_args: (_ for _ in ()).throw(AssertionError("compose")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "review")

    assert status == 1
    assert payload["executed"] is False
    assert "pending human review" in payload["blocked_reasons"][0]


def test_compose_reuses_current_sound_manifest_and_strong_entry(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    calls = []
    _allow_compose_gates(monkeypatch, calls)
    monkeypatch.setattr(
        factory_cli,
        "load_pet_sound_design",
        lambda _plan: calls.append("sound-load") or {},
    )
    monkeypatch.setattr(
        factory_cli,
        "prepare_pet_sound_design",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prepare")
        ),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "compose")

    assert status == 0
    assert payload["composed"] is True
    assert calls == [
        "source",
        "reviews",
        "owner",
        "sound-load",
        "compose",
        "final-build",
        "final-validate",
        "markdown",
    ]


def test_compose_prepares_approved_absolute_music_source(
    tmp_path, monkeypatch, capsys
):
    plan = _install_plan(monkeypatch, tmp_path)
    music = tmp_path / "music.m4a"
    music.write_bytes(b"music")
    calls = []
    _allow_compose_gates(monkeypatch, calls)
    monkeypatch.setattr(
        factory_cli,
        "prepare_pet_sound_design",
        lambda current, **kwargs: calls.append(
            ("sound-prepare", current, kwargs["music_source"])
        )
        or current.output_dir / "sound_design.json",
    )

    status, _payload = _run(
        monkeypatch,
        capsys,
        "--stage",
        "compose",
        "--music-source",
        str(music),
    )

    assert status == 0
    assert calls[:4] == ["source", "reviews", "owner", ("sound-prepare", plan, music)]
    assert calls[4] == "compose"


@pytest.mark.parametrize("kind", ["missing", "stale", "tampered"])
def test_compose_invalid_reused_sound_blocks_before_ffmpeg(
    kind, tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    calls = []
    _allow_compose_gates(monkeypatch, calls)
    monkeypatch.setattr(
        factory_cli,
        "load_pet_sound_design",
        lambda _plan: (_ for _ in ()).throw(
            PetSoundError(f"{kind} sound manifest")
        ),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "compose")

    assert status == 1
    assert "sound manifest" in payload["blocked_reasons"][0]
    assert "compose" not in calls
    assert "final-build" not in calls


def test_compose_source_gate_precedes_sound_and_ffmpeg(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    _allow_review_inputs(monkeypatch)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_reviews",
        lambda _plan, _selections: {
            "passed_count": 10,
            "owner_verified": True,
            "source_valid": True,
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "validate_source_evidence",
        lambda _plan: (_ for _ in ()).throw(
            PetSitcomReviewError("source evidence is stale")
        ),
    )
    monkeypatch.setattr(
        factory_cli,
        "load_pet_sound_design",
        lambda _plan: (_ for _ in ()).throw(AssertionError("sound")),
    )
    monkeypatch.setattr(
        factory_cli,
        "compose_pet_sitcom",
        lambda _plan: (_ for _ in ()).throw(AssertionError("compose")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "compose")

    assert status == 1
    assert "source evidence is stale" in payload["blocked_reasons"][0]


@pytest.mark.parametrize("kind", ["relative", "noncanonical", "symlink"])
def test_music_source_rejects_unsafe_paths(kind, tmp_path):
    source = tmp_path / "music.m4a"
    source.write_bytes(b"music")
    if kind == "relative":
        value = "music.m4a"
    elif kind == "noncanonical":
        child = tmp_path / "child"
        child.mkdir()
        value = str(child / ".." / "music.m4a")
    else:
        link = tmp_path / "music-link.m4a"
        link.symlink_to(source)
        value = str(link)

    with pytest.raises(PetSoundError):
        factory_cli._pet_music_source(value)


def test_music_source_accepts_absolute_canonical_regular_file(tmp_path):
    source = tmp_path / "music.m4a"
    source.write_bytes(b"music")

    assert factory_cli._pet_music_source(str(source)) == source


def test_status_route_is_pure_read_and_reports_exact_fields(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    state = {
        "plan_ready": True,
        "anchors_approved": True,
        "audio_ready": True,
        "audio_probe_approved": False,
        "selected_shot_count": 0,
        "shot_review_passed_count": 0,
        "sound_design_approved": False,
        "composition_ready": False,
        "next_stage": "audio-probe",
    }
    monkeypatch.setattr(factory_cli, "_pet_status", lambda _plan: state)
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_doubao_tts_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("TTS")),
    )
    monkeypatch.setattr(
        factory_cli,
        "compose_pet_sitcom",
        lambda _plan: (_ for _ in ()).throw(AssertionError("ffmpeg")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "status")

    assert status == 0
    assert payload["executed"] is False
    assert {key: payload[key] for key in state} == state
    assert "--stage audio-probe" in payload["next_command"]


def test_status_reuses_current_sound_in_next_compose_command(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_status",
        lambda _plan: {
            "plan_ready": True,
            "anchors_approved": True,
            "audio_ready": True,
            "audio_probe_approved": True,
            "selected_shot_count": 10,
            "shot_review_passed_count": 10,
            "sound_design_approved": True,
            "composition_ready": False,
            "next_stage": "compose",
        },
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "status")

    assert status == 0
    assert "--stage compose" in payload["next_command"]
    assert "--music-source" not in payload["next_command"]


def test_status_uses_only_persistent_inspectors_for_complete_state(
    monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    calls = []
    monkeypatch.setattr(
        factory_cli, "_pet_plan_is_current", lambda _plan: True
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: calls.append("anchors") or {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: calls.append("audio") or {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: calls.append("probe")
        or {"state": "approved", "approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_selections",
        lambda _plan, _audio: calls.append("selections")
        or {"count": 10, "sources": {}},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_reviews",
        lambda _plan, _selections: calls.append("reviews")
        or {
            "passed_count": 10,
            "owner_verified": True,
            "source_valid": True,
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_sound",
        lambda _plan: calls.append("sound") or {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_final_evidence",
        lambda _plan, _selections: calls.append("final")
        or {"valid": True},
    )
    for name in (
        "_require_approved_anchors",
        "load_pet_speech_assets",
        "require_approved_pet_audio_probe",
        "load_pet_sound_design",
        "validate_source_evidence",
        "validate_final_evidence",
    ):
        monkeypatch.setattr(
            factory_cli,
            name,
            lambda *_args, _name=name, **_kwargs: (
                (_ for _ in ()).throw(AssertionError(_name))
            ),
        )

    state = factory_cli._pet_status(plan)

    assert state == {
        "plan_ready": True,
        "anchors_approved": True,
        "audio_ready": True,
        "audio_probe_approved": True,
        "selected_shot_count": 10,
        "shot_review_passed_count": 10,
        "sound_design_approved": True,
        "composition_ready": True,
        "next_stage": "status",
    }
    assert calls == [
        "anchors",
        "audio",
        "probe",
        "selections",
        "reviews",
        "sound",
        "final",
    ]


@pytest.mark.parametrize(
    ("failed", "next_stage"),
    [
        ("plan", "plan"),
        ("anchors", "anchors"),
        ("audio", "audio"),
        ("probe", "audio-probe"),
        ("shots", "shots"),
        ("review", "review"),
        ("sound", "compose"),
        ("composition", "compose"),
    ],
)
def test_status_next_stage_is_first_incomplete_safe_stage(
    failed, next_stage, monkeypatch, tmp_path
):
    plan = _plan(tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_plan_is_current",
        lambda _plan: failed != "plan",
    )

    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": failed != "anchors"},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": failed != "audio"},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "missing" if failed == "probe" else "approved",
            "approved": failed != "probe",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_selections",
        lambda _plan, _audio: {
            "count": 9 if failed == "shots" else 10,
            "sources": {},
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_reviews",
        lambda _plan, _selections: {
            "passed_count": 9 if failed == "review" else 10,
            "owner_verified": True,
            "source_valid": True,
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_sound",
        lambda _plan: {"approved": failed != "sound"},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_final_evidence",
        lambda _plan, _selections: {
            "valid": failed != "composition",
        },
    )

    assert factory_cli._pet_status(plan)["next_stage"] == next_stage


def test_status_plan_tamper_and_symlink_return_false_without_rewrite(tmp_path):
    plan = _plan(tmp_path)
    plan.output_dir.mkdir(parents=True)
    original = json.dumps(plan.to_report())
    plan.plan_path.write_text(original, encoding="utf-8")
    assert factory_cli._pet_plan_is_current(plan) is True

    plan.plan_path.write_text('{"tampered": true}', encoding="utf-8")
    assert factory_cli._pet_plan_is_current(plan) is False
    assert plan.plan_path.read_text(encoding="utf-8") == '{"tampered": true}'

    plan.plan_path.unlink()
    target = tmp_path / "external-plan.json"
    target.write_text(original, encoding="utf-8")
    plan.plan_path.symlink_to(target)
    assert factory_cli._pet_plan_is_current(plan) is False
    assert target.read_text(encoding="utf-8") == original


def test_status_selection_preflight_rejects_tampered_drive_without_repair(
    tmp_path,
):
    plan = _plan(tmp_path)
    shot = plan.shots[0]
    shot.candidate_dir.mkdir(parents=True)
    candidate = shot.candidate_dir / "candidate_001.mp4"
    drive = plan.output_dir / "audio" / "drive" / "shot_01_drive.wav"
    drive.parent.mkdir(parents=True)
    drive.write_bytes(b"tampered")
    candidate.with_suffix(".provenance.json").write_text(
        json.dumps({"reference_audio_path": str(drive)}),
        encoding="utf-8",
    )
    plan.selection_path.write_text(
        json.dumps(
            {
                "shots": {
                    shot.shot_id: {
                        "status": "selected",
                        "candidate_number": 1,
                        "reference_audio_sha256": "0" * 64,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    before = drive.read_bytes()

    assert factory_cli._pet_selection_side_effect_preflight(plan, shot) is False
    assert drive.read_bytes() == before


def test_status_probe_validator_receives_read_only_bound_drive(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    drive = plan.output_dir / "audio" / "drive" / "shot_04_drive.wav"
    drive.parent.mkdir(parents=True)
    drive.write_bytes(b"current-drive")
    plan.audio_probe_path.write_text(
        json.dumps(
            {
                "drive_audio_path": str(drive),
                "drive_audio_sha256": hashlib.sha256(
                    b"current-drive"
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        factory_cli.pet_sitcom_audio_probe_module,
        "build_pet_drive_audio",
        lambda _plan, _shot_id, **_kwargs: drive,
    )

    def validator(current):
        calls.append(
            factory_cli.pet_sitcom_audio_probe_module.build_pet_drive_audio(
                current,
                "shot_04",
            )
        )
        return {"approved": True}

    validator.__module__ = (
        factory_cli.pet_sitcom_audio_probe_module.__name__
    )
    monkeypatch.setattr(
        factory_cli, "require_approved_pet_audio_probe", validator
    )

    result = factory_cli._pet_require_approved_probe_read_only(plan)

    assert result == {"approved": True}
    assert calls == [drive]


def test_pet_sitcom_pins_required_gateway_models():
    args = SimpleNamespace(
        timeout=10.0,
        submit_timeout=20.0,
        download_timeout=30.0,
        poll_interval=1.0,
        max_wait=40.0,
    )
    profile = _profile()

    image = factory_cli._pet_image_client(profile, args)
    video = factory_cli._pet_video_client(profile, args)

    assert image.config.model == "doubao-seedream-4-5"
    assert video.config.model == "doubao-seedance-2-0"


def test_pet_sitcom_sanitizes_credentials_urls_and_audio_data():
    attack = {
        "error": (
            "Authorization: Bearer secret-key "
            "https://gateway.test/v1/task data:audio/wav;base64,AAAA"
        ),
        "api_key": "another-secret",
        "response_body": {"audio": "AAAA"},
    }

    rendered = json.dumps(factory_cli._pet_sanitize(attack, ("secret-key",)))

    assert "secret-key" not in rendered
    assert "another-secret" not in rendered
    assert "gateway.test" not in rendered
    assert "data:audio" not in rendered
    assert "AAAA" not in rendered


def test_pet_sitcom_command_redacts_free_text_error(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli, "resolve_provider_profile", lambda _config: _profile()
    )
    attack = (
        "api_key=leaked-key url=https://host.test/v1 "
        "data:audio/wav;base64,LEAK shot_id=shot_07"
    )
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_sitcom_anchors",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(attack)),
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "anchors", "--enable-live"
    )

    rendered = json.dumps(payload)
    assert status == 1
    assert "leaked-key" not in rendered
    assert "host.test" not in rendered
    assert "data:audio" not in rendered
    assert "shot_id=shot_07" in payload["error"]


def test_pet_sitcom_registration_does_not_break_existing_commands():
    parser = factory_cli.build_parser()
    args = parser.parse_args(["provider-report"])
    assert args.command == "provider-report"


def test_file_sha256_helper_reads_in_bounded_blocks(tmp_path):
    path = tmp_path / "asset.bin"
    payload = b"x" * (1024 * 1024 + 7)
    path.write_bytes(payload)

    assert factory_cli._pet_file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_audio_probe_dry_run_never_calls_mutating_probe_gate(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "require_approved_pet_audio_probe",
        lambda _plan: (_ for _ in ()).throw(AssertionError("mutating gate")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "audio-probe")

    assert status == 0
    assert payload["executed"] is False


def test_blocked_shots_never_calls_mutating_probe_gate(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "require_approved_pet_audio_probe",
        lambda _plan: (_ for _ in ()).throw(AssertionError("mutating gate")),
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "missing",
            "approved": False,
            "reason": "audio-drive probe evidence is missing",
        },
        raising=False,
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "shots", "--enable-live"
    )

    assert status == 1
    assert "probe evidence is missing" in payload["blocked_reasons"][0]


def test_terminal_probe_state_blocks_without_live_retry(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_status",
        lambda _plan: {
            "plan_ready": True,
            "anchors_approved": True,
            "audio_ready": True,
            "audio_probe_approved": False,
            "selected_shot_count": 0,
            "shot_review_passed_count": 0,
            "sound_design_approved": False,
            "composition_ready": False,
            "next_stage": "blocked",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_status_blockers",
        lambda _plan: ["Seedance model does not support reference audio"],
        raising=False,
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "status")

    assert status == 0
    assert payload["next_stage"] == "blocked"
    assert "--stage status" in payload["next_command"]
    assert "--enable-live" not in payload["next_command"]


def test_stage_error_recomputes_first_incomplete_gate(
    tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli, "_require_approved_anchors", lambda _plan: None
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_doubao_tts_config",
        lambda _config: SimpleNamespace(resource_id="seed-tts-2.0"),
    )
    monkeypatch.setattr(
        factory_cli, "DoubaoTTSClient", lambda config: SimpleNamespace(config=config)
    )
    monkeypatch.setattr(
        factory_cli,
        "generate_pet_speech_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("TTS failed")),
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_status",
        lambda _plan: {
            "plan_ready": True,
            "anchors_approved": False,
            "audio_ready": False,
            "audio_probe_approved": False,
            "selected_shot_count": 0,
            "shot_review_passed_count": 0,
            "sound_design_approved": False,
            "composition_ready": False,
            "next_stage": "anchors",
        },
    )

    status, payload = _run(
        monkeypatch, capsys, "--stage", "audio", "--enable-live"
    )

    assert status == 1
    assert "--stage anchors" in payload["next_command"]


def test_next_command_round_trips_shell_metacharacters(tmp_path):
    plan = _plan(tmp_path)
    args = SimpleNamespace(
        config="/tmp/factory config;quoted'.json",
        music_source="/tmp/music source;take'1.m4a",
    )

    command = factory_cli._pet_next_command(plan, args, "compose")

    assert shlex.split(command) == [
        "python",
        "factory_cli.py",
        "--config",
        args.config,
        "pet-sitcom",
        "--stage",
        "compose",
        "--output-dir",
        str(plan.output_dir),
        "--music-source",
        args.music_source,
    ]


def test_selection_inspector_accepts_valid_retry_selection_history(
    tmp_path,
    monkeypatch,
):
    plan = _build_complete_persistent_status_fixture(
        tmp_path,
        monkeypatch,
    )
    document = json.loads(
        plan.selection_path.read_text(encoding="utf-8")
    )
    document["history"]["shot_01"] = [
        dict(document["shots"]["shot_01"])
    ]
    _write_json(plan.selection_path, document)

    audio = factory_cli._pet_inspect_audio(plan)
    selections = factory_cli._pet_inspect_selections(plan, audio)

    assert selections["reason"] == ""
    assert selections["count"] == 10


def test_selection_state_fields_detect_local_recut_by_schema_not_candidate():
    generation = factory_cli.pet_sitcom_generation_module
    local_state = {
        "schema_version": generation.PET_LOCAL_RECUT_SCHEMA,
        "candidate_number": 4,
    }
    provider_state = {
        "schema_version": generation.PET_SHOT_GENERATION_SCHEMA,
        "candidate_number": 6,
    }

    assert factory_cli._pet_selection_state_fields(local_state) == (
        generation._PET_LOCAL_RECUT_FIELDS
    )
    assert factory_cli._pet_selection_state_fields(provider_state) == (
        generation._PET_PROVENANCE_FIELDS
    )


def test_complete_persistent_status_is_process_free_and_tree_immutable(
    tmp_path, monkeypatch, capsys
):
    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(factory_cli, "load_config", lambda _path: {})
    monkeypatch.setattr(
        factory_cli,
        "build_pet_sitcom_plan",
        lambda *_args, **_kwargs: plan,
    )
    before = _tree_snapshot(plan.output_dir)

    def denied(*_args, **_kwargs):
        raise AssertionError("status attempted a process or filesystem write")

    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    for name in (
        "mkdir",
        "makedirs",
        "replace",
        "rename",
        "remove",
        "unlink",
        "rmdir",
        "utime",
    ):
        monkeypatch.setattr(os, name, denied)
    for name in (
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
        "unlink",
        "rename",
        "replace",
    ):
        monkeypatch.setattr(Path, name, denied)

    status, payload = _run(monkeypatch, capsys, "--stage", "status")

    assert status == 0
    assert {
        key: payload[key]
        for key in (
            "plan_ready",
            "anchors_approved",
            "audio_ready",
            "audio_probe_approved",
            "selected_shot_count",
            "shot_review_passed_count",
            "sound_design_approved",
            "composition_ready",
            "next_stage",
        )
    } == {
        "plan_ready": True,
        "anchors_approved": True,
        "audio_ready": True,
        "audio_probe_approved": True,
        "selected_shot_count": 10,
        "shot_review_passed_count": 10,
        "sound_design_approved": True,
        "composition_ready": True,
        "next_stage": "status",
    }
    assert _tree_snapshot(plan.output_dir) == before


def test_missing_task2_source_keeps_dry_probe_and_blocked_shots_immutable(
    tmp_path, monkeypatch, capsys
):
    from factory.pet_sitcom import build_pet_sitcom_plan, write_pet_sitcom_plan
    from tests.test_pet_sitcom_review import _write_current_task2_manifest

    plan = build_pet_sitcom_plan({}, output_dir=tmp_path / "missing-source")
    write_pet_sitcom_plan(plan)
    assets = _write_current_task2_manifest(plan, monkeypatch)
    assets[0].output_path.unlink()
    monkeypatch.setattr(factory_cli, "load_config", lambda _path: {})
    monkeypatch.setattr(
        factory_cli,
        "build_pet_sitcom_plan",
        lambda *_args, **_kwargs: plan,
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )
    monkeypatch.setattr(
        factory_cli,
        "require_approved_pet_audio_probe",
        lambda _plan: (_ for _ in ()).throw(AssertionError("mutating gate")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ffmpeg")
        ),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ffmpeg")
        ),
    )
    before = _tree_snapshot(plan.output_dir)

    dry_status, dry = _run(
        monkeypatch,
        capsys,
        "--stage",
        "audio-probe",
    )
    shot_status, shots = _run(
        monkeypatch,
        capsys,
        "--stage",
        "shots",
        "--enable-live",
    )

    assert dry_status == 0
    assert dry["executed"] is False
    assert dry["next_stage"] == "audio"
    assert shot_status == 1
    assert "probe evidence is missing" in shots["blocked_reasons"][0]
    assert _tree_snapshot(plan.output_dir) == before


@pytest.mark.parametrize("capability", ["unsupported", "inconclusive"])
def test_persistent_terminal_probe_is_fail_closed(
    capability, tmp_path, monkeypatch
):
    import factory.pet_sitcom_audio_probe as audio_probe

    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    report = json.loads(plan.audio_probe_path.read_text())
    for field in ("probe_mp4_path", "probe_mp4_sha256", "frame_evidence"):
        report.pop(field)
    report["capability"] = capability
    report["success"] = False
    if capability == "unsupported":
        report["http_status_code"] = 400
    else:
        report.update(
            audio_probe._inconclusive_outcome("durable-task-123")
        )
    _write_json(plan.audio_probe_path, report)

    state = factory_cli._pet_status(plan)
    reasons = factory_cli._pet_status_blockers(plan)
    args = SimpleNamespace(
        config="fixture.json",
        music_source="",
    )

    assert state["audio_probe_approved"] is False
    assert state["next_stage"] == "blocked"
    if capability == "unsupported":
        assert "does not support" in reasons[0]
    else:
        assert "uncertain" in reasons[0]
    command = factory_cli._pet_next_command(plan, args, "blocked")
    parsed = shlex.split(command)
    assert parsed[parsed.index("--output-dir") + 1] == str(plan.output_dir)
    assert "--stage status" in command
    assert "--enable-live" not in command


@pytest.mark.parametrize(
    ("text", "fields"),
    [
        ('{"value": NaN}', {"value"}),
        ('{"value": 1, "value": 2}', {"value"}),
        ('{"value": 1, "extra": 2}', {"value"}),
    ],
)
def test_strict_json_rejects_noncanonical_documents(tmp_path, text, fields):
    path = tmp_path / "evidence.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError):
        factory_cli._pet_read_strict_json(path, fields=fields)


def test_persistent_inspector_primitives_reject_bool_alias_escape_and_symlink(
    tmp_path,
):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    target = root / "asset.bin"
    target.write_bytes(b"asset")
    alias = child / ".." / "asset.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = root / "linked.bin"
    link.symlink_to(outside)

    with pytest.raises(ValueError):
        factory_cli._pet_finite_number(True)
    with pytest.raises(ValueError):
        factory_cli._pet_canonical_file(str(alias), root=root)
    with pytest.raises(ValueError):
        factory_cli._pet_canonical_file(str(outside), root=root)
    with pytest.raises(ValueError):
        factory_cli._pet_canonical_file(str(link), root=root)


def test_audio_inspector_rejects_schema_numeric_path_and_symlink_tamper(
    tmp_path, monkeypatch
):
    from factory.pet_sitcom import build_pet_sitcom_plan
    from tests.test_pet_sitcom_review import _write_current_task2_manifest

    plan = build_pet_sitcom_plan({}, output_dir=tmp_path / "audio-tamper")
    assets = _write_current_task2_manifest(plan, monkeypatch)
    original = plan.audio_manifest_path.read_text()

    document = json.loads(original)
    document["extra"] = True
    _write_json(plan.audio_manifest_path, document)
    assert factory_cli._pet_inspect_audio(plan)["ready"] is False

    document = json.loads(original)
    document["assets"][0]["duration_seconds"] = True
    _write_json(plan.audio_manifest_path, document)
    assert factory_cli._pet_inspect_audio(plan)["ready"] is False

    plan.audio_manifest_path.write_text(
        original.replace('"duration_seconds": 1.0', '"duration_seconds": NaN', 1),
        encoding="utf-8",
    )
    assert factory_cli._pet_inspect_audio(plan)["ready"] is False

    document = json.loads(original)
    source = assets[0].output_path
    alias = source.parent / "alias" / ".." / source.name
    document["assets"][0]["output_path"] = str(alias)
    _write_json(plan.audio_manifest_path, document)
    assert factory_cli._pet_inspect_audio(plan)["ready"] is False

    plan.audio_manifest_path.write_text(original, encoding="utf-8")
    payload = source.read_bytes()
    source.unlink()
    external = tmp_path / "external.wav"
    external.write_bytes(payload)
    source.symlink_to(external)
    assert factory_cli._pet_inspect_audio(plan)["ready"] is False


def test_pre_profile_error_redacts_raw_credentials_url_and_data_uri(
    tmp_path, monkeypatch, capsys
):
    attack = (
        "api_key=bare-secret https://provider.test/v1 "
        "data:audio/wav;base64,LEAK"
    )
    monkeypatch.setattr(
        factory_cli,
        "load_config",
        lambda _path: (_ for _ in ()).throw(ValueError(attack)),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", "anchors")

    rendered = json.dumps(payload)
    assert status == 1
    assert "bare-secret" not in rendered
    assert "provider.test" not in rendered
    assert "data:audio" not in rendered
    assert "LEAK" not in rendered


def test_real_subprocess_pet_and_non_pet_cli_smoke(tmp_path):
    output = tmp_path / "subprocess output;quoted"
    pet = subprocess.run(
        [
            sys.executable,
            "factory_cli.py",
            "--config",
            "config/factory.config.json",
            "pet-sitcom",
            "--stage",
            "plan",
            "--output-dir",
            str(output),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    provider = subprocess.run(
        [
            sys.executable,
            "factory_cli.py",
            "--config",
            "config/factory.config.json",
            "provider-report",
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert pet.returncode == 0, pet.stderr
    assert json.loads(pet.stdout)["stage"] == "plan"
    assert provider.returncode == 0, provider.stderr
    assert isinstance(json.loads(provider.stdout), dict)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("duration_seconds", 54.5),
        ("stream_duration_seconds", 54.5),
        ("sample_rate", 96_000),
        ("channels", 1),
        ("codec_type", "video"),
        ("codec_name", "ac3"),
        ("channel_layout", "mono"),
        ("looped", True),
    ],
)
def test_status_rejects_each_invalid_sound_source_contract_field(
    field, invalid, tmp_path, monkeypatch
):
    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    manifest = json.loads(
        (plan.output_dir / "sound_design.json").read_text()
    )
    manifest["source"][field] = invalid
    _write_json(plan.output_dir / "sound_design.json", manifest)

    state = factory_cli._pet_status(plan)
    args = SimpleNamespace(config="fixture.json", music_source="")
    command = factory_cli._pet_next_command(plan, args, "compose")

    assert state["sound_design_approved"] is False
    assert state["composition_ready"] is False
    assert state["next_stage"] == "compose"
    assert manifest["source"]["path"] in shlex.split(command)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("path", "/tmp/not-the-fixed-stem.wav"),
        ("sha256", "0" * 64),
        ("duration_seconds", 1.0),
        ("stream_duration_seconds", 1.0),
        ("sample_rate", 44_100),
        ("channels", 1),
        ("codec_type", "video"),
        ("codec_name", "aac"),
        ("channel_layout", "mono"),
        ("source_sha256", "1" * 64),
        ("approval_sha256", "2" * 64),
        ("config_sha256", "3" * 64),
        ("binding_sha256", "4" * 64),
    ],
)
def test_status_rejects_each_invalid_sound_stem_contract_field(
    field, invalid, tmp_path, monkeypatch
):
    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    manifest_path = plan.output_dir / "sound_design.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["stems"]["music"][field] = invalid
    _write_json(manifest_path, manifest)

    state = factory_cli._pet_status(plan)

    assert state["sound_design_approved"] is False
    assert state["composition_ready"] is False
    assert state["next_stage"] == "compose"


@pytest.mark.parametrize(
    "mutation",
    [
        f"{artifact}-{operation}"
        for artifact in (
            "shot-sheet",
            "mouth",
            "prop",
            "continuity",
            "qc",
            "incremental",
        )
        for operation in ("delete", "hash", "path-alias", "symlink")
    ],
)
def test_status_review_count_requires_complete_source_and_incremental_evidence(
    mutation, tmp_path, monkeypatch
):
    import factory.pet_sitcom_review as review

    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    manifest_path = review._source_manifest_path(plan)
    manifest = json.loads(manifest_path.read_text())
    artifact, operation = mutation.rsplit("-", 1)
    if mutation.endswith("path-alias"):
        artifact = mutation.removesuffix("-path-alias")
        operation = "path-alias"

    def mutate_bound_record(record, *, path_field, hash_field):
        path = Path(record[path_field])
        if operation == "delete":
            path.unlink()
        elif operation == "hash":
            record[hash_field] = "0" * 64
        elif operation == "path-alias":
            record[path_field] = str(
                path.parent / "alias" / ".." / path.name
            )
        else:
            target = path.with_name(f"{path.stem}.target{path.suffix}")
            path.rename(target)
            path.symlink_to(target)

    if artifact == "shot-sheet":
        mutate_bound_record(
            manifest["shot_sheets"][0],
            path_field="evidence_path",
            hash_field="evidence_sha256",
        )
        _write_json(manifest_path, manifest)
    elif artifact == "mouth":
        mutate_bound_record(
            manifest["mouth_sequences"]["shot_03"],
            path_field="evidence_path",
            hash_field="evidence_sha256",
        )
        _write_json(manifest_path, manifest)
    elif artifact == "prop":
        mutate_bound_record(
            manifest["prop_sequences"]["bag"]["shot_01"],
            path_field="evidence_path",
            hash_field="evidence_sha256",
        )
        _write_json(manifest_path, manifest)
    elif artifact == "continuity":
        mutate_bound_record(
            manifest["continuity_comparisons"][0],
            path_field="evidence_path",
            hash_field="evidence_sha256",
        )
        _write_json(manifest_path, manifest)
    elif artifact == "qc":
        qc_record = {
            "path": manifest["source_technical_qc_path"],
            "sha256": manifest["source_technical_qc_sha256"],
        }
        mutate_bound_record(
            qc_record,
            path_field="path",
            hash_field="sha256",
        )
        manifest["source_technical_qc_path"] = qc_record["path"]
        manifest["source_technical_qc_sha256"] = qc_record["sha256"]
        _write_json(manifest_path, manifest)
    else:
        incremental_path = (
            review._evidence_root(plan)
            / "incremental"
            / "shot_01.json"
        )
        if operation == "delete":
            incremental_path.unlink()
        elif operation == "symlink":
            target = incremental_path.with_name("shot_01.target.json")
            incremental_path.rename(target)
            incremental_path.symlink_to(target)
        else:
            incremental = json.loads(incremental_path.read_text())
            mutate_bound_record(
                incremental["shot_sheet"],
                path_field="evidence_path",
                hash_field="evidence_sha256",
            )
            _write_json(incremental_path, incremental)
    before = _tree_snapshot(plan.output_dir)

    def denied(*_args, **_kwargs):
        raise AssertionError("status attempted subprocess or write repair")

    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    state = factory_cli._pet_status(plan)

    assert state["shot_review_passed_count"] < 10
    assert state["composition_ready"] is False
    assert state["next_stage"] == "review"
    assert _tree_snapshot(plan.output_dir) == before


@pytest.mark.parametrize("stage", ["audio", "audio-probe"])
def test_dry_audio_stages_fail_at_anchors_before_old_downstream_evidence(
    stage, tmp_path, monkeypatch, capsys
):
    _install_plan(monkeypatch, tmp_path)
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_anchors",
        lambda _plan: {"approved": False},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_audio",
        lambda _plan: {"ready": True},
    )
    monkeypatch.setattr(
        factory_cli,
        "_pet_inspect_probe",
        lambda _plan, _audio: {
            "state": "approved",
            "approved": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        factory_cli,
        "resolve_provider_profile",
        lambda _config: (_ for _ in ()).throw(AssertionError("provider")),
    )

    status, payload = _run(monkeypatch, capsys, "--stage", stage)

    assert status == 1
    assert payload["success"] is False
    assert payload["next_stage"] == "anchors"
    assert "anchor" in payload["blocked_reasons"][0].lower()


def test_status_accepts_current_ac3_sound_binding_without_subprocess(
    tmp_path,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    manifest_path = plan.output_dir / "sound_design.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source"]["codec_name"] = "ac3"
    binding_base = sound._binding_base(
        plan=plan,
        plan_sha256=manifest["plan_sha256"],
        source=Path(manifest["source"]["path"]),
        source_sha256=manifest["source"]["sha256"],
        source_metadata=manifest["source"],
        approval_sha256=manifest["approval"]["sha256"],
        config_sha256=manifest["config_sha256"],
    )
    binding = sound._binding_sha256(
        binding_base,
        manifest["stems_content_root_sha256"],
    )
    manifest["binding_sha256"] = binding
    for stem in manifest["stems"].values():
        stem["binding_sha256"] = binding
    _write_json(manifest_path, manifest)
    before = _tree_snapshot(plan.output_dir)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("status subprocess")
        ),
    )

    state = factory_cli._pet_status(plan)

    assert state["sound_design_approved"] is True
    assert state["composition_ready"] is True
    assert _tree_snapshot(plan.output_dir) == before


@pytest.mark.parametrize(
    "mutation",
    ["missing", "stale", "verified-false", "field-conflict"],
)
def test_owner_review_failure_keeps_status_at_review(
    mutation,
    tmp_path,
    monkeypatch,
):
    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    owner_path = plan.output_dir / "owner_native_audio_review.json"
    owner = json.loads(owner_path.read_text())
    if mutation == "missing":
        owner_path.unlink()
    elif mutation == "stale":
        first = next(iter(owner["shots"].values()))
        first["selected_mp4_sha256"] = "0" * 64
        _write_json(owner_path, owner)
    elif mutation == "verified-false":
        owner["verified"] = False
        _write_json(owner_path, owner)
    else:
        owner["reviewer_method"] = "automated"
        _write_json(owner_path, owner)

    state = factory_cli._pet_status(plan)

    if mutation != "missing":
        assert state["shot_review_passed_count"] == 10
    assert state["composition_ready"] is False
    assert state["next_stage"] == "review"


def test_invalid_middle_shot_review_still_counts_later_current_reviews(
    tmp_path,
    monkeypatch,
):
    plan = _build_complete_persistent_status_fixture(tmp_path, monkeypatch)
    reviews = json.loads(plan.shot_review_path.read_text())
    reviews["shots"]["shot_03"]["selected_mp4_sha256"] = "0" * 64
    _write_json(plan.shot_review_path, reviews)

    state = factory_cli._pet_status(plan)

    assert state["shot_review_passed_count"] == 9
    assert state["composition_ready"] is False
    assert state["next_stage"] == "review"


def test_status_and_strong_review_gate_use_persisted_qc_duration(
    tmp_path,
    monkeypatch,
):
    import factory.pet_sitcom_review as review

    plan = _build_complete_persistent_status_fixture(
        tmp_path,
        monkeypatch,
        qc_duration_overrides={"shot_02": 4.2},
    )
    reviews = json.loads(plan.shot_review_path.read_text())
    for gate in reviews["shots"]["shot_02"]["gates"].values():
        gate["timestamps_seconds"] = [0.1, 2.5, 4.1]
    _write_json(plan.shot_review_path, reviews)

    strong = review.validate_pet_shot_reviews(plan)
    current = factory_cli._pet_status(plan)

    assert strong["passed"] is True
    assert strong["durations"]["shot_02"] == 4.2
    assert current["shot_review_passed_count"] == 10
    assert current["composition_ready"] is True
    assert current["next_stage"] == "status"

    reviews = json.loads(plan.shot_review_path.read_text())
    for gate in reviews["shots"]["shot_02"]["gates"].values():
        gate["timestamps_seconds"] = [0.1, 2.5, 4.25]
    _write_json(plan.shot_review_path, reviews)

    with pytest.raises(PetSitcomReviewError, match="outside"):
        review.validate_pet_shot_reviews(plan)
    stale = factory_cli._pet_status(plan)
    assert stale["shot_review_passed_count"] == 9
    assert stale["next_stage"] == "review"
