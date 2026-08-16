from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Literal
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .media_types import safe_media_type
from .pipeline_jobs import ProjectBusyError
from .pipeline_store import ApprovalInProgressError, PipelineInProgressError
from .video_preflight import GenerationTokenError
from .workbench_service import (
    MediaSnapshotBusyError,
    MediaSnapshotQuotaError,
    MediaTooLargeError,
    WorkbenchService,
    sanitize_public_filename,
)


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_JOB_ID = re.compile(r"[0-9a-f]{32}")
_PLAN_ID = re.compile(r"[0-9a-f]{64}")
_RAW_PATH = re.compile(r"(?<![A-Za-z0-9:])/(?:[^\s,;:'\"]+/)*[^\s,;:'\"]+")


def _attachment_header(name: str) -> str:
    cleaned = sanitize_public_filename(name)
    fallback = cleaned.encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", fallback).strip(" .") or "download"
    fallback = fallback.replace('"', "_")[:120]
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"


async def _open_media_snapshot(service: WorkbenchService, artifact_id: str):
    task = asyncio.create_task(asyncio.to_thread(service.open_media, artifact_id))

    def close_late_snapshot(completed: asyncio.Task) -> None:
        try:
            opened = completed.result()
        except BaseException:
            return
        opened.close()

    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(close_late_snapshot)
        raise


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateProjectBody(_StrictBody):
    project_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    mode: Literal["original", "novel", "replica"]
    idea: str = Field(default="", max_length=200_000)
    source_artifact_id: str = Field(default="", max_length=128)
    target: dict[str, Any] = Field(default_factory=dict)
    approval_preset: Literal["quick", "standard", "strict"] = "standard"


class RunStageBody(_StrictBody):
    enable_live: bool = False


class ApproveStageBody(_StrictBody):
    revision: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=10_000)
    evidence_artifact_ids: list[str] = Field(min_length=1, max_length=100)


class RequestChangesBody(_StrictBody):
    revision: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=10_000)


class ImpactRequestBody(_StrictBody):
    stage: Literal[
        "concept",
        "script",
        "storyboard",
        "assets",
        "audio",
        "video",
        "edit",
        "eval",
        "deliver",
    ]
    scope: Literal["dialogue", "character", "shot", "subtitle_style"]
    dialogue_ids: list[str] = Field(default_factory=list, max_length=10_000)
    character_ids: list[str] = Field(default_factory=list, max_length=10_000)
    shot_ids: list[str] = Field(default_factory=list, max_length=10_000)
    subtitle_style: bool = False


class VideoPreflightBody(_StrictBody):
    shot_ids: list[str] = Field(min_length=1, max_length=10_000)


class VideoShotBody(_StrictBody):
    shot_id: str = Field(min_length=1, max_length=128)
    duration: int = Field(gt=0, le=3600)
    resolution: str = Field(min_length=1, max_length=32)


class VideoGenerationRequestBody(_StrictBody):
    schema_version: Literal["motion-comic-factory.video-generation-request.v1"]
    project_id: str = Field(min_length=1, max_length=128)
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_hashes: dict[str, str]
    artifact_hashes: dict[str, str]
    approval_hashes: dict[str, str]
    repair_plan_sha256: str = Field(pattern=r"^(?:[0-9a-f]{64})?$")
    shot_ids: list[str] = Field(min_length=1, max_length=10_000)
    shots: list[VideoShotBody] = Field(min_length=1, max_length=10_000)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    resolution: str = Field(min_length=1, max_length=32)
    output_seconds: int = Field(gt=0)
    estimated_cost_yuan: float = Field(gt=0)
    price_yuan_per_second: float = Field(gt=0)


class VideoGenerationBody(_StrictBody):
    generation_token: str = Field(min_length=10, max_length=300)
    generation_request: VideoGenerationRequestBody


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


def _public_error_message(service: WorkbenchService, error: Exception) -> str:
    sanitizer = getattr(service, "public_error_message", None)
    if callable(sanitizer):
        return str(sanitizer(error))
    return _RAW_PATH.sub("[redacted-path]", str(error))


def _identifier(value: str, pattern: re.Pattern[str] = _SAFE_ID) -> str:
    if not pattern.fullmatch(str(value)):
        raise ValueError("Identifier is invalid")
    return str(value)


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("Only one byte range is supported")
    spec = value[6:]
    if "-" not in spec:
        raise ValueError("Byte range is invalid")
    first, last = spec.split("-", 1)
    if not first:
        if not last.isdigit():
            raise ValueError("Byte range is invalid")
        suffix = int(last)
        if suffix <= 0 or size <= 0:
            raise ValueError("Byte range is invalid")
        return max(0, size - suffix), size - 1
    if not first.isdigit() or (last and not last.isdigit()):
        raise ValueError("Byte range is invalid")
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Byte range is unsatisfiable")
    return start, min(end, size - 1)


