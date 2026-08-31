from __future__ import annotations

import json
import math
from typing import Any

from .gateway_text import GatewayTextClient
from .prompt_safety import (
    ACTION_TEMPLATES,
    OPTIONAL_NEGATIVE_CONSTRAINTS,
    PREVIOUS_SHOT_CONTINUITY,
    SOURCE_UNSPECIFIED_TIME,
    contains_literal,
    episode_integrity_errors,
    extract_source_locations,
    extract_source_time_expressions,
)
from .schema import Episode, NARRATOR_ID, episode_to_dict
from .performance_card import (
    PERFORMANCE_SHEET_SCHEMA,
    PerformanceCardError,
    PerformanceSheet,
    performance_sheet_from_dict,
)
from .visual_timeline import (
    CAMERA_MODES,
    CUT_MODES,
    PURPOSES,
    VISUAL_TIMELINE_SCHEMA,
    VisualTimeline,
    VisualTimelineError,
    validate_visual_timeline,
    visual_timeline_from_dict,
)


class PerformancePlanError(ValueError):
    pass


_PERFORMANCE_PLAN_KEYS = frozenset({"visual_timeline", "performance_sheet"})


def build_performance_plan_messages(episode: Episode) -> list[dict[str, str]]:
    source = episode_to_dict(episode)
    micro_keys = [
        "id",
        "index",
        "parent_shot_id",
        "scene_context",
        "time_context",
        "purpose",
        "character_ids",
        "emotion_start",
        "emotion_end",
        "emotion_intensity",
        "gaze",
        "pose_start",
        "pose_end",
        "action_actor_id",
        "action_code",
        "action_target",
        "camera_mode",
        "source_duration_seconds",
        "timeline_duration_seconds",
        "entry_cut",
        "exit_cut",
        "negative_constraints",
        "cadence_fps",
    ]
    card_keys = [
        "micro_shot_id",
        "purpose",
        "speaker_id",
        "dialogue_id",
        "requires_visible_lipsync",
        "entry_anchor_id",
        "scene_keyframe_id",
        "actor_id",
        "target_id",
        "contact_point",
        "prop_hand",
        "start_beat",
        "main_beat",
        "end_beat",
        "negative_constraints",
    ]
    example = _performance_plan_example(episode)
    allowed_character_ids = ", ".join(character.id for character in episode.characters)
    example_clause = (
        "Compact JSON example: "
        + json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        if example is not None
        else (
            "No populated JSON example is available for this Episode because it cannot "
            "be represented without invalid placeholders. Shape-only type schema: "
            "root(visual_timeline:object,performance_sheet:object); "
            "visual_timeline(all visual timeline keys); performance_sheet(schema_version,project_id,cards)."
        )
    )
    return [
        {
            "role": "system",
            "content": (
                "You are a motion-comic performance director. Return one JSON object only; "
                "no Markdown, fences, comments, prose, omitted keys, or extra keys. "
                "Root keys are exactly \"visual_timeline\", \"performance_sheet\". "
                f"visual_timeline schema_version must be {VISUAL_TIMELINE_SCHEMA}; "
                f"performance_sheet schema_version must be {PERFORMANCE_SHEET_SCHEMA}. "
                "Every visual_timeline micro-shot has exactly these keys: "
                + ", ".join(f'"{key}"' for key in micro_keys)
                + ". visual_timeline schema_version, project_id, and all textual micro-shot fields are strings; "
                "micro_shots, character_ids, and negative_constraints are arrays; index, "
                "emotion_intensity, source_duration_seconds, and cadence_fps are actual integer "
                "types, not booleans or numeric strings; timeline_duration_seconds is an actual "
                "integer or number type, not a boolean or numeric string. Split every parent "
                "shot into 2-4 second timeline durations. source_duration_seconds is 1-15; "
                "emotion_intensity is 1-5; cadence_fps is 1-10. Micro-shot indexes are contiguous "
                f"from 1. project_id must exactly equal {episode.project_id}. Micro-shot IDs "
                "and indexes are globally unique, and character IDs within each micro-shot are unique. "
                "Every parent shot must have micro-shots and each parent's timeline durations "
                "must sum exactly equal to that parent duration. scene_context is one exact "
                f"source-grounded location; use {PREVIOUS_SHOT_CONTINUITY} only after the first "
                "micro-shot when the current parent source has no location and the prior micro-shot "
                "resolves to a concrete scene. time_context is one current-scene source time expression; "
                f"use {SOURCE_UNSPECIFIED_TIME} when none is explicit. Allowed Episode character IDs: "
                + allowed_character_ids
                + ". Each character ID must be named in that parent action or dialogue. The only "
                "exceptions are a reaction including another present dialogue speaker, or a collective "
                "pronoun naming exactly both characters in an Episode with exactly two main characters. "
                "action_actor_id is an on-screen character ID or object or environment. "
                "object_insert requires character_ids=[] and action_actor_id object or environment. "
                "action_target is self only for supported self actions, otherwise one source-grounded "
                "noun phrase. action_code values are exactly: "
                + ", ".join(sorted(ACTION_TEMPLATES))
                + ". camera_mode values are exactly: "
                + ", ".join(sorted(CAMERA_MODES))
                + ". entry_cut and exit_cut values are exactly: "
                + ", ".join(sorted(CUT_MODES))
                + ". purpose values are exactly: "
                + ", ".join(sorted(PURPOSES))
                + ". Return negative constraint codes only: "
                + ", ".join(sorted(OPTIONAL_NEGATIVE_CONSTRAINTS))
                + ". Never write free-form action, denial, camera, transition, time-change, "
                "text, subtitle, watermark, logo, person invention, or instruction-override prose. "
                "Use self only as action_target, never in gaze, pose, emotion, scene, or time fields. "
                "performance_sheet cards contain one card per microshot and exactly these keys: "
                + ", ".join(f'\"{key}\"' for key in card_keys)
                + ". A visible spoken line maps to one non-narrator source dialogue. "
                "Every non-narrator source dialogue maps exactly once, and its visible "
                "speaker must be in that microshot's character_ids. "
                "A microshot has a maximum of two characters. A contact action has one actor "
                "and one contact point. "
                + example_clause
            ),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


def _performance_plan_example(episode: Episode) -> dict[str, Any] | None:
    if episode_integrity_errors(episode) or not episode.shots:
        return None
    micro_shots: list[dict[str, Any]] = []
    last_concrete_scene: str | None = None
    for parent in episode.shots:
        durations = _split_example_duration(parent.duration_seconds)
        if durations is None:
            return None
        locations = extract_source_locations(parent)
        if locations:
            scene_context = locations[0]
            last_concrete_scene = scene_context
        elif last_concrete_scene is not None:
            scene_context = PREVIOUS_SHOT_CONTINUITY
        else:
            return None
        character_ids = _example_character_ids(episode, parent)
        if character_ids:
            action_actor_id = character_ids[0]
            action_target = "self"
            purpose = "action"
            camera_mode = "locked"
            emotion_start = "神情平静"
            emotion_end = "神情平静"
            gaze = "视线稳定"
            pose_start = "姿态保持稳定"
            pose_end = "姿态保持稳定"
        else:
            if scene_context == PREVIOUS_SHOT_CONTINUITY:
                return None
            action_actor_id = "environment"
            action_target = scene_context
            purpose = "object"
            camera_mode = "object_insert"
            emotion_start = "静态氛围"
            emotion_end = "静态氛围"
            gaze = "构图焦点稳定"
            pose_start = "构图保持稳定"
            pose_end = "构图保持稳定"
        source_times = sorted(extract_source_time_expressions(parent))
        time_context = source_times[0] if source_times else SOURCE_UNSPECIFIED_TIME
        source_duration = min(15, max(1, int(round(parent.duration_seconds))))
        for duration in durations:
            index = len(micro_shots) + 1
            micro_shots.append(
                {
                    "id": f"micro_{index:03d}",
                    "index": index,
                    "parent_shot_id": parent.id,
                    "scene_context": scene_context,
                    "time_context": time_context,
                    "purpose": purpose,
                    "character_ids": list(character_ids),
                    "emotion_start": emotion_start,
                    "emotion_end": emotion_end,
                    "emotion_intensity": 3,
                    "gaze": gaze,
                    "pose_start": pose_start,
                    "pose_end": pose_end,
                    "action_actor_id": action_actor_id,
                    "action_code": "hold_still",
                    "action_target": action_target,
                    "camera_mode": camera_mode,
                    "source_duration_seconds": source_duration,
                    "timeline_duration_seconds": duration,
                    "entry_cut": "hard_cut",
                    "exit_cut": "hard_cut",
                    "negative_constraints": ["no_text"],
                    "cadence_fps": 8,
                }
            )
    visual_timeline = {
        "schema_version": VISUAL_TIMELINE_SCHEMA,
        "project_id": episode.project_id,
        "micro_shots": micro_shots,
    }
    try:
        timeline = visual_timeline_from_dict(visual_timeline)
    except VisualTimelineError:
        return None
    if validate_visual_timeline(timeline, episode):
        return None
    remaining_dialogue = [
        (parent.id, index, line.speaker_id)
        for parent in episode.shots
        for index, line in enumerate(parent.dialogue, start=1)
        if line.speaker_id != NARRATOR_ID
    ]
    cards = []
    for shot in micro_shots:
        match = next(
            (
                item
                for item in remaining_dialogue
                if item[0] == shot["parent_shot_id"]
                and item[2] in shot["character_ids"]
            ),
            None,
        )
        if match is not None:
            remaining_dialogue.remove(match)
        cards.append({
            "micro_shot_id": shot["id"],
            "purpose": shot["purpose"],
            "speaker_id": match[2] if match else "",
            "dialogue_id": f"{match[0]}.dialogue_{match[1]:02d}" if match else "",
            "requires_visible_lipsync": match is not None,
            "entry_anchor_id": "scene_start",
            "scene_keyframe_id": "scene_keyframe",
            "actor_id": shot["action_actor_id"],
            "target_id": shot["action_target"],
            "contact_point": "",
            "prop_hand": "",
            "start_beat": shot["pose_start"],
            "main_beat": shot["action_code"],
            "end_beat": shot["pose_end"],
            "negative_constraints": shot["negative_constraints"],
        })
    if remaining_dialogue:
        return None
    return {
        "visual_timeline": visual_timeline,
        "performance_sheet": {
            "schema_version": PERFORMANCE_SHEET_SCHEMA,
            "project_id": episode.project_id,
            "cards": cards,
        },
    }


def _split_example_duration(duration: Any) -> tuple[int | float, ...] | None:
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 2
    ):
        return None
    minimum_segments = math.ceil(duration / 4)
    maximum_segments = math.floor(duration / 2)
    if minimum_segments > maximum_segments:
        return None
    segment_count = minimum_segments
    segment_duration = duration / segment_count
    return tuple(segment_duration for _ in range(segment_count))


def _example_character_ids(episode: Episode, parent: Any) -> tuple[str, ...]:
    parent_text = " ".join([parent.action, *(line.text for line in parent.dialogue)])
    named = tuple(
        character.id
        for character in episode.characters
        if contains_literal(parent_text, character.name)
    )
    if named:
        return named
    if len(episode.characters) == 2 and any(
        contains_literal(parent_text, pronoun)
        for pronoun in ("他们", "二人", "两人", "they", "both of them", "they both")
    ):
        return tuple(character.id for character in episode.characters)
    return ()


def parse_performance_plan(
    content: str, episode: Episode, timeline: VisualTimeline | None = None
) -> tuple[VisualTimeline, PerformanceSheet] | VisualTimeline:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PerformancePlanError("performance plan must be valid JSON.") from exc
    if isinstance(payload, dict) and "visual_timeline" in payload:
        missing = _PERFORMANCE_PLAN_KEYS - payload.keys()
        unexpected = payload.keys() - _PERFORMANCE_PLAN_KEYS
        if missing:
            raise PerformancePlanError(
                f"performance plan missing keys: {', '.join(sorted(missing))}."
            )
        if unexpected:
            raise PerformancePlanError(
                f"performance plan unexpected keys: {', '.join(sorted(unexpected))}."
            )
    try:
        payload_timeline = payload.get("visual_timeline") if isinstance(payload, dict) else None
        parsed_timeline = visual_timeline_from_dict(
            payload_timeline if payload_timeline is not None else payload
        )
    except VisualTimelineError as exc:
        raise PerformancePlanError(str(exc)) from exc
    errors = validate_visual_timeline(parsed_timeline, episode)
    if errors:
        raise PerformancePlanError("; ".join(errors))
    if timeline is None:
        if not isinstance(payload, dict) or "performance_sheet" not in payload:
            return parsed_timeline
        timeline = parsed_timeline
    if not isinstance(payload, dict) or "performance_sheet" not in payload:
        raise PerformancePlanError("performance plan missing performance_sheet.")
    if timeline != parsed_timeline:
        raise PerformancePlanError("performance plan visual_timeline does not match supplied timeline.")
    try:
        sheet = performance_sheet_from_dict(payload["performance_sheet"], episode, timeline)
    except PerformanceCardError as exc:
        raise PerformancePlanError(str(exc)) from exc
    return timeline, sheet


def generate_performance_plan(
    episode: Episode,
    client: GatewayTextClient,
    *,
    allow_network: bool,
) -> tuple[VisualTimeline, dict[str, Any]]:
    result = client.chat(
        build_performance_plan_messages(episode),
        response_format={"type": "json_object"},
        allow_network=allow_network,
    )
    parsed = parse_performance_plan(result.content, episode)
    timeline = parsed[0] if isinstance(parsed, tuple) else parsed
    return timeline, result.to_report()
