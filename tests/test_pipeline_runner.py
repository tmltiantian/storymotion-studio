from __future__ import annotations

from pathlib import Path

from factory.pipeline_context import StageContext, StageExecution
from factory.pipeline_contracts import ProjectMode, ProjectSpec, StageName, StageState
from factory.pipeline_runner import pipeline_status, resume_pipeline, run_pipeline
from factory.pipeline_store import create_project, load_production_package, update_stage


def _project(tmp_path: Path, mode: ProjectMode = ProjectMode.ORIGINAL) -> Path:
    root = tmp_path / "project"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    create_project(
        root,
        ProjectSpec(
            project_id="episode",
            title="Episode",
            mode=mode,
            input=(
                {"kind": "idea", "text": "two cats"}
                if mode is ProjectMode.ORIGINAL
                else {"kind": "reference", "path": str(source.resolve())}
            ),
            output_dir=(tmp_path / "output").resolve(),
        ),
    )
    return root


def _passing_executor(calls: list[StageName]):
    def execute(context: StageContext) -> StageExecution:
        calls.append(context.stage)
        artifact = context.stage_dir / f"{context.stage.value}.json"
        artifact.write_text("{}", encoding="utf-8")
        return StageExecution.passed(
            executor=context.step.executor_id,
            artifacts=(artifact,),
        )

    return execute


def test_run_executes_exactly_one_executor_per_stage_through_boundary(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []

    result = run_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == [StageName.CONCEPT, StageName.SCRIPT, StageName.STORYBOARD]
    assert result.completed_stages == tuple(calls)
    assert result.next_stage is StageName.ASSETS
    assert pipeline_status(root)["next_stage"] == "assets"


def test_passed_stage_registers_own_manifest_and_artifact(tmp_path: Path) -> None:
    root = _project(tmp_path)

    run_pipeline(
        root,
        through=StageName.CONCEPT,
        executor=_passing_executor([]),
    )
    record = load_production_package(root).stages[0]

    assert [Path(path).name for path in record.artifacts] == [
        "manifest.json",
        "concept.json",
    ]
    assert all("stages/concept" in path for path in record.artifacts)


def test_resume_reruns_stage_and_downstream_when_artifact_is_missing(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor([]),
    )
    script_record = load_production_package(root).stages[1]
    Path(script_record.artifacts[-1]).unlink()
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == [StageName.SCRIPT, StageName.STORYBOARD]


def test_resume_reruns_stage_and_downstream_when_artifact_content_changed(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor([]),
    )
    script_record = load_production_package(root).stages[1]
    Path(script_record.artifacts[-1]).write_text('{"edited":true}', encoding="utf-8")
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == [StageName.SCRIPT, StageName.STORYBOARD]


def test_resume_skips_complete_stage_when_artifacts_are_current(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.SCRIPT,
        executor=_passing_executor([]),
    )
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == [StageName.STORYBOARD]


def test_resume_preserves_imported_legacy_signature(tmp_path: Path) -> None:
    root = _project(tmp_path)
    update_stage(
        root,
        StageName.CONCEPT,
        StageState.PASSED,
        executor="legacy-import",
        input_signature="legacy:/archived/project",
    )
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.CONCEPT,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == []


def test_failed_executor_stops_without_touching_later_stage(tmp_path: Path) -> None:
    root = _project(tmp_path)

    def execute(context: StageContext) -> StageExecution:
        if context.stage is StageName.SCRIPT:
            return StageExecution.failed("bad script", executor=context.step.executor_id)
        return _passing_executor([])(context)

    result = run_pipeline(root, executor=execute)
    status = pipeline_status(root)

    assert result.success is False
    assert result.stopped_at is StageName.SCRIPT
    assert result.stopped_state is StageState.FAILED
    assert status["stages"]["storyboard"] == StageState.PENDING.value


def test_manual_gate_prepares_artifacts_then_blocks_for_approval(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []

    result = run_pipeline(
        root,
        through=StageName.ASSETS,
        executor=_passing_executor(calls),
    )
    package = load_production_package(root)

    assert result.success is False
    assert result.stopped_at is StageName.ASSETS
    assert result.stopped_state is StageState.BLOCKED
    assert package.stages[3].artifacts
    assert calls[-1] is StageName.ASSETS
    status = pipeline_status(root)
    assert status["required_action"] == "approve_review_evidence"
    assert status["stage_details"]["assets"]["artifacts"]


def test_cloud_gate_blocks_before_executor_without_live_permission(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, ProjectMode.REPLICA)
    for stage in (
        StageName.CONCEPT,
        StageName.SCRIPT,
        StageName.STORYBOARD,
        StageName.ASSETS,
        StageName.AUDIO,
    ):
        update_stage(root, stage, StageState.PASSED, executor="fixture")
    calls: list[StageName] = []

    result = run_pipeline(
        root,
        through=StageName.VIDEO,
        executor=_passing_executor(calls),
    )

    assert result.success is False
    assert result.stopped_at is StageName.VIDEO
    assert calls == []
    assert pipeline_status(root)["required_action"] == "enable_live_generation"


def test_executor_receives_project_stage_mode_and_live_context(tmp_path: Path) -> None:
    root = _project(tmp_path)
    received: list[StageContext] = []

    def execute(context: StageContext) -> StageExecution:
        received.append(context)
        return _passing_executor([])(context)

    run_pipeline(
        root,
        through=StageName.CONCEPT,
        enable_live=True,
        executor=execute,
    )

    assert received[0].project_dir == root.resolve()
    assert received[0].spec.mode is ProjectMode.ORIGINAL
    assert received[0].stage is StageName.CONCEPT
    assert received[0].enable_live is True
