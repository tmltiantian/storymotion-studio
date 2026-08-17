from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from .pipeline_contracts import StageName


SUPPORTED_SCHEMAS = {
    "motion-comic-factory.concept.v1": "concept",
    "motion-comic-factory.script.v1": "script",
    "motion-comic-factory.episode.v1": "storyboard",
    "motion-comic-factory.character-assets.v1": "assets",
    "motion-comic-factory.asset-review.v1": "assets",
    "motion-comic-factory.audio.v1": "audio",
    "motion-comic-factory.video.v1": "video",
    "motion-comic-factory.edit.v1": "edit",
    "motion-comic-factory.eval.v1": "eval",
    "motion-comic-factory.eval.v2": "eval",
    "motion-comic-factory.delivery.v1": "deliver",
}

_TARGET_STRING_FIELDS = {
    "aspect_ratio": ("aspect_ratio", "target_aspect_ratio"),
    "resolution": ("resolution", "target_resolution"),
}
_TARGET_NUMBER_FIELDS = {
    "duration_seconds": ("duration_seconds", "target_duration_seconds"),
    "fps": ("fps",),
    "shots": ("shots",),
}
_SAFE_EVIDENCE_KEYS = (
    "overlap_count",
    "actual_seconds",
    "expected_seconds",
    "tolerance_seconds",
    "expected",
    "actual",
)
_PASS_STATUSES = {"approved", "auto_approved", "pass", "passed", "success"}


