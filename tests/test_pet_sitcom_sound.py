from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import signal
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.pet_sitcom import build_pet_sitcom_plan


APPROVAL_SCHEMA = "motion-comic-factory.pet-sitcom-music-approval.v1"
SOUND_SCHEMA = "motion-comic-factory.pet-sitcom-sound-design.v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_pcm_wav(
    path: Path,
    *,
    seconds: float,
    sample_rate: int = 44_100,
    channels: int = 2,
    sample: bytes = b"\x20\x00",
    explicit_layout: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(
            sample * channels * int(round(seconds * sample_rate))
        )
    if explicit_layout and channels == 2:
        _write_stereo_channel_mask(path)


def _write_stereo_channel_mask(path: Path) -> None:
    payload = path.read_bytes()
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    offset = 12
    chunks: list[tuple[bytes, bytes]] = []
    while offset + 8 <= len(payload):
        chunk_id = payload[offset : offset + 4]
        size = struct.unpack_from("<I", payload, offset + 4)[0]
        start = offset + 8
        chunks.append((chunk_id, payload[start : start + size]))
        offset = start + size + (size & 1)
    result = bytearray(b"RIFF\x00\x00\x00\x00WAVE")
    for chunk_id, body in chunks:
        if chunk_id == b"fmt ":
            (
                format_tag,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", body)
            assert format_tag == 1
            assert channels == 2
            body = struct.pack(
                "<HHIIHHHHI",
                0xFFFE,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
                22,
                bits_per_sample,
                0x3,
            ) + bytes.fromhex("0100000000001000800000aa00389b71")
        result.extend(chunk_id)
        result.extend(struct.pack("<I", len(body)))
        result.extend(body)
        if len(body) & 1:
            result.append(0)
    struct.pack_into("<I", result, 4, len(result) - 8)
    path.write_bytes(result)


def _approval_path(source: Path) -> Path:
    return Path(f"{source}.approval.json")


def _dummy_staging(path: Path, marker: bytes = b"new") -> Path:
    path.mkdir(parents=True)
    for name in (
        "music",
        "room_tone",
        "bag_rustle",
        "tail_floor_rustle",
        "light_paw_steps",
        "mirror_slide",
        "ending_button",
    ):
        (path / f"{name}.wav").write_bytes(marker + name.encode())
    return path


def _write_approval(source: Path, **updates: object) -> Path:
    path = _approval_path(source)
    payload = {
        "schema_version": APPROVAL_SCHEMA,
        "source_path": str(source.resolve()),
        "source_sha256": _sha256(source),
        "reviewed": True,
        "approved": True,
        "not_harsh": True,
        "not_repetitive": True,
        "dialogue_compatible": True,
        "reviewed_at": "2026-07-27T08:00:00+00:00",
    }
    payload.update(updates)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class FakeSoundRunner:
    def __init__(
        self,
        *,
        fail_ffmpeg_call: int | None = None,
        source_codec_name: str = "pcm_s16le",
        stem_codec_name: str = "pcm_s16le",
    ) -> None:
        self.commands: list[list[str]] = []
        self.ffmpeg_calls = 0
        self.fail_ffmpeg_call = fail_ffmpeg_call
        self.source_codec_name = source_codec_name
        self.stem_codec_name = stem_codec_name

    def __call__(self, command, **kwargs):
        command = [str(item) for item in command]
        self.commands.append(command)
        tool = Path(command[0]).name
        if tool == "ffprobe":
            path = Path(command[-1])
            with wave.open(str(path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                payload = {
                    "format": {"duration": str(duration)},
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": (
                                self.source_codec_name
                                if path.name == "approved.wav"
                                or ".source-snapshot-" in str(path)
                                else self.stem_codec_name
                            ),
                            "sample_rate": str(audio.getframerate()),
                            "channels": audio.getnchannels(),
                            "channel_layout": (
                                "stereo"
                                if audio.getnchannels() == 2
                                else "mono"
                            ),
                            "duration": str(duration),
                        }
                    ],
                }
            return SimpleNamespace(
                stdout=json.dumps(payload),
                stderr="",
                returncode=0,
            )
        if tool != "ffmpeg":
            raise AssertionError(f"unexpected sound command: {command}")
        rendered = " ".join(command)
        if "ebur128=peak=true" in rendered:
            path = Path(command[command.index("-i") + 1])
            start = (
                float(command[command.index("-ss") + 1])
                if "-ss" in command
                else 0.0
            )
            target = {
                "music.wav": (
                    -31.0
                    if start == 0.0
                    else (-34.0 if start == 26.5 else -30.0)
                ),
                "room_tone.wav": -42.0,
            }.get(path.name, -30.0)
            return SimpleNamespace(
                stdout="",
                stderr=f"Integrated loudness:\n  I: {target:.1f} LUFS\n",
                returncode=0,
            )
        if "astats=metadata=1:reset=0" in rendered:
            path = Path(command[command.index("-i") + 1])
            target = {
                "bag_rustle.wav": -30.0,
                "tail_floor_rustle.wav": -32.0,
                "light_paw_steps.wav": -34.0,
                "mirror_slide.wav": -29.0,
                "ending_button.wav": -30.0,
            }[path.name]
            return SimpleNamespace(
                stdout="",
                stderr=f"RMS level dB: {target:.1f}\n",
                returncode=0,
            )
        self.ffmpeg_calls += 1
        if self.ffmpeg_calls == self.fail_ffmpeg_call:
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="forced sound render failure",
            )
        duration = float(command[command.index("-t") + 1])
        _write_pcm_wav(
            Path(command[-1]),
            seconds=duration,
            sample_rate=48_000,
            channels=2,
        )
        return SimpleNamespace(stdout="", stderr="", returncode=0)


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, output_dir=tmp_path / "pet-case")


