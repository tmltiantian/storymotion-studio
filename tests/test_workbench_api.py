from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from factory.pipeline_contracts import ProjectMode, ProjectSpec, StageState
from factory.pipeline_jobs import JobManager
from factory.pipeline_store import create_project
from factory.workbench_api import create_workbench_app, run_workbench_api
from factory.workbench_service import WorkbenchService


class FakeWorkbenchService:
    frontend_origins = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    def project_detail(self, project_id: str):
        if project_id != "episode_01":
            raise KeyError(project_id)
        return {
            "project_id": project_id,
            "stages": [
                {
                    "stage": "storyboard",
                    "execution_state": "passed",
                    "review_state": "awaiting_review",
                    "artifacts": [],
                }
            ],
        }

    def list_projects(self):
        return [self.project_detail("episode_01")]

    def video_workspace(self, project_id: str):
        if project_id != "episode_01":
            raise KeyError(project_id)
        return {
            "schema_version": "motion-comic-factory.video-workspace.v1",
            "project_id": project_id,
            "shots": [{"shot_id": "shot_01", "duration_seconds": 5.0}],
            "selected_shot_ids": ["shot_01"],
            "job": None,
            "failed_job_recovery": None,
        }

    def provider_status(self):
        return {
            "capabilities": {
                "video": {
                    "provider": "gateway",
                    "model": "safe-model",
                    "ready": True,
                    "blockers": [],
                    "enabled": True,
                    "supports_reference_images": True,
                }
            }
        }

    def media_info(self, artifact_id: str):
        raise KeyError(artifact_id)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_workbench_app(FakeWorkbenchService()))


def _authorized_media_service(
    tmp_path: Path,
) -> tuple[WorkbenchService, Path, str]:
    project = tmp_path / "runs/episode_01"
    artifact = project / "stages/concept/preview.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"0123456789")
    spec = ProjectSpec(
        project_id="episode_01",
        title="Episode 01",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "Media test"},
        output_dir=project / "output",
    )
    package = create_project(project, spec)
    first = replace(
        package.stages[0],
        state=StageState.PASSED,
        artifacts=(str(artifact),),
    )
    (project / "production_package.json").write_text(
        json.dumps(package.with_stages((first, *package.stages[1:])).to_dict()),
        encoding="utf-8",
    )
    service = WorkbenchService(
        tmp_path,
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    return service, artifact, artifact_id


def test_project_detail_contains_execution_and_review_states(client: TestClient):
    response = client.get("/api/projects/episode_01")

    assert response.status_code == 200
    assert response.json()["stages"][0]["review_state"] == "awaiting_review"


def test_video_workspace_route_returns_path_free_shots_and_persisted_job(client: TestClient):
    response = client.get("/api/projects/episode_01/video/workspace")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "motion-comic-factory.video-workspace.v1",
        "project_id": "episode_01",
        "shots": [{"shot_id": "shot_01", "duration_seconds": 5.0}],
        "selected_shot_ids": ["shot_01"],
        "job": None,
        "failed_job_recovery": None,
    }


def test_media_route_rejects_raw_paths(client: TestClient):
    assert client.get("/api/media/../../.env").status_code in {400, 404}


def test_provider_status_never_returns_secrets(client: TestClient):
    payload = client.get("/api/settings/providers").json()

    assert "sk-" not in json.dumps(payload)


