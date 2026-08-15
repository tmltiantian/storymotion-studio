import asyncio
import threading
from pathlib import Path
from types import MethodType

import pytest

from factory import video_provider
from factory.gateway_video import GatewayVideoClient, GatewayVideoError
from factory.gateway_video import GatewayVideoConfig
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


def _blocking_guarded_client():
    entered = threading.Event()
    release = threading.Event()
    raw_calls: list[int] = []
    client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="fake-key",
            base_url="https://gateway.example/v1",
            model="test-model",
        )
    )

    def raw_submit_prepared(self, _submission, **_kwargs):
        del self
        raw_calls.append(threading.get_ident())
        if len(raw_calls) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return object()

    client.submit_prepared = MethodType(raw_submit_prepared, client)
    video_provider._install_confirmation_gate(client)
    return client, entered, release, raw_calls


def test_submit_permit_cannot_be_stolen_or_shared_between_threads() -> None:
    client, entered, release, raw_calls = _blocking_guarded_client()
    permit = video_provider._authorize_confirmed_video_submit(client)
    intended_errors: list[BaseException] = []

    def intended() -> None:
        try:
            client.submit_prepared(
                object(),
                allow_network=True,
                _generation_permit=permit,
            )
        except BaseException as exc:
            intended_errors.append(exc)

    intended_thread = threading.Thread(target=intended)
    intended_thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(GatewayVideoError, match="confirmation"):
            client.submit_prepared(object(), allow_network=True)
    finally:
        release.set()
        intended_thread.join(timeout=5)

    assert not intended_thread.is_alive()
    assert intended_errors == []
    assert len(raw_calls) == 1


def test_submit_permit_cannot_be_stolen_before_intended_call() -> None:
    client, _entered, release, raw_calls = _blocking_guarded_client()
    release.set()
    permit = video_provider._authorize_confirmed_video_submit(client)

    with pytest.raises(GatewayVideoError, match="confirmation"):
        client.submit_prepared(object(), allow_network=True)

    client.submit_prepared(
        object(),
        allow_network=True,
        _generation_permit=permit,
    )
    with pytest.raises(GatewayVideoError, match="confirmation"):
        client.submit_prepared(
            object(),
            allow_network=True,
            _generation_permit=permit,
        )
    assert len(raw_calls) == 1


def test_submit_permit_is_isolated_between_async_tasks() -> None:
    async def exercise() -> tuple[list[BaseException], list[int]]:
        client, entered, release, raw_calls = _blocking_guarded_client()
        permit = video_provider._authorize_confirmed_video_submit(client)
        intended = asyncio.create_task(
            asyncio.to_thread(
                client.submit_prepared,
                object(),
                allow_network=True,
                _generation_permit=permit,
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)

        async def unconfirmed() -> list[BaseException]:
            try:
                await asyncio.to_thread(
                    client.submit_prepared,
                    object(),
                    allow_network=True,
                )
            except BaseException as exc:
                return [exc]
            return []

        errors = await unconfirmed()
        release.set()
        await intended
        return errors, raw_calls

    errors, raw_calls = asyncio.run(exercise())

    assert len(errors) == 1
    assert isinstance(errors[0], GatewayVideoError)
    assert len(raw_calls) == 1


def test_submit_permit_is_not_inherited_by_child_async_task() -> None:
    client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key="fake-key",
            base_url="https://gateway.example/v1",
            model="test-model",
        )
    )
    raw_calls: list[str] = []
    child_errors: list[BaseException] = []

    def raw_submit_prepared(self, submission, **_kwargs):
        del self
        raw_calls.append(str(submission))

        async def child_submit() -> None:
            await asyncio.sleep(0)
            try:
                client.submit_prepared("child", allow_network=True)
            except BaseException as exc:
                child_errors.append(exc)

        return asyncio.create_task(child_submit())

    client.submit_prepared = MethodType(raw_submit_prepared, client)
    video_provider._install_confirmation_gate(client)

    async def exercise() -> None:
        permit = video_provider._authorize_confirmed_video_submit(client)
        child = client.submit_prepared(
            "parent",
            allow_network=True,
            _generation_permit=permit,
        )
        await child

    asyncio.run(exercise())

    assert raw_calls == ["parent"]
    assert len(child_errors) == 1
    assert isinstance(child_errors[0], GatewayVideoError)
