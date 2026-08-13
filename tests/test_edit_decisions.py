import json
from pathlib import Path

import pytest

from factory.edit_decisions import (
    EditDecisionError,
    load_edit_decisions,
    prepare_render_subtitles,
)


def _write_decisions(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "edit_decisions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_edit_decisions_validates_and_normalizes_shot_edits(tmp_path):
    path = _write_decisions(
        tmp_path,
        {
            "schema_version": "motion-comic-factory.edit-decisions.v1",
            "project_id": "sample",
            "subtitle": {"suppress_cues": [4]},
            "shots": {
                "shot_004": {
                    "drop_ranges_seconds": [[1.8, 3.0]],
                    "note": "remove duplicated face transition",
                },
                "shot_006": {
                    "source_end_seconds": 6.5,
                    "note": "freeze before generated source caption",
                },
            },
        },
    )

    decisions = load_edit_decisions(
        path,
        expected_project_id="sample",
        valid_shot_ids={"shot_004", "shot_006"},
    )

    assert decisions.suppressed_subtitle_cues == (4,)
    assert decisions.shots["shot_004"].drop_ranges_seconds == ((1.8, 3.0),)
    assert decisions.shots["shot_006"].source_end_seconds == 6.5
    assert decisions.shots["shot_006"].note == "freeze before generated source caption"


def test_load_edit_decisions_rejects_wrong_project_and_overlapping_ranges(tmp_path):
    wrong_project = _write_decisions(
        tmp_path,
        {
            "schema_version": "motion-comic-factory.edit-decisions.v1",
            "project_id": "other",
            "shots": {},
        },
    )

    with pytest.raises(EditDecisionError, match="project ID"):
        load_edit_decisions(
            wrong_project,
            expected_project_id="sample",
            valid_shot_ids=set(),
        )

    wrong_project.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.edit-decisions.v1",
                "project_id": "sample",
                "shots": {
                    "shot_001": {
                        "drop_ranges_seconds": [[1.0, 2.0], [1.5, 3.0]]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EditDecisionError, match="overlap"):
        load_edit_decisions(
            wrong_project,
            expected_project_id="sample",
            valid_shot_ids={"shot_001"},
        )


def test_prepare_render_subtitles_preserves_canonical_file_and_omits_selected_cue(
    tmp_path,
):
    source = tmp_path / "subtitles.srt"
    original = (
        "1\n00:00:00,000 --> 00:00:01,000\n旁白：第一句\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n苏眠：第二句\n"
    )
    source.write_text(original, encoding="utf-8")
    decisions_path = _write_decisions(
        tmp_path,
        {
            "schema_version": "motion-comic-factory.edit-decisions.v1",
            "project_id": "sample",
            "subtitle": {"suppress_cues": [2]},
            "shots": {},
        },
    )
    decisions = load_edit_decisions(
        decisions_path,
        expected_project_id="sample",
        valid_shot_ids=set(),
    )

    plan = prepare_render_subtitles(source, decisions)

    assert plan.path == tmp_path / "subtitles.render.srt"
    assert plan.suppressed_cues == (2,)
    assert "第一句" in plan.path.read_text(encoding="utf-8")
    assert "第二句" not in plan.path.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == original
