from __future__ import annotations

import hashlib
import posixpath
from urllib.parse import urlsplit

from .gateway_video import GatewayVideoError


def gateway_endpoint_fingerprint(base_url: str) -> str:
    """Hash a normalized endpoint identity without retaining URL secrets."""
    raw_base_url = str(base_url).strip()
    try:
        parsed = urlsplit(raw_base_url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise GatewayVideoError("Gateway video endpoint is invalid.") from exc
    if scheme not in {"http", "https"} or not hostname:
        raise GatewayVideoError(
            "Gateway video endpoint must be an HTTP(S) URL with a host."
        )
    if port is not None and not 1 <= port <= 65535:
        raise GatewayVideoError("Gateway video endpoint port is invalid.")
    normalized_path = posixpath.normpath(f"/{parsed.path.lstrip('/')}")
    if normalized_path == "/.":
        normalized_path = "/"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    port_suffix = "" if port is None or default_port else f":{port}"
    normalized = f"{scheme}://{display_host}{port_suffix}{normalized_path}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