def _public_string(value: Any, *, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if isinstance(value, int):
        return value
    return number


def _finite_float(value: Any) -> float | None:
    number = _finite_number(value)
    return float(number) if number is not None else None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _items(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _ready(stage: StageName, fields: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"stage": stage.value, "state": "ready"}
    result.update(fields)
    return result


def _unavailable(stage: StageName) -> dict[str, Any]:
    return {"stage": stage.value, "state": "unavailable"}


def _schema(document: Mapping[str, Any]) -> str:
    return _public_string(document.get("schema_version"))


def _documents_for(
    documents: Sequence[Mapping[str, Any]] | Sequence[Any], schemas: set[str]
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        document
        for document in documents
        if isinstance(document, Mapping) and _schema(document) in schemas
    )


def _creator_character(source: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for public_name, source_name in (
        ("name", "name"),
        ("role", "role"),
        ("description", "description"),
        ("appearance", "visual_anchor"),
        ("voice", "voice_style"),
    ):
        value = _public_string(source.get(source_name))
        if value:
            result[public_name] = value
    return result


def _character_name_map(characters: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_character in _items(characters):
        character = _mapping(raw_character)
        if character is None:
            continue
        name = _public_string(character.get("name"))
        character_id = _public_string(character.get("id"))
        if name and character_id:
            result[character_id] = name
    return result


def _dialogue_projection(
    dialogue: Any, character_names: Mapping[str, str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_line in _items(dialogue):
        line = _mapping(raw_line)
        if line is None:
            continue
        speaker_id = _public_string(line.get("speaker_id"))
        speaker = "旁白" if speaker_id == "narrator" else character_names.get(speaker_id, "")
        if not speaker:
            speaker = _public_string(line.get("speaker"))
        if not speaker:
            continue
        item: dict[str, Any] = {"speaker": speaker}
        emotion = _public_string(line.get("emotion"))
        text = _public_string(line.get("text"))
        if emotion:
            item["emotion"] = emotion
        if text:
            item["text"] = text
        result.append(item)
    return result


def _shot_projection(
    source: Mapping[str, Any], character_names: Mapping[str, str]
) -> tuple[dict[str, Any], float | None]:
    result: dict[str, Any] = {}
    index = _finite_number(source.get("index"))
    if index is not None:
        result["index"] = int(index) if float(index).is_integer() else index
    for public_name, source_name in (
        ("title", "scene_title"),
        ("action", "action"),
        ("camera", "camera"),
    ):
        value = _public_string(source.get(source_name))
        if value:
            result[public_name] = value
    duration = _finite_float(source.get("duration_seconds"))
    if duration is not None:
        result["duration_seconds"] = duration
    dialogue = _dialogue_projection(source.get("dialogue"), character_names)
    if dialogue:
        result["dialogue"] = dialogue
    total_duration = duration if duration is not None and duration > 0 else None
    return result, total_duration


def _episode_projection(root: Mapping[str, Any], stage: StageName) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    title = _public_string(root.get("title"))
    if title:
        fields["title"] = title

    raw_characters = _items(root.get("characters"))
    character_names = _character_name_map(raw_characters)
    characters = [
        projected
        for raw_character in raw_characters
        if (character := _mapping(raw_character)) is not None
        for projected in (_creator_character(character),)
        if projected
    ]
    if characters:
        fields["characters"] = characters

    shots: list[dict[str, Any]] = []
    total_duration = 0.0
    has_duration = False
    for raw_shot in _items(root.get("shots")):
        shot = _mapping(raw_shot)
        if shot is None:
            continue
        projected, duration = _shot_projection(shot, character_names)
        if projected:
            shots.append(projected)
        if duration is not None:
            total_duration += duration
            has_duration = True
    if shots:
        fields["shots"] = shots
    if has_duration:
        fields["total_duration_seconds"] = total_duration
    return fields


def _target_projection(source: Mapping[str, Any]) -> dict[str, Any]:
    target = _mapping(source.get("target")) or source
    result: dict[str, Any] = {}
    for public_name, source_names in _TARGET_STRING_FIELDS.items():
        for source_name in source_names:
            value = _public_string(target.get(source_name))
            if value:
                result[public_name] = value
                break
    for public_name, source_names in _TARGET_NUMBER_FIELDS.items():
        for source_name in source_names:
            value = _finite_number(target.get(source_name))
            if value is not None:
                result[public_name] = value
                break
    return result


def _concept(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.concept.v1"})
    if not supported:
        return None
    source = supported[0]
    fields: dict[str, Any] = {}
    for name in ("title", "premise"):
        value = _public_string(source.get(name))
        if value:
            fields[name] = value
    target = _target_projection(source)
    if target:
        fields["target"] = target
    characters = [
        projected
        for raw_character in _items(source.get("characters"))
        if (character := _mapping(raw_character)) is not None
        for projected in (_creator_character(character),)
        if projected
    ]
    if characters:
        fields["characters"] = characters
    return _ready(StageName.CONCEPT, fields)


def _script(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.script.v1"})
    if not supported:
        return None
    root = _mapping(supported[0].get("episode_draft"))
    if root is None:
        return _ready(StageName.SCRIPT, {})
    return _ready(StageName.SCRIPT, _episode_projection(root, StageName.SCRIPT))


def _is_episode_document(document: Mapping[str, Any]) -> bool:
    schema = _schema(document)
    if schema == "motion-comic-factory.episode.v1":
        return True
    return not schema and isinstance(document.get("title"), str) and (
        isinstance(document.get("characters"), list)
        or isinstance(document.get("shots"), list)
    )


def _storyboard(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = tuple(
        document
        for document in documents
        if isinstance(document, Mapping) and _is_episode_document(document)
    )
    if not supported:
        return None
    return _ready(StageName.STORYBOARD, _episode_projection(supported[0], StageName.STORYBOARD))


def _assets(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    asset_documents = _documents_for(
        documents, {"motion-comic-factory.character-assets.v1"}
    )
    review_documents = _documents_for(
        documents, {"motion-comic-factory.asset-review.v1"}
    )
    if not asset_documents and not review_documents:
        return None

    fields: dict[str, Any] = {"production_ready": False}
    if asset_documents:
        asset_document = asset_documents[0]
        if isinstance(asset_document.get("production_ready"), bool):
            fields["production_ready"] = asset_document["production_ready"]
        characters: list[dict[str, Any]] = []
        for raw_character in _items(asset_document.get("characters")):
            character = _mapping(raw_character)
            if character is None:
                continue
            item: dict[str, Any] = {}
            name = _public_string(character.get("name"))
            if name:
                item["name"] = name
            item["ready"] = character.get("production_ready") is True
            source_status = _public_string(character.get("provenance_status"))
            if source_status:
                item["source_status"] = source_status
            if item:
                characters.append(item)
        if characters:
            fields["characters"] = characters
    elif review_documents:
        review_ready = review_documents[0].get("production_ready")
        if isinstance(review_ready, bool):
            fields["production_ready"] = review_ready

    review_items: list[str] = []
    for review_document in review_documents:
        for raw_item in _items(review_document.get("review_items")):
            item = _public_string(raw_item)
            if item:
                review_items.append(item)
    if review_items:
        fields["review_items"] = review_items
    return _ready(StageName.ASSETS, fields)


def _audio(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.audio.v1"})
    if not supported:
        return None
    source = supported[0]
    timing_items: list[dict[str, Any]] = []
    speaker_counts: dict[str, int] = {}
    total_duration: float | None = None
    for raw_timing in _items(source.get("timings")):
        timing = _mapping(raw_timing)
        if timing is None:
            continue
        speaker = _public_string(timing.get("speaker_name")) or _public_string(
            timing.get("speaker")
        )
        item: dict[str, Any] = {}
        if speaker:
            item["speaker"] = speaker
            speaker_counts[speaker] = speaker_counts.get(speaker, 0) + 1
        text = _public_string(timing.get("text"))
        if text:
            item["text"] = text
        start = _finite_float(timing.get("start_seconds"))
        end = _finite_float(timing.get("end_seconds"))
        if start is not None:
            item["start_seconds"] = start
        if end is not None:
            item["end_seconds"] = end
            total_duration = end if total_duration is None else max(total_duration, end)
        if item:
            timing_items.append(item)

    fields: dict[str, Any] = {"dialogue_count": len(timing_items)}
    if total_duration is not None:
        fields["total_duration_seconds"] = total_duration
    if speaker_counts:
        fields["speakers"] = [
            {"name": name, "line_count": count}
            for name, count in speaker_counts.items()
        ]
    if timing_items:
        fields["timings"] = timing_items
    return _ready(StageName.AUDIO, fields)


def _video(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.video.v1"})
    if not supported:
        return None
    source = supported[0]
    fields: dict[str, Any] = {}
    generation_mode = _public_string(source.get("generation_mode"))
    if generation_mode:
        fields["generation_mode"] = generation_mode
    clips = source.get("clips")
    if isinstance(clips, (list, tuple)):
        fields["clip_count"] = len(clips)
    elif isinstance(source.get("clip_by_shot"), Mapping):
        fields["clip_count"] = len(source["clip_by_shot"])
    if isinstance(source.get("cloud_generation_requested"), bool):
        fields["cloud_generated"] = source["cloud_generation_requested"]
    lip_sync = _public_string(source.get("lip_sync_policy"))
    if lip_sync:
        fields["lip_sync"] = lip_sync
    return _ready(StageName.VIDEO, fields)


def _edit(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.edit.v1"})
    if not supported:
        return None
    source = supported[0]
    fields: dict[str, Any] = {}
    duration = _finite_float(source.get("duration_seconds"))
    if duration is not None:
        fields["duration_seconds"] = duration
    for public_name, source_name in (
        ("transition", "transition_policy"),
        ("assembly", "assembly_policy"),
    ):
        value = _public_string(source.get(source_name))
        if value:
            fields[public_name] = value
    fields["subtitle_ready"] = bool(source.get("subtitles"))
    return _ready(StageName.EDIT, fields)


def _human_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        return _public_string(value, maximum=200)
    if _finite_number(value) is not None:
        return _public_string(str(value), maximum=200)
    return ""


def _evidence_findings(evidence: Any) -> list[str]:
    if not isinstance(evidence, Mapping):
        return []
    findings: list[str] = []
    for key in _SAFE_EVIDENCE_KEYS:
        if key not in evidence:
            continue
        value = _human_scalar(evidence[key])
        if value:
            findings.append(f"{key}: {value}")
    return findings


def _check_projection(source: Mapping[str, Any], *, default_severity: str = "info") -> dict[str, Any] | None:
    name = _public_string(source.get("name")) or _public_string(source.get("message"))
    if not name:
        return None
    severity = _public_string(source.get("severity"))
    if severity not in {"error", "warning", "info"}:
        severity = default_severity
    result: dict[str, Any] = {
        "name": name,
        "severity": severity,
        "passed": source.get("passed") is True,
    }
    findings = [
        item
        for raw_item in _items(source.get("findings"))
        if (item := _public_string(raw_item))
    ]
    if findings:
        result["findings"] = findings
    return result


def _summary_check(
    name: str, values: tuple[tuple[str, Any], ...], passed: bool
) -> dict[str, Any]:
    findings = [
        f"{key}: {value}"
        for key, raw_value in values
        if (value := _human_scalar(raw_value))
    ]
    result: dict[str, Any] = {
        "name": name,
        "severity": "info",
        "passed": passed,
    }
    if findings:
        result["findings"] = findings
    return result


def _eval(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(
        documents,
        {"motion-comic-factory.eval.v1", "motion-comic-factory.eval.v2"},
    )
    if not supported:
        return None
    source = supported[0]
    fields: dict[str, Any] = {}
    automatic_passed = source.get("automatic_passed")
    if isinstance(automatic_passed, bool):
        fields["passed"] = automatic_passed
    elif isinstance(source.get("passed"), bool):
        fields["passed"] = source["passed"]
    else:
        fields["passed"] = False
    status = _public_string(source.get("status"))
    if status:
        fields["status"] = status

    checks: list[dict[str, Any]] = []
    if _schema(source) == "motion-comic-factory.eval.v2":
        for raw_failure in _items(source.get("hard_failures")):
            failure = _mapping(raw_failure)
            if failure is None:
                continue
            name = _public_string(failure.get("message"))
            if not name:
                continue
            check: dict[str, Any] = {
                "name": name,
                "severity": "error",
                "passed": False,
            }
            findings = _evidence_findings(failure.get("evidence"))
            if findings:
                check["findings"] = findings
            checks.append(check)
        timing = _mapping(source.get("timing"))
        if timing is not None:
            values = tuple(
                (key, timing[key])
                for key in ("cue_count", "overlap_count")
                if _finite_number(timing.get(key)) is not None
            )
            if values:
                overlap = _finite_number(timing.get("overlap_count"))
                checks.append(_summary_check("timing", values, overlap in (None, 0)))
        shots = _mapping(source.get("shots"))
        if shots is not None:
            values = tuple(
                (key, shots[key])
                for key in ("expected_count", "rendered_count")
                if _finite_number(shots.get(key)) is not None
            )
            if values:
                expected = _finite_number(shots.get("expected_count"))
                rendered = _finite_number(shots.get("rendered_count"))
                checks.append(_summary_check("shots", values, expected in (None, rendered)))
    else:
        for raw_check in _items(source.get("checks")):
            check = _mapping(raw_check)
            if check is None:
                continue
            projected = _check_projection(check)
            if projected:
                checks.append(projected)
    if checks:
        fields["checks"] = checks
    review_dimensions = [
        item
        for raw_item in _items(source.get("review_dimensions"))
        if (item := _public_string(raw_item))
    ]
    if review_dimensions:
        fields["review_dimensions"] = review_dimensions
    return _ready(StageName.EVAL, fields)


def _delivery(documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    supported = _documents_for(documents, {"motion-comic-factory.delivery.v1"})
    if not supported:
        return None
    source = supported[0]
    fields: dict[str, Any] = {}
    publication_status = _public_string(source.get("publication_status"))
    if publication_status:
        fields["publication_status"] = publication_status
    quality_approved = False
    evidence = _mapping(source.get("eval_evidence"))
    if evidence is not None:
        automatic_passed = evidence.get("automatic_passed")
        if isinstance(automatic_passed, bool):
            quality_approved = automatic_passed
        else:
            status = _public_string(evidence.get("status")).lower()
            quality_approved = status in _PASS_STATUSES
    fields["quality_approved"] = quality_approved
    return _ready(StageName.DELIVER, fields)


def build_stage_presentation(
    stage: StageName | str, documents: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    try:
        selected = StageName(stage)
    except (TypeError, ValueError):
        return None
    builders = {
        StageName.CONCEPT: _concept,
        StageName.SCRIPT: _script,
        StageName.STORYBOARD: _storyboard,
        StageName.ASSETS: _assets,
        StageName.AUDIO: _audio,
        StageName.VIDEO: _video,
        StageName.EDIT: _edit,
        StageName.EVAL: _eval,
        StageName.DELIVER: _delivery,
    }
    builder = builders[selected]
    try:
        result = builder(tuple(documents))
    except (TypeError, ValueError):
        result = None
    return result or _unavailable(selected)
