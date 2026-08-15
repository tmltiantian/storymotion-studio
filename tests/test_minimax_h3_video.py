import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from factory.gateway_video import (
    GatewayVideoError,
    GatewayVideoTask,
    GatewayVideoTransientError,
)
from factory.gateway_video_batch import (
    GatewayVideoBatchError,
    render_gateway_video_single,
)
from factory.minimax_h3_video import (
    MiniMaxH3Client,
    MiniMaxH3Config,
    MiniMaxH3ImageInput,
    MiniMaxH3Result,
    _estimate_cost_yuan,
)
from tests.media_fixtures import VALID_VIDEO_MP4


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
        "api_key": "minimax-secret",
        "base_url": "https://api.minimaxi.com",
        "model": "MiniMax-H3",
        "poll_interval_seconds": 0,
        "max_wait_seconds": 10,
    }
    values.update(overrides)
    return MiniMaxH3Config(**values)


def _client(fake_urlopen, **config_overrides):
    return MiniMaxH3Client(
        _config(**config_overrides),
        urlopen_fn=fake_urlopen,
        sleep_fn=lambda _: None,
    )


def test_prepare_submission_builds_official_h3_v2_reference_payload():
    client = _client(lambda *_args, **_kwargs: None)

    submission = client.prepare_submission(
        "A black-and-white cat walks naturally into a warm wooden room.",
        images=["https://assets.example/cat.png"],
        audio="https://assets.example/line.wav",
        duration=4,
        ratio="9:16",
        resolution="768p",
        allow_network=True,
    )

    payload = json.loads(submission.request_body)
    assert submission.endpoint == "https://api.minimaxi.com/v2/video_generation"
    assert payload == {
        "model": "MiniMax-H3",
        "duration": 4,
        "resolution": "768P",
        "ratio": "9:16",
        "content": [
            {
                "type": "text",
                "text": "A black-and-white cat walks naturally into a warm wooden room.",
            },
            {
                "type": "image_url",
                "image_url": {"url": "https://assets.example/cat.png"},
                "role": "reference_image",
            },
            {
                "type": "audio_url",
                "audio_url": {"url": "https://assets.example/line.wav"},
                "role": "reference_audio",
            },
        ],
    }
    assert "minimax-secret" not in submission.request_body.decode("utf-8")


def test_prepare_submission_preserves_explicit_first_and_last_frame_roles():
    client = _client(lambda *_args, **_kwargs: None)

    submission = client.prepare_submission(
        "Connect the supplied keyframes with one physically continuous action.",
        images=[
            MiniMaxH3ImageInput("https://assets.example/first.png", "first_frame"),
            MiniMaxH3ImageInput("https://assets.example/last.png", "last_frame"),
        ],
        duration=6,
        ratio="adaptive",
        resolution="768P",
        allow_network=True,
    )

    image_content = json.loads(submission.request_body)["content"][1:]
    assert [item["role"] for item in image_content] == ["first_frame", "last_frame"]


def test_prepare_submission_accepts_parallel_provider_neutral_image_roles():
    client = _client(lambda *_args, **_kwargs: None)

    submission = client.prepare_submission(
        "Connect the supplied keyframes.",
        images=[
            "https://assets.example/first.png",
            "https://assets.example/cat.png",
        ],
        image_roles=["first_frame", "reference_image"],
        duration=6,
        ratio="adaptive",
        resolution="768P",
        allow_network=True,
    )

    image_content = json.loads(submission.request_body)["content"][1:]
    assert [item["role"] for item in image_content] == [
        "first_frame",
        "reference_image",
    ]


def test_h3_result_reports_unavoidable_native_audio_truthfully(tmp_path):
    result = MiniMaxH3Result(
        output_path=str(tmp_path / "clip.mp4"),
        model="MiniMax-H3",
        task_id="task",
        status="completed",
        poll_count=1,
        output_size_bytes=10,
        duration_seconds=4,
        source_host="cdn.example",
    )

    assert result.to_report()["native_audio_generated"] is True


def test_prepare_submission_rejects_duplicate_keyframe_roles():
    client = _client(lambda *_args, **_kwargs: None)

    with pytest.raises(GatewayVideoError, match="at most one first_frame"):
        client.prepare_submission(
            "A cat walks forward.",
            images=[
                MiniMaxH3ImageInput("https://assets.example/one.png", "first_frame"),
                MiniMaxH3ImageInput("https://assets.example/two.png", "first_frame"),
            ],
            duration=4,
            ratio="adaptive",
            resolution="768P",
            allow_network=True,
        )


