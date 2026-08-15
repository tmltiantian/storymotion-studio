from __future__ import annotations

import math
from types import MethodType
from typing import Any, Callable

from .gateway_video import GatewayVideoClient, GatewayVideoConfig, GatewayVideoError
from .minimax_h3_video import (
    H3_OUTPUT_PRICE_YUAN_PER_SECOND,
    MiniMaxH3Client,
    MiniMaxH3Config,
)
from .provider_profile import CapabilityConfig


def default_video_resolution(provider: str) -> str:
    return "768P" if provider.strip().lower() == "minimax" else "720p"


def estimate_video_cost_yuan(
    provider: str,
    *,
    resolution: str,
    output_seconds: int,
    price_yuan_per_second: float | None = None,
) -> float:
    if isinstance(output_seconds, bool) or output_seconds <= 0:
        raise GatewayVideoError("Video output seconds must be positive.")
    normalized_provider = provider.strip().lower()
    normalized_resolution = resolution.strip()
    if price_yuan_per_second is None and normalized_provider == "minimax":
        price_yuan_per_second = H3_OUTPUT_PRICE_YUAN_PER_SECOND.get(
            normalized_resolution.upper()
        )
    if price_yuan_per_second is None:
        raise GatewayVideoError("Video price per output second is not configured.")
    if isinstance(price_yuan_per_second, bool):
        raise GatewayVideoError("Video price per output second must be positive.")
    try:
        rate = float(price_yuan_per_second)
    except (TypeError, ValueError) as exc:
        raise GatewayVideoError(
            "Video price per output second must be positive."
        ) from exc
    if not math.isfinite(rate) or rate <= 0:
        raise GatewayVideoError("Video price per output second must be positive.")
    estimate = round(output_seconds * rate, 4)
    if not math.isfinite(estimate) or estimate <= 0:
        raise GatewayVideoError("Video cost estimate must be positive and finite.")
    return estimate


def _confirmation_guard(
    raw_method: Callable[..., Any],
) -> Callable[..., Any]:
    def guarded(client: GatewayVideoClient, *args: Any, **kwargs: Any) -> Any:
        depth = int(getattr(client, "_generation_confirmation_depth", 0))
        if depth:
            return raw_method(*args, **kwargs)
        permits = int(getattr(client, "_generation_confirmation_permits", 0))
        if permits <= 0:
            raise GatewayVideoError(
                "Paid video submission requires a consumed generation confirmation."
            )
        client._generation_confirmation_permits = permits - 1
        client._generation_confirmation_depth = depth + 1
        try:
            return raw_method(*args, **kwargs)
        finally:
            client._generation_confirmation_depth = depth

    return guarded


def _install_confirmation_gate(client: GatewayVideoClient) -> None:
    client._generation_confirmation_permits = 0
    client._generation_confirmation_depth = 0
    for name in ("submit", "submit_prepared", "generate"):
        raw_method = getattr(client, name)
        setattr(client, name, MethodType(_confirmation_guard(raw_method), client))
    client.requires_generation_confirmation = True


def _authorize_confirmed_video_submit(client: GatewayVideoClient) -> None:
    """Grant one fresh-submit entry to a production-built client."""
    if not getattr(client, "requires_generation_confirmation", False):
        return
    client._generation_confirmation_permits = (
        int(getattr(client, "_generation_confirmation_permits", 0)) + 1
    )


def build_video_client(
    capability: CapabilityConfig,
    *,
    model: str = "",
    timeout_seconds: float = 60.0,
    submit_timeout_seconds: float = 300.0,
    download_timeout_seconds: float = 120.0,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: float = 900.0,
) -> GatewayVideoClient:
    provider = capability.provider.strip().lower()
    config_values = {
        "api_key": capability.api_key,
        "base_url": capability.base_url,
        "model": model.strip() or capability.model,
        "timeout_seconds": timeout_seconds,
        "submit_timeout_seconds": submit_timeout_seconds,
        "download_timeout_seconds": download_timeout_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "max_wait_seconds": max_wait_seconds,
    }
    if provider == "minimax":
        client = MiniMaxH3Client(MiniMaxH3Config(**config_values))
    elif provider == "gateway":
        client = GatewayVideoClient(GatewayVideoConfig(**config_values))
    else:
        raise GatewayVideoError(f"Unsupported video provider: {provider or 'empty'}.")
    _install_confirmation_gate(client)
    return client
