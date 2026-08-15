from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .pipeline_artifacts import (
    load_stage_manifest,
    manifest_artifact_paths,
    stage_dir,
)
from .pipeline_contracts import PIPELINE_STAGES, ProjectSpec, StageName
from .pipeline_contracts import StageState
from .pipeline_modes import ModeStep


@dataclass(frozen=True)
class StageContext:
    project_dir: Path
    spec: ProjectSpec
    stage: StageName
    step: ModeStep
    enable_live: bool
    repair_scope: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        root = Path(self.project_dir).expanduser()
        if root.is_symlink():
            raise ValueError("project directory cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "project_dir", root.resolve())
        object.__setattr__(self, "stage", StageName(self.stage))
        repair_scope = self.repair_scope
        if repair_scope is None:
            from .pipeline_store import load_active_repair_state

            state = load_active_repair_state(root)
            raw_scope = state.get("affected") or {}
            if not isinstance(raw_scope, Mapping):
                raise ValueError("Active repair scope is invalid")
            repair_scope = {
                str(stage): tuple(map(str, item_ids))
                for stage, item_ids in raw_scope.items()
            }
        object.__setattr__(self, "repair_scope", dict(repair_scope))

    @property
    def stage_dir(self) -> Path:
        return stage_dir(self.project_dir, self.stage)

    def require_manifest(self, stage: StageName | str) -> dict[str, object]:
        selected = StageName(stage)
        if PIPELINE_STAGES.index(selected) >= PIPELINE_STAGES.index(self.stage):
            raise ValueError(f"Cannot read current or future stage {selected.value}")
        return load_stage_manifest(self.project_dir, selected)

    def require_artifact(self, stage: StageName | str, name: str) -> Path:
        selected = StageName(stage)
        self.require_manifest(selected)
        matches = [
            path
            for path in manifest_artifact_paths(self.project_dir, selected)
            if path.name == name
        ]
        direct = [
            path
            for path in matches
            if path.parent == stage_dir(self.project_dir, selected)
        ]
        if len(direct) == 1:
            return direct[0]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"Expected one {name} artifact from stage {selected.value}"
            )
        return matches[0]


@dataclass(frozen=True)
class StageExecution:
    state: StageState
    executor: str
    artifacts: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    error: str = ""
    metadata: Mapping[str, object] | None = None

    @classmethod
    def passed(
        cls,
        *,
        executor: str,
        artifacts: Sequence[str | Path] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> StageExecution:
        return cls(
            StageState.PASSED,
            executor,
            tuple(str(Path(path)) for path in artifacts),
            metadata=metadata,
        )

    @classmethod
    def blocked(
        cls,
        reason: str,
        *,
        executor: str,
        artifacts: Sequence[str | Path] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> StageExecution:
        return cls(
            StageState.BLOCKED,
            executor,
            tuple(str(Path(path)) for path in artifacts),
            blocked_reasons=(reason,),
            metadata=metadata,
        )

    @classmethod
    def failed(cls, error: str, *, executor: str) -> StageExecution:
        return cls(StageState.FAILED, executor, error=error)
