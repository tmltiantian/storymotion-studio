from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GatewayTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayTextConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class GatewayTextResult:
    content: str
    model: str
    duration_seconds: float
    usage: dict[str, Any]

    def to_report(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "duration_seconds": round(self.duration_seconds, 3),
            "usage": self.usage,
            "content_length": len(self.content),
        }


class GatewayTextClient:
    def __init__(
        self,
        config: GatewayTextConfig,
        *,
        urlopen_fn: Callable[..., Any] = urlopen,
    ):
        self.config = config
        self.urlopen = urlopen_fn

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        allow_network: bool = False,
    ) -> GatewayTextResult:
        if not allow_network:
            raise GatewayTextError("Gateway text network access must be explicitly enabled.")
        if not self.config.api_key:
            raise GatewayTextError("Gateway text API key is missing.")
        if not messages:
            raise GatewayTextError("Gateway text messages are empty.")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        if response_format:
            payload["response_format"] = response_format
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
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
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                raise GatewayTextError("Gateway text response did not include choices.")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str):
                raise GatewayTextError("Gateway text response did not include message content.")
        except GatewayTextError:
            raise
        except HTTPError as exc:
            raise GatewayTextError(self._http_error(exc)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GatewayTextError(self._sanitize(f"Gateway text request failed: {exc}")) from exc
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GatewayTextError(self._sanitize(f"Invalid gateway text response: {exc}")) from exc

        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        return GatewayTextResult(
            content=content,
            model=str(data.get("model") or self.config.model),
            duration_seconds=time.monotonic() - started,
            usage=usage,
        )

    @staticmethod
    def _http_error(exc: HTTPError) -> str:
        if exc.code == 401:
            return "Gateway text authentication failed."
        if exc.code == 403:
            return "Gateway text access was forbidden."
        if exc.code == 429:
            return "Gateway text rate limit was exceeded."
        if exc.code >= 500:
            return f"Gateway text provider failed with HTTP {exc.code}."
        return f"Gateway text request failed with HTTP {exc.code}."

    def _sanitize(self, message: str) -> str:
        return message.replace(self.config.api_key, "[redacted]") if self.config.api_key else message
