from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.pipeline_context import StageContext, StageExecution
from factory.pipeline_contracts import (
    PIPELINE_STAGES,
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from factory.pipeline_runner import pipeline_status, resume_pipeline, run_pipeline
from factory.pipeline_modes import ModeAdapter, ModeStep, get_mode_adapter
from factory.pipeline_review import ApprovalPreset, ReviewConfig
from factory.pipeline_store import (
    approve_review_bundle,
    create_project,
    load_production_package,
    request_stage_changes,
    update_stage,
)


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


def _review_config(**overrides: ReviewPolicy) -> ReviewConfig:
    policies = {stage: ReviewPolicy.AUTOMATIC for stage in PIPELINE_STAGES}
    policies.update({StageName(stage): policy for stage, policy in overrides.items()})
    return ReviewConfig(ApprovalPreset.QUICK, policies)


def test_manual_policy_runs_stage_then_waits_for_review(tmp_path: Path) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []

    result = run_pipeline(
        root,
        through=StageName.SCRIPT,
        executor=_passing_executor(calls),
        review_config=_review_config(script=ReviewPolicy.MANUAL),
    )
    record = load_production_package(root).stages[1]

    assert calls == [StageName.CONCEPT, StageName.SCRIPT]
    assert record.state is StageState.PASSED
    assert record.review_policy is ReviewPolicy.MANUAL
    assert record.review_state is ReviewState.AWAITING_REVIEW
    assert record.review_blocks_progress is True
    assert record.revision == 1
    assert result.next_stage is StageName.SCRIPT


def test_revision_write_failure_leaves_manual_execution_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []

    def fail_revision_write(*_args, **_kwargs):
        raise OSError("forced revision write failure")

    monkeypatch.setattr(
        "factory.pipeline_runner.write_stage_revision", fail_revision_write
    )

    with pytest.raises(OSError, match="forced revision write failure"):
        run_pipeline(
            root,
            through=StageName.CONCEPT,
            executor=_passing_executor(calls),
            review_config=_review_config(concept=ReviewPolicy.MANUAL),
        )

    record = load_production_package(root).stages[0]
    assert calls == [StageName.CONCEPT]
    assert record.state is StageState.RUNNING
    assert record.review_state is ReviewState.NOT_READY
    assert record.state is not StageState.BLOCKED


def test_resume_reruns_passed_stage_with_incomplete_review_metadata(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    artifact = tmp_path / "interrupted-concept.json"
    artifact.write_text("{}", encoding="utf-8")
    update_stage(
        root,
        StageName.CONCEPT,
        StageState.PASSED,
        executor="generic.concept",
        artifacts=(str(artifact),),
        review_policy=ReviewPolicy.MANUAL,
        review_state=ReviewState.NOT_READY,
        review_blocks_progress=False,
    )
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.CONCEPT,
        executor=_passing_executor(calls),
        review_config=_review_config(concept=ReviewPolicy.MANUAL),
    )
    record = load_production_package(root).stages[0]

    assert calls == [StageName.CONCEPT]
    assert result.next_stage is StageName.CONCEPT
    assert record.state is StageState.PASSED
    assert record.review_state is ReviewState.AWAITING_REVIEW
    assert record.review_blocks_progress is True
    assert record.state is not StageState.BLOCKED


def test_grouped_policy_runs_members_and_stops_at_group_terminal(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []
    grouped = _review_config(
        script=ReviewPolicy.GROUPED,
        storyboard=ReviewPolicy.GROUPED,
        assets=ReviewPolicy.GROUPED,
        audio=ReviewPolicy.GROUPED,
    )

    result = run_pipeline(
        root,
        through=StageName.AUDIO,
        executor=_passing_executor(calls),
        review_config=grouped,
    )
    package = load_production_package(root)
    records = package.stages[1:5]

    assert calls == list(PIPELINE_STAGES[:5])
    assert all(record.state is StageState.PASSED for record in records)
    assert all(record.review_state is ReviewState.AWAITING_REVIEW for record in records)
    assert [record.review_blocks_progress for record in records] == [
        False,
        False,
        False,
        True,
    ]
    assert result.next_stage is StageName.AUDIO


def test_grouped_bundle_approval_updates_every_bound_revision(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    grouped_stages = (
        StageName.SCRIPT,
        StageName.STORYBOARD,
        StageName.ASSETS,
        StageName.AUDIO,
    )
    grouped = _review_config(
        **{stage.value: ReviewPolicy.GROUPED for stage in grouped_stages}
    )
    run_pipeline(
        root,
        through=StageName.AUDIO,
        executor=_passing_executor([]),
        review_config=grouped,
    )
    evidence = tmp_path / "story-review.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")

    package = approve_review_bundle(
        root,
        grouped_stages,
        note="story package approved",
        evidence=(evidence,),
    )

    records = package.stages[1:5]
    assert all(record.review_state is ReviewState.APPROVED for record in records)
    assert all(record.review_blocks_progress is False for record in records)
    assert all(record.revision == 1 for record in records)
    transaction_ids = {record.review_transaction_id for record in records}
    assert len(transaction_ids) == 1
    assert "" not in transaction_ids
    transaction_id = transaction_ids.pop()
    persisted_reviews = [
        json.loads(
            (root / "reviews" / f"{stage.value}.review.json").read_text(
                encoding="utf-8"
            )
        )
        for stage in grouped_stages
    ]
    assert {review["transaction_id"] for review in persisted_reviews} == {
        transaction_id
    }


def test_grouped_bundle_rejects_partial_span_before_updating_reviews(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    grouped_stages = (
        StageName.SCRIPT,
        StageName.STORYBOARD,
        StageName.ASSETS,
        StageName.AUDIO,
    )
    run_pipeline(
        root,
        through=StageName.AUDIO,
        executor=_passing_executor([]),
        review_config=_review_config(
            **{stage.value: ReviewPolicy.GROUPED for stage in grouped_stages}
        ),
    )
    evidence = tmp_path / "story-review.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="complete contiguous"):
        approve_review_bundle(
            root,
            (StageName.AUDIO,),
            note="partial approval",
            evidence=(evidence,),
        )

    records = load_production_package(root).stages[1:5]
    assert all(record.review_state is ReviewState.AWAITING_REVIEW for record in records)


def test_automatic_policy_continues_without_user_action(tmp_path: Path) -> None:
    root = _project(tmp_path)

    result = run_pipeline(
        root,
        through=StageName.CONCEPT,
        executor=_passing_executor([]),
        review_config=_review_config(),
    )
    record = load_production_package(root).stages[0]

    assert result.success is True
    assert record.state is StageState.PASSED
    assert record.review_state is ReviewState.AUTO_APPROVED
    assert record.review_blocks_progress is False
    assert record.revision == 1


def test_not_applicable_policy_skips_executor_and_continues(tmp_path: Path) -> None:
    root = _project(tmp_path)
    calls: list[StageName] = []

    result = run_pipeline(
        root,
        through=StageName.CONCEPT,
        executor=_passing_executor(calls),
        review_config=_review_config(concept=ReviewPolicy.NOT_APPLICABLE),
    )
    record = load_production_package(root).stages[0]

    assert result.success is True
    assert calls == []
    assert record.state is StageState.PASSED
    assert record.review_state is ReviewState.SKIPPED
    assert record.review_policy is ReviewPolicy.NOT_APPLICABLE


def test_status_exposes_review_progress_fields(tmp_path: Path) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.SCRIPT,
        executor=_passing_executor([]),
        review_config=_review_config(script=ReviewPolicy.MANUAL),
    )

    status = pipeline_status(root)

    assert status["next_stage"] == "script"
    assert status["review_state"] == "awaiting_review"
    assert status["review_policy"] == "manual"
    assert status["current_revision"] == 1
    assert status["required_action"] == "approve_review_evidence"
    assert status["stage_details"]["script"]["review_state"] == "awaiting_review"
    assert status["stage_details"]["script"]["current_revision"] == 1


def test_request_changes_records_current_revision_without_blocking_execution(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.SCRIPT,
        executor=_passing_executor([]),
        review_config=_review_config(script=ReviewPolicy.MANUAL),
    )

    package = request_stage_changes(
        root,
        StageName.SCRIPT,
        revision=1,
        reason="shorten the second scene",
    )
    review = json.loads(
        (root / "reviews" / "script.review.json").read_text(encoding="utf-8")
    )

    record = package.stages[1]
    assert record.state is StageState.PASSED
    assert record.review_state is ReviewState.CHANGES_REQUESTED
    assert record.review_blocks_progress is True
    assert review["revision"] == 1
    assert review["state"] == "changes_requested"
    assert review["note"] == "shorten the second scene"
    assert pipeline_status(root)["required_action"] == "address_review_changes"


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


def test_resume_reruns_changed_stage_revision_and_downstream(
    tmp_path: Path, monkeypatch
) -> None:
    root = _project(tmp_path)
    run_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor([]),
    )
    original = get_mode_adapter(ProjectMode.ORIGINAL)
    steps = tuple(
        ModeStep(
            step.stage,
            step.executor_id,
            version=step.version + (1 if step.stage is StageName.SCRIPT else 0),
            requires_live=step.requires_live,
            manual_gate=step.manual_gate,
            prepare_before_gate=step.prepare_before_gate,
        )
        for step in original.stage_steps.values()
    )
    revised = ModeAdapter(ProjectMode.ORIGINAL, steps)
    monkeypatch.setattr(
        "factory.pipeline_runner.get_mode_adapter",
        lambda mode: revised,
    )
    calls: list[StageName] = []

    result = resume_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=_passing_executor(calls),
    )

    assert result.success is True
    assert calls == [StageName.SCRIPT, StageName.STORYBOARD]


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
            return StageExecution.failed(
                "bad script", executor=context.step.executor_id
            )
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
