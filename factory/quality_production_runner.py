from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .gateway_image import GatewayImageConfig
from .gateway_video import GatewayVideoConfig
from .micro_preview import MicroPreviewError, select_micro_sources
from .micro_still_batch import (
    MicroStillBatchError,
    _still_eligibility,
    build_micro_still_jobs,
    render_micro_still_batch,
)
from .micro_video_batch import (
    MicroVideoBatchError,
    build_micro_video_jobs,
    render_micro_video_batch,
)
from .model_bakeoff import (
    ModelBakeoffError,
    require_selected_production_model,
    require_selected_still_model,
)
from .provider_profile import resolve_provider_profile
from .schema import episode_from_dict
from .visual_timeline import visual_timeline_from_dict


QUALITY_PRODUCTION_CANDIDATES_SCHEMA = (
    "motion-comic-factory.quality-production-candidates.v1"
)
QUALITY_VISUAL_SELECTION_REPORT_SCHEMA = (
    "motion-comic-factory.quality-visual-selection-report.v1"
)


class QualityProductionRunnerError(RuntimeError):
    pass


def run_quality_production_candidates(
    config: Mapping[str, Any],
    project_id: str,
    *,
    candidate_number: int = 1,
    kind: str = "all",
    micro_shot_ids: Sequence[str] | None = None,
    limit: int = 0,
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
    _candidate_number(candidate_number)
    if kind not in {"all", "video", "still"}:
        raise QualityProductionRunnerError(
            "Production candidate kind must be all, video, or still."
        )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise QualityProductionRunnerError(
            "Production candidate limit must be a non-negative integer."
        )

    run_dir = _run_dir(config, project)
    episode = episode_from_dict(_read_json(run_dir / "episode.json"))
    timeline = visual_timeline_from_dict(
        _read_json(run_dir / "visual_timeline.json")
    )
    selected_micro_shot_ids = _production_micro_shot_ids(
        timeline,
        micro_shot_ids,
        kind=kind,
    )
    selected_micro_shot_id_set = set(selected_micro_shot_ids)
    bakeoff_report = _read_json(run_dir / "model_bakeoff_report.json")
    character_assets = _read_json(run_dir / "character_assets.json")
    needs_video = kind in {"all", "video"} and any(
        shot.id in selected_micro_shot_id_set and shot.character_ids
        for shot in timeline.micro_shots
    )
    needs_still = kind in {"all", "still"} and any(
        shot.id in selected_micro_shot_id_set and _still_eligibility(shot)[0]
        for shot in timeline.micro_shots
    )
    try:
        video_model = (
            require_selected_production_model(bakeoff_report)
            if needs_video
            else ""
        )
        still_model = (
            require_selected_still_model(bakeoff_report)
            if needs_still
            else ""
        )
    except (ModelBakeoffError, ValueError) as exc:
        raise QualityProductionRunnerError(
            f"Production model bakeoff gate failed: {exc}"
        ) from exc

    try:
        video_jobs = (
            build_micro_video_jobs(
                episode,
                timeline,
                character_assets,
                model=video_model,
                run_dir=run_dir,
                candidate_number=candidate_number,
            )
            if needs_video
            else []
        )
        still_jobs = (
            build_micro_still_jobs(
                episode,
                timeline,
                model=still_model,
                run_dir=run_dir,
                candidate_number=candidate_number,
            )
            if needs_still
            else []
        )
    except (MicroVideoBatchError, MicroStillBatchError, ValueError) as exc:
        raise QualityProductionRunnerError(
            f"Production candidate planning failed: {exc}"
        ) from exc
    video_jobs = [
        job for job in video_jobs if job.micro_shot_id in selected_micro_shot_id_set
    ]
    still_jobs = [
        job for job in still_jobs if job.micro_shot_id in selected_micro_shot_id_set
    ]
    if limit:
        video_jobs = video_jobs[:limit]
        still_jobs = still_jobs[:limit]

    profile = profile_resolver(config)
    report_path = run_dir / "quality_production_candidates.json"
    report: dict[str, Any] = {
        "schema_version": QUALITY_PRODUCTION_CANDIDATES_SCHEMA,
        "project_id": project,
        "run_dir": str(run_dir),
        "candidate_number": candidate_number,
        "kind": kind,
        "micro_shot_ids": selected_micro_shot_ids,
        "limit": limit,
        "allow_network": allow_network,
        "overwrite": overwrite,
        "selected_video_model": video_model,
        "selected_still_model": still_model,
        "plan_ready": True,
        "executed": False,
        "success": False,
        "planned_count": len(video_jobs) + len(still_jobs),
        "completed_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
        "blocked_reasons": [],
        "video_run": None,
        "still_run": None,
    }
    blockers = _live_blockers(
        profile,
        require_video=bool(video_jobs),
        require_image=bool(still_jobs),
    )
    if allow_network and blockers:
        report["blocked_reasons"] = blockers
        _write_atomic_json(report_path, report)
        return report

    video_config = GatewayVideoConfig(
        api_key=str(profile.video.api_key),
        base_url=str(profile.video.base_url),
        model=video_model or str(profile.video.model),
        timeout_seconds=timeout_seconds,
        submit_timeout_seconds=submit_timeout_seconds,
        download_timeout_seconds=download_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )
    image_config = GatewayImageConfig(
        api_key=str(profile.image.api_key),
        base_url=str(profile.image.base_url),
        model=still_model or str(profile.image.model),
        timeout_seconds=timeout_seconds,
        download_timeout_seconds=download_timeout_seconds,
    )

    runs: list[dict[str, Any]] = []
    if video_jobs:
        result = video_renderer(
            video_jobs,
            run_dir,
            replace(video_config, model=video_model),
            allow_network=allow_network,
            overwrite=overwrite,
        )
        report["video_run"] = _run_summary(
            result,
            video_model,
            api_key=str(profile.video.api_key),
        )
        runs.append(report["video_run"])
    if still_jobs:
        result = still_renderer(
            episode,
            timeline,
            model=still_model,
            run_dir=run_dir,
            candidate_number=candidate_number,
            micro_shot_ids=[job.micro_shot_id for job in still_jobs],
            config=replace(image_config, model=still_model),
            allow_network=allow_network,
            overwrite=overwrite,
        )
        report["still_run"] = _run_summary(
            result,
            still_model,
            api_key=str(profile.image.api_key),
        )
        runs.append(report["still_run"])

    for key in (
        "completed_count",
        "skipped_count",
        "failed_count",
        "blocked_count",
    ):
        report[key] = sum(int(run.get(key) or 0) for run in runs)
    report["executed"] = any(bool(run.get("executed")) for run in runs)
    report["success"] = (
        all(
            bool(run.get("success"))
            if allow_network
            else bool(run.get("plan_ready"))
            for run in runs
        )
        if runs
        else True
    )
    _write_atomic_json(report_path, report)
    return report


def write_quality_visual_selection(
    config: Mapping[str, Any],
    project_id: str,
    selection_path: str | Path,
) -> dict[str, Any]:
    project = _project_id(project_id)
    run_dir = _run_dir(config, project)
    source = _project_file(selection_path, run_dir)
    selection = _read_json(source)
    episode = episode_from_dict(_read_json(run_dir / "episode.json"))
    timeline = visual_timeline_from_dict(
        _read_json(run_dir / "visual_timeline.json")
    )
    bakeoff_report = _read_json(run_dir / "model_bakeoff_report.json")
    try:
        sources = select_micro_sources(
            episode,
            timeline,
            selection,
            run_dir=run_dir,
            bakeoff_report=bakeoff_report,
        )
    except (MicroPreviewError, ModelBakeoffError, ValueError) as exc:
        raise QualityProductionRunnerError(
            f"Visual selection validation failed: {exc}"
        ) from exc

    destination = run_dir / "visual_selection.json"
    _write_atomic_json(destination, selection)
    video_count = sum(source.kind == "video" for source in sources)
    still_count = sum(source.kind == "still" for source in sources)
    return {
        "schema_version": QUALITY_VISUAL_SELECTION_REPORT_SCHEMA,
        "success": True,
        "project_id": project,
        "input_path": str(source),
        "output_path": str(destination),
        "selected_count": len(sources),
        "video_count": video_count,
        "still_count": still_count,
    }


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


def _count(result: Mapping[str, Any], key: str) -> int:
    value = result.get(key)
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else 0
    )


