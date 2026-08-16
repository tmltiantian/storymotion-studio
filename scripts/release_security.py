#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


KNOWN_SECRET_SHA256 = frozenset(
    {
        "433fa2823146bf61d732b6c3af8a7ded864ce691317cd2efd30af5859ace13a3",
    }
)
_MAX_SCAN_BYTES = 16 * 1024 * 1024
_TOKEN = re.compile(rb"[A-Za-z0-9_.-]{20,200}")
_OPENAI_KEY = re.compile(rb"sk-[A-Za-z0-9_-]{20,200}")
_JWT = re.compile(
    rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_FICTIONAL_MARKERS = (
    b"FICTIONAL_TEST_SECRET_SENTINEL",
    b"SK-FAKE",
    b"FAKE",
    b"EXAMPLE",
    b"PLACEHOLDER",
    b"CHANGEME",
    b"TESTONLY",
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    rule: str
    fingerprint: str
    object_id: str = ""

    def render(self) -> str:
        location = f"{self.path}:{self.line}"
        if self.object_id:
            location = f"{self.object_id[:12]}:{location}"
        return f"{location} rule={self.rule} fingerprint={self.fingerprint}"


def _run(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", *args], cwd=repo, input=input_bytes, stderr=subprocess.DEVNULL
    )


def _fictional(value: bytes) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in _FICTIONAL_MARKERS)


def _finding(
    path: str,
    data: bytes,
    start: int,
    value: bytes,
    rule: str,
    *,
    object_id: str = "",
) -> SecretFinding:
    return SecretFinding(
        path=path,
        line=data.count(b"\n", 0, start) + 1,
        rule=rule,
        fingerprint=hashlib.sha256(value).hexdigest()[:16],
        object_id=object_id,
    )


def scan_bytes(path: str, data: bytes, *, object_id: str = "") -> tuple[SecretFinding, ...]:
    if len(data) > _MAX_SCAN_BYTES or b"\0" in data[:8192]:
        return ()
    findings: list[SecretFinding] = []
    seen: set[tuple[int, str]] = set()

    for match in _TOKEN.finditer(data):
        value = match.group(0)
        if hashlib.sha256(value).hexdigest() in KNOWN_SECRET_SHA256:
            key = (match.start(), "revoked-credential")
            seen.add(key)
            findings.append(
                _finding(
                    path,
                    data,
                    match.start(),
                    value,
                    "revoked-credential",
                    object_id=object_id,
                )
            )

    patterns = (
        ("provider-key", _OPENAI_KEY, 0),
        ("jwt", _JWT, 0),
        ("private-key", _PRIVATE_KEY, 0),
    )
    for rule, pattern, group in patterns:
        for match in pattern.finditer(data):
            value = match.group(group)
            if _fictional(value):
                continue
            key = (match.start(group), rule)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                _finding(
                    path,
                    data,
                    match.start(group),
                    value,
                    rule,
                    object_id=object_id,
                )
            )
    return tuple(findings)


def _tracked_paths(repo: Path) -> tuple[str, ...]:
    payload = _run(repo, "ls-files", "-z")
    return tuple(item.decode("utf-8") for item in payload.split(b"\0") if item)


def scan_tracked_tree(repo: Path) -> tuple[SecretFinding, ...]:
    root = repo.resolve()
    findings: list[SecretFinding] = []
    for name in _tracked_paths(root):
        path = root / name
        if path.is_file() and not path.is_symlink():
            findings.extend(scan_bytes(name, path.read_bytes()))
    return tuple(findings)


def _history_blobs(repo: Path) -> Iterable[tuple[str, str, bytes]]:
    objects = _run(repo, "rev-list", "--objects", "--all").splitlines()
    paths: dict[str, str] = {}
    object_ids: list[str] = []
    for raw in objects:
        identifier, _, raw_path = raw.partition(b" ")
        object_id = identifier.decode("ascii")
        object_ids.append(object_id)
        paths[object_id] = raw_path.decode("utf-8", errors="replace") or "<unknown>"
    if not object_ids:
        return
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    payload = ("\n".join(object_ids) + "\n").encode("ascii")
    output, _ = process.communicate(payload)
    if process.returncode:
        raise RuntimeError("Unable to inspect Git history")
    cursor = 0
    while cursor < len(output):
        header_end = output.index(b"\n", cursor)
        header = output[cursor:header_end].split()
        cursor = header_end + 1
        if len(header) < 3 or header[1] == b"missing":
            continue
        object_id = header[0].decode("ascii")
        object_type = header[1]
        size = int(header[2])
        content = output[cursor : cursor + size]
        cursor += size + 1
        if object_type == b"blob" and size <= _MAX_SCAN_BYTES:
            yield object_id, paths.get(object_id, "<unknown>"), content


def scan_git_history(repo: Path) -> tuple[SecretFinding, ...]:
    findings: list[SecretFinding] = []
    for object_id, path, data in _history_blobs(repo.resolve()):
        findings.extend(scan_bytes(path, data, object_id=object_id))
    return tuple(findings)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan tracked release content without printing secret values"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--history", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    findings = (
        scan_git_history(args.repo) if args.history else scan_tracked_tree(args.repo)
    )
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"Secret scan failed: {len(findings)} redacted finding(s).")
        return 1
    mode = "history" if args.history else "tracked tree"
    print(f"Secret scan passed: {mode} contains no findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
