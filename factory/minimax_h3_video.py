from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote, urlsplit
from urllib.request import Request

from .gateway_video import (
    DOWNLOAD_ATTEMPTS,
    GatewayVideoClient,
    GatewayVideoConfig,
    GatewayVideoError,
    GatewayVideoResult,
    GatewayVideoSubmission,
    GatewayVideoTask,
    GatewayVideoTransientError,
)


H3_RATIOS = {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
H3_OUTPUT_PRICE_YUAN_PER_SECOND = {"768P": 0.50, "2K": 0.80}
H3_IMAGE_ROLES = {"first_frame", "last_frame", "reference_image"}


@dataclass(frozen=True)
class MiniMaxH3ImageInput:
    source: str | Path
    role: str = "reference_image"


@dataclass(frozen=True)
class MiniMaxH3Config(GatewayVideoConfig):
    retry_submit_with_curl: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.model.strip().lower() == "minimax-h3":
            object.__setattr__(self, "model", "MiniMax-H3")


@dataclass(frozen=True)
class MiniMaxH3Task(GatewayVideoTask):
    usage: dict[str, int | float] | None = None


@dataclass(frozen=True)
class MiniMaxH3Result(GatewayVideoResult):
    usage: dict[str, int | float] | None = None
    resolution: str = "768P"
    estimated_cost_yuan: float = 0.0
    native_audio_generated: bool = True

    def to_report(self) -> dict[str, Any]:
        report = super().to_report()
        report.update(
            {
                "provider": "minimax",
                "usage": dict(self.usage or {}),
                "resolution": self.resolution,
                "estimated_cost_yuan": round(self.estimated_cost_yuan, 4),
                "native_audio_generated": self.native_audio_generated,
            }
        )
        return report


class MiniMaxH3Client(GatewayVideoClient):
    provider = "minimax"

    def __init__(self, config: MiniMaxH3Config, **kwargs: Any):
        super().__init__(config, **kwargs)
        self._task_settings: dict[str, dict[str, Any]] = {}

    def prepare_submission(
        self,
        prompt: str,
        *,
        images: Sequence[str | Path | MiniMaxH3ImageInput] | None = None,
        image_roles: Sequence[str] | None = None,
        audio: str | Path | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str = "768P",
        generate_audio: bool = False,
        allow_network: bool = False,
    ) -> GatewayVideoSubmission:
        del generate_audio
        image_inputs = tuple(images or ())
        explicit_roles = tuple(str(role).strip() for role in image_roles or ())
        if explicit_roles and len(explicit_roles) != len(image_inputs):
            raise GatewayVideoError("MiniMax H3 image roles must match images.")
        normalized_roles = tuple(
            image.role.strip()
            if isinstance(image, MiniMaxH3ImageInput)
            else explicit_roles[index]
            if explicit_roles
            else "reference_image"
            for index, image in enumerate(image_inputs)
        )
        normalized_resolution = _normalize_resolution(resolution)
        normalized_ratio = ratio.strip().lower()
        self._validate_h3_request(
            prompt,
            duration=duration,
            ratio=normalized_ratio,
            resolution=normalized_resolution,
            image_count=len(image_inputs),
            image_roles=normalized_roles,
            allow_network=allow_network,
        )
        image_values = [
            (
                self._normalize_image(
                    image.source if isinstance(image, MiniMaxH3ImageInput) else image
                ),
                role,
            )
            for image, role in zip(image_inputs, normalized_roles, strict=True)
        ]
        audio_value = self._normalize_audio(audio) if audio is not None else ""
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt.strip()}
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": value},
                "role": role,
            }
            for value, role in image_values
        )
        if audio_value:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": audio_value},
                    "role": "reference_audio",
                }
            )
        payload = {
            "model": self.config.model,
            "duration": duration,
            "resolution": normalized_resolution,
            "ratio": normalized_ratio,
            "content": content,
        }
        request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(request_body) > self.config.max_request_body_bytes:
            raise GatewayVideoError(
                "MiniMax H3 request body exceeded the maximum allowed size."
            )
        return GatewayVideoSubmission(
            endpoint=f"{_v2_base(self.config.base_url)}/video_generation",
            request_body=request_body,
        )

    def submit_prepared(
        self,
        submission: GatewayVideoSubmission,
        *,
        allow_network: bool = False,
    ) -> MiniMaxH3Task:
        task = super().submit_prepared(submission, allow_network=allow_network)
        if not isinstance(task, MiniMaxH3Task):
            task = MiniMaxH3Task(
                task_id=task.task_id,
                status=task.status,
                video_url=task.video_url,
                status_code=task.status_code,
            )
        try:
            payload = json.loads(submission.request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        if task.task_id:
            self._task_settings[task.task_id] = {
                "resolution": str(payload.get("resolution") or "768P"),
                "duration": int(payload.get("duration") or 0),
                "image_count": sum(
                    1
                    for item in payload.get("content") or ()
                    if isinstance(item, dict) and item.get("type") == "image_url"
                ),
            }
        return task

    def complete_task(
        self,
        task: GatewayVideoTask,
        output_path: str | Path,
        *,
        allow_network: bool = False,
        overwrite: bool = False,
        started_at: float | None = None,
    ) -> MiniMaxH3Result:
        self._validate_client_config(allow_network)
        output = Path(output_path)
        self._validate_output_target(output, overwrite=overwrite)
        started = self.monotonic() if started_at is None else started_at
        if task.status == "failed":
            raise GatewayVideoError("MiniMax H3 task failed during submission.")

        poll_count = 0
        completed_task = task
        if not task.video_url:
            if not task.task_id:
                raise GatewayVideoError(
                    "MiniMax H3 response did not include a task identifier."
                )
            completed_task, poll_count = self._wait_for_task(task.task_id)

        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                output_size = self._download(completed_task.video_url, output)
                break
            except GatewayVideoTransientError:
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise

        settings = self._task_settings.get(completed_task.task_id, {})
        resolution = str(settings.get("resolution") or "768P")
        usage = (
            dict(completed_task.usage or {})
            if isinstance(completed_task, MiniMaxH3Task)
            else {}
        )
        estimated_cost = _estimate_cost_yuan(
            usage,
            resolution=resolution,
            fallback_duration=int(settings.get("duration") or 0),
            fallback_image_count=int(settings.get("image_count") or 0),
        )
        return MiniMaxH3Result(
            output_path=str(output),
            model=self.config.model,
            task_id=completed_task.task_id,
            status="completed",
            poll_count=poll_count,
            output_size_bytes=output_size,
            duration_seconds=self.monotonic() - started,
            source_host=urlsplit(completed_task.video_url).hostname or "",
            usage=usage,
            resolution=resolution,
            estimated_cost_yuan=estimated_cost,
        )

    def restore_task_settings(
        self,
        task_id: str,
        *,
        resolution: str,
        duration: int,
        image_count: int,
    ) -> None:
        self._task_settings[task_id] = {
            "resolution": _normalize_resolution(resolution),
            "duration": int(duration),
            "image_count": int(image_count),
        }

    def validate_generation_settings(
        self,
        *,
        duration: int,
        ratio: str,
        resolution: str,
        image_count: int,
    ) -> None:
        self._validate_h3_generation_settings(
            duration=duration,
            ratio=ratio.strip().lower(),
            resolution=_normalize_resolution(resolution),
            image_count=image_count,
        )

    def _wait_for_task(self, task_id: str) -> tuple[MiniMaxH3Task, int]:
        endpoint = (
            f"{_v2_base(self.config.base_url)}/query/video_generation/"
            f"{quote(task_id, safe='')}"
        )
        deadline = self.monotonic() + self.config.max_wait_seconds
        poll_count = 0
        consecutive_transient_errors = 0
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise GatewayVideoError(
                    f"MiniMax H3 task {task_id} timed out after "
                    f"{self.config.max_wait_seconds:g} seconds."
                )
            if self.config.poll_interval_seconds > 0:
                self.sleep(min(self.config.poll_interval_seconds, remaining))
            request = Request(endpoint, method="GET", headers=self._headers())
            try:
                data, status_code = self._request_json(
                    request,
                    operation="poll",
                    timeout_seconds=min(self.config.timeout_seconds, remaining),
                )
            except GatewayVideoTransientError:
                consecutive_transient_errors += 1
                if consecutive_transient_errors >= 3:
                    raise
                continue
            consecutive_transient_errors = 0
            poll_count += 1
            task = self._parse_task(
                data,
                status_code=status_code,
                fallback_task_id=task_id,
            )
            if task.status == "completed":
                if not task.video_url:
                    raise GatewayVideoError(
                        f"MiniMax H3 task {task_id} completed without a video URL."
                    )
                return task, poll_count

    def _parse_task(
        self,
        data: Any,
        *,
        status_code: int,
        fallback_task_id: str = "",
    ) -> MiniMaxH3Task:
        if not isinstance(data, dict):
            raise GatewayVideoError("MiniMax H3 response must be a JSON object.")
        nested = data.get("task")
        task_data = nested if isinstance(nested, dict) else data
        task_id = str(
            task_data.get("task_id")
            or data.get("task_id")
            or fallback_task_id
            or ""
        )
        raw_status = str(task_data.get("status") or "queued").strip().lower()
        status = {
            "succeeded": "completed",
            "success": "completed",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "failed",
            "canceled": "failed",
            "queued": "queued",
            "running": "running",
            "processing": "running",
        }.get(raw_status, raw_status)
        content = task_data.get("content")
        video_url = ""
        if isinstance(content, dict):
            video_url = str(content.get("url") or "")
        usage = task_data.get("usage")
        normalized_usage = (
            {
                str(key): value
                for key, value in usage.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            if isinstance(usage, dict)
            else {}
        )
        if status == "failed":
            detail = _failure_detail(task_data)
            raise GatewayVideoError(
                self._sanitize(f"MiniMax H3 task failed{detail}.")
            )
        if not task_id and not video_url:
            raise GatewayVideoError(
                "MiniMax H3 response did not include a video URL or task identifier."
            )
        return MiniMaxH3Task(
            task_id=task_id,
            status=status,
            video_url=video_url,
            status_code=status_code,
            usage=normalized_usage,
        )

    def _validate_h3_request(
        self,
        prompt: str,
        *,
        duration: int,
        ratio: str,
        resolution: str,
        image_count: int,
        image_roles: Sequence[str] = (),
        allow_network: bool,
    ) -> None:
        self._validate_client_config(allow_network)
        if not prompt.strip():
            raise GatewayVideoError("MiniMax H3 prompt is empty.")
        self._validate_h3_generation_settings(
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            image_count=image_count,
            image_roles=image_roles,
        )

    def _validate_h3_generation_settings(
        self,
        *,
        duration: int,
        ratio: str,
        resolution: str,
        image_count: int,
        image_roles: Sequence[str] = (),
    ) -> None:
        if self.config.model != "MiniMax-H3":
            raise GatewayVideoError(
                "The MiniMax H3 adapter only supports MiniMax-H3."
            )
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 15:
            raise GatewayVideoError(
                "MiniMax H3 duration must be between 4 and 15 seconds."
            )
        if resolution not in H3_OUTPUT_PRICE_YUAN_PER_SECOND:
            raise GatewayVideoError("MiniMax H3 resolution must be 768P or 2K.")
        if ratio not in H3_RATIOS:
            raise GatewayVideoError("MiniMax H3 aspect ratio is unsupported.")
        if ratio == "adaptive" and image_count == 0:
            raise GatewayVideoError(
                "MiniMax H3 text-only generation requires a concrete aspect ratio."
            )
        if image_count > 9:
            raise GatewayVideoError(
                "MiniMax H3 accepts at most 9 reference images."
            )
        unknown_roles = [role for role in image_roles if role not in H3_IMAGE_ROLES]
        if unknown_roles:
            raise GatewayVideoError(
                f"MiniMax H3 image role is unsupported: {unknown_roles[0]}."
            )
        if image_roles.count("first_frame") > 1:
            raise GatewayVideoError("MiniMax H3 accepts at most one first_frame.")
        if image_roles.count("last_frame") > 1:
            raise GatewayVideoError("MiniMax H3 accepts at most one last_frame.")


def _v2_base(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    return normalized if normalized.endswith("/v2") else f"{normalized}/v2"


def _normalize_resolution(resolution: str) -> str:
    normalized = resolution.strip().upper()
    if normalized == "768P":
        return "768P"
    if normalized == "2K":
        return "2K"
    raise GatewayVideoError("MiniMax H3 resolution must be 768P or 2K.")


def _failure_detail(task_data: dict[str, Any]) -> str:
    error = task_data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("msg") or "").strip()
    else:
        message = str(error or "").strip()
    return f": {message}" if message else ""


def _estimate_cost_yuan(
    usage: dict[str, int | float],
    *,
    resolution: str,
    fallback_duration: int,
    fallback_image_count: int,
) -> float:
    duration = float(
        usage.get("output_video_duration")
        or usage.get("output_duration")
        or usage.get("output_seconds")
        or fallback_duration
        or 0
    )
    image_count = int(usage.get("input_image_count") or fallback_image_count or 0)
    output_cost = duration * H3_OUTPUT_PRICE_YUAN_PER_SECOND.get(resolution, 0.0)
    extra_image_cost = max(0, image_count - 5) * 0.20
    return round(output_cost + extra_image_cost, 4)
