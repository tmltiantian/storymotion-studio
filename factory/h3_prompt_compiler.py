from __future__ import annotations

from collections.abc import Sequence

from .schema import Character, Episode, NARRATOR_ID, Shot


class H3PromptCompilerError(ValueError):
    pass


def compile_h3_shot_prompt(
    episode: Episode,
    shot: Shot,
    *,
    character_ids: Sequence[str],
    reference_character_ids: Sequence[str] = (),
) -> str:
    characters = {character.id: character for character in episode.characters}
    present = _characters(character_ids, characters, "on-screen")
    referenced = _characters(reference_character_ids, characters, "reference")
    present_ids = {character.id for character in present}
    if any(character.id not in present_ids for character in referenced):
        raise H3PromptCompilerError(
            "H3 reference characters must also be present in the shot."
        )

    subject_labels = {
        character.id: f"<Subject {index}>"
        for index, character in enumerate(referenced, start=1)
    }
    picture_labels = {
        character.id: f"<Picture {index}>"
        for index, character in enumerate(referenced, start=1)
    }
    description = _shot_description(
        episode,
        shot,
        present,
        subject_labels=subject_labels,
    )
    soundscape = _soundscape(shot)
    if not referenced:
        return (
            f"integrated_multimodal_description: [Shot 1] {description}\n\n"
            f"overall_soundscape: {soundscape}\n\n"
            "non_diegetic_music: N/A"
        )

    definitions = "\n".join(
        f"{subject_labels[character.id]} is {character.name}, "
        f"{_identity_description(character)}, as shown in "
        f"{picture_labels[character.id]}."
        for character in referenced
    )
    summary_subjects = ", ".join(
        subject_labels[character.id] for character in referenced
    )
    retention = "\n".join(
        f"{subject_labels[character.id]} (appears in [Shot 1]): "
        "fully_preserved - identity, coat or clothing, facial features, body "
        "proportions, and signature markings remain unchanged."
        for character in referenced
    )
    return (
        f"subject_definitions:\n{definitions}\n\n"
        "summary:\n"
        f"[reference generation] The target video is one continuous shot in "
        f"{shot.scene_title}, preserving {summary_subjects} while performing the "
        "specified action and dialogue.\n\n"
        f"retention_analysis:\n{retention}\n\n"
        "detailed_description:\n"
        f"The target video uses {episode.style} with stable character identity, "
        "consistent lighting, and physically plausible motion.\n"
        f"[Shot 1] {description}\n\n"
        f"overall_soundscape:\n{soundscape}\n\n"
        "non_diegetic_music:\nN/A"
    )


def _characters(
    character_ids: Sequence[str],
    characters: dict[str, Character],
    label: str,
) -> list[Character]:
    normalized = tuple(str(character_id).strip() for character_id in character_ids)
    if any(not character_id for character_id in normalized):
        raise H3PromptCompilerError(f"H3 {label} character IDs cannot be empty.")
    if len(set(normalized)) != len(normalized):
        raise H3PromptCompilerError(f"H3 {label} character IDs cannot be duplicated.")
    unknown = [character_id for character_id in normalized if character_id not in characters]
    if unknown:
        raise H3PromptCompilerError(
            f"Unknown H3 {label} character: {', '.join(unknown)}."
        )
    return [characters[character_id] for character_id in normalized]


def _identity_description(character: Character) -> str:
    return ", ".join(
        part.strip(" .")
        for part in (character.description, character.visual_anchor)
        if part.strip()
    )


