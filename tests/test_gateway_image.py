import base64
import io
import json
import os
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from factory.gateway_image import (
    GatewayImageClient,
    GatewayImageConfig,
    GatewayImageError,
    normalize_image_size,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "application/json"):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_normalize_image_size_converts_lumenx_separator():
    assert normalize_image_size("576*1024") == "576x1024"
    assert normalize_image_size("1024x1024") == "1024x1024"


def test_gateway_image_posts_expected_payload_and_downloads_url(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/images/generations"):
            return FakeResponse(
                json.dumps({"data": [{"url": "https://cdn.example/image.png"}]}).encode()
            )
        assert request.full_url == "https://cdn.example/image.png"
        return FakeResponse(b"image-bytes", content_type="image/png")

    client = GatewayImageClient(
        GatewayImageConfig(
            api_key="secret",
            base_url="https://gateway.example.invalid/v1",
            model="qwen-image-2.0",
        ),
        urlopen_fn=fake_urlopen,
    )
    output = tmp_path / "shot.png"

    result = client.generate("cinematic alley", output, size="576*1024")

    api_request = requests[0][0]
    payload = json.loads(api_request.data.decode("utf-8"))
    assert api_request.full_url == "https://gateway.example.invalid/v1/images/generations"
    assert api_request.get_header("Authorization") == "Bearer secret"
    assert payload == {
        "model": "qwen-image-2.0",
        "prompt": "cinematic alley",
        "size": "576x1024",
        "n": 1,
        "response_format": "url",
        "sequential_image_generation": "disabled",
    }
    assert output.read_bytes() == b"image-bytes"
    assert result.output_path == str(output)
    assert result.response_format == "url"


def test_gateway_image_retries_incomplete_cdn_download_without_regenerating(tmp_path):
    api_calls = 0
    download_calls = 0

    class IncompleteResponse(FakeResponse):
        def read(self) -> bytes:
            raise IncompleteRead(b"partial", 10)

    def fake_urlopen(request, timeout):
        nonlocal api_calls, download_calls
        if request.full_url.endswith("/images/generations"):
            api_calls += 1
            return FakeResponse(
                json.dumps(
                    {"data": [{"url": "https://cdn.example/retry-image.png"}]}
                ).encode()
            )
        download_calls += 1
        if download_calls == 1:
            return IncompleteResponse(b"")
        return FakeResponse(b"complete-image", content_type="image/png")

    client = GatewayImageClient(
        GatewayImageConfig(
            "secret",
            "https://gateway.test/v1",
            "doubao-seedream-4-5",
            download_retry_delay_seconds=0,
        ),
        urlopen_fn=fake_urlopen,
    )
    output = tmp_path / "image.png"

    client.generate("prompt", output)

    assert api_calls == 1
    assert download_calls == 2
    assert output.read_bytes() == b"complete-image"


def test_gateway_image_does_not_retry_cdn_http_error(tmp_path):
    download_calls = 0

    def fake_urlopen(request, timeout):
        nonlocal download_calls
        if request.full_url.endswith("/images/generations"):
            return FakeResponse(
                json.dumps(
                    {"data": [{"url": "https://cdn.example/missing-image.png"}]}
                ).encode()
            )
        download_calls += 1
        raise HTTPError(
            request.full_url,
            404,
            "Not Found",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    client = GatewayImageClient(
        GatewayImageConfig(
            "secret",
            "https://gateway.test/v1",
            "doubao-seedream-4-5",
            download_attempts=3,
            download_retry_delay_seconds=0,
        ),
        urlopen_fn=fake_urlopen,
    )

    with pytest.raises(GatewayImageError, match="HTTP 404"):
        client.generate("prompt", tmp_path / "image.png")

    assert download_calls == 1


def test_gateway_image_exhausted_download_retry_is_sanitized(tmp_path):
    download_calls = 0
    signed_url = "https://cdn.example/image.png?signature=do-not-leak"

    def fake_urlopen(request, timeout):
        nonlocal download_calls
        if request.full_url.endswith("/images/generations"):
            return FakeResponse(
                json.dumps({"data": [{"url": signed_url}]}).encode()
            )
        download_calls += 1
        if download_calls < 3:
            raise IncompleteRead(b"partial", 10)
        raise URLError(signed_url)

    client = GatewayImageClient(
        GatewayImageConfig(
            "secret",
            "https://gateway.test/v1",
            "doubao-seedream-4-5",
            download_attempts=3,
            download_retry_delay_seconds=0,
        ),
        urlopen_fn=fake_urlopen,
    )
    output = tmp_path / "image.png"

    with pytest.raises(GatewayImageError) as exc_info:
        client.generate("prompt", output)

    message = str(exc_info.value)
    assert "download failed after 3 attempts" in message
    assert "signature" not in message
    assert "do-not-leak" not in message
    assert download_calls == 3
    assert not output.exists()


def test_gateway_image_decodes_base64_response(tmp_path):
    encoded = base64.b64encode(b"png-data").decode("ascii")

    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps({"data": [{"b64_json": encoded}]}).encode())

    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "qwen-image-2.0"),
        urlopen_fn=fake_urlopen,
    )
    output = tmp_path / "image.png"

    result = client.generate("prompt", output)

    assert output.read_bytes() == b"png-data"
    assert result.response_format == "b64_json"


