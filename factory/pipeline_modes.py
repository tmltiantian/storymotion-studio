from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .pipeline_contracts import PIPELINE_STAGES, ProjectMode, ReviewPolicy, StageName


@dataclass(frozen=True)
class ModeStep:
    stage: StageName
    executor_id: str
    version: int
    requires_live: bool = False
    manual_gate: bool = False
    prepare_before_gate: bool = False

    @property
    def compatibility_review_policy(self) -> ReviewPolicy:
        return ReviewPolicy.MANUAL if self.manual_gate else ReviewPolicy.AUTOMATIC


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
        ModeStep(StageName.CONCEPT, "generic.concept", version=2),
        ModeStep(StageName.SCRIPT, script_executor, version=2),
        ModeStep(StageName.STORYBOARD, "generic.storyboard", version=2),
        ModeStep(
            StageName.ASSETS,
            "generic.assets",
            version=2,
            manual_gate=True,
            prepare_before_gate=True,
        ),
        ModeStep(StageName.AUDIO, "generic.audio", version=2),
        ModeStep(StageName.VIDEO, "generic.video", version=2),
        ModeStep(StageName.EDIT, "generic.edit", version=2),
        ModeStep(
            StageName.EVAL,
            "generic.eval",
            version=2,
            manual_gate=True,
            prepare_before_gate=True,
        ),
        ModeStep(
            StageName.DELIVER,
            "generic.deliver",
            version=2,
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
                ModeStep(StageName.CONCEPT, "replica.concept", version=2),
                ModeStep(StageName.SCRIPT, "replica.script", version=2),
                ModeStep(StageName.STORYBOARD, "replica.storyboard", version=2),
                ModeStep(
                    StageName.ASSETS,
                    "replica.assets",
                    version=2,
                    manual_gate=True,
                    prepare_before_gate=True,
                ),
                ModeStep(StageName.AUDIO, "replica.audio", version=2),
                ModeStep(
                    StageName.VIDEO,
                    "replica.video",
                    version=2,
                    requires_live=True,
                ),
                ModeStep(StageName.EDIT, "replica.edit", version=2),
                ModeStep(
                    StageName.EVAL,
                    "replica.eval",
                    version=2,
                    manual_gate=True,
                    prepare_before_gate=True,
                ),
                ModeStep(
                    StageName.DELIVER,
                    "replica.deliver",
                    version=2,
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
