from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

import factory.micro_preview as micro_preview
from factory.micro_preview import (
    MicroPreviewError,
    MicroSource,
    build_micro_preview_ffmpeg_command,
    render_micro_preview_video,
    select_micro_sources,
)
from factory.media_validation import probe_media
from factory.schema import Character, Episode, Shot
from factory.visual_timeline import (
    MicroShot,
    VisualTimeline,
    visual_timeline_to_dict,
)
from factory.visual_qc import VisualQCError


VIDEO_MODEL = "doubao-seedance-2-0"
STILL_MODEL = "doubao-seedream-4-5"


@pytest.fixture
def sample_episode() -> Episode:
    return Episode(
        project_id="sample_episode",
        title="Sample episode",
        language="zh-CN",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[Character("char_1", "林澈", "lead", "guarded", "dark coat", "low")],
        shots=[
            Shot(
                "shot_001",
                1,
                "和平路书店",
                "夜晚，林澈伸手靠近信封。",
                "雨停后的旧城区，和平路书店柜台上的信封。",
                "static",
                3.0,
                "tense",
            )
        ],
    )


@pytest.fixture
def visual_timeline(sample_episode: Episode) -> VisualTimeline:
    return VisualTimeline(
        project_id=sample_episode.project_id,
        micro_shots=(
            MicroShot(
                id="micro_001",
                index=1,
                parent_shot_id="shot_001",
                scene_context="和平路书店",
                time_context="夜晚",
                purpose="action",
                character_ids=("char_1",),
                emotion_start="guarded",
                emotion_end="alarmed",
                emotion_intensity=4,
                gaze="at the envelope",
                pose_start="beside the counter",
                pose_end="near the envelope",
                action_actor_id="char_1",
                action_code="reach",
                action_target="信封",
                camera_mode="locked",
                source_duration_seconds=3,
                timeline_duration_seconds=3.0,
                entry_cut="hard_cut",
                exit_cut="hard_cut",
                negative_constraints=("no_rain",),
                cadence_fps=8,
            ),
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _micro_source(
    path: Path,
    *,
    kind: str = "video",
    start: float = 1.2,
    end: float = 3.4,
    duration: float = 4.0,
    cadence_fps: int = 8,
    camera_mode: str = "locked",
    exit_cut: str = "hard_cut",
) -> MicroSource:
    return MicroSource(
        micro_shot_id="micro_001",
        index=1,
        kind=kind,
        path=path,
        model=VIDEO_MODEL if kind == "video" else STILL_MODEL,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        qc_report_path=path.with_name("visual_qc.json") if kind == "video" else None,
        selected_start_seconds=start,
        selected_end_seconds=end,
        timeline_duration_seconds=duration,
        cadence_fps=cadence_fps,
        camera_mode=camera_mode,
        entry_cut="hard_cut",
        exit_cut=exit_cut,
    )


def _video_selection(run_dir: Path, qc_path: Path) -> dict:
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    return {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": "sample_episode",
        "selected_candidates": {
            "micro_001": {
                "kind": "video",
                "candidate_path": str(candidate),
                "qc_report_path": str(qc_path),
            }
        },
    }


def _install_gate_stubs(monkeypatch, qc: dict) -> None:
    monkeypatch.setattr(
        micro_preview, "require_selected_production_model", lambda report: VIDEO_MODEL
    )
    monkeypatch.setattr(
        micro_preview, "require_selected_still_model", lambda report: STILL_MODEL
    )

    def require_qc(report, **kwargs):
        assert report == qc
        assert kwargs["expected_micro_shot"].id == "micro_001"
        assert kwargs["expected_reference_image_labels"] == ("char_1",)
        return report

    monkeypatch.setattr(micro_preview, "require_passed_visual_qc", require_qc)


def _write_approved_candidate_review(run_dir: Path, candidate: Path) -> None:
    selection = {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": "sample_episode",
        "selected_candidates": {
            "micro_001": {
                "kind": "video", "candidate_path": str(candidate),
                "qc_report_path": str(run_dir / "visual_qc.json"),
                "audio_sha256": "", "entry_anchor_id": "scene_entry",
            }
        },
    }
    (run_dir / "visual_selection.json").write_text(json.dumps(selection), encoding="utf-8")
    (run_dir / "candidate_review.json").write_text(json.dumps({
        "schema_version": "motion-comic-factory.candidate-review.v1",
        "project_id": "sample_episode",
        "candidates": [{
            "micro_shot_id": "micro_001", "candidate_path": str(candidate),
            "candidate_sha256": _sha256(candidate), "state": "approved",
            "audio_sha256": "", "entry_anchor_id": "scene_entry",
            "visual_qc_report_path": str(run_dir / "visual_qc.json"),
            "reason": "approved", "evidence": {
                "first_frame": "first.png", "middle_frame": "middle.png",
                "last_frame": "last.png", "review_note": "approved",
            },
        }],
    }), encoding="utf-8")


def test_preview_refuses_an_unapproved_mp4(
    tmp_path, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    (run_dir / "visual_timeline.json").write_text(
        json.dumps(visual_timeline_to_dict(visual_timeline)), encoding="utf-8"
    )
    (run_dir / "visual_selection.json").write_text(
        json.dumps({"schema_version": "motion-comic-factory.visual-selection.v1", "project_id": "sample_episode", "selected_candidates": {}}),
        encoding="utf-8",
    )
    (run_dir / "model_bakeoff_report.json").write_text("{}", encoding="utf-8")
    candidate = run_dir / "candidate_001.mp4"
    candidate.write_bytes(b"unapproved")
    (run_dir / "candidate_review.json").write_text(
        json.dumps({
            "schema_version": "motion-comic-factory.candidate-review.v1",
            "project_id": "sample_episode",
            "candidates": [{
                "micro_shot_id": "micro_001", "candidate_path": str(candidate),
                "candidate_sha256": _sha256(candidate), "state": "review_required",
                "audio_sha256": "", "entry_anchor_id": "scene_entry",
                "visual_qc_report_path": str(run_dir / "visual_qc.json"), "reason": "",
                "evidence": {
                    "first_frame": "first.png", "middle_frame": "middle.png",
                    "last_frame": "last.png", "review_note": "needs review",
                },
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(MicroPreviewError, match="not approved"):
        render_micro_preview_video(
            sample_episode,
            timeline_path=run_dir / "visual_timeline.json",
            selection_path=run_dir / "visual_selection.json",
            bakeoff_report_path=run_dir / "model_bakeoff_report.json",
            run_dir=run_dir,
            output_path=run_dir / "preview.mp4",
            report_path=run_dir / "preview_report.json",
        )


def test_select_micro_sources_uses_exact_video_schema_and_manual_qc_range(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"approved-video")
    qc_path = (run_dir / "qc/micro_001/visual_qc.json").resolve()
    qc_path.parent.mkdir(parents=True)
    qc = {
        "candidate_evidence": {"path": str(candidate)},
        "manual_review": {
            "selected_start_seconds": 1.2,
            "selected_end_seconds": 2.8,
        },
    }
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    _install_gate_stubs(monkeypatch, qc)
    bakeoff = {"project_id": "sample_episode", "run_dir": str(run_dir)}

    sources = select_micro_sources(
        sample_episode,
        visual_timeline,
        _video_selection(run_dir, qc_path),
        run_dir=run_dir,
        bakeoff_report=bakeoff,
    )

    assert sources == [
        MicroSource(
            micro_shot_id="micro_001",
            index=1,
            kind="video",
            path=candidate,
            model=VIDEO_MODEL,
            size_bytes=len(b"approved-video"),
            sha256=hashlib.sha256(b"approved-video").hexdigest(),
            qc_report_path=qc_path,
            selected_start_seconds=1.2,
            selected_end_seconds=2.8,
            timeline_duration_seconds=3.0,
            cadence_fps=8,
            camera_mode="locked",
            entry_cut="hard_cut",
            exit_cut="hard_cut",
        )
    ]


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda selection: selection.update(extra=True), "exact-key schema"),
        (
            lambda selection: selection["selected_candidates"]["micro_001"].update(
                selected_start_seconds=0.0
            ),
            "exact-key schema",
        ),
        (
            lambda selection: selection["selected_candidates"].update(
                micro_999=selection["selected_candidates"]["micro_001"]
            ),
            "exactly match",
        ),
    ],
)
def test_select_micro_sources_rejects_non_exact_selection_schema(
    tmp_path,
    monkeypatch,
    sample_episode,
    visual_timeline,
    mutation,
    match,
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"video")
    qc_path = (run_dir / "qc/micro_001/visual_qc.json").resolve()
    qc_path.parent.mkdir(parents=True)
    qc = {
        "candidate_evidence": {"path": str(candidate)},
        "manual_review": {"selected_start_seconds": 0.0, "selected_end_seconds": 1.0},
    }
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    _install_gate_stubs(monkeypatch, qc)
    selection = _video_selection(run_dir, qc_path)
    mutation(selection)

    with pytest.raises(MicroPreviewError, match=match):
        select_micro_sources(
            sample_episode,
            visual_timeline,
            selection,
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_select_micro_sources_rejects_forged_qc_candidate_path(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    forged = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_002.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"video-1")
    forged.write_bytes(b"video-2")
    qc_path = (run_dir / "qc/micro_001/visual_qc.json").resolve()
    qc_path.parent.mkdir(parents=True)
    qc = {
        "candidate_evidence": {"path": str(forged)},
        "manual_review": {"selected_start_seconds": 0.0, "selected_end_seconds": 1.0},
    }
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    _install_gate_stubs(monkeypatch, qc)

    with pytest.raises(MicroPreviewError, match="candidate path does not match"):
        select_micro_sources(
            sample_episode,
            visual_timeline,
            _video_selection(run_dir, qc_path),
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_select_micro_sources_wraps_failed_visual_qc_gate(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"failed-video")
    qc_path = (run_dir / "qc/micro_001/visual_qc.json").resolve()
    qc_path.parent.mkdir(parents=True)
    qc = {
        "candidate_evidence": {"path": str(candidate)},
        "manual_review": {"selected_start_seconds": 0.0, "selected_end_seconds": 1.0},
    }
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    monkeypatch.setattr(
        micro_preview, "require_selected_production_model", lambda report: VIDEO_MODEL
    )
    monkeypatch.setattr(
        micro_preview,
        "require_passed_visual_qc",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            VisualQCError("Candidate failed automatic visual QC.")
        ),
    )

    with pytest.raises(MicroPreviewError, match="failed visual QC"):
        select_micro_sources(
            sample_episode,
            visual_timeline,
            _video_selection(run_dir, qc_path),
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_select_micro_sources_rejects_credentials_in_local_paths(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "api_key=do-not-store" / "sample_episode").resolve()
    candidate = run_dir / "micro_clips/micro_001" / VIDEO_MODEL / "candidate_001.mp4"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"video")
    qc_path = (run_dir / "qc/micro_001/visual_qc.json").resolve()
    qc_path.parent.mkdir(parents=True)
    qc = {
        "candidate_evidence": {"path": str(candidate)},
        "manual_review": {"selected_start_seconds": 0.0, "selected_end_seconds": 1.0},
    }
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    _install_gate_stubs(monkeypatch, qc)

    with pytest.raises(MicroPreviewError, match="credentials"):
        select_micro_sources(
            sample_episode,
            visual_timeline,
            _video_selection(run_dir, qc_path),
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_select_micro_sources_requires_per_still_review_and_current_fingerprint(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    shot = replace(
        visual_timeline.micro_shots[0],
        character_ids=(),
        purpose="object",
        action_actor_id="object",
        action_code="hold_still",
        camera_mode="locked",
    )
    still_timeline = replace(visual_timeline, micro_shots=(shot,))
    candidate = run_dir / "micro_stills/micro_001" / STILL_MODEL / "candidate_003.png"
    candidate.parent.mkdir(parents=True)
    Image.new("RGB", (32, 48), "navy").save(candidate)
    monkeypatch.setattr(micro_preview, "validate_visual_timeline", lambda *args: [])
    monkeypatch.setattr(
        micro_preview, "require_selected_production_model", lambda report: VIDEO_MODEL
    )
    monkeypatch.setattr(
        micro_preview, "require_selected_still_model", lambda report: STILL_MODEL
    )
    selection = {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": "sample_episode",
        "selected_candidates": {
            "micro_001": {
                "kind": "still",
                "candidate_path": str(candidate),
                "size_bytes": candidate.stat().st_size,
                "sha256": _sha256(candidate),
                "score": 80,
                "hard_failures": [],
                "notes": "locally reviewed",
            }
        },
    }

    source = select_micro_sources(
        sample_episode,
        still_timeline,
        selection,
        run_dir=run_dir,
        bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
    )[0]

    assert source.kind == "still"
    assert source.selected_start_seconds == 0.0
    assert source.selected_end_seconds == 3.0
    for field, value, match in (
        ("score", 79, "score"),
        ("hard_failures", ["embedded_text"], "hard failure"),
        ("notes", "https://remote.example/image", "remote assets"),
    ):
        failed = json.loads(json.dumps(selection))
        failed["selected_candidates"]["micro_001"][field] = value
        with pytest.raises(MicroPreviewError, match=match):
            select_micro_sources(
                sample_episode,
                still_timeline,
                failed,
                run_dir=run_dir,
                bakeoff_report={
                    "project_id": "sample_episode",
                    "run_dir": str(run_dir),
                },
            )
    with pytest.raises(MicroPreviewError, match="character micro-shot"):
        select_micro_sources(
            sample_episode,
            visual_timeline,
            selection,
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )
    selection["selected_candidates"]["micro_001"]["sha256"] = "0" * 64
    with pytest.raises(MicroPreviewError, match="SHA-256"):
        select_micro_sources(
            sample_episode,
            still_timeline,
            selection,
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_select_micro_sources_accepts_audited_editorial_still_without_model_gate(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    shot = replace(
        visual_timeline.micro_shots[0],
        character_ids=(),
        purpose="object",
        action_actor_id="object",
        action_code="hold_still",
        camera_mode="locked",
    )
    still_timeline = replace(visual_timeline, micro_shots=(shot,))
    source = run_dir / "clips/shot_001.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    candidate = run_dir / "editorial_stills/micro_001/editorial_001.jpg"
    candidate.parent.mkdir(parents=True)
    Image.new("RGB", (32, 48), "navy").save(candidate)
    monkeypatch.setattr(micro_preview, "validate_visual_timeline", lambda *args: [])
    monkeypatch.setattr(
        micro_preview,
        "require_selected_production_model",
        lambda report: VIDEO_MODEL,
    )
    monkeypatch.setattr(
        micro_preview,
        "require_selected_still_model",
        lambda report: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    selection = {
        "schema_version": "motion-comic-factory.visual-selection.v1",
        "project_id": "sample_episode",
        "selected_candidates": {
            "micro_001": {
                "kind": "editorial_still",
                "candidate_path": str(candidate),
                "source_path": str(source),
                "source_size_bytes": source.stat().st_size,
                "source_sha256": _sha256(source),
                "operation": "extract_frame",
                "parameters": {"timestamp_seconds": 0.4},
                "size_bytes": candidate.stat().st_size,
                "sha256": _sha256(candidate),
                "score": 86,
                "hard_failures": [],
                "notes": "Rain-stopped establishing frame reviewed locally.",
            }
        },
    }

    result = select_micro_sources(
        sample_episode,
        still_timeline,
        selection,
        run_dir=run_dir,
        bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
    )[0]

    assert result.kind == "still"
    assert result.model == "editorial-derived"
    assert result.provenance == {
        "source_path": str(source),
        "source_size_bytes": source.stat().st_size,
        "source_sha256": _sha256(source),
        "operation": "extract_frame",
        "parameters": {"timestamp_seconds": 0.4},
    }
    assert result.to_report()["provenance"] == result.provenance


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda item, run_dir: item.update(source_sha256="0" * 64),
            "source SHA-256",
        ),
        (
            lambda item, run_dir: item.update(
                candidate_path=str(
                    run_dir / "editorial_stills/wrong/editorial_001.jpg"
                )
            ),
            "editorial still path",
        ),
        (
            lambda item, run_dir: item.update(
                source_path=str(run_dir.parent / "outside.mp4")
            ),
            "inside the project run",
        ),
        (
            lambda item, run_dir: item.update(
                parameters={"timestamp_seconds": -1.0}
            ),
            "timestamp_seconds",
        ),
        (
            lambda item, run_dir: item.update(operation="invented_filter"),
            "operation",
        ),
    ],
)
def test_select_micro_sources_rejects_invalid_editorial_still_provenance(
    tmp_path,
    monkeypatch,
    sample_episode,
    visual_timeline,
    mutation,
    match,
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    shot = replace(
        visual_timeline.micro_shots[0],
        character_ids=(),
        purpose="object",
        action_actor_id="object",
        action_code="hold_still",
        camera_mode="locked",
    )
    still_timeline = replace(visual_timeline, micro_shots=(shot,))
    source = run_dir / "clips/shot_001.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source-video")
    outside = run_dir.parent / "outside.mp4"
    outside.write_bytes(b"outside")
    candidate = run_dir / "editorial_stills/micro_001/editorial_001.jpg"
    candidate.parent.mkdir(parents=True)
    Image.new("RGB", (32, 48), "navy").save(candidate)
    wrong = run_dir / "editorial_stills/wrong/editorial_001.jpg"
    wrong.parent.mkdir(parents=True)
    Image.new("RGB", (32, 48), "navy").save(wrong)
    monkeypatch.setattr(micro_preview, "validate_visual_timeline", lambda *args: [])
    monkeypatch.setattr(
        micro_preview,
        "require_selected_production_model",
        lambda report: VIDEO_MODEL,
    )
    item = {
        "kind": "editorial_still",
        "candidate_path": str(candidate),
        "source_path": str(source),
        "source_size_bytes": source.stat().st_size,
        "source_sha256": _sha256(source),
        "operation": "extract_frame",
        "parameters": {"timestamp_seconds": 0.4},
        "size_bytes": candidate.stat().st_size,
        "sha256": _sha256(candidate),
        "score": 86,
        "hard_failures": [],
        "notes": "Locally reviewed.",
    }
    mutation(item, run_dir)

    with pytest.raises(MicroPreviewError, match=match):
        select_micro_sources(
            sample_episode,
            still_timeline,
            {
                "schema_version": "motion-comic-factory.visual-selection.v1",
                "project_id": "sample_episode",
                "selected_candidates": {"micro_001": item},
            },
            run_dir=run_dir,
            bakeoff_report={"project_id": "sample_episode", "run_dir": str(run_dir)},
        )


def test_micro_preview_command_uses_per_shot_cadence_and_selected_range(tmp_path):
    source_path = tmp_path / "candidate.mp4"
    source_path.write_bytes(b"video")
    command = build_micro_preview_ffmpeg_command(
        sources=[_micro_source(source_path)],
        resolution="1080x1920",
        output_fps=30,
        output_path=tmp_path / "preview.mp4",
    )

    filters = command[command.index("-filter_complex") + 1]
    assert "trim=start=1.200:end=3.400,setpts=PTS-STARTPTS" in filters
    assert "fps=8,fps=30" in filters
    assert "tpad=stop_mode=clone" in filters
    assert "trim=duration=4.000,setpts=PTS-STARTPTS" in filters
    assert command[-9:] == [
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(tmp_path / "preview.mp4"),
    ]


def test_micro_preview_command_holds_locked_still_and_limits_micro_pan(tmp_path):
    locked = tmp_path / "locked.png"
    moving = tmp_path / "moving.png"
    Image.new("RGB", (32, 48), "black").save(locked)
    Image.new("RGB", (32, 48), "white").save(moving)
    command = build_micro_preview_ffmpeg_command(
        sources=[
            _micro_source(locked, kind="still", duration=2.0),
            replace(
                _micro_source(moving, kind="still", duration=3.0),
                micro_shot_id="micro_002",
                index=2,
                camera_mode="micro_pan",
            ),
        ],
        resolution="1080x1920",
        output_fps=30,
        output_path=tmp_path / "preview.mp4",
    )

    filters = command[command.index("-filter_complex") + 1]
    assert command.count("-loop") == 2
    assert "[0:v]scale=" in filters and "[v0]" in filters
    locked_filters = filters.split("[0:v]", 1)[1].split("[v0]", 1)[0]
    assert "zoompan" not in locked_filters
    assert "zoompan=z='min(1.02" in filters
    assert ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'" in filters
    assert "concat=n=2:v=1:a=0" in filters


def test_micro_preview_command_inserts_exact_three_frame_black_only_for_time_jump(
    tmp_path,
):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    sources = [
        _micro_source(first, duration=2.0, exit_cut="time_jump_black"),
        replace(
            _micro_source(second, duration=3.0),
            micro_shot_id="micro_002",
            index=2,
            entry_cut="time_jump_black",
        ),
    ]

    command = build_micro_preview_ffmpeg_command(
        sources=sources,
        resolution="1080x1920",
        output_fps=30,
        output_path=tmp_path / "preview.mp4",
    )
    filters = command[command.index("-filter_complex") + 1]

    assert "trim=duration=1.900" in filters
    assert "color=c=black:s=1080x1920:r=30:d=0.100" in filters
    assert "concat=n=3:v=1:a=0" in filters
    assert "concat=n=3:v=1:a=0,trim=duration=5.000" in filters
    assert "xfade" not in filters and "dissolve" not in filters

    with pytest.raises(MicroPreviewError, match="final micro-shot"):
        build_micro_preview_ffmpeg_command(
            sources=[replace(sources[1], exit_cut="time_jump_black")],
            resolution="1080x1920",
            output_fps=30,
            output_path=tmp_path / "invalid.mp4",
        )


def test_micro_preview_ffmpeg_renders_black_cut_zoom_and_exact_total_duration(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (64, 96), "navy").save(first)
    Image.new("RGB", (64, 96), "white").save(second)
    output = tmp_path / "preview.mp4"
    command = build_micro_preview_ffmpeg_command(
        sources=[
            _micro_source(
                first,
                kind="still",
                duration=0.3,
                exit_cut="time_jump_black",
            ),
            replace(
                _micro_source(
                    second,
                    kind="still",
                    duration=0.3,
                    camera_mode="micro_pan",
                ),
                micro_shot_id="micro_002",
                index=2,
                entry_cut="time_jump_black",
            ),
        ],
        resolution="1080x1920",
        output_fps=30,
        output_path=output,
    )

    subprocess.run(command, check=True, capture_output=True, text=True)
    media = probe_media(output, required_stream="video")

    assert media.valid is True
    assert abs(media.duration_seconds - 0.6) <= 1 / 30


def test_render_revalidates_source_and_preserves_last_good_outputs(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    candidate = run_dir / "candidate.mp4"
    candidate.write_bytes(b"selected")
    source = _micro_source(candidate, duration=3.0)
    timeline_path = run_dir / "visual_timeline.json"
    timeline_path.write_text(
        json.dumps(visual_timeline_to_dict(visual_timeline)), encoding="utf-8"
    )
    selection_path = run_dir / "visual_selection.json"
    _write_approved_candidate_review(run_dir, candidate)
    bakeoff_path = run_dir / "model_bakeoff_report.json"
    bakeoff_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        micro_preview, "select_micro_sources", lambda *args, **kwargs: [source]
    )
    output = run_dir / "micro_preview.mp4"
    report_path = run_dir / "micro_preview_report.json"
    output.write_bytes(b"last-good-video")
    report_path.write_bytes(b"last-good-report")
    candidate.write_bytes(b"replaced")

    with pytest.raises(MicroPreviewError, match="does not match|changed after selection"):
        render_micro_preview_video(
            sample_episode,
            timeline_path=timeline_path,
            selection_path=selection_path,
            bakeoff_report_path=bakeoff_path,
            run_dir=run_dir,
            output_path=output,
            report_path=report_path,
        )

    assert output.read_bytes() == b"last-good-video"
    assert report_path.read_bytes() == b"last-good-report"


def test_render_is_atomic_and_writes_source_decisions_after_valid_video(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    candidate = run_dir / "candidate.mp4"
    candidate.write_bytes(b"selected")
    source = _micro_source(candidate, duration=3.0)
    timeline_path = run_dir / "visual_timeline.json"
    timeline_path.write_text(
        json.dumps(visual_timeline_to_dict(visual_timeline)), encoding="utf-8"
    )
    selection_path = run_dir / "visual_selection.json"
    _write_approved_candidate_review(run_dir, candidate)
    bakeoff_path = run_dir / "model_bakeoff_report.json"
    bakeoff_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        micro_preview, "select_micro_sources", lambda *args, **kwargs: [source]
    )
    output = run_dir / "micro_preview.mp4"
    report_path = run_dir / "micro_preview_report.json"

    def fake_runner(command, **kwargs):
        Path(command[-1]).write_bytes(b"rendered")

    report = render_micro_preview_video(
        sample_episode,
        timeline_path=timeline_path,
        selection_path=selection_path,
        bakeoff_report_path=bakeoff_path,
        run_dir=run_dir,
        output_path=output,
        report_path=report_path,
        command_runner=fake_runner,
        media_validator=lambda path: path.read_bytes() == b"rendered",
    )

    assert output.read_bytes() == b"rendered"
    assert report["schema_version"] == "motion-comic-factory.micro-preview.v1"
    assert report["duration_seconds"] == 3.0
    assert report["sources"][0]["sha256"] == source.sha256
    assert report["sources"][0]["cadence_fps"] == 8
    assert report["ffmpeg_command"][-1] != str(output)
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_render_ffmpeg_failure_preserves_last_good_output_and_report(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    candidate = run_dir / "candidate.mp4"
    candidate.write_bytes(b"selected")
    source = _micro_source(candidate, duration=3.0)
    for name, payload in {
        "visual_timeline.json": visual_timeline_to_dict(visual_timeline),
        "model_bakeoff_report.json": {},
    }.items():
        (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    _write_approved_candidate_review(run_dir, candidate)
    monkeypatch.setattr(
        micro_preview, "select_micro_sources", lambda *args, **kwargs: [source]
    )
    output = run_dir / "micro_preview.mp4"
    report_path = run_dir / "micro_preview_report.json"
    output.write_bytes(b"last-good-video")
    report_path.write_bytes(b"last-good-report")

    def failing_runner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, command, stderr="render exploded")

    with pytest.raises(MicroPreviewError, match="render exploded"):
        render_micro_preview_video(
            sample_episode,
            timeline_path=run_dir / "visual_timeline.json",
            selection_path=run_dir / "visual_selection.json",
            bakeoff_report_path=run_dir / "model_bakeoff_report.json",
            run_dir=run_dir,
            output_path=output,
            report_path=report_path,
            command_runner=failing_runner,
        )

    assert output.read_bytes() == b"last-good-video"
    assert report_path.read_bytes() == b"last-good-report"
    assert not list(run_dir.glob(".micro_preview.*.mp4"))


def test_render_rejects_non_file_report_target_before_replacing_video(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    candidate = run_dir / "candidate.mp4"
    candidate.write_bytes(b"selected")
    source = _micro_source(candidate, duration=3.0)
    for name, payload in {
        "visual_timeline.json": visual_timeline_to_dict(visual_timeline),
        "model_bakeoff_report.json": {},
    }.items():
        (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    _write_approved_candidate_review(run_dir, candidate)
    monkeypatch.setattr(
        micro_preview, "select_micro_sources", lambda *args, **kwargs: [source]
    )
    output = run_dir / "micro_preview.mp4"
    output.write_bytes(b"last-good-video")
    report_path = run_dir / "micro_preview_report.json"
    report_path.mkdir()

    with pytest.raises(MicroPreviewError, match="regular file path"):
        render_micro_preview_video(
            sample_episode,
            timeline_path=run_dir / "visual_timeline.json",
            selection_path=run_dir / "visual_selection.json",
            bakeoff_report_path=run_dir / "model_bakeoff_report.json",
            run_dir=run_dir,
            output_path=output,
            report_path=report_path,
            command_runner=lambda *args, **kwargs: pytest.fail("must not render"),
        )

    assert output.read_bytes() == b"last-good-video"


def test_render_rejects_same_bytes_replacement_through_symlink(
    tmp_path, monkeypatch, sample_episode, visual_timeline
):
    run_dir = (tmp_path / "runs/sample_episode").resolve()
    run_dir.mkdir(parents=True)
    candidate = run_dir / "candidate.mp4"
    replacement = run_dir / "replacement.mp4"
    candidate.write_bytes(b"same-bytes")
    replacement.write_bytes(b"same-bytes")
    source = _micro_source(candidate, duration=3.0)
    for name, payload in {
        "visual_timeline.json": visual_timeline_to_dict(visual_timeline),
        "model_bakeoff_report.json": {},
    }.items():
        (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    _write_approved_candidate_review(run_dir, candidate)
    monkeypatch.setattr(
        micro_preview, "select_micro_sources", lambda *args, **kwargs: [source]
    )
    candidate.unlink()
    candidate.symlink_to(replacement)

    with pytest.raises(MicroPreviewError, match="changed after selection"):
        render_micro_preview_video(
            sample_episode,
            timeline_path=run_dir / "visual_timeline.json",
            selection_path=run_dir / "visual_selection.json",
            bakeoff_report_path=run_dir / "model_bakeoff_report.json",
            run_dir=run_dir,
            output_path=run_dir / "micro_preview.mp4",
            report_path=run_dir / "micro_preview_report.json",
            command_runner=lambda *args, **kwargs: pytest.fail("must not render"),
        )
