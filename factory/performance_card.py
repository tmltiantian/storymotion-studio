from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema import Episode, NARRATOR_ID
from .visual_timeline import PURPOSES, VisualTimeline


PERFORMANCE_SHEET_SCHEMA = "motion-comic-factory.performance-sheet.v1"


class PerformanceCardError(ValueError):
    pass


@dataclass(frozen=True)
class PerformanceCard:
    micro_shot_id: str
    purpose: str
    speaker_id: str
    dialogue_id: str
    requires_visible_lipsync: bool
    entry_anchor_id: str
    scene_keyframe_id: str
    actor_id: str
    target_id: str
    contact_point: str
    prop_hand: str
    start_beat: str
    main_beat: str
    end_beat: str
    negative_constraints: tuple[str, ...]


@dataclass(frozen=True)
class PerformanceSheet:
    project_id: str
    cards: tuple[PerformanceCard, ...]
    schema_version: str = PERFORMANCE_SHEET_SCHEMA


_SHEET_KEYS = frozenset(PerformanceSheet.__dataclass_fields__)
_CARD_KEYS = frozenset(PerformanceCard.__dataclass_fields__)
_STRING_CARD_FIELDS = tuple(key for key in _CARD_KEYS if key != "requires_visible_lipsync" and key != "negative_constraints")
_CONTACT_ACTION_CODES = frozenset({"grasp", "hand_over", "receive"})


def dialogue_id_for(parent_shot_id: str, dialogue_index: int) -> str:
    return f"{parent_shot_id}.dialogue_{dialogue_index:02d}"


def performance_sheet_from_dict(
    data: dict[str, Any], episode: Episode, timeline: VisualTimeline
) -> PerformanceSheet:
    if not isinstance(data, dict):
        raise PerformanceCardError("performance sheet must be an object.")
    _require_exact_keys(data, _SHEET_KEYS, "performance sheet")
    if data["schema_version"] != PERFORMANCE_SHEET_SCHEMA:
        raise PerformanceCardError("performance sheet has unsupported schema_version.")
    if not isinstance(data["project_id"], str) or not data["project_id"].strip():
        raise PerformanceCardError("performance sheet project_id must be a non-empty string.")
    if not isinstance(data["cards"], list):
        raise PerformanceCardError("performance sheet cards must be a list.")
    cards = tuple(
        _card_from_dict(item, position)
        for position, item in enumerate(data["cards"], start=1)
    )
    sheet = PerformanceSheet(
        project_id=data["project_id"], cards=cards, schema_version=data["schema_version"]
    )
    errors = validate_performance_sheet(sheet, episode, timeline)
    if errors:
        raise PerformanceCardError("; ".join(errors))
    return sheet


