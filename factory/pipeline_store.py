from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

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
    prepare_stage_review,
    write_stage_revision,
)


SPEC_FILENAME = "project.json"
PACKAGE_FILENAME = "production_package.json"
LEGACY_REVIEW_TRANSACTION_SCHEMA = "motion-comic-factory.review-transaction.v1"
REVIEW_TRANSACTION_SCHEMA = "motion-comic-factory.review-transaction.v2"
REVIEW_TRANSACTION_TOMBSTONE_SUFFIX = ".tombstone"
ACTIVE_REPAIR_SCHEMA = "motion-comic-factory.active-repair.v1"
REPAIR_TRANSACTION_SCHEMA = "motion-comic-factory.repair-transaction.v1"


class ApprovalInProgressError(RuntimeError):
    pass


class PipelineInProgressError(RuntimeError):
    pass


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


def load_active_repair_state(project_dir: str | Path) -> dict[str, Any]:
    root = _require_safe_project_dir(project_dir)
    path = root / "impact_plans" / "active.json"
    if path.is_symlink():
        raise ValueError(f"Active repair state cannot be a symlink: {path}")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"Active repair state is invalid: {path}")
    value = _read_object(path)
    if value.get("schema_version") != ACTIVE_REPAIR_SCHEMA:
        raise ValueError(f"Active repair state is invalid: {path}")
    return value


@contextmanager
def _pipeline_lock(root: Path):
    lock_path = root / ".pipeline.lock"
    if lock_path.is_symlink():
        raise ValueError("Pipeline lock cannot be a symlink")
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineInProgressError(
                "Unified project is already running"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _repair_transactions_path(root: Path) -> Path:
    plans = root / "impact_plans"
    if plans.is_symlink():
        raise ValueError("Impact plan directory cannot be a symlink")
    plans.mkdir(parents=True, exist_ok=True)
    transactions = plans / ".transactions"
    if transactions.is_symlink():
        raise ValueError("Repair transaction directory cannot be a symlink")
    transactions.mkdir(parents=True, exist_ok=True)
    return transactions


def _json_payload_sha256(payload: dict[str, Any]) -> str:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _initialize_repair_transaction(
    root: Path,
    *,
    plan_id: str,
    source_package_sha256: str,
    target_package: ProductionPackage,
    active_state: dict[str, Any],
) -> tuple[Path, str]:
    transactions = _repair_transactions_path(root)
    transaction_id = uuid4().hex
    temporary = transactions / f".{transaction_id}.tmp"
    transaction = transactions / transaction_id
    temporary.mkdir()
    try:
        package_payload = target_package.to_dict()
        target_sha256 = _json_payload_sha256(package_payload)
        active_sha256 = _json_payload_sha256(active_state)
        write_json_atomic(temporary / "production_package.json", package_payload)
        write_json_atomic(temporary / "active.json", active_state)
        write_json_atomic(
            temporary / "transaction.json",
            {
                "schema_version": REPAIR_TRANSACTION_SCHEMA,
                "transaction_id": transaction_id,
                "plan_id": plan_id,
                "source_package_sha256": source_package_sha256,
                "target_package_sha256": target_sha256,
                "active_sha256": active_sha256,
            },
        )
        os.replace(temporary, transaction)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=False)
    return transaction, target_sha256


def _read_repair_transaction(transaction: Path) -> dict[str, Any]:
    if transaction.is_symlink() or not transaction.is_dir():
        raise ValueError(f"Repair transaction is invalid: {transaction}")
    journal_path = transaction / "transaction.json"
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError(f"Repair transaction is incomplete: {transaction}")
    payload = _read_object(journal_path)
    if (
        payload.get("schema_version") != REPAIR_TRANSACTION_SCHEMA
        or payload.get("transaction_id") != transaction.name
        or not str(payload.get("plan_id") or "")
    ):
        raise ValueError(f"Repair transaction is invalid: {transaction}")
    for name in ("production_package.json", "active.json"):
        path = transaction / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Repair transaction is incomplete: {transaction}")
    target_payload = _read_object(transaction / "production_package.json")
    if _json_payload_sha256(target_payload) != payload.get(
        "target_package_sha256"
    ):
        raise ValueError(f"Repair transaction target changed: {transaction}")
    active_payload = _read_object(transaction / "active.json")
    if (
        _json_payload_sha256(active_payload) != payload.get("active_sha256")
        or active_payload.get("plan_id") != payload.get("plan_id")
    ):
        raise ValueError(f"Repair transaction active state changed: {transaction}")
    return payload


