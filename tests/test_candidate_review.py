from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.candidate_review import (
    CandidateRecord,
    CandidateReviewError,
    CandidateReviewManifest,
    CandidateState,
    approved_anchor_for_micro_shot,
    approved_selection_from_manifest,
    transition_candidate,
)
from factory.visual_timeline import MicroShot, VisualTimeline


def _timeline() -> VisualTimeline:
    return VisualTimeline(
        project_id="sample",
        micro_shots=(
            MicroShot(
                id="micro_001", index=1, parent_shot_id="shot_001",
                scene_context="station", time_context="night", purpose="action",
                character_ids=("lead",), emotion_start="tense", emotion_end="calm",
                emotion_intensity=3, gaze="forward", pose_start="standing",
                pose_end="turning", action_actor_id="lead", action_code="turn",
                action_target="door", camera_mode="locked", source_duration_seconds=3,
                timeline_duration_seconds=3, entry_cut="hard_cut", exit_cut="hard_cut",
                negative_constraints=("no_text",), cadence_fps=8,
            ),
        ),
    )


def _record(path: Path, state: CandidateState = CandidateState.PLANNED) -> CandidateRecord:
    return CandidateRecord(
        micro_shot_id="micro_001",
        candidate_path=str(path),
        candidate_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        state=state,
        audio_sha256="",
        entry_anchor_id="scene_entry",
        visual_qc_report_path=str(path.with_name("visual_qc.json")),
    )


def _qc_report(path: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    frames = {}
    samples = []
    for position in range(1, 10):
        label = {1: "first_frame", 5: "middle_frame", 9: "last_frame"}.get(position, "")
        frame = path.parent / f"sample_{position:02d}.png"
        frame.write_bytes((label or str(position)).encode())
        digest = hashlib.sha256(frame.read_bytes()).hexdigest()
        samples.append({
            "evidence": {
                "path": str(frame), "sha256": digest,
                "size_bytes": frame.stat().st_size,
                "device": frame.stat().st_dev, "inode": frame.stat().st_ino,
            }
        })
        if label:
            frames[label] = str(frame)
    report = path.with_name("visual_qc.json")
    report.write_text(json.dumps({
        "schema_version": "motion-comic-factory.visual-qc.v2",
        "candidate_evidence": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
        "sample_frames": samples, "automatic_passed": True,
        "manual_review": {}, "passed": True,
    }), encoding="utf-8")
    return report, frames, {
        "visual_qc_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "first_frame_sha256": hashlib.sha256(Path(frames["first_frame"]).read_bytes()).hexdigest(),
        "middle_frame_sha256": hashlib.sha256(Path(frames["middle_frame"]).read_bytes()).hexdigest(),
        "last_frame_sha256": hashlib.sha256(Path(frames["last_frame"]).read_bytes()).hexdigest(),
        "sample_frame_sha256": {
            f"sample_{index:02d}": hashlib.sha256(
                (path.parent / f"sample_{index:02d}.png").read_bytes()
            ).hexdigest()
            for index in range(1, 10)
        },
    }


def _task5_report(root: Path, candidate: Path, *, audio_sha256: str = "", anchor: str = "scene_entry") -> tuple[Path, str]:
    report = root / "micro_video_batch.json"
    report.write_text(json.dumps({
        "schema_version": "motion-comic-factory.micro-video-batch.v1",
        "project_id": "sample", "run_dir": str(root), "success": True,
        "completed_count": 1,
        "jobs": [{
            "micro_shot_id": "micro_001", "output_path": str(candidate),
            "output_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "reference_audio_sha256": audio_sha256, "entry_anchor_id": anchor,
        }],
    }), encoding="utf-8")
    return report, hashlib.sha256(report.read_bytes()).hexdigest()


def test_candidate_state_machine_cannot_skip_review(tmp_path):
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"candidate")

    with pytest.raises(CandidateReviewError, match="planned cannot transition to approved"):
        transition_candidate(_record(candidate), CandidateState.APPROVED)


