from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.error import HTTPError


MAX_PROVIDER_ERROR_BODY_BYTES = 64 * 1024
MAX_PROVIDER_ERROR_DETAIL_CHARS = 480

_DETAIL_FIELDS = ("code", "type", "message", "detail", "param", "fail_reason")
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_DATA_URI_PATTERN = re.compile(
    r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"""
    (?P<label>
        api[ _-]?key
        | authorization
        | token
        | secret
        | password
        | passwd
        | signature
        | credential
    )
    \s*[:=]\s*
    (?:bearer\s+)?
    [^;\s,}\]]+
    """,
    re.IGNORECASE | re.VERBOSE,
)


def read_provider_http_error_detail(
    error: HTTPError,
    *,
    api_key: str = "",
) -> str:
    """Extract a short diagnostic from a provider JSON error without echoing payloads."""
    try:
        body = error.read(MAX_PROVIDER_ERROR_BODY_BYTES + 1)
    except (OSError, ValueError):
        return ""
    if (
        not isinstance(body, bytes)
        or not body
        or len(body) > MAX_PROVIDER_ERROR_BODY_BYTES
    ):
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""

    parts: list[str] = []
    for source in _detail_sources(payload):
        for field in _DETAIL_FIELDS:
            value = source.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (str, int, float))
            ):
                continue
            text = _sanitize_detail(str(value), api_key=api_key)
            if not text:
                continue
            entry = f"{field}={text}"
            if entry not in parts:
                parts.append(entry)

    detail = "; ".join(parts)
    if len(detail) > MAX_PROVIDER_ERROR_DETAIL_CHARS:
        detail = detail[: MAX_PROVIDER_ERROR_DETAIL_CHARS - 3].rstrip() + "..."
    return detail


def _detail_sources(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    sources: list[Mapping[str, Any]] = [payload]
    for field in ("error", "data", "output"):
        nested = payload.get(field)
        if isinstance(nested, Mapping):
            sources.append(nested)
    return sources


def _sanitize_detail(value: str, *, api_key: str) -> str:
    text = "".join(character if character.isprintable() else " " for character in value)
    text = " ".join(text.split())
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = _DATA_URI_PATTERN.sub("[redacted-image]", text)
    text = _URL_PATTERN.sub("[redacted-url]", text)
    text = _CREDENTIAL_VALUE_PATTERN.sub(
        lambda match: f"{match.group('label')}=[redacted]",
        text,
    )
    return text[:MAX_PROVIDER_ERROR_DETAIL_CHARS].strip()
