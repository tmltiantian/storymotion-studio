from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from factory.pet_replica import PetReplicaPlan, ReplicaShot, validate_pet_replica_plan
from factory.pet_replica_generation import ReplicaCandidate
from factory.pet_replica_reference import (
    PetReplicaReferenceError,
    load_reviewed_shot_annotations,
)


REVIEW_SCHEMA_VERSION = "motion-comic-factory.pet-replica-review.v2"
MANUAL_REVIEW_GATES = (
    "new_identity_match",
    "source_identity_absent",
    "character_count_correct",
    "anatomy_correct",
    "framing_matches_reference",
    "screen_position_matches_reference",
    "action_function_matches_reference",
    "mouth_timing_natural",
    "silent_characters_closed_mouth",
    "prop_state_physical",
    "scene_axis_consistent",
    "no_platform_branding",
    "no_generated_text",
)

_GENERATION_SCHEMA = "motion-comic-factory.pet-replica-generation.v1"
_SELECTION_SCHEMA = "motion-comic-factory.pet-replica-selection.v1"
_REFERENCE_SCHEMA = "motion-comic-factory.pet-replica-reference.v1"
_FRAME_RATE = 30
_CONTACT_FRAME_COUNT = 12
_MOUTH_FRAME_RATE = 8
_MAX_FREEZE_S = 0.35
_MAX_FINAL_SETTLE_S = 0.25
_BLACK_LUMA = 8.0
_CUT_RGB_DELTA = 85.0
_SOURCE_COPY_RGB_DELTA = 8.0
_DURATION_EPSILON_S = 0.000001
_NON_EVIDENCE_NOTES = frozenset({"pass", "passed", "ok", "okay", "approved", "yes", "good"})
_EVIDENCE_TERMS = frozenset(
    {
        "frame", "start", "middle", "end", "mouth", "cat", "woman", "phone",
        "prop", "screen", "sofa", "人物", "猫", "嘴", "画面", "起始", "中间",
        "结尾", "手机", "道具", "沙发",
    }
)


class PetReplicaReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicaReviewResult:
    shot_id: str
    candidate_number: int
    candidate_sha256: str
    passed: bool
    failures: tuple[str, ...]
    review_path: Path
    evidence: Mapping[str, str]


@dataclass(frozen=True)
class _SourceEvidence:
    manifest: Mapping[str, Any]
    manifest_path: Path
    records: tuple[Mapping[str, Any], ...]
    images: tuple[Image.Image, ...]


def review_replica_candidate(
    plan: PetReplicaPlan,
    shot: ReplicaShot,
    candidate: ReplicaCandidate,
    frame_reader: Callable[[Path, float], Image.Image] | None = None,
    probe_runner: Callable[[Path], Any] | None = None,
) -> ReplicaReviewResult:
    """Review one candidate and persist an auditable, SHA-bound evidence bundle."""
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    canonical_shot = _canonical_shot(plan, shot)
    _validate_candidate_location(root, canonical_shot, candidate)
    reader = frame_reader or _read_frame
    probe = probe_runner or _default_probe
    candidate_sha = _sha256(candidate.video_path)
    attempt_id = f"attempt-{uuid.uuid4().hex}"
    review_path = _review_path(root, canonical_shot.shot_id, candidate.candidate_number)
    _write_pending_review(review_path, canonical_shot, candidate, candidate_sha, attempt_id)
    failures: list[str] = []
    provenance = _read_optional_json(candidate.provenance_path)
    if provenance is None:
        failures.append("candidate provenance is invalid")
        provenance = {}
    _check_candidate_binding(plan, canonical_shot, candidate, candidate_sha, provenance, failures)
    annotation = _annotation_for_shot(plan, canonical_shot, failures)
    speaking = bool(annotation and annotation.speaker)
    source = _load_source_evidence(plan, canonical_shot, failures)
    media = _coerce_probe(_call_probe(probe, candidate.video_path), "candidate video", failures)
    duration = _number(media.get("duration_s"))
    if media.get("width") != plan.width or media.get("height") != plan.height:
        failures.append("resolution")
    if duration is None or duration + _DURATION_EPSILON_S < canonical_shot.duration_s:
        failures.append("shorter than editorial window")
    if (fps := _number(media.get("fps"))) is not None and fps <= 0:
        failures.append("invalid frame rate")

    timestamps = _editorial_timestamps(canonical_shot.duration_s)
    frames = _candidate_frames(candidate.video_path, timestamps, reader, failures)
    if frames:
        _check_frames(frames, source, timestamps, canonical_shot.duration_s, failures)
    else:
        failures.append("candidate frames unavailable")
    audio = _check_drive_audio(
        plan, canonical_shot, provenance, speaking, probe, failures
    )

    evidence: dict[str, str] = {}
    evidence_records: dict[str, Mapping[str, str]] = {}
    try:
        evidence, evidence_records = _write_review_evidence(
            root, canonical_shot, candidate, candidate_sha, attempt_id, reader,
            frames, timestamps, source, speaking, failures,
        )
    except (OSError, ValueError, PetReplicaReviewError) as exc:
        failures.append(f"evidence rendering failed: {exc}")
    result = ReplicaReviewResult(
        shot_id=canonical_shot.shot_id,
        candidate_number=candidate.candidate_number,
        candidate_sha256=candidate_sha,
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        review_path=review_path,
        evidence=evidence,
    )
    record = _review_record(
        root, result, attempt_id, provenance, source, audio, evidence_records
    )
    _write_json_atomic(review_path, record)
    if not result.passed:
        _archive_failed_attempt(root, record)
    return result


