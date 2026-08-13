from __future__ import annotations

from typing import Callable

from .pipeline_context import StageContext, StageExecution


NativeStageExecutor = Callable[[StageContext], StageExecution]
_EXECUTORS: dict[str, NativeStageExecutor] = {}
_DEFAULTS_LOADED = False


def register_executor(executor_id: str) -> Callable[[NativeStageExecutor], NativeStageExecutor]:
    value = str(executor_id).strip()
    if not value:
        raise ValueError("executor_id cannot be empty")

    def decorator(executor: NativeStageExecutor) -> NativeStageExecutor:
        existing = _EXECUTORS.get(value)
        if existing is not None and existing is not executor:
            raise ValueError(f"Stage executor is already registered: {value}")
        _EXECUTORS[value] = executor
        return executor

    return decorator


def _load_default_executors() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    from . import pipeline_generic_stages, pipeline_replica_stages  # noqa: F401

    _DEFAULTS_LOADED = True


def resolve_executor(executor_id: str) -> NativeStageExecutor:
    _load_default_executors()
    try:
        return _EXECUTORS[executor_id]
    except KeyError as exc:
        raise ValueError(f"Unknown native stage executor: {executor_id}") from exc


def execute_native_stage(context: StageContext) -> StageExecution:
    return resolve_executor(context.step.executor_id)(context)


def registered_executor_ids() -> tuple[str, ...]:
    _load_default_executors()
    return tuple(sorted(_EXECUTORS))
