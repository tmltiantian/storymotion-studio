from __future__ import annotations

import json
import shutil
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
    DEFAULT_DEPENDENCIES,
    apply_impact_plan,
    load_active_repair_scope,
    preview_impact,
    registered_preserved_artifacts,
)
from factory.pipeline_migration import migrate_existing_project
from factory.pipeline_modes import get_mode_adapter
from factory import pipeline_store
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
        Path(f"{clip}.gateway.json").write_text(
            json.dumps({"status": "completed", "shot_id": clip.stem}),
            encoding="utf-8",
        )
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
            (
                str(manifest.resolve()),
                *(str(path.resolve()) for path in clips),
                *(
                    str(Path(f"{path}.gateway.json").resolve())
                    for path in clips
                ),
            )
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


def test_legacy_missing_character_ids_targets_every_character_shot(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    episode_path = root / "stages" / "storyboard" / "episode.json"
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    episode["shots"][1].pop("character_ids")
    episode_path.write_text(json.dumps(episode), encoding="utf-8")

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.ASSETS, character_ids=("char_a",)),
    )

    assert plan.affected[StageName.VIDEO] == (
        "shot_01",
        "shot_02",
        "shot_03",
    )


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
    payload = json.loads(preview_path.read_text(encoding="utf-8"))
    assert "affected" not in payload


@pytest.mark.parametrize(
    ("stage", "kwargs"),
    (
        (StageName.DELIVER, {"dialogue_ids": ("d1",)}),
        (StageName.SCRIPT, {"character_ids": ("char_a",)}),
        (StageName.ASSETS, {"shot_ids": ("shot_01",)}),
        (StageName.STORYBOARD, {"scope": ChangeScope.SUBTITLE_STYLE}),
        (StageName.SCRIPT, {"scope": ChangeScope.DIALOGUE}),
        (StageName.ASSETS, {"scope": ChangeScope.CHARACTER}),
        (StageName.STORYBOARD, {"scope": ChangeScope.SHOT}),
    ),
)
def test_change_request_rejects_illegal_origin_or_empty_item_scope(
    stage: StageName, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        ChangeRequest(stage=stage, **kwargs)


def test_default_dependencies_is_the_authoritative_expansion_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    monkeypatch.setitem(
        DEFAULT_DEPENDENCIES,
        ChangeScope.SHOT.value,
        {StageName.VIDEO: "shot_ids"},
    )

    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )

    assert plan.affected == {StageName.VIDEO: ("shot_03",)}


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
    assert Path(f"{shot_01}.gateway.json").resolve() in (
        registered_preserved_artifacts(root)
    )
    assert Path(f"{shot_02}.gateway.json").resolve() in (
        registered_preserved_artifacts(root)
    )
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


def test_apply_rejects_added_noncanonical_plan_field(tmp_path: Path) -> None:
    root = _project(tmp_path)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    path = root / "impact_plans" / f"{plan.plan_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["affected"] = {"video": ["shot_01"]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="content has changed"):
        apply_impact_plan(root, plan.plan_id)


def test_preview_and_apply_reject_symlinked_project_and_plan_paths(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    alias = tmp_path / "project-alias"
    alias.symlink_to(root, target_is_directory=True)
    request = ChangeRequest(
        stage=StageName.STORYBOARD, shot_ids=("shot_03",)
    )

    with pytest.raises(ValueError, match="symlink"):
        preview_impact(alias, request)

    outside = tmp_path / "outside-plans"
    outside.mkdir()
    plans = root / "impact_plans"
    plans.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        preview_impact(root, request)
    assert not tuple(outside.iterdir())

    plans.unlink()
    plan = preview_impact(root, request)
    plan_path = plans / f"{plan.plan_id}.json"
    moved = tmp_path / "moved-plan.json"
    shutil.move(plan_path, moved)
    plan_path.symlink_to(moved)
    with pytest.raises(ValueError, match="symlink"):
        apply_impact_plan(root, plan.plan_id)


def test_apply_rejects_pipeline_in_progress(tmp_path: Path) -> None:
    import fcntl

    root = _project(tmp_path)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    before = (root / "production_package.json").read_bytes()

    with (root / ".pipeline.lock").open("a+", encoding="utf-8") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already running"):
            apply_impact_plan(root, plan.plan_id)

    assert (root / "production_package.json").read_bytes() == before
    assert not (root / "impact_plans" / "active.json").exists()


def test_interrupted_apply_recovers_active_and_package_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    _register_video_artifacts(root)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    original_save = pipeline_store.save_production_package

    def interrupt_after_package(project_dir, package):
        original_save(project_dir, package)
        raise KeyboardInterrupt("simulated process interruption")

    monkeypatch.setattr(
        pipeline_store, "save_production_package", interrupt_after_package
    )
    with pytest.raises(KeyboardInterrupt, match="simulated"):
        apply_impact_plan(root, plan.plan_id)
    monkeypatch.setattr(pipeline_store, "save_production_package", original_save)

    recovered = apply_impact_plan(root, plan.plan_id)

    assert load_active_repair_scope(root)[StageName.VIDEO.value] == ("shot_03",)
    assert next(
        record for record in recovered.stages if record.stage is StageName.VIDEO
    ).state is StageState.STALE
    transactions = root / "impact_plans" / ".transactions"
    assert not transactions.exists() or not any(transactions.iterdir())


def test_active_scope_read_recovers_interrupted_active_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    _register_video_artifacts(root)
    plan = preview_impact(
        root,
        ChangeRequest(stage=StageName.STORYBOARD, shot_ids=("shot_03",)),
    )
    original_finish = pipeline_store._finish_repair_transaction

    def interrupt_active_publication(project_dir, transaction):
        raise OSError("simulated active publication interruption")

    monkeypatch.setattr(
        pipeline_store,
        "_finish_repair_transaction",
        interrupt_active_publication,
    )
    with pytest.raises(OSError, match="active publication"):
        apply_impact_plan(root, plan.plan_id)
    monkeypatch.setattr(
        pipeline_store, "_finish_repair_transaction", original_finish
    )

    assert load_active_repair_scope(root)[StageName.VIDEO.value] == ("shot_03",)
    transactions = root / "impact_plans" / ".transactions"
    assert not any(transactions.iterdir())


def test_preview_uses_registered_migrated_episode_snapshot(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    episode = {
        "project_id": "legacy-impact",
        "title": "Legacy",
        "language": "zh-CN",
        "style": "comic",
        "target_aspect_ratio": "9:16",
        "target_resolution": "1080x1920",
        "characters": [{"id": "char_a"}, {"id": "char_b"}],
        "shots": [
            {
                "id": "shot_legacy",
                "index": 1,
                "character_ids": ["char_a", "char_b"],
                "dialogue": [],
            }
        ],
    }
    (legacy / "episode.json").write_text(json.dumps(episode), encoding="utf-8")
    root = tmp_path / "migrated"
    migrate_existing_project(
        legacy,
        root,
        project_id="legacy-impact",
        title="Legacy",
        mode=ProjectMode.NOVEL,
    )

    plan = preview_impact(
        root,
        ChangeRequest(
            stage=StageName.STORYBOARD, shot_ids=("shot_legacy",)
        ),
    )

    assert plan.affected[StageName.VIDEO] == ("shot_legacy",)


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
