from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping

from .pipeline_contracts import (
    ProductionPackage,
    ProjectSpec,
    ReviewState,
    StageName,
    StageState,
)
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
    payload = anchor.read_bytes(relative)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("catalog artifact hash changed")
    name = relative.name
    media_type = _mime(name)
    return CatalogArtifact(
        artifact_id=_opaque("art", "delivery", digest, relative.as_posix()),
        name=name,
        media_type=media_type,
        kind=kind or _media_kind(name, media_type),
        size_bytes=len(payload),
        sha256=digest,
        path=anchor.canonical_path / relative,
    )


def _delivered_work(project: AnchoredDirectory) -> WorkRecord:
    spec = ProjectSpec.from_dict(_read_json(project, "project.json"))
    package = ProductionPackage.from_dict(
        _read_json(project, "production_package.json")
    )
    if package.project_id != spec.project_id or package.mode != spec.mode:
        raise ValueError("project catalog records disagree")
    deliver = next(
        record for record in package.stages if record.stage is StageName.DELIVER
    )
    if (
        deliver.state is not StageState.PASSED
        or deliver.review_state
        not in {
            ReviewState.APPROVED,
            ReviewState.AUTO_APPROVED,
            ReviewState.SKIPPED,
        }
        or deliver.review_blocks_progress
    ):
        raise ValueError("delivery is not approved")
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
    if (
        manifest.get("schema_version") != "motion-comic-factory.delivery.v1"
        or manifest.get("project_id") != spec.project_id
        or not _HASH.fullmatch(str(manifest.get("sha256") or ""))
    ):
        raise ValueError("delivery manifest is invalid")
    master_raw = str(manifest.get("master") or "")
    if master_raw not in deliver.artifacts or master_raw not in package.final_outputs:
        raise ValueError("delivery master is not registered")
    master = _artifact_from_project(
        project,
        master_raw,
        allowed_prefix="stages/deliver",
        expected_sha256=str(manifest["sha256"]),
        kind="video",
    )
    eval_reports: list[CatalogArtifact] = []
    for raw_path in package.eval_reports:
        eval_reports.append(
            _artifact_from_project(
                project,
                raw_path,
                allowed_prefix="stages/eval",
                kind="eval",
            )
        )
    revision = int(deliver.revision or 1)
    label = str(manifest.get("version") or f"V{revision}").strip()[:40]
    delivered_at = _trusted_time(manifest.get("delivered_at"))
    version = WorkVersion(
        version_id=_opaque("version", spec.project_id, master.sha256, str(revision)),
        label=label,
        created_at=delivered_at,
        outputs=(master,),
        eval_reports=tuple(eval_reports),
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
                    payload = archive_anchor.read_bytes(entry.archive_relative)
                    if (
                        len(payload) != entry.size_bytes
                        or hashlib.sha256(payload).hexdigest() != entry.sha256
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
            works.append(
                WorkRecord(
                    work_id=_opaque("work", "archive", collection_id),
                    project_id="",
                    title="历史归档" if historical else entries[0].title,
                    mode="historical",
                    source="historical",
                    delivered_at=max(
                        (item.created_at for item in versions), default=""
                    ),
                    current_version=f"{len(versions)} 项素材",
                    versions=tuple(versions),
                )
            )
    return works, warnings


def build_work_catalog(
    runs_dir: str | Path,
    archive_manifest: str | Path | ArchiveManifest | Mapping[str, Any] | None,
) -> WorkCatalog:
    delivered: list[WorkRecord] = []
    warnings: list[str] = []
    runs = Path(runs_dir).expanduser()
    if runs.exists():
        try:
            with AnchoredDirectory.open(runs, label="Work catalog runs") as root:
                for name in sorted(root.listdir()):
                    if name.startswith(".") or not _SAFE_TOKEN.fullmatch(name):
                        continue
                    try:
                        project = root.child(name, label="Delivered project")
                    except (FileNotFoundError, OSError, ValueError):
                        continue
                    try:
                        delivered.append(_delivered_work(project))
                    except (
                        FileNotFoundError,
                        KeyError,
                        OSError,
                        StopIteration,
                        TypeError,
                        ValueError,
                    ):
                        warnings.append(
                            f"invalid_delivery:{_opaque('record', name, length=16)}"
                        )
                    finally:
                        project.close()
        except ValueError:
            warnings.append("runs_catalog_unavailable")
    archive_works: list[WorkRecord] = []
    try:
        manifest = _load_archive_manifest(archive_manifest)
        if manifest is not None:
            archive_works, archive_warnings = _archive_works(manifest)
            warnings.extend(archive_warnings)
    except (OSError, TypeError, ValueError):
        warnings.append("archive_manifest_invalid")
    delivered.sort(
        key=lambda item: (item.delivered_at, item.title, item.work_id), reverse=True
    )
    works = tuple(delivered + archive_works)
    if len({work.work_id for work in works}) != len(works):
        return WorkCatalog(
            works=(), warnings=tuple(sorted(set((*warnings, "duplicate_work_id"))))
        )
    return WorkCatalog(works=works, warnings=tuple(sorted(set(warnings))))
