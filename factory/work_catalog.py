from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import stat
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping

from .pipeline_contracts import (
    PIPELINE_STAGES,
    ProductionPackage,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from .pipeline_modes import get_mode_adapter
from .pipeline_review import (
    REVISIONS_SCHEMA,
    ApprovalPreset,
    ReviewConfig,
    StageRevision,
    resolve_review_config,
    validate_stage_review,
)
from .secure_posix import AnchoredDirectory


ARCHIVE_MANIFEST_SCHEMA = "storymotion.archive-manifest.v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UNVERIFIED_RIGHTS = {
    "origin": "unverified",
    "creator": "unverified",
    "license": "unverified",
    "commercial_use": "unverified",
    "redistribution_status": "unverified",
    "distribution_warning": (
        "Rights documentation is incomplete; do not redistribute publicly until cleared."
    ),
}


def _archive_text(value: Any, *, label: str, maximum: int) -> str:
    text = unicodedata.normalize("NFC", str(value)).strip()
    if (
        not text
        or len(text) > maximum
        or any(unicodedata.category(character) == "Cc" for character in text)
    ):
        raise ValueError(f"archive {label} is invalid")
    return text


def _opaque(prefix: str, *parts: str, length: int = 32) -> str:
    value = "\0".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(value).hexdigest()[:length]}"


def _safe_relative(value: str, *, label: str) -> str:
    path = PurePosixPath(str(value))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} is unsafe")
    return path.as_posix()


def _media_kind(name: str, media_type: str = "") -> str:
    mime = (
        media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    ).lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/json":
        return "eval" if "eval" in name.lower() else "text"
    if mime.startswith("text/"):
        return "text"
    return "file"


def _mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _trusted_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return text


def _delivery_date(value: str) -> str:
    return value[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value) else ""


def _open_parent(anchor: AnchoredDirectory, relative: Path) -> tuple[int, str]:
    if len(relative.parts) == 1:
        return os.dup(anchor.descriptor), relative.name
    return anchor.open_directory(Path(*relative.parts[:-1])), relative.name


@dataclass(frozen=True)
class FileEvidence:
    size_bytes: int
    sha256: str
    device: int
    inode: int
    modified_ns: int
    changed_ns: int


