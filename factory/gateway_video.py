from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from .media_validation import probe_media
from .provider_http_error import read_provider_http_error_detail


DOWNLOAD_ATTEMPTS = 3


class GatewayVideoError(RuntimeError):
    pass


class GatewayVideoTransientError(GatewayVideoError):
    pass


class GatewayVideoHTTPError(GatewayVideoError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GatewayVideoConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    submit_timeout_seconds: float = 300.0
    download_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 3.0
    max_wait_seconds: float = 900.0
    max_json_response_bytes: int = 2 * 1024 * 1024
    max_request_body_bytes: int = 24 * 1024 * 1024
    target_request_body_bytes: int = 2 * 1024 * 1024
    max_download_bytes: int = 512 * 1024 * 1024
    download_chunk_bytes: int = 1024 * 1024
    max_reference_image_bytes: int = 12 * 1024 * 1024
    max_reference_audio_bytes: int = 16 * 1024 * 1024
    send_idempotency_key: bool = False
    retry_submit_with_curl: bool = True


@dataclass(frozen=True)
class GatewayVideoSubmission:
    endpoint: str
    request_body: bytes


@dataclass(frozen=True)
class GatewayVideoTask:
    task_id: str
    status: str
    video_url: str = ""
    status_code: int = 200

    @property
    def response_shape(self) -> str:
        return "immediate_url" if self.video_url and not self.task_id else "async_task"


@dataclass(frozen=True)
class GatewayVideoResult:
    output_path: str
    model: str
    task_id: str
    status: str
    poll_count: int
    output_size_bytes: int
    duration_seconds: float
    source_host: str

    def to_report(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "model": self.model,
            "task_id": self.task_id,
            "status": self.status,
            "poll_count": self.poll_count,
            "output_size_bytes": self.output_size_bytes,
            "duration_seconds": round(self.duration_seconds, 3),
            "source_host": self.source_host,
        }


class GatewayVideoClient:
    provider = "gateway"

    def __init__(
        self,
        config: GatewayVideoConfig,
        *,
        urlopen_fn: Callable[..., Any] = urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        curl_runner: Callable[..., Any] = subprocess.run,
        curl_bin: str | None = None,
        enable_curl_fallback: bool | None = None,
    ):
        self.config = config
        self.urlopen = urlopen_fn
        self.sleep = sleep_fn
        self.monotonic = monotonic_fn
        self.curl_runner = curl_runner
        self.curl_bin = curl_bin or shutil.which("curl") or ""
        self.enable_curl_fallback = (
            urlopen_fn is urlopen
            if enable_curl_fallback is None
            else enable_curl_fallback
        )

    def validate_reference_images(
        self,
        images: Sequence[str | Path] | None,
    ) -> None:
        for image in images or ():
            self._normalize_image(image)

    def validate_reference_audio(self, audio: str | Path | None) -> None:
        if audio is not None:
            self._normalize_audio(audio)

    def submit(
        self,
        prompt: str,
        *,
        images: Sequence[str | Path] | None = None,
        image_roles: Sequence[str] | None = None,
        audio: str | Path | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str = "720p",
        generate_audio: bool = False,
        allow_network: bool = False,
    ) -> GatewayVideoTask:
        submission = self.prepare_submission(
            prompt,
            images=images,
            image_roles=image_roles,
            audio=audio,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            generate_audio=generate_audio,
            allow_network=allow_network,
        )
        return self.submit_prepared(submission, allow_network=allow_network)

    def prepare_submission(
        self,
        prompt: str,
        *,
        images: Sequence[str | Path] | None = None,
        image_roles: Sequence[str] | None = None,
        audio: str | Path | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str = "720p",
        generate_audio: bool = False,
        allow_network: bool = False,
    ) -> GatewayVideoSubmission:
        image_inputs = tuple(images or ())
        roles = tuple(image_roles or ("reference_image",) * len(image_inputs))
        if len(roles) != len(image_inputs):
            raise GatewayVideoError("Gateway video image roles must match images.")
        self._validate_request(
            prompt,
            duration,
            ratio,
            resolution,
            allow_network,
            image_count=len(image_inputs),
        )
        image_values = [self._normalize_image(image) for image in image_inputs]
        audio_value = self._normalize_audio(audio) if audio is not None else ""
        request_body = self._request_body(
            prompt,
            image_values,
            roles,
            audio_value,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            generate_audio=generate_audio,
        )
        transport_limit = min(
            self.config.target_request_body_bytes,
            self.config.max_request_body_bytes,
        )
        if (
            len(request_body) > transport_limit
            and image_inputs
        ):
            for max_dimension, quality in (
                (1536, 84),
                (1280, 78),
                (1024, 72),
                (896, 64),
                (768, 58),
            ):
                image_values = [
                    self._transport_image(
                        image,
                        normalized,
                        max_dimension=max_dimension,
                        quality=quality,
                    )
                    for image, normalized in zip(
                        image_inputs,
                        image_values,
                        strict=True,
                    )
                ]
                request_body = self._request_body(
                    prompt,
                    image_values,
                    roles,
                    audio_value,
                    duration=duration,
                    ratio=ratio,
                    resolution=resolution,
                    generate_audio=generate_audio,
                )
                if len(request_body) <= transport_limit:
                    break
        if len(request_body) > self.config.max_request_body_bytes:
            raise GatewayVideoError(
                "Gateway video request body exceeded the maximum allowed size."
            )
        endpoint = f"{self.config.base_url.rstrip('/')}/video/generations"
        return GatewayVideoSubmission(
            endpoint=endpoint,
            request_body=request_body,
        )

    def _request_body(
        self,
        prompt: str,
        image_values: Sequence[str],
        image_roles: Sequence[str],
        audio_value: str,
        *,
        duration: int,
        ratio: str,
        resolution: str,
        generate_audio: bool,
    ) -> bytes:
        metadata: dict[str, Any] = {
            # New API's Doubao adapter currently reads provider duration from metadata.
            "duration": duration,
            "ratio": ratio.strip(),
            "resolution": resolution.strip(),
            "generate_audio": bool(generate_audio),
        }
        content = [
                {
                    "type": "image_url",
                    "image_url": {"url": value},
                    "role": role,
                }
                for value, role in zip(image_values, image_roles, strict=True)
            ]
        if audio_value:
            content.append(
                {
                    "type": "audio_url",
                    "audio_url": {"url": audio_value},
                    "role": "reference_audio",
                }
            )
        if content:
            metadata["content"] = content
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": prompt.strip(),
            "duration": duration,
            "metadata": metadata,
        }
        if "seedance-2-0" in self.config.model.strip().lower():
            payload["seconds"] = str(duration)
            payload["size"] = resolution.strip()
        if image_values:
            payload["images"] = image_values

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _transport_image(
        self,
        image: str | Path,
        normalized: str,
        *,
        max_dimension: int,
        quality: int,
    ) -> str:
        value = str(image).strip()
        if value.lower().startswith(("https://", "http://", "data:image/")):
            return normalized
        try:
            with Image.open(Path(value).expanduser()) as opened:
                converted = ImageOps.exif_transpose(opened)
                if converted.mode in {"RGBA", "LA"} or (
                    converted.mode == "P" and "transparency" in converted.info
                ):
                    rgba = converted.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = converted.convert("RGB")
                rgb.thumbnail(
                    (max_dimension, max_dimension),
                    Image.Resampling.LANCZOS,
                )
                buffer = BytesIO()
                rgb.save(
                    buffer,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                    subsampling=2,
                )
        except (OSError, ValueError):
            return normalized
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def submit_prepared(
        self,
        submission: GatewayVideoSubmission,
        *,
        allow_network: bool = False,
    ) -> GatewayVideoTask:
        self._validate_client_config(allow_network)
        request = Request(
            submission.endpoint,
            data=submission.request_body,
            method="POST",
            headers=self._headers(
                idempotency_key=(
                    self._submission_idempotency_key(submission)
                    if self.config.send_idempotency_key
                    else ""
                )
            ),
        )
        data, status_code = self._request_json(
            request,
            operation="submit",
            timeout_seconds=self.config.submit_timeout_seconds,
        )
        return self._parse_task(data, status_code=status_code)

    def generate(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        images: Sequence[str | Path] | None = None,
        image_roles: Sequence[str] | None = None,
        audio: str | Path | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str = "720p",
        generate_audio: bool = False,
        allow_network: bool = False,
        overwrite: bool = False,
    ) -> GatewayVideoResult:
        output = Path(output_path)
        self._validate_output_target(output, overwrite=overwrite)
        started = self.monotonic()
        task = self.submit(
            prompt,
            images=images,
            image_roles=image_roles,
            audio=audio,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            generate_audio=generate_audio,
            allow_network=allow_network,
        )
        return self.complete_task(
            task,
            output,
            allow_network=True,
            overwrite=overwrite,
            started_at=started,
        )

    def complete_task(
        self,
        task: GatewayVideoTask,
        output_path: str | Path,
        *,
        allow_network: bool = False,
        overwrite: bool = False,
        started_at: float | None = None,
    ) -> GatewayVideoResult:
        self._validate_client_config(allow_network)
        output = Path(output_path)
        self._validate_output_target(output, overwrite=overwrite)
        started = self.monotonic() if started_at is None else started_at
        if task.status == "failed":
            raise GatewayVideoError("Gateway video task failed during submission.")

        poll_count = 0
        if not task.video_url:
            if not task.task_id:
                raise GatewayVideoError(
                    "Gateway video response did not include a video URL or task identifier."
                )
            task, poll_count = self._wait_for_task(task.task_id)

        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                output_size = self._download(task.video_url, output)
                break
            except GatewayVideoTransientError:
                if attempt == DOWNLOAD_ATTEMPTS:
                    raise
        source_host = urlsplit(task.video_url).hostname or ""
        return GatewayVideoResult(
            output_path=str(output),
            model=self.config.model,
            task_id=task.task_id,
            status="completed",
            poll_count=poll_count,
            output_size_bytes=output_size,
            duration_seconds=self.monotonic() - started,
            source_host=source_host,
        )

    def _wait_for_task(self, task_id: str) -> tuple[GatewayVideoTask, int]:
        encoded_task_id = quote(task_id, safe="")
        endpoint = (
            f"{self.config.base_url.rstrip('/')}/video/generations/{encoded_task_id}"
        )
        deadline = self.monotonic() + self.config.max_wait_seconds
        poll_count = 0
        consecutive_transient_errors = 0
        while True:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise GatewayVideoError(
                    f"Gateway video task {task_id} timed out after "
                    f"{self.config.max_wait_seconds:g} seconds."
                )
            if self.config.poll_interval_seconds > 0:
                self.sleep(min(self.config.poll_interval_seconds, remaining))
                if self.monotonic() >= deadline:
                    raise GatewayVideoError(
                        f"Gateway video task {task_id} timed out after "
                        f"{self.config.max_wait_seconds:g} seconds."
                    )
            request = Request(endpoint, method="GET", headers=self._headers())
            request_timeout = min(
                self.config.timeout_seconds,
                deadline - self.monotonic(),
            )
            try:
                data, status_code = self._request_json(
                    request,
                    operation="poll",
                    timeout_seconds=request_timeout,
                )
            except GatewayVideoTransientError:
                consecutive_transient_errors += 1
                if consecutive_transient_errors >= 3:
                    raise
                continue
            consecutive_transient_errors = 0
            poll_count += 1
            task = self._parse_task(data, status_code=status_code, fallback_task_id=task_id)
            if task.status == "completed":
                if not task.video_url:
                    raise GatewayVideoError(
                        f"Gateway video task {task_id} completed without a video URL."
                    )
                return task, poll_count
            if task.status == "failed":
                detail = _task_error_detail(data)
                raise GatewayVideoError(
                    self._sanitize(f"Gateway video task failed{detail}.")
                )

    def _request_json(
        self,
        request: Request,
        *,
        operation: str,
        timeout_seconds: float | None = None,
    ) -> tuple[Any, int]:
        try:
            timeout = self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
            with self.urlopen(request, timeout=timeout) as response:
                status_code = int(getattr(response, "status", 200))
                body = response.read(self.config.max_json_response_bytes + 1)
            if len(body) > self.config.max_json_response_bytes:
                raise GatewayVideoError(
                    f"Gateway video {operation} JSON response exceeded the maximum "
                    "allowed size."
                )
            return json.loads(body.decode("utf-8")), status_code
        except GatewayVideoError:
            raise
        except HTTPError as exc:
            detail = read_provider_http_error_detail(
                exc,
                api_key=self.config.api_key,
            )
            message = _http_error(exc.code, operation=operation)
            if detail:
                message = f"{message} Provider detail: {detail}."
            raise GatewayVideoHTTPError(
                self._sanitize(message),
                status_code=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            curl_retry_allowed = (
                operation != "submit" or self.config.retry_submit_with_curl
            )
            if self.enable_curl_fallback and self.curl_bin and curl_retry_allowed:
                return self._request_json_via_curl(
                    request,
                    operation=operation,
                    timeout_seconds=timeout_seconds,
                )
            raise GatewayVideoTransientError(
                self._sanitize(f"Gateway video {operation} request failed: {exc}")
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise GatewayVideoError(
                self._sanitize(f"Invalid gateway video {operation} response: {exc}")
            ) from exc

    def _download(self, url: str, output: Path) -> int:
        if not url:
            raise GatewayVideoError("Gateway video download URL is empty.")
        if not _is_safe_http_url(url):
            raise GatewayVideoError(
                "Gateway video download URL must use HTTP or HTTPS."
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".part")
        partial.unlink(missing_ok=True)
        try:
            request = Request(url, method="GET")
            output_size = 0
            with self.urlopen(request, timeout=self.config.download_timeout_seconds) as response:
                headers = getattr(response, "headers", {})
                content_length_value = headers.get("Content-Length") if headers else None
                try:
                    expected_size = (
                        int(content_length_value)
                        if content_length_value is not None
                        else None
                    )
                except (TypeError, ValueError):
                    expected_size = None
                with partial.open("wb") as handle:
                    while True:
                        chunk = response.read(self.config.download_chunk_bytes)
                        if not chunk:
                            break
                        output_size += len(chunk)
                        if output_size > self.config.max_download_bytes:
                            raise GatewayVideoError(
                                "Gateway video download exceeded the maximum allowed size."
                            )
                        handle.write(chunk)
            if expected_size is not None and expected_size >= 0 and output_size != expected_size:
                raise GatewayVideoTransientError(
                    "Gateway video download size did not match Content-Length."
                )
            if output_size <= 0:
                raise GatewayVideoError("Gateway video download returned an empty body.")
            if not is_valid_mp4_file(partial):
                raise GatewayVideoError(
                    "Gateway video download did not contain a valid MP4 file."
                )
            partial.replace(output)
            return output_size
        except GatewayVideoError:
            partial.unlink(missing_ok=True)
            raise
        except HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise GatewayVideoError(_http_error(exc.code, operation="download")) from exc
        except (URLError, TimeoutError, OSError) as exc:
            partial.unlink(missing_ok=True)
            if self.enable_curl_fallback and self.curl_bin:
                return self._download_via_curl(url, output)
            raise GatewayVideoError(
                self._sanitize(f"Gateway video download failed: {exc}")
            ) from exc

    def _request_json_via_curl(
        self,
        request: Request,
        *,
        operation: str,
        timeout_seconds: float | None,
    ) -> tuple[Any, int]:
        timeout = (
            self.config.timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        with tempfile.TemporaryDirectory(prefix="gateway-video-curl-") as directory:
            response_path = Path(directory) / "response.json"
            status_code = self._curl_to_file(
                request,
                response_path,
                timeout_seconds=timeout,
            )
            try:
                body = response_path.read_bytes()
            except OSError as exc:
                raise GatewayVideoTransientError(
                    f"Gateway video {operation} curl response could not be read."
                ) from exc
            if len(body) > self.config.max_json_response_bytes:
                raise GatewayVideoError(
                    f"Gateway video {operation} JSON response exceeded the maximum "
                    "allowed size."
                )
            try:
                data = json.loads(body.decode("utf-8"))
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                TypeError,
                ValueError,
            ) as exc:
                raise GatewayVideoError(
                    self._sanitize(
                        f"Invalid gateway video {operation} response: {exc}"
                    )
                ) from exc
            if status_code >= 400:
                message = _http_error(status_code, operation=operation)
                detail = _task_error_detail(data)
                if detail:
                    message = f"{message.rstrip('.')} Provider detail{detail}."
                raise GatewayVideoHTTPError(
                    self._sanitize(message),
                    status_code=status_code,
                )
            return data, status_code

    def _curl_to_file(
        self,
        request: Request,
        output_path: Path,
        *,
        timeout_seconds: float,
    ) -> int:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="gateway-video-curl-body-"
        ) as directory:
            command = [
                self.curl_bin,
                "--config",
                "-",
                "--http1.1",
                "--silent",
                "--show-error",
                "--max-time",
                str(max(1.0, float(timeout_seconds))),
                "--request",
                request.get_method(),
                "--output",
                str(output_path),
                "--write-out",
                "%{http_code}",
            ]
            if request.data is not None:
                request_path = Path(directory) / "request.json"
                request_path.write_bytes(request.data)
                command.extend(["--data-binary", f"@{request_path}"])
            command.append(request.full_url)

            config_lines = []
            for name, value in request.header_items():
                escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
                config_lines.append(f'header = "{name}: {escaped}"')
            result = self.curl_runner(
                command,
                input="\n".join(config_lines) + "\n",
                text=True,
                capture_output=True,
                check=False,
            )
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            detail = self._sanitize(str(result.stderr or "").strip())[-800:]
            suffix = f": {detail}" if detail else ""
            raise GatewayVideoTransientError(
                f"Gateway video curl request failed{suffix}."
            )
        try:
            return int(str(result.stdout).strip())
        except ValueError as exc:
            output_path.unlink(missing_ok=True)
            raise GatewayVideoTransientError(
                "Gateway video curl request returned no HTTP status."
            ) from exc

    def _download_via_curl(self, url: str, output: Path) -> int:
        partial = output.with_suffix(output.suffix + ".part")
        partial.unlink(missing_ok=True)
        status_code = self._curl_to_file(
            Request(url, method="GET"),
            partial,
            timeout_seconds=self.config.download_timeout_seconds,
        )
        if status_code >= 400:
            partial.unlink(missing_ok=True)
            raise GatewayVideoError(
                _http_error(status_code, operation="download")
            )
        try:
            output_size = partial.stat().st_size
        except OSError as exc:
            raise GatewayVideoTransientError(
                "Gateway video curl download did not create a file."
            ) from exc
        if output_size <= 0:
            partial.unlink(missing_ok=True)
            raise GatewayVideoError(
                "Gateway video download returned an empty body."
            )
        if output_size > self.config.max_download_bytes:
            partial.unlink(missing_ok=True)
            raise GatewayVideoError(
                "Gateway video download exceeded the maximum allowed size."
            )
        if not is_valid_mp4_file(partial):
            partial.unlink(missing_ok=True)
            raise GatewayVideoError(
                "Gateway video download did not contain a valid MP4 file."
            )
        partial.replace(output)
        return output_size

    def _normalize_image(self, image: str | Path) -> str:
        value = str(image).strip()
        if not value:
            raise GatewayVideoError("Gateway video reference image is empty.")
        if value.lower().startswith(("https://", "http://")):
            if not _is_safe_http_url(value):
                raise GatewayVideoError(
                    "Gateway video reference image must be a valid HTTP or HTTPS URL."
                )
            return value
        if value.startswith("data:image/"):
            return self._validate_data_uri(value)

        path = Path(value).expanduser()
        if not path.is_file():
            raise GatewayVideoError(f"Gateway video reference image not found: {path}")
        size = path.stat().st_size
        if size <= 0:
            raise GatewayVideoError(f"Gateway video reference image is empty: {path}")
        if size > self.config.max_reference_image_bytes:
            raise GatewayVideoError(
                f"Gateway video reference image exceeds the maximum allowed size: {path}"
            )
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise GatewayVideoError(
                f"Gateway video reference image must be PNG, JPEG, or WebP: {path}"
            )
        try:
            raw_image = path.read_bytes()
        except OSError as exc:
            raise GatewayVideoError(
                f"Unable to read gateway video reference image: {path}"
            ) from exc
        mime_type = _supported_image_mime_type(raw_image)
        if not mime_type:
            raise GatewayVideoError(
                f"Gateway video reference image must contain valid PNG, JPEG, or WebP data: {path}"
            )
        encoded = base64.b64encode(raw_image).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _normalize_audio(self, audio: str | Path) -> str:
        value = str(audio).strip()
        if not value:
            raise GatewayVideoError("Gateway video reference audio is empty.")
        if value.lower().startswith(("https://", "http://")):
            if not _is_safe_http_url(value) or not value.lower().startswith("https://"):
                raise GatewayVideoError(
                    "Gateway video reference audio must be a valid HTTPS URL without credentials."
                )
            return value
        if value.lower().startswith("data:audio/"):
            return self._validate_audio_data_uri(value)

        path = Path(value).expanduser()
        if path.is_symlink():
            raise GatewayVideoError("Gateway video reference audio must not be a symlink.")
        if not path.is_file():
            raise GatewayVideoError("Gateway video reference audio file was not found.")
        if path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac"}:
            raise GatewayVideoError(
                "Gateway video reference audio must use WAV, MP3, M4A, or AAC."
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise GatewayVideoError("Unable to inspect gateway video reference audio.") from exc
        if size <= 0:
            raise GatewayVideoError("Gateway video reference audio is empty.")
        if size > self.config.max_reference_audio_bytes:
            raise GatewayVideoError(
                "Gateway video reference audio exceeds the maximum allowed size."
            )
        probe = probe_media(path, required_stream="audio")
        if (
            not probe.valid
            or probe.audio_stream_count != 1
            or probe.video_stream_count != 0
            or probe.duration_seconds <= 0
        ):
            raise GatewayVideoError("Gateway video reference audio is not valid audio.")
        try:
            with path.open("rb") as handle:
                opened_size = handle.seek(0, 2)
                handle.seek(0)
                if opened_size <= 0 or opened_size > self.config.max_reference_audio_bytes:
                    raise GatewayVideoError(
                        "Gateway video reference audio is empty or exceeds the maximum allowed size."
                    )
                raw_audio = handle.read(opened_size)
        except OSError as exc:
            raise GatewayVideoError("Unable to read gateway video reference audio.") from exc
        if not raw_audio or len(raw_audio) != opened_size:
            raise GatewayVideoError(
                "Gateway video reference audio is empty or exceeds the maximum allowed size."
            )
        mime_type = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
        }[path.suffix.lower()]
        encoded = base64.b64encode(raw_audio).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _validate_data_uri(self, value: str) -> str:
        match = re.fullmatch(
            r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)",
            value,
        )
        if not match:
            raise GatewayVideoError("Gateway video reference data URI is invalid.")
        encoded = match.group(2)
        max_encoded_bytes = 4 * ((self.config.max_reference_image_bytes + 2) // 3)
        if len(encoded) > max_encoded_bytes:
            raise GatewayVideoError(
                "Gateway video reference data URI is empty or exceeds the maximum allowed size."
            )
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise GatewayVideoError("Gateway video reference data URI is invalid.") from exc
        if not decoded or len(decoded) > self.config.max_reference_image_bytes:
            raise GatewayVideoError(
                "Gateway video reference data URI is empty or exceeds the maximum allowed size."
            )
        if not _is_supported_image_bytes(decoded, match.group(1)):
            raise GatewayVideoError(
                "Gateway video reference data URI must contain valid PNG, JPEG, or WebP data."
            )
        return value

    def _validate_audio_data_uri(self, value: str) -> str:
        match = re.fullmatch(
            r"data:(audio/(?:wav|mpeg|mp4|aac));base64,([A-Za-z0-9+/]*={0,2})",
            value,
        )
        if not match:
            raise GatewayVideoError("Gateway video reference audio data URI is invalid.")
        encoded = match.group(2)
        max_encoded_bytes = 4 * ((self.config.max_reference_audio_bytes + 2) // 3)
        if not encoded or len(encoded) > max_encoded_bytes:
            raise GatewayVideoError(
                "Gateway video reference audio data URI is empty or exceeds the maximum allowed size."
            )
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise GatewayVideoError(
                "Gateway video reference audio data URI is invalid."
            ) from exc
        if not decoded or len(decoded) > self.config.max_reference_audio_bytes:
            raise GatewayVideoError(
                "Gateway video reference audio data URI is empty or exceeds the maximum allowed size."
            )
        return value

    def _parse_task(
        self,
        data: Any,
        *,
        status_code: int,
        fallback_task_id: str = "",
    ) -> GatewayVideoTask:
        if not isinstance(data, dict):
            raise GatewayVideoError("Gateway video response must be a JSON object.")
        task_data = _task_data(data)
        task_id = _find_task_id(data) or fallback_task_id
        video_url = _find_video_url(data)
        status_value = task_data.get("status") if isinstance(task_data, dict) else None
        status = _normalize_status(status_value, video_url=video_url)
        if status == "failed":
            detail = _task_error_detail(task_data)
            raise GatewayVideoError(self._sanitize(f"Gateway video task failed{detail}."))
        if not task_id and not video_url:
            raise GatewayVideoError(
                "Gateway video response did not include a video URL or task identifier."
            )
        return GatewayVideoTask(
            task_id=task_id,
            status=status,
            video_url=video_url,
            status_code=status_code,
        )

    def _validate_request(
        self,
        prompt: str,
        duration: int,
        ratio: str,
        resolution: str,
        allow_network: bool,
        image_count: int,
    ) -> None:
        self._validate_client_config(allow_network)
        if not prompt.strip():
            raise GatewayVideoError("Gateway video prompt is empty.")
        validate_gateway_video_generation_settings(
            model=self.config.model,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            image_count=image_count,
        )

    def _validate_client_config(self, allow_network: bool) -> None:
        if not allow_network:
            raise GatewayVideoError(
                "Gateway video network access must be explicitly enabled."
            )
        if not self.config.api_key:
            raise GatewayVideoError("Gateway video API key is missing.")
        if not self.config.base_url.strip():
            raise GatewayVideoError("Gateway video base URL is missing.")
        if not _is_safe_http_url(self.config.base_url, allow_query=False):
            raise GatewayVideoError(
                "Gateway video base URL must use HTTP or HTTPS without credentials, "
                "a query, or a fragment."
            )
        if not self.config.model.strip():
            raise GatewayVideoError("Gateway video model is missing.")
        if self.config.timeout_seconds <= 0:
            raise GatewayVideoError("Gateway video request timeout must be positive.")
        if self.config.submit_timeout_seconds <= 0:
            raise GatewayVideoError("Gateway video submit timeout must be positive.")
        if self.config.download_timeout_seconds <= 0:
            raise GatewayVideoError("Gateway video download timeout must be positive.")
        if self.config.max_wait_seconds <= 0:
            raise GatewayVideoError("Gateway video maximum wait must be positive.")
        if self.config.poll_interval_seconds < 0:
            raise GatewayVideoError("Gateway video poll interval cannot be negative.")
        if self.config.max_json_response_bytes <= 0:
            raise GatewayVideoError(
                "Gateway video maximum JSON response size must be positive."
            )
        if self.config.max_request_body_bytes <= 0:
            raise GatewayVideoError(
                "Gateway video maximum request body size must be positive."
            )
        if self.config.target_request_body_bytes <= 0:
            raise GatewayVideoError(
                "Gateway video target request body size must be positive."
            )
        if self.config.max_download_bytes <= 0:
            raise GatewayVideoError("Gateway video maximum download size must be positive.")
        if self.config.download_chunk_bytes <= 0:
            raise GatewayVideoError("Gateway video download chunk size must be positive.")
        if self.config.max_reference_image_bytes <= 0:
            raise GatewayVideoError(
                "Gateway video maximum reference image size must be positive."
            )
        if self.config.max_reference_audio_bytes <= 0:
            raise GatewayVideoError(
                "Gateway video maximum reference audio size must be positive."
            )

    @staticmethod
    def _validate_output_target(output: Path, *, overwrite: bool) -> None:
        prepare_gateway_video_output_target(output, overwrite=overwrite)

    def _headers(self, *, idempotency_key: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _submission_idempotency_key(
        submission: GatewayVideoSubmission,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(submission.endpoint.encode("utf-8"))
        digest.update(b"\0")
        digest.update(submission.request_body)
        return f"mcf-video-{digest.hexdigest()}"

    def _sanitize(self, message: str) -> str:
        sanitized = message.replace(self.config.api_key, "[redacted]") if self.config.api_key else message
        sanitized = re.sub(
            r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+",
            "[redacted-image]",
            sanitized,
        )
        sanitized = re.sub(
            r"data:audio/[A-Za-z0-9.+-]+;base64,[^\s<>'\"]+",
            "[redacted-audio]",
            sanitized,
        )
        return re.sub(
            r"https?://[^\s<>'\"]+",
            lambda match: _safe_url(match.group(0)) or "[redacted-url]",
            sanitized,
        )


def validate_gateway_video_generation_settings(
    *,
    model: str,
    duration: int,
    ratio: str,
    resolution: str,
    image_count: int,
) -> None:
    if (
        isinstance(duration, bool)
        or not isinstance(duration, int)
        or not 1 <= duration <= 3600
    ):
        raise GatewayVideoError(
            "Gateway video duration must be between 1 and 3600 seconds."
        )
    if not ratio.strip():
        raise GatewayVideoError("Gateway video aspect ratio is empty.")
    if not resolution.strip():
        raise GatewayVideoError("Gateway video resolution is empty.")
    normalized_model = model.strip().lower()
    if not normalized_model:
        raise GatewayVideoError("Gateway video model is missing.")
    if "seedance-2-0" in normalized_model:
        if duration < 4:
            raise GatewayVideoError(
                "Seedance 2.0 video duration must be between 4 and 15 seconds."
            )
        if duration > 15:
            raise GatewayVideoError(
                "Seedance 2.0 video duration can be at most 15 seconds."
            )
        if image_count > 9:
            raise GatewayVideoError(
                "Seedance 2.0 accepts at most 9 reference images."
            )
    if "seedance-2-0-fast" in normalized_model and resolution.strip().lower() not in {
            "480p",
            "720p",
    }:
        raise GatewayVideoError(
            "Seedance 2.0 Fast supports 480p or 720p resolution."
        )
    if normalized_model == "minimax-h3":
        if not 4 <= duration <= 15:
            raise GatewayVideoError(
                "MiniMax H3 duration must be between 4 and 15 seconds."
            )
        if image_count > 9:
            raise GatewayVideoError(
                "MiniMax H3 accepts at most 9 reference images."
            )
        if resolution.strip().upper() not in {"768P", "2K"}:
            raise GatewayVideoError(
                "MiniMax H3 resolution must be 768P or 2K."
            )
        normalized_ratio = ratio.strip().lower()
        if normalized_ratio not in {
            "adaptive",
            "21:9",
            "16:9",
            "4:3",
            "1:1",
            "3:4",
            "9:16",
        }:
            raise GatewayVideoError(
                "MiniMax H3 aspect ratio is unsupported."
            )
        if normalized_ratio == "adaptive" and image_count == 0:
            raise GatewayVideoError(
                "MiniMax H3 text-only generation requires a concrete aspect ratio."
            )


class GatewayVideoProbe:
    def __init__(
        self,
        config: GatewayVideoConfig,
        *,
        urlopen_fn: Callable[..., Any] = urlopen,
    ):
        self.config = config
        self.urlopen = urlopen_fn

    def run(
        self,
        prompt: str,
        *,
        images: Sequence[str | Path] | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str = "720p",
        generate_audio: bool = False,
        allow_network: bool = False,
    ) -> dict[str, Any]:
        endpoint = f"{self.config.base_url.rstrip('/')}/video/generations"
        report: dict[str, Any] = {
            "schema_version": "motion-comic-factory.gateway-video-probe.v2",
            "model": self.config.model,
            "endpoint": endpoint,
            "executed": False,
            "success": False,
            "production_ready": False,
            "validation_scope": "submission_only",
            "billable_submission": False,
            "response_shape": "none",
            "video_url": "",
            "task_id": "",
            "status_url": "",
            "status_code": None,
            "duration_seconds": 0.0,
            "blocked_reasons": [],
            "error": "",
        }
        if not allow_network:
            report["blocked_reasons"] = ["Live gateway video probe is disabled."]
            return report
        if not self.config.api_key:
            report["blocked_reasons"] = ["Gateway video API key is missing."]
            return report
        if not prompt.strip():
            report["blocked_reasons"] = ["Gateway video prompt is empty."]
            return report

        started = time.monotonic()
        report["executed"] = True
        try:
            task = GatewayVideoClient(
                self.config,
                urlopen_fn=self.urlopen,
            ).submit(
                prompt,
                images=images,
                duration=duration,
                ratio=ratio,
                resolution=resolution,
                generate_audio=generate_audio,
                allow_network=True,
            )
            report.update(
                {
                    "success": True,
                    "billable_submission": True,
                    "response_shape": task.response_shape,
                    "video_url": _safe_url(task.video_url),
                    "task_id": task.task_id,
                    "status_url": (
                        f"{endpoint}/{quote(task.task_id, safe='')}"
                        if task.task_id
                        else ""
                    ),
                    "status_code": task.status_code,
                }
            )
        except GatewayVideoError as exc:
            report["error"] = str(exc)
        finally:
            report["duration_seconds"] = round(time.monotonic() - started, 3)
        return report


def _find_video_url(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("url") or metadata.get("video_url")
        if isinstance(value, str) and value:
            return value
    for key in ("video_url", "result_url", "url"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    output = data.get("output")
    if isinstance(output, dict):
        found = _find_video_url(output)
        if found:
            return found
    nested = data.get("data")
    if isinstance(nested, dict):
        return _find_video_url(nested)
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return _find_video_url(nested[0])
    return ""


def _find_task_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("task_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    output = data.get("output")
    if isinstance(output, dict):
        found = _find_task_id(output)
        if found:
            return found
    nested = data.get("data")
    if isinstance(nested, dict):
        return _find_task_id(nested)
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return _find_task_id(nested[0])
    return ""


def _task_data(data: Any) -> Any:
    current = data
    while isinstance(current, dict):
        if any(
            key in current
            for key in ("status", "task_id", "id", "error", "fail_reason", "reason")
        ):
            break
        nested = None
        for key in ("data", "output"):
            candidate = current.get(key)
            if isinstance(candidate, dict):
                nested = candidate
                break
            if (
                isinstance(candidate, list)
                and candidate
                and isinstance(candidate[0], dict)
            ):
                nested = candidate[0]
                break
        if nested is not None:
            current = nested
            continue
        break
    return current


def _normalize_status(value: Any, *, video_url: str) -> str:
    if video_url:
        return "completed"
    normalized = str(value or "queued").strip().lower()
    if normalized in {"completed", "succeeded", "success", "succeed"}:
        return "completed"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled"}:
        return "failed"
    if normalized in {"processing", "running", "in-progress", "in_progress"}:
        return "in_progress"
    return "queued"


def _task_error_detail(data: Any) -> str:
    data = _task_data(data)
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code and message:
            return f" ({code}): {message}"
        if code:
            return f" ({code})"
        if message:
            return f": {message}"
    if isinstance(error, str) and error.strip():
        return f": {error.strip()}"
    if isinstance(data, dict):
        reason = str(data.get("fail_reason") or data.get("reason") or "").strip()
        if reason:
            return f": {reason}"
    return ""


def _safe_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return ""
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return ""
    if port is not None:
        host = f"{host}:{port}"
    return f"{parts.scheme}://{host}"


def _is_supported_image_bytes(data: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def _supported_image_mime_type(data: bytes) -> str:
    for mime_type in ("image/png", "image/jpeg", "image/webp"):
        if _is_supported_image_bytes(data, mime_type):
            return mime_type
    return ""


def prepare_gateway_video_output_directory(output_path: str | Path) -> None:
    output = Path(output_path)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.parent.is_dir():
            raise OSError("output parent is not a directory")
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.gateway-write-test-",
        ):
            pass
    except OSError as exc:
        raise GatewayVideoError(
            f"Gateway video output directory is not writable: {output.parent}"
        ) from exc


def prepare_gateway_video_output_target(
    output_path: str | Path,
    *,
    overwrite: bool,
) -> None:
    output = Path(output_path)
    if output.exists() and not output.is_file():
        raise GatewayVideoError(
            f"Gateway video output path is not a file: {output}"
        )
    if output.exists() and not overwrite:
        raise GatewayVideoError(
            f"Gateway video output already exists; enable overwrite to replace it: {output}"
        )
    prepare_gateway_video_output_directory(output)


def _is_safe_http_url(value: str, *, allow_query: bool = True) -> bool:
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if not allow_query and (parts.query or parts.fragment):
            return False
        _ = parts.port
    except ValueError:
        return False
    return True


def is_valid_mp4_file(path: str | Path) -> bool:
    source = Path(path)
    try:
        file_size = source.stat().st_size
        if file_size < 24:
            return False
        box_types: list[bytes] = []
        offset = 0
        with source.open("rb") as handle:
            while offset < file_size:
                if file_size - offset < 8:
                    return False
                handle.seek(offset)
                header = handle.read(8)
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                header_size = 8
                if box_size == 1:
                    extended_size = handle.read(8)
                    if len(extended_size) != 8:
                        return False
                    box_size = int.from_bytes(extended_size, "big")
                    header_size = 16
                elif box_size == 0:
                    box_size = file_size - offset
                if box_size < header_size or offset + box_size > file_size:
                    return False
                box_types.append(box_type)
                offset += box_size
        structurally_valid = (
            offset == file_size
            and bool(box_types)
            and box_types[0] == b"ftyp"
            and b"moov" in box_types
            and b"mdat" in box_types
        )
        if not structurally_valid:
            return False
        return probe_media(source, required_stream="video").valid
    except OSError:
        return False


def _http_error(status_code: int, *, operation: str = "request") -> str:
    if status_code == 401:
        return "Gateway video authentication failed."
    if status_code == 403:
        return "Gateway video access was forbidden."
    if status_code == 429:
        return "Gateway video rate limit was exceeded."
    if status_code >= 500:
        return f"Gateway video provider failed with HTTP {status_code}."
    return f"Gateway video {operation} failed with HTTP {status_code}."
