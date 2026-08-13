from __future__ import annotations

import base64
import json
import os
import re
import stat
import time
from http.client import IncompleteRead
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .character_assets import (
    JPEG_SIGNATURE,
    PNG_SIGNATURE,
    RIFF_SIGNATURE,
    WEBP_SIGNATURE,
)
from .provider_http_error import read_provider_http_error_detail


MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024


class GatewayImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayImageConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 120.0
    download_timeout_seconds: float = 60.0
    download_attempts: int = 3
    download_retry_delay_seconds: float = 0.5


@dataclass(frozen=True)
class GatewayImageResult:
    output_path: str
    model: str
    size: str
    duration_seconds: float
    response_format: str

    def to_report(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "model": self.model,
            "size": self.size,
            "duration_seconds": round(self.duration_seconds, 3),
            "response_format": self.response_format,
        }


def normalize_image_size(size: str) -> str:
    normalized = size.strip().lower().replace("*", "x")
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", normalized)
    if not match:
        raise ValueError(f"Invalid image size: {size}")
    return f"{match.group(1)}x{match.group(2)}"


def is_valid_image_file(path: str | Path) -> bool:
    """Return whether a local file has a recognized PNG, JPEG, or WebP signature."""
    try:
        data = Path(path).read_bytes()[:12]
    except OSError:
        return False
    return is_valid_image_bytes(data)


def is_valid_image_bytes(data: bytes) -> bool:
    """Return whether bytes begin with a recognized PNG, JPEG, or WebP signature."""
    return (
        data.startswith(PNG_SIGNATURE)
        or data.startswith(JPEG_SIGNATURE)
        or (data.startswith(RIFF_SIGNATURE) and data[8:12] == WEBP_SIGNATURE)
    )


