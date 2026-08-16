#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_ROOT = _REPO_ROOT / "sites" / "storymotion-studio"
_VITE = _WEB_ROOT / "node_modules" / ".bin" / "vite"
_FRONTEND_ENVIRONMENT_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
)
_MAX_LAUNCH_ATTEMPTS = 3


class LauncherError(RuntimeError):
    pass


class _RetryableLauncherError(LauncherError):
    pass


@dataclass(frozen=True)
class LaunchConfig:
    api_host: str
    api_port: int
    web_host: str
    web_port: int

    @staticmethod
    def _origin(host: str, port: int) -> str:
        url_host = f"[{host}]" if ":" in host else host
        return f"http://{url_host}:{port}"

    @property
    def api_url(self) -> str:
        return self._origin(self.api_host, self.api_port)

    @property
    def web_url(self) -> str:
        return self._origin(self.web_host, self.web_port)


def _validate_port(value: int) -> int:
    port = int(value)
    if not 0 <= port <= 65_535:
        raise ValueError("Workbench port must be between 0 and 65535")
    return port


def _port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _free_port(host: str, requested: int, excluded: set[int]) -> int:
    if requested == 0:
        family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        while True:
            probe = socket.socket(family, socket.SOCK_STREAM)
            try:
                probe.bind((host, 0))
                selected = int(probe.getsockname()[1])
            finally:
                probe.close()
            if selected not in excluded:
                return selected

    candidates = range(requested, 65_536)
    for candidate in candidates:
        if candidate not in excluded and _port_is_free(host, candidate):
            return candidate
    for candidate in range(1024, requested):
        if candidate not in excluded and _port_is_free(host, candidate):
            return candidate
    raise RuntimeError("No free loopback port is available")


def build_launch_config(
    api_host: str = "127.0.0.1",
    api_port: int = 8787,
    web_port: int = 5173,
) -> LaunchConfig:
    if api_host not in _LOOPBACK_HOSTS:
        raise ValueError("Workbench services may only bind to a loopback host")
    requested_api = _validate_port(api_port)
    requested_web = _validate_port(web_port)
    selected_api = _free_port(api_host, requested_api, set())
    selected_web = _free_port(api_host, requested_web, {selected_api})
    return LaunchConfig(
        api_host=api_host,
        api_port=selected_api,
        web_host=api_host,
        web_port=selected_web,
    )


@dataclass(frozen=True)
class _OwnedChild:
    label: str
    process: subprocess.Popen[bytes]


def _endpoint_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:
            return 200 <= response.status < 400
    except (HTTPError, OSError, TimeoutError, URLError):
        return False


def _frontend_environment(
    parent: dict[str, str], api_url: str
) -> dict[str, str]:
    selected = {
        key: parent[key]
        for key in _FRONTEND_ENVIRONMENT_KEYS
        if key in parent and parent[key]
    }
    selected["STORYMOTION_API_URL"] = api_url
    return selected


