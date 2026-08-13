from __future__ import annotations

import pytest

from factory.pipeline_contracts import PIPELINE_STAGES, ProjectMode, StageName
from factory.pipeline_modes import get_mode_adapter


@pytest.mark.parametrize("mode", tuple(ProjectMode))
def test_every_mode_maps_one_native_executor_per_standard_stage(
    mode: ProjectMode,
) -> None:
    adapter = get_mode_adapter(mode)

    assert tuple(adapter.stage_steps) == PIPELINE_STAGES
    assert adapter.mode is mode
    assert all(step.executor_id for step in adapter.stage_steps.values())
    assert all(not hasattr(step, "covered_stages") for step in adapter.stage_steps.values())
    assert all(not hasattr(step, "legacy_stage") for step in adapter.stage_steps.values())


def test_original_and_novel_share_media_stages_but_not_script_strategy() -> None:
    original = get_mode_adapter(ProjectMode.ORIGINAL)
    novel = get_mode_adapter(ProjectMode.NOVEL)

    assert original.stage_steps[StageName.SCRIPT].executor_id == "original.script"
    assert novel.stage_steps[StageName.SCRIPT].executor_id == "novel.script"
    for stage in PIPELINE_STAGES[2:]:
        assert (
            original.stage_steps[stage].executor_id
            == novel.stage_steps[stage].executor_id
        )


def test_replica_uses_native_mode_specific_executors_and_gates() -> None:
    adapter = get_mode_adapter(ProjectMode.REPLICA)

    assert all(
        step.executor_id.startswith("replica.")
        for step in adapter.stage_steps.values()
    )
    assert adapter.stage_steps[StageName.VIDEO].requires_live is True
    assert adapter.stage_steps[StageName.ASSETS].manual_gate is True
    assert adapter.stage_steps[StageName.EVAL].prepare_before_gate is True
