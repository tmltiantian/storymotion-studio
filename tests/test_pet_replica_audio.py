from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_audio import (
    PetReplicaAudioError,
    audio_for_shot,
    extract_replica_audio,
    validate_replica_audio_manifest,
)
from factory.pet_replica_reference import ReplicaShotAnnotation


def replica_plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    return build_pet_replica_plan(source, tmp_path / "output")


def reviewed_annotations(plan):
    return tuple(
        ReplicaShotAnnotation(
            shot_id=shot.shot_id,
            characters=("source_woman",),
            speaker="source_woman",
            scene_anchor_id="scene_sofa",
            location="living_room_sofa",
            framing="medium closeup",
            action="woman looks at both cats",
            subtitle="",
            source_audio=shot.source_audio,
            manual_review_required=False,
        )
        for shot in plan.shots
    )


def _probe_payload(
    *, path: Path, duration_s: float, codec: str, sample_rate: int, channels: int
):
    return {
        "format": {"duration": f"{duration_s:.6f}"},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": codec,
                "sample_rate": str(sample_rate),
                "channels": channels,
            }
        ],
    }


_PACKET_DURATION = 1024 / 44100


def _source_packets() -> dict:
    return {
        "packets": [
            {
                "pts_time": f"{-_PACKET_DURATION:.12f}",
                "duration_time": f"{_PACKET_DURATION:.12f}",
                "side_data_list": [
                    {
                        "side_data_type": "Skip Samples",
                        "skip_samples": 1024,
                        "discard_padding": 0,
                    }
                ],
            },
            {"pts_time": "0.000000000000", "duration_time": "77.229569000000"},
        ]
    }


def _raw_packets() -> dict:
    return {
        "packets": [
            {"pts_time": "0.000000000000", "duration_time": f"{_PACKET_DURATION:.12f}"},
            {
                "pts_time": f"{_PACKET_DURATION:.12f}",
                "duration_time": "77.345669000000",
            },
        ]
    }