@pytest.fixture
def approved_music(plan) -> Path:
    source = plan.output_dir / "assets" / "music" / "approved.wav"
    _write_pcm_wav(source, seconds=54.25)
    _write_approval(source)
    return source


def test_sound_design_requires_non_looped_music_at_least_54_seconds(
    plan,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    source = plan.output_dir / "assets" / "music" / "short.wav"
    _write_pcm_wav(source, seconds=53.75)
    _write_approval(source)

    with pytest.raises(PetSoundError, match="at least 54"):
        prepare_pet_sound_design(
            plan,
            music_source=source,
            command_runner=FakeSoundRunner(),
        )


def test_sound_manifest_has_three_story_cues_and_four_foley_events(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import prepare_pet_sound_design

    manifest = json.loads(
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(),
        ).read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == SOUND_SCHEMA
    assert [
        (cue["start"], cue["end"], cue["name"])
        for cue in manifest["music_cues"]
    ] == [
        (0.0, 26.5, "light_interrogation"),
        (26.5, 37.4, "surveillance_investigation"),
        (37.4, 54.0, "comic_reveal"),
    ]
    assert [event["name"] for event in manifest["foley"]] == [
        "bag_rustle",
        "tail_floor_rustle",
        "light_paw_steps",
        "mirror_slide",
    ]
    assert manifest["dialogue_fades"] == [
        {"start": 48.8, "end": 49.9, "reduction_db": 8.0},
        {"start": 50.0, "end": 53.6, "reduction_db": 8.0},
    ]
    assert manifest["ending_button"] == {
        "start": 53.6,
        "end": 54.0,
        "target_rms_dbfs": -30.0,
        "tolerance_db": 1.5,
        "stem": "ending_button",
    }


def test_sound_design_never_loops_music_and_binds_every_stem(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import prepare_pet_sound_design

    runner = FakeSoundRunner()
    manifest = json.loads(
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=runner,
        ).read_text(encoding="utf-8")
    )
    rendered = "\n".join(" ".join(command) for command in runner.commands)

    assert "-stream_loop" not in rendered
    assert "aloop" not in rendered
    assert manifest["source"]["looped"] is False
    assert set(manifest["stems"]) == {
        "music",
        "room_tone",
        "bag_rustle",
        "tail_floor_rustle",
        "light_paw_steps",
        "mirror_slide",
        "ending_button",
    }
    content_root = manifest["stems_content_root_sha256"]
    expected_root = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / content_root
    ).resolve()
    for name, stem in manifest["stems"].items():
        assert stem["source_sha256"] == manifest["source"]["sha256"]
        assert stem["approval_sha256"] == manifest["approval"]["sha256"]
        assert stem["config_sha256"] == manifest["config_sha256"]
        assert stem["binding_sha256"] == manifest["binding_sha256"]
        assert stem["codec_type"] == "audio"
        assert stem["codec_name"] == "pcm_s16le"
        assert stem["sample_rate"] == 48_000
        assert stem["channels"] == 2
        assert stem["channel_layout"] == "stereo"
        path = Path(stem["path"])
        assert path.parent == expected_root
        assert path.name == f"{name}.wav"
        assert path.is_file()
        assert stem["sha256"] == _sha256(path)
    assert re.fullmatch(
        r"[0-9a-f]{64}",
        content_root,
    )


def test_sound_reuse_recomputes_files_without_rerendering(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    first = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    render_count = runner.ffmpeg_calls
    second = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    loaded = load_pet_sound_design(plan, command_runner=runner)

    assert first == second
    assert runner.ffmpeg_calls == render_count == 7
    assert loaded["binding_sha256"] == json.loads(
        first.read_text()
    )["binding_sha256"]


def test_sound_binding_changes_for_every_source_metadata_field(plan):
    import factory.pet_sitcom_sound as sound

    source = plan.output_dir / "approved.wav"
    metadata = {
        "duration_seconds": 54.25,
        "stream_duration_seconds": 54.25,
        "sample_rate": 44_100,
        "channels": 2,
        "codec_type": "audio",
        "codec_name": "pcm_s16le",
        "channel_layout": "stereo",
    }
    arguments = {
        "plan": plan,
        "plan_sha256": "1" * 64,
        "source": source,
        "source_sha256": "2" * 64,
        "source_metadata": metadata,
        "approval_sha256": "3" * 64,
        "config_sha256": "4" * 64,
    }
    original = sound._binding_sha256(
        sound._binding_base(**arguments),
        "5" * 64,
    )
    mutations = {
        "duration_seconds": 54.5,
        "stream_duration_seconds": 54.5,
        "sample_rate": 96_000,
        "channels": 1,
        "codec_type": "data",
        "codec_name": "ac3",
        "channel_layout": "mono",
    }

    for field, value in mutations.items():
        changed = {**metadata, field: value}
        binding = sound._binding_sha256(
            sound._binding_base(
                **{**arguments, "source_metadata": changed}
            ),
            "5" * 64,
        )
        assert binding != original, field


def test_sound_prepare_and_load_accept_nonempty_ac3_source_codec(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner(source_codec_name="ac3")
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    loaded = load_pet_sound_design(plan, command_runner=runner)

    assert json.loads(manifest_path.read_text())["source"]["codec_name"] == "ac3"
    assert loaded["source"]["codec_name"] == "ac3"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_seconds", 54.5),
        ("stream_duration_seconds", 54.5),
        ("sample_rate", 96_000),
        ("codec_name", "ac3"),
    ],
)
def test_sound_load_rejects_legal_source_metadata_tamper_via_binding(
    field,
    value,
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["source"][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def matching_probe(command, **kwargs):
        result = runner(command, **kwargs)
        path = Path(command[-1])
        if (
            Path(command[0]).name != "ffprobe"
            or (
                path.name != "approved.wav"
                and ".source-snapshot-" not in str(path)
            )
        ):
            return result
        payload = json.loads(result.stdout)
        if field == "duration_seconds":
            payload["format"]["duration"] = str(value)
        elif field == "stream_duration_seconds":
            payload["streams"][0]["duration"] = str(value)
        else:
            payload["streams"][0][field] = (
                str(value) if field == "sample_rate" else value
            )
        return SimpleNamespace(
            stdout=json.dumps(payload),
            stderr="",
            returncode=0,
        )

    with pytest.raises(PetSoundError, match="binding hash"):
        load_pet_sound_design(plan, command_runner=matching_probe)


def test_legacy_v2_binding_without_source_metadata_fails_closed(
    plan,
    approved_music,
):
    import factory.pet_sitcom_sound as sound

    runner = FakeSoundRunner()
    manifest_path = sound.prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text())
    legacy_base = {
        "schema_version": sound.SOUND_DESIGN_SCHEMA,
        "project_id": plan.project_id,
        "plan_sha256": manifest["plan_sha256"],
        "source_path": manifest["source"]["path"],
        "source_sha256": manifest["source"]["sha256"],
        "approval_sha256": manifest["approval"]["sha256"],
        "config_sha256": manifest["config_sha256"],
    }
    legacy_binding = sound._binding_sha256(
        legacy_base,
        manifest["stems_content_root_sha256"],
    )
    manifest["binding_sha256"] = legacy_binding
    for stem in manifest["stems"].values():
        stem["binding_sha256"] = legacy_binding
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(sound.PetSoundError, match="binding hash"):
        sound.load_pet_sound_design(plan, command_runner=runner)


def test_sound_manifest_rejects_explicit_non_stereo_layout(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    real_call = runner.__call__

    def conflicting_layout(command, **kwargs):
        result = real_call(command, **kwargs)
        if Path(command[0]).name != "ffprobe":
            return result
        path = Path(command[-1])
        if path.name != "music.wav":
            return result
        payload = json.loads(result.stdout)
        payload["streams"][0]["channel_layout"] = "5.1"
        return SimpleNamespace(stdout=json.dumps(payload), stderr="")

    with pytest.raises(PetSoundError, match="stereo channel layout"):
        load_pet_sound_design(
            plan,
            command_runner=conflicting_layout,
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value.update(source_sha256="0" * 64), "source hash"),
        (lambda value: value.update(approved=False), "approved=true"),
        (lambda value: value.update(dialogue_compatible=False), "dialogue"),
        (lambda value: value.update(extra="not allowed"), "unknown field"),
    ],
)
def test_music_approval_is_exact_and_current(
    plan,
    approved_music,
    mutation,
    expected,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    path = _approval_path(approved_music)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetSoundError, match=expected):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(),
        )


