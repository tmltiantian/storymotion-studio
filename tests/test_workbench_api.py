from __future__ import annotations

import asyncio
import json
import os
import threading
import time
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
from factory.work_catalog import WorkCatalog
from scripts.migrate_showcase_works import migrate_showcase_media


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
                    "credential_present": True,
                }
            },
            "defaults": {
                "voice_mapping": [],
                "output": {
                    "aspect_ratio": "9:16",
                    "resolution": "1080x1920",
                    "fps": 30,
                    "target_duration_seconds": 75,
                },
                "generation": {"concurrency": 1, "fee_cap_yuan": None},
            },
        }

    def list_works(self):
        return [
            {
                "work_id": "work_0123456789abcdef0123456789abcdef",
                "project_id": "episode_01",
                "title": "咪要去面试",
                "mode": "replica",
                "source": "delivered",
                "delivered_at": "2026-08-15T12:00:00Z",
                "delivery_date": "2026-08-15",
                "roles": ["豆包"],
                "current_version": "V3.1",
            }
        ]

    def work_detail(self, work_id: str):
        if work_id != "work_0123456789abcdef0123456789abcdef":
            raise KeyError(work_id)
        return {
            **self.list_works()[0],
            "versions": [
                {
                    "version_id": "version_0123456789abcdef",
                    "label": "V3.1",
                    "created_at": "2026-08-15T12:00:00Z",
                    "outputs": [],
                    "eval_reports": [],
                }
            ],
        }

    def media_info(self, artifact_id: str):
        raise KeyError(artifact_id)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_workbench_app(FakeWorkbenchService()))


def _authorized_media_service(
    tmp_path: Path,
    **service_options,
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
        **service_options,
    )
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    return service, artifact, artifact_id


def test_project_detail_contains_execution_and_review_states(client: TestClient):
    response = client.get("/api/projects/episode_01")

    assert response.status_code == 200
    assert response.json()["stages"][0]["review_state"] == "awaiting_review"


def test_video_workspace_route_returns_path_free_shots_and_persisted_job(
    client: TestClient,
):
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


def test_works_routes_return_path_free_catalog_and_safe_404(client: TestClient):
    listed = client.get("/api/works")
    detail = client.get("/api/works/work_0123456789abcdef0123456789abcdef")
    missing = client.get("/api/works/work_ffffffffffffffffffffffffffffffff")

    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "咪要去面试"
    assert listed.json()[0]["roles"] == ["豆包"]
    assert listed.json()[0]["delivery_date"] == "2026-08-15"
    assert detail.status_code == 200
    assert detail.json()["versions"][0]["label"] == "V3.1"
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "not_found", "message": "Resource was not found"}
    }
    serialized = listed.text + detail.text
    assert "/Users/" not in serialized
    assert "archive_relative" not in serialized
    assert "source_relative" not in serialized


def test_settings_expose_public_defaults_and_credential_booleans_only(
    client: TestClient,
) -> None:
    payload = client.get("/api/settings/providers").json()

    assert payload["capabilities"]["video"]["credential_present"] is True
    assert payload["defaults"]["output"]["resolution"] == "1080x1920"
    assert payload["defaults"]["generation"]["fee_cap_yuan"] is None
    serialized = json.dumps(payload)
    assert "api_key" not in serialized
    assert "base_url" not in serialized
    assert "key_source" not in serialized


