from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from factory.pipeline_jobs import JobManager
from factory.provider_profile import CapabilityConfig, ProviderProfile
from factory.video_preflight import consume_generation_token
from factory.workbench_api import run_workbench_api
from factory.workbench_service import WorkbenchService


REPO_ROOT = Path(__file__).resolve().parents[1]


def _offline_profile() -> ProviderProfile:
    local = CapabilityConfig(
        provider="local",
        model="deterministic-local",
        base_url="",
        api_key="",
        key_name="",
        key_source=None,
        ready=True,
    )
    video = CapabilityConfig(
        provider="minimax",
        model="MiniMax-H3",
        base_url="https://provider.example.invalid",
        api_key="FICTIONAL_E2E_SECRET_SENTINEL_DO_NOT_USE",
        key_name="FICTIONAL_E2E_KEY_NAME",
        key_source=None,
        ready=True,
    )
    return ProviderProfile(
        text=local,
        image=local,
        video=video,
        audio=local,
        source_paths={},
    )


def _offline_video_renderer(**kwargs: Any) -> dict[str, Any]:
    project_dir = Path(kwargs["project_dir"])
    request = kwargs["generation_request"]
    generation_token = str(kwargs["generation_token"])
    provider_tasks = dict(kwargs["provider_tasks"])
    persist = kwargs["provider_task_persisted"]
    if generation_token:
        consume_generation_token(project_dir, generation_token, request)
    if not provider_tasks:
        for shot_id in request.shot_ids:
            persist(shot_id, f"offline-{shot_id}", "submitted")
        return {
            "success": False,
            "failed_count": len(request.shot_ids),
            "errors": [{"error": "FICTIONAL_OFFLINE_RENDER_INTERRUPTION"}],
        }

    output = project_dir / "stages" / "video" / "offline-render.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "storymotion.e2e-offline-render.v1",
                "project_id": request.project_id,
                "shot_ids": list(request.shot_ids),
                "provider_tasks": sorted(provider_tasks),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "success": True,
        "completed_count": len(request.shot_ids),
        "output_path": str(output),
    }


def main() -> None:
    root = Path(
        os.environ.get(
            "STORYMOTION_E2E_ROOT",
            "/tmp/storymotion-studio-playwright-e2e",
        )
    ).resolve()
    if root.exists():
        shutil.rmtree(root)
    archive = root / "assets" / "workbench_archive"
    archive.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "assets" / "workbench_archive", archive)
    config = json.loads(
        (REPO_ROOT / "config" / "factory.config.json").read_text(encoding="utf-8")
    )
    service = WorkbenchService(
        root,
        archive_manifest=archive / "archive_manifest.json",
        config=config,
        frontend_origins=("http://127.0.0.1:4175",),
        job_manager=JobManager(root),
        provider_profile_loader=_offline_profile,
        video_renderer=_offline_video_renderer,
    )
    run_workbench_api(service, host="127.0.0.1", port=18788)


if __name__ == "__main__":
    main()