def test_generate_polls_nested_h3_task_downloads_video_and_reports_usage(tmp_path):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/v2/video_generation"):
            return FakeResponse({"task_id": "h3-task-1"})
        if request.full_url.endswith("/v2/query/video_generation/h3-task-1"):
            return FakeResponse(
                {
                    "task": {
                        "task_id": "h3-task-1",
                        "status": "succeeded",
                        "content": {
                            "url": "https://cdn.example/h3.mp4",
                        },
                        "usage": {
                            "output_video_duration": 4,
                            "input_image_count": 1,
                        },
                    }
                }
            )
        assert request.full_url == "https://cdn.example/h3.mp4"
        return FakeResponse(
            VALID_VIDEO_MP4,
            headers={"Content-Length": str(len(VALID_VIDEO_MP4))},
        )

    output = tmp_path / "h3.mp4"
    result = _client(fake_urlopen).generate(
        "A cute cat raises one paw.",
        output,
        images=["https://assets.example/cat.png"],
        duration=4,
        ratio="9:16",
        resolution="768P",
        allow_network=True,
    )

    assert output.read_bytes() == VALID_VIDEO_MP4
    assert requests[0].get_header("Authorization") == "Bearer minimax-secret"
    assert result.task_id == "h3-task-1"
    assert result.status == "completed"
    assert result.poll_count == 1
    assert result.usage == {
        "output_video_duration": 4,
        "input_image_count": 1,
    }
    assert result.estimated_cost_yuan == 2.0
    report_text = json.dumps(result.to_report(), ensure_ascii=False)
    assert "minimax-secret" not in report_text


@pytest.mark.parametrize(
    ("duration", "resolution", "ratio", "message"),
    [
        (3, "768P", "9:16", "between 4 and 15"),
        (16, "768P", "9:16", "between 4 and 15"),
        (4, "720p", "9:16", "768P or 2K"),
        (4, "768P", "5:4", "aspect ratio"),
    ],
)
def test_h3_rejects_unsupported_generation_settings(
    duration,
    resolution,
    ratio,
    message,
):
    client = _client(lambda *_args, **_kwargs: None)

    with pytest.raises(GatewayVideoError, match=message):
        client.prepare_submission(
            "A cat moves naturally.",
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            allow_network=True,
        )


def test_h3_text_only_rejects_adaptive_ratio():
    client = _client(lambda *_args, **_kwargs: None)

    with pytest.raises(GatewayVideoError, match="text-only"):
        client.prepare_submission(
            "A cat moves naturally.",
            duration=4,
            ratio="adaptive",
            resolution="768P",
            allow_network=True,
        )


def test_h3_rejects_more_than_nine_reference_images():
    client = _client(lambda *_args, **_kwargs: None)

    with pytest.raises(GatewayVideoError, match="at most 9"):
        client.prepare_submission(
            "A cast of cats.",
            images=[f"https://assets.example/cat-{index}.png" for index in range(10)],
            duration=4,
            ratio="9:16",
            resolution="768P",
            allow_network=True,
        )


def test_h3_failure_message_redacts_api_key():
    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/v2/video_generation"):
            return FakeResponse({"task_id": "failed-task"})
        return FakeResponse(
            {
                "task": {
                    "task_id": "failed-task",
                    "status": "failed",
                    "error": {
                        "message": "request minimax-secret was rejected",
                    },
                }
            }
        )

    with pytest.raises(GatewayVideoError) as caught:
        _client(fake_urlopen).generate(
            "A cat moves naturally.",
            Path("unused.mp4"),
            duration=4,
            ratio="9:16",
            resolution="768P",
            allow_network=True,
        )

    assert "minimax-secret" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_shared_video_renderer_reports_minimax_provider_in_plan(tmp_path):
    report_path = tmp_path / "report.json"

    report = render_gateway_video_single(
        "A cat moves naturally.",
        tmp_path / "clip.mp4",
        _client(lambda *_args, **_kwargs: None),
        report_path,
        duration=4,
        ratio="9:16",
        resolution="768P",
        allow_network=False,
    )

    assert report["provider"] == "minimax"
    assert report["plan_ready"] is True
    assert report["executed"] is False


def test_h3_cost_estimate_accepts_real_output_seconds_usage_field():
    assert _estimate_cost_yuan(
        {"output_seconds": 6, "input_image_count": 0},
        resolution="768P",
        fallback_duration=0,
        fallback_image_count=0,
    ) == 3.0


