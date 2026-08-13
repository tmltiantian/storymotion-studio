import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from PIL import Image

from factory.gateway_video import (
    GatewayVideoClient,
    GatewayVideoConfig,
    GatewayVideoError,
    GatewayVideoHTTPError,
    GatewayVideoProbe,
    GatewayVideoTask,
)
from tests.media_fixtures import VALID_VIDEO_MP4


FTYP_ONLY_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
MINIMAL_MP4 = VALID_VIDEO_MP4


class FakeResponse:
    def __init__(self, data, *, status=200, headers=None):
        self.body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        self.stream = io.BytesIO(self.body)
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self.stream.read(-1 if size is None else size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _config(**overrides):
    values = {
        "api_key": "secret",
        "base_url": "https://gateway.test/v1",
        "model": "doubao-seedance-2-0-fast",
        "poll_interval_seconds": 0,
        "max_wait_seconds": 10,
    }
    values.update(overrides)
    return GatewayVideoConfig(**values)


def _client(fake_urlopen, **config_overrides):
    return GatewayVideoClient(
        _config(**config_overrides),
        urlopen_fn=fake_urlopen,
        sleep_fn=lambda _: None,
    )


def _write_wav(path: Path, *, duration: float = 1.0) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\x01\x00\x02\x00" * int(duration * 48000))


def test_gateway_video_submit_uses_video_contract_and_reference_images(tmp_path):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nrole-image")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": "task-123", "status": "queued"})

    task = _client(fake_urlopen).submit(
        "camera pans across a station",
        images=[reference, "https://assets.example/second-role.webp"],
        duration=6,
        ratio="9:16",
        resolution="720p",
        generate_audio=False,
        allow_network=True,
    )

    payload = json.loads(requests[0].data.decode("utf-8"))
    assert requests[0].full_url == "https://gateway.test/v1/video/generations"
    assert requests[0].get_method() == "POST"
    assert requests[0].get_header("Authorization") == "Bearer secret"
    assert requests[0].get_header("Idempotency-key") is None
    assert payload["model"] == "doubao-seedance-2-0-fast"
    assert payload["prompt"] == "camera pans across a station"
    assert payload["duration"] == 6
    assert payload["seconds"] == "6"
    assert payload["size"] == "720p"
    assert payload["images"][0].startswith("data:image/png;base64,")
    assert payload["images"][1] == "https://assets.example/second-role.webp"
    assert payload["metadata"] == {
        "duration": 6,
        "ratio": "9:16",
        "resolution": "720p",
        "generate_audio": False,
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": payload["images"][0]},
                "role": "reference_image",
            },
            {
                "type": "image_url",
                "image_url": {"url": "https://assets.example/second-role.webp"},
                "role": "reference_image",
            },
        ],
    }
    assert "messages" not in payload
    assert task.task_id == "task-123"
    assert task.status == "queued"


def test_gateway_video_submit_uses_dedicated_long_timeout():
    timeouts = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        return FakeResponse({"id": "task-123", "status": "queued"})

    task = _client(
        fake_urlopen,
        timeout_seconds=12,
        submit_timeout_seconds=300,
    ).submit("prompt", allow_network=True)

    assert task.task_id == "task-123"
    assert timeouts == [300]


def test_gateway_video_submit_idempotency_key_is_stable_per_request():
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": f"task-{len(requests)}", "status": "queued"})

    client = _client(fake_urlopen, send_idempotency_key=True)
    client.submit("same prompt", allow_network=True)
    client.submit("same prompt", allow_network=True)
    client.submit("different prompt", allow_network=True)

    keys = [
        request.get_header("Idempotency-key")
        for request in requests
    ]
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]


