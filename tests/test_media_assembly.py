import json
import shutil
import subprocess
from pathlib import Path

import pytest

from factory.media_assembly import (
    assemble_visual_track,
    build_final_mux_command,
    mux_final_audio,
)


def test_final_mux_command_pads_and_trims_without_shortest(tmp_path: Path) -> None:
    command = build_final_mux_command(
        tmp_path / "visual.mp4",
        tmp_path / "voice.m4a",
        tmp_path / "final.mp4",
        duration_seconds=7.5,
        subtitles=tmp_path / "subtitles.srt",
    )

    joined = " ".join(command)
    assert "-shortest" not in command
    assert "tpad=stop_mode=clone" in joined
    assert "atrim=duration=7.500" in joined
    assert "-t 7.500" in joined
    assert "mov_text" in command


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is unavailable")
def test_media_assembly_normalizes_mixed_clips_and_keeps_full_duration(
    tmp_path: Path,
) -> None:
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    audio = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=240x320:r=24:d=1",
            "-c:v", "mpeg4", str(clip_a),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=30:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_b),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "3", str(audio),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    visual = assemble_visual_track(
        [clip_a, clip_b],
        [1.25, 1.75],
        tmp_path / "visual.mp4",
        width=320,
        height=568,
        fps=30,
    )
    final = mux_final_audio(
        visual,
        audio,
        tmp_path / "final.mp4",
        duration_seconds=3.0,
    )

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-show_entries", "stream=codec_type,width,height,r_frame_rate",
            "-of", "json", str(final),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(probe.stdout)
    assert float(payload["format"]["duration"]) == pytest.approx(3.0, abs=0.08)
    assert [stream["codec_type"] for stream in payload["streams"]].count("audio") == 1
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (320, 568, "30/1")