def _shot_description(
    episode: Episode,
    shot: Shot,
    present: Sequence[Character],
    *,
    subject_labels: dict[str, str],
) -> str:
    subject_text = "; ".join(
        f"{subject_labels.get(character.id, character.name)}, "
        f"{_identity_description(character)}"
        for character in present
    ) or "No character is visible"
    camera = _camera_instruction(shot.camera)
    dialogue = _dialogue_description(episode, shot, present, subject_labels)
    parts = [
        f"{episode.style}, {shot.visual_prompt.strip()}",
        f"The scene remains in {shot.scene_title}",
        f"The visible subjects are {subject_text}",
        f"The physical action unfolds in this order: {shot.action.strip()}",
        _motion_rhythm_instruction(),
        camera,
        "All paws, hands, limbs, props, contact points, weight shifts, and object "
        "trajectories remain anatomically correct and physically continuous",
    ]
    if dialogue:
        parts.append(dialogue)
    return ". ".join(part.strip(" .") for part in parts if part.strip()) + "."


def _motion_rhythm_instruction() -> str:
    return (
        "The action uses readable causal beats instead of one blended movement: "
        "establish the starting pose and trigger, use a brief reaction hold when "
        "the character changes intention, establish a support or contact point "
        "before applying force, show the resulting weight transfer or prop "
        "trajectory, and ensure the result settles before the next major action begins. "
        "Motion remains continuous but not uniformly smooth: use natural "
        "acceleration, deceleration, and short holds; never use constant-speed "
        "tweening, floating, gliding, teleporting, or over-smoothed interpolation"
    )


def _camera_instruction(camera: str) -> str:
    normalized = camera.strip()
    lowered = normalized.lower()
    if not normalized or lowered in {"static", "locked", "固定", "固定镜头"}:
        return "The camera holds a static shot for the entire duration"
    return (
        f"The camera performs {normalized} with small amplitude at slow speed, "
        "without sudden acceleration or an unmotivated viewpoint change"
    )


def _dialogue_description(
    episode: Episode,
    shot: Shot,
    present: Sequence[Character],
    subject_labels: dict[str, str],
) -> str:
    if not shot.dialogue:
        return ""
    characters = {character.id: character for character in episode.characters}
    speaker_ids = _episode_speaker_ids(episode)
    clauses: list[str] = []
    visible_names = " and ".join(character.name for character in present)
    for line in shot.dialogue:
        speaker_id = speaker_ids[line.speaker_id]
        dialogue = f"<d>[{_language_name(episode.language)}] {line.text}</d>"
        if line.speaker_id == NARRATOR_ID:
            closed = (
                f" while {visible_names} keep their lips completely closed"
                if visible_names
                else ""
            )
            clauses.append(
                f"The narrator ({speaker_id}) says in an off-screen voiceover: "
                f"{dialogue}{closed}"
            )
            continue
        character = characters.get(line.speaker_id)
        if character is None:
            raise H3PromptCompilerError(
                f"Unknown H3 dialogue speaker: {line.speaker_id}."
            )
        if character not in present:
            raise H3PromptCompilerError(
                f"H3 on-screen dialogue speaker {character.name} is not present."
            )
        source = subject_labels.get(character.id, character.name)
        clauses.append(
            f"{source} ({speaker_id}), using {character.voice_style}, physically "
            f"speaks with mouth movements naturally synchronized to the audible "
            f"syllables and says: {dialogue}; their lips close completely when the "
            "line ends"
        )
    return ". ".join(clauses)


def _episode_speaker_ids(episode: Episode) -> dict[str, str]:
    speaker_ids: dict[str, str] = {}
    for episode_shot in episode.shots:
        for line in episode_shot.dialogue:
            if line.speaker_id not in speaker_ids:
                speaker_ids[line.speaker_id] = f"S{len(speaker_ids) + 1}"
    return speaker_ids


def _language_name(language: str) -> str:
    normalized = language.strip().lower()
    if normalized.startswith("zh"):
        return "Chinese"
    if normalized.startswith("en"):
        return "English"
    return language.strip() or "Chinese"


def _soundscape(shot: Shot) -> str:
    mood = shot.audio_mood.strip(" .")
    lead = f"The audible scene follows this concrete sound direction: {mood}. " if mood else ""
    return (
        lead
        + "A stable room tone continues underneath. Every movement and prop contact "
        "uses one synchronized physical sound, with no duplicated dialogue, abrupt "
        "noise, or unrelated sound event."
    )
