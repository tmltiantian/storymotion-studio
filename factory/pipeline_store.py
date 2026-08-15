from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_io import sha256_file, write_json_atomic
from .pipeline_contracts import (
    PIPELINE_STAGES,
    ProductionPackage,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageRecord,
    StageState,
)
from .pipeline_modes import get_mode_adapter
from .pipeline_review import approve_stage_revision, write_stage_revision


SPEC_FILENAME = "project.json"
PACKAGE_FILENAME = "production_package.json"


def _require_safe_project_dir(project_dir: str | Path) -> Path:
    path = Path(project_dir).expanduser()
    if path.is_symlink():
        raise ValueError("project directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("project directory cannot be a symlink")
    return path.resolve()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def create_project(
    project_dir: str | Path,
    spec: ProjectSpec,
    *,
    overwrite: bool = False,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    spec_path = root / SPEC_FILENAME
    package_path = root / PACKAGE_FILENAME
    if not overwrite and (spec_path.exists() or package_path.exists()):
        raise FileExistsError(f"Unified project already exists: {root}")
    package = ProductionPackage.new(spec, spec_path=spec_path)
    write_json_atomic(spec_path, spec.to_dict())
    write_json_atomic(package_path, package.to_dict())
    return package


def load_project_spec(project_dir: str | Path) -> ProjectSpec:
    root = _require_safe_project_dir(project_dir)
    return ProjectSpec.from_dict(_read_object(root / SPEC_FILENAME))


def load_production_package(project_dir: str | Path) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    return ProductionPackage.from_dict(_read_object(root / PACKAGE_FILENAME))


def save_production_package(
    project_dir: str | Path, package: ProductionPackage
) -> Path:
    root = _require_safe_project_dir(project_dir)
    path = root / PACKAGE_FILENAME
    write_json_atomic(path, package.to_dict())
    return path


def _refresh_output_indexes(package: ProductionPackage) -> ProductionPackage:
    by_stage = {record.stage: record for record in package.stages}
    final_outputs: list[str] = []
    for stage in (StageName.EDIT, StageName.DELIVER):
        record = by_stage[stage]
        if record.state is not StageState.PASSED:
            continue
        for value in record.artifacts:
            path = Path(value).expanduser()
            if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".mkv"}:
                final_outputs.append(str(path.resolve()))
    eval_record = by_stage[StageName.EVAL]
    eval_reports = (
        tuple(
            str(Path(value).expanduser().resolve())
            for value in eval_record.artifacts
            if Path(value).expanduser().is_file()
            and Path(value).suffix.lower() == ".json"
        )
        if eval_record.state is StageState.PASSED
        else ()
    )
    return ProductionPackage(
        project_id=package.project_id,
        mode=package.mode,
        spec_path=package.spec_path,
        spec_sha256=package.spec_sha256,
        stages=package.stages,
        final_outputs=tuple(dict.fromkeys(final_outputs)),
        eval_reports=tuple(dict.fromkeys(eval_reports)),
        schema_version=package.schema_version,
    )


def update_stage(
    project_dir: str | Path,
    stage: StageName,
    state: StageState,
    *,
    executor: str = "",
    input_signature: str = "",
    artifacts: tuple[str, ...] = (),
    blocked_reasons: tuple[str, ...] = (),
    error: str = "",
    revision: int | None = None,
    review_policy: ReviewPolicy | None = None,
    review_state: ReviewState | None = None,
    review_blocks_progress: bool | None = None,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    spec = load_project_spec(root)
    package = load_production_package(root)
    if package.spec_sha256 != spec.sha256:
        raise ValueError("production package is stale relative to project.json")
    target = StageName(stage)
    current = next(record for record in package.stages if record.stage is target)
    changed_signature = current.input_signature != input_signature
    changed_artifacts = current.artifacts != tuple(map(str, artifacts))
    changed = changed_signature or changed_artifacts
    reset_review = changed and current.revision is not None and revision is None
    target_index = PIPELINE_STAGES.index(target)
    records: list[StageRecord] = []
    for index, record in enumerate(package.stages):
        if record.stage is target:
            records.append(
                StageRecord(
                    stage=target,
                    state=state,
                    executor=executor,
                    input_signature=input_signature,
                    artifacts=artifacts,
                    blocked_reasons=blocked_reasons,
                    error=error,
                    revision=(
                        None
                        if reset_review
                        else revision if revision is not None else current.revision
                    ),
                    review_policy=(
                        review_policy
                        if review_policy is not None
                        else current.review_policy
                    ),
                    review_state=(
                        ReviewState.NOT_READY
                        if reset_review
                        else (
                            review_state
                            if review_state is not None
                            else current.review_state
                        )
                    ),
                    review_blocks_progress=(
                        False
                        if reset_review
                        else (
                            review_blocks_progress
                            if review_blocks_progress is not None
                            else current.review_blocks_progress
                        )
                    ),
                )
            )
        elif changed and index > target_index and record.state is StageState.PASSED:
            records.append(replace(record, state=StageState.STALE))
        else:
            records.append(record)
    updated = _refresh_output_indexes(package.with_stages(records))
    save_production_package(root, updated)
    return updated


def invalidate_stage_and_downstream(
    project_dir: str | Path,
    stage: StageName,
    *,
    reason: str,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    package = load_production_package(root)
    target_index = PIPELINE_STAGES.index(StageName(stage))
    records: list[StageRecord] = []
    for index, record in enumerate(package.stages):
        if index < target_index or record.state is not StageState.PASSED:
            records.append(record)
            continue
        records.append(
            replace(
                record,
                state=StageState.STALE,
                blocked_reasons=(reason,) if index == target_index else (),
            )
        )
    updated = _refresh_output_indexes(package.with_stages(records))
    save_production_package(root, updated)
    return updated


def approve_stage(
    project_dir: str | Path,
    stage: StageName,
    *,
    note: str,
    evidence: tuple[str | Path, ...],
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    spec = load_project_spec(root)
    package = load_production_package(root)
    target = StageName(stage)
    target_index = PIPELINE_STAGES.index(target)
    current = package.stages[target_index]
    if current.state is not StageState.BLOCKED:
        raise ValueError(f"Stage {target.value} is not waiting for approval")
    if not get_mode_adapter(spec.mode).stage_steps[target].manual_gate:
        raise ValueError(f"Stage {target.value} is not a manual gate")
    if any(
        record.state is not StageState.PASSED
        for record in package.stages[:target_index]
    ):
        raise ValueError("All earlier stages must pass before approval")
    review_note = str(note).strip()
    if not review_note:
        raise ValueError("Approval note cannot be empty")
    supplied_evidence: list[Path] = []
    for raw_path in evidence:
        path = Path(raw_path).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Approval evidence must be a real file: {path}")
        supplied_evidence.append(path.resolve())
    if not supplied_evidence:
        raise ValueError("At least one approval evidence file is required")
    bound_paths: list[Path] = []
    for raw_path in (*current.artifacts, *(str(path) for path in supplied_evidence)):
        path = Path(raw_path).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Approval evidence must be a real file: {path}")
        resolved = path.resolve()
        if resolved not in bound_paths:
            bound_paths.append(resolved)
    approval_dir = root / "approvals"
    approval_dir.mkdir(parents=True, exist_ok=True)
    approval_path = approval_dir / f"{target.value}.approval.json"
    write_json_atomic(
        approval_path,
        {
            "schema_version": "motion-comic-factory.stage-approval.v1",
            "project_id": package.project_id,
            "stage": target.value,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "note": review_note,
            "evidence": [
                {
                    "path": str(path),
                    "sha256": sha256_file(path),
                }
                for path in bound_paths
            ],
        },
    )
    revision = write_stage_revision(
        root,
        target,
        tuple(bound_paths),
        current.input_signature,
        current.executor,
    )
    approve_stage_revision(
        root,
        target,
        revision.number,
        review_note,
        tuple(supplied_evidence),
    )
    return update_stage(
        root,
        target,
        StageState.PASSED,
        executor=f"{current.executor}:manual-approval",
        input_signature=current.input_signature,
        artifacts=(str(approval_path.resolve()), *(str(path) for path in bound_paths)),
        revision=revision.number,
        review_policy=ReviewPolicy.MANUAL,
        review_state=ReviewState.APPROVED,
        review_blocks_progress=False,
    )
