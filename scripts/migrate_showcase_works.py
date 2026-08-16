#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from factory.work_catalog import ArchiveEntry, ArchiveManifest  # noqa: E402
from factory.secure_posix import AnchoredDirectory  # noqa: E402


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_TRANSACTION_NAMESPACE = ".storymotion-migration-transactions"
_TRANSACTION_SCHEMA = "storymotion.archive-publish.v2"
_OWNER_SCHEMA = "storymotion.archive-transaction-owner.v1"
_TRANSACTION_ID = re.compile(r"tx-[0-9a-f]{32}")
_OWNER_TEMP = re.compile(r"\.owner\.[0-9a-f]{32}\.tmp")
_JOURNAL_TEMP = re.compile(r"\.journal\.[0-9a-f]{32}\.tmp")
_APPROVED_AUDIO: dict[str, dict[str, str]] = {
    "audio/black-cat-approved.m4a": {
        "version_label": "黑白猫定版音色",
        "role": "黑白猫",
        "personality": "高冷御姐",
        "voice_name": "魅力女友",
        "speed": "+4",
        "description": "自然偏低、冷静克制、短停顿。",
    },
    "audio/orange-cat-approved.m4a": {
        "version_label": "橘猫定版音色",
        "role": "橘猫",
        "personality": "可爱活泼",
        "voice_name": "调皮公主",
        "speed": "+2",
        "description": "声线轻亮、反应灵动、有自然笑意。",
    },
    "audio/two-cat-approved-dialogue.m4a": {
        "version_label": "双猫对话试听",
        "role": "双猫",
        "description": "黑白猫与橘猫的定版对话试听。",
    },
}


@dataclass(frozen=True)
class _Snapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    link_count: int
    sha256: str


@dataclass(frozen=True)
class _PlannedFile:
    entry: ArchiveEntry
    snapshot: _Snapshot


@dataclass
class _DestinationEvidence:
    parent: int
    name: str
    descriptor: int
    metadata: os.stat_result
    sha256: str

    def verify(self) -> None:
        current_descriptor = os.fstat(self.descriptor)
        current_path = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
        expected = (
            self.metadata.st_dev,
            self.metadata.st_ino,
            self.metadata.st_size,
            self.metadata.st_mtime_ns,
            self.metadata.st_ctime_ns,
        )
        for current in (current_descriptor, current_path):
            if (
                (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                )
                != expected
                or current.st_nlink != 1
                or not stat.S_ISREG(current.st_mode)
            ):
                raise ValueError("archived file ownership verification failed")

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


@dataclass
class _PublishTransaction:
    namespace: int
    descriptor: int
    transaction_id: str
    destination: str
    expected_sha256: str
    owner_sha256: str = ""
    payload_descriptor: int = -1
    payload_metadata: os.stat_result | None = None

    def close(self) -> None:
        if self.payload_descriptor >= 0:
            os.close(self.payload_descriptor)
            self.payload_descriptor = -1
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        if self.namespace >= 0:
            os.close(self.namespace)
            self.namespace = -1


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _snapshot(metadata: os.stat_result, digest: str) -> _Snapshot:
    return _Snapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IFMT(metadata.st_mode),
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
        link_count=metadata.st_nlink,
        sha256=digest,
    )


def _snapshot_matches(value: _Snapshot, metadata: os.stat_result) -> bool:
    return (
        value.device == metadata.st_dev
        and value.inode == metadata.st_ino
        and value.mode == stat.S_IFMT(metadata.st_mode)
        and value.size == metadata.st_size
        and value.modified_ns == metadata.st_mtime_ns
        and value.changed_ns == metadata.st_ctime_ns
        and value.link_count == metadata.st_nlink
        and stat.S_ISREG(metadata.st_mode)
    )


def _open_parent(anchor: AnchoredDirectory, relative: str) -> tuple[int, str]:
    path = PurePosixPath(relative)
    if len(path.parts) == 1:
        return os.dup(anchor.descriptor), path.name
    return anchor.open_directory(Path(*path.parts[:-1])), path.name


