from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .gateway_video import is_valid_mp4_file
from .pet_replica import PetReplicaPlan, validate_pet_replica_plan
from .pet_replica_generation import ReplicaCandidate


LIPSYNC_SCHEMA_VERSION = "motion-comic-factory.pet-replica-lipsync.v1"
LIPSYNC_ENGINE = "wav2lip"
LIPSYNC_MODEL = "wav2lip_gan"
LIPSYNC_LICENSE_SCOPE = "local_evaluation_noncommercial_only"
MOUTH_BLEND_SCHEMA_VERSION = "motion-comic-factory.pet-replica-mouth-blend.v1"
MOUTH_BLEND_ENGINE = "opencv_yunet_mouth_blend"
MOUTH_BLEND_MODEL = "face_detection_yunet_2026may"
MOUTH_BLEND_SCORE_THRESHOLD = 0.35
MOUTH_BLEND_NMS_THRESHOLD = 0.3
MOUTH_BLEND_TOP_K = 5000
MOUTH_BLEND_CORNER_SCALE = 0.75
MOUTH_BLEND_RADIUS_RATIOS = [0.28, 0.15]
MOUTH_BLEND_MAX_ALPHA = 0.92
MOUTH_BLEND_FEATHER_RATIO = 0.3
VISUAL_TIMING_SCHEMA_VERSION_V1 = "motion-comic-factory.pet-replica-visual-timing.v1"
VISUAL_TIMING_SCHEMA_VERSION = "motion-comic-factory.pet-replica-visual-timing.v2"
VISUAL_TIMING_ENGINE = "ffmpeg_visual_timing"
VISUAL_TIMING_MODEL = "forward_then_reverse_close"
VISUAL_TIMING_LICENSE_SCOPE = "inherits_gateway_output_terms"
VISUAL_TIMING_FILTER_CONTRACT_V1 = "forward[0,end)+reverse[0,end)"
VISUAL_TIMING_FILTER_CONTRACT = (
    "forward[start,start+end)+reverse[start,start+end)"
)
DIRECT_TIMING_SCHEMA_VERSION = (
    "motion-comic-factory.pet-replica-direct-timing.v1"
)
DIRECT_TIMING_MODEL = "continuous_source_window"
DIRECT_TIMING_FILTER_CONTRACT = (
    "trim[start,start+duration),retime_to_editorial_duration"
)
PIECEWISE_TIMING_SCHEMA_VERSION = (
    "motion-comic-factory.pet-replica-piecewise-timing.v1"
)
PIECEWISE_TIMING_MODEL = "piecewise_monotonic_retime"
PIECEWISE_TIMING_FILTER_CONTRACT = (
    "concat_monotonic_trim_segments,retime_to_segment_output_durations"
)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class PetReplicaLipSyncError(RuntimeError):
    pass


def mouth_blend_mask(face: Any, *, width: int, height: int) -> np.ndarray:
    """Return a feathered mask around YuNet's two mouth landmarks."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise PetReplicaLipSyncError("OpenCV is required for mouth-only lip sync.") from exc
    values = np.asarray(face, dtype=np.float32).reshape(-1)
    if values.size < 14 or width <= 0 or height <= 0:
        raise PetReplicaLipSyncError("YuNet face landmarks are incomplete.")
    face_width = max(1.0, float(values[2]))
    face_height = max(1.0, float(values[3]))
    mouth_left = values[10:12]
    mouth_right = values[12:14]
    center_x = float((mouth_left[0] + mouth_right[0]) / 2)
    center_y = float((mouth_left[1] + mouth_right[1]) / 2 + face_height * 0.01)
    corner_distance = abs(float(mouth_right[0] - mouth_left[0]))
    radius_x = max(
        face_width * MOUTH_BLEND_RADIUS_RATIOS[0],
        corner_distance * MOUTH_BLEND_CORNER_SCALE,
    )
    radius_y = face_height * MOUTH_BLEND_RADIUS_RATIOS[1]
    mask = np.zeros((height, width), dtype=np.float32)
    cv2.ellipse(
        mask,
        (round(center_x), round(center_y)),
        (max(1, round(radius_x)), max(1, round(radius_y))),
        0,
        0,
        360,
        1.0,
        -1,
    )
    feather = max(3, round(min(radius_x, radius_y) * MOUTH_BLEND_FEATHER_RATIO))
    kernel = feather * 2 + 1
    mask = cv2.GaussianBlur(mask, (kernel, kernel), 0)
    return np.clip(mask * MOUTH_BLEND_MAX_ALPHA, 0.0, MOUTH_BLEND_MAX_ALPHA)


def mouth_blend_temporal_alpha(
    frame_index: int,
    *,
    frame_count: int,
    tail_close_frames: int,
) -> float:
    """Fade the mouth composite to the provider's closed mouth at shot end."""
    if (
        isinstance(frame_index, bool)
        or isinstance(frame_count, bool)
        or isinstance(tail_close_frames, bool)
        or not all(isinstance(value, int) for value in (frame_index, frame_count, tail_close_frames))
        or frame_count <= 0
        or not 0 <= frame_index < frame_count
        or not 0 <= tail_close_frames <= 12
    ):
        raise PetReplicaLipSyncError("Mouth blend tail-close frame contract is invalid.")
    if tail_close_frames == 0:
        return 1.0
    remaining = frame_count - 1 - frame_index
    return min(1.0, max(0.0, remaining / tail_close_frames))


