from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .character_assets import character_asset_by_id
from .file_io import write_json_atomic
from .schema import Episode, validate_episode


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_openmontage_package(
    episode: Episode,
    config: dict[str, Any],
    character_assets: dict[str, Any] | None = None,
    *,
    run_dir: str | Path | None = None,
    shot_audio: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    errors = validate_episode(episode)
    if errors:
        raise ValueError("; ".join(errors))

    workspace = Path(config["workspace"])
    runs_dir = Path(config["runsDir"])
    run_dir = Path(run_dir) if run_dir is not None else runs_dir / episode.project_id
    openmontage_path = Path(config["sources"]["openMontage"])
    assets_by_character_id = character_asset_by_id(character_assets)
    characters = []
    for character in episode.characters:
        data = asdict(character)
        asset = assets_by_character_id.get(character.id, {})
        data["reference_image_path"] = asset.get("reference_image_path", "")
        data["reference_image_exists"] = asset.get("reference_image_exists", False)
        characters.append(data)

    package = {
        "schema_version": "motion-comic-factory.openmontage.v1",
        "project_id": episode.project_id,
        "title": episode.title,
        "mode": "dry_run_handoff",
        "openmontage_path": str(openmontage_path),
        "openmontage_available": openmontage_path.exists(),
        "render_runtime": "remotion" if openmontage_path.exists() else "ffmpeg",
        "target": {
            "aspect_ratio": episode.target_aspect_ratio,
            "resolution": episode.target_resolution,
            "fps": config["episodeDefaults"]["targetFps"],
            "motion_cadence_fps": config["episodeDefaults"].get(
                "motionCadenceFps",
                config["episodeDefaults"]["targetFps"],
            ),
            "final_video": str(run_dir / "final.mp4"),
            "subtitle_srt": str(run_dir / "subtitles.srt"),
            "audio_mix": str(run_dir / "audio_mix.wav"),
        },
        "character_assets": character_assets
        or {
            "schema_version": "motion-comic-factory.character-assets.v1",
            "project_id": episode.project_id,
            "asset_ready": False,
            "characters": [],
        },
        "characters": characters,
        "timeline": [
            {
                "shot_id": shot.id,
                "index": shot.index,
                "duration_seconds": shot.duration_seconds,
                "visual_prompt": shot.visual_prompt,
                "camera": shot.camera,
                "audio_mood": shot.audio_mood,
                "dialogue": [line.__dict__ for line in shot.dialogue],
                "expected_assets": {
                    "first_frame": str(run_dir / "frames" / f"{shot.id}_first.png"),
                    "video_clip": str(run_dir / "clips" / f"{shot.id}.mp4"),
                    "voice_audio": str(
                        (shot_audio or {}).get(
                            shot.id, run_dir / "audio" / f"{shot.id}.wav"
                        )
                    ),
                },
            }
            for shot in episode.shots
        ],
        "factory_workspace": str(workspace),
    }
    return package


def validate_openmontage_package(package: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if package.get("schema_version") != "motion-comic-factory.openmontage.v1":
        errors.append("unsupported openmontage package schema_version")
    if not package.get("project_id"):
        errors.append("project_id is required")
    if package.get("render_runtime") not in {"remotion", "hyperframes", "ffmpeg"}:
        errors.append("render_runtime must be remotion, hyperframes, or ffmpeg")

    target = package.get("target") or {}
    for key in ("final_video", "subtitle_srt", "audio_mix"):
        if not target.get(key):
            errors.append(f"target.{key} is required")

    timeline = package.get("timeline") or []
    if not timeline:
        errors.append("timeline must contain at least one shot")
    indexes = [item.get("index") for item in timeline]
    if indexes != sorted(indexes):
        errors.append("timeline indexes must be sorted")
    for item in timeline:
        if not item.get("visual_prompt"):
            errors.append(f"{item.get('shot_id', '<unknown>')} visual_prompt is required")
        if not item.get("expected_assets", {}).get("video_clip"):
            errors.append(f"{item.get('shot_id', '<unknown>')} expected_assets.video_clip is required")
    return errors


def write_openmontage_package(
    episode: Episode,
    config: dict[str, Any],
    character_assets: dict[str, Any] | None = None,
    *,
    run_dir: str | Path | None = None,
    shot_audio: dict[str, str | Path] | None = None,
) -> Path:
    run_dir = (
        Path(run_dir)
        if run_dir is not None
        else Path(config["runsDir"]) / episode.project_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    for child in ("frames", "clips", "audio"):
        (run_dir / child).mkdir(exist_ok=True)

    package = build_openmontage_package(
        episode,
        config,
        character_assets=character_assets,
        run_dir=run_dir,
        shot_audio=shot_audio,
    )
    package_errors = validate_openmontage_package(package)
    if package_errors:
        raise ValueError("; ".join(package_errors))
    return write_json_atomic(run_dir / "openmontage_package.json", package)
