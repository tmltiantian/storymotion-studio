from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .file_io import read_json_object
from .pipeline_contracts import ProductionPackage, StageName
from .pipeline_store import (
    apply_repair_state,
    load_active_repair_state,
    load_production_package,
)


IMPACT_PLAN_SCHEMA = "motion-comic-factory.impact-plan.v1"


class ChangeScope(str, Enum):
    DIALOGUE = "dialogue"
    CHARACTER = "character"
    SHOT = "shot"
    SUBTITLE_STYLE = "subtitle_style"


DEFAULT_DEPENDENCIES = {
    ChangeScope.DIALOGUE.value: {
        StageName.AUDIO: "dialogue_ids",
        StageName.VIDEO: "bound_shot_ids",
        StageName.EDIT: "timeline",
    },
    ChangeScope.CHARACTER.value: {
        StageName.ASSETS: "character_ids",
        StageName.VIDEO: "character_shot_ids",
        StageName.EDIT: "timeline",
    },
    ChangeScope.SHOT.value: {
        StageName.STORYBOARD: "shot_ids",
        StageName.VIDEO: "shot_ids",
        StageName.EDIT: "timeline",
    },
    ChangeScope.SUBTITLE_STYLE.value: {
        StageName.EDIT: "subtitles",
        StageName.EVAL: "full",
        StageName.DELIVER: "full",
    },
}


