from __future__ import annotations

import json
from pathlib import Path

import pytest

import factory.source_locked_cat_replica as replica
from factory.source_locked_cat_replica import (
    ANALYSIS_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SourceLockedReplicaError,
    _anchor_copy_source,
    _anchor_prompt,
    _parallel_map,
    _video_anchor_labels,
    _video_prompt,
    _video_segments,
    load_project,
)


def test_source_locked_replica_requires_an_explicit_gateway_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)

    with pytest.raises(SourceLockedReplicaError, match="GATEWAY_BASE_URL"):
        replica._gateway_base_url()


def _config(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    reference = tmp_path / "cat.png"
    source.write_bytes(b"source")
    reference.write_bytes(b"reference")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project_id": "test-project",
        "title": "test",
        "source": {
            "video_path": str(source),
            "duration_s": 8.0,
            "width": 1076,
            "height": 1920,
            "fps": 30,
            "active_picture": {"x": 2, "y": 664, "width": 1072, "height": 592},
        },
        "target": {
            "name": "Doubao",
            "identity": "black-and-white tuxedo cat",
            "reference_path": str(reference),
        },
        "generation": {
            "image_model": "doubao-seedream-4-5",
            "image_size": "2560x1440",
            "video_model": "doubao-seedance-2-0",
            "video_resolution": "720p",
            "video_ratio": "16:9",
            "long_shot_second_anchor_threshold_s": 7.0,
        },
        "boundaries_s": [0.0, 2.366667, 8.0],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _annotation(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "shot_id": "S002",
        "scene": "bedroom at night",
        "framing": "fixed close shot",
        "action": "looks down, taps once, then looks up",
        "props_and_contacts": "one phone under the right front paw",
        "visible_others": "no other visible character",
        "speech_mode": "mixed",
        "cat_speech_windows": [[2.0, 3.5]],
        "subtitle": "",
        "reviewed": True,
    }
    value.update(updates)
    return value


def test_load_project_locks_boundaries_to_frames(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    assert [shot.frame_count for shot in project.shots] == [71, 169]
    assert project.shots[0].duration_s == pytest.approx(71 / 30)
    assert project.shots[0].provider_duration_s == 4
    assert project.shots[1].provider_duration_s == 6


def test_load_project_rejects_non_increasing_boundaries(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["boundaries_s"] = [0.0, 4.0, 4.0, 8.0]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceLockedReplicaError, match="strictly increasing"):
        load_project(path, tmp_path / "output")


def test_load_project_reads_source_locked_ending_fade(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source"]["ending_fade_start_s"] = 7.2
    payload["source"]["ending_fade_duration_s"] = 0.8
    path.write_text(json.dumps(payload), encoding="utf-8")

    project = load_project(path, tmp_path / "output")

    assert project.ending_fade_start_s == pytest.approx(7.2)
    assert project.ending_fade_duration_s == pytest.approx(0.8)


def test_load_project_reads_motion_smoothing_policy(tmp_path: Path) -> None:
    path = _config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generation"]["motion_interpolation"] = True
    payload["generation"]["fade_transition_frames"] = 2
    payload["generation"]["fade_transition_after_shots"] = ["S001"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    project = load_project(path, tmp_path / "output")

    assert project.motion_interpolation is True
    assert project.fade_transition_frames == 2
    assert project.fade_transition_after_shots == ("S001",)


def test_normalization_filter_interpolates_24fps_and_applies_short_fades() -> None:
    result = replica._normalization_filter(
        input_fps=24.0,
        output_fps=30,
        frame_count=60,
        motion_interpolation=True,
        fade_in=True,
        fade_out=True,
        transition_frames=2,
    )

    assert "minterpolate=fps=30" in result
    assert "fade=t=in:st=0:d=0.066667:color=black" in result
    assert "fade=t=out:st=1.933333:d=0.066667:color=black" in result


def test_normalization_filter_keeps_native_30fps_without_interpolation() -> None:
    result = replica._normalization_filter(
        input_fps=30.0,
        output_fps=30,
        frame_count=60,
        motion_interpolation=True,
        fade_in=False,
        fade_out=False,
        transition_frames=0,
    )

    assert "minterpolate" not in result
    assert "fps=30" in result


def test_anchor_prompt_forbids_source_cat_and_source_text(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    prompt = _anchor_prompt(project, project.shots[1], "start", _annotation())

    assert "Replace the orange-and-white source cat completely" in prompt
    assert "Remove all source captions" in prompt
    assert "four" not in prompt.lower() or "four white" not in prompt.lower()


def test_anchor_prompt_can_preserve_source_device_ui_text(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    prompt = _anchor_prompt(
        project,
        project.shots[1],
        "start",
        _annotation(preserve_source_ui_text=True),
    )

    assert "Preserve source in-device UI text exactly" in prompt
    assert "no text" not in prompt.lower()
    assert "Remove all source captions" in prompt


def test_anchor_prompt_applies_only_the_matching_label_constraint(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")
    annotation = _annotation(
        anchor_label_constraints={
            "start": "START ONLY CONSTRAINT",
            "end": "END ONLY CONSTRAINT",
        }
    )

    start_prompt = _anchor_prompt(project, project.shots[1], "start", annotation)
    end_prompt = _anchor_prompt(project, project.shots[1], "end", annotation)

    assert "START ONLY CONSTRAINT" in start_prompt
    assert "END ONLY CONSTRAINT" not in start_prompt
    assert "END ONLY CONSTRAINT" in end_prompt
    assert "START ONLY CONSTRAINT" not in end_prompt


def test_anchor_copy_source_is_limited_to_project_anchor_directory(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")
    annotation = _annotation(
        anchor_label_copy_from={"start": "assets/anchors/S001/end.png"}
    )

    assert _anchor_copy_source(project, annotation, "start") == (
        project.output_root / "assets/anchors/S001/end.png"
    ).resolve()
    with pytest.raises(SourceLockedReplicaError, match="escapes the anchors directory"):
        _anchor_copy_source(
            project,
            _annotation(anchor_label_copy_from={"start": "../outside.png"}),
            "start",
        )


def test_video_prompt_limits_mouth_motion_to_reviewed_mixed_windows(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    prompt = _video_prompt(project, project.shots[1], _annotation(), True)

    assert "off-screen interviewer" in prompt
    assert "[[2.0, 3.5]]" in prompt
    assert "close between speakers" in prompt
    assert "exact end state" in prompt


def test_video_prompt_includes_reviewed_extra_constraints(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")
    prompt = _video_prompt(
        project,
        project.shots[1],
        _annotation(extra_video_constraints="VISIBLE LOWER JAW REQUIRED"),
        True,
    )

    assert "VISIBLE LOWER JAW REQUIRED" in prompt


def test_video_anchor_labels_can_disable_a_dissimilar_end_anchor() -> None:
    assert _video_anchor_labels({"video_anchor_labels": ["start"]}, True) == ("start",)


def test_video_segments_lock_an_internal_cut_to_exact_frames(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")
    shot = project.shots[1]
    annotation = _annotation(
        speech_mode="none",
        video_segments=[
            {
                "name": "night",
                "duration_frames": 120,
                "provider_duration_s": 4,
                "anchor_label": "start",
                "action": "remain awake at night",
            },
            {
                "name": "morning",
                "duration_frames": 49,
                "provider_duration_s": 4,
                "anchor_label": "end",
                "action": "reach for the alarm in morning light",
            },
        ],
    )

    segments = _video_segments(shot, annotation)

    assert [segment.duration_frames for segment in segments] == [120, 49]
    assert sum(segment.duration_frames for segment in segments) == shot.frame_count


def test_video_segments_reject_frame_drift(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")
    with pytest.raises(SourceLockedReplicaError, match="exact editorial frames"):
        _video_segments(
            project.shots[1],
            _annotation(
                speech_mode="none",
                video_segments=[
                    {
                        "name": "night",
                        "duration_frames": 120,
                        "provider_duration_s": 4,
                        "anchor_label": "start",
                        "action": "night",
                    },
                    {
                        "name": "morning",
                        "duration_frames": 48,
                        "provider_duration_s": 4,
                        "anchor_label": "end",
                        "action": "morning",
                    },
                ],
            ),
        )


def test_analysis_schema_constant_is_stable() -> None:
    assert ANALYSIS_SCHEMA_VERSION == "motion-comic-factory.cat-replica-shot-analysis.v1"


def test_selected_shots_limits_work_to_requested_ids(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    selected = replica._selected_shots(project, ["S002"])

    assert [shot.shot_id for shot in selected] == ["S002"]


def test_selected_shots_rejects_unknown_ids(tmp_path: Path) -> None:
    project = load_project(_config(tmp_path), tmp_path / "output")

    with pytest.raises(SourceLockedReplicaError, match="Unknown shot ids: S999"):
        replica._selected_shots(project, ["S999"])


def test_parallel_map_finishes_independent_jobs_before_reporting_failure() -> None:
    completed: list[int] = []

    def worker(value: int) -> Path:
        if value == 2:
            raise ValueError("bad input")
        completed.append(value)
        return Path(str(value))

    with pytest.raises(SourceLockedReplicaError, match="job 2: ValueError: bad input"):
        _parallel_map(worker, [1, 2, 3], concurrency=3)

    assert sorted(completed) == [1, 3]