def test_music_source_and_approval_reject_symlinks(
    plan,
    approved_music,
    tmp_path,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    linked_source = plan.output_dir / "assets" / "music" / "linked.wav"
    linked_source.symlink_to(approved_music)
    with pytest.raises(PetSoundError, match="symlink"):
        prepare_pet_sound_design(
            plan,
            music_source=linked_source,
            command_runner=FakeSoundRunner(),
        )

    outside = tmp_path / "outside-approval.json"
    shutil.copyfile(_approval_path(approved_music), outside)
    _approval_path(approved_music).unlink()
    _approval_path(approved_music).symlink_to(outside)
    with pytest.raises(PetSoundError, match="symlink"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(),
        )


def test_sound_manifest_rejects_tampered_stem(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    music = Path(manifest["stems"]["music"]["path"])
    music.write_bytes(music.read_bytes() + b"tampered")

    with pytest.raises(PetSoundError, match="stem.*hash"):
        load_pet_sound_design(plan, command_runner=runner)


def test_sound_manifest_rejects_noncanonical_stem_alias(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    music = Path(manifest["stems"]["music"]["path"])
    manifest["stems"]["music"]["path"] = str(
        music.parent / ".." / music.parent.name / music.name
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PetSoundError, match="canonical"):
        load_pet_sound_design(plan, command_runner=runner)


def test_sound_manifest_recomputes_content_root_from_actual_stems(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stems_content_root_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PetSoundError, match="content root"):
        load_pet_sound_design(plan, command_runner=runner)


def test_sound_manifest_only_stem_hash_edit_is_rejected(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import (
        PetSoundError,
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    runner = FakeSoundRunner()
    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stems"]["music"]["sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PetSoundError, match="stem music hash"):
        load_pet_sound_design(plan, command_runner=runner)


def test_sound_prepare_rejects_non_pcm_rendered_stem(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    with pytest.raises(PetSoundError, match="pcm_s16le"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(stem_codec_name="adpcm_ima_wav"),
        )


def test_sound_prepare_rejects_short_audio_stream_in_long_container(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    runner = FakeSoundRunner()

    def short_stream_runner(command, **kwargs):
        result = runner(command, **kwargs)
        if (
            Path(command[0]).name == "ffprobe"
            and Path(command[-1]).name == "music.wav"
        ):
            payload = json.loads(result.stdout)
            payload["streams"][0]["duration"] = "1.0"
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return result

    with pytest.raises(PetSoundError, match="exact-duration"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=short_stream_runner,
        )


def test_sound_prepare_rejects_short_source_stream_in_long_container(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    runner = FakeSoundRunner()

    def short_source_stream(command, **kwargs):
        result = runner(command, **kwargs)
        if (
            Path(command[0]).name == "ffprobe"
            and Path(command[-1]) == approved_music
        ):
            payload = json.loads(result.stdout)
            payload["streams"][0]["duration"] = "1.0"
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return result

    with pytest.raises(PetSoundError, match="at least 54"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=short_source_stream,
        )


@pytest.mark.parametrize("target", ["source", "stem"])
def test_sound_rejects_missing_explicit_channel_layout(
    plan,
    approved_music,
    target,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    runner = FakeSoundRunner()

    def missing_layout(command, **kwargs):
        result = runner(command, **kwargs)
        if Path(command[0]).name != "ffprobe":
            return result
        path = Path(command[-1])
        is_target = (
            target == "source" and path == approved_music
        ) or (
            target == "stem"
            and path.name == "music.wav"
            and ".render-" in str(path)
        )
        if is_target:
            payload = json.loads(result.stdout)
            payload["streams"][0].pop("channel_layout")
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return result

    with pytest.raises(PetSoundError, match="stereo"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=missing_layout,
        )


def test_sound_source_rejects_any_extra_non_audio_stream(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    runner = FakeSoundRunner()

    def extra_stream(command, **kwargs):
        result = runner(command, **kwargs)
        if (
            Path(command[0]).name == "ffprobe"
            and Path(command[-1]) == approved_music
        ):
            payload = json.loads(result.stdout)
            payload["streams"].append(
                {"codec_type": "subtitle", "codec_name": "mov_text"}
            )
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return result

    with pytest.raises(PetSoundError, match="exactly one audio stream"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=extra_stream,
        )


def test_sound_ffmpeg_reads_private_verified_music_snapshot(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import prepare_pet_sound_design

    runner = FakeSoundRunner()
    prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=runner,
    )
    music_render = next(
        command
        for command in runner.commands
        if Path(command[-1]).name == "music.wav"
    )
    consumed = Path(music_render[music_render.index("-i") + 1])

    assert consumed != approved_music
    assert consumed.name == "approved-music.wav"
    assert consumed.parent.parent == (
        plan.output_dir / "audio" / "sound_design"
    )
    assert not consumed.exists()


def test_sound_fails_closed_if_music_changes_before_snapshot_copy(
    plan,
    approved_music,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    original = sound._copy_verified_snapshot

    def replace_before_copy(source, destination, expected_sha256):
        source.write_bytes(source.read_bytes() + b"changed")
        return original(source, destination, expected_sha256)

    monkeypatch.setattr(
        sound,
        "_copy_verified_snapshot",
        replace_before_copy,
    )
    with pytest.raises(sound.PetSoundError, match="changed during snapshot"):
        sound.prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(),
        )


def test_sound_manifest_post_replace_fsync_failure_is_committed_success(
    plan,
    approved_music,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    real_fsync_dir = sound._fsync_dir

    def fail_manifest_dir(path):
        if (
            Path(path) == plan.output_dir
            and sound._manifest_path(plan).is_file()
        ):
            raise OSError("forced manifest directory fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(sound, "_fsync_dir", fail_manifest_dir)
    manifest_path = sound.prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=FakeSoundRunner(),
    )

    assert manifest_path.is_file()
    assert json.loads(manifest_path.read_text())["schema_version"] == SOUND_SCHEMA


def test_prepare_sound_swallows_cleanup_interrupt_after_pointer_commit(
    plan,
    approved_music,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    real_rmtree = sound.shutil.rmtree
    interrupted = False

    def interrupt_post_commit(path, *args, **kwargs):
        nonlocal interrupted
        if (
            not interrupted
            and sound._manifest_path(plan).is_file()
            and Path(path).name.startswith(".source-snapshot-")
        ):
            interrupted = True
            raise KeyboardInterrupt
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(sound.shutil, "rmtree", interrupt_post_commit)
    manifest_path = sound.prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=FakeSoundRunner(),
    )

    assert interrupted is True
    assert manifest_path == sound._manifest_path(plan)
    loaded = sound.load_pet_sound_design(
        plan,
        command_runner=FakeSoundRunner(),
    )
    assert loaded["schema_version"] == SOUND_SCHEMA


def test_sound_directory_creation_fsyncs_each_child_and_parent(
    plan,
    approved_music,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    calls: list[Path] = []
    real_fsync_dir = sound._fsync_dir

    def record_fsync(path):
        calls.append(Path(path))
        return real_fsync_dir(path)

    monkeypatch.setattr(sound, "_fsync_dir", record_fsync)
    manifest_path = sound.prepare_pet_sound_design(
        plan,
        music_source=approved_music,
        command_runner=FakeSoundRunner(),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / manifest["stems_content_root_sha256"]
    )
    created_edges = (
        (plan.output_dir / "audio", plan.output_dir),
        (
            plan.output_dir / "audio" / "sound_design",
            plan.output_dir / "audio",
        ),
        (
            plan.output_dir / "audio" / "sound_design" / "versions",
            plan.output_dir / "audio" / "sound_design",
        ),
    )

    for child, parent in created_edges:
        child_index = calls.index(child)
        assert parent in calls[child_index + 1 :]
    version_index = calls.index(version)
    assert version.parent in calls[version_index + 1 :]


def test_sound_first_directory_fsync_failure_preserves_old_pointer(
    plan,
    approved_music,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    manifest = sound._manifest_path(plan)
    manifest.write_bytes(b"old-pointer")
    first_child = plan.output_dir / "audio"
    real_fsync_dir = sound._fsync_dir
    failed = False

    def fail_first_created_child(path):
        nonlocal failed
        if not failed and Path(path) == first_child:
            failed = True
            raise OSError("forced first directory fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(sound, "_fsync_dir", fail_first_created_child)
    with pytest.raises(OSError, match="first directory fsync"):
        sound.prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(),
        )

    assert failed is True
    assert manifest.read_bytes() == b"old-pointer"
    assert not (
        plan.output_dir / "audio" / "sound_design" / "versions"
    ).exists()


def test_sound_manifest_post_replace_keyboard_interrupt_is_committed_success(
    plan,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"state":"old"}', encoding="utf-8")
    staging = _dummy_staging(plan.output_dir / "staging")
    version = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / ("a" * 64)
    )
    real_replace = sound.os.replace

    def interrupt_after_commit(source, destination):
        real_replace(source, destination)
        if Path(destination) == manifest:
            raise KeyboardInterrupt

    monkeypatch.setattr(sound.os, "replace", interrupt_after_commit)
    sound._publish_sound_version_and_manifest(
        staging=staging,
        version=version,
        manifest_path=manifest,
        manifest={"state": "new"},
        root=plan.output_dir,
    )

    assert json.loads(manifest.read_text()) == {"state": "new"}
    assert version.is_dir()


def test_sound_manifest_pre_replace_keyboard_interrupt_preserves_old_pointer(
    plan,
    monkeypatch,
):
    import factory.pet_sitcom_sound as sound

    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"state":"old"}', encoding="utf-8")
    staging = _dummy_staging(plan.output_dir / "staging")
    version = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / ("e" * 64)
    )
    real_replace = sound.os.replace

    def interrupt_before_commit(source, destination):
        if Path(destination) == manifest:
            raise KeyboardInterrupt
        return real_replace(source, destination)

    monkeypatch.setattr(sound.os, "replace", interrupt_before_commit)
    with pytest.raises(KeyboardInterrupt):
        sound._publish_sound_version_and_manifest(
            staging=staging,
            version=version,
            manifest_path=manifest,
            manifest={"state": "new"},
            root=plan.output_dir,
        )

    assert json.loads(manifest.read_text()) == {"state": "old"}
    assert version.is_dir()


def test_sound_never_overwrites_existing_content_version(
    plan,
):
    import factory.pet_sitcom_sound as sound

    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"state":"old"}', encoding="utf-8")
    version = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / ("b" * 64)
    )
    _dummy_staging(version, marker=b"published")
    original = {
        path.name: path.read_bytes()
        for path in version.iterdir()
    }
    staging = _dummy_staging(plan.output_dir / "staging", marker=b"changed")

    with pytest.raises(sound.PetSoundError, match="immutable"):
        sound._publish_sound_version_and_manifest(
            staging=staging,
            version=version,
            manifest_path=manifest,
            manifest={"state": "new"},
            root=plan.output_dir,
        )

    assert {
        path.name: path.read_bytes()
        for path in version.iterdir()
    } == original
    assert json.loads(manifest.read_text()) == {"state": "old"}


@pytest.mark.parametrize("window", ["before_manifest", "after_manifest"])
def test_sound_sigkill_manifest_commit_windows_are_consistent(
    plan,
    window,
):
    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"state":"old"}', encoding="utf-8")
    staging = _dummy_staging(plan.output_dir / f"staging-{window}")
    version = (
        plan.output_dir
        / "audio"
        / "sound_design"
        / "versions"
        / (("c" if window == "before_manifest" else "d") * 64)
    )
    script = """
import os
import signal
import sys
from pathlib import Path
import factory.pet_sitcom_sound as sound

staging, version, manifest, root = map(Path, sys.argv[1:5])
window = sys.argv[5]
real_replace = sound.os.replace
def crash(source, destination):
    if Path(destination) == manifest and window == "before_manifest":
        os.kill(os.getpid(), signal.SIGKILL)
    result = real_replace(source, destination)
    if Path(destination) == manifest and window == "after_manifest":
        os.kill(os.getpid(), signal.SIGKILL)
    return result
sound.os.replace = crash
sound._publish_sound_version_and_manifest(
    staging=staging,
    version=version,
    manifest_path=manifest,
    manifest={"state": "new"},
    root=root,
)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(staging),
            str(version),
            str(manifest),
            str(plan.output_dir),
            window,
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=30,
    )

    assert result.returncode == -signal.SIGKILL
    expected = "old" if window == "before_manifest" else "new"
    assert json.loads(manifest.read_text()) == {"state": expected}
    assert version.is_dir()


def test_sound_render_failure_preserves_existing_manifest(
    plan,
    approved_music,
):
    from factory.pet_sitcom_sound import PetSoundError, prepare_pet_sound_design

    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(b"existing-sound-design")

    with pytest.raises(PetSoundError, match="(?i)sound command failed"):
        prepare_pet_sound_design(
            plan,
            music_source=approved_music,
            command_runner=FakeSoundRunner(fail_ffmpeg_call=2),
        )

    assert manifest.read_bytes() == b"existing-sound-design"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
def test_local_ffmpeg_builds_real_54_second_stems_and_hash_bound_manifest(
    plan,
):
    from factory.pet_sitcom_sound import (
        load_pet_sound_design,
        prepare_pet_sound_design,
    )

    source = plan.output_dir / "assets" / "music" / "real-approved.wav"
    ffmpeg = str(shutil.which("ffmpeg"))
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=44100:duration=54.1",
            "-c:a",
            "pcm_s16le",
            "-ac",
            "2",
            str(source),
        ],
        check=True,
        timeout=60,
    )
    _write_stereo_channel_mask(source)
    approval = _write_approval(source)
    commands: list[list[str]] = []

    def recording_runner(command, **kwargs):
        commands.append([str(item) for item in command])
        return subprocess.run(command, **kwargs)

    manifest_path = prepare_pet_sound_design(
        plan,
        music_source=source,
        command_runner=recording_runner,
    )
    manifest = load_pet_sound_design(plan)

    assert manifest_path == plan.output_dir / "sound_design.json"
    assert manifest["approval"]["sha256"] == _sha256(approval)
    assert manifest["source"]["sha256"] == _sha256(source)
    assert "-stream_loop" not in "\n".join(
        " ".join(command) for command in commands
    )
    for name in ("music", "room_tone"):
        stem = Path(manifest["stems"][name]["path"])
        with wave.open(str(stem), "rb") as audio:
            assert audio.getframerate() == 48_000
            assert audio.getnchannels() == 2
            assert audio.getnframes() == 54 * 48_000
        assert manifest["stems"][name]["duration_seconds"] == 54.0
        assert manifest["stems"][name]["sha256"] == _sha256(stem)

    def integrated_lufs(path: Path, start: float, end: float) -> float:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-ss",
                str(start),
                "-t",
                str(end - start),
                "-i",
                str(path),
                "-af",
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        values = re.findall(
            r"\bI:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS",
            result.stderr,
        )
        assert values
        return float(values[-1])

    def rms_dbfs(path: Path) -> float:
        result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "astats=metadata=1:reset=0",
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        values = [
            float(value)
            for value in re.findall(
                r"RMS level dB:\s*(-?inf|-?[0-9]+(?:\.[0-9]+)?)",
                result.stderr,
            )
            if value.lower() != "-inf"
        ]
        assert values
        return values[-1]

    music = Path(manifest["stems"]["music"]["path"])
    for cue in manifest["music_cues"]:
        measured = integrated_lufs(music, cue["start"], cue["end"])
        assert math.isclose(
            measured,
            cue["target_lufs"],
            abs_tol=2.0,
        )
    room = Path(manifest["stems"]["room_tone"]["path"])
    assert math.isclose(
        integrated_lufs(room, 0.0, 54.0),
        manifest["room_tone"]["target_lufs"],
        abs_tol=2.0,
    )
    for event in manifest["foley"]:
        assert math.isclose(
            rms_dbfs(Path(manifest["stems"][event["stem"]]["path"])),
            event["target_lufs"],
            abs_tol=3.0,
        )
    button = manifest["ending_button"]
    assert math.isclose(
        rms_dbfs(Path(manifest["stems"][button["stem"]]["path"])),
        button["target_rms_dbfs"],
        abs_tol=button["tolerance_db"],
    )
