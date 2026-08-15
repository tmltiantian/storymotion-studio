from __future__ import annotations

from pathlib import Path

import pytest

from factory.pipeline_contracts import (
    PIPELINE_STAGES,
    ProductionPackage,
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageRecord,
    StageState,
)


def test_pipeline_has_three_modes_and_audio_precedes_video() -> None:
    assert [mode.value for mode in ProjectMode] == [
        "original",
        "novel",
        "replica",
    ]
    assert [stage.value for stage in PIPELINE_STAGES] == [
        "concept",
        "script",
        "storyboard",
        "assets",
        "audio",
        "video",
        "edit",
        "eval",
        "deliver",
    ]
    assert PIPELINE_STAGES.index(StageName.AUDIO) < PIPELINE_STAGES.index(
        StageName.VIDEO
    )


def test_project_spec_round_trips_without_credentials(tmp_path: Path) -> None:
    spec = ProjectSpec(
        project_id="cat_series_01",
        title="窗边的声音",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "两只猫调查窗帘后的声音"},
        output_dir=tmp_path / "episode",
        target={"duration_seconds": 60, "ratio": "9:16"},
        characters=(
            {"id": "doubao", "voice": "魅力女友", "speech_rate": 4},
            {"id": "naitang", "voice": "调皮公主", "speech_rate": 2},
        ),
        providers={"video": "doubao-seedance-2-0"},
        policies={"enable_live": False, "audio_first": True},
    )

    payload = spec.to_dict()
    restored = ProjectSpec.from_dict(payload)

    assert restored == spec
    assert "api_key" not in str(payload).lower()
    assert restored.output_dir.is_absolute()


@pytest.mark.parametrize("project_id", ["", "../escape", "a/b", "a\\b"])
def test_project_spec_rejects_unsafe_project_ids(
    tmp_path: Path, project_id: str
) -> None:
    with pytest.raises(ValueError, match="project_id"):
        ProjectSpec(
            project_id=project_id,
            title="unsafe",
            mode=ProjectMode.NOVEL,
            input={"kind": "novel", "path": str(tmp_path / "novel.txt")},
            output_dir=tmp_path / "output",
        )


def test_production_package_reports_first_incomplete_stage(tmp_path: Path) -> None:
    spec = ProjectSpec(
        project_id="episode",
        title="Episode",
        mode=ProjectMode.REPLICA,
        input={"kind": "reference", "path": str(tmp_path / "source.mp4")},
        output_dir=tmp_path / "output",
    )
    records = tuple(
        StageRecord(
            stage=stage,
            state=(
                StageState.PASSED
                if stage in (StageName.CONCEPT, StageName.SCRIPT)
                else StageState.PENDING
            ),
        )
        for stage in PIPELINE_STAGES
    )

    package = ProductionPackage.new(spec, spec_path=tmp_path / "project.json")
    package = package.with_stages(records)

    assert package.next_stage is StageName.STORYBOARD
    assert ProductionPackage.from_dict(package.to_dict()) == package


def test_legacy_passed_record_migrates_as_approved() -> None:
    record = StageRecord.from_dict({"stage": "script", "state": "passed"})

    assert record.review_state is ReviewState.APPROVED
    assert record.review_policy is ReviewPolicy.AUTOMATIC
    assert record.review_blocks_progress is False
