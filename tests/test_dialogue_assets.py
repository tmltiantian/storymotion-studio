import hashlib
import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest

from factory.dialogue_assets import (
    DialogueAudioError,
    require_dialogue_audio,
    write_dialogue_audio_manifest,
)
from factory.performance_card import PerformanceCard, PerformanceSheet
from factory.schema import Character, DialogueLine, Episode, Shot


def _write_wav(path: Path, *, duration_seconds: float = 0.25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\x00\x00" * int(8_000 * duration_seconds))


@pytest.fixture
def episode() -> Episode:
    return Episode(
        project_id="dialogue_assets",
        title="Dialogue assets",
        language="en",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[Character("wukong", "Wukong", "lead", "alert", "armor", "low")],
        shots=[
            Shot(
                "s01",
                1,
                "Gate",
                "Wukong speaks.",
                "A gate.",
                "static",
                4.0,
                "tense",
                dialogue=[DialogueLine("wukong", "Open the gate.")],
                character_ids=["wukong"],
            )
        ],
    )


@pytest.fixture
def sheet() -> PerformanceSheet:
    return PerformanceSheet(
        project_id="dialogue_assets",
        cards=(
            PerformanceCard(
                micro_shot_id="micro_001",
                purpose="speak",
                speaker_id="wukong",
                dialogue_id="s01.dialogue_01",
                requires_visible_lipsync=True,
                entry_anchor_id="scene_gate",
                scene_keyframe_id="kf_gate",
                actor_id="wukong",
                target_id="",
                contact_point="",
                prop_hand="",
                start_beat="looks at the gate",
                main_beat="says the line",
                end_beat="holds position",
                negative_constraints=("no floating",),
            ),
        ),
    )


@pytest.fixture
def completed_voiceover(tmp_path: Path) -> tuple[Path, Path]:
    voiceover = tmp_path / "voiceover.m4a"
    _write_wav(voiceover, duration_seconds=1.0)
    output_hash = hashlib.sha256(voiceover.read_bytes()).hexdigest()
    report = tmp_path / "voiceover_provider_report.json"
    report.write_text(
        json.dumps(
            {
                "final_output_sha256": output_hash,
                "completed_cues": [
                    {
                        "dialogue_id": "s01.dialogue_01",
                        "source_text_sha256": hashlib.sha256(
                            b"Open the gate."
                        ).hexdigest(),
                        "speaker_id": "wukong",
                        "voice_id": "wukong-voice",
                        "absolute_start_seconds": 0.2,
                        "absolute_end_seconds": 0.45,
                        "final_output_sha256": output_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return voiceover, report


def fake_ffmpeg(command, **_kwargs):
    _write_wav(Path(command[-1]))


def test_manifest_records_final_asset_hash_and_rejects_speaker_mismatch(
    tmp_path: Path,
    episode: Episode,
    sheet: PerformanceSheet,
    completed_voiceover: tuple[Path, Path],
):
    voiceover, provider_report = completed_voiceover
    manifest = write_dialogue_audio_manifest(
        episode,
        sheet,
        voiceover,
        tmp_path / "dialogue_audio",
        provider_report_path=provider_report,
        command_runner=fake_ffmpeg,
    )

    asset = require_dialogue_audio(manifest, sheet.cards[0])

    assert asset.dialogue_id == "s01.dialogue_01"
    assert len(asset.sha256) == 64
    assert asset.duration_seconds > 0
    with pytest.raises(DialogueAudioError, match="speaker does not match"):
        require_dialogue_audio(manifest, replace(sheet.cards[0], speaker_id="yangjian"))


def test_manifest_rejects_a_visible_speaking_card_without_final_audio(
    tmp_path: Path, episode: Episode, sheet: PerformanceSheet
):
    with pytest.raises(DialogueAudioError, match="missing final dialogue audio"):
        write_dialogue_audio_manifest(
            episode,
            sheet,
            tmp_path / "missing.m4a",
            tmp_path / "out",
            provider_report_path=tmp_path / "missing-report.json",
        )


def test_manifest_rejects_changed_source_text_evidence(
    tmp_path: Path,
    episode: Episode,
    sheet: PerformanceSheet,
    completed_voiceover: tuple[Path, Path],
):
    voiceover, provider_report = completed_voiceover
    payload = json.loads(provider_report.read_text(encoding="utf-8"))
    payload["completed_cues"][0]["source_text_sha256"] = hashlib.sha256(
        b"A different line."
    ).hexdigest()
    provider_report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DialogueAudioError, match="source text does not match"):
        write_dialogue_audio_manifest(
            episode,
            sheet,
            voiceover,
            tmp_path / "out",
            provider_report_path=provider_report,
            command_runner=fake_ffmpeg,
        )


def test_manifest_rejects_changed_final_audio_evidence(
    tmp_path: Path,
    episode: Episode,
    sheet: PerformanceSheet,
    completed_voiceover: tuple[Path, Path],
):
    voiceover, provider_report = completed_voiceover
    _write_wav(voiceover, duration_seconds=0.75)

    with pytest.raises(DialogueAudioError, match="does not match provider report"):
        write_dialogue_audio_manifest(
            episode,
            sheet,
            voiceover,
            tmp_path / "out",
            provider_report_path=provider_report,
            command_runner=fake_ffmpeg,
        )


def test_require_dialogue_audio_rejects_a_tampered_asset_file(
    tmp_path: Path,
    episode: Episode,
    sheet: PerformanceSheet,
    completed_voiceover: tuple[Path, Path],
):
    voiceover, provider_report = completed_voiceover
    manifest = write_dialogue_audio_manifest(
        episode,
        sheet,
        voiceover,
        tmp_path / "dialogue_audio",
        provider_report_path=provider_report,
        command_runner=fake_ffmpeg,
    )
    _write_wav(Path(manifest.assets[0].path), duration_seconds=0.1)

    with pytest.raises(DialogueAudioError, match="final dialogue audio is invalid"):
        require_dialogue_audio(manifest, sheet.cards[0])


def test_manifest_rejects_a_cut_shorter_than_the_completed_cue(
    tmp_path: Path,
    episode: Episode,
    sheet: PerformanceSheet,
    completed_voiceover: tuple[Path, Path],
):
    voiceover, provider_report = completed_voiceover

    def truncated_ffmpeg(command, **_kwargs):
        _write_wav(Path(command[-1]), duration_seconds=0.1)

    with pytest.raises(DialogueAudioError, match="shorter than completed cue"):
        write_dialogue_audio_manifest(
            episode,
            sheet,
            voiceover,
            tmp_path / "dialogue_audio",
            provider_report_path=provider_report,
            command_runner=truncated_ffmpeg,
        )