def _hash_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _read_snapshot(anchor: AnchoredDirectory, relative: str) -> _Snapshot:
    parent, name = _open_parent(anchor, relative)
    descriptor = -1
    try:
        listed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(listed.st_mode):
            raise ValueError("showcase source contains a symlink")
        if listed.st_nlink != 1:
            raise ValueError("showcase source contains a hard link")
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_identity(listed, opened):
            raise ValueError("showcase source identity changed")
        digest = _hash_descriptor(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not _same_identity(opened, after) or not _same_identity(opened, current):
            raise ValueError("showcase source changed during migration planning")
        result = _snapshot(after, digest)
        if not _snapshot_matches(result, opened):
            raise ValueError("showcase source changed during migration planning")
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _walk_regular_files(anchor: AnchoredDirectory) -> tuple[str, ...]:
    found: list[str] = []

    def visit(descriptor: int, prefix: tuple[str, ...]) -> None:
        for name in sorted(os.listdir(descriptor)):
            if name in {"", ".", ".."} or "/" in name:
                raise ValueError("showcase source contains an unsafe name")
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            relative = (*prefix, name)
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("showcase source contains a symlink")
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    opened = os.fstat(child)
                    if not _same_identity(metadata, opened):
                        raise ValueError(
                            "showcase directory changed during migration planning"
                        )
                    visit(child, relative)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("showcase source contains an unsupported file type")
            found.append(PurePosixPath(*relative).as_posix())

    root = os.dup(anchor.descriptor)
    try:
        visit(root, ())
    finally:
        os.close(root)
    return tuple(found)


def _media_type(relative: str) -> str:
    suffix = PurePosixPath(relative).suffix.lower()
    return {
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }.get(suffix, "application/octet-stream")


def _media_kind(media_type: str) -> str:
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("image/"):
        return "image"
    if media_type == "application/json":
        return "text"
    return "file"


def _classify_source(relative: str, snapshot: _Snapshot) -> ArchiveEntry:
    approved = _APPROVED_AUDIO.get(relative)
    if approved is not None:
        classification = "linked"
        collection_id = "approved-cat-voices"
        title = "双猫定版音色"
        destination = PurePosixPath(
            "linked", collection_id, PurePosixPath(relative).name
        )
        version_label = approved["version_label"]
        metadata = {
            key: value for key, value in approved.items() if key != "version_label"
        }
    else:
        classification = "unclassified"
        collection_id = "historical-unclassified"
        title = "历史归档"
        destination = PurePosixPath("unclassified", relative)
        version_label = PurePosixPath(relative).name
        metadata = {"description": "旧展示站未归类素材，原样保留。"}
    media_type = _media_type(relative)
    rights = {
        "origin": "legacy_storymotion_showcase",
        "creator": "unverified",
        "license": "unverified",
        "commercial_use": "unverified",
        "redistribution_status": "unverified",
        "distribution_warning": (
            "Rights documentation is unavailable; do not redistribute publicly until cleared."
        ),
    }
    return ArchiveEntry(
        entry_id=f"archive_{hashlib.sha256((relative + chr(0) + snapshot.sha256).encode('utf-8')).hexdigest()[:32]}",
        source_relative=relative,
        archive_relative=destination.as_posix(),
        classification=classification,
        collection_id=collection_id,
        title=title,
        version_label=version_label,
        media_type=media_type,
        media_kind=_media_kind(media_type),
        size_bytes=snapshot.size,
        sha256=snapshot.sha256,
        metadata=metadata,
        rights=rights,
    )


def _build_plan(source: AnchoredDirectory) -> tuple[_PlannedFile, ...]:
    plan: list[_PlannedFile] = []
    for relative in _walk_regular_files(source):
        snapshot = _read_snapshot(source, relative)
        plan.append(_PlannedFile(_classify_source(relative, snapshot), snapshot))
    destinations = [item.entry.archive_relative for item in plan]
    if len(set(destinations)) != len(destinations):
        raise ValueError("migration plan contains a duplicate destination collision")
    return tuple(plan)


def _mkdir_secure(path: Path) -> None:
    absolute = path.expanduser().absolute()
    if not absolute.is_absolute():
        raise ValueError("archive destination must be absolute")
    descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            try:
                metadata = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ValueError("archive destination contains a symlink")
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError("archive destination ancestor is not a directory")
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    raced = os.stat(
                        component,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if stat.S_ISLNK(raced.st_mode) or not stat.S_ISDIR(raced.st_mode):
                        raise ValueError(
                            "archive destination creation raced with an unsafe node"
                        )
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    finally:
        os.close(descriptor)


def _existing_digest(parent: int, name: str) -> _DestinationEvidence | None:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("archive destination collision is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("archive destination collision is not a regular file")
        if metadata.st_nlink != 1:
            raise ValueError("archive destination contains a hard link")
        digest = _hash_descriptor(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not _same_identity(metadata, after) or not _same_identity(metadata, current):
            raise ValueError("archive destination identity changed while hashing")
        evidence = _DestinationEvidence(parent, name, descriptor, after, digest)
        evidence.verify()
        descriptor = -1
        return evidence
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _private_directory(descriptor: int) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise ValueError("archive transaction namespace is not private")
    return metadata


def _transaction_namespace(destination: AnchoredDirectory) -> int:
    descriptor = destination.open_directory(_TRANSACTION_NAMESPACE, create=True)
    try:
        _private_directory(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _journal_payload(
    transaction: _PublishTransaction,
    *,
    phase: str,
    payload_metadata: os.stat_result | None = None,
    payload_size: int | None = None,
    payload_sha256: str = "",
) -> dict[str, Any]:
    namespace = _private_directory(transaction.namespace)
    owned = _private_directory(transaction.descriptor)
    payload: dict[str, Any] = {
        "schema_version": _TRANSACTION_SCHEMA,
        "transaction_id": transaction.transaction_id,
        "destination": transaction.destination,
        "expected_sha256": transaction.expected_sha256,
        "owner_sha256": transaction.owner_sha256,
        "phase": phase,
        "owner_uid": os.geteuid(),
        "namespace": {"device": namespace.st_dev, "inode": namespace.st_ino},
        "transaction": {"device": owned.st_dev, "inode": owned.st_ino},
    }
    if payload_metadata is not None:
        payload["payload"] = {
            "name": "payload.tmp",
            "device": payload_metadata.st_dev,
            "inode": payload_metadata.st_ino,
            "mode": stat.S_IMODE(payload_metadata.st_mode),
            "size": payload_size
            if payload_size is not None
            else payload_metadata.st_size,
            "sha256": payload_sha256,
        }
    return payload


def _owner_payload(transaction: _PublishTransaction) -> dict[str, Any]:
    namespace = _private_directory(transaction.namespace)
    owned = _private_directory(transaction.descriptor)
    return {
        "schema_version": _OWNER_SCHEMA,
        "transaction_id": transaction.transaction_id,
        "destination": transaction.destination,
        "expected_sha256": transaction.expected_sha256,
        "owner_uid": os.geteuid(),
        "namespace": {"device": namespace.st_dev, "inode": namespace.st_ino},
        "transaction": {"device": owned.st_dev, "inode": owned.st_ino},
    }


def _write_owner_marker(transaction: _PublishTransaction) -> str:
    payload = _owner_payload(transaction)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    temporary_name = f".owner.{uuid4().hex}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=transaction.descriptor,
    )
    published = False
    try:
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("archive transaction owner marker could not be completed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary_name,
            "owner.json",
            src_dir_fd=transaction.descriptor,
            dst_dir_fd=transaction.descriptor,
        )
        published = True
        os.fsync(transaction.descriptor)
        return hashlib.sha256(encoded).hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary_name, dir_fd=transaction.descriptor)
            except FileNotFoundError:
                pass


def _write_journal(
    transaction: _PublishTransaction, payload: Mapping[str, Any]
) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary_name = f".journal.{uuid4().hex}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=transaction.descriptor,
    )
    published = False
    try:
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("archive publication journal could not be completed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary_name,
            "journal.json",
            src_dir_fd=transaction.descriptor,
            dst_dir_fd=transaction.descriptor,
        )
        published = True
        os.fsync(transaction.descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary_name, dir_fd=transaction.descriptor)
            except FileNotFoundError:
                pass


def _begin_transaction(
    destination: AnchoredDirectory,
    relative: str,
    expected_sha256: str,
) -> _PublishTransaction:
    namespace = _transaction_namespace(destination)
    transaction_id = f"tx-{uuid4().hex}"
    created = False
    try:
        os.mkdir(transaction_id, mode=0o700, dir_fd=namespace)
        created = True
        descriptor = os.open(transaction_id, _DIRECTORY_FLAGS, dir_fd=namespace)
    except Exception:
        if created:
            os.rmdir(transaction_id, dir_fd=namespace)
            os.fsync(namespace)
        os.close(namespace)
        raise
    except BaseException:
        os.close(namespace)
        raise
    transaction = _PublishTransaction(
        namespace,
        descriptor,
        transaction_id,
        PurePosixPath(relative).as_posix(),
        expected_sha256,
    )
    try:
        _private_directory(descriptor)
        transaction.owner_sha256 = _write_owner_marker(transaction)
        _write_journal(transaction, _journal_payload(transaction, phase="initialized"))
        return transaction
    except Exception:
        _discard_new_transaction(transaction)
        transaction.close()
        raise
    except BaseException:
        transaction.close()
        raise


def _unlink_owned_private_file(descriptor: int, name: str) -> None:
    metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise ValueError("archive transaction contains an unsafe private file")
    os.unlink(name, dir_fd=descriptor)


def _discard_new_transaction(transaction: _PublishTransaction) -> None:
    names = set(os.listdir(transaction.descriptor))
    allowed = {
        name
        for name in names
        if name in {"owner.json", "journal.json"}
        or _OWNER_TEMP.fullmatch(name)
        or _JOURNAL_TEMP.fullmatch(name)
    }
    if names != allowed:
        raise ValueError("archive transaction contains unowned residue")
    for name in sorted(names):
        _unlink_owned_private_file(transaction.descriptor, name)
    os.fsync(transaction.descriptor)
    os.rmdir(transaction.transaction_id, dir_fd=transaction.namespace)
    os.fsync(transaction.namespace)


def _create_payload(transaction: _PublishTransaction) -> int:
    descriptor = os.open(
        "payload.tmp",
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=transaction.descriptor,
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size != 0
    ):
        os.close(descriptor)
        raise ValueError("archive transaction payload is unsafe")
    transaction.payload_descriptor = descriptor
    transaction.payload_metadata = metadata
    _write_journal(
        transaction,
        _journal_payload(
            transaction,
            phase="payload_created",
            payload_metadata=metadata,
            payload_size=0,
        ),
    )
    return descriptor


def _payload_ready(transaction: _PublishTransaction, *, size: int, digest: str) -> None:
    if transaction.payload_metadata is None:
        raise ValueError("archive transaction payload identity is missing")
    current = os.fstat(transaction.payload_descriptor)
    if (
        (current.st_dev, current.st_ino)
        != (transaction.payload_metadata.st_dev, transaction.payload_metadata.st_ino)
        or current.st_size != size
        or digest != transaction.expected_sha256
    ):
        raise ValueError("archive transaction payload evidence is invalid")
    _write_journal(
        transaction,
        _journal_payload(
            transaction,
            phase="payload_ready",
            payload_metadata=transaction.payload_metadata,
            payload_size=size,
            payload_sha256=digest,
        ),
    )


def _read_private_json(descriptor: int, name: str) -> Mapping[str, Any] | None:
    try:
        journal = os.open(name, _FILE_FLAGS, dir_fd=descriptor)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(journal)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_size > 8192
        ):
            return None
        content = b""
        while chunk := os.read(journal, 8192):
            content += chunk
        value = json.loads(content.decode("utf-8"))
        return value if isinstance(value, Mapping) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(journal)


def _read_transaction_journal(descriptor: int) -> Mapping[str, Any] | None:
    return _read_private_json(descriptor, "journal.json")


def _read_owner_marker(descriptor: int) -> Mapping[str, Any] | None:
    return _read_private_json(descriptor, "owner.json")


def _valid_transaction_relative(value: Any) -> str | None:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return path.as_posix()


def _owner_owned(
    transaction_id: str,
    namespace: int,
    descriptor: int,
    owner: Mapping[str, Any],
) -> bool:
    namespace_stat = _private_directory(namespace)
    transaction_stat = _private_directory(descriptor)
    return (
        set(owner)
        == {
            "schema_version",
            "transaction_id",
            "destination",
            "expected_sha256",
            "owner_uid",
            "namespace",
            "transaction",
        }
        and owner.get("schema_version") == _OWNER_SCHEMA
        and owner.get("transaction_id") == transaction_id
        and owner.get("owner_uid") == os.geteuid()
        and _valid_transaction_relative(owner.get("destination")) is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(owner.get("expected_sha256") or ""))
        is not None
        and owner.get("namespace")
        == {"device": namespace_stat.st_dev, "inode": namespace_stat.st_ino}
        and owner.get("transaction")
        == {"device": transaction_stat.st_dev, "inode": transaction_stat.st_ino}
    )