class _ClosingStreamingResponse(StreamingResponse):
    def __init__(self, *args: Any, close: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._close = close

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._close()


def create_workbench_app(service: WorkbenchService) -> FastAPI:
    app = FastAPI(title="StoryMotion Studio Workbench", docs_url=None, redoc_url=None)
    origins = tuple(service.frontend_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID", "Range"],
        expose_headers=[
            "Accept-Ranges",
            "Content-Disposition",
            "Content-Length",
            "Content-Range",
        ],
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error("invalid_request", "Request body is invalid", 400)

    @app.exception_handler(ProjectBusyError)
    @app.exception_handler(ApprovalInProgressError)
    @app.exception_handler(PipelineInProgressError)
    async def busy_error(_request: Request, exc: Exception) -> JSONResponse:
        return _error(
            "busy", _public_error_message(service, exc) or "Project is busy", 409
        )

    @app.exception_handler(GenerationTokenError)
    async def stale_error(_request: Request, exc: GenerationTokenError) -> JSONResponse:
        return _error("stale_confirmation", _public_error_message(service, exc), 409)

    @app.exception_handler(MediaTooLargeError)
    async def media_too_large_error(
        _request: Request, _exc: MediaTooLargeError
    ) -> JSONResponse:
        return _error(
            "media_too_large",
            "Media exceeds the configured snapshot limit",
            413,
        )

    @app.exception_handler(MediaSnapshotBusyError)
    async def media_busy_error(
        _request: Request, _exc: MediaSnapshotBusyError
    ) -> JSONResponse:
        return _error("media_busy", "Media snapshot capacity is busy", 429)

    @app.exception_handler(MediaSnapshotQuotaError)
    async def media_quota_error(
        _request: Request, _exc: MediaSnapshotQuotaError
    ) -> JSONResponse:
        return _error("media_quota", "Media snapshot byte quota is busy", 429)

    @app.exception_handler(KeyError)
    @app.exception_handler(FileNotFoundError)
    async def not_found_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error("not_found", "Resource was not found", 404)

    @app.exception_handler(ValueError)
    async def invalid_error(_request: Request, exc: ValueError) -> JSONResponse:
        return _error(
            "invalid_request",
            _public_error_message(service, exc) or "Request is invalid",
            400,
        )

    @app.exception_handler(RuntimeError)
    async def blocked_error(_request: Request, exc: RuntimeError) -> JSONResponse:
        message = _public_error_message(service, exc)
        if "already" in message.lower() or "busy" in message.lower():
            return _error("busy", message, 409)
        return _error("blocked", message or "Operation is blocked", 409)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error(
            "internal_error", "The workbench could not complete the request", 500
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects")
    async def projects() -> Any:
        return service.list_projects()

    @app.get("/api/works")
    async def works() -> Any:
        return service.list_works()

    @app.get("/api/works/{work_id}")
    async def work_detail(work_id: str) -> Any:
        return service.work_detail(_identifier(work_id))

    @app.post("/api/projects", status_code=202)
    async def create_project(body: CreateProjectBody) -> Any:
        return service.create_project_job(**body.model_dump())

    @app.get("/api/projects/{project_id}")
    async def project_detail(project_id: str) -> Any:
        return service.project_detail(_identifier(project_id))

    @app.get("/api/projects/{project_id}/stages/{stage}")
    async def stage_detail(project_id: str, stage: str) -> Any:
        return service.stage_detail(_identifier(project_id), _identifier(stage))

    @app.get("/api/projects/{project_id}/video/workspace")
    async def video_workspace(project_id: str) -> Any:
        return service.video_workspace(_identifier(project_id))

    @app.post("/api/projects/{project_id}/stages/{stage}/run", status_code=202)
    async def run_stage(project_id: str, stage: str, body: RunStageBody) -> Any:
        return service.submit_stage_run(
            _identifier(project_id),
            _identifier(stage),
            enable_live=body.enable_live,
        )

    @app.post("/api/projects/{project_id}/stages/{stage}/approve")
    async def approve(project_id: str, stage: str, body: ApproveStageBody) -> Any:
        for artifact_id in body.evidence_artifact_ids:
            _identifier(artifact_id)
        return service.approve_stage(
            _identifier(project_id),
            _identifier(stage),
            revision=body.revision,
            note=body.note,
            evidence_artifact_ids=body.evidence_artifact_ids,
        )

    @app.post("/api/projects/{project_id}/stages/{stage}/request-changes")
    async def request_changes(
        project_id: str, stage: str, body: RequestChangesBody
    ) -> Any:
        return service.request_stage_changes(
            _identifier(project_id),
            _identifier(stage),
            revision=body.revision,
            reason=body.reason,
        )

    @app.post("/api/projects/{project_id}/impact-plan")
    async def impact_plan(project_id: str, body: ImpactRequestBody) -> Any:
        return service.preview_impact(
            _identifier(project_id),
            body.model_dump(),
        )

    @app.post("/api/projects/{project_id}/impact-plan/{plan_id}/apply")
    async def apply_plan(project_id: str, plan_id: str) -> Any:
        return service.apply_impact(
            _identifier(project_id),
            _identifier(plan_id, _PLAN_ID),
        )

    @app.post("/api/projects/{project_id}/video/preflight")
    async def video_preflight(project_id: str, body: VideoPreflightBody) -> Any:
        return service.video_preflight(_identifier(project_id), body.shot_ids)

    @app.post("/api/projects/{project_id}/video/confirm")
    async def confirm_video(project_id: str, body: VideoPreflightBody) -> Any:
        return service.confirm_video_preflight(_identifier(project_id), body.shot_ids)

    async def submit_video(
        project_id: str,
        body: VideoGenerationBody,
        *,
        test_mode: bool,
    ) -> Any:
        return service.submit_video_generation(
            _identifier(project_id),
            generation_token=body.generation_token,
            generation_request=body.generation_request.model_dump(),
            test_mode=test_mode,
        )

    @app.post("/api/projects/{project_id}/video/test", status_code=202)
    async def test_video(project_id: str, body: VideoGenerationBody) -> Any:
        return await submit_video(project_id, body, test_mode=True)

    @app.post("/api/projects/{project_id}/video/generate", status_code=202)
    async def generate_video(project_id: str, body: VideoGenerationBody) -> Any:
        return await submit_video(project_id, body, test_mode=False)

    @app.get("/api/settings/providers")
    async def providers() -> Any:
        return service.provider_status()

    @app.get("/api/jobs/{job_id}")
    async def job_detail(job_id: str) -> Any:
        return service.job_detail(_identifier(job_id, _JOB_ID))

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    async def resume_job(job_id: str) -> Any:
        return service.resume_job(_identifier(job_id, _JOB_ID))

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(
        request: Request,
        job_id: str,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        selected_job = _identifier(job_id, _JOB_ID)
        try:
            after = int(last_event_id or "0")
        except ValueError as exc:
            raise ValueError("Last-Event-ID must be an event sequence") from exc
        if after < 0:
            raise ValueError("Last-Event-ID must be non-negative")
        service.job_detail(selected_job)

        async def events():
            sequence = after
            heartbeat_seconds = 15.0
            last_write = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                pending = service.job_events(selected_job, after_sequence=sequence)
                for event in pending:
                    sequence = int(event["sequence"])
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {sequence}\nevent: {event['kind']}\ndata: {data}\n\n"
                    last_write = asyncio.get_running_loop().time()
                job = service.job_detail(selected_job)
                if (
                    job["status"] in {"completed", "failed", "cancelled"}
                    and not pending
                ):
                    return
                now = asyncio.get_running_loop().time()
                if now - last_write >= heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    last_write = now
                await asyncio.sleep(0.2)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def media_response(
        artifact_id: str,
        range_header: str | None,
        *,
        head: bool,
        attachment: bool = False,
    ):
        selected = _identifier(artifact_id)
        info = service.media_info(selected)
        size = int(info["size"])
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": safe_media_type(info.get("media_type")),
        }
        if attachment:
            headers["Content-Disposition"] = _attachment_header(str(info["name"]))

        if range_header is None:
            headers["Content-Length"] = str(size)
            if head:
                return Response(status_code=200, headers=headers)
            if size == 0:
                return Response(content=b"", status_code=200, headers=headers)
            start, end, status_code = 0, size - 1, 200
        else:
            try:
                start, end = _parse_range(range_header, size)
            except ValueError:
                return Response(
                    status_code=416,
                    headers={**headers, "Content-Range": f"bytes */{size}"},
                )
            headers.update(
                {
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(end - start + 1),
                }
            )
            if head:
                return Response(status_code=206, headers=headers)
            status_code = 206
        opened = await _open_media_snapshot(service, selected)
        if int(opened.info["size"]) != size:
            opened.close()
            raise KeyError(selected)

        async def stream():
            try:
                for chunk in opened.iter_range(start=start, end=end):
                    yield chunk
                    await asyncio.sleep(0)
            finally:
                opened.close()

        return _ClosingStreamingResponse(
            stream(),
            status_code=status_code,
            headers=headers,
            close=opened.close,
        )

    @app.head("/api/media/{artifact_id}")
    async def head_media(
        artifact_id: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        return await media_response(artifact_id, range_header, head=True)

    @app.get("/api/media/{artifact_id}")
    async def get_media(
        artifact_id: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        return await media_response(artifact_id, range_header, head=False)

    @app.head("/api/download/{artifact_id}")
    async def head_download(
        artifact_id: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        return await media_response(
            artifact_id,
            range_header,
            head=True,
            attachment=True,
        )

    @app.get("/api/download/{artifact_id}")
    async def get_download(
        artifact_id: str,
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        return await media_response(
            artifact_id,
            range_header,
            head=False,
            attachment=True,
        )

    return app


def run_workbench_api(
    service: WorkbenchService,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Workbench API may only bind to localhost")
    uvicorn.run(create_workbench_app(service), host=host, port=port)