def test_gateway_video_submit_falls_back_to_curl_without_secret_in_argv(
    tmp_path,
):
    observed = {}

    def failing_urlopen(*_args, **_kwargs):
        raise URLError("tls eof")

    def fake_curl(command, *, input, **_kwargs):
        observed["command"] = command
        observed["input"] = input
        output = Path(command[command.index("--output") + 1])
        output.write_text(
            json.dumps({"id": "task-curl", "status": "queued"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="200", stderr="")

    client = GatewayVideoClient(
        _config(),
        urlopen_fn=failing_urlopen,
        sleep_fn=lambda _: None,
        curl_runner=fake_curl,
        curl_bin="curl",
        enable_curl_fallback=True,
    )

    task = client.submit("prompt", allow_network=True)

    assert task.task_id == "task-curl"
    assert "secret" not in " ".join(observed["command"])
    assert "Authorization: Bearer secret" in observed["input"]
    assert "idempotency-key:" not in observed["input"].lower()
    assert "--http1.1" in observed["command"]


def test_gateway_video_download_falls_back_to_curl(tmp_path):
    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/video/generations/task-123"):
            return FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            )
        raise URLError("tls eof")

    def fake_curl(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(MINIMAL_MP4)
        return SimpleNamespace(returncode=0, stdout="200", stderr="")

    client = GatewayVideoClient(
        _config(),
        urlopen_fn=fake_urlopen,
        sleep_fn=lambda _: None,
        curl_runner=fake_curl,
        curl_bin="curl",
        enable_curl_fallback=True,
    )
    output = tmp_path / "video.mp4"

    result = client.complete_task(
        GatewayVideoTask(task_id="task-123", status="queued"),
        output,
        allow_network=True,
    )

    assert result.task_id == "task-123"
    assert output.read_bytes() == MINIMAL_MP4


def test_gateway_video_submit_embeds_one_reference_audio_after_images(tmp_path):
    audio = tmp_path / "line.wav"
    image = tmp_path / "role.png"
    _write_wav(audio)
    image.write_bytes(b"\x89PNG\r\n\x1a\nrole-image")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": "task-audio", "status": "queued"})

    _client(fake_urlopen).submit(
        "the cat speaks only with the supplied audio",
        images=[image],
        audio=audio,
        duration=5,
        generate_audio=True,
        allow_network=True,
    )

    payload = json.loads(requests[0].data)
    content = payload["metadata"]["content"]

    assert content[0]["type"] == "image_url"
    assert content[1]["type"] == "audio_url"
    assert content[1]["role"] == "reference_audio"
    assert content[1]["audio_url"]["url"].startswith("data:audio/wav;base64,")
    assert "audio" not in payload


def test_gateway_video_submit_rejects_invalid_audio_before_network(tmp_path):
    audio = tmp_path / "line.wav"
    audio.write_bytes(b"not-a-wave")
    contacted = False

    def fake_urlopen(request, timeout):
        nonlocal contacted
        contacted = True
        raise AssertionError("network must not be called")

    with pytest.raises(GatewayVideoError, match="valid audio"):
        _client(fake_urlopen).submit(
            "speak", audio=audio, allow_network=True
        )

    assert contacted is False


@pytest.mark.parametrize(
    "audio",
    [
        "https://user:password@assets.example/line.wav",
        "data:audio/wav;base64,not-base64!",
    ],
)
def test_gateway_video_submit_rejects_unsafe_audio_without_exposing_value(audio):
    with pytest.raises(GatewayVideoError) as exc_info:
        _client(lambda *_args, **_kwargs: None).submit(
            "speak", audio=audio, allow_network=True
        )

    assert audio not in str(exc_info.value)


def test_gateway_video_submit_rejects_invalid_local_reference_before_network(tmp_path):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"not-a-real-png")
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match="valid PNG, JPEG, or WebP"):
        _client(fake_urlopen).submit(
            "animate the character",
            images=[reference],
            allow_network=True,
        )

    assert contacted is False


def test_gateway_video_submit_rejects_invalid_reference_data_uri_before_network():
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    invalid_data_uri = "data:image/png;base64," + "bm90LWEtcmVhbC1wbmc="
    with pytest.raises(GatewayVideoError, match="valid PNG, JPEG, or WebP"):
        _client(fake_urlopen).submit(
            "animate the character",
            images=[invalid_data_uri],
            allow_network=True,
        )

    assert contacted is False


def test_gateway_video_submit_rejects_oversized_aggregate_request_before_network(
    tmp_path,
):
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 256)
    with pytest.raises(GatewayVideoError, match="request body exceeded"):
        _client(
            fake_urlopen,
            max_request_body_bytes=256,
        ).submit(
            "animate the character",
            images=[reference],
            allow_network=True,
        )

    assert contacted is False