def test_gateway_image_disables_seedream_provider_watermark(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/images/generations"):
            return FakeResponse(
                json.dumps(
                    {"data": [{"url": "https://cdn.example/image.png"}]}
                ).encode()
            )
        return FakeResponse(b"image-bytes", content_type="image/png")

    client = GatewayImageClient(
        GatewayImageConfig(
            "secret",
            "https://gateway.test/v1",
            "doubao-seedream-4-5",
        ),
        urlopen_fn=fake_urlopen,
    )

    client.generate("prompt", tmp_path / "image.png", size="1440x2560")

    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["watermark"] is False


def test_gateway_image_rejects_reference_images_for_non_seedream_before_network(
    tmp_path,
):
    called = False

    def fake_urlopen(request, timeout):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "qwen-image-2.0"),
        urlopen_fn=fake_urlopen,
    )

    with pytest.raises(GatewayImageError, match="does not support reference images"):
        client.generate("prompt", tmp_path / "image.png", ref_image_path="role.png")

    assert called is False


def test_gateway_image_posts_single_seedream_reference_as_png_data_uri(tmp_path):
    reference = tmp_path / "woman.png"
    reference_bytes = b"\x89PNG\r\n\x1a\nreference-bytes"
    reference.write_bytes(reference_bytes)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/images/generations"):
            return FakeResponse(
                json.dumps({"data": [{"url": "https://cdn.example/image.png"}]}).encode()
            )
        return FakeResponse(b"image-bytes", content_type="image/png")

    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "doubao-seedream-4-5"),
        urlopen_fn=fake_urlopen,
    )

    client.generate("same woman, front view", tmp_path / "output.png", ref_image_path=reference)

    payload = json.loads(requests[0].data.decode("utf-8"))
    expected = base64.b64encode(reference_bytes).decode("ascii")
    assert payload["image"] == f"data:image/png;base64,{expected}"
    assert payload["sequential_image_generation"] == "disabled"
    assert payload["watermark"] is False
    assert str(reference) not in json.dumps(payload)


def test_gateway_image_posts_multiple_seedream_references_with_correct_mime(tmp_path):
    references = (
        (tmp_path / "first.bin", b"\x89PNG\r\n\x1a\npng", "image/png"),
        (tmp_path / "second.bin", b"\xff\xd8\xffjpeg", "image/jpeg"),
        (tmp_path / "third.bin", b"RIFF\x04\x00\x00\x00WEBPwebp", "image/webp"),
    )
    for path, content, _mime in references:
        path.write_bytes(content)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse(
            json.dumps({"data": [{"b64_json": base64.b64encode(b"output").decode()}]}).encode()
        )

    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "doubao-seedream-4-5"),
        urlopen_fn=fake_urlopen,
    )
    client.generate(
        "conditioned image",
        tmp_path / "output.png",
        ref_image_paths=[path for path, _content, _mime in references],
    )

    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["image"] == [
        f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
        for _path, content, mime in references
    ]
    assert payload["sequential_image_generation"] == "disabled"


