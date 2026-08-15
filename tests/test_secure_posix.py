from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from factory import secure_posix
from factory.secure_posix import AnchoredDirectory


def test_rejects_user_controlled_ancestor_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    workspace = target / "workspace"
    workspace.mkdir(parents=True)
    alias = tmp_path / "user-alias"
    alias.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink|trusted"):
        AnchoredDirectory.open(alias / "workspace", label="Workspace")

    with AnchoredDirectory.open(target, label="Target") as anchor:
        with pytest.raises(ValueError, match="outside"):
            anchor.relative_path(alias / "workspace")


def test_rejects_alias_in_mutable_parent_even_with_trusted_owner_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    workspace = target / "workspace"
    workspace.mkdir(parents=True)
    mutable_parent = tmp_path / "mutable-parent"
    mutable_parent.mkdir()
    mutable_parent.chmod(0o777)
    alias = mutable_parent / "system-looking-alias"
    alias.symlink_to(target, target_is_directory=True)
    real_stat = os.stat

    def report_symlinks_as_root_owned(path, *args, **kwargs):
        metadata = real_stat(path, *args, **kwargs)
        if not stat.S_ISLNK(metadata.st_mode):
            return metadata
        values = list(metadata)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(secure_posix.os, "stat", report_symlinks_as_root_owned)

    with pytest.raises(ValueError, match="symlink|trusted"):
        AnchoredDirectory.open(alias / "workspace", label="Workspace")


def test_accepts_trusted_macos_var_alias(tmp_path: Path) -> None:
    private_var = Path("/private/var")
    if not Path("/var").is_symlink() or not tmp_path.is_relative_to(private_var):
        pytest.skip("macOS /var system alias is unavailable")
    alias = Path("/var") / tmp_path.relative_to(private_var)

    with AnchoredDirectory.open(alias, label="Workspace") as anchor:
        assert anchor.canonical_path == tmp_path.resolve()
        assert os.path.samestat(anchor.identity, tmp_path.stat())
