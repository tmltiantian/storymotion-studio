from __future__ import annotations

import re
from typing import Any


DEFAULT_MEDIA_TYPE = "application/octet-stream"
_MIME = re.compile(r"[a-z0-9!#$&^_.+-]{1,32}/[a-z0-9!#$&^_.+-]{1,64}")
_SAFE_MEDIA_TYPES = frozenset(
    {
        DEFAULT_MEDIA_TYPE,
        "application/json",
        "application/pdf",
        "application/zip",
        "application/x-subrip",
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mp4a-latm",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
        "text/markdown",
        "text/plain",
        "text/vtt",
        "video/mp4",
        "video/quicktime",
        "video/webm",
    }
)


def validate_media_type(value: Any) -> str:
    text = str(value)
    lowered = text.lower()
    if (
        text != text.strip()
        or len(text) > 97
        or _MIME.fullmatch(lowered) is None
        or lowered not in _SAFE_MEDIA_TYPES
    ):
        raise ValueError("media type is invalid")
    return lowered


def safe_media_type(value: Any) -> str:
    try:
        return validate_media_type(value)
    except (TypeError, ValueError):
        return DEFAULT_MEDIA_TYPE
