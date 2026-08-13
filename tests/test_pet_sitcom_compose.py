from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import factory.pet_sitcom_audio_first as audio_first
import factory.pet_sitcom_compose as compose_module
import factory.pet_sitcom_review as review_module
from factory.pet_sitcom import build_pet_sitcom_plan
from factory.pet_sitcom_compose import (
    PetSitcomComposeError,
    build_pet_sitcom_ffmpeg_commands,
    compose_pet_sitcom,
    load_verified_pet_timings,
)


def _write_pcm_wav(
    path: Path,
    *,
    seconds: float,
    rate: int = 48_000,
    channels: int = 2,
    sample: bytes = b"\x10\x00",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(sample * channels * int(round(seconds * rate)))


def _write_task2_manifest(
    plan,
    monkeypatch,
    *,
    duration_seconds: float = 0.8,
    mock_probe: bool = True,
) -> tuple[audio_first.PetSpeechAsset, ...]:
    starts = audio_first._shot_start_times(plan)
    assets = []
    for shot in plan.shots:
        if not shot.dialogue:
            continue
        voice = audio_first.PET_VOICES[str(shot.speaker)]
        output = audio_first._speech_output(plan, shot)
        _write_pcm_wav(output, seconds=duration_seconds)
        absolute_start = (
            starts[shot.shot_id] + float(shot.dialogue_offset_seconds)
        )
        assets.append(
            audio_first.PetSpeechAsset(
                shot_id=shot.shot_id,
                speaker=str(shot.speaker),
                text=shot.dialogue,
                voice_id=voice.voice_id,
                speech_rate=voice.speech_rate,
                output_path=output,
                output_sha256=audio_first._sha256(output),
                duration_seconds=duration_seconds,
                absolute_start_seconds=absolute_start,
                absolute_end_seconds=absolute_start + duration_seconds,
            )
        )
    result = tuple(assets)
    audio_first._write_manifest(plan, result)

    def audio_probe(path, *, required_stream):
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        return SimpleNamespace(
            valid=required_stream == "audio",
            duration_seconds=duration,
            audio_stream_count=1,
            video_stream_count=0,
        )

    if mock_probe:
        monkeypatch.setattr(audio_first, "probe_media", audio_probe)
    return result


def _source_map(plan) -> dict[str, dict[str, object]]:
    result = {}
    for shot in plan.shots:
        source = shot.candidate_dir / "candidate_001.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"selected-{shot.shot_id}".encode("ascii"))
        result[shot.shot_id] = {
            "path": source,
            "sha256": compose_module._sha(source),
            "candidate_number": 1,
        }
    return result


def _source_evidence(plan, sources) -> dict:
    return {
        "valid": True,
        "sources": sources,
        "qc": {
            "records": [
                {
                    "name": shot.shot_id,
                    "duration_seconds": float(
                        shot.generation_duration_seconds
                    ),
                    "video_duration_seconds": float(
                        shot.generation_duration_seconds
                    ),
                    "passed": True,
                }
                for shot in plan.shots
            ]
        },
    }


def _wire_passing_task6(monkeypatch, plan, sources) -> dict:
    evidence = _source_evidence(plan, sources)
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: evidence,
    )
    monkeypatch.setattr(
        review_module,
        "validate_pet_shot_reviews",
        lambda _plan: {
            "passed": True,
            "failed_shots": [],
            "document": {"schema_version": review_module.SHOT_REVIEW_SCHEMA},
        },
    )
    monkeypatch.setattr(
        review_module,
        "validate_owner_native_audio_review",
        lambda _plan: {"verified": True},
    )
    monkeypatch.setattr(
        review_module,
        "_selected_sources",
        lambda _plan: sources,
    )
    return evidence


def _sound_design(plan) -> dict:
    root = plan.output_dir / "audio" / "sound-test"
    durations = {
        "music": 54.0,
        "room_tone": 54.0,
        "bag_rustle": 0.3,
        "tail_floor_rustle": 3.2,
        "light_paw_steps": 2.6,
        "mirror_slide": 1.6,
        "ending_button": 0.4,
    }
    stems = {}
    for name, duration in durations.items():
        path = root / f"{name}.wav"
        _write_pcm_wav(path, seconds=duration)
        stems[name] = {
            "path": str(path.resolve()),
            "sha256": compose_module._sha(path),
            "duration_seconds": duration,
            "sample_rate": 48_000,
            "channels": 2,
        }
    return {
        "duration_seconds": 54.0,
        "sample_rate": 48_000,
        "channels": 2,
        "music_cues": [
            {
                "name": "light_interrogation",
                "start": 0.0,
                "end": 26.5,
            },
            {
                "name": "surveillance_investigation",
                "start": 26.5,
                "end": 37.4,
            },
            {"name": "comic_reveal", "start": 37.4, "end": 54.0},
        ],
        "foley": [
            {
                "name": "bag_rustle",
                "start": 5.25,
                "duration": 0.3,
                "target_lufs": -30.0,
                "bridge_end": None,
                "stem": "bag_rustle",
            },
            {
                "name": "tail_floor_rustle",
                "start": 29.7,
                "duration": 0.45,
                "target_lufs": -32.0,
                "bridge_end": 32.9,
                "stem": "tail_floor_rustle",
            },
            {
                "name": "light_paw_steps",
                "start": 30.3,
                "duration": 1.2,
                "target_lufs": -34.0,
                "bridge_end": 32.9,
                "stem": "light_paw_steps",
            },
            {
                "name": "mirror_slide",
                "start": 44.8,
                "duration": 1.6,
                "target_lufs": -29.0,
                "bridge_end": None,
                "stem": "mirror_slide",
            },
        ],
        "room_tone": {
            "start": 0.0,
            "end": 54.0,
            "target_lufs": -42.0,
            "stem": "room_tone",
        },
        "ending_button": {
            "start": 53.6,
            "end": 54.0,
            "target_rms_dbfs": -30.0,
            "tolerance_db": 1.5,
            "stem": "ending_button",
        },
        "stems": stems,
    }


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, output_dir=tmp_path / "pet-case")


@pytest.fixture
def selected_sources(plan):
    return _source_map(plan)


@pytest.fixture
def prepared_plan(plan, selected_sources, monkeypatch):
    _write_task2_manifest(plan, monkeypatch)
    _wire_passing_task6(monkeypatch, plan, selected_sources)
    sound = _sound_design(plan)
    monkeypatch.setattr(
        compose_module,
        "_selected_sources",
        lambda _plan: selected_sources,
    )
    if hasattr(compose_module, "_require_owner_native_audio_review"):
        monkeypatch.setattr(
            compose_module,
            "_require_owner_native_audio_review",
            lambda _plan, _sources: None,
        )
    try:
        import factory.pet_sitcom_sound as sound_module
    except ModuleNotFoundError:
        sound_module = None
    if sound_module is not None:
        monkeypatch.setattr(
            sound_module,
            "load_pet_sound_design",
            lambda _plan: sound,
        )
    return plan


def _input_paths(command: list[str]) -> list[Path]:
    return [
        Path(command[index + 1])
        for index, value in enumerate(command[:-1])
        if value == "-i"
    ]


def _filter_graph(command: list[str]) -> str:
    return command[command.index("-filter_complex") + 1]


def _mp4_box(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _write_faststart_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(_mp4_box(kind) for kind in (b"ftyp", b"moov", b"mdat"))
    )


def _valid_output_probe_payload(
    *,
    profile: str = "High",
    fps: str = "30/1",
    r_fps: str = "30/1",
    audio_bit_rate: str = "192000",
    duration: str = "54.0",
    video_duration: str | None = None,
    audio_duration: str | None = None,
) -> dict:
    return {
        "format": {"start_time": "0.0", "duration": duration},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "profile": profile,
                "pix_fmt": "yuv420p",
                "width": 1080,
                "height": 1920,
                "avg_frame_rate": fps,
                "r_frame_rate": r_fps,
                "start_time": "0.0",
                "duration": video_duration or duration,
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "bit_rate": audio_bit_rate,
                "start_time": "0.0",
                "duration": audio_duration or duration,
            },
        ],
    }


class ComposeRunner:
    def __init__(
        self,
        *,
        probe_payload: dict | None = None,
        integrated_lufs: float = -16.0,
        true_peak_dbtp: float = -1.6,
        max_volume_db: float = -1.7,
        frame_timestamps: list[float] | None = None,
    ) -> None:
        self.probe_payload = probe_payload or _valid_output_probe_payload()
        self.integrated_lufs = integrated_lufs
        self.true_peak_dbtp = true_peak_dbtp
        self.max_volume_db = max_volume_db
        self.frame_timestamps = frame_timestamps or [
            index / 30.0 for index in range(1620)
        ]
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        command = [str(item) for item in command]
        self.commands.append(command)
        tool = Path(command[0]).name
        rendered = " ".join(command)
        if tool == "ffprobe":
            if "frame=best_effort_timestamp_time" in rendered:
                return SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "frames": [
                                {
                                    "best_effort_timestamp_time": str(value)
                                }
                                for value in self.frame_timestamps
                            ]
                        }
                    ),
                    stderr="",
                )
            return SimpleNamespace(
                stdout=json.dumps(self.probe_payload),
                stderr="",
            )
        if "ebur128=peak=true" in rendered:
            return SimpleNamespace(
                stdout="",
                stderr=(
                    "Integrated loudness:\n"
                    f"  I: {self.integrated_lufs:.1f} LUFS\n"
                    "True peak:\n"
                    f"  Peak: {self.true_peak_dbtp:.1f} dBFS\n"
                ),
            )
        if "volumedetect" in rendered:
            return SimpleNamespace(
                stdout="",
                stderr=f"max_volume: {self.max_volume_db:.1f} dB\n",
            )
        output = Path(command[-1])
        _write_faststart_stub(output)
        return SimpleNamespace(stdout="", stderr="")


class ReviewEvidenceRunner:
    def __init__(self, plan) -> None:
        self.plan = plan
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        command = [str(item) for item in command]
        self.commands.append(command)
        tool = Path(command[0]).name
        if tool == "ffprobe":
            path = Path(command[-1])
            shot = next(
                item
                for item in self.plan.shots
                if item.shot_id == path.parent.name
            )
            duration = float(shot.generation_duration_seconds)
            streams = [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "profile": "High",
                    "pix_fmt": "yuv420p",
                    "width": 32,
                    "height": 48,
                    "avg_frame_rate": "30/1",
                    "duration": str(duration),
                }
            ]
            if shot.speaker in {"naitang", "doubao"}:
                streams.append(
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "sample_rate": "48000",
                        "channels": 2,
                        "bit_rate": "192000",
                        "duration": str(duration),
                    }
                )
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "format": {
                            "duration": str(duration),
                            "format_name": "mov,mp4",
                        },
                        "streams": streams,
                    }
                ),
                stderr="",
            )
        if tool != "ffmpeg":
            raise AssertionError(f"unexpected review command: {command}")
        joined = " ".join(command)
        if "blackdetect=" in joined or "freezedetect=" in joined:
            return SimpleNamespace(stdout="", stderr="")
        output = Path(command[-1])
        if "-frames:v" not in command or output.suffix != ".png":
            raise AssertionError(f"unexpected review ffmpeg command: {command}")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 12), "white").save(output)
        return SimpleNamespace(stdout="", stderr="")


