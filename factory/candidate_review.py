"""Immutable review-state records for render candidates.

Only records that have reached ``approved`` may become a visual selection.
The manifest deliberately stores the candidate fingerprint and the audio/anchor
binding carried by the rendered job report, so a later editorial step cannot
silently substitute a different render.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .character_assets import is_supported_image_file
from .prompt_safety import PREVIOUS_SHOT_CONTINUITY
from .visual_timeline import VisualTimeline


CANDIDATE_REVIEW_SCHEMA = "motion-comic-factory.candidate-review.v1"
_SHA256_LENGTH = 64
_REQUIRED_REVIEW_EVIDENCE = frozenset(
    {"first_frame", "middle_frame", "last_frame", "review_note"}
)
_VISIBLE_SPEECH_EVIDENCE = frozenset(
    {"audio_sha256", "speaker_visible", "lipsync_score"}
)


class CandidateReviewError(ValueError):
    pass


class CandidateState(StrEnum):
    PLANNED = "planned"
    AUDIO_READY = "audio_ready"
    SUBMITTED = "submitted"
    RENDERED = "rendered"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


_ALLOWED: dict[CandidateState, set[CandidateState]] = {
    CandidateState.PLANNED: {CandidateState.AUDIO_READY, CandidateState.BLOCKED},
    CandidateState.AUDIO_READY: {CandidateState.SUBMITTED, CandidateState.BLOCKED},
    CandidateState.SUBMITTED: {CandidateState.RENDERED, CandidateState.BLOCKED},
    CandidateState.RENDERED: {CandidateState.REVIEW_REQUIRED, CandidateState.BLOCKED},
    CandidateState.REVIEW_REQUIRED: {
        CandidateState.APPROVED,
        CandidateState.REJECTED,
        CandidateState.BLOCKED,
    },
    CandidateState.REJECTED: {CandidateState.PLANNED, CandidateState.BLOCKED},
}


@dataclass(frozen=True)
class CandidateRecord:
    micro_shot_id: str
    candidate_path: str
    candidate_sha256: str
    state: CandidateState
    audio_sha256: str
    entry_anchor_id: str
    visual_qc_report_path: str
    visual_qc_report_sha256: str = ""
    first_frame_sha256: str = ""
    middle_frame_sha256: str = ""
    last_frame_sha256: str = ""
    sample_frame_sha256: Mapping[str, str] = field(default_factory=dict)
    rendered_job_report_path: str = ""
    rendered_job_report_sha256: str = ""
    reason: str = ""
    evidence: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateReviewManifest:
    project_id: str
    candidates: tuple[CandidateRecord, ...]
    schema_version: str = CANDIDATE_REVIEW_SCHEMA


def transition_candidate(
    record: CandidateRecord,
    target: CandidateState,
    *,
    reason: str = "",
    evidence: Mapping[str, str] | None = None,
) -> CandidateRecord:
    """Return a validated immutable state transition for one candidate."""
    _validate_record(record, require_existing=False)
    if not isinstance(target, CandidateState):
        raise CandidateReviewError("Candidate target state is invalid.")
    allowed = _ALLOWED.get(record.state, set())
    if target not in allowed:
        raise CandidateReviewError(
            f"{record.state.value} cannot transition to {target.value}"
        )
    updated_evidence = dict(record.evidence if evidence is None else evidence)
    updated_reason = reason if reason else record.reason
    updated = CandidateRecord(
        micro_shot_id=record.micro_shot_id,
        candidate_path=record.candidate_path,
        candidate_sha256=record.candidate_sha256,
        state=target,
        audio_sha256=record.audio_sha256,
        entry_anchor_id=record.entry_anchor_id,
        visual_qc_report_path=record.visual_qc_report_path,
        visual_qc_report_sha256=record.visual_qc_report_sha256,
        first_frame_sha256=record.first_frame_sha256,
        middle_frame_sha256=record.middle_frame_sha256,
        last_frame_sha256=record.last_frame_sha256,
        sample_frame_sha256=dict(record.sample_frame_sha256),
        rendered_job_report_path=record.rendered_job_report_path,
        rendered_job_report_sha256=record.rendered_job_report_sha256,
        reason=updated_reason,
        evidence=updated_evidence,
    )
    _validate_record(updated, require_existing=False)
    if target in {CandidateState.REVIEW_REQUIRED, CandidateState.APPROVED}:
        _validate_review_evidence(updated)
    return updated


def write_candidate_review_manifest(
    manifest: CandidateReviewManifest, output_path: str | Path
) -> Path:
    """Validate and atomically persist a candidate review manifest."""
    _validate_manifest(manifest, require_existing=False)
    output = Path(output_path).expanduser().resolve()
    if output.exists() and not output.is_file():
        raise CandidateReviewError("Candidate review manifest output must be a file path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(_manifest_payload(manifest), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def candidate_review_manifest_from_dict(data: Mapping[str, Any]) -> CandidateReviewManifest:
    if not isinstance(data, Mapping) or set(data) != {
        "schema_version",
        "project_id",
        "candidates",
    }:
        raise CandidateReviewError("Candidate review manifest has an invalid exact-key schema.")
    candidates_value = data["candidates"]
    if not isinstance(candidates_value, list):
        raise CandidateReviewError("Candidate review candidates must be a list.")
    records: list[CandidateRecord] = []
    for position, item in enumerate(candidates_value, start=1):
        if not isinstance(item, Mapping):
            raise CandidateReviewError(f"Candidate review record {position} must be an object.")
        if set(item) != {
            "micro_shot_id", "candidate_path", "candidate_sha256", "state",
            "audio_sha256", "entry_anchor_id", "visual_qc_report_path",
            "visual_qc_report_sha256", "first_frame_sha256", "middle_frame_sha256",
            "last_frame_sha256", "sample_frame_sha256", "rendered_job_report_path",
            "rendered_job_report_sha256", "reason", "evidence",
        }:
            raise CandidateReviewError(
                f"Candidate review record {position} has an invalid exact-key schema."
            )
        try:
            state = CandidateState(item["state"])
        except (TypeError, ValueError) as exc:
            raise CandidateReviewError(f"Candidate review record {position} state is invalid.") from exc
        evidence = item["evidence"]
        if not isinstance(evidence, Mapping):
            raise CandidateReviewError(f"Candidate review record {position} evidence must be an object.")
        records.append(
            CandidateRecord(
                micro_shot_id=item["micro_shot_id"],
                candidate_path=item["candidate_path"],
                candidate_sha256=item["candidate_sha256"],
                state=state,
                audio_sha256=item["audio_sha256"],
                entry_anchor_id=item["entry_anchor_id"],
                visual_qc_report_path=item["visual_qc_report_path"],
                visual_qc_report_sha256=item["visual_qc_report_sha256"],
                first_frame_sha256=item["first_frame_sha256"],
                middle_frame_sha256=item["middle_frame_sha256"],
                last_frame_sha256=item["last_frame_sha256"],
                sample_frame_sha256=dict(item["sample_frame_sha256"]),
                rendered_job_report_path=item["rendered_job_report_path"],
                rendered_job_report_sha256=item["rendered_job_report_sha256"],
                reason=item["reason"],
                evidence=dict(evidence),
            )
        )
    manifest = CandidateReviewManifest(
        project_id=data["project_id"],
        candidates=tuple(records),
        schema_version=data["schema_version"],
    )
    _validate_manifest(manifest, require_existing=False)
    return manifest


def approved_selection_from_manifest(
    manifest: CandidateReviewManifest,
    timeline: VisualTimeline,
    *,
    bakeoff_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only editable selection: all approved timeline candidates in order."""
    _validate_manifest(manifest, require_existing=True)
    if not isinstance(timeline, VisualTimeline):
        raise CandidateReviewError("Visual timeline must be a VisualTimeline instance.")
    if manifest.project_id != timeline.project_id:
        raise CandidateReviewError("Candidate review project_id does not match visual timeline.")
    by_micro_shot = {record.micro_shot_id: record for record in manifest.candidates}
    expected_ids = [shot.id for shot in timeline.micro_shots]
    expected_video_ids = [shot.id for shot in timeline.micro_shots if shot.character_ids]
    missing = [micro_shot_id for micro_shot_id in expected_video_ids if micro_shot_id not in by_micro_shot]
    extra = sorted(set(by_micro_shot) - set(expected_video_ids))
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing approved slots: " + ", ".join(missing))
        if extra:
            details.append("unknown candidate slots: " + ", ".join(extra))
        raise CandidateReviewError("Candidate review manifest has " + "; ".join(details) + ".")
    selected: dict[str, dict[str, str]] = {}
    shots = {shot.id: shot for shot in timeline.micro_shots}
    for micro_shot_id in expected_ids:
        if not shots[micro_shot_id].character_ids:
            selected[micro_shot_id] = _approved_still_selection(
                bakeoff_report, micro_shot_id
            )
            continue
        record = by_micro_shot[micro_shot_id]
        if record.state is not CandidateState.APPROVED:
            raise CandidateReviewError(f"Candidate {micro_shot_id} is not approved.")
        _validate_review_evidence(record)
        selected[micro_shot_id] = {
            "kind": "video",
            "candidate_path": record.candidate_path,
            "qc_report_path": record.visual_qc_report_path,
            "audio_sha256": record.audio_sha256,
            "entry_anchor_id": record.entry_anchor_id,
        }
    return {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": manifest.project_id,
        "selected_candidates": selected,
    }


