from __future__ import annotations

import json
import argparse
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import factory_cli
from factory.micro_video_batch import MicroVideoBatchError
from factory.quality_production_runner import (
    QualityProductionRunnerError,
    run_quality_production_candidates,
    write_quality_visual_selection,
)
from factory.schema import Character, Episode, Shot, episode_to_dict
from factory.visual_timeline import (
    MicroShot,
    VisualTimeline,
    visual_timeline_to_dict,
)


VIDEO_MODEL = "doubao-seedance-2-0"
STILL_MODEL = "doubao-seedream-4-5"


def _micro_shot(micro_id: str, index: int, *, character: bool) -> MicroShot:
    return MicroShot(
        id=micro_id,
        index=index,
        parent_shot_id="shot_001",
        scene_context="旧店",
        time_context="source-unspecified",
        purpose="reaction" if character else "object",
        character_ids=("char_1",) if character else (),
        emotion_start="警惕" if character else "沉在暗部",
        emotion_end="惊讶" if character else "边缘显出冷光",
        emotion_intensity=4 if character else 1,
        gaze="看向黑色信封",
        pose_start="站在柜台前" if character else "信封放在柜台上",
        pose_end="肩线绷紧" if character else "信封保持不动",
        action_actor_id="char_1" if character else "object",
        action_code="eyes_widen" if character else "hold_still",
        action_target="黑色信封",
        camera_mode="locked" if character else "object_insert",
        source_duration_seconds=4,
        timeline_duration_seconds=2.0,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_text", "no_scene_change"),
        cadence_fps=8,
    )


def _fixture(tmp_path: Path) -> tuple[dict, Path]:
    run_dir = (tmp_path / "runs" / "sample").resolve()
    run_dir.mkdir(parents=True)
    episode = Episode(
        project_id="sample",
        title="样片",
        language="zh-CN",
        style="动态漫画",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character(
                id="char_1",
                name="林澈",
                role="主角",
                description="黑发青年",
                visual_anchor="深蓝外套",
                voice_style="沉稳",
            )
        ],
        shots=[
            Shot(
                id="shot_001",
                index=1,
                scene_title="旧店",
                action="林澈在旧店柜台前看向黑色信封。",
                visual_prompt="旧店柜台与黑色信封。",
                camera="固定机位",
                duration_seconds=4.0,
                audio_mood="紧张",
            )
        ],
    )
    timeline = VisualTimeline(
        project_id="sample",
        micro_shots=(
            _micro_shot("micro_001", 1, character=True),
            _micro_shot("micro_002", 2, character=False),
        ),
    )
    reference = run_dir / "assets" / "characters" / "char_1.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    artifacts = {
        "episode.json": episode_to_dict(episode),
        "visual_timeline.json": visual_timeline_to_dict(timeline),
        "character_assets.json": {
            "project_id": "sample",
            "production_ready": True,
            "characters": [
                {
                    "character_id": "char_1",
                    "reference_image_path": str(reference),
                    "asset_source": "user_generated_ai",
                    "provenance_status": "confirmed",
                    "production_ready": True,
                }
            ],
        },
        "model_bakeoff_report.json": {
            "project_id": "sample",
            "run_dir": str(run_dir),
        },
    }
    for name, payload in artifacts.items():
        (run_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    return {"runsDir": str(tmp_path / "runs")}, run_dir


def _candidate_qc_evidence(run_dir: Path, candidate: Path, micro_shot_id: str) -> tuple[Path, dict[str, str], dict[str, str], dict[str, str]]:
    frames: dict[str, str] = {}
    hashes: dict[str, str] = {}
    sample_hashes: dict[str, str] = {}
    samples = []
    for index in range(1, 10):
        label = {1: "first_frame", 5: "middle_frame", 9: "last_frame"}.get(index, "")
        frame = run_dir / f"{micro_shot_id}.sample_{index:02d}.png"
        frame.write_bytes((label or str(index)).encode())
        stat = frame.stat()
        digest = hashlib.sha256(frame.read_bytes()).hexdigest()
        sample_hashes[f"sample_{index:02d}"] = digest
        samples.append({"evidence": {"path": str(frame), "sha256": digest, "size_bytes": stat.st_size, "device": stat.st_dev, "inode": stat.st_ino}})
        if label:
            frames[label] = str(frame)
            hashes[label] = digest
    report = run_dir / f"{micro_shot_id}.visual_qc.json"
    report.write_text(json.dumps({
        "schema_version": "motion-comic-factory.visual-qc.v2",
        "candidate_evidence": {"path": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()},
        "sample_frames": samples, "automatic_passed": True,
        "manual_review": {}, "passed": True,
    }), encoding="utf-8")
    return report, frames, hashes, sample_hashes


def _profile():
    capability = SimpleNamespace(
        provider="gateway",
        ready=True,
        blockers=(),
        api_key="do-not-leak",
        base_url="https://gateway.example/v1",
        model="ignored",
    )
    return SimpleNamespace(video=capability, image=capability)


def _dry_route_result(count: int) -> dict:
    return {
        "plan_ready": True,
        "executed": False,
        "success": True,
        "planned_count": count,
        "completed_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "blocked_count": count,
        "errors": [],
    }


def test_production_batch_fails_before_renderers_when_bakeoff_gate_fails(
    tmp_path, monkeypatch
):
    config, _ = _fixture(tmp_path)
    calls: list[str] = []

    def reject(_report):
        raise ValueError("no selected model")

    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        reject,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        lambda _report: STILL_MODEL,
    )

    with pytest.raises(QualityProductionRunnerError, match="bakeoff gate"):
        run_quality_production_candidates(
            config,
            "sample",
            allow_network=True,
            profile_resolver=lambda _: _profile(),
            video_renderer=lambda *args, **kwargs: calls.append("video"),
            still_renderer=lambda *args, **kwargs: calls.append("still"),
        )

    assert calls == []