class GatewayImageClient:
    def __init__(
        self,
        config: GatewayImageConfig,
        *,
        urlopen_fn: Callable[..., Any] = urlopen,
    ):
        self.config = config
        self.urlopen = urlopen_fn

    def generate(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        size: str = "1024x1024",
        n: int = 1,
        ref_image_path: str | Path | None = None,
        ref_image_paths: list[str | Path] | None = None,
        output_file_descriptor: int | None = None,
    ) -> GatewayImageResult:
        if ref_image_path is not None and ref_image_paths is not None:
            raise GatewayImageError(
                "Reference image inputs are mutually exclusive."
            )
        references: list[str | Path] | None = None
        if ref_image_path is not None:
            references = [ref_image_path]
        elif ref_image_paths is not None:
            references = list(ref_image_paths)
        if references is not None and not 1 <= len(references) <= 10:
            raise GatewayImageError("Reference image count must be between 1 and 10.")
        if references and not self.config.model.strip().lower().startswith(
            "doubao-seedream-"
        ):
            raise GatewayImageError(
                "The selected gateway image model does not support reference images."
            )
        if not self.config.api_key:
            raise GatewayImageError("Gateway image API key is missing.")
        if not prompt.strip():
            raise GatewayImageError("Image prompt is empty.")
        if n < 1 or n > 10:
            raise GatewayImageError("Image count must be between 1 and 10.")

        normalized_size = normalize_image_size(size)
        endpoint = f"{self.config.base_url.rstrip('/')}/images/generations"
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "size": normalized_size,
            "n": n,
            "response_format": "url",
            "sequential_image_generation": "disabled",
        }
        if references:
            encoded_references = [
                self._reference_data_uri(value, index=index)
                for index, value in enumerate(references, start=1)
            ]
            payload["image"] = (
                encoded_references[0]
                if len(encoded_references) == 1
                else encoded_references
            )
        if self.config.model.strip().lower().startswith("doubao-seedream-"):
            payload["watermark"] = False
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        started = time.monotonic()
        try:
            with self.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read()
            data = json.loads(body.decode("utf-8"))
            item = self._first_image(data)
            output = Path(output_path)
            if output_file_descriptor is None:
                output.parent.mkdir(parents=True, exist_ok=True)
            if item.get("url"):
                self._download(
                    str(item["url"]),
                    output,
                    output_file_descriptor=output_file_descriptor,
                )
                response_format = "url"
            elif item.get("b64_json"):
                self._write_output(
                    output,
                    base64.b64decode(item["b64_json"], validate=True),
                    output_file_descriptor=output_file_descriptor,
                )
                response_format = "b64_json"
            else:
                raise GatewayImageError(
                    "Gateway image response did not include an image URL or b64_json."
                )
        except GatewayImageError:
            raise
        except HTTPError as exc:
            message = self._http_error(exc)
            detail = read_provider_http_error_detail(
                exc,
                api_key=self.config.api_key,
            )
            if detail:
                message = f"{message} Provider detail: {detail}."
            raise GatewayImageError(message) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayImageError(
                self._sanitize(f"Gateway image request failed: {exc}")
            ) from exc
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            raise GatewayImageError(
                self._sanitize(f"Invalid gateway image response: {exc}")
            ) from exc

        return GatewayImageResult(
            output_path=str(output),
            model=self.config.model,
            size=normalized_size,
            duration_seconds=time.monotonic() - started,
            response_format=response_format,
        )

    @staticmethod
    def _reference_data_uri(value: str | Path, *, index: int) -> str:
        path = Path(value).expanduser()
        if path.is_symlink() or any(parent.is_symlink() for parent in path.absolute().parents):
            raise GatewayImageError(
                f"Reference image {index} must be a readable regular file."
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise GatewayImageError(
                f"Reference image {index} must be a readable regular file."
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise GatewayImageError(
                    f"Reference image {index} must be a readable regular file."
                )
            if metadata.st_size == 0:
                raise GatewayImageError(f"Reference image {index} is empty.")
            if metadata.st_size > MAX_REFERENCE_IMAGE_BYTES:
                raise GatewayImageError(
                    f"Reference image {index} exceeds the 20 MB size limit."
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read(MAX_REFERENCE_IMAGE_BYTES + 1)
        except GatewayImageError:
            raise
        except OSError as exc:
            raise GatewayImageError(
                f"Reference image {index} could not be read."
            ) from exc
        finally:
            os.close(descriptor)
        if len(content) != metadata.st_size:
            raise GatewayImageError(
                f"Reference image {index} changed while it was being read."
            )
        if content.startswith(PNG_SIGNATURE):
            mime_type = "image/png"
        elif content.startswith(JPEG_SIGNATURE):
            mime_type = "image/jpeg"
        elif content.startswith(RIFF_SIGNATURE) and content[8:12] == WEBP_SIGNATURE:
            mime_type = "image/webp"
        else:
            raise GatewayImageError(
                f"Reference image {index} has an unsupported image signature."
            )
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _first_image(data: Any) -> dict[str, Any]:
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise GatewayImageError(
                "Gateway image response did not include a data item."
            )
        return items[0]

    def _download(
        self, url: str, output: Path, *, output_file_descriptor: int | None = None
    ) -> None:
        request = Request(url, method="GET")
        if self.config.download_attempts < 1:
            raise GatewayImageError("Gateway image download attempts must be positive.")
        body: bytes | None = None
        for attempt in range(1, self.config.download_attempts + 1):
            try:
                with self.urlopen(
                    request, timeout=self.config.download_timeout_seconds
                ) as response:
                    body = response.read()
                break
            except HTTPError:
                raise
            except (IncompleteRead, TimeoutError, URLError, OSError) as exc:
                if attempt == self.config.download_attempts:
                    raise GatewayImageError(
                        "Gateway image download failed after "
                        f"{self.config.download_attempts} attempts "
                        f"({type(exc).__name__})."
                    ) from exc
                time.sleep(self.config.download_retry_delay_seconds * attempt)
        if body is None:
            raise GatewayImageError("Gateway image download did not complete.")
        if not body:
            raise GatewayImageError("Gateway image download returned an empty body.")
        self._write_output(output, body, output_file_descriptor=output_file_descriptor)

    @staticmethod
    def _write_output(
        output: Path, body: bytes, *, output_file_descriptor: int | None
    ) -> None:
        if output_file_descriptor is None:
            output.write_bytes(body)
            return
        os.lseek(output_file_descriptor, 0, os.SEEK_SET)
        os.ftruncate(output_file_descriptor, 0)
        offset = 0
        while offset < len(body):
            offset += os.write(output_file_descriptor, body[offset:])

    def _http_error(self, exc: HTTPError) -> str:
        labels = {
            401: "Gateway image authentication failed.",
            403: "Gateway image access was forbidden.",
            429: "Gateway image rate limit was exceeded.",
        }
        if exc.code in labels:
            return labels[exc.code]
        if exc.code >= 500:
            return f"Gateway image provider failed with HTTP {exc.code}."
        return f"Gateway image request failed with HTTP {exc.code}."

    def _sanitize(self, message: str) -> str:
        if self.config.api_key:
            return message.replace(self.config.api_key, "[redacted]")
        return message
