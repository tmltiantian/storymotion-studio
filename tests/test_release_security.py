from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.export_clean_release import export_clean_release
from scripts.release_security import (
    KNOWN_SECRET_SHA256,
    SecretFinding,
    scan_git_history,
    scan_tracked_tree,
)


REVOKED_CREDENTIAL_SHA256 = (
    "433fa2823146bf61d732b6c3af8a7ded864ce691317cd2efd30af5859ace13a3"
)
PRIVATE_GATEWAY_HOST_SHA256 = (
    "1a5a506cb1b58f1b4f53bfba94b653b10d4ca73876b66b2d114b5e31b10a8730"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=repo, text=True, stderr=subprocess.STDOUT
    ).strip()


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True)


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=repo,
        check=True,
    )
    return repo


def test_release_scanner_knows_revoked_fingerprint_and_never_renders_secrets(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    canary = "sk-" + "DELIBERATE_SECRET_SCAN_CANARY_7F3A91C5E2D84B6A"
    (repo / "fixture.txt").write_text(f"access_token={canary}\n", encoding="utf-8")
    _commit(repo, "add scanner canary")

    findings = scan_tracked_tree(repo)

    assert REVOKED_CREDENTIAL_SHA256 in KNOWN_SECRET_SHA256
    assert findings
    rendered = "\n".join(finding.render() for finding in findings)
    assert canary not in rendered
    assert "fixture.txt:1" in rendered


def test_release_scanner_allows_unmistakably_fictional_test_sentinels(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    (repo / "fixture.txt").write_text(
        "access_token=FICTIONAL_TEST_SECRET_SENTINEL_DO_NOT_USE_000000000000\n"
        "api_key=FAKE\n"
        "令牌 sk-FAKE000000000000000000000000000000TESTONLY\n",
        encoding="utf-8",
    )
    _commit(repo, "add fictional fixtures")

    assert scan_tracked_tree(repo) == ()


def test_clean_release_export_drops_old_history_and_ignored_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _repository(tmp_path)
    canary = "sk-" + "DELIBERATE_SECRET_SCAN_CANARY_4D2C8A6E1F7B9305"
    (source / ".gitignore").write_text("ignored-output/\n", encoding="utf-8")
    (source / "README.md").write_text(
        f"access_token={canary}\n", encoding="utf-8"
    )
    _commit(source, "historical compromised commit")
    (source / "README.md").write_text("sanitized release\n", encoding="utf-8")
    (source / "ignored-output").mkdir()
    (source / "ignored-output" / "artifact.txt").write_text(
        "not for release", encoding="utf-8"
    )
    _commit(source, "sanitize release")
    destination = tmp_path / "clean-release"
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    scan_log = tmp_path / "gitleaks.log"
    gitleaks = tool_dir / "gitleaks"
    gitleaks.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$STORYMOTION_GITLEAKS_LOG\"\n",
        encoding="utf-8",
    )
    gitleaks.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tool_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("STORYMOTION_GITLEAKS_LOG", str(scan_log))

    assert scan_tracked_tree(source) == ()
    assert any(
        isinstance(finding, SecretFinding) for finding in scan_git_history(source)
    )

    result = export_clean_release(source, destination)

    assert result.commit == _git(destination, "rev-parse", "HEAD")
    assert _git(destination, "rev-list", "--count", "--all") == "1"
    assert not (destination / "ignored-output").exists()
    assert not (destination / ".git" / "objects" / "info" / "alternates").exists()
    assert scan_tracked_tree(destination) == ()
    assert scan_git_history(destination) == ()
    calls = scan_log.read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("dir ") and "--redact=100" in call for call in calls)
    assert any(call.startswith("git ") and "--redact=100" in call for call in calls)


def test_clean_release_export_requires_a_clean_tracked_head(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    (source / "README.md").write_text("release\n", encoding="utf-8")
    _commit(source, "initial")
    (source / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="tracked worktree must be clean"):
        export_clean_release(source, tmp_path / "clean-release")


def test_clean_release_export_cli_runs_from_repository_root(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    (source / "README.md").write_text("sanitized release\n", encoding="utf-8")
    (source / "scripts").mkdir()
    for name in ("export_clean_release.py", "release_security.py"):
        (source / "scripts" / name).write_bytes((Path("scripts") / name).read_bytes())
    _commit(source, "add release tools")
    destination = tmp_path / "clean-release"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/export_clean_release.py",
            str(destination),
            "--source",
            str(source),
        ],
        cwd=source,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert _git(destination, "rev-list", "--count", "--all") == "1"


def test_current_operator_docs_have_no_retired_runtime_or_machine_paths() -> None:
    current_docs = (
        Path("README.md"),
        Path("docs/deployment.md"),
        Path("docs/pipeline-code-map.md"),
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in current_docs)

    assert not Path("docs/runtime-evaluation.md").exists()
    assert Path("docs/archive/runtime-evaluation-2026-07.md").is_file()
    assert "/Users/" not in combined
    assert "Use LumenX as the production runtime" not in combined


def test_tracked_text_has_no_retired_private_gateway_host() -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    for raw_path in tracked:
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8"))
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for url in re.findall(r"https?://[^\s\"']+", text):
            host = re.sub(r"^https?://", "", url).split("/", 1)[0].rstrip("`)},.")
            assert hashlib.sha256(host.encode()).hexdigest() != PRIVATE_GATEWAY_HOST_SHA256