def test_archived_work_media_is_available_only_through_opaque_descriptor(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    content = b"approved-voice"
    (source / "black-cat-approved.m4a").write_bytes(content)
    archive = tmp_path / "output/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    service = WorkbenchService(
        tmp_path,
        archive_manifest=archive / "archive_manifest.json",
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    api = TestClient(create_workbench_app(service))

    works = api.get("/api/works").json()
    detail = api.get(f"/api/works/{works[0]['work_id']}").json()
    artifact = detail["versions"][0]["outputs"][0]
    media = api.get(artifact["media_url"])

    assert artifact["artifact_id"].startswith("art_")
    assert "archive_relative" not in json.dumps(detail)
    assert str(archive) not in json.dumps(detail)
    assert media.status_code == 200
    assert media.content == content
    assert api.get("/api/media/audio/black-cat-approved.m4a").status_code in {400, 404}


def test_catalog_media_rejects_replacement_after_authorization(tmp_path: Path) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    (source / "black-cat-approved.m4a").write_bytes(b"approved-voice")
    archive = tmp_path / "assets/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    service = WorkbenchService(
        tmp_path,
        archive_manifest=archive / "archive_manifest.json",
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    detail = service.work_detail(service.list_works()[0]["work_id"])
    artifact_id = detail["versions"][0]["outputs"][0]["artifact_id"]
    archived = next(archive.rglob("black-cat-approved.m4a"))
    replacement = archived.with_suffix(".replacement")
    replacement.write_bytes(b"replacement bytes")
    os.replace(replacement, archived)

    response = TestClient(create_workbench_app(service)).get(
        f"/api/media/{artifact_id}"
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Resource was not found"}
    }


def test_catalog_media_serves_verified_snapshot_after_in_place_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    (source / "black-cat-approved.m4a").write_bytes(b"0123456789")
    archive = tmp_path / "assets/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    service = WorkbenchService(
        tmp_path,
        archive_manifest=archive / "archive_manifest.json",
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    detail = service.work_detail(service.list_works()[0]["work_id"])
    artifact_id = detail["versions"][0]["outputs"][0]["artifact_id"]
    opened = service.open_media(artifact_id)
    archived = next(archive.rglob("black-cat-approved.m4a"))
    archived.write_bytes(b"abcdefghij")

    assert b"".join(opened.iter_range(start=0, end=9, chunk_size=3)) == b"0123456789"
    assert opened.closed


def test_catalog_media_range_never_mixes_bytes_after_source_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    original = b"0123456789abcdef"
    (source / "black-cat-approved.m4a").write_bytes(original)
    archive = tmp_path / "assets/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    service = WorkbenchService(
        tmp_path,
        archive_manifest=archive / "archive_manifest.json",
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    detail = service.work_detail(service.list_works()[0]["work_id"])
    artifact_id = detail["versions"][0]["outputs"][0]["artifact_id"]
    opened = service.open_media(artifact_id)
    chunks = opened.iter_range(start=2, end=13, chunk_size=4)
    first = next(chunks)
    archived = next(archive.rglob("black-cat-approved.m4a"))
    timestamps = archived.stat().st_atime_ns, archived.stat().st_mtime_ns
    archived.write_bytes(b"X" * len(original))
    os.utime(archived, ns=timestamps)

    assert first + b"".join(chunks) == original[2:14]
    assert opened.closed


def test_catalog_media_binding_updates_when_archive_path_moves(tmp_path: Path) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    content = b"approved-voice"
    (source / "black-cat-approved.m4a").write_bytes(content)
    archive = tmp_path / "assets/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    service = WorkbenchService(
        tmp_path,
        archive_manifest=archive / "archive_manifest.json",
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    detail = service.work_detail(service.list_works()[0]["work_id"])
    artifact_id = detail["versions"][0]["outputs"][0]["artifact_id"]
    manifest_path = archive / "archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_relative = manifest["entries"][0]["archive_relative"]
    new_relative = "linked/moved/black-cat-approved.m4a"
    (archive / "linked/moved").mkdir()
    (archive / old_relative).rename(archive / new_relative)
    manifest["entries"][0]["archive_relative"] = new_relative
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert service.read_media(artifact_id)[1] == content


def test_work_catalog_redacts_and_bounds_all_public_metadata(tmp_path: Path) -> None:
    source = tmp_path / "public/audio"
    source.mkdir(parents=True)
    (source / "black-cat-approved.m4a").write_bytes(b"approved-voice")
    archive = tmp_path / "assets/workbench_archive"
    migrate_showcase_media(source.parent, archive)
    manifest_path = archive / "archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["entries"][0]
    entry["title"] = r"C:\Users\private\.env"
    entry["version_label"] = "ftp://alice:password@provider.example/private"
    entry["metadata"]["role"] = "access_token＝DLgKqSHww3_LsM3Rqrx4Ks22dvRpjie"
    entry["metadata"]["description"] = (
        "保留中文说明 "
        + "长" * 900
        + " \\server\\private\\secret DLgKqSHww3_LsM3Rqrx4Ks22dvRpjie"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service = WorkbenchService(
        tmp_path,
        archive_manifest=manifest_path,
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )

    listed = service.list_works()
    detail = service.work_detail(listed[0]["work_id"])
    serialized = json.dumps({"listed": listed, "detail": detail}, ensure_ascii=False)

    assert "C:\\Users" not in serialized
    assert "access_token" not in serialized
    assert "DLgKq" not in serialized
    assert "ftp://" not in serialized
    assert "server" not in serialized
    assert len(listed[0]["title"]) <= 120
    assert all(len(role) <= 80 for role in listed[0]["roles"])
    assert len(detail["versions"][0]["label"]) <= 80
    assert len(detail["versions"][0]["iteration_summary"]) <= 500
    assert "保留中文说明" in detail["versions"][0]["iteration_summary"]
    assert (
        detail["versions"][0]["outputs"][0]["rights"]["redistribution_status"]
        == "unverified"
    )


def test_download_content_disposition_sanitizes_both_filenames_and_crlf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _artifact, artifact_id = _authorized_media_service(tmp_path)
    original_info = service.media_info

    def unsafe_name(selected: str):
        info = original_info(selected)
        info["name"] = "access_token=VerySecretValue.mp4\r\nX-Injected: yes"
        return info

    monkeypatch.setattr(service, "media_info", unsafe_name)
    response = TestClient(create_workbench_app(service)).head(
        f"/api/download/{artifact_id}"
    )
    header = response.headers["content-disposition"]

    assert response.status_code == 200
    assert header == "attachment; filename=\"download\"; filename*=UTF-8''download"
    assert "secret" not in header.lower()
    assert "injected" not in header.lower()


def test_catalog_snapshot_cannot_overwrite_newer_index_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = WorkbenchService(
        tmp_path,
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )
    old = WorkCatalog(works=(), warnings=("old",))
    new = WorkCatalog(works=(), warnings=("new",))
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0
    call_lock = threading.Lock()

    def sequenced_catalog():
        nonlocal calls
        with call_lock:
            calls += 1
            current = calls
        if current == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
            return old
        return new

    monkeypatch.setattr(service, "_work_catalog", sequenced_catalog)
    first = threading.Thread(target=service._catalog_snapshot)
    second = threading.Thread(target=service._catalog_snapshot)
    first.start()
    assert first_started.wait(timeout=2)
    second.start()
    time.sleep(0.02)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert calls == 2
    assert service._catalog_index_catalog == new


def test_download_route_sets_safe_attachment_headers_for_get_head_and_range(
    tmp_path: Path,
) -> None:
    service, artifact, artifact_id = _authorized_media_service(tmp_path)
    artifact.rename(artifact.with_name("预览 final.mp4"))
    package_path = tmp_path / "runs/episode_01/production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][0]["artifacts"] = [str(artifact.with_name("预览 final.mp4"))]
    package_path.write_text(json.dumps(package), encoding="utf-8")
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    api = TestClient(create_workbench_app(service))

    preview = api.get(f"/api/media/{artifact_id}")
    downloaded = api.get(f"/api/download/{artifact_id}")
    head = api.head(f"/api/download/{artifact_id}")
    partial = api.get(f"/api/download/{artifact_id}", headers={"Range": "bytes=2-5"})

    assert preview.headers.get("content-disposition") is None
    for response in (downloaded, head, partial):
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; filename=")
        assert "filename*=UTF-8''" in disposition
        assert "\r" not in disposition and "\n" not in disposition
    assert downloaded.content == b"0123456789"
    assert head.content == b""
    assert partial.status_code == 206
    assert partial.content == b"2345"


def test_settings_keep_public_defaults_when_no_provider_profile_exists(
    tmp_path: Path,
) -> None:
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "config/factory.config.json").read_text(
            encoding="utf-8"
        )
    )
    service = WorkbenchService(
        tmp_path,
        config=config,
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )

    payload = (
        TestClient(create_workbench_app(service)).get("/api/settings/providers").json()
    )

    assert payload["capabilities"] == {}
    assert [item["role_name"] for item in payload["defaults"]["voice_mapping"]] == [
        "黑白猫",
        "橘猫",
    ]
    assert payload["defaults"]["output"]["resolution"] == "1080x1920"
    assert payload["defaults"]["generation"] == {
        "concurrency": 1,
        "fee_cap_yuan": None,
    }


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