def test_production_wraps_invalid_job_build_as_operator_error(
    tmp_path, monkeypatch
):
    config, _ = _fixture(tmp_path)
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        lambda _report: VIDEO_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        lambda _report: STILL_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.build_micro_video_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MicroVideoBatchError("invalid visual timeline")
        ),
    )

    with pytest.raises(
        QualityProductionRunnerError,
        match="Production candidate planning failed: invalid visual timeline",
    ):
        run_quality_production_candidates(
            config,
            "sample",
            profile_resolver=lambda _: _profile(),
        )


def test_production_dry_run_plans_all_routes_with_selected_models(
    tmp_path, monkeypatch
):
    config, run_dir = _fixture(tmp_path)
    video_calls: list[dict] = []
    still_calls: list[dict] = []
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        lambda _report: VIDEO_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        lambda _report: STILL_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.build_micro_video_jobs",
        lambda *_args, **_kwargs: [SimpleNamespace(micro_shot_id="micro_001")],
    )

    def video_renderer(jobs, root, gateway_config, **kwargs):
        video_calls.append(
            {
                "ids": [job.micro_shot_id for job in jobs],
                "model": gateway_config.model,
                **kwargs,
            }
        )
        return {
            "plan_ready": True,
            "executed": False,
            "success": False,
            "planned_count": len(jobs),
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_count": len(jobs),
            "errors": [],
        }

    def still_renderer(episode, timeline, **kwargs):
        still_calls.append(kwargs)
        return {
            "plan_ready": True,
            "executed": False,
            "success": True,
            "planned_count": 1,
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_count": 1,
            "errors": [],
        }

    report = run_quality_production_candidates(
        config,
        "sample",
        allow_network=False,
        profile_resolver=lambda _: _profile(),
        video_renderer=video_renderer,
        still_renderer=still_renderer,
    )

    assert report["success"] is True
    assert report["executed"] is False
    assert report["planned_count"] == 2
    assert video_calls[0]["ids"] == ["micro_001"]
    assert video_calls[0]["model"] == VIDEO_MODEL
    assert video_calls[0]["allow_network"] is False
    assert still_calls[0]["model"] == STILL_MODEL
    assert still_calls[0]["allow_network"] is False
    persisted = (run_dir / "quality_production_candidates.json").read_text(
        encoding="utf-8"
    )
    assert "do-not-leak" not in persisted


