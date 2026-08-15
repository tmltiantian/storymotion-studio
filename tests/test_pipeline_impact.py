from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.pipeline_context import StageContext
from factory.pipeline_contracts import (
    PIPELINE_STAGES,
    ProjectMode,
    ProjectSpec,
    StageName,
    StageState,
)
from factory.pipeline_impact import (
    ChangeScope,
    ChangeRequest,
    apply_impact_plan,
    load_active_repair_scope,
    preview_impact,
    registered_preserved_artifacts,
)
from factory.pipeline_modes import get_mode_adapter
from factory.pipeline_store import (
    ApprovalInProgressError,
    create_project,
    load_project_spec,
    update_stage,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    create_project(
        root,
        ProjectSpec(
            project_id="impact-test",
            title="Impact Test",
            mode=ProjectMode.ORIGINAL,
            input={"kind": "idea", "text": "A compact fixture"},
            output_dir=(tmp_path / "output").resolve(),
        ),
    )
    storyboard_dir = root / "stages" / "storyboard"
    storyboard_dir.mkdir(parents=True)
    (storyboard_dir / "episode.json").write_text(
        json.dumps(
            {
                "project_id": "impact-test",
                "title": "Impact Test",
                "language": "zh-CN",
                "style": "comic",
                "target_aspect_ratio": "9:16",
                "target_resolution": "1080x1920",
                "characters": [
                    {"id": "char_a"},
                    {"id": "char_b"},
                ],
                "shots": [
                    {
                        "id": "shot_01",
                        "index": 1,
                        "character_ids": ["char_a"],
                        "dialogue": [{"id": "d1", "speaker_id": "char_a"}],
                    },
                    {
                        "id": "shot_02",
                        "index": 2,
                        "character_ids": ["char_b"],
                        "dialogue": [],
                    },
                    {
                        "id": "shot_03",
                        "index": 3,
                        "character_ids": ["char_a", "char_b"],
                        "dialogue": [{"id": "d2", "speaker_id": "char_b"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def _register_video_artifacts(root: Path) -> tuple[Path, Path, Path]:
    video_dir = root / "stages" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    clips = tuple(video_dir / f"shot_{index:02d}.mp4" for index in range(1, 4))
    for clip in clips:
        clip.write_bytes(clip.name.encode("ascii"))
    manifest = video_dir / "video_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video.v1",
                "clips": [str(path.resolve()) for path in clips],
                "clip_by_shot": {
                    f"shot_{index:02d}": str(path.resolve())
                    for index, path in enumerate(clips, start=1)
                },
            }
        ),
        encoding="utf-8",
    )
    for stage in PIPELINE_STAGES:
        artifacts = (
            (str(manifest.resolve()), *(str(path.resolve()) for path in clips))
            if stage is StageName.VIDEO
            else ()
        )
        update_stage(
            root,
            stage,
            StageState.PASSED,
            executor="fixture",
            input_signature=f"sig-{stage.value}",
            artifacts=artifacts,
        )
    return clips


def test_single_dialogue_change_targets_only_bound_audio_and_video_shot(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.SCRIPT, dialogue_ids=("d2",)),
    )

    assert plan.affected[StageName.AUDIO] == ("d2",)
    assert plan.affected[StageName.VIDEO] == ("shot_03",)
    assert plan.affected[StageName.EDIT] == ("timeline",)


def test_character_change_targets_asset_and_every_bound_video_shot(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.ASSETS, character_ids=("char_a",)),
    )

    assert plan.affected[StageName.ASSETS] == ("char_a",)
    assert plan.affected[StageName.VIDEO] == ("shot_01", "shot_03")
    assert plan.affected[StageName.EDIT] == ("timeline",)


def test_subtitle_style_change_targets_only_finishing_dependencies(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.EDIT, scope=ChangeScope.SUBTITLE_STYLE),
    )

    assert plan.affected == {
        StageName.EDIT: ("subtitles",),
        StageName.EVAL: ("full",),
        StageName.DELIVER: ("full",),
    }


def test_preview_persists_immutable_plan_without_invalidating_state(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    _register_video_artifacts(root)
    before = (root / "production_package.json").read_bytes()

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    preview_path = root / "impact_plans" / f"{plan.plan_id}.json"
    persisted = preview_path.read_bytes()
    repeated = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )

    assert (root / "production_package.json").read_bytes() == before
    assert preview_path.read_bytes() == persisted
    assert repeated.plan_id == plan.plan_id
    assert not (root / "impact_plans" / "active.json").exists()


def test_apply_impact_keeps_unaffected_video_artifacts(tmp_path: Path) -> None:
    root = _project(tmp_path)
    shot_01, shot_02, shot_03 = _register_video_artifacts(root)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )

    package = apply_impact_plan(root, plan.plan_id)

    scope = load_active_repair_scope(root)
    assert scope[StageName.VIDEO.value] == ("shot_03",)
    assert shot_01.resolve() in registered_preserved_artifacts(root)
    assert shot_02.resolve() in registered_preserved_artifacts(root)
    assert shot_03.resolve() not in registered_preserved_artifacts(root)
    video = next(record for record in package.stages if record.stage is StageName.VIDEO)
    assert video.state is StageState.STALE
    assert str(shot_02.resolve()) in video.artifacts


def test_stage_context_loads_applied_repair_scope(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _register_video_artifacts(root)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    apply_impact_plan(root, plan.plan_id)
    adapter = get_mode_adapter(ProjectMode.ORIGINAL)

    context = StageContext(
        root,
        load_project_spec(root),
        StageName.VIDEO,
        adapter.stage_steps[StageName.VIDEO],
        False,
    )

    assert context.repair_scope[StageName.VIDEO.value] == ("shot_03",)


def test_apply_rejects_plan_after_package_state_changes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    update_stage(
        root,
        StageName.CONCEPT,
        StageState.PASSED,
        executor="fixture",
        input_signature="new-state",
    )

    with pytest.raises(ValueError, match="stale relative"):
        apply_impact_plan(root, plan.plan_id)


def test_apply_rejects_modified_immutable_preview(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    path = root / "impact_plans" / f"{plan.plan_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"][0]["item_ids"] = ["shot_01"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="content has changed"):
        apply_impact_plan(root, plan.plan_id)


def test_apply_impact_rejects_concurrent_review_mutation(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )

    import fcntl

    with (root / ".approval.lock").open("a+", encoding="utf-8") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ApprovalInProgressError):
            apply_impact_plan(root, plan.plan_id)
