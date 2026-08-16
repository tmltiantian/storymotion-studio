import json
from pathlib import Path

from factory.provider_profile import resolve_provider_profile


def _config(tmp_path: Path) -> dict:
    workspace = tmp_path / "workspace"
    openmontage = tmp_path / "openmontage"
    workspace.mkdir()
    openmontage.mkdir()
    return {
        "workspace": str(workspace),
        "sources": {
            "openMontage": str(openmontage),
        },
    }


def test_provider_profile_preserves_legacy_dashscope_defaults(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={"DASHSCOPE_API_KEY": "dash-secret"},
    )

    assert profile.text.provider == "dashscope"
    assert profile.text.model == "qwen3.7-plus"
    assert profile.text.ready is True
    assert profile.image.provider == "dashscope"
    assert profile.image.ready is True
    assert profile.audio.provider == "local"
    assert profile.audio.ready is True


def test_provider_profile_selects_gateway_by_capability(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "IMAGE_PROVIDER": "gateway",
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "gateway-secret",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
            "ENABLE_GATEWAY_VIDEO": "1",
        },
    )

    assert profile.text.provider == "gateway"
    assert profile.text.base_url == "https://gateway.example.invalid/v1"
    assert profile.text.model == "qwen3.6-plus"
    assert profile.text.ready is True
    assert profile.image.provider == "gateway"
    assert profile.image.model == "qwen-image-2.0"
    assert profile.image.ready is True
    assert profile.video.provider == "gateway"
    assert profile.video.model == "doubao-seedance-2-0-fast"
    assert profile.video.enabled is True
    assert profile.video.ready is True
    assert profile.video.blockers == ()
    assert profile.video.supports_reference_images is True


def test_gateway_credentials_never_imply_a_private_default_endpoint(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "IMAGE_PROVIDER": "gateway",
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "FICTIONAL_GATEWAY_KEY",
            "ENABLE_GATEWAY_VIDEO": "1",
        },
    )

    for capability in (profile.text, profile.image, profile.video):
        assert capability.base_url == ""
        assert capability.ready is False
        assert "GATEWAY_BASE_URL is missing." in capability.blockers


def test_gateway_video_requires_both_key_and_explicit_enable(tmp_path):
    config = _config(tmp_path)

    missing_key = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
            "ENABLE_GATEWAY_VIDEO": "1",
        },
    )
    disabled = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "gateway-secret",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
            "ENABLE_GATEWAY_VIDEO": "0",
        },
    )

    assert missing_key.video.ready is False
    assert missing_key.video.blockers == ("GATEWAY_API_KEY is missing.",)
    assert disabled.video.ready is False
    assert disabled.video.blockers == (
        "Gateway video is disabled; set ENABLE_GATEWAY_VIDEO=1.",
    )


def test_provider_profile_selects_minimax_h3_video(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "minimax-secret",
            "ENABLE_MINIMAX_VIDEO": "1",
        },
    )

    assert profile.video.provider == "minimax"
    assert profile.video.model == "MiniMax-H3"
    assert profile.video.base_url == "https://api.minimaxi.com"
    assert profile.video.key_name == "MINIMAX_API_KEY"
    assert profile.video.key_source == "process"
    assert profile.video.enabled is True
    assert profile.video.ready is True
    assert profile.video.blockers == ()
    assert profile.video.supports_reference_images is True


def test_minimax_h3_requires_both_key_and_explicit_enable(tmp_path):
    config = _config(tmp_path)

    missing_key = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "minimax",
            "ENABLE_MINIMAX_VIDEO": "1",
        },
    )
    disabled = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "minimax-secret",
            "ENABLE_MINIMAX_VIDEO": "0",
        },
    )

    assert missing_key.video.ready is False
    assert missing_key.video.blockers == ("MINIMAX_API_KEY is missing.",)
    assert disabled.video.ready is False
    assert disabled.video.blockers == (
        "MiniMax video is disabled; set ENABLE_MINIMAX_VIDEO=1.",
    )


def test_unknown_video_provider_fails_closed_instead_of_using_dashscope(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={
            "VIDEO_PROVIDER": "gatway",
            "DASHSCOPE_API_KEY": "dash-secret",
        },
    )

    assert profile.video.provider == "gatway"
    assert profile.video.ready is False
    assert profile.video.blockers == ("Unsupported VIDEO_PROVIDER: gatway.",)


def test_unknown_text_image_and_tts_providers_fail_closed(tmp_path):
    config = _config(tmp_path)

    profile = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gatway",
            "IMAGE_PROVIDER": "gatway",
            "TTS_PROVIDER": "doubou",
            "DASHSCOPE_API_KEY": "dash-secret",
            "DOUBAO_SPEECH_API_KEY": "doubao-secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
    )

    assert profile.text.provider == "gatway"
    assert profile.text.ready is False
    assert profile.text.blockers == ("Unsupported LLM_PROVIDER: gatway.",)
    assert profile.image.provider == "gatway"
    assert profile.image.ready is False
    assert profile.image.blockers == ("Unsupported IMAGE_PROVIDER: gatway.",)
    assert profile.audio.provider == "doubou"
    assert profile.audio.ready is False
    assert profile.audio.blockers == ("Unsupported TTS_PROVIDER: doubou.",)


