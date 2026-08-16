from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory import pipeline_jobs
from factory.pipeline_jobs import JobManager, ProjectBusyError


def test_job_survives_manager_restart(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)

    job_id = manager.submit(project_id="p1", operation="video_test", payload={})

    restored = JobManager(tmp_path).get(job_id)
    assert restored.status == "queued"
    assert restored.project_id == "p1"
    assert (tmp_path / "runs/.workbench/jobs" / f"{job_id}.json").is_file()


def test_project_jobs_returns_only_selected_project_operations_newest_first(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    old = manager.submit(project_id="episode_01", operation="video_test", payload={})
    manager.start(old)
    manager.complete(old, result={})
    ignored = manager.submit(project_id="episode_01", operation="run_stage", payload={})
    manager.start(ignored)
    manager.complete(ignored, result={})
    latest = manager.submit(
        project_id="episode_01", operation="video_generate", payload={}
    )
    other = manager.submit(project_id="episode_02", operation="video_generate", payload={})

    records = manager.project_jobs(
        "episode_01", operations=("video_test", "video_generate")
    )

    assert [record.job_id for record in records] == [latest, old]
    assert ignored not in {record.job_id for record in records}
    assert other not in {record.job_id for record in records}


def test_same_project_rejects_second_mutating_job_after_restart(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    manager.submit(project_id="p1", operation="video_test", payload={})

    with pytest.raises(ProjectBusyError):
        JobManager(tmp_path).submit(
            project_id="p1",
            operation="approve_stage",
            payload={},
        )


def test_terminal_job_releases_project_for_next_mutation(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    manager.complete(job_id, result={"completed": 1})

    next_job_id = JobManager(tmp_path).submit(
        project_id="p1",
        operation="approve_stage",
        payload={},
    )

    assert next_job_id != job_id


def test_provider_task_state_survives_resume_without_resubmission_marker(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_generate", payload={})
    manager.start(job_id)
    manager.record_provider_task(
        job_id,
        shot_id="shot_03",
        provider="minimax",
        task_id="task-123",
        status="submitted",
    )

    resumed = JobManager(tmp_path).resume(job_id)

    assert resumed.status == "queued"
    assert resumed.provider_tasks["shot_03"]["task_id"] == "task-123"
    assert resumed.provider_tasks["shot_03"]["status"] == "submitted"
    events = JobManager(tmp_path).events(job_id)
    assert [event.kind for event in events][-2:] == ["provider_task", "resumed"]


def test_completed_job_resume_is_read_only(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_generate", payload={})
    completed = manager.complete(job_id, result={"completed": 1})
    before = (tmp_path / "runs/.workbench/jobs" / f"{job_id}.json").read_bytes()

    resumed = JobManager(tmp_path).resume(job_id)

    assert resumed == completed
    assert (tmp_path / "runs/.workbench/jobs" / f"{job_id}.json").read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    (
        {"api_key": "secret"},
        {"headers": {"Authorization": "Bearer secret"}},
        {"environment": {"HOME": "/private/home"}},
        {"provider_url": "https://user:secret@example.test/video"},
        {"message": "failed at https://user:secret@example.test/video"},
        {"provider_url": "https://example.test/video?accessToken=secret"},
    ),
)
def test_job_records_reject_secret_bearing_payloads(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    manager = JobManager(tmp_path)

    with pytest.raises(ValueError, match="sensitive"):
        manager.submit(project_id="p1", operation="video_generate", payload=payload)

    assert not list((tmp_path / "runs/.workbench/jobs").glob("*.json"))


def test_interrupted_owner_publication_does_not_leave_project_stuck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(tmp_path)
    original = pipeline_jobs._write_json_atomic

    class SimulatedProcessInterruption(BaseException):
        pass

    def interrupt_after_owner_write(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.parent.name == ".projects":
            raise SimulatedProcessInterruption

    monkeypatch.setattr(
        pipeline_jobs, "_write_json_atomic", interrupt_after_owner_write
    )
    with pytest.raises(SimulatedProcessInterruption):
        manager.submit(project_id="p1", operation="video_test", payload={})

    monkeypatch.setattr(pipeline_jobs, "_write_json_atomic", original)
    job_files = list((tmp_path / "runs/.workbench/jobs").glob("*.json"))
    assert len(job_files) == 1
    interrupted_job_id = job_files[0].stem
    restored = JobManager(tmp_path)
    assert restored.resume(interrupted_job_id).status == "queued"
    restored.complete(interrupted_job_id, result={})
    job_id = JobManager(tmp_path).submit(
        project_id="p1",
        operation="video_test",
        payload={},
    )
    assert JobManager(tmp_path).get(job_id).status == "queued"


def test_job_manager_rejects_unsafe_ids_and_symlinked_job_storage(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    with pytest.raises(ValueError, match="project_id"):
        manager.submit(project_id="../outside", operation="video_test", payload={})

    linked_workspace = tmp_path / "linked-workspace"
    workbench = linked_workspace / "runs/.workbench"
    workbench.mkdir(parents=True)
    jobs = workbench / "jobs"
    outside = tmp_path / "outside"
    outside.mkdir()
    jobs.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        JobManager(linked_workspace)
    assert not list(outside.iterdir())


def test_event_journal_contains_complete_json_lines(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    manager.append_event(job_id, "progress", {"completed": 1, "total": 3})

    event_path = tmp_path / "runs/.workbench/jobs" / f"{job_id}.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert [event.sequence for event in manager.events(job_id)] == [1, 2]


@pytest.mark.parametrize(
    "payload",
    (
        {"x-api-key": "secret"},
        {"accessToken": "secret"},
        {"clientSecret": "secret"},
        {"refresh-token": "secret"},
    ),
)
def test_job_secret_detection_normalizes_common_key_spellings(
    tmp_path: Path,
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        JobManager(tmp_path).submit(
            project_id="p1",
            operation="video_generate",
            payload=payload,
        )


def test_job_rejects_sensitive_operation_and_event_kind(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    with pytest.raises(ValueError, match="sensitive"):
        manager.submit(project_id="p1", operation="accessToken", payload={})

    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    with pytest.raises(ValueError, match="sensitive"):
        manager.append_event(job_id, "x-api-key", {})


@pytest.mark.parametrize(
    "transition", ("submitted", "started", "provider_task", "completed")
)
def test_interrupted_record_event_transition_recovers_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    manager = JobManager(tmp_path)
    original = pipeline_jobs.JobManager._append_event_locked

    class SimulatedProcessInterruption(BaseException):
        pass

    def interrupt(self, job_id, kind, data):
        if kind == transition:
            raise SimulatedProcessInterruption
        return original(self, job_id, kind, data)

    if transition == "submitted":
        monkeypatch.setattr(pipeline_jobs.JobManager, "_append_event_locked", interrupt)
        with pytest.raises(SimulatedProcessInterruption):
            manager.submit(project_id="p1", operation="video_test", payload={})
        job_id = next((tmp_path / "runs/.workbench/jobs").glob("*.json")).stem
    else:
        job_id = manager.submit(project_id="p1", operation="video_test", payload={})
        if transition in {"provider_task", "completed"}:
            manager.start(job_id)
        monkeypatch.setattr(pipeline_jobs.JobManager, "_append_event_locked", interrupt)
        with pytest.raises(SimulatedProcessInterruption):
            if transition == "started":
                manager.start(job_id)
            elif transition == "provider_task":
                manager.record_provider_task(
                    job_id,
                    shot_id="shot_03",
                    provider="minimax",
                    task_id="task-123",
                    status="submitted",
                )
            else:
                manager.complete(job_id, result={"completed": 1})

    monkeypatch.setattr(pipeline_jobs.JobManager, "_append_event_locked", original)
    restored = JobManager(tmp_path)
    restored.get(job_id)
    events = restored.events(job_id)

    assert [event.kind for event in events].count(transition) == 1


def test_job_directory_swap_fails_closed_without_outside_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(tmp_path)
    jobs = tmp_path / "runs/.workbench/jobs"
    held = tmp_path / "jobs-held"
    outside = tmp_path / "outside-jobs"
    outside.mkdir()
    original_replace = pipeline_jobs.os.replace
    swapped = False

    def swap_on_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        destination_path = Path(destination)
        if not swapped and destination_path.suffix == ".json":
            swapped = True
            jobs.rename(held)
            jobs.symlink_to(outside, target_is_directory=True)
            relocated_source = held / Path(source).name
            return original_replace(relocated_source, destination, *args, **kwargs)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(pipeline_jobs.os, "replace", swap_on_replace)

    with pytest.raises((OSError, ValueError), match="identity|symlink|No such file"):
        manager.submit(project_id="p1", operation="video_test", payload={})
    assert not list(outside.iterdir())


def test_event_journal_has_hard_scaling_bound(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_jobs, "MAX_JOB_EVENTS", 2, raising=False)
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    manager.append_event(job_id, "progress", {"completed": 1})

    with pytest.raises(ValueError, match="limit"):
        manager.append_event(job_id, "progress", {"completed": 2})


def test_job_swap_out_read_swap_back_cannot_substitute_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    jobs = tmp_path / "runs/.workbench/jobs"
    held = tmp_path / "jobs-held"
    attacker = tmp_path / "attacker-jobs"
    shutil.copytree(jobs, attacker)
    attacker_record = attacker / f"{job_id}.json"
    payload = json.loads(attacker_record.read_text(encoding="utf-8"))
    payload["operation"] = "attacker_operation"
    attacker_record.write_text(json.dumps(payload), encoding="utf-8")
    original = pipeline_jobs._read_bytes

    def swap_for_job_read(path: Path, *args, **kwargs):
        if path.name != f"{job_id}.json":
            return original(path, *args, **kwargs)
        jobs.rename(held)
        attacker.rename(jobs)
        try:
            return original(path, *args, **kwargs)
        finally:
            jobs.rename(attacker)
            held.rename(jobs)

    monkeypatch.setattr(pipeline_jobs, "_read_bytes", swap_for_job_read)

    restored = manager.get(job_id)

    assert restored.operation == "video_test"


def test_job_swap_out_write_swap_back_cannot_redirect_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    jobs = tmp_path / "runs/.workbench/jobs"
    held = tmp_path / "jobs-held"
    attacker = tmp_path / "attacker-jobs"
    shutil.copytree(jobs, attacker)
    original = pipeline_jobs._write_bytes_atomic

    def swap_for_job_write(path: Path, content: bytes, *args, **kwargs):
        jobs.rename(held)
        attacker.rename(jobs)
        try:
            return original(path, content, *args, **kwargs)
        finally:
            jobs.rename(attacker)
            held.rename(jobs)

    monkeypatch.setattr(pipeline_jobs, "_write_bytes_atomic", swap_for_job_write)

    manager.append_event(job_id, "progress", {"completed": 1})

    original_events = (jobs / f"{job_id}.jsonl").read_text(encoding="utf-8")
    attacker_events = (attacker / f"{job_id}.jsonl").read_text(encoding="utf-8")
    assert '"kind": "progress"' in original_events
    assert '"kind": "progress"' not in attacker_events


def test_macos_var_alias_supports_job_storage(tmp_path: Path) -> None:
    private_var = Path("/private/var")
    if not Path("/var").is_symlink() or not tmp_path.is_relative_to(private_var):
        pytest.skip("macOS /var system alias is unavailable")
    alias = Path("/var") / tmp_path.relative_to(private_var)

    manager = JobManager(alias)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})

    assert manager.get(job_id).status == "queued"