def _prepare_real_task5_task6_chain(
    plan,
    monkeypatch,
    *,
    ffmpeg: str,
) -> dict[str, dict[str, object]]:
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review
    import factory.pet_sitcom_sound as sound

    _write_task2_manifest(
        plan,
        monkeypatch,
        duration_seconds=0.8,
        mock_probe=False,
    )
    for index, character in enumerate(plan.characters, start=1):
        character.reference_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (index * 60, 30, 30)).save(
            character.reference_path
        )
    for scene in plan.scenes:
        scene.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), "white").save(scene.anchor_path)

    selections: dict[str, dict[str, object]] = {}
    sources: dict[str, dict[str, object]] = {}
    for shot in plan.shots:
        candidate = generation._pet_candidate_path(shot, 1)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        duration = str(shot.generation_duration_seconds)
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=32x48:rate=30:duration={duration}",
        ]
        if shot.speaker in {"naitang", "doubao"}:
            command += [
                "-f",
                "lavfi",
                "-i",
                (
                    "sine=frequency=997:sample_rate=48000:"
                    f"duration={duration}"
                ),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ac",
                "2",
                "-shortest",
            ]
        else:
            command += ["-an"]
        command += [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-movflags",
            "+faststart",
            str(candidate),
        ]
        subprocess.run(command, check=True, timeout=60)

        references = generation._pet_shot_references(
            plan,
            shot,
            selections,
        )
        drive_audio, source_tts_sha256 = (
            generation._pet_shot_audio_bindings(plan, shot)
        )
        provenance = generation._pet_candidate_provenance(
            shot,
            1,
            generation._pet_shot_prompt(shot, 1, ""),
            "",
            references,
            selections,
            drive_audio,
            source_tts_sha256,
        )
        provenance.update(
            {
                "provider_success": True,
                "video_sha256": compose_module._sha(candidate),
            }
        )
        generation._pet_candidate_state_path(candidate).write_text(
            json.dumps(provenance),
            encoding="utf-8",
        )
        generation._pet_gateway_report_path(candidate).write_text(
            json.dumps(
                {
                    "success": True,
                    "pet_sitcom_provenance": provenance,
                }
            ),
            encoding="utf-8",
        )
        frame = generation._pet_continuity_frame_path(
            plan,
            shot.shot_id,
        )
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), "white").save(frame)
        sidecar = generation._pet_continuity_state_path(frame)
        timestamp = min(
            float(shot.duration_seconds) - 0.08,
            float(shot.generation_duration_seconds) - 0.08,
        )
        sidecar.write_text(
            json.dumps(
                {
                    "schema_version": generation.PET_CONTINUITY_SCHEMA,
                    "source_video_path": str(candidate.resolve()),
                    "source_video_sha256": compose_module._sha(candidate),
                    "source_video_duration_seconds": float(
                        shot.generation_duration_seconds
                    ),
                    "edit_duration_seconds": float(shot.duration_seconds),
                    "timestamp_seconds": timestamp,
                    "extracted_at": "2026-07-27T08:00:00+00:00",
                    "frame_sha256": compose_module._sha(frame),
                }
            ),
            encoding="utf-8",
        )
        entry = {
            "candidate_number": 1,
            "status": "selected",
            "video_path": str(candidate.resolve()),
            "video_sha256": compose_module._sha(candidate),
            "prompt_sha256": provenance["prompt_sha256"],
            "reference_paths": provenance["reference_paths"],
            "reference_sha256": provenance["reference_sha256"],
            "dependency_video_sha256": provenance[
                "dependency_video_sha256"
            ],
            "source_tts_sha256": provenance["source_tts_sha256"],
            "reference_audio_sha256": provenance[
                "reference_audio_sha256"
            ],
            "continuity_frame_path": str(frame.resolve()),
            "continuity_sidecar_path": str(sidecar.resolve()),
            "continuity_frame_sha256": compose_module._sha(frame),
            "continuity_timestamp_seconds": timestamp,
            "selected_at": "2026-07-27T08:00:00+00:00",
        }
        selections[shot.shot_id] = entry
        sources[shot.shot_id] = {
            "path": candidate,
            "sha256": entry["video_sha256"],
            "candidate_number": 1,
        }
    plan.selection_path.parent.mkdir(parents=True, exist_ok=True)
    plan.selection_path.write_text(
        json.dumps(
            {
                "schema_version": generation.PET_SELECTION_SCHEMA,
                "shots": selections,
                "history": {},
            }
        ),
        encoding="utf-8",
    )

    review.build_pet_sitcom_evidence(
        plan,
        phase="source",
        command_runner=ReviewEvidenceRunner(plan),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    review.write_pet_shot_review_template(plan)
    shot_reviews = json.loads(plan.shot_review_path.read_text())
    for shot in plan.shots:
        record = shot_reviews["shots"][shot.shot_id]
        record["reviewed"] = True
        record["reviewed_at"] = "2026-07-27T08:00:00+00:00"
        record["retry_reason"] = ""
        for gate in review.SHOT_REVIEW_GATES:
            record["gates"][gate] = {
                "passed": True,
                "notes": f"{gate} checked frame by frame",
                "timestamps_seconds": [
                    0.1,
                    min(
                        2.5,
                        float(shot.generation_duration_seconds) - 0.2,
                    ),
                    float(shot.generation_duration_seconds) - 0.1,
                ],
                "issue_codes": [],
            }
        record["passed"] = True
    for record in shot_reviews["mouth_timing"].values():
        audio_start = float(record["audio_onset_seconds"])
        audio_end = float(record["audio_offset_seconds"])
        record.update(
            {
                "mouth_onset_seconds": audio_start + 0.05,
                "mouth_offset_seconds": audio_end + 0.05,
                "onset_error_seconds": 0.05,
                "offset_error_seconds": 0.05,
                "no_silent_mouth_flapping": True,
                "no_closed_mouth_during_speech": True,
                "reviewed": True,
                "passed": True,
            }
        )
    plan.shot_review_path.write_text(
        json.dumps(shot_reviews),
        encoding="utf-8",
    )
    review.write_owner_native_audio_review_template(plan)
    owner_path = plan.output_dir / "owner_native_audio_review.json"
    owner = json.loads(owner_path.read_text())
    owner.update(
        {
            "reviewed": True,
            "verified": True,
            "generated_at": "2026-07-27T08:00:00+00:00",
        }
    )
    for record in owner["shots"].values():
        record.update(
            {
                "no_native_voice": True,
                "room_tone_allowed": True,
                "reviewed_at": "2026-07-27T08:00:00+00:00",
                "notes": "",
            }
        )
    owner_path.write_text(json.dumps(owner), encoding="utf-8")

    music = plan.output_dir / "assets" / "music" / "compose-approved.mov"
    music.parent.mkdir(parents=True, exist_ok=True)
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
            str(music),
        ],
        check=True,
        timeout=60,
    )
    approval = {
        "schema_version": sound.MUSIC_APPROVAL_SCHEMA,
        "source_path": str(music.resolve()),
        "source_sha256": sound._sha256(music),
        "reviewed": True,
        "approved": True,
        "not_harsh": True,
        "not_repetitive": True,
        "dialogue_compatible": True,
        "reviewed_at": "2026-07-27T08:00:00+00:00",
    }
    sound.music_approval_path(music).write_text(
        json.dumps(approval),
        encoding="utf-8",
    )
    sound.prepare_pet_sound_design(
        plan,
        music_source=music,
        command_runner=subprocess.run,
    )
    return sources


def test_composition_uses_variable_trim_durations_and_no_retiming(
    prepared_plan,
):
    commands = build_pet_sitcom_ffmpeg_commands(prepared_plan)
    serialized = "\n".join(" ".join(command) for command in commands)

    assert "trim=duration=5.2,setpts=PTS-STARTPTS" in serialized
    assert "trim=duration=3.4,setpts=PTS-STARTPTS" in serialized
    assert "trim=duration=7.3,setpts=PTS-STARTPTS" in serialized
    assert "concat=n=10:v=1:a=0[vstory]" in serialized
    assert "fps=30" in serialized
    assert "-preset medium -tune grain" in serialized
    assert "-crf 16" in serialized
    assert "tpad" not in serialized
    assert "atempo" not in serialized
    assert "minterpolate" not in serialized
    assert "xfade" not in serialized
    assert all(
        command[command.index("-t") + 1] == "54" for command in commands
    )


def test_composition_discards_every_provider_audio_stream(
    prepared_plan,
):
    command = build_pet_sitcom_ffmpeg_commands(prepared_plan)[0]
    graph = _filter_graph(command)

    assert all(
        f"[{index}:a]" not in graph for index in range(len(prepared_plan.shots))
    )
    assert all(":v]" in item for item in graph.split(";")[:10])
    assert "/audio/drive/" not in " ".join(command)
    assert all(
        str(audio_first._speech_output(prepared_plan, shot)) in command
        for shot in prepared_plan.shots
        if shot.dialogue
    )


