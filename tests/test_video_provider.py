import pytest

from factory.gateway_video import GatewayVideoClient, GatewayVideoError
from factory.minimax_h3_video import MiniMaxH3Client
from factory.provider_profile import CapabilityConfig
from factory.video_provider import build_video_client, default_video_resolution


def _capability(provider: str, *, model: str, base_url: str):
    return CapabilityConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key="secret",
        key_name="TEST_API_KEY",
        key_source="process",
        ready=True,
    )


def test_build_video_client_selects_minimax_h3():
    client = build_video_client(
        _capability(
            "minimax",
            model="MiniMax-H3",
            base_url="https://api.minimaxi.com",
        )
    )

    assert isinstance(client, MiniMaxH3Client)
    assert client.config.model == "MiniMax-H3"
    assert default_video_resolution("minimax") == "768P"


def test_build_video_client_preserves_gateway_provider():
    client = build_video_client(
        _capability(
            "gateway",
            model="doubao-seedance-2-0-fast",
            base_url="https://gateway.example/v1",
        )
    )

    assert isinstance(client, GatewayVideoClient)
    assert not isinstance(client, MiniMaxH3Client)
    assert default_video_resolution("gateway") == "720p"


def test_build_video_client_rejects_unsupported_provider():
    with pytest.raises(GatewayVideoError, match="Unsupported video provider"):
        build_video_client(
            _capability("local", model="preview", base_url="")
        )
