from __future__ import annotations

import os
import re
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .candidate_review import (
    CandidateReviewError,
    CandidateReviewManifest,
    approved_anchor_for_micro_shot,
    candidate_review_manifest_from_dict,
)
from .character_assets import is_production_asset_source, is_supported_image_file
from .dialogue_assets import (
    DialogueAudioError,
    DialogueAudioManifest,
    require_dialogue_audio,
)
from .gateway_video import GatewayVideoClient, GatewayVideoConfig
from .gateway_video_batch import (
    GatewayVideoBatchError,
    render_gateway_video_single,
    write_atomic_json,
)
from .prompt_compiler import PromptCompilerError, compile_video_prompt
from .prompt_safety import PREVIOUS_SHOT_CONTINUITY
from .performance_card import PerformanceSheet, validate_performance_sheet
from .schema import Episode
from .visual_timeline import MicroShot, VisualTimeline, validate_visual_timeline


PRODUCTION_VIDEO_MODELS = frozenset({"doubao-seedance-2-0"})
MICRO_VIDEO_BATCH_SCHEMA = "motion-comic-factory.micro-video-batch.v1"


class MicroVideoBatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class MicroVideoJob:
    micro_shot_id: str
    model: str
    prompt: str
    images: tuple[str, ...]
    duration: int
    resolution: str
    output_path: str
    report_path: str
    image_roles: tuple[str, ...] = ()
    audio_path: str = ""
    audio_sha256: str = ""
    entry_anchor_id: str = ""
    capability: str = "action_only"
    capability_provenance: Mapping[str, Any] | None = None
    capability_provenance_sha256: str = ""


def candidate_output_path(
    run_dir: str | Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int,
) -> Path:
    return _candidate_paths(run_dir, micro_shot_id, model, candidate_number)[0]


def candidate_report_path(
    run_dir: str | Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int,
) -> Path:
    return _candidate_paths(run_dir, micro_shot_id, model, candidate_number)[1]


