from __future__ import annotations

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
    if isinstance(price_yuan_per_second, bool) or price_yuan_per_second < 0:
        raise GatewayVideoError("Video price per output second cannot be negative.")
    return round(output_seconds * float(price_yuan_per_second), 4)


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
    client.requires_generation_confirmation = True
    return client
