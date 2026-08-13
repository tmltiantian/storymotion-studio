from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal


StreamType = Literal["video", "audio"]


@dataclass(frozen=True)
class MediaProbeResult:
    path: Path
    valid: bool
    duration_seconds: float
    video_stream_count: int
    audio_stream_count: int
    error: str = ""


class MediaValidationError(RuntimeError):
    pass


def temporary_media_path(output_path: str | Path) -> Path:
    output = Path(output_path)
    suffix = output.suffix or ".bin"
    return output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp{suffix}")


def probe_media(
    path: str | Path,
    *,
    required_stream: StreamType | None = None,
    ffprobe_bin: str = "ffprobe",
    command_runner: Callable[..., Any] = subprocess.run,
    timeout_seconds: float = 30.0,
) -> MediaProbeResult:
    source = Path(path)
    if not source.is_file():
        return MediaProbeResult(source, False, 0.0, 0, 0, "media file is missing")
    try:
        if source.stat().st_size <= 0:
            return MediaProbeResult(source, False, 0.0, 0, 0, "media file is empty")
    except OSError as exc:
        return MediaProbeResult(source, False, 0.0, 0, 0, str(exc))

    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,duration",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = command_runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
        payload = json.loads(completed.stdout or "{}")
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        return MediaProbeResult(
            source,
            False,
            0.0,
            0,
            0,
            f"ffprobe failed: {detail}",
        )
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError) as exc:
        return MediaProbeResult(source, False, 0.0, 0, 0, f"ffprobe failed: {exc}")

    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video_count = sum(
        isinstance(stream, dict) and stream.get("codec_type") == "video"
        for stream in streams
    )
    audio_count = sum(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    )
    raw_duration = (
        (payload.get("format") or {}).get("duration")
        if isinstance(payload, dict) and isinstance(payload.get("format"), dict)
        else 0
    )
    try:
        duration = max(0.0, float(raw_duration or 0))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0

    if required_stream == "video" and video_count == 0:
        error = "media does not contain a video stream"
    elif required_stream == "audio" and audio_count == 0:
        error = "media does not contain an audio stream"
    elif video_count == 0 and audio_count == 0:
        error = "media does not contain audio or video streams"
    elif duration <= 0:
        error = "media duration is not positive"
    else:
        error = ""
    return MediaProbeResult(
        path=source,
        valid=not error,
        duration_seconds=duration,
        video_stream_count=video_count,
        audio_stream_count=audio_count,
        error=error,
    )


def require_media(
    path: str | Path,
    *,
    required_stream: StreamType,
    ffprobe_bin: str = "ffprobe",
) -> MediaProbeResult:
    result = probe_media(
        path,
        required_stream=required_stream,
        ffprobe_bin=ffprobe_bin,
    )
    if not result.valid:
        raise MediaValidationError(f"Invalid {required_stream} media: {result.error}")
    return result
