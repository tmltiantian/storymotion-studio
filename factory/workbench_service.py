from __future__ import annotations

import hashlib
import inspect
import json
import mimetypes
import os
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

from .gateway_video_batch import render_gateway_video_batch
from .openmontage_adapter import write_openmontage_package
from .pipeline_context import StageContext
from .pipeline_contracts import ProductionPackage, ProjectMode, ProjectSpec, StageName
from .pipeline_impact import (
    ChangeRequest,
    ImpactPlan,
    apply_impact_plan,
    preview_impact,
)
from .pipeline_jobs import JobEvent, JobManager, JobRecord
from .pipeline_modes import get_mode_adapter
from .pipeline_runner import PipelineRunResult, resume_pipeline
from .pipeline_store import (
    approve_stage,
    create_project,
    request_stage_changes,
)
from .provider_profile import ProviderProfile, resolve_provider_profile
from .secure_posix import AnchoredDirectory
from .shot_audio import write_shot_audio_assets
from .video_handoff import write_video_handoff
from .video_preflight import (
    VideoGenerationRequest,
    build_video_preflight,
    issue_generation_token,
)
from .video_provider import build_video_client, default_video_resolution


DEFAULT_FRONTEND_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
_ARTIFACT_ID = re.compile(r"art_[0-9a-f]{32}")
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_EMBEDDED_PATH = re.compile(r"(?<![A-Za-z0-9:])/(?:[^\s,;:'\"]+/)*[^\s,;:'\"]+")
_URL_TEXT = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_TOKEN_TEXT = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b")
_AUTH_TEXT = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+=*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_MEDIA_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".m4a": "audio/mp4",
    ".md": "text/markdown; charset=utf-8",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".srt": "application/x-subrip; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".json": "application/json; charset=utf-8",
}


@dataclass(frozen=True)
class _ArtifactRef:
    artifact_id: str
    project_id: str
    root: Path
    relative_path: Path
    name: str
    media_type: str


def _safe_identifier(value: str, label: str) -> str:
    identifier = str(value).strip()
    if (
        not identifier
        or identifier in {".", ".."}
        or "/" in identifier
        or "\\" in identifier
        or len(identifier) > 128
        or any(ord(character) < 32 for character in identifier)
    ):
        raise ValueError(f"{label} must be an opaque identifier")
    return identifier


def _media_type(name: str, registered: str = "") -> str:
    if registered and registered != "application/octet-stream":
        return registered
    return _MEDIA_TYPES.get(
        Path(name).suffix.lower(),
        mimetypes.guess_type(name, strict=True)[0] or "application/octet-stream",
    )


def _run_result(result: PipelineRunResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "stopped_at": result.stopped_at.value if result.stopped_at else None,
        "completed_stages": [stage.value for stage in result.completed_stages],
        "next_stage": result.next_stage.value if result.next_stage else "complete",
        "stopped_state": (
            result.stopped_state.value if result.stopped_state is not None else None
        ),
        "review_in_progress": result.review_in_progress,
    }


