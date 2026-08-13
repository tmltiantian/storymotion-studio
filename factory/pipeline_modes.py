from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .pipeline_contracts import PIPELINE_STAGES, ProjectMode, StageName


@dataclass(frozen=True)
class ModeStep:
    stage: StageName
    executor_id: str
    requires_live: bool = False
    manual_gate: bool = False
    prepare_before_gate: bool = False
    version: int = 1


class ModeAdapter:
    mode: ProjectMode
    stage_steps: Mapping[StageName, ModeStep]

    def __init__(self, mode: ProjectMode, steps: tuple[ModeStep, ...]) -> None:
        if tuple(step.stage for step in steps) != PIPELINE_STAGES:
            raise ValueError("Mode adapter must map every standard stage in order")
        if len({step.executor_id for step in steps}) != len(steps):
            raise ValueError("Mode executor IDs must be unique within one adapter")
        self.mode = mode
        self.stage_steps = MappingProxyType({step.stage: step for step in steps})


def _generic_steps(script_executor: str) -> tuple[ModeStep, ...]:
    return (
        ModeStep(StageName.CONCEPT, "generic.concept"),
        ModeStep(StageName.SCRIPT, script_executor),
        ModeStep(StageName.STORYBOARD, "generic.storyboard"),
        ModeStep(
            StageName.ASSETS,
            "generic.assets",
            manual_gate=True,
            prepare_before_gate=True,
        ),
        ModeStep(StageName.AUDIO, "generic.audio"),
        ModeStep(StageName.VIDEO, "generic.video"),
        ModeStep(StageName.EDIT, "generic.edit"),
        ModeStep(
            StageName.EVAL,
            "generic.eval",
            manual_gate=True,
            prepare_before_gate=True,
        ),
        ModeStep(
            StageName.DELIVER,
            "generic.deliver",
            manual_gate=True,
            prepare_before_gate=True,
        ),
    )


class OriginalModeAdapter(ModeAdapter):
    def __init__(self) -> None:
        super().__init__(ProjectMode.ORIGINAL, _generic_steps("original.script"))


class NovelModeAdapter(ModeAdapter):
    def __init__(self) -> None:
        super().__init__(ProjectMode.NOVEL, _generic_steps("novel.script"))


class ReplicaModeAdapter(ModeAdapter):
    def __init__(self) -> None:
        super().__init__(
            ProjectMode.REPLICA,
            (
                ModeStep(StageName.CONCEPT, "replica.concept"),
                ModeStep(StageName.SCRIPT, "replica.script"),
                ModeStep(StageName.STORYBOARD, "replica.storyboard"),
                ModeStep(
                    StageName.ASSETS,
                    "replica.assets",
                    manual_gate=True,
                    prepare_before_gate=True,
                ),
                ModeStep(StageName.AUDIO, "replica.audio"),
                ModeStep(
                    StageName.VIDEO,
                    "replica.video",
                    requires_live=True,
                ),
                ModeStep(StageName.EDIT, "replica.edit"),
                ModeStep(
                    StageName.EVAL,
                    "replica.eval",
                    manual_gate=True,
                    prepare_before_gate=True,
                ),
                ModeStep(
                    StageName.DELIVER,
                    "replica.deliver",
                    manual_gate=True,
                    prepare_before_gate=True,
                ),
            ),
        )


_ADAPTERS = {
    ProjectMode.ORIGINAL: OriginalModeAdapter(),
    ProjectMode.NOVEL: NovelModeAdapter(),
    ProjectMode.REPLICA: ReplicaModeAdapter(),
}


def get_mode_adapter(mode: ProjectMode | str) -> ModeAdapter:
    return _ADAPTERS[ProjectMode(mode)]