def _journal_owned(
    transaction_id: str,
    namespace: int,
    descriptor: int,
    journal: Mapping[str, Any],
    owner_sha256: str,
) -> bool:
    namespace_stat = _private_directory(namespace)
    transaction_stat = _private_directory(descriptor)
    return (
        journal.get("schema_version") == _TRANSACTION_SCHEMA
        and journal.get("transaction_id") == transaction_id
        and journal.get("owner_sha256") == owner_sha256
        and journal.get("owner_uid") == os.geteuid()
        and journal.get("namespace")
        == {"device": namespace_stat.st_dev, "inode": namespace_stat.st_ino}
        and journal.get("transaction")
        == {"device": transaction_stat.st_dev, "inode": transaction_stat.st_ino}
    )


def _finish_transaction(transaction: _PublishTransaction) -> None:
    names = set(os.listdir(transaction.descriptor))
    if names != {"owner.json", "journal.json"}:
        raise ValueError("archive transaction contains unowned residue")
    _unlink_owned_private_file(transaction.descriptor, "journal.json")
    _unlink_owned_private_file(transaction.descriptor, "owner.json")
    os.fsync(transaction.descriptor)
    os.rmdir(transaction.transaction_id, dir_fd=transaction.namespace)
    os.fsync(transaction.namespace)


