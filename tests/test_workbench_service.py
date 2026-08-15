from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.pipeline_contracts import (
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageState,
)
from factory.pipeline_jobs import JobManager
from factory.pipeline_store import create_project, load_production_package
from factory.provider_profile import CapabilityConfig, ProviderProfile
from factory.workbench_service import WorkbenchService


def _provider_profile() -> ProviderProfile:
    capability = CapabilityConfig(
        provider="gateway",
        model="safe-model",
        base_url="https://provider.example/v1?token=secret",
        api_key="sk-secret-value",
        key_name="GATEWAY_API_KEY",
        key_source="/private/.env",
        ready=True,
        blockers=("sk-secret-value failed at /private/.env",),
        supports_reference_images=True,
    )
    return ProviderProfile(
        text=capability,
        image=capability,
        video=capability,
        audio=capability,
        source_paths={"factory": "/private/.env"},
    )


@pytest.fixture
def project_workspace(tmp_path: Path) -> tuple[Path, Path]:
    runs = tmp_path / "runs"
    project = runs / "episode_01"
    artifact = project / "stages" / "concept" / "preview.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"0123456789")
    spec = ProjectSpec(
        project_id="episode_01",
        title="Episode 01",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "A quiet test story."},
        output_dir=project / "output",
    )
    package = create_project(project, spec)
    first = replace(
        package.stages[0],
        state=StageState.PASSED,
        artifacts=(str(artifact),),
        revision=1,
        review_policy=ReviewPolicy.MANUAL,
        review_state=ReviewState.AWAITING_REVIEW,
        review_blocks_progress=True,
    )
    updated = package.with_stages((first, *package.stages[1:]))
    (project / "production_package.json").write_text(
        json.dumps(updated.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert load_production_package(project).project_id == "episode_01"
    return tmp_path, artifact


@pytest.fixture
def service(project_workspace: tuple[Path, Path]) -> WorkbenchService:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    return WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
    )


def test_project_detail_contains_execution_review_and_opaque_artifacts(
    service: WorkbenchService,
) -> None:
    payload = service.project_detail("episode_01")

    assert payload["stages"][0]["execution_state"] == "passed"
    assert payload["stages"][0]["review_state"] == "awaiting_review"
    artifact_id = payload["stages"][0]["artifacts"][0]["artifact_id"]
    assert artifact_id.startswith("art_")
    assert "/" not in artifact_id
    assert "/runs/" not in json.dumps(payload)


def test_artifact_ids_are_stable_and_only_registered_files_are_readable(
    service: WorkbenchService,
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, artifact = project_workspace
    first = service.project_detail("episode_01")["stages"][0]["artifacts"][0]
    restarted = WorkbenchService(
        workspace,
        job_manager=JobManager(workspace),
        provider_profile_loader=_provider_profile,
    )
    second = restarted.project_detail("episode_01")["stages"][0]["artifacts"][0]

    assert first["artifact_id"] == second["artifact_id"]
    assert service.read_media(first["artifact_id"], start=2, end=5)[1] == b"2345"
    artifact.write_bytes(b"changed")
    assert (
        restarted.project_detail("episode_01")["stages"][0]["artifacts"][0][
            "artifact_id"
        ]
        == first["artifact_id"]
    )
    with pytest.raises(KeyError):
        service.media_info("../../.env")


def test_provider_status_exposes_capabilities_without_credentials_or_urls(
    service: WorkbenchService,
) -> None:
    payload = service.provider_status()
    encoded = json.dumps(payload)

    assert payload["capabilities"]["video"]["model"] == "safe-model"
    assert payload["capabilities"]["video"]["ready"] is True
    assert "sk-" not in encoded
    assert "token=" not in encoded
    assert ".env" not in encoded
    assert "base_url" not in encoded
    assert "key_" not in encoded
    assert "/private/" not in encoded


def test_video_generation_wires_exact_confirmation_and_provider_persistence(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, artifact = project_workspace
    manager = JobManager(workspace)
    observed: dict[str, object] = {}

    def render_video(**kwargs):
        observed.update(kwargs)
        kwargs["provider_task_persisted"]("shot-1", "task-123", "submitted")
        return {"completed_count": 1}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    public_request = {
        "schema_version": "motion-comic-factory.video-generation-request.v1",
        "project_id": "episode_01",
        "project_sha256": "a" * 64,
        "package_sha256": "b" * 64,
        "revision_hashes": {},
        "artifact_hashes": {artifact_id: "c" * 64},
        "approval_hashes": {},
        "repair_plan_sha256": "",
        "shot_ids": ["shot-1"],
        "shots": [{"shot_id": "shot-1", "duration": 5, "resolution": "720p"}],
        "provider": "gateway",
        "model": "safe-model",
        "resolution": "720p",
        "output_seconds": 5,
        "estimated_cost_yuan": 1.0,
        "price_yuan_per_second": 0.2,
    }

    job = service.submit_video_generation(
        "episode_01",
        generation_token="token-id.secret",
        generation_request=public_request,
        test_mode=True,
    )

    assert observed["generation_token"] == "token-id.secret"
    internal_request = observed["generation_request"].to_dict()
    assert internal_request["artifact_hashes"] == {str(artifact.resolve()): "c" * 64}
    assert "/" not in next(iter(public_request["artifact_hashes"]))
    record = manager.get(job["job_id"])
    assert record.status == "completed"
    assert record.provider_tasks["shot-1"]["task_id"] == "task-123"
    assert "token-id.secret" not in json.dumps(record.to_dict())


def test_job_events_redact_token_shaped_text_and_raw_paths(
    service: WorkbenchService,
) -> None:
    job_id = service.jobs.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    service.jobs.append_event(
        job_id,
        "progress",
        {"message": "sk-secret-value failed at /private/workspace/file.json"},
    )

    encoded = json.dumps(service.job_events(job_id))

    assert "sk-secret-value" not in encoded
    assert "/private/" not in encoded


def test_failed_video_resume_reuses_provider_tasks_without_a_fresh_token(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    calls: list[dict[str, object]] = []

    def render_video(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["provider_task_persisted"]("shot-1", "task-123", "submitted")
            raise RuntimeError("interrupted after provider submission")
        return {"resumed_count": 1}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )
    request = {
        "schema_version": "motion-comic-factory.video-generation-request.v1",
        "project_id": "episode_01",
        "project_sha256": "a" * 64,
        "package_sha256": "b" * 64,
        "revision_hashes": {},
        "artifact_hashes": {},
        "approval_hashes": {},
        "repair_plan_sha256": "",
        "shot_ids": ["shot-1"],
        "shots": [{"shot_id": "shot-1", "duration": 5, "resolution": "720p"}],
        "provider": "gateway",
        "model": "safe-model",
        "resolution": "720p",
        "output_seconds": 5,
        "estimated_cost_yuan": 1.0,
        "price_yuan_per_second": 0.2,
    }
    submitted = service.submit_video_generation(
        "episode_01",
        generation_token="token-id.secret",
        generation_request=request,
        test_mode=True,
    )

    service.resume_job(submitted["job_id"])

    assert calls[1]["generation_token"] == ""
    assert calls[1]["provider_tasks"]["shot-1"]["task_id"] == "task-123"
    assert manager.get(submitted["job_id"]).status == "completed"


def test_job_artifacts_remain_authorized_after_service_restart(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, artifact = project_workspace
    manager = JobManager(workspace)

    def render_video(**_kwargs):
        return {"output_path": str(artifact)}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )
    request = {
        "schema_version": "motion-comic-factory.video-generation-request.v1",
        "project_id": "episode_01",
        "project_sha256": "a" * 64,
        "package_sha256": "b" * 64,
        "revision_hashes": {},
        "artifact_hashes": {},
        "approval_hashes": {},
        "repair_plan_sha256": "",
        "shot_ids": ["shot-1"],
        "shots": [{"shot_id": "shot-1", "duration": 5, "resolution": "720p"}],
        "provider": "gateway",
        "model": "safe-model",
        "resolution": "720p",
        "output_seconds": 5,
        "estimated_cost_yuan": 1.0,
        "price_yuan_per_second": 0.2,
    }
    submitted = service.submit_video_generation(
        "episode_01",
        generation_token="token-id.secret",
        generation_request=request,
        test_mode=True,
    )
    artifact_id = service.job_detail(submitted["job_id"])["result"]["output_path"]
    restarted = WorkbenchService(
        workspace,
        job_manager=JobManager(workspace),
        provider_profile_loader=_provider_profile,
    )

    assert artifact_id.startswith("art_")
    assert restarted.media_info(artifact_id)["size"] == len(artifact.read_bytes())


def test_non_original_project_creation_accepts_artifact_id_not_source_path(
    service: WorkbenchService,
) -> None:
    source_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    service._dispatch = lambda callback: callback()

    submitted = service.create_project_job(
        project_id="novel_01",
        title="Novel 01",
        mode="novel",
        idea="",
        source_artifact_id=source_id,
        target={},
        approval_preset="standard",
    )

    assert service.job_detail(submitted["job_id"])["status"] == "completed"
    detail = service.project_detail("novel_01")
    assert detail["mode"] == "novel"
    assert "/runs/" not in json.dumps(detail)
