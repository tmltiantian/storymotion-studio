from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.pipeline_artifacts import (
    load_stage_manifest,
    stage_manifest_integrity_issue,
    stage_dir,
    write_stage_manifest,
)
from factory.pipeline_context import StageContext
from factory.pipeline_contracts import ProjectMode, ProjectSpec, StageName
from factory.pipeline_modes import get_mode_adapter


def _context(tmp_path: Path, stage: StageName = StageName.CONCEPT) -> StageContext:
    project_dir = tmp_path / "runs" / "episode"
    spec = ProjectSpec(
        project_id="episode",
        title="Episode",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "两只猫调查纸盒"},
        output_dir=(tmp_path / "output" / "episode").resolve(),
    )
    return StageContext(
        project_dir=project_dir,
        spec=spec,
        stage=stage,
        step=get_mode_adapter(spec.mode).stage_steps[stage],
        enable_live=False,
    )


def test_stage_context_owns_stable_isolated_directory(tmp_path: Path) -> None:
    context = _context(tmp_path, StageName.SCRIPT)

    assert context.stage_dir == tmp_path / "runs/episode/stages/script"
    assert context.stage_dir.is_dir()
    assert stage_dir(context.project_dir, StageName.AUDIO) != context.stage_dir


def test_stage_manifest_registers_only_real_files_inside_stage(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    artifact = context.stage_dir / "concept.json"
    artifact.write_text("{}", encoding="utf-8")

    manifest_path = write_stage_manifest(
        context,
        artifacts=(artifact,),
        metadata={"summary": "box mystery"},
    )
    manifest = load_stage_manifest(context.project_dir, StageName.CONCEPT)

    assert manifest_path == context.stage_dir / "manifest.json"
    assert manifest["stage"] == "concept"
    assert manifest["mode"] == "original"
    assert manifest["artifacts"] == ["concept.json"]
    assert set(manifest["artifact_sha256"]) == {"concept.json"}
    assert manifest["metadata"]["summary"] == "box mystery"
    assert not list(context.stage_dir.glob(".*.tmp"))

    artifact.write_text('{"changed":true}', encoding="utf-8")
    assert "changed after completion" in stage_manifest_integrity_issue(
        context.project_dir, StageName.CONCEPT
    )


def test_stage_manifest_rejects_external_or_missing_artifact(tmp_path: Path) -> None:
    context = _context(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="inside its stage directory"):
        write_stage_manifest(context, artifacts=(outside,))
    with pytest.raises(ValueError, match="regular file"):
        write_stage_manifest(context, artifacts=(context.stage_dir / "missing.json",))


def test_context_reads_prior_stage_artifact_but_rejects_future_stage(
    tmp_path: Path,
) -> None:
    concept = _context(tmp_path, StageName.CONCEPT)
    artifact = concept.stage_dir / "concept.json"
    artifact.write_text(json.dumps({"idea": "box"}), encoding="utf-8")
    write_stage_manifest(concept, artifacts=(artifact,))
    script = _context(tmp_path, StageName.SCRIPT)

    assert script.require_artifact(StageName.CONCEPT, "concept.json") == artifact
    with pytest.raises(ValueError, match="future stage"):
        script.require_manifest(StageName.VIDEO)


def test_context_prefers_direct_contract_over_same_named_snapshot(
    tmp_path: Path,
) -> None:
    assets = _context(tmp_path, StageName.ASSETS)
    direct = assets.stage_dir / "asset_review.json"
    snapshot = assets.stage_dir / "snapshot/assets/asset_review.json"
    direct.write_text('{"kind":"contract"}', encoding="utf-8")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text('{"kind":"legacy"}', encoding="utf-8")
    write_stage_manifest(assets, artifacts=(direct, snapshot))
    audio = _context(tmp_path, StageName.AUDIO)

    assert audio.require_artifact(StageName.ASSETS, "asset_review.json") == direct
