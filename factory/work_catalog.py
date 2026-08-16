from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping

from .pipeline_contracts import (
    ProductionPackage,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from .pipeline_review import REVISIONS_SCHEMA, StageRevision, validate_stage_review
from .secure_posix import AnchoredDirectory


ARCHIVE_MANIFEST_SCHEMA = "storymotion.archive-manifest.v1"
_HASH = re.compile(r"[0-9a-f]{64}")
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


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
        if not self.title.strip() or not self.version_label.strip():
            raise ValueError("archive title and version are required")
        if self.size_bytes < 0 or not _HASH.fullmatch(self.sha256):
            raise ValueError("archive file evidence is invalid")
        if self.media_kind not in {"text", "image", "audio", "video", "eval", "file"}:
            raise ValueError("archive media kind is invalid")
        object.__setattr__(self, "metadata", dict(self.metadata))

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
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArchiveEntry:
        metadata = value.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("archive metadata is invalid")
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


def _delivery_revision(
    project: AnchoredDirectory, deliver: Any
) -> dict[str, FileEvidence]:
    payload = _read_json(project, "reviews/deliver.revisions.json")
    raw_revisions = payload.get("revisions")
    if (
        payload.get("schema_version") != REVISIONS_SCHEMA
        or payload.get("stage") != StageName.DELIVER.value
        or not isinstance(raw_revisions, list)
    ):
        raise ValueError("delivery revision history is invalid")
    revisions = tuple(StageRevision.from_dict(item) for item in raw_revisions)
    if (
        not revisions
        or deliver.revision is None
        or revisions[-1].number != deliver.revision
    ):
        raise ValueError("delivery does not reference the latest revision")
    latest = revisions[-1]
    if (
        latest.stage is not StageName.DELIVER
        or latest.executor != deliver.executor
        or latest.input_signature != deliver.input_signature
    ):
        raise ValueError("delivery revision does not match the package stage")
    revision_paths: dict[str, str] = {}
    for artifact in latest.artifacts:
        relative = project.relative_path(artifact.path).as_posix()
        if relative in revision_paths:
            raise ValueError("delivery revision contains duplicate artifacts")
        revision_paths[relative] = artifact.sha256
    package_paths = {
        project.relative_path(raw_path).as_posix() for raw_path in deliver.artifacts
    }
    if set(revision_paths) != package_paths:
        raise ValueError(
            "delivery package artifacts do not match the immutable revision"
        )
    evidence: dict[str, FileEvidence] = {}
    for relative, expected_digest in revision_paths.items():
        _, current = _stream_file_evidence(project, relative)
        if current.sha256 != expected_digest:
            raise ValueError("delivery artifact changed after revision")
        evidence[relative] = current
    return evidence


def _delivery_review_is_current(project: AnchoredDirectory, deliver: Any) -> None:
    if deliver.review_blocks_progress:
        raise ValueError("delivery review still blocks progress")
    if deliver.review_policy is ReviewPolicy.AUTOMATIC:
        if deliver.review_state is not ReviewState.AUTO_APPROVED:
            raise ValueError("automatic delivery has no current approval evidence")
        return
    if deliver.review_policy not in {ReviewPolicy.MANUAL, ReviewPolicy.GROUPED}:
        raise ValueError("delivery review policy cannot publish a work")
    if deliver.review_state is not ReviewState.APPROVED:
        raise ValueError("delivery is not durably approved")
    validation = validate_stage_review(project.canonical_path, StageName.DELIVER)
    if not validation.valid or validation.review is None:
        raise ValueError("delivery review evidence is invalid")
    if validation.review.revision != deliver.revision:
        raise ValueError("delivery review applies to a stale revision")


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
        masters = tuple(raw_masters)
    else:
        raise ValueError("delivery manifest schema is unsupported")
    package_outputs = {
        project.relative_path(raw_path).as_posix() for raw_path in package.final_outputs
    }
    stage_artifacts = {
        project.relative_path(raw_path).as_posix() for raw_path in deliver.artifacts
    }
    outputs: list[CatalogArtifact] = []
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
        outputs.append(
            CatalogArtifact(
                artifact_id=_opaque("art", "delivery", evidence.sha256, key),
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
    revision_evidence = _delivery_revision(project, deliver)
    _delivery_review_is_current(project, deliver)
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
    eval_reports = tuple(
        _artifact_from_project(
            project, raw_path, allowed_prefix="stages/eval", kind="eval"
        )
        for raw_path in package.eval_reports
    )
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
    return all(_stat_identity(path) == identity for path, identity in dependencies)


def _project_dependencies(project: Path, work: WorkRecord) -> tuple[Path, ...]:
    values = {
        project / "project.json",
        project / "production_package.json",
        project / "reviews/deliver.revisions.json",
        project / "reviews/deliver.review.json",
        project / "stages/deliver/delivery_manifest.json",
    }
    for artifact in WorkCatalog((work,)).artifacts():
        values.add(artifact.path)
    return tuple(sorted(values, key=str))


class WorkCatalogCache:
    """Bounded, thread-safe cache keyed by authoritative file identities."""

    def __init__(self, *, max_entries: int = 256):
        if max_entries < 1:
            raise ValueError("catalog cache size must be positive")
        self.max_entries = max_entries
        self._projects: OrderedDict[str, _CachedProject] = OrderedDict()
        self._archive: _CachedArchive | None = None
        self._archive_source = ""
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
            work = _delivered_work(project)
            paths = _project_dependencies(project.canonical_path, work)
            dependencies = tuple((path, _stat_identity(path)) for path in paths)
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
        source = (
            str(archive_manifest) if isinstance(archive_manifest, (str, Path)) else ""
        )
        if (
            source
            and source == self._archive_source
            and self._archive is not None
            and _dependencies_current(self._archive.dependencies)
        ):
            return list(self._archive.works), list(self._archive.warnings)
        manifest = _load_archive_manifest(archive_manifest)
        if manifest is None:
            return [], []
        works, warnings = _archive_works(manifest)
        if source and manifest.archive_root is not None:
            paths = [Path(source).expanduser().absolute()]
            paths.extend(
                manifest.archive_root / entry.archive_relative
                for entry in manifest.entries
            )
            self._archive = _CachedArchive(
                tuple(works),
                tuple(warnings),
                tuple((path, _stat_identity(path)) for path in paths),
            )
            self._archive_source = source
        return works, warnings

    def build(
        self,
        runs_dir: str | Path,
        archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
    ) -> WorkCatalog:
        with self._lock:
            delivered: list[WorkRecord] = []
            warnings: list[str] = []
            runs = Path(runs_dir).expanduser()
            active_keys: set[str] = set()
            if runs.exists():
                try:
                    with AnchoredDirectory.open(
                        runs, label="Work catalog runs"
                    ) as root:
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