def test_h3_does_not_retry_ambiguous_billable_post_with_curl():
    curl_calls = []

    def failing_urlopen(*_args, **_kwargs):
        raise URLError("response lost after submit")

    def forbidden_curl(*args, **kwargs):
        curl_calls.append((args, kwargs))
        raise AssertionError("ambiguous H3 POST must not be resubmitted")

    client = MiniMaxH3Client(
        _config(),
        urlopen_fn=failing_urlopen,
        sleep_fn=lambda _: None,
        curl_runner=forbidden_curl,
        curl_bin="curl",
        enable_curl_fallback=True,
    )

    with pytest.raises(GatewayVideoTransientError, match="response lost"):
        client.submit(
            "A cat raises one paw.",
            duration=4,
            ratio="9:16",
            resolution="768P",
            allow_network=True,
        )

    assert curl_calls == []


def test_h3_resumed_2k_task_restores_settings_for_cost_report(tmp_path):
    def fake_urlopen(request, timeout):
        if request.full_url.endswith("/v2/query/video_generation/resume-2k"):
            return FakeResponse(
                {
                    "task": {
                        "task_id": "resume-2k",
                        "status": "succeeded",
                        "content": {"url": "https://cdn.example/resume-2k.mp4"},
                        "usage": {"output_seconds": 4, "input_image_count": 0},
                    }
                }
            )
        return FakeResponse(
            VALID_VIDEO_MP4,
            headers={"Content-Length": str(len(VALID_VIDEO_MP4))},
        )

    client = _client(fake_urlopen)
    client.restore_task_settings(
        "resume-2k",
        resolution="2K",
        duration=4,
        image_count=0,
    )

    result = client.complete_task(
        GatewayVideoTask(task_id="resume-2k", status="queued"),
        tmp_path / "resume-2k.mp4",
        allow_network=True,
    )

    assert result.resolution == "2K"
    assert result.estimated_cost_yuan == 3.2


def test_shared_renderer_accepts_h3_remote_reference_audio_without_leaking_it(tmp_path):
    audio_url = "https://assets.example/private-line.wav?signature=secret"
    report_path = tmp_path / "report.json"

    report = render_gateway_video_single(
        "A cat speaks naturally.",
        tmp_path / "clip.mp4",
        _client(lambda *_args, **_kwargs: None),
        report_path,
        audio=audio_url,
        duration=4,
        ratio="9:16",
        resolution="768P",
        allow_network=False,
    )

    report_text = report_path.read_text(encoding="utf-8")
    assert report["plan_ready"] is True
    assert audio_url not in report_text
    assert "signature=secret" not in report_text


def test_h3_rejects_model_override_outside_adapter_contract():
    client = _client(
        lambda *_args, **_kwargs: None,
        model="MiniMax-Hailuo-2.3",
    )

    with pytest.raises(GatewayVideoError, match="only supports MiniMax-H3"):
        client.prepare_submission(
            "A cat moves naturally.",
            duration=4,
            ratio="9:16",
            resolution="768P",
            allow_network=True,
        )


def test_h3_submit_retry_guard_cannot_be_overridden():
    with pytest.raises(TypeError, match="retry_submit_with_curl"):
        MiniMaxH3Config(
            api_key="secret",
            base_url="https://api.minimaxi.com",
            model="MiniMax-H3",
            retry_submit_with_curl=True,
        )


def test_h3_canonicalizes_case_insensitive_official_model_name():
    client = _client(lambda *_args, **_kwargs: None, model="minimax-h3")

    submission = client.prepare_submission(
        "A cat moves naturally.",
        duration=4,
        ratio="9:16",
        resolution="768P",
        allow_network=True,
    )

    assert client.config.model == "MiniMax-H3"
    assert json.loads(submission.request_body)["model"] == "MiniMax-H3"


def test_shared_renderer_rejects_wrong_h3_model_during_dry_run(tmp_path):
    client = _client(
        lambda *_args, **_kwargs: None,
        model="MiniMax-Hailuo-2.3",
    )

    with pytest.raises(GatewayVideoBatchError, match="only supports MiniMax-H3"):
        render_gateway_video_single(
            "A cat moves naturally.",
            tmp_path / "clip.mp4",
            client,
            tmp_path / "report.json",
            duration=4,
            ratio="9:16",
            resolution="768P",
            allow_network=False,
        )
