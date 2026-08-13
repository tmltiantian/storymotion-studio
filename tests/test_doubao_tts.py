import base64
import json
from pathlib import Path

import pytest

from factory.doubao_tts import (
    DoubaoTTSClient,
    DoubaoTTSConfig,
    DoubaoTTSTask,
    resolve_doubao_tts_config,
)
from tests.media_fixtures import VALID_AUDIO_MP3


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        content: bytes = b"",
        text: str = "",
        status_code: int = 200,
    ):
        self._payload = payload
        self.content = content
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, post_payloads, *, audio: bytes = VALID_AUDIO_MP3):
        self.post_payloads = list(post_payloads)
        self.audio = audio
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return FakeResponse(self.post_payloads.pop(0))

    def get(self, url, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return FakeResponse(content=self.audio)


def test_resolve_doubao_config_prefers_process_env(tmp_path: Path):
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    (openmontage / ".env").write_text(
        "DOUBAO_SPEECH_API_KEY=file-key\n"
        "DOUBAO_SPEECH_VOICE_TYPE=file-voice\n",
        encoding="utf-8",
    )
    config = {
        "workspace": str(tmp_path),
        "sources": {"openMontage": str(openmontage)},
    }

    resolved = resolve_doubao_tts_config(
        config,
        process_env={
            "DOUBAO_SPEECH_API_KEY": "process-key",
            "DOUBAO_SPEECH_VOICE_TYPE": "process-voice",
        },
    )

    assert resolved is not None
    assert resolved.api_key == "process-key"
    assert resolved.voice_type == "process-voice"
    assert resolved.source == "process"


def test_resolve_doubao_config_reads_role_voice_map(tmp_path: Path):
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    config = {
        "workspace": str(tmp_path),
        "sources": {"openMontage": str(openmontage)},
    }

    resolved = resolve_doubao_tts_config(
        config,
        process_env={
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "default-voice",
            "DOUBAO_SPEECH_VOICE_MAP": (
                '{"narrator":"narrator-voice","character_1":"lead-voice"}'
            ),
        },
    )

    assert resolved is not None
    assert resolved.voice_map == {
        "narrator": "narrator-voice",
        "character_1": "lead-voice",
    }


def test_resolve_doubao_config_accepts_verified_legacy_credentials(tmp_path: Path):
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    config = {
        "workspace": str(tmp_path),
        "sources": {"openMontage": str(openmontage)},
    }

    resolved = resolve_doubao_tts_config(
        config,
        process_env={
            "DOUBAO_TTS_APPID": "legacy-app",
            "DOUBAO_TTS_ACCESS_KEY": "legacy-access",
            "DOUBAO_TTS_SPEAKER": "legacy-voice",
        },
    )

    assert resolved is not None
    assert resolved.auth_mode == "app_access"
    assert resolved.api_key == ""
    assert resolved.app_id == "legacy-app"
    assert resolved.access_key == "legacy-access"
    assert resolved.voice_type == "legacy-voice"


def test_legacy_streaming_synthesize_writes_valid_audio_and_safe_metadata(
    tmp_path: Path,
):
    class StreamingSession:
        def __init__(self):
            self.posts = []

        def post(self, url, **kwargs):
            self.posts.append({"url": url, **kwargs})
            encoded = base64.b64encode(VALID_AUDIO_MP3).decode("ascii")
            return FakeResponse(
                text=(
                    json.dumps({"code": 0, "data": encoded})
                    + "\n"
                    + json.dumps({"code": 20000000})
                )
            )

    session = StreamingSession()
    client = DoubaoTTSClient(
        DoubaoTTSConfig(
            api_key="",
            voice_type="legacy-voice",
            source="process",
            app_id="legacy-app",
            access_key="legacy-access",
            auth_mode="app_access",
        ),
        session=session,
    )

    result = client.synthesize("别急", tmp_path / "clip.mp3", speech_rate=-5)

    assert result.output_path.read_bytes() == VALID_AUDIO_MP3
    assert client.supports_async_tasks is False
    assert session.posts[0]["headers"]["X-Api-App-Id"] == "legacy-app"
    assert session.posts[0]["headers"]["X-Api-Access-Key"] == "legacy-access"
    assert session.posts[0]["json"]["req_params"]["speaker"] == "legacy-voice"
    metadata = result.metadata_path.read_text(encoding="utf-8")
    assert "legacy-app" not in metadata
    assert "legacy-access" not in metadata
    assert json.loads(metadata)["speech_rate"] == -5


def test_synthesize_submits_polls_downloads_and_writes_metadata(tmp_path: Path):
    session = FakeSession(
        [
            {"code": 20000000, "data": {"task_id": "task-1"}},
            {
                "code": 20000000,
                "data": {
                    "task_status": 2,
                    "audio_url": "https://audio.test/clip.mp3",
                    "sentences": [{"text": "别急", "words": []}],
                },
            },
        ]
    )
    config = DoubaoTTSConfig(
        api_key="secret",
        voice_type="zh_female_vv_uranus_bigtts",
        source="process",
    )
    client = DoubaoTTSClient(config, session=session, sleep=lambda _: None)

    result = client.synthesize("别急", tmp_path / "clip.mp3")

    assert result.output_path.read_bytes() == VALID_AUDIO_MP3
    assert result.request_id
    assert result.task_id == "task-1"
    assert result.sentences[0]["text"] == "别急"
    assert json.loads(result.metadata_path.read_text(encoding="utf-8"))["data"]["task_status"] == 2
    assert session.posts[0]["headers"]["X-Api-Key"] == "secret"
    assert session.posts[0]["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert session.posts[0]["json"]["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
    assert session.posts[1]["json"] == {"task_id": "task-1"}
    assert session.gets[0]["url"] == "https://audio.test/clip.mp3"


def test_submit_and_complete_task_can_resume_without_second_submission(tmp_path: Path):
    session = FakeSession(
        [
            {"code": 20000000, "data": {"task_id": "task-1"}},
            {
                "code": 20000000,
                "data": {
                    "task_status": 2,
                    "audio_url": "https://audio.test/clip.mp3",
                    "sentences": [],
                },
            },
        ]
    )
    client = DoubaoTTSClient(
        DoubaoTTSConfig(api_key="secret", voice_type="voice", source="process"),
        session=session,
        sleep=lambda _: None,
    )

    task = client.submit("别急", voice_id="lead-voice", request_id="request-1")
    resumed = DoubaoTTSTask(task_id=task.task_id, request_id=task.request_id)
    result = client.complete_task(resumed, tmp_path / "clip.mp3")

    assert result.task_id == "task-1"
    assert len(session.posts) == 2
    assert session.posts[0]["json"]["req_params"]["speaker"] == "lead-voice"
    assert session.posts[1]["json"] == {"task_id": "task-1"}


def test_invalid_download_preserves_previous_audio_and_metadata(tmp_path: Path):
    session = FakeSession(
        [
            {
                "code": 20000000,
                "data": {
                    "task_status": 2,
                    "audio_url": "https://audio.test/clip.mp3",
                    "sentences": [],
                },
            }
        ],
        audio=b"not-audio",
    )
    client = DoubaoTTSClient(
        DoubaoTTSConfig(api_key="secret", voice_type="voice", source="process"),
        session=session,
        sleep=lambda _: None,
    )
    output = tmp_path / "clip.mp3"
    metadata = tmp_path / "clip.mp3.json"
    output.write_bytes(b"last-good-audio")
    metadata.write_text('{"last":"good"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="valid audio"):
        client.complete_task(
            DoubaoTTSTask(task_id="task-1", request_id="request-1"),
            output,
            metadata_path=metadata,
        )

    assert output.read_bytes() == b"last-good-audio"
    assert json.loads(metadata.read_text(encoding="utf-8")) == {"last": "good"}


def test_synthesize_redacts_api_key_from_provider_errors(tmp_path: Path):
    class ErrorSession:
        def post(self, url, **kwargs):
            return FakeResponse(
                {"code": 401, "message": "invalid secret credential"},
                status_code=401,
            )

    config = DoubaoTTSConfig(
        api_key="secret",
        voice_type="voice",
        source="process",
    )
    client = DoubaoTTSClient(config, session=ErrorSession(), sleep=lambda _: None)

    with pytest.raises(RuntimeError) as exc_info:
        client.synthesize("别急", tmp_path / "clip.mp3")

    assert "secret" not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)


def test_synthesize_redacts_api_key_from_failed_tasks(tmp_path: Path):
    session = FakeSession(
        [
            {"code": 20000000, "data": {"task_id": "task-1"}},
            {
                "code": 20000000,
                "message": "task rejected for secret",
                "data": {"task_status": 3},
            },
        ]
    )
    config = DoubaoTTSConfig(api_key="secret", voice_type="voice", source="process")
    client = DoubaoTTSClient(config, session=session, sleep=lambda _: None)

    with pytest.raises(RuntimeError) as exc_info:
        client.synthesize("别急", tmp_path / "clip.mp3")

    assert "secret" not in str(exc_info.value)
    assert "[redacted]" in str(exc_info.value)
