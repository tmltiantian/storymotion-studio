from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .prompt_safety import (
    ACTION_TEMPLATES,
    OPTIONAL_NEGATIVE_CONSTRAINTS,
    PREVIOUS_SHOT_CONTINUITY,
    episode_integrity_errors,
    is_valid_concrete_scene_context,
    micro_shot_safety_errors,
)
from .schema import Episode


VISUAL_TIMELINE_SCHEMA = "motion-comic-factory.visual-timeline.v3"
CAMERA_MODES = {"locked", "micro_pan", "object_insert"}
CUT_MODES = {"hard_cut", "match_cut", "time_jump_black"}
PURPOSES = {"establishing", "action", "reaction", "object", "turn", "resolve"}


class VisualTimelineError(ValueError):
    pass


@dataclass(frozen=True)
class MicroShot:
    id: str
    index: int
    parent_shot_id: str
    scene_context: str
    time_context: str
    purpose: str
    character_ids: tuple[str, ...]
    emotion_start: str
    emotion_end: str
    emotion_intensity: int
    gaze: str
    pose_start: str
    pose_end: str
    action_actor_id: str
    action_code: str
    action_target: str
    camera_mode: str
    source_duration_seconds: int
    timeline_duration_seconds: int | float
    entry_cut: str
    exit_cut: str
    negative_constraints: tuple[str, ...]
    cadence_fps: int


@dataclass(frozen=True)
class VisualTimeline:
    project_id: str
    micro_shots: tuple[MicroShot, ...]
    schema_version: str = VISUAL_TIMELINE_SCHEMA


_ROOT_KEYS = frozenset({"schema_version", "project_id", "micro_shots"})
_MICRO_SHOT_KEYS = frozenset(MicroShot.__dataclass_fields__)
_STRING_FIELDS = (
    "id",
    "parent_shot_id",
    "scene_context",
    "time_context",
    "purpose",
    "emotion_start",
    "emotion_end",
    "gaze",
    "pose_start",
    "pose_end",
    "action_actor_id",
    "action_code",
    "action_target",
    "camera_mode",
    "entry_cut",
    "exit_cut",
)
_INTEGER_FIELDS = (
    "index",
    "emotion_intensity",
    "source_duration_seconds",
    "cadence_fps",
)


def visual_timeline_from_dict(data: dict[str, Any]) -> VisualTimeline:
    if not isinstance(data, dict):
        raise VisualTimelineError("visual timeline must be an object.")
    _require_exact_keys(data, _ROOT_KEYS, "visual timeline")
    if data["schema_version"] != VISUAL_TIMELINE_SCHEMA:
        raise VisualTimelineError("visual timeline has unsupported schema_version.")
    if not isinstance(data["project_id"], str) or not data["project_id"].strip():
        raise VisualTimelineError(
            "visual timeline project_id must be a non-empty string."
        )
    if not isinstance(data["micro_shots"], list):
        raise VisualTimelineError("visual timeline micro_shots must be a list.")
    micro_shots = tuple(
        _micro_shot_from_dict(item, position)
        for position, item in enumerate(data["micro_shots"], start=1)
    )
    return VisualTimeline(
        project_id=data["project_id"],
        micro_shots=micro_shots,
        schema_version=data["schema_version"],
    )


def visual_timeline_to_dict(timeline: VisualTimeline) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(timeline)))