def approved_anchor_for_micro_shot(
    manifest: CandidateReviewManifest,
    timeline: VisualTimeline,
    micro_shot_id: str,
) -> tuple[str, str]:
    """Return the nearest prior approved same-scene immutable last frame."""
    _validate_manifest(manifest, require_existing=True)
    if manifest.project_id != timeline.project_id:
        raise CandidateReviewError(
            "Candidate review project_id does not match visual timeline."
        )
    ordered = sorted(timeline.micro_shots, key=lambda shot: shot.index)
    resolved_scenes: dict[str, str] = {}
    previous_scene = ""
    target = None
    for shot in ordered:
        scene = previous_scene if shot.scene_context == PREVIOUS_SHOT_CONTINUITY else shot.scene_context
        if not scene:
            raise CandidateReviewError(
                f"Candidate {shot.id} has unresolved scene continuity."
            )
        resolved_scenes[shot.id] = scene
        previous_scene = scene
        if shot.id == micro_shot_id:
            target = shot
    if target is None:
        raise CandidateReviewError(f"Unknown micro-shot ID: {micro_shot_id}.")
    records = {record.micro_shot_id: record for record in manifest.candidates}
    eligible = [
        shot
        for shot in ordered
        if shot.index < target.index
        and resolved_scenes[shot.id] == resolved_scenes[target.id]
        and shot.id in records
        and records[shot.id].state is CandidateState.APPROVED
    ]
    prior_same_scene = [
        shot
        for shot in ordered
        if shot.index < target.index
        and resolved_scenes[shot.id] == resolved_scenes[target.id]
    ]
    if not prior_same_scene:
        return "", ""
    if not eligible:
        raise CandidateReviewError(f"{micro_shot_id} missing approved entry anchor.")
    source = eligible[-1]
    record = records[source.id]
    anchor = Path(record.evidence["last_frame"])
    if _sha256(anchor) != record.last_frame_sha256:
        raise CandidateReviewError(
            f"Candidate {source.id} approved last frame changed."
        )
    return str(anchor), source.id