def test_gateway_video_compresses_valid_local_references_to_fit_request(
    tmp_path,
):
    references = []
    for index in range(4):
        reference = tmp_path / f"reference-{index}.png"
        noise = Image.effect_noise((1024, 1024), 100).convert("RGB")
        noise.save(reference, format="PNG")
        references.append(reference)
    original_bytes = sum(path.stat().st_size for path in references)
    client = _client(
        lambda *_args, **_kwargs: None,
        max_request_body_bytes=3 * 1024 * 1024,
    )

    submission = client.prepare_submission(
        "preserve four reference images",
        images=references,
        allow_network=True,
    )
    payload = json.loads(submission.request_body)

    assert original_bytes > client.config.max_request_body_bytes
    assert len(submission.request_body) <= client.config.max_request_body_bytes
    assert all(
        image.startswith("data:image/jpeg;base64,")
        for image in payload["images"]
    )


def test_gateway_video_compacts_large_seedance_request_below_transport_target(
    tmp_path,
):
    references = []
    for index in range(2):
        reference = tmp_path / f"reference-{index}.png"
        Image.effect_noise((1024, 1024), 100).convert("RGB").save(
            reference,
            format="PNG",
        )
        references.append(reference)
    client = _client(lambda *_args, **_kwargs: None)

    submission = client.prepare_submission(
        "preserve both reference images",
        images=references,
        allow_network=True,
    )
    payload = json.loads(submission.request_body)

    assert len(submission.request_body) <= client.config.target_request_body_bytes
    assert all(
        image.startswith("data:image/jpeg;base64,")
        for image in payload["images"]
    )


@pytest.mark.parametrize(
    "image_url",
    ["https://", "http://user:password@assets.example/role.png"],
)
def test_gateway_video_submit_rejects_unsafe_remote_reference_before_network(
    image_url,
):
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match="valid HTTP or HTTPS URL"):
        _client(fake_urlopen).submit(
            "animate the character",
            images=[image_url],
            allow_network=True,
        )

    assert contacted is False


def test_gateway_video_generate_polls_and_downloads_completed_video(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.get_method() == "POST":
            return FakeResponse({"id": "task-123", "status": "queued"})
        if request.full_url.endswith("/video/generations/task-123"):
            poll_number = sum(
                item.full_url.endswith("/video/generations/task-123") for item in requests
            )
            if poll_number == 1:
                return FakeResponse({"id": "task-123", "status": "in_progress", "progress": 50})
            return FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {
                        "url": "https://cdn.example/clip.mp4?signature=private-token"
                    },
                }
            )
        return FakeResponse(MINIMAL_MP4)

    output = tmp_path / "clips/shot_001.mp4"
    result = _client(fake_urlopen).generate(
        "animate the two characters",
        output,
        duration=5,
        allow_network=True,
    )

    assert output.read_bytes() == MINIMAL_MP4
    assert result.task_id == "task-123"
    assert result.status == "completed"
    assert result.poll_count == 2
    assert result.output_size_bytes == len(MINIMAL_MP4)
    report_text = json.dumps(result.to_report())
    assert result.to_report()["source_host"] == "cdn.example"
    assert "private-token" not in report_text
    assert "secret" not in report_text
    assert [request.get_method() for request in requests] == ["POST", "GET", "GET", "GET"]


def test_gateway_video_poll_recovers_from_one_transient_network_error(tmp_path):
    requests = []
    poll_attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal poll_attempts
        requests.append(request)
        if request.get_method() == "POST":
            return FakeResponse({"id": "task-123", "status": "queued"})
        if request.full_url.endswith("/video/generations/task-123"):
            poll_attempts += 1
            if poll_attempts == 1:
                raise URLError("temporary TLS EOF")
            return FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            )
        return FakeResponse(MINIMAL_MP4)

    output = tmp_path / "video.mp4"
    result = _client(fake_urlopen).generate(
        "animate the character",
        output,
        allow_network=True,
    )

    assert output.read_bytes() == MINIMAL_MP4
    assert result.poll_count == 1
    assert poll_attempts == 2
    assert [request.get_method() for request in requests] == [
        "POST",
        "GET",
        "GET",
        "GET",
    ]


