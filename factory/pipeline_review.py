from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .file_io import sha256_file, write_json_atomic
from .media_types import validate_media_type
from .pipeline_contracts import (
    PIPELINE_STAGES,
    ProductionPackage,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)


REVIEW_DIRECTORY = "reviews"
REVISIONS_SCHEMA = "motion-comic-factory.stage-revisions.v1"
REVIEW_SCHEMA = "motion-comic-factory.stage-review.v1"
DELIVERY_EVAL_EVIDENCE_SCHEMA = "motion-comic-factory.delivery-eval-evidence.v2"


class ApprovalPreset(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    STRICT = "strict"


@dataclass(frozen=True)
class ArtifactRevision:
    path: str
    sha256: str
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_type", validate_media_type(self.media_type))

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArtifactRevision:
        return cls(
            path=str(value["path"]),
            sha256=str(value["sha256"]),
            media_type=str(value.get("media_type", "application/octet-stream")),
        )


@dataclass(frozen=True)
class StageRevision:
    stage: StageName
    number: int
    input_signature: str
    executor: str
    artifacts: tuple[ArtifactRevision, ...]
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", StageName(self.stage))
        if self.number < 1:
            raise ValueError("revision number must be positive")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "number": self.number,
            "input_signature": self.input_signature,
            "executor": self.executor,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageRevision:
        return cls(
            stage=StageName(str(value["stage"])),
            number=int(value["number"]),
            input_signature=str(value.get("input_signature", "")),
            executor=str(value.get("executor", "")),
            artifacts=tuple(
                ArtifactRevision.from_dict(item)
                for item in value.get("artifacts", ())
            ),
            created_at=str(value["created_at"]),
        )


@dataclass(frozen=True)
class StageReview:
    stage: StageName
    revision: int
    state: ReviewState
    note: str
    evidence: tuple[ArtifactRevision, ...]
    created_at: str
    transaction_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", StageName(self.stage))
        object.__setattr__(self, "state", ReviewState(self.state))
        if self.revision < 1:
            raise ValueError("review revision must be positive")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "transaction_id", str(self.transaction_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "revision": self.revision,
            "state": self.state.value,
            "note": self.note,
            "evidence": [artifact.to_dict() for artifact in self.evidence],
            "created_at": self.created_at,
            "transaction_id": self.transaction_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageReview:
        return cls(
            stage=StageName(str(value["stage"])),
            revision=int(value["revision"]),
            state=ReviewState(str(value.get("state", ReviewState.APPROVED.value))),
            note=str(value.get("note", "")),
            evidence=tuple(
                ArtifactRevision.from_dict(item) for item in value.get("evidence", ())
            ),
            created_at=str(value["created_at"]),
            transaction_id=str(value.get("transaction_id", "")),
        )


@dataclass(frozen=True)
class ReviewValidation:
    valid: bool
    reason: str = ""
    review: StageReview | None = None
    busy: bool = False


@dataclass(frozen=True)
class ReviewConfig:
    preset: ApprovalPreset
    policies: Mapping[StageName, ReviewPolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "preset", ApprovalPreset(self.preset))
        object.__setattr__(
            self,
            "policies",
            {StageName(stage): ReviewPolicy(policy) for stage, policy in self.policies.items()},
        )

    def policy_for(self, stage: StageName) -> ReviewPolicy:
        return self.policies[StageName(stage)]


def _preset_policies(preset: ApprovalPreset) -> dict[StageName, ReviewPolicy]:
    policies = {stage: ReviewPolicy.AUTOMATIC for stage in PIPELINE_STAGES}
    if preset is ApprovalPreset.QUICK:
        for stage in (StageName.STORYBOARD, StageName.VIDEO, StageName.DELIVER):
            policies[stage] = ReviewPolicy.MANUAL
    elif preset is ApprovalPreset.STANDARD:
        for stage in (
            StageName.SCRIPT,
            StageName.STORYBOARD,
            StageName.ASSETS,
            StageName.AUDIO,
            StageName.VIDEO,
            StageName.EVAL,
            StageName.DELIVER,
        ):
            policies[stage] = ReviewPolicy.MANUAL
    else:
        policies = {stage: ReviewPolicy.MANUAL for stage in PIPELINE_STAGES}
    return policies


def resolve_review_config(
    preset: ApprovalPreset, overrides: Mapping[str, str]
) -> ReviewConfig:
    resolved_preset = ApprovalPreset(preset)
    policies = _preset_policies(resolved_preset)
    protected_stages = {StageName.VIDEO, StageName.EVAL, StageName.DELIVER}
    for raw_stage, raw_policy in overrides.items():
        stage = StageName(raw_stage)
        policy = ReviewPolicy(raw_policy)
        if stage in protected_stages and policy is not ReviewPolicy.MANUAL:
            raise ValueError(f"{stage.value} requires manual review")
        policies[stage] = policy
    return ReviewConfig(preset=resolved_preset, policies=policies)


def _review_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser()
    if root.is_symlink():
        raise ValueError("project directory cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("project directory cannot be a symlink")
    directory = root.resolve() / REVIEW_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _revisions_path(project_dir: str | Path, stage: StageName) -> Path:
    return _review_root(project_dir) / f"{StageName(stage).value}.revisions.json"


def _review_path(project_dir: str | Path, stage: StageName) -> Path:
    return _review_root(project_dir) / f"{StageName(stage).value}.review.json"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _snapshot_artifacts(artifacts: tuple[str | Path, ...]) -> tuple[ArtifactRevision, ...]:
    snapshots: list[ArtifactRevision] = []
    seen: set[Path] = set()
    for raw_path in artifacts:
        path = Path(raw_path).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Review artifact must be a real file: {path}")
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        snapshots.append(
            ArtifactRevision(
                path=str(resolved),
                sha256=sha256_file(resolved),
                media_type=mimetypes.guess_type(resolved.name)[0]
                or "application/octet-stream",
            )
        )
    if not snapshots:
        raise ValueError("At least one review artifact is required")
    return tuple(snapshots)


def _load_revisions(project_dir: str | Path, stage: StageName) -> tuple[StageRevision, ...]:
    path = _revisions_path(project_dir, stage)
    if not path.exists():
        return ()
    payload = _read_object(path)
    if payload.get("schema_version") != REVISIONS_SCHEMA:
        raise ValueError(f"Unsupported stage revision schema: {path}")
    if StageName(str(payload.get("stage"))) is not StageName(stage):
        raise ValueError(f"Stage revision file does not match {StageName(stage).value}")
    return tuple(StageRevision.from_dict(item) for item in payload.get("revisions", ()))


def write_stage_revision(
    project_dir: str | Path,
    stage: StageName,
    artifacts: tuple[str | Path, ...],
    input_signature: str,
    executor: str,
) -> StageRevision:
    target = StageName(stage)
    revisions = _load_revisions(project_dir, target)
    revision = StageRevision(
        stage=target,
        number=(revisions[-1].number + 1) if revisions else 1,
        input_signature=str(input_signature),
        executor=str(executor),
        artifacts=_snapshot_artifacts(artifacts),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    write_json_atomic(
        _revisions_path(project_dir, target),
        {
            "schema_version": REVISIONS_SCHEMA,
            "stage": target.value,
            "revisions": [item.to_dict() for item in (*revisions, revision)],
        },
    )
    return revision


def _current_revision(
    project_dir: str | Path, stage: StageName, number: int
) -> StageRevision:
    revisions = _load_revisions(project_dir, stage)
    for revision in revisions:
        if revision.number == number:
            return revision
    raise ValueError(f"Unknown {StageName(stage).value} revision: {number}")


def canonical_json_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _delivery_artifact_snapshot(
    root: Path,
    artifact: ArtifactRevision,
    *,
    require_project_path: bool,
) -> dict[str, str]:
    path = Path(artifact.path).expanduser().resolve()
    if sha256_file(path) != artifact.sha256:
        raise ValueError("EVAL immutable artifact changed before delivery")
    try:
        stored_path = path.relative_to(root).as_posix()
    except ValueError:
        if require_project_path:
            raise ValueError("EVAL revision artifact escaped the project") from None
        stored_path = str(path)
    return {
        "path": stored_path,
        "sha256": artifact.sha256,
        "media_type": artifact.media_type,
    }


def canonical_review_snapshot(
    review: StageReview,
    artifact_snapshot: Callable[[ArtifactRevision], Mapping[str, str]],
) -> dict[str, Any]:
    evidence: list[dict[str, str]] = []
    for artifact in review.evidence:
        snapshot = dict(artifact_snapshot(artifact))
        if set(snapshot) != {"path", "sha256", "media_type"}:
            raise ValueError("Review evidence snapshot is invalid")
        evidence.append(snapshot)
    return {
        "schema_version": REVIEW_SCHEMA,
        "stage": review.stage.value,
        "revision": review.revision,
        "state": review.state.value,
        "note": review.note,
        "evidence": evidence,
        "created_at": review.created_at,
        "transaction_id": review.transaction_id,
    }


def delivery_eval_evidence(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    package = ProductionPackage.from_dict(
        _read_object(root / "production_package.json")
    )
    record = next(item for item in package.stages if item.stage is StageName.EVAL)
    if record.state is not StageState.PASSED or record.revision is None:
        raise ValueError("EVAL must pass before delivery")
    revision = _current_revision(root, StageName.EVAL, record.revision)
    if (
        revision.input_signature != record.input_signature
        or revision.executor != record.executor
    ):
        raise ValueError("EVAL revision does not match the production package")
    revision_artifacts: dict[str, dict[str, str]] = {}
    for artifact in revision.artifacts:
        snapshot = _delivery_artifact_snapshot(
            root,
            artifact,
            require_project_path=True,
        )
        if snapshot["path"] in revision_artifacts:
            raise ValueError("EVAL revision contains duplicate artifacts")
        revision_artifacts[snapshot["path"]] = snapshot
    package_artifacts = {
        Path(path).expanduser().resolve().relative_to(root).as_posix()
        for path in record.artifacts
    }
    if package_artifacts != set(revision_artifacts):
        raise ValueError("EVAL revision does not match package artifacts")
    reports: list[dict[str, str]] = []
    for raw_path in package.eval_reports:
        path = Path(raw_path).expanduser().resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("EVAL report escaped the project") from exc
        artifact = revision_artifacts.get(relative)
        if artifact is None:
            raise ValueError("EVAL report is not immutable revision evidence")
        reports.append(dict(artifact))
    if not reports:
        raise ValueError("EVAL delivery evidence has no reports")
    revision_snapshot: dict[str, Any] = {
        "schema_version": REVISIONS_SCHEMA,
        "stage": revision.stage.value,
        "number": revision.number,
        "input_signature": revision.input_signature,
        "executor": revision.executor,
        "artifacts": list(revision_artifacts.values()),
        "created_at": revision.created_at,
    }
    evidence: dict[str, Any] = {
        "schema_version": DELIVERY_EVAL_EVIDENCE_SCHEMA,
        "stage": StageName.EVAL.value,
        "policy": record.review_policy.value,
        "state": record.review_state.value,
        "revision": revision.number,
        "stage_revision": revision_snapshot,
        "stage_revision_sha256": canonical_json_digest(revision_snapshot),
        "reports": reports,
    }
    if record.review_blocks_progress:
        raise ValueError("EVAL approval still blocks delivery")
    if record.review_policy is ReviewPolicy.AUTOMATIC:
        if record.review_state is not ReviewState.AUTO_APPROVED:
            raise ValueError("Automatic EVAL is not auto-approved")
    elif record.review_policy in {ReviewPolicy.MANUAL, ReviewPolicy.GROUPED}:
        validation = validate_stage_review(root, StageName.EVAL)
        if (
            record.review_state is not ReviewState.APPROVED
            or not validation.valid
            or validation.review is None
            or validation.review.state is not ReviewState.APPROVED
            or validation.review.revision != revision.number
        ):
            raise ValueError("EVAL must have current durable approval before delivery")
        review = validation.review
        review_snapshot = canonical_review_snapshot(
            review,
            lambda artifact: _delivery_artifact_snapshot(
                root,
                artifact,
                require_project_path=False,
            ),
        )
        evidence["review"] = {
            "snapshot": review_snapshot,
            "sha256": canonical_json_digest(review_snapshot),
        }
    else:
        raise ValueError("EVAL review policy cannot authorize delivery")
    evidence["snapshot_sha256"] = canonical_json_digest(evidence)
    return evidence


def _artifact_integrity_issue(artifacts: tuple[ArtifactRevision, ...]) -> str:
    for artifact in artifacts:
        path = Path(artifact.path)
        if path.is_symlink() or not path.is_file():
            return f"Review artifact is missing: {path}"
        if sha256_file(path) != artifact.sha256:
            return f"Review artifact changed: {path}"
    return ""


def prepare_stage_review(
    project_dir: str | Path,
    stage: StageName,
    revision: int,
    note: str,
    evidence: tuple[str | Path, ...],
    *,
    transaction_id: str = "",
) -> StageReview:
    target = StageName(stage)
    revisions = _load_revisions(project_dir, target)
    if not revisions or revision != revisions[-1].number:
        raise ValueError(f"Only the latest revision can be approved: {revision}")
    approved_revision = _current_revision(project_dir, target, revision)
    integrity_issue = _artifact_integrity_issue(approved_revision.artifacts)
    if integrity_issue:
        raise ValueError(integrity_issue)
    review_note = str(note).strip()
    if not review_note:
        raise ValueError("Approval note cannot be empty")
    review = StageReview(
        stage=target,
        revision=approved_revision.number,
        state=ReviewState.APPROVED,
        note=review_note,
        evidence=_snapshot_artifacts(evidence),
        created_at=datetime.now(timezone.utc).isoformat(),
        transaction_id=transaction_id,
    )
    return review


def approve_stage_revision(
    project_dir: str | Path,
    stage: StageName,
    revision: int,
    note: str,
    evidence: tuple[str | Path, ...],
) -> StageReview:
    review = prepare_stage_review(project_dir, stage, revision, note, evidence)
    write_json_atomic(
        _review_path(project_dir, review.stage),
        {"schema_version": REVIEW_SCHEMA, **review.to_dict()},
    )
    return review


def _transaction_ownership_issue(
    project_dir: str | Path, review: StageReview
) -> str:
    if not review.transaction_id:
        return ""
    package_path = Path(project_dir).expanduser().resolve() / "production_package.json"
    package = _read_object(package_path)
    raw_stages = package.get("stages")
    if not isinstance(raw_stages, list):
        return "Production package has no stage records"
    record = next(
        (
            item
            for item in raw_stages
            if isinstance(item, dict) and item.get("stage") == review.stage.value
        ),
        None,
    )
    if record is None:
        return "Production package has no matching stage record"
    if str(record.get("review_transaction_id") or "") != review.transaction_id:
        return "Review transaction does not own the package approval"
    if int(record.get("revision")) != review.revision:
        return "Review transaction does not match the package revision"
    if record.get("review_state") != ReviewState.APPROVED.value:
        return "Review transaction is not committed by the package"
    return ""


def validate_stage_review(
    project_dir: str | Path,
    stage: StageName,
    *,
    expected_revision: StageRevision | None = None,
    expected_review: StageReview | None = None,
) -> ReviewValidation:
    target = StageName(stage)
    try:
        from .pipeline_store import (
            ApprovalInProgressError,
            recover_review_transactions,
        )

        recover_review_transactions(project_dir)
    except ApprovalInProgressError as exc:
        return ReviewValidation(False, str(exc), busy=True)
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return ReviewValidation(False, f"Review transaction is unreadable: {exc}")
    path = _review_path(project_dir, target)
    if not path.exists():
        return ReviewValidation(False, "No approved review exists")
    try:
        payload = _read_object(path)
        if payload.get("schema_version") != REVIEW_SCHEMA:
            return ReviewValidation(False, "Review record has an unsupported schema")
        review = StageReview.from_dict(payload)
        if review.stage is not target or review.state is not ReviewState.APPROVED:
            return ReviewValidation(False, "Review record is not an approval")
        if expected_review is not None and review != expected_review:
            return ReviewValidation(False, "Review record changed during validation")
        ownership_issue = _transaction_ownership_issue(project_dir, review)
        if ownership_issue:
            return ReviewValidation(False, ownership_issue, review)
        revisions = _load_revisions(project_dir, target)
        if not revisions or review.revision != revisions[-1].number:
            return ReviewValidation(False, "Review does not apply to the latest revision")
        revision = _current_revision(project_dir, target, review.revision)
        if expected_revision is not None and revision != expected_revision:
            return ReviewValidation(False, "Review does not match the bound revision")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return ReviewValidation(False, f"Review record is unreadable: {exc}")
    integrity_issue = _artifact_integrity_issue(revision.artifacts)
    if integrity_issue:
        return ReviewValidation(False, integrity_issue, review)
    integrity_issue = _artifact_integrity_issue(review.evidence)
    if integrity_issue:
        return ReviewValidation(False, integrity_issue, review)
    return ReviewValidation(True, review=review)