def validate_visual_timeline(timeline: VisualTimeline, episode: Episode) -> list[str]:
    if not isinstance(timeline, VisualTimeline):
        return ["visual timeline must be a VisualTimeline instance"]
    integrity_errors = episode_integrity_errors(episode)
    if integrity_errors:
        return integrity_errors
    errors: list[str] = []
    parent_by_id = {item.id: item for item in episode.shots}
    duration_by_parent = {item.id: 0.0 for item in episode.shots}
    if not isinstance(timeline.schema_version, str):
        errors.append("visual timeline schema_version must be a string")
    elif timeline.schema_version != VISUAL_TIMELINE_SCHEMA:
        errors.append("visual timeline has unsupported schema_version")
    if not isinstance(timeline.project_id, str) or not timeline.project_id.strip():
        errors.append("visual timeline project_id must be a non-empty string")
    elif timeline.project_id != episode.project_id:
        errors.append("visual timeline project_id does not match episode")
    if not isinstance(timeline.micro_shots, tuple):
        errors.append("visual timeline micro_shots must be a tuple")
        return errors
    valid_items: list[MicroShot] = []
    for position, item in enumerate(timeline.micro_shots, start=1):
        runtime_errors = micro_shot_runtime_errors(item, label=f"micro-shot {position}")
        errors.extend(runtime_errors)
        if not runtime_errors:
            valid_items.append(item)
    if len({item.id for item in valid_items}) != len(valid_items):
        errors.append("visual timeline has duplicate micro-shot ids")
    indexes = [item.index for item in valid_items]
    if len(set(indexes)) != len(indexes):
        errors.append("visual timeline has duplicate micro-shot indexes")
    if len(valid_items) == len(timeline.micro_shots) and indexes != list(
        range(1, len(valid_items) + 1)
    ):
        errors.append("micro-shot indexes must be contiguous from 1")
    for item in valid_items:
        parent = parent_by_id.get(item.parent_shot_id)
        errors.extend(
            validate_micro_shot(
                item,
                episode,
                parent=parent,
                character_free=not item.character_ids,
                include_runtime=False,
            )
        )
        if parent is not None:
            duration_by_parent[item.parent_shot_id] += item.timeline_duration_seconds
    errors.extend(_continuity_resolution_errors(valid_items, parent_by_id))
    for parent_id, duration in duration_by_parent.items():
        expected = parent_by_id[parent_id].duration_seconds
        if abs(duration - expected) > 0.001:
            errors.append(
                f"{parent_id} visual duration {duration:.3f} does not match {expected:.3f}"
            )
    return errors


def micro_shot_runtime_errors(shot: Any, *, label: str = "micro-shot") -> list[str]:
    if not isinstance(shot, MicroShot):
        return [f"{label} must be a MicroShot instance"]
    return _field_value_errors(vars(shot), tuple, label)


def validate_micro_shot(
    shot: Any,
    episode: Episode,
    *,
    parent: Any = None,
    character_free: bool | None = None,
    include_runtime: bool = True,
) -> list[str]:
    errors = micro_shot_runtime_errors(shot) if include_runtime else []
    if errors:
        return errors
    integrity_errors = episode_integrity_errors(episode)
    if integrity_errors:
        return integrity_errors
    parent_by_id = {item.id: item for item in episode.shots}
    if parent is None:
        parent = parent_by_id.get(shot.parent_shot_id)
    if parent is None:
        return [f"{shot.id} has unknown parent {shot.parent_shot_id}"]
    known_character_ids = {item.id for item in episode.characters}
    if len(set(shot.character_ids)) != len(shot.character_ids):
        errors.append(f"{shot.id} has duplicate character_ids")
    for character_id in shot.character_ids:
        if character_id not in known_character_ids:
            errors.append(f"{shot.id} has unknown character {character_id}")
    errors.extend(
        micro_shot_safety_errors(
            episode,
            shot,
            parent,
            character_free=(
                not shot.character_ids if character_free is None else character_free
            ),
        )
    )
    if shot.purpose not in PURPOSES:
        errors.append(f"{shot.id} purpose must be a canonical enum value")
    if shot.camera_mode not in CAMERA_MODES:
        errors.append(f"{shot.id} has invalid camera_mode")
    if shot.camera_mode == "object_insert" and shot.character_ids:
        errors.append(f"{shot.id} object_insert requires a character-free shot")
    if shot.entry_cut not in CUT_MODES or shot.exit_cut not in CUT_MODES:
        errors.append(f"{shot.id} has invalid cut mode")
    if not 1 <= shot.emotion_intensity <= 5:
        errors.append(f"{shot.id} emotion_intensity must be 1-5")
    if not 1 <= shot.source_duration_seconds <= 15:
        errors.append(f"{shot.id} source duration must be 1-15 seconds")
    if not 2 <= shot.timeline_duration_seconds <= 4:
        errors.append(f"{shot.id} timeline duration must be 2-4 seconds")
    if not 1 <= shot.cadence_fps <= 10:
        errors.append(f"{shot.id} cadence_fps must be 1-10")
    return errors