def _remove_repair_transaction(transaction: Path) -> None:
    if transaction.is_symlink() or not transaction.is_dir():
        raise ValueError(f"Repair transaction is invalid: {transaction}")
    shutil.rmtree(transaction, ignore_errors=False)


def _finish_repair_transaction(root: Path, transaction: Path) -> None:
    active = _read_object(transaction / "active.json")
    if active.get("schema_version") != ACTIVE_REPAIR_SCHEMA:
        raise ValueError(f"Repair active state is invalid: {transaction}")
    active_path = root / "impact_plans" / "active.json"
    if active_path.is_symlink():
        raise ValueError("Active repair state cannot be a symlink")
    write_json_atomic(active_path, active)
    _remove_repair_transaction(transaction)


def _recover_repair_transactions_locked(root: Path) -> None:
    transactions = root / "impact_plans" / ".transactions"
    if not transactions.exists():
        return
    if transactions.is_symlink() or not transactions.is_dir():
        raise ValueError("Repair transaction directory is invalid")
    package_path = root / PACKAGE_FILENAME
    if package_path.is_symlink() or not package_path.is_file():
        raise ValueError("Production package is invalid during repair recovery")
    for transaction in sorted(transactions.iterdir()):
        if transaction.name.startswith(".") and transaction.name.endswith(".tmp"):
            if transaction.is_symlink() or not transaction.is_dir():
                raise ValueError(f"Repair transaction is invalid: {transaction}")
            shutil.rmtree(transaction, ignore_errors=False)
            continue
        payload = _read_repair_transaction(transaction)
        current_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
        if current_sha256 == payload["target_package_sha256"]:
            _finish_repair_transaction(root, transaction)
        elif current_sha256 == payload["source_package_sha256"]:
            _remove_repair_transaction(transaction)
        else:
            raise RuntimeError(
                "Repair transaction package state is neither source nor target"
            )


def recover_repair_transactions(
    project_dir: str | Path, *, pipeline_lock_held: bool = False
) -> None:
    root = _require_safe_project_dir(project_dir)
    transactions = root / "impact_plans" / ".transactions"
    if not transactions.exists():
        return
    if transactions.is_symlink() or not transactions.is_dir():
        raise ValueError("Repair transaction directory is invalid")
    if not any(transactions.iterdir()):
        return
    if pipeline_lock_held:
        with _approval_lock(root):
            _recover_repair_transactions_locked(root)
        return
    with _pipeline_lock(root):
        with _approval_lock(root):
            _recover_repair_transactions_locked(root)


