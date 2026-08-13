from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .pipeline_contracts import (
    PIPELINE_STAGES,
    ProjectMode,
    ProjectSpec,
    StageName,
    StageRecord,
    StageState,
)
from .pipeline_store import create_project, save_production_package


_STAGE_PATTERNS: dict[StageName, tuple[str, ...]] = {
    StageName.CONCEPT: (),
    StageName.SCRIPT: (
        "episode.json",
        "pet_sitcom_plan.json",
        "series_plan.json",
        "reference/shot_timeline.json",
    ),
    StageName.STORYBOARD: (
        "storyboard_preview.md",
        "episode.json",
        "reference/shot_timeline.json",
        "pet_sitcom_plan.json",
    ),
    StageName.ASSETS: (
        "character_assets.json",
        "assets/asset_manifest.json",
        "anchor_generation_report.json",
        "characters/*",
        "scenes/*",
    ),
    StageName.AUDIO: (
        "audio_manifest.json",
        "audio/audio_manifest.json",
        "voiceover/voiceover.m4a",
        "voiceover/voiceover.wav",
        "audio/*.mp3",
        "audio/*.m4a",
        "audio/*.wav",
    ),
    StageName.VIDEO: (
        "selected_candidates.json",
        "generation_report.json",
        "shots/*.mp4",
        "shots/**/*.mp4",
    ),
    StageName.EDIT: (
        "final_preview.mp4",
        "final/*.mp4",
        "deliveries/*master.mp4",
        "final/releases/**/*master.mp4",
    ),
    StageName.EVAL: (
        "deliveries/eval_result.json",
        "eval_result.json",
        "evidence/final_technical_qc.json",
        "shot_review.json",
    ),
    StageName.DELIVER: (
        "final/*.mp4",
        "deliveries/*master.mp4",
        "final/releases/**/*master.mp4",
        "final_preview.mp4",
    ),
}


def _matches(root: Path, patterns: Iterable[str]) -> tuple[str, ...]:
    matches: list[str] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not path.is_symlink():
                matches.append(str(path.resolve()))
    return tuple(sorted(dict.fromkeys(matches)))


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _explicit_eval_passed(paths: tuple[str, ...]) -> bool:
    for raw_path in paths:
        path = Path(raw_path)
        if path.name != "eval_result.json":
            continue
        value = _json_object(path)
        if str(value.get("status", "")).upper() == "PASS":
            return True
        if value.get("passed") is True or value.get("success") is True:
            return True
    return False


def _eval_bound_output(eval_paths: tuple[str, ...]) -> tuple[str, ...]:
    for raw_path in eval_paths:
        value = _json_object(Path(raw_path))
        artifact_manifest = value.get("artifact_manifest")
        if not isinstance(artifact_manifest, dict):
            continue
        video = artifact_manifest.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("path"), str):
            continue
        path = Path(str(video["path"])).expanduser()
        if path.is_file() and not path.is_symlink():
            return (str(path.resolve()),)
    return ()


def _final_outputs(root: Path, eval_paths: tuple[str, ...]) -> tuple[str, ...]:
    bound = _eval_bound_output(eval_paths)
    if bound:
        return bound
    direct = _matches(root, ("final/*.mp4", "deliveries/*master.mp4", "final_preview.mp4"))
    if direct:
        return direct
    releases = _matches(root, ("final/releases/**/*master.mp4",))
    if not releases:
        return ()
    newest_parent = max(
        (Path(path).parent for path in releases),
        key=lambda directory: directory.stat().st_mtime_ns,
    )
    return tuple(path for path in releases if Path(path).parent == newest_parent)


def migrate_existing_project(
    legacy_root: str | Path,
    project_dir: str | Path,
    *,
    project_id: str,
    title: str,
    mode: ProjectMode | str,
    factory_config: str | Path = "",
) -> dict[str, object]:
    legacy = Path(legacy_root).expanduser().resolve(strict=True)
    if not legacy.is_dir() or legacy.is_symlink():
        raise ValueError("legacy_root must be a real local directory")
    destination = Path(project_dir).expanduser()
    if (destination / "project.json").exists() or (
        destination / "production_package.json"
    ).exists():
        raise FileExistsError(f"Unified project already exists: {destination}")
    selected_mode = ProjectMode(mode)
    spec = ProjectSpec(
        project_id=project_id,
        title=title,
        mode=selected_mode,
        input={"kind": "legacy_project", "path": str(legacy)},
        output_dir=destination.resolve() / "output",
        policies={"enable_live": False, "audio_first": True, "migrated": True},
        mode_options={
            "legacy_root": str(legacy),
            "factory_config": str(Path(factory_config).expanduser().resolve())
            if factory_config
            else "",
        },
    )
    package = create_project(destination, spec)
    stage_artifacts = {
        stage: _matches(legacy, _STAGE_PATTERNS[stage]) for stage in PIPELINE_STAGES
    }
    eval_passed = _explicit_eval_passed(stage_artifacts[StageName.EVAL])
    outputs = _final_outputs(legacy, stage_artifacts[StageName.EVAL])
    records: list[StageRecord] = []
    for stage in PIPELINE_STAGES:
        artifacts = stage_artifacts[stage]
        passed = stage is StageName.CONCEPT or bool(artifacts)
        if stage is StageName.EVAL:
            passed = eval_passed
        elif stage is StageName.DELIVER:
            passed = eval_passed and bool(outputs)
        records.append(
            StageRecord(
                stage=stage,
                state=StageState.PASSED if passed else StageState.PENDING,
                executor="legacy-import",
                input_signature=f"legacy:{legacy}",
                artifacts=artifacts,
            )
        )
    eval_reports = (
        stage_artifacts[StageName.EVAL] if eval_passed else ()
    )
    migrated = replace(
        package,
        stages=tuple(records),
        final_outputs=outputs if eval_passed else (),
        eval_reports=eval_reports,
    )
    save_production_package(destination, migrated)
    return {
        "success": True,
        "project_id": project_id,
        "mode": selected_mode.value,
        "project_dir": str(destination.resolve()),
        "legacy_root": str(legacy),
        "next_stage": migrated.next_stage.value if migrated.next_stage else "complete",
        "final_outputs": list(migrated.final_outputs),
    }
