from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .dotenv import parse_dotenv
from .media_validation import probe_media, temporary_media_path


class DoubaoTTSError(RuntimeError):
    pass


class DoubaoTTSDefinitiveError(DoubaoTTSError):
    """A provider response proves the request can be retried without ambiguity."""


class DoubaoTTSAPIError(DoubaoTTSDefinitiveError):
    pass


class DoubaoTTSTaskFailedError(DoubaoTTSDefinitiveError):
    pass


class DoubaoTTSOutputError(DoubaoTTSDefinitiveError):
    pass


@dataclass(frozen=True)
class DoubaoTTSConfig:
    api_key: str
    voice_type: str
    source: str
    voice_map: Mapping[str, str] = field(default_factory=dict)
    app_id: str = ""
    access_key: str = ""
    auth_mode: str = "api_key"
    speech_rate: int = 0
    context_text: str = ""
    resource_id: str = "seed-tts-2.0"
    submit_url: str = "https://openspeech.bytedance.com/api/v3/tts/submit"
    query_url: str = "https://openspeech.bytedance.com/api/v3/tts/query"
    stream_url: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


@dataclass(frozen=True)
class DoubaoTTSResult:
    output_path: Path
    metadata_path: Path
    task_id: str
    request_id: str
    sentences: list[dict[str, Any]]
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class DoubaoTTSTask:
    task_id: str
    request_id: str


def _resolve_setting(
    key: str,
    *,
    process_env: Mapping[str, str],
    dotenv_sources: list[tuple[str, Mapping[str, str]]],
) -> tuple[str, str] | None:
    value = process_env.get(key, "").strip()
    if value:
        return value, "process"
    for source, values in dotenv_sources:
        value = values.get(key, "").strip()
        if value:
            return value, source
    return None