def write_visual_timeline(timeline: VisualTimeline, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(visual_timeline_to_dict(timeline), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return output


def _micro_shot_from_dict(data: Any, position: int) -> MicroShot:
    if not isinstance(data, dict):
        raise VisualTimelineError(f"micro_shot {position} must be an object.")
    _require_exact_keys(data, _MICRO_SHOT_KEYS, f"micro_shot {position}")
    type_errors = _field_value_errors(data, list, f"micro_shot {position}")
    if type_errors:
        raise VisualTimelineError("; ".join(type_errors))
    if data["action_code"] not in ACTION_TEMPLATES:
        raise VisualTimelineError(
            f"micro_shot {position} action_code is not a production enum value."
        )
    if any(
        code not in OPTIONAL_NEGATIVE_CONSTRAINTS
        for code in data["negative_constraints"]
    ):
        raise VisualTimelineError(
            f"micro_shot {position} negative_constraints must contain only canonical codes."
        )
    return MicroShot(
        id=data["id"],
        index=data["index"],
        parent_shot_id=data["parent_shot_id"],
        scene_context=data["scene_context"],
        time_context=data["time_context"],
        purpose=data["purpose"],
        character_ids=tuple(data["character_ids"]),
        emotion_start=data["emotion_start"],
        emotion_end=data["emotion_end"],
        emotion_intensity=data["emotion_intensity"],
        gaze=data["gaze"],
        pose_start=data["pose_start"],
        pose_end=data["pose_end"],
        action_actor_id=data["action_actor_id"],
        action_code=data["action_code"],
        action_target=data["action_target"],
        camera_mode=data["camera_mode"],
        source_duration_seconds=data["source_duration_seconds"],
        timeline_duration_seconds=data["timeline_duration_seconds"],
        entry_cut=data["entry_cut"],
        exit_cut=data["exit_cut"],
        negative_constraints=tuple(data["negative_constraints"]),
        cadence_fps=data["cadence_fps"],
    )


def _require_exact_keys(
    data: dict[str, Any], expected: frozenset[str], label: str
) -> None:
    missing = expected - data.keys()
    unexpected = data.keys() - expected
    if missing:
        raise VisualTimelineError(
            f"{label} missing keys: {', '.join(sorted(missing))}."
        )
    if unexpected:
        raise VisualTimelineError(
            f"{label} has unexpected keys: {', '.join(sorted(unexpected))}."
        )


def _is_actual_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _field_value_errors(
    values: dict[str, Any], sequence_type: type, label: str
) -> list[str]:
    errors: list[str] = []
    for field in _STRING_FIELDS:
        value = values[field]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} {field} must be a non-empty string")
    for field in _INTEGER_FIELDS:
        if not _is_actual_int(values[field]):
            errors.append(f"{label} {field} must be an integer")
    duration = values["timeline_duration_seconds"]
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        errors.append(f"{label} timeline_duration_seconds must be a number")
    for field in ("character_ids", "negative_constraints"):
        value = values[field]
        if not isinstance(value, sequence_type) or any(
            not isinstance(entry, str) or not entry.strip() for entry in value
        ):
            errors.append(
                f"{label} {field} must be a {sequence_type.__name__} of non-empty strings"
            )
    return errors


def _continuity_resolution_errors(
    items: list[MicroShot], parent_by_id: dict[str, Any]
) -> list[str]:
    by_index = {item.index: item for item in items}
    resolved: dict[int, str] = {}
    errors: list[str] = []
    for item in sorted(items, key=lambda candidate: candidate.index):
        parent = parent_by_id.get(item.parent_shot_id)
        if item.scene_context != PREVIOUS_SHOT_CONTINUITY:
            if parent is not None and is_valid_concrete_scene_context(
                item.scene_context, parent
            ):
                resolved[item.index] = item.scene_context
            continue
        previous = by_index.get(item.index - 1)
        if previous is None:
            errors.append(
                f"{item.id} previous-shot-continuity requires an actual previous micro-shot at index {item.index - 1}."
            )
            continue
        concrete = resolved.get(previous.index)
        if concrete is None:
            errors.append(
                f"{item.id} previous-shot-continuity cannot resolve through the prior micro-shot."
            )
            continue
        resolved[item.index] = concrete
    return errors