def _finish_empty_transaction(transaction: _PublishTransaction) -> None:
    names = set(os.listdir(transaction.descriptor))
    temporary = {
        name for name in names if _JOURNAL_TEMP.fullmatch(name) is not None
    }
    if names != {"owner.json", *temporary}:
        raise ValueError("archive transaction contains unowned residue")
    for name in sorted(temporary):
        _unlink_owned_private_file(transaction.descriptor, name)
    _unlink_owned_private_file(transaction.descriptor, "owner.json")
    os.fsync(transaction.descriptor)
    os.rmdir(transaction.transaction_id, dir_fd=transaction.namespace)
    os.fsync(transaction.namespace)


def _abort_transaction(transaction: _PublishTransaction) -> None:
    if transaction.payload_descriptor >= 0:
        os.close(transaction.payload_descriptor)
        transaction.payload_descriptor = -1
    try:
        payload = os.stat(
            "payload.tmp", dir_fd=transaction.descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        payload = None
    if payload is not None and transaction.payload_metadata is not None:
        if (payload.st_dev, payload.st_ino) != (
            transaction.payload_metadata.st_dev,
            transaction.payload_metadata.st_ino,
        ):
            raise ValueError("archive transaction payload ownership changed")
        os.unlink("payload.tmp", dir_fd=transaction.descriptor)
    _finish_transaction(transaction)


def _publish_no_replace(
    source_parent: int,
    destination_parent: int,
    temporary_name: str,
    destination_name: str,
) -> None:
    try:
        os.link(
            temporary_name,
            destination_name,
            src_dir_fd=source_parent,
            dst_dir_fd=destination_parent,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise ValueError(
            "archive destination collision appeared during migration"
        ) from exc
    temporary = os.stat(temporary_name, dir_fd=source_parent, follow_symlinks=False)
    published = os.stat(
        destination_name, dir_fd=destination_parent, follow_symlinks=False
    )
    if (
        not stat.S_ISREG(published.st_mode)
        or (temporary.st_dev, temporary.st_ino) != (published.st_dev, published.st_ino)
        or published.st_nlink != 2
    ):
        try:
            os.unlink(destination_name, dir_fd=destination_parent)
        except FileNotFoundError:
            pass
        raise ValueError("archive publication ownership could not be verified")
    os.unlink(temporary_name, dir_fd=source_parent)
    final = os.stat(destination_name, dir_fd=destination_parent, follow_symlinks=False)
    if (final.st_dev, final.st_ino) != (
        published.st_dev,
        published.st_ino,
    ) or final.st_nlink != 1:
        raise ValueError("archive publication retained an unsafe hard link")
    os.fsync(source_parent)
    os.fsync(destination_parent)


def _recover_transaction(
    transaction: _PublishTransaction,
    destination_parent: int,
    destination_name: str,
    journal: Mapping[str, Any],
) -> None:
    raw_payload = journal.get("payload")
    if not isinstance(raw_payload, Mapping):
        try:
            unbound = os.stat(
                "payload.tmp",
                dir_fd=transaction.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            _finish_transaction(transaction)
            return
        if (
            journal.get("phase") != "initialized"
            or not stat.S_ISREG(unbound.st_mode)
            or stat.S_IMODE(unbound.st_mode) != 0o600
            or unbound.st_uid != os.geteuid()
            or unbound.st_nlink != 1
            or unbound.st_size != 0
        ):
            raise ValueError(
                "archive transaction has payload without ownership evidence"
            )
        os.unlink("payload.tmp", dir_fd=transaction.descriptor)
        _finish_transaction(transaction)
        return
    if raw_payload.get("name") != "payload.tmp" or raw_payload.get("mode") != 0o600:
        raise ValueError("archive transaction payload evidence is invalid")
    try:
        payload = os.stat(
            "payload.tmp", dir_fd=transaction.descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        payload = None
    try:
        final = os.stat(
            destination_name, dir_fd=destination_parent, follow_symlinks=False
        )
    except FileNotFoundError:
        final = None
    expected_identity = (raw_payload.get("device"), raw_payload.get("inode"))
    if payload is not None and (
        not stat.S_ISREG(payload.st_mode)
        or stat.S_IMODE(payload.st_mode) != 0o600
        or payload.st_uid != os.geteuid()
        or (payload.st_dev, payload.st_ino) != expected_identity
    ):
        raise ValueError("archive transaction payload ownership is invalid")
    if final is not None and (
        not stat.S_ISREG(final.st_mode)
        or (final.st_dev, final.st_ino) != expected_identity
    ):
        raise ValueError("archive transaction destination ownership is invalid")
    if payload is not None and final is not None:
        if payload.st_nlink != 2 or final.st_nlink != 2:
            raise ValueError("archive transaction link ownership is invalid")
        os.unlink("payload.tmp", dir_fd=transaction.descriptor)
    elif payload is not None:
        descriptor = os.open("payload.tmp", _FILE_FLAGS, dir_fd=transaction.descriptor)
        try:
            digest = _hash_descriptor(descriptor)
        finally:
            os.close(descriptor)
        if (
            journal.get("phase") != "payload_ready"
            or raw_payload.get("sha256") != transaction.expected_sha256
            or raw_payload.get("size") != payload.st_size
            or digest != transaction.expected_sha256
        ):
            os.unlink("payload.tmp", dir_fd=transaction.descriptor)
            _finish_transaction(transaction)
            return
        _publish_no_replace(
            transaction.descriptor,
            destination_parent,
            "payload.tmp",
            destination_name,
        )
    elif final is None:
        _finish_transaction(transaction)
        return
    evidence = _existing_digest(destination_parent, destination_name)
    try:
        if evidence is None or evidence.sha256 != transaction.expected_sha256:
            raise ValueError("archive transaction recovery digest is invalid")
    finally:
        if evidence is not None:
            evidence.close()
    _finish_transaction(transaction)


def _recover_publication(
    destination: AnchoredDirectory,
    _relative: str,
    _expected: str,
) -> None:
    namespace = _transaction_namespace(destination)
    try:
        for transaction_id in sorted(os.listdir(namespace)):
            if not _TRANSACTION_ID.fullmatch(transaction_id):
                continue
            try:
                descriptor = os.open(transaction_id, _DIRECTORY_FLAGS, dir_fd=namespace)
            except OSError:
                continue
            owner = _read_owner_marker(descriptor)
            if owner is None or not _owner_owned(
                transaction_id,
                namespace,
                descriptor,
                owner,
            ):
                os.close(descriptor)
                continue
            relative = _valid_transaction_relative(owner.get("destination"))
            expected = str(owner.get("expected_sha256") or "")
            if relative is None:
                os.close(descriptor)
                continue
            owner_sha256 = hashlib.sha256(
                json.dumps(
                    dict(owner), sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            transaction = _PublishTransaction(
                os.dup(namespace),
                descriptor,
                transaction_id,
                relative,
                expected,
                owner_sha256,
            )
            try:
                journal = _read_transaction_journal(descriptor)
                if journal is None:
                    if "journal.json" in os.listdir(descriptor):
                        continue
                    _finish_empty_transaction(transaction)
                    continue
                if not _journal_owned(
                    transaction_id,
                    namespace,
                    descriptor,
                    journal,
                    owner_sha256,
                ) or journal.get("destination") != transaction.destination or journal.get(
                    "expected_sha256"
                ) != expected:
                    continue
                destination_parent, destination_name = _open_parent(
                    destination, relative
                )
                try:
                    _recover_transaction(
                        transaction,
                        destination_parent,
                        destination_name,
                        journal,
                    )
                finally:
                    os.close(destination_parent)
            finally:
                transaction.close()
    finally:
        os.close(namespace)


def _write_bytes_no_replace(
    destination: AnchoredDirectory,
    relative: str,
    content: bytes,
) -> None:
    parent, name = _open_parent(destination, relative)
    transaction: _PublishTransaction | None = None
    try:
        expected = hashlib.sha256(content).hexdigest()
        os.close(parent)
        parent = -1
        _recover_publication(destination, relative, expected)
        parent, name = _open_parent(destination, relative)
        existing = _existing_digest(parent, name)
        if existing is not None:
            try:
                if existing.sha256 != expected:
                    raise ValueError("archive manifest collision has different content")
                return
            finally:
                existing.close()
        transaction = _begin_transaction(destination, relative, expected)
        descriptor = _create_payload(transaction)
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("archive manifest could not be completed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        _payload_ready(transaction, size=len(content), digest=expected)
        _publish_no_replace(
            transaction.descriptor,
            parent,
            "payload.tmp",
            name,
        )
        os.close(transaction.payload_descriptor)
        transaction.payload_descriptor = -1
        _finish_transaction(transaction)
    except Exception:
        if transaction is not None:
            _abort_transaction(transaction)
        raise
    finally:
        if transaction is not None:
            transaction.close()
        if parent >= 0:
            os.close(parent)


def _copy_file_atomic(
    source: AnchoredDirectory,
    destination: AnchoredDirectory,
    planned: _PlannedFile,
) -> None:
    source_parent, source_name = _open_parent(source, planned.entry.source_relative)
    destination_path = PurePosixPath(planned.entry.archive_relative)
    destination_parent = destination.open_directory(
        Path(*destination_path.parts[:-1]), create=True
    )
    source_descriptor = -1
    transaction: _PublishTransaction | None = None
    try:
        os.close(destination_parent)
        destination_parent = -1
        _recover_publication(
            destination,
            destination_path.as_posix(),
            planned.entry.sha256,
        )
        destination_parent = destination.open_directory(
            Path(*destination_path.parts[:-1]), create=True
        )
        existing = _existing_digest(destination_parent, destination_path.name)
        if existing is not None:
            try:
                if existing.sha256 != planned.entry.sha256:
                    raise ValueError(
                        "archive destination collision has different content"
                    )
                current = os.stat(
                    source_name, dir_fd=source_parent, follow_symlinks=False
                )
                if not _snapshot_matches(planned.snapshot, current):
                    raise ValueError("showcase source changed during migration")
                return
            finally:
                existing.close()
        source_descriptor = os.open(source_name, _FILE_FLAGS, dir_fd=source_parent)
        opened = os.fstat(source_descriptor)
        if not _snapshot_matches(planned.snapshot, opened):
            raise ValueError("showcase source changed during migration")
        transaction = _begin_transaction(
            destination, destination_path.as_posix(), planned.entry.sha256
        )
        temporary_descriptor = _create_payload(transaction)
        digest = hashlib.sha256()
        copied = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            copied += len(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(temporary_descriptor, remaining)
                if written <= 0:
                    raise OSError("archive copy could not be completed")
                remaining = remaining[written:]
        os.fsync(temporary_descriptor)
        after = os.fstat(source_descriptor)
        current = os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
        if (
            digest.hexdigest() != planned.entry.sha256
            or not _snapshot_matches(planned.snapshot, after)
            or not _snapshot_matches(planned.snapshot, current)
        ):
            raise ValueError("showcase source changed during migration")
        _payload_ready(transaction, size=copied, digest=digest.hexdigest())
        _publish_no_replace(
            transaction.descriptor,
            destination_parent,
            "payload.tmp",
            destination_path.name,
        )
        os.close(transaction.payload_descriptor)
        transaction.payload_descriptor = -1
        _finish_transaction(transaction)
    except Exception:
        if transaction is not None:
            _abort_transaction(transaction)
        raise
    finally:
        if transaction is not None:
            transaction.close()
        if source_descriptor >= 0:
            os.close(source_descriptor)
        os.close(source_parent)
        if destination_parent >= 0:
            os.close(destination_parent)


def _validate_roots(source: Path, archive: Path) -> None:
    if source.is_symlink():
        raise ValueError("showcase source cannot be a symlink")
    if not source.is_dir():
        raise FileNotFoundError("showcase source directory is missing")
    if archive.is_symlink():
        raise ValueError("archive destination cannot be a symlink")
    source_absolute = source.absolute()
    archive_absolute = archive.absolute()
    if archive_absolute == source_absolute or archive_absolute.is_relative_to(
        source_absolute
    ):
        raise ValueError("archive destination cannot be inside the showcase source")
    if source_absolute.is_relative_to(archive_absolute):
        raise ValueError("showcase source cannot be inside the archive destination")


def migrate_showcase_media(
    source_public: str | Path,
    archive_root: str | Path,
    *,
    dry_run: bool = False,
) -> ArchiveManifest:
    source_path = Path(source_public).expanduser()
    archive_path = Path(archive_root).expanduser()
    _validate_roots(source_path, archive_path)
    with AnchoredDirectory.open(source_path, label="Showcase source") as source:
        plan = _build_plan(source)
        manifest = ArchiveManifest(
            entries=tuple(item.entry for item in plan),
            archive_root=archive_path.absolute(),
        )
        if dry_run:
            return manifest
        _mkdir_secure(archive_path)
        with AnchoredDirectory.open(archive_path, label="Showcase archive") as archive:
            fcntl.flock(archive.descriptor, fcntl.LOCK_EX)
            try:
                verified_destinations: list[_DestinationEvidence] = []
                for item in plan:
                    _copy_file_atomic(source, archive, item)
                for item in plan:
                    current = _read_snapshot(source, item.entry.source_relative)
                    if current != item.snapshot:
                        raise ValueError("showcase source changed during migration")
                    parent, name = _open_parent(archive, item.entry.archive_relative)
                    try:
                        evidence = _existing_digest(parent, name)
                        if evidence is None or evidence.sha256 != item.entry.sha256:
                            raise ValueError(
                                "archived file ownership verification failed"
                            )
                        if (evidence.metadata.st_dev, evidence.metadata.st_ino) == (
                            item.snapshot.device,
                            item.snapshot.inode,
                        ):
                            raise ValueError(
                                "archived file ownership verification failed"
                            )
                        verified_destinations.append(evidence)
                    except BaseException:
                        os.close(parent)
                        raise
                encoded = (
                    json.dumps(
                        manifest.to_dict(),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                for evidence in verified_destinations:
                    evidence.verify()
                _write_bytes_no_replace(archive, "archive_manifest.json", encoded)
                for evidence in verified_destinations:
                    evidence.verify()
            finally:
                for evidence in locals().get("verified_destinations", []):
                    evidence.close()
                    os.close(evidence.parent)
                fcntl.flock(archive.descriptor, fcntl.LOCK_UN)
        return manifest


def _summary(manifest: ArchiveManifest, *, dry_run: bool) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "dry_run": dry_run,
        "source_file_count": len(manifest.entries),
        "linked_count": len(manifest.linked),
        "unclassified_count": len(manifest.unclassified),
        "files": [
            {
                "source": entry.source_relative,
                "classification": entry.classification,
                "sha256": entry.sha256,
            }
            for entry in manifest.entries
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate old showcase media safely")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = migrate_showcase_media(
            args.source,
            args.destination,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            _summary(manifest, dry_run=args.dry_run), ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