def test_dialogue_uses_task2_absolute_timing_j_cut_and_padded_ducking(
    prepared_plan,
):
    command = build_pet_sitcom_ffmpeg_commands(prepared_plan)[0]
    graph = _filter_graph(command)

    assert "adelay=26300|26300" in graph
    assert "between(t,26.200,27.300)" in graph
    assert "between(t,0.450,1.550)" in graph
    assert "volume=volume=0.398107" in graph
    assert "atempo" not in graph


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
def test_real_task2_task5_task6_default_compose_and_tamper_gates(
    plan,
    monkeypatch,
):
    import factory.pet_sitcom_generation as generation

    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    _prepare_real_task5_task6_chain(
        plan,
        monkeypatch,
        ffmpeg=ffmpeg,
    )

    review_timings = review_module._validate_composition_preflight(plan)
    timings = load_verified_pet_timings(plan)
    commands = build_pet_sitcom_ffmpeg_commands(
        plan,
        ffmpeg_bin=ffmpeg,
    )
    shot06 = next(
        timing for timing in timings if timing.shot_id == "shot_06"
    )

    assert shot06.start_seconds == pytest.approx(-0.20)
    assert shot06.absolute_start_seconds == pytest.approx(26.30)
    assert next(
        timing
        for timing in review_timings
        if timing.shot_id == "shot_06"
    ).absolute_start_seconds == pytest.approx(26.30)
    assert "adelay=26300|26300" in _filter_graph(commands[0])
    assert "concat=n=10:v=1:a=0" in _filter_graph(commands[0])
    assert "between(t,51.550,54.000)" in _filter_graph(commands[1])

    shot_review_bytes = plan.shot_review_path.read_bytes()
    plan.shot_review_path.unlink()
    with pytest.raises(
        PetSitcomComposeError,
        match="Task 6|review|missing",
    ):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    plan.shot_review_path.write_bytes(shot_review_bytes)
    failed_review = json.loads(shot_review_bytes)
    failed_record = failed_review["shots"]["shot_01"]
    failed_gate = review_module.SHOT_REVIEW_GATES[0]
    failed_record["gates"][failed_gate]["passed"] = False
    failed_record["gates"][failed_gate]["issue_codes"] = [
        "identity_drift"
    ]
    failed_record["passed"] = False
    failed_record["retry_reason"] = "identity_drift"
    plan.shot_review_path.write_text(
        json.dumps(failed_review),
        encoding="utf-8",
    )
    with pytest.raises(PetSitcomComposeError, match="Task 6|review|failed"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    plan.shot_review_path.write_bytes(shot_review_bytes)

    selection_bytes = plan.selection_path.read_bytes()
    selection = json.loads(selection_bytes)
    selection["shots"]["shot_01"]["video_sha256"] = "0" * 64
    plan.selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(PetSitcomComposeError, match="Task 5|selection"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    plan.selection_path.write_bytes(selection_bytes)

    candidate = generation._pet_candidate_path(plan.shots[0], 1)
    candidate_bytes = candidate.read_bytes()
    candidate.write_bytes(candidate_bytes + b"replaced")
    with pytest.raises(PetSitcomComposeError, match="Task 5|provenance|stale"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    candidate.write_bytes(candidate_bytes)

    provenance_path = generation._pet_candidate_state_path(candidate)
    provenance_bytes = provenance_path.read_bytes()
    provenance = json.loads(provenance_bytes)
    provenance["video_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(PetSitcomComposeError, match="Task 5|provenance"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    provenance_path.write_bytes(provenance_bytes)

    gateway_path = generation._pet_gateway_report_path(candidate)
    gateway_bytes = gateway_path.read_bytes()
    gateway = json.loads(gateway_bytes)
    gateway["success"] = False
    gateway_path.write_text(json.dumps(gateway), encoding="utf-8")
    with pytest.raises(PetSitcomComposeError, match="Task 5|provenance"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    gateway_path.write_bytes(gateway_bytes)

    continuity = generation._pet_continuity_state_path(
        generation._pet_continuity_frame_path(plan, "shot_01")
    )
    continuity_bytes = continuity.read_bytes()
    continuity.unlink()
    continuity_copy = plan.output_dir / "continuity-copy.json"
    continuity_copy.write_bytes(continuity_bytes)
    continuity.symlink_to(continuity_copy)
    with pytest.raises(PetSitcomComposeError, match="symlink|continuity"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    continuity.unlink()
    continuity.write_bytes(continuity_bytes)

    owner_path = plan.output_dir / "owner_native_audio_review.json"
    owner_bytes = owner_path.read_bytes()
    owner_path.unlink()
    with pytest.raises(PetSitcomComposeError, match="human|owner|missing"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    owner_path.write_bytes(owner_bytes)
    owner = json.loads(owner_bytes)
    owner["shots"]["shot_06"]["selected_mp4_sha256"] = "0" * 64
    owner_path.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(PetSitcomComposeError, match="stale|invalid"):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    owner_path.write_bytes(owner_bytes)

    task2_bytes = plan.audio_manifest_path.read_bytes()
    shot06_audio = audio_first._speech_output(plan, plan.shots[5])
    shot06_audio_bytes = shot06_audio.read_bytes()
    shot06_audio.write_bytes(shot06_audio_bytes + b"replaced")
    with pytest.raises(
        PetSitcomComposeError,
        match="speech asset|hash|stale|Task 5 provenance",
    ):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    shot06_audio.write_bytes(shot06_audio_bytes)
    linked_audio = plan.output_dir / "shot06-audio-copy.wav"
    linked_audio.write_bytes(shot06_audio_bytes)
    shot06_audio.unlink()
    shot06_audio.symlink_to(linked_audio)
    with pytest.raises(
        PetSitcomComposeError,
        match="symlink|speech asset|Task 5 provenance",
    ):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    shot06_audio.unlink()
    shot06_audio.write_bytes(shot06_audio_bytes)

    task2 = json.loads(task2_bytes)
    shot06_record = next(
        item for item in task2["assets"] if item["shot_id"] == "shot_06"
    )
    shot06_record["output_sha256"] = "0" * 64
    plan.audio_manifest_path.write_text(json.dumps(task2), encoding="utf-8")
    with pytest.raises(
        PetSitcomComposeError,
        match="Task 5 provenance|speech asset|stale",
    ):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    plan.audio_manifest_path.write_bytes(task2_bytes)

    task2 = json.loads(task2_bytes)
    shot06_record = next(
        item for item in task2["assets"] if item["shot_id"] == "shot_06"
    )
    shot06_record["absolute_start_seconds"] = 26.50
    shot06_record["absolute_end_seconds"] = 27.30
    plan.audio_manifest_path.write_text(json.dumps(task2), encoding="utf-8")
    with pytest.raises(
        PetSitcomComposeError,
        match="Task 5 provenance|absolute timing",
    ):
        build_pet_sitcom_ffmpeg_commands(plan, ffmpeg_bin=ffmpeg)
    plan.audio_manifest_path.write_bytes(task2_bytes)

    plan.dialogue_timing_path.write_text(
        json.dumps(
            {
                "schema_version": compose_module.DIALOGUE_TIMING_SCHEMA,
                "verified": True,
                "timings": [
                    {
                        "shot_id": "shot_06",
                        "start_seconds": 0.40,
                        "end_seconds": 1.20,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert next(
        item
        for item in load_verified_pet_timings(plan)
        if item.shot_id == "shot_06"
    ).start_seconds == pytest.approx(-0.20)

    report = compose_pet_sitcom(
        plan,
        command_runner=subprocess.run,
        ffmpeg_bin=ffmpeg,
    )
    assert report["success"] is True
    for output in (plan.clean_output, plan.release_output):
        compose_module._validate_output(
            output,
            subprocess.run,
            ffprobe,
            ffmpeg=ffmpeg,
            timings=timings,
        )
        payload = json.loads(
            subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    (
                        "format=duration:"
                        "stream=codec_type,width,height,avg_frame_rate,"
                        "r_frame_rate,duration"
                    ),
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
                timeout=30,
            ).stdout
        )
        video = next(
            item
            for item in payload["streams"]
            if item["codec_type"] == "video"
        )
        assert (video["width"], video["height"]) == (1080, 1920)
        assert video["avg_frame_rate"] == "30/1"
        assert video["r_frame_rate"] == "30/1"
        assert float(video["duration"]) == pytest.approx(54.0, abs=0.15)

    def band_mean(path: Path) -> float:
        measured = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "bandpass=f=997:w=20,volumedetect",
                "-f",
                "null",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        ).stderr
        matches = re.findall(
            r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB",
            measured,
        )
        assert matches
        return float(matches[-1])

    provider = generation._pet_candidate_path(plan.shots[2], 1)
    assert band_mean(provider) > -25.0
    assert band_mean(plan.clean_output) < -30.0


def test_tail_and_footsteps_form_the_shot_07_l_bridge(
    prepared_plan,
):
    command = build_pet_sitcom_ffmpeg_commands(prepared_plan)[0]
    graph = _filter_graph(command)

    assert "adelay=29700|29700" in graph
    assert "atrim=duration=3.2" in graph
    assert "adelay=30300|30300" in graph
    assert "atrim=duration=2.6" in graph
    assert "32.9" in json.dumps(
        _sound_design(prepared_plan)["foley"]
    )


def test_audio_categories_remain_independent_until_final_amix(
    prepared_plan,
):
    command = build_pet_sitcom_ffmpeg_commands(prepared_plan)[0]
    graph = _filter_graph(command)

    assert "[dialogue]" in graph
    assert "[foley]" in graph
    assert "[room]" in graph
    assert "[musicduck8]" in graph
    assert (
        "[dialogue][foley][room][musicduck8]"
        "amix=inputs=4:duration=longest"
    ) in graph
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph


def test_release_copy_keeps_text_assets_out_of_clean_copy(
    prepared_plan,
):
    clean, release = build_pet_sitcom_ffmpeg_commands(prepared_plan)
    clean_inputs = _input_paths(clean)
    release_inputs = _input_paths(release)
    clean_graph = _filter_graph(clean)
    release_graph = _filter_graph(release)

    assert len(clean_inputs) == 25
    assert len(release_inputs) == 39
    assert len(
        [item for item in release_graph.split(";") if "overlay=" in item]
    ) == 14
    assert "overlay=" not in clean_graph
    assert "drawtext" not in release_graph
    assert all(path.suffix == ".png" for path in release_inputs[25:])
    assert all(path.is_file() for path in release_inputs[25:])


def test_compose_reuses_task5_task6_review_validators(
    plan,
    selected_sources,
    monkeypatch,
):
    _write_task2_manifest(plan, monkeypatch)

    def reject(_plan):
        raise review_module.PetSitcomReviewError(
            "Task 5 selection provenance is stale"
        )

    monkeypatch.setattr(review_module, "validate_source_evidence", reject)
    with pytest.raises(
        PetSitcomComposeError,
        match="Task 5 selection provenance",
    ):
        build_pet_sitcom_ffmpeg_commands(plan)


def test_compose_requires_every_task6_v4_gate_to_pass(
    plan,
    selected_sources,
    monkeypatch,
):
    _write_task2_manifest(plan, monkeypatch)
    evidence = _source_evidence(plan, selected_sources)
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: evidence,
    )
    monkeypatch.setattr(
        review_module,
        "validate_pet_shot_reviews",
        lambda _plan: {
            "passed": False,
            "failed_shots": ["shot_07"],
        },
    )

    with pytest.raises(PetSitcomComposeError, match="shot_07"):
        build_pet_sitcom_ffmpeg_commands(plan)


def test_source_video_shorter_than_edit_requires_regeneration(
    prepared_plan,
    monkeypatch,
):
    evidence = review_module.validate_source_evidence(prepared_plan)
    short = {
        **evidence,
        "qc": {
            "records": [
                dict(record)
                for record in evidence["qc"]["records"]
            ]
        },
    }
    short["qc"]["records"][0]["video_duration_seconds"] = 5.19
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: short,
    )

    with pytest.raises(
        PetSitcomComposeError,
        match="shot_01.*regenerat",
    ):
        build_pet_sitcom_ffmpeg_commands(prepared_plan)


def test_source_video_even_half_millisecond_short_requires_regeneration(
    prepared_plan,
    monkeypatch,
):
    evidence = review_module.validate_source_evidence(prepared_plan)
    short = {
        **evidence,
        "qc": {
            "records": [
                dict(record)
                for record in evidence["qc"]["records"]
            ]
        },
    }
    short["qc"]["records"][0]["video_duration_seconds"] = 5.1995
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: short,
    )

    with pytest.raises(
        PetSitcomComposeError,
        match="shot_01.*regenerat",
    ):
        build_pet_sitcom_ffmpeg_commands(prepared_plan)


def test_local_single_frame_shortfall_uses_distributed_pts_retime(
    prepared_plan,
    monkeypatch,
):
    evidence = review_module.validate_source_evidence(prepared_plan)
    short = {
        **evidence,
        "qc": {
            "records": [
                dict(record)
                for record in evidence["qc"]["records"]
            ]
        },
    }
    short["qc"]["records"][0]["video_duration_seconds"] = 5.19
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: short,
    )
    monkeypatch.setattr(
        review_module,
        "_local_recut_micro_retime_allowed",
        lambda _plan, shot_id, _source, _duration: shot_id == "shot_01",
    )

    commands = build_pet_sitcom_ffmpeg_commands(prepared_plan)
    serialized = "\n".join(" ".join(command) for command in commands)

    assert (
        "trim=duration=5.19,setpts=1.001927*(PTS-STARTPTS)"
        in serialized
    )
    assert "tpad" not in serialized
    assert "minterpolate" not in serialized


@pytest.mark.parametrize(
    "bad_duration",
    [None, True, float("nan"), float("inf"), 0.0, -1.0, "missing"],
)
def test_compose_rejects_invalid_source_stream_duration_before_ffmpeg(
    prepared_plan,
    monkeypatch,
    bad_duration,
):
    evidence = review_module.validate_source_evidence(prepared_plan)
    invalid = {
        **evidence,
        "qc": {
            "records": [
                dict(record)
                for record in evidence["qc"]["records"]
            ]
        },
    }
    record = invalid["qc"]["records"][0]
    if bad_duration == "missing":
        record.pop("video_duration_seconds")
    else:
        record["video_duration_seconds"] = bad_duration
    monkeypatch.setattr(
        review_module,
        "validate_source_evidence",
        lambda _plan: invalid,
    )
    runner = ComposeRunner()

    with pytest.raises(
        PetSitcomComposeError,
        match="numbers|finite|regenerated",
    ):
        compose_pet_sitcom(prepared_plan, command_runner=runner)

    assert runner.commands == []


def test_task2_dialogue_overlap_is_revalidated(
    prepared_plan,
    monkeypatch,
):
    assets = list(audio_first.load_pet_speech_assets(prepared_plan))
    assets[1] = replace(
        assets[1],
        absolute_start_seconds=assets[0].absolute_end_seconds - 0.1,
        absolute_end_seconds=assets[0].absolute_end_seconds + 0.7,
    )
    monkeypatch.setattr(
        compose_module,
        "load_pet_speech_assets",
        lambda _plan: tuple(assets),
    )

    with pytest.raises(PetSitcomComposeError, match="dialogue overlap"):
        load_verified_pet_timings(prepared_plan)


def test_compatibility_voice_wrappers_and_timing_binding_smoke(
    prepared_plan,
    monkeypatch,
):
    records = []
    for shot in prepared_plan.shots:
        if not shot.dialogue:
            continue
        output = audio_first._speech_output(prepared_plan, shot)
        records.append(
            {
                "shot_id": shot.shot_id,
                "speaker": shot.speaker,
                "status": "reused",
                "output_path": str(output),
                "output_sha256": compose_module._sha(output),
            }
        )
    shared = {
        "success": True,
        "executed": False,
        "assets": records,
        "errors": [],
    }
    monkeypatch.setattr(
        compose_module,
        "generate_pet_speech_assets",
        lambda *_args, **_kwargs: shared,
    )
    client = SimpleNamespace(
        config=SimpleNamespace(
            voice_type="zh_female_vv_uranus_bigtts"
        )
    )

    owner = compose_module.generate_owner_voice_lines(
        prepared_plan,
        tts_client=client,
    )
    cats = compose_module.generate_cat_voice_lines(
        prepared_plan,
        tts_client=client,
    )
    timing_path = compose_module.bind_cat_voice_lines_to_verified_timings(
        prepared_plan
    )
    timing = json.loads(timing_path.read_text())

    assert owner["completed_count"] == 2
    assert cats["completed_count"] == 6
    assert timing["source_manifest_path"] == str(
        prepared_plan.audio_manifest_path
    )
    shot06 = next(
        item for item in timing["timings"] if item["shot_id"] == "shot_06"
    )
    assert shot06["start_seconds"] == pytest.approx(-0.20)
    assert shot06["absolute_start_seconds"] == pytest.approx(26.30)


def test_composition_rejects_forged_story_contract(prepared_plan):
    forged = replace(
        prepared_plan,
        shots=(
            replace(
                prepared_plan.shots[0],
                duration_seconds=5.3,
            ),
            *prepared_plan.shots[1:],
        ),
    )

    with pytest.raises(
        PetSitcomComposeError,
        match="approved pet sitcom contract",
    ):
        build_pet_sitcom_ffmpeg_commands(forged)


def test_composition_rejects_symlinked_sound_manifest(
    plan,
    tmp_path,
):
    outside = tmp_path / "outside-sound.json"
    outside.write_text("{}", encoding="utf-8")
    manifest = plan.output_dir / "sound_design.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.symlink_to(outside)

    with pytest.raises(PetSitcomComposeError, match="symlink"):
        build_pet_sitcom_ffmpeg_commands(plan)


def test_compose_validates_before_atomic_publish(prepared_plan):
    runner = ComposeRunner()

    report = compose_pet_sitcom(
        prepared_plan,
        command_runner=runner,
    )

    assert report["success"] is True
    assert prepared_plan.clean_output.is_file()
    assert prepared_plan.release_output.is_file()
    assert prepared_plan.clean_output.read_bytes() == prepared_plan.release_output.read_bytes()
    assert any(
        "ebur128=peak=true" in " ".join(command)
        for command in runner.commands
    )
    assert any(
        "volumedetect" in " ".join(command)
        for command in runner.commands
    )


def test_clean_and_release_consume_one_private_verified_snapshot(
    prepared_plan,
):
    runner = ComposeRunner()

    compose_pet_sitcom(
        prepared_plan,
        command_runner=runner,
    )

    renders = [
        command
        for command in runner.commands
        if command
        and Path(command[0]).name == "ffmpeg"
        and "-filter_complex" in command
        and Path(command[-1]).name in {"clean.mp4", "release.mp4"}
    ]
    assert len(renders) == 2
    clean_inputs = _input_paths(renders[0])
    release_inputs = _input_paths(renders[1])
    assert release_inputs[: len(clean_inputs)] == clean_inputs
    assert all(".compose-run-" in str(path) for path in clean_inputs)
    assert not any(path.exists() for path in release_inputs)


def test_compose_fails_closed_if_selected_video_changes_before_snapshot(
    prepared_plan,
    selected_sources,
    monkeypatch,
):
    original = compose_module._copy_verified_input
    selected = Path(selected_sources["shot_01"]["path"])
    changed = False

    def replace_before_copy(source, destination, expected_sha256, label):
        nonlocal changed
        if not changed and Path(source) == selected:
            selected.write_bytes(b"replacement-after-validation")
            changed = True
        return original(source, destination, expected_sha256, label)

    monkeypatch.setattr(
        compose_module,
        "_copy_verified_input",
        replace_before_copy,
    )
    with pytest.raises(PetSitcomComposeError, match="changed during snapshot"):
        compose_pet_sitcom(
            prepared_plan,
            command_runner=ComposeRunner(),
        )
    assert not prepared_plan.clean_output.exists()
    assert not prepared_plan.release_output.exists()


@pytest.mark.parametrize(
    "target_label",
    [
        "Sound stem music",
        "shot_01 Task 2 audio",
        "Release overlay opening_title",
    ],
)
def test_compose_fails_closed_if_bound_input_changes_before_snapshot(
    prepared_plan,
    monkeypatch,
    target_label,
):
    original = compose_module._copy_verified_input
    changed = False

    def replace_before_copy(source, destination, expected_sha256, label):
        nonlocal changed
        if not changed and label == target_label:
            Path(source).write_bytes(Path(source).read_bytes() + b"changed")
            changed = True
        return original(source, destination, expected_sha256, label)

    monkeypatch.setattr(
        compose_module,
        "_copy_verified_input",
        replace_before_copy,
    )
    with pytest.raises(PetSitcomComposeError, match="changed during snapshot"):
        compose_pet_sitcom(
            prepared_plan,
            command_runner=ComposeRunner(),
        )
    assert changed is True
    assert not prepared_plan.clean_output.exists()
    assert not prepared_plan.release_output.exists()


def test_failed_output_validation_keeps_existing_finals(prepared_plan):
    prepared_plan.clean_output.parent.mkdir(parents=True, exist_ok=True)
    prepared_plan.clean_output.write_bytes(b"existing-clean")
    prepared_plan.release_output.write_bytes(b"existing-release")
    runner = ComposeRunner(probe_payload={"streams": []})

    with pytest.raises(
        PetSitcomComposeError,
        match="one video and one audio",
    ):
        compose_pet_sitcom(prepared_plan, command_runner=runner)

    assert prepared_plan.clean_output.read_bytes() == b"existing-clean"
    assert prepared_plan.release_output.read_bytes() == b"existing-release"


def test_publish_failure_rolls_back_both_existing_finals(
    prepared_plan,
    monkeypatch,
):
    prepared_plan.clean_output.parent.mkdir(parents=True, exist_ok=True)
    prepared_plan.clean_output.write_bytes(b"existing-clean")
    prepared_plan.release_output.write_bytes(b"existing-release")
    build_pet_sitcom_ffmpeg_commands(prepared_plan)
    real_replace = os.replace
    failed = False

    def fail_release_stage(source, destination):
        nonlocal failed
        destination_path = Path(destination)
        if destination_path == prepared_plan.release_output and not failed:
            failed = True
            raise OSError("forced release publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(compose_module.os, "replace", fail_release_stage)
    with pytest.raises(PetSitcomComposeError, match="(?i)atomic publish"):
        compose_pet_sitcom(
            prepared_plan,
            command_runner=ComposeRunner(),
        )

    assert prepared_plan.clean_output.read_bytes() == b"existing-clean"
    assert prepared_plan.release_output.read_bytes() == b"existing-release"


@pytest.mark.parametrize("existing_indices", [(), (0,), (1,), (0, 1)])
def test_atomic_publish_handles_every_existing_final_combination(
    plan,
    existing_indices,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    for index, destination in enumerate(destinations):
        if index in existing_indices:
            destination.write_bytes(f"old-{index}".encode("ascii"))
    staged = []
    for index, destination in enumerate(destinations):
        path = destination.with_name(f".new-{index}.stage")
        path.write_bytes(f"new-{index}".encode("ascii"))
        staged.append(path)

    compose_module._publish_outputs_atomically(
        tuple(staged),
        destinations,
        plan.output_dir,
    )

    assert [path.read_bytes() for path in destinations] == [
        b"new-0",
        b"new-1",
    ]
    assert not compose_module._publish_journal_path(plan.output_dir).exists()


def test_overlapping_publish_is_locked_without_touching_shared_state(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    first_staged = (
        plan.output_dir / ".first-clean.stage",
        plan.output_dir / ".first-release.stage",
    )
    second_staged = (
        plan.output_dir / ".second-clean.stage",
        plan.output_dir / ".second-release.stage",
    )
    first_staged[0].write_bytes(b"first-clean")
    first_staged[1].write_bytes(b"first-release")
    second_staged[0].write_bytes(b"second-clean")
    second_staged[1].write_bytes(b"second-release")
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []
    real_create = compose_module._create_publish_transaction_dir

    def pause_first_publish(root, transaction_root, transaction):
        if threading.current_thread().name == "first-publisher":
            entered.set()
            assert release.wait(timeout=10)
        return real_create(root, transaction_root, transaction)

    monkeypatch.setattr(
        compose_module,
        "_create_publish_transaction_dir",
        pause_first_publish,
    )

    def publish_first() -> None:
        try:
            compose_module._publish_outputs_atomically(
                first_staged,
                destinations,
                plan.output_dir,
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(
        target=publish_first,
        name="first-publisher",
    )
    thread.start()
    assert entered.wait(timeout=10)
    journal = compose_module._publish_journal_path(plan.output_dir)

    try:
        with pytest.raises(PetSitcomComposeError, match="locked"):
            compose_module._publish_outputs_atomically(
                second_staged,
                destinations,
                plan.output_dir,
            )
        assert not journal.exists()
        assert [path.read_bytes() for path in destinations] == [
            b"old-clean",
            b"old-release",
        ]
        assert all(path.is_file() for path in second_staged)
    finally:
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert [path.read_bytes() for path in destinations] == [
        b"first-clean",
        b"first-release",
    ]


def test_compose_lock_is_held_across_render_validate_and_publish(
    prepared_plan,
):
    entered_render = threading.Event()
    release_render = threading.Event()
    errors: list[BaseException] = []

    class BlockingComposeRunner(ComposeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.blocked = False

        def __call__(self, command, **kwargs):
            rendered = " ".join(str(item) for item in command)
            if (
                Path(command[0]).name == "ffmpeg"
                and "-filter_complex" in command
                and "ebur128=peak=true" not in rendered
                and not self.blocked
            ):
                self.blocked = True
                entered_render.set()
                assert release_render.wait(timeout=10)
            return super().__call__(command, **kwargs)

    first_runner = BlockingComposeRunner()

    def run_first() -> None:
        try:
            compose_pet_sitcom(
                prepared_plan,
                command_runner=first_runner,
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_first, name="compose-a")
    thread.start()
    assert entered_render.wait(timeout=10)
    second_runner = ComposeRunner()
    try:
        with pytest.raises(PetSitcomComposeError, match="locked"):
            compose_pet_sitcom(
                prepared_plan,
                command_runner=second_runner,
            )
        assert second_runner.commands == []
    finally:
        release_render.set()
        thread.join(timeout=15)

    assert not thread.is_alive()
    assert errors == []
    assert prepared_plan.clean_output.is_file()
    assert prepared_plan.release_output.is_file()


def test_subprocess_lock_blocks_publish_and_sigkill_releases_it(plan):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".subprocess-clean.stage",
        plan.output_dir / ".subprocess-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    ready = plan.output_dir / ".lock-ready"
    script = """
import sys
import time
from pathlib import Path
import factory.pet_sitcom_compose as compose

root, ready = map(Path, sys.argv[1:])
with compose._compose_publish_lock(root):
    ready.write_text("locked", encoding="utf-8")
    while True:
        time.sleep(1)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(plan.output_dir),
            str(ready),
        ],
        cwd=Path(__file__).parents[1],
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file()

        with pytest.raises(PetSitcomComposeError, match="locked"):
            compose_module._publish_outputs_atomically(
                staged,
                destinations,
                plan.output_dir,
            )

        assert not compose_module._publish_journal_path(
            plan.output_dir
        ).exists()
        assert [path.read_bytes() for path in destinations] == [
            b"old-clean",
            b"old-release",
        ]
        assert all(path.is_file() for path in staged)
    finally:
        process.kill()
        process.wait(timeout=10)

    assert process.returncode == -signal.SIGKILL
    compose_module._publish_outputs_atomically(
        staged,
        destinations,
        plan.output_dir,
    )
    assert [path.read_bytes() for path in destinations] == [
        b"new-clean",
        b"new-release",
    ]


def test_publish_lock_registry_interrupt_after_store_revokes_token_and_retries(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = compose_module._publish_lock_path(plan.output_dir)
    real_open = compose_module.os.open
    real_close = compose_module.os.close
    opened: list[int] = []
    closed: list[int] = []

    class InterruptAfterStoreRegistry(dict):
        def __init__(self):
            super().__init__()
            self.interrupted = False

        def _interrupt_once(self):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt

        def __setitem__(self, key, value):
            dict.__setitem__(self, key, value)
            self._interrupt_once()

        def add(self, key):
            dict.__setitem__(self, key, object())
            self._interrupt_once()

        def discard(self, key):
            self.pop(key, None)

    registry = InterruptAfterStoreRegistry()

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        if descriptor in opened:
            closed.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(
        compose_module,
        "_PUBLISH_LOCKED_ROOTS",
        registry,
    )
    monkeypatch.setattr(compose_module.os, "open", tracked_open)
    monkeypatch.setattr(compose_module.os, "close", tracked_close)

    with pytest.raises(KeyboardInterrupt):
        with compose_module._compose_publish_lock(plan.output_dir):
            pass

    assert registry == {}
    assert opened == closed == []

    with compose_module._compose_publish_lock(plan.output_dir):
        pass

    assert registry == {}
    assert len(opened) == len(closed) == 1


def test_publish_lock_cleanup_does_not_remove_another_ownership_token(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)

    class CompatibleOwnershipRegistry(dict):
        def add(self, key):
            dict.__setitem__(self, key, object())

        def discard(self, key):
            self.pop(key, None)

    registry = CompatibleOwnershipRegistry()
    foreign_token = object()
    monkeypatch.setattr(
        compose_module,
        "_PUBLISH_LOCKED_ROOTS",
        registry,
    )

    with compose_module._compose_publish_lock(plan.output_dir):
        with compose_module._PUBLISH_LOCK_GUARD:
            registry[plan.output_dir] = foreign_token

    assert registry.get(plan.output_dir) is foreign_token
    with compose_module._PUBLISH_LOCK_GUARD:
        registry.pop(plan.output_dir)

    with compose_module._compose_publish_lock(plan.output_dir):
        pass
    assert registry == {}


def test_publish_lock_unlock_interrupt_closes_fd_and_allows_retry(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = compose_module._publish_lock_path(plan.output_dir)
    real_open = compose_module.os.open
    real_close = compose_module.os.close
    real_flock = compose_module.fcntl.flock
    opened: list[int] = []
    closed: list[int] = []
    interrupted = False

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        if descriptor in opened:
            closed.append(descriptor)
        return real_close(descriptor)

    def interrupt_first_unlock(descriptor, operation):
        nonlocal interrupted
        if operation == compose_module.fcntl.LOCK_UN and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return real_flock(descriptor, operation)

    monkeypatch.setattr(compose_module.os, "open", tracked_open)
    monkeypatch.setattr(compose_module.os, "close", tracked_close)
    monkeypatch.setattr(
        compose_module.fcntl,
        "flock",
        interrupt_first_unlock,
    )

    with pytest.raises(PetSitcomComposeError, match="unlock"):
        with compose_module._compose_publish_lock(plan.output_dir):
            pass

    assert len(opened) == len(closed) == 1
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS
    with compose_module._compose_publish_lock(plan.output_dir):
        pass
    assert len(opened) == len(closed) == 2


def test_publish_lock_close_error_clears_registry_and_allows_retry(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = compose_module._publish_lock_path(plan.output_dir)
    real_open = compose_module.os.open
    real_close = compose_module.os.close
    opened: list[int] = []
    closed: list[int] = []
    failed = False

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            opened.append(descriptor)
        return descriptor

    def fail_first_close(descriptor):
        nonlocal failed
        result = real_close(descriptor)
        if descriptor in opened:
            closed.append(descriptor)
            if not failed:
                failed = True
                raise OSError("forced lock close failure")
        return result

    monkeypatch.setattr(compose_module.os, "open", tracked_open)
    monkeypatch.setattr(compose_module.os, "close", fail_first_close)

    with pytest.raises(PetSitcomComposeError, match="close"):
        with compose_module._compose_publish_lock(plan.output_dir):
            pass

    assert len(opened) == len(closed) == 1
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS
    with compose_module._compose_publish_lock(plan.output_dir):
        pass
    assert len(opened) == len(closed) == 2


def test_publish_lock_cleanup_errors_do_not_mask_body_error(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = compose_module._publish_lock_path(plan.output_dir)
    real_open = compose_module.os.open
    real_close = compose_module.os.close
    real_flock = compose_module.fcntl.flock
    opened: list[int] = []
    closed: list[int] = []
    unlock_failed = False
    close_failed = False
    body_error = RuntimeError("body failed")

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            opened.append(descriptor)
        return descriptor

    def fail_first_close(descriptor):
        nonlocal close_failed
        result = real_close(descriptor)
        if descriptor in opened:
            closed.append(descriptor)
            if not close_failed:
                close_failed = True
                raise OSError("forced lock close failure")
        return result

    def fail_first_unlock(descriptor, operation):
        nonlocal unlock_failed
        if operation == compose_module.fcntl.LOCK_UN and not unlock_failed:
            unlock_failed = True
            raise KeyboardInterrupt
        return real_flock(descriptor, operation)

    monkeypatch.setattr(compose_module.os, "open", tracked_open)
    monkeypatch.setattr(compose_module.os, "close", fail_first_close)
    monkeypatch.setattr(
        compose_module.fcntl,
        "flock",
        fail_first_unlock,
    )

    with pytest.raises(RuntimeError, match="body failed") as caught:
        with compose_module._compose_publish_lock(plan.output_dir):
            raise body_error

    assert caught.value is body_error
    assert any("unlock" in note for note in caught.value.__notes__)
    assert any("close" in note for note in caught.value.__notes__)
    assert len(opened) == len(closed) == 1
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS
    with compose_module._compose_publish_lock(plan.output_dir):
        pass
    assert len(opened) == len(closed) == 2


def test_publish_lock_unsafe_cleanup_diagnostics_preserve_primary(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    real_flock = compose_module.fcntl.flock
    unlock_failed = False
    note_attempts: list[str] = []

    class UnstringableCleanupError(RuntimeError):
        def __str__(self):
            raise AssertionError("cleanup __str__ must not be called")

    class NoteRejectingPrimary(RuntimeError):
        def add_note(self, note):
            note_attempts.append(note)
            raise AssertionError("primary add_note failed")

    cleanup_error = UnstringableCleanupError()
    primary_error = NoteRejectingPrimary("primary body failure")

    def fail_first_unlock(descriptor, operation):
        nonlocal unlock_failed
        if operation == compose_module.fcntl.LOCK_UN and not unlock_failed:
            unlock_failed = True
            raise cleanup_error
        return real_flock(descriptor, operation)

    monkeypatch.setattr(
        compose_module.fcntl,
        "flock",
        fail_first_unlock,
    )

    with pytest.raises(NoteRejectingPrimary) as caught:
        with compose_module._compose_publish_lock(plan.output_dir):
            raise primary_error

    assert caught.value is primary_error
    assert len(note_attempts) == 1
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS
    with compose_module._compose_publish_lock(plan.output_dir):
        pass


def test_publish_lock_acquire_failure_and_double_exit_close_once(
    plan,
    monkeypatch,
):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = compose_module._publish_lock_path(plan.output_dir)
    real_open = compose_module.os.open
    real_close = compose_module.os.close
    real_flock = compose_module.fcntl.flock
    opened: list[int] = []
    closed: list[int] = []
    acquire_failed = False

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor):
        if descriptor in opened:
            closed.append(descriptor)
        return real_close(descriptor)

    def fail_first_acquire(descriptor, operation):
        nonlocal acquire_failed
        if (
            operation
            == compose_module.fcntl.LOCK_EX | compose_module.fcntl.LOCK_NB
            and not acquire_failed
        ):
            acquire_failed = True
            raise BlockingIOError("forced acquire failure")
        return real_flock(descriptor, operation)

    monkeypatch.setattr(compose_module.os, "open", tracked_open)
    monkeypatch.setattr(compose_module.os, "close", tracked_close)
    monkeypatch.setattr(
        compose_module.fcntl,
        "flock",
        fail_first_acquire,
    )

    with pytest.raises(PetSitcomComposeError, match="locked"):
        with compose_module._compose_publish_lock(plan.output_dir):
            pass

    assert len(opened) == len(closed) == 1
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS

    manager = compose_module._compose_publish_lock(plan.output_dir)
    manager.__enter__()
    manager.__exit__(None, None, None)
    manager.__exit__(None, None, None)
    assert len(opened) == len(closed) == 2
    assert plan.output_dir not in compose_module._PUBLISH_LOCKED_ROOTS


def test_publish_rejects_symlinked_fixed_lock_path(plan, tmp_path):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations = (plan.clean_output, plan.release_output)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".lock-clean.stage",
        plan.output_dir / ".lock-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"outside")
    compose_module._publish_lock_path(plan.output_dir).symlink_to(outside)

    with pytest.raises(PetSitcomComposeError, match="symlink|canonical"):
        compose_module._publish_outputs_atomically(
            staged,
            destinations,
            plan.output_dir,
        )

    assert outside.read_bytes() == b"outside"
    assert [path.read_bytes() for path in destinations] == [
        b"old-clean",
        b"old-release",
    ]
    assert all(path.is_file() for path in staged)


def _seed_publish_transaction(
    plan,
    *,
    transaction_id: str,
    phase: str,
    old_values: tuple[bytes | None, bytes | None],
    current_values: tuple[bytes | None, bytes | None],
) -> tuple[Path, Path]:
    destinations = (plan.clean_output, plan.release_output)
    roles = ("clean", "release")
    new_values = (b"new-clean", b"new-release")
    transaction = (
        plan.output_dir
        / ".pet-sitcom-compose-transactions"
        / transaction_id
    )
    transaction.mkdir(parents=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, (role, destination) in enumerate(
        zip(roles, destinations, strict=True)
    ):
        new_copy = transaction / f"{index:02d}-{role}.new"
        old_backup = transaction / f"{index:02d}-{role}.old"
        new_copy.write_bytes(new_values[index])
        if old_values[index] is not None:
            old_backup.write_bytes(old_values[index])
        if current_values[index] is not None:
            destination.write_bytes(current_values[index])
        entries.append(
            {
                "role": role,
                "index": index,
                "destination": str(destination),
                "new_copy": str(new_copy),
                "old_backup": str(old_backup),
                "had_original": old_values[index] is not None,
                "old_sha256": (
                    hashlib.sha256(old_values[index]).hexdigest()
                    if old_values[index] is not None
                    else ""
                ),
                "new_sha256": hashlib.sha256(
                    new_values[index]
                ).hexdigest(),
            }
        )
    journal = compose_module._publish_journal_path(plan.output_dir)
    journal.write_text(
        json.dumps(
            {
                "schema_version": compose_module.PUBLISH_JOURNAL_SCHEMA,
                "transaction_id": transaction_id,
                "validated": True,
                "phase": phase,
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return journal, transaction


def test_second_publish_keyboard_interrupt_rolls_back_consistent_old_pair(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".new-clean.stage",
        plan.output_dir / ".new-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    real_replace = os.replace
    interrupted = False

    def interrupt_second(source, destination):
        nonlocal interrupted
        if Path(destination) == destinations[1] and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return real_replace(source, destination)

    monkeypatch.setattr(compose_module.os, "replace", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        compose_module._publish_outputs_atomically(
            staged,
            destinations,
            plan.output_dir,
        )

    assert destinations[0].read_bytes() == b"old-clean"
    assert destinations[1].read_bytes() == b"old-release"
    assert not compose_module._publish_journal_path(plan.output_dir).exists()


def test_post_commit_cleanup_keyboard_interrupt_returns_verified_new_pair(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".new-clean.stage",
        plan.output_dir / ".new-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    journal = compose_module._publish_journal_path(plan.output_dir)
    real_fsync_dir = compose_module._fsync_dir
    interrupted = False

    def interrupt_cleanup(path):
        nonlocal interrupted
        if (
            not interrupted
            and Path(path) == plan.output_dir
            and not journal.exists()
            and all(
                destination.is_file()
                and destination.read_bytes().startswith(b"new-")
                for destination in destinations
            )
        ):
            interrupted = True
            raise KeyboardInterrupt
        return real_fsync_dir(path)

    monkeypatch.setattr(
        compose_module,
        "_fsync_dir",
        interrupt_cleanup,
    )
    compose_module._publish_outputs_atomically(
        staged,
        destinations,
        plan.output_dir,
    )

    assert interrupted is True
    assert [path.read_bytes() for path in destinations] == [
        b"new-clean",
        b"new-release",
    ]


def test_startup_recovery_completes_hash_verified_new_pair(plan):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, transaction = _seed_publish_transaction(
        plan,
        transaction_id="0123456789abcdef",
        phase="published",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"old-release"),
    )

    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )

    assert destinations[0].read_bytes() == b"new-clean"
    assert destinations[1].read_bytes() == b"new-release"
    assert not journal.exists()
    assert not transaction.exists()


def test_startup_recovery_rolls_back_uncommitted_new_pair(plan):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, _ = _seed_publish_transaction(
        plan,
        transaction_id="fedcba9876543210",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )

    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )

    assert destinations[0].read_bytes() == b"old-clean"
    assert destinations[1].read_bytes() == b"old-release"
    assert not journal.exists()


def test_recovery_second_replace_interrupt_never_returns_mixed_pair(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, _ = _seed_publish_transaction(
        plan,
        transaction_id="1111111111111111",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )
    real_replace = os.replace
    replacement_count = 0
    interrupted = False

    def interrupt_second_restore(source, destination):
        nonlocal replacement_count, interrupted
        if Path(destination) in destinations:
            replacement_count += 1
            if replacement_count == 2 and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
        return real_replace(source, destination)

    monkeypatch.setattr(
        compose_module.os,
        "replace",
        interrupt_second_restore,
    )
    with pytest.raises(KeyboardInterrupt):
        compose_module._recover_publish_transaction(
            plan.output_dir,
            destinations,
        )

    assert [path.read_bytes() for path in destinations] == [
        b"old-clean",
        b"old-release",
    ]
    assert journal.exists()

    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )
    assert [path.read_bytes() for path in destinations] == [
        b"old-clean",
        b"old-release",
    ]
    assert not journal.exists()


@pytest.mark.parametrize(
    ("failure_point", "interruptions"),
    [
        ("validation", 1),
        ("validation", 2),
        ("first_install", 1),
        ("first_install", 2),
        ("second_install", 1),
        ("second_install", 2),
        ("pair_check", 1),
        ("pair_check", 2),
        ("post_pair_fsync", 1),
        ("post_fsync_pair_check", 1),
    ],
)
def test_recovery_retries_every_stage_before_rethrowing_interrupt(
    plan,
    monkeypatch,
    failure_point,
    interruptions,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, _ = _seed_publish_transaction(
        plan,
        transaction_id="3131313131313131",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )
    calls = 0
    if failure_point == "validation":
        original = compose_module._validate_old_materials

        def injected(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= interruptions:
                raise KeyboardInterrupt
            return original(*args, **kwargs)

        monkeypatch.setattr(
            compose_module,
            "_validate_old_materials",
            injected,
        )
    elif failure_point in {"first_install", "second_install"}:
        original = compose_module._install_transaction_copy
        fail_index = 0 if failure_point == "first_install" else 1

        def injected(entry, *args, **kwargs):
            nonlocal calls
            if int(entry["index"]) == fail_index:
                calls += 1
                if calls <= interruptions:
                    raise KeyboardInterrupt
            return original(entry, *args, **kwargs)

        monkeypatch.setattr(
            compose_module,
            "_install_transaction_copy",
            injected,
        )
    elif failure_point == "pair_check":
        original = compose_module._pair_matches

        def injected(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= interruptions:
                raise KeyboardInterrupt
            return original(*args, **kwargs)

        monkeypatch.setattr(compose_module, "_pair_matches", injected)
    elif failure_point == "post_pair_fsync":
        original_pair_matches = compose_module._pair_matches
        original_fsync_dir = compose_module._fsync_dir
        destination_parent = destinations[0].parent
        pair_check_succeeded = False
        fsync_armed = False

        def tracked_pair_check(*args, **kwargs):
            nonlocal pair_check_succeeded, fsync_armed
            result = original_pair_matches(*args, **kwargs)
            if result:
                pair_check_succeeded = True
                fsync_armed = True
            return result

        def injected(path):
            nonlocal calls, fsync_armed
            if (
                Path(path) == destination_parent
                and pair_check_succeeded
                and fsync_armed
            ):
                fsync_armed = False
                calls += 1
                if calls <= interruptions:
                    raise KeyboardInterrupt
            return original_fsync_dir(path)

        monkeypatch.setattr(
            compose_module,
            "_pair_matches",
            tracked_pair_check,
        )
        monkeypatch.setattr(compose_module, "_fsync_dir", injected)
    else:
        original_pair_matches = compose_module._pair_matches
        original_fsync_dir = compose_module._fsync_dir
        destination_parent = destinations[0].parent
        first_pair_check_succeeded = False
        post_pair_fsync_completed = False

        def injected(*args, **kwargs):
            nonlocal calls, first_pair_check_succeeded
            nonlocal post_pair_fsync_completed
            if post_pair_fsync_completed:
                post_pair_fsync_completed = False
                calls += 1
                if calls <= interruptions:
                    raise KeyboardInterrupt
            result = original_pair_matches(*args, **kwargs)
            if result:
                first_pair_check_succeeded = True
            return result

        def tracked_fsync(path):
            nonlocal first_pair_check_succeeded
            nonlocal post_pair_fsync_completed
            result = original_fsync_dir(path)
            if (
                Path(path) == destination_parent
                and first_pair_check_succeeded
            ):
                first_pair_check_succeeded = False
                post_pair_fsync_completed = True
            return result

        monkeypatch.setattr(compose_module, "_pair_matches", injected)
        monkeypatch.setattr(
            compose_module,
            "_fsync_dir",
            tracked_fsync,
        )

    with pytest.raises(KeyboardInterrupt):
        compose_module._recover_publish_transaction(
            plan.output_dir,
            destinations,
        )

    assert calls > interruptions
    assert [path.read_bytes() for path in destinations] == [
        b"old-clean",
        b"old-release",
    ]
    assert journal.exists()

    monkeypatch.undo()
    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )
    assert not journal.exists()


def test_cleanup_keeps_journal_with_different_transaction_identity(plan):
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, _ = _seed_publish_transaction(
        plan,
        transaction_id="4141414141414141",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )

    compose_module._cleanup_publish_transaction(
        plan.output_dir,
        journal,
        "4242424242424242",
        remove_journal=True,
    )

    assert journal.exists()
    payload = json.loads(journal.read_text(encoding="utf-8"))
    assert payload["transaction_id"] == "4141414141414141"


def test_recovery_persistent_durability_failure_retains_journal_and_materials(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, transaction = _seed_publish_transaction(
        plan,
        transaction_id="4343434343434343",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )
    real_fsync_dir = compose_module._fsync_dir

    def remain_unwritable(path):
        if Path(path) == destinations[0].parent:
            raise OSError("persistent destination fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(
        compose_module,
        "_fsync_dir",
        remain_unwritable,
    )
    with pytest.raises(
        PetSitcomComposeError,
        match="durably restore.*retained",
    ):
        compose_module._recover_publish_transaction(
            plan.output_dir,
            destinations,
        )

    assert journal.is_file()
    assert transaction.is_dir()
    assert {
        path.name for path in transaction.iterdir()
    } == {
        "00-clean.new",
        "00-clean.old",
        "01-release.new",
        "01-release.old",
    }

    monkeypatch.undo()
    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )
    assert not journal.exists()
    assert not transaction.exists()


@pytest.mark.parametrize("existing_indices", [(), (0,), (1,), (0, 1)])
def test_sigkill_publish_recovers_old_pair_on_next_start(
    plan,
    existing_indices,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    old_values = (b"old-clean", b"old-release")
    for index in existing_indices:
        destinations[index].write_bytes(old_values[index])
    staged = (
        plan.output_dir / ".child-clean.stage",
        plan.output_dir / ".child-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    script = """
import os
import signal
import sys
from pathlib import Path
import factory.pet_sitcom_compose as compose

root, clean, release, staged_clean, staged_release = map(Path, sys.argv[1:])
real_replace = compose.os.replace
def crash_before_release(source, destination):
    if Path(destination) == release:
        os.kill(os.getpid(), signal.SIGKILL)
    return real_replace(source, destination)
compose.os.replace = crash_before_release
compose._publish_outputs_atomically(
    (staged_clean, staged_release),
    (clean, release),
    root,
)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(plan.output_dir),
            str(destinations[0]),
            str(destinations[1]),
            str(staged[0]),
            str(staged[1]),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=30,
    )

    assert result.returncode == -signal.SIGKILL
    compose_module._recover_publish_transaction(
        plan.output_dir,
        destinations,
    )
    for index, destination in enumerate(destinations):
        if index in existing_indices:
            assert destination.read_bytes() == old_values[index]
        else:
            assert not destination.exists()


def test_malicious_journal_cannot_redirect_cleanup(plan):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    journal, _ = _seed_publish_transaction(
        plan,
        transaction_id="2222222222222222",
        phase="publishing",
        old_values=(b"old-clean", b"old-release"),
        current_values=(b"new-clean", b"new-release"),
    )
    protected = plan.output_dir / "do-not-delete.txt"
    protected.write_bytes(b"protected")
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["entries"][0]["new_copy"] = str(protected)
    journal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetSitcomComposeError, match="journal"):
        compose_module._recover_publish_transaction(
            plan.output_dir,
            destinations,
        )

    assert protected.read_bytes() == b"protected"


def test_publish_fsync_failure_rolls_back_old_pair(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".new-clean.stage",
        plan.output_dir / ".new-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    real_fsync_dir = compose_module._fsync_dir
    failed = False

    def fail_after_first_publish(path):
        nonlocal failed
        if (
            not failed
            and destinations[0].is_file()
            and destinations[0].read_bytes() == b"new-clean"
        ):
            failed = True
            raise OSError("forced publish fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(
        compose_module,
        "_fsync_dir",
        fail_after_first_publish,
    )
    with pytest.raises(PetSitcomComposeError, match="Atomic publish failed"):
        compose_module._publish_outputs_atomically(
            staged,
            destinations,
            plan.output_dir,
        )

    assert destinations[0].read_bytes() == b"old-clean"
    assert destinations[1].read_bytes() == b"old-release"
    assert not compose_module._publish_journal_path(plan.output_dir).exists()


def test_publish_journal_fsync_failure_keeps_old_pair(
    plan,
    monkeypatch,
):
    destinations = (plan.clean_output, plan.release_output)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    destinations[0].parent.mkdir(parents=True, exist_ok=True)
    destinations[0].write_bytes(b"old-clean")
    destinations[1].write_bytes(b"old-release")
    staged = (
        plan.output_dir / ".new-clean.stage",
        plan.output_dir / ".new-release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")
    real_fsync_dir = compose_module._fsync_dir
    failed = False

    def fail_first_root_fsync(path):
        nonlocal failed
        if not failed and Path(path) == plan.output_dir:
            failed = True
            raise OSError("forced journal fsync failure")
        return real_fsync_dir(path)

    monkeypatch.setattr(
        compose_module,
        "_fsync_dir",
        fail_first_root_fsync,
    )
    with pytest.raises(PetSitcomComposeError, match="journal failed"):
        compose_module._publish_outputs_atomically(
            staged,
            destinations,
            plan.output_dir,
        )

    assert destinations[0].read_bytes() == b"old-clean"
    assert destinations[1].read_bytes() == b"old-release"
    assert not compose_module._publish_journal_path(plan.output_dir).exists()


def test_publish_rejects_symlinked_destination_parent(plan, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = plan.output_dir / "linked"
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(outside, target_is_directory=True)
    destinations = (linked / "clean.mp4", linked / "release.mp4")
    staged = (
        plan.output_dir / ".clean.stage",
        plan.output_dir / ".release.stage",
    )
    staged[0].write_bytes(b"new-clean")
    staged[1].write_bytes(b"new-release")

    with pytest.raises(PetSitcomComposeError, match="symlink|inside"):
        compose_module._publish_outputs_atomically(
            staged,
            destinations,
            plan.output_dir,
        )


def test_output_validation_requires_high_profile_and_30_fps(tmp_path):
    output = tmp_path / "invalid-video.mp4"
    _write_faststart_stub(output)

    with pytest.raises(PetSitcomComposeError, match="30 fps"):
        compose_module._validate_output(
            output,
            ComposeRunner(
                probe_payload=_valid_output_probe_payload(fps="24/1")
            ),
            "ffprobe",
        )

    with pytest.raises(PetSitcomComposeError, match="High profile"):
        compose_module._validate_output(
            output,
            ComposeRunner(
                probe_payload=_valid_output_probe_payload(profile="Main")
            ),
            "ffprobe",
        )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            _valid_output_probe_payload(
                video_duration="54.0",
                audio_duration="1.0",
            ),
            "audio stream duration",
        ),
        (
            _valid_output_probe_payload(
                video_duration="1.0",
                audio_duration="54.0",
            ),
            "video stream duration",
        ),
        (
            _valid_output_probe_payload(fps="30/1", r_fps="60/2"),
            "constant 30 fps",
        ),
        (
            _valid_output_probe_payload(fps="30000/1001", r_fps="30/1"),
            "constant 30 fps",
        ),
        (
            _valid_output_probe_payload(fps="nan", r_fps="30/1"),
            "constant 30 fps",
        ),
        (
            _valid_output_probe_payload(fps="30/0", r_fps="30/1"),
            "constant 30 fps",
        ),
    ],
)
def test_output_validation_requires_each_stream_duration_and_exact_cfr(
    tmp_path,
    payload,
    expected,
):
    output = tmp_path / "invalid-stream-contract.mp4"
    _write_faststart_stub(output)

    with pytest.raises(PetSitcomComposeError, match=expected):
        compose_module._validate_output(
            output,
            ComposeRunner(probe_payload=payload),
            "ffprobe",
        )


def test_output_validation_requires_explicit_stereo_layout(tmp_path):
    output = tmp_path / "missing-layout.mp4"
    _write_faststart_stub(output)
    payload = _valid_output_probe_payload()
    payload["streams"][1].pop("channel_layout")

    with pytest.raises(PetSitcomComposeError, match="stereo"):
        compose_module._validate_output(
            output,
            ComposeRunner(probe_payload=payload),
            "ffprobe",
        )


def test_output_validation_rejects_extra_container_stream(tmp_path):
    output = tmp_path / "extra-stream.mp4"
    _write_faststart_stub(output)
    payload = _valid_output_probe_payload()
    payload["streams"].append(
        {"codec_type": "subtitle", "codec_name": "mov_text"}
    )

    with pytest.raises(PetSitcomComposeError, match="one video and one audio"):
        compose_module._validate_output(
            output,
            ComposeRunner(probe_payload=payload),
            "ffprobe",
        )


@pytest.mark.parametrize(
    "timestamps",
    [
        [index / 30.0 for index in range(10)],
        [
            0.0,
            1 / 30,
            2 / 30,
            3 / 30,
            3 / 30 + 0.05,
            *[3 / 30 + 0.05 + index / 30 for index in range(1, 28)],
        ],
        [0.0, 1 / 30, float("nan"), 3 / 30, *[
            index / 30 for index in range(4, 35)
        ]],
    ],
)
def test_output_validation_rejects_short_malformed_or_vfr_pts(
    tmp_path,
    timestamps,
):
    output = tmp_path / "invalid-frame-pts.mp4"
    _write_faststart_stub(output)

    with pytest.raises(PetSitcomComposeError, match="frame timestamps|CFR"):
        compose_module._validate_output(
            output,
            ComposeRunner(frame_timestamps=timestamps),
            "ffprobe",
        )


def test_output_validation_reads_all_frame_timestamps(tmp_path):
    output = tmp_path / "all-frame-pts.mp4"
    _write_faststart_stub(output)
    runner = ComposeRunner()

    compose_module._validate_output(output, runner, "ffprobe")

    frame_command = next(
        command
        for command in runner.commands
        if "frame=best_effort_timestamp_time" in " ".join(command)
    )
    assert "-read_intervals" not in frame_command


def test_output_validation_queries_and_requires_finite_timeline_starts(
    tmp_path,
):
    output = tmp_path / "timeline-starts.mp4"
    _write_faststart_stub(output)
    runner = ComposeRunner()

    compose_module._validate_output(output, runner, "ffprobe")

    contract_command = next(
        command
        for command in runner.commands
        if Path(command[0]).name == "ffprobe"
        and "frame=best_effort_timestamp_time" not in " ".join(command)
    )
    show_entries = contract_command[
        contract_command.index("-show_entries") + 1
    ]
    assert "format=duration,start_time" in show_entries
    assert "stream=" in show_entries
    assert "start_time" in show_entries

    invalid_payloads = []
    missing_container = _valid_output_probe_payload()
    missing_container["format"].pop("start_time")
    invalid_payloads.append(missing_container)
    nonfinite_video = _valid_output_probe_payload()
    nonfinite_video["streams"][0]["start_time"] = "nan"
    invalid_payloads.append(nonfinite_video)
    bool_audio = _valid_output_probe_payload()
    bool_audio["streams"][1]["start_time"] = True
    invalid_payloads.append(bool_audio)

    for payload in invalid_payloads:
        with pytest.raises(PetSitcomComposeError, match="start"):
            compose_module._validate_output(
                output,
                ComposeRunner(probe_payload=payload),
                "ffprobe",
            )


def test_output_validation_binds_first_and_last_presentation_pts(
    tmp_path,
):
    output = tmp_path / "presentation-pts.mp4"
    _write_faststart_stub(output)
    shifted = [1.0 + index / 30.0 for index in range(1620)]
    short_tail_step = 1 / 30.0 - 0.0004
    short_tail = [
        index * short_tail_step for index in range(1620)
    ]

    for timestamps in (shifted, short_tail):
        with pytest.raises(
            PetSitcomComposeError,
            match="presentation|timeline|start|end",
        ):
            compose_module._validate_output(
                output,
                ComposeRunner(frame_timestamps=timestamps),
                "ffprobe",
            )


def test_output_validation_rejects_audio_delay_and_short_tails(tmp_path):
    output = tmp_path / "audio-sync.mp4"
    _write_faststart_stub(output)
    delayed_audio = _valid_output_probe_payload()
    delayed_audio["streams"][1]["start_time"] = "0.300"
    short_audio = _valid_output_probe_payload(audio_duration="53.900")
    short_container = _valid_output_probe_payload(duration="53.900")
    short_container["streams"][0]["duration"] = "54.0"
    short_container["streams"][1]["duration"] = "54.0"

    for payload in (delayed_audio, short_audio, short_container):
        with pytest.raises(
            PetSitcomComposeError,
            match="timeline|synchron|start|end",
        ):
            compose_module._validate_output(
                output,
                ComposeRunner(probe_payload=payload),
                "ffprobe",
            )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "nonfinite", "duplicate", "reverse"],
)
def test_full_frame_timestamp_validation_rejects_malformed_pts(
    tmp_path,
    mutation,
):
    output = tmp_path / f"{mutation}-pts.mp4"
    output.write_bytes(b"frame-probe-target")
    frames = [
        {"best_effort_timestamp_time": str(index / 30.0)}
        for index in range(1620)
    ]
    if mutation == "missing":
        frames[900] = {}
    elif mutation == "nonfinite":
        frames[900]["best_effort_timestamp_time"] = "nan"
    elif mutation == "duplicate":
        frames[900]["best_effort_timestamp_time"] = frames[899][
            "best_effort_timestamp_time"
        ]
    else:
        frames[900]["best_effort_timestamp_time"] = str(898 / 30.0)

    def frame_runner(command, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps({"frames": frames}),
            stderr="",
        )

    with pytest.raises(PetSitcomComposeError, match="timestamps|CFR"):
        compose_module._validate_cfr_frame_timestamps(
            output,
            frame_runner,
            "ffprobe",
            duration_seconds=54.0,
        )


def test_full_frame_timestamp_validation_rejects_cumulative_phase_drift(
    tmp_path,
):
    output = tmp_path / "cumulative-phase-drift.mp4"
    output.write_bytes(b"frame-probe-target")
    interval_count = 1619
    forward_intervals = 809
    forward_drift = 0.00049
    recovery_drift = (
        forward_intervals
        * forward_drift
        / (interval_count - forward_intervals)
    )
    timestamps = [0.0]
    for index in range(interval_count):
        adjustment = (
            forward_drift
            if index < forward_intervals
            else -recovery_drift
        )
        timestamps.append(timestamps[-1] + 1 / 30.0 + adjustment)

    assert timestamps[-1] == pytest.approx(1619 / 30.0, abs=1e-9)
    assert timestamps[forward_intervals] - forward_intervals / 30.0 > 0.39

    def frame_runner(command, **kwargs):
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "frames": [
                        {"best_effort_timestamp_time": str(value)}
                        for value in timestamps
                    ]
                }
            ),
            stderr="",
        )

    with pytest.raises(PetSitcomComposeError, match="phase|CFR"):
        compose_module._validate_cfr_frame_timestamps(
            output,
            frame_runner,
            "ffprobe",
            duration_seconds=54.0,
        )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
def test_real_ffprobe_rejects_middle_phase_drift_at_reported_30_fps(
    tmp_path,
):
    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    output = tmp_path / "middle-phase-drift.mp4"
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
            "testsrc2=size=32x48:rate=30:duration=54",
            "-vf",
            (
                "settb=expr=1/90000,"
                "setpts=N*3000+"
                "if(lt(N\\,300)\\,0\\,"
                "if(lte(N\\,810)\\,(N-300)*40\\,"
                "if(lte(N\\,1320)\\,"
                "(510-(N-810))*40\\,0)))"
            ),
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "1:90000",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-video_track_timescale",
            "90000",
            "-an",
            str(output),
        ],
        check=True,
        timeout=30,
    )
    rates = json.loads(
        subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(output),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            timeout=30,
        ).stdout
    )["streams"][0]
    assert rates["avg_frame_rate"] == "30/1"
    assert rates["r_frame_rate"] == "30/1"

    with pytest.raises(PetSitcomComposeError, match="phase|CFR"):
        compose_module._validate_cfr_frame_timestamps(
            output,
            subprocess.run,
            ffprobe,
            duration_seconds=54.0,
        )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
