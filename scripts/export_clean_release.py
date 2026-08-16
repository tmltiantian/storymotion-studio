#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_security import scan_git_history, scan_tracked_tree


@dataclass(frozen=True)
class CleanRelease:
    destination: Path
    commit: str
    tracked_files: int


def _git(repo: Path, *args: str, output: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE if output else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip() if output else ""


def _extract_archive(payload: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or ".git" in relative.parts
                or not (member.isdir() or member.isfile())
            ):
                raise ValueError("Release archive contains an unsafe entry")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Release archive file is unreadable")
            target.write_bytes(source.read())
            mode = stat.S_IMODE(member.mode) & 0o755
            os.chmod(target, mode or 0o644)


def _run_gitleaks(mode: str, repository: Path) -> None:
    executable = shutil.which("gitleaks")
    if executable is None:
        return
    result = subprocess.run(
        [
            executable,
            mode,
            str(repository),
            "--no-banner",
            "--redact=100",
            "--exit-code=1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise ValueError(f"Clean release failed the redacted Gitleaks {mode} scan")


def export_clean_release(source: Path, destination: Path) -> CleanRelease:
    root = source.resolve()
    target = destination.resolve()
    if target.exists():
        raise ValueError("Release destination must be a fresh path")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Source tracked worktree must be clean")
    findings = scan_tracked_tree(root)
    if findings:
        raise ValueError("Source tracked tree failed the redacted secret scan")

    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", "HEAD"], cwd=root
    )
    target.mkdir(parents=True)
    try:
        _extract_archive(archive, target)
        if scan_tracked_tree(root):
            raise ValueError("Source tracked tree changed during export")
        _run_gitleaks("dir", target)
        _git(target, "init", "-q", "--initial-branch=release", output=False)
        _git(target, "config", "user.name", "StoryMotion Release Export", output=False)
        _git(
            target,
            "config",
            "user.email",
            "release-export@example.invalid",
            output=False,
        )
        _git(target, "add", "-A", output=False)
        _git(
            target,
            "commit",
            "-q",
            "-m",
            "chore: import sanitized StoryMotion release",
            output=False,
        )
        current_findings = scan_tracked_tree(target)
        history_findings = scan_git_history(target)
        if current_findings or history_findings:
            raise ValueError("Clean release failed the redacted secret scan")
        _run_gitleaks("git", target)
        commit = _git(target, "rev-parse", "HEAD")
        tracked = _git(target, "ls-files").splitlines()
        return CleanRelease(target, commit, len(tracked))
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export sanitized tracked HEAD into a new one-commit Git history"
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        release = export_clean_release(args.source, args.destination)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Clean release export failed: {exc}")
        return 1
    print(f"Clean release path: {release.destination}")
    print(f"Clean release commit: {release.commit}")
    print(f"Tracked files: {release.tracked_files}")
    print("Tracked-tree and one-commit history secret scans passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