def test_gateway_video_poll_stops_after_three_consecutive_network_errors(tmp_path):
    poll_attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal poll_attempts
        if request.get_method() == "POST":
            return FakeResponse({"id": "task-123", "status": "queued"})
        poll_attempts += 1
        raise URLError("temporary TLS EOF")

    with pytest.raises(GatewayVideoError, match="poll request failed"):
        _client(fake_urlopen).generate(
            "animate the character",
            tmp_path / "video.mp4",
            allow_network=True,
        )

    assert poll_attempts == 3


def test_gateway_video_generate_parses_nested_new_api_success_response(tmp_path):
    responses = iter(
        [
            FakeResponse({"task_id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "code": "success",
                    "data": {
                        "task_id": "task-123",
                        "status": "SUCCESS",
                        "result_url": "https://cdn.example/clip.mp4",
                    },
                }
            ),
            FakeResponse(MINIMAL_MP4),
        ]
    )

    result = _client(lambda *args, **kwargs: next(responses)).generate(
        "prompt",
        tmp_path / "video.mp4",
        allow_network=True,
    )

    assert result.status == "completed"
    assert result.task_id == "task-123"
    assert result.poll_count == 1


def test_gateway_video_generate_parses_nested_new_api_failure_response(tmp_path):
    responses = iter(
        [
            FakeResponse({"task_id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "code": "success",
                    "data": {
                        "task_id": "task-123",
                        "status": "FAILURE",
                        "fail_reason": "content policy rejected",
                    },
                }
            ),
        ]
    )

    with pytest.raises(GatewayVideoError, match="content policy rejected"):
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            tmp_path / "video.mp4",
            allow_network=True,
        )


def test_gateway_video_generate_parses_failure_inside_output_envelope(tmp_path):
    responses = iter(
        [
            FakeResponse({"task_id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "output": {
                        "task_id": "task-123",
                        "status": "failed",
                        "error": {
                            "code": "content_policy",
                            "message": "request rejected",
                        },
                    }
                }
            ),
        ]
    )

    with pytest.raises(GatewayVideoError, match="content_policy"):
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            tmp_path / "video.mp4",
            allow_network=True,
        )


def test_gateway_video_submit_wraps_local_reference_read_failure(
    tmp_path,
    monkeypatch,
):
    reference = tmp_path / "role.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\nrole-image")
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("read failed")))

    with pytest.raises(GatewayVideoError, match="Unable to read gateway video reference"):
        _client(fake_urlopen).submit(
            "animate the character",
            images=[reference],
            allow_network=True,
        )

    assert contacted is False


def test_gateway_video_uses_image_signature_when_suffix_differs(tmp_path):
    reference = tmp_path / "provider-output.png"
    reference.write_bytes(b"\xff\xd8\xffprovider-jpeg")

    normalized = _client(lambda *_args, **_kwargs: None)._normalize_image(reference)

    assert normalized.startswith("data:image/jpeg;base64,")


def test_gateway_video_complete_task_resumes_without_new_submission(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/video/generations/task-123"):
            return FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            )
        return FakeResponse(MINIMAL_MP4)

    output = tmp_path / "video.mp4"
    result = _client(fake_urlopen).complete_task(
        GatewayVideoTask(task_id="task-123", status="queued"),
        output,
        allow_network=True,
    )

    assert result.task_id == "task-123"
    assert output.read_bytes() == MINIMAL_MP4
    assert [request.get_method() for request in requests] == ["GET", "GET"]


def test_gateway_video_generate_reports_failed_task_without_leaking_secret(tmp_path):
    responses = iter(
        [
            FakeResponse({"task_id": "task-failed", "status": "queued"}),
            FakeResponse(
                {
                    "task_id": "task-failed",
                    "status": "failed",
                    "error": {"code": "content_policy", "message": "secret was rejected"},
                }
            ),
        ]
    )

    with pytest.raises(GatewayVideoError, match="content_policy") as error:
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            tmp_path / "video.mp4",
            allow_network=True,
        )

    assert "secret" not in str(error.value)
    assert not (tmp_path / "video.mp4").exists()


