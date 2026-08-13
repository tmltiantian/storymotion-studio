from pathlib import Path

from factory.media_validation import probe_media
from tests.media_fixtures import STREAMLESS_MP4, VALID_VIDEO_MP4


def test_probe_media_accepts_real_video_stream(tmp_path: Path):
    path = tmp_path / "valid.mp4"
    path.write_bytes(VALID_VIDEO_MP4)

    result = probe_media(path, required_stream="video")

    assert result.valid is True
    assert result.video_stream_count == 1
    assert result.duration_seconds > 0


def test_probe_media_rejects_box_only_mp4_without_streams(tmp_path: Path):
    path = tmp_path / "streamless.mp4"
    path.write_bytes(STREAMLESS_MP4)

    result = probe_media(path, required_stream="video")

    assert result.valid is False
    assert "video stream" in result.error