@pytest.mark.parametrize(
    ("kind", "expected_ids", "forbidden_gate", "forbidden_renderer"),
    [
        ("video", ["micro_001"], "still", "still"),
        ("still", ["micro_002"], "video", "video"),
    ],
)
def test_production_kind_without_explicit_targets_filters_to_eligible_route(
    tmp_path,
    monkeypatch,
    kind,
    expected_ids,
    forbidden_gate,
    forbidden_renderer,
):
    config, _ = _fixture(tmp_path)
    calls: list[tuple[str, list[str]]] = []

    def selected_video(_report):
        if forbidden_gate == "video":
            pytest.fail("unselected video model gate must not execute")
        return VIDEO_MODEL

    def selected_still(_report):
        if forbidden_gate == "still":
            pytest.fail("unselected still model gate must not execute")
        return STILL_MODEL

    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        selected_video,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        selected_still,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.build_micro_video_jobs",
        lambda *_args, **_kwargs: [SimpleNamespace(micro_shot_id="micro_001")],
    )

    def video_renderer(jobs, *_args, **_kwargs):
        if forbidden_renderer == "video":
            pytest.fail("unselected video renderer must not execute")
        calls.append(("video", [job.micro_shot_id for job in jobs]))
        return _dry_route_result(len(jobs))

    def still_renderer(_episode, _timeline, **kwargs):
        if forbidden_renderer == "still":
            pytest.fail("unselected still renderer must not execute")
        calls.append(("still", list(kwargs["micro_shot_ids"])))
        return _dry_route_result(len(kwargs["micro_shot_ids"]))

    report = run_quality_production_candidates(
        config,
        "sample",
        kind=kind,
        profile_resolver=lambda _: _profile(),
        video_renderer=video_renderer,
        still_renderer=still_renderer,
    )

    assert report["micro_shot_ids"] == expected_ids
    assert report["planned_count"] == 1
    assert calls == [(kind, expected_ids)]


def test_production_filters_exact_micro_shots_before_route_execution(
    tmp_path, monkeypatch
):
    config, _ = _fixture(tmp_path)
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        lambda _report: VIDEO_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        lambda _report: STILL_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.build_micro_video_jobs",
        lambda *_args, **_kwargs: [SimpleNamespace(micro_shot_id="micro_001")],
    )

    def video_renderer(jobs, *_args, **_kwargs):
        calls.append(("video", [job.micro_shot_id for job in jobs]))
        return {
            "plan_ready": True,
            "executed": False,
            "success": True,
            "planned_count": len(jobs),
            "completed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_count": len(jobs),
            "errors": [],
        }

    report = run_quality_production_candidates(
        config,
        "sample",
        micro_shot_ids=("micro_001",),
        allow_network=False,
        profile_resolver=lambda _: _profile(),
        video_renderer=video_renderer,
        still_renderer=lambda *_args, **_kwargs: pytest.fail(
            "unselected still route must not execute"
        ),
    )

    assert report["micro_shot_ids"] == ["micro_001"]
    assert report["planned_count"] == 1
    assert calls == [("video", ["micro_001"])]


@pytest.mark.parametrize(
    ("micro_shot_ids", "kind", "message"),
    [
        (("missing",), "all", "Unknown production micro-shot ID"),
        (("micro_001", "micro_001"), "all", "must not contain duplicates"),
        (("micro_002",), "video", "not eligible for the video route"),
        (("micro_001",), "still", "not eligible for the still route"),
    ],
)
def test_production_rejects_invalid_targeted_micro_shots_before_rendering(
    tmp_path, monkeypatch, micro_shot_ids, kind, message
):
    config, _ = _fixture(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_production_model",
        lambda _report: VIDEO_MODEL,
    )
    monkeypatch.setattr(
        "factory.quality_production_runner.require_selected_still_model",
        lambda _report: STILL_MODEL,
    )

    with pytest.raises(QualityProductionRunnerError, match=message):
        run_quality_production_candidates(
            config,
            "sample",
            micro_shot_ids=micro_shot_ids,
            kind=kind,
            profile_resolver=lambda _: _profile(),
            video_renderer=lambda *_args, **_kwargs: calls.append("video"),
            still_renderer=lambda *_args, **_kwargs: calls.append("still"),
        )

    assert calls == []


