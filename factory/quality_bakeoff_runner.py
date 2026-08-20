from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .gateway_image import GatewayImageConfig
from .gateway_video import GatewayVideoConfig
from .micro_still_batch import (
    PRODUCTION_STILL_MODELS,
    render_micro_still_batch,
)
from .micro_video_batch import (
    PRODUCTION_VIDEO_MODELS,
    build_micro_video_jobs,
    render_micro_video_batch,
)
from .provider_profile import resolve_provider_profile
from .quality_runner_common import _count, _live_blockers, _write_atomic_json
from .schema import episode_from_dict
from .visual_timeline import visual_timeline_from_dict


QUALITY_BAKEOFF_CANDIDATES_SCHEMA = (
    "motion-comic-factory.quality-bakeoff-candidates.v1"
)


class QualityBakeoffRunnerError(RuntimeError):
    pass


def run_quality_bakeoff_candidates(
    config: Mapping[str, Any],
    project_id: str,
    *,
    candidate_number: int = 1,
    kind: str = "all",
    micro_shot_ids: Sequence[str] | None = None,
    allow_network: bool = False,
    overwrite: bool = False,
    timeout_seconds: float = 120.0,
    submit_timeout_seconds: float = 300.0,
    download_timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: float = 900.0,
    profile_resolver: Callable[[Mapping[str, Any]], Any] = resolve_provider_profile,
    video_renderer: Callable[..., dict[str, Any]] = render_micro_video_batch,
    still_renderer: Callable[..., dict[str, Any]] = render_micro_still_batch,
) -> dict[str, Any]:
    project = _project_id(project_id)
    if kind not in {"all", "video", "still"}:
        raise QualityBakeoffRunnerError("Bakeoff kind must be all, video, or still.")
    if (
        isinstance(candidate_number, bool)
        or not isinstance(candidate_number, int)
        or not 1 <= candidate_number <= 3
    ):
        raise QualityBakeoffRunnerError(
            "Bakeoff candidate number must be between 1 and 3."
        )

    run_dir = _run_dir(config, project)
    episode = episode_from_dict(_read_json(run_dir / "episode.json"))
    timeline = visual_timeline_from_dict(
        _read_json(run_dir / "visual_timeline.json")
    )
    character_assets = _read_json(run_dir / "character_assets.json")
    plan = _validated_plan(
        _read_json(run_dir / "model_bakeoff_plan.json"),
        project,
        run_dir,
    )
    profile = profile_resolver(config)
    selected_video = list(plan["video_models"]) if kind in {"all", "video"} else []
    selected_still = (
        list(plan["still_models"])
        if kind in {"all", "still"} and plan["requires_still"]
        else []
    )
    representative_ids = list(plan["representative_character_micro_shot_ids"])
    targeted_video_ids = _targeted_video_ids(
        micro_shot_ids,
        representative_ids,
        video_selected=bool(selected_video),
    )
    report_path = run_dir / "model_bakeoff_candidates.json"
    report: dict[str, Any] = {
        "schema_version": QUALITY_BAKEOFF_CANDIDATES_SCHEMA,
        "project_id": project,
        "run_dir": str(run_dir),
        "candidate_number": candidate_number,
        "kind": kind,
        "micro_shot_ids": targeted_video_ids,
        "allow_network": allow_network,
        "overwrite": overwrite,
        "plan_ready": True,
        "executed": False,
        "success": False,
        "planned_count": 0,
        "completed_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
        "blocked_reasons": [],
        "video_runs": [],
        "still_runs": [],
    }

    blockers = _live_blockers(
        profile,
        require_video=bool(selected_video),
        require_image=bool(selected_still),
    )
    if allow_network and blockers:
        report["blocked_reasons"] = blockers
        _write_atomic_json(report_path, report)
        return report

    video_config = GatewayVideoConfig(
        api_key=str(profile.video.api_key),
        base_url=str(profile.video.base_url),
        model=selected_video[0] if selected_video else str(profile.video.model),
        timeout_seconds=timeout_seconds,
        submit_timeout_seconds=submit_timeout_seconds,
        download_timeout_seconds=download_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )
    image_config = GatewayImageConfig(
        api_key=str(profile.image.api_key),
        base_url=str(profile.image.base_url),
        model=selected_still[0] if selected_still else str(profile.image.model),
        timeout_seconds=timeout_seconds,
        download_timeout_seconds=download_timeout_seconds,
    )

    for model in selected_video:
        jobs = build_micro_video_jobs(
            episode,
            timeline,
            character_assets,
            model=model,
            run_dir=run_dir,
            candidate_number=candidate_number,
            micro_shot_ids=targeted_video_ids,
        )
        result = video_renderer(
            jobs,
            run_dir,
            replace(video_config, model=model),
            allow_network=allow_network,
            overwrite=overwrite,
        )
        report["video_runs"].append(
            _run_summary(result, model, api_key=str(profile.video.api_key))
        )

    for model in selected_still:
        result = still_renderer(
            episode,
            timeline,
            model=model,
            run_dir=run_dir,
            candidate_number=candidate_number,
            micro_shot_ids=[plan["still_micro_shot_id"]],
            config=replace(image_config, model=model),
            allow_network=allow_network,
            overwrite=overwrite,
        )
        report["still_runs"].append(
            _run_summary(result, model, api_key=str(profile.image.api_key))
        )

    runs = [*report["video_runs"], *report["still_runs"]]
    for key in (
        "planned_count",
        "completed_count",
        "skipped_count",
        "failed_count",
        "blocked_count",
    ):
        report[key] = sum(int(item.get(key) or 0) for item in runs)
    report["executed"] = any(bool(item.get("executed")) for item in runs)
    report["success"] = bool(runs) and (
        all(bool(item.get("success")) for item in runs)
        if allow_network
        else all(bool(item.get("plan_ready")) for item in runs)
    )
    _write_atomic_json(report_path, report)
    return report


