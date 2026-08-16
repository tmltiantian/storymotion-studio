from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from factory import workbench_service as workbench_service_module
from factory.pipeline_contracts import (
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from factory.pipeline_jobs import JobManager, ProjectBusyError
from factory.pipeline_migration import migrate_existing_project
from factory.pipeline_review import approve_stage_revision, write_stage_revision
from factory.pipeline_store import (
    create_project,
    load_production_package,
    update_stage,
)
from factory.provider_profile import CapabilityConfig, ProviderProfile
from factory.video_preflight import VideoGenerationRequest, build_video_preflight
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


def _video_request(project_id: str = "episode_01") -> dict[str, object]:
    return {
        "schema_version": "motion-comic-factory.video-generation-request.v1",
        "project_id": project_id,
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


def _failed_video_job(
    manager: JobManager,
    *,
    request_project: str = "episode_01",
    with_provider_task: bool = True,
) -> str:
    job_id = manager.submit(
        project_id="episode_01",
        operation="video_generate",
        payload={"generation_request": _video_request(request_project)},
    )
    manager.start(job_id)
    if with_provider_task:
        manager.record_provider_task(
            job_id,
            shot_id="shot-1",
            provider="gateway",
            task_id="task-123",
            status="submitted",
        )
    manager.fail(job_id, error="interrupted")
    return job_id


def _ready_video_workspace(
    tmp_path: Path,
    *,
    project_id: str = "episode_ready",
) -> tuple[Path, Path]:
    workspace = tmp_path
    project = workspace / "runs" / project_id
    create_project(
        project,
        ProjectSpec(
            project_id=project_id,
            title="Ready video project",
            mode=ProjectMode.ORIGINAL,
            input={"kind": "idea", "text": "A canonical recovery fixture"},
            output_dir=project / "output",
            target={
                "video_resolution": "768P",
                "video_price_yuan_per_second": 0.5,
            },
            providers={
                "video_provider": "minimax",
                "video_model": "MiniMax-H3",
            },
        ),
    )
    storyboard_payload = {
        "project_id": project_id,
        "title": "Ready video project",
        "language": "zh-CN",
        "style": "motion comic",
        "target_aspect_ratio": "9:16",
        "target_resolution": "768x1365",
        "characters": [],
        "shots": [
            {
                "id": "shot-1",
                "index": 1,
                "scene_title": "一",
                "action": "等待",
                "visual_prompt": "室内",
                "camera": "locked",
                "duration_seconds": 5,
                "audio_mood": "quiet",
                "dialogue": [],
            },
            {
                "id": "shot-2",
                "index": 2,
                "scene_title": "二",
                "action": "回应",
                "visual_prompt": "室内",
                "camera": "medium",
                "duration_seconds": 4,
                "audio_mood": "warm",
                "dialogue": [],
            },
        ],
    }
    payloads = {
        StageName.SCRIPT: {"script": "ready"},
        StageName.STORYBOARD: storyboard_payload,
        StageName.ASSETS: {"production_ready": True},
        StageName.AUDIO: {"voiceover_audio": "ready.wav"},
    }
    evidence = project / "approval-evidence.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")
    for stage, payload in payloads.items():
        artifact = project / "stages" / stage.value / (
            "episode.json" if stage is StageName.STORYBOARD else f"{stage.value}.json"
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        revision = write_stage_revision(
            project,
            stage,
            (artifact,),
            f"sig-{stage.value}",
            "fixture",
        )
        approve_stage_revision(
            project,
            stage,
            revision.number,
            f"approve {stage.value}",
            (evidence,),
        )
        update_stage(
            project,
            stage,
            StageState.PASSED,
            executor="fixture",
            input_signature=f"sig-{stage.value}",
            artifacts=(artifact,),
            revision=revision.number,
            review_policy=ReviewPolicy.MANUAL,
            review_state=ReviewState.APPROVED,
        )
    return workspace, project


def _canonical_video_request(
    project: Path,
    shot_ids: tuple[str, ...] = ("shot-1",),
) -> VideoGenerationRequest:
    return VideoGenerationRequest.from_preflight(
        build_video_preflight(project, shot_ids)
    )


def _canonical_failed_video_job(
    manager: JobManager,
    project: Path,
    *,
    with_provider_tasks: bool,
    shot_ids: tuple[str, ...] = ("shot-1",),
) -> tuple[str, VideoGenerationRequest]:
    request = _canonical_video_request(project, shot_ids)
    job_id = manager.submit(
        project_id=request.project_id,
        operation="video_generate",
        payload={"generation_request": request.to_dict()},
    )
    manager.start(job_id)
    if with_provider_tasks:
        for shot_id in request.shot_ids:
            manager.record_provider_task(
                job_id,
                shot_id=shot_id,
                provider=request.provider,
                task_id=f"task-{shot_id}",
                status="submitted",
            )
    manager.fail(job_id, error="interrupted")
    return job_id, request


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


def test_artifact_viewer_projection_uses_registered_audio_manifest_without_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "runs" / "episode_audio"
    audio_dir = project / "stages" / "audio"
    audio_dir.mkdir(parents=True)
    audio = audio_dir / "voiceover.m4a"
    audio.write_bytes(b"safe-audio")
    manifest = audio_dir / "audio_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.audio.v1",
                "voiceover_audio": str(audio.resolve()),
                "timings": [
                    {
                        "shot_id": "shot_02",
                        "speaker_name": "黑白猫",
                        "start_seconds": 4.2,
                        "end_seconds": 6.1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    spec = ProjectSpec(
        project_id="episode_audio",
        title="Audio Episode",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "Audio viewer"},
        output_dir=project / "output",
    )
    package = create_project(project, spec)
    audio_stage = next(item for item in package.stages if item.stage.value == "audio")
    updated_audio = replace(
        audio_stage,
        state=StageState.PASSED,
        artifacts=(str(manifest), str(audio)),
    )
    stages = tuple(updated_audio if item.stage.value == "audio" else item for item in package.stages)
    (project / "production_package.json").write_text(
        json.dumps(package.with_stages(stages).to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    service = WorkbenchService(
        tmp_path,
        job_manager=JobManager(tmp_path),
        provider_profile_loader=lambda: None,
    )

    detail = service.stage_detail("episode_audio", "audio")
    descriptor = next(item for item in detail["artifacts"] if item["name"] == "voiceover.m4a")
    encoded = json.dumps(detail, ensure_ascii=False)

    assert descriptor["kind"] == "audio"
    assert descriptor["viewer"]["size_bytes"] == len(b"safe-audio")
    assert descriptor["viewer"]["dialogues"] == [
        {
            "dialogue_id": "shot_02:0",
            "speaker": "黑白猫",
            "start_seconds": 4.2,
            "end_seconds": 6.1,
        }
    ]
    assert str(tmp_path) not in encoded
    assert "voiceover_audio" not in encoded


def test_video_workspace_and_dialogues_use_registered_authoritative_contracts(
    tmp_path: Path,
) -> None:
    project = tmp_path / "runs" / "episode_video"
    storyboard = project / "stages" / "storyboard"
    audio_dir = project / "stages" / "audio"
    video_dir = project / "stages" / "video"
    for directory in (storyboard, audio_dir, video_dir):
        directory.mkdir(parents=True)
    episode = storyboard / "episode.json"
    episode.write_text(
        json.dumps(
            {
                "project_id": "episode_video",
                "title": "Video Episode",
                "language": "zh-CN",
                "style": "motion comic",
                "target_aspect_ratio": "9:16",
                "target_resolution": "1080x1920",
                "characters": [],
                "shots": [
                    {
                        "id": "shot_01",
                        "index": 1,
                        "scene_title": "一",
                        "action": "等待",
                        "visual_prompt": "室内",
                        "camera": "locked",
                        "duration_seconds": 4.0,
                        "audio_mood": "quiet",
                        "dialogue": [],
                    },
                    {
                        "id": "shot_02",
                        "index": 2,
                        "scene_title": "二",
                        "action": "回应",
                        "visual_prompt": "室内",
                        "camera": "medium",
                        "duration_seconds": 5.0,
                        "audio_mood": "warm",
                        "dialogue": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    voiceover = audio_dir / "voiceover.m4a"
    voiceover.write_bytes(b"voice")
    audio_manifest = audio_dir / "audio_manifest.json"
    audio_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.audio.v1",
                "project_id": "episode_video",
                "voiceover_audio": str(voiceover.resolve()),
                "timings": [
                    {
                        "shot_id": "shot_02",
                        "speaker_name": "旁白",
                        "start_seconds": 4.5,
                        "end_seconds": 5.75,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    clip = video_dir / "shot_02.mp4"
    clip.write_bytes(b"video")
    video_manifest = video_dir / "video_manifest.json"
    video_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video.v1",
                "project_id": "episode_video",
                "clip_by_shot": {"shot_02": str(clip.resolve())},
            }
        ),
        encoding="utf-8",
    )
    spec = ProjectSpec(
        project_id="episode_video",
        title="Video Episode",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "Video viewer"},
        output_dir=project / "output",
        target={"fps": 25, "resolution": "1080x1920"},
    )
    package = create_project(project, spec)
    replacements = {
        "storyboard": (episode,),
        "audio": (audio_manifest, voiceover),
        "video": (video_manifest, clip),
    }
    stages = tuple(
        replace(item, state=StageState.PASSED, artifacts=replacements[item.stage.value])
        if item.stage.value in replacements
        else item
        for item in package.stages
    )
    (project / "production_package.json").write_text(
        json.dumps(package.with_stages(stages).to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    manager = JobManager(tmp_path)
    old_job = manager.submit(
        project_id="episode_video",
        operation="video_test",
        payload={"generation_request": _video_request("episode_video")},
    )
    manager.start(old_job)
    manager.complete(old_job, result={})
    latest_job = manager.submit(
        project_id="episode_video",
        operation="video_generate",
        payload={"generation_request": _video_request("episode_video")},
    )
    service = WorkbenchService(
        tmp_path,
        job_manager=manager,
        provider_profile_loader=lambda: None,
    )

    workspace = service.video_workspace("episode_video")
    detail = service.stage_detail("episode_video", "video")
    descriptor = next(item for item in detail["artifacts"] if item["name"] == "shot_02.mp4")
    encoded = json.dumps({"workspace": workspace, "detail": detail}, ensure_ascii=False)

    assert workspace == {
        "schema_version": "motion-comic-factory.video-workspace.v1",
        "project_id": "episode_video",
        "shots": [
            {"shot_id": "shot_01", "duration_seconds": 4.0},
            {"shot_id": "shot_02", "duration_seconds": 5.0},
        ],
        "selected_shot_ids": ["shot_01", "shot_02"],
        "job": service.job_detail(latest_job),
        "failed_job_recovery": None,
    }
    assert descriptor["viewer"] == {
        "size_bytes": len(b"video"),
        "fps": 25,
        "width": 1080,
        "height": 1920,
        "shot_id": "shot_02",
        "dialogues": [
            {
                "dialogue_id": "shot_02:0",
                "speaker": "旁白",
                "start_seconds": 0.5,
                "end_seconds": 1.75,
            }
        ],
    }
    assert str(tmp_path) not in encoded
    assert "generation_request" not in encoded

    video_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video.v1",
                "project_id": "episode_video",
                "clip_by_shot": {
                    "shot_01": str(clip.resolve()),
                    "shot_02": str(clip.resolve()),
                },
            }
        ),
        encoding="utf-8",
    )
    duplicate = service.stage_detail("episode_video", "video")
    duplicate_descriptor = next(
        item for item in duplicate["artifacts"] if item["name"] == "shot_02.mp4"
    )
    assert "shot_id" not in duplicate_descriptor["viewer"]
    assert "dialogues" not in duplicate_descriptor["viewer"]

    video_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video.v1",
                "project_id": "episode_video",
                "clip_by_shot": {"unknown-shot": str(clip.resolve())},
            }
        ),
        encoding="utf-8",
    )
    unknown = service.stage_detail("episode_video", "video")
    unknown_descriptor = next(
        item for item in unknown["artifacts"] if item["name"] == "shot_02.mp4"
    )
    assert "shot_id" not in unknown_descriptor["viewer"]
    assert "dialogues" not in unknown_descriptor["viewer"]

    video_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.video.v1",
                "project_id": "another_project",
                "clip_by_shot": {"shot_02": str(clip.resolve())},
            }
        ),
        encoding="utf-8",
    )
    changed = service.stage_detail("episode_video", "video")
    changed_descriptor = next(
        item for item in changed["artifacts"] if item["name"] == "shot_02.mp4"
    )
    assert "shot_id" not in changed_descriptor["viewer"]
    assert "dialogues" not in changed_descriptor["viewer"]


def test_failed_video_workspace_requires_new_confirmation_before_submit(
    tmp_path: Path,
) -> None:
    workspace, project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    job_id, _request = _canonical_failed_video_job(
        manager,
        project,
        with_provider_tasks=False,
    )
    calls: list[dict[str, object]] = []
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        video_renderer=lambda **kwargs: calls.append(kwargs) or {"success": True},
        dispatch=lambda callback: callback(),
    )

    payload = service.video_workspace("episode_ready")

    assert payload["failed_job_recovery"] == {
        "mode": "new_submission_required",
        "shot_ids": ["shot-1"],
    }
    assert payload["selected_shot_ids"] == ["shot-1"]
    with pytest.raises(ValueError, match="fresh confirmation"):
        service.resume_job(job_id)
    assert calls == []
    assert manager.get(job_id).status == "failed"