def test_gateway_video_generate_rejects_oversized_download(tmp_path):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            FakeResponse(b"12345"),
        ]
    )
    output = tmp_path / "video.mp4"

    with pytest.raises(GatewayVideoError, match="maximum allowed size"):
        _client(
            lambda *args, **kwargs: next(responses),
            max_download_bytes=4,
        ).generate("prompt", output, allow_network=True)

    assert not output.exists()
    assert not output.with_suffix(".mp4.part").exists()


def test_gateway_video_generate_rejects_non_video_download(tmp_path):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            FakeResponse(b'{"error":"expired signed URL"}'),
        ]
    )
    output = tmp_path / "video.mp4"

    with pytest.raises(GatewayVideoError, match="valid MP4"):
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            output,
            allow_network=True,
        )

    assert not output.exists()
    assert not output.with_suffix(".mp4.part").exists()


def test_gateway_video_generate_rejects_truncated_mp4_with_only_ftyp_header(
    tmp_path,
):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            FakeResponse(FTYP_ONLY_MP4),
        ]
    )
    output = tmp_path / "video.mp4"

    with pytest.raises(GatewayVideoError, match="valid MP4"):
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            output,
            allow_network=True,
        )

    assert not output.exists()


def test_gateway_video_generate_retries_short_content_length_download(tmp_path):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            FakeResponse(
                MINIMAL_MP4,
                headers={"Content-Length": str(len(MINIMAL_MP4) + 10)},
            ),
            FakeResponse(
                MINIMAL_MP4,
                headers={"Content-Length": str(len(MINIMAL_MP4))},
            ),
        ]
    )
    output = tmp_path / "video.mp4"

    result = _client(lambda *args, **kwargs: next(responses)).generate(
        "prompt",
        output,
        allow_network=True,
    )

    assert result.output_size_bytes == len(MINIMAL_MP4)
    assert output.read_bytes() == MINIMAL_MP4
    assert not output.with_suffix(".mp4.part").exists()


def test_gateway_video_generate_rejects_repeated_short_content_length_download(
    tmp_path,
):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            *[
                FakeResponse(
                    MINIMAL_MP4,
                    headers={"Content-Length": str(len(MINIMAL_MP4) + 10)},
                )
                for _ in range(3)
            ],
        ]
    )
    output = tmp_path / "video.mp4"

    with pytest.raises(GatewayVideoError, match="Content-Length"):
        _client(lambda *args, **kwargs: next(responses)).generate(
            "prompt",
            output,
            allow_network=True,
        )

    assert not output.exists()


def test_gateway_video_generate_rejects_non_http_download_url(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.get_method() == "POST":
            return FakeResponse({"id": "task-123", "status": "queued"})
        if request.full_url.startswith("https://gateway.test/"):
            return FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "file:///etc/passwd"},
                }
            )
        raise AssertionError("non-HTTP download must not be opened")

    with pytest.raises(GatewayVideoError, match="HTTP or HTTPS"):
        _client(fake_urlopen).generate(
            "prompt",
            tmp_path / "video.mp4",
            allow_network=True,
        )

    assert all(not request.full_url.startswith("file:") for request in requests)


def test_gateway_video_download_streams_in_bounded_chunks(tmp_path):
    streaming_mp4 = VALID_VIDEO_MP4
    video_response = FakeResponse(streaming_mp4)
    read_sizes = []
    original_read = video_response.read

    def bounded_read(size=-1):
        read_sizes.append(size)
        return original_read(size)

    video_response.read = bounded_read
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            ),
            video_response,
        ]
    )

    output = tmp_path / "video.mp4"
    _client(
        lambda *args, **kwargs: next(responses),
        download_chunk_bytes=8,
    ).generate("prompt", output, allow_network=True)

    assert output.read_bytes() == streaming_mp4
    assert len(read_sizes) > 2
    assert set(read_sizes) == {8}


def test_gateway_video_rejects_oversized_json_response_in_a_bounded_read():
    response = FakeResponse({"id": "task-123", "padding": "x" * 128})
    read_sizes = []
    original_read = response.read

    def bounded_read(size=-1):
        read_sizes.append(size)
        return original_read(size)

    response.read = bounded_read

    with pytest.raises(GatewayVideoError, match="JSON response exceeded"):
        _client(
            lambda *args, **kwargs: response,
            max_json_response_bytes=32,
        ).submit("prompt", allow_network=True)

    assert read_sizes == [33]