def _stream_file_evidence(
    anchor: AnchoredDirectory,
    raw_path: str | Path,
) -> tuple[Path, FileEvidence]:
    relative = anchor.relative_path(raw_path)
    parent, name = _open_parent(anchor, relative)
    descriptor = -1
    try:
        listed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
            raise ValueError("catalog artifact is not a regular file")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or (listed.st_dev, listed.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ValueError("catalog artifact identity changed")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if identity(before) != identity(after) or identity(before) != identity(current):
            raise ValueError("catalog artifact changed while hashing")
        return relative, FileEvidence(
            size_bytes=size,
            sha256=digest.hexdigest(),
            device=after.st_dev,
            inode=after.st_ino,
            modified_ns=after.st_mtime_ns,
            changed_ns=after.st_ctime_ns,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


@dataclass(frozen=True)
class ArchiveEntry:
    entry_id: str
    source_relative: str
    archive_relative: str
    classification: Literal["linked", "unclassified"]
    collection_id: str
    title: str
    version_label: str
    media_type: str
    media_kind: str
    size_bytes: int
    sha256: str
    created_at: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)
    rights: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SAFE_TOKEN.fullmatch(self.entry_id):
            raise ValueError("archive entry id is invalid")
        object.__setattr__(
            self,
            "source_relative",
            _safe_relative(self.source_relative, label="source path"),
        )
        object.__setattr__(
            self,
            "archive_relative",
            _safe_relative(self.archive_relative, label="archive path"),
        )
        if self.classification not in {"linked", "unclassified"}:
            raise ValueError("archive classification is invalid")
        if not _SAFE_TOKEN.fullmatch(self.collection_id):
            raise ValueError("archive collection id is invalid")
        object.__setattr__(
            self, "title", _archive_text(self.title, label="title", maximum=2048)
        )
        object.__setattr__(
            self,
            "version_label",
            _archive_text(self.version_label, label="version", maximum=512),
        )
        if self.size_bytes < 0 or not _HASH.fullmatch(self.sha256):
            raise ValueError("archive file evidence is invalid")
        if self.media_kind not in {"text", "image", "audio", "video", "eval", "file"}:
            raise ValueError("archive media kind is invalid")
        object.__setattr__(
            self,
            "metadata",
            {
                _archive_text(key, label="metadata key", maximum=128): _archive_text(
                    value, label="metadata value", maximum=4096
                )
                for key, value in self.metadata.items()
            },
        )
        normalized_rights = {
            _archive_text(key, label="rights key", maximum=128): _archive_text(
                value, label="rights value", maximum=2048
            )
            for key, value in self.rights.items()
        }
        if not set(_UNVERIFIED_RIGHTS).issubset(normalized_rights):
            normalized_rights = dict(_UNVERIFIED_RIGHTS)
        object.__setattr__(self, "rights", normalized_rights)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "source_relative": self.source_relative,
            "archive_relative": self.archive_relative,
            "classification": self.classification,
            "collection_id": self.collection_id,
            "title": self.title,
            "version_label": self.version_label,
            "media_type": self.media_type,
            "media_kind": self.media_kind,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "metadata": dict(sorted(self.metadata.items())),
            "rights": dict(sorted(self.rights.items())),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArchiveEntry:
        metadata = value.get("metadata") or {}
        rights = value.get("rights") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("archive metadata is invalid")
        if not isinstance(rights, Mapping):
            raise ValueError("archive rights metadata is invalid")
        return cls(
            entry_id=str(value["entry_id"]),
            source_relative=str(value["source_relative"]),
            archive_relative=str(value["archive_relative"]),
            classification=str(value["classification"]),  # type: ignore[arg-type]
            collection_id=str(value["collection_id"]),
            title=str(value["title"]),
            version_label=str(value["version_label"]),
            media_type=str(value["media_type"]),
            media_kind=str(value["media_kind"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
            created_at=_trusted_time(value.get("created_at")),
            metadata={str(key): str(item) for key, item in metadata.items()},
            rights={str(key): str(item) for key, item in rights.items()},
        )


@dataclass(frozen=True)
class ArchiveManifest:
    entries: tuple[ArchiveEntry, ...]
    archive_root: Path | None = field(default=None, compare=False, repr=False)
    schema_version: str = ARCHIVE_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != ARCHIVE_MANIFEST_SCHEMA:
            raise ValueError("archive manifest schema is unsupported")
        ordered = tuple(sorted(self.entries, key=lambda item: item.source_relative))
        if len({entry.entry_id for entry in ordered}) != len(ordered):
            raise ValueError("archive manifest contains duplicate entry ids")
        if len({entry.source_relative for entry in ordered}) != len(ordered):
            raise ValueError("archive manifest contains duplicate source files")
        if len({entry.archive_relative for entry in ordered}) != len(ordered):
            raise ValueError("archive manifest contains duplicate destinations")
        object.__setattr__(self, "entries", ordered)

    @property
    def linked(self) -> tuple[str, ...]:
        return tuple(
            entry.source_relative
            for entry in self.entries
            if entry.classification == "linked"
        )

    @property
    def unclassified(self) -> tuple[str, ...]:
        return tuple(
            entry.source_relative
            for entry in self.entries
            if entry.classification == "unclassified"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_file_count": len(self.entries),
            "linked_count": len(self.linked),
            "unclassified_count": len(self.unclassified),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, archive_root: Path | None = None
    ) -> ArchiveManifest:
        if value.get("schema_version") != ARCHIVE_MANIFEST_SCHEMA:
            raise ValueError("archive manifest schema is unsupported")
        raw_entries = value.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("archive manifest entries are invalid")
        manifest = cls(
            entries=tuple(
                ArchiveEntry.from_dict(item)
                for item in raw_entries
                if isinstance(item, Mapping)
            ),
            archive_root=archive_root,
        )
        if len(manifest.entries) != len(raw_entries):
            raise ValueError("archive manifest entries are invalid")
        if value.get("source_file_count") not in {None, len(manifest.entries)}:
            raise ValueError("archive manifest accounting is invalid")
        return manifest


@dataclass(frozen=True)
class CatalogArtifact:
    artifact_id: str
    name: str
    media_type: str
    kind: str
    size_bytes: int
    sha256: str
    path: Path = field(compare=False, repr=False)
    rights: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkVersion:
    version_id: str
    label: str
    created_at: str
    outputs: tuple[CatalogArtifact, ...]
    eval_reports: tuple[CatalogArtifact, ...] = ()
    iteration_summary: str = ""


@dataclass(frozen=True)
class WorkRecord:
    work_id: str
    project_id: str
    title: str
    mode: Literal["original", "novel", "replica", "historical"]
    source: Literal["delivered", "historical"]
    delivered_at: str
    current_version: str
    versions: tuple[WorkVersion, ...]
    roles: tuple[str, ...] = ("未知角色",)
    delivery_date: str = ""
    cover: CatalogArtifact | None = None


@dataclass(frozen=True)
class WorkCatalog:
    works: tuple[WorkRecord, ...]
    warnings: tuple[str, ...] = ()

    def find(self, work_id: str) -> WorkRecord:
        for work in self.works:
            if work.work_id == work_id:
                return work
        raise KeyError(work_id)

    def to_public(self) -> dict[str, Any]:
        return {
            "works": [
                {
                    "work_id": work.work_id,
                    "project_id": work.project_id,
                    "title": work.title,
                    "mode": work.mode,
                    "source": work.source,
                    "delivered_at": work.delivered_at,
                    "current_version": work.current_version,
                    "roles": list(work.roles),
                    "delivery_date": work.delivery_date,
                }
                for work in self.works
            ],
            "warnings": list(self.warnings),
        }

    def artifacts(self) -> Iterable[CatalogArtifact]:
        for work in self.works:
            if work.cover is not None:
                yield work.cover
            for version in work.versions:
                yield from version.outputs
                yield from version.eval_reports


def _read_json(anchor: AnchoredDirectory, relative: str) -> dict[str, Any]:
    try:
        value = json.loads(anchor.read_bytes(relative).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("catalog record is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("catalog record is invalid")
    return value


def _artifact_from_project(
    anchor: AnchoredDirectory,
    raw_path: str,
    *,
    allowed_prefix: str,
    expected_sha256: str = "",
    kind: str | None = None,
) -> CatalogArtifact:
    relative = anchor.relative_path(raw_path)
    prefix = PurePosixPath(allowed_prefix)
    candidate = PurePosixPath(relative.as_posix())
    if candidate == prefix or not candidate.is_relative_to(prefix):
        raise ValueError("catalog artifact escaped its registered stage")
    relative, evidence = _stream_file_evidence(anchor, relative)
    digest = evidence.sha256
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("catalog artifact hash changed")
    name = relative.name
    media_type = _mime(name)
    return CatalogArtifact(
        artifact_id=_opaque("art", "delivery", digest, relative.as_posix()),
        name=name,
        media_type=media_type,
        kind=kind or _media_kind(name, media_type),
        size_bytes=evidence.size_bytes,
        sha256=digest,
        path=anchor.canonical_path / relative,
    )


def _stage_revision(
    project: AnchoredDirectory, record: Any, stage: StageName
) -> dict[str, FileEvidence]:
    payload = _read_json(project, f"reviews/{stage.value}.revisions.json")
    raw_revisions = payload.get("revisions")
    if (
        payload.get("schema_version") != REVISIONS_SCHEMA
        or payload.get("stage") != stage.value
        or not isinstance(raw_revisions, list)
    ):
        raise ValueError(f"{stage.value} revision history is invalid")
    revisions = tuple(StageRevision.from_dict(item) for item in raw_revisions)
    if (
        not revisions
        or record.revision is None
        or revisions[-1].number != record.revision
    ):
        raise ValueError(f"{stage.value} does not reference the latest revision")
    latest = revisions[-1]
    if (
        latest.stage is not stage
        or latest.executor != record.executor
        or latest.input_signature != record.input_signature
    ):
        raise ValueError(f"{stage.value} revision does not match the package stage")
    revision_paths: dict[str, str] = {}
    for artifact in latest.artifacts:
        relative = project.relative_path(artifact.path).as_posix()
        if relative in revision_paths:
            raise ValueError(f"{stage.value} revision contains duplicate artifacts")
        revision_paths[relative] = artifact.sha256
    package_paths = {
        project.relative_path(raw_path).as_posix() for raw_path in record.artifacts
    }
    if set(revision_paths) != package_paths:
        raise ValueError(f"{stage.value} package artifacts do not match its revision")
    evidence: dict[str, FileEvidence] = {}
    for relative, expected_digest in revision_paths.items():
        _, current = _stream_file_evidence(project, relative)
        if current.sha256 != expected_digest:
            raise ValueError(f"{stage.value} artifact changed after revision")
        evidence[relative] = current
    return evidence


def _delivery_eval_reports(
    project: AnchoredDirectory,
    manifest: Mapping[str, Any],
) -> tuple[CatalogArtifact, ...]:
    binding = manifest.get("eval_evidence")
    if not isinstance(binding, Mapping) or binding.get("stage") != StageName.EVAL.value:
        return ()
    try:
        revision_number = int(binding["revision"])
    except (KeyError, TypeError, ValueError):
        return ()
    review = binding.get("review")
    raw_reports = binding.get("reports")
    if (
        revision_number < 1
        or not isinstance(review, Mapping)
        or int(review.get("revision") or 0) != revision_number
        or not _HASH.fullmatch(str(review.get("sha256") or ""))
        or not isinstance(raw_reports, list)
        or not raw_reports
    ):
        return ()
    payload = _read_json(project, "reviews/eval.revisions.json")
    raw_revisions = payload.get("revisions")
    if (
        payload.get("schema_version") != REVISIONS_SCHEMA
        or payload.get("stage") != StageName.EVAL.value
        or not isinstance(raw_revisions, list)
    ):
        return ()
    revisions = tuple(StageRevision.from_dict(item) for item in raw_revisions)
    selected = next(
        (revision for revision in revisions if revision.number == revision_number),
        None,
    )
    if (
        selected is None
        or selected.stage is not StageName.EVAL
        or selected.input_signature != str(binding.get("input_signature") or "")
        or selected.executor != str(binding.get("executor") or "")
    ):
        return ()
    revision_artifacts: dict[str, str] = {}
    for artifact in selected.artifacts:
        relative = project.relative_path(artifact.path).as_posix()
        if relative in revision_artifacts:
            return ()
        revision_artifacts[relative] = artifact.sha256
    requested: dict[str, str] = {}
    for item in raw_reports:
        if not isinstance(item, Mapping):
            return ()
        relative = project.relative_path(str(item.get("path") or "")).as_posix()
        digest = str(item.get("sha256") or "")
        if (
            relative in requested
            or not PurePosixPath(relative).is_relative_to(PurePosixPath("stages/eval"))
            or not _HASH.fullmatch(digest)
            or revision_artifacts.get(relative) != digest
        ):
            return ()
        requested[relative] = digest
    return tuple(
        _artifact_from_project(
            project,
            relative,
            allowed_prefix="stages/eval",
            expected_sha256=digest,
            kind="eval",
        )
        for relative, digest in sorted(requested.items())
    )


def _review_config(spec: ProjectSpec) -> ReviewConfig:
    adapter = get_mode_adapter(spec.mode)
    raw_preset = spec.policies.get("approval_preset")
    if raw_preset:
        overrides = spec.policies.get("review_overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError("review overrides are invalid")
        return resolve_review_config(ApprovalPreset(str(raw_preset)), overrides)
    return ReviewConfig(
        ApprovalPreset.QUICK,
        {
            stage: adapter.stage_steps[stage].compatibility_review_policy
            for stage in PIPELINE_STAGES
        },
    )


def _review_is_current(
    project: AnchoredDirectory,
    record: Any,
    stage: StageName,
    expected_policy: ReviewPolicy,
) -> None:
    if record.review_blocks_progress:
        raise ValueError(f"{stage.value} review still blocks progress")
    if expected_policy not in {ReviewPolicy.MANUAL, ReviewPolicy.GROUPED}:
        raise ValueError(f"{stage.value} requires human review")
    if record.review_policy is not expected_policy:
        raise ValueError(f"{stage.value} package review policy is stale")
    if record.review_state is not ReviewState.APPROVED:
        raise ValueError(f"{stage.value} is not durably approved")
    validation = validate_stage_review(project.canonical_path, stage)
    if not validation.valid or validation.review is None:
        raise ValueError(f"{stage.value} review evidence is invalid")
    if validation.review.revision != record.revision:
        raise ValueError(f"{stage.value} review applies to a stale revision")


def _manifest_masters(
    project: AnchoredDirectory,
    package: ProductionPackage,
    deliver: Any,
    manifest: Mapping[str, Any],
    revision_evidence: Mapping[str, FileEvidence],
) -> tuple[CatalogArtifact, ...]:
    schema = manifest.get("schema_version")
    declared_hashes: dict[str, str] = {}
    if schema == "motion-comic-factory.delivery.v1":
        raw_master = manifest.get("master")
        digest = str(manifest.get("sha256") or "")
        if (
            manifest.get("project_id") != package.project_id
            or not isinstance(raw_master, str)
            or not _HASH.fullmatch(digest)
        ):
            raise ValueError("delivery manifest master is invalid")
        masters = (raw_master,)
        declared_hashes[raw_master] = digest
    elif schema == "motion-comic-factory.replica-delivery.v1":
        raw_masters = manifest.get("masters")
        if (
            not isinstance(manifest.get("workspace"), str)
            or not str(manifest.get("workspace")).strip()
            or not isinstance(manifest.get("operation"), Mapping)
            or manifest.get("publication_status") != "REVIEW_REQUIRED"
            or not isinstance(raw_masters, list)
            or not raw_masters
            or any(
                not isinstance(item, str) or not item.strip() for item in raw_masters
            )
            or len(set(raw_masters)) != len(raw_masters)
        ):
            raise ValueError("replica delivery masters are invalid")
        normalized = tuple(project.relative_path(item) for item in raw_masters)
        if len({item.as_posix() for item in normalized}) != len(normalized):
            raise ValueError("replica delivery masters contain normalized aliases")
        masters = tuple(item.as_posix() for item in normalized)
    else:
        raise ValueError("delivery manifest schema is unsupported")
    package_outputs = {
        project.relative_path(raw_path).as_posix() for raw_path in package.final_outputs
    }
    stage_artifacts = {
        project.relative_path(raw_path).as_posix() for raw_path in deliver.artifacts
    }
    outputs: list[CatalogArtifact] = []
    artifact_ids: set[str] = set()
    for raw_master in masters:
        relative = project.relative_path(raw_master)
        key = relative.as_posix()
        if (
            not PurePosixPath(key).is_relative_to(PurePosixPath("stages/deliver"))
            or key not in package_outputs
            or key not in stage_artifacts
            or key not in revision_evidence
        ):
            raise ValueError("delivery master is not registered everywhere")
        evidence = revision_evidence[key]
        if evidence.sha256 != declared_hashes.get(raw_master, evidence.sha256):
            raise ValueError("delivery master hash does not match its manifest")
        media_type = _mime(relative.name)
        if not media_type.startswith("video/"):
            raise ValueError("delivery master is not video media")
        artifact_id = _opaque("art", "delivery", evidence.sha256, key)
        if artifact_id in artifact_ids:
            raise ValueError("delivery masters contain duplicate artifact ids")
        artifact_ids.add(artifact_id)
        outputs.append(
            CatalogArtifact(
                artifact_id=artifact_id,
                name=relative.name,
                media_type=media_type,
                kind="video",
                size_bytes=evidence.size_bytes,
                sha256=evidence.sha256,
                path=project.canonical_path / relative,
            )
        )
    return tuple(outputs)


def _authoritative_roles(spec: ProjectSpec) -> tuple[str, ...]:
    roles: list[str] = []
    for character in spec.characters:
        label = str(
            character.get("name")
            or character.get("role_name")
            or character.get("character_id")
            or character.get("id")
            or ""
        ).strip()
        if label and label not in roles:
            roles.append(label[:80])
    return tuple(roles) or ("未知角色",)


def _delivered_work(project: AnchoredDirectory) -> WorkRecord:
    spec = ProjectSpec.from_dict(_read_json(project, "project.json"))
    package = ProductionPackage.from_dict(
        _read_json(project, "production_package.json")
    )
    if (
        package.project_id != spec.project_id
        or package.mode != spec.mode
        or package.spec_sha256 != spec.sha256
    ):
        raise ValueError("project catalog records disagree")
    deliver = next(
        record for record in package.stages if record.stage is StageName.DELIVER
    )
    if deliver.state is not StageState.PASSED or deliver.revision is None:
        raise ValueError("delivery is not passed")
    review_config = _review_config(spec)
    revision_evidence = _stage_revision(project, deliver, StageName.DELIVER)
    _review_is_current(
        project,
        deliver,
        StageName.DELIVER,
        review_config.policy_for(StageName.DELIVER),
    )
    manifest_raw = next(
        (
            value
            for value in deliver.artifacts
            if Path(value).name == "delivery_manifest.json"
        ),
        "",
    )
    if not manifest_raw:
        raise ValueError("delivery manifest is missing")
    manifest_relative = project.relative_path(manifest_raw)
    if manifest_relative.as_posix() != "stages/deliver/delivery_manifest.json":
        raise ValueError("delivery manifest is not authoritative")
    manifest = _read_json(project, manifest_relative.as_posix())
    masters = _manifest_masters(project, package, deliver, manifest, revision_evidence)
    try:
        eval_reports = _delivery_eval_reports(project, manifest)
    except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
        eval_reports = ()
    revision = deliver.revision
    label = str(manifest.get("version") or f"V{revision}").strip()[:40]
    delivered_at = _trusted_time(manifest.get("delivered_at"))
    version_digest = hashlib.sha256(
        "\0".join(master.sha256 for master in masters).encode("ascii")
    ).hexdigest()
    version = WorkVersion(
        version_id=_opaque("version", spec.project_id, version_digest, str(revision)),
        label=label,
        created_at=delivered_at,
        outputs=masters,
        eval_reports=eval_reports,
        iteration_summary=str(manifest.get("iteration_summary") or "").strip()[:500],
    )
    return WorkRecord(
        work_id=_opaque("work", "delivered", spec.project_id),
        project_id=spec.project_id,
        title=spec.title,
        mode=spec.mode.value,  # type: ignore[arg-type]
        source="delivered",
        delivered_at=delivered_at,
        current_version=label,
        versions=(version,),
        roles=_authoritative_roles(spec),
        delivery_date=_delivery_date(delivered_at),
    )


def _load_archive_manifest(
    value: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> ArchiveManifest | None:
    if value is None:
        return None
    if isinstance(value, ArchiveManifest):
        return value
    if isinstance(value, Mapping):
        return ArchiveManifest.from_dict(value)
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise ValueError("archive manifest is unavailable")
    try:
        with AnchoredDirectory.open(path.parent, label="Archive catalog root") as root:
            payload = json.loads(root.read_bytes(path.name).decode("utf-8"))
            archive_root = root.canonical_path
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("archive manifest is invalid") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("archive manifest is invalid")
    return ArchiveManifest.from_dict(payload, archive_root=archive_root)


def _archive_works(manifest: ArchiveManifest) -> tuple[list[WorkRecord], list[str]]:
    if manifest.archive_root is None:
        return [], ["archive_root_unavailable"]
    grouped: dict[str, list[ArchiveEntry]] = {}
    for entry in manifest.entries:
        grouped.setdefault(entry.collection_id, []).append(entry)
    works: list[WorkRecord] = []
    warnings: list[str] = []
    collection_order = sorted(
        grouped,
        key=lambda key: (key == "historical-unclassified", key),
    )
    try:
        archive_anchor = AnchoredDirectory.open(
            manifest.archive_root, label="Archive catalog root"
        )
    except ValueError:
        return [], ["archive_root_unavailable"]
    with archive_anchor:
        for collection_id in collection_order:
            entries = sorted(
                grouped[collection_id], key=lambda item: item.source_relative
            )
            labels = [entry.version_label for entry in entries]
            if len(set(labels)) != len(labels):
                warnings.append("duplicate_archive_version")
                continue
            versions: list[WorkVersion] = []
            for entry in entries:
                try:
                    _relative, evidence = _stream_file_evidence(
                        archive_anchor, entry.archive_relative
                    )
                    if (
                        evidence.size_bytes != entry.size_bytes
                        or evidence.sha256 != entry.sha256
                    ):
                        raise ValueError
                except (FileNotFoundError, OSError, ValueError):
                    warnings.append(f"archive_entry_unavailable:{entry.entry_id}")
                    continue
                artifact = CatalogArtifact(
                    artifact_id=_opaque("art", "archive", entry.entry_id, entry.sha256),
                    name=Path(entry.archive_relative).name,
                    media_type=entry.media_type,
                    kind=entry.media_kind,
                    size_bytes=entry.size_bytes,
                    sha256=entry.sha256,
                    path=archive_anchor.canonical_path / entry.archive_relative,
                    rights=entry.rights,
                )
                versions.append(
                    WorkVersion(
                        version_id=_opaque("version", "archive", entry.entry_id),
                        label=entry.version_label,
                        created_at=entry.created_at,
                        outputs=(artifact,),
                        iteration_summary=str(entry.metadata.get("description") or "")[
                            :500
                        ],
                    )
                )
            if not versions:
                continue
            historical = entries[0].classification == "unclassified"
            roles = tuple(
                dict.fromkeys(
                    str(entry.metadata.get("role") or "").strip()
                    for entry in entries
                    if str(entry.metadata.get("role") or "").strip()
                )
            ) or ("未知角色",)
            delivered_at = max((item.created_at for item in versions), default="")
            works.append(
                WorkRecord(
                    work_id=_opaque("work", "archive", collection_id),
                    project_id="",
                    title="历史归档" if historical else entries[0].title,
                    mode="historical",
                    source="historical",
                    delivered_at=delivered_at,
                    current_version=f"{len(versions)} 项素材",
                    versions=tuple(versions),
                    roles=roles,
                    delivery_date=_delivery_date(delivered_at),
                )
            )
    return works, warnings


_StatIdentity = tuple[int, int, int, int, int, int]


def _stat_identity(path: Path) -> _StatIdentity | None:
    try:
        value = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (
        stat.S_IFMT(value.st_mode),
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _dependency_identity(path: Path) -> _StatIdentity | None:
    identity = _stat_identity(path)
    if identity is not None and identity[0] == stat.S_IFDIR:
        return (*identity[:3], 0, 0, 0)
    return identity


@dataclass(frozen=True)
class _CachedProject:
    work: WorkRecord
    dependencies: tuple[tuple[Path, _StatIdentity | None], ...]


@dataclass(frozen=True)
class _CachedArchive:
    works: tuple[WorkRecord, ...]
    warnings: tuple[str, ...]
    dependencies: tuple[tuple[Path, _StatIdentity | None], ...]


def _dependencies_current(
    dependencies: tuple[tuple[Path, _StatIdentity | None], ...],
) -> bool:
    return all(
        _dependency_identity(path) == identity for path, identity in dependencies
    )


def _dependency_tree_paths(root: Path) -> tuple[Path, ...]:
    if not root.exists() and not root.is_symlink():
        return (root,)
    paths = [root]
    if root.is_dir() and not root.is_symlink():
        for directory, names, files in os.walk(root, followlinks=False):
            base = Path(directory)
            names[:] = [name for name in names if not name.startswith(".")]
            paths.extend(base / name for name in sorted(names))
            paths.extend(
                base / name for name in sorted(files) if not name.startswith(".")
            )
    return tuple(paths)


def _registered_evidence_paths(project: Path) -> tuple[Path, ...]:
    found: set[Path] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            raw_path = value.get("path")
            digest = str(value.get("sha256") or "")
            if isinstance(raw_path, str) and _HASH.fullmatch(digest):
                candidate = Path(raw_path).expanduser()
                found.add(candidate if candidate.is_absolute() else project / candidate)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    paths = [
        project / "reviews" / f"{stage}.{kind}.json"
        for stage in (StageName.EVAL.value, StageName.DELIVER.value)
        for kind in ("revisions", "review")
    ]
    paths.append(project / "stages" / "deliver" / "delivery_manifest.json")
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        visit(payload)
    try:
        package = json.loads(
            (project / "production_package.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        package = {}
    if isinstance(package, Mapping):
        for record in package.get("stages") or ():
            if not isinstance(record, Mapping) or record.get("stage") not in {
                StageName.EVAL.value,
                StageName.DELIVER.value,
            }:
                continue
            for raw_path in record.get("artifacts") or ():
                if isinstance(raw_path, str):
                    candidate = Path(raw_path).expanduser()
                    found.add(
                        candidate if candidate.is_absolute() else project / candidate
                    )
    return tuple(sorted(found, key=str))


def _authoritative_project_paths(project: Path) -> tuple[Path, ...]:
    values = {
        project,
        project / "project.json",
        project / "production_package.json",
    }
    values.update(
        project / "reviews" / f"{stage}.{kind}.json"
        for stage in (StageName.EVAL.value, StageName.DELIVER.value)
        for kind in ("revisions", "review")
    )
    transaction_root = project / "reviews" / ".transactions"
    if transaction_root.exists() or transaction_root.is_symlink():
        values.update(_dependency_tree_paths(transaction_root))
    values.update(_registered_evidence_paths(project))
    return tuple(sorted(values, key=str))


def _project_dependencies(project: Path, work: WorkRecord) -> tuple[Path, ...]:
    values = set(_authoritative_project_paths(project))
    for artifact in WorkCatalog((work,)).artifacts():
        values.add(artifact.path)
    return tuple(sorted(values, key=str))


def _catalog_dependency_snapshot(
    runs: Path,
    archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> tuple[tuple[Path, _StatIdentity | None], ...]:
    paths: set[Path] = {runs}
    if runs.is_dir() and not runs.is_symlink():
        for project in runs.iterdir():
            if (
                not project.is_symlink()
                and project.is_dir()
                and _SAFE_TOKEN.fullmatch(project.name)
            ):
                paths.add(project)
                paths.update(_authoritative_project_paths(project))
    paths.update(_archive_dependency_paths(archive_manifest))
    return tuple((path, _dependency_identity(path)) for path in sorted(paths, key=str))


def _archive_source_key(
    archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> str:
    if isinstance(archive_manifest, (str, Path)):
        return f"path:{Path(archive_manifest).expanduser().absolute()}"
    if isinstance(archive_manifest, ArchiveManifest):
        encoded = json.dumps(
            archive_manifest.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"object:{archive_manifest.archive_root}:{digest}"
    return repr(archive_manifest)


def _archive_dependency_paths(
    archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> tuple[Path, ...]:
    if archive_manifest is None:
        return ()
    paths: set[Path] = set()
    if isinstance(archive_manifest, (str, Path)):
        paths.add(Path(archive_manifest).expanduser().absolute())
    try:
        manifest = _load_archive_manifest(archive_manifest)
    except (OSError, TypeError, ValueError):
        return tuple(sorted(paths, key=str))
    if manifest is not None and manifest.archive_root is not None:
        paths.add(manifest.archive_root)
        paths.update(
            manifest.archive_root / entry.archive_relative for entry in manifest.entries
        )
    return tuple(sorted(paths, key=str))


class WorkCatalogCache:
    """Bounded, thread-safe cache keyed by authoritative file identities."""

    def __init__(self, *, max_entries: int = 256):
        if max_entries < 1:
            raise ValueError("catalog cache size must be positive")
        self.max_entries = max_entries
        self._projects: OrderedDict[str, _CachedProject] = OrderedDict()
        self._archive: _CachedArchive | None = None
        self._archive_source = ""
        self._catalog_source: tuple[str, str] | None = None
        self._catalog_dependencies: tuple[tuple[Path, _StatIdentity | None], ...] = ()
        self._catalog: WorkCatalog | None = None
        self._lock = threading.RLock()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._projects) + int(self._archive is not None)

    def _project_work(
        self, root: AnchoredDirectory, name: str
    ) -> tuple[WorkRecord | None, str | None]:
        key = str(root.canonical_path / name)
        cached = self._projects.get(key)
        if cached is not None and _dependencies_current(cached.dependencies):
            self._projects.move_to_end(key)
            return cached.work, None
        try:
            project = root.child(name, label="Delivered project")
        except (FileNotFoundError, OSError, ValueError):
            self._projects.pop(key, None)
            return None, None
        try:
            for attempt in range(2):
                before_paths = _authoritative_project_paths(project.canonical_path)
                before = tuple(
                    (path, _dependency_identity(path)) for path in before_paths
                )
                work = _delivered_work(project)
                paths = _project_dependencies(project.canonical_path, work)
                dependencies = tuple(
                    (path, _dependency_identity(path)) for path in paths
                )
                before_map = dict(before)
                after_authoritative = tuple(
                    (path, _dependency_identity(path))
                    for path in _authoritative_project_paths(project.canonical_path)
                )
                if before_map == dict(after_authoritative):
                    break
                if attempt == 1:
                    raise ValueError("project changed while building catalog")
            self._projects[key] = _CachedProject(work, dependencies)
            self._projects.move_to_end(key)
            while len(self._projects) > self.max_entries:
                self._projects.popitem(last=False)
            return work, None
        except (
            FileNotFoundError,
            KeyError,
            OSError,
            StopIteration,
            TypeError,
            ValueError,
        ):
            self._projects.pop(key, None)
            return None, f"invalid_delivery:{_opaque('record', name, length=16)}"
        finally:
            project.close()

    def _archive_works_cached(
        self,
        archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
    ) -> tuple[list[WorkRecord], list[str]]:
        if archive_manifest is None:
            self._archive = None
            self._archive_source = ""
            return [], []
        source = _archive_source_key(archive_manifest)
        if (
            source == self._archive_source
            and self._archive is not None
            and _dependencies_current(self._archive.dependencies)
        ):
            return list(self._archive.works), list(self._archive.warnings)
        manifest = _load_archive_manifest(archive_manifest)
        if manifest is None:
            return [], []
        works, warnings = _archive_works(manifest)
        if manifest.archive_root is not None:
            paths = _archive_dependency_paths(archive_manifest)
            self._archive = _CachedArchive(
                tuple(works),
                tuple(warnings),
                tuple((path, _dependency_identity(path)) for path in paths),
            )
            self._archive_source = source
        return works, warnings

    def build(
        self,
        runs_dir: str | Path,
        archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
    ) -> WorkCatalog:
        with self._lock:
            runs = Path(runs_dir).expanduser()
            source_key = (
                str(runs.absolute()),
                _archive_source_key(archive_manifest),
            )
            for attempt in range(2):
                before = _catalog_dependency_snapshot(runs, archive_manifest)
                if (
                    self._catalog is not None
                    and self._catalog_source == source_key
                    and self._catalog_dependencies == before
                ):
                    return self._catalog
                catalog = self._build_once(runs, archive_manifest)
                after = _catalog_dependency_snapshot(runs, archive_manifest)
                if before == after:
                    self._catalog_source = source_key
                    self._catalog_dependencies = after
                    self._catalog = catalog
                    return catalog
                if attempt == 1:
                    return WorkCatalog(works=(), warnings=("catalog_dependency_race",))
            raise AssertionError("unreachable")

    def _build_once(
        self,
        runs: Path,
        archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
    ) -> WorkCatalog:
        delivered: list[WorkRecord] = []
        warnings: list[str] = []
        active_keys: set[str] = set()
        if runs.exists():
            try:
                with AnchoredDirectory.open(runs, label="Work catalog runs") as root:
                    for name in sorted(root.listdir()):
                        if name.startswith(".") or not _SAFE_TOKEN.fullmatch(name):
                            continue
                        active_keys.add(str(root.canonical_path / name))
                        work, warning = self._project_work(root, name)
                        if work is not None:
                            delivered.append(work)
                        if warning:
                            warnings.append(warning)
            except ValueError:
                warnings.append("runs_catalog_unavailable")
        for key in tuple(self._projects):
            if key.startswith(str(runs.absolute())) and key not in active_keys:
                self._projects.pop(key, None)
        try:
            archive_works, archive_warnings = self._archive_works_cached(
                archive_manifest
            )
            warnings.extend(archive_warnings)
        except (OSError, TypeError, ValueError):
            archive_works = []
            warnings.append("archive_manifest_invalid")
        delivered.sort(
            key=lambda item: (item.delivered_at, item.title, item.work_id),
            reverse=True,
        )
        works = tuple(delivered + archive_works)
        if len({work.work_id for work in works}) != len(works):
            return WorkCatalog(
                works=(),
                warnings=tuple(sorted(set((*warnings, "duplicate_work_id")))),
            )
        return WorkCatalog(works=works, warnings=tuple(sorted(set(warnings))))


def build_work_catalog(
    runs_dir: str | Path,
    archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> WorkCatalog:
    return WorkCatalogCache().build(runs_dir, archive_manifest)
