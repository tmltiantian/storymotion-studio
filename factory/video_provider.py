from __future__ import annotations

import asyncio
import math
import threading
import weakref
from contextvars import ContextVar
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


_ACTIVE_SUBMISSIONS: ContextVar[
    tuple[tuple[object, object, int, object | None], ...]
] = ContextVar(
    "video_active_submissions",
    default=(),
)
_GATE_LOCK = threading.Lock()
_GATE_KEYS: weakref.WeakKeyDictionary[GatewayVideoClient, object] = (
    weakref.WeakKeyDictionary()
)


class _GenerationSubmitPermit:
    __slots__ = ("_client", "_gate_key", "_lock", "_used")

    def __init__(self, client: GatewayVideoClient, gate_key: object):
        self._client = weakref.ref(client)
        self._gate_key = gate_key
        self._lock = threading.Lock()
        self._used = False

    def consume(self, client: GatewayVideoClient, gate_key: object) -> bool:
        with self._lock:
            if (
                self._used
                or self._client() is not client
                or self._gate_key is not gate_key
            ):
                return False
            self._used = True
            return True


def _execution_identity() -> tuple[int, object | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def _confirmation_guard(
    raw_method: Callable[..., Any],
    gate_key: object,
) -> Callable[..., Any]:
    def guarded(client: GatewayVideoClient, *args: Any, **kwargs: Any) -> Any:
        permit = kwargs.pop("_generation_permit", None)
        active = _ACTIVE_SUBMISSIONS.get()
        thread_id, task = _execution_identity()
        if any(
            active_client is client
            and active_key is gate_key
            and active_thread == thread_id
            and active_task is task
            for active_client, active_key, active_thread, active_task in active
        ):
            if permit is not None:
                raise GatewayVideoError("Generation confirmation permit is invalid.")
            return raw_method(*args, **kwargs)
        if not isinstance(permit, _GenerationSubmitPermit) or not permit.consume(
            client,
            gate_key,
        ):
            raise GatewayVideoError(
                "Paid video submission requires a consumed generation confirmation."
            )
        context_token = _ACTIVE_SUBMISSIONS.set(
            (*active, (client, gate_key, thread_id, task))
        )
        try:
            return raw_method(*args, **kwargs)
        finally:
            _ACTIVE_SUBMISSIONS.reset(context_token)

    return guarded


def _install_confirmation_gate(client: GatewayVideoClient) -> None:
    gate_key = object()
    with _GATE_LOCK:
        _GATE_KEYS[client] = gate_key
    for name in ("submit", "submit_prepared", "generate"):
        raw_method = getattr(client, name)
        setattr(
            client,
            name,
            MethodType(_confirmation_guard(raw_method, gate_key), client),
        )
    client.requires_generation_confirmation = True


def _authorize_confirmed_video_submit(
    client: GatewayVideoClient,
) -> _GenerationSubmitPermit | None:
    """Create one execution-bound permit for a production-built client."""
    with _GATE_LOCK:
        gate_key = _GATE_KEYS.get(client)
    if gate_key is None:
        return None
    return _GenerationSubmitPermit(client, gate_key)


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
