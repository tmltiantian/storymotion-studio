from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .file_io import sha256_file, write_json_atomic
from .pet_replica import build_pet_replica_plan
from .pipeline_context import StageContext, StageExecution
from .pipeline_contracts import StageState
from .pipeline_executors import register_executor
from .pipeline_eval import build_specialist_eval


def _source(context: StageContext) -> Path:
    raw = str(context.spec.input.get("path") or "").strip()
    if not raw:
        raise ValueError("Replica mode requires input.path")
    path = Path(raw).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"Replica source video is missing: {path}")
    return path


def _workspace(context: StageContext) -> Path:
    configured = str(
        context.spec.mode_options.get("replica_workspace") or ""
    ).strip()
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else context.spec.output_dir.resolve()
    )
    if root.is_symlink():
        raise ValueError("Replica workspace cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _args(context: StageContext) -> argparse.Namespace:
    return argparse.Namespace(
        stage="",
        source=str(_source(context)),
        output_dir=str(_workspace(context)),
        shot=list(context.spec.mode_options.get("shots") or ()),
        candidate=int(context.spec.mode_options.get("candidate") or 1),
        pilot_only=bool(context.spec.mode_options.get("pilot_only", False)),
        enable_live=context.enable_live,
        replace_stale=bool(
            context.spec.mode_options.get("replace_stale", False)
        ),
        postprocess_lipsync=bool(
            context.spec.mode_options.get("postprocess_lipsync", True)
        ),
    )


def _run_operation(
    context: StageContext, operation: str
) -> tuple[int, dict[str, Any]]:
    from . import pet_replica_cli
    return pet_replica_cli.execute_pet_replica_stage(_args(context), operation)


def _snapshot(
    context: StageContext,
    paths: list[Path],
    *,
    prefix: str = "snapshot",
) -> list[Path]:
    workspace = _workspace(context)
    outputs: list[Path] = []
    for source in paths:
        source = source.expanduser().resolve()
        if source.is_symlink() or not source.is_file():
            continue
        try:
            relative = source.relative_to(workspace)
        except ValueError:
            relative = Path(source.name)
        destination = context.stage_dir / prefix / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        outputs.append(destination)
    return outputs


def _workspace_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        found.extend(path for path in root.glob(pattern) if path.is_file())
    return sorted(set(found))


def _failure(
    context: StageContext,
    message: str,
    artifacts: tuple[Path, ...] = (),
) -> StageExecution:
    return StageExecution(
        state=StageState.FAILED,
        executor=context.step.executor_id,
        artifacts=tuple(str(path) for path in artifacts),
        error=message,
    )


@register_executor("replica.concept")
def execute_concept(context: StageContext) -> StageExecution:
    source = _source(context)
    output = write_json_atomic(
        context.stage_dir / "concept.json",
        {
            "schema_version": "motion-comic-factory.replica-concept.v1",
            "project_id": context.spec.project_id,
            "source_video": str(source),
            "source_sha256": sha256_file(source),
            "intent": "source_locked_reference_replica",
            "replacement_policy": (
                "保留剧情功能、时间结构和镜头关系，替换人物与猫咪身份，"
                "不复用来源人物的可识别身份。"
            ),
        },
    )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(output,))


@register_executor("replica.script")
def execute_script(context: StageContext) -> StageExecution:
    context.require_artifact("concept", "concept.json")
    plan = build_pet_replica_plan(_source(context), _workspace(context))
    output = write_json_atomic(
        context.stage_dir / "script.json",
        {
            "schema_version": "motion-comic-factory.replica-script.v1",
            "project_id": context.spec.project_id,
            "adaptation_mode": "source_locked_replica",
            "duration_seconds": plan.duration_s,
            "pilot_end_seconds": plan.pilot_end_s,
            "characters": {
                key: asdict(value) for key, value in plan.characters.items()
            },
            "story_rule": "剧情节拍和对白功能跟随来源，角色身份全部替换。",
        },
    )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(output,))


@register_executor("replica.storyboard")
def execute_storyboard(context: StageContext) -> StageExecution:
    context.require_artifact("script", "script.json")
    code, detail = _run_operation(context, "plan")
    workspace = _workspace(context)
    snapshots = _snapshot(
        context,
        [
            workspace / "reference" / "shot_timeline.json",
            workspace / "story_contract.md",
            workspace / "reference" / "source_binding.json",
        ],
    )
    report = write_json_atomic(
        context.stage_dir / "storyboard_manifest.json",
        {
            "schema_version": "motion-comic-factory.replica-storyboard.v1",
            "operation": detail,
            "workspace": str(workspace),
        },
    )
    if code != 0:
        return _failure(context, "Replica planning failed", (report, *snapshots))
    return StageExecution.passed(
        executor=context.step.executor_id, artifacts=(report, *snapshots)
    )