def test_failed_video_workspace_marks_changed_revision_historical(
    tmp_path: Path,
) -> None:
    workspace, project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    job_id, _request = _canonical_failed_video_job(
        manager,
        project,
        with_provider_tasks=True,
    )
    script = project / "stages" / "script" / "script.json"
    script.write_text('{"script":"current revision changed"}', encoding="utf-8")
    old_output = project / "stages" / "video" / "old-output.mp4"
    old_output.parent.mkdir(parents=True, exist_ok=True)
    old_output.write_bytes(b"old output")
    calls: list[dict[str, object]] = []
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        video_renderer=lambda **kwargs: calls.append(kwargs) or {
            "output_path": str(old_output)
        },
        dispatch=lambda callback: callback(),
    )

    payload = service.video_workspace("episode_ready")

    assert payload["failed_job_recovery"] == {
        "mode": "historical",
        "shot_ids": [],
    }
    assert payload["selected_shot_ids"] == ["shot-1", "shot-2"]
    with pytest.raises(ValueError, match="current project revision"):
        service.resume_job(job_id)
    assert calls == []
    assert all(
        artifact["name"] != "old-output.mp4"
        for artifact in service.stage_detail("episode_ready", "video")["artifacts"]
    )


def test_failed_video_workspace_allows_same_revision_provider_task_poll(
    tmp_path: Path,
) -> None:
    workspace, project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    job_id, request = _canonical_failed_video_job(
        manager,
        project,
        with_provider_tasks=True,
    )
    calls: list[dict[str, object]] = []
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        video_renderer=lambda **kwargs: calls.append(kwargs) or {"success": True},
        dispatch=lambda callback: callback(),
    )

    payload = service.video_workspace("episode_ready")

    assert payload["failed_job_recovery"] == {
        "mode": "poll_only",
        "shot_ids": ["shot-1"],
    }
    service.resume_job(job_id)
    assert calls[0]["generation_token"] == ""
    assert calls[0]["generation_request"] == request
    assert calls[0]["provider_tasks"]["shot-1"]["task_id"] == "task-shot-1"
    assert manager.get(job_id).status == "completed"