def test_approved_selection_requires_unchanged_approved_timeline_candidate(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    job_report, job_hash = _task5_report(tmp_path, candidate)
    record = CandidateRecord(
        **{**_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__, "visual_qc_report_path": str(report), **fingerprints, "rendered_job_report_path": str(job_report), "rendered_job_report_sha256": job_hash}
    )
    reviewed = transition_candidate(
        record,
        CandidateState.APPROVED,
        reason="reviewed",
        evidence={
            **frames,
            "review_note": "approved locally",
        },
    )
    manifest = CandidateReviewManifest(project_id="sample", candidates=(reviewed,))

    selection = approved_selection_from_manifest(manifest, _timeline())

    assert list(selection["selected_candidates"]) == ["micro_001"]
    assert selection["selected_candidates"]["micro_001"]["candidate_path"] == str(candidate)


def test_anchor_comes_from_nearest_prior_approved_same_scene_last_frame(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    job_report, job_hash = _task5_report(tmp_path, candidate)
    approved = transition_candidate(
        CandidateRecord(
            **{
                **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
                "visual_qc_report_path": str(report),
                **fingerprints,
                "rendered_job_report_path": str(job_report),
                "rendered_job_report_sha256": job_hash,
            }
        ),
        CandidateState.APPROVED,
        evidence={**frames, "review_note": "approved"},
    )
    first = _timeline().micro_shots[0]
    second = replace(first, id="micro_002", index=2)
    timeline = VisualTimeline(project_id="sample", micro_shots=(first, second))

    anchor_path, source_id = approved_anchor_for_micro_shot(
        CandidateReviewManifest(project_id="sample", candidates=(approved,)),
        timeline,
        "micro_002",
    )

    assert anchor_path == frames["last_frame"]
    assert source_id == "micro_001"


def test_approved_selection_rejects_caller_supplied_frame_paths(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, _, fingerprints = _qc_report(candidate)
    record = CandidateRecord(
        **{**_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__, "visual_qc_report_path": str(report), **fingerprints}
    )
    with pytest.raises(CandidateReviewError, match="does not match visual QC sample"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={
                "first_frame": "forged.png", "middle_frame": "forged.png",
                "last_frame": "forged.png", "review_note": "approved locally",
            },
        )


def test_visible_candidate_rejects_incomplete_task5_job_report(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    audio_hash = "a" * 64
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["manual_review"] = {
        "audio_sha256": audio_hash,
        "entry_anchor_id": "scene_entry",
        "speaker_visible": True,
        "lipsync_score": 5.0,
    }
    report.write_text(json.dumps(payload), encoding="utf-8")
    job_report = tmp_path / "micro_video_batch.json"
    job_report.write_text(json.dumps({
        "schema_version": "motion-comic-factory.micro-video-batch.v1",
        "project_id": "sample", "run_dir": str(tmp_path), "success": False,
        "completed_count": 0,
        "jobs": [{
            "micro_shot_id": "micro_001", "output_path": str(candidate),
            "output_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "reference_audio_sha256": audio_hash, "entry_anchor_id": "scene_entry",
        }],
    }), encoding="utf-8")
    record = CandidateRecord(
        **{
            **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
            "audio_sha256": audio_hash,
            "visual_qc_report_path": str(report),
            **fingerprints,
            "visual_qc_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "rendered_job_report_path": str(job_report),
            "rendered_job_report_sha256": hashlib.sha256(job_report.read_bytes()).hexdigest(),
        }
    )

    with pytest.raises(CandidateReviewError, match="not completed successfully"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={
                **frames, "review_note": "approved", "audio_sha256": audio_hash,
                "speaker_visible": "true", "lipsync_score": "5.0",
            },
        )


def test_approval_requires_authoritative_qc_to_have_passed(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["passed"] = False
    report.write_text(json.dumps(payload), encoding="utf-8")
    job_report, job_hash = _task5_report(tmp_path, candidate)
    record = CandidateRecord(
        **{
            **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
            "visual_qc_report_path": str(report),
            **fingerprints,
            "visual_qc_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "rendered_job_report_path": str(job_report),
            "rendered_job_report_sha256": job_hash,
        }
    )

    with pytest.raises(CandidateReviewError, match="visual QC did not pass"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={**frames, "review_note": "approved"},
        )


def test_speech_approval_cross_checks_lipsync_against_authoritative_qc(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    audio_hash = "a" * 64
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["manual_review"] = {
        "audio_sha256": audio_hash,
        "entry_anchor_id": "scene_entry",
        "speaker_visible": True,
        "lipsync_score": 3.4,
    }
    report.write_text(json.dumps(payload), encoding="utf-8")
    job_report, job_hash = _task5_report(
        tmp_path, candidate, audio_sha256=audio_hash
    )
    record = CandidateRecord(
        **{
            **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
            "audio_sha256": audio_hash,
            "visual_qc_report_path": str(report),
            **fingerprints,
            "visual_qc_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "rendered_job_report_path": str(job_report),
            "rendered_job_report_sha256": job_hash,
        }
    )

    with pytest.raises(CandidateReviewError, match="at least 3.5"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={
                **frames,
                "review_note": "approved",
                "audio_sha256": audio_hash,
                "speaker_visible": "true",
                "lipsync_score": "3.4",
            },
        )


def test_approved_selection_rejects_tampered_non_reviewed_sample(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    record = CandidateRecord(
        **{
            **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
            "visual_qc_report_path": str(report),
            **fingerprints,
        }
    )
    (tmp_path / "sample_02.png").write_bytes(b"tampered")

    with pytest.raises(CandidateReviewError, match="sample evidence changed"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={**frames, "review_note": "approved"},
        )


def test_action_candidate_rejects_forged_task5_job_report(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    report, frames, fingerprints = _qc_report(candidate)
    job_report, job_hash = _task5_report(tmp_path, candidate)
    forged = json.loads(job_report.read_text(encoding="utf-8"))
    forged["jobs"][0]["output_sha256"] = "0" * 64
    job_report.write_text(json.dumps(forged), encoding="utf-8")
    record = CandidateRecord(
        **{
            **_record(candidate, CandidateState.REVIEW_REQUIRED).__dict__,
            "visual_qc_report_path": str(report), **fingerprints,
            "rendered_job_report_path": str(job_report),
            "rendered_job_report_sha256": job_hash,
        }
    )

    with pytest.raises(CandidateReviewError, match="Task 5 job report changed"):
        transition_candidate(
            record,
            CandidateState.APPROVED,
            evidence={**frames, "review_note": "approved"},
        )