class WorkbenchService:
    """Path-free public facade over the production workbench services."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        runs_dir: str | Path | None = None,
        artifact_roots: Sequence[str | Path] = (),
        frontend_origins: Sequence[str] = DEFAULT_FRONTEND_ORIGINS,
        config: Mapping[str, Any] | None = None,
        job_manager: JobManager | None = None,
        provider_profile_loader: Callable[..., ProviderProfile | None] | None = None,
        stage_runner: Callable[..., PipelineRunResult] = resume_pipeline,
        video_renderer: Callable[..., Mapping[str, Any]] | None = None,
        dispatch: Callable[[Callable[[], None]], Any] | None = None,
    ):
        with AnchoredDirectory.open(workspace, label="Workbench workspace") as anchor:
            self.workspace = anchor.canonical_path
        configured_runs = (
            Path(runs_dir) if runs_dir is not None else self.workspace / "runs"
        )
        configured_runs.mkdir(parents=True, exist_ok=True)
        with AnchoredDirectory.open(
            configured_runs, label="Workbench project root"
        ) as anchor:
            self.runs_dir = anchor.canonical_path
        resolved_artifact_roots: list[Path] = []
        for path in artifact_roots:
            with AnchoredDirectory.open(
                path, label="Workbench artifact root"
            ) as anchor:
                resolved_artifact_roots.append(anchor.canonical_path)
        self.artifact_roots = tuple(resolved_artifact_roots)
        self.frontend_origins = self._frontend_origins(frontend_origins)
        self.config = dict(config or {})
        self.jobs = job_manager or JobManager(self.workspace)
        self._provider_profile_loader = provider_profile_loader or (
            lambda: resolve_provider_profile(self.config)
        )
        self._stage_runner = stage_runner
        self._video_renderer = video_renderer or self._render_confirmed_video
        self._dispatch = dispatch or self._dispatch_thread
        self._job_artifacts: dict[str, _ArtifactRef] = {}
        self._registry_lock = threading.Lock()

    @staticmethod
    def _frontend_origins(origins: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(origin).rstrip("/") for origin in origins))
        if not normalized:
            raise ValueError("At least one localhost frontend origin is required")
        for origin in normalized:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Frontend origins must be explicit localhost origins")
        return normalized

    @staticmethod
    def _dispatch_thread(callback: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=callback, daemon=True, name="workbench-job")
        thread.start()
        return thread

    def _project_dir(self, project_id: str) -> Path:
        identifier = _safe_identifier(project_id, "project_id")
        with AnchoredDirectory.open(
            self.runs_dir, label="Workbench project root"
        ) as root:
            try:
                project = root.child(
                    identifier,
                    create=False,
                    label="Workbench project directory",
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise KeyError(identifier) from exc
            try:
                return project.canonical_path
            finally:
                project.close()

    def _new_project_dir(self, project_id: str) -> Path:
        identifier = _safe_identifier(project_id, "project_id")
        with AnchoredDirectory.open(
            self.runs_dir, label="Workbench project root"
        ) as root:
            if identifier in root.listdir():
                raise FileExistsError(identifier)
            return root.canonical_path / identifier

    def _project_ids(self) -> tuple[str, ...]:
        with AnchoredDirectory.open(
            self.runs_dir, label="Workbench project root"
        ) as root:
            names = root.listdir()
        project_ids: list[str] = []
        for name in names:
            if name.startswith("."):
                continue
            try:
                identifier = _safe_identifier(name, "project_id")
                project = self._project_dir(identifier)
                self._load_project_records(project)
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
                continue
            project_ids.append(identifier)
        return tuple(sorted(project_ids))

    def list_projects(self) -> list[dict[str, Any]]:
        return [self.project_detail(project_id) for project_id in self._project_ids()]

    def project_detail(self, project_id: str) -> dict[str, Any]:
        project = self._project_dir(project_id)
        spec, package = self._load_project_records(project)
        stages = [
            {
                "stage": record.stage.value,
                "execution_state": record.state.value,
                "review_state": record.review_state.value,
                "review_policy": record.review_policy.value,
                "review_blocks_progress": record.review_blocks_progress,
                "revision": record.revision,
                "executor": record.executor,
                "blocked_reasons": [
                    self._public_text(item) for item in record.blocked_reasons
                ],
                "error": self._public_text(record.error),
                "artifacts": [
                    self._artifact_public(ref)
                    for raw_path in record.artifacts
                    if (ref := self._register_artifact(project_id, raw_path))
                    is not None
                ],
            }
            for record in package.stages
        ]
        next_record = next(
            (
                record
                for record in package.stages
                if record.state.value != "passed" or record.review_blocks_progress
            ),
            None,
        )
        return {
            "project_id": spec.project_id,
            "title": spec.title,
            "mode": spec.mode.value,
            "target": self._public_value(spec.target),
            "next_stage": next_record.stage.value if next_record else "complete",
            "required_action": self._required_action(next_record),
            "stages": stages,
            "final_outputs": self._artifact_list(project_id, package.final_outputs),
            "eval_reports": self._artifact_list(project_id, package.eval_reports),
        }

    @staticmethod
    def _load_project_records(
        project: Path,
    ) -> tuple[ProjectSpec, ProductionPackage]:
        with AnchoredDirectory.open(
            project, label="Workbench project directory"
        ) as anchor:
            try:
                spec_value = json.loads(anchor.read_bytes("project.json"))
                package_value = json.loads(anchor.read_bytes("production_package.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Workbench project records are invalid") from exc
        if not isinstance(spec_value, dict) or not isinstance(package_value, dict):
            raise ValueError("Workbench project records are invalid")
        return (
            ProjectSpec.from_dict(spec_value),
            ProductionPackage.from_dict(package_value),
        )

    @staticmethod
    def _required_action(record: Any | None) -> str:
        if record is None:
            return "none"
        if record.review_state.value == "awaiting_review":
            return "approve_review_evidence"
        if record.review_state.value == "changes_requested":
            return "address_review_changes"
        if record.state.value == "failed":
            return "fix_stage_error_and_resume"
        return "run_or_resume"

    def stage_detail(self, project_id: str, stage: str) -> dict[str, Any]:
        target = StageName(stage)
        project = self.project_detail(project_id)
        return next(item for item in project["stages"] if item["stage"] == target.value)

    def _artifact_list(
        self, project_id: str, values: Sequence[str]
    ) -> list[dict[str, Any]]:
        return [
            self._artifact_public(ref)
            for raw_path in values
            if (ref := self._register_artifact(project_id, raw_path)) is not None
        ]

    @staticmethod
    def _artifact_public(ref: _ArtifactRef) -> dict[str, Any]:
        return {
            "artifact_id": ref.artifact_id,
            "name": ref.name,
            "media_type": ref.media_type,
            "media_url": f"/api/media/{ref.artifact_id}",
        }

    def _candidate_roots(self, project_id: str) -> tuple[Path, ...]:
        return (self._project_dir(project_id), *self.artifact_roots)

    @staticmethod
    def _open_artifact_parent(anchor: AnchoredDirectory, relative_path: Path) -> int:
        if len(relative_path.parts) == 1:
            return os.dup(anchor.descriptor)
        return anchor.open_directory(relative_path.parent)

    def _register_artifact(
        self,
        project_id: str,
        raw_path: str | Path,
        *,
        registered_media_type: str = "",
    ) -> _ArtifactRef | None:
        raw = Path(raw_path).expanduser()
        candidate = raw if raw.is_absolute() else self._project_dir(project_id) / raw
        if candidate.is_symlink():
            return None
        try:
            with AnchoredDirectory.open(
                candidate.parent,
                label="Registered artifact parent",
            ) as parent_anchor:
                absolute = parent_anchor.canonical_path / candidate.name
        except (FileNotFoundError, OSError, ValueError):
            return None
        for root_index, root in enumerate(self._candidate_roots(project_id)):
            try:
                with AnchoredDirectory.open(
                    root, label="Authorized artifact root"
                ) as anchor:
                    relative = anchor.relative_path(absolute)
                    parent = self._open_artifact_parent(anchor, relative)
                    try:
                        descriptor = os.open(
                            relative.name,
                            os.O_RDONLY | os.O_NOFOLLOW,
                            dir_fd=parent,
                        )
                        try:
                            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                                return None
                        finally:
                            os.close(descriptor)
                    finally:
                        os.close(parent)
            except (FileNotFoundError, OSError, ValueError):
                continue
            identity = f"{project_id}\0{root_index}\0{relative.as_posix()}"
            artifact_id = (
                "art_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            )
            ref = _ArtifactRef(
                artifact_id=artifact_id,
                project_id=project_id,
                root=root,
                relative_path=relative,
                name=relative.name,
                media_type=_media_type(relative.name, registered_media_type),
            )
            with self._registry_lock:
                self._job_artifacts[artifact_id] = ref
            return ref
        return None

    def _registered_artifacts(self) -> dict[str, _ArtifactRef]:
        registry: dict[str, _ArtifactRef] = {}
        with self._registry_lock:
            registry.update(self._job_artifacts)
        for project_id in self._project_ids():
            project = self._project_dir(project_id)
            _spec, package = self._load_project_records(project)
            raw_paths = [
                *package.final_outputs,
                *package.eval_reports,
                *(path for record in package.stages for path in record.artifacts),
            ]
            for path in raw_paths:
                ref = self._register_artifact(project_id, path)
                if ref is not None:
                    registry[ref.artifact_id] = ref
            with AnchoredDirectory.open(
                project,
                label="Workbench project directory",
            ) as project_anchor:
                try:
                    review_names = sorted(project_anchor.listdir("reviews"))
                except (FileNotFoundError, OSError, ValueError):
                    review_names = []
                for name in review_names:
                    if not name.endswith(".revisions.json") or "/" in name:
                        continue
                    try:
                        payload = json.loads(
                            project_anchor.read_bytes(Path("reviews") / name)
                        )
                        revisions = payload.get("revisions") or ()
                    except (
                        OSError,
                        UnicodeDecodeError,
                        ValueError,
                        json.JSONDecodeError,
                        AttributeError,
                    ):
                        continue
                    for revision in revisions:
                        if not isinstance(revision, dict):
                            continue
                        for artifact in revision.get("artifacts") or ():
                            if not isinstance(artifact, dict):
                                continue
                            ref = self._register_artifact(
                                project_id,
                                str(artifact.get("path") or ""),
                                registered_media_type=str(
                                    artifact.get("media_type") or ""
                                ),
                            )
                            if ref is not None:
                                registry[ref.artifact_id] = ref
        with AnchoredDirectory.open(
            self.jobs.jobs_dir,
            label="Workbench job storage",
        ) as jobs_anchor:
            job_ids = tuple(
                name.removesuffix(".json")
                for name in jobs_anchor.listdir()
                if name.endswith(".json")
                and _JOB_ID.fullmatch(name.removesuffix(".json"))
            )
        for job_id in job_ids:
            try:
                record = self.jobs.get(job_id)
                self._collect_job_artifacts(
                    record.project_id,
                    record.result,
                    registry,
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
                continue
        return registry

    def _collect_job_artifacts(
        self,
        project_id: str,
        value: Any,
        registry: dict[str, _ArtifactRef],
    ) -> None:
        if isinstance(value, Mapping):
            for item in value.values():
                self._collect_job_artifacts(project_id, item, registry)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                self._collect_job_artifacts(project_id, item, registry)
            return
        if isinstance(value, (str, Path)) and Path(value).is_absolute():
            ref = self._register_artifact(project_id, value)
            if ref is not None:
                registry[ref.artifact_id] = ref

    def _media_ref(self, artifact_id: str) -> _ArtifactRef:
        value = str(artifact_id)
        if not _ARTIFACT_ID.fullmatch(value):
            raise KeyError(value)
        try:
            return self._registered_artifacts()[value]
        except KeyError as exc:
            raise KeyError(value) from exc

    def media_info(self, artifact_id: str) -> dict[str, Any]:
        ref = self._media_ref(artifact_id)
        with AnchoredDirectory.open(
            ref.root, label="Authorized artifact root"
        ) as anchor:
            parent = self._open_artifact_parent(anchor, ref.relative_path)
            try:
                descriptor = os.open(
                    ref.relative_path.name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                try:
                    metadata = os.fstat(descriptor)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise KeyError(artifact_id)
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
        return {
            "artifact_id": ref.artifact_id,
            "name": ref.name,
            "media_type": ref.media_type,
            "size": metadata.st_size,
        }

    def read_media(
        self,
        artifact_id: str,
        *,
        start: int = 0,
        end: int | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        info = self.media_info(artifact_id)
        size = int(info["size"])
        last = size - 1 if end is None else end
        if start < 0 or last < start or last >= size:
            raise ValueError("Media byte range is invalid")
        ref = self._media_ref(artifact_id)
        with AnchoredDirectory.open(
            ref.root, label="Authorized artifact root"
        ) as anchor:
            parent = self._open_artifact_parent(anchor, ref.relative_path)
            try:
                descriptor = os.open(
                    ref.relative_path.name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                try:
                    os.lseek(descriptor, start, os.SEEK_SET)
                    remaining = last - start + 1
                    chunks: list[bytes] = []
                    while remaining:
                        chunk = os.read(descriptor, min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("Authorized artifact changed while reading")
                        chunks.append(chunk)
                        remaining -= len(chunk)
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
        return info, b"".join(chunks)

    def iter_media(
        self,
        artifact_id: str,
        *,
        start: int = 0,
        end: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> tuple[dict[str, Any], Iterator[bytes]]:
        info = self.media_info(artifact_id)
        size = int(info["size"])
        last = size - 1 if end is None else end
        if start < 0 or last < start or last >= size:
            raise ValueError("Media byte range is invalid")
        ref = self._media_ref(artifact_id)

        def chunks() -> Iterator[bytes]:
            with AnchoredDirectory.open(
                ref.root, label="Authorized artifact root"
            ) as anchor:
                parent = self._open_artifact_parent(anchor, ref.relative_path)
                try:
                    descriptor = os.open(
                        ref.relative_path.name,
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=parent,
                    )
                    try:
                        os.lseek(descriptor, start, os.SEEK_SET)
                        remaining = last - start + 1
                        while remaining:
                            chunk = os.read(descriptor, min(chunk_size, remaining))
                            if not chunk:
                                raise OSError(
                                    "Authorized artifact changed while reading"
                                )
                            remaining -= len(chunk)
                            yield chunk
                    finally:
                        os.close(descriptor)
                finally:
                    os.close(parent)

        return info, chunks()

    def provider_status(self) -> dict[str, Any]:
        profile = self._load_provider_profile()
        if profile is None:
            return {"capabilities": {}}
        provider_secrets = tuple(
            value
            for capability in (
                profile.text,
                profile.image,
                profile.video,
                profile.audio,
            )
            for value in (capability.api_key, capability.base_url)
            if value
        )
        capabilities: dict[str, dict[str, Any]] = {}
        for name in ("text", "image", "video", "audio"):
            capability = getattr(profile, name)
            payload: dict[str, Any] = {
                "provider": capability.provider,
                "model": capability.model,
                "ready": capability.ready,
                "blockers": [
                    self._public_text(self._redact_values(item, provider_secrets))
                    for item in capability.blockers
                ],
                "enabled": capability.enabled,
            }
            if capability.supports_reference_images is not None:
                payload["supports_reference_images"] = (
                    capability.supports_reference_images
                )
            if capability.voice:
                payload["voice_configured"] = True
            capabilities[name] = payload
        return {"capabilities": capabilities}

    def _load_provider_profile(self) -> ProviderProfile | None:
        loader = self._provider_profile_loader
        try:
            signature = inspect.signature(loader)
        except (TypeError, ValueError):
            return loader()
        return loader(self.config) if signature.parameters else loader()

    @staticmethod
    def _redact_values(value: str, secrets: Sequence[str]) -> str:
        redacted = str(value)
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def public_error_message(self, error: Exception) -> str:
        return self._public_text(str(error)) or "Request could not be completed"

    def approve_stage(
        self,
        project_id: str,
        stage: str,
        *,
        revision: int,
        note: str,
        evidence_artifact_ids: Sequence[str],
    ) -> dict[str, Any]:
        project = self._project_dir(project_id)
        evidence = tuple(
            self._artifact_path_for_project(project_id, artifact_id)
            for artifact_id in evidence_artifact_ids
        )
        approve_stage(
            project,
            StageName(stage),
            revision=revision,
            note=note,
            evidence=evidence,
        )
        return self.stage_detail(project_id, stage)

    def request_stage_changes(
        self,
        project_id: str,
        stage: str,
        *,
        revision: int,
        reason: str,
    ) -> dict[str, Any]:
        request_stage_changes(
            self._project_dir(project_id),
            StageName(stage),
            revision=revision,
            reason=reason,
        )
        return self.stage_detail(project_id, stage)

    def _artifact_path_for_project(self, project_id: str, artifact_id: str) -> Path:
        ref = self._media_ref(artifact_id)
        if ref.project_id != project_id:
            raise KeyError(artifact_id)
        return ref.root / ref.relative_path

    def preview_impact(
        self, project_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        plan = preview_impact(
            self._project_dir(project_id),
            ChangeRequest.from_dict(request),
        )
        return self._impact_public(project_id, plan)

    def apply_impact(self, project_id: str, plan_id: str) -> dict[str, Any]:
        apply_impact_plan(self._project_dir(project_id), plan_id)
        return self.project_detail(project_id)

    def _impact_public(self, project_id: str, plan: ImpactPlan) -> dict[str, Any]:
        payload = plan.to_dict()
        payload["preserved_artifacts"] = [
            ref.artifact_id
            for path in plan.preserved_artifacts
            if (ref := self._register_artifact(project_id, path)) is not None
        ]
        return payload

    def video_preflight(
        self, project_id: str, shot_ids: Sequence[str]
    ) -> dict[str, Any]:
        preflight = build_video_preflight(
            self._project_dir(project_id), tuple(shot_ids)
        )
        payload = self._generation_request_public(
            project_id,
            VideoGenerationRequest.from_preflight(preflight),
        )
        payload.update(
            {
                "ready": preflight.ready,
                "blockers": [self._public_text(item) for item in preflight.blockers],
            }
        )
        return payload

    def confirm_video_preflight(
        self, project_id: str, shot_ids: Sequence[str]
    ) -> dict[str, Any]:
        project = self._project_dir(project_id)
        preflight = build_video_preflight(project, tuple(shot_ids))
        token = issue_generation_token(project, preflight)
        request = VideoGenerationRequest.from_preflight(preflight)
        return {
            "generation_token": token,
            "generation_request": self._generation_request_public(
                project_id,
                request,
            ),
        }

    def _generation_request_public(
        self,
        project_id: str,
        request: VideoGenerationRequest,
    ) -> dict[str, Any]:
        payload = request.to_dict()
        artifact_hashes: dict[str, str] = {}
        for path, digest in request.artifact_hashes.items():
            ref = self._register_artifact(project_id, path)
            if ref is None:
                raise ValueError("Confirmed generation artifact is not registered")
            artifact_hashes[ref.artifact_id] = digest
        payload["artifact_hashes"] = artifact_hashes
        return payload

    def _generation_request_internal(
        self,
        project_id: str,
        value: Mapping[str, Any],
    ) -> VideoGenerationRequest:
        payload = dict(value)
        raw_hashes = payload.get("artifact_hashes")
        if not isinstance(raw_hashes, Mapping):
            raise ValueError("Generation request artifact hashes are invalid")
        artifact_hashes: dict[str, str] = {}
        for artifact_id, digest in raw_hashes.items():
            ref = self._media_ref(str(artifact_id))
            if ref.project_id != project_id:
                raise KeyError(str(artifact_id))
            artifact_hashes[str(ref.root / ref.relative_path)] = str(digest)
        payload["artifact_hashes"] = artifact_hashes
        return VideoGenerationRequest.from_dict(payload)

    def create_project_job(
        self,
        *,
        project_id: str,
        title: str,
        mode: str,
        idea: str,
        target: Mapping[str, Any],
        approval_preset: str,
        source_artifact_id: str = "",
    ) -> dict[str, Any]:
        identifier = _safe_identifier(project_id, "project_id")
        project_mode = ProjectMode(mode)
        if project_mode is ProjectMode.ORIGINAL and not str(idea).strip():
            raise ValueError("Original projects require an idea")
        source_id = str(source_artifact_id).strip()
        if project_mode is not ProjectMode.ORIGINAL:
            if not source_id:
                raise ValueError(
                    "Non-original projects require a registered source artifact"
                )
            self._media_ref(source_id)
        payload = {
            "title": str(title).strip(),
            "mode": project_mode.value,
            "idea": str(idea).strip(),
            "source_artifact_id": source_id,
            "target": self._reject_path_input(target),
            "approval_preset": str(approval_preset),
        }
        job_id = self.jobs.submit(
            project_id=identifier,
            operation="create_project",
            payload=payload,
        )

        def create() -> Mapping[str, Any]:
            project = self._new_project_dir(identifier)
            if project_mode is ProjectMode.ORIGINAL:
                project_input = {"kind": "idea", "text": payload["idea"]}
            else:
                source = self._media_ref(payload["source_artifact_id"])
                project_input = {
                    "kind": (
                        "novel" if project_mode is ProjectMode.NOVEL else "reference"
                    ),
                    "path": str(source.root / source.relative_path),
                }
            spec = ProjectSpec(
                project_id=identifier,
                title=payload["title"],
                mode=project_mode,
                input=project_input,
                output_dir=project / "output",
                target=dict(payload["target"]),
                policies={"approval_preset": payload["approval_preset"]},
            )
            create_project(project, spec)
            return {"project_id": identifier}

        self._execute_job(job_id, create)
        return {"job_id": job_id, "status": "queued"}

    def submit_stage_run(
        self,
        project_id: str,
        stage: str,
        *,
        enable_live: bool = False,
    ) -> dict[str, Any]:
        target = StageName(stage)
        if target is StageName.VIDEO and enable_live:
            raise ValueError("Paid video generation requires confirmed video routes")
        project = self._project_dir(project_id)
        job_id = self.jobs.submit(
            project_id=project_id,
            operation="run_stage",
            payload={"stage": target.value, "enable_live": enable_live},
        )

        def run() -> Mapping[str, Any]:
            return _run_result(
                self._stage_runner(
                    project,
                    through=target,
                    enable_live=enable_live,
                )
            )

        self._execute_job(job_id, run)
        return {"job_id": job_id, "status": "queued"}

    def submit_video_generation(
        self,
        project_id: str,
        *,
        generation_token: str,
        generation_request: Mapping[str, Any],
        test_mode: bool,
    ) -> dict[str, Any]:
        project = self._project_dir(project_id)
        request = self._generation_request_internal(project_id, generation_request)
        if request.project_id != project_id:
            raise ValueError("Generation request belongs to another project")
        if not generation_token:
            raise ValueError("Generation confirmation is required")
        if test_mode and not 1 <= len(request.shot_ids) <= 3:
            raise ValueError("Test generation requires one to three shots")
        operation = "video_test" if test_mode else "video_generate"
        job_id = self.jobs.submit(
            project_id=project_id,
            operation=operation,
            payload={"generation_request": request.to_dict()},
        )

        def run() -> Mapping[str, Any]:
            return self._video_renderer(
                project_dir=project,
                generation_token=generation_token,
                generation_request=request,
                provider_tasks=dict(self.jobs.get(job_id).provider_tasks),
                provider_task_persisted=lambda shot_id, task_id, status: (
                    self.jobs.record_provider_task(
                        job_id,
                        shot_id=shot_id,
                        provider=request.provider,
                        task_id=task_id,
                        status=status,
                    )
                ),
                test_mode=test_mode,
            )

        self._execute_job(job_id, run)
        return {"job_id": job_id, "status": "queued"}

    def _execute_job(
        self, job_id: str, operation: Callable[[], Mapping[str, Any]]
    ) -> None:
        def execute() -> None:
            try:
                self.jobs.start(job_id)
                result = dict(operation())
                self.jobs.complete(job_id, result=result)
            except Exception as exc:
                try:
                    self.jobs.fail(job_id, error=self._public_text(str(exc)))
                except (KeyError, RuntimeError, ValueError):
                    pass

        self._dispatch(execute)

    def job_detail(self, job_id: str) -> dict[str, Any]:
        if not _JOB_ID.fullmatch(str(job_id)):
            raise KeyError(job_id)
        return self._job_public(self.jobs.get(job_id))

    def job_events(
        self, job_id: str, *, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        if not _JOB_ID.fullmatch(str(job_id)):
            raise KeyError(job_id)
        return [
            self._event_public(event)
            for event in self.jobs.events(job_id, after_sequence=after_sequence)
        ]

    def resume_job(self, job_id: str) -> dict[str, Any]:
        record = self.jobs.resume(job_id)
        if record.status == "completed":
            return self._job_public(record)
        if record.operation not in {"video_test", "video_generate"}:
            raise ValueError("Only provider jobs can be resumed through the public API")
        raw_request = record.payload.get("generation_request")
        if not isinstance(raw_request, dict):
            raise ValueError("Stored generation request is invalid")
        request = VideoGenerationRequest.from_dict(raw_request)
        project = self._project_dir(record.project_id)

        def run() -> Mapping[str, Any]:
            return self._video_renderer(
                project_dir=project,
                generation_token="",
                generation_request=request,
                provider_tasks=dict(self.jobs.get(job_id).provider_tasks),
                provider_task_persisted=lambda shot_id, task_id, status: (
                    self.jobs.record_provider_task(
                        job_id,
                        shot_id=shot_id,
                        provider=request.provider,
                        task_id=task_id,
                        status=status,
                    )
                ),
                test_mode=record.operation == "video_test",
            )

        self._execute_job(job_id, run)
        return {"job_id": job_id, "status": "queued"}

    def _job_public(self, record: JobRecord) -> dict[str, Any]:
        return {
            "job_id": record.job_id,
            "project_id": record.project_id,
            "operation": record.operation,
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "provider_tasks": self._public_value(record.provider_tasks),
            "result": self._public_job_value(record.project_id, record.result),
            "error": self._public_text(record.error),
            "resume_count": record.resume_count,
        }

    def _event_public(self, event: JobEvent) -> dict[str, Any]:
        return {
            "job_id": event.job_id,
            "sequence": event.sequence,
            "kind": event.kind,
            "data": self._public_value(event.data),
            "created_at": event.created_at,
        }

    def _public_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): self._public_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._public_value(item) for item in value]
        if isinstance(value, Path):
            return "[redacted-path]"
        if isinstance(value, str):
            return self._public_text(value)
        return value

    def _public_job_value(self, project_id: str, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): self._public_job_value(project_id, item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._public_job_value(project_id, item) for item in value]
        if isinstance(value, (str, Path)) and Path(value).is_absolute():
            ref = self._register_artifact(project_id, value)
            return ref.artifact_id if ref is not None else "[redacted-path]"
        return self._public_value(value)

    def _public_text(self, value: str) -> str:
        text = str(value)
        text = _TOKEN_TEXT.sub("[redacted]", text)
        text = _AUTH_TEXT.sub("[redacted]", text)
        text = _SECRET_ASSIGNMENT.sub("[redacted]", text)

        def sanitize_url(match: re.Match[str]) -> str:
            parsed = urlsplit(match.group(0))
            if (
                parsed.query
                or parsed.username is not None
                or parsed.password is not None
            ):
                return "[redacted-url]"
            return match.group(0)

        text = _URL_TEXT.sub(sanitize_url, text)
        if Path(text).is_absolute():
            return "[redacted-path]"
        return _EMBEDDED_PATH.sub("[redacted-path]", text)

    def _reject_path_input(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if "path" in normalized or normalized.endswith("dir"):
                    raise ValueError("Filesystem paths are not accepted")
                result[str(key)] = self._reject_path_input(item)
            return result
        if isinstance(value, list):
            return [self._reject_path_input(item) for item in value]
        if isinstance(value, str) and (Path(value).is_absolute() or "\\" in value):
            raise ValueError("Filesystem paths are not accepted")
        return value

    def _render_confirmed_video(self, **kwargs: Any) -> Mapping[str, Any]:
        project = Path(kwargs["project_dir"])
        request: VideoGenerationRequest = kwargs["generation_request"]
        spec, _package = self._load_project_records(project)
        profile = self._load_provider_profile()
        if profile is None or not profile.video.ready:
            blockers = () if profile is None else profile.video.blockers
            raise RuntimeError("; ".join(blockers) or "Video provider is not ready")
        if (
            profile.video.provider != request.provider
            or profile.video.model != request.model
        ):
            raise ValueError("Current provider does not match the confirmed request")
        step = get_mode_adapter(spec.mode).stage_steps[StageName.VIDEO]
        context = StageContext(project, spec, StageName.VIDEO, step, True)
        from .pipeline_generic_stages import (
            _assets,
            _audio_manifest,
            _episode,
            _factory_config,
        )

        episode = _episode(context)
        audio = _audio_manifest(context)
        assets = _assets(context)
        config = _factory_config(context)
        handoff = write_video_handoff(
            episode,
            config,
            context.stage_dir,
            assets,
            video_provider=request.provider,
            video_model=request.model,
        )
        shot_audio = write_shot_audio_assets(
            episode,
            Path(str(audio["voiceover_audio"])),
            context.stage_dir / "reference_audio",
        )
        package = write_openmontage_package(
            episode,
            config,
            character_assets=assets,
            run_dir=context.stage_dir,
            shot_audio=shot_audio,
        )
        repair_ids = tuple(context.repair_scope.get(StageName.VIDEO.value, ()))
        selected = request.shot_ids if kwargs.get("test_mode") else repair_ids
        return render_gateway_video_batch(
            handoff,
            package,
            build_video_client(profile.video, model=request.model),
            context.stage_dir / "gateway_video_batch.json",
            limit=0,
            resolution=request.resolution
            or default_video_resolution(profile.video.provider),
            allow_network=True,
            replace_stale=bool(repair_ids),
            repair_shot_ids=selected if repair_ids else (),
            project_dir=project,
            generation_token=kwargs["generation_token"],
            generation_request=request,
            provider_task_persisted=kwargs["provider_task_persisted"],
            provider_tasks=kwargs["provider_tasks"],
            selected_shot_ids=request.shot_ids,
        )
