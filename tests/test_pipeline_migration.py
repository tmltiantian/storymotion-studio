from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.pipeline_contracts import ProjectMode, StageName, StageState
from factory.pipeline_migration import migrate_existing_project
from factory.pipeline_store import load_production_package, load_project_spec


def _write(path: Path, content: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_migrates_novel_run_without_moving_legacy_artifacts(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy_novel"
    episode = _write(legacy / "episode.json")
    audio = _write(legacy / "voiceover" / "voiceover.m4a", "audio")
    preview = _write(legacy / "final_preview.mp4", "video")
    destination = tmp_path / "runs" / "novel_migrated"

    result = migrate_existing_project(
        legacy,
        destination,
        project_id="novel_migrated",
        title="旧小说成片",
        mode=ProjectMode.NOVEL,
    )

    package = load_production_package(destination)
    states = {record.stage: record.state for record in package.stages}
    assert result["legacy_root"] == str(legacy.resolve())
    assert states[StageName.SCRIPT] is StageState.PASSED
    assert states[StageName.AUDIO] is StageState.PASSED
    assert states[StageName.EDIT] is StageState.PASSED
    assert states[StageName.EVAL] is StageState.PENDING
    assert episode.exists() and audio.exists() and preview.exists()
    assert load_project_spec(destination).input["path"] == str(legacy.resolve())


def test_migrates_replica_eval_only_when_explicitly_passed(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy_replica"
    _write(legacy / "reference" / "shot_timeline.json")
    _write(legacy / "assets" / "asset_manifest.json")
    _write(legacy / "audio" / "audio_manifest.json")
    _write(legacy / "shots" / "R001.mp4", "video")
    final = _write(legacy / "deliveries" / "master.mp4", "video")
    _write(legacy / "final" / "releases" / "run" / "normalized" / "R001.mp4", "clip")
    evaluation = _write(
        legacy / "deliveries" / "eval_result.json",
        json.dumps({"status": "PASS"}),
    )
    destination = tmp_path / "runs" / "replica_migrated"

    migrate_existing_project(
        legacy,
        destination,
        project_id="replica_migrated",
        title="参考复刻",
        mode=ProjectMode.REPLICA,
    )

    package = load_production_package(destination)
    states = {record.stage: record.state for record in package.stages}
    assert states[StageName.EVAL] is StageState.PASSED
    assert states[StageName.DELIVER] is StageState.PASSED
    assert package.final_outputs == (str(final.resolve()),)
    assert package.eval_reports == (str(evaluation.resolve()),)
    assert load_project_spec(destination).output_dir == destination.resolve() / "output"


def test_migration_prefers_eval_bound_master_over_old_release_candidates(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy_replica"
    selected = _write(legacy / "final" / "releases" / "selected" / "master.mp4", "selected")
    _write(legacy / "final" / "releases" / "old" / "old_master.mp4", "old")
    evaluation = {
        "status": "PASS",
        "artifact_manifest": {"video": {"path": str(selected.resolve())}},
    }
    _write(
        legacy / "deliveries" / "eval_result.json",
        json.dumps(evaluation),
    )

    migrate_existing_project(
        legacy,
        tmp_path / "runs" / "bound",
        project_id="bound",
        title="Bound",
        mode=ProjectMode.REPLICA,
    )

    package = load_production_package(tmp_path / "runs" / "bound")
    assert package.final_outputs == (str(selected.resolve()),)


def test_migration_refuses_existing_destination(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    destination = tmp_path / "project"
    destination.mkdir()
    _write(destination / "project.json")

    with pytest.raises(FileExistsError):
        migrate_existing_project(
            legacy,
            destination,
            project_id="existing",
            title="Existing",
            mode=ProjectMode.ORIGINAL,
        )
