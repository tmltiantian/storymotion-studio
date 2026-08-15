from __future__ import annotations

from typing import Any, Mapping, Sequence

from .media_validation import MediaProbeResult


_REVIEW_DIMENSIONS = [
    "角色与场景一致性",
    "动作物理合理性、肢体结构和道具接触连续性",
    "口型、对白和字幕同步",
    "声音角色匹配、停顿、噪声和情绪自然度",
    "转场动机、空间方向、节奏和观看舒适度",
]


def _failure(code: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "evidence": evidence}


def build_automatic_eval(
    *,
    project_id: str,
    probe: MediaProbeResult,
    expected_duration_seconds: float,
    timings: Sequence[Mapping[str, Any]],
    expected_shot_count: int,
    rendered_shot_count: int,
    generation_success: bool,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    if not probe.valid or probe.video_stream_count <= 0:
        failures.append(
            _failure("invalid_video", probe.error or "Final video is invalid")
        )
    if probe.audio_stream_count <= 0:
        failures.append(
            _failure("missing_audio_stream", "Final video has no audio stream")
        )
    expected_duration = float(expected_duration_seconds)
    duration_drift = abs(float(probe.duration_seconds) - expected_duration)
    duration_tolerance = max(0.12, expected_duration * 0.01)
    if expected_duration <= 0 or duration_drift > duration_tolerance:
        failures.append(
            _failure(
                "duration_drift",
                "Final duration differs from the bound edit timeline",
                expected_seconds=round(expected_duration, 3),
                actual_seconds=round(probe.duration_seconds, 3),
                tolerance_seconds=round(duration_tolerance, 3),
            )
        )
    overlap_count = sum(bool(item.get("overlaps_previous")) for item in timings)
    if overlap_count:
        failures.append(
            _failure(
                "dialogue_overlap",
                "Measured TTS cues overlap",
                overlap_count=overlap_count,
            )
        )
    if rendered_shot_count != expected_shot_count:
        failures.append(
            _failure(
                "shot_count_mismatch",
                "Rendered shot count does not match the storyboard",
                expected=expected_shot_count,
                actual=rendered_shot_count,
            )
        )
    if not generation_success:
        failures.append(
            _failure("generation_failed", "Video generation did not complete successfully")
        )
    return {
        "schema_version": "motion-comic-factory.eval.v2",
        "project_id": project_id,
        "status": "REVIEW_REQUIRED" if not failures else "AUTOMATIC_FAILURE",
        "automatic_passed": not failures,
        "hard_failures": failures,
        "technical": {
            "valid": probe.valid,
            "duration_seconds": probe.duration_seconds,
            "expected_duration_seconds": expected_duration,
            "video_stream_count": probe.video_stream_count,
            "audio_stream_count": probe.audio_stream_count,
            "error": probe.error,
        },
        "timing": {
            "cue_count": len(timings),
            "overlap_count": overlap_count,
        },
        "shots": {
            "expected_count": expected_shot_count,
            "rendered_count": rendered_shot_count,
        },
        "manual_review_required": True,
        "review_dimensions": list(_REVIEW_DIMENSIONS),
    }


def build_specialist_eval(
    *,
    project_id: str,
    operation_code: int,
    operation: Mapping[str, Any],
    candidate_count: int,
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    reviewed_count = int(operation.get("reviewed_count") or 0)
    if operation_code not in {0, 1} or operation.get("error"):
        failures.append(
            _failure(
                "specialist_review_failed",
                str(operation.get("error") or "Specialist review operation failed"),
                operation_code=operation_code,
            )
        )
    if reviewed_count <= 0:
        failures.append(
            _failure("no_reviewed_candidates", "No video candidate was reviewed")
        )
    if int(candidate_count) <= 0:
        failures.append(
            _failure("no_video_candidates", "No video candidate exists for evaluation")
        )
    return {
        "schema_version": "motion-comic-factory.eval.v2",
        "project_id": project_id,
        "status": "REVIEW_REQUIRED" if not failures else "AUTOMATIC_FAILURE",
        "automatic_passed": not failures,
        "hard_failures": failures,
        "specialist_review": {
            "operation_code": operation_code,
            "reviewed_count": reviewed_count,
            "candidate_count": int(candidate_count),
            "operation": dict(operation),
        },
        "manual_review_required": True,
        "review_dimensions": list(_REVIEW_DIMENSIONS),
    }