def test_gateway_video_encodes_task_id_as_a_poll_path_segment(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.startswith("https://gateway.test/"):
            return FakeResponse(
                {
                    "id": "task/123?attempt=1",
                    "status": "completed",
                    "metadata": {"url": "https://cdn.example/clip.mp4"},
                }
            )
        return FakeResponse(MINIMAL_MP4)

    result = _client(fake_urlopen).complete_task(
        GatewayVideoTask(task_id="task/123?attempt=1", status="queued"),
        tmp_path / "video.mp4",
        allow_network=True,
    )

    assert result.status == "completed"
    assert requests[0].full_url.endswith(
        "/video/generations/task%2F123%3Fattempt%3D1"
    )


def test_gateway_video_client_requires_explicit_network_enable(tmp_path):
    client = _client(lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    with pytest.raises(GatewayVideoError, match="explicitly enabled"):
        client.generate("prompt", tmp_path / "video.mp4")


def test_gateway_video_client_rejects_non_http_gateway_base_before_network():
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match="base URL must use HTTP or HTTPS"):
        _client(
            fake_urlopen,
            base_url="file:///etc/passwd",
        ).submit("prompt", allow_network=True)

    assert contacted is False


def test_gateway_video_generate_refuses_existing_output_before_paid_submit(tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"existing-video")
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match="already exists.*overwrite"):
        _client(fake_urlopen).generate("prompt", output, allow_network=True)

    assert contacted is False
    assert output.read_bytes() == b"existing-video"


def test_gateway_video_generate_rejects_invalid_output_parent_before_paid_submit(
    tmp_path,
):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_bytes(b"occupied")
    contacted = False

    def fake_urlopen(*args, **kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match="output directory"):
        _client(fake_urlopen).generate(
            "prompt",
            parent_file / "video.mp4",
            allow_network=True,
        )

    assert contacted is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"submit_timeout_seconds": 0}, "submit timeout must be positive"),
        ({"max_wait_seconds": 0}, "maximum wait must be positive"),
        ({"poll_interval_seconds": -1}, "poll interval cannot be negative"),
    ],
)
def test_gateway_video_client_rejects_unsafe_poll_settings_before_submit(
    overrides,
    message,
):
    client = _client(
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be contacted")
        ),
        **overrides,
    )

    with pytest.raises(GatewayVideoError, match=message):
        client.submit("prompt", allow_network=True)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"duration": 3}, "between 4 and 15 seconds"),
        ({"duration": 16}, "at most 15 seconds"),
        ({"resolution": "1080p"}, "Fast supports 480p or 720p"),
        (
            {"images": [f"https://assets.example/ref-{index}.png" for index in range(10)]},
            "at most 9 reference images",
        ),
    ],
)
def test_gateway_video_submit_rejects_unsupported_seedance_fast_settings_before_network(
    kwargs,
    message,
):
    contacted = False

    def fake_urlopen(*args, **unused_kwargs):
        nonlocal contacted
        contacted = True
        raise AssertionError("provider must not be contacted")

    with pytest.raises(GatewayVideoError, match=message):
        _client(fake_urlopen).submit("prompt", allow_network=True, **kwargs)

    assert contacted is False


def test_gateway_video_standard_seedance_accepts_1080p_contract():
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": "task-123", "status": "queued"})

    task = _client(
        fake_urlopen,
        model="doubao-seedance-2-0",
    ).submit(
        "prompt",
        resolution="1080p",
        allow_network=True,
    )

    assert task.task_id == "task-123"
    assert json.loads(requests[0].data)["metadata"]["resolution"] == "1080p"


def test_gateway_video_client_reports_authentication_failure():
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad token"}'),
        )

    with pytest.raises(GatewayVideoError, match="authentication failed"):
        _client(fake_urlopen).submit("prompt", allow_network=True)


