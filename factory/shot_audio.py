from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .schema import Episode, NARRATOR_ID


def write_shot_audio_assets(
    episode: Episode,
    voiceover_audio: str | Path,
    output_dir: str | Path,
    *,
    ffmpeg_bin: str = "ffmpeg",
    command_runner: Callable[..., object] = subprocess.run,
) -> dict[str, Path]:
    source = Path(voiceover_audio).expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError(f"Voiceover audio is missing: {source}")
    destination = Path(output_dir).expanduser()
    if destination.is_symlink():
        raise ValueError(f"Shot audio directory cannot be a symlink: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Path] = {}
    shot_start = 0.0
    for shot in episode.shots:
        has_visible_dialogue = any(
            line.speaker_id != NARRATOR_ID for line in shot.dialogue
        )
        if has_visible_dialogue:
            output = destination / f"{shot.id}.wav"
            if output.is_symlink():
                raise ValueError(f"Shot audio output cannot be a symlink: {output}")
            temporary = output.with_name(f".{output.stem}.tmp.wav")
            command_runner(
                [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(source),
                    "-ss",
                    f"{shot_start:.3f}",
                    "-t",
                    f"{shot.duration_seconds:.3f}",
                    "-vn",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(temporary),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if not temporary.is_file():
                raise RuntimeError(f"FFmpeg did not create shot audio: {temporary}")
            os.replace(temporary, output)
            assets[shot.id] = output
        shot_start += shot.duration_seconds
    return assets
