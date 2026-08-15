from pathlib import Path

from factory.media_validation import MediaProbeResult
from factory.pipeline_eval import build_automatic_eval, build_specialist_eval


def _probe(*, duration=10.0, video=1, audio=1, valid=True):
    return MediaProbeResult(Path("final.mp4"), valid, duration, video, audio, "bad" if not valid else "")


def test_automatic_eval_passes_bound_media_and_nonoverlapping_timing() -> None:
    report = build_automatic_eval(
        project_id="episode",
        probe=_probe(),
        expected_duration_seconds=10.0,
        timings=[{"start_seconds": 1.0, "end_seconds": 2.0, "overlaps_previous": False}],
        expected_shot_count=2,
        rendered_shot_count=2,
        generation_success=True,
    )

    assert report["automatic_passed"] is True
    assert report["hard_failures"] == []
    assert report["status"] == "REVIEW_REQUIRED"


def test_automatic_eval_fails_duration_audio_overlap_and_missing_shots() -> None:
    report = build_automatic_eval(
        project_id="episode",
        probe=_probe(duration=8.0, audio=0),
        expected_duration_seconds=10.0,
        timings=[{"start_seconds": 1.0, "end_seconds": 3.0, "overlaps_previous": True}],
        expected_shot_count=3,
        rendered_shot_count=2,
        generation_success=False,
    )

    assert report["automatic_passed"] is False
    assert {item["code"] for item in report["hard_failures"]} == {
        "duration_drift",
        "missing_audio_stream",
        "dialogue_overlap",
        "shot_count_mismatch",
        "generation_failed",
    }


def test_specialist_eval_rejects_failed_or_empty_review_operation() -> None:
    report = build_specialist_eval(
        project_id="replica",
        operation_code=2,
        operation={"success": False, "reviewed_count": 0, "error": "review crashed"},
        candidate_count=0,
    )

    assert report["automatic_passed"] is False
    assert {item["code"] for item in report["hard_failures"]} == {
        "specialist_review_failed",
        "no_reviewed_candidates",
        "no_video_candidates",
    }


def test_specialist_eval_allows_manual_gate_code_with_reviewed_candidates() -> None:
    report = build_specialist_eval(
        project_id="replica",
        operation_code=1,
        operation={"success": False, "reviewed_count": 1},
        candidate_count=1,
    )

    assert report["automatic_passed"] is True
    assert report["status"] == "REVIEW_REQUIRED"