def fake_audio_runner(command, **_kwargs):
    executable = Path(command[0]).name
    if executable == "ffprobe":
        target = Path(command[-1])
        if "-show_packets" in command:
            payload = _source_packets() if target.suffix == ".mp4" else _raw_packets()
        elif target.suffix == ".mp4":
            payload = _probe_payload(
                path=target,
                duration_s=77.229569,
                codec="aac",
                sample_rate=44100,
                channels=2,
            )
        elif target.name == "source_audio.aac":
            payload = _probe_payload(
                path=target,
                duration_s=77.229569,
                codec="aac",
                sample_rate=44100,
                channels=2,
            )
        else:
            shot_id = target.stem
            index = int(shot_id.removeprefix("R")) - 1
            boundaries = build_pet_replica_plan(
                target.parent / "reference.mp4", target.parent / "output"
            ).shots
            payload = _probe_payload(
                path=target,
                duration_s=boundaries[index].duration_s,
                codec="pcm_s16le",
                sample_rate=48000,
                channels=2,
            )
        return SimpleNamespace(stdout=json.dumps(payload), stderr="")

    destination = Path(command[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if "-f" in command and "data" in command:
        destination.write_bytes(b"normalized-aac-payload")
    else:
        destination.write_bytes(f"artifact:{destination.name}".encode("ascii"))
    return SimpleNamespace(stdout="", stderr="")


def test_audio_manifest_binds_full_aac_and_shot_drive_wavs(tmp_path):
    plan = replica_plan(tmp_path)
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(command)
        return fake_audio_runner(command, **kwargs)

    manifest = extract_replica_audio(plan, reviewed_annotations(plan), runner=runner)

    assert manifest.path == plan.output_root / "audio" / "audio_manifest.json"
    assert manifest.full_source.path == plan.output_root / "audio" / "source_audio.aac"
    assert manifest.full_source.codec == "aac"
    assert manifest.full_source.sample_rate == 44100
    assert manifest.full_source.channels == 2
    assert manifest.shots["R001"].codec == "pcm_s16le"
    assert manifest.shots["R001"].sample_rate == 48000
    assert manifest.shots["R001"].duration_s == pytest.approx(
        plan.shots[0].duration_s, abs=1 / plan.fps
    )
    assert manifest.shots["R037"].source_end_s == pytest.approx(77.229569)
    assert (
        audio_for_shot(manifest, "R001")
        == plan.output_root / "audio" / "drive" / "R001.wav"
    )
    assert audio_for_shot(manifest, "missing") is None
    assert manifest.usage_scope == "local_evaluation_only"
    assert manifest.public_release_ready is False
    assert manifest.public_release_blocker == "Replace or license the source audio."

    copy_command = next(
        command for command in commands if command[-1].endswith("source_audio.aac")
    )
    assert "copy" in copy_command
    assert "-af" not in copy_command
    drive_command = next(
        command for command in commands if command[-1].endswith("R001.wav")
    )
    assert "atrim=start=0.000000:end=1.733333" in " ".join(drive_command)
    assert "asetpts=PTS-STARTPTS" in " ".join(drive_command)
    assert "-ar" in drive_command and "48000" in drive_command
    assert "-ac" in drive_command and "2" in drive_command
    assert "pcm_s16le" in drive_command

    payload = json.loads(manifest.path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert str(plan.source_video) not in serialized
    assert "source-url-token" not in serialized


def test_audio_manifest_rejects_changed_source_hash(tmp_path):
    plan = replica_plan(tmp_path)
    manifest = extract_replica_audio(
        plan,
        reviewed_annotations(plan),
        runner=fake_audio_runner,
    )
    plan.source_video.write_bytes(b"changed")

    with pytest.raises(PetReplicaAudioError, match="source hash"):
        validate_replica_audio_manifest(plan, manifest.path, runner=fake_audio_runner)


def test_audio_extraction_requires_reviewed_annotations(tmp_path):
    plan = replica_plan(tmp_path)
    annotations = list(reviewed_annotations(plan))
    annotations[0] = replace(annotations[0], manual_review_required=True)

    with pytest.raises(PetReplicaAudioError, match="manual review"):
        extract_replica_audio(plan, tuple(annotations), runner=fake_audio_runner)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda: {"packets": []}, "non-empty"),
        (lambda: {"packets": [{"duration_time": "0.023220"}]}, "timestamp"),
        (lambda: {"packets": [{"pts_time": "0", "duration_time": None}]}, "duration"),
        (
            lambda: {"packets": [{"pts_time": "-0.01", "duration_time": "0.023220"}]},
            "zero",
        ),
        (
            lambda: {"packets": [{"pts_time": "0.01", "duration_time": "0.023220"}]},
            "zero",
        ),
        (
            lambda: {
                "packets": [
                    {"pts_time": "0", "duration_time": "0.023220"},
                    {"pts_time": "-0.023220", "duration_time": "0.023220"},
                ]
            },
            "monotonic",
        ),
    ),
)
def test_audio_extraction_rejects_invalid_raw_aac_packets(tmp_path, mutate, message):
    plan = replica_plan(tmp_path)

    def runner(command, **kwargs):
        if Path(command[0]).name == "ffprobe" and "-show_packets" in command:
            if Path(command[-1]).suffix == ".aac":
                return SimpleNamespace(stdout=json.dumps(mutate()), stderr="")
        return fake_audio_runner(command, **kwargs)

    with pytest.raises(PetReplicaAudioError, match=message):
        extract_replica_audio(plan, reviewed_annotations(plan), runner=runner)


def test_audio_extraction_rejects_unexplained_negative_source_pts(tmp_path):
    plan = replica_plan(tmp_path)

    def runner(command, **kwargs):
        if Path(command[0]).name == "ffprobe" and "-show_packets" in command:
            if Path(command[-1]).suffix == ".mp4":
                payload = _source_packets()
                payload["packets"][0]["side_data_list"][0]["skip_samples"] = 1
                return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return fake_audio_runner(command, **kwargs)

    with pytest.raises(PetReplicaAudioError, match="not explained"):
        extract_replica_audio(plan, reviewed_annotations(plan), runner=runner)


