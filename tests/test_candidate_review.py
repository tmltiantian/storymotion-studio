from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from factory.candidate_review import (
    CandidateRecord,
    CandidateReviewError,
    CandidateReviewManifest,
    CandidateState,
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


def test_candidate_state_machine_cannot_skip_review(tmp_path):
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"candidate")

    with pytest.raises(CandidateReviewError, match="planned cannot transition to approved"):
        transition_candidate(_record(candidate), CandidateState.APPROVED)


def test_approved_selection_requires_unchanged_approved_timeline_candidate(tmp_path):
    candidate = tmp_path / "candidate_001.mp4"
    candidate.write_bytes(b"candidate")
    record = _record(candidate, CandidateState.REVIEW_REQUIRED)
    reviewed = transition_candidate(
        record,
        CandidateState.APPROVED,
        reason="reviewed",
        evidence={
            "first_frame": "first.png",
            "middle_frame": "middle.png",
            "last_frame": "last.png",
            "review_note": "approved locally",
        },
    )
    manifest = CandidateReviewManifest(project_id="sample", candidates=(reviewed,))

    selection = approved_selection_from_manifest(manifest, _timeline())

    assert list(selection["selected_candidates"]) == ["micro_001"]
    assert selection["selected_candidates"]["micro_001"]["candidate_path"] == str(candidate)