def test_gateway_openai_key_alias_requires_gateway_base_url(tmp_path):
    config = _config(tmp_path)

    unrelated = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "OPENAI_API_KEY": "openai-secret",
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
        },
    )
    gateway = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "OPENAI_API_KEY": "gateway-secret",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
            "OPENAI_BASE_URL": "https://gateway.example.invalid/v1",
        },
    )

    assert unrelated.text.ready is False
    assert gateway.text.ready is True
    assert gateway.text.key_source == "process"
    assert gateway.text.key_name == "OPENAI_API_KEY"


def test_provider_profile_process_values_override_factory_dotenv(tmp_path):
    config = _config(tmp_path)
    workspace = Path(config["workspace"])
    (workspace / ".env").write_text(
        "LLM_PROVIDER=gateway\n"
        "GATEWAY_API_KEY=factory-secret\n"
        "GATEWAY_BASE_URL=https://gateway.example.invalid\n"
        "GATEWAY_IMAGE_MODEL=qwen-image-2.0\n",
        encoding="utf-8",
    )

    profile = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "process-secret",
            "OPENAI_MODEL": "qwen3.7-max",
        },
    )

    assert profile.text.api_key == "process-secret"
    assert profile.text.key_source == "process"
    assert profile.text.model == "qwen3.7-max"


def test_provider_profile_reuses_openmontage_doubao_settings(tmp_path):
    config = _config(tmp_path)
    openmontage = Path(config["sources"]["openMontage"])
    (openmontage / ".env").write_text(
        "DOUBAO_SPEECH_API_KEY=doubao-secret\n"
        "DOUBAO_SPEECH_VOICE_TYPE=test-voice\n",
        encoding="utf-8",
    )

    profile = resolve_provider_profile(config, process_env={"TTS_PROVIDER": "auto"})

    assert profile.audio.provider == "doubao"
    assert profile.audio.ready is True
    assert profile.audio.key_source == "openmontage.env"


def test_provider_profile_accepts_legacy_doubao_streaming_credentials(tmp_path):
    config = _config(tmp_path)
    workspace = Path(config["workspace"])
    (workspace / ".env").write_text(
        "TTS_PROVIDER=doubao\n"
        "DOUBAO_TTS_APPID=legacy-app\n"
        "DOUBAO_TTS_ACCESS_KEY=legacy-access\n"
        "DOUBAO_TTS_SPEAKER=legacy-voice\n",
        encoding="utf-8",
    )

    profile = resolve_provider_profile(config, process_env={})

    assert profile.audio.provider == "doubao"
    assert profile.audio.model == "seed-tts-2.0"
    assert profile.audio.ready is True
    assert profile.audio.key_name == "DOUBAO_TTS_ACCESS_KEY"
    assert profile.audio.key_source == "factory.env"


def test_provider_profile_never_reuses_openmontage_openai_key_for_gateway(tmp_path):
    config = _config(tmp_path)
    workspace = Path(config["workspace"])
    openmontage = Path(config["sources"]["openMontage"])
    (workspace / ".env").write_text(
        "LLM_PROVIDER=gateway\n"
        "IMAGE_PROVIDER=gateway\n"
        "OPENAI_BASE_URL=https://gateway.example.invalid/v1\n",
        encoding="utf-8",
    )
    (openmontage / ".env").write_text(
        "OPENAI_API_KEY=unrelated-openmontage-secret\n"
        "DOUBAO_SPEECH_API_KEY=doubao-secret\n"
        "DOUBAO_SPEECH_VOICE_TYPE=test-voice\n",
        encoding="utf-8",
    )

    profile = resolve_provider_profile(config, process_env={})

    assert profile.text.ready is False
    assert profile.text.key_name == "GATEWAY_API_KEY"
    assert profile.text.key_source is None
    assert profile.image.ready is False
    assert profile.image.key_name == "GATEWAY_API_KEY"
    assert profile.audio.ready is True
    assert profile.audio.key_source == "openmontage.env"


def test_provider_report_never_serializes_credentials(tmp_path):
    config = _config(tmp_path)
    profile = resolve_provider_profile(
        config,
        process_env={
            "LLM_PROVIDER": "gateway",
            "IMAGE_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "do-not-leak",
            "GATEWAY_BASE_URL": "https://gateway.example.invalid",
        },
    )

    report_text = json.dumps(profile.to_report(), ensure_ascii=False)

    assert "do-not-leak" not in report_text
    assert profile.to_report()["capabilities"]["text"]["key_present"] is True
    assert "api_key" not in profile.to_report()["capabilities"]["text"]
