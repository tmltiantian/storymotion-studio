from __future__ import annotations

import subprocess
from pathlib import Path

from .schema import Episode


def episode_duration_seconds(episode: Episode) -> float:
    return sum(shot.duration_seconds for shot in episode.shots)


def build_placeholder_ffmpeg_command(
    *,
    subtitles_path: str | Path,
    output_path: str | Path,
    duration_seconds: float,
    resolution: str,
    fps: int,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x111827:s={resolution}:r={fps}:d={duration_seconds:.3f}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-i",
        str(subtitles_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-c:s",
        "mov_text",
        "-shortest",
        str(output_path),
    ]


def render_placeholder_video(
    episode: Episode,
    subtitles_path: str | Path,
    output_path: str | Path,
    fps: int = 30,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_placeholder_ffmpeg_command(
        subtitles_path=subtitles_path,
        output_path=output,
        duration_seconds=episode_duration_seconds(episode),
        resolution=episode.target_resolution,
        fps=fps,
        ffmpeg_bin=ffmpeg_bin,
    )
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return output
