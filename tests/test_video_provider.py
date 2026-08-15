from pathlib import Path

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
        build_video_client(_capability("local", model="preview", base_url=""))


@pytest.mark.parametrize("method_name", ("submit", "submit_prepared", "generate"))
def test_production_built_client_blocks_every_direct_fresh_submit(method_name):
    client = build_video_client(
        _capability(
            "gateway",
            model="doubao-seedance-2-0-fast",
            base_url="https://gateway.example/v1",
        )
    )
    client.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("provider must not be contacted")
    )
    client.enable_curl_fallback = False

    if method_name == "submit":

        def call():
            return client.submit("prompt", allow_network=True)
    elif method_name == "submit_prepared":
        submission = client.prepare_submission("prompt", allow_network=True)

        def call():
            return client.submit_prepared(submission, allow_network=True)
    else:

        def call():
            return client.generate(
                "prompt",
                Path("unused.mp4"),
                allow_network=True,
            )

    with pytest.raises(GatewayVideoError, match="confirmation"):
        call()


def test_direct_adapter_construction_is_explicit_unconfirmed_test_path():
    client = GatewayVideoClient(
        build_video_client(
            _capability(
                "gateway",
                model="doubao-seedance-2-0-fast",
                base_url="https://gateway.example/v1",
            )
        ).config,
        urlopen_fn=lambda *_args, **_kwargs: None,
    )

    submission = client.prepare_submission("prompt", allow_network=True)

    assert submission.request_body