def _approved_still_selection(
    report: Mapping[str, Any] | None, micro_shot_id: str
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise CandidateReviewError(
            f"Candidate review manifest is missing approved still slot: {micro_shot_id}."
        )
    try:
        from .model_bakeoff import ModelBakeoffError, require_selected_still_model

        model = require_selected_still_model(report)
    except ModelBakeoffError as exc:
        raise CandidateReviewError(f"Approved still gate failed: {exc}") from exc
    matches = [
        result
        for result in report.get("still_results", [])
        if isinstance(result, Mapping)
        and result.get("model") == model
        and result.get("micro_shot_id") == micro_shot_id
        and result.get("passed") is True
    ]
    if len(matches) != 1:
        raise CandidateReviewError(
            f"Candidate review manifest is missing approved still slot: {micro_shot_id}."
        )
    result = matches[0]
    candidate = Path(str(result.get("candidate_path") or ""))
    if (
        not is_supported_image_file(candidate)
        or result.get("size_bytes") != candidate.stat().st_size
        or result.get("sha256") != _sha256(candidate)
    ):
        raise CandidateReviewError(f"Approved still {micro_shot_id} changed.")
    return {
        "kind": "still",
        "candidate_path": str(candidate),
        "size_bytes": result["size_bytes"],
        "sha256": result["sha256"],
        "score": result["score"],
        "hard_failures": list(result["hard_failures"]),
        "notes": result["notes"],
    }


def _manifest_payload(manifest: CandidateReviewManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "project_id": manifest.project_id,
        "candidates": [
            {
                **asdict(record),
                "state": record.state.value,
                "evidence": dict(record.evidence),
            }
            for record in manifest.candidates
        ],
    }


def _validate_manifest(manifest: CandidateReviewManifest, *, require_existing: bool) -> None:
    if not isinstance(manifest, CandidateReviewManifest):
        raise CandidateReviewError("Candidate review manifest must be a CandidateReviewManifest instance.")
    if manifest.schema_version != CANDIDATE_REVIEW_SCHEMA:
        raise CandidateReviewError("Candidate review manifest has an unsupported schema.")
    if not _text(manifest.project_id):
        raise CandidateReviewError("Candidate review project_id must be a non-empty string.")
    if not isinstance(manifest.candidates, tuple):
        raise CandidateReviewError("Candidate review candidates must be a tuple.")
    ids: set[str] = set()
    for record in manifest.candidates:
        _validate_record(record, require_existing=require_existing)
        if record.micro_shot_id in ids:
            raise CandidateReviewError("Candidate review manifest must not contain duplicate micro-shot ids.")
        ids.add(record.micro_shot_id)
        if record.state is CandidateState.APPROVED:
            _validate_review_evidence(record, project_id=manifest.project_id)


def _validate_record(record: CandidateRecord, *, require_existing: bool) -> None:
    if not isinstance(record, CandidateRecord):
        raise CandidateReviewError("Candidate review record must be a CandidateRecord instance.")
    if not all(_text(value) for value in (
        record.micro_shot_id, record.candidate_path, record.candidate_sha256,
        record.entry_anchor_id, record.visual_qc_report_path,
    )):
        raise CandidateReviewError("Candidate review record has empty required fields.")
    if not isinstance(record.state, CandidateState):
        raise CandidateReviewError("Candidate review record state is invalid.")
    _validate_hash(record.candidate_sha256, "candidate SHA-256")
    for value, label in (
        (record.visual_qc_report_sha256, "visual QC report SHA-256"),
        (record.first_frame_sha256, "first frame SHA-256"),
        (record.middle_frame_sha256, "middle frame SHA-256"),
        (record.last_frame_sha256, "last frame SHA-256"),
        (record.rendered_job_report_sha256, "rendered job report SHA-256"),
    ):
        if value:
            _validate_hash(value, label)
    if record.audio_sha256:
        _validate_hash(record.audio_sha256, "audio SHA-256")
    if not isinstance(record.reason, str):
        raise CandidateReviewError("Candidate review reason must be a string.")
    if not isinstance(record.sample_frame_sha256, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in record.sample_frame_sha256.items()
    ):
        raise CandidateReviewError("Candidate review sample frame fingerprints are invalid.")
    if not isinstance(record.evidence, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in record.evidence.items()
    ):
        raise CandidateReviewError("Candidate review evidence must be non-empty string values.")
    if require_existing:
        candidate = Path(record.candidate_path)
        if candidate.suffix.lower() != ".mp4" or not candidate.is_file():
            raise CandidateReviewError(
                f"Candidate {record.micro_shot_id} must reference an existing MP4."
            )
        if _sha256(candidate) != record.candidate_sha256:
            raise CandidateReviewError(
                f"Candidate {record.micro_shot_id} MP4 does not match its approved SHA-256."
            )


def _validate_review_evidence(record: CandidateRecord, *, project_id: str = "") -> None:
    missing = _REQUIRED_REVIEW_EVIDENCE - set(record.evidence)
    if missing:
        raise CandidateReviewError(
            "Candidate review evidence is missing: " + ", ".join(sorted(missing)) + "."
        )
    _validate_qc_frame_evidence(record)
    _validate_task5_job_evidence(record, project_id=project_id)
    if record.audio_sha256:
        missing = _VISIBLE_SPEECH_EVIDENCE - set(record.evidence)
        if missing:
            raise CandidateReviewError(
                "Visible-speaking candidate review evidence is missing: "
                + ", ".join(sorted(missing)) + "."
            )
        if record.evidence["audio_sha256"] != record.audio_sha256:
            raise CandidateReviewError("Candidate review audio evidence does not match the rendered job.")
        if record.evidence["speaker_visible"] != "true":
            raise CandidateReviewError("Visible-speaking candidate review requires speaker_visible=true.")
        try:
            score = float(record.evidence["lipsync_score"])
        except ValueError as exc:
            raise CandidateReviewError("Visible-speaking candidate lipsync_score is invalid.") from exc
        if not 0 <= score <= 5:
            raise CandidateReviewError("Visible-speaking candidate lipsync_score must be 0 to 5.")
        if score < 3.5:
            raise CandidateReviewError(
                "Visible-speaking candidate lipsync_score must be at least 3.5."
            )
        manual = _visual_qc_manual_review(record)
        if manual.get("audio_sha256") != record.audio_sha256:
            raise CandidateReviewError("Candidate review audio does not match visual QC evidence.")
        if manual.get("entry_anchor_id") != record.entry_anchor_id:
            raise CandidateReviewError("Candidate review anchor does not match visual QC evidence.")
        if manual.get("speaker_visible") is not True:
            raise CandidateReviewError(
                "Candidate review speaker visibility does not match visual QC evidence."
            )
        manual_lipsync = manual.get("lipsync_score")
        if (
            isinstance(manual_lipsync, bool)
            or not isinstance(manual_lipsync, (int, float))
            or float(manual_lipsync) < 3.5
        ):
            raise CandidateReviewError(
                "Visible-speaking authoritative QC lipsync_score must be at least 3.5."
            )
        if float(manual_lipsync) != score:
            raise CandidateReviewError(
                "Candidate review lipsync_score does not match visual QC evidence."
            )


def _validate_qc_frame_evidence(record: CandidateRecord) -> None:
    try:
        report = json.loads(Path(record.visual_qc_report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReviewError("Candidate review visual QC report is unavailable.") from exc
    if _sha256(Path(record.visual_qc_report_path)) != record.visual_qc_report_sha256:
        raise CandidateReviewError("Candidate review visual QC report changed.")
    if not isinstance(report, Mapping) or report.get("schema_version") != "motion-comic-factory.visual-qc.v2":
        raise CandidateReviewError("Candidate review visual QC report schema is invalid.")
    required = {"candidate_evidence", "sample_frames", "automatic_passed", "manual_review"}
    if not required.issubset(report):
        raise CandidateReviewError("Candidate review visual QC report provenance is incomplete.")
    if report["automatic_passed"] is not True:
        raise CandidateReviewError("Candidate review visual QC automatic provenance did not pass.")
    if report.get("passed") is not True:
        raise CandidateReviewError("Candidate review visual QC did not pass.")
    candidate_evidence = report["candidate_evidence"]
    if not isinstance(candidate_evidence, Mapping) or candidate_evidence.get("path") != record.candidate_path or candidate_evidence.get("sha256") != record.candidate_sha256:
        raise CandidateReviewError("Candidate review visual QC candidate provenance is invalid.")
    samples = report.get("sample_frames")
    if not isinstance(samples, list) or len(samples) != 9:
        raise CandidateReviewError("Candidate review visual QC sample evidence is invalid.")
    fingerprints = {
        "first_frame": record.first_frame_sha256,
        "middle_frame": record.middle_frame_sha256,
        "last_frame": record.last_frame_sha256,
    }
    expected_sample_keys = {f"sample_{index:02d}" for index in range(1, 10)}
    if set(record.sample_frame_sha256) != expected_sample_keys:
        raise CandidateReviewError(
            "Candidate review must contain fingerprints for every visual QC sample."
        )
    for index in range(9):
        label = {0: "first_frame", 4: "middle_frame", 8: "last_frame"}.get(index)
        sample = samples[index]
        evidence = sample.get("evidence") if isinstance(sample, Mapping) else None
        if not isinstance(evidence, Mapping):
            raise CandidateReviewError("Candidate review visual QC sample evidence is invalid.")
        path = Path(str(evidence.get("path") or ""))
        if label is not None and record.evidence[label] != str(path):
            raise CandidateReviewError(
                f"Candidate review {label} does not match visual QC sample evidence."
            )
        try:
            stat = path.stat()
            digest = _sha256(path)
        except OSError as exc:
            raise CandidateReviewError("Candidate review visual QC sample evidence is unavailable.") from exc
        if (
            evidence.get("sha256") != digest
            or evidence.get("size_bytes") != stat.st_size
            or evidence.get("device") != stat.st_dev
            or evidence.get("inode") != stat.st_ino
        ):
            raise CandidateReviewError("Candidate review visual QC sample evidence changed.")
        sample_key = f"sample_{index + 1:02d}"
        if digest != record.sample_frame_sha256[sample_key]:
            raise CandidateReviewError(
                f"Candidate review {sample_key} changed."
            )
        if label is not None and digest != fingerprints[label]:
            raise CandidateReviewError(
                f"Candidate review {label} fingerprint does not match visual QC evidence."
            )


def _validate_task5_job_evidence(record: CandidateRecord, *, project_id: str) -> None:
    try:
        report_path = Path(record.rendered_job_report_path)
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReviewError("Candidate review Task 5 job report is unavailable.") from exc
    if _sha256(report_path) != record.rendered_job_report_sha256:
        raise CandidateReviewError("Candidate review Task 5 job report changed.")
    if not isinstance(report, Mapping) or report.get("schema_version") != "motion-comic-factory.micro-video-batch.v1":
        raise CandidateReviewError("Candidate review Task 5 job report schema is invalid.")
    if project_id and report.get("project_id") != project_id:
        raise CandidateReviewError("Candidate review Task 5 job report project_id is invalid.")
    reported_run = report.get("run_dir")
    if not isinstance(reported_run, str) or not Path(reported_run).is_dir():
        raise CandidateReviewError("Candidate review Task 5 job report run_dir is invalid.")
    if not Path(record.candidate_path).resolve().is_relative_to(Path(reported_run).resolve()):
        raise CandidateReviewError("Candidate review Task 5 job report does not own the candidate.")
    if report.get("success") is not True or not isinstance(report.get("completed_count"), int) or report["completed_count"] < 1:
        raise CandidateReviewError("Candidate review Task 5 job report is not completed successfully.")
    jobs = report.get("jobs")
    if not isinstance(jobs, list):
        raise CandidateReviewError("Candidate review Task 5 job report has no jobs.")
    matches = [job for job in jobs if isinstance(job, Mapping) and job.get("micro_shot_id") == record.micro_shot_id]
    if len(matches) != 1:
        raise CandidateReviewError("Candidate review Task 5 job report has no matching job.")
    job = matches[0]
    if (
        job.get("output_path") != record.candidate_path
        or job.get("output_sha256") != record.candidate_sha256
        or job.get("reference_audio_sha256") != record.audio_sha256
        or job.get("entry_anchor_id") != record.entry_anchor_id
    ):
        raise CandidateReviewError("Candidate review Task 5 job evidence does not match candidate bindings.")


def _visual_qc_manual_review(record: CandidateRecord) -> Mapping[str, Any]:
    try:
        report = json.loads(Path(record.visual_qc_report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateReviewError("Candidate review visual QC report is unavailable.") from exc
    manual = report.get("manual_review") if isinstance(report, Mapping) else None
    if not isinstance(manual, Mapping):
        raise CandidateReviewError("Candidate review is missing authoritative visual QC review.")
    return manual


def _validate_hash(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CandidateReviewError(f"Candidate review {label} must be lowercase SHA-256.")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
