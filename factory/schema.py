from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


NARRATOR_ID = "narrator"


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    role: str
    description: str
    visual_anchor: str
    voice_style: str


@dataclass(frozen=True)
class DialogueLine:
    speaker_id: str
    text: str
    emotion: str = "neutral"


@dataclass(frozen=True)
class Shot:
    id: str
    index: int
    scene_title: str
    action: str
    visual_prompt: str
    camera: str
    duration_seconds: float
    audio_mood: str
    dialogue: list[DialogueLine] = field(default_factory=list)
    character_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Episode:
    project_id: str
    title: str
    language: str
    style: str
    target_aspect_ratio: str
    target_resolution: str
    characters: list[Character]
    shots: list[Shot]


def episode_to_dict(episode: Episode) -> dict[str, Any]:
    return asdict(episode)


def dialogue_from_dict(data: dict[str, Any]) -> DialogueLine:
    return DialogueLine(
        speaker_id=str(data["speaker_id"]),
        text=str(data["text"]),
        emotion=str(data.get("emotion", "neutral")),
    )


def character_from_dict(data: dict[str, Any]) -> Character:
    return Character(
        id=str(data["id"]),
        name=str(data["name"]),
        role=str(data["role"]),
        description=str(data["description"]),
        visual_anchor=str(data["visual_anchor"]),
        voice_style=str(data["voice_style"]),
    )


def shot_from_dict(
    data: dict[str, Any],
    *,
    legacy_character_ids: list[str] | None = None,
) -> Shot:
    raw_character_ids = data.get("character_ids")
    if raw_character_ids is None:
        raw_character_ids = legacy_character_ids or []
    return Shot(
        id=str(data["id"]),
        index=int(data["index"]),
        scene_title=str(data["scene_title"]),
        action=str(data["action"]),
        visual_prompt=str(data["visual_prompt"]),
        camera=str(data["camera"]),
        duration_seconds=float(data["duration_seconds"]),
        audio_mood=str(data["audio_mood"]),
        character_ids=[str(item) for item in raw_character_ids],
        dialogue=[dialogue_from_dict(item) for item in data.get("dialogue", [])],
    )


def episode_from_dict(data: dict[str, Any]) -> Episode:
    characters = [character_from_dict(item) for item in data.get("characters", [])]
    legacy_character_ids = [character.id for character in characters]
    return Episode(
        project_id=str(data["project_id"]),
        title=str(data["title"]),
        language=str(data["language"]),
        style=str(data["style"]),
        target_aspect_ratio=str(data["target_aspect_ratio"]),
        target_resolution=str(data["target_resolution"]),
        characters=characters,
        shots=[
            shot_from_dict(item, legacy_character_ids=legacy_character_ids)
            for item in data.get("shots", [])
        ],
    )


def validate_episode(episode: Episode) -> list[str]:
    errors: list[str] = []

    if not episode.project_id:
        errors.append("project_id is required")
    if not episode.title:
        errors.append("title is required")
    if len(episode.characters) < 2:
        errors.append("at least two main characters are required")
    if not episode.shots:
        errors.append("at least one shot is required")

    character_ids = {character.id for character in episode.characters}
    if len(character_ids) != len(episode.characters):
        errors.append("character ids must be unique")

    shot_indexes = [shot.index for shot in episode.shots]
    if shot_indexes != sorted(shot_indexes):
        errors.append("shot indexes must be monotonically increasing")
    if len(set(shot_indexes)) != len(shot_indexes):
        errors.append("shot indexes must be unique")

    for shot in episode.shots:
        if shot.duration_seconds <= 0:
            errors.append(f"{shot.id} duration_seconds must be positive")
        if not shot.visual_prompt.strip():
            errors.append(f"{shot.id} visual_prompt is required")
        if len(set(shot.character_ids)) != len(shot.character_ids):
            errors.append(f"{shot.id} character_ids must be unique")
        for character_id in shot.character_ids:
            if character_id not in character_ids:
                errors.append(f"{shot.id} character {character_id!r} is unknown")
        for line in shot.dialogue:
            if line.speaker_id != NARRATOR_ID and line.speaker_id not in character_ids:
                errors.append(
                    f"{shot.id} dialogue speaker {line.speaker_id!r} is not a character or narrator"
                )
            if not line.text.strip():
                errors.append(f"{shot.id} dialogue text is empty")
            if (
                line.speaker_id != NARRATOR_ID
                and shot.character_ids
                and line.speaker_id not in shot.character_ids
            ):
                errors.append(
                    f"{shot.id} dialogue speaker {line.speaker_id!r} is not on screen"
                )

    return errors


def assert_valid_episode(episode: Episode) -> None:
    errors = validate_episode(episode)
    if errors:
        raise ValueError("; ".join(errors))
