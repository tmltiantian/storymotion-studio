from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory import pipeline_replica_stages
from factory.pipeline_artifacts import load_stage_manifest
from factory.pipeline_contracts import ProjectMode, ProjectSpec, StageName, StageState
from factory.pipeline_runner import run_pipeline
from factory.pipeline_store import approve_stage, create_project


def _project(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"reference-video")
    root = tmp_path / "runs" / "replica"
    workspace = tmp_path / "replica-workspace"
    create_project(
        root,
        ProjectSpec(
            project_id="replica",
            title="Replica",
            mode=ProjectMode.REPLICA,
            input={"kind": "reference", "path": str(source)},
            output_dir=workspace.resolve(),
            mode_options={"replica_workspace": str(workspace)},
        ),
    )
    return root, workspace


def _fake_operation(context, operation):
    workspace = Path(context.spec.mode_options["replica_workspace"])
    if operation == "plan":
        (workspace / "reference").mkdir(parents=True, exist_ok=True)
        (workspace / "reference/shot_timeline.json").write_text(
            '{"shots":[]}', encoding="utf-8"
        )
        (workspace / "reference/source_binding.json").write_text(
            '{}', encoding="utf-8"
        )
        (workspace / "story_contract.md").write_text("contract", encoding="utf-8")
        return 0, {"success": True, "stage": "plan"}
    if operation == "reference":
        (workspace / "reference/evidence_manifest.json").write_text(
            '{}', encoding="utf-8"
        )
        (workspace / "reference/shot_annotations.json").write_text(
            '{}', encoding="utf-8"
        )
        return 0, {"success": True, "annotations_reviewed": True}
    if operation == "assets":
        (workspace / "assets").mkdir(parents=True, exist_ok=True)
        (workspace / "assets/asset_manifest.json").write_text(
            '{}', encoding="utf-8"
        )
        (workspace / "assets/asset_review.json").write_text(
            '{}', encoding="utf-8"
        )
        return 0, {"success": True, "approved": True}
    if operation == "audio":
        (workspace / "audio/drive").mkdir(parents=True, exist_ok=True)
        (workspace / "audio/audio_manifest.json").write_text(
            '{}', encoding="utf-8"
        )
        (workspace / "audio/drive/R001.wav").write_bytes(b"audio")
        return 0, {"success": True, "shot_count": 1}
    if operation == "generate":
        (workspace / "shots/R001").mkdir(parents=True, exist_ok=True)
        (workspace / "shots/R001/candidate_01.mp4").write_bytes(b"video")
        return 0, {"success": True, "completed_count": 1}
    if operation == "review":
        (workspace / "review").mkdir(parents=True, exist_ok=True)
        (workspace / "review/R001.json").write_text('{}', encoding="utf-8")
        return 1, {"success": False, "reviewed_count": 1}
    if operation == "compose":
        (workspace / "final").mkdir(parents=True, exist_ok=True)
        (workspace / "final/final.mp4").write_bytes(b"release")
        return 0, {"success": True, "mode": "final"}
    raise AssertionError(operation)


def test_replica_stages_map_source_locked_work_into_standard_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _workspace = _project(tmp_path)
    monkeypatch.setattr(pipeline_replica_stages, "_run_operation", _fake_operation)

    assets = run_pipeline(root, through=StageName.ASSETS)

    assert assets.stopped_at is StageName.ASSETS
    assert assets.stopped_state is StageState.BLOCKED
    assert load_stage_manifest(root, StageName.CONCEPT)["executor_id"] == "replica.concept"
    script = json.loads((root / "stages/script/script.json").read_text(encoding="utf-8"))
    assert script["adaptation_mode"] == "source_locked_replica"
    assert (root / "stages/storyboard/snapshot/reference/shot_timeline.json").is_file()
    review = json.loads((root / "stages/assets/asset_review.json").read_text(encoding="utf-8"))
    assert review["approved"] is True


def test_replica_continues_from_asset_review_to_release_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _workspace = _project(tmp_path)
    monkeypatch.setattr(pipeline_replica_stages, "_run_operation", _fake_operation)
    run_pipeline(root, through=StageName.ASSETS)
    approve_stage(
        root,
        StageName.ASSETS,
        note="源片标注和替换资产已检查",
        evidence=(root / "stages/assets/asset_review.json",),
    )

    result = run_pipeline(root, through=StageName.EVAL, enable_live=True)

    assert result.stopped_at is StageName.EVAL
    assert result.stopped_state is StageState.BLOCKED
    assert (root / "stages/audio/audio_manifest.json").is_file()
    assert (root / "stages/video/snapshot/shots/R001/candidate_01.mp4").is_file()
    assert (root / "stages/edit/edit_manifest.json").is_file()

    approve_stage(
        root,
        StageName.EVAL,
        note="候选镜头逐镜通过",
        evidence=(root / "stages/eval/eval_result.json",),
    )
    delivery = run_pipeline(root, through=StageName.DELIVER, enable_live=True)

    assert delivery.stopped_at is StageName.DELIVER
    assert delivery.stopped_state is StageState.BLOCKED
    assert (root / "stages/deliver/release/final/final.mp4").read_bytes() == b"release"