def _wait_until_ready(
    config: LaunchConfig,
    children: Sequence[_OwnedChild],
    stop_requested: threading.Event,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    endpoints = (f"{config.api_url}/health", config.web_url)
    while time.monotonic() < deadline:
        if stop_requested.is_set():
            raise LauncherError("Workbench startup was interrupted.")
        for child in children:
            return_code = child.process.poll()
            if return_code is not None:
                raise _RetryableLauncherError(
                    f"{child.label} service exited before readiness (code {return_code})."
                )
        if all(_endpoint_ready(url) for url in endpoints):
            return
        time.sleep(0.1)
    raise _RetryableLauncherError(
        "Local services did not become ready before the timeout."
    )


def _signal_process_group(child: _OwnedChild, selected_signal: int) -> None:
    if child.process.poll() is not None:
        return
    try:
        os.killpg(child.process.pid, selected_signal)
    except ProcessLookupError:
        pass


def _stop_children(children: Sequence[_OwnedChild], timeout: float = 5.0) -> None:
    for child in children:
        _signal_process_group(child, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    for child in children:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            child.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            pass
    for child in children:
        if child.process.poll() is None:
            _signal_process_group(child, signal.SIGKILL)
    for child in children:
        try:
            child.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def _start_children(config: LaunchConfig) -> list[_OwnedChild]:
    if not _VITE.is_file() or not os.access(_VITE, os.X_OK):
        raise LauncherError("Frontend dependencies are not installed.")

    api_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--serve-api",
        "--api-host",
        config.api_host,
        "--api-port",
        str(config.api_port),
        "--web-origin",
        config.web_url,
    ]
    web_command = [
        str(_VITE),
        "--host",
        config.web_host,
        "--port",
        str(config.web_port),
        "--strictPort",
    ]
    child_environment = os.environ.copy()
    web_environment = _frontend_environment(child_environment, config.api_url)
    children: list[_OwnedChild] = []
    try:
        children.append(
            _OwnedChild(
                "API",
                subprocess.Popen(
                    api_command,
                    cwd=_REPO_ROOT,
                    env=child_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ),
            )
        )
        children.append(
            _OwnedChild(
                "Frontend",
                subprocess.Popen(
                    web_command,
                    cwd=_WEB_ROOT,
                    env=web_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                ),
            )
        )
    except OSError as exc:
        _stop_children(children)
        raise LauncherError("A local service could not be started.") from exc
    return children


def run_launcher(config: LaunchConfig, *, ready_timeout: float = 30.0) -> int:
    if ready_timeout <= 0:
        raise ValueError("Readiness timeout must be positive")
    stop_requested = threading.Event()
    previous_handlers: dict[int, object] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    for selected_signal in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[selected_signal] = signal.getsignal(selected_signal)
        signal.signal(selected_signal, request_stop)

    children: list[_OwnedChild] = []
    current = config
    try:
        for attempt in range(1, _MAX_LAUNCH_ATTEMPTS + 1):
            print("Starting local services", flush=True)
            try:
                children = _start_children(current)
                _wait_until_ready(
                    current,
                    children,
                    stop_requested,
                    ready_timeout,
                )
                break
            except _RetryableLauncherError:
                _stop_children(children)
                children = []
                if stop_requested.is_set() or attempt == _MAX_LAUNCH_ATTEMPTS:
                    raise
                current = build_launch_config(
                    api_host=current.api_host,
                    api_port=0,
                    web_port=0,
                )
                print("Retrying with new local ports", flush=True)
        print("Ready", flush=True)
        print(f"Web: {current.web_url}", flush=True)
        print(f"API: {current.api_url}", flush=True)
        print("Press Ctrl+C to stop", flush=True)
        while not stop_requested.wait(0.2):
            for child in children:
                return_code = child.process.poll()
                if return_code is not None:
                    raise LauncherError(
                        f"{child.label} service stopped unexpectedly (code {return_code})."
                    )
        print("Stopping", flush=True)
        return 0
    finally:
        _stop_children(children)
        for selected_signal, handler in previous_handlers.items():
            signal.signal(selected_signal, handler)
        if children:
            print("Stopped", flush=True)


def _serve_api(host: str, port: int, web_origin: str) -> int:
    if _REPO_ROOT.as_posix() not in sys.path:
        sys.path.insert(0, _REPO_ROOT.as_posix())
    from factory.workbench_api import run_workbench_api
    from factory.workbench_service import WorkbenchService

    config_path = _REPO_ROOT / "config" / "factory.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    service = WorkbenchService(
        _REPO_ROOT,
        config=config,
        frontend_origins=(web_origin,),
    )
    run_workbench_api(service, host=host, port=port)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run StoryMotion Studio locally")
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8787)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--ready-timeout", type=float, default=30.0)
    parser.add_argument("--serve-api", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--web-origin", default="", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.serve_api:
            if not args.web_origin:
                raise ValueError("Frontend origin is required")
            return _serve_api(args.api_host, args.api_port, args.web_origin)
        config = build_launch_config(
            api_host=args.api_host,
            api_port=args.api_port,
            web_port=args.web_port,
        )
        return run_launcher(config, ready_timeout=args.ready_timeout)
    except (LauncherError, ValueError) as exc:
        print(f"Launcher error: {exc}", file=sys.stderr, flush=True)
        return 1
    except Exception:
        print(
            "Launcher error: the local workbench could not be started.",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