@register_executor("replica.assets")
def execute_assets(context: StageContext) -> StageExecution:
    context.require_artifact("storyboard", "storyboard_manifest.json")
    reference_code, reference = _run_operation(context, "reference")
    assets: dict[str, Any] = {"skipped": True}
    assets_code = 1
    if reference_code == 0:
        assets_code, assets = _run_operation(context, "assets")
    workspace = _workspace(context)
    snapshots = _snapshot(
        context,
        _workspace_files(
            workspace,
            (
                "reference/*.json",
                "reference/contact_sheets/*.jpg",
                "assets/*.json",
            ),
        ),
    )
    review = write_json_atomic(
        context.stage_dir / "asset_review.json",
        {
            "schema_version": "motion-comic-factory.replica-assets.v1",
            "project_id": context.spec.project_id,
            "workspace": str(workspace),
            "reference": reference,
            "assets": assets,
            "approved": bool(reference_code == 0 and assets_code == 0),
            "required_review": [
                "逐镜动作、构图和 OCR 标注完整",
                "替换角色身份稳定且不复制来源人物身份",
                "猫咪花色、体型、房间几何与道具状态连续",
            ],
        },
    )
    return StageExecution.passed(
        executor=context.step.executor_id,
        artifacts=(review, *snapshots),
        metadata={"reference_ready": reference_code == 0, "assets_ready": assets_code == 0},
    )


@register_executor("replica.audio")
def execute_audio(context: StageContext) -> StageExecution:
    context.require_artifact("assets", "asset_review.json")
    code, detail = _run_operation(context, "audio")
    workspace = _workspace(context)
    snapshots = _snapshot(
        context,
        _workspace_files(workspace, ("audio/audio_manifest.json", "audio/**/*.wav", "audio/*.aac")),
    )
    report = write_json_atomic(
        context.stage_dir / "audio_manifest.json",
        {
            "schema_version": "motion-comic-factory.replica-audio-stage.v1",
            "workspace": str(workspace),
            "operation": detail,
            "public_release_audio_required": True,
        },
    )
    if code != 0:
        return _failure(context, "Replica audio extraction failed", (report, *snapshots))
    return StageExecution.passed(
        executor=context.step.executor_id, artifacts=(report, *snapshots)
    )


@register_executor("replica.video")
def execute_video(context: StageContext) -> StageExecution:
    context.require_artifact("audio", "audio_manifest.json")
    code, detail = _run_operation(context, "generate")
    workspace = _workspace(context)
    snapshots = _snapshot(
        context,
        _workspace_files(
            workspace,
            ("shots/*/candidate_*.mp4", "shots/*/candidate_*.json", "shots/*/candidate_*.provenance.json", "shots/*.json"),
        ),
    )
    report = write_json_atomic(
        context.stage_dir / "video_manifest.json",
        {
            "schema_version": "motion-comic-factory.replica-video.v1",
            "workspace": str(workspace),
            "operation": detail,
            "candidate_count": sum(path.suffix == ".mp4" for path in snapshots),
        },
    )
    completed = int(detail.get("completed_count") or 0)
    if code != 0 or completed <= 0:
        return _failure(
            context,
            "Replica video generation produced no completed candidates",
            (report, *snapshots),
        )
    return StageExecution.passed(
        executor=context.step.executor_id, artifacts=(report, *snapshots)
    )


@register_executor("replica.edit")
def execute_edit(context: StageContext) -> StageExecution:
    context.require_artifact("video", "video_manifest.json")
    workspace = _workspace(context)
    candidates = _workspace_files(workspace, ("shots/*/candidate_*.mp4",))
    if not candidates:
        return _failure(context, "Replica edit has no video candidates")
    plan = write_json_atomic(
        context.stage_dir / "edit_manifest.json",
        {
            "schema_version": "motion-comic-factory.replica-edit.v1",
            "workspace": str(workspace),
            "candidate_count": len(candidates),
            "candidates": [str(path) for path in candidates],
            "assembly_status": "WAITING_FOR_CANDIDATE_REVIEW",
        },
    )
    return StageExecution.passed(executor=context.step.executor_id, artifacts=(plan,))


@register_executor("replica.eval")
def execute_eval(context: StageContext) -> StageExecution:
    context.require_artifact("edit", "edit_manifest.json")
    code, detail = _run_operation(context, "review")
    workspace = _workspace(context)
    snapshots = _snapshot(
        context,
        _workspace_files(workspace, ("review/**/*.json", "shots/*/*.review.json")),
    )
    evaluation = build_specialist_eval(
        project_id=context.spec.project_id,
        operation_code=code,
        operation=detail,
        candidate_count=len(
            _workspace_files(workspace, ("shots/*/candidate_*.mp4",))
        ),
    )
    report = write_json_atomic(context.stage_dir / "eval_result.json", evaluation)
    if not evaluation["automatic_passed"]:
        return _failure(
            context,
            "; ".join(
                str(item["message"]) for item in evaluation["hard_failures"]
            ),
            (report, *snapshots),
        )
    return StageExecution.passed(
        executor=context.step.executor_id, artifacts=(report, *snapshots)
    )


@register_executor("replica.deliver")
def execute_deliver(context: StageContext) -> StageExecution:
    context.require_artifact("eval", "eval_result.json")
    code, detail = _run_operation(context, "compose")
    workspace = _workspace(context)
    release_files = _workspace_files(workspace, ("final/**/*",))
    snapshots = _snapshot(context, release_files, prefix="release")
    media = [path for path in snapshots if path.suffix.lower() == ".mp4"]
    report = write_json_atomic(
        context.stage_dir / "delivery_manifest.json",
        {
            "schema_version": "motion-comic-factory.replica-delivery.v1",
            "workspace": str(workspace),
            "operation": detail,
            "masters": [str(path.resolve()) for path in media],
            "publication_status": "REVIEW_REQUIRED",
        },
    )
    if code != 0 or not media:
        return _failure(context, "Replica composition produced no release video", (report, *snapshots))
    return StageExecution.passed(
        executor=context.step.executor_id, artifacts=(report, *snapshots)
    )
