import json

import pytest

from factory.gateway_text import GatewayTextClient, GatewayTextConfig, GatewayTextError


class FakeResponse:
    def __init__(self, data):
        self.body = json.dumps(data).encode("utf-8")
        self.status = 200

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_gateway_text_forwards_json_response_format():
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = GatewayTextClient(
        GatewayTextConfig("secret", "https://gateway.test/v1", "qwen3.6-plus"),
        urlopen_fn=fake_urlopen,
    )

    result = client.chat(
        [{"role": "user", "content": "return json"}],
        response_format={"type": "json_object"},
        allow_network=True,
    )

    request = requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://gateway.test/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret"
    assert payload["model"] == "qwen3.6-plus"
    assert payload["response_format"] == {"type": "json_object"}
    assert result.content == '{"ok":true}'


def test_gateway_text_requires_explicit_network_enable():
    client = GatewayTextClient(
        GatewayTextConfig("secret", "https://gateway.test/v1", "qwen3.6-plus"),
        urlopen_fn=lambda *args, **kwargs: pytest.fail("network should not run"),
    )

    with pytest.raises(GatewayTextError, match="explicitly enabled"):
        client.chat([{"role": "user", "content": "hello"}])


def test_gateway_text_rejects_malformed_response_without_secret():
    client = GatewayTextClient(
        GatewayTextConfig("do-not-leak", "https://gateway.test/v1", "qwen3.6-plus"),
        urlopen_fn=lambda *args, **kwargs: FakeResponse({"choices": []}),
    )

    with pytest.raises(GatewayTextError) as exc_info:
        client.chat([{"role": "user", "content": "hello"}], allow_network=True)

    assert "choices" in str(exc_info.value)
    assert "do-not-leak" not in str(exc_info.value)