def render_replica_contact_sheet(
    plan: PetReplicaPlan,
    shot: ReplicaShot,
    candidate: ReplicaCandidate,
) -> Path:
    """Render the contracted 12-frame sheet without approving the candidate."""
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    canonical_shot = _canonical_shot(plan, shot)
    _validate_candidate_location(root, canonical_shot, candidate)
    sha = _sha256(candidate.video_path)
    path = _evidence_dir(root, canonical_shot, candidate, sha, f"attempt-{uuid.uuid4().hex}") / "contact_4x3.jpg"
    frames = _candidate_frames(
        candidate.video_path,
        _even_timestamps(canonical_shot.duration_s, _CONTACT_FRAME_COUNT),
        _read_frame,
        [],
    )
    if len(frames) != _CONTACT_FRAME_COUNT:
        raise PetReplicaReviewError("Unable to render all candidate contact-sheet frames.")
    _write_sheet(frames, path, columns=4, rows=3)
    return path


def approve_replica_candidate(
    plan: PetReplicaPlan | None = None,
    candidate: ReplicaCandidate | None = None,
    manual_review: Mapping[str, Any] | None = None,
) -> Path:
    """Select only a complete, current review bundle with all manual gates."""
    if candidate is None:
        raise PetReplicaReviewError("A replica candidate is required.")
    _validate_manual_review(manual_review or {})
    if plan is None:
        raise PetReplicaReviewError("Plan is required after manual gates are complete.")
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    shot = _shot_by_id(plan, candidate.shot_id)
    _validate_candidate_location(root, shot, candidate)
    record = _current_passing_review(plan, shot, candidate)
    selection_path = _selection_path(root, shot)
    bundle = record["bindings"]
    payload = {
        "schema_version": _SELECTION_SCHEMA,
        "shot_id": shot.shot_id,
        "candidate_number": candidate.candidate_number,
        "candidate_path": _relative(root, candidate.video_path),
        "candidate_sha256": _sha256(candidate.video_path),
        "manual_review_note": str((manual_review or {})["note"]).strip(),
        "manual_gates": {gate: True for gate in MANUAL_REVIEW_GATES},
        "quality_approved": True,
        "quality_review_path": _relative(root, _review_path(root, shot.shot_id, candidate.candidate_number)),
        "quality_review_sha256": _sha256(_review_path(root, shot.shot_id, candidate.candidate_number)),
        "quality_bindings_sha256": _json_sha256(bundle),
        "quality_provenance_path": bundle["provenance"]["path"],
        "quality_provenance_sha256": bundle["provenance"]["sha256"],
        "quality_source_evidence_sha256": bundle["source_evidence"]["manifest_sha256"],
        "quality_drive_audio": bundle["drive_audio"],
        "quality_evidence": bundle["evidence"],
    }
    _write_json_atomic(selection_path, payload)
    return selection_path


