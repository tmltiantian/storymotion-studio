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
from .pipeline_review import (
    REVIEW_SCHEMA,
    REVISIONS_SCHEMA,
    StageReview,
    approve_stage_revision,
    write_stage_revision,
)


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
    reset_review = (
        changed
        and revision is None
        and (
            current.revision is not None
            or current.review_state in (ReviewState.APPROVED, ReviewState.AUTO_APPROVED)
        )
    )
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
                        else revision
                        if revision is not None
                        else current.revision
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


def _current_revision_integrity_issue(
    root: Path, record: StageRecord, expected_revision: int
) -> str:
    if record.revision != expected_revision:
        return (
            f"Revision {expected_revision} is stale; "
            f"current revision is {record.revision}"
        )
    revisions_path = root / "reviews" / f"{record.stage.value}.revisions.json"
    try:
        payload = _read_object(revisions_path)
        revisions = payload.get("revisions")
        if payload.get("schema_version") != REVISIONS_SCHEMA:
            return f"Revision record is invalid: {revisions_path}"
        if not isinstance(revisions, list) or not revisions:
            return f"Revision record is missing: {revisions_path}"
        current = revisions[-1]
        if not isinstance(current, dict) or current.get("number") != expected_revision:
            return f"Revision {expected_revision} is not current"
        artifacts = current.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            return f"Revision {expected_revision} has no artifacts"
        for item in artifacts:
            if not isinstance(item, dict):
                return f"Revision {expected_revision} has invalid artifacts"
            path = Path(str(item.get("path") or "")).expanduser()
            if path.is_symlink() or not path.is_file():
                return f"Review artifact is missing: {path}"
            if sha256_file(path) != str(item.get("sha256") or ""):
                return f"Review artifact changed: {path}"
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return f"Revision record is unreadable: {exc}"
    return ""


def approve_review_bundle(
    project_dir: str | Path,
    stages: tuple[StageName, ...],
    note: str,
    evidence: tuple[str | Path, ...],
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    package = load_production_package(root)
    targets = tuple(StageName(stage) for stage in stages)
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("Review bundle must contain unique stages")
    if tuple(sorted(targets, key=PIPELINE_STAGES.index)) != targets:
        raise ValueError("Review bundle stages must be in pipeline order")
    review_note = str(note).strip()
    if not review_note:
        raise ValueError("Approval note cannot be empty")

    target_records = tuple(
        package.stages[PIPELINE_STAGES.index(stage)] for stage in targets
    )
    if any(record.review_policy is ReviewPolicy.GROUPED for record in target_records):
        if any(
            record.review_policy is not ReviewPolicy.GROUPED
            for record in target_records
        ):
            raise ValueError("Grouped review bundles cannot mix policies")
        start = PIPELINE_STAGES.index(targets[0])
        end = PIPELINE_STAGES.index(targets[-1])
        while (
            start > 0
            and package.stages[start - 1].review_policy is ReviewPolicy.GROUPED
        ):
            start -= 1
        while (
            end + 1 < len(package.stages)
            and package.stages[end + 1].review_policy is ReviewPolicy.GROUPED
        ):
            end += 1
        complete_span = tuple(
            record.stage for record in package.stages[start : end + 1]
        )
        if targets != complete_span:
            raise ValueError("Grouped review requires the complete contiguous span")
    if any(record.state is not StageState.PASSED for record in target_records):
        raise ValueError("Every review bundle stage must have passed execution")
    if any(
        record.review_state is not ReviewState.AWAITING_REVIEW
        for record in target_records
    ):
        raise ValueError("Every review bundle stage must be awaiting review")
    if len(targets) > 1 and any(
        record.review_policy is not ReviewPolicy.GROUPED for record in target_records
    ):
        raise ValueError("Multi-stage review bundles require grouped policy")
    first_index = PIPELINE_STAGES.index(targets[0])
    if any(
        record.state is not StageState.PASSED for record in package.stages[:first_index]
    ):
        raise ValueError("All earlier stages must pass before approval")

    for record in target_records:
        if record.revision is None:
            raise ValueError(f"Stage {record.stage.value} has no current revision")
        integrity_issue = _current_revision_integrity_issue(
            root, record, record.revision
        )
        if integrity_issue:
            raise ValueError(integrity_issue)

    for record in target_records:
        approve_stage_revision(
            root,
            record.stage,
            record.revision,
            review_note,
            evidence,
        )

    target_set = set(targets)
    records = tuple(
        replace(
            record,
            review_state=ReviewState.APPROVED,
            review_blocks_progress=False,
            blocked_reasons=(),
        )
        if record.stage in target_set
        else record
        for record in package.stages
    )
    updated = _refresh_output_indexes(package.with_stages(records))
    save_production_package(root, updated)
    return updated


def request_stage_changes(
    project_dir: str | Path,
    stage: StageName,
    *,
    revision: int,
    reason: str,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    package = load_production_package(root)
    target = StageName(stage)
    index = PIPELINE_STAGES.index(target)
    current = package.stages[index]
    if current.state is not StageState.PASSED or current.review_state not in (
        ReviewState.AWAITING_REVIEW,
        ReviewState.APPROVED,
    ):
        raise ValueError(f"Stage {target.value} is not reviewable")
    review_reason = str(reason).strip()
    if not review_reason:
        raise ValueError("Change reason cannot be empty")
    integrity_issue = _current_revision_integrity_issue(root, current, int(revision))
    if integrity_issue:
        raise ValueError(integrity_issue)
    review = StageReview(
        stage=target,
        revision=int(revision),
        state=ReviewState.CHANGES_REQUESTED,
        note=review_reason,
        evidence=(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_json_atomic(
        root / "reviews" / f"{target.value}.review.json",
        {"schema_version": REVIEW_SCHEMA, **review.to_dict()},
    )
    records = tuple(
        replace(
            record,
            review_state=ReviewState.CHANGES_REQUESTED,
            review_blocks_progress=True,
            blocked_reasons=(review_reason,),
        )
        if record.stage is target
        else record
        for record in package.stages
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
    revision: int | None = None,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    spec = load_project_spec(root)
    package = load_production_package(root)
    target = StageName(stage)
    target_index = PIPELINE_STAGES.index(target)
    current = package.stages[target_index]
    if current.state is StageState.PASSED:
        if revision is None:
            revision = current.revision
        if revision is None:
            raise ValueError(f"Stage {target.value} has no current revision")
        integrity_issue = _current_revision_integrity_issue(root, current, revision)
        if integrity_issue:
            raise ValueError(integrity_issue)
        if current.review_policy is ReviewPolicy.GROUPED:
            if not current.review_blocks_progress:
                raise ValueError(
                    f"Stage {target.value} is not a grouped review terminal"
                )
            start = target_index
            while (
                start > 0
                and package.stages[start - 1].review_policy is ReviewPolicy.GROUPED
                and package.stages[start - 1].review_state
                is ReviewState.AWAITING_REVIEW
            ):
                start -= 1
            stages = tuple(
                record.stage for record in package.stages[start : target_index + 1]
            )
        else:
            stages = (target,)
        return approve_review_bundle(root, stages, note=note, evidence=evidence)
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
