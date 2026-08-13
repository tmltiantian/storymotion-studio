from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .character_assets import character_asset_by_id
from .file_io import write_json_atomic
from .provider_profile import resolve_provider_profile
from .schema import Episode, NARRATOR_ID, validate_episode


VIDEO_HANDOFF_SCHEMA = "motion-comic-factory.video-handoff.v1"


def _speaker_name(episode: Episode, speaker_id: str) -> str:
    if speaker_id == NARRATOR_ID:
        return "旁白"
    return next(
        (
            character.name
            for character in episode.characters
            if character.id == speaker_id
        ),
        speaker_id,
    )


def _primary_dialogue(
    episode: Episode, shot_index: int
) -> tuple[str | None, str | None]:
    shot = episode.shots[shot_index]
    line = next(
        (item for item in shot.dialogue if item.speaker_id != NARRATOR_ID),
        shot.dialogue[0] if shot.dialogue else None,
    )
    if line is None:
        return None, None
    return _speaker_name(episode, line.speaker_id), line.text


def build_video_handoff(
    episode: Episode,
    config: dict[str, Any],
    character_assets: dict[str, Any] | None = None,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    errors = validate_episode(episode)
    if errors:
        raise ValueError("; ".join(errors))

    assets_by_id = character_asset_by_id(character_assets)
    provider_report = resolve_provider_profile(
        config, process_env=process_env
    ).to_report()
    characters = []
    for character in episode.characters:
        asset = assets_by_id.get(character.id, {})
        characters.append(
            {
                "id": character.id,
                "name": character.name,
                "description": character.description,
                "visual_anchor": character.visual_anchor,
                "voice_style": character.voice_style,
                "reference_image_path": str(
                    asset.get("reference_image_path") or ""
                ),
                "reference_image_exists": bool(
                    asset.get("reference_image_exists")
                ),
                "production_ready": bool(asset.get("production_ready")),
            }
        )

    shots = []
    for offset, shot in enumerate(episode.shots):
        speaker, dialogue = _primary_dialogue(episode, offset)
        character_ids = [
            character.id
            for character in episode.characters
            if character.name in shot.action
            or any(
                line.speaker_id == character.id for line in shot.dialogue
            )
        ] or [character.id for character in episode.characters]
        shots.append(
            {
                "id": shot.id,
                "index": shot.index,
                "character_ids": character_ids,
                "action": shot.action,
                "dialogue": dialogue,
                "speaker": speaker,
                "image_prompt": shot.visual_prompt,
                "video_prompt": f"{shot.visual_prompt} 运镜：{shot.camera}",
                "camera": shot.camera,
                "duration_seconds": shot.duration_seconds,
                "audio_mood": shot.audio_mood,
            }
        )

    return {
        "schema_version": VIDEO_HANDOFF_SCHEMA,
        "project_id": episode.project_id,
        "title": episode.title,
        "provider_capabilities": provider_report["capabilities"],
        "characters": characters,
        "shots": shots,
    }


def write_video_handoff(
    episode: Episode,
    config: dict[str, Any],
    run_dir: str | Path,
    character_assets: dict[str, Any] | None = None,
) -> Path:
    return write_json_atomic(
        Path(run_dir) / "video_handoff.json",
        build_video_handoff(
            episode,
            config,
            character_assets=character_assets,
        ),
    )