def build_micro_video_jobs(
    episode: Episode,
    timeline: VisualTimeline,
    character_assets: dict[str, Any],
    *,
    model: str,
    run_dir: str | Path,
    candidate_number: int,
    performance_sheet: PerformanceSheet,
    dialogue_manifest: DialogueAudioManifest,
    capability_report: Mapping[str, Any],
    scene_keyframes: Mapping[str, str],
    approved_anchors: Mapping[str, str] | None = None,
    candidate_review: CandidateReviewManifest | Mapping[str, Any] | None = None,
    micro_shot_ids: Sequence[str] | None = None,
) -> list[MicroVideoJob]:
    timeline_errors = validate_visual_timeline(timeline, episode)
    if timeline_errors:
        raise MicroVideoBatchError(
            "Visual timeline is invalid: " + "; ".join(timeline_errors)
        )
    _require_production_model(model)
    _require_candidate_number(candidate_number)
    run_root = _run_root(run_dir)
    sheet_errors = validate_performance_sheet(performance_sheet, episode, timeline)
    if sheet_errors:
        raise MicroVideoBatchError(
            "Performance sheet is invalid: " + "; ".join(sheet_errors)
        )
    if not isinstance(dialogue_manifest, DialogueAudioManifest):
        raise MicroVideoBatchError("Dialogue manifest must be a DialogueAudioManifest.")
    if not isinstance(capability_report, Mapping):
        raise MicroVideoBatchError("Capability report must be a mapping.")
    if not isinstance(scene_keyframes, Mapping):
        raise MicroVideoBatchError("Scene keyframes must be a mapping.")
    if approved_anchors:
        raise MicroVideoBatchError(
            "Approved anchors must come from candidate review evidence, not a path map."
        )
    try:
        review_manifest = (
            candidate_review
            if isinstance(candidate_review, CandidateReviewManifest)
            else candidate_review_manifest_from_dict(candidate_review)
            if isinstance(candidate_review, Mapping)
            else CandidateReviewManifest(project_id=timeline.project_id, candidates=())
        )
    except CandidateReviewError as exc:
        raise MicroVideoBatchError(str(exc)) from exc
    references = _validated_character_references(episode, character_assets, run_root)
    selected_ids = _selected_micro_shot_ids(timeline, micro_shot_ids)
    resolved_scenes = _resolved_scene_contexts(timeline)
    cards = {card.micro_shot_id: card for card in performance_sheet.cards}

    jobs: list[MicroVideoJob] = []
    for shot in sorted(timeline.micro_shots, key=lambda item: item.index):
        if selected_ids is not None and shot.id not in selected_ids:
            continue
        if not shot.character_ids:
            if selected_ids is not None:
                raise MicroVideoBatchError(
                    f"{shot.id} is character-free and must use the still route; "
                    "video requires a character reference."
                )
            continue
        card = cards[shot.id]
        audio_path = ""
        audio_sha256 = ""
        capability = "action_only"
        capability_provenance: Mapping[str, Any] | None = None
        capability_provenance_sha256 = ""
        if card.requires_visible_lipsync:
            try:
                # Imported here to avoid model_bakeoff's production-model import cycle.
                from .model_bakeoff import ModelBakeoffError, require_speaking_capability

                audio = require_dialogue_audio(dialogue_manifest, card)
                require_speaking_capability(capability_report, model, shot.id)
            except (DialogueAudioError, ModelBakeoffError) as exc:
                raise MicroVideoBatchError(str(exc)) from exc
            audio_path = str(Path(audio.path).resolve())
            audio_sha256 = audio.sha256
            capability = "speaking"
            capability_provenance = _capability_provenance(capability_report)
            capability_provenance_sha256 = _capability_provenance_sha256(
                capability_provenance
            )
        try:
            anchor, _anchor_source_id = approved_anchor_for_micro_shot(
                review_manifest, timeline, shot.id
            )
        except CandidateReviewError as exc:
            raise MicroVideoBatchError(str(exc)) from exc
        keyframe = _require_evidence_image(
            scene_keyframes,
            card.scene_keyframe_id,
            "scene keyframe",
            shot.id,
            run_root,
        )
        character_images = _references_for_shot(shot, references)
        images = ((anchor,) if anchor else ()) + (keyframe, *character_images)
        image_roles = (("last_frame",) if anchor else ()) + (
            "first_frame",
            *("reference_image",) * len(character_images),
        )
        previous_scene_context = (
            resolved_scenes[shot.index - 1]
            if shot.scene_context == PREVIOUS_SHOT_CONTINUITY
            else None
        )
        try:
            prompt = compile_video_prompt(
                episode,
                shot,
                card=card,
                previous_scene_context=previous_scene_context,
            ).strip()
        except PromptCompilerError as exc:
            raise MicroVideoBatchError(
                f"Unable to compile video prompt for {shot.id}: {exc}"
            ) from exc
        if not prompt:
            raise MicroVideoBatchError(f"Micro-video prompt is empty for {shot.id}.")
        if PREVIOUS_SHOT_CONTINUITY in prompt:
            raise MicroVideoBatchError(
                f"Micro-video prompt for {shot.id} contains unresolved continuity."
            )
        if not 1 <= shot.source_duration_seconds <= 15:
            raise MicroVideoBatchError(
                f"Micro-video duration must be 1-15 seconds for {shot.id}."
            )
        output_path, report_path = _candidate_paths(
            run_root, shot.id, model, candidate_number
        )
        jobs.append(
            MicroVideoJob(
                micro_shot_id=shot.id,
                model=model,
                prompt=prompt,
                images=images,
                duration=max(4, shot.source_duration_seconds),
                resolution="1080p",
                output_path=str(output_path),
                report_path=str(report_path),
                image_roles=image_roles,
                audio_path=audio_path,
                audio_sha256=audio_sha256,
                entry_anchor_id=card.entry_anchor_id if anchor else "",
                capability=capability,
                capability_provenance=capability_provenance,
                capability_provenance_sha256=capability_provenance_sha256,
            )
        )
    return jobs