def validate_replica_selection(plan: PetReplicaPlan, pilot_only: bool = False) -> None:
    """Fail closed unless every selected candidate still has its complete review bundle."""
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    for shot in plan.shots:
        if pilot_only and shot.start_s >= plan.pilot_end_s:
            continue
        try:
            selection_path = _selection_path(root, shot)
            _require_regular(root, selection_path, "selection")
            selection = _read_required_json(selection_path, "selection")
            _validate_selection_identity(root, shot, selection)
            candidate = _candidate_from_selection(root, shot, selection)
            record = _current_passing_review(plan, shot, candidate)
            if selection.get("quality_review_sha256") != _sha256(record["review_path"]):
                raise PetReplicaReviewError("selected review hash changed")
            if selection.get("quality_bindings_sha256") != _json_sha256(record["bindings"]):
                raise PetReplicaReviewError("selected review bindings changed")
            _validate_selection_bindings(selection, record["bindings"])
        except PetReplicaReviewError as exc:
            raise PetReplicaReviewError(f"{shot.shot_id} selection is invalid: {exc}") from exc


def _check_candidate_binding(plan, shot, candidate, candidate_sha, provenance, failures):
    if candidate.output_sha256 != candidate_sha:
        failures.append("candidate bytes changed")
    if candidate.editorial_duration_s != shot.duration_s:
        failures.append("candidate editorial window is stale")
    expected = {
        "schema_version": _GENERATION_SCHEMA,
        "shot_id": shot.shot_id,
        "candidate_number": candidate.candidate_number,
        "output_sha256": candidate_sha,
        "source_sha256": _sha256(plan.source_video),
        "editorial_duration_s": shot.duration_s,
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        failures.append("candidate provenance is stale")
    window = provenance.get("source_window")
    if not isinstance(window, Mapping) or window.get("start_s") != shot.start_s or window.get("end_s") != shot.end_s:
        failures.append("candidate provenance source window is stale")
    if provenance.get("output_path") != _relative(plan.output_root, candidate.video_path):
        failures.append("candidate provenance path is stale")


def _check_frames(frames, source, timestamps, duration, failures):
    if any(_mean_luma(frame) <= _BLACK_LUMA for frame in frames):
        failures.append("black frame")
    if _has_internal_cut(frames):
        failures.append("unexpected internal cut")
    if _longest_exact_run_s(frames) > _MAX_FREEZE_S:
        failures.append("freeze")
    if _trailing_exact_run_s(frames) > _MAX_FINAL_SETTLE_S:
        failures.append("final settle")
    if source is not None:
        samples = _even_timestamps(duration, 3)
        candidate_samples = [_frame_at(frames, timestamps, timestamp) for timestamp in samples]
        if any(_rgb_delta(frame, reference) <= _SOURCE_COPY_RGB_DELTA for frame, reference in zip(candidate_samples, source.images)):
            failures.append("sampled source frame copy")


def _check_drive_audio(plan, shot, provenance, speaking, probe, failures):
    if not speaking:
        return None
    path = plan.output_root / "audio" / "drive" / f"{shot.shot_id}.wav"
    try:
        _require_regular(plan.output_root, path, "drive audio")
    except PetReplicaReviewError:
        failures.append("drive audio provenance is stale")
        return None
    sha = _sha256(path)
    audio_sha = provenance.get("drive_audio_sha256")
    if not isinstance(audio_sha, str) or not audio_sha:
        failures.append("drive audio provenance is missing")
    elif audio_sha != sha:
        failures.append("drive audio provenance is stale")
    activity = _coerce_probe(_call_probe(probe, path), "drive audio", failures)
    start, end = _number(activity.get("speech_start_s")), _number(activity.get("speech_end_s"))
    if (
        start is None
        or end is None
        or start < 0
        or end <= start
        or end > shot.duration_s + _DURATION_EPSILON_S
    ):
        failures.append("drive-audio speaking activity outside declared window")
    return {"path": _relative(plan.output_root, path), "sha256": sha}


def _write_review_evidence(root, shot, candidate, candidate_sha, attempt_id, reader, frames, timestamps, source, speaking, failures):
    evidence_dir = _evidence_dir(root, shot, candidate, candidate_sha, attempt_id)
    contact_frames = _candidate_frames(candidate.video_path, _even_timestamps(shot.duration_s, _CONTACT_FRAME_COUNT), reader, failures)
    if len(contact_frames) != _CONTACT_FRAME_COUNT:
        raise PetReplicaReviewError("contact sheet frames are unavailable")
    contact = evidence_dir / "contact_4x3.jpg"
    _write_sheet(contact_frames, contact, columns=4, rows=3)
    if source is None or not frames:
        raise PetReplicaReviewError("source comparison frames are unavailable")
    comparison = evidence_dir / "source_candidate_start_middle_end.jpg"
    candidates = [_frame_at(frames, timestamps, value) for value in _even_timestamps(shot.duration_s, 3)]
    _write_sheet([frame for pair in zip(source.images, candidates) for frame in pair], comparison, columns=2, rows=3)
    artifacts = {"contact_sheet": contact, "comparison_sheet": comparison}
    if speaking:
        mouth = evidence_dir / "mouth_8fps.jpg"
        mouth_frames = _candidate_frames(candidate.video_path, _mouth_timestamps(shot.duration_s), reader, failures)
        if not mouth_frames:
            raise PetReplicaReviewError("mouth sheet frames are unavailable")
        _write_sheet(mouth_frames, mouth, columns=8, rows=max(1, (len(mouth_frames) + 7) // 8))
        artifacts["mouth_sheet"] = mouth
    return (
        {name: _relative(root, path) for name, path in artifacts.items()},
        {name: _file_binding(root, path) for name, path in artifacts.items()},
    )


def _review_record(root, result, attempt_id, provenance, source, audio, evidence):
    candidate = {
        "path": _relative(root, result.review_path.parent.parent / f"candidate_{result.candidate_number:02d}.mp4"),
        "sha256": result.candidate_sha256,
    }
    bindings: dict[str, Any] = {
        "candidate": candidate,
        "provenance": _file_binding(root, result.review_path.parent.parent / f"candidate_{result.candidate_number:02d}.provenance.json"),
        "source_evidence": _source_binding(root, source),
        "drive_audio": audio,
        "evidence": evidence,
    }
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "shot_id": result.shot_id,
        "candidate_number": result.candidate_number,
        "candidate_sha256": result.candidate_sha256,
        "passed": result.passed,
        "failures": list(result.failures),
        "review_path": _relative(root, result.review_path),
        "evidence": dict(result.evidence),
        "bindings": bindings,
    }


def _source_binding(root, source):
    if source is None:
        return None
    return {
        "manifest_path": _relative(root, source.manifest_path),
        "manifest_sha256": _sha256(source.manifest_path),
        "records": [dict(record) for record in source.records],
    }


def _current_passing_review(plan, shot, candidate):
    root = _output_root(plan)
    path = _review_path(root, shot.shot_id, candidate.candidate_number)
    try:
        _require_regular(root, path, "review")
        record = _read_required_json(path, "review")
        if record.get("schema_version") != REVIEW_SCHEMA_VERSION or record.get("passed") is not True:
            raise PetReplicaReviewError("automatic review is not passing")
        if record.get("shot_id") != shot.shot_id or record.get("candidate_number") != candidate.candidate_number:
            raise PetReplicaReviewError("automatic review identity is stale")
        if record.get("candidate_sha256") != _sha256(candidate.video_path):
            raise PetReplicaReviewError("automatic review candidate bytes changed")
        if record.get("review_path") != _relative(root, path):
            raise PetReplicaReviewError("automatic review path is stale")
        _validate_review_bundle(plan, shot, candidate, record)
        return dict(record) | {"review_path": path}
    except PetReplicaReviewError as exc:
        raise PetReplicaReviewError("Candidate requires a current passing automatic review.") from exc


def _validate_review_bundle(plan, shot, candidate, record):
    root = _output_root(plan)
    bindings = record.get("bindings")
    if not isinstance(bindings, Mapping):
        raise PetReplicaReviewError("review bindings are missing")
    expected_candidate = _file_binding(root, candidate.video_path)
    if bindings.get("candidate") != expected_candidate:
        raise PetReplicaReviewError("review candidate binding changed")
    provenance_path = candidate.video_path.with_suffix(".provenance.json")
    if bindings.get("provenance") != _file_binding(root, provenance_path):
        raise PetReplicaReviewError("review provenance binding changed")
    provenance = _read_required_json(provenance_path, "candidate provenance")
    failures: list[str] = []
    _check_candidate_binding(plan, shot, candidate, _sha256(candidate.video_path), provenance, failures)
    annotation = _annotation_for_shot(plan, shot, failures)
    source = _load_source_evidence(plan, shot, failures)
    if failures or source is None or bindings.get("source_evidence") != _source_binding(root, source):
        raise PetReplicaReviewError("review source evidence binding changed")
    speaking = bool(annotation and annotation.speaker)
    if speaking:
        path = root / "audio" / "drive" / f"{shot.shot_id}.wav"
        if bindings.get("drive_audio") != _file_binding(root, path):
            raise PetReplicaReviewError("review drive audio binding changed")
        audio_sha = provenance.get("drive_audio_sha256")
        if audio_sha != _sha256(path):
            raise PetReplicaReviewError("review drive audio provenance changed")
    elif bindings.get("drive_audio") is not None:
        raise PetReplicaReviewError("review drive audio contract changed")
    evidence = bindings.get("evidence")
    expected_names = {"contact_sheet", "comparison_sheet"} | ({"mouth_sheet"} if speaking else set())
    if not isinstance(evidence, Mapping) or set(evidence) != expected_names or set(record.get("evidence") or {}) != expected_names:
        raise PetReplicaReviewError("review evidence inventory changed")
    for name in expected_names:
        binding = evidence[name]
        if not isinstance(binding, Mapping) or record["evidence"].get(name) != binding.get("path"):
            raise PetReplicaReviewError("review evidence binding changed")
        path = _path_from_relative(root, binding.get("path"), "review evidence")
        _require_regular(root, path, "review evidence")
        if binding != _file_binding(root, path) or not _is_candidate_evidence_path(root, shot, candidate, record, path):
            raise PetReplicaReviewError("review evidence binding changed")


def _validate_selection_identity(root, shot, selection):
    if selection.get("schema_version") != _SELECTION_SCHEMA or selection.get("quality_approved") is not True:
        raise PetReplicaReviewError("selection is not quality approved")
    if selection.get("shot_id") != shot.shot_id:
        raise PetReplicaReviewError("selection shot identity changed")
    number = selection.get("candidate_number")
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 3:
        raise PetReplicaReviewError("selection candidate number is invalid")
    expected_candidate = _candidate_path(root, shot, number)
    expected_review = _review_path(root, shot.shot_id, number)
    if selection.get("candidate_path") != _relative(root, expected_candidate) or selection.get("quality_review_path") != _relative(root, expected_review):
        raise PetReplicaReviewError("selection paths are not canonical")
    gates = selection.get("manual_gates")
    if not isinstance(gates, Mapping) or set(gates) != set(MANUAL_REVIEW_GATES) or not all(gates.get(gate) is True for gate in MANUAL_REVIEW_GATES):
        raise PetReplicaReviewError("selection is missing manual gates")


def _candidate_from_selection(root, shot, selection):
    number = selection["candidate_number"]
    path = _candidate_path(root, shot, number)
    _require_regular(root, path, "selected candidate")
    if selection.get("candidate_sha256") != _sha256(path):
        raise PetReplicaReviewError("selected candidate bytes changed")
    return ReplicaCandidate(shot.shot_id, number, path, path.with_suffix(".provenance.json"), path.with_suffix(".gateway.json"), shot.duration_s, 0, _sha256(path))


def _validate_selection_bindings(selection, bindings):
    expected = {
        "quality_provenance_path": bindings["provenance"]["path"],
        "quality_provenance_sha256": bindings["provenance"]["sha256"],
        "quality_source_evidence_sha256": bindings["source_evidence"]["manifest_sha256"],
        "quality_drive_audio": bindings["drive_audio"],
        "quality_evidence": bindings["evidence"],
    }
    if any(selection.get(key) != value for key, value in expected.items()):
        raise PetReplicaReviewError("selection review bindings changed")


def _archive_failed_attempt(root, record):
    attempt = str(record.get("attempt_id", ""))
    sha = str(record.get("candidate_sha256", ""))
    number = record.get("candidate_number")
    shot_id = record.get("shot_id")
    if not attempt.startswith("attempt-") or not sha or not isinstance(number, int) or not isinstance(shot_id, str):
        raise PetReplicaReviewError("Failed review archive requires a complete identity.")
    archive = root / "rejected" / "reviews" / shot_id / f"candidate_{number:02d}" / sha / attempt
    if archive.exists():
        raise PetReplicaReviewError("Failed review archive already exists.")
    archive.mkdir(parents=True)
    copied: dict[str, Mapping[str, str]] = {}
    for name, binding in (record.get("bindings", {}).get("evidence", {}) or {}).items():
        if not isinstance(binding, Mapping):
            continue
        source = _path_from_relative(root, binding.get("path"), "failed review evidence")
        if not source.is_file() or source.is_symlink():
            continue
        destination = archive / "evidence" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied[str(name)] = {"path": str(destination.relative_to(root)), "sha256": _sha256(destination)}
    _write_json_atomic(archive / "review.json", record)
    _write_json_atomic(archive / "evidence_manifest.json", {"artifacts": copied})


def _write_pending_review(path, shot, candidate, sha, attempt_id):
    _write_json_atomic(path, {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "shot_id": shot.shot_id,
        "candidate_number": candidate.candidate_number,
        "candidate_sha256": sha,
        "passed": False,
        "failures": ["review evidence pending"],
        "review_path": "",
        "evidence": {},
        "bindings": {},
    })


def _annotation_for_shot(plan, shot, failures):
    try:
        annotations = load_reviewed_shot_annotations(plan)
    except PetReplicaReferenceError:
        failures.append("reviewed shot contract is unavailable")
        return None
    annotation = next((item for item in annotations if item.shot_id == shot.shot_id), None)
    if annotation is None:
        failures.append("reviewed shot contract is unavailable")
    return annotation


def _load_source_evidence(plan, shot, failures):
    root = _output_root(plan)
    manifest_path = root / "reference" / "evidence_manifest.json"
    try:
        _require_regular(root, manifest_path, "source evidence manifest")
        manifest = _read_required_json(manifest_path, "source evidence manifest")
        if manifest.get("schema_version") != _REFERENCE_SCHEMA or manifest.get("source_sha256") != _sha256(plan.source_video):
            raise PetReplicaReviewError("source evidence manifest is stale")
        entries = manifest.get("frames")
        if not isinstance(entries, list):
            raise PetReplicaReviewError("source evidence manifest is invalid")
        records, images = [], []
        for label in ("start", "middle", "end"):
            entry = next((value for value in entries if isinstance(value, Mapping) and value.get("shot_id") == shot.shot_id and value.get("label") == label), None)
            if not isinstance(entry, Mapping) or entry.get("source_sha256") != manifest["source_sha256"]:
                raise PetReplicaReviewError("source evidence is incomplete")
            expected = root / "reference" / "shots" / shot.shot_id / f"{label}.jpg"
            if entry.get("image_path") != _relative(root, expected):
                raise PetReplicaReviewError("source evidence path is not canonical")
            path = _path_from_relative(root, entry.get("image_path"), "source evidence")
            if path != _canonicalize(expected, "source evidence"):
                raise PetReplicaReviewError("source evidence path is not canonical")
            _require_regular(root, path, "source evidence")
            if entry.get("image_sha256") != _sha256(path):
                raise PetReplicaReviewError("source evidence hash changed")
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
            records.append({"label": label, "path": _relative(root, path), "sha256": _sha256(path)})
        return _SourceEvidence(manifest, manifest_path, tuple(records), tuple(images))
    except (OSError, PetReplicaReviewError, ValueError):
        failures.append("source evidence is stale")
        return None


def _canonical_shot(plan, shot):
    canonical = _shot_by_id(plan, shot.shot_id)
    if shot != canonical:
        raise PetReplicaReviewError("Review shot must equal the canonical plan shot.")
    return canonical


def _shot_by_id(plan, shot_id):
    for shot in plan.shots:
        if shot.shot_id == shot_id:
            return shot
    raise PetReplicaReviewError("Candidate shot is not in the replica plan.")


def _validate_candidate_location(root, shot, candidate):
    expected = _candidate_path(root, shot, candidate.candidate_number)
    if candidate.shot_id != shot.shot_id or _absolute(candidate.video_path) != expected:
        raise PetReplicaReviewError("Candidate path does not match the canonical shot.")
    if _absolute(candidate.provenance_path) != expected.with_suffix(".provenance.json"):
        raise PetReplicaReviewError("Candidate provenance path does not match the canonical shot.")
    _require_regular(root, expected, "candidate video")
    _require_regular(root, expected.with_suffix(".provenance.json"), "candidate provenance")


def _candidate_path(root, shot, number):
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 3:
        raise PetReplicaReviewError("Candidate number must be between 1 and 3.")
    return root / "shots" / shot.shot_id / f"candidate_{number:02d}.mp4"


def _selection_path(root, shot):
    return root / "shots" / shot.shot_id / "selection.json"


def _review_path(root, shot_id, number):
    return root / "shots" / shot_id / "reviews" / f"candidate_{number:02d}.review.json"


def _evidence_dir(root, shot, candidate, sha, attempt):
    return root / "shots" / shot.shot_id / "reviews" / f"candidate_{candidate.candidate_number:02d}" / sha / attempt


def _is_candidate_evidence_path(root, shot, candidate, record, path):
    prefix = _evidence_dir(root, shot, candidate, record["candidate_sha256"], record["attempt_id"])
    try:
        path.relative_to(prefix)
    except ValueError:
        return False
    return True


def _output_root(plan):
    root = _canonicalize(plan.output_root, "Replica output root")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise PetReplicaReviewError("Replica output root must be a regular directory.")
    return root


def _require_regular(root, path, label):
    path = _absolute(path)
    _require_inside(root, path, label)
    if not path.is_file() or path.is_symlink():
        raise PetReplicaReviewError(f"{label} must be a regular non-symlink file.")


def _require_inside(root, path, label):
    root = _canonicalize(root, "Replica output root")
    path = _canonicalize(path, label)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PetReplicaReviewError(f"{label} must stay inside the output root.") from exc


def _path_from_relative(root, value, label):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise PetReplicaReviewError(f"{label} path is not a relative path.")
    path = _canonicalize(_absolute(root) / value, label)
    _require_inside(root, path, label)
    return path


def _relative(root, path):
    path = _absolute(path)
    _require_inside(root, path, "review artifact")
    return str(path.relative_to(_absolute(root)))


def _absolute(path):
    return Path(path).expanduser().absolute()


def _canonicalize(path, label):
    raw = _absolute(path)
    if ".." in raw.parts:
        raise PetReplicaReviewError(f"{label} path may not contain '..'.")
    cursor = Path(raw.anchor)
    for component in raw.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise PetReplicaReviewError(f"{label} may not use symlinks.")
    try:
        return raw.resolve(strict=False)
    except OSError as exc:
        raise PetReplicaReviewError(f"{label} path cannot be canonicalized.") from exc


def _file_binding(root, path):
    _require_regular(root, path, "review artifact")
    return {"path": _relative(root, path), "sha256": _sha256(path)}


def _read_required_json(path, label):
    payload = _read_optional_json(path)
    if payload is None:
        raise PetReplicaReviewError(f"{label} is missing or invalid.")
    return payload


def _read_optional_json(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _candidate_frames(path, timestamps, reader, failures):
    frames = []
    for timestamp in timestamps:
        try:
            frame = reader(path, timestamp)
            if not isinstance(frame, Image.Image):
                raise TypeError("frame reader must return PIL images")
            frames.append(frame.convert("RGB"))
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            failures.append(f"frame read failed: {exc}")
            return []
    return frames


def _default_probe(path: Path) -> Mapping[str, Any]:
    if path.suffix.lower() == ".wav":
        return _probe_audio_activity(path)
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(path)]
    completed = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    return {"duration_s": float(payload["format"]["duration"]), "width": video.get("width"), "height": video.get("height"), "fps": _frame_rate(video.get("avg_frame_rate"))}


def _probe_audio_activity(path: Path) -> Mapping[str, Any]:
    command = ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-50dB:d=0.05", "-f", "null", "-"]
    completed = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    try:
        with wave.open(str(path), "rb") as opened:
            frame_rate = opened.getframerate()
            duration = opened.getnframes() / frame_rate if frame_rate else 0.0
    except (OSError, wave.Error):
        return {}
    starts = [
        min(duration, float(value))
        for value in re.findall(r"silence_start: ([0-9.]+)", completed.stderr)
    ]
    ends = [
        min(duration, float(value))
        for value in re.findall(r"silence_end: ([0-9.]+)", completed.stderr)
    ]
    cursor = 0.0
    for index, start in enumerate(starts):
        if start > cursor:
            return {"speech_start_s": cursor, "speech_end_s": start}
        cursor = max(cursor, ends[index] if index < len(ends) else duration)
    return {"speech_start_s": cursor, "speech_end_s": duration} if duration > cursor else {}


def _read_frame(path: Path, timestamp_s: float) -> Image.Image:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        output = Path(handle.name)
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp_s:.6f}", "-i", str(path), "-frames:v", "1", str(output)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        with Image.open(output) as frame:
            return frame.convert("RGB")
    finally:
        output.unlink(missing_ok=True)


def _editorial_timestamps(duration):
    return tuple(index / _FRAME_RATE for index in range(max(1, int(round(duration * _FRAME_RATE)))))


def _even_timestamps(duration, count):
    end = max(0.0, duration - 1 / _FRAME_RATE)
    return (0.0,) if count == 1 else tuple(end * index / (count - 1) for index in range(count))


def _mouth_timestamps(duration):
    return tuple(index / _MOUTH_FRAME_RATE for index in range(max(1, int(duration * _MOUTH_FRAME_RATE))))


def _has_internal_cut(frames):
    return any(_rgb_delta(left, right) >= _CUT_RGB_DELTA for left, right in zip(frames, frames[1:]))


def _longest_exact_run_s(frames):
    longest = current = 1
    previous = _pixel_digest(frames[0])
    for frame in frames[1:]:
        digest = _pixel_digest(frame)
        current = current + 1 if digest == previous else 1
        longest, previous = max(longest, current), digest
    return longest / _FRAME_RATE


def _trailing_exact_run_s(frames):
    digest, count = _pixel_digest(frames[-1]), 0
    for frame in reversed(frames):
        if _pixel_digest(frame) != digest:
            break
        count += 1
    return count / _FRAME_RATE


def _rgb_delta(left, right):
    first, second = left.resize((24, 24), Image.Resampling.BILINEAR), right.resize((24, 24), Image.Resampling.BILINEAR)
    return sum(sum(abs(a - b) for a, b in zip(x, y)) / 3 for x, y in zip(first.get_flattened_data(), second.get_flattened_data())) / (24 * 24)


def _mean_luma(frame):
    pixels = frame.resize((24, 24), Image.Resampling.BILINEAR).get_flattened_data()
    return sum(0.2126 * red + 0.7152 * green + 0.0722 * blue for red, green, blue in pixels) / (24 * 24)


def _pixel_digest(frame):
    return hashlib.sha256(frame.tobytes()).digest()


def _frame_at(frames, timestamps, target):
    return min(zip(timestamps, frames), key=lambda item: abs(item[0] - target))[1]


def _write_sheet(frames, path, *, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (columns * 144, rows * 256), "black")
    for index, frame in enumerate(frames[: columns * rows]):
        image = frame.copy()
        image.thumbnail((144, 256), Image.Resampling.LANCZOS)
        x, y = (index % columns) * 144 + (144 - image.width) // 2, (index // columns) * 256 + (256 - image.height) // 2
        canvas.paste(image, (x, y))
    _write_image_atomic(path, canvas)


def _validate_manual_review(review):
    if {gate for gate in MANUAL_REVIEW_GATES if review.get(gate) is True} != set(MANUAL_REVIEW_GATES):
        raise PetReplicaReviewError("All manual gates must be explicitly true.")
    note = str(review.get("note", "")).strip()
    lowered = note.lower()
    cjk_count = sum("\u4e00" <= character <= "\u9fff" for character in note)
    words = re.findall(r"[A-Za-z0-9]+", lowered)
    has_evidence = any(term in note or term in lowered for term in _EVIDENCE_TERMS)
    if not note or lowered in _NON_EVIDENCE_NOTES or not has_evidence or (cjk_count < 8 and len(words) < 5):
        raise PetReplicaReviewError("Manual review note must name visible evidence, not only pass/ok.")


def _call_probe(probe, path):
    try:
        return probe(path)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        return {"probe_error": str(exc)}


def _coerce_probe(value, label, failures):
    if isinstance(value, Mapping):
        if value.get("probe_error"):
            failures.append(f"{label} probe failed")
        return value
    result = {name: getattr(value, name) for name in ("duration_s", "width", "height", "fps", "speech_start_s", "speech_end_s") if hasattr(value, name)}
    if not result:
        failures.append(f"{label} probe failed")
    return result


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _frame_rate(value):
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator) if float(denominator) else None


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_image_atomic(path, image):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        image.save(temporary, format="JPEG", quality=90)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