def _ids(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("Change request item IDs cannot be empty")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class ChangeRequest:
    stage: StageName
    dialogue_ids: tuple[str, ...] = ()
    character_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()
    subtitle_style: bool = False
    scope: ChangeScope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", StageName(self.stage))
        object.__setattr__(self, "dialogue_ids", _ids(self.dialogue_ids))
        object.__setattr__(self, "character_ids", _ids(self.character_ids))
        object.__setattr__(self, "shot_ids", _ids(self.shot_ids))
        explicit = ChangeScope(self.scope) if self.scope is not None else None
        inferred = tuple(
            scope
            for scope, selected in (
                (ChangeScope.DIALOGUE, bool(self.dialogue_ids)),
                (ChangeScope.CHARACTER, bool(self.character_ids)),
                (ChangeScope.SHOT, bool(self.shot_ids)),
                (ChangeScope.SUBTITLE_STYLE, bool(self.subtitle_style)),
            )
            if selected
        )
        if explicit is None and len(inferred) != 1:
            raise ValueError("Change request must select exactly one scope")
        if explicit is not None and inferred and inferred != (explicit,):
            raise ValueError("Explicit change scope conflicts with selected items")
        object.__setattr__(self, "scope", explicit or inferred[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "scope": self.scope.value,
            "dialogue_ids": list(self.dialogue_ids),
            "character_ids": list(self.character_ids),
            "shot_ids": list(self.shot_ids),
            "subtitle_style": self.subtitle_style,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChangeRequest:
        return cls(
            stage=StageName(str(value["stage"])),
            dialogue_ids=tuple(value.get("dialogue_ids") or ()),
            character_ids=tuple(value.get("character_ids") or ()),
            shot_ids=tuple(value.get("shot_ids") or ()),
            subtitle_style=bool(value.get("subtitle_style")),
            scope=ChangeScope(str(value["scope"])),
        )


@dataclass(frozen=True)
class ImpactEntry:
    stage: StageName
    item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", StageName(self.stage))
        object.__setattr__(self, "item_ids", _ids(self.item_ids))

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage.value, "item_ids": list(self.item_ids)}


@dataclass(frozen=True)
class ImpactPlan:
    plan_id: str
    request: ChangeRequest
    entries: tuple[ImpactEntry, ...]
    preserved_artifacts: tuple[str, ...]
    package_sha256: str
    schema_version: str = IMPACT_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != IMPACT_PLAN_SCHEMA:
            raise ValueError(f"Unsupported impact plan schema: {self.schema_version}")
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(
            self,
            "preserved_artifacts",
            tuple(dict.fromkeys(map(str, self.preserved_artifacts))),
        )

    @property
    def affected(self) -> Mapping[StageName, tuple[str, ...]]:
        return MappingProxyType({entry.stage: entry.item_ids for entry in self.entries})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "request": self.request.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
            "affected": {
                entry.stage.value: list(entry.item_ids) for entry in self.entries
            },
            "preserved_artifacts": list(self.preserved_artifacts),
            "package_sha256": self.package_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ImpactPlan:
        return cls(
            plan_id=str(value["plan_id"]),
            request=ChangeRequest.from_dict(dict(value["request"])),
            entries=tuple(
                ImpactEntry(
                    stage=StageName(str(item["stage"])),
                    item_ids=tuple(item.get("item_ids") or ()),
                )
                for item in value.get("entries") or ()
            ),
            preserved_artifacts=tuple(value.get("preserved_artifacts") or ()),
            package_sha256=str(value["package_sha256"]),
            schema_version=str(value.get("schema_version", "")),
        )


def _package_sha256(root: Path) -> str:
    return hashlib.sha256((root / "production_package.json").read_bytes()).hexdigest()


def _episode_payload(root: Path) -> dict[str, Any]:
    candidates = (
        root / "stages" / "storyboard" / "episode.json",
        root / "stages" / "script" / "script.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        payload = read_json_object(path)
        nested = payload.get("episode_draft")
        return dict(nested) if isinstance(nested, dict) else payload
    raise FileNotFoundError("Impact preview requires a storyboard episode snapshot")


def _shot_rows(episode: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = episode.get("shots")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Storyboard episode shots are invalid")
    ordered = sorted((dict(row) for row in rows), key=lambda row: int(row["index"]))
    shot_ids = [str(row.get("id") or "") for row in ordered]
    if not all(shot_ids) or len(set(shot_ids)) != len(shot_ids):
        raise ValueError("Storyboard shot IDs must be non-empty and unique")
    return tuple(ordered)


def _require_known(requested: tuple[str, ...], known: set[str], label: str) -> None:
    missing = tuple(value for value in requested if value not in known)
    if missing:
        raise ValueError(f"Unknown {label}: {', '.join(missing)}")


def _dialogue_bindings(shots: tuple[dict[str, Any], ...]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    ordinal = 0
    for shot in shots:
        lines = shot.get("dialogue")
        if lines is None:
            lines = []
        if not isinstance(lines, list):
            raise ValueError("Storyboard dialogue must be a list")
        for line in lines:
            if not isinstance(line, dict):
                raise ValueError("Storyboard dialogue entry must be an object")
            ordinal += 1
            dialogue_id = str(line.get("id") or f"d{ordinal}")
            if dialogue_id in bindings:
                raise ValueError(f"Duplicate dialogue ID: {dialogue_id}")
            bindings[dialogue_id] = str(shot["id"])
    return bindings


def _expand_request(
    request: ChangeRequest, episode: Mapping[str, Any]
) -> tuple[ImpactEntry, ...]:
    shots = _shot_rows(episode)
    ordered_shot_ids = tuple(str(shot["id"]) for shot in shots)
    if request.scope is ChangeScope.DIALOGUE:
        bindings = _dialogue_bindings(shots)
        _require_known(request.dialogue_ids, set(bindings), "dialogue IDs")
        bound = tuple(
            shot_id
            for shot_id in ordered_shot_ids
            if any(bindings[item] == shot_id for item in request.dialogue_ids)
        )
        affected = {
            StageName.AUDIO: request.dialogue_ids,
            StageName.VIDEO: bound,
            StageName.EDIT: ("timeline",),
        }
    elif request.scope is ChangeScope.CHARACTER:
        characters = episode.get("characters") or ()
        known = {
            str(item.get("id") or "")
            for item in characters
            if isinstance(item, dict)
        }
        _require_known(request.character_ids, known, "character IDs")
        character_shots = tuple(
            str(shot["id"])
            for shot in shots
            if set(map(str, shot.get("character_ids") or ()))
            & set(request.character_ids)
        )
        affected = {
            StageName.ASSETS: request.character_ids,
            StageName.VIDEO: character_shots,
            StageName.EDIT: ("timeline",),
        }
    elif request.scope is ChangeScope.SHOT:
        _require_known(request.shot_ids, set(ordered_shot_ids), "shot IDs")
        selected = tuple(
            shot_id for shot_id in ordered_shot_ids if shot_id in request.shot_ids
        )
        affected = {
            StageName.STORYBOARD: selected,
            StageName.VIDEO: selected,
            StageName.EDIT: ("timeline",),
        }
    else:
        affected = {
            StageName.EDIT: ("subtitles",),
            StageName.EVAL: ("full",),
            StageName.DELIVER: ("full",),
        }
    return tuple(ImpactEntry(stage, item_ids) for stage, item_ids in affected.items())


def _preserved_video_artifacts(
    root: Path,
    package: ProductionPackage,
    episode: Mapping[str, Any],
    entries: tuple[ImpactEntry, ...],
) -> tuple[str, ...]:
    video_entry = next(
        (entry for entry in entries if entry.stage is StageName.VIDEO), None
    )
    manifest_path = root / "stages" / "video" / "video_manifest.json"
    if video_entry is None or not manifest_path.is_file():
        return ()
    manifest = read_json_object(manifest_path)
    clip_by_shot = manifest.get("clip_by_shot")
    if not isinstance(clip_by_shot, dict):
        clips = tuple(map(str, manifest.get("clips") or ()))
        shots = _shot_rows(episode)
        clip_by_shot = {
            str(shot["id"]): clip for shot, clip in zip(shots, clips, strict=False)
        }
    video_record = next(
        record for record in package.stages if record.stage is StageName.VIDEO
    )
    registered = {
        str(Path(value).expanduser().resolve()) for value in video_record.artifacts
    }
    affected = set(video_entry.item_ids)
    preserved: list[str] = []
    for shot in _shot_rows(episode):
        shot_id = str(shot["id"])
        raw_path = clip_by_shot.get(shot_id)
        if shot_id in affected or not raw_path:
            continue
        path = Path(str(raw_path)).expanduser().resolve()
        if path.is_file() and str(path) in registered:
            preserved.append(str(path))
    return tuple(preserved)


def _plan_payload_without_id(
    request: ChangeRequest,
    entries: tuple[ImpactEntry, ...],
    preserved_artifacts: tuple[str, ...],
    package_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": IMPACT_PLAN_SCHEMA,
        "request": request.to_dict(),
        "entries": [entry.to_dict() for entry in entries],
        "preserved_artifacts": list(preserved_artifacts),
        "package_sha256": package_sha256,
    }


def preview_impact(project_dir: str | Path, request: ChangeRequest) -> ImpactPlan:
    root = Path(project_dir).expanduser().resolve()
    package = load_production_package(root)
    episode = _episode_payload(root)
    entries = _expand_request(request, episode)
    package_sha256 = _package_sha256(root)
    preserved = _preserved_video_artifacts(root, package, episode, entries)
    seed = _plan_payload_without_id(request, entries, preserved, package_sha256)
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    plan_id = hashlib.sha256(encoded).hexdigest()[:24]
    plan = ImpactPlan(plan_id, request, entries, preserved, package_sha256)
    plans_dir = root / "impact_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / f"{plan_id}.json"
    serialized = json.dumps(
        plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != serialized:
            raise ValueError(f"Impact plan identity collision: {plan_id}")
    else:
        path.write_text(serialized, encoding="utf-8")
    return plan


def _load_plan(root: Path, plan_id: str) -> ImpactPlan:
    if not plan_id or Path(plan_id).name != plan_id:
        raise ValueError("Impact plan ID must be a safe path component")
    path = root / "impact_plans" / f"{plan_id}.json"
    plan = ImpactPlan.from_dict(read_json_object(path))
    if plan.plan_id != plan_id:
        raise ValueError("Impact plan identity does not match its file")
    seed = _plan_payload_without_id(
        plan.request,
        plan.entries,
        plan.preserved_artifacts,
        plan.package_sha256,
    )
    encoded = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest()[:24] != plan.plan_id:
        raise ValueError("Impact plan content has changed")
    return plan


def apply_impact_plan(project_dir: str | Path, plan_id: str) -> ProductionPackage:
    root = Path(project_dir).expanduser().resolve()
    plan = _load_plan(root, plan_id)
    return apply_repair_state(
        root,
        plan_id=plan.plan_id,
        request_stage=plan.request.stage,
        affected={entry.stage.value: entry.item_ids for entry in plan.entries},
        preserved_artifacts=plan.preserved_artifacts,
        expected_package_sha256=plan.package_sha256,
    )


def load_active_repair_scope(
    project_dir: str | Path,
) -> Mapping[str, tuple[str, ...]]:
    state = load_active_repair_state(project_dir)
    affected = state.get("affected") or {}
    if not isinstance(affected, dict):
        raise ValueError("Active repair scope is invalid")
    return MappingProxyType(
        {str(stage): tuple(map(str, item_ids)) for stage, item_ids in affected.items()}
    )


def registered_preserved_artifacts(project_dir: str | Path) -> tuple[Path, ...]:
    state = load_active_repair_state(project_dir)
    return tuple(
        Path(value).expanduser().resolve()
        for value in state.get("preserved_artifacts") or ()
    )