def test_visual_selection_rejects_arbitrary_selection_json(
    tmp_path, monkeypatch
):
    config, run_dir = _fixture(tmp_path)
    candidate = run_dir / "micro_clips" / "micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"video")
    still = run_dir / "micro_stills" / "micro_002" / STILL_MODEL / "candidate_001.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"still")
    input_path = run_dir / "visual_selection.reviewed.json"
    selection = {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": "sample",
        "selected_candidates": {
            "micro_001": {
                "kind": "video",
                "candidate_path": str(candidate),
                "qc_report_path": str(run_dir / "micro_qc" / "visual_qc.json"),
            },
            "micro_002": {
                "kind": "still",
                "candidate_path": str(still),
                "size_bytes": len(b"still"),
                "sha256": "0" * 64,
                "score": 90,
                "hard_failures": [],
                "notes": "locally reviewed",
            },
        },
    }
    input_path.write_text(json.dumps(selection), encoding="utf-8")
    captured: dict = {}

    def validate_sources(episode, timeline, payload, **kwargs):
        captured.update(
            {
                "episode": episode,
                "timeline": timeline,
                "payload": payload,
                **kwargs,
            }
        )
        return [
            SimpleNamespace(kind="video"),
            SimpleNamespace(kind="still"),
        ]

    monkeypatch.setattr(
        "factory.quality_production_runner.select_micro_sources",
        validate_sources,
    )

    with pytest.raises(
        QualityProductionRunnerError,
        match="candidate_review.json",
    ):
        write_quality_visual_selection(config, "sample", input_path)


def test_visual_selection_rejects_input_outside_project_run(tmp_path):
    config, _ = _fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(QualityProductionRunnerError, match="inside the project run"):
        write_quality_visual_selection(config, "sample", outside)


def test_visual_selection_is_built_from_the_approved_candidate_manifest(
    tmp_path, monkeypatch
):
    config, run_dir = _fixture(tmp_path)
    timeline_payload = json.loads((run_dir / "visual_timeline.json").read_text())
    timeline_payload["micro_shots"][1]["character_ids"] = ["char_1"]
    (run_dir / "visual_timeline.json").write_text(
        json.dumps(timeline_payload), encoding="utf-8"
    )
    candidates = []
    jobs = []
    for micro_shot_id in ("micro_001", "micro_002"):
        candidate = run_dir / f"{micro_shot_id}.mp4"
        candidate.write_bytes(micro_shot_id.encode())
        qc_report, frames, hashes, sample_hashes = _candidate_qc_evidence(run_dir, candidate, micro_shot_id)
        jobs.append({
            "micro_shot_id": micro_shot_id, "output_path": str(candidate),
            "output_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "reference_audio_sha256": "", "entry_anchor_id": "scene_entry",
        })
        candidates.append({
            "micro_shot_id": micro_shot_id,
            "candidate_path": str(candidate),
            "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "state": "approved", "audio_sha256": "", "entry_anchor_id": "scene_entry",
            "visual_qc_report_path": str(qc_report),
            "visual_qc_report_sha256": hashlib.sha256(qc_report.read_bytes()).hexdigest(),
            "first_frame_sha256": hashes["first_frame"],
            "middle_frame_sha256": hashes["middle_frame"],
            "last_frame_sha256": hashes["last_frame"],
            "sample_frame_sha256": sample_hashes,
            "rendered_job_report_path": "", "rendered_job_report_sha256": "",
            "reason": "reviewed",
            "evidence": {**frames, "review_note": "approved"},
        })
    job_report = run_dir / "micro_video_batch.json"
    job_report.write_text(json.dumps({
        "schema_version": "motion-comic-factory.micro-video-batch.v1",
        "project_id": "sample", "run_dir": str(run_dir), "success": True,
        "completed_count": 2, "jobs": jobs,
    }), encoding="utf-8")
    for record in candidates:
        record["rendered_job_report_path"] = str(job_report)
        record["rendered_job_report_sha256"] = hashlib.sha256(job_report.read_bytes()).hexdigest()
    manifest_path = run_dir / "candidate_review.json"
    manifest_path.write_text(json.dumps({
        "schema_version": "motion-comic-factory.candidate-review.v1",
        "project_id": "sample", "candidates": candidates,
    }), encoding="utf-8")
    captured = {}

    def validate_sources(_episode, _timeline, selection, **kwargs):
        captured["selection"] = selection
        assert kwargs["candidate_review"] is not None
        return [SimpleNamespace(kind="video"), SimpleNamespace(kind="video")]

    monkeypatch.setattr(
        "factory.quality_production_runner.select_micro_sources", validate_sources
    )

    report = write_quality_visual_selection(config, "sample", manifest_path)

    assert report["selected_count"] == 2
    assert list(captured["selection"]["selected_candidates"]) == [
        "micro_001", "micro_002"
    ]


