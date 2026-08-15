from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory import pipeline_store
from factory.pipeline_contracts import (
    PIPELINE_STAGES,
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from factory.pipeline_store import (
    approve_review_bundle,
    approve_stage,
    create_project,
    invalidate_stage_and_downstream,
    load_production_package,
    load_project_spec,
    update_stage,
)
from factory.pipeline_review import ApprovalPreset, ReviewConfig
from factory.pipeline_runner import StageExecution, resume_pipeline, run_pipeline


def _spec(tmp_path: Path) -> ProjectSpec:
    return ProjectSpec(
        project_id="episode_01",
        title="Episode 01",
        mode=ProjectMode.ORIGINAL,
        input={"kind": "idea", "text": "two cats investigate a noise"},
        output_dir=tmp_path / "output",
    )


def _grouped_review_project(tmp_path: Path) -> tuple[Path, tuple[StageName, ...]]:
    project_dir = tmp_path / "projects" / "grouped"
    spec = _spec(tmp_path)
    create_project(project_dir, spec)
    grouped_stages = (
        StageName.SCRIPT,
        StageName.STORYBOARD,
        StageName.ASSETS,
        StageName.AUDIO,
    )
    policies = {stage: ReviewPolicy.AUTOMATIC for stage in PIPELINE_STAGES}
    policies.update({stage: ReviewPolicy.GROUPED for stage in grouped_stages})

    def execute(context):
        artifact = context.stage_dir / f"{context.stage.value}.json"
        artifact.write_text("{}", encoding="utf-8")
        return StageExecution.passed(
            executor=context.step.executor_id,
            artifacts=(artifact,),
        )

    run_pipeline(
        project_dir,
        through=StageName.AUDIO,
        executor=execute,
        review_config=ReviewConfig(ApprovalPreset.QUICK, policies),
    )
    return project_dir, grouped_stages


def test_grouped_approval_failure_rolls_back_partial_review_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir, grouped_stages = _grouped_review_project(tmp_path)
    evidence = tmp_path / "group-review.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")
    real_approve = pipeline_store.approve_stage_revision
    writes = 0

    def fail_after_first_write(*args, **kwargs):
        nonlocal writes
        if writes == 1:
            raise OSError("forced grouped review write failure")
        review = real_approve(*args, **kwargs)
        writes += 1
        return review

    monkeypatch.setattr(
        pipeline_store, "approve_stage_revision", fail_after_first_write
    )

    with pytest.raises(OSError, match="forced grouped review write failure"):
        approve_review_bundle(
            project_dir,
            grouped_stages,
            note="group approved",
            evidence=(evidence,),
        )

    package = load_production_package(project_dir)
    records = package.stages[1:5]
    assert all(record.review_state is ReviewState.AWAITING_REVIEW for record in records)
    assert all(
        not (project_dir / "reviews" / f"{stage.value}.review.json").exists()
        for stage in grouped_stages
    )


def test_grouped_approval_retry_recovers_interrupted_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_dir, grouped_stages = _grouped_review_project(tmp_path)
    evidence = tmp_path / "group-review.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")
    real_approve = pipeline_store.approve_stage_revision
    writes = 0

    class SimulatedProcessInterruption(BaseException):
        pass

    def interrupt_after_first_write(*args, **kwargs):
        nonlocal writes
        if writes == 1:
            raise SimulatedProcessInterruption
        review = real_approve(*args, **kwargs)
        writes += 1
        return review

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            pipeline_store,
            "approve_stage_revision",
            interrupt_after_first_write,
        )
        with pytest.raises(SimulatedProcessInterruption):
            approve_review_bundle(
                project_dir,
                grouped_stages,
                note="group approved",
                evidence=(evidence,),
            )

    interrupted_package = load_production_package(project_dir)
    assert all(
        record.review_state is ReviewState.AWAITING_REVIEW
        for record in interrupted_package.stages[1:5]
    )
    assert (project_dir / "reviews" / "script.review.json").is_file()

    package = approve_review_bundle(
        project_dir,
        grouped_stages,
        note="group approved",
        evidence=(evidence,),
    )

    assert all(
        record.review_state is ReviewState.APPROVED for record in package.stages[1:5]
    )
    transactions = project_dir / "reviews" / ".transactions"
    assert not any(transactions.iterdir())


