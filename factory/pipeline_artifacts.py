from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from .file_io import sha256_file, write_json_atomic
from .pipeline_contracts import StageName

if TYPE_CHECKING:
    from .pipeline_context import StageContext


STAGE_MANIFEST_SCHEMA = "motion-comic-factory.stage-manifest.v1"


def stage_dir(project_dir: str | Path, stage: StageName | str) -> Path:
    root = Path(project_dir).expanduser()
    if root.is_symlink():
        raise ValueError("project directory cannot be a symlink")
    directory = root / "stages" / StageName(stage).value
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError("stage directory cannot be a symlink")
    return directory.resolve()


def write_stage_manifest(
    context: StageContext,
    *,
    artifacts: Sequence[str | Path],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    directory = context.stage_dir
    relative_artifacts: list[str] = []
    artifact_sha256: dict[str, str] = {}
    for raw_path in artifacts:
        path = Path(raw_path).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Stage artifact must be a regular file: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(directory)
        except ValueError as exc:
            raise ValueError(
                "Stage artifact must be inside its stage directory"
            ) from exc
        relative_value = str(relative)
        relative_artifacts.append(relative_value)
        artifact_sha256[relative_value] = sha256_file(resolved)
    manifest_path = directory / "manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "schema_version": STAGE_MANIFEST_SCHEMA,
            "project_id": context.spec.project_id,
            "mode": context.spec.mode.value,
            "stage": context.stage.value,
            "executor_id": context.step.executor_id,
            "artifacts": list(dict.fromkeys(relative_artifacts)),
            "artifact_sha256": artifact_sha256,
            "metadata": dict(metadata or {}),
        },
    )
    return manifest_path


def load_stage_manifest(
    project_dir: str | Path,
    stage: StageName | str,
) -> dict[str, Any]:
    selected = StageName(stage)
    path = stage_dir(project_dir, selected) / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"Stage manifest is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Stage manifest must be a JSON object: {path}")
    if value.get("schema_version") != STAGE_MANIFEST_SCHEMA:
        raise ValueError(f"Unsupported stage manifest schema: {path}")
    if value.get("stage") != selected.value:
        raise ValueError(f"Stage manifest does not match {selected.value}: {path}")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or not all(
        isinstance(item, str) and item for item in artifacts
    ):
        raise ValueError(f"Stage manifest artifacts are invalid: {path}")
    hashes = value.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(artifacts):
        raise ValueError(f"Stage manifest artifact hashes are invalid: {path}")
    return value


def manifest_artifact_paths(
    project_dir: str | Path,
    stage: StageName | str,
) -> tuple[Path, ...]:
    selected = StageName(stage)
    directory = stage_dir(project_dir, selected)
    manifest = load_stage_manifest(project_dir, selected)
    paths: list[Path] = []
    for relative in manifest["artifacts"]:
        candidate = directory / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError(f"Stage artifact is missing: {candidate}")
        resolved = candidate.resolve()
        try:
            resolved.relative_to(directory)
        except ValueError as exc:
            raise ValueError("Stage artifact escaped its stage directory") from exc
        paths.append(resolved)
    return tuple(paths)


def stage_manifest_integrity_issue(
    project_dir: str | Path,
    stage: StageName | str,
) -> str:
    selected = StageName(stage)
    manifest = load_stage_manifest(project_dir, selected)
    directory = stage_dir(project_dir, selected)
    hashes = manifest["artifact_sha256"]
    for relative in manifest["artifacts"]:
        path = directory / relative
        if path.is_symlink() or not path.is_file():
            return f"Stage artifact is missing: {path}"
        if sha256_file(path) != hashes[relative]:
            return f"Stage artifact changed after completion: {path}"
    return ""