def test_quality_production_commands_are_registered():
    parser = factory_cli.build_parser()

    bakeoff = parser.parse_args(
        [
            "quality-bakeoff-candidates",
            "--project",
            "sample",
            "--candidate",
            "2",
            "--kind",
            "video",
            "--micro-shot",
            "micro_018",
        ]
    )
    production = parser.parse_args(
        [
            "quality-production-candidates",
            "--project",
            "sample",
            "--candidate",
            "2",
            "--kind",
            "video",
            "--micro-shot",
            "micro_001",
            "--limit",
            "3",
        ]
    )
    selection = parser.parse_args(
        [
            "quality-select",
            "--project",
            "sample",
            "--selection",
            "selection.json",
        ]
    )

    assert bakeoff.micro_shot == ["micro_018"]
    assert production.func is factory_cli.quality_production_candidates_command
    assert production.enable_live is False
    assert production.candidate == 2
    assert production.kind == "video"
    assert production.micro_shot == ["micro_001"]
    assert production.limit == 3
    assert selection.func is factory_cli.quality_select_command
    assert selection.selection == "selection.json"


def test_cli_quality_production_forwards_explicit_network_gate(
    monkeypatch, capsys, tmp_path
):
    captured: dict = {}

    def fake_runner(config, project_id, **kwargs):
        captured.update(
            {"config": config, "project_id": project_id, **kwargs}
        )
        return {
            "plan_ready": True,
            "executed": True,
            "success": True,
            "planned_count": 4,
            "completed_count": 3,
            "skipped_count": 1,
            "failed_count": 0,
            "blocked_count": 0,
            "blocked_reasons": [],
            "selected_video_model": VIDEO_MODEL,
            "selected_still_model": STILL_MODEL,
        }

    monkeypatch.setattr(factory_cli, "load_config", lambda _: {"runsDir": str(tmp_path)})
    monkeypatch.setattr(
        factory_cli,
        "run_quality_production_candidates",
        fake_runner,
    )
    args = argparse.Namespace(
        config="ignored.json",
        project="sample",
        candidate=2,
        kind="all",
        limit=4,
        overwrite=False,
        enable_live=True,
        timeout=120.0,
        submit_timeout=300.0,
        download_timeout=120.0,
        poll_interval=3.0,
        max_wait=900.0,
    )

    code = factory_cli.quality_production_candidates_command(args)

    assert code == 0
    assert captured["project_id"] == "sample"
    assert captured["candidate_number"] == 2
    assert captured["allow_network"] is True
    assert captured["limit"] == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed_count"] == 3
    assert payload["selected_video_model"] == VIDEO_MODEL


def test_cli_quality_select_publishes_validated_selection(
    monkeypatch, capsys, tmp_path
):
    captured: dict = {}

    def fake_writer(config, project_id, selection_path):
        captured.update(
            {
                "config": config,
                "project_id": project_id,
                "selection_path": selection_path,
            }
        )
        return {
            "success": True,
            "output_path": str(tmp_path / "visual_selection.json"),
            "selected_count": 19,
            "video_count": 12,
            "still_count": 7,
        }

    monkeypatch.setattr(factory_cli, "load_config", lambda _: {"runsDir": str(tmp_path)})
    monkeypatch.setattr(factory_cli, "write_quality_visual_selection", fake_writer)
    args = argparse.Namespace(
        config="ignored.json",
        project="sample",
        selection="reviewed.json",
    )

    code = factory_cli.quality_select_command(args)

    assert code == 0
    assert captured["project_id"] == "sample"
    assert captured["selection_path"] == "reviewed.json"
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_count"] == 19
    assert payload["still_count"] == 7