def test_gateway_video_http_error_includes_bounded_sanitized_provider_detail():
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
                            "code": "InvalidParameter",
                            "message": (
                                "resolution 1080p is not supported; "
                                "api_key=secret; inspect "
                                "https://provider.test/private?signature=hidden; "
                                "audio=data:audio/wav;base64,"
                                "U0VDUkVUX0FVRElPX1BBWUxPQUQ="
                            ),
                        }
                    }
                ).encode()
            ),
        )

    with pytest.raises(GatewayVideoHTTPError) as exc_info:
        _client(fake_urlopen).submit("prompt", allow_network=True)

    message = str(exc_info.value)
    assert exc_info.value.status_code == 400
    assert "InvalidParameter" in message
    assert "resolution 1080p is not supported" in message
    assert "secret" not in message
    assert "signature=hidden" not in message
    assert "data:audio/wav;base64," not in message
    assert "U0VDUkVUX0FVRElPX1BBWUxPQUQ=" not in message
    assert len(message) < 700


def test_gateway_video_generate_honors_poll_deadline(tmp_path):
    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = FakeClock()
    requests = []
    timeouts = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        timeouts.append(timeout)
        if request.get_method() == "POST":
            return FakeResponse({"id": "task-123", "status": "queued"})
        return FakeResponse({"id": "task-123", "status": "in_progress"})

    client = GatewayVideoClient(
        _config(poll_interval_seconds=2, max_wait_seconds=3),
        urlopen_fn=fake_urlopen,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    with pytest.raises(GatewayVideoError, match="timed out after 3 seconds"):
        client.generate("prompt", tmp_path / "video.mp4", allow_network=True)

    assert clock.now == 3
    assert [request.get_method() for request in requests] == ["POST", "GET"]
    assert timeouts == [300.0, 1.0]


def test_gateway_video_probe_uses_real_contract_and_reports_async_task():
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": "task-123", "status": "queued"})

    report = GatewayVideoProbe(_config(), urlopen_fn=fake_urlopen).run(
        "camera pans across a station",
        duration=5,
        allow_network=True,
    )

    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["prompt"] == "camera pans across a station"
    assert "messages" not in payload
    assert report["success"] is True
    assert report["production_ready"] is False
    assert report["validation_scope"] == "submission_only"
    assert report["billable_submission"] is True
    assert report["response_shape"] == "async_task"
    assert report["task_id"] == "task-123"
    assert report["status_url"] == "https://gateway.test/v1/video/generations/task-123"
    assert "secret" not in json.dumps(report)


def test_gateway_video_probe_redacts_immediate_signed_url():
    report = GatewayVideoProbe(
        _config(),
        urlopen_fn=lambda *args, **kwargs: FakeResponse(
            {
                "url": (
                    "https://cdn.example/private-path-token/clip.mp4"
                    "?signature=private-query-token"
                )
            }
        ),
    ).run("prompt", allow_network=True)

    report_text = json.dumps(report)
    assert report["video_url"] == "https://cdn.example"
    assert "private-path-token" not in report_text
    assert "private-query-token" not in report_text


def test_gateway_video_download_error_redacts_signed_url_path_and_query(tmp_path):
    responses = iter(
        [
            FakeResponse({"id": "task-123", "status": "queued"}),
            FakeResponse(
                {
                    "id": "task-123",
                    "status": "completed",
                    "metadata": {
                        "url": (
                            "https://cdn.example/private-path-token/clip.mp4"
                            "?signature=private-query-token"
                        )
                    },
                }
            ),
        ]
    )

    def fake_urlopen(request, timeout):
        try:
            return next(responses)
        except StopIteration:
            raise URLError(
                "request to https://cdn.example/private-path-token/clip.mp4"
                "?signature=private-query-token failed"
            )

    with pytest.raises(GatewayVideoError) as error:
        _client(fake_urlopen).generate(
            "prompt",
            tmp_path / "video.mp4",
            allow_network=True,
        )

    message = str(error.value)
    assert "private-path-token" not in message
    assert "private-query-token" not in message


def test_gateway_video_probe_does_not_run_without_explicit_enable():
    report = GatewayVideoProbe(
        _config(),
        urlopen_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    ).run("prompt")

    assert report["executed"] is False
    assert report["success"] is False
    assert report["blocked_reasons"] == ["Live gateway video probe is disabled."]


def test_mp4_validator_rejects_streamless_container(tmp_path):
    from factory.gateway_video import is_valid_mp4_file
    from tests.media_fixtures import STREAMLESS_MP4

    path = tmp_path / "streamless.mp4"
    path.write_bytes(STREAMLESS_MP4)

    assert is_valid_mp4_file(path) is False