def render_micro_video_batch(
    jobs: Sequence[MicroVideoJob],
    run_dir: str | Path,
    config: GatewayVideoConfig,
    *,
    client_factory: Callable[
        [GatewayVideoConfig], GatewayVideoClient
    ] = GatewayVideoClient,
    allow_network: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    requested_jobs = list(jobs)
    run_root = _run_root(run_dir)
    candidate_numbers: list[int] = []
    seen_jobs: set[tuple[str, str, int]] = set()
    all_destinations: set[Path] = set()
    normalized_jobs: list[MicroVideoJob] = []
    for requested_job in requested_jobs:
        candidate_number, job = _validate_job(requested_job, run_root)
        identity = (job.micro_shot_id, job.model, candidate_number)
        output = Path(job.output_path)
        report_path = Path(job.report_path)
        if identity in seen_jobs:
            raise MicroVideoBatchError(
                f"Duplicate micro-video job: {job.micro_shot_id}."
            )
        if output in all_destinations:
            raise MicroVideoBatchError(
                f"Duplicate micro-video destination path: {job.output_path}."
            )
        if report_path in all_destinations:
            raise MicroVideoBatchError(
                f"Duplicate micro-video destination path: {job.report_path}."
            )
        seen_jobs.add(identity)
        all_destinations.add(output)
        all_destinations.add(report_path)
        candidate_numbers.append(candidate_number)
        normalized_jobs.append(job)

    candidate_numbers = sorted(set(candidate_numbers))
    models = sorted({job.model for job in normalized_jobs})
    destination = run_root / "micro_video_batch.json"
    report: dict[str, Any] = {
        "schema_version": MICRO_VIDEO_BATCH_SCHEMA,
        "project_id": run_root.name,
        "run_dir": str(run_root),
        "provider": "gateway",
        "model": models[0] if len(models) == 1 else "",
        "models": models,
        "candidate_number": candidate_numbers[0]
        if len(candidate_numbers) == 1
        else None,
        "candidate_numbers": candidate_numbers,
        "plan_ready": True,
        "planned_count": len(normalized_jobs),
        "executed": False,
        "success": False,
        "completed_count": 0,
        "resumed_count": 0,
        "skipped_count": 0,
        "blocked_count": 0,
        "failed_count": 0,
        "overwrite": overwrite,
        "jobs": [_safe_job_report(job) for job in normalized_jobs],
        "results": [],
        "errors": [],
    }

    for job in normalized_jobs:
        try:
            client = client_factory(replace(config, model=job.model))
            result = render_gateway_video_single(
                job.prompt,
                job.output_path,
                client,
                job.report_path,
                images=job.images,
                image_roles=job.image_roles,
                audio=job.audio_path or None,
                reference_audio_sha256=job.audio_sha256,
                entry_anchor_id=job.entry_anchor_id,
                capability=job.capability,
                capability_provenance_sha256=job.capability_provenance_sha256,
                duration=job.duration,
                ratio="9:16",
                resolution=job.resolution,
                generate_audio=False,
                allow_network=allow_network,
                overwrite=overwrite,
                report_sanitizer=lambda candidate_report: _safe_data(
                    candidate_report, config
                ),
            )
        except (
            GatewayVideoBatchError,
            MicroVideoBatchError,
            OSError,
            ValueError,
        ) as exc:
            report["failed_count"] += 1
            report["errors"].append(
                {"micro_shot_id": job.micro_shot_id, "error": _redact(str(exc), config)}
            )
            continue

        safe_result = _safe_data(result, config)
        report["results"].append(
            {"micro_shot_id": job.micro_shot_id, "result": safe_result}
        )
        report["completed_count"] += _report_count(result, "completed_count")
        report["resumed_count"] += _report_count(result, "resumed_count")
        report["skipped_count"] += _report_count(result, "skipped_count")
        if result.get("blocked_reasons"):
            report["blocked_count"] += 1
        if result.get("executed"):
            report["executed"] = True
        if result.get("errors"):
            report["errors"].extend(
                {
                    "micro_shot_id": job.micro_shot_id,
                    "error": _redact(str(error.get("error") or error), config),
                }
                for error in result["errors"]
                if isinstance(error, dict)
            )
        if not result.get("success") and not result.get("blocked_reasons"):
            report["failed_count"] += max(1, _report_count(result, "failed_count"))

    report["success"] = (
        report["completed_count"] + report["skipped_count"] == report["planned_count"]
        and report["blocked_count"] == 0
        and report["failed_count"] == 0
    )
    for job_report in report["jobs"]:
        output = Path(str(job_report["output_path"]))
        if output.is_file():
            job_report["output_sha256"] = _sha256_file(output)
    safe_report = _safe_data(report, config)
    write_atomic_json(destination, safe_report)
    return safe_report


def _candidate_paths(
    run_dir: str | Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int,
) -> tuple[Path, Path]:
    _require_production_model(model)
    _require_candidate_number(candidate_number)
    if (
        not isinstance(micro_shot_id, str)
        or not micro_shot_id
        or micro_shot_id != micro_shot_id.strip()
    ):
        raise MicroVideoBatchError(
            "Micro-shot ID must be a non-empty exact identifier."
        )
    if Path(micro_shot_id).name != micro_shot_id:
        raise MicroVideoBatchError(
            "Micro-video candidate paths must stay inside run_dir/micro_clips."
        )
    run_root = _run_root(run_dir)
    clips_root = run_root / "micro_clips"
    output = (
        clips_root / micro_shot_id / model / f"candidate_{candidate_number:03d}.mp4"
    )
    report = (
        clips_root
        / micro_shot_id
        / model
        / f"candidate_{candidate_number:03d}.report.json"
    )
    _require_path_in_clips_root(output, run_root)
    _require_path_in_clips_root(report, run_root)
    return output, report


def _require_production_model(model: str) -> None:
    if not isinstance(model, str) or model not in PRODUCTION_VIDEO_MODELS:
        raise MicroVideoBatchError(f"Unsupported production video model: {model}")


def _require_candidate_number(candidate_number: int) -> None:
    if (
        isinstance(candidate_number, bool)
        or not isinstance(candidate_number, int)
        or not 1 <= candidate_number <= 3
    ):
        raise MicroVideoBatchError(
            "A micro-shot may submit at most 3 paid candidates per model."
        )


def _run_root(run_dir: str | Path) -> Path:
    root = _lexical_path(run_dir, "run_dir")
    return root


def _lexical_path(
    value: str | Path,
    label: str,
    *,
    require_absolute_exact: bool = False,
) -> Path:
    if not isinstance(value, (str, Path)):
        raise MicroVideoBatchError(f"Micro-video {label} must be a string or Path.")
    raw_value = str(value)
    if not raw_value or raw_value != raw_value.strip():
        raise MicroVideoBatchError(f"Micro-video {label} is empty.")
    raw_path = Path(raw_value)
    _validate_raw_path(raw_value, raw_path, label)
    path = raw_path if raw_path.is_absolute() else Path.cwd() / raw_path
    _reject_existing_symlink_components(path)
    if require_absolute_exact and (
        not raw_path.is_absolute() or raw_value != str(path)
    ):
        raise MicroVideoBatchError(
            f"Micro-video {label} must be an exact absolute canonical path."
        )
    return path


def _validate_raw_path(raw_value: str, raw_path: Path, label: str) -> None:
    raw_parts = raw_path.parts
    string_parts = raw_value.split(os.sep)
    if any(part in {".", ".."} for part in (*raw_parts, *string_parts)):
        raise MicroVideoBatchError(
            f"Micro-video {label} must not contain . or .. path components."
        )
    _reject_existing_symlink_components(raw_path)


def _reject_existing_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in path.parts:
        if part == path.anchor:
            continue
        current /= part
        try:
            if current.is_symlink():
                raise MicroVideoBatchError(
                    f"Micro-video path must not use a symlink: {current}"
                )
        except OSError as exc:
            raise MicroVideoBatchError(
                f"Unable to inspect micro-video path: {current}"
            ) from exc


def _require_path_in_clips_root(path: Path, run_root: Path) -> None:
    allowed = run_root / "micro_clips"
    _reject_existing_symlink_components(path)
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise MicroVideoBatchError(
            "Micro-video candidate paths must stay inside run_dir/micro_clips."
        ) from exc


def _require_production_asset_path(
    path: Path, run_root: Path, character_id: str
) -> None:
    assets_root = run_root / "assets" / "characters"
    _reject_existing_symlink_components(path)
    try:
        path.relative_to(assets_root)
    except ValueError as exc:
        raise MicroVideoBatchError(
            f"Character asset {character_id} must be installed under run_dir/assets/characters."
        ) from exc


def _require_evidence_image(
    evidence: Mapping[str, str],
    evidence_id: str,
    label: str,
    micro_shot_id: str,
    run_root: Path,
) -> str:
    value = evidence.get(evidence_id)
    if not isinstance(value, str) or not value.strip():
        raise MicroVideoBatchError(f"{micro_shot_id} missing {label}.")
    try:
        path = _lexical_path(value, label)
        _require_path_in_run_root(path, run_root, label)
    except MicroVideoBatchError as exc:
        raise MicroVideoBatchError(f"{micro_shot_id} invalid {label}: {exc}") from exc
    if not is_supported_image_file(path):
        raise MicroVideoBatchError(
            f"{micro_shot_id} invalid {label}: must be a supported existing local image."
        )
    return str(path)


def _require_path_in_run_root(path: Path, run_root: Path, label: str) -> None:
    _reject_existing_symlink_components(path)
    try:
        path.relative_to(run_root)
    except ValueError as exc:
        raise MicroVideoBatchError(
            f"Micro-video {label} must stay inside run_dir."
        ) from exc


def _validate_manual_images(
    images: Any, image_roles: Any, run_root: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(images, (tuple, list)) or not images:
        raise MicroVideoBatchError(
            "Micro-video job images must be a non-empty tuple or list."
        )
    roles = _image_roles(images, image_roles)
    if not isinstance(roles, (tuple, list)) or len(roles) != len(images):
        raise MicroVideoBatchError(
            "Micro-video job image roles must exactly match image references."
        )
    if any(role not in {"last_frame", "first_frame", "reference_image"} for role in roles):
        raise MicroVideoBatchError("Micro-video job image roles are invalid.")
    seen: set[Path] = set()
    normalized: list[str] = []
    for image, role in zip(images, roles, strict=True):
        if not isinstance(image, (str, Path)):
            raise MicroVideoBatchError(
                "Micro-video job image references must be local strings or Paths."
            )
        value = str(image).strip()
        parsed = urlsplit(value)
        if not value or value.lower().startswith("data:") or parsed.scheme:
            raise MicroVideoBatchError(
                "Micro-video job image references must be local production files."
            )
        path = _lexical_path(image, "image reference")
        if role == "reference_image":
            _require_production_asset_path(path, run_root, "manual")
        else:
            _require_path_in_run_root(path, run_root, str(role))
        if path in seen:
            raise MicroVideoBatchError(
                "Micro-video job image references must be unique."
            )
        if not is_supported_image_file(path):
            raise MicroVideoBatchError(
                "Micro-video job image references must be supported existing production images."
            )
        seen.add(path)
        normalized.append(str(path))
    return tuple(normalized), tuple(roles)


def _image_roles(images: Any, image_roles: Any) -> tuple[str, ...]:
    if not isinstance(images, (tuple, list)):
        return ()
    roles = (
        ("reference_image",) * len(images)
        if image_roles in (None, ())
        else image_roles
    )
    if not isinstance(roles, (tuple, list)) or len(roles) != len(images):
        raise MicroVideoBatchError(
            "Micro-video job image roles must exactly match image references."
        )
    if any(role not in {"last_frame", "first_frame", "reference_image"} for role in roles):
        raise MicroVideoBatchError("Micro-video job image roles are invalid.")
    return tuple(roles)


def _validated_character_references(
    episode: Episode, character_assets: dict[str, Any], run_root: Path
) -> dict[str, str]:
    if not isinstance(character_assets, dict):
        raise MicroVideoBatchError("Character asset manifest must be an object.")
    if character_assets.get("project_id") != episode.project_id:
        raise MicroVideoBatchError(
            "Character asset manifest project_id does not match episode."
        )
    if character_assets.get("production_ready") is not True:
        raise MicroVideoBatchError("Character asset manifest is not production-ready.")
    entries = character_assets.get("characters")
    if not isinstance(entries, list):
        raise MicroVideoBatchError(
            "Character asset manifest must contain a characters list."
        )

    references: dict[str, str] = {}
    resolved_paths: set[Path] = set()
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise MicroVideoBatchError(
                f"Character asset entry {position} must be an object."
            )
        character_id = entry.get("character_id")
        if (
            not isinstance(character_id, str)
            or not character_id
            or character_id != character_id.strip()
        ):
            raise MicroVideoBatchError(
                f"Character asset entry {position} must have an exact character_id."
            )
        if character_id in references:
            raise MicroVideoBatchError(
                f"Character asset manifest has duplicate character_id: {character_id}."
            )
        image = entry.get("reference_image_path")
        if not isinstance(image, str) or not image or image != image.strip():
            raise MicroVideoBatchError(
                f"Character asset {character_id} is missing a supported existing reference image."
            )
        path = _lexical_path(image, f"character asset {character_id} reference image")
        _require_production_asset_path(path, run_root, character_id)
        if path in resolved_paths:
            raise MicroVideoBatchError(
                f"Character asset manifest has duplicate reference image: {path.name}."
            )
        resolved_paths.add(path)
        if entry.get("production_ready") is not True:
            raise MicroVideoBatchError(
                f"Character asset {character_id} is not production_ready."
            )
        if not is_production_asset_source(str(entry.get("asset_source") or "")):
            raise MicroVideoBatchError(
                f"Character asset {character_id} has an unconfirmed production source."
            )
        if entry.get("provenance_status") != "confirmed":
            raise MicroVideoBatchError(
                f"Character asset {character_id} has unconfirmed provenance."
            )
        if not is_supported_image_file(path):
            raise MicroVideoBatchError(
                f"Character asset {character_id} must use a supported existing reference image."
            )
        references[character_id] = str(path)
    episode_character_ids = {character.id for character in episode.characters}
    manifest_character_ids = set(references)
    if manifest_character_ids != episode_character_ids:
        missing = sorted(episode_character_ids - manifest_character_ids)
        extra = sorted(manifest_character_ids - episode_character_ids)
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if extra:
            details.append("extra: " + ", ".join(extra))
        raise MicroVideoBatchError(
            "Character asset manifest character IDs must exactly match Episode characters"
            + (" (" + "; ".join(details) + ")." if details else ".")
        )
    return references


def _selected_micro_shot_ids(
    timeline: VisualTimeline, micro_shot_ids: Sequence[str] | None
) -> set[str] | None:
    if micro_shot_ids is None:
        return None
    selected = list(micro_shot_ids)
    if len(set(selected)) != len(selected):
        raise MicroVideoBatchError("Micro-shot selection contains duplicate IDs.")
    known_ids = {shot.id for shot in timeline.micro_shots}
    unknown = [shot_id for shot_id in selected if shot_id not in known_ids]
    if unknown:
        raise MicroVideoBatchError(
            "Unknown selected micro-shot IDs: "
            + ", ".join(str(item) for item in unknown)
        )
    return set(selected)


def _resolved_scene_contexts(timeline: VisualTimeline) -> dict[int, str]:
    resolved: dict[int, str] = {}
    for shot in sorted(timeline.micro_shots, key=lambda item: item.index):
        if shot.scene_context == PREVIOUS_SHOT_CONTINUITY:
            previous_scene = resolved.get(shot.index - 1)
            if previous_scene is None:
                raise MicroVideoBatchError(
                    f"{shot.id} has unresolved previous-shot-continuity."
                )
            resolved[shot.index] = previous_scene
        else:
            resolved[shot.index] = shot.scene_context
    return resolved


def _references_for_shot(
    shot: MicroShot, references: dict[str, str]
) -> tuple[str, ...]:
    missing = [
        character_id
        for character_id in shot.character_ids
        if character_id not in references
    ]
    if missing:
        raise MicroVideoBatchError(
            f"Missing production character reference for {shot.id}: {', '.join(missing)}."
        )
    images = tuple(references[character_id] for character_id in shot.character_ids)
    if len(set(images)) != len(images):
        raise MicroVideoBatchError(
            f"Micro-video references are duplicated for {shot.id}."
        )
    return images


def _validate_job(job: MicroVideoJob, run_root: Path) -> tuple[int, MicroVideoJob]:
    if not isinstance(job, MicroVideoJob):
        raise MicroVideoBatchError("Micro-video jobs must be MicroVideoJob instances.")
    _require_production_model(job.model)
    if not isinstance(job.prompt, str) or not job.prompt.strip():
        raise MicroVideoBatchError(
            f"Micro-video prompt is empty for {job.micro_shot_id}."
        )
    if (
        isinstance(job.duration, bool)
        or not isinstance(job.duration, int)
        or not 4 <= job.duration <= 15
    ):
        raise MicroVideoBatchError(
            f"Seedance production duration must be 4-15 seconds for {job.micro_shot_id}."
        )
    if job.resolution != "1080p":
        raise MicroVideoBatchError(
            f"Micro-video job settings are invalid for {job.micro_shot_id}."
        )
    output = _lexical_path(job.output_path, "output path", require_absolute_exact=True)
    report_path = _lexical_path(
        job.report_path, "report path", require_absolute_exact=True
    )
    if output == report_path:
        raise MicroVideoBatchError(
            f"Micro-video output and report paths must differ for {job.micro_shot_id}."
        )
    candidate_number = _candidate_number_from_output(job.output_path)
    expected_output, expected_report = _candidate_paths(
        run_root,
        job.micro_shot_id,
        job.model,
        candidate_number,
    )
    if output != expected_output:
        raise MicroVideoBatchError(
            f"Micro-video job has a non-deterministic output path for {job.micro_shot_id}."
        )
    if report_path != expected_report:
        raise MicroVideoBatchError(
            f"Micro-video job has a non-deterministic report path for {job.micro_shot_id}."
        )
    _require_path_in_clips_root(output, run_root)
    _require_path_in_clips_root(report_path, run_root)
    if job.capability not in {"action_only", "speaking"}:
        raise MicroVideoBatchError(
            f"Micro-video job capability is invalid for {job.micro_shot_id}."
        )
    if job.capability == "speaking":
        image_roles = _image_roles(job.images, job.image_roles)
        anchored = bool(image_roles and image_roles[0] == "last_frame")
        first_frame_index = 1 if anchored else 0
        if (
            len(image_roles) < 2
            or image_roles[first_frame_index] != "first_frame"
            or image_roles.count("last_frame") != (1 if anchored else 0)
            or image_roles.count("first_frame") != 1
            or any(
                role != "reference_image"
                for role in image_roles[first_frame_index + 1 :]
            )
        ):
            raise MicroVideoBatchError(
                f"Speaking micro-video speaking frame evidence is invalid for {job.micro_shot_id}."
            )
        if (
            not job.audio_path
            or not job.audio_sha256
            or (anchored and not job.entry_anchor_id)
            or (not anchored and job.entry_anchor_id)
        ):
            raise MicroVideoBatchError(
                f"Speaking micro-video job evidence is incomplete for {job.micro_shot_id}."
            )
        audio = _lexical_path(job.audio_path, "audio reference")
        _require_path_in_run_root(audio, run_root, "audio reference")
        if not audio.is_file() or _sha256_file(audio) != job.audio_sha256:
            raise MicroVideoBatchError(
                f"Speaking micro-video audio is invalid for {job.micro_shot_id}."
            )
        if not isinstance(job.capability_provenance, Mapping):
            raise MicroVideoBatchError(
                f"Speaking micro-video speaking capability provenance is missing for {job.micro_shot_id}."
            )
        if (
            not isinstance(job.capability_provenance_sha256, str)
            or job.capability_provenance_sha256
            != _capability_provenance_sha256(job.capability_provenance)
        ):
            raise MicroVideoBatchError(
                f"Speaking micro-video speaking capability provenance is invalid for {job.micro_shot_id}."
            )
        try:
            from .model_bakeoff import ModelBakeoffError, require_speaking_capability

            require_speaking_capability(
                job.capability_provenance,
                job.model,
                job.micro_shot_id,
            )
        except ModelBakeoffError as exc:
            raise MicroVideoBatchError(
                f"Speaking micro-video speaking capability provenance is invalid for {job.micro_shot_id}: {exc}"
            ) from exc
    elif job.audio_path or job.audio_sha256:
        raise MicroVideoBatchError(
            f"Action-only micro-video job must not include dialogue audio for {job.micro_shot_id}."
        )
    images, image_roles = _validate_manual_images(job.images, job.image_roles, run_root)
    return candidate_number, replace(
        job,
        output_path=str(output),
        report_path=str(report_path),
        images=images,
        image_roles=image_roles,
    )


def _candidate_number_from_output(output_path: str | Path) -> int:
    output = _lexical_path(output_path, "output path")
    match = re.fullmatch(r"candidate_(\d{3})\.mp4", output.name)
    if not match:
        raise MicroVideoBatchError(
            f"Micro-video output path is not a deterministic candidate path: {output_path}"
        )
    candidate_number = int(match.group(1))
    _require_candidate_number(candidate_number)
    return candidate_number


def _safe_job_report(job: MicroVideoJob) -> dict[str, Any]:
    return {
        "micro_shot_id": job.micro_shot_id,
        "model": job.model,
        "prompt": job.prompt,
        "reference_image_count": len(job.images),
        "reference_images": [_reference_label(image) for image in job.images],
        "reference_image_roles": list(job.image_roles),
        "reference_audio_sha256": job.audio_sha256,
        "entry_anchor_id": job.entry_anchor_id,
        "capability": job.capability,
        "capability_provenance_sha256": job.capability_provenance_sha256,
        "duration": job.duration,
        "ratio": "9:16",
        "resolution": job.resolution,
        "output_path": job.output_path,
        "output_sha256": (
            _sha256_file(Path(job.output_path))
            if Path(job.output_path).is_file()
            else ""
        ),
        "report_path": job.report_path,
    }


def _reference_label(value: str) -> str:
    normalized = str(value).strip()
    parsed = urlsplit(normalized)
    if normalized.lower().startswith("data:") or parsed.scheme:
        return "[remote-url]"
    return Path(normalized).name


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise MicroVideoBatchError("Unable to hash micro-video dialogue audio.") from exc
    return digest.hexdigest()


def _capability_provenance(report: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        return json.loads(
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError) as exc:
        raise MicroVideoBatchError("Capability report cannot be serialized.") from exc


def _capability_provenance_sha256(report: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MicroVideoBatchError("Capability report cannot be serialized.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _report_count(result: dict[str, Any], key: str) -> int:
    value = result.get(key, 0)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _safe_data(value: Any, config: GatewayVideoConfig) -> Any:
    if isinstance(value, dict):
        return {
            _redact(str(key), config): (
                "[redacted]"
                if _is_sensitive_report_key(str(key))
                else _safe_data(item, config)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_data(item, config) for item in value]
    if isinstance(value, tuple):
        return [_safe_data(item, config) for item in value]
    if isinstance(value, str):
        return _redact(value, config)
    return value


def _is_sensitive_report_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z]", "", key.lower())
    return any(
        marker in normalized
        for marker in ("authorization", "apikey", "token", "secret", "credential")
    )


def _redact(value: str, config: GatewayVideoConfig) -> str:
    sanitized = value.replace(config.api_key, "[redacted]") if config.api_key else value
    sanitized = re.sub(
        r"(?i)([\"']?[A-Za-z0-9_-]*(?:authorization|api[-_]?key|token|secret|credential)[A-Za-z0-9_-]*[\"']?\s*[:=]\s*)(?!\[redacted\])(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|(?:bearer\s+)?[^\s,;}\]]+)",
        r"\1[redacted]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\bbearer[ \t]+(?!\[redacted\])\S+",
        "Bearer [redacted]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b[a-z][a-z0-9+.-]*:(?=\S)[^\s<>'\"]*",
        "[redacted-url]",
        sanitized,
    )
    return sanitized
