from __future__ import annotations

import math
import re
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
    "resolution": ("resolution", "target_resolution", "video_resolution"),
}
_TARGET_NUMBER_FIELDS = {
    "duration_seconds": ("duration_seconds", "target_duration_seconds"),
    "fps": ("fps",),
    "shots": ("shots",),
}
_EVIDENCE_LABELS = {
    "overlap_count": ("重叠对白", "条"),
    "expected_seconds": ("目标时长", "秒"),
    "actual_seconds": ("实际时长", "秒"),
    "tolerance_seconds": ("允许偏差", "秒"),
    "expected": ("目标数量", ""),
    "actual": ("实际数量", ""),
}
_SUMMARY_FINDING_LABELS = {
    "cue_count": ("台词段数", "段"),
    "overlap_count": ("重叠对白", "条"),
    "expected_count": ("目标镜头数", ""),
    "rendered_count": ("已生成镜头数", ""),
}
_PASS_STATUSES = {"approved", "auto_approved"}
_MODE_LABELS = {
    "original": "原创",
    "novel": "小说改编",
    "replica": "参考复刻",
}
_SOURCE_LABELS = {
    "idea": "创作构想",
    "novel": "小说原文",
    "reference": "参考素材",
}
_EMOTION_LABELS = {
    "narrating": "叙述",
    "focused": "专注",
    "neutral": "平静",
}
_FAILURE_LABELS = {
    "invalid_video": "成片文件检查未通过",
    "missing_audio_stream": "成片缺少声音",
    "duration_drift": "成片时长与剪辑不一致",
    "dialogue_overlap": "对白时间有重叠",
    "shot_count_mismatch": "镜头数量与分镜不一致",
    "generation_failed": "视频生成未完成",
    "specialist_review_failed": "候选视频检查未完成",
    "no_reviewed_candidates": "尚未完成候选视频检查",
    "no_video_candidates": "没有可供检查的视频候选",
}
_GENERIC_CHECK_LABELS = {
    "对白同步": "对白同步",
    "dialogue_sync": "对白同步",
    "角色一致性": "角色一致性",
    "character_consistency": "角色一致性",
    "画面连续性": "画面连续性",
    "visual_continuity": "画面连续性",
    "声音质量": "声音质量",
    "audio_quality": "声音质量",
    "字幕同步": "字幕同步",
    "subtitle_sync": "字幕同步",
}
_REVIEW_DIMENSION_LABELS = {
    "声音": "声音",
    "画面": "画面",
    "连续性": "连续性",
    "角色与场景一致性": "角色与场景一致性",
    "动作物理合理性、肢体结构和道具接触连续性": "动作与道具连续性",
    "口型、对白和字幕同步": "口型、对白和字幕同步",
    "声音角色匹配、停顿、噪声和情绪自然度": "配音与声音自然度",
    "转场动机、空间方向、节奏和观看舒适度": "转场、节奏和观看舒适度",
}
_KNOWN_CAMERA_LABELS = {
    "medium shot, slow push-in": "中景，缓慢推进",
    "close-up, slight handheld tension": "近景，轻微手持",
}
_CHINESE_CHARACTER = re.compile(r"[\u3400-\u9fff]")
_ASCII_LETTER = re.compile(r"[A-Za-z]")