def resolve_doubao_tts_config(
    config: dict,
    process_env: Mapping[str, str] | None = None,
) -> DoubaoTTSConfig | None:
    env = process_env if process_env is not None else os.environ
    workspace = Path(config["workspace"])
    openmontage = Path(config["sources"]["openMontage"])
    dotenv_sources = [
        ("factory.env", parse_dotenv(workspace / ".env")),
        ("openmontage.env", parse_dotenv(openmontage / ".env")),
    ]
    api_key = _resolve_setting(
        "DOUBAO_SPEECH_API_KEY",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    voice_type = _resolve_setting(
        "DOUBAO_SPEECH_VOICE_TYPE",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    voice_map_setting = _resolve_setting(
        "DOUBAO_SPEECH_VOICE_MAP",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    speech_rate_setting = _resolve_setting(
        "DOUBAO_TTS_SPEECH_RATE",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    context_setting = _resolve_setting(
        "DOUBAO_TTS_CONTEXT",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    app_id = _resolve_setting(
        "DOUBAO_TTS_APPID",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    access_key = _resolve_setting(
        "DOUBAO_TTS_ACCESS_KEY",
        process_env=env,
        dotenv_sources=dotenv_sources,
    )
    if voice_type is None:
        voice_type = _resolve_setting(
            "DOUBAO_TTS_SPEAKER",
            process_env=env,
            dotenv_sources=dotenv_sources,
        )
    if voice_type is None and app_id is not None and access_key is not None:
        voice_type = ("zh_female_vv_uranus_bigtts", "default")
    has_api_key = api_key is not None and voice_type is not None
    has_legacy_credentials = (
        app_id is not None and access_key is not None and voice_type is not None
    )
    if not has_api_key and not has_legacy_credentials:
        return None

    voice_map: dict[str, str] = {}
    if voice_map_setting is not None:
        try:
            raw_voice_map = json.loads(voice_map_setting[0])
        except json.JSONDecodeError as exc:
            raise ValueError("DOUBAO_SPEECH_VOICE_MAP must be a JSON object.") from exc
        if not isinstance(raw_voice_map, dict):
            raise ValueError("DOUBAO_SPEECH_VOICE_MAP must be a JSON object.")
        for raw_key, raw_value in raw_voice_map.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if not key or not value:
                raise ValueError(
                    "DOUBAO_SPEECH_VOICE_MAP keys and voice IDs must be non-empty."
                )
            voice_map[key] = value

    try:
        speech_rate = int(speech_rate_setting[0]) if speech_rate_setting else 0
    except ValueError as exc:
        raise ValueError("DOUBAO_TTS_SPEECH_RATE must be an integer.") from exc
    if not -50 <= speech_rate <= 100:
        raise ValueError("DOUBAO_TTS_SPEECH_RATE must be between -50 and 100.")

    auth_settings = [api_key] if has_api_key else [app_id, access_key]
    sources = {item[1] for item in auth_settings if item is not None}
    sources.add(voice_type[1])
    if voice_map_setting is not None:
        sources.add(voice_map_setting[1])
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    return DoubaoTTSConfig(
        api_key=api_key[0] if api_key is not None else "",
        voice_type=voice_type[0],
        source=source,
        voice_map=voice_map,
        app_id=app_id[0] if app_id is not None else "",
        access_key=access_key[0] if access_key is not None else "",
        auth_mode="api_key" if has_api_key else "app_access",
        speech_rate=speech_rate,
        context_text=context_setting[0] if context_setting else "",
    )


class DoubaoTTSClient:
    def __init__(
        self,
        config: DoubaoTTSConfig,
        *,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.config = config
        self.session = session
        self.sleep = sleep
        self.monotonic = monotonic

    @property
    def supports_async_tasks(self) -> bool:
        return self.config.auth_mode == "api_key"

    def synthesize(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice_id: str | None = None,
        metadata_path: str | Path | None = None,
        speech_rate: int = 0,
        sample_rate: int = 24000,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: int = 300,
    ) -> DoubaoTTSResult:
        if self.config.auth_mode == "app_access":
            return self._synthesize_streaming(
                text,
                output_path,
                voice_id=voice_id or self.config.voice_type,
                metadata_path=metadata_path,
                speech_rate=speech_rate,
                sample_rate=sample_rate,
            )
        task = self.submit(
            text,
            voice_id=voice_id,
            speech_rate=speech_rate,
            sample_rate=sample_rate,
        )
        return self.complete_task(
            task,
            output_path,
            metadata_path=metadata_path,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )

    def submit(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        request_id: str | None = None,
        speech_rate: int = 0,
        sample_rate: int = 24000,
    ) -> DoubaoTTSTask:
        if not self.supports_async_tasks:
            raise DoubaoTTSAPIError(
                "Legacy Doubao AppID/AccessKey authentication uses synchronous streaming."
            )
        request_id = request_id or str(uuid.uuid4())
        submit_response = self.session.post(
            self.config.submit_url,
            headers=self._headers(request_id),
            json=self._submit_body(
                text=text,
                voice_id=voice_id or self.config.voice_type,
                request_id=request_id,
                speech_rate=speech_rate,
                sample_rate=sample_rate,
            ),
            timeout=(10, 60),
        )
        submit_data = self._response_json(submit_response)
        self._raise_for_error(submit_response.status_code, submit_data)
        task_id = submit_data.get("data", {}).get("task_id")
        if not task_id:
            raise RuntimeError("Doubao submit succeeded without data.task_id")
        return DoubaoTTSTask(task_id=str(task_id), request_id=request_id)

    def complete_task(
        self,
        task: DoubaoTTSTask,
        output_path: str | Path,
        *,
        metadata_path: str | Path | None = None,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: int = 300,
    ) -> DoubaoTTSResult:
        output = Path(output_path)
        metadata = (
            Path(metadata_path)
            if metadata_path is not None
            else output.with_suffix(output.suffix + ".json")
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        metadata.parent.mkdir(parents=True, exist_ok=True)

        deadline = self.monotonic() + timeout_seconds
        query_data: dict[str, Any] | None = None
        while self.monotonic() < deadline:
            self.sleep(poll_interval_seconds)
            query_response = self.session.post(
                self.config.query_url,
                headers=self._headers(str(uuid.uuid4())),
                json={"task_id": task.task_id},
                timeout=(10, 60),
            )
            query_data = self._response_json(query_response)
            self._raise_for_error(query_response.status_code, query_data)
            task_status = query_data.get("data", {}).get("task_status")
            if task_status == 2:
                break
            if task_status == 3:
                raise DoubaoTTSTaskFailedError(
                    "Doubao task failed: "
                    f"{self._redact(str(query_data.get('message', 'unknown error')))}"
                )
        else:
            raise TimeoutError(f"Doubao task did not finish within {timeout_seconds} seconds")

        data = (query_data or {}).get("data", {})
        audio_url = data.get("audio_url")
        if not audio_url:
            raise RuntimeError("Doubao task completed without data.audio_url")
        audio_response = self.session.get(audio_url, timeout=(10, 120))
        audio_response.raise_for_status()
        temporary_output = temporary_media_path(output)
        temporary_metadata = temporary_media_path(metadata)
        try:
            temporary_output.write_bytes(audio_response.content)
            audio_probe = probe_media(temporary_output, required_stream="audio")
            if not audio_probe.valid:
                raise DoubaoTTSOutputError(
                    "Doubao TTS download did not contain valid audio: "
                    f"{audio_probe.error}"
                )
            temporary_metadata.write_text(
                json.dumps(query_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_metadata.chmod(0o600)
            temporary_output.replace(output)
            temporary_metadata.replace(metadata)
        except Exception:
            temporary_output.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)
            raise
        return DoubaoTTSResult(
            output_path=output,
            metadata_path=metadata,
            task_id=task.task_id,
            request_id=task.request_id,
            sentences=data.get("sentences", []),
            usage=data.get("usage"),
        )

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "X-Api-Key": self.config.api_key,
            "X-Api-Resource-Id": self.config.resource_id,
            "X-Api-Request-Id": request_id,
            "X-Control-Require-Usage-Tokens-Return": "true",
            "Content-Type": "application/json",
        }

    def _synthesize_streaming(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice_id: str,
        metadata_path: str | Path | None,
        speech_rate: int,
        sample_rate: int,
    ) -> DoubaoTTSResult:
        request_id = str(uuid.uuid4())
        response = self.session.post(
            self.config.stream_url,
            headers={
                "X-Api-App-Id": self.config.app_id,
                "X-Api-Access-Key": self.config.access_key,
                "X-Api-Resource-Id": self.config.resource_id,
                "X-Api-Request-Id": request_id,
                "Content-Type": "application/json",
            },
            json=self._submit_body(
                text=text,
                voice_id=voice_id,
                request_id=request_id,
                speech_rate=speech_rate,
                sample_rate=sample_rate,
            ),
            timeout=(10, 120),
        )
        if response.status_code >= 400:
            raise DoubaoTTSAPIError(
                f"Doubao streaming API error: HTTP {response.status_code}: "
                f"{self._redact(getattr(response, 'text', '')[:300])}"
            )

        chunks: list[bytes] = []
        response_items: list[dict[str, Any]] = []
        for raw_line in getattr(response, "text", "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            response_items.append(
                {key: value for key, value in item.items() if key != "data"}
            )
            code = item.get("code")
            if code == 0 and item.get("data"):
                try:
                    chunks.append(base64.b64decode(item["data"], validate=True))
                except (ValueError, TypeError) as exc:
                    raise DoubaoTTSOutputError(
                        "Doubao streaming response contained invalid audio data."
                    ) from exc
            elif code == 20000000:
                break
            elif code not in (0, None):
                raise DoubaoTTSAPIError(
                    f"Doubao streaming API error: code {code}: "
                    f"{self._redact(str(item.get('message', 'unknown error')))}"
                )
        if not chunks:
            raise DoubaoTTSOutputError(
                "Doubao streaming API completed without audio data."
            )

        output = Path(output_path)
        metadata = (
            Path(metadata_path)
            if metadata_path is not None
            else output.with_suffix(output.suffix + ".json")
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        metadata.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = temporary_media_path(output)
        temporary_metadata = temporary_media_path(metadata)
        try:
            temporary_output.write_bytes(b"".join(chunks))
            audio_probe = probe_media(temporary_output, required_stream="audio")
            if not audio_probe.valid:
                raise DoubaoTTSOutputError(
                    "Doubao streaming response did not contain valid audio: "
                    f"{audio_probe.error}"
                )
            temporary_metadata.write_text(
                json.dumps(
                    {
                        "schema_version": "motion-comic-factory.doubao-streaming.v1",
                        "request_id": request_id,
                        "resource_id": self.config.resource_id,
                        "voice_id": voice_id,
                        "speech_rate": speech_rate,
                        "sample_rate": sample_rate,
                        "response": response_items,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary_metadata.chmod(0o600)
            temporary_output.replace(output)
            temporary_metadata.replace(metadata)
        except Exception:
            temporary_output.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)
            raise
        return DoubaoTTSResult(
            output_path=output,
            metadata_path=metadata,
            task_id=request_id,
            request_id=request_id,
            sentences=[],
            usage=None,
        )

    def _submit_body(
        self,
        *,
        text: str,
        voice_id: str,
        request_id: str,
        speech_rate: int,
        sample_rate: int,
    ) -> dict[str, Any]:
        return {
            "user": {"uid": "motion_comic_factory"},
            "unique_id": request_id,
            "req_params": {
                "text": text,
                "speaker": voice_id,
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": sample_rate,
                    "speech_rate": speech_rate,
                    "enable_timestamp": True,
                },
                "additions": json.dumps(
                    {
                        "disable_markdown_filter": False,
                        **(
                            {"context_texts": [self.config.context_text]}
                            if self.config.context_text
                            else {}
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        }

    @staticmethod
    def _response_json(response: Any) -> dict[str, Any]:
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Non-JSON response from Doubao API: HTTP {response.status_code}"
            ) from exc

    def _raise_for_error(self, http_status: int, payload: dict[str, Any]) -> None:
        if http_status < 400 and payload.get("code") == 20000000:
            return
        raise DoubaoTTSAPIError(
            f"Doubao API error: HTTP {http_status}, code {payload.get('code')}: "
            f"{self._redact(str(payload.get('message', 'unknown error')))}"
        )

    def _redact(self, message: str) -> str:
        for secret in (
            self.config.api_key,
            self.config.app_id,
            self.config.access_key,
        ):
            if secret:
                message = message.replace(secret, "[redacted]")
        return message
