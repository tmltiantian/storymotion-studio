from __future__ import annotations

import fcntl
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .file_io import sha256_file
from .pipeline_artifacts import stage_manifest_integrity_issue, write_stage_manifest
from .pipeline_context import StageContext, StageExecution
from .pipeline_contracts import PIPELINE_STAGES, StageName, StageState
from .pipeline_executors import execute_native_stage
from .pipeline_modes import ModeStep, get_mode_adapter
from .pipeline_store import (
    invalidate_stage_and_downstream,
    load_production_package,
    load_project_spec,
    update_stage,
)


_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b", re.I)


@dataclass(frozen=True)
class PipelineRunResult:
    success: bool
    stopped_at: StageName | None
    completed_stages: tuple[StageName, ...]
    next_stage: StageName | None = None
    stopped_state: StageState | None = None


StageExecutor = Callable[[StageContext], StageExecution]


def _signature(spec_sha256: str, stage: StageName, step: ModeStep) -> str:
    payload = {
        "spec_sha256": spec_sha256,
        "stage": stage.value,
        "executor_id": step.executor_id,
        "executor_version": step.version,
        "requires_live": step.requires_live,
        "manual_gate": step.manual_gate,
        "prepare_before_gate": step.prepare_before_gate,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _redact(message: str) -> str:
    return _SECRET.sub("[redacted]", message)


def _missing_artifacts(artifacts: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        artifact
        for artifact in artifacts
        if artifact
        and "://" not in artifact
        and not Path(artifact).expanduser().exists()
    )


def _approval_integrity_issue(artifacts: Sequence[str]) -> str:
    approval_paths = [
        Path(value)
        for value in artifacts
        if Path(value).parent.name == "approvals"
        and Path(value).name.endswith(".approval.json")
    ]
    for approval_path in approval_paths:
        if approval_path.is_symlink():
            return f"Approval record cannot be a symlink: {approval_path}"
        try:
            payload = json.loads(approval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"Approval record is unreadable: {approval_path}"
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != "motion-comic-factory.stage-approval.v1"
        ):
            return f"Approval record is invalid: {approval_path}"
        evidence = payload.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return f"Approval evidence is missing: {approval_path}"
        registered = {
            str(Path(value).expanduser().resolve())
            for value in artifacts
            if Path(value) != approval_path
        }
        bound: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                return f"Approval evidence entry is invalid: {approval_path}"
            path = Path(str(item.get("path") or "")).expanduser()
            expected = str(item.get("sha256") or "")
            if path.is_symlink() or not path.is_file():
                return f"Approval evidence is missing: {path}"
            resolved = str(path.resolve())
            bound.add(resolved)
            if sha256_file(path) != expected:
                return f"Approval evidence changed after review: {path}"
        if bound != registered:
            return f"Approval evidence registration changed: {approval_path}"
    return ""


def _native_manifest_integrity_issue(
    project_dir: Path,
    stage: StageName,
    artifacts: Sequence[str],
) -> str:
    expected = project_dir / "stages" / stage.value / "manifest.json"
    registered = {Path(value).expanduser().resolve() for value in artifacts}
    if expected.resolve() not in registered:
        return ""
    try:
        return stage_manifest_integrity_issue(project_dir, stage)
    except (OSError, ValueError, FileNotFoundError) as exc:
        return str(exc)


def _persist_execution(
    context: StageContext,
    result: StageExecution,
) -> tuple[StageState, tuple[str, ...], tuple[str, ...]]:
    artifacts = tuple(str(Path(path).expanduser().resolve()) for path in result.artifacts)
    state = result.state
    blocked_reasons = result.blocked_reasons
    should_write_manifest = state is StageState.PASSED or bool(artifacts)
    if should_write_manifest:
        manifest = write_stage_manifest(
            context,
            artifacts=artifacts,
            metadata=result.metadata,
        )
        artifacts = (str(manifest.resolve()), *artifacts)
    if state is StageState.PASSED and context.step.manual_gate:
        state = StageState.BLOCKED
        blocked_reasons = (
            f"Manual approval is required after {context.step.executor_id}.",
        )
    return state, artifacts, blocked_reasons


def run_pipeline(
    project_dir: str | Path,
    *,
    through: StageName | None = None,
    enable_live: bool = False,
    executor: StageExecutor = execute_native_stage,
) -> PipelineRunResult:
    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".pipeline.lock"
    completed: list[StageName] = []
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Unified project is already running") from exc
        spec = load_project_spec(root)
        package = load_production_package(root)
        adapter = get_mode_adapter(spec.mode)
        through_index = (
            PIPELINE_STAGES.index(StageName(through))
            if through is not None
            else len(PIPELINE_STAGES) - 1
        )
        for index, stage in enumerate(PIPELINE_STAGES):
            if index > through_index:
                break
            record = next(item for item in package.stages if item.stage is stage)
            step = adapter.stage_steps[stage]
            signature = _signature(spec.sha256, stage, step)
            if record.state is StageState.PASSED:
                missing = _missing_artifacts(record.artifacts)
                integrity_issue = _approval_integrity_issue(record.artifacts)
                if not integrity_issue:
                    integrity_issue = _native_manifest_integrity_issue(
                        root, stage, record.artifacts
                    )
                signature_changed = bool(
                    record.input_signature
                    and not record.input_signature.startswith("legacy:")
                    and record.input_signature != signature
                )
                if not missing and not integrity_issue and not signature_changed:
                    continue
                package = invalidate_stage_and_downstream(
                    root,
                    stage,
                    reason=(
                        f"Registered artifact is missing: {missing[0]}"
                        if missing
                        else integrity_issue
                        or "Stage executor signature changed"
                    ),
                )
            if step.requires_live and not enable_live:
                reason = (
                    "Cloud generation is disabled; rerun with --enable-live after review."
                )
                update_stage(
                    root,
                    stage,
                    StageState.BLOCKED,
                    executor=step.executor_id,
                    input_signature=signature,
                    blocked_reasons=(reason,),
                )
                return PipelineRunResult(
                    False,
                    stage,
                    tuple(completed),
                    next_stage=stage,
                    stopped_state=StageState.BLOCKED,
                )
            if step.manual_gate and not step.prepare_before_gate:
                reason = f"Manual approval is required before {step.executor_id}."
                update_stage(
                    root,
                    stage,
                    StageState.BLOCKED,
                    executor=step.executor_id,
                    input_signature=signature,
                    blocked_reasons=(reason,),
                )
                return PipelineRunResult(
                    False,
                    stage,
                    tuple(completed),
                    next_stage=stage,
                    stopped_state=StageState.BLOCKED,
                )
            update_stage(
                root,
                stage,
                StageState.RUNNING,
                executor=step.executor_id,
                input_signature=signature,
            )
            context = StageContext(root, spec, stage, step, enable_live)
            try:
                result = executor(context)
                state, artifacts, blocked_reasons = _persist_execution(context, result)
            except Exception as exc:
                result = StageExecution.failed(_redact(str(exc)), executor=step.executor_id)
                state, artifacts, blocked_reasons = result.state, (), ()
            package = update_stage(
                root,
                stage,
                state,
                executor=result.executor,
                input_signature=signature,
                artifacts=artifacts,
                blocked_reasons=blocked_reasons,
                error=_redact(result.error),
            )
            if state is not StageState.PASSED:
                return PipelineRunResult(
                    False,
                    stage,
                    tuple(completed),
                    next_stage=stage,
                    stopped_state=state,
                )
            completed.append(stage)
    package = load_production_package(root)
    return PipelineRunResult(True, None, tuple(completed), package.next_stage)


def resume_pipeline(
    project_dir: str | Path,
    *,
    through: StageName | None = None,
    enable_live: bool = False,
    executor: StageExecutor = execute_native_stage,
) -> PipelineRunResult:
    return run_pipeline(
        project_dir,
        through=through,
        enable_live=enable_live,
        executor=executor,
    )


def pipeline_status(project_dir: str | Path) -> dict[str, object]:
    spec = load_project_spec(project_dir)
    package = load_production_package(project_dir)
    next_record = next(
        (record for record in package.stages if record.state is not StageState.PASSED),
        None,
    )
    if next_record is None:
        required_action = "none"
    elif next_record.state is StageState.BLOCKED:
        required_action = (
            "approve_review_evidence"
            if get_mode_adapter(spec.mode).stage_steps[next_record.stage].manual_gate
            else "enable_live_generation"
        )
    elif next_record.state is StageState.FAILED:
        required_action = "fix_stage_error_and_resume"
    else:
        required_action = "run_or_resume"
    return {
        "success": True,
        "project_id": spec.project_id,
        "title": spec.title,
        "mode": spec.mode.value,
        "next_stage": package.next_stage.value if package.next_stage else "complete",
        "stages": {
            record.stage.value: record.state.value for record in package.stages
        },
        "stage_details": {
            record.stage.value: {
                "state": record.state.value,
                "executor": record.executor,
                "artifacts": list(record.artifacts),
                "blocked_reasons": list(record.blocked_reasons),
                "error": record.error,
            }
            for record in package.stages
        },
        "required_action": required_action,
        "final_outputs": list(package.final_outputs),
        "eval_reports": list(package.eval_reports),
    }