def _public_string(value: Any, *, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def _creator_chinese_string(value: Any) -> str:
    text = _public_string(value)
    if not text or not _CHINESE_CHARACTER.search(text) or _ASCII_LETTER.search(text):
        return ""
    return text


def _creator_camera(value: Any) -> str:
    text = _public_string(value)
    if text in _KNOWN_CAMERA_LABELS:
        return _KNOWN_CAMERA_LABELS[text]
    return _creator_chinese_string(text)


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
        ("voice", "voice_style"),
    ):
        value = _public_string(source.get(source_name))
        if value:
            result[public_name] = value
    appearance = _creator_chinese_string(source.get("visual_anchor"))
    if appearance:
        result["appearance"] = appearance
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
        text = _public_string(line.get("text"))
        if not text:
            continue
        speaker_id = _public_string(line.get("speaker_id"))
        speaker = "旁白" if speaker_id == "narrator" else character_names.get(speaker_id, "")
        if not speaker:
            raw_speaker = _public_string(line.get("speaker"))
            speaker = "旁白" if raw_speaker == "narrator" else _creator_chinese_string(raw_speaker)
        if not speaker:
            continue
        item: dict[str, Any] = {"speaker": speaker, "text": text}
        raw_emotion = _public_string(line.get("emotion"))
        emotion = _EMOTION_LABELS.get(raw_emotion) or _creator_chinese_string(raw_emotion)
        if emotion:
            item["emotion"] = emotion
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
    ):
        value = _public_string(source.get(source_name))
        if value:
            result[public_name] = value
    camera = _creator_camera(source.get("camera"))
    if camera:
        result["camera"] = camera
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
    aggregate_is_finite = True
    for raw_shot in _items(root.get("shots")):
        shot = _mapping(raw_shot)
        if shot is None:
            continue
        projected, duration = _shot_projection(shot, character_names)
        if projected:
            shots.append(projected)
        if duration is not None and duration > 0 and aggregate_is_finite:
            candidate_total = total_duration + duration
            if math.isfinite(candidate_total):
                total_duration = candidate_total
                has_duration = True
            else:
                aggregate_is_finite = False
    if shots:
        fields["shots"] = shots
    if has_duration and aggregate_is_finite:
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
    mode_label = _MODE_LABELS.get(_public_string(source.get("mode")))
    if mode_label:
        fields["mode_label"] = mode_label
    source_label = _SOURCE_LABELS.get(_public_string(source.get("source_kind")))
    if source_label:
        fields["source_label"] = source_label
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
        return None
    fields = _episode_projection(root, StageName.SCRIPT)
    return _ready(StageName.SCRIPT, fields) if fields else None


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
    clips = source.get("clips")
    if isinstance(clips, (list, tuple)):
        fields["clip_count"] = len(clips)
    elif isinstance(source.get("clip_by_shot"), Mapping):
        fields["clip_count"] = len(source["clip_by_shot"])
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
    fields["subtitle_ready"] = bool(source.get("subtitles"))
    return _ready(StageName.EDIT, fields)


def _human_scalar(value: Any) -> str:
    number = _finite_number(value)
    if number is not None:
        return str(int(number)) if float(number).is_integer() else _public_string(str(number), maximum=200)
    return ""


def _evidence_findings(evidence: Any) -> list[str]:
    if not isinstance(evidence, Mapping):
        return []
    findings: list[str] = []
    for key, (label, unit) in _EVIDENCE_LABELS.items():
        if key not in evidence:
            continue
        value = _human_scalar(evidence[key])
        if value:
            suffix = f" {unit}" if unit else ""
            findings.append(f"{label}：{value}{suffix}")
    return findings


def _check_projection(
    source: Mapping[str, Any],
    *,
    ordinal: int,
    default_severity: str = "info",
) -> dict[str, Any]:
    raw_name = _public_string(source.get("name"))
    name = _GENERIC_CHECK_LABELS.get(raw_name, f"检查项目 {ordinal}")
    severity = _public_string(source.get("severity"))
    if severity not in {"error", "warning", "info"}:
        severity = default_severity
    result: dict[str, Any] = {
        "name": name,
        "severity": severity,
        "passed": source.get("passed") is True,
    }
    return result


def _summary_check(
    name: str, values: tuple[tuple[str, Any], ...], passed: bool
) -> dict[str, Any]:
    findings = [
        f"{label}：{value}{f' {unit}' if unit else ''}"
        for key, raw_value in values
        if (label_and_unit := _SUMMARY_FINDING_LABELS.get(key)) is not None
        for label, unit in (label_and_unit,)
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
    checks: list[dict[str, Any]] = []
    if _schema(source) == "motion-comic-factory.eval.v2":
        for raw_failure in _items(source.get("hard_failures")):
            failure = _mapping(raw_failure)
            if failure is None:
                continue
            code = _public_string(failure.get("code"))
            name = _FAILURE_LABELS.get(code, "检查项目未通过")
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
                checks.append(_summary_check("时间安排", values, overlap in (None, 0)))
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
                checks.append(_summary_check("镜头完成情况", values, expected in (None, rendered)))
    else:
        for ordinal, raw_check in enumerate(_items(source.get("checks")), start=1):
            check = _mapping(raw_check)
            if check is None:
                continue
            checks.append(_check_projection(check, ordinal=ordinal))
    if checks:
        fields["checks"] = checks
    review_dimensions = [
        _REVIEW_DIMENSION_LABELS.get(
            _public_string(raw_item),
            f"检查范围 {ordinal}",
        )
        for ordinal, raw_item in enumerate(
            _items(source.get("review_dimensions")), start=1
        )
        if isinstance(raw_item, str) and _public_string(raw_item)
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
    quality_approved = False
    evidence = _mapping(source.get("eval_evidence"))
    if evidence is not None:
        state = _public_string(evidence.get("state")).lower()
        quality_approved = state in _PASS_STATUSES
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