def test_cors_allows_configured_localhost_and_rejects_other_origins(
    client: TestClient,
) -> None:
    allowed = client.options(
        "/api/projects",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    rejected = client.options(
        "/api/projects",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert rejected.status_code == 400
    assert rejected.headers.get("access-control-allow-origin") is None


def test_request_bodies_are_strict_and_errors_have_no_traceback(client: TestClient):
    response = client.post(
        "/api/projects/episode_01/stages/video/run",
        json={"enable_live": "yes", "unexpected": True},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "traceback" not in response.text.lower()


def test_domain_errors_redact_raw_paths() -> None:
    class InvalidService(FakeWorkbenchService):
        def submit_stage_run(self, *_args, **_kwargs):
            raise ValueError("Invalid project file: /private/workspace/project.json")

    response = TestClient(create_workbench_app(InvalidService())).post(
        "/api/projects/episode_01/stages/video/run",
        json={"enable_live": False},
    )

    assert response.status_code == 400
    assert "/private/" not in response.text
    assert "project.json" not in response.text


def test_media_supports_head_and_exact_single_ranges(tmp_path: Path) -> None:
    workspace = tmp_path
    runs = workspace / "runs"
    project = runs / "episode_01"
    artifact = project / "clip.mp4"
    project.mkdir(parents=True)
    artifact.write_bytes(b"0123456789")
    (project / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.project-spec.v1",
                "project_id": "episode_01",
                "title": "Episode 01",
                "mode": "original",
                "input": {"kind": "idea"},
                "output_dir": str(project / "output"),
                "target": {},
                "characters": [],
                "providers": {},
                "policies": {},
                "mode_options": {},
            }
        ),
        encoding="utf-8",
    )
    stages = [
        {
            "stage": name,
            "state": "passed" if name == "concept" else "pending",
            "artifacts": [str(artifact)] if name == "concept" else [],
        }
        for name in (
            "concept",
            "script",
            "storyboard",
            "assets",
            "audio",
            "video",
            "edit",
            "eval",
            "deliver",
        )
    ]
    (project / "production_package.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.production-package.v1",
                "project_id": "episode_01",
                "mode": "original",
                "spec_path": str(project / "project.json"),
                "spec_sha256": "a" * 64,
                "stages": stages,
                "final_outputs": [],
                "eval_reports": [],
            }
        ),
        encoding="utf-8",
    )
    service = WorkbenchService(
        workspace,
        job_manager=JobManager(workspace),
        provider_profile_loader=lambda: None,
    )
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    media_client = TestClient(create_workbench_app(service))

    head = media_client.head(f"/api/media/{artifact_id}")
    partial = media_client.get(
        f"/api/media/{artifact_id}", headers={"Range": "bytes=2-5"}
    )
    invalid = media_client.get(
        f"/api/media/{artifact_id}", headers={"Range": "bytes=0-1,4-5"}
    )

    assert head.status_code == 200
    assert head.headers["accept-ranges"] == "bytes"
    assert head.headers["content-length"] == "10"
    assert head.content == b""
    assert partial.status_code == 206
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == "bytes 2-5/10"
    assert partial.headers["content-length"] == "4"
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */10"

    suffix = media_client.get(
        f"/api/media/{artifact_id}", headers={"Range": "bytes=-3"}
    )
    open_ended = media_client.get(
        f"/api/media/{artifact_id}", headers={"Range": "bytes=7-"}
    )
    assert suffix.content == b"789"
    assert suffix.headers["content-range"] == "bytes 7-9/10"
    assert open_ended.content == b"789"
    assert open_ended.headers["content-range"] == "bytes 7-9/10"

    artifact.write_bytes(b"")
    empty = media_client.get(f"/api/media/{artifact_id}")
    assert empty.status_code == 200
    assert empty.headers["content-length"] == "0"
    assert empty.content == b""


def test_media_stream_and_head_use_and_close_one_authorized_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, artifact, artifact_id = _authorized_media_service(tmp_path)
    assert hasattr(service, "open_media"), "service must expose opened media contract"
    original_open = service.open_media
    opened = []

    def open_then_replace(selected_artifact_id: str):
        authorized = original_open(selected_artifact_id)
        opened.append(authorized)
        if len(opened) == 1:
            replacement = artifact.with_name("replacement.mp4")
            replacement.write_bytes(b"xy")
            os.replace(replacement, artifact)
        return authorized

    monkeypatch.setattr(service, "open_media", open_then_replace)
    media_client = TestClient(create_workbench_app(service))

    streamed = media_client.get(f"/api/media/{artifact_id}")
    head = media_client.head(f"/api/media/{artifact_id}")

    assert streamed.status_code == 200
    assert streamed.headers["content-length"] == "10"
    assert streamed.content == b"0123456789"
    assert head.status_code == 200
    assert head.headers["content-length"] == "2"
    assert all(item.closed for item in opened)


def test_media_disconnect_before_first_chunk_closes_authorized_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _artifact, artifact_id = _authorized_media_service(tmp_path)
    original_open = service.open_media
    opened = []

    def capture_open(selected_artifact_id: str):
        authorized = original_open(selected_artifact_id)
        opened.append(authorized)
        return authorized

    monkeypatch.setattr(service, "open_media", capture_open)
    app = create_workbench_app(service)
    route = next(
        item
        for item in app.routes
        if getattr(item, "path", "") == "/api/media/{artifact_id}"
        and "GET" in getattr(item, "methods", set())
    )

    async def disconnect() -> None:
        response = await route.endpoint(artifact_id=artifact_id, range_header=None)

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            raise OSError("client disconnected")

        with pytest.raises(ClientDisconnect):
            await response(
                {
                    "type": "http",
                    "asgi": {"spec_version": "2.4"},
                    "method": "GET",
                    "path": f"/api/media/{artifact_id}",
                    "headers": [],
                },
                receive,
                send,
            )

    asyncio.run(disconnect())

    assert len(opened) == 1
    assert opened[0].closed


def test_sse_replays_only_events_after_last_event_id(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="episode_01", operation="run_stage", payload={})
    manager.start(job_id)
    manager.complete(job_id, result={})
    service = WorkbenchService(
        tmp_path,
        job_manager=manager,
        provider_profile_loader=lambda: None,
    )
    event_client = TestClient(create_workbench_app(service))

    response = event_client.get(
        f"/api/jobs/{job_id}/events",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\n" not in response.text
    assert "id: 2\n" in response.text
    assert "id: 3\n" in response.text
    assert "/runs/" not in response.text


def test_server_runner_rejects_non_loopback_binding(monkeypatch) -> None:
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("factory.workbench_api.uvicorn.run", fake_run)

    with pytest.raises(ValueError):
        run_workbench_api(FakeWorkbenchService(), host="0.0.0.0")

    assert called is False
