from __future__ import annotations

import os
import stat
from collections import deque
from pathlib import Path
from uuid import uuid4


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MUTABLE_BY_OTHERS = stat.S_IWGRP | stat.S_IWOTH
_MAX_SYMLINKS = 40


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _safe_parts(value: str | Path) -> tuple[str, ...]:
    path = Path(value)
    if path.is_absolute():
        raise ValueError("Anchored path must be relative")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Anchored path contains an unsafe component")
    return parts


def _trusted_system_directory(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == 0
        and not metadata.st_mode & _MUTABLE_BY_OTHERS
    )


def _trusted_system_symlink(
    metadata: os.stat_result,
    parent_metadata: os.stat_result,
    *,
    parent_path_is_trusted: bool,
) -> bool:
    return (
        stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == 0
        and parent_path_is_trusted
        and _trusted_system_directory(parent_metadata)
    )


def _open_trusted_directory(path: Path, *, label: str) -> tuple[Path, int]:
    if not path.is_absolute() or path.anchor != os.sep:
        raise ValueError(f"{label} must be an absolute POSIX path")
    descriptors = [os.open(os.sep, _DIRECTORY_FLAGS)]
    canonical_parts: list[str] = []
    root_metadata = os.fstat(descriptors[0])
    trusted_paths = [_trusted_system_directory(root_metadata)]
    pending = deque(path.parts[1:])
    followed_symlinks = 0
    try:
        while pending:
            component = pending.popleft()
            if component in {"", "."}:
                continue
            if component == "..":
                if len(descriptors) > 1:
                    os.close(descriptors.pop())
                    canonical_parts.pop()
                    trusted_paths.pop()
                continue
            parent = descriptors[-1]
            metadata = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                if not _trusted_system_symlink(
                    metadata,
                    os.fstat(parent),
                    parent_path_is_trusted=trusted_paths[-1],
                ):
                    raise ValueError(f"{label} has an untrusted symlink ancestor")
                followed_symlinks += 1
                if followed_symlinks > _MAX_SYMLINKS:
                    raise ValueError(f"{label} has too many symlink ancestors")
                target = os.readlink(component, dir_fd=parent)
                confirmed = os.stat(
                    component,
                    dir_fd=parent,
                    follow_symlinks=False,
                )
                if (
                    not _same_file(metadata, confirmed)
                    or metadata.st_uid != confirmed.st_uid
                    or metadata.st_mode != confirmed.st_mode
                ):
                    raise ValueError(f"{label} symlink identity changed")
                target_path = Path(target)
                if target_path.is_absolute():
                    while len(descriptors) > 1:
                        os.close(descriptors.pop())
                    canonical_parts.clear()
                    trusted_paths[:] = [trusted_paths[0]]
                    target_parts = target_path.parts[1:]
                else:
                    target_parts = target_path.parts
                pending.extendleft(reversed(target_parts))
                continue
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=parent,
            )
            opened = os.fstat(next_descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or not stat.S_ISDIR(opened.st_mode)
                or not _same_file(metadata, opened)
            ):
                os.close(next_descriptor)
                raise ValueError(f"{label} identity changed")
            descriptors.append(next_descriptor)
            canonical_parts.append(component)
            trusted_paths.append(
                trusted_paths[-1] and _trusted_system_directory(opened)
            )
        canonical = Path(os.sep).joinpath(*canonical_parts)
        descriptor = descriptors.pop()
        return canonical, descriptor
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


