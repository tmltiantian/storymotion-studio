from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .dotenv import parse_dotenv


DEFAULT_MINIMAX_API_BASE = "https://api.minimaxi.com"


def _parse_dotenv(path: Path | None) -> dict[str, str]:
    return parse_dotenv(path) if path is not None else {}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _api_base(root_or_api_base: str) -> str:
    value = root_or_api_base.strip().rstrip("/")
    if not value:
        return ""
    return value if value.endswith("/v1") else f"{value}/v1"


def _same_origin(left: str, right: str) -> bool:
    left_url = urlparse(left)
    right_url = urlparse(right)
    if not left_url.scheme or not left_url.netloc or not right_url.scheme or not right_url.netloc:
        return False
    return (left_url.scheme, left_url.netloc) == (right_url.scheme, right_url.netloc)


@dataclass(frozen=True)
class CapabilityConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    key_name: str
    key_source: str | None
    ready: bool
    blockers: tuple[str, ...] = ()
    enabled: bool = True
    supports_reference_images: bool | None = None
    voice: str = ""

    def to_report(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "key_name": self.key_name,
            "key_present": bool(self.api_key),
            "key_source": self.key_source,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "enabled": self.enabled,
        }
        if self.supports_reference_images is not None:
            report["supports_reference_images"] = self.supports_reference_images
        if self.voice:
            report["voice_configured"] = True
        return report


@dataclass(frozen=True)
class ProviderProfile:
    text: CapabilityConfig
    image: CapabilityConfig
    video: CapabilityConfig
    audio: CapabilityConfig
    source_paths: dict[str, str]

    def to_report(self) -> dict[str, Any]:
        capabilities = {
            "text": self.text.to_report(),
            "image": self.image.to_report(),
            "video": self.video.to_report(),
            "audio": self.audio.to_report(),
        }
        return {
            "schema_version": "motion-comic-factory.provider-profile.v1",
            "sources": self.source_paths,
            "capabilities": capabilities,
            "stages": {
                "assets": _stage_report(self.image),
                "storyboard": _stage_report(
                    self.image,
                    degraded=self.image.provider == "gateway",
                    limitations=("Gateway image generation does not accept role references.",)
                    if self.image.provider == "gateway"
                    else (),
                ),
                "audio": _stage_report(self.audio),
                "video": _stage_report(self.video),
            },
        }


def _stage_report(
    capability: CapabilityConfig,
    *,
    degraded: bool = False,
    limitations: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "provider": capability.provider,
        "ready": capability.ready,
        "degraded": degraded,
        "blockers": list(capability.blockers),
        "limitations": list(limitations),
    }


class _Values:
    def __init__(
        self,
        process_env: Mapping[str, str],
        factory: Mapping[str, str],
        openmontage: Mapping[str, str],
    ):
        self.sources = (
            ("process", process_env),
            ("factory.env", factory),
            ("openmontage.env", openmontage),
        )

    def get(self, key: str, default: str = "") -> tuple[str, str | None]:
        for source_name, values in self.sources:
            value = values.get(key)
            if value:
                return value, source_name
        return default, None

    def first(self, keys: tuple[str, ...]) -> tuple[str, str, str | None]:
        for key in keys:
            value, source = self.get(key)
            if value:
                return value, key, source
        return "", keys[0], None


def _paths(config: dict[str, Any]) -> tuple[Path | None, Path | None]:
    workspace = config.get("workspace")
    sources = config.get("sources", {})
    factory_path = Path(workspace) / ".env" if workspace else None
    openmontage = sources.get("openMontage")
    openmontage_path = Path(openmontage) / ".env" if openmontage else None
    return factory_path, openmontage_path


def _gateway_key(values: _Values, gateway_api_base: str, openai_base: str) -> tuple[str, str, str | None]:
    key, key_name, source = values.first(("GATEWAY_API_KEY", "NEW_API_KEY"))
    if key:
        return key, key_name, source
    if _same_origin(openai_base, gateway_api_base):
        alias_key, alias_name, alias_source = values.first(("OPENAI_API_KEY",))
        if alias_key:
            return alias_key, alias_name, alias_source
    return "", "GATEWAY_API_KEY", None


