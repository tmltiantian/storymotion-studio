import os
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/browser_fast.sh")


def _fake_agent_browser(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "agent-browser.log"
    fake = bin_dir / "agent-browser"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$AGENT_BROWSER_TEST_LOG\"\n"
        "if [[ \"$*\" == *'get title'* ]]; then echo 'Test title'; fi\n"
        "if [[ \"$*\" == *'get url'* ]]; then echo 'https://example.test/'; fi\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AGENT_BROWSER_TEST_LOG"] = str(log_path)
    return env, log_path


def test_browser_fast_inspect_reuses_named_session_and_prints_compact_snapshot(tmp_path):
    env, log_path = _fake_agent_browser(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT), "inspect", "https://example.test/"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "--session manju-factory --session-name manju-factory open https://example.test/",
        "--session manju-factory --session-name manju-factory get title",
        "--session manju-factory --session-name manju-factory get url",
        "--session manju-factory --session-name manju-factory snapshot -i -c -d 6",
    ]


def test_browser_fast_allows_session_and_snapshot_depth_overrides(tmp_path):
    env, log_path = _fake_agent_browser(tmp_path)
    env["BROWSER_SESSION"] = "gateway-check"
    env["BROWSER_SNAPSHOT_DEPTH"] = "4"

    result = subprocess.run(
        ["bash", str(SCRIPT), "snapshot"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").strip() == (
        "--session gateway-check --session-name gateway-check snapshot -i -c -d 4"
    )


def test_browser_fast_login_is_the_only_headed_command(tmp_path):
    env, log_path = _fake_agent_browser(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT), "login", "https://example.test/login"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "--session manju-factory --session-name manju-factory close",
        "--session manju-factory --session-name manju-factory --headed open "
        "https://example.test/login",
    ]


def test_browser_fast_rejects_unknown_command_without_invoking_browser(tmp_path):
    env, log_path = _fake_agent_browser(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT), "unknown"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert not log_path.exists()
