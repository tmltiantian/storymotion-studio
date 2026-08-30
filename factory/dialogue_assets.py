from __future__ import annotations

import hashlib
import json
import os
import subprocess
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .file_io import sha256_file, write_json_atomic
from .performance_card import PerformanceCard, PerformanceSheet, dialogue_id_for
from .schema import Episode, NARRATOR_ID


DIALOGUE_AUDIO_MANIFEST_SCHEMA = "motion-comic-factory.dialogue-audio.v1"


class DialogueAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class DialogueAudioAsset:
    dialogue_id: str
    speaker_id: str
    path: str
    sha256: str
    duration_seconds: float
    voice_id: str


@dataclass(frozen=True)
class DialogueAudioManifest:
    assets: tuple[DialogueAudioAsset, ...]
    path: str
    voiceover_audio: str
    voiceover_sha256: str
    schema_version: str = DIALOGUE_AUDIO_MANIFEST_SCHEMA

    @property
    def by_dialogue_id(self) -> Mapping[str, DialogueAudioAsset]:
        return MappingProxyType({asset.dialogue_id: asset for asset in self.assets})


def write_dialogue_audio_manifest(
    episode: Episode,
    sheet: PerformanceSheet,
    voiceover_audio: str | Path,
    output_dir: str | Path,
    *,
    provider_report_path: str | Path,
    command_runner: Callable[..., object] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> DialogueAudioManifest:
    source = Path(voiceover_audio).expanduser()
    if source.is_symlink() or not source.is_file():
        raise DialogueAudioError("missing final dialogue audio")
    source = source.resolve()
    source_sha256 = sha256_file(source)
    report = _read_completed_provider_report(provider_report_path, source_sha256)
    destination = Path(output_dir).expanduser()
    if destination.is_symlink():
        raise DialogueAudioError("dialogue audio directory cannot be a symlink")
    destination.mkdir(parents=True, exist_ok=True)

    assets: list[DialogueAudioAsset] = []
    assets_by_dialogue_id: dict[str, DialogueAudioAsset] = {}
    for card in sheet.cards:
        if not card.requires_visible_lipsync:
            continue
        existing = assets_by_dialogue_id.get(card.dialogue_id)
        if existing is not None:
            if existing.speaker_id != card.speaker_id:
                raise DialogueAudioError(
                    f"{card.micro_shot_id} dialogue speaker does not match"
                )
            continue
        cue = _matching_completed_cue(episode, card, report)
        asset_path = _cut_cue_wav(
            source,
            cue,
            destination,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
        )
        asset = DialogueAudioAsset(
            dialogue_id=card.dialogue_id,
            speaker_id=card.speaker_id,
            path=str(asset_path.resolve()),
            sha256=sha256_file(asset_path),
            duration_seconds=_probe_duration(asset_path),
            voice_id=str(cue["voice_id"]),
        )
        assets.append(asset)
        assets_by_dialogue_id[asset.dialogue_id] = asset

    manifest_path = destination / "dialogue_audio_manifest.json"
    payload = {
        "schema_version": DIALOGUE_AUDIO_MANIFEST_SCHEMA,
        "voiceover_audio": str(source),
        "voiceover_sha256": source_sha256,
        "assets": [asdict(asset) for asset in assets],
    }
    write_json_atomic(manifest_path, payload)
    return DialogueAudioManifest(
        assets=tuple(assets),
        path=str(manifest_path.resolve()),
        voiceover_audio=str(source),
        voiceover_sha256=source_sha256,
    )


def require_dialogue_audio(
    manifest: DialogueAudioManifest, card: PerformanceCard
) -> DialogueAudioAsset:
    if not card.requires_visible_lipsync:
        raise DialogueAudioError(f"{card.micro_shot_id} is not a visible speech card")
    asset = manifest.by_dialogue_id.get(card.dialogue_id)
    if asset is None:
        raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")
    if asset.speaker_id != card.speaker_id:
        raise DialogueAudioError(
            f"{card.micro_shot_id} dialogue speaker does not match"
        )
    return asset


def _read_completed_provider_report(
    provider_report_path: str | Path, source_sha256: str
) -> Mapping[str, Any]:
    path = Path(provider_report_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DialogueAudioError("missing final dialogue audio") from exc
    if not isinstance(payload, dict):
        raise DialogueAudioError("missing final dialogue audio")
    if payload.get("final_output_sha256") != source_sha256:
        raise DialogueAudioError(
            "final dialogue audio output does not match provider report"
        )
    cues = payload.get("completed_cues")
    if not isinstance(cues, list):
        raise DialogueAudioError("missing final dialogue audio")
    return payload


def _matching_completed_cue(
    episode: Episode, card: PerformanceCard, report: Mapping[str, Any]
) -> Mapping[str, Any]:
    source_text = _source_text_for_card(episode, card)
    cues = report["completed_cues"]
    matches = [
        cue
        for cue in cues
        if isinstance(cue, dict) and cue.get("dialogue_id") == card.dialogue_id
    ]
    if len(matches) != 1:
        raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")
    cue = matches[0]
    if cue.get("speaker_id") != card.speaker_id:
        raise DialogueAudioError(
            f"{card.micro_shot_id} dialogue speaker does not match final audio"
        )
    if cue.get("source_text_sha256") != _text_sha256(source_text):
        raise DialogueAudioError(
            f"{card.micro_shot_id} dialogue source text does not match final audio"
        )
    if cue.get("final_output_sha256") != report.get("final_output_sha256"):
        raise DialogueAudioError(
            f"{card.micro_shot_id} final dialogue audio output does not match provider report"
        )
    if not isinstance(cue.get("voice_id"), str) or not cue["voice_id"].strip():
        raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")
    start = _finite_seconds(cue.get("absolute_start_seconds"))
    end = _finite_seconds(cue.get("absolute_end_seconds"))
    if start < 0 or end <= start:
        raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")
    return cue


def _source_text_for_card(episode: Episode, card: PerformanceCard) -> str:
    for shot in episode.shots:
        for index, line in enumerate(shot.dialogue, start=1):
            if dialogue_id_for(shot.id, index) == card.dialogue_id:
                if line.speaker_id == NARRATOR_ID or line.speaker_id != card.speaker_id:
                    break
                return line.text
    raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")


def _cut_cue_wav(
    source: Path,
    cue: Mapping[str, Any],
    destination: Path,
    *,
    command_runner: Callable[..., object],
    ffmpeg_bin: str,
) -> Path:
    dialogue_id = str(cue["dialogue_id"])
    output = destination / f"{dialogue_id}.wav"
    if output.is_symlink():
        raise DialogueAudioError("dialogue audio output cannot be a symlink")
    temporary = output.with_name(f".{output.stem}.tmp.wav")
    start = _finite_seconds(cue["absolute_start_seconds"])
    duration = _finite_seconds(cue["absolute_end_seconds"]) - start
    try:
        command_runner(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration:.3f}",
                "-vn",
                "-ac",
                "1",
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
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise DialogueAudioError("ffmpeg did not create final dialogue audio")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _probe_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise DialogueAudioError(
            "final dialogue audio duration is unavailable"
        ) from exc
    if duration <= 0:
        raise DialogueAudioError("final dialogue audio duration is unavailable")
    return duration


def _finite_seconds(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DialogueAudioError("missing final dialogue audio") from exc
    if not result == result or result in {float("inf"), float("-inf")}:
        raise DialogueAudioError("missing final dialogue audio")
    return result


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