def test_create_project_writes_spec_and_full_pending_package(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "episode_01"

    package = create_project(project_dir, _spec(tmp_path))

    assert load_project_spec(project_dir).project_id == "episode_01"
    assert load_production_package(project_dir) == package
    assert package.next_stage is StageName.CONCEPT
    assert all(record.state is StageState.PENDING for record in package.stages)


def test_update_stage_invalidates_only_passed_downstream_stages(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in (
        StageName.CONCEPT,
        StageName.SCRIPT,
        StageName.STORYBOARD,
        StageName.ASSETS,
    ):
        update_stage(
            project_dir,
            stage,
            StageState.PASSED,
            executor="fixture",
            input_signature=f"sig-{stage.value}",
            artifacts=(f"{stage.value}.json",),
        )

    package = update_stage(
        project_dir,
        StageName.SCRIPT,
        StageState.PASSED,
        executor="fixture-v2",
        input_signature="changed",
        artifacts=("script-v2.json",),
    )

    states = {record.stage: record.state for record in package.stages}
    assert states[StageName.CONCEPT] is StageState.PASSED
    assert states[StageName.SCRIPT] is StageState.PASSED
    assert states[StageName.STORYBOARD] is StageState.STALE
    assert states[StageName.ASSETS] is StageState.STALE
    assert states[StageName.AUDIO] is StageState.PENDING


@pytest.mark.parametrize(
    ("input_signature", "artifacts"),
    [
        ("script-v2", ("script-v1.json",)),
        ("", ("script-v1.json",)),
        ("script-v1", ("script-v2.json",)),
    ],
    ids=("changed-signature", "cleared-signature", "changed-artifacts"),
)
def test_update_stage_replacement_clears_target_approval(
    tmp_path: Path,
    input_signature: str,
    artifacts: tuple[str, ...],
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    update_stage(
        project_dir,
        StageName.SCRIPT,
        StageState.PASSED,
        executor="fixture",
        input_signature="script-v1",
        artifacts=("script-v1.json",),
        revision=3,
        review_policy=ReviewPolicy.MANUAL,
        review_state=ReviewState.APPROVED,
        review_blocks_progress=True,
    )

    package = update_stage(
        project_dir,
        StageName.SCRIPT,
        StageState.PASSED,
        executor="fixture-v2",
        input_signature=input_signature,
        artifacts=artifacts,
    )

    record = package.stages[1]
    assert record.state is StageState.PASSED
    assert record.revision is None
    assert record.review_policy is ReviewPolicy.MANUAL
    assert record.review_state is ReviewState.NOT_READY
    assert record.review_blocks_progress is False


@pytest.mark.parametrize(
    ("input_signature", "artifacts"),
    [
        ("legacy-script-v2", ("legacy-script-v1.json",)),
        ("legacy-script-v1", ("legacy-script-v2.json",)),
    ],
    ids=("changed-signature", "changed-artifacts"),
)
def test_update_stage_replacement_resets_legacy_passed_review(
    tmp_path: Path,
    input_signature: str,
    artifacts: tuple[str, ...],
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    package_path = project_dir / "production_package.json"
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    legacy_record = payload["stages"][1]
    legacy_record.update(
        {
            "state": StageState.PASSED.value,
            "executor": "legacy.script",
            "input_signature": "legacy-script-v1",
            "artifacts": ["legacy-script-v1.json"],
        }
    )
    for field in (
        "revision",
        "review_policy",
        "review_state",
        "review_blocks_progress",
    ):
        legacy_record.pop(field, None)
    package_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_production_package(project_dir).stages[1]
    assert loaded.revision is None
    assert loaded.review_state is ReviewState.APPROVED

    package = update_stage(
        project_dir,
        StageName.SCRIPT,
        StageState.PASSED,
        executor="fixture-v2",
        input_signature=input_signature,
        artifacts=artifacts,
    )

    record = package.stages[1]
    assert record.revision is None
    assert record.review_state is ReviewState.NOT_READY
    assert record.review_blocks_progress is False


def test_store_rejects_symlinked_project_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        create_project(linked, _spec(tmp_path))


def test_approve_stage_requires_bound_evidence_and_marks_stage_passed(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in PIPELINE_STAGES[:7]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    update_stage(
        project_dir,
        StageName.EVAL,
        StageState.BLOCKED,
        executor="quality-eval",
        blocked_reasons=("manual review",),
    )
    evidence = tmp_path / "eval_result.json"
    evidence.write_text('{"status":"PASS"}', encoding="utf-8")

    package = approve_stage(
        project_dir,
        StageName.EVAL,
        note="逐镜检查通过",
        evidence=(evidence,),
    )

    record = package.stages[7]
    assert record.state is StageState.PASSED
    assert record.executor == "quality-eval:manual-approval"
    assert record.review_policy is ReviewPolicy.MANUAL
    assert record.review_state is ReviewState.APPROVED
    assert Path(record.artifacts[0]).name == "eval.approval.json"
    assert record.artifacts[1] == str(evidence.resolve())


def test_approve_stage_preserves_and_hashes_prepared_stage_artifacts(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in PIPELINE_STAGES[:3]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    prepared = tmp_path / "prepared-assets.json"
    review = tmp_path / "asset-review.json"
    prepared.write_text('{"assets":2}', encoding="utf-8")
    review.write_text('{"approved":true}', encoding="utf-8")
    update_stage(
        project_dir,
        StageName.ASSETS,
        StageState.BLOCKED,
        executor="generic.assets",
        artifacts=(str(prepared),),
    )

    package = approve_stage(
        project_dir,
        StageName.ASSETS,
        note="素材检查通过",
        evidence=(review,),
    )

    record = package.stages[3]
    assert str(prepared.resolve()) in record.artifacts
    assert str(review.resolve()) in record.artifacts
    approval = json.loads(Path(record.artifacts[0]).read_text(encoding="utf-8"))
    assert {item["path"] for item in approval["evidence"]} == {
        str(prepared.resolve()),
        str(review.resolve()),
    }


def test_approve_stage_rejects_missing_evidence(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in PIPELINE_STAGES[:7]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    update_stage(
        project_dir,
        StageName.EVAL,
        StageState.BLOCKED,
        executor="quality-eval",
    )

    with pytest.raises(ValueError, match="evidence"):
        approve_stage(
            project_dir,
            StageName.EVAL,
            note="approved",
            evidence=(tmp_path / "missing.json",),
        )


def test_approve_stage_rejects_non_manual_cloud_block(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in PIPELINE_STAGES[:4]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    update_stage(
        project_dir,
        StageName.AUDIO,
        StageState.BLOCKED,
        executor="audio-stage",
        blocked_reasons=("Cloud generation is disabled",),
    )
    evidence = tmp_path / "note.json"
    evidence.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="manual gate"):
        approve_stage(
            project_dir,
            StageName.AUDIO,
            note="skip cloud",
            evidence=(evidence,),
        )


def test_stage_outputs_are_indexed_and_cleared_when_upstream_is_stale(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    final_video = tmp_path / "final.mp4"
    eval_report = tmp_path / "eval_result.json"
    final_video.write_bytes(b"video")
    eval_report.write_text('{"status":"PASS"}', encoding="utf-8")
    for stage in PIPELINE_STAGES[:6]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    package = update_stage(
        project_dir,
        StageName.EDIT,
        StageState.PASSED,
        executor="fixture",
        artifacts=(str(final_video),),
    )
    package = update_stage(
        project_dir,
        StageName.EVAL,
        StageState.PASSED,
        executor="fixture",
        artifacts=(str(eval_report),),
    )

    assert package.final_outputs == (str(final_video.resolve()),)
    assert package.eval_reports == (str(eval_report.resolve()),)

    package = invalidate_stage_and_downstream(
        project_dir,
        StageName.EDIT,
        reason="edit changed",
    )

    assert package.final_outputs == ()
    assert package.eval_reports == ()


def test_modified_approval_evidence_is_invalidated_on_resume(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "episode_01"
    create_project(project_dir, _spec(tmp_path))
    for stage in PIPELINE_STAGES[:7]:
        update_stage(project_dir, stage, StageState.PASSED, executor="fixture")
    update_stage(
        project_dir,
        StageName.EVAL,
        StageState.BLOCKED,
        executor="quality-eval",
    )
    evidence = tmp_path / "eval_result.json"
    evidence.write_text('{"status":"PASS"}', encoding="utf-8")
    approve_stage(
        project_dir,
        StageName.EVAL,
        note="reviewed",
        evidence=(evidence,),
    )
    evidence.write_text('{"status":"FAIL"}', encoding="utf-8")
    calls = []

    def execute(context):
        calls.append(context.stage)
        artifact = context.stage_dir / "eval.json"
        artifact.write_text("{}", encoding="utf-8")
        return StageExecution.passed(executor="fixture", artifacts=(artifact,))

    resume_pipeline(project_dir, through=StageName.EVAL, executor=execute)

    assert calls == [StageName.EVAL]
