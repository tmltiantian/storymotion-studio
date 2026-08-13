from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from factory.pet_sitcom import build_pet_sitcom_plan
from factory.pet_sitcom_audio_first import (
    PET_VOICES,
    PetSitcomAudioFirstError,
    PetVoice,
    build_pet_drive_audio,
    generate_pet_speech_assets,
    load_pet_speech_assets,
)


def _write_wav(
    path: Path,
    *,
    seconds: float = 1.35,
    rate: int = 48000,
    channels: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * channels * int(seconds * rate))


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, tmp_path / "pet-sitcom")


@pytest.fixture
def fake_tts():
    class FakeTTS:
        config = SimpleNamespace(
            resource_id="seed-tts-2.0",
            voice_type="zh_female_vv_uranus_bigtts",
        )

        def __init__(self) -> None:
            self.calls: list[SimpleNamespace] = []

        def synthesize(
            self,
            text: str,
            output_path: Path,
            *,
            voice_id: str,
            speech_rate: int,
            sample_rate: int,
        ) -> SimpleNamespace:
            self.calls.append(
                SimpleNamespace(
                    text=text,
                    voice_id=voice_id,
                    speech_rate=speech_rate,
                    sample_rate=sample_rate,
                )
            )
            _write_wav(Path(output_path), rate=sample_rate, channels=1)
            return SimpleNamespace(output_path=Path(output_path))

    return FakeTTS()


@pytest.fixture
def fake_audio_probe():
    def factory(*, duration_seconds: float):
        def probe(path: Path, *, required_stream: str):
            return SimpleNamespace(
                valid=True,
                duration_seconds=duration_seconds,
                audio_stream_count=1,
                video_stream_count=0,
            )

        return probe

    return factory


@pytest.fixture
def copying_runner():
    def runner(command: list[str], **kwargs):
        output = Path(command[-1])
        _write_wav(output)
        return subprocess.CompletedProcess(command, 0, "", "")

    return runner


@pytest.fixture
def command_recorder():
    class CommandRecorder:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def __call__(self, command: list[str], **kwargs):
            self.calls.append(command)
            _write_wav(Path(command[-1]), seconds=5.0)
            return subprocess.CompletedProcess(command, 0, "", "")

    return CommandRecorder()


def test_voice_map_is_immutable_and_preserves_approved_role_contrast():
    assert PET_VOICES == {
        "owner": PetVoice("zh_female_vv_uranus_bigtts", -4),
        "naitang": PetVoice("saturn_zh_female_tiaopigongzhu_tob", 2),
        "doubao": PetVoice("zh_female_meilinvyou_saturn_bigtts", 4),
    }
    assert isinstance(PET_VOICES, MappingProxyType)
    with pytest.raises(TypeError):
        PET_VOICES["owner"] = PetVoice("changed", 0)


def test_manifest_uses_real_tts_duration_without_atempo(
    plan, fake_tts, fake_audio_probe, copying_runner, monkeypatch
):
    monkeypatch.setattr(
        "factory.pet_sitcom_audio_first.probe_media",
        fake_audio_probe(duration_seconds=1.35),
    )

    report = generate_pet_speech_assets(
        plan,
        tts_client=fake_tts,
        allow_network=True,
        command_runner=copying_runner,
    )
    assets = load_pet_speech_assets(plan)

    assert report["success"] is True
    assert len(assets) == 8
    assert assets[0].absolute_start_seconds == pytest.approx(0.55)
    assert assets[0].absolute_end_seconds == pytest.approx(1.90)
    assert all(asset.output_sha256 for asset in assets)
    assert all(call.sample_rate == 24000 for call in fake_tts.calls)
    assert [call.speech_rate for call in fake_tts.calls] == [
        -4,
        2,
        4,
        4,
        -4,
        2,
        4,
        2,
    ]
    assert "atempo" not in " ".join(
        command
        for command in report["commands"]
    )


@pytest.fixture
def fake_assets(
    plan, fake_tts, fake_audio_probe, copying_runner, monkeypatch
):
    monkeypatch.setattr(
        "factory.pet_sitcom_audio_first.probe_media",
        fake_audio_probe(duration_seconds=1.35),
    )
    generate_pet_speech_assets(
        plan,
        tts_client=fake_tts,
        allow_network=True,
        command_runner=copying_runner,
    )
    return load_pet_speech_assets(plan)