def apply_repair_state(
    project_dir: str | Path,
    *,
    plan_id: str,
    request_stage: StageName,
    affected: dict[str, tuple[str, ...]],
    preserved_artifacts: tuple[str, ...],
    expected_package_sha256: str,
    source_snapshot_validator: Callable[[], None] | None = None,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    with _pipeline_lock(root):
        with _approval_lock(root):
            if source_snapshot_validator is not None:
                source_snapshot_validator()
            _recover_repair_transactions_locked(root)
            _recover_review_transactions_locked(root)
            package_path = root / PACKAGE_FILENAME
            current_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
            if current_sha256 != expected_package_sha256:
                active = load_active_repair_state(root)
                if (
                    active.get("plan_id") == plan_id
                    and active.get("target_package_sha256") == current_sha256
                ):
                    return load_production_package(root)
                raise ValueError("Impact plan is stale relative to production package")
            package = load_production_package(root)
            target_index = PIPELINE_STAGES.index(StageName(request_stage))
            records: list[StageRecord] = []
            for index, record in enumerate(package.stages):
                if index < target_index or record.state is StageState.PENDING:
                    records.append(record)
                    continue
                records.append(
                    replace(
                        record,
                        state=StageState.STALE,
                        blocked_reasons=(
                            (f"Impact plan {plan_id} applied.",)
                            if index == target_index
                            else ()
                        ),
                        error="",
                        revision=None,
                        review_state=ReviewState.NOT_READY,
                        review_blocks_progress=False,
                        review_transaction_id="",
                    )
                )
            updated = _refresh_output_indexes(package.with_stages(records))
            target_sha256 = _json_payload_sha256(updated.to_dict())
            state = {
                "schema_version": ACTIVE_REPAIR_SCHEMA,
                "plan_id": plan_id,
                "request_stage": StageName(request_stage).value,
                "affected": {
                    str(stage): list(map(str, item_ids))
                    for stage, item_ids in affected.items()
                },
                "preserved_artifacts": list(map(str, preserved_artifacts)),
                "source_package_sha256": expected_package_sha256,
                "target_package_sha256": target_sha256,
            }
            transaction, staged_target_sha256 = _initialize_repair_transaction(
                root,
                plan_id=plan_id,
                source_package_sha256=expected_package_sha256,
                target_package=updated,
                active_state=state,
            )
            if staged_target_sha256 != target_sha256:
                raise RuntimeError("Repair transaction target hash changed")
            try:
                save_production_package(root, updated)
                _finish_repair_transaction(root, transaction)
            except BaseException:
                durable_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
                if durable_sha256 == target_sha256:
                    _finish_repair_transaction(root, transaction)
                elif durable_sha256 == expected_package_sha256:
                    _remove_repair_transaction(transaction)
                raise
            return updated


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
    review_transaction_id: str | None = None,
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
                    review_transaction_id=(
                        ""
                        if reset_review
                        else (
                            review_transaction_id
                            if review_transaction_id is not None
                            else current.review_transaction_id
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


def _review_record_path(root: Path, stage: StageName) -> Path:
    return root / "reviews" / f"{stage.value}.review.json"


@contextmanager
def _approval_lock(root: Path):
    lock_path = root / ".approval.lock"
    if lock_path.is_symlink():
        raise ValueError("Approval lock cannot be a symlink")
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ApprovalInProgressError(
                "Project approval is already in progress"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _review_transaction_entries(
    root: Path, records: tuple[StageRecord, ...]
) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for record in records:
        target = _review_record_path(root, record.stage)
        if target.is_symlink():
            raise ValueError(f"Review record cannot be a symlink: {target}")
        entries.append(
            {
                "stage": record.stage.value,
                "revision": record.revision,
            }
        )
    return tuple(entries)


def _review_transactions_path(root: Path) -> Path:
    transactions = root / "reviews" / ".transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    if transactions.is_symlink():
        raise ValueError("Review transaction directory cannot be a symlink")
    return transactions


def _initialize_review_transaction(
    root: Path,
    transaction_id: str,
    entries: tuple[dict[str, Any], ...],
    reviews: tuple[StageReview, ...],
) -> Path:
    transactions = _review_transactions_path(root)
    temporary = transactions / f".{transaction_id}.tmp"
    transaction = transactions / transaction_id
    temporary.mkdir()
    try:
        staged = temporary / "staged"
        staged.mkdir()
        for review in reviews:
            write_json_atomic(
                staged / f"{review.stage.value}.review.json",
                {"schema_version": REVIEW_SCHEMA, **review.to_dict()},
            )
        write_json_atomic(
            temporary / "transaction.json",
            {
                "schema_version": REVIEW_TRANSACTION_SCHEMA,
                "transaction_id": transaction_id,
                "entries": list(entries),
            },
        )
        os.replace(temporary, transaction)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return transaction


def _read_review_transaction(
    transaction: Path,
) -> tuple[str, str, tuple[dict[str, Any], ...]]:
    payload = _read_object(transaction / "transaction.json")
    schema = str(payload.get("schema_version") or "")
    if schema not in (LEGACY_REVIEW_TRANSACTION_SCHEMA, REVIEW_TRANSACTION_SCHEMA):
        raise ValueError(f"Review transaction is invalid: {transaction}")
    transaction_id = str(payload.get("transaction_id") or "")
    if schema == REVIEW_TRANSACTION_SCHEMA and transaction_id != transaction.name:
        raise ValueError(f"Review transaction identity is invalid: {transaction}")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"Review transaction has no entries: {transaction}")
    entries: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Review transaction entry is invalid: {transaction}")
        stage = StageName(str(raw_entry.get("stage")))
        revision = int(raw_entry.get("revision"))
        entry: dict[str, Any] = {
            "stage": stage.value,
            "revision": revision,
        }
        if schema == LEGACY_REVIEW_TRANSACTION_SCHEMA:
            had_review = raw_entry.get("had_review")
            if not isinstance(had_review, bool):
                raise ValueError(f"Review transaction entry is invalid: {transaction}")
            entry["had_review"] = had_review
        entries.append(entry)
    if len({entry["stage"] for entry in entries}) != len(entries):
        raise ValueError(f"Review transaction has duplicate stages: {transaction}")
    return schema, transaction_id, tuple(entries)


def _review_backup_path(transaction: Path, stage: StageName) -> Path:
    return transaction / f"{stage.value}.review.backup.json"


def _is_review_transaction_tombstone(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(
        REVIEW_TRANSACTION_TOMBSTONE_SUFFIX
    )


def _review_transaction_tombstone_path(transaction: Path) -> Path:
    return transaction.with_name(
        f".{transaction.name}{REVIEW_TRANSACTION_TOMBSTONE_SUFFIX}"
    )


def _delete_review_transaction_tombstone(tombstone: Path) -> None:
    shutil.rmtree(tombstone, ignore_errors=False)


def _remove_review_transaction(transaction: Path) -> None:
    if _is_review_transaction_tombstone(transaction):
        _delete_review_transaction_tombstone(transaction)
        return
    tombstone = _review_transaction_tombstone_path(transaction)
    if transaction.exists() or transaction.is_symlink():
        if tombstone.exists() or tombstone.is_symlink():
            raise RuntimeError(
                f"Review transaction cleanup is ambiguous: {transaction}"
            )
        os.replace(transaction, tombstone)
    elif not tombstone.exists():
        return
    _delete_review_transaction_tombstone(tombstone)


def _rollback_review_transaction(
    root: Path, transaction: Path, entries: tuple[dict[str, Any], ...]
) -> tuple[str, ...]:
    errors: list[str] = []
    for entry in reversed(entries):
        stage = StageName(str(entry["stage"]))
        target = _review_record_path(root, stage)
        backup = _review_backup_path(transaction, stage)
        try:
            if bool(entry["had_review"]):
                if backup.is_file():
                    if target.exists() or target.is_symlink():
                        if target.is_symlink() or not target.is_file():
                            raise ValueError(
                                f"Cannot roll back invalid review record: {target}"
                            )
                        target.unlink()
                    os.replace(backup, target)
            elif target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file():
                    raise ValueError(
                        f"Cannot roll back invalid review record: {target}"
                    )
                target.unlink()
        except Exception as exc:
            errors.append(str(exc))
    if not errors:
        _remove_review_transaction(transaction)
    return tuple(errors)


def _review_transaction_committed(
    root: Path, transaction_id: str, entries: tuple[dict[str, Any], ...]
) -> bool:
    package_path = root / PACKAGE_FILENAME
    if not package_path.is_file() or package_path.is_symlink():
        return False
    package = ProductionPackage.from_dict(_read_object(package_path))
    by_stage = {record.stage: record for record in package.stages}
    return all(
        by_stage[StageName(str(entry["stage"]))].review_state is ReviewState.APPROVED
        and by_stage[StageName(str(entry["stage"]))].revision == int(entry["revision"])
        and by_stage[StageName(str(entry["stage"]))].review_transaction_id
        == transaction_id
        for entry in entries
    )


def _legacy_review_transaction_committed(
    root: Path, entries: tuple[dict[str, Any], ...]
) -> bool:
    package_path = root / PACKAGE_FILENAME
    if not package_path.is_file() or package_path.is_symlink():
        return False
    package = ProductionPackage.from_dict(_read_object(package_path))
    by_stage = {record.stage: record for record in package.stages}
    return all(
        by_stage[StageName(str(entry["stage"]))].review_state
        is ReviewState.APPROVED
        and by_stage[StageName(str(entry["stage"]))].revision
        == int(entry["revision"])
        for entry in entries
    )


def _staged_review_path(transaction: Path, stage: StageName) -> Path:
    return transaction / "staged" / f"{stage.value}.review.json"


def _canonical_review_transaction_id(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        payload = _read_object(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ""
    return str(payload.get("transaction_id") or "")


def _publish_staged_review(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Staged review is missing: {source}")
    if destination.is_symlink():
        raise ValueError(f"Review record cannot be a symlink: {destination}")
    os.replace(source, destination)


def _finish_committed_review_transaction(
    root: Path,
    transaction: Path,
    transaction_id: str,
    entries: tuple[dict[str, Any], ...],
) -> None:
    for entry in entries:
        stage = StageName(str(entry["stage"]))
        staged = _staged_review_path(transaction, stage)
        canonical = _review_record_path(root, stage)
        if staged.is_file() and not staged.is_symlink():
            _publish_staged_review(staged, canonical)
        elif _canonical_review_transaction_id(canonical) != transaction_id:
            raise RuntimeError(
                f"Committed review transaction is missing {stage.value}"
            )
    _remove_review_transaction(transaction)


def _discard_uncommitted_review_transaction(
    root: Path,
    transaction: Path,
    transaction_id: str,
    entries: tuple[dict[str, Any], ...],
) -> None:
    for entry in entries:
        stage = StageName(str(entry["stage"]))
        canonical = _review_record_path(root, stage)
        if _canonical_review_transaction_id(canonical) == transaction_id:
            canonical.unlink()
    _remove_review_transaction(transaction)


def _recover_review_transactions_locked(root: Path) -> None:
    _recover_repair_transactions_locked(root)
    transactions = root / "reviews" / ".transactions"
    if not transactions.exists():
        return
    if transactions.is_symlink() or not transactions.is_dir():
        raise ValueError("Review transaction directory is invalid")
    for transaction in sorted(transactions.iterdir()):
        if transaction.is_symlink() or not transaction.is_dir():
            raise ValueError(f"Review transaction is invalid: {transaction}")
        if _is_review_transaction_tombstone(transaction):
            _remove_review_transaction(transaction)
            continue
        if transaction.name.startswith(".") and transaction.name.endswith(".tmp"):
            shutil.rmtree(transaction, ignore_errors=False)
            continue
        schema, transaction_id, entries = _read_review_transaction(transaction)
        if schema == LEGACY_REVIEW_TRANSACTION_SCHEMA:
            if _legacy_review_transaction_committed(root, entries):
                _remove_review_transaction(transaction)
                continue
            rollback_errors = _rollback_review_transaction(root, transaction, entries)
            if rollback_errors:
                raise RuntimeError(
                    "Review transaction recovery failed: "
                    + " | ".join(rollback_errors)
                )
            continue
        if _review_transaction_committed(root, transaction_id, entries):
            _finish_committed_review_transaction(
                root, transaction, transaction_id, entries
            )
        else:
            _discard_uncommitted_review_transaction(
                root, transaction, transaction_id, entries
            )


def recover_review_transactions(project_dir: str | Path) -> None:
    root = _require_safe_project_dir(project_dir)
    with _approval_lock(root):
        _recover_review_transactions_locked(root)


def approve_review_bundle(
    project_dir: str | Path,
    stages: tuple[StageName, ...],
    note: str,
    evidence: tuple[str | Path, ...],
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    with _approval_lock(root):
        _recover_review_transactions_locked(root)
        return _approve_review_bundle_locked(root, stages, note, evidence)


def _approve_review_bundle_locked(
    root: Path,
    stages: tuple[StageName, ...],
    note: str,
    evidence: tuple[str | Path, ...],
) -> ProductionPackage:
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

    transaction_id = uuid4().hex
    target_set = set(targets)
    records = tuple(
        replace(
            record,
            review_state=ReviewState.APPROVED,
            review_blocks_progress=False,
            blocked_reasons=(),
            review_transaction_id=transaction_id,
        )
        if record.stage in target_set
        else record
        for record in package.stages
    )
    updated = _refresh_output_indexes(package.with_stages(records))
    entries = _review_transaction_entries(root, target_records)
    reviews = tuple(
        prepare_stage_review(
            root,
            record.stage,
            record.revision,
            review_note,
            evidence,
            transaction_id=transaction_id,
        )
        for record in target_records
    )
    transaction = _initialize_review_transaction(
        root, transaction_id, entries, reviews
    )
    try:
        save_production_package(root, updated)
        _finish_committed_review_transaction(
            root, transaction, transaction_id, entries
        )
    except Exception:
        if _review_transaction_committed(root, transaction_id, entries):
            _finish_committed_review_transaction(
                root, transaction, transaction_id, entries
            )
            return updated
        _discard_uncommitted_review_transaction(
            root, transaction, transaction_id, entries
        )
        raise
    return updated


def request_stage_changes(
    project_dir: str | Path,
    stage: StageName,
    *,
    revision: int,
    reason: str,
) -> ProductionPackage:
    root = _require_safe_project_dir(project_dir)
    with _approval_lock(root):
        _recover_review_transactions_locked(root)
        return _request_stage_changes_locked(root, stage, revision, reason)


def _request_stage_changes_locked(
    root: Path,
    stage: StageName,
    revision: int,
    reason: str,
) -> ProductionPackage:
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
            review_transaction_id="",
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
    with _approval_lock(root):
        _recover_review_transactions_locked(root)
        return _approve_stage_locked(root, stage, note, evidence, revision)


def _approve_stage_locked(
    root: Path,
    stage: StageName,
    note: str,
    evidence: tuple[str | Path, ...],
    revision: int | None,
) -> ProductionPackage:
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
        return _approve_review_bundle_locked(root, stages, note, evidence)
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
