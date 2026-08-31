"""Local-only planning gate for the audio-first microshot handoff.

The preflight intentionally builds jobs but never renders them.  A paid video
request remains a separate, explicit later action.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .candidate_review import CandidateState, candidate_review_manifest_from_dict
from .dialogue_assets import DialogueAudioAsset, DialogueAudioManifest
from .file_io import read_json_object, write_json_atomic
from .micro_video_batch import MicroVideoBatchError, MicroVideoJob, build_micro_video_jobs
from .performance_card import performance_sheet_from_dict
from .schema import episode_from_dict
from .visual_timeline import visual_timeline_from_dict


AUDIO_FIRST_PREFLIGHT_SCHEMA = "motion-comic-factory.audio-first-preflight.v1"


class AudioFirstPreflightError(ValueError):
    """An on-disk audio-first planning artifact is missing or malformed."""


def run_audio_first_preflight(
    run_dir: str | Path, *, model: str
) -> dict[str, Any]:
    """Build local candidate plans and persist a report without rendering.

    Each character-bearing microshot is planned independently.  This allows an
    action-only card to remain ready when a visible-speaking card is blocked by
    absent audio, capability, or continuity evidence.
    """
    root = Path(run_dir).resolve()
    try:
        episode, timeline, sheet = _load_core_artifacts(root)
        manifest = read_dialogue_audio_manifest(root / "dialogue_audio_manifest.json")
        capability_report = _read_json(root / "model_bakeoff_report.json")
        character_assets = _read_json(root / "character_assets.json")
        scene_keyframes = _read_json(root / "scene_keyframes.json")
        candidate_review = candidate_review_manifest_from_dict(
            _read_json(root / "candidate_review.json")
        )
    except _VALIDATION_ERRORS as exc:
        return _write_preflight_report(root, model=model, jobs=[], errors=[str(exc)])

    jobs: list[MicroVideoJob] = []
    errors: list[str] = []
    approved_ids = {
        record.micro_shot_id
        for record in candidate_review.candidates
        if record.state is CandidateState.APPROVED
    }
    for micro_shot in timeline.micro_shots:
        if not micro_shot.character_ids or micro_shot.id in approved_ids:
            continue
        try:
            jobs.extend(
                build_micro_video_jobs(
                    episode,
                    timeline,
                    character_assets,
                    model=model,
                    run_dir=root,
                    candidate_number=1,
                    performance_sheet=sheet,
                    dialogue_manifest=manifest,
                    capability_report=capability_report,
                    scene_keyframes=scene_keyframes,
                    candidate_review=candidate_review,
                    micro_shot_ids=(micro_shot.id,),
                )
            )
        except _VALIDATION_ERRORS as exc:
            errors.append(str(exc))
    return _write_preflight_report(root, model=model, jobs=jobs, errors=errors)


def read_dialogue_audio_manifest(path: str | Path) -> DialogueAudioManifest:
    """Read the immutable dialogue-audio manifest from local JSON evidence."""
    source = Path(path)
    payload = _read_json(source)
    required = {"schema_version", "voiceover_audio", "voiceover_sha256", "assets"}
    if set(payload) != required:
        raise AudioFirstPreflightError(
            "Dialogue audio manifest has an invalid exact-key schema."
        )
    if payload["schema_version"] != "motion-comic-factory.dialogue-audio.v1":
        raise AudioFirstPreflightError("Dialogue audio manifest has an unsupported schema.")
    if not isinstance(payload["voiceover_audio"], str) or not isinstance(
        payload["voiceover_sha256"], str
    ):
        raise AudioFirstPreflightError("Dialogue audio manifest voiceover evidence is invalid.")
    if not isinstance(payload["assets"], list):
        raise AudioFirstPreflightError("Dialogue audio manifest assets must be a list.")
    assets: list[DialogueAudioAsset] = []
    for position, raw_asset in enumerate(payload["assets"], start=1):
        if not isinstance(raw_asset, Mapping) or set(raw_asset) != {
            "dialogue_id", "speaker_id", "path", "sha256", "duration_seconds", "voice_id"
        }:
            raise AudioFirstPreflightError(
                f"Dialogue audio manifest asset {position} has an invalid exact-key schema."
            )
        if not all(
            isinstance(raw_asset[key], str)
            for key in ("dialogue_id", "speaker_id", "path", "sha256", "voice_id")
        ) or not isinstance(raw_asset["duration_seconds"], (int, float)):
            raise AudioFirstPreflightError(
                f"Dialogue audio manifest asset {position} has invalid fields."
            )
        assets.append(
            DialogueAudioAsset(
                dialogue_id=raw_asset["dialogue_id"],
                speaker_id=raw_asset["speaker_id"],
                path=raw_asset["path"],
                sha256=raw_asset["sha256"],
                duration_seconds=float(raw_asset["duration_seconds"]),
                voice_id=raw_asset["voice_id"],
            )
        )
    return DialogueAudioManifest(
        assets=tuple(assets),
        path=str(source.resolve()),
        voiceover_audio=payload["voiceover_audio"],
        voiceover_sha256=payload["voiceover_sha256"],
        schema_version=payload["schema_version"],
    )


def _load_core_artifacts(root: Path):
    episode = episode_from_dict(_read_json(root / "episode.json"))
    timeline = visual_timeline_from_dict(_read_json(root / "visual_timeline.json"))
    sheet = performance_sheet_from_dict(
        _read_json(root / "performance_sheet.json"), episode, timeline
    )
    return episode, timeline, sheet


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return read_json_object(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AudioFirstPreflightError(f"Local preflight artifact is invalid: {path.name}: {exc}") from exc


def _write_preflight_report(
    root: Path,
    *,
    model: str,
    jobs: list[MicroVideoJob],
    errors: list[str],
) -> dict[str, Any]:
    report = {
        "schema_version": AUDIO_FIRST_PREFLIGHT_SCHEMA,
        "run_dir": str(root),
        "model": model,
        "success": not errors,
        "planned_count": len(jobs),
        "blocked_count": len(errors),
        "errors": errors,
        "jobs": [_planned_job(job) for job in jobs],
    }
    write_json_atomic(root / "preflight_report.json", report)
    return report


def _planned_job(job: MicroVideoJob) -> dict[str, Any]:
    return {
        "micro_shot_id": job.micro_shot_id,
        "model": job.model,
        "capability": job.capability,
        "output_path": job.output_path,
        "report_path": job.report_path,
        "reference_audio_sha256": job.audio_sha256,
        "entry_anchor_id": job.entry_anchor_id,
        "reference_image_roles": list(job.image_roles),
    }


_VALIDATION_ERRORS = (
    AudioFirstPreflightError,
    MicroVideoBatchError,
    OSError,
    TypeError,
    ValueError,
    KeyError,
)