def test_gateway_image_reference_inputs_are_mutually_exclusive_and_bounded(tmp_path):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "doubao-seedream-4-5"),
        urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network should not be called")
        ),
    )

    with pytest.raises(GatewayImageError, match="mutually exclusive"):
        client.generate(
            "prompt",
            tmp_path / "output.png",
            ref_image_path=reference,
            ref_image_paths=[reference],
        )
    with pytest.raises(GatewayImageError, match="between 1 and 10"):
        client.generate("prompt", tmp_path / "output.png", ref_image_paths=[])
    with pytest.raises(GatewayImageError, match="between 1 and 10"):
        client.generate(
            "prompt",
            tmp_path / "output.png",
            ref_image_paths=[reference] * 11,
        )


def test_gateway_image_rejects_unsafe_reference_files_without_path_leak(tmp_path):
    valid = tmp_path / "valid.png"
    valid.write_bytes(b"\x89PNG\r\n\x1a\nrole")
    symlink = tmp_path / "secret-symlink.png"
    os.symlink(valid, symlink)
    linked_directory = tmp_path / "secret-linked-directory"
    os.symlink(tmp_path, linked_directory)
    parent_symlink = linked_directory / "valid.png"
    oversized = tmp_path / "secret-large.png"
    with oversized.open("wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.truncate(20 * 1024 * 1024 + 1)
    invalid = tmp_path / "secret-invalid.png"
    invalid.write_bytes(b"not-an-image")
    empty = tmp_path / "secret-empty.png"
    empty.write_bytes(b"")
    contacted = False

    def forbidden_network(*_args, **_kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("network should not be called")

    client = GatewayImageClient(
        GatewayImageConfig("secret", "https://gateway.test/v1", "doubao-seedream-4-5"),
        urlopen_fn=forbidden_network,
    )
    for reference, message in (
        (symlink, "regular file"),
        (parent_symlink, "regular file"),
        (oversized, "20 MB"),
        (invalid, "signature"),
        (empty, "empty"),
    ):
        with pytest.raises(GatewayImageError, match=message) as exc_info:
            client.generate("prompt", tmp_path / "output.png", ref_image_path=reference)
        error = str(exc_info.value)
        assert str(reference) not in error
        assert "data:image" not in error
        assert "secret" not in error
    assert contacted is False


def test_gateway_image_rejects_malformed_response_without_leaking_key(tmp_path):
    def fake_urlopen(request, timeout):
        return FakeResponse(json.dumps({"data": [{}]}).encode())

    client = GatewayImageClient(
        GatewayImageConfig("do-not-leak", "https://gateway.test/v1", "qwen-image-2.0"),
        urlopen_fn=fake_urlopen,
    )

    with pytest.raises(GatewayImageError) as exc_info:
        client.generate("prompt", tmp_path / "image.png")

    assert "image URL or b64_json" in str(exc_info.value)
    assert "do-not-leak" not in str(exc_info.value)


def test_gateway_image_http_error_includes_bounded_sanitized_provider_detail(
    tmp_path,
):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "code": "InvalidSize",
                            "message": (
                                "size 1024x1536 is unsupported; "
                                "token=secret; "
                                "https://provider.test/private?signature=hidden"
                            ),
                        }
                    }
                ).encode()
            ),
        )

    client = GatewayImageClient(
        GatewayImageConfig(
            "secret",
            "https://gateway.test/v1",
            "doubao-seedream-4-5",
        ),
        urlopen_fn=fake_urlopen,
    )

    with pytest.raises(GatewayImageError) as exc_info:
        client.generate("prompt", tmp_path / "image.png", size="1024x1536")

    message = str(exc_info.value)
    assert "InvalidSize" in message
    assert "size 1024x1536 is unsupported" in message
    assert "secret" not in message
    assert "signature=hidden" not in message
    assert len(message) < 700
