from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .dialogue_assets import DialogueAudioError, DialogueAudioManifest
from .file_io import sha256_file
from .performance_card import dialogue_id_for
from .schema import Episode, NARRATOR_ID


def write_shot_audio_assets(
    episode: Episode,
    voiceover_audio: str | Path,
    output_dir: str | Path,
    *,
    dialogue_manifest: DialogueAudioManifest | None = None,
    ffmpeg_bin: str = "ffmpeg",
    command_runner: Callable[..., object] = subprocess.run,
) -> dict[str, Path]:
    if dialogue_manifest is not None:
        return _legacy_aliases_from_dialogue_manifest(episode, dialogue_manifest)
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


def _legacy_aliases_from_dialogue_manifest(
    episode: Episode, manifest: DialogueAudioManifest
) -> dict[str, Path]:
    aliases: dict[str, Path] = {}
    for shot in episode.shots:
        dialogue_lines = [
            (dialogue_id_for(shot.id, index), line)
            for index, line in enumerate(shot.dialogue, start=1)
            if line.speaker_id != NARRATOR_ID
        ]
        if not dialogue_lines:
            continue
        if len(dialogue_lines) != 1:
            raise DialogueAudioError(
                f"{shot.id} cannot use one legacy parent-shot alias for multiple dialogue lines"
            )
        dialogue_id, line = dialogue_lines[0]
        asset = manifest.by_dialogue_id.get(dialogue_id)
        if asset is None:
            raise DialogueAudioError(f"{shot.id} missing final dialogue audio")
        if asset.speaker_id != line.speaker_id:
            raise DialogueAudioError(f"{shot.id} dialogue speaker does not match")
        path = Path(asset.path)
        if path.is_symlink() or not path.is_file() or sha256_file(path) != asset.sha256:
            raise DialogueAudioError(f"{shot.id} final dialogue audio is invalid")
        aliases[shot.id] = path
    return aliases
