from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4


JOB_SCHEMA = "motion-comic-factory.pipeline-job.v1"
EVENT_SCHEMA = "motion-comic-factory.pipeline-job-event.v1"
PROJECT_OWNER_SCHEMA = "motion-comic-factory.pipeline-job-owner.v1"
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_SAFE_PROVIDER_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "environment",
        "env",
        "headers",
        "password",
        "secret",
        "token",
    }
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "apikey", "authorization", "key", "secret", "token"}
)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


class ProjectBusyError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_project_id(value: str) -> str:
    project_id = str(value).strip()
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
        or len(project_id) > 128
        or any(ord(character) < 32 for character in project_id)
    ):
        raise ValueError("project_id must be a safe single path component")
    return project_id


def _safe_job_id(value: str) -> str:
    job_id = str(value)
    if not _JOB_ID.fullmatch(job_id):
        raise ValueError("job_id must be a safe identifier")
    return job_id


def _path_uses_symlink(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False


def _require_safe_directory(path: Path, *, create: bool = True) -> Path:
    expanded = path.expanduser().absolute()
    if _path_uses_symlink(expanded):
        raise ValueError(f"Job storage path cannot use a symlink: {expanded}")
    if create:
        expanded.mkdir(parents=True, exist_ok=True)
    if _path_uses_symlink(expanded):
        raise ValueError(f"Job storage path cannot use a symlink: {expanded}")
    if not expanded.is_dir():
        raise ValueError(f"Job storage path is not a directory: {expanded}")
    return expanded.resolve()


def _validate_safe_data(value: Any, *, key: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            name = str(raw_key).strip().lower()
            normalized = name.replace("-", "_")
            if normalized in _SENSITIVE_KEYS or any(
                part in _SENSITIVE_KEYS for part in normalized.split("_")
            ):
                raise ValueError("Job data contains a sensitive field")
            _validate_safe_data(item, key=name)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_data(item, key=key)
        return
    if isinstance(value, Path):
        return
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith(("bearer ", "basic ")):
            raise ValueError("Job data contains sensitive authorization material")
        urls = _URL_IN_TEXT.findall(value)
        for url in urls:
            parsed = urlsplit(url)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Job data contains a sensitive provider URL")
            if any(
                query_key.lower() in _SENSITIVE_QUERY_KEYS
                for query_key, _query_value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
            ):
                raise ValueError("Job data contains a sensitive provider URL")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ValueError(f"Job {key} contains an unsupported value")


def _plain(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    if _path_uses_symlink(path) or path.is_symlink():
        raise ValueError(f"Job path cannot use a symlink: {path}")
    if not path.parent.is_dir() or _path_uses_symlink(path.parent):
        raise ValueError(f"Job directory cannot use a symlink: {path.parent}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_bytes_atomic(
        path,
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    )


def _read_object(path: Path) -> dict[str, Any]:
    if _path_uses_symlink(path) or path.is_symlink():
        raise ValueError(f"Job path cannot use a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Job record is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Job record is invalid: {path.name}")
    return value


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    sequence: int
    kind: str
    data: Mapping[str, Any]
    created_at: str
    schema_version: str = EVENT_SCHEMA

    def __post_init__(self) -> None:
        _safe_job_id(self.job_id)
        if self.sequence < 1:
            raise ValueError("Job event sequence must be positive")
        if not str(self.kind).strip():
            raise ValueError("Job event kind cannot be empty")
        _validate_safe_data(self.data, key="event")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "data": _plain(self.data),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JobEvent:
        if value.get("schema_version") != EVENT_SCHEMA:
            raise ValueError("Unsupported job event schema")
        data = value.get("data")
        if not isinstance(data, dict):
            raise ValueError("Job event data must be an object")
        return cls(
            job_id=str(value["job_id"]),
            sequence=int(value["sequence"]),
            kind=str(value["kind"]),
            data=data,
            created_at=str(value["created_at"]),
        )


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    project_id: str
    operation: str
    payload: Mapping[str, Any]
    status: str
    created_at: str
    updated_at: str
    provider_tasks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    resume_count: int = 0
    schema_version: str = JOB_SCHEMA

    def __post_init__(self) -> None:
        _safe_job_id(self.job_id)
        _safe_project_id(self.project_id)
        if not str(self.operation).strip():
            raise ValueError("Job operation cannot be empty")
        if self.status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
            raise ValueError(f"Unsupported job status: {self.status}")
        if self.resume_count < 0:
            raise ValueError("Job resume count cannot be negative")
        _validate_safe_data(self.payload)
        _validate_safe_data(self.provider_tasks, key="provider_tasks")
        _validate_safe_data(self.result, key="result")
        _validate_safe_data(self.error, key="error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "operation": self.operation,
            "payload": _plain(self.payload),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider_tasks": _plain(self.provider_tasks),
            "result": _plain(self.result),
            "error": self.error,
            "resume_count": self.resume_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JobRecord:
        if value.get("schema_version") != JOB_SCHEMA:
            raise ValueError("Unsupported pipeline job schema")
        payload = value.get("payload")
        provider_tasks = value.get("provider_tasks")
        result = value.get("result")
        if not all(isinstance(item, dict) for item in (payload, provider_tasks, result)):
            raise ValueError("Pipeline job object fields are invalid")
        return cls(
            job_id=str(value["job_id"]),
            project_id=str(value["project_id"]),
            operation=str(value["operation"]),
            payload=payload,
            status=str(value["status"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            provider_tasks=provider_tasks,
            result=result,
            error=str(value.get("error") or ""),
            resume_count=int(value.get("resume_count") or 0),
        )


class JobManager:
    def __init__(self, workspace: str | Path):
        self.workspace = _require_safe_directory(Path(workspace))
        self.jobs_dir = _require_safe_directory(
            self.workspace / "runs" / ".workbench" / "jobs"
        )
        self.owners_dir = _require_safe_directory(self.jobs_dir / ".projects")
        self._manager_lock_path = self.jobs_dir / ".manager.lock"
        if self._manager_lock_path.is_symlink():
            raise ValueError("Job manager lock cannot be a symlink")

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{_safe_job_id(job_id)}.json"

    def _event_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{_safe_job_id(job_id)}.jsonl"

    def _owner_path(self, project_id: str) -> Path:
        digest = hashlib.sha256(_safe_project_id(project_id).encode("utf-8")).hexdigest()
        return self.owners_dir / f"{digest}.json"

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        if self._manager_lock_path.is_symlink():
            raise ValueError("Job manager lock cannot be a symlink")
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        descriptor = os.open(self._manager_lock_path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Job manager lock is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def get(self, job_id: str) -> JobRecord:
        path = self._job_path(job_id)
        if not path.is_file():
            raise KeyError(job_id)
        record = JobRecord.from_dict(_read_object(path))
        if record.job_id != job_id:
            raise ValueError("Pipeline job identity does not match its file")
        return record

    def _events_locked(self, job_id: str) -> tuple[JobEvent, ...]:
        path = self._event_path(job_id)
        if not path.exists():
            return ()
        if _path_uses_symlink(path) or path.is_symlink() or not path.is_file():
            raise ValueError("Job event path is invalid")
        events: list[JobEvent] = []
        content = path.read_text(encoding="utf-8")
        if content and not content.endswith("\n"):
            raise ValueError("Job event journal is incomplete")
        for line in content.splitlines():
            try:
                event = JobEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("Job event journal is invalid") from exc
            if event.job_id != job_id or event.sequence != len(events) + 1:
                raise ValueError("Job event journal sequence is invalid")
            events.append(event)
        return tuple(events)

    def events(self, job_id: str, *, after_sequence: int = 0) -> tuple[JobEvent, ...]:
        _safe_job_id(job_id)
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        return tuple(
            event
            for event in self._events_locked(job_id)
            if event.sequence > after_sequence
        )

    def _append_event_locked(
        self,
        job_id: str,
        kind: str,
        data: Mapping[str, Any],
    ) -> JobEvent:
        events = self._events_locked(job_id)
        event = JobEvent(
            job_id=job_id,
            sequence=len(events) + 1,
            kind=str(kind).strip(),
            data=dict(data),
            created_at=_utc_now(),
        )
        content = b"".join(
            (
                json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            for item in (*events, event)
        )
        _write_bytes_atomic(self._event_path(job_id), content)
        return event

    def append_event(
        self,
        job_id: str,
        kind: str,
        data: Mapping[str, Any],
    ) -> JobEvent:
        self.get(job_id)
        with self._mutation_lock():
            return self._append_event_locked(job_id, kind, data)

    def _active_owner_locked(self, project_id: str) -> JobRecord | None:
        owner_path = self._owner_path(project_id)
        if not owner_path.exists():
            return None
        owner = _read_object(owner_path)
        if owner.get("schema_version") != PROJECT_OWNER_SCHEMA:
            raise ValueError("Project job owner is invalid")
        if owner.get("project_id") != project_id:
            raise ValueError("Project job owner identity is invalid")
        job_id = _safe_job_id(str(owner.get("job_id") or ""))
        try:
            record = self.get(job_id)
        except KeyError as exc:
            raise ValueError("Project job owner references a missing job") from exc
        if record.project_id != project_id:
            raise ValueError("Project job owner references another project")
        if record.status in TERMINAL_STATUSES:
            owner_path.unlink()
            _fsync_directory(owner_path.parent)
            return None
        return record

    def _publish_owner_locked(self, record: JobRecord) -> None:
        _write_json_atomic(
            self._owner_path(record.project_id),
            {
                "schema_version": PROJECT_OWNER_SCHEMA,
                "project_id": record.project_id,
                "job_id": record.job_id,
            },
        )

    def _claim_project_locked(
        self,
        record: JobRecord,
        *,
        publish: bool = True,
    ) -> None:
        active = self._active_owner_locked(record.project_id)
        if active is not None and active.job_id != record.job_id:
            raise ProjectBusyError(
                f"Project {record.project_id} already has active job {active.job_id}"
            )
        if active is None:
            for path in self.jobs_dir.glob("*.json"):
                candidate = JobRecord.from_dict(_read_object(path))
                if (
                    candidate.project_id == record.project_id
                    and candidate.job_id != record.job_id
                    and candidate.status in ACTIVE_STATUSES
                ):
                    raise ProjectBusyError(
                        f"Project {record.project_id} already has active job {candidate.job_id}"
                    )
        if publish:
            self._publish_owner_locked(record)

    def _release_project_locked(self, record: JobRecord) -> None:
        owner_path = self._owner_path(record.project_id)
        if not owner_path.exists():
            return
        owner = _read_object(owner_path)
        if owner.get("job_id") == record.job_id:
            owner_path.unlink()
            _fsync_directory(owner_path.parent)

    def submit(
        self,
        *,
        project_id: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> str:
        normalized_project = _safe_project_id(project_id)
        normalized_operation = str(operation).strip()
        if not normalized_operation:
            raise ValueError("Job operation cannot be empty")
        _validate_safe_data(payload)
        now = _utc_now()
        record = JobRecord(
            job_id=uuid4().hex,
            project_id=normalized_project,
            operation=normalized_operation,
            payload=dict(payload),
            status="queued",
            created_at=now,
            updated_at=now,
        )
        with self._mutation_lock():
            self._claim_project_locked(record, publish=False)
            try:
                _write_json_atomic(self._job_path(record.job_id), record.to_dict())
                self._publish_owner_locked(record)
                self._append_event_locked(
                    record.job_id,
                    "submitted",
                    {"status": record.status, "operation": record.operation},
                )
            except BaseException:
                if not self._job_path(record.job_id).exists():
                    self._release_project_locked(record)
                raise
        return record.job_id

    def _replace_locked(self, record: JobRecord, **changes: Any) -> JobRecord:
        updated = replace(record, updated_at=_utc_now(), **changes)
        _write_json_atomic(self._job_path(record.job_id), updated.to_dict())
        return updated

    def start(self, job_id: str) -> JobRecord:
        with self._mutation_lock():
            record = self.get(job_id)
            if record.status == "completed":
                return record
            if record.status not in {"queued", "running"}:
                raise ValueError(f"Cannot start {record.status} job")
            self._claim_project_locked(record)
            updated = self._replace_locked(record, status="running", error="")
            self._append_event_locked(job_id, "started", {"status": "running"})
            return updated

    def complete(
        self,
        job_id: str,
        *,
        result: Mapping[str, Any],
    ) -> JobRecord:
        _validate_safe_data(result, key="result")
        with self._mutation_lock():
            record = self.get(job_id)
            if record.status == "completed":
                return record
            if record.status not in ACTIVE_STATUSES:
                raise ValueError(f"Cannot complete {record.status} job")
            updated = self._replace_locked(
                record,
                status="completed",
                result=dict(result),
                error="",
            )
            self._append_event_locked(job_id, "completed", {"status": "completed"})
            self._release_project_locked(updated)
            return updated

    def fail(self, job_id: str, *, error: str) -> JobRecord:
        message = str(error).strip()
        _validate_safe_data(message, key="error")
        with self._mutation_lock():
            record = self.get(job_id)
            if record.status not in ACTIVE_STATUSES:
                raise ValueError(f"Cannot fail {record.status} job")
            updated = self._replace_locked(record, status="failed", error=message)
            self._append_event_locked(
                job_id,
                "failed",
                {"status": "failed", "error": message},
            )
            self._release_project_locked(updated)
            return updated

    def resume(self, job_id: str) -> JobRecord:
        with self._mutation_lock():
            record = self.get(job_id)
            if record.status in {"completed", "cancelled"}:
                return record
            self._claim_project_locked(record)
            if record.status == "queued":
                return record
            updated = self._replace_locked(
                record,
                status="queued",
                error="",
                resume_count=record.resume_count + 1,
            )
            self._append_event_locked(
                job_id,
                "resumed",
                {"status": "queued", "resume_count": updated.resume_count},
            )
            return updated

    def record_provider_task(
        self,
        job_id: str,
        *,
        shot_id: str,
        provider: str,
        task_id: str,
        status: str,
    ) -> JobRecord:
        normalized_shot = str(shot_id).strip()
        normalized_provider = str(provider).strip().lower()
        normalized_task = str(task_id).strip()
        normalized_status = str(status).strip().lower()
        if not normalized_shot or "/" in normalized_shot or "\\" in normalized_shot:
            raise ValueError("Provider shot ID is unsafe")
        for label, value in (
            ("provider", normalized_provider),
            ("task ID", normalized_task),
            ("task status", normalized_status),
        ):
            if not _SAFE_PROVIDER_VALUE.fullmatch(value):
                raise ValueError(f"Provider {label} is unsafe")
        with self._mutation_lock():
            record = self.get(job_id)
            if record.status not in ACTIVE_STATUSES:
                raise ValueError("Provider task state requires an active job")
            tasks = {key: dict(value) for key, value in record.provider_tasks.items()}
            tasks[normalized_shot] = {
                "provider": normalized_provider,
                "task_id": normalized_task,
                "status": normalized_status,
                "updated_at": _utc_now(),
            }
            updated = self._replace_locked(record, provider_tasks=tasks)
            self._append_event_locked(
                job_id,
                "provider_task",
                {
                    "shot_id": normalized_shot,
                    "provider": normalized_provider,
                    "task_id": normalized_task,
                    "status": normalized_status,
                },
            )
            return updated