@pytest.mark.parametrize("dropped_frame", [70, 810, 1600])
def test_real_ffprobe_rejects_late_vfr_even_when_rate_fields_claim_30(
    tmp_path,
    dropped_frame,
):
    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    output = tmp_path / f"constructed-vfr-{dropped_frame}.mp4"
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
            "testsrc2=size=32x48:rate=30:duration=54",
            "-vf",
            rf"select=not(eq(n\,{dropped_frame}))",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-an",
            str(output),
        ],
        check=True,
        timeout=30,
    )
    contract_probe = _valid_output_probe_payload()
    assert contract_probe["streams"][0]["avg_frame_rate"] == "30/1"
    assert contract_probe["streams"][0]["r_frame_rate"] == "30/1"
    fake = ComposeRunner(probe_payload=contract_probe)

    def hybrid_runner(command, **kwargs):
        if (
            Path(command[0]).name == "ffprobe"
            and "frame=best_effort_timestamp_time" in " ".join(command)
        ):
            return subprocess.run(command, **kwargs)
        return fake(command, **kwargs)

    with pytest.raises(PetSitcomComposeError, match="CFR"):
        compose_module._validate_output(
            output,
            hybrid_runner,
            ffprobe,
        )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
def test_real_ffprobe_rejects_cfr_video_shifted_by_one_second(tmp_path):
    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    output = tmp_path / "shifted-cfr.mp4"
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
            "testsrc2=size=32x48:rate=30:duration=54",
            "-vf",
            "setpts=PTS+1/TB",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-movflags",
            "+faststart",
            "-an",
            str(output),
        ],
        check=True,
        timeout=30,
    )
    contract_probe = _valid_output_probe_payload()
    fake = ComposeRunner(probe_payload=contract_probe)

    def hybrid_runner(command, **kwargs):
        if (
            Path(command[0]).name == "ffprobe"
            and "frame=best_effort_timestamp_time" in " ".join(command)
        ):
            return subprocess.run(command, **kwargs)
        return fake(command, **kwargs)

    with pytest.raises(
        PetSitcomComposeError,
        match="presentation|timeline|start",
    ):
        compose_module._validate_output(
            output,
            hybrid_runner,
            ffprobe,
        )


