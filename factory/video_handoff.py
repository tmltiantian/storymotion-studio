from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .character_assets import character_asset_by_id
from .file_io import write_json_atomic
from .h3_prompt_compiler import compile_h3_shot_prompt
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
    video_provider: str | None = None,
    video_model: str | None = None,
) -> dict[str, Any]:
    errors = validate_episode(episode)
    if errors:
        raise ValueError("; ".join(errors))

    assets_by_id = character_asset_by_id(character_assets)
    provider_profile = resolve_provider_profile(config, process_env=process_env)
    effective_provider = str(video_provider or provider_profile.video.provider).strip().lower()
    effective_model = str(video_model or provider_profile.video.model).strip()
    provider_report = provider_profile.to_report()
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
        character_ids = list(shot.character_ids)
        reference_character_ids = tuple(
            character_id
            for character_id in character_ids
            if bool(assets_by_id.get(character_id, {}).get("production_ready"))
            and bool(assets_by_id.get(character_id, {}).get("reference_image_exists"))
        )
        if (
            effective_provider == "minimax"
            and effective_model.lower() == "minimax-h3"
        ):
            video_prompt = compile_h3_shot_prompt(
                episode,
                shot,
                character_ids=tuple(character_ids),
                reference_character_ids=reference_character_ids,
            )
        else:
            video_prompt = f"{shot.visual_prompt} 运镜：{shot.camera}"
        shots.append(
            {
                "id": shot.id,
                "index": shot.index,
                "character_ids": character_ids,
                "action": shot.action,
                "dialogue": dialogue,
                "speaker": speaker,
                "image_prompt": shot.visual_prompt,
                "video_prompt": video_prompt,
                "camera": shot.camera,
                "duration_seconds": shot.duration_seconds,
                "audio_mood": shot.audio_mood,
            }
        )

    return {
        "schema_version": VIDEO_HANDOFF_SCHEMA,
        "project_id": episode.project_id,
        "title": episode.title,
        "video_provider": effective_provider,
        "video_model": effective_model,
        "provider_capabilities": provider_report["capabilities"],
        "characters": characters,
        "shots": shots,
    }


def write_video_handoff(
    episode: Episode,
    config: dict[str, Any],
    run_dir: str | Path,
    character_assets: dict[str, Any] | None = None,
    *,
    video_provider: str | None = None,
    video_model: str | None = None,
) -> Path:
    return write_json_atomic(
        Path(run_dir) / "video_handoff.json",
        build_video_handoff(
            episode,
            config,
            character_assets=character_assets,
            video_provider=video_provider,
            video_model=video_model,
        ),
    )