def test_drive_audio_is_padded_not_retimed(
    plan, fake_assets, command_recorder
):
    path = build_pet_drive_audio(
        plan, "shot_04", command_runner=command_recorder
    )
    command = command_recorder.calls[0]

    assert path.name == "shot_04_drive.wav"
    assert "atempo" not in " ".join(command)
    assert "adelay=650|650" in " ".join(command)
    assert "atrim=duration=5" in " ".join(command)


def test_generation_rejects_symlinked_audio_directory(
    plan, fake_tts, copying_runner, tmp_path
):
    outside = tmp_path / "outside-audio"
    outside.mkdir()
    plan.output_dir.mkdir(parents=True)
    (plan.output_dir / "audio").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(PetSitcomAudioFirstError, match="symlink"):
        generate_pet_speech_assets(
            plan,
            tts_client=fake_tts,
            allow_network=True,
            command_runner=copying_runner,
        )


@pytest.mark.parametrize(
    "field",
    ("absolute_start_seconds", "absolute_end_seconds"),
)
def test_manifest_rejects_non_finite_absolute_timing(
    plan, fake_assets, field
):
    manifest = json.loads(plan.audio_manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0][field] = float("nan")
    plan.audio_manifest_path.write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(PetSitcomAudioFirstError, match="finite"):
        load_pet_speech_assets(plan)


def test_non_wav_tts_source_rejects_sample_rate_below_24khz(
    plan,
    fake_tts,
    fake_audio_probe,
    copying_runner,
    monkeypatch,
):
    def synthesize(text, output_path, **kwargs):
        Path(output_path).write_bytes(b"ID3-low-rate-provider-audio")
        return SimpleNamespace(output_path=Path(output_path))

    def ffprobe_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"streams": [{"sample_rate": "16000"}]}),
            "",
        )

    fake_tts.synthesize = synthesize
    monkeypatch.setattr(
        "factory.pet_sitcom_audio_first.probe_media",
        fake_audio_probe(duration_seconds=1.35),
    )
    monkeypatch.setattr(
        "factory.pet_sitcom_audio_first.subprocess.run",
        ffprobe_runner,
    )

    with pytest.raises(PetSitcomAudioFirstError, match="at least 24 kHz"):
        generate_pet_speech_assets(
            plan,
            tts_client=fake_tts,
            allow_network=True,
            command_runner=copying_runner,
        )


def test_provider_output_path_must_stay_in_current_temporary_directory(
    plan, fake_tts, copying_runner, tmp_path
):
    outside = tmp_path / "escaped-provider.wav"

    def synthesize(text, output_path, **kwargs):
        _write_wav(outside, rate=24000, channels=1)
        return SimpleNamespace(output_path=outside)

    fake_tts.synthesize = synthesize

    with pytest.raises(
        PetSitcomAudioFirstError, match="temporary directory"
    ):
        generate_pet_speech_assets(
            plan,
            tts_client=fake_tts,
            allow_network=True,
            command_runner=copying_runner,
        )


def test_provider_output_path_rejects_parent_traversal_from_temporary_directory(
    plan, fake_tts, copying_runner
):
    def synthesize(text, output_path, **kwargs):
        escaped = Path(output_path).parent / ".." / "escaped.wav"
        _write_wav(escaped, rate=24000, channels=1)
        return SimpleNamespace(output_path=escaped)

    fake_tts.synthesize = synthesize

    with pytest.raises(
        PetSitcomAudioFirstError, match="temporary directory"
    ):
        generate_pet_speech_assets(
            plan,
            tts_client=fake_tts,
            allow_network=True,
            command_runner=copying_runner,
        )


def test_drive_rejects_symlinked_state_before_reuse(
    plan, fake_assets, command_recorder, tmp_path
):
    drive = build_pet_drive_audio(
        plan, "shot_04", command_runner=command_recorder
    )
    state = drive.with_suffix(".state.json")
    outside = tmp_path / "outside-drive-state.json"
    outside.write_bytes(state.read_bytes())
    state.unlink()
    state.symlink_to(outside)

    with pytest.raises(PetSitcomAudioFirstError, match="symlink"):
        build_pet_drive_audio(
            plan, "shot_04", command_runner=command_recorder
        )