def _validated_plan(
    plan: dict[str, Any], project_id: str, run_dir: Path
) -> dict[str, Any]:
    if plan.get("schema_version") != "motion-comic-factory.model-bakeoff-plan.v1":
        raise QualityBakeoffRunnerError("Model bakeoff plan schema is invalid.")
    if plan.get("project_id") != project_id or plan.get("run_dir") != str(run_dir):
        raise QualityBakeoffRunnerError(
            "Model bakeoff plan does not match the selected project."
        )
    video_models = plan.get("video_models")
    still_models = plan.get("still_models")
    representative_ids = plan.get("representative_character_micro_shot_ids")
    if (
        not isinstance(video_models, list)
        or not video_models
        or any(model not in PRODUCTION_VIDEO_MODELS for model in video_models)
    ):
        raise QualityBakeoffRunnerError(
            "Model bakeoff plan has invalid production video models."
        )
    if (
        not isinstance(representative_ids, list)
        or len(representative_ids) != 2
        or len(set(representative_ids)) != 2
    ):
        raise QualityBakeoffRunnerError(
            "Model bakeoff plan must select two representative character shots."
        )
    if plan.get("requires_still"):
        if (
            not isinstance(still_models, list)
            or not still_models
            or any(model not in PRODUCTION_STILL_MODELS for model in still_models)
            or not isinstance(plan.get("still_micro_shot_id"), str)
            or not plan["still_micro_shot_id"]
        ):
            raise QualityBakeoffRunnerError(
                "Model bakeoff plan has invalid production still selection."
            )
    return plan


def _targeted_video_ids(
    requested: Sequence[str] | None,
    representative_ids: list[str],
    *,
    video_selected: bool,
) -> list[str]:
    if requested is None:
        return representative_ids
    values = list(requested)
    if not video_selected:
        raise QualityBakeoffRunnerError(
            "Micro-shot filtering requires the video bakeoff route."
        )
    if not values or any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        for value in values
    ):
        raise QualityBakeoffRunnerError(
            "Bakeoff micro-shot IDs must be non-empty exact identifiers."
        )
    if len(set(values)) != len(values):
        raise QualityBakeoffRunnerError(
            "Bakeoff micro-shot IDs must not contain duplicates."
        )
    unknown = sorted(set(values) - set(representative_ids))
    if unknown:
        raise QualityBakeoffRunnerError(
            "Bakeoff micro-shot IDs are not in the representative plan: "
            + ", ".join(unknown)
        )
    requested_set = set(values)
    return [
        micro_shot_id
        for micro_shot_id in representative_ids
        if micro_shot_id in requested_set
    ]


def _run_summary(
    result: Mapping[str, Any], model: str, *, api_key: str
) -> dict[str, Any]:
    return {
        "model": model,
        "plan_ready": bool(result.get("plan_ready")),
        "executed": bool(result.get("executed")),
        "success": bool(result.get("success")),
        "planned_count": _count(result, "planned_count"),
        "completed_count": _count(result, "completed_count"),
        "skipped_count": _count(result, "skipped_count"),
        "failed_count": _count(result, "failed_count"),
        "blocked_count": _count(result, "blocked_count"),
        "errors": _safe_errors(result.get("errors"), api_key),
    }


def _safe_errors(value: Any, api_key: str) -> list[str]:
    if not isinstance(value, list):
        return []
    errors = []
    for item in value:
        text = str(item.get("error") if isinstance(item, Mapping) else item)
        errors.append(text.replace(api_key, "[redacted]") if api_key else text)
    return errors


def _project_id(value: str) -> str:
    project = str(value).strip()
    if (
        not project
        or project in {".", ".."}
        or "/" in project
        or "\\" in project
        or len(project) > 128
    ):
        raise QualityBakeoffRunnerError("Project ID is invalid.")
    return project


def _run_dir(config: Mapping[str, Any], project_id: str) -> Path:
    runs_dir = config.get("runsDir")
    if not isinstance(runs_dir, str) or not runs_dir.strip():
        raise QualityBakeoffRunnerError("Configuration is missing runsDir.")
    root = Path(runs_dir).expanduser().resolve()
    run_dir = (root / project_id).resolve()
    if run_dir.parent != root or not run_dir.is_dir():
        raise QualityBakeoffRunnerError(
            f"Project run directory is missing: {run_dir}"
        )
    return run_dir


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityBakeoffRunnerError(
            f"Unable to read quality artifact {path.name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise QualityBakeoffRunnerError(
            f"Quality artifact {path.name} must contain an object."
        )
    return payload
