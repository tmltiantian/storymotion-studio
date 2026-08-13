from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import pet_sitcom_audio_first as _audio_first
from . import pet_sitcom_compose as _compose
from . import pet_sitcom_generation as _generation
from .pet_sitcom import (
    DIALOGUE_TAIL_SECONDS,
    PetSitcomError,
    PetSitcomPlan,
    _validate_plan_contract,
)


SOURCE_EVIDENCE_SCHEMA = "motion-comic-factory.pet-sitcom-source-evidence.v3"
FINAL_EVIDENCE_SCHEMA = "motion-comic-factory.pet-sitcom-final-evidence.v2"
EVIDENCE_SCHEMA = SOURCE_EVIDENCE_SCHEMA
SOURCE_TECHNICAL_SCHEMA = "motion-comic-factory.pet-sitcom-source-qc.v3"
FINAL_TECHNICAL_SCHEMA = "motion-comic-factory.pet-sitcom-final-qc.v2"
SHOT_REVIEW_SCHEMA = "motion-comic-factory.pet-sitcom-shot-review.v4"
SHOT_EVIDENCE_SCHEMA = "motion-comic-factory.pet-sitcom-shot-evidence.v2"
REVIEW_HISTORY_SCHEMA = "motion-comic-factory.pet-sitcom-review-history.v1"
OWNER_NATIVE_AUDIO_REVIEW_SCHEMA = _compose.OWNER_NATIVE_AUDIO_REVIEW_SCHEMA
OWNER_REVIEW_METHOD = "listening_and_waveform"

_MOUTH_SHOTS = (
    "shot_03",
    "shot_04",
    "shot_05",
    "shot_08",
    "shot_09",
    "shot_10",
)
_PAW_SHOTS = ("shot_01", "shot_09")
_PROP_SHOTS = {
    "bag": ("shot_01", "shot_02", "shot_06"),
    "orange_tail": ("shot_06", "shot_07"),
    "crumbs": ("shot_07", "shot_08", "shot_09", "shot_10"),
    "mirror": ("shot_09", "shot_10"),
}
SHOT_REVIEW_GATES = (
    "planned_action",
    "naitang_doubao_identity",
    "scene_and_light_direction",
    "paws_and_feline_anatomy",
    "prop_state",
    "camera_stability_and_unexplained_cuts",
    "no_extra_animal_person_text_or_watermark",
    "designated_speaker",
    "restrained_speaking_cat_mouth_jaw_motion",
    "silent_cat_no_sustained_speech_mouth",
    "subjective_speech_start_pause_end_alignment",
    "previous_selected_ending_frame_continuity",
    "action_preparation_execution_settle",
    "screen_position_and_eyeline",
    "music_transition_motivation",
    "physical_transition_logic",
)

_AUTOMATION_LIMITATIONS = (
    "Automated evidence can reject broken media only; it does not pass identity, "
    "anatomy, acting, speaker assignment, or mouth sync."
)
_SOURCE_DURATION_TOLERANCE = 0.35
_FINAL_DURATION_TOLERANCE = 0.15
_MAX_SOURCE_DURATION = 8.35
_MAX_BLACK_SECONDS = 0.08
_MAX_FREEZE_SECONDS = 0.35
_MIN_MOUTH_TIMING_INTERVAL_SECONDS = 0.05
_SIDECAR_DURATION_TOLERANCE_SECONDS = 0.001
_SIDECAR_TIMESTAMP_TOLERANCE_SECONDS = 1e-9
_PERSISTED_FLOAT_EPSILON_SECONDS = 1e-12
_INTEGRATED_TARGET = -16.0
_INTEGRATED_TOLERANCE = 0.5
_TRUE_PEAK_LIMIT = -1.5
_RETRY_REASONS = frozenset(_generation.PET_RETRY_SUFFIXES)
_GATE_ISSUE_CODES = {
    "planned_action": frozenset({"continuity"}),
    "naitang_doubao_identity": frozenset({"identity"}),
    "scene_and_light_direction": frozenset({"continuity"}),
    "paws_and_feline_anatomy": frozenset({"paw_anatomy"}),
    "prop_state": frozenset({"continuity"}),
    "camera_stability_and_unexplained_cuts": frozenset({"continuity"}),
    "no_extra_animal_person_text_or_watermark": frozenset({"identity"}),
    "designated_speaker": frozenset({"wrong_speaker"}),
    "restrained_speaking_cat_mouth_jaw_motion": frozenset(
        {"mouth_anatomy"}
    ),
    "silent_cat_no_sustained_speech_mouth": frozenset({"wrong_speaker"}),
    "subjective_speech_start_pause_end_alignment": frozenset(
        {"mouth_anatomy", "wrong_speaker"}
    ),
    "previous_selected_ending_frame_continuity": frozenset({"continuity"}),
    "action_preparation_execution_settle": frozenset({"continuity"}),
    "screen_position_and_eyeline": frozenset({"continuity"}),
    "music_transition_motivation": frozenset({"continuity"}),
    "physical_transition_logic": frozenset({"continuity"}),
}

_OWNER_TOP_FIELDS = _compose._OWNER_REVIEW_TOP_LEVEL_FIELDS
_OWNER_RECORD_FIELDS = _compose._OWNER_REVIEW_RECORD_FIELDS
_SHOT_REVIEW_TOP_FIELDS = frozenset(
    {"schema_version", "generated_at", "shots", "mouth_timing"}
)
_SHOT_REVIEW_FIELDS = frozenset(
    {
        "selected_mp4_path",
        "selected_mp4_sha256",
        "reviewed",
        "passed",
        "reviewed_at",
        "retry_reason",
        "gates",
    }
)
_GATE_FIELDS = frozenset(
    {"passed", "notes", "timestamps_seconds", "issue_codes"}
)
_LEGACY_GATE_FIELDS = frozenset(
    {"passed", "notes", "timestamps_seconds"}
)
_MOUTH_TIMING_FIELDS = frozenset(
    {
        "selected_mp4_sha256",
        "drive_audio_sha256",
        "audio_onset_seconds",
        "mouth_onset_seconds",
        "audio_offset_seconds",
        "mouth_offset_seconds",
        "onset_error_seconds",
        "offset_error_seconds",
        "max_onset_error_seconds",
        "max_offset_error_seconds",
        "no_silent_mouth_flapping",
        "no_closed_mouth_during_speech",
        "reviewed",
        "passed",
    }
)
_SOURCE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "phase",
        "generated_at",
        "source_technical_qc_path",
        "source_technical_qc_sha256",
        "shot_sheets",
        "mouth_sequences",
        "paw_sequences",
        "prop_sequences",
        "continuity_comparisons",
        "manual_review_paths",
        "automation_limitations",
    }
)
_FINAL_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "phase",
        "generated_at",
        "source_manifest_sha256",
        "final_technical_qc_path",
        "final_technical_qc_sha256",
        "whole_cut_sheet",
        "final_checks",
        "automation_limitations",
    }
)
_SHOT_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "generated_at",
        "shot_id",
        "source_technical_qc",
        "shot_sheet",
        "mouth_sequence",
        "paw_sequence",
        "prop_sequences",
        "continuity_comparison",
        "manual_review_path",
        "automation_limitations",
    }
)
_SEQUENCE_FIELDS = frozenset(
    {
        "shot_id",
        "label",
        "source_path",
        "selected_mp4_sha256",
        "source_duration_seconds",
        "timestamps_seconds",
        "layout",
        "evidence_path",
        "evidence_sha256",
        "extracted_at",
    }
)
_FINAL_SEQUENCE_FIELDS = frozenset(
    {
        "label",
        "source_path",
        "source_sha256",
        "source_duration_seconds",
        "timestamps_seconds",
        "layout",
        "evidence_path",
        "evidence_sha256",
        "extracted_at",
    }
)
_CONTINUITY_FIELDS = frozenset(
    {
        "previous_shot_id",
        "current_shot_id",
        "label",
        "previous_source_path",
        "previous_selected_mp4_sha256",
        "previous_duration_seconds",
        "previous_video_duration_seconds",
        "previous_edit_end_seconds",
        "current_source_path",
        "current_selected_mp4_sha256",
        "current_duration_seconds",
        "current_video_duration_seconds",
        "previous_timestamps_seconds",
        "current_timestamps_seconds",
        "frame_paths",
        "frame_sha256",
        "evidence_path",
        "evidence_sha256",
        "extracted_at",
    }
)
_QC_TOP_FIELDS = frozenset(
    {"schema_version", "phase", "generated_at", "records"}
)
_QC_RECORD_FIELDS = frozenset(
    {
        "name",
        "path",
        "sha256",
        "ffprobe",
        "duration_seconds",
        "video_duration_seconds",
        "audio_present",
        "blackdetect",
        "freezedetect",
        "loudness",
        "passed",
        "errors",
        "checked_at",
    }
)
_DETECTION_FIELDS = frozenset(
    {
        "filter",
        "starts_seconds",
        "ends_seconds",
        "durations_seconds",
        "max_duration_seconds",
    }
)
_LOUDNESS_FIELDS = frozenset(
    {
        "filter",
        "integrated_lufs",
        "true_peak_dbtp",
        "measurement_available",
    }
)
_HISTORY_FIELDS = frozenset(
    {
        "schema_version",
        "review_type",
        "shot_id",
        "archived_at",
        "old_candidate_number",
        "old_selected_mp4_path",
        "old_selected_mp4_sha256",
        "current_candidate_number",
        "current_selected_mp4_path",
        "current_selected_mp4_sha256",
        "review_record",
        "document_metadata",
    }
)
_SELECTION_TOP_FIELDS = frozenset(
    {"schema_version", "shots", "history"}
)
_SELECTION_ENTRY_FIELDS = frozenset(
    {
        "candidate_number",
        "status",
        "video_path",
        "video_sha256",
        "prompt_sha256",
        "reference_paths",
        "reference_sha256",
        "dependency_video_sha256",
        "source_tts_sha256",
        "reference_audio_sha256",
        "selected_at",
        "continuity_frame_path",
        "continuity_sidecar_path",
        "continuity_frame_sha256",
        "continuity_timestamp_seconds",
    }
)


class PetSitcomReviewError(RuntimeError):
    pass


class _VideoStreamDurationError(PetSitcomReviewError):
    pass


