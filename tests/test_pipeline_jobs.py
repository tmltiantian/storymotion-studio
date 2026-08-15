from __future__ import annotations

import json
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

    def interrupt_after_owner_write(path, payload):
        original(path, payload)
        if path.parent.name == ".projects":
            raise SimulatedProcessInterruption

    monkeypatch.setattr(pipeline_jobs, "_write_json_atomic", interrupt_after_owner_write)
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