def test_output_validation_requires_contract_audio_bitrate(tmp_path):
    output = tmp_path / "low-bitrate.mp4"
    _write_faststart_stub(output)

    with pytest.raises(PetSitcomComposeError, match="160 kbps"):
        compose_module._validate_output(
            output,
            ComposeRunner(
                probe_payload=_valid_output_probe_payload(
                    audio_bit_rate="128000"
                )
            ),
            "ffprobe",
        )


@pytest.mark.parametrize(
    ("runner", "expected"),
    [
        (ComposeRunner(integrated_lufs=-15.2), "loudness"),
        (ComposeRunner(true_peak_dbtp=-1.4), "true peak"),
        (ComposeRunner(max_volume_db=0.0), "clipping"),
    ],
)
def test_output_validation_requires_loudness_peak_and_no_clipping(
    tmp_path,
    runner,
    expected,
):
    output = tmp_path / f"{expected}.mp4"
    _write_faststart_stub(output)

    with pytest.raises(PetSitcomComposeError, match=expected):
        compose_module._validate_output(output, runner, "ffprobe")


def test_output_validation_requires_duration_and_faststart(tmp_path):
    duration = tmp_path / "long.mp4"
    _write_faststart_stub(duration)
    with pytest.raises(PetSitcomComposeError, match="54 second"):
        compose_module._validate_output(
            duration,
            ComposeRunner(
                probe_payload=_valid_output_probe_payload(duration="54.16")
            ),
            "ffprobe",
        )

    atom_order = tmp_path / "not-faststart.mp4"
    atom_order.write_bytes(
        b"".join(
            _mp4_box(kind) for kind in (b"ftyp", b"mdat", b"moov")
        )
    )
    with pytest.raises(PetSitcomComposeError, match="faststart"):
        compose_module._validate_output(
            atom_order,
            ComposeRunner(),
            "ffprobe",
        )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="local FFmpeg tools are unavailable",
)
def test_local_ffmpeg_runs_full_release_filter_graph(
    prepared_plan,
    selected_sources,
):
    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    source = prepared_plan.output_dir / "integration" / "source.mp4"
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
            "color=c=gray:s=32x48:r=2:d=8",
            "-an",
            "-c:v",
            "mpeg4",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    for item in selected_sources.values():
        shutil.copyfile(source, Path(item["path"]))

    outputs = (
        prepared_plan.output_dir / "integration" / "clean.mp4",
        prepared_plan.output_dir / "integration" / "release.mp4",
    )
    commands = build_pet_sitcom_ffmpeg_commands(
        prepared_plan,
        ffmpeg_bin=ffmpeg,
        output_paths=outputs,
        video_width=32,
        video_height=48,
        output_fps=2,
    )
    release = commands[1]
    graph = _filter_graph(release)

    assert "concat=n=10:v=1:a=0" in graph
    assert "tpad" not in graph
    assert "atempo" not in graph
    assert all(f"[{index}:a]" not in graph for index in range(10))
    subprocess.run(release, check=True, timeout=180)

    payload = json.loads(
        subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
                str(outputs[1]),
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            timeout=30,
        ).stdout
    )
    assert float(payload["format"]["duration"]) == pytest.approx(
        54.0,
        abs=0.10,
    )
    assert {stream["codec_type"] for stream in payload["streams"]} == {
        "video",
        "audio",
    }
    video = next(
        stream
        for stream in payload["streams"]
        if stream["codec_type"] == "video"
    )
    assert (video["width"], video["height"]) == (32, 48)
