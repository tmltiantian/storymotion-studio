from __future__ import annotations

from .prompt_safety import (
    STILL_HARD_CONSTRAINTS,
    PREVIOUS_SHOT_CONTINUITY,
    continuity_context_errors,
    episode_integrity_errors,
    extract_source_locations,
    normalize_text,
    optional_negative_phrases,
    render_action,
    video_hard_constraints,
)
from .performance_card import PerformanceCard
from .schema import Episode, Shot
from .visual_timeline import MicroShot, micro_shot_runtime_errors, validate_micro_shot


class PromptCompilerError(ValueError):
    pass


MOTION_REALISM = (
    "real-time natural acceleration and deceleration",
    "visible weight transfer and stable ground contact",
    "no floating, no uniform gliding, no slow motion",
    "no unexplained camera movement",
)


def compile_video_prompt(
    episode: Episode,
    shot: MicroShot,
    *,
    card: PerformanceCard | None = None,
    previous_scene_context: str | None = None,
) -> str:
    _require_runtime_micro_shot(shot)
    _require_episode_integrity(episode)
    parent = _parent_shot(episode, shot)
    if card is not None:
        _require_matching_card(shot, card)
    _require_safe_micro_shot(
        episode, shot, parent, character_free=not shot.character_ids
    )
    characters = {item.id: item for item in episode.characters}
    try:
        present = [characters[item] for item in shot.character_ids]
    except KeyError as exc:
        raise PromptCompilerError(
            f"{shot.id} references unknown character {exc.args[0]}."
        ) from exc
    camera = {
        "locked": "locked camera",
        "micro_pan": "one restrained lateral move under two percent of frame width",
        "object_insert": "locked object insert",
    }.get(shot.camera_mode)
    if camera is None:
        raise PromptCompilerError(
            f"{shot.id} has unsupported camera mode {shot.camera_mode!r}."
        )
    scene = _resolve_scene_context(episode, shot, previous_scene_context)
    parts = [
        episode.style,
        "vertical 9:16 cinematic motion comic",
        f"Scene: {scene}",
        f"Time: {shot.time_context}",
        "On-screen characters: " + (", ".join(item.name for item in present) or "none"),
        f"Opening composition, expression and pose: {scene}; {shot.emotion_start}; {shot.pose_start}",
        f"Only action: {render_action(episode, shot)}",
        *render_performance_clauses(card),
        f"Ending expression, gaze and pose: {shot.emotion_end}; {shot.gaze}; {shot.pose_end}",
        camera,
        *optional_negative_phrases(shot.negative_constraints),
        *video_hard_constraints(shot.camera_mode),
    ]
    return _join_prompt_parts(parts)


def render_performance_clauses(card: PerformanceCard | None) -> tuple[str, ...]:
    """Render card-specific motion constraints for every video prompt format."""
    if card is None:
        return ()
    parts = [
        f"Performance beats: start {card.start_beat}; main {card.main_beat}; end {card.end_beat}",
        f"Actor and target: {card.actor_id} -> {card.target_id or 'self'}",
        *MOTION_REALISM,
    ]
    if card.contact_point:
        parts.append(
            f"one visible contact at {card.contact_point}; no second contact"
        )
    if card.requires_visible_lipsync:
        parts.append(
            "the named speaker visibly speaks this one short line; no off-screen narration"
        )
    return tuple(parts)


def compile_still_prompt(
    episode: Episode,
    shot: MicroShot,
    *,
    previous_scene_context: str | None = None,
) -> str:
    _require_runtime_micro_shot(shot)
    _require_episode_integrity(episode)
    parent = _parent_shot(episode, shot)
    if shot.character_ids:
        raise PromptCompilerError(
            f"{shot.id} contains characters and cannot use the reference-free still route."
        )
    _require_safe_micro_shot(episode, shot, parent, character_free=True)
    camera = {
        "locked": "locked still composition",
        "micro_pan": "static composition with lateral framing margin for a restrained post-production pan under two percent of frame width",
        "object_insert": "locked object insert",
    }[shot.camera_mode]
    scene = _resolve_scene_context(episode, shot, previous_scene_context)
    parts = [
        episode.style,
        "cinematic motion-comic keyframe",
        f"Scene: {scene}",
        f"Time: {shot.time_context}",
        "On-screen characters: none",
        f"Opening composition, expression and pose: {scene}; {shot.emotion_start}; {shot.pose_start}",
        f"Only visible action: {render_action(episode, shot)}",
        f"Ending expression, gaze and pose: {shot.emotion_end}; {shot.gaze}; {shot.pose_end}",
        camera,
        *optional_negative_phrases(shot.negative_constraints),
        *STILL_HARD_CONSTRAINTS,
    ]
    return _join_prompt_parts(parts)


def _parent_shot(episode: Episode, shot: MicroShot) -> Shot:
    for parent in episode.shots:
        if parent.id == shot.parent_shot_id:
            return parent
    raise PromptCompilerError(f"{shot.id} has unknown parent {shot.parent_shot_id}.")


def _require_matching_card(shot: MicroShot, card: PerformanceCard) -> None:
    if not isinstance(card, PerformanceCard):
        raise PromptCompilerError("card must be a PerformanceCard instance.")
    if card.micro_shot_id != shot.id:
        raise PromptCompilerError(
            f"performance card {card.micro_shot_id} does not belong to {shot.id}."
        )


def _require_safe_micro_shot(
    episode: Episode, shot: MicroShot, parent: Shot, *, character_free: bool
) -> None:
    errors = validate_micro_shot(
        shot,
        episode,
        parent=parent,
        character_free=character_free,
        include_runtime=False,
    )
    if errors:
        raise PromptCompilerError("; ".join(errors))


def _join_prompt_parts(parts: list[str] | tuple[str, ...]) -> str:
    return ". ".join(item.strip(" .") for item in parts if item.strip()) + "."


def _require_runtime_micro_shot(shot: MicroShot) -> None:
    errors = micro_shot_runtime_errors(shot)
    if errors:
        raise PromptCompilerError("; ".join(errors))


def _require_episode_integrity(episode: Episode) -> None:
    errors = episode_integrity_errors(episode)
    if errors:
        raise PromptCompilerError("; ".join(errors))


def _resolve_scene_context(
    episode: Episode, shot: MicroShot, previous_scene_context: str | None
) -> str:
    if shot.scene_context != PREVIOUS_SHOT_CONTINUITY:
        return shot.scene_context
    if previous_scene_context is None:
        raise PromptCompilerError(
            f"{shot.id} previous-shot-continuity requires previous_scene_context."
        )
    context_errors = continuity_context_errors(previous_scene_context)
    if context_errors:
        raise PromptCompilerError("; ".join(context_errors))
    parent_position = next(
        (
            position
            for position, parent in enumerate(episode.shots)
            if parent.id == shot.parent_shot_id
        ),
        None,
    )
    prior_locations: tuple[str, ...] = ()
    for prior_parent in reversed(episode.shots[: parent_position or 0]):
        prior_locations = extract_source_locations(prior_parent)
        if prior_locations:
            break
    if prior_locations and not any(
        normalize_text(previous_scene_context) == normalize_text(location)
        for location in prior_locations
    ):
        raise PromptCompilerError(
            f"{shot.id} previous_scene_context is inconsistent with prior source locations."
        )
    return previous_scene_context
