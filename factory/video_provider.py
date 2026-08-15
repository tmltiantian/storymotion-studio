from __future__ import annotations

from .gateway_video import GatewayVideoClient, GatewayVideoConfig, GatewayVideoError
from .minimax_h3_video import MiniMaxH3Client, MiniMaxH3Config
from .provider_profile import CapabilityConfig


def default_video_resolution(provider: str) -> str:
    return "768P" if provider.strip().lower() == "minimax" else "720p"


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
        return MiniMaxH3Client(MiniMaxH3Config(**config_values))
    if provider == "gateway":
        return GatewayVideoClient(GatewayVideoConfig(**config_values))
    raise GatewayVideoError(f"Unsupported video provider: {provider or 'empty'}.")