def test_audio_extraction_rejects_payload_hash_mismatch(tmp_path):
    plan = replica_plan(tmp_path)

    def runner(command, **kwargs):
        if Path(command[0]).name == "ffmpeg" and "-f" in command and "data" in command:
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(
                b"raw-payload" if "-bsf:a" in command else b"source-payload"
            )
            return SimpleNamespace(stdout="", stderr="")
        return fake_audio_runner(command, **kwargs)

    with pytest.raises(PetReplicaAudioError, match="normalized payload"):
        extract_replica_audio(plan, reviewed_annotations(plan), runner=runner)


def test_audio_manifest_rejects_changed_normalized_payload_evidence(tmp_path):
    plan = replica_plan(tmp_path)
    manifest = extract_replica_audio(
        plan,
        reviewed_annotations(plan),
        runner=fake_audio_runner,
    )
    payload = json.loads(manifest.path.read_text(encoding="utf-8"))
    payload["normalized_payload"]["sha256"] = "0" * 64
    manifest.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetReplicaAudioError, match="payload evidence"):
        validate_replica_audio_manifest(plan, manifest.path, runner=fake_audio_runner)


def test_audio_manifest_rejects_asset_path_escape_and_symlink(tmp_path):
    plan = replica_plan(tmp_path)
    manifest = extract_replica_audio(
        plan,
        reviewed_annotations(plan),
        runner=fake_audio_runner,
    )
    payload = json.loads(manifest.path.read_text(encoding="utf-8"))
    payload["shots"]["R001"]["path"] = "../escape.wav"
    manifest.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetReplicaAudioError, match="output root"):
        validate_replica_audio_manifest(plan, manifest.path, runner=fake_audio_runner)

    payload["shots"]["R001"]["path"] = "audio/drive/R001.wav"
    manifest.path.write_text(json.dumps(payload), encoding="utf-8")
    asset = plan.output_root / "audio" / "drive" / "R001.wav"
    backup = asset.with_suffix(".original")
    asset.rename(backup)
    os.symlink(backup, asset)
    with pytest.raises(PetReplicaAudioError, match="symlinks"):
        validate_replica_audio_manifest(plan, manifest.path, runner=fake_audio_runner)


def test_audio_extraction_accepts_sub_millisecond_container_timestamp_rounding(tmp_path):
    plan = replica_plan(tmp_path)

    def runner(command, **kwargs):
        if Path(command[0]).name == "ffprobe" and "-show_packets" in command:
            target = Path(command[-1])
            if target.suffix == ".mp4":
                payload = _source_packets()
                payload["packets"][-1]["duration_time"] = "77.229002000000"
                return SimpleNamespace(stdout=json.dumps(payload), stderr="")
        return fake_audio_runner(command, **kwargs)

    manifest = extract_replica_audio(plan, reviewed_annotations(plan), runner=runner)

    assert manifest.source_timeline.last_packet_end_s == pytest.approx(77.229002)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for replica audio integration",
)
def test_audio_extraction_keeps_audio_tail_beyond_last_video_frame(tmp_path):
    source = tmp_path / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=720x1280:r=30:d=77.166667",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=77.229569",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            source.as_posix(),
        ],
        check=True,
    )
    plan = build_pet_replica_plan(source, tmp_path / "output")
    video_probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            source.as_posix(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(video_probe.stdout.strip().splitlines()[-1]) < plan.duration_s

    manifest = extract_replica_audio(plan, reviewed_annotations(plan))
    validated = validate_replica_audio_manifest(plan, manifest.path)

    assert validated.full_source.codec == "aac"
    assert validated.full_source.duration_s == pytest.approx(
        plan.duration_s, abs=1 / plan.fps
    )
    assert validated.source_timeline.first_packet_pts_s < 0
    assert validated.source_timeline.skip_samples == 1024
    assert validated.raw_aac_timeline.first_packet_pts_s == pytest.approx(0.0)
    assert (
        validated.raw_aac_timeline.packet_count
        == validated.source_timeline.packet_count
    )
    assert (
        validated.raw_aac_timeline.packet_span_s
        > validated.source_timeline.logical_duration_s
    )
    assert validated.normalized_payload.byte_count > 0
    assert validated.shots["R037"].source_start_s == pytest.approx(75.566667)
    assert validated.shots["R037"].source_end_s == pytest.approx(77.229569)
    assert validated.shots["R037"].duration_s == pytest.approx(
        plan.shots[-1].duration_s, abs=1 / plan.fps
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == validated.source_sha256