class AnchoredDirectory:
    def __init__(
        self,
        supplied_path: Path,
        canonical_path: Path,
        descriptor: int,
        identity: os.stat_result,
        *,
        label: str,
    ):
        self.supplied_path = supplied_path
        self.canonical_path = canonical_path
        self.descriptor = descriptor
        self.identity = identity
        self.label = label
        self._closed = False

    @classmethod
    def open(cls, path: str | Path, *, label: str) -> AnchoredDirectory:
        supplied = Path(path).expanduser().absolute()
        descriptor = -1
        try:
            canonical, descriptor = _open_trusted_directory(supplied, label=label)
            final_component = os.stat(supplied, follow_symlinks=False)
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ValueError(f"{label} cannot be opened safely") from exc
        if stat.S_ISLNK(final_component.st_mode):
            os.close(descriptor)
            raise ValueError(f"{label} cannot be a symlink")
        try:
            identity = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(final_component.st_mode)
                or not stat.S_ISDIR(identity.st_mode)
                or not _same_file(final_component, identity)
            ):
                raise ValueError(f"{label} identity changed")
            anchor = cls(
                supplied,
                canonical,
                descriptor,
                identity,
                label=label,
            )
            anchor.verify()
            return anchor
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def verify(self) -> None:
        if self._closed:
            raise ValueError(f"{self.label} anchor is closed")
        try:
            supplied = os.stat(self.supplied_path, follow_symlinks=False)
            canonical = os.stat(self.canonical_path, follow_symlinks=False)
            opened = os.fstat(self.descriptor)
        except OSError as exc:
            raise ValueError(f"{self.label} identity changed") from exc
        if (
            stat.S_ISLNK(supplied.st_mode)
            or not stat.S_ISDIR(supplied.st_mode)
            or not stat.S_ISDIR(canonical.st_mode)
            or not _same_file(self.identity, supplied)
            or not _same_file(self.identity, canonical)
            or not _same_file(self.identity, opened)
        ):
            raise ValueError(f"{self.label} identity changed")

    def close(self) -> None:
        if not self._closed:
            os.close(self.descriptor)
            self._closed = True

    def __enter__(self) -> AnchoredDirectory:
        self.verify()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.verify()
        finally:
            self.close()

    def relative_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            return Path(*_safe_parts(candidate))
        absolute = candidate.absolute()
        for root in (self.supplied_path, self.canonical_path):
            try:
                relative = absolute.relative_to(root)
            except ValueError:
                continue
            return Path(*_safe_parts(relative))
        raise ValueError(f"Path is outside {self.label}")

    def child(
        self,
        path: str | Path,
        *,
        create: bool = False,
        label: str,
    ) -> AnchoredDirectory:
        relative = Path(*_safe_parts(path))
        descriptor = self.open_directory(relative, create=create)
        identity = os.fstat(descriptor)
        child = AnchoredDirectory(
            self.supplied_path / relative,
            self.canonical_path / relative,
            descriptor,
            identity,
            label=label,
        )
        try:
            child.verify()
        except Exception:
            child.close()
            raise
        return child

    def open_directory(self, path: str | Path, *, create: bool = False) -> int:
        parts = _safe_parts(path)
        descriptor = os.dup(self.descriptor)
        try:
            for component in parts:
                try:
                    next_descriptor = os.open(
                        component,
                        _DIRECTORY_FLAGS,
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    next_descriptor = os.open(
                        component,
                        _DIRECTORY_FLAGS,
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def verify_directory(self, path: str | Path, descriptor: int) -> None:
        try:
            current = self.open_directory(path)
        except OSError as exc:
            raise ValueError("Anchored directory identity changed") from exc
        try:
            expected_stat = os.fstat(descriptor)
            current_stat = os.fstat(current)
            if (
                not stat.S_ISDIR(expected_stat.st_mode)
                or not stat.S_ISDIR(current_stat.st_mode)
                or not _same_file(expected_stat, current_stat)
            ):
                raise ValueError("Anchored directory identity changed")
        finally:
            os.close(current)

    def _open_parent(
        self, path: str | Path, *, create: bool = False
    ) -> tuple[int, str]:
        parts = _safe_parts(path)
        if len(parts) == 1:
            return os.dup(self.descriptor), parts[0]
        return self.open_directory(Path(*parts[:-1]), create=create), parts[-1]

    def read_bytes(self, path: str | Path) -> bytes:
        parent, filename = self._open_parent(path)
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("Anchored file is not regular")
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)

    def write_bytes_atomic(self, path: str | Path, content: bytes) -> None:
        parent, filename = self._open_parent(path)
        temporary = f".{filename}.{uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                remaining = memoryview(content)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("Unable to write anchored file")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(
                temporary,
                filename,
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def unlink(self, path: str | Path) -> None:
        parent, filename = self._open_parent(path)
        try:
            try:
                os.unlink(filename, dir_fd=parent)
            except FileNotFoundError:
                return
            os.fsync(parent)
        finally:
            os.close(parent)

    def regular_file_exists(self, path: str | Path) -> bool:
        try:
            self.read_bytes(path)
        except FileNotFoundError:
            return False
        return True

    def listdir(self, path: str | Path | None = None) -> tuple[str, ...]:
        descriptor = (
            os.dup(self.descriptor) if path is None else self.open_directory(path)
        )
        try:
            return tuple(os.listdir(descriptor))
        finally:
            os.close(descriptor)