def resolve_provider_profile(
    config: dict[str, Any],
    process_env: Mapping[str, str] | None = None,
) -> ProviderProfile:
    env = process_env if process_env is not None else os.environ
    factory_path, openmontage_path = _paths(config)
    factory_values = _parse_dotenv(factory_path)
    openmontage_values = _parse_dotenv(openmontage_path)
    values = _Values(
        env,
        factory_values,
        {},
    )
    tts_values = _Values(
        env,
        factory_values,
        openmontage_values,
    )

    gateway_root, _ = values.get("GATEWAY_BASE_URL")
    gateway_api_base = _api_base(gateway_root)
    configured_openai_base, _ = values.get("OPENAI_BASE_URL", gateway_api_base)
    gateway_key, gateway_key_name, gateway_key_source = _gateway_key(
        values,
        gateway_api_base,
        configured_openai_base,
    )
    dashscope_key, dashscope_source = values.get("DASHSCOPE_API_KEY")

    llm_provider, _ = values.get("LLM_PROVIDER", "dashscope")
    llm_provider = llm_provider.lower()
    if llm_provider in {"gateway", "openai"}:
        openai_is_gateway = _same_origin(configured_openai_base, gateway_api_base)
        text_provider = "gateway" if llm_provider == "gateway" or openai_is_gateway else "openai"
        if text_provider == "gateway":
            text_key = gateway_key
            text_key_name = gateway_key_name
            text_key_source = gateway_key_source
            text_base = configured_openai_base if openai_is_gateway else gateway_api_base
            text_model, _ = values.get("OPENAI_MODEL")
            if not text_model:
                text_model, _ = values.get("GATEWAY_TEXT_MODEL", "qwen3.6-plus")
        else:
            text_key, text_key_source = values.get("OPENAI_API_KEY")
            text_key_name = "OPENAI_API_KEY"
            text_base = configured_openai_base
            text_model, _ = values.get("OPENAI_MODEL", "gpt-4o")
    elif llm_provider == "dashscope":
        text_provider = "dashscope"
        text_key = dashscope_key
        text_key_name = "DASHSCOPE_API_KEY"
        text_key_source = dashscope_source
        text_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        text_model, _ = values.get("DASHSCOPE_TEXT_MODEL", "qwen3.7-plus")
        text_blockers = () if text_key else (f"{text_key_name} is missing.",)
    else:
        text_provider = llm_provider
        text_key = ""
        text_key_name = ""
        text_key_source = None
        text_base = ""
        text_model = ""
        text_blockers = (f"Unsupported LLM_PROVIDER: {llm_provider}.",)
    if llm_provider in {"gateway", "openai"}:
        text_blocker_values: list[str] = []
        if not text_key:
            text_blocker_values.append(f"{text_key_name} is missing.")
        if text_provider == "gateway" and not gateway_api_base:
            text_blocker_values.append("GATEWAY_BASE_URL is missing.")
        text_blockers = tuple(text_blocker_values)
    text = CapabilityConfig(
        provider=text_provider,
        model=text_model,
        base_url=text_base.rstrip("/"),
        api_key=text_key,
        key_name=text_key_name,
        key_source=text_key_source,
        ready=not text_blockers,
        blockers=text_blockers,
    )

    image_provider, _ = values.get("IMAGE_PROVIDER", "dashscope")
    image_provider = image_provider.lower()
    if image_provider == "gateway":
        image_key, image_key_name, image_key_source = gateway_key, gateway_key_name, gateway_key_source
        image_model, _ = values.get("GATEWAY_IMAGE_MODEL", "qwen-image-2.0")
        image_base = gateway_api_base
        supports_refs = False
        image_blocker_values: list[str] = []
        if not image_key:
            image_blocker_values.append(f"{image_key_name} is missing.")
        if not gateway_api_base:
            image_blocker_values.append("GATEWAY_BASE_URL is missing.")
        image_ready = not image_blocker_values
        image_blockers = tuple(image_blocker_values)
    elif image_provider == "local":
        image_key, image_key_name, image_key_source = "", "", None
        image_model, image_base, supports_refs = "static-role-preview", "", True
        image_ready = True
        image_blockers = ()
    elif image_provider == "dashscope":
        image_key, image_key_name, image_key_source = dashscope_key, "DASHSCOPE_API_KEY", dashscope_source
        image_model, _ = values.get("DASHSCOPE_IMAGE_MODEL", "wan2.7-image-pro")
        image_base, supports_refs = "https://dashscope.aliyuncs.com", True
        image_ready = bool(image_key)
        image_blockers = () if image_ready else (f"{image_key_name} is missing.",)
    else:
        image_key, image_key_name, image_key_source = "", "", None
        image_model, image_base, supports_refs = "", "", None
        image_ready = False
        image_blockers = (f"Unsupported IMAGE_PROVIDER: {image_provider}.",)
    image = CapabilityConfig(
        provider=image_provider,
        model=image_model,
        base_url=image_base,
        api_key=image_key,
        key_name=image_key_name,
        key_source=image_key_source,
        ready=image_ready,
        blockers=image_blockers,
        supports_reference_images=supports_refs,
    )

    video_provider, _ = values.get("VIDEO_PROVIDER", "dashscope")
    video_provider = video_provider.lower()
    if video_provider == "gateway":
        video_enabled_value, _ = values.get("ENABLE_GATEWAY_VIDEO", "0")
        video_enabled = _truthy(video_enabled_value)
        video_model, _ = values.get("GATEWAY_VIDEO_MODEL", "doubao-seedance-2-0-fast")
        video_blockers: list[str] = []
        if not gateway_key:
            video_blockers.append(f"{gateway_key_name} is missing.")
        if not gateway_api_base:
            video_blockers.append("GATEWAY_BASE_URL is missing.")
        if not video_enabled:
            video_blockers.append("Gateway video is disabled; set ENABLE_GATEWAY_VIDEO=1.")
        video = CapabilityConfig(
            provider="gateway",
            model=video_model,
            base_url=gateway_api_base,
            api_key=gateway_key,
            key_name=gateway_key_name,
            key_source=gateway_key_source,
            ready=not video_blockers,
            blockers=tuple(video_blockers),
            enabled=video_enabled,
            supports_reference_images=True,
        )
    elif video_provider == "minimax":
        minimax_key, minimax_key_source = values.get("MINIMAX_API_KEY")
        minimax_base, _ = values.get(
            "MINIMAX_API_BASE",
            DEFAULT_MINIMAX_API_BASE,
        )
        minimax_model, _ = values.get("MINIMAX_VIDEO_MODEL", "MiniMax-H3")
        video_enabled_value, _ = values.get("ENABLE_MINIMAX_VIDEO", "0")
        video_enabled = _truthy(video_enabled_value)
        video_blockers = []
        if not minimax_key:
            video_blockers.append("MINIMAX_API_KEY is missing.")
        if not video_enabled:
            video_blockers.append(
                "MiniMax video is disabled; set ENABLE_MINIMAX_VIDEO=1."
            )
        video = CapabilityConfig(
            provider="minimax",
            model=minimax_model,
            base_url=minimax_base.rstrip("/"),
            api_key=minimax_key,
            key_name="MINIMAX_API_KEY",
            key_source=minimax_key_source,
            ready=not video_blockers,
            blockers=tuple(video_blockers),
            enabled=video_enabled,
            supports_reference_images=True,
        )
    elif video_provider == "local":
        video = CapabilityConfig(
            provider="local",
            model="openmontage-motion-preview",
            base_url="",
            api_key="",
            key_name="",
            key_source=None,
            ready=True,
        )
    elif video_provider == "dashscope":
        video_blockers = () if dashscope_key else ("DASHSCOPE_API_KEY is missing.",)
        video = CapabilityConfig(
            provider="dashscope",
            model="wan2.7-i2v-flash",
            base_url="https://dashscope.aliyuncs.com",
            api_key=dashscope_key,
            key_name="DASHSCOPE_API_KEY",
            key_source=dashscope_source,
            ready=bool(dashscope_key),
            blockers=video_blockers,
        )
    else:
        video = CapabilityConfig(
            provider=video_provider,
            model="",
            base_url="",
            api_key="",
            key_name="",
            key_source=None,
            ready=False,
            blockers=(f"Unsupported VIDEO_PROVIDER: {video_provider}.",),
            enabled=False,
        )

    requested_tts, _ = tts_values.get("TTS_PROVIDER", "auto")
    requested_tts = requested_tts.lower()
    doubao_key, doubao_key_source = tts_values.get("DOUBAO_SPEECH_API_KEY")
    doubao_app_id, doubao_app_source = tts_values.get("DOUBAO_TTS_APPID")
    doubao_access_key, doubao_access_source = tts_values.get(
        "DOUBAO_TTS_ACCESS_KEY"
    )
    doubao_voice, _ = tts_values.get("DOUBAO_SPEECH_VOICE_TYPE")
    if not doubao_voice:
        doubao_voice, _ = tts_values.get(
            "DOUBAO_TTS_SPEAKER",
            "zh_female_vv_uranus_bigtts",
        )
    doubao_new_ready = bool(doubao_key and doubao_voice)
    doubao_legacy_ready = bool(
        doubao_app_id and doubao_access_key and doubao_voice
    )
    use_doubao = requested_tts == "doubao" or (
        requested_tts == "auto" and (doubao_new_ready or doubao_legacy_ready)
    )
    if requested_tts == "dashscope":
        audio_blockers = () if dashscope_key else ("DASHSCOPE_API_KEY is missing.",)
        audio = CapabilityConfig(
            provider="dashscope",
            model="cosyvoice-v3-flash",
            base_url="https://dashscope.aliyuncs.com",
            api_key=dashscope_key,
            key_name="DASHSCOPE_API_KEY",
            key_source=dashscope_source,
            ready=bool(dashscope_key),
            blockers=audio_blockers,
        )
    elif use_doubao:
        audio_ready = doubao_new_ready or doubao_legacy_ready
        audio_blockers = () if audio_ready else (
            "Configure either DOUBAO_SPEECH_API_KEY or both "
            "DOUBAO_TTS_APPID and DOUBAO_TTS_ACCESS_KEY, plus a Doubao voice.",
        )
        if doubao_new_ready:
            audio_key = doubao_key
            audio_key_name = "DOUBAO_SPEECH_API_KEY"
            audio_key_source = doubao_key_source
        else:
            audio_key = doubao_access_key
            audio_key_name = "DOUBAO_TTS_ACCESS_KEY"
            legacy_sources = {doubao_app_source, doubao_access_source} - {None}
            audio_key_source = (
                next(iter(legacy_sources)) if len(legacy_sources) == 1 else "mixed"
            )
        audio = CapabilityConfig(
            provider="doubao",
            model="seed-tts-2.0",
            base_url="https://openspeech.bytedance.com",
            api_key=audio_key,
            key_name=audio_key_name,
            key_source=audio_key_source,
            ready=audio_ready,
            blockers=audio_blockers,
            voice=doubao_voice,
        )
    elif requested_tts in {"auto", "local"}:
        audio = CapabilityConfig(
            provider="local",
            model="macos-say",
            base_url="",
            api_key="",
            key_name="",
            key_source=None,
            ready=True,
        )
    else:
        audio = CapabilityConfig(
            provider=requested_tts,
            model="",
            base_url="",
            api_key="",
            key_name="",
            key_source=None,
            ready=False,
            blockers=(f"Unsupported TTS_PROVIDER: {requested_tts}.",),
            enabled=False,
        )

    return ProviderProfile(
        text=text,
        image=image,
        video=video,
        audio=audio,
        source_paths={
            "factory_env": str(factory_path) if factory_path else "",
            "openmontage_env": str(openmontage_path) if openmontage_path else "",
        },
    )