def build_pet_sitcom_evidence(
    plan: PetSitcomPlan,
    *,
    phase: str = "source",
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Build one review phase without claiming any subjective quality pass."""
    if phase == "source":
        return build_source_evidence(
            plan,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
    if phase == "final":
        return build_final_evidence(
            plan,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
    raise PetSitcomReviewError("Evidence phase must be 'source' or 'final'.")


def build_source_evidence(
    plan: PetSitcomPlan,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Build source-only evidence before composition."""
    _validate_plan(plan)
    sources = _selected_sources(plan)
    root = _evidence_root(plan)
    _ensure_directory(plan, root)
    generated_at = _now()
    qc_path = root / "source_technical_qc.json"
    records = [
        _technical_record(
            plan,
            shot.shot_id,
            sources[shot.shot_id],
            final=False,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
        for shot in plan.shots
    ]
    qc = {
        "schema_version": SOURCE_TECHNICAL_SCHEMA,
        "phase": "source",
        "generated_at": generated_at,
        "records": records,
    }
    _write_json(plan, qc_path, qc)
    if any(not record["passed"] for record in records):
        raise PetSitcomReviewError(
            "Source technical QC failed; inspect source_technical_qc.json."
        )
    durations = {record["name"]: record["duration_seconds"] for record in records}
    video_durations = {
        record["name"]: record["video_duration_seconds"]
        for record in records
    }

    shot_sheets = [
        _source_sequence(
            plan,
            shot.shot_id,
            sources[shot.shot_id],
            durations[shot.shot_id],
            sample_duration=video_durations[shot.shot_id],
            folder="shot_sheets",
            label=shot.shot_id,
            frame_count=9,
            layout="3x3",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        for shot in plan.shots
    ]
    mouth_sequences = {
        shot_id: _source_sequence(
            plan,
            shot_id,
            sources[shot_id],
            durations[shot_id],
            sample_duration=video_durations[shot_id],
            folder="mouth",
            label=shot_id,
            frame_count=13,
            layout="4x4",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        for shot_id in _MOUTH_SHOTS
    }
    paw_sequences = {
        shot_id: _source_sequence(
            plan,
            shot_id,
            sources[shot_id],
            durations[shot_id],
            sample_duration=video_durations[shot_id],
            folder="paws",
            label=shot_id,
            frame_count=9,
            layout="3x3",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        for shot_id in _PAW_SHOTS
    }
    prop_sequences = {
        label: {
            shot_id: _source_sequence(
                plan,
                shot_id,
                sources[shot_id],
                durations[shot_id],
                sample_duration=video_durations[shot_id],
                folder=f"props/{label}/{shot_id}",
                label=label,
                frame_count=9,
                layout="3x3",
                runner=command_runner,
                ffmpeg=ffmpeg_bin,
                extracted_at=generated_at,
            )
            for shot_id in shot_ids
        }
        for label, shot_ids in _PROP_SHOTS.items()
    }
    continuity = [
        _continuity_evidence(
            plan,
            previous_id,
            current.shot_id,
            sources[previous_id],
            sources[current.shot_id],
            durations[previous_id],
            durations[current.shot_id],
            command_runner,
            ffmpeg_bin,
            generated_at,
            previous_video_duration=video_durations[previous_id],
            current_video_duration=video_durations[current.shot_id],
        )
        for current in plan.shots
        for previous_id in current.continuity_source_ids
    ]
    owner_path = write_owner_native_audio_review_template(plan, sources=sources)
    shot_review_path = write_pet_shot_review_template(plan, sources=sources)
    manifest = {
        "schema_version": SOURCE_EVIDENCE_SCHEMA,
        "phase": "source",
        "generated_at": generated_at,
        "source_technical_qc_path": str(qc_path.resolve()),
        "source_technical_qc_sha256": _sha(qc_path),
        "shot_sheets": shot_sheets,
        "mouth_sequences": mouth_sequences,
        "paw_sequences": paw_sequences,
        "prop_sequences": prop_sequences,
        "continuity_comparisons": continuity,
        "manual_review_paths": {
            "shot_reviews": str(shot_review_path.resolve()),
            "owner_native_audio": str(owner_path.resolve()),
        },
        "automation_limitations": _AUTOMATION_LIMITATIONS,
    }
    _write_json(plan, _source_manifest_path(plan), manifest)
    return manifest


def build_pet_shot_evidence(
    plan: PetSitcomPlan,
    shot_id: str,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Build review evidence for one selected shot and its predecessor."""
    _validate_plan(plan)
    shots = {shot.shot_id: shot for shot in plan.shots}
    shot = shots.get(shot_id)
    if shot is None:
        raise PetSitcomReviewError("Incremental evidence requires a known shot id.")
    chain = _selected_source_chain(plan, shot)
    source = chain[shot_id]
    generated_at = _now()
    record = _technical_record(
        plan,
        shot_id,
        source,
        final=False,
        command_runner=command_runner,
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
    )
    duration = float(record["duration_seconds"])
    video_duration = float(record["video_duration_seconds"])
    shot_sheet = _source_sequence(
        plan,
        shot_id,
        source,
        duration,
        sample_duration=video_duration,
        folder="shot_sheets",
        label=shot_id,
        frame_count=9,
        layout="3x3",
        runner=command_runner,
        ffmpeg=ffmpeg_bin,
        extracted_at=generated_at,
    )
    mouth = (
        _source_sequence(
            plan,
            shot_id,
            source,
            duration,
            sample_duration=video_duration,
            folder="mouth",
            label=shot_id,
            frame_count=13,
            layout="4x4",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        if shot_id in _MOUTH_SHOTS
        else None
    )
    paw = (
        _source_sequence(
            plan,
            shot_id,
            source,
            duration,
            sample_duration=video_duration,
            folder="paws",
            label=shot_id,
            frame_count=9,
            layout="3x3",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        if shot_id in _PAW_SHOTS
        else None
    )
    props = {
        label: _source_sequence(
            plan,
            shot_id,
            source,
            duration,
            sample_duration=video_duration,
            folder=f"props/{label}/{shot_id}",
            label=label,
            frame_count=9,
            layout="3x3",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        for label, prop_shot_ids in _PROP_SHOTS.items()
        if shot_id in prop_shot_ids
    }
    continuity = []
    for previous_id in shot.continuity_source_ids:
        previous_source = chain[previous_id]
        previous_probe = _ffprobe(
            command_runner,
            ffprobe_bin,
            Path(previous_source["path"]),
        )
        previous_duration = _duration(previous_probe)
        previous_video_duration = _video_duration(previous_probe)
        expected_previous_duration, _expected_audio_streams = (
            _technical_expectations(
                plan,
                previous_id,
                final=False,
                source=chain[previous_id],
            )
        )
        if (
            abs(
                previous_duration
                - expected_previous_duration
            )
            > _SOURCE_DURATION_TOLERANCE
        ):
            raise PetSitcomReviewError(
                f"Predecessor technical duration is invalid for {shot_id}."
            )
        continuity.append(
            _continuity_evidence(
                plan,
                previous_id,
                shot_id,
                previous_source,
                source,
                previous_duration,
                duration,
                command_runner,
                ffmpeg_bin,
                generated_at,
                previous_video_duration=previous_video_duration,
                current_video_duration=video_duration,
            )
        )
    review_path = write_pet_shot_review_template(
        plan,
        sources={shot_id: source},
    )
    evidence = {
        "schema_version": SHOT_EVIDENCE_SCHEMA,
        "generated_at": generated_at,
        "shot_id": shot_id,
        "source_technical_qc": record,
        "shot_sheet": shot_sheet,
        "mouth_sequence": mouth,
        "paw_sequence": paw,
        "prop_sequences": props,
        "continuity_comparison": continuity,
        "manual_review_path": str(review_path.resolve()),
        "automation_limitations": _AUTOMATION_LIMITATIONS,
    }
    incremental_path = (
        _evidence_root(plan) / "incremental" / f"{shot_id}.json"
    )
    _write_json(plan, incremental_path, evidence)
    return evidence


def validate_pet_shot_review(
    plan: PetSitcomPlan,
    shot_id: str,
) -> dict[str, Any]:
    """Validate one current incremental shot review without requiring all shots."""
    _validate_plan(plan)
    shots = {shot.shot_id: shot for shot in plan.shots}
    shot = shots.get(shot_id)
    if shot is None:
        raise PetSitcomReviewError(
            "Single-shot review validation requires a known shot id."
        )
    sources = _selected_source_chain(plan, shot)
    source = sources[shot_id]
    evidence_path = (
        _evidence_root(plan) / "incremental" / f"{shot_id}.json"
    )
    evidence = _require_json(
        plan,
        evidence_path,
        f"Incremental evidence for {shot_id}",
    )
    _require_exact_fields(
        evidence,
        _SHOT_EVIDENCE_FIELDS,
        f"Incremental evidence for {shot_id}",
    )
    if (
        evidence.get("schema_version") != SHOT_EVIDENCE_SCHEMA
        or evidence.get("shot_id") != shot_id
        or not _iso(evidence.get("generated_at"))
        or evidence.get("manual_review_path")
        != str(plan.shot_review_path.resolve())
        or evidence.get("automation_limitations") != _AUTOMATION_LIMITATIONS
    ):
        raise PetSitcomReviewError(
            f"Incremental evidence for {shot_id} is invalid or stale."
        )
    technical = _validate_qc_record(
        plan,
        evidence.get("source_technical_qc"),
        phase="source",
        expected_name=shot_id,
        expected_item=source,
        allow_failed=True,
    )
    duration = float(technical["duration_seconds"])
    video_duration = float(technical["video_duration_seconds"])
    root = _evidence_root(plan)
    _validate_source_sequence(
        plan,
        evidence.get("shot_sheet"),
        shot_id,
        shot_id,
        source,
        duration,
        video_duration,
        9,
        "3x3",
        root / "shot_sheets" / f"{shot_id}.png",
    )
    _validate_optional_incremental_sequence(
        plan,
        evidence.get("mouth_sequence"),
        shot_id=shot_id,
        source=source,
        duration=duration,
        video_duration=video_duration,
        expected_shots=_MOUTH_SHOTS,
        frame_count=13,
        layout="4x4",
        folder="mouth",
    )
    _validate_optional_incremental_sequence(
        plan,
        evidence.get("paw_sequence"),
        shot_id=shot_id,
        source=source,
        duration=duration,
        video_duration=video_duration,
        expected_shots=_PAW_SHOTS,
        frame_count=9,
        layout="3x3",
        folder="paws",
    )
    _validate_incremental_props(
        plan,
        evidence.get("prop_sequences"),
        shot_id,
        source,
        duration,
        video_duration,
    )
    _validate_incremental_continuity(
        plan,
        evidence.get("continuity_comparison"),
        shot,
        sources,
        duration,
        video_duration,
    )
    document = _require_json(plan, plan.shot_review_path, "Shot review")
    _require_exact_fields(
        document,
        _SHOT_REVIEW_TOP_FIELDS,
        "Shot review document",
    )
    records = document.get("shots")
    mouth_timing = document.get("mouth_timing")
    if (
        document.get("schema_version") != SHOT_REVIEW_SCHEMA
        or not _iso(document.get("generated_at"))
        or not isinstance(records, Mapping)
        or shot_id not in records
        or any(record_id not in shots for record_id in records)
        or not isinstance(mouth_timing, Mapping)
        or set(mouth_timing)
        != (set(records) & set(_MOUTH_SHOTS))
    ):
        raise PetSitcomReviewError(
            f"Shot review document for {shot_id} is incomplete."
        )
    result = _validate_shot_review_record(
        shot_id,
        records[shot_id],
        source,
        duration,
    )
    if shot_id in _MOUTH_SHOTS:
        mouth_validator = (
            _validate_mouth_timing_record
            if result["passed"]
            else _validate_failed_mouth_timing_record
        )
        mouth_validator(shot_id, mouth_timing[shot_id], source, duration)
    if technical["passed"] is not True:
        if result["passed"] is True:
            raise PetSitcomReviewError(
                f"Technically failed source cannot pass review for {shot_id}."
            )
        if result["retry_reason"] != "continuity":
            raise PetSitcomReviewError(
                f"Technical source failure requires a continuity retry for {shot_id}."
            )
    passed = result["passed"]
    return {
        "passed": passed,
        "failed": not passed,
        "retry_reason": result["retry_reason"],
        "candidate": source["candidate_number"],
        "hash": source["sha256"],
    }


def build_final_evidence(
    plan: PetSitcomPlan,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> dict[str, Any]:
    """Build final-cut evidence after source review and composition."""
    validate_source_evidence(plan)
    generated_at = _now()
    root = _evidence_root(plan)
    qc_path = root / "final_technical_qc.json"
    outputs = {"clean": plan.clean_output, "release": plan.release_output}
    records = [
        _technical_record(
            plan,
            name,
            {"path": path, "sha256": _sha_or_empty(path)},
            final=True,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
        for name, path in outputs.items()
    ]
    qc = {
        "schema_version": FINAL_TECHNICAL_SCHEMA,
        "phase": "final",
        "generated_at": generated_at,
        "records": records,
    }
    _write_json(plan, qc_path, qc)
    if any(not record["passed"] for record in records):
        raise PetSitcomReviewError(
            "Final technical QC failed; inspect final_technical_qc.json."
        )
    by_name = {record["name"]: record for record in records}
    release = by_name["release"]
    whole_cut = _final_sequence(
        plan,
        "whole_cut",
        plan.release_output,
        release["sha256"],
        release["duration_seconds"],
        sample_duration=release["video_duration_seconds"],
        frame_count=16,
        layout="4x4",
        output=root / "final" / "whole_cut.png",
        runner=command_runner,
        ffmpeg=ffmpeg_bin,
        extracted_at=generated_at,
    )
    final_checks = {
        name: _final_sequence(
            plan,
            f"{name}_start_cut_end",
            path,
            by_name[name]["sha256"],
            by_name[name]["duration_seconds"],
            sample_duration=by_name[name]["video_duration_seconds"],
            frame_count=3,
            layout="3x1",
            output=root / "final" / f"{name}_start_cut_end.png",
            runner=command_runner,
            ffmpeg=ffmpeg_bin,
            extracted_at=generated_at,
        )
        for name, path in outputs.items()
    }
    manifest = {
        "schema_version": FINAL_EVIDENCE_SCHEMA,
        "phase": "final",
        "generated_at": generated_at,
        "source_manifest_sha256": _sha(_source_manifest_path(plan)),
        "final_technical_qc_path": str(qc_path.resolve()),
        "final_technical_qc_sha256": _sha(qc_path),
        "whole_cut_sheet": whole_cut,
        "final_checks": final_checks,
        "automation_limitations": _AUTOMATION_LIMITATIONS,
    }
    _write_json(plan, _final_manifest_path(plan), manifest)
    return manifest


def validate_source_evidence(plan: PetSitcomPlan) -> dict[str, Any]:
    """Validate exact source evidence against current Task 5 selections."""
    _validate_plan(plan)
    sources = _selected_sources(plan)
    manifest_path = _source_manifest_path(plan)
    manifest = _require_json(plan, manifest_path, "Source evidence manifest")
    _require_exact_fields(manifest, _SOURCE_MANIFEST_FIELDS, "Source manifest")
    if (
        manifest.get("schema_version") != SOURCE_EVIDENCE_SCHEMA
        or manifest.get("phase") != "source"
        or not _iso(manifest.get("generated_at"))
        or manifest.get("automation_limitations") != _AUTOMATION_LIMITATIONS
    ):
        raise PetSitcomReviewError("Source evidence manifest is invalid or stale.")
    qc_path = _evidence_root(plan) / "source_technical_qc.json"
    _safe_path(plan, qc_path, "Source technical QC")
    if (
        manifest.get("source_technical_qc_path") != str(qc_path.resolve())
        or manifest.get("source_technical_qc_sha256") != _sha(qc_path)
    ):
        raise PetSitcomReviewError("Source technical QC evidence is stale.")
    qc = _validate_qc_document(
        plan,
        qc_path,
        phase="source",
        expected={
            shot.shot_id: sources[shot.shot_id]
            for shot in plan.shots
        },
    )
    durations = {
        record["name"]: float(record["duration_seconds"])
        for record in qc["records"]
    }
    video_durations = {
        record["name"]: float(record["video_duration_seconds"])
        for record in qc["records"]
    }
    shot_sheets = manifest.get("shot_sheets")
    if not isinstance(shot_sheets, list) or len(shot_sheets) != len(
        plan.shots
    ):
        raise PetSitcomReviewError(
            "Source evidence must contain exactly ten shot sheets."
        )
    for shot, item in zip(plan.shots, shot_sheets, strict=True):
        _validate_source_sequence(
            plan,
            item,
            shot.shot_id,
            shot.shot_id,
            sources[shot.shot_id],
            durations[shot.shot_id],
            video_durations[shot.shot_id],
            9,
            "3x3",
            _evidence_root(plan)
            / "shot_sheets"
            / f"{shot.shot_id}.png",
        )
    _validate_sequence_group(
        plan,
        manifest.get("mouth_sequences"),
        _MOUTH_SHOTS,
        sources,
        durations,
        video_durations,
        frame_count=13,
        layout="4x4",
        folder="mouth",
    )
    _validate_sequence_group(
        plan,
        manifest.get("paw_sequences"),
        _PAW_SHOTS,
        sources,
        durations,
        video_durations,
        frame_count=9,
        layout="3x3",
        folder="paws",
    )
    props = manifest.get("prop_sequences")
    if not isinstance(props, Mapping) or set(props) != set(_PROP_SHOTS):
        raise PetSitcomReviewError(
            "Source evidence prop records are missing or contain extras."
        )
    for label, shot_ids in _PROP_SHOTS.items():
        group = props[label]
        if not isinstance(group, Mapping) or set(group) != set(shot_ids):
            raise PetSitcomReviewError(
                f"Source evidence prop {label} records are incomplete."
            )
        for shot_id in shot_ids:
            _validate_source_sequence(
                plan,
                group[shot_id],
                shot_id,
                label,
                sources[shot_id],
                durations[shot_id],
                video_durations[shot_id],
                9,
                "3x3",
                _evidence_root(plan)
                / "props"
                / label
                / shot_id
                / f"{label}.png",
            )
    comparisons = manifest.get("continuity_comparisons")
    edges = _continuity_edges(plan)
    if not isinstance(comparisons, list) or len(comparisons) != len(edges):
        raise PetSitcomReviewError(
            "Source evidence must cover every declared continuity edge."
        )
    for (previous_id, current_id), item in zip(
        edges,
        comparisons,
        strict=True,
    ):
        _validate_continuity_item(
            plan,
            item,
            previous_id,
            current_id,
            sources,
            durations,
            video_durations,
        )
    review_paths = manifest.get("manual_review_paths")
    expected_review_paths = {
        "shot_reviews": str(plan.shot_review_path.resolve()),
        "owner_native_audio": str(
            (plan.output_dir / "owner_native_audio_review.json").resolve()
        ),
    }
    if review_paths != expected_review_paths:
        raise PetSitcomReviewError("Source manual review paths are invalid.")
    for path in (
        plan.shot_review_path,
        plan.output_dir / "owner_native_audio_review.json",
    ):
        _safe_path(plan, path, "Manual review template")
        if not path.is_file():
            raise PetSitcomReviewError("Source manual review template is missing.")
    return {
        "valid": True,
        "manifest": manifest,
        "qc": qc,
        "sources": sources,
        "durations": durations,
        "manifest_sha256": _sha(manifest_path),
    }


def validate_final_evidence(plan: PetSitcomPlan) -> dict[str, Any]:
    """Validate exact final evidence against current clean and release files."""
    source = validate_source_evidence(plan)
    manifest_path = _final_manifest_path(plan)
    manifest = _require_json(plan, manifest_path, "Final evidence manifest")
    _require_exact_fields(manifest, _FINAL_MANIFEST_FIELDS, "Final manifest")
    if (
        manifest.get("schema_version") != FINAL_EVIDENCE_SCHEMA
        or manifest.get("phase") != "final"
        or not _iso(manifest.get("generated_at"))
        or manifest.get("automation_limitations") != _AUTOMATION_LIMITATIONS
        or manifest.get("source_manifest_sha256")
        != source["manifest_sha256"]
    ):
        raise PetSitcomReviewError("Final evidence manifest is invalid or stale.")
    qc_path = _evidence_root(plan) / "final_technical_qc.json"
    _safe_path(plan, qc_path, "Final technical QC")
    if (
        manifest.get("final_technical_qc_path") != str(qc_path.resolve())
        or manifest.get("final_technical_qc_sha256") != _sha(qc_path)
    ):
        raise PetSitcomReviewError("Final technical QC evidence is stale.")
    expected = {
        "clean": {
            "path": plan.clean_output,
            "sha256": _sha_or_empty(plan.clean_output),
        },
        "release": {
            "path": plan.release_output,
            "sha256": _sha_or_empty(plan.release_output),
        },
    }
    qc = _validate_qc_document(
        plan,
        qc_path,
        phase="final",
        expected=expected,
    )
    records = {record["name"]: record for record in qc["records"]}
    _validate_final_sequence(
        plan,
        manifest.get("whole_cut_sheet"),
        "whole_cut",
        plan.release_output,
        records["release"],
        16,
        "4x4",
        _evidence_root(plan) / "final" / "whole_cut.png",
    )
    checks = manifest.get("final_checks")
    if not isinstance(checks, Mapping) or set(checks) != {"clean", "release"}:
        raise PetSitcomReviewError(
            "Final evidence checks are missing or contain extras."
        )
    for name, path in (
        ("clean", plan.clean_output),
        ("release", plan.release_output),
    ):
        _validate_final_sequence(
            plan,
            checks[name],
            f"{name}_start_cut_end",
            path,
            records[name],
            3,
            "3x1",
            _evidence_root(plan)
            / "final"
            / f"{name}_start_cut_end.png",
        )
    return {
        "valid": True,
        "manifest": manifest,
        "qc": qc,
        "records": records,
        "source": source,
    }


def _pending_owner_record(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selected_mp4_path": str(Path(source["path"]).resolve()),
        "selected_mp4_sha256": str(source["sha256"]),
        "no_native_voice": False,
        "room_tone_allowed": False,
        "reviewer_method": OWNER_REVIEW_METHOD,
        "reviewed_at": "",
        "notes": "pending human review",
    }


def _pending_shot_review(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selected_mp4_path": str(Path(source["path"]).resolve()),
        "selected_mp4_sha256": str(source["sha256"]),
        "reviewed": False,
        "passed": None,
        "reviewed_at": "",
        "retry_reason": "",
        "gates": {
            name: {
                "passed": None,
                "notes": "",
                "timestamps_seconds": [],
                "issue_codes": [],
            }
            for name in SHOT_REVIEW_GATES
        },
    }


def _pending_mouth_timing(source: Mapping[str, Any]) -> dict[str, Any]:
    audio_onset, audio_offset = _source_drive_audio_timing(source)
    return {
        "selected_mp4_sha256": str(source["sha256"]),
        "drive_audio_sha256": _source_drive_audio_sha256(source),
        "audio_onset_seconds": audio_onset,
        "mouth_onset_seconds": None,
        "audio_offset_seconds": audio_offset,
        "mouth_offset_seconds": None,
        "onset_error_seconds": None,
        "offset_error_seconds": None,
        "max_onset_error_seconds": 0.25,
        "max_offset_error_seconds": 0.25,
        "no_silent_mouth_flapping": False,
        "no_closed_mouth_during_speech": False,
        "reviewed": False,
        "passed": None,
    }


def _source_drive_audio_sha256(source: Mapping[str, Any]) -> str:
    digest = source.get("reference_audio_sha256")
    path_value = source.get("reference_audio_path")
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not isinstance(path_value, str)
        or not path_value
    ):
        raise PetSitcomReviewError(
            "Cat speaking source requires a current drive audio binding."
        )
    path = Path(path_value)
    if _sha_or_empty(path) != digest:
        raise PetSitcomReviewError("Cat speaking drive audio is stale.")
    return digest


def _source_drive_audio_timing(
    source: Mapping[str, Any],
) -> tuple[float, float]:
    onset = source.get("audio_onset_seconds")
    offset = source.get("audio_offset_seconds")
    if (
        isinstance(onset, bool)
        or not isinstance(onset, (int, float))
        or not math.isfinite(float(onset))
        or isinstance(offset, bool)
        or not isinstance(offset, (int, float))
        or not math.isfinite(float(offset))
        or float(onset) < 0.0
        or float(offset) - float(onset)
        < _audio_first.MINIMUM_DURATION_SECONDS
    ):
        raise PetSitcomReviewError(
            "Cat speaking source requires canonical Task 2 audio timing."
        )
    return float(onset), float(offset)


def _derived_timing_error(
    audio_endpoint: float,
    mouth_endpoint: Any,
    prior_error: Any,
) -> float | None:
    if (
        isinstance(mouth_endpoint, bool)
        or not isinstance(mouth_endpoint, (int, float))
        or not math.isfinite(float(mouth_endpoint))
        or float(mouth_endpoint) < 0.0
    ):
        return None
    expected = abs(audio_endpoint - float(mouth_endpoint))
    if (
        not isinstance(prior_error, bool)
        and isinstance(prior_error, (int, float))
        and math.isfinite(float(prior_error))
        and math.isclose(
            float(prior_error),
            expected,
            abs_tol=1e-9,
        )
    ):
        return float(prior_error)
    return expected


def _validate_source_continuity_timing(
    source: Mapping[str, Any],
    qc_duration: float,
    edit_duration: float,
) -> None:
    source_duration = source.get(
        "continuity_source_video_duration_seconds"
    )
    if (
        isinstance(source_duration, bool)
        or not isinstance(source_duration, (int, float))
        or not math.isfinite(float(source_duration))
        or float(source_duration) <= 0.0
        or abs(float(source_duration) - qc_duration)
        > _SIDECAR_DURATION_TOLERANCE_SECONDS
        + _PERSISTED_FLOAT_EPSILON_SECONDS
    ):
        raise PetSitcomReviewError(
            "Task 5 sidecar source duration does not match current source QC."
        )
    sidecar_edit_duration = source.get("edit_duration_seconds")
    if (
        isinstance(sidecar_edit_duration, bool)
        or not isinstance(sidecar_edit_duration, (int, float))
        or not math.isfinite(float(sidecar_edit_duration))
        or float(sidecar_edit_duration) <= 0.0
        or float(sidecar_edit_duration) != edit_duration
    ):
        raise PetSitcomReviewError(
            "Task 5 sidecar edit duration does not match the current plan."
        )
    timestamp = source.get("continuity_timestamp_seconds")
    expected_timestamp = min(
        float(sidecar_edit_duration) - 0.08,
        float(source_duration) - 0.08,
    )
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(float(timestamp))
        or float(timestamp) < 0.0
        or float(timestamp) >= float(source_duration)
        or abs(float(timestamp) - expected_timestamp)
        > _SIDECAR_TIMESTAMP_TOLERANCE_SECONDS
    ):
        raise PetSitcomReviewError(
            "Task 5 sidecar timestamp is invalid for its current durations."
        )


def _review_source_matches(
    record: Any,
    source: Mapping[str, Any],
) -> bool:
    return bool(
        isinstance(record, Mapping)
        and record.get("selected_mp4_path")
        == str(Path(source["path"]).resolve())
        and record.get("selected_mp4_sha256") == source["sha256"]
    )


def _current_gate_schema(record: Any) -> bool:
    gates = record.get("gates") if isinstance(record, Mapping) else None
    return bool(
        isinstance(gates, Mapping)
        and set(gates) == set(SHOT_REVIEW_GATES)
        and all(
            isinstance(gate, Mapping) and set(gate) == set(_GATE_FIELDS)
            for gate in gates.values()
        )
    )


def _upgrade_legacy_shot_review(
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    if set(record) != set(_SHOT_REVIEW_FIELDS):
        return None
    gates = record.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(
        SHOT_REVIEW_GATES
    ):
        return None
    retry_reason = record.get("retry_reason")
    upgraded = dict(record)
    upgraded_gates: dict[str, Any] = {}
    for gate_name in SHOT_REVIEW_GATES:
        gate = gates[gate_name]
        if not isinstance(gate, Mapping):
            return None
        if set(gate) == set(_GATE_FIELDS):
            upgraded_gates[gate_name] = dict(gate)
            continue
        if set(gate) != set(_LEGACY_GATE_FIELDS):
            return None
        passed = gate.get("passed")
        if passed is False:
            if retry_reason not in _GATE_ISSUE_CODES[gate_name]:
                return None
            issue_codes = [retry_reason]
        else:
            issue_codes = []
        upgraded_gates[gate_name] = {
            **dict(gate),
            "issue_codes": issue_codes,
        }
    upgraded["gates"] = upgraded_gates
    return upgraded


def _is_v3_shot_review_record(record: Any) -> bool:
    if (
        not isinstance(record, Mapping)
        or set(record) != set(_SHOT_REVIEW_FIELDS)
        or not _current_gate_schema(record)
        or not isinstance(record.get("selected_mp4_path"), str)
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            str(record.get("selected_mp4_sha256") or ""),
        )
        or type(record.get("reviewed")) is not bool
        or not isinstance(record.get("reviewed_at"), str)
        or not isinstance(record.get("retry_reason"), str)
    ):
        return False
    derived_pass = True
    failed_codes: set[str] = set()
    gates = record["gates"]
    reviewed = record["reviewed"]
    for gate_name in SHOT_REVIEW_GATES:
        gate = gates[gate_name]
        passed = gate.get("passed")
        notes = gate.get("notes")
        timestamps = gate.get("timestamps_seconds")
        issue_codes = gate.get("issue_codes")
        if (
            (passed is not None and type(passed) is not bool)
            or not isinstance(notes, str)
            or not isinstance(timestamps, list)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= _MAX_SOURCE_DURATION
                for value in timestamps
            )
            or not isinstance(issue_codes, list)
            or len(issue_codes) != len(set(issue_codes))
            or any(
                not isinstance(code, str)
                or code not in _GATE_ISSUE_CODES[gate_name]
                for code in issue_codes
            )
            or (passed is False and not issue_codes)
            or (passed is not False and issue_codes)
            or (
                reviewed
                and (
                    type(passed) is not bool
                    or not notes.strip()
                    or (passed is False and not timestamps)
                )
            )
        ):
            return False
        if passed is None:
            derived_pass = False
        else:
            derived_pass &= passed
        if passed is False:
            failed_codes.update(issue_codes)
    if reviewed is False:
        return bool(
            record.get("passed") is None
            and record["reviewed_at"] == ""
            and record["retry_reason"] == ""
            and all(gate["passed"] is None for gate in gates.values())
            and all(
                gate["notes"] == ""
                and gate["timestamps_seconds"] == []
                for gate in gates.values()
            )
        )
    if (
        not _iso(record["reviewed_at"])
        or record.get("passed") is not derived_pass
    ):
        return False
    if derived_pass:
        return record["retry_reason"] == ""
    return record["retry_reason"] in failed_codes


def _v3_shot_review_for_archive(
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    candidate = (
        dict(record)
        if _current_gate_schema(record)
        else _upgrade_legacy_shot_review(record)
    )
    if candidate is None or not _is_v3_shot_review_record(candidate):
        return None
    candidate["gates"] = {
        gate_name: dict(gate)
        for gate_name, gate in candidate["gates"].items()
    }
    return candidate


def _source_candidate_number(source: Mapping[str, Any]) -> int:
    declared = source.get("candidate_number")
    if type(declared) is int and declared in {1, 2, 3, 4, 5, 6}:
        return declared
    match = re.search(
        r"candidate_(\d{3})\.mp4$",
        Path(str(source.get("path") or "")).name,
    )
    if match and int(match.group(1)) in {1, 2, 3, 4, 5, 6}:
        return int(match.group(1))
    return 1


def _record_candidate_number(record: Mapping[str, Any]) -> int:
    return _source_candidate_number(
        {
            "path": record.get("selected_mp4_path", ""),
            "sha256": record.get("selected_mp4_sha256", ""),
        }
    )


def _archive_review_record(
    plan: PetSitcomPlan,
    *,
    review_type: str,
    shot_id: str,
    record: Mapping[str, Any],
    current: Mapping[str, Any],
    document: Mapping[str, Any],
) -> Path:
    if review_type not in {"shot", "owner"}:
        raise PetSitcomReviewError("Review history type is invalid.")
    if review_type == "shot" and not _is_v3_shot_review_record(record):
        raise PetSitcomReviewError(
            f"Cannot archive a non-v3 shot review for {shot_id}."
        )
    old_hash = str(record.get("selected_mp4_sha256") or "")
    new_hash = str(current.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", old_hash) or not re.fullmatch(
        r"[0-9a-f]{64}", new_hash
    ):
        raise PetSitcomReviewError(
            f"Cannot archive invalid review hashes for {shot_id}."
        )
    folder = _evidence_root(plan) / "review_history" / review_type / shot_id
    path = folder / f"{old_hash}_to_{new_hash}.json"
    _safe_path(plan, path, "Review history")
    if path.exists():
        existing = _require_json(plan, path, "Review history")
        if (
            existing.get("old_selected_mp4_sha256") != old_hash
            or existing.get("current_selected_mp4_sha256") != new_hash
            or existing.get("review_record") != dict(record)
        ):
            raise PetSitcomReviewError(
                f"Review history conflict for {shot_id}."
            )
        return path
    metadata = {
        key: value
        for key, value in document.items()
        if key != "shots"
    }
    payload = {
        "schema_version": REVIEW_HISTORY_SCHEMA,
        "review_type": review_type,
        "shot_id": shot_id,
        "archived_at": _now(),
        "old_candidate_number": _record_candidate_number(record),
        "old_selected_mp4_path": str(
            record.get("selected_mp4_path") or ""
        ),
        "old_selected_mp4_sha256": old_hash,
        "current_candidate_number": _source_candidate_number(current),
        "current_selected_mp4_path": str(Path(current["path"]).resolve()),
        "current_selected_mp4_sha256": new_hash,
        "review_record": dict(record),
        "document_metadata": metadata,
    }
    _write_json(plan, path, payload)
    return path


def write_owner_native_audio_review_template(
    plan: PetSitcomPlan,
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
) -> Path:
    """Write the exact Task 4 schema without asserting a human pass."""
    _validate_plan(plan)
    current = dict(sources or _selected_sources(plan))
    path = plan.output_dir / "owner_native_audio_review.json"
    _safe_path(plan, path, "Owner native-audio review")
    existing = _read_json(path)
    existing_records = (
        existing.get("shots")
        if isinstance(existing.get("shots"), Mapping)
        else {}
    )
    records: dict[str, Any] = {}
    changed = False
    for shot in plan.shots:
        if shot.speaker != "owner":
            continue
        if shot.shot_id not in current:
            raise PetSitcomReviewError(
                f"Owner review source is missing for {shot.shot_id}."
            )
        source = current[shot.shot_id]
        _validate_current_source(plan, shot.shot_id, source)
        prior = existing_records.get(shot.shot_id)
        if _review_source_matches(prior, source):
            records[shot.shot_id] = dict(prior)
            continue
        if isinstance(prior, Mapping):
            _archive_review_record(
                plan,
                review_type="owner",
                shot_id=shot.shot_id,
                record=prior,
                current=source,
                document=existing,
            )
        records[shot.shot_id] = _pending_owner_record(source)
        changed = True
    unchanged_document = bool(
        existing
        and not changed
        and set(existing) == _OWNER_TOP_FIELDS
        and set(existing_records) == set(records)
    )
    if unchanged_document:
        return path
    _write_json(
        plan,
        path,
        {
            "schema_version": OWNER_NATIVE_AUDIO_REVIEW_SCHEMA,
            "reviewed": (
                existing.get("reviewed") is True if not changed else False
            ),
            "verified": (
                existing.get("verified") is True if not changed else False
            ),
            "shots": records,
            "generated_at": (
                str(existing.get("generated_at") or "")
                if not changed
                else ""
            ),
            "reviewer_method": OWNER_REVIEW_METHOD,
        },
    )
    return path


def validate_owner_native_audio_review(plan: PetSitcomPlan) -> dict[str, Any]:
    _validate_plan(plan)
    sources = _selected_sources(plan)
    path = plan.output_dir / "owner_native_audio_review.json"
    _safe_path(plan, path, "Owner native-audio review")
    document = _read_json(path)
    records = document.get("shots")
    expected_ids = {
        shot.shot_id for shot in plan.shots if shot.speaker == "owner"
    }
    if (
        set(document) != set(_OWNER_TOP_FIELDS)
        or document.get("schema_version") != OWNER_NATIVE_AUDIO_REVIEW_SCHEMA
        or document.get("reviewed") is not True
        or document.get("verified") is not True
        or not _iso(document.get("generated_at"))
        or document.get("reviewer_method") != OWNER_REVIEW_METHOD
        or not isinstance(records, Mapping)
        or set(records) != expected_ids
    ):
        raise PetSitcomReviewError(
            "Owner native-audio review is not completed by a human reviewer."
        )
    for shot_id in expected_ids:
        record = records[shot_id]
        source = sources[shot_id]
        if (
            not isinstance(record, Mapping)
            or set(record) != set(_OWNER_RECORD_FIELDS)
            or record.get("selected_mp4_path")
            != str(Path(source["path"]).resolve())
            or record.get("selected_mp4_sha256") != source["sha256"]
            or record.get("no_native_voice") is not True
            or record.get("room_tone_allowed") is not True
            or record.get("reviewer_method") != OWNER_REVIEW_METHOD
            or not _iso(record.get("reviewed_at"))
            or not isinstance(record.get("notes"), str)
        ):
            raise PetSitcomReviewError(
                "Owner native-audio review is invalid or stale."
            )
    return {
        "verified": True,
        "path": path,
        "source_hashes": {
            key: value["sha256"]
            for key, value in sources.items()
            if key in expected_ids
        },
        "document": document,
    }


def write_pet_shot_review_template(
    plan: PetSitcomPlan,
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
) -> Path:
    """Preserve current reviews and reset only changed selected shots."""
    _validate_plan(plan)
    current = dict(sources or _selected_sources(plan))
    known = {shot.shot_id for shot in plan.shots}
    if not current or not set(current) <= known:
        raise PetSitcomReviewError(
            "Shot review sources must contain known selected shots."
        )
    path = plan.shot_review_path
    _safe_path(plan, path, "Shot review")
    existing = _read_json(path)
    existing_records = (
        existing.get("shots")
        if isinstance(existing.get("shots"), Mapping)
        else {}
    )
    existing_timings = (
        existing.get("mouth_timing")
        if isinstance(existing.get("mouth_timing"), Mapping)
        else {}
    )
    shots = {
        str(shot_id): dict(record)
        for shot_id, record in existing_records.items()
        if shot_id in known and isinstance(record, Mapping)
    }
    changed = False
    for shot in plan.shots:
        if shot.shot_id not in current:
            continue
        source = current[shot.shot_id]
        _validate_current_source(plan, shot.shot_id, source)
        prior = shots.get(shot.shot_id)
        if _review_source_matches(prior, source):
            if _current_gate_schema(prior):
                continue
            upgraded = (
                _upgrade_legacy_shot_review(prior)
                if isinstance(prior, Mapping)
                else None
            )
            if upgraded is not None:
                shots[shot.shot_id] = upgraded
                changed = True
                continue
        if isinstance(prior, Mapping) and not _review_source_matches(
            prior, source
        ):
            archive_record = _v3_shot_review_for_archive(prior)
            if archive_record is not None:
                _archive_review_record(
                    plan,
                    review_type="shot",
                    shot_id=shot.shot_id,
                    record=archive_record,
                    current=source,
                    document=existing,
                )
        shots[shot.shot_id] = _pending_shot_review(source)
        changed = True
    mouth_timing = {
        str(shot_id): dict(record)
        for shot_id, record in existing_timings.items()
        if shot_id in _MOUTH_SHOTS and isinstance(record, Mapping)
    }
    for shot_id in _MOUTH_SHOTS:
        if shot_id not in current:
            continue
        source = current[shot_id]
        drive_hash = _source_drive_audio_sha256(source)
        audio_onset, audio_offset = _source_drive_audio_timing(source)
        prior = mouth_timing.get(shot_id)
        if (
            isinstance(prior, Mapping)
            and set(prior) == set(_MOUTH_TIMING_FIELDS)
            and prior.get("selected_mp4_sha256") == source["sha256"]
            and prior.get("drive_audio_sha256") == drive_hash
        ):
            refreshed = dict(prior)
            refreshed["audio_onset_seconds"] = audio_onset
            refreshed["audio_offset_seconds"] = audio_offset
            refreshed["onset_error_seconds"] = _derived_timing_error(
                audio_onset,
                refreshed.get("mouth_onset_seconds"),
                refreshed.get("onset_error_seconds"),
            )
            refreshed["offset_error_seconds"] = _derived_timing_error(
                audio_offset,
                refreshed.get("mouth_offset_seconds"),
                refreshed.get("offset_error_seconds"),
            )
            if refreshed != prior:
                mouth_timing[shot_id] = refreshed
                changed = True
            continue
        mouth_timing[shot_id] = _pending_mouth_timing(source)
        changed = True
    unchanged_document = bool(
        existing
        and not changed
        and existing.get("schema_version") == SHOT_REVIEW_SCHEMA
        and set(existing) == _SHOT_REVIEW_TOP_FIELDS
    )
    if unchanged_document:
        return path
    _write_json(
        plan,
        path,
        {
            "schema_version": SHOT_REVIEW_SCHEMA,
            "generated_at": (
                str(existing.get("generated_at"))
                if existing and not changed and _iso(existing.get("generated_at"))
                else _now()
            ),
            "shots": shots,
            "mouth_timing": mouth_timing,
        },
    )
    return path


def _validate_shot_review_record(
    shot_id: str,
    record: Any,
    source: Mapping[str, Any],
    duration: float,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise PetSitcomReviewError(
            f"Shot review for {shot_id} must be an object."
        )
    _require_exact_fields(
        record,
        _SHOT_REVIEW_FIELDS,
        f"Shot review for {shot_id}",
    )
    if (
        record.get("selected_mp4_path")
        != str(Path(source["path"]).resolve())
        or record.get("selected_mp4_sha256") != source["sha256"]
    ):
        raise PetSitcomReviewError(f"Shot review for {shot_id} is stale.")
    if record.get("reviewed") is not True or not _iso(
        record.get("reviewed_at")
    ):
        raise PetSitcomReviewError(
            f"Shot review for {shot_id} is not completed by a human reviewer."
        )
    gates = record.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(
        SHOT_REVIEW_GATES
    ):
        raise PetSitcomReviewError(
            f"Shot review for {shot_id} has invalid hard gates."
        )
    derived_pass = True
    failed_issue_codes: set[str] = set()
    for gate_name in SHOT_REVIEW_GATES:
        gate = gates[gate_name]
        if not isinstance(gate, Mapping):
            raise PetSitcomReviewError(
                f"Shot review gate {gate_name} must be structured."
            )
        _require_exact_fields(
            gate,
            _GATE_FIELDS,
            f"Shot review gate {gate_name}",
        )
        if type(gate.get("passed")) is not bool:
            raise PetSitcomReviewError(
                f"Shot review gate {gate_name} must explicitly pass or fail."
            )
        notes = gate.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            raise PetSitcomReviewError(
                f"Shot review gate {gate_name} requires specific notes."
            )
        timestamps = _validate_manual_timestamps(
            gate.get("timestamps_seconds"),
            duration,
            f"{shot_id}/{gate_name}",
        )
        issue_codes = gate.get("issue_codes")
        if (
            not isinstance(issue_codes, list)
            or len(issue_codes) != len(set(issue_codes))
            or any(
                not isinstance(code, str)
                or code not in _GATE_ISSUE_CODES[gate_name]
                for code in issue_codes
            )
        ):
            raise PetSitcomReviewError(
                f"Shot review gate {gate_name} has an invalid issue code."
            )
        if gate["passed"] is True:
            if issue_codes:
                raise PetSitcomReviewError(
                    f"Passing shot review gate {gate_name} may not have an issue code."
                )
        else:
            if not timestamps:
                raise PetSitcomReviewError(
                    f"Failed shot review gate {gate_name} requires a timestamp."
                )
            if not issue_codes:
                raise PetSitcomReviewError(
                    f"Failed shot review gate {gate_name} requires an issue code."
                )
            failed_issue_codes.update(issue_codes)
        derived_pass &= gate["passed"]
    if record.get("passed") is not derived_pass:
        raise PetSitcomReviewError(
            f"Shot review for {shot_id} overall result contradicts its gates."
        )
    retry_reason = record.get("retry_reason")
    if not isinstance(retry_reason, str):
        raise PetSitcomReviewError(
            f"Shot review for {shot_id} retry reason is invalid."
        )
    if derived_pass:
        if retry_reason:
            raise PetSitcomReviewError(
                f"Passing shot review for {shot_id} may not include a retry reason."
            )
    else:
        if retry_reason not in _RETRY_REASONS:
            raise PetSitcomReviewError(
                f"Failed shot review for {shot_id} requires an allowed retry reason."
            )
        if retry_reason not in failed_issue_codes:
            raise PetSitcomReviewError(
                f"Shot review retry reason for {shot_id} does not match a failed gate issue code."
            )
    return {"passed": derived_pass, "retry_reason": retry_reason}


def _validate_mouth_timing_record(
    shot_id: str,
    record: Any,
    source: Mapping[str, Any],
    duration: float,
) -> None:
    if not isinstance(record, Mapping):
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} must be an object."
        )
    _require_exact_fields(
        record,
        _MOUTH_TIMING_FIELDS,
        f"Mouth timing for {shot_id}",
    )
    if (
        record.get("selected_mp4_sha256") != source["sha256"]
        or record.get("drive_audio_sha256")
        != _source_drive_audio_sha256(source)
    ):
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} is stale or has stale drive audio."
        )
    numeric_fields = (
        "audio_onset_seconds",
        "mouth_onset_seconds",
        "audio_offset_seconds",
        "mouth_offset_seconds",
        "onset_error_seconds",
        "offset_error_seconds",
    )
    values: dict[str, float] = {}
    for field in numeric_fields:
        value = record.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise PetSitcomReviewError(
                f"Mouth timing {field} for {shot_id} must be finite."
            )
        if float(value) < 0.0:
            raise PetSitcomReviewError(
                f"Mouth timing {field} for {shot_id} must be non-negative."
            )
        values[field] = float(value)
    if any(
        values[field] > duration
        for field in (
            "audio_onset_seconds",
            "mouth_onset_seconds",
            "audio_offset_seconds",
            "mouth_offset_seconds",
        )
    ):
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} exceeds source duration."
        )
    if (
        values["audio_onset_seconds"] > values["audio_offset_seconds"]
        or values["mouth_onset_seconds"] > values["mouth_offset_seconds"]
    ):
        raise PetSitcomReviewError(
            f"Mouth timing order for {shot_id} is invalid."
        )
    audio_interval = (
        values["audio_offset_seconds"] - values["audio_onset_seconds"]
    )
    mouth_interval = (
        values["mouth_offset_seconds"] - values["mouth_onset_seconds"]
    )
    if (
        audio_interval < _MIN_MOUTH_TIMING_INTERVAL_SECONDS
        or mouth_interval < _MIN_MOUTH_TIMING_INTERVAL_SECONDS
    ):
        raise PetSitcomReviewError(
            f"Mouth timing interval for {shot_id} is empty or too short."
        )
    canonical_audio_onset, canonical_audio_offset = (
        _source_drive_audio_timing(source)
    )
    if (
        values["audio_onset_seconds"] != canonical_audio_onset
        or values["audio_offset_seconds"] != canonical_audio_offset
    ):
        raise PetSitcomReviewError(
            f"Mouth timing audio endpoints for {shot_id} do not match canonical Task 2 timing."
        )
    expected_onset_error = abs(
        values["audio_onset_seconds"] - values["mouth_onset_seconds"]
    )
    expected_offset_error = abs(
        values["audio_offset_seconds"] - values["mouth_offset_seconds"]
    )
    if not math.isclose(
        values["onset_error_seconds"],
        expected_onset_error,
        abs_tol=1e-9,
    ):
        raise PetSitcomReviewError(
            f"Mouth timing onset error for {shot_id} contradicts measurements."
        )
    if not math.isclose(
        values["offset_error_seconds"],
        expected_offset_error,
        abs_tol=1e-9,
    ):
        raise PetSitcomReviewError(
            f"Mouth timing offset error for {shot_id} contradicts measurements."
        )
    if (
        record.get("max_onset_error_seconds") != 0.25
        or record.get("max_offset_error_seconds") != 0.25
    ):
        raise PetSitcomReviewError(
            f"Mouth timing threshold for {shot_id} must remain 0.25 seconds."
        )
    if values["onset_error_seconds"] > 0.25:
        raise PetSitcomReviewError(
            f"mouth onset for {shot_id} exceeds 0.25 seconds."
        )
    if values["offset_error_seconds"] > 0.25:
        raise PetSitcomReviewError(
            f"mouth offset for {shot_id} exceeds 0.25 seconds."
        )
    if record.get("no_silent_mouth_flapping") is not True:
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} reports silent mouth flapping."
        )
    if record.get("no_closed_mouth_during_speech") is not True:
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} reports closed mouth during speech."
        )
    if record.get("reviewed") is not True:
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} is not completed."
        )
    if record.get("passed") is not True:
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} must pass."
        )


def _validate_pending_mouth_timing_record(
    shot_id: str,
    record: Any,
    source: Mapping[str, Any],
    duration: float,
) -> None:
    if not isinstance(record, Mapping):
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} must be an object."
        )
    _require_exact_fields(
        record,
        _MOUTH_TIMING_FIELDS,
        f"Mouth timing for {shot_id}",
    )
    expected = _pending_mouth_timing(source)
    if dict(record) != expected:
        raise PetSitcomReviewError(
            f"Failed shot mouth timing for {shot_id} must remain the current pending template."
        )
    if float(expected["audio_offset_seconds"]) > duration:
        raise PetSitcomReviewError(
            f"Mouth timing for {shot_id} exceeds source duration."
        )


def _validate_failed_mouth_timing_record(
    shot_id: str,
    record: Any,
    source: Mapping[str, Any],
    duration: float,
) -> None:
    if isinstance(record, Mapping) and record.get("reviewed") is True:
        _validate_mouth_timing_record(shot_id, record, source, duration)
        return
    _validate_pending_mouth_timing_record(
        shot_id,
        record,
        source,
        duration,
    )


def validate_pet_shot_reviews(plan: PetSitcomPlan) -> dict[str, Any]:
    """Validate human gate detail and derive each overall result."""
    source_evidence = validate_source_evidence(plan)
    sources = source_evidence["sources"]
    durations = source_evidence["durations"]
    document = _require_json(plan, plan.shot_review_path, "Shot review")
    _require_exact_fields(
        document, _SHOT_REVIEW_TOP_FIELDS, "Shot review document"
    )
    if (
        document.get("schema_version") != SHOT_REVIEW_SCHEMA
        or not _iso(document.get("generated_at"))
        or not isinstance(document.get("shots"), Mapping)
    ):
        raise PetSitcomReviewError("Shot review document is incomplete.")
    records = document["shots"]
    expected_ids = {shot.shot_id for shot in plan.shots}
    if set(records) != expected_ids:
        raise PetSitcomReviewError(
            "Shot review must cover exactly ten selected shots."
        )
    mouth_timing = document.get("mouth_timing")
    if (
        not isinstance(mouth_timing, Mapping)
        or set(mouth_timing) != set(_MOUTH_SHOTS)
    ):
        raise PetSitcomReviewError(
            "Shot review mouth timing records are missing or contain extras."
        )
    failed: list[str] = []
    results: dict[str, Mapping[str, Any]] = {}
    for shot in plan.shots:
        result = _validate_shot_review_record(
            shot.shot_id,
            records[shot.shot_id],
            sources[shot.shot_id],
            durations[shot.shot_id],
        )
        results[shot.shot_id] = result
        if not result["passed"]:
            failed.append(shot.shot_id)
    for shot_id in _MOUTH_SHOTS:
        mouth_validator = (
            _validate_mouth_timing_record
            if results[shot_id]["passed"]
            else _validate_failed_mouth_timing_record
        )
        mouth_validator(
            shot_id,
            mouth_timing[shot_id],
            sources[shot_id],
            durations[shot_id],
        )
    return {
        "passed": not failed,
        "failed_shots": failed,
        "path": plan.shot_review_path,
        "document": document,
        "durations": durations,
    }


def write_pet_sitcom_review_markdown(plan: PetSitcomPlan) -> Path:
    """Write a final review only after all evidence and human gates validate."""
    source = validate_source_evidence(plan)
    final = validate_final_evidence(plan)
    shots = validate_pet_shot_reviews(plan)
    if not shots["passed"]:
        raise PetSitcomReviewError(
            "Final review markdown requires every human shot review to pass."
        )
    owner = validate_owner_native_audio_review(plan)
    task2 = _validate_anchor_and_mouth_reviews(plan)
    timings = _validate_composition_preflight(plan)
    selections = _selection_details(plan, source["sources"])
    history = _validated_shot_review_history(
        plan,
        source["sources"],
        selections,
    )
    source_records = {
        record["name"]: record for record in source["qc"]["records"]
    }
    final_records = final["records"]
    shot_lines = []
    problem_lines = []
    for shot in plan.shots:
        record = shots["document"]["shots"][shot.shot_id]
        detail = selections[shot.shot_id]
        gate_summary = "; ".join(
            (
                f"{gate_name}=pass, notes={gate['notes']}, "
                f"timestamps={gate['timestamps_seconds']}"
            )
            for gate_name, gate in record["gates"].items()
        )
        shot_lines.append(
            f"- {shot.shot_id} `{detail['video_sha256']}` candidate "
            f"{detail['candidate_number']}: {gate_summary}"
        )
    for item in history:
        record = item["review_record"]
        for gate_name, gate in record["gates"].items():
            if gate["passed"] is not False:
                continue
            for issue_code in gate["issue_codes"]:
                problem_lines.append(
                    f"- {item['shot_id']}/{gate_name}: "
                    f"issue_code={issue_code}; "
                    f"timestamps={gate['timestamps_seconds']}; "
                    f"notes={gate['notes']}"
                )
    if not problem_lines:
        problem_lines = [
            "- No failed hard gates remain in the validated selected candidates."
        ]
    candidate_lines = []
    for shot_id, detail in selections.items():
        candidate_number = detail["candidate_number"]
        if candidate_number == 1:
            continue
        matching = _review_history_for_selection(
            plan,
            shot_id,
            candidate_number,
            source["sources"][shot_id],
            history,
        )
        if not matching:
            raise PetSitcomReviewError(
                f"Candidate {candidate_number} review history is missing "
                f"for {shot_id}."
            )
        issue_codes = {
            code
            for item in matching
            for gate in item["review_record"]["gates"].values()
            if gate["passed"] is False
            for code in gate["issue_codes"]
        }
        if detail["retry_reason"] not in issue_codes:
            raise PetSitcomReviewError(
                f"Candidate {candidate_number} retry reason conflicts with "
                f"review history for {shot_id}."
            )
        candidate_lines.append(
            f"- {shot_id}: candidate {candidate_number}; "
            f"retry_reason={detail['retry_reason']}; "
            f"prompt_change={detail['retry_suffix']}; "
            f"prompt_sha256={detail['prompt_sha256']}; "
            "retest_result=selected_after_human_review"
        )
    if not candidate_lines:
        candidate_lines = [
            "- No selected production shot uses a retry candidate."
        ]
    source_qc_lines = [
        (
            f"- {name}: duration={record['duration_seconds']:.3f}s, "
            f"black_max={record['blackdetect']['max_duration_seconds']:.3f}s, "
            f"freeze_max={record['freezedetect']['max_duration_seconds']:.3f}s"
        )
        for name, record in source_records.items()
    ]
    final_qc_lines = [
        (
            f"- {name}: sha256={record['sha256']}, "
            f"duration={record['duration_seconds']:.3f}s, "
            f"integrated={record['loudness']['integrated_lufs']:.2f} LUFS, "
            f"true_peak={record['loudness']['true_peak_dbtp']:.2f} dBTP"
        )
        for name, record in final_records.items()
    ]
    timing_lines = [
        (
            f"- {timing.shot_id}: speaker={timing.speaker}, "
            f"start={timing.start_seconds:.3f}s, "
            f"end={timing.end_seconds:.3f}s, text={timing.text}"
        )
        for timing in timings
    ]
    anchor = task2["anchor"]
    mouth = task2["mouth"]
    text = (
        f"# {plan.title} review\n\n"
        "## 1. Originality/reference-use boundary\n"
        "This is an original pet-story workflow. Reference material informed "
        "only high-level genre observations; no reference frames, audio, "
        "dialogue, watermarks, characters, or packaging were reused.\n\n"
        "## 2. Models, providers, candidates, and hashes\n"
        "Image: gateway / `doubao-seedream-4-5`. Video: gateway / "
        "`doubao-seedance-2-0`. Voice overlays: Doubao / `seed-tts-2.0`. "
        "Owner fixed voice: `zh_female_vv_uranus_bigtts`. "
        "Naitang fixed voice: `saturn_zh_female_tiaopigongzhu_tob`. "
        "Doubao fixed voice: `saturn_zh_female_keainvsheng_tob`.\n"
        + "\n".join(
            (
                f"- {shot_id}: candidate {detail['candidate_number']}, "
                f"sha256={detail['video_sha256']}, "
                f"prompt_sha256={detail['prompt_sha256']}"
            )
            for shot_id, detail in selections.items()
        )
        + "\n\n## 3. Anchor and audio-drive outcomes\n"
        f"Anchor review approved={anchor['approved']}, "
        f"source_hashes={json.dumps(anchor['source_hashes'], ensure_ascii=False)}.\n"
        f"Audio-drive probe approved={mouth.get('approved')}, "
        f"capability={mouth.get('capability', 'approved')}, "
        f"probe_sha256={mouth.get('probe_mp4_sha256', '')}, "
        f"notes={mouth.get('notes', '')}.\n\n"
        "## 4. Per-shot action, identity, anatomy, speaker, and continuity findings\n"
        + "\n".join(shot_lines)
        + "\n\n## 5. Problems and exact timestamps\n"
        + "\n".join(problem_lines)
        + "\n\n## 6. Candidate-2 prompt changes and retest result\n"
        + "\n".join(candidate_lines)
        + "\n\n## 7. Subtitle/audio/technical preflight\n"
        + "\n".join(source_qc_lines + final_qc_lines + timing_lines)
        + f"\nOwner native-audio review verified={owner['verified']}.\n\n"
        "## 8. Residual risks\n"
        "The evidence is hash-bound to the current local outputs. Subjective "
        "feline acting and visual speech alignment remain human observations "
        "rather than model-independent measurements.\n\n"
        "## 9. Mouth-sync review boundary\n"
        "口型已经完成**逐帧人工复核**与主观的起止/停顿观察；"
        "**未进行音素级认证**。\n"
    )
    _write_text(plan, plan.review_markdown_path, text)
    return plan.review_markdown_path


def _source_sequence(
    plan: PetSitcomPlan,
    shot_id: str,
    source: Mapping[str, Any],
    duration: float,
    *,
    sample_duration: float,
    folder: str,
    label: str,
    frame_count: int,
    layout: str,
    runner: Callable[..., Any],
    ffmpeg: str,
    extracted_at: str,
) -> dict[str, Any]:
    path = Path(source["path"])
    output = _evidence_root(plan) / folder / f"{label}.png"
    timestamps = _sample_timestamps(frame_count, sample_duration)
    _extract_at_times(
        plan,
        path,
        output,
        timestamps,
        layout,
        runner,
        ffmpeg,
    )
    return {
        "shot_id": shot_id,
        "label": label,
        "source_path": str(path.resolve()),
        "selected_mp4_sha256": str(source["sha256"]),
        "source_duration_seconds": duration,
        "timestamps_seconds": timestamps,
        "layout": layout,
        "evidence_path": str(output.resolve()),
        "evidence_sha256": _sha(output),
        "extracted_at": extracted_at,
    }


def _final_sequence(
    plan: PetSitcomPlan,
    label: str,
    source: Path,
    source_hash: str,
    duration: float,
    *,
    sample_duration: float,
    frame_count: int,
    layout: str,
    output: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
    extracted_at: str,
) -> dict[str, Any]:
    timestamps = _sample_timestamps(frame_count, sample_duration)
    _extract_at_times(
        plan,
        source,
        output,
        timestamps,
        layout,
        runner,
        ffmpeg,
    )
    return {
        "label": label,
        "source_path": str(source.resolve()),
        "source_sha256": source_hash,
        "source_duration_seconds": duration,
        "timestamps_seconds": timestamps,
        "layout": layout,
        "evidence_path": str(output.resolve()),
        "evidence_sha256": _sha(output),
        "extracted_at": extracted_at,
    }


def _continuity_evidence(
    plan: PetSitcomPlan,
    previous_id: str,
    current_id: str,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    previous_duration: float,
    current_duration: float,
    runner: Callable[..., Any],
    ffmpeg: str,
    extracted_at: str,
    *,
    previous_video_duration: float | None = None,
    current_video_duration: float | None = None,
) -> dict[str, Any]:
    shots = {shot.shot_id: shot for shot in plan.shots}
    previous_shot = shots.get(previous_id)
    current_shot = shots.get(current_id)
    if (
        previous_shot is None
        or current_shot is None
        or previous_id not in current_shot.continuity_source_ids
    ):
        raise PetSitcomReviewError(
            "Continuity evidence must follow a declared plan dependency."
        )
    edit_end = float(previous_shot.duration_seconds)
    previous_video_duration = (
        previous_duration
        if previous_video_duration is None
        else previous_video_duration
    )
    current_video_duration = (
        current_duration
        if current_video_duration is None
        else current_video_duration
    )
    if (
        not math.isfinite(previous_video_duration)
        or not math.isfinite(current_video_duration)
        or previous_video_duration <= 0.0
        or current_video_duration <= 0.0
    ):
        raise PetSitcomReviewError(
            "Continuity video durations must be finite and positive."
        )
    previous_times = _continuity_previous_times(
        previous,
        edit_end=edit_end,
        video_duration=previous_video_duration,
    )
    current_times = [0.04, 0.12, 0.30]
    if (
        previous_times[0] < 0.0
        or previous_times[-1] > previous_video_duration - 0.001
        or current_times[-1] > current_video_duration - 0.001
    ):
        raise PetSitcomReviewError(
            "Continuity timestamps cannot preserve the declared edit endpoint safely."
        )
    label = _continuity_label(previous_id, current_id)
    output = (
        _evidence_root(plan)
        / "continuity"
        / f"{previous_id}_to_{current_id}.png"
    )
    frame_root = output.with_suffix("")
    frame_paths = [
        *(
            frame_root / f"previous_{index:02d}.png"
            for index in range(1, 4)
        ),
        *(
            frame_root / f"current_{index:02d}.png"
            for index in range(1, 4)
        ),
    ]
    sources_and_times = [
        *((Path(previous["path"]), value) for value in previous_times),
        *((Path(current["path"]), value) for value in current_times),
    ]
    for frame_path, (source_path, timestamp) in zip(
        frame_paths,
        sources_and_times,
        strict=True,
    ):
        _extract_frame(
            plan,
            source_path,
            timestamp,
            frame_path,
            runner,
            ffmpeg,
        )
    _compose_frame_sheet(
        plan,
        frame_paths,
        output,
        runner,
        ffmpeg,
    )
    return {
        "previous_shot_id": previous_id,
        "current_shot_id": current_id,
        "label": label,
        "previous_source_path": str(Path(previous["path"]).resolve()),
        "previous_selected_mp4_sha256": str(previous["sha256"]),
        "previous_duration_seconds": previous_duration,
        "previous_video_duration_seconds": previous_video_duration,
        "previous_edit_end_seconds": edit_end,
        "current_source_path": str(Path(current["path"]).resolve()),
        "current_selected_mp4_sha256": str(current["sha256"]),
        "current_duration_seconds": current_duration,
        "current_video_duration_seconds": current_video_duration,
        "previous_timestamps_seconds": previous_times,
        "current_timestamps_seconds": current_times,
        "frame_paths": [str(path.resolve()) for path in frame_paths],
        "frame_sha256": [_sha(path) for path in frame_paths],
        "evidence_path": str(output.resolve()),
        "evidence_sha256": _sha(output),
        "extracted_at": extracted_at,
    }


def _continuity_previous_times(
    previous: Mapping[str, Any],
    *,
    edit_end: float,
    video_duration: float,
) -> list[float]:
    endpoint = previous.get("continuity_timestamp_seconds")
    if (
        isinstance(endpoint, bool)
        or not isinstance(endpoint, (int, float))
        or not math.isfinite(float(endpoint))
        or float(endpoint) <= 0.26
        or float(endpoint) > edit_end
        or float(endpoint) > video_duration - 0.001
    ):
        raise PetSitcomReviewError(
            "Continuity timestamps cannot preserve the declared edit endpoint safely."
        )
    endpoint_value = float(endpoint)
    return [
        round(endpoint_value - offset, 3)
        for offset in (0.26, 0.08, 0.0)
    ]


def _continuity_label(previous_id: str, current_id: str) -> str:
    if (previous_id, current_id) == ("shot_05", "shot_07"):
        return "main_axis_and_pose_return"
    if (previous_id, current_id) == ("shot_06", "shot_07"):
        return "tail_direction_match"
    return "state_match"


def _continuity_edges(plan: PetSitcomPlan) -> tuple[tuple[str, str], ...]:
    return tuple(
        (previous_id, shot.shot_id)
        for shot in plan.shots
        for previous_id in shot.continuity_source_ids
    )


def _sample_timestamps(count: int, duration: float) -> list[float]:
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or duration <= 0.02
    ):
        raise PetSitcomReviewError(
            "Evidence sampling requires a positive count and duration."
        )
    end = max(0.0, duration - 0.05)
    if count == 1:
        return [0.0]
    return [
        round(index * end / (count - 1), 3)
        for index in range(count)
    ]


def _extract_at_times(
    plan: PetSitcomPlan,
    source: Path,
    output: Path,
    timestamps: Sequence[float],
    layout: str,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> None:
    columns, rows = _parse_layout(layout)
    if columns * rows < len(timestamps):
        raise PetSitcomReviewError(
            "Evidence layout does not contain all requested frames."
        )
    _safe_path(plan, source, "Evidence source")
    _safe_path(plan, output, "Evidence image")
    temporary = _temporary(output)
    cell_width = max(16, 1080 // columns)
    cell_height = max(16, 1920 // rows)
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for timestamp in timestamps:
        command.extend(["-ss", _format_time(timestamp), "-i", str(source)])
    filters = []
    labels = []
    positions = []
    for index in range(len(timestamps)):
        label = f"v{index}"
        filters.append(
            f"[{index}:v]scale={cell_width}:{cell_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={cell_width}:{cell_height}:(ow-iw)/2:(oh-ih)/2:"
            f"color=black[{label}]"
        )
        labels.append(f"[{label}]")
        positions.append(
            f"{(index % columns) * cell_width}_"
            f"{(index // columns) * cell_height}"
        )
    filters.append(
        f"{''.join(labels)}xstack=inputs={len(timestamps)}:"
        f"layout={'|'.join(positions)}:fill=black[out]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(temporary),
        ]
    )
    try:
        _run(runner, command)
        _publish_asset(plan, temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _extract_frame(
    plan: PetSitcomPlan,
    source: Path,
    timestamp: float,
    output: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> None:
    _safe_path(plan, source, "Continuity source")
    _safe_path(plan, output, "Continuity frame")
    temporary = _temporary(output)
    try:
        _run(
            runner,
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                _format_time(timestamp),
                "-i",
                str(source),
                "-vf",
                (
                    "scale=360:640:force_original_aspect_ratio=decrease,"
                    "pad=360:640:(ow-iw)/2:(oh-ih)/2:color=black"
                ),
                "-frames:v",
                "1",
                str(temporary),
            ],
        )
        _publish_asset(plan, temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _compose_frame_sheet(
    plan: PetSitcomPlan,
    frames: Sequence[Path],
    output: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> None:
    if len(frames) != 6:
        raise PetSitcomReviewError(
            "Continuity evidence requires exactly six frames."
        )
    for frame in frames:
        _safe_path(plan, frame, "Continuity frame")
        if not frame.is_file():
            raise PetSitcomReviewError("Continuity frame is missing.")
    _safe_path(plan, output, "Continuity evidence")
    temporary = _temporary(output)
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for frame in frames:
        command.extend(["-i", str(frame)])
    command.extend(
        [
            "-filter_complex",
            (
                "[0:v][1:v][2:v]hstack=inputs=3[top];"
                "[3:v][4:v][5:v]hstack=inputs=3[bottom];"
                "[top][bottom]vstack=inputs=2[out]"
            ),
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(temporary),
        ]
    )
    try:
        _run(runner, command)
        _publish_asset(plan, temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _extract_pair(
    plan: PetSitcomPlan,
    first_source: Path,
    first_time: float,
    second_source: Path,
    second_time: float,
    output: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> None:
    _safe_path(plan, first_source, "Continuity source")
    _safe_path(plan, second_source, "Continuity source")
    _safe_path(plan, output, "Continuity evidence")
    temporary = _temporary(output)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        _format_time(first_time),
        "-i",
        str(first_source),
        "-ss",
        _format_time(second_time),
        "-i",
        str(second_source),
        "-filter_complex",
        (
            "[0:v]scale=540:960:force_original_aspect_ratio=decrease,"
            "pad=540:960:(ow-iw)/2:(oh-ih)/2:black[a];"
            "[1:v]scale=540:960:force_original_aspect_ratio=decrease,"
            "pad=540:960:(ow-iw)/2:(oh-ih)/2:black[b];"
            "[a][b]hstack=inputs=2[out]"
        ),
        "-map",
        "[out]",
        "-frames:v",
        "1",
        str(temporary),
    ]
    try:
        _run(runner, command)
        _publish_asset(plan, temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _technical_record(
    plan: PetSitcomPlan,
    name: str,
    source: Mapping[str, Any],
    *,
    final: bool,
    command_runner: Callable[..., Any],
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> dict[str, Any]:
    path = Path(source["path"])
    _safe_path(plan, path, "Technical QC source")
    blank = {
        "filter": "not_run",
        "starts_seconds": [],
        "ends_seconds": [],
        "durations_seconds": [],
        "max_duration_seconds": 0.0,
    }
    try:
        exists = path.is_file() and path.stat().st_size > 0
    except OSError:
        exists = False
    if not exists:
        return {
            "name": name,
            "path": str(path.resolve()),
            "sha256": str(source["sha256"]),
            "ffprobe": {},
            "duration_seconds": 0.0,
            "video_duration_seconds": 0.0,
            "audio_present": False,
            "blackdetect": blank,
            "freezedetect": blank,
            "loudness": None,
            "passed": False,
            "errors": ["media file is missing or empty"],
            "checked_at": _now(),
        }
    video_duration = 0.0
    try:
        probe = _ffprobe(command_runner, ffprobe_bin, path)
        video_duration = _video_duration(probe)
        expected_duration, expected_audio_streams = _technical_expectations(
            plan,
            name,
            final=final,
            source=source,
        )
        analysis_duration = (
            None
            if final
            else _source_analysis_duration(plan, name)
        )
        black = _detect(
            command_runner,
            ffmpeg_bin,
            path,
            "blackdetect=d=0.08:pix_th=0.10",
            "black_duration",
            source_duration=_duration(probe),
            analysis_duration=analysis_duration,
        )
        freeze = _detect(
            command_runner,
            ffmpeg_bin,
            path,
            "freezedetect=n=-50dB:d=0.35",
            "freeze_duration",
            source_duration=_duration(probe),
            analysis_duration=analysis_duration,
        )
        loudness = (
            _loudness(command_runner, ffmpeg_bin, path)
            if final
            else None
        )
        errors = _technical_errors(
            probe,
            black,
            freeze,
            loudness,
            final,
            path,
            expected_duration=expected_duration,
            expected_audio_streams=expected_audio_streams,
            minimum_video_duration=(
                float(plan.duration_seconds)
                if final
                else (
                    None
                    if _local_recut_micro_retime_allowed(
                        plan,
                        name,
                        source,
                        video_duration,
                    )
                    else float(
                        next(
                            shot
                            for shot in plan.shots
                            if shot.shot_id == name
                        ).duration_seconds
                    )
                )
            ),
        )
    except _VideoStreamDurationError:
        raise
    except PetSitcomReviewError:
        probe, black, freeze, loudness = {}, blank, blank, None
        errors = ["local technical inspection failed"]
    return {
        "name": name,
        "path": str(path.resolve()),
        "sha256": str(source["sha256"]),
        "ffprobe": probe,
        "duration_seconds": _duration(probe),
        "video_duration_seconds": video_duration,
        "audio_present": _stream_count(probe, "audio") > 0,
        "blackdetect": black,
        "freezedetect": freeze,
        "loudness": loudness,
        "passed": not errors,
        "errors": errors,
        "checked_at": _now(),
    }


def _ffprobe(
    runner: Callable[..., Any],
    binary: str,
    path: Path,
) -> dict[str, Any]:
    result = _run(
        runner,
        [
            binary,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,format_name:"
                "stream=index,codec_type,codec_name,profile,pix_fmt,width,"
                "height,avg_frame_rate,r_frame_rate,duration,sample_rate,"
                "channels,channel_layout,bit_rate"
            ),
            "-of",
            "json",
            str(path),
        ],
    )
    try:
        payload = json.loads(
            str(getattr(result, "stdout", "") or "{}")
        )
    except json.JSONDecodeError as exc:
        raise PetSitcomReviewError(
            "ffprobe did not return a valid local media report."
        ) from exc
    if not isinstance(payload, Mapping):
        raise PetSitcomReviewError(
            "ffprobe did not return a local media object."
        )
    return dict(payload)


def _detect(
    runner: Callable[..., Any],
    binary: str,
    path: Path,
    filter_name: str,
    key: str,
    *,
    source_duration: float,
    analysis_duration: float | None = None,
) -> dict[str, Any]:
    command = [
        binary,
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(path),
    ]
    if analysis_duration is not None:
        command.extend(["-t", _format_time(analysis_duration)])
    command.extend(
        [
            "-vf",
            filter_name,
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    result = _run(
        runner,
        command,
    )
    text = (
        f"{getattr(result, 'stdout', '')}\n"
        f"{getattr(result, 'stderr', '')}"
    )
    prefix = key.removesuffix("_duration")
    starts = _detected_values(text, f"{prefix}_start")
    logged_ends = _detected_values(text, f"{prefix}_end")
    logged_durations = _detected_values(text, key)
    ends: list[float] = []
    durations: list[float] = []
    for index, start in enumerate(starts):
        logged_duration = (
            logged_durations[index]
            if index < len(logged_durations)
            else None
        )
        if index < len(logged_ends):
            end = logged_ends[index]
        elif logged_duration is not None:
            end = start + logged_duration
        else:
            end = min(
                float(source_duration),
                (
                    float(analysis_duration)
                    if analysis_duration is not None
                    else float(source_duration)
                ),
            )
        if end < start:
            continue
        duration = (
            logged_duration
            if logged_duration is not None
            else end - start
        )
        ends.append(end)
        durations.append(max(0.0, duration))
    if not starts:
        ends = logged_ends
        durations = logged_durations
    return {
        "filter": filter_name,
        "starts_seconds": starts,
        "ends_seconds": ends,
        "durations_seconds": durations,
        "max_duration_seconds": max(durations, default=0.0),
    }


def _detected_values(text: str, key: str) -> list[float]:
    return [
        float(value)
        for value in re.findall(
            rf"{re.escape(key)}:\s*([0-9]+(?:\.[0-9]+)?)",
            text,
        )
    ]


def _loudness(
    runner: Callable[..., Any],
    binary: str,
    path: Path,
) -> dict[str, Any]:
    result = _run(
        runner,
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
    )
    text = (
        f"{getattr(result, 'stdout', '')}\n"
        f"{getattr(result, 'stderr', '')}"
    )
    integrated_matches = re.findall(
        r"\bI:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS",
        text,
    )
    true_peak_matches = re.findall(
        (
            r"True\s+peak:\s*"
            r"(?:.|\n)*?Peak:\s*"
            r"(-?[0-9]+(?:\.[0-9]+)?)\s*dB(?:FS|TP)"
        ),
        text,
        flags=re.IGNORECASE,
    )
    integrated = (
        float(integrated_matches[-1]) if integrated_matches else None
    )
    true_peak = (
        float(true_peak_matches[-1]) if true_peak_matches else None
    )
    available = (
        _is_finite_measurement(integrated)
        and _is_finite_measurement(true_peak)
    )
    return {
        "filter": "ebur128=peak=true",
        "integrated_lufs": integrated if available else None,
        "true_peak_dbtp": true_peak if available else None,
        "measurement_available": available,
    }


def _technical_errors(
    probe: Mapping[str, Any],
    black: Mapping[str, Any],
    freeze: Mapping[str, Any],
    loudness: Mapping[str, Any] | None,
    final: bool,
    path: Path,
    *,
    expected_duration: float | None = None,
    expected_audio_streams: int | None = None,
    minimum_video_duration: float | None = None,
) -> list[str]:
    errors: list[str] = []
    videos = _stream_count(probe, "video")
    audios = _stream_count(probe, "audio")
    duration = _duration(probe)
    try:
        video_duration = _video_duration(probe)
    except PetSitcomReviewError:
        video_duration = 0.0
    if expected_duration is None:
        expected_duration = 54.0 if final else 5.0
    if expected_audio_streams is None:
        expected_audio_streams = 1
    tolerance = (
        _FINAL_DURATION_TOLERANCE
        if final
        else _SOURCE_DURATION_TOLERANCE
    )
    if (
        videos != 1
        or audios != expected_audio_streams
        or not math.isfinite(duration)
        or abs(duration - expected_duration) > tolerance
        or abs(video_duration - expected_duration) > tolerance
    ):
        errors.append("stream layout or duration is invalid")
    if (
        minimum_video_duration is not None
        and video_duration < minimum_video_duration
    ):
        errors.append("video stream is shorter than the edit duration")
    if float(black.get("max_duration_seconds", 0.0)) > _MAX_BLACK_SECONDS:
        errors.append("black run exceeds 0.08 seconds")
    if float(freeze.get("max_duration_seconds", 0.0)) > _MAX_FREEZE_SECONDS:
        errors.append("freeze exceeds 0.35 seconds")
    if final:
        video, audio = _stream(probe, "video"), _stream(probe, "audio")
        if not (
            video.get("codec_name") == "h264"
            and video.get("profile") == "High"
            and video.get("pix_fmt") == "yuv420p"
            and (video.get("width"), video.get("height")) == (1080, 1920)
            and video.get("avg_frame_rate") == "30/1"
            and video.get("r_frame_rate") == "30/1"
        ):
            errors.append("final video contract is invalid")
        if not (
            audio.get("codec_name") == "aac"
            and str(audio.get("sample_rate")) == "48000"
            and audio.get("channels") == 2
            and audio.get("channel_layout") == "stereo"
            and _int(audio.get("bit_rate")) >= _compose.MIN_AAC_BIT_RATE
        ):
            errors.append("final audio contract is invalid")
        if not _mp4_has_faststart(path):
            errors.append("final output lacks faststart")
        if (
            loudness is None
            or loudness.get("measurement_available") is not True
        ):
            errors.append("final loudness measurement is missing")
        elif not (
            _is_finite_measurement(loudness.get("integrated_lufs"))
            and _is_finite_measurement(loudness.get("true_peak_dbtp"))
        ):
            errors.append("final loudness measurement is invalid")
        else:
            integrated = float(loudness["integrated_lufs"])
            true_peak = float(loudness["true_peak_dbtp"])
            if abs(integrated - _INTEGRATED_TARGET) > _INTEGRATED_TOLERANCE:
                errors.append(
                    "integrated loudness is outside -16 LUFS tolerance"
                )
            if true_peak > _TRUE_PEAK_LIMIT:
                errors.append("true peak exceeds -1.5 dBTP tolerance")
    return errors


def _is_finite_measurement(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _technical_expectations(
    plan: PetSitcomPlan,
    name: str,
    *,
    final: bool,
    source: Mapping[str, Any] | None = None,
) -> tuple[float, int]:
    if final:
        return float(plan.duration_seconds), 1
    shot = next(
        (item for item in plan.shots if item.shot_id == name),
        None,
    )
    if shot is None:
        raise PetSitcomReviewError(
            f"Unknown source technical QC shot: {name}."
        )
    expected_duration = float(shot.generation_duration_seconds)
    if source is not None:
        candidate_number = source.get("candidate_number")
        if (
            type(candidate_number) is int
            and candidate_number in {1, 2, 3, 4, 5, 6}
        ):
            candidate = _generation._pet_candidate_path(
                shot,
                candidate_number,
            )
            state = _generation._read_json_object(
                _generation._pet_candidate_state_path(candidate)
            )
            if state.get("schema_version") == _generation.PET_LOCAL_RECUT_SCHEMA:
                recipe = state.get("recipe")
                output_duration = (
                    recipe.get("output_duration_seconds")
                    if isinstance(recipe, Mapping)
                    else None
                )
                if (
                    isinstance(output_duration, bool)
                    or not isinstance(output_duration, (int, float))
                    or not math.isfinite(float(output_duration))
                    or float(output_duration) <= 0.0
                ):
                    raise PetSitcomReviewError(
                        f"Local recut duration is invalid for {name}."
                    )
                expected_duration = float(output_duration)
    return (
        expected_duration,
        1 if shot.shot_id in _MOUTH_SHOTS else 0,
    )


def _local_recut_micro_retime_allowed(
    plan: PetSitcomPlan,
    shot_id: str,
    source: Mapping[str, Any],
    video_duration: float,
) -> bool:
    shot = next(
        (item for item in plan.shots if item.shot_id == shot_id),
        None,
    )
    if shot is None or not math.isfinite(video_duration):
        return False
    deficit = float(shot.duration_seconds) - video_duration
    if deficit <= 0.0:
        return False
    candidate = Path(str(source.get("path") or ""))
    state = _generation._read_json_object(
        _generation._pet_candidate_state_path(candidate)
    )
    recipe = state.get("recipe")
    return bool(
        deficit <= (1.0 / 24.0) + 1e-9
        and state.get("schema_version") == _generation.PET_LOCAL_RECUT_SCHEMA
        and state.get("provider_success") is True
        and state.get("shot_id") == shot_id
        and state.get("video_sha256") == source.get("sha256")
        and isinstance(recipe, Mapping)
        and recipe.get("output_fps") == 24
        and recipe.get("shot_id") == shot_id
        and recipe.get("output_duration_seconds") == shot.duration_seconds
    )


def _source_analysis_duration(
    plan: PetSitcomPlan,
    name: str,
) -> float:
    shot = next(
        (item for item in plan.shots if item.shot_id == name),
        None,
    )
    if shot is None:
        raise PetSitcomReviewError(
            f"Unknown source technical QC shot: {name}."
        )
    return float(shot.duration_seconds)


def _validate_qc_document(
    plan: PetSitcomPlan,
    path: Path,
    *,
    phase: str,
    expected: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    document = _require_json(plan, path, f"{phase} technical QC")
    _require_exact_fields(document, _QC_TOP_FIELDS, f"{phase} QC")
    schema = (
        SOURCE_TECHNICAL_SCHEMA
        if phase == "source"
        else FINAL_TECHNICAL_SCHEMA
    )
    records = document.get("records")
    if (
        document.get("schema_version") != schema
        or document.get("phase") != phase
        or not _iso(document.get("generated_at"))
        or not isinstance(records, list)
        or len(records) != len(expected)
    ):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} technical QC is invalid or incomplete."
        )
    by_name: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise PetSitcomReviewError(
                f"{phase.capitalize()} QC record must be an object."
            )
        _require_exact_fields(
            record,
            _QC_RECORD_FIELDS,
            f"{phase.capitalize()} QC record",
        )
        name = record.get("name")
        if (
            not isinstance(name, str)
            or name not in expected
            or name in by_name
        ):
            raise PetSitcomReviewError(
                f"{phase.capitalize()} QC records contain missing or extra entries."
            )
        by_name[name] = record
        _validate_qc_record(
            plan,
            record,
            phase=phase,
            expected_name=name,
            expected_item=expected[name],
        )
    if set(by_name) != set(expected):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} QC records contain missing or extra entries."
        )
    return document


def _validate_qc_record(
    plan: PetSitcomPlan,
    record: Any,
    *,
    phase: str,
    expected_name: str,
    expected_item: Mapping[str, Any],
    allow_failed: bool = False,
) -> Mapping[str, Any]:
    if phase not in {"source", "final"}:
        raise PetSitcomReviewError("Technical QC phase is invalid.")
    if not isinstance(record, Mapping):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} QC record must be an object."
        )
    _require_exact_fields(
        record,
        _QC_RECORD_FIELDS,
        f"{phase.capitalize()} QC record",
    )
    media_path = Path(expected_item["path"])
    _safe_path(plan, media_path, f"{phase.capitalize()} QC media")
    expected_duration, expected_audio_streams = _technical_expectations(
        plan,
        expected_name,
        final=phase == "final",
        source=expected_item,
    )
    if (
        record.get("name") != expected_name
        or record.get("path") != str(media_path.resolve())
        or record.get("sha256") != expected_item["sha256"]
        or _sha_or_empty(media_path) != expected_item["sha256"]
        or not _iso(record.get("checked_at"))
        or record.get("audio_present")
        is not (expected_audio_streams > 0)
    ):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} technical QC is stale or failed."
        )
    _validate_detection(record.get("blackdetect"), "blackdetect")
    _validate_detection(record.get("freezedetect"), "freezedetect")
    duration = record.get("duration_seconds")
    video_duration = record.get("video_duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or float(duration) <= 0.0
        or isinstance(video_duration, bool)
        or not isinstance(video_duration, (int, float))
        or not math.isfinite(float(video_duration))
        or float(video_duration) <= 0.0
    ):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} QC duration is invalid."
        )
    if phase == "source":
        shot = next(
            (
                item
                for item in plan.shots
                if item.shot_id == expected_name
            ),
            None,
        )
        if shot is None:
            raise PetSitcomReviewError(
                f"Unknown source technical QC shot: {expected_name}."
            )
        analysis_duration = float(shot.duration_seconds)
        for detector in (
            record["blackdetect"],
            record["freezedetect"],
        ):
            if any(
                float(end) > analysis_duration + 0.001
                for end in detector["ends_seconds"]
            ):
                raise PetSitcomReviewError(
                    f"Source QC for {expected_name} exceeds its edit window."
                )
        _validate_source_continuity_timing(
            expected_item,
            float(duration),
            float(shot.duration_seconds),
        )
    ffprobe = record.get("ffprobe")
    technical_errors = _technical_errors(
        ffprobe if isinstance(ffprobe, Mapping) else {},
        record["blackdetect"],
        record["freezedetect"],
        record.get("loudness")
        if isinstance(record.get("loudness"), Mapping)
        else None,
        phase == "final",
        media_path,
        expected_duration=expected_duration,
        expected_audio_streams=expected_audio_streams,
        minimum_video_duration=(
            float(plan.duration_seconds)
            if phase == "final"
            else (
                None
                if _local_recut_micro_retime_allowed(
                    plan,
                    expected_name,
                    expected_item,
                    float(video_duration),
                )
                else float(shot.duration_seconds)
            )
        ),
    )
    if (
        not isinstance(ffprobe, Mapping)
        or float(duration) != _duration(ffprobe)
        or float(video_duration) != _video_duration(ffprobe)
        or record.get("passed") is not (not technical_errors)
        or record.get("errors") != technical_errors
        or (technical_errors and not allow_failed)
    ):
        raise PetSitcomReviewError(
            f"{phase.capitalize()} technical QC video duration or claims "
            "conflict with measurements."
        )
    if phase == "source" and record.get("loudness") is not None:
        raise PetSitcomReviewError(
            "Source QC must not contain final loudness claims."
        )
    if phase == "final":
        _validate_loudness(record.get("loudness"))
    return record


def _validate_detection(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise PetSitcomReviewError(f"{label} evidence must be an object.")
    _require_exact_fields(value, _DETECTION_FIELDS, label)
    starts = value.get("starts_seconds")
    ends = value.get("ends_seconds")
    durations = value.get("durations_seconds")
    maximum = value.get("max_duration_seconds")
    expected_filter = {
        "blackdetect": "blackdetect=d=0.08:pix_th=0.10",
        "freezedetect": "freezedetect=n=-50dB:d=0.35",
    }.get(label)
    if (
        value.get("filter") != expected_filter
        or not isinstance(starts, list)
        or not isinstance(ends, list)
        or not isinstance(durations, list)
        or len(starts) != len(ends)
        or (starts and len(starts) != len(durations))
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or float(item) < 0.0
            for item in (*starts, *ends, *durations)
        )
        or any(float(end) < float(start) for start, end in zip(starts, ends))
        or any(
            abs((float(end) - float(start)) - float(duration)) > 0.001
            for start, end, duration in zip(starts, ends, durations)
        )
        or isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
        or float(maximum) < 0.0
        or float(maximum) != max((float(item) for item in durations), default=0.0)
    ):
        raise PetSitcomReviewError(f"{label} evidence is invalid.")


def _validate_loudness(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise PetSitcomReviewError("Final loudness evidence is missing.")
    _require_exact_fields(value, _LOUDNESS_FIELDS, "Final loudness")
    if (
        value.get("filter") != "ebur128=peak=true"
        or value.get("measurement_available") is not True
        or not _is_finite_measurement(value.get("integrated_lufs"))
        or not _is_finite_measurement(value.get("true_peak_dbtp"))
    ):
        raise PetSitcomReviewError("Final loudness evidence is invalid.")


def _validate_optional_incremental_sequence(
    plan: PetSitcomPlan,
    item: Any,
    *,
    shot_id: str,
    source: Mapping[str, Any],
    duration: float,
    video_duration: float,
    expected_shots: Sequence[str],
    frame_count: int,
    layout: str,
    folder: str,
) -> None:
    if shot_id not in expected_shots:
        if item is not None:
            raise PetSitcomReviewError(
                f"Incremental {folder} evidence contains an extra record."
            )
        return
    _validate_source_sequence(
        plan,
        item,
        shot_id,
        shot_id,
        source,
        duration,
        video_duration,
        frame_count,
        layout,
        _evidence_root(plan) / folder / f"{shot_id}.png",
    )


def _validate_incremental_props(
    plan: PetSitcomPlan,
    value: Any,
    shot_id: str,
    source: Mapping[str, Any],
    duration: float,
    video_duration: float,
) -> None:
    expected = {
        label
        for label, prop_shot_ids in _PROP_SHOTS.items()
        if shot_id in prop_shot_ids
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PetSitcomReviewError(
            "Incremental prop evidence is missing or contains extras."
        )
    for label in expected:
        _validate_source_sequence(
            plan,
            value[label],
            shot_id,
            label,
            source,
            duration,
            video_duration,
            9,
            "3x3",
            _evidence_root(plan)
            / "props"
            / label
            / shot_id
            / f"{label}.png",
        )


def _validate_incremental_continuity(
    plan: PetSitcomPlan,
    value: Any,
    shot: Any,
    sources: Mapping[str, Mapping[str, Any]],
    duration: float,
    video_duration: float,
) -> None:
    expected_ids = tuple(shot.continuity_source_ids)
    if not isinstance(value, list) or len(value) != len(expected_ids):
        raise PetSitcomReviewError(
            "Incremental continuity evidence is missing or contains extras."
        )
    for previous_id, item in zip(expected_ids, value, strict=True):
        previous_duration = (
            item.get("previous_duration_seconds")
            if isinstance(item, Mapping)
            else None
        )
        previous_video_duration = (
            item.get("previous_video_duration_seconds")
            if isinstance(item, Mapping)
            else None
        )
        item_current_video_duration = (
            item.get("current_video_duration_seconds")
            if isinstance(item, Mapping)
            else None
        )
        expected, _expected_audio_streams = _technical_expectations(
            plan,
            previous_id,
            final=False,
            source=sources[previous_id],
        )
        if (
            isinstance(previous_duration, bool)
            or not isinstance(previous_duration, (int, float))
            or not math.isfinite(float(previous_duration))
            or abs(float(previous_duration) - expected)
            > _SOURCE_DURATION_TOLERANCE
        ):
            raise PetSitcomReviewError(
                "Incremental continuity duration is invalid."
            )
        if (
            isinstance(previous_video_duration, bool)
            or not isinstance(previous_video_duration, (int, float))
            or not math.isfinite(float(previous_video_duration))
            or float(previous_video_duration) <= 0.0
            or item_current_video_duration != video_duration
        ):
            raise PetSitcomReviewError(
                "Incremental continuity video duration is invalid."
            )
        _validate_continuity_item(
            plan,
            item,
            previous_id,
            shot.shot_id,
            sources,
            {
                previous_id: float(previous_duration),
                shot.shot_id: duration,
            },
            {
                previous_id: float(previous_video_duration),
                shot.shot_id: video_duration,
            },
        )


def _validate_sequence_group(
    plan: PetSitcomPlan,
    group: Any,
    expected_ids: Sequence[str],
    sources: Mapping[str, Mapping[str, Any]],
    durations: Mapping[str, float],
    video_durations: Mapping[str, float],
    *,
    frame_count: int,
    layout: str,
    folder: str,
) -> None:
    if not isinstance(group, Mapping) or set(group) != set(expected_ids):
        raise PetSitcomReviewError(
            f"Source evidence {folder} records are missing or contain extras."
        )
    for shot_id in expected_ids:
        _validate_source_sequence(
            plan,
            group[shot_id],
            shot_id,
            shot_id,
            sources[shot_id],
            durations[shot_id],
            video_durations[shot_id],
            frame_count,
            layout,
            _evidence_root(plan) / folder / f"{shot_id}.png",
        )


def _validate_source_sequence(
    plan: PetSitcomPlan,
    item: Any,
    shot_id: str,
    label: str,
    source: Mapping[str, Any],
    duration: float,
    video_duration: float,
    frame_count: int,
    layout: str,
    expected_output: Path,
) -> None:
    if not isinstance(item, Mapping):
        raise PetSitcomReviewError("Source sequence evidence must be an object.")
    _require_exact_fields(item, _SEQUENCE_FIELDS, "Source sequence evidence")
    timestamps = _sample_timestamps(frame_count, video_duration)
    if (
        item.get("shot_id") != shot_id
        or item.get("label") != label
        or item.get("source_path") != str(Path(source["path"]).resolve())
        or item.get("selected_mp4_sha256") != source["sha256"]
        or item.get("source_duration_seconds") != duration
        or item.get("timestamps_seconds") != timestamps
        or item.get("layout") != layout
        or not _iso(item.get("extracted_at"))
    ):
        raise PetSitcomReviewError("Source sequence evidence is stale.")
    _validate_image_binding(plan, item, expected_output)


def _validate_final_sequence(
    plan: PetSitcomPlan,
    item: Any,
    label: str,
    source: Path,
    record: Mapping[str, Any],
    frame_count: int,
    layout: str,
    expected_output: Path,
) -> None:
    if not isinstance(item, Mapping):
        raise PetSitcomReviewError("Final sequence evidence must be an object.")
    _require_exact_fields(
        item,
        _FINAL_SEQUENCE_FIELDS,
        "Final sequence evidence",
    )
    duration = float(record["duration_seconds"])
    video_duration = float(record["video_duration_seconds"])
    if (
        item.get("label") != label
        or item.get("source_path") != str(source.resolve())
        or item.get("source_sha256") != record["sha256"]
        or item.get("source_duration_seconds") != duration
        or item.get("timestamps_seconds")
        != _sample_timestamps(frame_count, video_duration)
        or item.get("layout") != layout
        or not _iso(item.get("extracted_at"))
    ):
        raise PetSitcomReviewError("Final sequence evidence is stale.")
    _validate_image_binding(plan, item, expected_output)


def _validate_continuity_item(
    plan: PetSitcomPlan,
    item: Any,
    previous_id: str,
    current_id: str,
    sources: Mapping[str, Mapping[str, Any]],
    durations: Mapping[str, float],
    video_durations: Mapping[str, float] | None = None,
) -> None:
    if not isinstance(item, Mapping):
        raise PetSitcomReviewError(
            "Continuity evidence must be an object."
        )
    _require_exact_fields(item, _CONTINUITY_FIELDS, "Continuity evidence")
    previous_duration = durations[previous_id]
    current_duration = durations[current_id]
    previous_video_duration = (
        previous_duration
        if video_durations is None
        else video_durations[previous_id]
    )
    current_video_duration = (
        current_duration
        if video_durations is None
        else video_durations[current_id]
    )
    duration_values = {
        "previous duration": previous_duration,
        "current duration": current_duration,
        "previous video duration": previous_video_duration,
        "current video duration": current_video_duration,
    }
    for label, value in duration_values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise PetSitcomReviewError(
                f"Continuity {label} must be finite and positive."
            )
    item_duration_fields = (
        "previous_duration_seconds",
        "previous_video_duration_seconds",
        "previous_edit_end_seconds",
        "current_duration_seconds",
        "current_video_duration_seconds",
    )
    if any(
        isinstance(item.get(field), bool)
        or not isinstance(item.get(field), (int, float))
        or not math.isfinite(float(item[field]))
        or float(item[field]) <= 0.0
        for field in item_duration_fields
    ):
        raise PetSitcomReviewError(
            "Continuity persisted durations must be finite and positive."
        )
    shots = {shot.shot_id: shot for shot in plan.shots}
    previous_shot = shots.get(previous_id)
    current_shot = shots.get(current_id)
    if (
        previous_shot is None
        or current_shot is None
        or previous_id not in current_shot.continuity_source_ids
    ):
        raise PetSitcomReviewError(
            "Continuity evidence does not match a declared edge."
        )
    edit_end = float(previous_shot.duration_seconds)
    _validate_source_continuity_timing(
        sources[previous_id],
        float(previous_duration),
        edit_end,
    )
    _validate_source_continuity_timing(
        sources[current_id],
        float(current_duration),
        float(current_shot.duration_seconds),
    )
    previous_times = _continuity_previous_times(
        sources[previous_id],
        edit_end=edit_end,
        video_duration=float(previous_video_duration),
    )
    current_times = [0.04, 0.12, 0.30]
    if (
        any(
            timestamp < 0.0
            or timestamp >= float(previous_video_duration)
            for timestamp in previous_times
        )
        or any(
            timestamp < 0.0
            or timestamp >= float(current_video_duration)
            for timestamp in current_times
        )
    ):
        raise PetSitcomReviewError(
            "Continuity timestamps exceed a persisted video duration."
        )
    expected_output = (
        _evidence_root(plan)
        / "continuity"
        / f"{previous_id}_to_{current_id}.png"
    )
    frame_root = expected_output.with_suffix("")
    expected_frames = [
        *(
            frame_root / f"previous_{index:02d}.png"
            for index in range(1, 4)
        ),
        *(
            frame_root / f"current_{index:02d}.png"
            for index in range(1, 4)
        ),
    ]
    if (
        item.get("previous_shot_id") != previous_id
        or item.get("current_shot_id") != current_id
        or item.get("label") != _continuity_label(previous_id, current_id)
        or item.get("previous_source_path")
        != str(Path(sources[previous_id]["path"]).resolve())
        or item.get("previous_selected_mp4_sha256")
        != sources[previous_id]["sha256"]
        or item.get("previous_duration_seconds") != previous_duration
        or item.get("previous_video_duration_seconds")
        != previous_video_duration
        or item.get("previous_edit_end_seconds") != edit_end
        or item.get("current_source_path")
        != str(Path(sources[current_id]["path"]).resolve())
        or item.get("current_selected_mp4_sha256")
        != sources[current_id]["sha256"]
        or item.get("current_duration_seconds") != current_duration
        or item.get("current_video_duration_seconds")
        != current_video_duration
        or item.get("previous_timestamps_seconds") != previous_times
        or item.get("current_timestamps_seconds") != current_times
        or item.get("frame_paths")
        != [str(path.resolve()) for path in expected_frames]
        or item.get("frame_sha256")
        != [_sha_or_empty(path) for path in expected_frames]
        or not _iso(item.get("extracted_at"))
    ):
        raise PetSitcomReviewError("Continuity evidence is stale.")
    for frame in expected_frames:
        _safe_path(plan, frame, "Continuity frame")
        if not frame.is_file():
            raise PetSitcomReviewError(
                "Continuity frame is missing or stale."
            )
    _validate_image_binding(plan, item, expected_output)


def _validate_image_binding(
    plan: PetSitcomPlan,
    item: Mapping[str, Any],
    expected: Path,
) -> None:
    _safe_path(plan, expected, "Evidence image")
    if (
        item.get("evidence_path") != str(expected.resolve())
        or not expected.is_file()
        or item.get("evidence_sha256") != _sha(expected)
    ):
        raise PetSitcomReviewError("Evidence image is missing or stale.")


def _validate_manual_timestamps(
    value: Any,
    duration: float,
    label: str,
) -> list[float]:
    if not isinstance(value, list):
        raise PetSitcomReviewError(
            f"Shot review timestamp list for {label} is invalid."
        )
    result: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not 0.0 <= float(item) <= duration
        ):
            raise PetSitcomReviewError(
                f"Shot review timestamp for {label} is outside the source duration."
            )
        result.append(float(item))
    if result != sorted(result):
        raise PetSitcomReviewError(
            f"Shot review timestamps for {label} must be ordered."
        )
    return result


def _validate_anchor_and_mouth_reviews(
    plan: PetSitcomPlan,
) -> dict[str, Any]:
    from .pet_sitcom_audio_probe import require_approved_pet_audio_probe

    try:
        _generation._require_approved_anchors(plan)
        audio_probe = require_approved_pet_audio_probe(plan)
    except _generation.PetSitcomGenerationError as exc:
        raise PetSitcomReviewError(
            "Approved current anchor and audio-drive human reviews are required."
        ) from exc
    anchor_path = plan.output_dir / "anchor_review_template.json"
    _safe_path(plan, anchor_path, "Anchor review")
    anchor = _read_json(anchor_path)
    if (
        anchor.get("completed") is not True
        or anchor.get("approved") is not True
        or not isinstance(anchor.get("source_hashes"), Mapping)
    ):
        raise PetSitcomReviewError(
            "Approved current anchor review is invalid."
        )
    return {"anchor": anchor, "mouth": audio_probe, "audio_probe": audio_probe}


def _validate_composition_preflight(
    plan: PetSitcomPlan,
) -> tuple[Any, ...]:
    try:
        timings = tuple(_compose.load_verified_pet_timings(plan))
    except _compose.PetSitcomComposeError as exc:
        raise PetSitcomReviewError(
            "Task 4 composition preflight is missing, invalid, or stale."
        ) from exc
    expected = tuple(shot for shot in plan.shots if shot.dialogue)
    starts: dict[str, float] = {}
    current = 0.0
    for shot in plan.shots:
        starts[shot.shot_id] = current
        current += float(shot.duration_seconds)
    if (
        len(timings) != len(expected)
        or [timing.shot_id for timing in timings]
        != [shot.shot_id for shot in expected]
        or any(
            timing.speaker != shot.speaker
            or timing.text != shot.dialogue
            or not math.isclose(
                float(timing.start_seconds),
                float(shot.dialogue_offset_seconds),
                abs_tol=1e-6,
            )
            or not math.isclose(
                float(timing.absolute_start_seconds),
                starts[shot.shot_id]
                + float(shot.dialogue_offset_seconds),
                abs_tol=1e-6,
            )
            or not math.isclose(
                float(timing.absolute_end_seconds),
                starts[shot.shot_id] + float(timing.end_seconds),
                abs_tol=1e-6,
            )
            or float(timing.end_seconds) <= float(timing.start_seconds)
            or float(timing.absolute_end_seconds)
            > starts[shot.shot_id]
            + float(shot.duration_seconds)
            - DIALOGUE_TAIL_SECONDS
            + 1e-9
            for timing, shot in zip(timings, expected, strict=True)
        )
    ):
        raise PetSitcomReviewError(
            "Task 4 composition preflight does not match the approved dialogue."
        )
    return timings


def _selection_details(
    plan: PetSitcomPlan,
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    document = _generation._read_pet_selection_document(plan)
    records = _generation._selection_shots(document)
    if set(records) != {shot.shot_id for shot in plan.shots}:
        raise PetSitcomReviewError(
            "Current Task 5 selection details are incomplete."
        )
    result = {}
    for shot in plan.shots:
        selection = records[shot.shot_id]
        candidate_number = selection.get("candidate_number")
        path = Path(sources[shot.shot_id]["path"])
        state = _generation._read_json_object(
            _generation._pet_candidate_state_path(path)
        )
        if (
            selection.get("video_sha256") != sources[shot.shot_id]["sha256"]
            or state.get("video_sha256") != sources[shot.shot_id]["sha256"]
            or state.get("candidate_number") != candidate_number
        ):
            raise PetSitcomReviewError(
                f"Task 5 details for {shot.shot_id} are stale."
            )
        result[shot.shot_id] = {
            "candidate_number": candidate_number,
            "video_sha256": state["video_sha256"],
            "prompt_sha256": state.get("prompt_sha256", ""),
            "retry_reason": state.get("retry_reason", ""),
            "retry_suffix": state.get("retry_suffix", ""),
            "reference_paths": state.get("reference_paths", []),
            "reference_sha256": state.get("reference_sha256", []),
        }
    return result


def _validated_shot_review_history(
    plan: PetSitcomPlan,
    sources: Mapping[str, Mapping[str, Any]],
    selections: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    root = _evidence_root(plan) / "review_history" / "shot"
    _safe_path(plan, root, "Shot review history")
    if not root.exists():
        return []
    result: list[dict[str, Any]] = []
    known = {shot.shot_id for shot in plan.shots}
    for path in sorted(root.glob("*/*.json")):
        _safe_path(plan, path, "Shot review history")
        item = _require_json(plan, path, "Shot review history")
        _require_exact_fields(item, _HISTORY_FIELDS, "Shot review history")
        shot_id = item.get("shot_id")
        record = item.get("review_record")
        if (
            item.get("schema_version") != REVIEW_HISTORY_SCHEMA
            or item.get("review_type") != "shot"
            or shot_id not in known
            or not _iso(item.get("archived_at"))
            or type(item.get("old_candidate_number")) is not int
            or item.get("old_candidate_number") not in {1, 2, 3, 4, 5, 6}
            or type(item.get("current_candidate_number")) is not int
            or item.get("current_candidate_number") not in {1, 2, 3, 4, 5, 6}
            or not isinstance(item.get("document_metadata"), Mapping)
            or not isinstance(record, Mapping)
        ):
            raise PetSitcomReviewError("Shot review history is invalid.")
        old_path = Path(str(item.get("old_selected_mp4_path") or ""))
        current_path = Path(
            str(item.get("current_selected_mp4_path") or "")
        )
        old_hash = item.get("old_selected_mp4_sha256")
        current_hash = item.get("current_selected_mp4_sha256")
        _safe_path(plan, old_path, "Shot review history source")
        _safe_path(plan, current_path, "Shot review history source")
        if (
            not isinstance(old_hash, str)
            or not isinstance(current_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", old_hash)
            or not re.fullmatch(r"[0-9a-f]{64}", current_hash)
            or record.get("selected_mp4_path") != str(old_path.resolve())
            or record.get("selected_mp4_sha256") != old_hash
        ):
            raise PetSitcomReviewError(
                "Shot review history source binding is stale."
            )
        expected_stem = f"{old_hash}_to_{current_hash}"
        if path.parent.name != shot_id or path.stem != expected_stem:
            raise PetSitcomReviewError(
                "Shot review history filename binding is invalid."
            )
        if _archived_failed_review_is_valid(record):
            result.append(item)
    for shot_id, detail in selections.items():
        candidate_number = detail.get("candidate_number")
        if candidate_number == 1:
            continue
        source = sources[shot_id]
        if not _review_history_for_selection(
            plan,
            shot_id,
            candidate_number,
            source,
            result,
        ):
            raise PetSitcomReviewError(
                f"Candidate {candidate_number} review history is missing "
                f"for {shot_id}."
            )
    return result


def _review_history_for_selection(
    plan: PetSitcomPlan,
    shot_id: str,
    candidate_number: Any,
    source: Mapping[str, Any],
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_path = Path(str(source.get("path") or "")).resolve()
    source_hash = source.get("sha256")
    direct = [
        item
        for item in history
        if (
            item["shot_id"] == shot_id
            and item["current_selected_mp4_path"] == str(source_path)
            and item["current_selected_mp4_sha256"] == source_hash
        )
    ]
    if direct:
        return direct

    state_path = _generation._pet_candidate_state_path(source_path)
    _safe_path(plan, state_path, "Local recut provenance")
    state = _generation._read_json_object(state_path)
    if (
        state.get("schema_version") != _generation.PET_LOCAL_RECUT_SCHEMA
        or state.get("provider") != "local_ffmpeg_recut"
        or state.get("candidate_number") != candidate_number
        or state.get("video_sha256") != source_hash
        or not isinstance(state.get("source_candidates"), list)
    ):
        return []
    matches: list[dict[str, Any]] = []
    for ancestor in state["source_candidates"]:
        if (
            not isinstance(ancestor, Mapping)
            or type(ancestor.get("candidate_number")) is not int
            or ancestor.get("candidate_number") not in {1, 2, 3, 4, 5, 6}
            or not isinstance(ancestor.get("video_sha256"), str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                ancestor["video_sha256"],
            )
        ):
            continue
        ancestor_path = Path(str(ancestor.get("video_path") or ""))
        _safe_path(plan, ancestor_path, "Local recut source")
        declared_shot = ancestor.get("source_shot_id")
        if (
            ancestor_path.parent.name != shot_id
            or declared_shot not in {None, shot_id}
        ):
            continue
        matches.extend(
            item
            for item in history
            if (
                item["shot_id"] == shot_id
                and item["current_candidate_number"]
                == ancestor["candidate_number"]
                and item["current_selected_mp4_path"]
                == str(ancestor_path.resolve())
                and item["current_selected_mp4_sha256"]
                == ancestor["video_sha256"]
                and item not in matches
            )
        )
    return matches


def _archived_failed_review_is_valid(record: Mapping[str, Any]) -> bool:
    _require_exact_fields(
        record,
        _SHOT_REVIEW_FIELDS,
        "Archived shot review",
    )
    if (
        record.get("reviewed") is not True
        or record.get("passed") is not False
        or not _iso(record.get("reviewed_at"))
        or not isinstance(record.get("gates"), Mapping)
        or set(record["gates"]) != set(SHOT_REVIEW_GATES)
    ):
        return False
    failed_codes: set[str] = set()
    derived_pass = True
    for gate_name, gate in record["gates"].items():
        if not isinstance(gate, Mapping):
            raise PetSitcomReviewError("Archived shot review gate is invalid.")
        _require_exact_fields(
            gate,
            _GATE_FIELDS,
            "Archived shot review gate",
        )
        passed = gate.get("passed")
        notes = gate.get("notes")
        if type(passed) is not bool or not isinstance(notes, str) or not notes.strip():
            raise PetSitcomReviewError("Archived shot review gate is invalid.")
        timestamps = _validate_manual_timestamps(
            gate.get("timestamps_seconds"),
            _MAX_SOURCE_DURATION,
            f"history/{gate_name}",
        )
        codes = gate.get("issue_codes")
        if (
            not isinstance(codes, list)
            or len(codes) != len(set(codes))
            or any(
                not isinstance(code, str)
                or code not in _GATE_ISSUE_CODES[gate_name]
                for code in codes
            )
        ):
            raise PetSitcomReviewError(
                "Archived shot review issue code is invalid."
            )
        if passed and codes:
            raise PetSitcomReviewError(
                "Archived passing gate has an issue code."
            )
        if not passed:
            if not timestamps or not codes:
                raise PetSitcomReviewError(
                    "Archived failed gate lacks structured evidence."
                )
            failed_codes.update(codes)
        derived_pass &= passed
    retry_reason = record.get("retry_reason")
    if (
        derived_pass is not False
        or retry_reason not in _RETRY_REASONS
        or retry_reason not in failed_codes
    ):
        raise PetSitcomReviewError(
            "Archived shot retry reason conflicts with its gates."
        )
    return True


def _selected_source_chain(
    plan: PetSitcomPlan,
    target: Any,
) -> dict[str, dict[str, Any]]:
    document = _generation._read_pet_selection_document(plan)
    if (
        set(document) != set(_SELECTION_TOP_FIELDS)
        or document.get("schema_version")
        != _generation.PET_SELECTION_SCHEMA
        or not isinstance(document.get("history"), Mapping)
    ):
        raise PetSitcomReviewError(
            "Incremental evidence requires the Task 5 selection schema."
        )
    selections = _generation._selection_shots(document)
    required = tuple(plan.shots[: target.index])
    missing = [
        shot.shot_id for shot in required if shot.shot_id not in selections
    ]
    if missing:
        raise PetSitcomReviewError(
            "Incremental evidence predecessor chain is missing "
            + ", ".join(missing)
            + "."
        )
    result: dict[str, dict[str, Any]] = {}
    for shot in required:
        result[shot.shot_id] = _validate_selection_source(
            plan,
            shot,
            selections,
        )
    return result


def _validate_selection_source(
    plan: PetSitcomPlan,
    shot: Any,
    selections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry = selections.get(shot.shot_id)
    candidate_number = (
        entry.get("candidate_number")
        if isinstance(entry, Mapping)
        else None
    )
    if (
        not isinstance(entry, Mapping)
        or set(entry) != set(_SELECTION_ENTRY_FIELDS)
        or entry.get("status") != "selected"
        or type(candidate_number) is not int
        or candidate_number not in {1, 2, 3, 4, 5, 6}
        or not _iso(entry.get("selected_at"))
    ):
        raise PetSitcomReviewError(
            f"Task 5 selection is invalid for {shot.shot_id}."
        )
    candidate = _generation._pet_candidate_path(shot, candidate_number)
    state_path = _generation._pet_candidate_state_path(candidate)
    report_path = _generation._pet_gateway_report_path(candidate)
    frame = _generation._pet_continuity_frame_path(plan, shot.shot_id)
    sidecar = _generation._pet_continuity_state_path(frame)
    for path, label in (
        (candidate, "Selected source"),
        (state_path, "Candidate provenance"),
        (report_path, "Candidate gateway report"),
        (frame, "Selected continuity frame"),
        (sidecar, "Selected continuity sidecar"),
    ):
        _safe_path(plan, path, label)
    declared = Path(str(entry.get("video_path") or ""))
    _safe_path(plan, declared, "Selected source")
    if declared.resolve() != candidate.resolve():
        raise PetSitcomReviewError(
            f"Task 5 selected path is stale for {shot.shot_id}."
        )
    try:
        references = _generation._pet_shot_references(
            plan,
            shot,
            selections,
        )
        drive_audio, source_tts_sha256 = (
            _generation._pet_shot_audio_bindings(plan, shot)
        )
        speech_asset = (
            next(
                (
                    asset
                    for asset in _generation.load_pet_speech_assets(plan)
                    if asset.shot_id == shot.shot_id
                ),
                None,
            )
            if drive_audio is not None
            else None
        )
        state = _generation._read_json_object(state_path)
        report = _generation._read_json_object(report_path)
        _generation._validate_candidate_for_selection(
            plan,
            shot,
            candidate_number,
            candidate,
            state,
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
    except (
        _audio_first.PetSitcomAudioFirstError,
        _generation.PetSitcomGenerationError,
    ) as exc:
        raise PetSitcomReviewError(
            f"Task 5 provenance chain is invalid for {shot.shot_id}."
        ) from exc
    if drive_audio is not None and speech_asset is None:
        raise PetSitcomReviewError(
            f"Task 5 canonical audio timing is missing for {shot.shot_id}."
        )
    expected_entry = {
        "candidate_number": candidate_number,
        "status": "selected",
        "video_path": str(candidate.resolve()),
        "video_sha256": _sha_or_empty(candidate),
        "prompt_sha256": state.get("prompt_sha256"),
        "reference_paths": state.get("reference_paths"),
        "reference_sha256": state.get("reference_sha256"),
        "dependency_video_sha256": state.get(
            "dependency_video_sha256"
        ),
        "source_tts_sha256": state.get("source_tts_sha256"),
        "reference_audio_sha256": state.get(
            "reference_audio_sha256"
        ),
        "continuity_frame_path": str(frame.resolve()),
        "continuity_sidecar_path": str(sidecar.resolve()),
        "continuity_frame_sha256": _sha_or_empty(frame),
    }
    if (
        report.get("success") is not True
        or report.get("pet_sitcom_provenance") != state
        or any(entry.get(key) != value for key, value in expected_entry.items())
        or not _generation._pet_continuity_matches(
            candidate,
            frame,
            edit_duration_seconds=float(shot.duration_seconds),
        )
    ):
        raise PetSitcomReviewError(
            f"Task 5 selection provenance is stale for {shot.shot_id}."
        )
    continuity_state = _generation._read_json_object(sidecar)
    if entry.get("continuity_timestamp_seconds") != continuity_state.get(
        "timestamp_seconds"
    ):
        raise PetSitcomReviewError(
            f"Task 5 continuity endpoint is stale for {shot.shot_id}."
        )
    source = {
        "path": candidate,
        "sha256": str(entry["video_sha256"]),
        "candidate_number": candidate_number,
        "reference_audio_path": str(state.get("reference_audio_path") or ""),
        "reference_audio_sha256": str(
            state.get("reference_audio_sha256") or ""
        ),
        "source_tts_sha256": str(state.get("source_tts_sha256") or ""),
        "dependency_video_sha256": dict(
            state.get("dependency_video_sha256") or {}
        ),
        "audio_onset_seconds": (
            float(shot.dialogue_offset_seconds)
            if speech_asset is not None
            else None
        ),
        "audio_offset_seconds": (
            float(shot.dialogue_offset_seconds)
            + float(speech_asset.duration_seconds)
            if speech_asset is not None
            else None
        ),
        "continuity_source_video_duration_seconds": continuity_state.get(
            "source_video_duration_seconds"
        ),
        "edit_duration_seconds": continuity_state.get(
            "edit_duration_seconds"
        ),
        "continuity_timestamp_seconds": continuity_state.get(
            "timestamp_seconds"
        ),
    }
    _validate_current_source(plan, shot.shot_id, source)
    return source


def _validate_current_source(
    plan: PetSitcomPlan,
    shot_id: str,
    source: Mapping[str, Any],
) -> None:
    path = Path(str(source.get("path") or ""))
    digest = source.get("sha256")
    _safe_path(plan, path, "Selected source")
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or _sha_or_empty(path) != digest
    ):
        raise PetSitcomReviewError(
            f"Selected source for {shot_id} is stale."
        )


def _selected_sources(plan: PetSitcomPlan) -> dict[str, dict[str, Any]]:
    document = _generation._read_pet_selection_document(plan)
    selections = _generation._selection_shots(document)
    expected_ids = {shot.shot_id for shot in plan.shots}
    if (
        set(document) != set(_SELECTION_TOP_FIELDS)
        or document.get("schema_version")
        != _generation.PET_SELECTION_SCHEMA
        or not isinstance(document.get("history"), Mapping)
        or set(selections) != expected_ids
    ):
        raise PetSitcomReviewError(
            "Current Task 5 selections must contain exactly ten shots."
        )
    return {
        shot.shot_id: _validate_selection_source(plan, shot, selections)
        for shot in plan.shots
    }


def _stream_count(probe: Mapping[str, Any], kind: str) -> int:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return 0
    return sum(
        isinstance(item, Mapping) and item.get("codec_type") == kind
        for item in streams
    )


def _stream(
    probe: Mapping[str, Any],
    kind: str,
) -> Mapping[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return {}
    return next(
        (
            item
            for item in streams
            if isinstance(item, Mapping)
            and item.get("codec_type") == kind
        ),
        {},
    )


def _duration(probe: Mapping[str, Any]) -> float:
    try:
        return float((probe.get("format") or {}).get("duration"))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _video_duration(probe: Mapping[str, Any]) -> float:
    streams = probe.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if (
                isinstance(stream, Mapping)
                and stream.get("codec_type") == "video"
            ):
                value = stream.get("duration")
                if isinstance(value, bool):
                    break
                try:
                    duration = float(value)
                except (TypeError, ValueError, OverflowError):
                    break
                if math.isfinite(duration) and duration > 0:
                    return duration
                break
    raise _VideoStreamDurationError(
        "Video stream duration must be explicitly finite and positive."
    )


def _fps(value: Any) -> float:
    try:
        numerator, denominator = str(value).split("/", 1)
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _parse_layout(layout: str) -> tuple[int, int]:
    try:
        columns, rows = (int(part) for part in layout.split("x"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PetSitcomReviewError("Evidence layout is invalid.") from exc
    if columns <= 0 or rows <= 0:
        raise PetSitcomReviewError("Evidence layout is invalid.")
    return columns, rows


def _format_time(value: float) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
    ):
        raise PetSitcomReviewError("Evidence timestamp is invalid.")
    return f"{float(value):.3f}"


def _source_manifest_path(plan: PetSitcomPlan) -> Path:
    return _evidence_root(plan) / "source_manifest.json"


def _final_manifest_path(plan: PetSitcomPlan) -> Path:
    return _evidence_root(plan) / "final_manifest.json"


def _evidence_root(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "evidence"


def _validate_plan(plan: PetSitcomPlan) -> None:
    try:
        _validate_plan_contract(plan)
    except PetSitcomError as exc:
        raise PetSitcomReviewError("Pet sitcom plan is invalid.") from exc
    _safe_path(plan, plan.output_dir, "Pet sitcom output directory")


def _safe_path(plan: PetSitcomPlan, path: Path, label: str) -> None:
    target = path.expanduser().absolute()
    root = plan.output_dir.expanduser().absolute()
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PetSitcomReviewError(f"{label} may not use a symlink.")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PetSitcomReviewError(
            f"{label} must remain inside the project output directory."
        ) from exc


def _ensure_directory(plan: PetSitcomPlan, path: Path) -> None:
    _safe_path(plan, path, "Evidence directory")
    path.mkdir(parents=True, exist_ok=True)
    _safe_path(plan, path, "Evidence directory")


def _temporary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=path.suffix,
    )
    os.close(descriptor)
    return Path(raw)


def _publish_asset(
    plan: PetSitcomPlan,
    temporary: Path,
    output: Path,
) -> None:
    _safe_path(plan, output, "Evidence image")
    if not temporary.is_file() or temporary.stat().st_size <= 0:
        raise PetSitcomReviewError(
            "FFmpeg did not create an evidence image."
        )
    os.replace(temporary, output)
    _fsync_directory(output.parent)


def _write_json(
    plan: PetSitcomPlan,
    path: Path,
    payload: Mapping[str, Any],
) -> None:
    _safe_path(plan, path, "Review JSON")
    try:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PetSitcomReviewError(
            "Review JSON values must be finite and serializable."
        ) from exc
    _write_bytes(plan, path, encoded)


def _write_text(
    plan: PetSitcomPlan,
    path: Path,
    text: str,
) -> None:
    _safe_path(plan, path, "Review markdown")
    _write_bytes(plan, path, text.encode("utf-8"))


def _write_bytes(
    plan: PetSitcomPlan,
    path: Path,
    payload: bytes,
) -> None:
    _safe_path(plan, path, "Review output")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary(path)
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _require_json(
    plan: PetSitcomPlan,
    path: Path,
    label: str,
) -> dict[str, Any]:
    _safe_path(plan, path, label)
    document = _read_json(path)
    if not document:
        raise PetSitcomReviewError(f"{label} is missing or invalid.")
    return document


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: frozenset[str],
    label: str,
) -> None:
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise PetSitcomReviewError(
            f"{label} has missing or unsupported fields."
        )


def _sha(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise PetSitcomReviewError(
            "Unable to read a local review artifact."
        ) from exc


def _sha_or_empty(path: Path) -> str:
    try:
        return _sha(path)
    except PetSitcomReviewError:
        return ""


def _run(
    runner: Callable[..., Any],
    command: Sequence[str],
) -> Any:
    try:
        return runner(
            list(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300.0,
        )
    except (OSError, subprocess.SubprocessError, TypeError) as exc:
        raise PetSitcomReviewError(
            "Local FFmpeg/FFprobe evidence command failed."
        ) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mp4_has_faststart(path: Path) -> bool:
    try:
        size = path.stat().st_size
        offset = 0
        atoms = []
        with path.open("rb") as handle:
            while offset + 8 <= size:
                handle.seek(offset)
                header = handle.read(8)
                atom_size = int.from_bytes(header[:4], "big")
                atom = header[4:8]
                header_size = 8
                if atom_size == 1:
                    atom_size = int.from_bytes(handle.read(8), "big")
                    header_size = 16
                if atom_size < header_size or offset + atom_size > size:
                    return False
                atoms.append(atom)
                offset += atom_size
        return (
            b"moov" in atoms
            and b"mdat" in atoms
            and atoms.index(b"moov") < atoms.index(b"mdat")
        )
    except OSError:
        return False
