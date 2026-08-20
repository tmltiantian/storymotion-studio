from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import factory_cli
import factory.quality_bakeoff_runner as bakeoff_runner
import factory.quality_production_runner as production_runner

from factory.quality_bakeoff_runner import run_quality_bakeoff_candidates
from factory.schema import Character, Episode, Shot, episode_to_dict
from factory.visual_timeline import (
    MicroShot,
    VisualTimeline,
    visual_timeline_to_dict,
)


def test_quality_runners_share_common_helpers() -> None:
    assert bakeoff_runner._count is production_runner._count
    assert bakeoff_runner._live_blockers is production_runner._live_blockers
    assert bakeoff_runner._write_atomic_json is production_runner._write_atomic_json


def _shot(
    micro_id: str,
    index: int,
    *,
    character: bool,
    purpose: str = "action",
) -> MicroShot:
    return MicroShot(
        id=micro_id,
        index=index,
        parent_shot_id="shot_001",
        scene_context="旧店",
        time_context="source-unspecified",
        purpose=purpose,
        character_ids=("char_1",) if character else (),
        emotion_start="警惕" if character else "静止",
        emotion_end="惊讶" if character else "静止",
        emotion_intensity=4 if character else 1,
        gaze="看向信封",
        pose_start="站在柜台前" if character else "信封放在柜台上",
        pose_end="肩线绷紧" if character else "信封仍在柜台上",
        action_actor_id="char_1" if character else "object",
        action_code="eyes_widen" if character else "hold_still",
        action_target="信封",
        camera_mode="locked" if character else "object_insert",
        source_duration_seconds=5 if character else 4,
        timeline_duration_seconds=3.0,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_text", "no_scene_change"),
        cadence_fps=8,
    )


