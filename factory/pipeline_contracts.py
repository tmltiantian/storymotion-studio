from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_SPEC_SCHEMA = "motion-comic-factory.project-spec.v1"
PRODUCTION_PACKAGE_SCHEMA = "motion-comic-factory.production-package.v1"


class ProjectMode(str, Enum):
    ORIGINAL = "original"
    NOVEL = "novel"
    REPLICA = "replica"


class StageName(str, Enum):
    CONCEPT = "concept"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    ASSETS = "assets"
    AUDIO = "audio"
    VIDEO = "video"
    EDIT = "edit"
    EVAL = "eval"
    DELIVER = "deliver"


PIPELINE_STAGES = tuple(StageName)


class StageState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    STALE = "stale"


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _validate_project_id(value: str) -> str:
    project_id = str(value).strip()
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
        or len(project_id) > 128
        or any(ord(character) < 32 for character in project_id)
    ):
        raise ValueError("project_id must be a safe single path component")
    return project_id


def _mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class ProjectSpec:
    project_id: str
    title: str
    mode: ProjectMode
    input: Mapping[str, Any]
    output_dir: Path
    target: Mapping[str, Any] = field(default_factory=dict)
    characters: tuple[Mapping[str, Any], ...] = ()
    providers: Mapping[str, Any] = field(default_factory=dict)
    policies: Mapping[str, Any] = field(default_factory=dict)
    mode_options: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = PROJECT_SPEC_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_project_id(self.project_id))
        object.__setattr__(self, "mode", ProjectMode(self.mode))
        output_dir = Path(self.output_dir).expanduser()
        if not output_dir.is_absolute():
            raise ValueError("output_dir must be absolute")
        object.__setattr__(self, "output_dir", output_dir)
        if self.schema_version != PROJECT_SPEC_SCHEMA:
            raise ValueError(f"Unsupported project spec schema: {self.schema_version}")
        if not str(self.title).strip():
            raise ValueError("title cannot be empty")
        if not isinstance(self.input, Mapping) or not self.input:
            raise ValueError("input must be a non-empty object")
        object.__setattr__(self, "input", _mapping(self.input))
        object.__setattr__(self, "target", _mapping(self.target))
        object.__setattr__(
            self, "characters", tuple(_mapping(item) for item in self.characters)
        )
        object.__setattr__(self, "providers", _mapping(self.providers))
        object.__setattr__(self, "policies", _mapping(self.policies))
        object.__setattr__(self, "mode_options", _mapping(self.mode_options))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "title": self.title,
            "mode": self.mode.value,
            "input": _plain(self.input),
            "output_dir": str(self.output_dir),
            "target": _plain(self.target),
            "characters": _plain(self.characters),
            "providers": _plain(self.providers),
            "policies": _plain(self.policies),
            "mode_options": _plain(self.mode_options),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProjectSpec:
        return cls(
            project_id=str(value["project_id"]),
            title=str(value["title"]),
            mode=ProjectMode(str(value["mode"])),
            input=_mapping(value.get("input")),
            output_dir=Path(str(value["output_dir"])),
            target=_mapping(value.get("target")),
            characters=tuple(value.get("characters") or ()),
            providers=_mapping(value.get("providers")),
            policies=_mapping(value.get("policies")),
            mode_options=_mapping(value.get("mode_options")),
            schema_version=str(value.get("schema_version", "")),
        )

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class StageRecord:
    stage: StageName
    state: StageState = StageState.PENDING
    executor: str = ""
    input_signature: str = ""
    artifacts: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", StageName(self.stage))
        object.__setattr__(self, "state", StageState(self.state))
        object.__setattr__(self, "artifacts", tuple(map(str, self.artifacts)))
        object.__setattr__(
            self, "blocked_reasons", tuple(map(str, self.blocked_reasons))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "state": self.state.value,
            "executor": self.executor,
            "input_signature": self.input_signature,
            "artifacts": list(self.artifacts),
            "blocked_reasons": list(self.blocked_reasons),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageRecord:
        return cls(
            stage=StageName(str(value["stage"])),
            state=StageState(str(value.get("state", StageState.PENDING.value))),
            executor=str(value.get("executor", "")),
            input_signature=str(value.get("input_signature", "")),
            artifacts=tuple(value.get("artifacts") or ()),
            blocked_reasons=tuple(value.get("blocked_reasons") or ()),
            error=str(value.get("error", "")),
        )


@dataclass(frozen=True)
class ProductionPackage:
    project_id: str
    mode: ProjectMode
    spec_path: Path
    spec_sha256: str
    stages: tuple[StageRecord, ...]
    final_outputs: tuple[str, ...] = ()
    eval_reports: tuple[str, ...] = ()
    schema_version: str = PRODUCTION_PACKAGE_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _validate_project_id(self.project_id))
        object.__setattr__(self, "mode", ProjectMode(self.mode))
        object.__setattr__(self, "spec_path", Path(self.spec_path))
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "final_outputs", tuple(self.final_outputs))
        object.__setattr__(self, "eval_reports", tuple(self.eval_reports))
        if self.schema_version != PRODUCTION_PACKAGE_SCHEMA:
            raise ValueError(
                f"Unsupported production package schema: {self.schema_version}"
            )
        if tuple(record.stage for record in self.stages) != PIPELINE_STAGES:
            raise ValueError("Production package must contain all pipeline stages in order")

    @classmethod
    def new(cls, spec: ProjectSpec, *, spec_path: Path) -> ProductionPackage:
        return cls(
            project_id=spec.project_id,
            mode=spec.mode,
            spec_path=spec_path,
            spec_sha256=spec.sha256,
            stages=tuple(StageRecord(stage=stage) for stage in PIPELINE_STAGES),
        )

    @property
    def next_stage(self) -> StageName | None:
        for record in self.stages:
            if record.state is not StageState.PASSED:
                return record.stage
        return None

    def with_stages(self, stages: Sequence[StageRecord]) -> ProductionPackage:
        return replace(self, stages=tuple(stages))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "mode": self.mode.value,
            "spec_path": str(self.spec_path),
            "spec_sha256": self.spec_sha256,
            "stages": [record.to_dict() for record in self.stages],
            "next_stage": self.next_stage.value if self.next_stage else "complete",
            "final_outputs": list(self.final_outputs),
            "eval_reports": list(self.eval_reports),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProductionPackage:
        return cls(
            project_id=str(value["project_id"]),
            mode=ProjectMode(str(value["mode"])),
            spec_path=Path(str(value["spec_path"])),
            spec_sha256=str(value["spec_sha256"]),
            stages=tuple(
                StageRecord.from_dict(record) for record in value.get("stages", ())
            ),
            final_outputs=tuple(value.get("final_outputs") or ()),
            eval_reports=tuple(value.get("eval_reports") or ()),
            schema_version=str(value.get("schema_version", "")),
        )