def _safe_errors(value: Any, api_key: str) -> list[str]:
    if not isinstance(value, list):
        return []
    errors: list[str] = []
    for item in value:
        text = str(item.get("error") if isinstance(item, Mapping) else item)
        errors.append(text.replace(api_key, "[redacted]") if api_key else text)
    return errors


def _live_blockers(
    profile: Any, *, require_video: bool, require_image: bool
) -> list[str]:
    blockers: list[str] = []
    for label, capability, required in (
        ("video", profile.video, require_video),
        ("image", profile.image, require_image),
    ):
        if not required:
            continue
        if capability.provider != "gateway":
            blockers.append(f"{label}: provider is not configured as gateway")
        elif not capability.ready:
            reasons = capability.blockers or ("provider is not ready",)
            blockers.extend(f"{label}: {reason}" for reason in reasons)
    return blockers


def _candidate_number(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 3
    ):
        raise QualityProductionRunnerError(
            "Production candidate number must be between 1 and 3."
        )


def _production_micro_shot_ids(
    timeline: Any,
    values: Sequence[str] | None,
    *,
    kind: str,
) -> list[str]:
    shots = list(timeline.micro_shots)
    shot_by_id = {shot.id: shot for shot in shots}
    explicit_targets = values is not None
    if values is None:
        requested = set(shot_by_id)
    else:
        if (
            isinstance(values, (str, bytes))
            or not isinstance(values, Sequence)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise QualityProductionRunnerError(
                "Production micro-shot IDs must be a non-empty string sequence."
            )
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != len(normalized):
            raise QualityProductionRunnerError(
                "Production micro-shot IDs must not contain duplicates."
            )
        unknown = [value for value in normalized if value not in shot_by_id]
        if unknown:
            raise QualityProductionRunnerError(
                f"Unknown production micro-shot ID: {unknown[0]}."
            )
        requested = set(normalized)

    selected: list[str] = []
    for shot in shots:
        if shot.id not in requested:
            continue
        is_video = bool(shot.character_ids)
        is_still = _still_eligibility(shot)[0]
        if explicit_targets and kind == "video" and not is_video:
            raise QualityProductionRunnerError(
                f"Production micro-shot {shot.id} is not eligible for the video route."
            )
        if explicit_targets and kind == "still" and not is_still:
            raise QualityProductionRunnerError(
                f"Production micro-shot {shot.id} is not eligible for the still route."
            )
        if kind == "all" and not (is_video or is_still):
            raise QualityProductionRunnerError(
                f"Production micro-shot {shot.id} has no eligible production route."
            )
        if kind == "all" or (kind == "video" and is_video) or (
            kind == "still" and is_still
        ):
            selected.append(shot.id)
    return selected


def _project_id(value: str) -> str:
    project = str(value).strip()
    if (
        not project
        or project in {".", ".."}
        or "/" in project
        or "\\" in project
        or len(project) > 128
    ):
        raise QualityProductionRunnerError("Project ID is invalid.")
    return project


def _run_dir(config: Mapping[str, Any], project_id: str) -> Path:
    runs_dir = config.get("runsDir")
    if not isinstance(runs_dir, str) or not runs_dir.strip():
        raise QualityProductionRunnerError("Configuration is missing runsDir.")
    root = Path(runs_dir).expanduser().resolve()
    run_dir = (root / project_id).resolve()
    if run_dir.parent != root or not run_dir.is_dir():
        raise QualityProductionRunnerError(
            f"Project run directory is missing: {run_dir}"
        )
    return run_dir


def _project_file(value: str | Path, run_dir: Path) -> Path:
    raw = Path(value).expanduser()
    source = raw if raw.is_absolute() else Path.cwd() / raw
    if source.is_symlink():
        raise QualityProductionRunnerError(
            "Visual selection input must not be a symlink."
        )
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise QualityProductionRunnerError(
            f"Visual selection input is unavailable: {source}"
        ) from exc
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise QualityProductionRunnerError(
            "Visual selection input must stay inside the project run directory."
        ) from exc
    if not resolved.is_file():
        raise QualityProductionRunnerError(
            "Visual selection input must be a regular file."
        )
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityProductionRunnerError(
            f"Unable to read quality artifact {path.name}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise QualityProductionRunnerError(
            f"Quality artifact {path.name} must contain an object."
        )
    return payload


def _write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