def _fixture(tmp_path: Path) -> tuple[dict, Path]:
    run_dir = tmp_path / "runs" / "sample"
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
            ),
            Character(
                id="char_2",
                name="苏眠",
                role="主角",
                description="黑发青年女性",
                visual_anchor="浅色针织衫",
                voice_style="克制",
            ),
        ],
        shots=[
            Shot(
                id="shot_001",
                index=1,
                scene_title="旧店",
                action="林澈在旧店柜台前看向信封。",
                visual_prompt="旧店柜台与黑色信封。",
                camera="固定机位",
                duration_seconds=9.0,
                audio_mood="紧张",
            )
        ],
    )
    timeline = VisualTimeline(
        project_id="sample",
        micro_shots=(
            _shot("micro_001", 1, character=True),
            _shot("micro_002", 2, character=True),
            _shot("micro_003", 3, character=False, purpose="object"),
        ),
    )
    role = run_dir / "assets" / "characters" / "lin_che.png"
    role.parent.mkdir(parents=True)
    role.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    su_mian = run_dir / "assets" / "characters" / "su_mian.png"
    su_mian.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    artifacts = {
        "episode.json": episode_to_dict(episode),
        "visual_timeline.json": visual_timeline_to_dict(timeline),
        "character_assets.json": {
            "project_id": "sample",
            "production_ready": True,
            "characters": [
                {
                    "character_id": "char_1",
                    "reference_image_path": str(role),
                    "asset_source": "user_generated_ai",
                    "provenance_status": "confirmed",
                    "production_ready": True,
                },
                {
                    "character_id": "char_2",
                    "reference_image_path": str(su_mian),
                    "asset_source": "user_generated_ai",
                    "provenance_status": "confirmed",
                    "production_ready": True,
                }
            ],
        },
        "model_bakeoff_plan.json": {
            "schema_version": "motion-comic-factory.model-bakeoff-plan.v1",
            "project_id": "sample",
            "run_dir": str(run_dir),
            "representative_character_micro_shot_ids": [
                "micro_001",
                "micro_002",
            ],
            "requires_still": True,
            "still_micro_shot_id": "micro_003",
            "video_models": [
                "doubao-seedance-2-0",
            ],
            "still_models": ["doubao-seedream-4-5"],
            "minimum_score": 80,
            "max_candidates_per_model": 3,
        },
    }
    for name, payload in artifacts.items():
        (run_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    config = {"runsDir": str(tmp_path / "runs")}
    return config, run_dir


def _profile(*, video_ready: bool = True, image_ready: bool = True):
    def capability(ready: bool):
        return SimpleNamespace(
            provider="gateway",
            ready=ready,
            blockers=() if ready else ("missing gateway credential",),
            api_key="do-not-leak",
            base_url="https://gateway.example/v1",
            model="ignored",
        )

    return SimpleNamespace(
        video=capability(video_ready),
        image=capability(image_ready),
    )


def test_dry_run_plans_all_representative_models_without_network(tmp_path):
    config, run_dir = _fixture(tmp_path)
    video_calls: list[dict] = []
    still_calls: list[dict] = []

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

    report = run_quality_bakeoff_candidates(
        config,
        "sample",
        allow_network=False,
        profile_resolver=lambda _: _profile(),
        video_renderer=video_renderer,
        still_renderer=still_renderer,
    )

    assert report["plan_ready"] is True
    assert report["executed"] is False
    assert report["success"] is True
    assert [call["model"] for call in video_calls] == [
        "doubao-seedance-2-0",
    ]
    assert all(call["ids"] == ["micro_001", "micro_002"] for call in video_calls)
    assert all(call["allow_network"] is False for call in video_calls)
    assert [call["model"] for call in still_calls] == [
        "doubao-seedream-4-5",
    ]
    assert all(call["micro_shot_ids"] == ["micro_003"] for call in still_calls)
    persisted = (run_dir / "model_bakeoff_candidates.json").read_text(
        encoding="utf-8"
    )
    assert "do-not-leak" not in persisted


def test_bakeoff_can_target_one_planned_representative_video_shot(tmp_path):
    config, _ = _fixture(tmp_path)
    captured: list[str] = []

    def video_renderer(jobs, root, gateway_config, **kwargs):
        captured.extend(job.micro_shot_id for job in jobs)
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

    report = run_quality_bakeoff_candidates(
        config,
        "sample",
        candidate_number=2,
        kind="video",
        micro_shot_ids=["micro_002"],
        allow_network=False,
        profile_resolver=lambda _: _profile(),
        video_renderer=video_renderer,
    )

    assert captured == ["micro_002"]
    assert report["planned_count"] == 1


def test_live_run_fails_closed_before_partial_billing_when_capability_is_blocked(
    tmp_path,
):
    config, run_dir = _fixture(tmp_path)
    calls: list[str] = []

    report = run_quality_bakeoff_candidates(
        config,
        "sample",
        allow_network=True,
        profile_resolver=lambda _: _profile(image_ready=False),
        video_renderer=lambda *args, **kwargs: calls.append("video"),
        still_renderer=lambda *args, **kwargs: calls.append("still"),
    )

    assert calls == []
    assert report["plan_ready"] is True
    assert report["executed"] is False
    assert report["success"] is False
    assert report["blocked_reasons"] == [
        "image: missing gateway credential",
    ]
    assert (run_dir / "model_bakeoff_candidates.json").is_file()


def test_cli_quality_bakeoff_candidates_forwards_explicit_paid_gate(
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
            "planned_count": 6,
            "completed_count": 6,
            "skipped_count": 0,
            "failed_count": 0,
            "blocked_reasons": [],
        }

    monkeypatch.setattr(factory_cli, "load_config", lambda _: {"runsDir": str(tmp_path)})
    monkeypatch.setattr(
        factory_cli,
        "run_quality_bakeoff_candidates",
        fake_runner,
    )
    args = argparse.Namespace(
        config="ignored.json",
        project="sample",
        candidate=2,
        kind="all",
        overwrite=True,
        enable_live=True,
        timeout=120.0,
        submit_timeout=300.0,
        download_timeout=120.0,
        poll_interval=3.0,
        max_wait=900.0,
    )

    code = factory_cli.quality_bakeoff_candidates_command(args)

    assert code == 0
    assert captured["project_id"] == "sample"
    assert captured["candidate_number"] == 2
    assert captured["allow_network"] is True
    assert captured["overwrite"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["planned_count"] == 6
    assert payload["completed_count"] == 6


def test_cli_quality_visual_qc_analyzes_deterministic_candidate(
    monkeypatch, capsys, tmp_path
):
    config, run_dir = _fixture(tmp_path)
    candidate = (
        run_dir
        / "micro_clips"
        / "micro_001"
        / "doubao-seedance-2-0"
        / "candidate_001.mp4"
    )
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    captured: dict = {}

    def fake_analyze(candidate_path, micro_shot, **kwargs):
        captured.update(
            {
                "candidate_path": candidate_path,
                "micro_shot": micro_shot,
                **kwargs,
            }
        )
        return {
            "automatic_passed": True,
            "automatic_hard_failures": [],
            "passed": False,
            "contact_sheet": {
                "evidence": {"path": str(run_dir / "contact.png")}
            },
        }

    monkeypatch.setattr(factory_cli, "load_config", lambda _: config)
    monkeypatch.setattr(factory_cli, "analyze_visual_candidate", fake_analyze)
    args = argparse.Namespace(
        config="ignored.json",
        project="sample",
        micro_shot="micro_001",
        model="doubao-seedance-2-0",
        candidate=1,
        refresh=False,
        review="",
    )

    code = factory_cli.quality_visual_qc_command(args)

    assert code == 0
    assert captured["candidate_path"] == candidate
    assert captured["micro_shot"].id == "micro_001"
    assert captured["reference_image_labels"] == ("char_1",)
    assert str(captured["output_dir"]).endswith(
        "micro_qc/micro_001/doubao-seedance-2-0/candidate_001"
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["automatic_passed"] is True
    assert payload["passed"] is False


def test_cli_quality_finalize_bakeoff_writes_validated_selection(
    monkeypatch, capsys, tmp_path
):
    config, run_dir = _fixture(tmp_path)
    review_path = run_dir / "model_bakeoff_review.input.json"
    review_path.write_text('{"review":"input"}', encoding="utf-8")
    captured: dict = {}

    def fake_finalize(plan, reviews):
        captured.update({"plan": plan, "reviews": reviews})
        return {
            "production_ready": True,
            "selected_model": "doubao-seedance-2-0",
            "selected_still_model": "doubao-seedream-4-5",
            "video_results": [{"passed": True}],
            "still_results": [{"passed": True}],
        }

    monkeypatch.setattr(factory_cli, "load_config", lambda _: config)
    monkeypatch.setattr(factory_cli, "finalize_bakeoff", fake_finalize)
    args = argparse.Namespace(
        config="ignored.json",
        project="sample",
        review=str(review_path),
    )

    code = factory_cli.quality_finalize_bakeoff_command(args)

    assert code == 0
    assert captured["plan"]["project_id"] == "sample"
    assert captured["reviews"] == {"review": "input"}
    payload = json.loads(capsys.readouterr().out)
    assert payload["production_ready"] is True
    assert payload["selected_model"] == "doubao-seedance-2-0"
    assert payload["selected_still_model"] == "doubao-seedream-4-5"
    assert payload["error"] == ""