def test_job_detail_includes_persisted_last_event_sequence(
    service: WorkbenchService,
) -> None:
    job_id = service.jobs.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    service.jobs.start(job_id)

    detail = service.job_detail(job_id)

    assert detail["last_event_sequence"] == 2


def test_impact_preview_exposes_path_free_authoritative_summary(
    service: WorkbenchService,
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    storyboard = workspace / "runs" / "episode_01" / "stages" / "storyboard"
    storyboard.mkdir(parents=True, exist_ok=True)
    (storyboard / "episode.json").write_text(
        json.dumps(
            {
                "shots": [
                    {"id": "shot_01", "index": 1, "dialogue": []},
                    {"id": "shot_02", "index": 2, "dialogue": []},
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = service.preview_impact(
        "episode_01",
        {
            "stage": "edit",
            "scope": "subtitle_style",
            "dialogue_ids": [],
            "character_ids": [],
            "shot_ids": [],
            "subtitle_style": True,
        },
    )

    assert payload["summary"] == {
        "schema_version": "motion-comic-factory.impact-summary.v2",
        "regenerated_video_shot_count": 0,
        "reused_video_shot_count": 2,
        "regenerated_audio_item_count": 0,
        "affected_stages": ["edit", "eval", "deliver"],
        "estimate": {"available": False},
    }
    assert payload["entries"] == [
        {"stage": "edit", "item_count": 1},
        {"stage": "eval", "item_count": 1},
        {"stage": "deliver", "item_count": 1},
    ]
    assert payload["preserved_artifacts"] == []
    assert str(workspace) not in json.dumps(payload)

    scoped = service.preview_impact(
        "episode_01",
        {
            "stage": "storyboard",
            "scope": "shot",
            "dialogue_ids": [],
            "character_ids": [],
            "shot_ids": ["shot_01"],
            "subtitle_style": False,
        },
    )
    assert scoped["request"]["selection_counts"]["shot"] == 1
    assert "shot_01" not in json.dumps(scoped)


def test_subtitle_preview_never_exposes_path_shaped_migrated_shot_id(
    service: WorkbenchService,
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    raw_shot_id = "/private/legacy/storyboards/shot_01.png"
    legacy = workspace / "legacy-source"
    legacy.mkdir()
    (legacy / "episode.json").write_text(
        json.dumps(
            {
                "project_id": "legacy-impact",
                "title": "Legacy Impact",
                "shots": [
                    {"id": raw_shot_id, "index": 1, "dialogue": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    migrate_existing_project(
        legacy,
        workspace / "runs" / "legacy-impact",
        project_id="legacy-impact",
        title="Legacy Impact",
        mode=ProjectMode.NOVEL,
    )

    payload = service.preview_impact(
        "legacy-impact",
        {
            "stage": "edit",
            "scope": "subtitle_style",
            "dialogue_ids": [],
            "character_ids": [],
            "shot_ids": [],
            "subtitle_style": True,
        },
    )
    encoded = json.dumps(payload)

    assert payload["summary"]["reused_video_shot_count"] == 1
    assert raw_shot_id not in encoded
    assert "/private/" not in encoded


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
    tmp_path: Path,
) -> None:
    workspace, _project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    calls: list[dict[str, object]] = []

    def render_video(**kwargs):
        calls.append(kwargs)
        return {"resumed_count": 1}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )
    job_id, _request = _canonical_failed_video_job(
        manager,
        _project,
        with_provider_tasks=True,
    )

    service.resume_job(job_id)

    assert calls[0]["generation_token"] == ""
    assert calls[0]["provider_tasks"]["shot-1"]["task_id"] == "task-shot-1"
    assert manager.get(job_id).status == "completed"


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


@pytest.mark.parametrize("stage", ("video", "edit", "eval", "deliver"))
def test_live_stage_traversal_cannot_reach_video_without_confirmation(
    service: WorkbenchService,
    stage: str,
) -> None:
    with pytest.raises(ValueError, match="confirmed video"):
        service.submit_stage_run("episode_01", stage, enable_live=True)


def test_failed_provider_result_is_resumable_and_keeps_provider_task(
    tmp_path: Path,
) -> None:
    workspace, _project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    attempts = 0

    def render_video(**kwargs):
        nonlocal attempts
        attempts += 1
        return {"success": True, "completed_count": 1}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )

    request = _canonical_video_request(_project)
    job_id = manager.submit(
        project_id=request.project_id,
        operation="video_generate",
        payload={"generation_request": request.to_dict()},
    )
    manager.start(job_id)
    manager.record_provider_task(
        job_id,
        shot_id="shot-1",
        provider=request.provider,
        task_id="task-123",
        status="submitted",
    )
    manager.fail(
        job_id,
        error="sk-secret-value failed at /private/provider/result.json",
        result={"success": False, "failed_count": 1},
    )
    failed = manager.get(job_id)

    assert failed.status == "failed"
    assert failed.result["success"] is False
    assert failed.provider_tasks["shot-1"]["task_id"] == "task-123"
    assert "sk-secret-value" not in json.dumps(service.job_detail(failed.job_id))
    assert "/private/" not in json.dumps(service.job_detail(failed.job_id))

    service.resume_job(failed.job_id)

    assert manager.get(failed.job_id).status == "completed"
    assert attempts == 1


def test_invalid_resume_does_not_mutate_or_reclaim_failed_job(
    service: WorkbenchService,
) -> None:
    job_id = service.jobs.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    service.jobs.fail(job_id, error="stage failed")
    record_path = service.jobs.jobs_dir / f"{job_id}.json"
    before = record_path.read_bytes()

    with pytest.raises(ValueError, match="provider jobs"):
        service.resume_job(job_id)

    assert record_path.read_bytes() == before
    assert service.jobs.get(job_id).status == "failed"
    next_job = service.jobs.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    assert next_job != job_id


def test_resume_does_not_dispatch_duplicate_for_live_worker(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    pending: list[object] = []
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=lambda **_kwargs: {"success": True},
        dispatch=pending.append,
    )
    submitted = service.submit_video_generation(
        "episode_01",
        generation_token="token-id.secret",
        generation_request=_video_request(),
        test_mode=True,
    )
    manager.start(submitted["job_id"])
    manager.record_provider_task(
        submitted["job_id"],
        shot_id="shot-1",
        provider="gateway",
        task_id="task-123",
        status="submitted",
    )

    with pytest.raises(RuntimeError, match="active worker"):
        service.resume_job(submitted["job_id"])

    assert len(pending) == 1
    assert manager.get(submitted["job_id"]).status == "running"


def test_cross_process_worker_lease_blocks_duplicate_and_releases_on_sigkill(
    tmp_path: Path,
) -> None:
    workspace, project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    job_id, _request = _canonical_failed_video_job(
        manager,
        project,
        with_provider_tasks=True,
    )
    ready = workspace / "child-worker-ready"
    script = """
import sys
import time
from pathlib import Path

from factory.pipeline_jobs import JobManager
from factory.workbench_service import WorkbenchService

workspace, job_id, ready = sys.argv[1], sys.argv[2], Path(sys.argv[3])
pending = []
service = WorkbenchService(
    workspace,
    job_manager=JobManager(workspace),
    provider_profile_loader=lambda: None,
    video_renderer=lambda **_kwargs: {"success": True},
    dispatch=pending.append,
)
service.resume_job(job_id)
ready.write_text(str(len(pending)), encoding="utf-8")
while True:
    time.sleep(1)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(workspace), job_id, str(ready)],
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pending: list[object] = []
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=lambda **_kwargs: {"success": True},
        dispatch=pending.append,
    )
    blocked = False
    try:
        deadline = time.monotonic() + 10
        while (
            not ready.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if not ready.exists():
            _stdout, stderr = child.communicate(timeout=1)
            pytest.fail(f"child worker did not start: {stderr}")
        assert ready.read_text(encoding="utf-8") == "1"

        try:
            service.resume_job(job_id)
        except RuntimeError as exc:
            blocked = "active worker" in str(exc)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)

    if pending:
        pending.pop()()
    assert blocked is True
    assert pending == []

    service.resume_job(job_id)
    assert len(pending) == 1
    pending.pop()()
    assert manager.get(job_id).status == "completed"


@pytest.mark.parametrize("outcome", ("completed", "failed", "base_exception"))
def test_worker_lease_releases_for_every_worker_exit(
    project_workspace: tuple[Path, Path],
    outcome: str,
) -> None:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    job_id = manager.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        dispatch=lambda callback: callback(),
    )

    def operation():
        if outcome == "failed":
            raise RuntimeError("operation failed")
        if outcome == "base_exception":
            raise KeyboardInterrupt("operation interrupted")
        return {"success": True}

    if outcome == "base_exception":
        with pytest.raises(KeyboardInterrupt):
            service._execute_job(job_id, operation)
    else:
        service._execute_job(job_id, operation)

    lease = JobManager(workspace).acquire_worker_lease(job_id)
    lease.release()


def test_worker_lease_releases_when_dispatch_fails(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    job_id = manager.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )

    def fail_dispatch(_callback):
        raise SystemExit("dispatcher stopped")

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        dispatch=fail_dispatch,
    )

    with pytest.raises(SystemExit):
        service._execute_job(job_id, lambda: {"success": True})

    lease = JobManager(workspace).acquire_worker_lease(job_id)
    lease.release()


def test_resume_without_provider_task_requires_a_fresh_confirmation(
    tmp_path: Path,
) -> None:
    workspace, project = _ready_video_workspace(tmp_path)
    manager = JobManager(workspace)
    job_id, _request = _canonical_failed_video_job(
        manager,
        project,
        with_provider_tasks=False,
    )
    calls: list[dict[str, object]] = []

    def render_video(**kwargs):
        calls.append(kwargs)
        return {"success": True, "resumed_count": 1}

    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        video_renderer=render_video,
        dispatch=lambda callback: callback(),
    )

    with pytest.raises(ValueError, match="fresh confirmation"):
        service.resume_job(job_id)

    assert calls == []
    assert manager.get(job_id).status == "failed"


def test_resume_validates_project_ownership_before_mutation(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    manager = JobManager(workspace)
    job_id = _failed_video_job(manager, request_project="other_project")
    record_path = manager.jobs_dir / f"{job_id}.json"
    before = record_path.read_bytes()
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=_provider_profile,
        dispatch=lambda callback: callback(),
    )

    with pytest.raises(ValueError, match="Stored generation"):
        service.resume_job(job_id)

    assert record_path.read_bytes() == before
    assert manager.get(job_id).status == "failed"


def test_review_and_repair_mutations_respect_persistent_project_owner(
    service: WorkbenchService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_id = service.project_detail("episode_01")["stages"][0]["artifacts"][0][
        "artifact_id"
    ]
    service.jobs.submit(
        project_id="episode_01",
        operation="run_stage",
        payload={},
    )
    called: list[str] = []
    monkeypatch.setattr(
        workbench_service_module,
        "approve_stage",
        lambda *_args, **_kwargs: called.append("approve"),
    )
    monkeypatch.setattr(
        workbench_service_module,
        "request_stage_changes",
        lambda *_args, **_kwargs: called.append("changes"),
    )
    monkeypatch.setattr(
        workbench_service_module,
        "apply_impact_plan",
        lambda *_args, **_kwargs: called.append("impact"),
    )

    operations = (
        lambda: service.approve_stage(
            "episode_01",
            "concept",
            revision=1,
            note="approved",
            evidence_artifact_ids=[artifact_id],
        ),
        lambda: service.request_stage_changes(
            "episode_01",
            "concept",
            revision=1,
            reason="revise",
        ),
        lambda: service.apply_impact("episode_01", "a" * 64),
    )
    for operation in operations:
        with pytest.raises(ProjectBusyError):
            operation()

    assert called == []


@pytest.mark.parametrize("fatal_error", (KeyboardInterrupt, SystemExit))
def test_reservation_baseexception_releases_project_and_reraises(
    service: WorkbenchService,
    fatal_error: type[BaseException],
) -> None:
    def interrupt():
        raise fatal_error("mutation interrupted")

    with pytest.raises(fatal_error, match="mutation interrupted"):
        service._reserved_mutation("episode_01", "request_changes", interrupt)

    assert (
        service._reserved_mutation(
            "episode_01",
            "request_changes",
            lambda: "next mutation",
        )
        == "next mutation"
    )


def test_reservation_fallback_releases_project_when_failure_persistence_errors(
    service: WorkbenchService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_calls = 0

    def interrupted_failure_persistence(*_args, **_kwargs):
        nonlocal fail_calls
        fail_calls += 1
        raise OSError("failure persistence interrupted")

    monkeypatch.setattr(service.jobs, "fail", interrupted_failure_persistence)

    def interrupt():
        raise KeyboardInterrupt("mutation interrupted")

    with pytest.raises(KeyboardInterrupt, match="mutation interrupted"):
        service._reserved_mutation("episode_01", "apply_impact", interrupt)

    assert fail_calls == 1
    assert (
        service._reserved_mutation(
            "episode_01",
            "request_changes",
            lambda: "next mutation",
        )
        == "next mutation"
    )


def test_exact_loaded_secrets_are_redacted_from_status_and_errors(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    secret = "opaque-value-12345"
    credential_url = "https://user:password@provider.example/v1"
    capability = CapabilityConfig(
        provider=secret,
        model=secret,
        base_url=credential_url,
        api_key=secret,
        key_name="GATEWAY_API_KEY",
        key_source=None,
        ready=False,
        blockers=(f"{secret} rejected by {credential_url}",),
    )
    profile = ProviderProfile(
        text=capability,
        image=capability,
        video=capability,
        audio=capability,
        source_paths={},
    )
    service = WorkbenchService(
        workspace,
        job_manager=JobManager(workspace),
        provider_profile_loader=lambda: profile,
    )

    encoded = json.dumps(service.provider_status())
    public_error = service.public_error_message(
        RuntimeError(f"Provider {secret} failed via {credential_url}")
    )

    assert secret not in encoded
    assert credential_url not in encoded
    assert secret not in public_error
    assert credential_url not in public_error


def test_public_payloads_sanitize_mapping_keys_recursively(
    project_workspace: tuple[Path, Path],
) -> None:
    workspace, _artifact = project_workspace
    secret = "opaque-value-12345"
    capability = CapabilityConfig(
        provider="gateway",
        model="safe-model",
        base_url="https://provider.example/v1",
        api_key=secret,
        key_name="GATEWAY_API_KEY",
        key_source=None,
        ready=True,
    )
    profile = ProviderProfile(
        text=capability,
        image=capability,
        video=capability,
        audio=capability,
        source_paths={},
    )
    project_file = workspace / "runs/episode_01/project.json"
    project_payload = json.loads(project_file.read_text(encoding="utf-8"))
    project_payload["target"] = {"/private/project-key": {secret: "project-value"}}
    project_file.write_text(json.dumps(project_payload), encoding="utf-8")
    manager = JobManager(workspace)
    job_id = manager.submit(
        project_id="episode_01",
        operation="video_generate",
        payload={},
    )
    manager.append_event(
        job_id,
        "progress",
        {"nested": {"/private/event-key": {secret: "event-value"}}},
    )
    manager.complete(
        job_id,
        result={"nested": {"/private/result-key": {secret: "result-value"}}},
    )
    service = WorkbenchService(
        workspace,
        job_manager=manager,
        provider_profile_loader=lambda: profile,
    )

    encoded = json.dumps(
        {
            "project": service.project_detail("episode_01"),
            "job": service.job_detail(job_id),
            "events": service.job_events(job_id),
        }
    )

    assert "/private/" not in encoded
    assert secret not in encoded