def validate_performance_sheet(
    sheet: PerformanceSheet, episode: Episode, timeline: VisualTimeline
) -> list[str]:
    if not isinstance(sheet, PerformanceSheet):
        return ["performance sheet must be a PerformanceSheet instance"]
    errors: list[str] = []
    if sheet.schema_version != PERFORMANCE_SHEET_SCHEMA:
        errors.append("performance sheet has unsupported schema_version")
    if not isinstance(sheet.project_id, str) or not sheet.project_id.strip():
        errors.append("performance sheet project_id must be a non-empty string")
    elif sheet.project_id != episode.project_id:
        errors.append("performance sheet project_id does not match episode")
    if not isinstance(sheet.cards, tuple):
        return [*errors, "performance sheet cards must be a tuple"]

    shots = {shot.id: shot for shot in timeline.micro_shots}
    cards = {card.micro_shot_id: card for card in sheet.cards if isinstance(card, PerformanceCard)}
    cards_match_timeline = len(cards) == len(sheet.cards) and set(cards) == set(shots)
    if not cards_match_timeline:
        errors.append("performance cards must match visual-timeline microshot ids")
    parent_by_id = {parent.id: parent for parent in episode.shots}
    source_dialogue_ids = {
        dialogue_id_for(parent.id, index)
        for parent in episode.shots
        for index, line in enumerate(parent.dialogue, start=1)
        if line.speaker_id != NARRATOR_ID
    }
    dialogue_binding_counts = {dialogue_id: 0 for dialogue_id in source_dialogue_ids}
    for position, card in enumerate(sheet.cards, start=1):
        if not isinstance(card, PerformanceCard):
            errors.append(f"performance card {position} must be a PerformanceCard instance")
            continue
        shot = shots.get(card.micro_shot_id)
        if shot is None:
            continue
        if len(shot.character_ids) > 2:
            errors.append(f"{card.micro_shot_id} has more than two characters")
        if card.purpose not in PURPOSES:
            errors.append(f"{card.micro_shot_id} purpose must be a canonical enum")
        elif card.purpose != shot.purpose:
            errors.append(f"{card.micro_shot_id} purpose does not match microshot")
        allowed_actor_ids = set(shot.character_ids)
        if shot.action_actor_id in {"object", "environment"}:
            allowed_actor_ids.add(shot.action_actor_id)
        if card.actor_id and card.actor_id not in allowed_actor_ids:
            errors.append(
                f"{card.micro_shot_id} actor_id must be an on-screen character or allowed object/environment actor"
            )
        if card.requires_visible_lipsync and not card.dialogue_id:
            errors.append(f"{card.micro_shot_id} visible speech requires dialogue_id")
        if card.requires_visible_lipsync and not card.speaker_id:
            errors.append(f"{card.micro_shot_id} visible speech requires speaker_id")
        if (
            card.requires_visible_lipsync
            and card.speaker_id
            and card.speaker_id not in shot.character_ids
        ):
            errors.append(
                f"{card.micro_shot_id} visible speaker must be in microshot character_ids"
            )
        parent = parent_by_id.get(shot.parent_shot_id)
        source_lines = parent.dialogue if parent is not None else []
        source_matches = [
            line
            for index, line in enumerate(source_lines, start=1)
            if dialogue_id_for(shot.parent_shot_id, index) == card.dialogue_id
        ]
        if card.requires_visible_lipsync and (
            len(source_matches) != 1 or source_matches[0].speaker_id == NARRATOR_ID
        ):
            errors.append(
                f"{card.micro_shot_id} dialogue_id does not identify one source line"
            )
        elif source_matches and source_matches[0].speaker_id != card.speaker_id:
            errors.append(
                f"{card.micro_shot_id} dialogue speaker does not match source line"
            )
        if card.dialogue_id in dialogue_binding_counts:
            dialogue_binding_counts[card.dialogue_id] += 1
        if shot.action_code in _CONTACT_ACTION_CODES:
            if not card.actor_id:
                errors.append(
                    f"{card.micro_shot_id} contact action requires exactly one actor_id"
                )
            if not card.contact_point:
                errors.append(
                    f"{card.micro_shot_id} contact action requires exactly one contact_point"
                )
    if cards_match_timeline:
        for dialogue_id, count in dialogue_binding_counts.items():
            if count != 1:
                errors.append(
                    f"source dialogue {dialogue_id} must bind exactly once (found {count})"
                )
    return errors


def _card_from_dict(data: Any, position: int) -> PerformanceCard:
    if not isinstance(data, dict):
        raise PerformanceCardError(f"performance card {position} must be an object.")
    _require_exact_keys(data, _CARD_KEYS, f"performance card {position}")
    for field in _STRING_CARD_FIELDS:
        if not isinstance(data[field], str):
            raise PerformanceCardError(f"performance card {position} {field} must be a string.")
    if not isinstance(data["requires_visible_lipsync"], bool):
        raise PerformanceCardError(
            f"performance card {position} requires_visible_lipsync must be a boolean."
        )
    if not isinstance(data["negative_constraints"], list) or not all(
        isinstance(item, str) for item in data["negative_constraints"]
    ):
        raise PerformanceCardError(
            f"performance card {position} negative_constraints must be a list of strings."
        )
    return PerformanceCard(
        **{
            **{field: data[field] for field in _STRING_CARD_FIELDS},
            "requires_visible_lipsync": data["requires_visible_lipsync"],
            "negative_constraints": tuple(data["negative_constraints"]),
        }
    )


def _require_exact_keys(data: dict[str, Any], expected: frozenset[str], label: str) -> None:
    missing = expected - data.keys()
    unexpected = data.keys() - expected
    if missing:
        raise PerformanceCardError(f"{label} missing keys: {', '.join(sorted(missing))}.")
    if unexpected:
        raise PerformanceCardError(
            f"{label} unexpected keys: {', '.join(sorted(unexpected))}."
        )
