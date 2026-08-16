from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from scripts.run_workbench import build_launch_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def _occupied_port() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener, int(listener.getsockname()[1])


def test_launcher_selects_distinct_free_ports_when_requests_collide():
    config = build_launch_config(api_port=0, web_port=0)

    assert config.api_port != config.web_port
    assert config.api_host == "127.0.0.1"
    assert config.web_host == "127.0.0.1"
    assert config.api_url == f"http://127.0.0.1:{config.api_port}"
    assert config.web_url == f"http://127.0.0.1:{config.web_port}"

    for port in (config.api_port, config.web_port):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()


def test_launcher_formats_ipv6_loopback_urls_for_http_clients():
    config = build_launch_config(api_host="::1", api_port=0, web_port=0)

    assert config.api_url == f"http://[::1]:{config.api_port}"
    assert config.web_url == f"http://[::1]:{config.web_port}"


def test_launcher_skips_ports_that_are_already_occupied():
    listener, occupied = _occupied_port()
    try:
        config = build_launch_config(api_port=occupied, web_port=occupied)
    finally:
        listener.close()

    assert occupied not in {config.api_port, config.web_port}
    assert config.api_port != config.web_port


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2", "example.test"])
def test_launcher_rejects_non_loopback_hosts(host: str):
    with pytest.raises(ValueError, match="loopback"):
        build_launch_config(api_host=host, api_port=0, web_port=0)


@pytest.mark.parametrize("port", [-1, 65_536])
def test_launcher_rejects_invalid_ports(port: int):
    with pytest.raises(ValueError, match="port"):
        build_launch_config(api_port=port, web_port=0)


def _wait_for_ready_lines(process: subprocess.Popen[str]) -> tuple[str, str, str]:
    assert process.stdout is not None
    deadline = time.monotonic() + 30
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        lines.append(line)
        web = next((item.removeprefix("Web: ").strip() for item in lines if item.startswith("Web: ")), "")
        api = next((item.removeprefix("API: ").strip() for item in lines if item.startswith("API: ")), "")
        if web and api and any(item.strip() == "Ready" for item in lines):
            return web, api, "".join(lines)
    pytest.fail(f"launcher did not become ready: {''.join(lines)}")


def _port_closed(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.1)
    try:
        return probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        probe.close()


def test_launcher_starts_proxy_ready_services_and_cleans_owned_children():
    secret = "launcher-secret-must-not-leak"
    process = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_workbench.py",
            "--api-port",
            "0",
            "--web-port",
            "0",
            "--ready-timeout",
            "30",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "TASK_10_TEST_SECRET": secret},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        web_url, api_url, output = _wait_for_ready_lines(process)
        assert web_url != api_url
        assert json.load(urlopen(f"{api_url}/health", timeout=2)) == {"status": "ok"}
        assert isinstance(json.load(urlopen(f"{web_url}/api/projects", timeout=2)), list)
        assert secret not in output
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        tail = process.communicate(timeout=15)[0]
        output += tail

    assert process.returncode == 0
    assert "Stopped" in output
    assert secret not in output
    for url in (web_url, api_url):
        port = int(url.rsplit(":", 1)[1])
        assert _port_closed(port)