def render_replica_mouth_blend(
    raw_video: str | Path,
    lipsynced_video: str | Path,
    output_video: str | Path,
    *,
    detector_path: str | Path,
    frame_transform: str = "none",
    tail_close_frames: int = 0,
) -> Mapping[str, Any]:
    """Composite only Wav2Lip's tracked mouth region over the provider video."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise PetReplicaLipSyncError("OpenCV is required for mouth-only lip sync.") from exc
    raw = _regular_file(Path(raw_video), "raw provider video")
    lipsynced = _regular_file(Path(lipsynced_video), "full-face lip-sync video")
    detector_model = _regular_file(Path(detector_path), "YuNet detector")
    destination = Path(output_video).resolve()
    if destination in {raw.resolve(), lipsynced.resolve()}:
        raise PetReplicaLipSyncError("Mouth blend output must use a new path.")
    transform = str(frame_transform).strip().lower()
    if not _valid_frame_transform(transform):
        raise PetReplicaLipSyncError("Mouth blend frame transform is invalid.")
    mouth_blend_temporal_alpha(
        0,
        frame_count=1,
        tail_close_frames=tail_close_frames,
    )

    lip_capture = cv2.VideoCapture(str(lipsynced))
    frame_count = int(lip_capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(lip_capture.get(cv2.CAP_PROP_FPS))
    width = int(lip_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(lip_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    lip_capture.release()
    if frame_count <= 0 or fps <= 0 or width <= 0 or height <= 0:
        raise PetReplicaLipSyncError("Full-face lip-sync video metadata is invalid.")

    detector = cv2.FaceDetectorYN.create(
        str(detector_model),
        "",
        (width, height),
        MOUTH_BLEND_SCORE_THRESHOLD,
        MOUTH_BLEND_NMS_THRESHOLD,
        MOUTH_BLEND_TOP_K,
    )
    raw_capture = cv2.VideoCapture(str(raw))
    faces: list[np.ndarray | None] = []
    for _ in range(frame_count):
        ok, frame = raw_capture.read()
        if not ok:
            break
        frame = _transform_frame(frame, transform, width=width, height=height)
        _result, detected = detector.detect(frame)
        selected = None
        if detected is not None and len(detected):
            selected = max(detected, key=lambda item: float(item[2] * item[3]))
        faces.append(None if selected is None else np.asarray(selected, dtype=np.float32))
    raw_capture.release()
    if len(faces) != frame_count:
        raise PetReplicaLipSyncError("Raw provider video is shorter than lip-sync output.")
    detection_count = sum(face is not None for face in faces)
    if detection_count < max(3, math.ceil(frame_count * 0.6)):
        raise PetReplicaLipSyncError("YuNet could not track the speaker reliably.")
    tracked = _interpolate_face_landmarks(faces)

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkstemp(dir=destination.parent, prefix=".mouth-blend-", suffix=".mp4")[1]
    )
    staged.unlink(missing_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:.8f}",
        "-i",
        "pipe:0",
        "-i",
        str(lipsynced),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-frames:v",
        str(frame_count),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(staged),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    raw_capture = cv2.VideoCapture(str(raw))
    lip_capture = cv2.VideoCapture(str(lipsynced))
    try:
        assert process.stdin is not None
        for index, face in enumerate(tracked):
            raw_ok, raw_frame = raw_capture.read()
            lip_ok, lip_frame = lip_capture.read()
            if not raw_ok or not lip_ok:
                raise PetReplicaLipSyncError(
                    f"Mouth blend input ended at frame {index}."
                )
            raw_frame = _transform_frame(
                raw_frame, transform, width=width, height=height
            )
            if lip_frame.shape[:2] != (height, width):
                raise PetReplicaLipSyncError("Lip-sync frame dimensions changed.")
            mask = mouth_blend_mask(face, width=width, height=height)
            mask *= mouth_blend_temporal_alpha(
                index,
                frame_count=frame_count,
                tail_close_frames=tail_close_frames,
            )
            mask = mask[..., None]
            blended = np.clip(
                raw_frame.astype(np.float32) * (1.0 - mask)
                + lip_frame.astype(np.float32) * mask,
                0,
                255,
            ).astype(np.uint8)
            process.stdin.write(blended.tobytes())
        process.stdin.close()
        error = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code != 0:
            raise PetReplicaLipSyncError(
                f"FFmpeg mouth blend failed: {error.strip() or return_code}"
            )
        if not is_valid_mp4_file(staged):
            raise PetReplicaLipSyncError("Mouth blend output is not a valid MP4.")
        os.replace(staged, destination)
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    finally:
        raw_capture.release()
        lip_capture.release()
        staged.unlink(missing_ok=True)
    return {
        "frame_count": frame_count,
        "fps": fps,
        "detection_count": detection_count,
        "detection_ratio": detection_count / frame_count,
        "detector_sha256": _sha256(detector_model),
        "tail_close_frames": tail_close_frames,
        "output_sha256": _sha256(destination),
    }


def promote_replica_mouth_blend_candidate(
    plan: PetReplicaPlan,
    candidate: ReplicaCandidate,
    blended_video: str | Path,
    *,
    detector_path: str | Path,
    tail_close_frames: int = 0,
    replace_existing: bool = False,
) -> ReplicaCandidate:
    """Promote a mouth-only composite while preserving full-face Wav2Lip bytes."""
    validate_pet_replica_plan(plan)
    root = Path(plan.output_root).resolve()
    video = _regular_inside(root, candidate.video_path, "candidate video")
    provenance_path = _regular_inside(
        root, candidate.provenance_path, "candidate provenance"
    )
    gateway_report = _regular_inside(
        root, candidate.gateway_report_path, "gateway report"
    )
    current_hash = _sha256(video)
    if candidate.output_sha256 != current_hash:
        raise PetReplicaLipSyncError("Candidate bytes changed before mouth blend promotion.")
    source = _regular_file(Path(blended_video), "mouth blend output")
    if source.resolve() == video.resolve():
        raise PetReplicaLipSyncError("Mouth blend output must not overwrite the candidate.")
    detector = _regular_file(Path(detector_path), "YuNet detector")
    mouth_blend_temporal_alpha(
        0,
        frame_count=1,
        tail_close_frames=tail_close_frames,
    )
    current = _read_json(provenance_path, "candidate provenance")
    drive_audio = _regular_inside(
        root,
        root / "audio" / "drive" / f"{candidate.shot_id}.wav",
        "drive audio",
    )
    drive_audio_hash = _sha256(drive_audio)
    record = current.get("postprocess")
    if not validate_replica_lipsync_provenance(
        root,
        record,
        candidate_sha256=current_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError("A valid full-face lip-sync candidate is required.")
    existing_blend = record.get("mouth_blend")
    detector_hash = _sha256(detector)
    if existing_blend is not None:
        if current_hash == _sha256(source) and existing_blend.get(
            "detector_sha256"
        ) == detector_hash and existing_blend.get("tail_close_frames", 0) == tail_close_frames:
            return _candidate_with_hash(candidate, current_hash)
        if not replace_existing:
            raise PetReplicaLipSyncError("Candidate already has a mouth blend record.")
        _validate_lipsync_media(source, plan, candidate)
        _archive_once(
            video.parent / "postprocess" / "history" / f"{current_hash}.mp4",
            video,
            current_hash,
        )
        output_hash = _sha256(source)
        updated_blend = dict(existing_blend)
        updated_blend["tail_close_frames"] = tail_close_frames
        updated_record = dict(record)
        updated_record["output_sha256"] = output_hash
        updated_record["mouth_blend"] = updated_blend
        updated = dict(current)
        updated["output_sha256"] = output_hash
        updated["postprocess"] = updated_record
        if not validate_replica_lipsync_provenance(
            root,
            updated_record,
            candidate_sha256=output_hash,
            expected_candidate_path=video.relative_to(root).as_posix(),
            drive_audio_sha256=drive_audio_hash,
        ):
            raise PetReplicaLipSyncError("Replacement mouth blend provenance failed validation.")
        _promote_pair(video, source, provenance_path, updated)
        return _candidate_with_hash(candidate, _sha256(video))

    _validate_lipsync_media(source, plan, candidate)
    history = video.parent / "postprocess" / "history" / f"{current_hash}.mp4"
    archived_detector = (
        video.parent
        / "postprocess"
        / "models"
        / detector_hash
        / detector.name
    )
    _archive_once(history, video, current_hash)
    _archive_once(archived_detector, detector, detector_hash)
    output_hash = _sha256(source)
    blend_record = {
        "schema_version": MOUTH_BLEND_SCHEMA_VERSION,
        "engine": MOUTH_BLEND_ENGINE,
        "model": MOUTH_BLEND_MODEL,
        "source_lipsync_path": history.relative_to(root).as_posix(),
        "source_lipsync_sha256": current_hash,
        "detector_path": archived_detector.relative_to(root).as_posix(),
        "detector_name": detector.name,
        "detector_sha256": detector_hash,
        "score_threshold": MOUTH_BLEND_SCORE_THRESHOLD,
        "nms_threshold": MOUTH_BLEND_NMS_THRESHOLD,
        "top_k": MOUTH_BLEND_TOP_K,
        "corner_scale": MOUTH_BLEND_CORNER_SCALE,
        "radius_ratios": MOUTH_BLEND_RADIUS_RATIOS,
        "max_alpha": MOUTH_BLEND_MAX_ALPHA,
        "feather_ratio": MOUTH_BLEND_FEATHER_RATIO,
        "tail_close_frames": tail_close_frames,
    }
    updated_record = dict(record)
    updated_record["output_sha256"] = output_hash
    updated_record["mouth_blend"] = blend_record
    updated = dict(current)
    updated["output_sha256"] = output_hash
    updated["postprocess"] = updated_record
    if not validate_replica_lipsync_provenance(
        root,
        updated_record,
        candidate_sha256=output_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError("Mouth blend provenance failed validation.")
    _promote_pair(video, source, provenance_path, updated)
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        video,
        provenance_path,
        gateway_report,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        _sha256(video),
    )


def promote_replica_lipsync_candidate(
    plan: PetReplicaPlan,
    candidate: ReplicaCandidate,
    lipsynced_video: str | Path,
    *,
    repository_commit: str,
    checkpoint_path: str | Path,
    face_detector_path: str | Path,
    replace_existing: bool = False,
    frame_transform: str = "none",
) -> ReplicaCandidate:
    """Promote a reviewed local lip-sync render while retaining provider bytes."""
    validate_pet_replica_plan(plan)
    root = Path(plan.output_root).resolve()
    video = _regular_inside(root, candidate.video_path, "candidate video")
    provenance_path = _regular_inside(
        root, candidate.provenance_path, "candidate provenance"
    )
    gateway_report = _regular_inside(
        root, candidate.gateway_report_path, "gateway report"
    )
    if candidate.output_sha256 != _sha256(video):
        raise PetReplicaLipSyncError("Candidate bytes changed before lip-sync promotion.")
    source = _regular_file(Path(lipsynced_video), "lip-sync output")
    if source.resolve() == video.resolve():
        raise PetReplicaLipSyncError("Lip-sync output must not overwrite the candidate in place.")
    checkpoint = _regular_file(Path(checkpoint_path), "Wav2Lip checkpoint")
    detector = _regular_file(Path(face_detector_path), "face detector checkpoint")
    commit = str(repository_commit).strip().lower()
    if not _COMMIT.fullmatch(commit):
        raise PetReplicaLipSyncError("Wav2Lip repository commit must be a full SHA-1.")
    transform = str(frame_transform).strip().lower()
    if not _valid_frame_transform(transform):
        raise PetReplicaLipSyncError(
            "Lip-sync frame transform must be none, hflip, or a bounded crop/scale."
        )

    current = _read_json(provenance_path, "candidate provenance")
    current_hash = _sha256(video)
    drive_audio = _regular_inside(
        root,
        root / "audio" / "drive" / f"{candidate.shot_id}.wav",
        "drive audio",
    )
    drive_audio_hash = _sha256(drive_audio)
    existing = current.get("postprocess")
    if existing is not None:
        if not validate_replica_lipsync_provenance(
            root,
            existing,
            candidate_sha256=current_hash,
            expected_candidate_path=video.relative_to(root).as_posix(),
            drive_audio_sha256=drive_audio_hash,
        ):
            raise PetReplicaLipSyncError("Existing lip-sync provenance is stale.")
        same_postprocess = bool(
            current_hash == _sha256(source)
            and existing.get("repository_commit") == commit
            and existing.get("checkpoint_sha256") == _sha256(checkpoint)
            and existing.get("face_detector_sha256") == _sha256(detector)
            and existing.get("frame_transform", "none") == transform
        )
        if same_postprocess:
            return _candidate_with_hash(candidate, current_hash)
        if not replace_existing:
            raise PetReplicaLipSyncError(
                "Candidate already has a different lip-sync postprocess record."
            )
        _validate_lipsync_media(source, plan, candidate)
        history = (
            video.parent
            / "postprocess"
            / "history"
            / f"{current_hash}.mp4"
        )
        _archive_once(history, video, current_hash)
        output_hash = _sha256(source)
        record = dict(existing)
        record.update(
            {
                "repository_commit": commit,
                "checkpoint_name": checkpoint.name,
                "checkpoint_sha256": _sha256(checkpoint),
                "face_detector_name": detector.name,
                "face_detector_sha256": _sha256(detector),
                "frame_transform": transform,
                "output_sha256": output_hash,
            }
        )
        updated = dict(current)
        updated["output_sha256"] = output_hash
        updated["postprocess"] = record
        if not validate_replica_lipsync_provenance(
            root,
            record,
            candidate_sha256=output_hash,
            expected_candidate_path=video.relative_to(root).as_posix(),
            drive_audio_sha256=drive_audio_hash,
        ):
            raise PetReplicaLipSyncError("Replacement lip-sync provenance is invalid.")
        _promote_pair(video, source, provenance_path, updated)
        return _candidate_with_hash(candidate, _sha256(video))

    if current.get("output_sha256") != current_hash:
        raise PetReplicaLipSyncError("Provider provenance no longer matches candidate bytes.")
    if current.get("shot_id") != candidate.shot_id or current.get(
        "candidate_number"
    ) != candidate.candidate_number:
        raise PetReplicaLipSyncError("Provider provenance identifies another candidate.")
    if current.get("drive_audio_sha256") != drive_audio_hash:
        raise PetReplicaLipSyncError("Drive audio no longer matches provider provenance.")

    _validate_lipsync_media(source, plan, candidate)
    output_hash = _sha256(source)
    raw_dir = (
        video.parent
        / "raw"
        / f"candidate_{candidate.candidate_number:02d}"
        / current_hash
    )
    raw_video = raw_dir / "gateway_output.mp4"
    raw_provenance = raw_dir / "gateway.provenance.json"
    _archive_once(raw_video, video, current_hash)
    _archive_once(raw_provenance, provenance_path, _sha256(provenance_path))

    record = {
        "schema_version": LIPSYNC_SCHEMA_VERSION,
        "engine": LIPSYNC_ENGINE,
        "model": LIPSYNC_MODEL,
        "repository_commit": commit,
        "source_candidate_path": raw_video.relative_to(root).as_posix(),
        "source_candidate_sha256": current_hash,
        "source_provenance_path": raw_provenance.relative_to(root).as_posix(),
        "source_provenance_sha256": _sha256(raw_provenance),
        "drive_audio_path": drive_audio.relative_to(root).as_posix(),
        "drive_audio_sha256": drive_audio_hash,
        "checkpoint_name": checkpoint.name,
        "checkpoint_sha256": _sha256(checkpoint),
        "face_detector_name": detector.name,
        "face_detector_sha256": _sha256(detector),
        "frame_transform": transform,
        "face_padding": [0, 10, 0, 0],
        "resize_factor": 1,
        "output_path": video.relative_to(root).as_posix(),
        "output_sha256": output_hash,
        "license_scope": LIPSYNC_LICENSE_SCOPE,
    }
    updated = dict(current)
    updated["output_sha256"] = output_hash
    updated["postprocess"] = record
    if not validate_replica_lipsync_provenance(
        root,
        record,
        candidate_sha256=output_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError("Lip-sync provenance failed validation.")
    _promote_pair(video, source, provenance_path, updated)
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        video,
        provenance_path,
        gateway_report,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        _sha256(video),
    )


def promote_replica_visual_timing_candidate(
    plan: PetReplicaPlan,
    candidate: ReplicaCandidate,
    timed_video: str | Path,
    *,
    ffmpeg_version: str,
    source_start_s: float = 0.0,
    forward_end_s: float,
) -> ReplicaCandidate:
    """Promote a selected cat-mouth timing window while retaining provider bytes."""
    validate_pet_replica_plan(plan)
    root = Path(plan.output_root).resolve()
    video = _regular_inside(root, candidate.video_path, "candidate video")
    provenance_path = _regular_inside(
        root, candidate.provenance_path, "candidate provenance"
    )
    gateway_report = _regular_inside(
        root, candidate.gateway_report_path, "gateway report"
    )
    if candidate.output_sha256 != _sha256(video):
        raise PetReplicaLipSyncError(
            "Candidate bytes changed before visual-timing promotion."
        )
    source = _regular_file(Path(timed_video), "visual-timing output")
    if source.resolve() == video.resolve():
        raise PetReplicaLipSyncError(
            "Visual-timing output must not overwrite the candidate in place."
        )
    version = str(ffmpeg_version).strip()
    if not version.startswith("ffmpeg version "):
        raise PetReplicaLipSyncError("A complete FFmpeg version line is required.")
    if (
        isinstance(source_start_s, bool)
        or not isinstance(source_start_s, (int, float))
        or not math.isfinite(float(source_start_s))
        or float(source_start_s) < 0
        or isinstance(forward_end_s, bool)
        or not isinstance(forward_end_s, (int, float))
        or not math.isfinite(float(forward_end_s))
        or float(forward_end_s) <= 0
        or float(forward_end_s) * 2 > candidate.editorial_duration_s + 0.25
        or float(source_start_s) + float(forward_end_s)
        > candidate.generation_duration_s + 1 / plan.fps
    ):
        raise PetReplicaLipSyncError("Visual-timing source window is invalid.")

    current = _read_json(provenance_path, "candidate provenance")
    if current.get("postprocess") is not None:
        raise PetReplicaLipSyncError(
            "Candidate already has a postprocess record."
        )
    current_hash = _sha256(video)
    if current.get("output_sha256") != current_hash:
        raise PetReplicaLipSyncError(
            "Provider provenance no longer matches candidate bytes."
        )
    if current.get("shot_id") != candidate.shot_id or current.get(
        "candidate_number"
    ) != candidate.candidate_number:
        raise PetReplicaLipSyncError("Provider provenance identifies another candidate.")
    drive_audio = _regular_inside(
        root,
        root / "audio" / "drive" / f"{candidate.shot_id}.wav",
        "drive audio",
    )
    drive_audio_hash = _sha256(drive_audio)
    if current.get("drive_audio_sha256") != drive_audio_hash:
        raise PetReplicaLipSyncError(
            "Drive audio no longer matches provider provenance."
        )
    _validate_visual_timing_media(source, plan, candidate)

    raw_dir = (
        video.parent
        / "raw"
        / f"candidate_{candidate.candidate_number:02d}"
        / current_hash
    )
    raw_video = raw_dir / "gateway_output.mp4"
    raw_provenance = raw_dir / "gateway.provenance.json"
    _archive_once(raw_video, video, current_hash)
    _archive_once(raw_provenance, provenance_path, _sha256(provenance_path))
    output_hash = _sha256(source)
    record = {
        "schema_version": VISUAL_TIMING_SCHEMA_VERSION,
        "engine": VISUAL_TIMING_ENGINE,
        "model": VISUAL_TIMING_MODEL,
        "ffmpeg_version": version,
        "filter_contract": VISUAL_TIMING_FILTER_CONTRACT,
        "source_start_s": float(source_start_s),
        "forward_end_s": float(forward_end_s),
        "source_candidate_path": raw_video.relative_to(root).as_posix(),
        "source_candidate_sha256": current_hash,
        "source_provenance_path": raw_provenance.relative_to(root).as_posix(),
        "source_provenance_sha256": _sha256(raw_provenance),
        "drive_audio_path": drive_audio.relative_to(root).as_posix(),
        "drive_audio_sha256": drive_audio_hash,
        "output_path": video.relative_to(root).as_posix(),
        "output_sha256": output_hash,
        "license_scope": VISUAL_TIMING_LICENSE_SCOPE,
    }
    updated = dict(current)
    updated["output_sha256"] = output_hash
    updated["postprocess"] = record
    if not validate_replica_postprocess_provenance(
        root,
        record,
        candidate_sha256=output_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError("Visual-timing provenance failed validation.")
    _promote_pair(video, source, provenance_path, updated)
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        video,
        provenance_path,
        gateway_report,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        _sha256(video),
    )


def promote_replica_direct_timing_candidate(
    plan: PetReplicaPlan,
    candidate: ReplicaCandidate,
    timed_video: str | Path,
    *,
    ffmpeg_version: str,
    source_start_s: float,
    source_duration_s: float,
) -> ReplicaCandidate:
    """Promote one continuous provider window without reversing its motion."""
    validate_pet_replica_plan(plan)
    root = Path(plan.output_root).resolve()
    video = _regular_inside(root, candidate.video_path, "candidate video")
    provenance_path = _regular_inside(
        root, candidate.provenance_path, "candidate provenance"
    )
    gateway_report = _regular_inside(
        root, candidate.gateway_report_path, "gateway report"
    )
    if candidate.output_sha256 != _sha256(video):
        raise PetReplicaLipSyncError(
            "Candidate bytes changed before direct-timing promotion."
        )
    source = _regular_file(Path(timed_video), "direct-timing output")
    if source.resolve() == video.resolve():
        raise PetReplicaLipSyncError(
            "Direct-timing output must not overwrite the candidate in place."
        )
    version = str(ffmpeg_version).strip()
    if not version.startswith("ffmpeg version "):
        raise PetReplicaLipSyncError("A complete FFmpeg version line is required.")
    if (
        isinstance(source_start_s, bool)
        or not isinstance(source_start_s, (int, float))
        or not math.isfinite(float(source_start_s))
        or float(source_start_s) < 0
        or isinstance(source_duration_s, bool)
        or not isinstance(source_duration_s, (int, float))
        or not math.isfinite(float(source_duration_s))
        or float(source_duration_s) <= 0
        or float(source_start_s) + float(source_duration_s)
        > candidate.generation_duration_s + 1 / plan.fps
    ):
        raise PetReplicaLipSyncError("Direct-timing source window is invalid.")

    current = _read_json(provenance_path, "candidate provenance")
    if current.get("postprocess") is not None:
        raise PetReplicaLipSyncError(
            "Candidate already has a postprocess record."
        )
    current_hash = _sha256(video)
    if current.get("output_sha256") != current_hash:
        raise PetReplicaLipSyncError(
            "Provider provenance no longer matches candidate bytes."
        )
    if current.get("shot_id") != candidate.shot_id or current.get(
        "candidate_number"
    ) != candidate.candidate_number:
        raise PetReplicaLipSyncError("Provider provenance identifies another candidate.")
    drive_audio = _regular_inside(
        root,
        root / "audio" / "drive" / f"{candidate.shot_id}.wav",
        "drive audio",
    )
    drive_audio_hash = _sha256(drive_audio)
    if current.get("drive_audio_sha256") != drive_audio_hash:
        raise PetReplicaLipSyncError(
            "Drive audio no longer matches provider provenance."
        )
    _validate_visual_timing_media(source, plan, candidate)

    raw_dir = (
        video.parent
        / "raw"
        / f"candidate_{candidate.candidate_number:02d}"
        / current_hash
    )
    raw_video = raw_dir / "gateway_output.mp4"
    raw_provenance = raw_dir / "gateway.provenance.json"
    _archive_once(raw_video, video, current_hash)
    _archive_once(raw_provenance, provenance_path, _sha256(provenance_path))
    output_hash = _sha256(source)
    record = {
        "schema_version": DIRECT_TIMING_SCHEMA_VERSION,
        "engine": VISUAL_TIMING_ENGINE,
        "model": DIRECT_TIMING_MODEL,
        "ffmpeg_version": version,
        "filter_contract": DIRECT_TIMING_FILTER_CONTRACT,
        "source_start_s": float(source_start_s),
        "source_duration_s": float(source_duration_s),
        "source_candidate_path": raw_video.relative_to(root).as_posix(),
        "source_candidate_sha256": current_hash,
        "source_provenance_path": raw_provenance.relative_to(root).as_posix(),
        "source_provenance_sha256": _sha256(raw_provenance),
        "drive_audio_path": drive_audio.relative_to(root).as_posix(),
        "drive_audio_sha256": drive_audio_hash,
        "output_path": video.relative_to(root).as_posix(),
        "output_sha256": output_hash,
        "license_scope": VISUAL_TIMING_LICENSE_SCOPE,
    }
    updated = dict(current)
    updated["output_sha256"] = output_hash
    updated["postprocess"] = record
    if not validate_replica_postprocess_provenance(
        root,
        record,
        candidate_sha256=output_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError("Direct-timing provenance failed validation.")
    _promote_pair(video, source, provenance_path, updated)
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        video,
        provenance_path,
        gateway_report,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        _sha256(video),
    )


def promote_replica_piecewise_timing_candidate(
    plan: PetReplicaPlan,
    candidate: ReplicaCandidate,
    timed_video: str | Path,
    *,
    ffmpeg_version: str,
    segments: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> ReplicaCandidate:
    """Promote contiguous forward-only segments with explicit output timing."""
    validate_pet_replica_plan(plan)
    root = Path(plan.output_root).resolve()
    video = _regular_inside(root, candidate.video_path, "candidate video")
    provenance_path = _regular_inside(
        root, candidate.provenance_path, "candidate provenance"
    )
    gateway_report = _regular_inside(
        root, candidate.gateway_report_path, "gateway report"
    )
    if candidate.output_sha256 != _sha256(video):
        raise PetReplicaLipSyncError(
            "Candidate bytes changed before piecewise-timing promotion."
        )
    source = _regular_file(Path(timed_video), "piecewise-timing output")
    if source.resolve() == video.resolve():
        raise PetReplicaLipSyncError(
            "Piecewise-timing output must not overwrite the candidate in place."
        )
    version = str(ffmpeg_version).strip()
    if not version.startswith("ffmpeg version "):
        raise PetReplicaLipSyncError("A complete FFmpeg version line is required.")
    normalized = _normalize_piecewise_timing_segments(
        segments,
        editorial_duration_s=candidate.editorial_duration_s,
        generation_duration_s=candidate.generation_duration_s,
        fps=plan.fps,
    )

    current = _read_json(provenance_path, "candidate provenance")
    if current.get("postprocess") is not None:
        raise PetReplicaLipSyncError("Candidate already has a postprocess record.")
    current_hash = _sha256(video)
    if current.get("output_sha256") != current_hash:
        raise PetReplicaLipSyncError(
            "Provider provenance no longer matches candidate bytes."
        )
    if current.get("shot_id") != candidate.shot_id or current.get(
        "candidate_number"
    ) != candidate.candidate_number:
        raise PetReplicaLipSyncError("Provider provenance identifies another candidate.")
    drive_audio = _regular_inside(
        root,
        root / "audio" / "drive" / f"{candidate.shot_id}.wav",
        "drive audio",
    )
    drive_audio_hash = _sha256(drive_audio)
    if current.get("drive_audio_sha256") != drive_audio_hash:
        raise PetReplicaLipSyncError(
            "Drive audio no longer matches provider provenance."
        )
    _validate_visual_timing_media(source, plan, candidate)

    raw_dir = (
        video.parent
        / "raw"
        / f"candidate_{candidate.candidate_number:02d}"
        / current_hash
    )
    raw_video = raw_dir / "gateway_output.mp4"
    raw_provenance = raw_dir / "gateway.provenance.json"
    _archive_once(raw_video, video, current_hash)
    _archive_once(raw_provenance, provenance_path, _sha256(provenance_path))
    output_hash = _sha256(source)
    record = {
        "schema_version": PIECEWISE_TIMING_SCHEMA_VERSION,
        "engine": VISUAL_TIMING_ENGINE,
        "model": PIECEWISE_TIMING_MODEL,
        "ffmpeg_version": version,
        "filter_contract": PIECEWISE_TIMING_FILTER_CONTRACT,
        "segments": normalized,
        "editorial_duration_s": float(candidate.editorial_duration_s),
        "source_candidate_path": raw_video.relative_to(root).as_posix(),
        "source_candidate_sha256": current_hash,
        "source_provenance_path": raw_provenance.relative_to(root).as_posix(),
        "source_provenance_sha256": _sha256(raw_provenance),
        "drive_audio_path": drive_audio.relative_to(root).as_posix(),
        "drive_audio_sha256": drive_audio_hash,
        "output_path": video.relative_to(root).as_posix(),
        "output_sha256": output_hash,
        "license_scope": VISUAL_TIMING_LICENSE_SCOPE,
    }
    updated = dict(current)
    updated["output_sha256"] = output_hash
    updated["postprocess"] = record
    if not validate_replica_postprocess_provenance(
        root,
        record,
        candidate_sha256=output_hash,
        expected_candidate_path=video.relative_to(root).as_posix(),
        drive_audio_sha256=drive_audio_hash,
    ):
        raise PetReplicaLipSyncError(
            "Piecewise-timing provenance failed validation."
        )
    _promote_pair(video, source, provenance_path, updated)
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        video,
        provenance_path,
        gateway_report,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        _sha256(video),
    )


def _normalize_piecewise_timing_segments(
    segments: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    editorial_duration_s: float,
    generation_duration_s: float,
    fps: int,
) -> list[dict[str, float]]:
    if not isinstance(segments, (tuple, list)) or not 2 <= len(segments) <= 8:
        raise PetReplicaLipSyncError(
            "Piecewise timing requires two to eight segments."
        )
    normalized: list[dict[str, float]] = []
    previous_end: float | None = None
    output_total = 0.0
    for value in segments:
        if not isinstance(value, Mapping) or set(value) != {
            "source_start_s",
            "source_duration_s",
            "output_duration_s",
        }:
            raise PetReplicaLipSyncError("Piecewise timing segment is invalid.")
        numbers: dict[str, float] = {}
        for key in ("source_start_s", "source_duration_s", "output_duration_s"):
            raw = value.get(key)
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or not math.isfinite(float(raw))
            ):
                raise PetReplicaLipSyncError("Piecewise timing segment is invalid.")
            numbers[key] = float(raw)
        if (
            numbers["source_start_s"] < 0
            or numbers["source_duration_s"] <= 0
            or numbers["output_duration_s"] <= 0
            or (
                previous_end is not None
                and not math.isclose(
                    numbers["source_start_s"],
                    previous_end,
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
            )
        ):
            raise PetReplicaLipSyncError(
                "Piecewise timing segments must be positive and contiguous."
            )
        previous_end = numbers["source_start_s"] + numbers["source_duration_s"]
        output_total += numbers["output_duration_s"]
        normalized.append(numbers)
    if (
        previous_end is None
        or previous_end > generation_duration_s + 1 / fps
        or not math.isclose(
            output_total,
            editorial_duration_s,
            rel_tol=0.0,
            abs_tol=1 / fps,
        )
    ):
        raise PetReplicaLipSyncError(
            "Piecewise timing segments do not match the media duration."
        )
    return normalized


def validate_replica_postprocess_provenance(
    root: str | Path,
    value: Any,
    *,
    candidate_sha256: str,
    expected_candidate_path: str,
    drive_audio_sha256: str,
) -> bool:
    """Validate either an audio-driven human lip-sync or visual cat-mouth timing."""
    if isinstance(value, Mapping) and value.get("schema_version") in {
        VISUAL_TIMING_SCHEMA_VERSION_V1,
        VISUAL_TIMING_SCHEMA_VERSION,
    }:
        return _validate_replica_visual_timing_provenance(
            root,
            value,
            candidate_sha256=candidate_sha256,
            expected_candidate_path=expected_candidate_path,
            drive_audio_sha256=drive_audio_sha256,
        )
    if (
        isinstance(value, Mapping)
        and value.get("schema_version") == DIRECT_TIMING_SCHEMA_VERSION
    ):
        return _validate_replica_direct_timing_provenance(
            root,
            value,
            candidate_sha256=candidate_sha256,
            expected_candidate_path=expected_candidate_path,
            drive_audio_sha256=drive_audio_sha256,
        )
    if (
        isinstance(value, Mapping)
        and value.get("schema_version") == PIECEWISE_TIMING_SCHEMA_VERSION
    ):
        return _validate_replica_piecewise_timing_provenance(
            root,
            value,
            candidate_sha256=candidate_sha256,
            expected_candidate_path=expected_candidate_path,
            drive_audio_sha256=drive_audio_sha256,
        )
    return validate_replica_lipsync_provenance(
        root,
        value,
        candidate_sha256=candidate_sha256,
        expected_candidate_path=expected_candidate_path,
        drive_audio_sha256=drive_audio_sha256,
    )


def _validate_replica_visual_timing_provenance(
    root: str | Path,
    value: Mapping[str, Any],
    *,
    candidate_sha256: str,
    expected_candidate_path: str,
    drive_audio_sha256: str,
) -> bool:
    root_path = Path(root).resolve()
    keys = {
        "schema_version",
        "engine",
        "model",
        "ffmpeg_version",
        "filter_contract",
        "forward_end_s",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_provenance_path",
        "source_provenance_sha256",
        "drive_audio_path",
        "drive_audio_sha256",
        "output_path",
        "output_sha256",
        "license_scope",
    }
    schema_version = value.get("schema_version")
    source_start = value.get("source_start_s", 0.0)
    if schema_version == VISUAL_TIMING_SCHEMA_VERSION:
        keys = keys | {"source_start_s"}
        filter_contract = VISUAL_TIMING_FILTER_CONTRACT
    elif schema_version == VISUAL_TIMING_SCHEMA_VERSION_V1:
        filter_contract = VISUAL_TIMING_FILTER_CONTRACT_V1
    else:
        return False
    forward_end = value.get("forward_end_s")
    if not (
        set(value) == keys
        and value.get("engine") == VISUAL_TIMING_ENGINE
        and value.get("model") == VISUAL_TIMING_MODEL
        and str(value.get("ffmpeg_version", "")).startswith("ffmpeg version ")
        and value.get("filter_contract") == filter_contract
        and isinstance(source_start, (int, float))
        and not isinstance(source_start, bool)
        and math.isfinite(float(source_start))
        and float(source_start) >= 0
        and isinstance(forward_end, (int, float))
        and not isinstance(forward_end, bool)
        and math.isfinite(float(forward_end))
        and float(forward_end) > 0
        and all(
            _SHA256.fullmatch(str(value.get(key, "")))
            for key in (
                "source_candidate_sha256",
                "source_provenance_sha256",
                "drive_audio_sha256",
                "output_sha256",
            )
        )
        and value.get("output_path") == expected_candidate_path
        and value.get("output_sha256") == candidate_sha256
        and value.get("drive_audio_sha256") == drive_audio_sha256
        and value.get("license_scope") == VISUAL_TIMING_LICENSE_SCOPE
    ):
        return False
    candidate_parts = Path(expected_candidate_path).parts
    if len(candidate_parts) != 3 or candidate_parts[0] != "shots":
        return False
    candidate_name = candidate_parts[2]
    if re.fullmatch(r"candidate_(0[1-3])\.mp4", candidate_name) is None:
        return False
    raw_hash = str(value["source_candidate_sha256"])
    raw_dir = (
        Path("shots")
        / candidate_parts[1]
        / "raw"
        / candidate_name[:-4]
        / raw_hash
    )
    if value.get("source_candidate_path") != (
        raw_dir / "gateway_output.mp4"
    ).as_posix() or value.get("source_provenance_path") != (
        raw_dir / "gateway.provenance.json"
    ).as_posix():
        return False
    expected_audio = Path("audio") / "drive" / f"{candidate_parts[1]}.wav"
    if value.get("drive_audio_path") != expected_audio.as_posix():
        return False
    raw_video = _validated_relative(root_path, value["source_candidate_path"])
    raw_provenance = _validated_relative(
        root_path, value["source_provenance_path"]
    )
    drive_audio = _validated_relative(root_path, value["drive_audio_path"])
    if raw_video is None or raw_provenance is None or drive_audio is None:
        return False
    if not (
        _sha256(raw_video) == raw_hash
        and _sha256(raw_provenance) == value["source_provenance_sha256"]
        and _sha256(drive_audio) == drive_audio_sha256
    ):
        return False
    try:
        raw_payload = json.loads(raw_provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(raw_payload, Mapping)
        and "postprocess" not in raw_payload
        and raw_payload.get("output_sha256") == raw_hash
        and raw_payload.get("output_path") == expected_candidate_path
    )


def _validate_replica_direct_timing_provenance(
    root: str | Path,
    value: Mapping[str, Any],
    *,
    candidate_sha256: str,
    expected_candidate_path: str,
    drive_audio_sha256: str,
) -> bool:
    root_path = Path(root).resolve()
    keys = {
        "schema_version",
        "engine",
        "model",
        "ffmpeg_version",
        "filter_contract",
        "source_start_s",
        "source_duration_s",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_provenance_path",
        "source_provenance_sha256",
        "drive_audio_path",
        "drive_audio_sha256",
        "output_path",
        "output_sha256",
        "license_scope",
    }
    source_start = value.get("source_start_s")
    source_duration = value.get("source_duration_s")
    if not (
        set(value) == keys
        and value.get("schema_version") == DIRECT_TIMING_SCHEMA_VERSION
        and value.get("engine") == VISUAL_TIMING_ENGINE
        and value.get("model") == DIRECT_TIMING_MODEL
        and str(value.get("ffmpeg_version", "")).startswith("ffmpeg version ")
        and value.get("filter_contract") == DIRECT_TIMING_FILTER_CONTRACT
        and isinstance(source_start, (int, float))
        and not isinstance(source_start, bool)
        and math.isfinite(float(source_start))
        and float(source_start) >= 0
        and isinstance(source_duration, (int, float))
        and not isinstance(source_duration, bool)
        and math.isfinite(float(source_duration))
        and float(source_duration) > 0
        and all(
            _SHA256.fullmatch(str(value.get(key, "")))
            for key in (
                "source_candidate_sha256",
                "source_provenance_sha256",
                "drive_audio_sha256",
                "output_sha256",
            )
        )
        and value.get("output_path") == expected_candidate_path
        and value.get("output_sha256") == candidate_sha256
        and value.get("drive_audio_sha256") == drive_audio_sha256
        and value.get("license_scope") == VISUAL_TIMING_LICENSE_SCOPE
    ):
        return False
    candidate_parts = Path(expected_candidate_path).parts
    if len(candidate_parts) != 3 or candidate_parts[0] != "shots":
        return False
    candidate_name = candidate_parts[2]
    if re.fullmatch(r"candidate_(0[1-3])\.mp4", candidate_name) is None:
        return False
    raw_hash = str(value["source_candidate_sha256"])
    raw_dir = (
        Path("shots")
        / candidate_parts[1]
        / "raw"
        / candidate_name[:-4]
        / raw_hash
    )
    if value.get("source_candidate_path") != (
        raw_dir / "gateway_output.mp4"
    ).as_posix() or value.get("source_provenance_path") != (
        raw_dir / "gateway.provenance.json"
    ).as_posix():
        return False
    expected_audio = Path("audio") / "drive" / f"{candidate_parts[1]}.wav"
    if value.get("drive_audio_path") != expected_audio.as_posix():
        return False
    raw_video = _validated_relative(root_path, value["source_candidate_path"])
    raw_provenance = _validated_relative(
        root_path, value["source_provenance_path"]
    )
    drive_audio = _validated_relative(root_path, value["drive_audio_path"])
    if raw_video is None or raw_provenance is None or drive_audio is None:
        return False
    if not (
        _sha256(raw_video) == raw_hash
        and _sha256(raw_provenance) == value["source_provenance_sha256"]
        and _sha256(drive_audio) == drive_audio_sha256
    ):
        return False
    try:
        raw_payload = json.loads(raw_provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(raw_payload, Mapping)
        and "postprocess" not in raw_payload
        and raw_payload.get("output_sha256") == raw_hash
        and raw_payload.get("output_path") == expected_candidate_path
    )


def _validate_replica_piecewise_timing_provenance(
    root: str | Path,
    value: Mapping[str, Any],
    *,
    candidate_sha256: str,
    expected_candidate_path: str,
    drive_audio_sha256: str,
) -> bool:
    root_path = Path(root).resolve()
    keys = {
        "schema_version",
        "engine",
        "model",
        "ffmpeg_version",
        "filter_contract",
        "segments",
        "editorial_duration_s",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_provenance_path",
        "source_provenance_sha256",
        "drive_audio_path",
        "drive_audio_sha256",
        "output_path",
        "output_sha256",
        "license_scope",
    }
    editorial_duration = value.get("editorial_duration_s")
    if not (
        set(value) == keys
        and value.get("schema_version") == PIECEWISE_TIMING_SCHEMA_VERSION
        and value.get("engine") == VISUAL_TIMING_ENGINE
        and value.get("model") == PIECEWISE_TIMING_MODEL
        and str(value.get("ffmpeg_version", "")).startswith("ffmpeg version ")
        and value.get("filter_contract") == PIECEWISE_TIMING_FILTER_CONTRACT
        and isinstance(editorial_duration, (int, float))
        and not isinstance(editorial_duration, bool)
        and math.isfinite(float(editorial_duration))
        and float(editorial_duration) > 0
        and all(
            _SHA256.fullmatch(str(value.get(key, "")))
            for key in (
                "source_candidate_sha256",
                "source_provenance_sha256",
                "drive_audio_sha256",
                "output_sha256",
            )
        )
        and value.get("output_path") == expected_candidate_path
        and value.get("output_sha256") == candidate_sha256
        and value.get("drive_audio_sha256") == drive_audio_sha256
        and value.get("license_scope") == VISUAL_TIMING_LICENSE_SCOPE
    ):
        return False
    segments = value.get("segments")
    try:
        if not isinstance(segments, list) or not segments:
            return False
        final_segment = segments[-1]
        source_end = float(final_segment["source_start_s"]) + float(
            final_segment["source_duration_s"]
        )
        normalized = _normalize_piecewise_timing_segments(
            segments,
            editorial_duration_s=float(editorial_duration),
            generation_duration_s=source_end,
            fps=30,
        )
    except (KeyError, TypeError, ValueError, PetReplicaLipSyncError):
        return False
    if normalized != segments:
        return False
    candidate_parts = Path(expected_candidate_path).parts
    if len(candidate_parts) != 3 or candidate_parts[0] != "shots":
        return False
    candidate_name = candidate_parts[2]
    if re.fullmatch(r"candidate_(0[1-3])\.mp4", candidate_name) is None:
        return False
    raw_hash = str(value["source_candidate_sha256"])
    raw_dir = (
        Path("shots")
        / candidate_parts[1]
        / "raw"
        / candidate_name[:-4]
        / raw_hash
    )
    if value.get("source_candidate_path") != (
        raw_dir / "gateway_output.mp4"
    ).as_posix() or value.get("source_provenance_path") != (
        raw_dir / "gateway.provenance.json"
    ).as_posix():
        return False
    expected_audio = Path("audio") / "drive" / f"{candidate_parts[1]}.wav"
    if value.get("drive_audio_path") != expected_audio.as_posix():
        return False
    raw_video = _validated_relative(root_path, value["source_candidate_path"])
    raw_provenance = _validated_relative(
        root_path, value["source_provenance_path"]
    )
    drive_audio = _validated_relative(root_path, value["drive_audio_path"])
    if raw_video is None or raw_provenance is None or drive_audio is None:
        return False
    if not (
        _sha256(raw_video) == raw_hash
        and _sha256(raw_provenance) == value["source_provenance_sha256"]
        and _sha256(drive_audio) == drive_audio_sha256
    ):
        return False
    try:
        raw_payload = json.loads(raw_provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    raw_editorial = raw_payload.get("editorial_duration_s")
    return bool(
        isinstance(raw_payload, Mapping)
        and "postprocess" not in raw_payload
        and raw_payload.get("output_sha256") == raw_hash
        and raw_payload.get("output_path") == expected_candidate_path
        and isinstance(raw_editorial, (int, float))
        and not isinstance(raw_editorial, bool)
        and math.isclose(
            float(raw_editorial),
            float(editorial_duration),
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    )


def validate_replica_lipsync_provenance(
    root: str | Path,
    value: Any,
    *,
    candidate_sha256: str,
    expected_candidate_path: str,
    drive_audio_sha256: str,
) -> bool:
    root_path = Path(root).resolve()
    keys = {
        "schema_version",
        "engine",
        "model",
        "repository_commit",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_provenance_path",
        "source_provenance_sha256",
        "drive_audio_path",
        "drive_audio_sha256",
        "checkpoint_name",
        "checkpoint_sha256",
        "face_detector_name",
        "face_detector_sha256",
        "face_padding",
        "resize_factor",
        "output_path",
        "output_sha256",
        "license_scope",
    }
    allowed_keys = (
        keys,
        keys | {"frame_transform"},
        keys | {"mouth_blend"},
        keys | {"frame_transform", "mouth_blend"},
    )
    if not isinstance(value, Mapping) or set(value) not in allowed_keys:
        return False
    if not (
        value.get("schema_version") == LIPSYNC_SCHEMA_VERSION
        and value.get("engine") == LIPSYNC_ENGINE
        and value.get("model") == LIPSYNC_MODEL
        and _COMMIT.fullmatch(str(value.get("repository_commit", "")))
        and value.get("checkpoint_name") == "wav2lip_gan.pth"
        and value.get("face_detector_name") == "s3fd.pth"
        and _valid_frame_transform(value.get("frame_transform", "none"))
        and all(
            _SHA256.fullmatch(str(value.get(key, "")))
            for key in (
                "source_candidate_sha256",
                "source_provenance_sha256",
                "drive_audio_sha256",
                "checkpoint_sha256",
                "face_detector_sha256",
                "output_sha256",
            )
        )
        and value.get("face_padding") == [0, 10, 0, 0]
        and value.get("resize_factor") == 1
        and value.get("output_path") == expected_candidate_path
        and value.get("output_sha256") == candidate_sha256
        and value.get("drive_audio_sha256") == drive_audio_sha256
        and value.get("license_scope") == LIPSYNC_LICENSE_SCOPE
    ):
        return False
    candidate_parts = Path(expected_candidate_path).parts
    if len(candidate_parts) != 3 or candidate_parts[:2] != (
        "shots",
        candidate_parts[1],
    ):
        return False
    candidate_name = candidate_parts[2]
    match = re.fullmatch(r"candidate_(0[1-3])\.mp4", candidate_name)
    if match is None:
        return False
    raw_hash = str(value["source_candidate_sha256"])
    raw_dir = Path("shots") / candidate_parts[1] / "raw" / candidate_name[:-4] / raw_hash
    if value.get("source_candidate_path") != (raw_dir / "gateway_output.mp4").as_posix():
        return False
    if value.get("source_provenance_path") != (
        raw_dir / "gateway.provenance.json"
    ).as_posix():
        return False
    if value.get("drive_audio_path") != (
        Path("audio") / "drive" / f"{candidate_parts[1]}.wav"
    ).as_posix():
        return False
    mouth_blend = value.get("mouth_blend")
    if mouth_blend is not None and not _validate_mouth_blend_provenance(
        root_path,
        mouth_blend,
        shot_id=candidate_parts[1],
    ):
        return False
    raw_video = _validated_relative(root_path, value["source_candidate_path"])
    raw_provenance = _validated_relative(root_path, value["source_provenance_path"])
    drive_audio = _validated_relative(root_path, value["drive_audio_path"])
    if raw_video is None or raw_provenance is None or drive_audio is None:
        return False
    if not (
        _sha256(raw_video) == raw_hash
        and _sha256(raw_provenance) == value["source_provenance_sha256"]
        and _sha256(drive_audio) == drive_audio_sha256
    ):
        return False
    try:
        raw_payload = json.loads(raw_provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(raw_payload, Mapping)
        and "postprocess" not in raw_payload
        and raw_payload.get("output_sha256") == raw_hash
        and raw_payload.get("output_path") == expected_candidate_path
    )


def _validate_mouth_blend_provenance(
    root: Path,
    value: Any,
    *,
    shot_id: str,
) -> bool:
    keys = {
        "schema_version",
        "engine",
        "model",
        "source_lipsync_path",
        "source_lipsync_sha256",
        "detector_path",
        "detector_name",
        "detector_sha256",
        "score_threshold",
        "nms_threshold",
        "top_k",
        "corner_scale",
        "radius_ratios",
        "max_alpha",
        "feather_ratio",
    }
    allowed_keys = (keys, keys | {"tail_close_frames"})
    tail_close_frames = value.get("tail_close_frames", 0) if isinstance(value, Mapping) else None
    if not (
        isinstance(value, Mapping)
        and set(value) in allowed_keys
        and value.get("schema_version") == MOUTH_BLEND_SCHEMA_VERSION
        and value.get("engine") == MOUTH_BLEND_ENGINE
        and value.get("model") == MOUTH_BLEND_MODEL
        and value.get("detector_name") == "face_detection_yunet_2026may.onnx"
        and all(
            _SHA256.fullmatch(str(value.get(key, "")))
            for key in (
                "source_lipsync_sha256",
                "detector_sha256",
            )
        )
        and value.get("score_threshold") == MOUTH_BLEND_SCORE_THRESHOLD
        and value.get("nms_threshold") == MOUTH_BLEND_NMS_THRESHOLD
        and value.get("top_k") == MOUTH_BLEND_TOP_K
        and value.get("corner_scale") == MOUTH_BLEND_CORNER_SCALE
        and value.get("radius_ratios") == MOUTH_BLEND_RADIUS_RATIOS
        and value.get("max_alpha") == MOUTH_BLEND_MAX_ALPHA
        and value.get("feather_ratio") == MOUTH_BLEND_FEATHER_RATIO
        and isinstance(tail_close_frames, int)
        and not isinstance(tail_close_frames, bool)
        and 0 <= tail_close_frames <= 12
    ):
        return False
    lipsync_hash = str(value["source_lipsync_sha256"])
    detector_hash = str(value["detector_sha256"])
    expected_lipsync = (
        Path("shots") / shot_id / "postprocess" / "history" / f"{lipsync_hash}.mp4"
    )
    expected_detector = (
        Path("shots")
        / shot_id
        / "postprocess"
        / "models"
        / detector_hash
        / "face_detection_yunet_2026may.onnx"
    )
    if value.get("source_lipsync_path") != expected_lipsync.as_posix() or value.get(
        "detector_path"
    ) != expected_detector.as_posix():
        return False
    lipsync = _validated_relative(root, value["source_lipsync_path"])
    detector = _validated_relative(root, value["detector_path"])
    return bool(
        lipsync is not None
        and detector is not None
        and _sha256(lipsync) == lipsync_hash
        and _sha256(detector) == detector_hash
    )


def _validate_visual_timing_media(
    path: Path, plan: PetReplicaPlan, candidate: ReplicaCandidate
) -> None:
    if not is_valid_mp4_file(path):
        raise PetReplicaLipSyncError("Visual-timing output is not a valid MP4.")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PetReplicaLipSyncError(
            "Could not probe visual-timing output."
        ) from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list):
        raise PetReplicaLipSyncError("Visual-timing output streams are missing.")
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1 or audios:
        raise PetReplicaLipSyncError(
            "Visual-timing output needs one video stream and no embedded audio."
        )
    video = videos[0]
    video_duration = _number(video.get("duration"))
    if not (
        video.get("width") == plan.width
        and video.get("height") == plan.height
        and video_duration is not None
        and video_duration + 0.000001 >= candidate.editorial_duration_s
        and video_duration <= candidate.editorial_duration_s + 0.25
        and _frame_rate(video.get("r_frame_rate")) > 0
    ):
        raise PetReplicaLipSyncError(
            "Visual-timing output violates the editorial media contract."
        )


def _validate_lipsync_media(
    path: Path, plan: PetReplicaPlan, candidate: ReplicaCandidate
) -> None:
    if not is_valid_mp4_file(path):
        raise PetReplicaLipSyncError("Lip-sync output is not a valid MP4.")
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PetReplicaLipSyncError("Could not probe lip-sync output.") from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list):
        raise PetReplicaLipSyncError("Lip-sync output streams are missing.")
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise PetReplicaLipSyncError("Lip-sync output needs one video and one audio stream.")
    video = videos[0]
    video_duration = _number(video.get("duration"))
    if not (
        video.get("width") == plan.width
        and video.get("height") == plan.height
        and video_duration is not None
        and video_duration + 0.000001 >= candidate.editorial_duration_s
        and video_duration <= candidate.editorial_duration_s + 0.25
        and _frame_rate(video.get("r_frame_rate")) > 0
    ):
        raise PetReplicaLipSyncError("Lip-sync output violates the editorial media contract.")


def _transform_frame(
    frame: np.ndarray,
    transform: str,
    *,
    width: int,
    height: int,
) -> np.ndarray:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise PetReplicaLipSyncError("OpenCV is required for mouth-only lip sync.") from exc
    result = frame
    if transform == "hflip":
        result = cv2.flip(result, 1)
    elif transform != "none":
        match = re.fullmatch(
            r"crop=(\d+):(\d+):(\d+):(\d+),scale=(\d+):(\d+)",
            transform,
        )
        if match is None:
            raise PetReplicaLipSyncError("Mouth blend frame transform is invalid.")
        crop_width, crop_height, x, y, output_width, output_height = (
            int(item) for item in match.groups()
        )
        result = result[y : y + crop_height, x : x + crop_width]
        result = cv2.resize(result, (output_width, output_height))
    if result.shape[:2] != (height, width):
        raise PetReplicaLipSyncError("Transformed raw frame dimensions are invalid.")
    return result


def _interpolate_face_landmarks(
    faces: list[np.ndarray | None],
) -> list[np.ndarray]:
    matrix = np.full((len(faces), 15), np.nan, dtype=np.float32)
    for index, face in enumerate(faces):
        if face is not None:
            values = np.asarray(face, dtype=np.float32).reshape(-1)
            matrix[index, : min(15, values.size)] = values[:15]
    positions = np.arange(len(faces), dtype=np.float32)
    for column in range(matrix.shape[1]):
        valid = np.flatnonzero(np.isfinite(matrix[:, column]))
        if len(valid):
            matrix[:, column] = np.interp(
                positions,
                valid.astype(np.float32),
                matrix[valid, column],
            )
    if not np.isfinite(matrix[:, :14]).all():
        raise PetReplicaLipSyncError("YuNet face landmarks could not be interpolated.")
    if len(faces) >= 3:
        padded = np.pad(matrix[:, :14], ((2, 2), (0, 0)), mode="edge")
        smoothed = np.empty_like(matrix[:, :14])
        for index in range(len(faces)):
            smoothed[index] = np.median(padded[index : index + 5], axis=0)
        matrix[:, :14] = smoothed
    return [row for row in matrix]


def _archive_once(destination: Path, source: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise PetReplicaLipSyncError("Lip-sync archive path is unsafe.")
        if _sha256(destination) != expected_sha256:
            raise PetReplicaLipSyncError("Lip-sync archive collides with different bytes.")
        return
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as opened:
        staged = Path(opened.name)
    try:
        shutil.copyfile(source, staged)
        if _sha256(staged) != expected_sha256:
            raise PetReplicaLipSyncError("Lip-sync archive copy changed bytes.")
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def _promote_pair(
    video: Path, source: Path, provenance_path: Path, provenance: Mapping[str, Any]
) -> None:
    parent = video.parent
    video_stage = Path(tempfile.mkstemp(dir=parent, prefix=".lipsync-", suffix=".mp4")[1])
    provenance_stage = Path(
        tempfile.mkstemp(dir=parent, prefix=".lipsync-", suffix=".json")[1]
    )
    video_backup = Path(tempfile.mkstemp(dir=parent, prefix=".candidate-backup-")[1])
    provenance_backup = Path(
        tempfile.mkstemp(dir=parent, prefix=".provenance-backup-")[1]
    )
    try:
        shutil.copyfile(source, video_stage)
        provenance_stage.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copyfile(video, video_backup)
        shutil.copyfile(provenance_path, provenance_backup)
        try:
            os.replace(video_stage, video)
            os.replace(provenance_stage, provenance_path)
        except Exception:
            os.replace(video_backup, video)
            os.replace(provenance_backup, provenance_path)
            raise
    except PetReplicaLipSyncError:
        raise
    except Exception as exc:
        raise PetReplicaLipSyncError("Lip-sync candidate promotion failed.") from exc
    finally:
        for path in (
            video_stage,
            provenance_stage,
            video_backup,
            provenance_backup,
        ):
            path.unlink(missing_ok=True)


def _candidate_with_hash(candidate: ReplicaCandidate, digest: str) -> ReplicaCandidate:
    return ReplicaCandidate(
        candidate.shot_id,
        candidate.candidate_number,
        candidate.video_path,
        candidate.provenance_path,
        candidate.gateway_report_path,
        candidate.editorial_duration_s,
        candidate.generation_duration_s,
        digest,
    )


def _regular_inside(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PetReplicaLipSyncError(f"{label} must be a regular file inside output root.") from exc
    if path.is_symlink() or not resolved.is_file():
        raise PetReplicaLipSyncError(f"{label} must be a regular file inside output root.")
    return resolved


def _regular_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise PetReplicaLipSyncError(f"{label} is missing.") from exc
    if path.is_symlink() or not resolved.is_file():
        raise PetReplicaLipSyncError(f"{label} must be a regular file.")
    return resolved


def _validated_relative(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    relative = Path(value)
    if any(part in {"", ".", ".."} for part in relative.parts):
        return None
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return None if path.is_symlink() or not resolved.is_file() else resolved


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaLipSyncError(f"{label} is invalid.") from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaLipSyncError(f"{label} is invalid.")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as opened:
        for block in iter(lambda: opened.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame_rate(value: Any) -> float:
    try:
        numerator, denominator = str(value).split("/", 1)
        result = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _valid_frame_transform(value: Any) -> bool:
    transform = str(value).strip().lower()
    if transform in {"none", "hflip"}:
        return True
    match = re.fullmatch(
        r"crop=(\d+):(\d+):(\d+):(\d+),scale=(\d+):(\d+)",
        transform,
    )
    if match is None:
        return False
    width, height, x, y, output_width, output_height = (
        int(item) for item in match.groups()
    )
    return bool(
        width > 0
        and height > 0
        and width % 2 == 0
        and height % 2 == 0
        and x >= 0
        and y >= 0
        and x + width <= 720
        and y + height <= 1280
        and output_width == 720
        and output_height == 1280
    )