def test_media_stream_snapshots_once_and_head_uses_metadata_only(
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
    assert len(opened) == 1
    assert all(item.closed for item in opened)


def test_media_oversize_get_is_413_but_head_uses_no_snapshot(tmp_path: Path) -> None:
    service, _artifact, artifact_id = _authorized_media_service(
        tmp_path,
        max_media_bytes=4,
    )
    media_client = TestClient(create_workbench_app(service))

    head = media_client.head(f"/api/media/{artifact_id}")
    response = media_client.get(f"/api/media/{artifact_id}")

    assert head.status_code == 200
    assert head.headers["content-length"] == "10"
    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "media_too_large",
            "message": "Media exceeds the configured snapshot limit",
        }
    }
    assert service.media_snapshot_usage == {"active": 0, "bytes": 0}


@pytest.mark.parametrize(
    ("options", "expected_code"),
    (
        ({"max_media_snapshots": 1, "max_media_snapshot_bytes": 100}, "media_busy"),
        ({"max_media_snapshots": 2, "max_media_snapshot_bytes": 15}, "media_quota"),
    ),
)
def test_media_snapshot_admission_is_bounded_and_released(
    tmp_path: Path,
    options: dict[str, int],
    expected_code: str,
) -> None:
    service, _artifact, artifact_id = _authorized_media_service(
        tmp_path,
        max_media_bytes=100,
        **options,
    )
    first = service.open_media(artifact_id)
    media_client = TestClient(create_workbench_app(service))

    rejected = media_client.get(f"/api/media/{artifact_id}")

    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == expected_code
    assert service.media_snapshot_usage == {"active": 1, "bytes": 10}
    first.close()
    assert service.media_snapshot_usage == {"active": 0, "bytes": 0}
    assert media_client.get(f"/api/media/{artifact_id}").status_code == 200
    assert service.media_snapshot_usage == {"active": 0, "bytes": 0}


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
    assert service.media_snapshot_usage == {"active": 0, "bytes": 0}


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
