from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, UnidentifiedImageError

from .candidate_review import (
    CandidateReviewError,
    CandidateReviewManifest,
    approved_selection_from_manifest,
    candidate_review_manifest_from_dict,
)
from .media_validation import probe_media, temporary_media_path
from .model_bakeoff import (
    STILL_HARD_FAILURES,
    ModelBakeoffError,
    require_selected_production_model,
    require_selected_still_model,
)
from .schema import Episode
from .visual_qc import VisualQCError, require_passed_visual_qc
from .visual_timeline import (
    MicroShot,
    VisualTimeline,
    VisualTimelineError,
    validate_visual_timeline,
    visual_timeline_from_dict,
)


VISUAL_SELECTION_SCHEMA = "motion-comic-factory.visual-selection.v1"
MICRO_PREVIEW_REPORT_SCHEMA = "motion-comic-factory.micro-preview.v1"
_SELECTION_KEYS = frozenset({"schema_version", "project_id", "selected_candidates"})
_VIDEO_SELECTION_KEYS = frozenset(
    {"kind", "candidate_path", "qc_report_path", "audio_sha256", "entry_anchor_id"}
)
_LEGACY_VIDEO_SELECTION_KEYS = frozenset(
    {"kind", "candidate_path", "qc_report_path"}
)
_STILL_SELECTION_KEYS = frozenset(
    {
        "kind",
        "candidate_path",
        "size_bytes",
        "sha256",
        "score",
        "hard_failures",
        "notes",
    }
)
_EDITORIAL_STILL_SELECTION_KEYS = frozenset(
    {
        "kind",
        "candidate_path",
        "source_path",
        "source_size_bytes",
        "source_sha256",
        "operation",
        "parameters",
        "size_bytes",
        "sha256",
        "score",
        "hard_failures",
        "notes",
    }
)
_VIDEO_CANDIDATE = re.compile(r"candidate_(00[1-3])\.mp4")
_STILL_CANDIDATE = re.compile(r"candidate_(00[1-3])\.(?:png|jpe?g|webp)", re.IGNORECASE)
_EDITORIAL_STILL_CANDIDATE = re.compile(
    r"editorial_(?:00[1-9]|0[1-9]\d|[1-9]\d{2})\.(?:png|jpe?g|webp)",
    re.IGNORECASE,
)
_EDITORIAL_SOURCE_SUFFIXES = frozenset({".mp4", ".png", ".jpg", ".jpeg", ".webp"})
_URI = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\bdata:|\bfile:")
_CREDENTIAL = re.compile(
    r"(?i)\b(?:api[ _-]?key|access[ _-]?key|authorization|bearer|password|"
    r"secret|credential|signed[ _-]?url)\b\s*[:=]?"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class MicroPreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class MicroSource:
    micro_shot_id: str
    index: int
    kind: str
    path: Path
    model: str
    size_bytes: int
    sha256: str
    qc_report_path: Path | None
    selected_start_seconds: float
    selected_end_seconds: float
    timeline_duration_seconds: float
    cadence_fps: int
    camera_mode: str
    entry_cut: str
    exit_cut: str
    provenance: Mapping[str, Any] | None = None

    def to_report(self) -> dict[str, Any]:
        report = {
            "micro_shot_id": self.micro_shot_id,
            "index": self.index,
            "kind": self.kind,
            "source_path": str(self.path),
            "model": self.model,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "qc_report_path": (
                str(self.qc_report_path) if self.qc_report_path is not None else None
            ),
            "selected_start_seconds": self.selected_start_seconds,
            "selected_end_seconds": self.selected_end_seconds,
            "timeline_duration_seconds": self.timeline_duration_seconds,
            "cadence_fps": self.cadence_fps,
            "camera_mode": self.camera_mode,
            "entry_cut": self.entry_cut,
            "exit_cut": self.exit_cut,
            "inserted_black_seconds": (
                0.1 if self.exit_cut == "time_jump_black" else 0.0
            ),
            "rendered_visual_seconds": self.timeline_duration_seconds
            - (0.1 if self.exit_cut == "time_jump_black" else 0.0),
        }
        if self.provenance is not None:
            report["provenance"] = dict(self.provenance)
        return report


def select_micro_sources(
    episode: Episode,
    timeline: VisualTimeline,
    selection: Mapping[str, Any],
    *,
    run_dir: str | Path,
    bakeoff_report: Mapping[str, Any],
    candidate_review: CandidateReviewManifest | Mapping[str, Any],
) -> list[MicroSource]:
    errors = validate_visual_timeline(timeline, episode)
    if errors:
        raise MicroPreviewError("Invalid visual timeline: " + "; ".join(errors))
    root = _canonical_directory(run_dir, "run directory")
    if candidate_review is None:
        raise MicroPreviewError("Candidate review manifest is required before editing.")
    try:
        manifest = (
            candidate_review
            if isinstance(candidate_review, CandidateReviewManifest)
            else candidate_review_manifest_from_dict(candidate_review)
        )
        approved_selection = approved_selection_from_manifest(manifest, timeline)
    except CandidateReviewError as exc:
        raise MicroPreviewError(f"Candidate review gate failed: {exc}") from exc
    if selection != approved_selection:
        raise MicroPreviewError(
            "Visual selection does not exactly match the approved candidate review."
        )
    normalized = _selection(selection, episode, timeline)
    if not isinstance(bakeoff_report, Mapping):
        raise MicroPreviewError("Model bakeoff report must be an object.")
    if bakeoff_report.get("project_id") != episode.project_id:
        raise MicroPreviewError(
            "Model bakeoff report project_id does not match episode."
        )
    if bakeoff_report.get("run_dir") != str(root):
        raise MicroPreviewError(
            "Model bakeoff report run_dir does not match run directory."
        )
    try:
        video_model = require_selected_production_model(bakeoff_report)
        still_model = (
            require_selected_still_model(bakeoff_report)
            if any(
                item.get("kind") == "still"
                for item in normalized["selected_candidates"].values()
            )
            else ""
        )
    except ModelBakeoffError as exc:
        raise MicroPreviewError(f"Model bakeoff gate failed: {exc}") from exc

    sources: list[MicroSource] = []
    for shot in timeline.micro_shots:
        item = normalized["selected_candidates"][shot.id]
        if item["kind"] == "video":
            sources.append(_video_source(root, shot, item, video_model))
        elif item["kind"] == "editorial_still":
            sources.append(_editorial_still_source(root, shot, item))
        else:
            sources.append(_still_source(root, shot, item, still_model))
    return sources


def build_micro_preview_ffmpeg_command(
    *,
    sources: Sequence[MicroSource],
    resolution: str,
    output_fps: int,
    output_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    if not sources:
        raise MicroPreviewError("Micro preview requires at least one source.")
    width, height = _parse_resolution(resolution)
    if (width, height) != (1080, 1920):
        raise MicroPreviewError("Micro preview resolution must be exactly 1080x1920.")
    if isinstance(output_fps, bool) or output_fps != 30:
        raise MicroPreviewError("Micro preview output FPS must be exactly 30.")
    if sources[-1].exit_cut == "time_jump_black":
        raise MicroPreviewError("The final micro-shot may not request time_jump_black.")

    command = [ffmpeg_bin, "-y"]
    for source in sources:
        _validate_source_fields(source, output_fps)
        if source.kind == "still":
            command.extend(["-loop", "1", "-framerate", str(output_fps)])
        command.extend(["-i", str(source.path)])

    filters: list[str] = []
    concat_labels: list[str] = []
    for input_index, source in enumerate(sources):
        black_seconds = 0.1 if source.exit_cut == "time_jump_black" else 0.0
        visual_seconds = source.timeline_duration_seconds - black_seconds
        if visual_seconds <= 0:
            raise MicroPreviewError(
                f"time_jump_black leaves a non-positive visual segment: {source.micro_shot_id}"
            )
        label = f"v{input_index}"
        if source.kind == "video":
            filters.append(
                f"[{input_index}:v]"
                f"trim=start={source.selected_start_seconds:.3f}:"
                f"end={source.selected_end_seconds:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={source.cadence_fps},fps={output_fps},"
                f"tpad=stop_mode=clone:stop_duration={visual_seconds:.3f},"
                f"trim=duration={visual_seconds:.3f},setpts=PTS-STARTPTS[{label}]"
            )
        else:
            still_filters = (
                f"[{input_index}:v]"
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},setsar=1"
            )
            if source.camera_mode == "micro_pan":
                frames = max(1, int(round(visual_seconds * output_fps)))
                denominator = max(1, frames - 1)
                still_filters += (
                    f",zoompan=z='min(1.02,1+0.02*on/{denominator})'"
                    ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={width}x{height}:fps={output_fps}"
                )
            else:
                still_filters += f",fps={output_fps}"
            filters.append(
                still_filters
                + f",trim=duration={visual_seconds:.3f},setpts=PTS-STARTPTS[{label}]"
            )
        concat_labels.append(f"[{label}]")
        if black_seconds:
            black_label = f"b{input_index}"
            filters.append(
                f"color=c=black:s={width}x{height}:r={output_fps}:d={black_seconds:.3f},"
                f"format=yuv420p[{black_label}]"
            )
            concat_labels.append(f"[{black_label}]")

    total_seconds = sum(source.timeline_duration_seconds for source in sources)
    filters.append(
        f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=1:a=0,"
        f"trim=duration={total_seconds:.3f},setpts=PTS-STARTPTS,"
        "format=yuv420p[vout]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def render_micro_preview_video(
    episode: Episode,
    *,
    timeline_path: str | Path,
    selection_path: str | Path,
    bakeoff_report_path: str | Path,
    run_dir: str | Path,
    output_path: str | Path,
    report_path: str | Path,
    command_runner: Callable[..., Any] = subprocess.run,
    media_validator: Callable[[Path], bool] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    root = _canonical_directory(run_dir, "run directory")
    timeline_source = _expected_artifact(timeline_path, root, "visual_timeline.json")
    selection_source = _expected_artifact(selection_path, root, "visual_selection.json")
    candidate_review_source = _expected_artifact(
        root / "candidate_review.json", root, "candidate_review.json"
    )
    bakeoff_source = _expected_artifact(
        bakeoff_report_path, root, "model_bakeoff_report.json"
    )
    try:
        timeline = visual_timeline_from_dict(
            _read_json_object(timeline_source, "visual timeline")
        )
    except VisualTimelineError as exc:
        raise MicroPreviewError(f"Invalid visual timeline: {exc}") from exc
    selection = _read_json_object(selection_source, "visual selection")
    bakeoff_report = _read_json_object(bakeoff_source, "model bakeoff report")
    try:
        candidate_review = candidate_review_manifest_from_dict(
            _read_json_object(candidate_review_source, "candidate review manifest")
        )
    except CandidateReviewError as exc:
        raise MicroPreviewError(f"Candidate review gate failed: {exc}") from exc
    sources = select_micro_sources(
        episode,
        timeline,
        selection,
        run_dir=root,
        bakeoff_report=bakeoff_report,
        candidate_review=candidate_review,
    )
    output = _canonical_output(output_path, root, "micro_preview.mp4")
    destination = _canonical_output(report_path, root, "micro_preview_report.json")
    temporary_output = temporary_media_path(output)
    command = build_micro_preview_ffmpeg_command(
        sources=sources,
        resolution="1080x1920",
        output_fps=30,
        output_path=temporary_output,
        ffmpeg_bin=ffmpeg_bin,
    )
    for source in sources:
        _require_unchanged(source)
    try:
        command_runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        temporary_output.unlink(missing_ok=True)
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise MicroPreviewError(
            f"Micro preview FFmpeg render failed: {detail}"
        ) from exc
    except OSError as exc:
        temporary_output.unlink(missing_ok=True)
        raise MicroPreviewError(f"Unable to run micro preview FFmpeg: {exc}") from exc
    validator = media_validator or (
        lambda path: probe_media(path, required_stream="video").valid
    )
    if not validator(temporary_output):
        temporary_output.unlink(missing_ok=True)
        raise MicroPreviewError(
            "Micro preview render did not produce a valid video stream."
        )

    report = {
        "schema_version": MICRO_PREVIEW_REPORT_SCHEMA,
        "success": True,
        "project_id": episode.project_id,
        "output_path": str(output),
        "output_size_bytes": temporary_output.stat().st_size,
        "resolution": "1080x1920",
        "fps": 30,
        "duration_seconds": round(
            sum(source.timeline_duration_seconds for source in sources), 6
        ),
        "micro_shot_count": len(sources),
        "sources": [source.to_report() for source in sources],
        "ffmpeg_command": command,
    }
    temporary_report = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        _write_json_file(temporary_report, report)
        temporary_output.replace(output)
        temporary_report.replace(destination)
    finally:
        temporary_output.unlink(missing_ok=True)
        temporary_report.unlink(missing_ok=True)
    return report


def _selection(
    selection: Mapping[str, Any], episode: Episode, timeline: VisualTimeline
) -> dict[str, Any]:
    if not isinstance(selection, Mapping) or set(selection) != _SELECTION_KEYS:
        raise MicroPreviewError("Visual selection has an invalid exact-key schema.")
    if selection["schema_version"] != VISUAL_SELECTION_SCHEMA:
        raise MicroPreviewError("Visual selection has an unsupported schema_version.")
    if selection["project_id"] != episode.project_id:
        raise MicroPreviewError("Visual selection project_id does not match episode.")
    selected = selection["selected_candidates"]
    if not isinstance(selected, Mapping):
        raise MicroPreviewError(
            "Visual selection selected_candidates must be an object."
        )
    expected_ids = {shot.id for shot in timeline.micro_shots}
    if set(selected) != expected_ids:
        raise MicroPreviewError(
            "Visual selection IDs must exactly match all visual timeline micro-shots."
        )
    normalized: dict[str, dict[str, Any]] = {}
    for shot in timeline.micro_shots:
        item = selected[shot.id]
        if not isinstance(item, Mapping):
            raise MicroPreviewError(
                f"Selected candidate for {shot.id} must be an object."
            )
        kind = item.get("kind")
        expected = {
            "video": _VIDEO_SELECTION_KEYS
            if "audio_sha256" in item or "entry_anchor_id" in item
            else _LEGACY_VIDEO_SELECTION_KEYS,
            "still": _STILL_SELECTION_KEYS,
            "editorial_still": _EDITORIAL_STILL_SELECTION_KEYS,
        }.get(kind)
        if expected is None or set(item) != expected:
            raise MicroPreviewError(
                f"Selected candidate for {shot.id} has an invalid exact-key schema."
            )
        normalized[shot.id] = dict(item)
    return {
        "schema_version": selection["schema_version"],
        "project_id": selection["project_id"],
        "selected_candidates": normalized,
    }


def _video_source(
    root: Path, shot: MicroShot, item: Mapping[str, Any], model: str
) -> MicroSource:
    candidate = _candidate_path(
        item["candidate_path"], root, shot.id, model, "micro_clips", _VIDEO_CANDIDATE
    )
    qc_path = _canonical_file(item["qc_report_path"], "visual QC report")
    if qc_path.name != "visual_qc.json":
        raise MicroPreviewError("Visual QC report path must end with visual_qc.json.")
    qc = _read_json_object(qc_path, "visual QC report")
    evidence = qc.get("candidate_evidence")
    evidence_path = evidence.get("path") if isinstance(evidence, Mapping) else None
    if evidence_path != str(candidate):
        raise MicroPreviewError(
            f"Visual QC candidate path does not match selection for {shot.id}."
        )
    try:
        require_passed_visual_qc(
            qc,
            expected_micro_shot=shot,
            expected_reference_image_labels=shot.character_ids,
        )
    except VisualQCError as exc:
        raise MicroPreviewError(f"{shot.id} failed visual QC: {exc}") from exc
    manual = qc.get("manual_review")
    if not isinstance(manual, Mapping):
        raise MicroPreviewError(f"{shot.id} failed visual QC: missing manual review.")
    start = _finite_seconds(manual.get("selected_start_seconds"), "selected start")
    end = _finite_seconds(manual.get("selected_end_seconds"), "selected end")
    if end <= start:
        raise MicroPreviewError(f"{shot.id} failed visual QC: invalid selected range.")
    size, digest = _fingerprint(candidate)
    return _source(shot, "video", candidate, model, size, digest, qc_path, start, end)


def _still_source(
    root: Path, shot: MicroShot, item: Mapping[str, Any], model: str
) -> MicroSource:
    if shot.character_ids:
        raise MicroPreviewError(
            f"Still candidate is not allowed for character micro-shot {shot.id}."
        )
    if not model:
        raise MicroPreviewError(
            "No selected still model is available for still routing."
        )
    candidate = _candidate_path(
        item["candidate_path"], root, shot.id, model, "micro_stills", _STILL_CANDIDATE
    )
    size, digest = _validated_still_review(candidate, item)
    duration = float(shot.timeline_duration_seconds)
    return _source(shot, "still", candidate, model, size, digest, None, 0.0, duration)


def _editorial_still_source(
    root: Path,
    shot: MicroShot,
    item: Mapping[str, Any],
) -> MicroSource:
    if shot.character_ids:
        raise MicroPreviewError(
            f"Editorial still is not allowed for character micro-shot {shot.id}."
        )
    candidate = _canonical_file(item["candidate_path"], "editorial still")
    expected_parent = root / "editorial_stills" / shot.id
    if (
        candidate.parent != expected_parent
        or _EDITORIAL_STILL_CANDIDATE.fullmatch(candidate.name) is None
    ):
        raise MicroPreviewError(
            f"Selected editorial still path is not valid for {shot.id}."
        )
    source = _canonical_file(item["source_path"], "editorial source")
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise MicroPreviewError(
            "Editorial source must be inside the project run directory."
        ) from exc
    if source == candidate:
        raise MicroPreviewError(
            "Editorial source and derived candidate must be different files."
        )
    if source.suffix.lower() not in _EDITORIAL_SOURCE_SUFFIXES:
        raise MicroPreviewError("Editorial source has an unsupported media type.")
    source_size = item["source_size_bytes"]
    if (
        isinstance(source_size, bool)
        or not isinstance(source_size, int)
        or source_size <= 0
    ):
        raise MicroPreviewError(
            "Editorial source size_bytes must be a positive integer."
        )
    source_hash = item["source_sha256"]
    if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
        raise MicroPreviewError(
            "Editorial source SHA-256 must be canonical lowercase hex."
        )
    current_source_size, current_source_hash = _fingerprint(source)
    if current_source_size != source_size:
        raise MicroPreviewError(
            "Editorial source size does not match its provenance."
        )
    if current_source_hash != source_hash:
        raise MicroPreviewError(
            "Editorial source SHA-256 does not match its provenance."
        )
    operation, parameters = _editorial_operation(
        item["operation"],
        item["parameters"],
    )
    size, digest = _validated_still_review(candidate, item)
    duration = float(shot.timeline_duration_seconds)
    provenance = {
        "source_path": str(source),
        "source_size_bytes": source_size,
        "source_sha256": source_hash,
        "operation": operation,
        "parameters": parameters,
    }
    return _source(
        shot,
        "still",
        candidate,
        "editorial-derived",
        size,
        digest,
        None,
        0.0,
        duration,
        provenance=provenance,
    )


def _validated_still_review(
    candidate: Path,
    item: Mapping[str, Any],
) -> tuple[int, str]:
    stored_size = item["size_bytes"]
    if (
        isinstance(stored_size, bool)
        or not isinstance(stored_size, int)
        or stored_size <= 0
    ):
        raise MicroPreviewError("Still review size_bytes must be a positive integer.")
    stored_hash = item["sha256"]
    if not isinstance(stored_hash, str) or not _SHA256.fullmatch(stored_hash):
        raise MicroPreviewError("Still review SHA-256 must be canonical lowercase hex.")
    score = item["score"]
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        raise MicroPreviewError("Still review score must be a finite number.")
    if score < 80 or score > 100:
        raise MicroPreviewError(
            "Still review score must be at least 80 and at most 100."
        )
    failures = item["hard_failures"]
    if (
        not isinstance(failures, list)
        or any(not isinstance(value, str) for value in failures)
        or len(set(failures)) != len(failures)
        or any(value not in STILL_HARD_FAILURES for value in failures)
    ):
        raise MicroPreviewError("Still review hard_failures are invalid.")
    if failures:
        raise MicroPreviewError("Still review contains a hard failure.")
    _safe_notes(item["notes"])
    try:
        with Image.open(candidate) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise MicroPreviewError("Still candidate has invalid dimensions.")
    except (OSError, UnidentifiedImageError) as exc:
        raise MicroPreviewError(
            f"Still candidate cannot be fully decoded: {candidate}"
        ) from exc
    size, digest = _fingerprint(candidate)
    if size != stored_size:
        raise MicroPreviewError("Still candidate size does not match its review.")
    if digest != stored_hash:
        raise MicroPreviewError("Still candidate SHA-256 does not match its review.")
    return size, digest


def _editorial_operation(value: Any, parameters: Any) -> tuple[str, dict[str, Any]]:
    if value not in {"extract_frame", "crop_scale", "copy"}:
        raise MicroPreviewError("Editorial still operation is invalid.")
    if not isinstance(parameters, Mapping):
        raise MicroPreviewError("Editorial still parameters must be an object.")
    normalized = dict(parameters)
    if value == "copy":
        if normalized:
            raise MicroPreviewError("Editorial copy parameters must be empty.")
        return value, normalized
    if value == "extract_frame":
        if set(normalized) != {"timestamp_seconds"}:
            raise MicroPreviewError(
                "Editorial extract_frame parameters require timestamp_seconds."
            )
        timestamp = normalized["timestamp_seconds"]
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise MicroPreviewError(
                "Editorial timestamp_seconds must be finite and non-negative."
            )
        normalized["timestamp_seconds"] = float(timestamp)
        return value, normalized
    expected = {"x", "y", "width", "height", "output_width", "output_height"}
    if set(normalized) != expected:
        raise MicroPreviewError(
            "Editorial crop_scale parameters have an invalid exact-key schema."
        )
    for key, parameter in normalized.items():
        minimum = 0 if key in {"x", "y"} else 1
        if (
            isinstance(parameter, bool)
            or not isinstance(parameter, int)
            or parameter < minimum
        ):
            raise MicroPreviewError(
                f"Editorial crop_scale {key} must be an integer of at least {minimum}."
            )
    return value, normalized


def _source(
    shot: MicroShot,
    kind: str,
    path: Path,
    model: str,
    size: int,
    digest: str,
    qc_path: Path | None,
    start: float,
    end: float,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> MicroSource:
    return MicroSource(
        micro_shot_id=shot.id,
        index=shot.index,
        kind=kind,
        path=path,
        model=model,
        size_bytes=size,
        sha256=digest,
        qc_report_path=qc_path,
        selected_start_seconds=start,
        selected_end_seconds=end,
        timeline_duration_seconds=float(shot.timeline_duration_seconds),
        cadence_fps=shot.cadence_fps,
        camera_mode=shot.camera_mode,
        entry_cut=shot.entry_cut,
        exit_cut=shot.exit_cut,
        provenance=provenance,
    )


def _candidate_path(
    value: Any,
    root: Path,
    shot_id: str,
    model: str,
    directory: str,
    pattern: re.Pattern[str],
) -> Path:
    candidate = _canonical_file(value, "selected candidate")
    expected_parent = root / directory / shot_id / model
    if candidate.parent != expected_parent or pattern.fullmatch(candidate.name) is None:
        raise MicroPreviewError(
            f"Selected candidate path is not valid for {shot_id} and model {model}."
        )
    return candidate


def _validate_source_fields(source: MicroSource, output_fps: int) -> None:
    if source.kind not in {"video", "still"}:
        raise MicroPreviewError(f"Unsupported micro preview source kind: {source.kind}")
    if source.timeline_duration_seconds <= 0:
        raise MicroPreviewError(
            f"Micro-shot duration must be positive: {source.micro_shot_id}"
        )
    if not 1 <= source.cadence_fps <= 10 or source.cadence_fps > output_fps:
        raise MicroPreviewError(
            f"Micro-shot cadence is invalid: {source.micro_shot_id}"
        )
    if (
        source.kind == "video"
        and source.selected_end_seconds <= source.selected_start_seconds
    ):
        raise MicroPreviewError(
            f"Video selected range is invalid: {source.micro_shot_id}"
        )
    if source.kind == "still" and source.camera_mode not in {
        "locked",
        "object_insert",
        "micro_pan",
    }:
        raise MicroPreviewError(f"Still camera mode is invalid: {source.micro_shot_id}")
    if source.exit_cut not in {"hard_cut", "match_cut", "time_jump_black"}:
        raise MicroPreviewError(f"Exit cut is invalid: {source.micro_shot_id}")


def _require_unchanged(source: MicroSource) -> None:
    try:
        current = _canonical_file(source.path, "selected source")
        if current != source.path:
            raise MicroPreviewError("Selected source path is no longer canonical.")
        size, digest = _fingerprint(current)
    except MicroPreviewError as exc:
        raise MicroPreviewError(
            f"Source changed after selection: {source.micro_shot_id}"
        ) from exc
    if size != source.size_bytes or digest != source.sha256:
        raise MicroPreviewError(
            f"Source changed after selection: {source.micro_shot_id}"
        )


def _fingerprint(path: Path) -> tuple[int, str]:
    try:
        before = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
    except OSError as exc:
        raise MicroPreviewError(f"Unable to fingerprint local source: {path}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise MicroPreviewError(f"Source changed while fingerprinting: {path}")
    return after.st_size, digest


def _safe_notes(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        raise MicroPreviewError("Still review notes must be a local text string.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise MicroPreviewError("Still review notes contain control characters.")
    if _URI.search(value) or _CREDENTIAL.search(value):
        raise MicroPreviewError(
            "Still review notes must not contain remote assets or credentials."
        )
    return value


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MicroPreviewError(f"Unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise MicroPreviewError(f"{label.capitalize()} must contain a JSON object.")
    return value


def _write_json_file(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _expected_artifact(value: str | Path, root: Path, name: str) -> Path:
    path = _canonical_file(value, name)
    if path != root / name:
        raise MicroPreviewError(f"{name} must be the canonical run artifact.")
    return path


def _canonical_directory(value: str | Path, label: str) -> Path:
    path = _absolute_path(value, label)
    _reject_symlink_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MicroPreviewError(f"{label.capitalize()} does not exist: {path}") from exc
    if resolved != path or not path.is_dir():
        raise MicroPreviewError(
            f"{label.capitalize()} must be a canonical local directory."
        )
    return path


def _canonical_file(value: Any, label: str) -> Path:
    path = _absolute_path(value, label)
    _reject_symlink_components(path, label)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MicroPreviewError(f"{label.capitalize()} does not exist: {path}") from exc
    if resolved != path or not path.is_file():
        raise MicroPreviewError(f"{label.capitalize()} must be a canonical local file.")
    return path


def _canonical_output(value: str | Path, root: Path, name: str) -> Path:
    path = _absolute_path(value, name)
    if path != root / name:
        raise MicroPreviewError(f"{name} must be the canonical run output path.")
    _reject_symlink_components(path.parent, name)
    if path.exists() and path.is_symlink():
        raise MicroPreviewError(f"{name} must not be a symlink.")
    if path.exists() and not path.is_file():
        raise MicroPreviewError(f"{name} must be a regular file path.")
    return path


def _absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise MicroPreviewError(f"{label.capitalize()} must be a local path.")
    raw = str(value)
    if _CREDENTIAL.search(raw):
        raise MicroPreviewError(f"{label.capitalize()} must not contain credentials.")
    if not raw or _URI.search(raw) or "?" in raw or "#" in raw:
        raise MicroPreviewError(f"{label.capitalize()} must be a canonical local path.")
    path = Path(raw)
    if not path.is_absolute() or str(path) != raw:
        raise MicroPreviewError(f"{label.capitalize()} must be an exact absolute path.")
    return path


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            if current.is_symlink():
                raise MicroPreviewError(
                    f"{label.capitalize()} must not use a symlink: {current}"
                )
        except OSError as exc:
            raise MicroPreviewError(f"Unable to inspect {label}: {current}") from exc


def _finite_seconds(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MicroPreviewError(f"{label.capitalize()} must be finite seconds.")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise MicroPreviewError(f"{label.capitalize()} must be finite seconds.")
    return seconds


def _parse_resolution(value: str) -> tuple[int, int]:
    if not isinstance(value, str):
        raise MicroPreviewError(
            "Micro preview resolution must use WIDTHxHEIGHT format."
        )
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", value)
    if match is None:
        raise MicroPreviewError(
            "Micro preview resolution must use WIDTHxHEIGHT format."
        )
    return int(match.group(1)), int(match.group(2))
