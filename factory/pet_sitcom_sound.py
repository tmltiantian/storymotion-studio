from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .pet_sitcom import (
    FINAL_DURATION_SECONDS,
    PetSitcomError,
    PetSitcomPlan,
    _validate_plan_contract,
)


SOUND_DESIGN_SCHEMA = "motion-comic-factory.pet-sitcom-sound-design.v2"
MUSIC_APPROVAL_SCHEMA = (
    "motion-comic-factory.pet-sitcom-music-approval.v1"
)
SOUND_ALGORITHM_VERSION = "pet-sitcom-three-act-v2"
SAMPLE_RATE = 48_000
CHANNELS = 2
MINIMUM_MUSIC_SAMPLE_RATE = 44_100
_DURATION_TOLERANCE_SECONDS = 0.002
_LOUDNESS_MEASUREMENT_TOLERANCE_DB = 2.0
_FOLEY_MEASUREMENT_TOLERANCE_DB = 3.0


class PetSoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class FoleyEvent:
    name: str
    start: float
    duration: float
    target_lufs: float


FOLEY_EVENTS = (
    FoleyEvent("bag_rustle", 5.25, 0.30, -30.0),
    FoleyEvent("tail_floor_rustle", 29.70, 0.45, -32.0),
    FoleyEvent("light_paw_steps", 30.30, 1.20, -34.0),
    FoleyEvent("mirror_slide", 44.80, 1.60, -29.0),
)
MUSIC_CUES = (
    {
        "name": "light_interrogation",
        "start": 0.0,
        "end": 26.5,
        "target_lufs": -31.0,
        "treatment": "high_shelf_minus_2db",
    },
    {
        "name": "surveillance_investigation",
        "start": 26.5,
        "end": 37.4,
        "target_lufs": -34.0,
        "treatment": "lowpass_4500hz_minus_3db",
    },
    {
        "name": "comic_reveal",
        "start": 37.4,
        "end": 54.0,
        "target_lufs": -30.0,
        "treatment": "open_timbre",
    },
)
DIALOGUE_FADES = (
    {"start": 48.8, "end": 49.9, "reduction_db": 8.0},
    {"start": 50.0, "end": 53.6, "reduction_db": 8.0},
)
ENDING_BUTTON = {
    "start": 53.6,
    "end": 54.0,
    "target_rms_dbfs": -30.0,
    "tolerance_db": 1.5,
    "stem": "ending_button",
}
ROOM_TONE = {
    "start": 0.0,
    "end": 54.0,
    "target_lufs": -42.0,
    "stem": "room_tone",
}
_FOLEY_BRIDGE_ENDS = {
    "tail_floor_rustle": 32.9,
    "light_paw_steps": 32.9,
}
_STEM_NAMES = (
    "music",
    "room_tone",
    *(event.name for event in FOLEY_EVENTS),
    "ending_button",
)
_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "source_path",
        "source_sha256",
        "reviewed",
        "approved",
        "not_harsh",
        "not_repetitive",
        "dialogue_compatible",
        "reviewed_at",
    }
)
_SOURCE_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "duration_seconds",
        "stream_duration_seconds",
        "sample_rate",
        "channels",
        "codec_type",
        "codec_name",
        "channel_layout",
        "looped",
    }
)
_SOURCE_BINDING_METADATA_FIELDS = (
    "duration_seconds",
    "stream_duration_seconds",
    "sample_rate",
    "channels",
    "codec_type",
    "codec_name",
    "channel_layout",
)
_MANIFEST_APPROVAL_FIELDS = frozenset({"path", "sha256", "reviewed_at"})
_STEM_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "duration_seconds",
        "stream_duration_seconds",
        "sample_rate",
        "channels",
        "codec_type",
        "codec_name",
        "channel_layout",
        "source_sha256",
        "approval_sha256",
        "config_sha256",
        "binding_sha256",
    }
)
_FOLEY_FIELDS = frozenset(
    {
        "name",
        "start",
        "duration",
        "target_lufs",
        "bridge_end",
        "stem",
    }
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "project_id",
        "plan_sha256",
        "duration_seconds",
        "sample_rate",
        "channels",
        "source",
        "approval",
        "config_sha256",
        "stems_content_root_sha256",
        "binding_sha256",
        "music_cues",
        "dialogue_fades",
        "ending_button",
        "foley",
        "room_tone",
        "stems",
    }
)
_URL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def prepare_pet_sound_design(
    plan: PetSitcomPlan,
    *,
    music_source: str | Path,
    command_runner: Callable[..., Any] = subprocess.run,
) -> Path:
    """Build and atomically publish one hash-bound three-act sound design."""
    _validate_plan(plan)
    source = _canonical_local_file(music_source, "Music source")
    source_probe = _probe_audio(
        source,
        command_runner=command_runner,
        label="Music source",
    )
    if (
        source_probe["duration_seconds"] < FINAL_DURATION_SECONDS
        or source_probe["stream_duration_seconds"] < FINAL_DURATION_SECONDS
    ):
        raise PetSoundError(
            "Approved music source container and audio stream must each be "
            "at least 54 seconds long."
        )
    if source_probe["sample_rate"] < MINIMUM_MUSIC_SAMPLE_RATE:
        raise PetSoundError(
            "Approved music source must be at least 44.1 kHz."
        )
    if (
        source_probe["channels"] != CHANNELS
        or source_probe["channel_layout"] != "stereo"
    ):
        raise PetSoundError(
            "Approved music source must declare an explicit stereo layout."
        )
    source_sha256 = _sha256(source)
    approval_path, approval, approval_sha256 = _load_approval(
        source,
        source_sha256,
    )
    config = _sound_config()
    config_sha256 = _json_hash(config)
    plan_sha256 = _json_hash(plan.to_report())
    binding_base = _binding_base(
        plan=plan,
        plan_sha256=plan_sha256,
        source=source,
        source_sha256=source_sha256,
        source_metadata=source_probe,
        approval_sha256=approval_sha256,
        config_sha256=config_sha256,
    )
    manifest_path = _manifest_path(plan)
    try:
        current = load_pet_sound_design(
            plan,
            command_runner=command_runner,
        )
    except PetSoundError:
        current = {}
    if (
        (current.get("source") or {}).get("path") == str(source)
        and (current.get("source") or {}).get("sha256") == source_sha256
        and (current.get("approval") or {}).get("sha256")
        == approval_sha256
        and current.get("config_sha256") == config_sha256
    ):
        return manifest_path

    sound_root = _sound_root(plan)
    _ensure_directory(sound_root, plan.output_dir, "Sound stem directory")
    snapshot_directory = Path(
        tempfile.mkdtemp(prefix=".source-snapshot-", dir=sound_root)
    )
    staging = Path(tempfile.mkdtemp(prefix=".render-", dir=sound_root))
    _reject_symlinks(snapshot_directory, "Music source snapshot directory")
    _reject_symlinks(staging, "Sound staging directory")
    rendered: dict[str, Path] = {}
    committed = False
    try:
        snapshot = snapshot_directory / "approved-music.wav"
        _copy_verified_snapshot(source, snapshot, source_sha256)
        snapshot_probe = _probe_audio(
            snapshot,
            command_runner=command_runner,
            label="Music source snapshot",
        )
        if not _same_audio_identity(source_probe, snapshot_probe):
            raise PetSoundError(
                "Music source changed during snapshot verification."
            )

        rendered["music"] = staging / "music.wav"
        _run(
            command_runner,
            _music_command(snapshot, rendered["music"]),
        )
        rendered["room_tone"] = staging / "room_tone.wav"
        _run(
            command_runner,
            _room_tone_command(rendered["room_tone"]),
        )
        for event in FOLEY_EVENTS:
            output = staging / f"{event.name}.wav"
            rendered[event.name] = output
            _run(
                command_runner,
                _foley_command(event, output),
            )
        rendered["ending_button"] = staging / "ending_button.wav"
        _run(
            command_runner,
            _ending_button_command(rendered["ending_button"]),
        )
        for path in rendered.values():
            _ensure_explicit_stereo_wav(path)

        expected_durations = _stem_durations()
        for name, path in rendered.items():
            _validate_stem(
                path,
                expected_durations[name],
                command_runner=command_runner,
                label=f"{name} stem",
            )
        _normalize_short_event_rms(
            rendered,
            command_runner=command_runner,
        )
        probes = {
            name: _validate_stem(
                path,
                expected_durations[name],
                command_runner=command_runner,
                label=f"{name} stem",
            )
            for name, path in rendered.items()
        }
        _validate_loudness_targets(
            rendered,
            command_runner=command_runner,
        )

        stems = {
            name: {
                "path": "",
                "sha256": _sha256(rendered[name]),
                "duration_seconds": expected_durations[name],
                "stream_duration_seconds": probes[name][
                    "stream_duration_seconds"
                ],
                "sample_rate": probes[name]["sample_rate"],
                "channels": probes[name]["channels"],
                "codec_type": probes[name]["codec_type"],
                "codec_name": probes[name]["codec_name"],
                "channel_layout": probes[name]["channel_layout"],
                "source_sha256": source_sha256,
                "approval_sha256": approval_sha256,
                "config_sha256": config_sha256,
                "binding_sha256": "",
            }
            for name in _STEM_NAMES
        }
        stems_content_root_sha256 = _stems_content_root(stems)
        version = _version_dir(plan, stems_content_root_sha256)
        for name, record in stems.items():
            record["path"] = str(
                _stem_path(plan, stems_content_root_sha256, name)
            )
        binding_sha256 = _binding_sha256(
            binding_base,
            stems_content_root_sha256,
        )
        for record in stems.values():
            record["binding_sha256"] = binding_sha256
        manifest = {
            "schema_version": SOUND_DESIGN_SCHEMA,
            "project_id": plan.project_id,
            "plan_sha256": plan_sha256,
            "duration_seconds": FINAL_DURATION_SECONDS,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "source": {
                "path": str(source),
                "sha256": source_sha256,
                "duration_seconds": source_probe["duration_seconds"],
                "stream_duration_seconds": source_probe[
                    "stream_duration_seconds"
                ],
                "sample_rate": source_probe["sample_rate"],
                "channels": source_probe["channels"],
                "codec_type": source_probe["codec_type"],
                "codec_name": source_probe["codec_name"],
                "channel_layout": source_probe["channel_layout"],
                "looped": False,
            },
            "approval": {
                "path": str(approval_path),
                "sha256": approval_sha256,
                "reviewed_at": approval["reviewed_at"],
            },
            "config_sha256": config_sha256,
            "stems_content_root_sha256": stems_content_root_sha256,
            "binding_sha256": binding_sha256,
            "music_cues": config["music_cues"],
            "dialogue_fades": config["dialogue_fades"],
            "ending_button": config["ending_button"],
            "foley": config["foley"],
            "room_tone": config["room_tone"],
            "stems": stems,
        }
        _publish_sound_version_and_manifest(
            staging=staging,
            version=version,
            manifest_path=manifest_path,
            manifest=manifest,
            root=plan.output_dir,
        )
        committed = True
    finally:
        for temporary_directory in (staging, snapshot_directory):
            try:
                if temporary_directory.exists():
                    shutil.rmtree(
                        temporary_directory,
                        ignore_errors=True,
                    )
            except BaseException:
                if not committed:
                    raise
    return manifest_path


def load_pet_sound_design(
    plan: PetSitcomPlan,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Load a current sound manifest and revalidate every bound local artifact."""
    _validate_plan(plan)
    manifest_path = _manifest_path(plan)
    document = _read_exact_json(
        manifest_path,
        _TOP_LEVEL_FIELDS,
        "Sound manifest",
    )
    config = _sound_config()
    config_sha256 = _json_hash(config)
    plan_sha256 = _json_hash(plan.to_report())
    if (
        document.get("schema_version") != SOUND_DESIGN_SCHEMA
        or document.get("project_id") != plan.project_id
        or document.get("plan_sha256") != plan_sha256
        or document.get("duration_seconds") != FINAL_DURATION_SECONDS
        or document.get("sample_rate") != SAMPLE_RATE
        or document.get("channels") != CHANNELS
        or document.get("config_sha256") != config_sha256
        or document.get("music_cues") != config["music_cues"]
        or document.get("dialogue_fades") != config["dialogue_fades"]
        or document.get("ending_button") != config["ending_button"]
        or document.get("foley") != config["foley"]
        or document.get("room_tone") != config["room_tone"]
    ):
        raise PetSoundError(
            "Sound manifest is stale or does not match the current configuration."
        )

    source_record = document.get("source")
    if not isinstance(source_record, Mapping):
        raise PetSoundError("Sound manifest source must be an object.")
    _require_exact_fields(source_record, _SOURCE_FIELDS, "Sound source")
    if source_record.get("looped") is not False:
        raise PetSoundError("Sound manifest may not loop the music source.")
    source = _canonical_local_file(
        str(source_record.get("path") or ""),
        "Music source",
    )
    source_sha256 = _sha256(source)
    if source_record.get("sha256") != source_sha256:
        raise PetSoundError("Music source hash is stale.")
    source_probe = _probe_audio(
        source,
        command_runner=command_runner,
        label="Music source",
    )
    if (
        source_probe["duration_seconds"] < FINAL_DURATION_SECONDS
        or source_probe["stream_duration_seconds"] < FINAL_DURATION_SECONDS
        or source_probe["sample_rate"] < MINIMUM_MUSIC_SAMPLE_RATE
        or source_probe["channels"] != CHANNELS
        or source_probe["channel_layout"] != "stereo"
        or any(
            source_record.get(field) != source_probe[field]
            for field in _SOURCE_BINDING_METADATA_FIELDS
        )
    ):
        raise PetSoundError("Music source media metadata is stale or invalid.")

    approval_record = document.get("approval")
    if not isinstance(approval_record, Mapping):
        raise PetSoundError("Sound manifest approval must be an object.")
    _require_exact_fields(
        approval_record,
        _MANIFEST_APPROVAL_FIELDS,
        "Sound approval",
    )
    approval_path, approval, approval_sha256 = _load_approval(
        source,
        source_sha256,
    )
    if (
        approval_record.get("path") != str(approval_path)
        or approval_record.get("sha256") != approval_sha256
        or approval_record.get("reviewed_at") != approval["reviewed_at"]
    ):
        raise PetSoundError("Sound approval hash is stale.")

    binding_base = _binding_base(
        plan=plan,
        plan_sha256=plan_sha256,
        source=source,
        source_sha256=source_sha256,
        source_metadata=source_probe,
        approval_sha256=approval_sha256,
        config_sha256=config_sha256,
    )

    stems = document.get("stems")
    if not isinstance(stems, Mapping) or set(stems) != set(_STEM_NAMES):
        raise PetSoundError(
            "Sound manifest stems must contain exactly the approved stems."
        )
    declared_content_root = document.get("stems_content_root_sha256")
    if not isinstance(declared_content_root, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        declared_content_root,
    ):
        raise PetSoundError("Sound stem content root is invalid.")
    version = _version_dir(plan, declared_content_root)
    _within(version, plan.output_dir, "Sound version directory")
    _reject_symlinks(version, "Sound version directory")
    expected_durations = _stem_durations()
    actual_stems: dict[str, dict[str, Any]] = {}
    for name in _STEM_NAMES:
        record = stems[name]
        if not isinstance(record, Mapping):
            raise PetSoundError(f"Sound stem {name} must be an object.")
        _require_exact_fields(record, _STEM_FIELDS, f"Sound stem {name}")
        path = _canonical_project_file(
            str(record.get("path") or ""),
            plan.output_dir,
            f"Sound stem {name}",
        )
        expected_path = _stem_path(plan, declared_content_root, name)
        if path != expected_path:
            raise PetSoundError(
                f"Sound stem {name} must use the fixed canonical path "
                "for its content root."
            )
        actual_sha256 = _sha256(path)
        if record.get("sha256") != actual_sha256:
            raise PetSoundError(f"Sound stem {name} hash is stale.")
        probe = _validate_stem(
            path,
            expected_durations[name],
            command_runner=command_runner,
            label=f"{name} stem",
        )
        if (
            record.get("source_sha256") != source_sha256
            or record.get("approval_sha256") != approval_sha256
            or record.get("config_sha256") != config_sha256
            or record.get("duration_seconds") != expected_durations[name]
            or any(
                record.get(field) != probe[field]
                for field in (
                    "stream_duration_seconds",
                    "sample_rate",
                    "channels",
                    "codec_type",
                    "codec_name",
                    "channel_layout",
                )
            )
        ):
            raise PetSoundError(
                f"Sound stem {name} binding metadata is stale."
            )
        actual_stems[name] = {
            **dict(record),
            "path": str(path),
            "sha256": actual_sha256,
        }
    content_root = _stems_content_root(actual_stems)
    if declared_content_root != content_root:
        raise PetSoundError("Sound stem content root is stale.")
    binding_sha256 = _binding_sha256(binding_base, content_root)
    if document.get("binding_sha256") != binding_sha256:
        raise PetSoundError("Sound manifest binding hash is stale.")
    if any(
        actual_stems[name].get("binding_sha256") != binding_sha256
        for name in _STEM_NAMES
    ):
        raise PetSoundError("Sound stem binding hash is stale.")
    _validate_loudness_targets(
        {name: Path(actual_stems[name]["path"]) for name in _STEM_NAMES},
        command_runner=command_runner,
    )
    return dict(document)


def music_approval_path(music_source: str | Path) -> Path:
    source = Path(music_source).expanduser()
    return Path(f"{source}.approval.json")


def _sound_config() -> dict[str, Any]:
    return {
        "algorithm_version": SOUND_ALGORITHM_VERSION,
        "duration_seconds": FINAL_DURATION_SECONDS,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "music_cues": [dict(cue) for cue in MUSIC_CUES],
        "dialogue_fades": [dict(item) for item in DIALOGUE_FADES],
        "ending_button": dict(ENDING_BUTTON),
        "foley": [
            {
                **asdict(event),
                "bridge_end": _FOLEY_BRIDGE_ENDS.get(event.name),
                "stem": event.name,
            }
            for event in FOLEY_EVENTS
        ],
        "room_tone": dict(ROOM_TONE),
    }


def _stem_durations() -> dict[str, float]:
    durations = {
        "music": FINAL_DURATION_SECONDS,
        "room_tone": FINAL_DURATION_SECONDS,
        "ending_button": round(
            ENDING_BUTTON["end"] - ENDING_BUTTON["start"],
            6,
        ),
    }
    for event in FOLEY_EVENTS:
        bridge_end = _FOLEY_BRIDGE_ENDS.get(event.name)
        durations[event.name] = (
            round(bridge_end - event.start, 6)
            if bridge_end is not None
            else event.duration
        )
    return durations


def _binding_base(
    *,
    plan: PetSitcomPlan,
    plan_sha256: str,
    source: Path,
    source_sha256: str,
    source_metadata: Mapping[str, Any],
    approval_sha256: str,
    config_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": SOUND_DESIGN_SCHEMA,
        "project_id": plan.project_id,
        "plan_sha256": plan_sha256,
        "source_path": str(source),
        "source_sha256": source_sha256,
        "source_metadata": {
            field: source_metadata[field]
            for field in _SOURCE_BINDING_METADATA_FIELDS
        },
        "approval_sha256": approval_sha256,
        "config_sha256": config_sha256,
    }


def _binding_sha256(
    base: Mapping[str, Any],
    stems_content_root_sha256: str,
) -> str:
    return _json_hash(
        {
            **dict(base),
            "stems_content_root_sha256": stems_content_root_sha256,
        }
    )


def _stems_content_root(stems: Mapping[str, Mapping[str, Any]]) -> str:
    if set(stems) != set(_STEM_NAMES):
        raise PetSoundError(
            "Sound content root requires exactly the fixed stem set."
        )
    entries = []
    for name in sorted(_STEM_NAMES):
        record = stems[name]
        entries.append(
            {
                "name": name,
                "filename": f"{name}.wav",
                "sha256": record.get("sha256"),
                "format": {
                    "codec_type": record.get("codec_type"),
                    "codec_name": record.get("codec_name"),
                    "duration_seconds": record.get("duration_seconds"),
                    "stream_duration_seconds": record.get(
                        "stream_duration_seconds"
                    ),
                    "sample_rate": record.get("sample_rate"),
                    "channels": record.get("channels"),
                    "channel_layout": record.get("channel_layout"),
                },
                "config_sha256": record.get("config_sha256"),
            }
        )
    return _json_hash({"stems": entries})


def _music_command(source: Path, output: Path) -> list[str]:
    gain = 10 ** (-8.0 / 20.0)
    slope = (1.0 - gain) / 0.1
    expression = (
        "if(between(t,48.8,48.9),"
        f"1-(t-48.8)*{slope:.6f},"
        "if(between(t,48.9,49.8),"
        f"{gain:.6f},"
        "if(between(t,49.8,49.9),"
        f"{gain:.6f}+(t-49.8)*{slope:.6f},"
        "if(between(t,50.0,50.1),"
        f"1-(t-50.0)*{slope:.6f},"
        "if(between(t,50.1,53.5),"
        f"{gain:.6f},"
        "if(between(t,53.5,53.6),"
        f"{gain:.6f}+(t-53.5)*{slope:.6f},1))))))"
    )
    filters = (
        "[0:a:0]atrim=start=0:end=26.5,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo,"
        "highshelf=f=6500:g=-2,loudnorm=I=-31:TP=-6:LRA=7[c1];"
        "[0:a:0]atrim=start=26.5:end=37.4,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo,"
        "lowpass=f=4500,volume=-3dB,"
        "loudnorm=I=-34:TP=-7:LRA=7[c2];"
        "[0:a:0]atrim=start=37.4:end=54,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo,"
        "loudnorm=I=-30:TP=-6:LRA=7[c3];"
        "[c1][c2][c3]concat=n=3:v=0:a=1[acts];"
        f"[acts]volume='{expression}':eval=frame,"
        "atrim=duration=54,asetpts=PTS-STARTPTS,"
        "aformat=channel_layouts=stereo[out]"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        filters,
        "-map",
        "[out]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-t",
        _number(FINAL_DURATION_SECONDS),
        str(output),
    ]


def _ending_button_command(output: Path) -> list[str]:
    duration = _stem_durations()["ending_button"]
    source = (
        "aevalsrc="
        "0.044668359*sin(2*PI*523.25*t)|"
        "0.044668359*sin(2*PI*523.25*t):"
        f"s=48000:d={_number(duration)}"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        source,
        "-af",
        (
            "afade=t=out:st=0.34:d=0.06,"
            "aformat=channel_layouts=stereo"
        ),
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-t",
        _number(duration),
        str(output),
    ]


def _room_tone_command(output: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        (
            "anoisesrc=color=pink:sample_rate=48000:"
            "duration=54:seed=20260727"
        ),
        "-af",
        (
            "highpass=f=80,lowpass=f=8000,"
            "loudnorm=I=-42:TP=-9:LRA=3,"
            "aformat=channel_layouts=stereo"
        ),
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-t",
        _number(FINAL_DURATION_SECONDS),
        str(output),
    ]


def _foley_command(event: FoleyEvent, output: Path) -> list[str]:
    render_duration = _stem_durations()[event.name]
    if event.name == "mirror_slide":
        source = (
            "sine=frequency=190:sample_rate=48000:"
            f"duration={_number(render_duration)}"
        )
        treatment = "highpass=f=120,lowpass=f=1800"
    else:
        source = (
            "anoisesrc=color=pink:sample_rate=48000:"
            f"duration={_number(render_duration)}:"
            f"seed={20260727 + FOLEY_EVENTS.index(event)}"
        )
        treatment = {
            "bag_rustle": "highpass=f=240,lowpass=f=6500",
            "tail_floor_rustle": "highpass=f=180,lowpass=f=3200",
            "light_paw_steps": (
                "highpass=f=120,lowpass=f=2400,tremolo=f=3:d=0.72"
            ),
        }[event.name]
    core_end = min(event.duration, render_duration)
    fade_out = min(0.18, max(0.03, core_end / 3.0))
    filters = (
        f"{treatment},"
        f"afade=t=in:st=0:d={_number(min(0.03, core_end / 4.0))},"
        f"afade=t=out:st={_number(max(0.0, render_duration - fade_out))}:"
        f"d={_number(fade_out)},"
        f"loudnorm=I={_number(event.target_lufs)}:TP=-8:LRA=5,"
        "aformat=channel_layouts=stereo"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        source,
        "-af",
        filters,
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        str(CHANNELS),
        "-t",
        _number(render_duration),
        str(output),
    ]


def _normalize_short_event_rms(
    rendered: Mapping[str, Path],
    *,
    command_runner: Callable[..., Any],
) -> None:
    targets = {
        **{event.name: event.target_lufs for event in FOLEY_EVENTS},
        "ending_button": float(ENDING_BUTTON["target_rms_dbfs"]),
    }
    durations = _stem_durations()
    for name, target in targets.items():
        path = rendered[name]
        measured = _measure_rms_dbfs(
            path,
            command_runner=command_runner,
            label=f"{name} pre-normalization",
        )
        delta = target - measured
        if abs(delta) <= 0.05:
            continue
        temporary = path.with_name(f".{path.stem}.rms-normalized.wav")
        try:
            _run(
                command_runner,
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-af",
                    (
                        f"volume={delta:.6f}dB,"
                        "aformat=channel_layouts=stereo"
                    ),
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-ac",
                    str(CHANNELS),
                    "-t",
                    _number(durations[name]),
                    str(temporary),
                ],
            )
            _ensure_explicit_stereo_wav(temporary)
            _validate_stem(
                temporary,
                durations[name],
                command_runner=command_runner,
                label=f"{name} normalized stem",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_loudness_targets(
    rendered: Mapping[str, Path],
    *,
    command_runner: Callable[..., Any],
) -> None:
    music = rendered["music"]
    for cue in MUSIC_CUES:
        measured = _measure_integrated_lufs(
            music,
            start=float(cue["start"]),
            end=float(cue["end"]),
            command_runner=command_runner,
            label=f"Music cue {cue['name']}",
        )
        _require_target_level(
            measured,
            float(cue["target_lufs"]),
            _LOUDNESS_MEASUREMENT_TOLERANCE_DB,
            f"Music cue {cue['name']} integrated loudness",
        )
    room_measured = _measure_integrated_lufs(
        rendered["room_tone"],
        start=0.0,
        end=FINAL_DURATION_SECONDS,
        command_runner=command_runner,
        label="Room tone",
    )
    _require_target_level(
        room_measured,
        float(ROOM_TONE["target_lufs"]),
        _LOUDNESS_MEASUREMENT_TOLERANCE_DB,
        "Room tone integrated loudness",
    )
    for event in FOLEY_EVENTS:
        measured = _measure_rms_dbfs(
            rendered[event.name],
            command_runner=command_runner,
            label=f"Foley {event.name}",
        )
        _require_target_level(
            measured,
            event.target_lufs,
            _FOLEY_MEASUREMENT_TOLERANCE_DB,
            f"Foley {event.name} RMS",
        )
    button_measured = _measure_rms_dbfs(
        rendered["ending_button"],
        command_runner=command_runner,
        label="Ending button",
    )
    _require_target_level(
        button_measured,
        float(ENDING_BUTTON["target_rms_dbfs"]),
        float(ENDING_BUTTON["tolerance_db"]),
        "Ending button RMS",
    )


def _measure_integrated_lufs(
    path: Path,
    *,
    start: float,
    end: float,
    command_runner: Callable[..., Any],
    label: str,
) -> float:
    result = _run(
        command_runner,
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            _number(start),
            "-t",
            _number(end - start),
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
    )
    report = (
        f"{getattr(result, 'stdout', '')}\n"
        f"{getattr(result, 'stderr', '')}"
    )
    matches = re.findall(
        r"\bI:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS",
        report,
    )
    if not matches:
        raise PetSoundError(
            f"{label} integrated loudness measurement is unavailable."
        )
    measured = float(matches[-1])
    if not math.isfinite(measured):
        raise PetSoundError(
            f"{label} integrated loudness measurement is invalid."
        )
    return measured


def _measure_rms_dbfs(
    path: Path,
    *,
    command_runner: Callable[..., Any],
    label: str,
) -> float:
    result = _run(
        command_runner,
        [
            "ffmpeg",
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
    )
    report = (
        f"{getattr(result, 'stdout', '')}\n"
        f"{getattr(result, 'stderr', '')}"
    )
    matches = re.findall(
        r"RMS level dB:\s*(-?[0-9]+(?:\.[0-9]+)?)",
        report,
    )
    if not matches:
        raise PetSoundError(f"{label} RMS measurement is unavailable.")
    measured = float(matches[-1])
    if not math.isfinite(measured):
        raise PetSoundError(f"{label} RMS measurement is invalid.")
    return measured


def _require_target_level(
    measured: float,
    target: float,
    tolerance: float,
    label: str,
) -> None:
    if abs(measured - target) > tolerance + 1e-9:
        raise PetSoundError(
            f"{label} must be {target:.1f} +/-{tolerance:.1f} dB; "
            f"measured {measured:.1f}."
        )


def _load_approval(
    source: Path,
    source_sha256: str,
) -> tuple[Path, dict[str, Any], str]:
    path = _canonical_local_file(
        music_approval_path(source),
        "Music approval",
    )
    approval = _read_exact_json(path, _APPROVAL_FIELDS, "Music approval")
    if approval.get("schema_version") != MUSIC_APPROVAL_SCHEMA:
        raise PetSoundError("Music approval schema is invalid.")
    if approval.get("source_path") != str(source):
        raise PetSoundError("Music approval source path is stale.")
    if approval.get("source_sha256") != source_sha256:
        raise PetSoundError("Music approval source hash is stale.")
    if approval.get("reviewed") is not True:
        raise PetSoundError("Music approval requires reviewed=true.")
    if approval.get("approved") is not True:
        raise PetSoundError("Music approval requires approved=true.")
    if approval.get("not_harsh") is not True:
        raise PetSoundError("Music approval requires not_harsh=true.")
    if approval.get("not_repetitive") is not True:
        raise PetSoundError("Music approval requires not_repetitive=true.")
    if approval.get("dialogue_compatible") is not True:
        raise PetSoundError(
            "Music approval requires dialogue_compatible=true."
        )
    if not _valid_iso_timestamp(approval.get("reviewed_at")):
        raise PetSoundError(
            "Music approval requires a timezone-aware reviewed_at."
        )
    return path, approval, _sha256(path)


def _probe_audio(
    path: Path,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    label: str,
) -> dict[str, Any]:
    result = _run(
        command_runner,
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,codec_name,sample_rate,channels,"
                "channel_layout,duration"
            ),
            "-of",
            "json",
            str(path),
        ],
    )
    try:
        payload = json.loads(str(getattr(result, "stdout", "") or "{}"))
    except json.JSONDecodeError as exc:
        raise PetSoundError(f"{label} ffprobe report is invalid.") from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list):
        raise PetSoundError(f"{label} ffprobe report is invalid.")
    audio = [
        stream
        for stream in streams
        if isinstance(stream, Mapping)
        and stream.get("codec_type") == "audio"
    ]
    video = [
        stream
        for stream in streams
        if isinstance(stream, Mapping)
        and stream.get("codec_type") == "video"
    ]
    if len(streams) != 1 or len(audio) != 1 or video:
        raise PetSoundError(
            f"{label} must contain exactly one audio stream and no video."
        )
    try:
        duration = float((payload.get("format") or {}).get("duration"))
        stream_duration = float(audio[0].get("duration"))
        sample_rate = int(str(audio[0].get("sample_rate")))
        channels = int(audio[0].get("channels"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PetSoundError(f"{label} media metadata is invalid.") from exc
    if (
        not math.isfinite(duration)
        or not math.isfinite(stream_duration)
        or duration <= 0
        or stream_duration <= 0
        or sample_rate <= 0
        or channels <= 0
    ):
        raise PetSoundError(f"{label} media metadata is invalid.")
    codec_name = audio[0].get("codec_name")
    if not isinstance(codec_name, str) or not codec_name:
        raise PetSoundError(f"{label} media metadata is invalid.")
    channel_layout = _normalize_channel_layout(
        audio[0].get("channel_layout"),
        channels,
    )
    return {
        "duration_seconds": duration,
        "stream_duration_seconds": stream_duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "codec_type": "audio",
        "codec_name": codec_name,
        "channel_layout": channel_layout,
    }


def _normalize_channel_layout(value: Any, channels: int) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip().lower())
    stereo_aliases = {
        "stereo",
        "2 channels",
        "2.0",
        "fl+fr",
    }
    mono_aliases = {"mono", "1 channel", "1 channels", "fc"}
    if channels == 2 and raw in stereo_aliases:
        return "stereo"
    if channels == 1 and raw in mono_aliases:
        return "mono"
    return raw


def _ensure_explicit_stereo_wav(path: Path) -> None:
    """Write the stereo channel mask that two-channel RIFF omits by default."""
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PetSoundError(
            "Rendered sound stem is missing or unreadable."
        ) from exc
    if payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        raise PetSoundError("Rendered sound stem must be a PCM WAV file.")
    offset = 12
    chunks: list[tuple[bytes, bytes]] = []
    found_format = False
    while offset + 8 <= len(payload):
        chunk_id = payload[offset : offset + 4]
        size = struct.unpack_from("<I", payload, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(payload):
            raise PetSoundError("Rendered sound stem WAV is truncated.")
        body = payload[start:end]
        if chunk_id == b"fmt ":
            if found_format or len(body) < 16:
                raise PetSoundError(
                    "Rendered sound stem WAV format is invalid."
                )
            found_format = True
            (
                format_tag,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
            ) = struct.unpack_from("<HHIIHH", body)
            if (
                channels != CHANNELS
                or bits_per_sample != 16
                or block_align != 4
                or byte_rate != sample_rate * block_align
            ):
                raise PetSoundError(
                    "Rendered sound stem must be 16-bit stereo PCM."
                )
            if format_tag == 0xFFFE:
                if (
                    len(body) < 40
                    or struct.unpack_from("<H", body, 16)[0] < 22
                    or struct.unpack_from("<H", body, 18)[0] != 16
                    or struct.unpack_from("<I", body, 20)[0] != 0x3
                    or body[24:40]
                    != bytes.fromhex(
                        "0100000000001000800000aa00389b71"
                    )
                ):
                    raise PetSoundError(
                        "Rendered sound stem stereo layout is invalid."
                    )
            elif format_tag == 1:
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
                ) + bytes.fromhex(
                    "0100000000001000800000aa00389b71"
                )
            else:
                raise PetSoundError(
                    "Rendered sound stem must use pcm_s16le."
                )
        chunks.append((chunk_id, body))
        offset = end + (size & 1)
    if not found_format:
        raise PetSoundError("Rendered sound stem WAV format is missing.")
    rewritten = bytearray(b"RIFF\x00\x00\x00\x00WAVE")
    for chunk_id, body in chunks:
        rewritten.extend(chunk_id)
        rewritten.extend(struct.pack("<I", len(body)))
        rewritten.extend(body)
        if len(body) & 1:
            rewritten.append(0)
    struct.pack_into("<I", rewritten, 4, len(rewritten) - 8)
    if bytes(rewritten) == payload:
        return
    descriptor, name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".layout",
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(rewritten)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise PetSoundError(
            "Unable to persist explicit stereo stem layout."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _validate_stem(
    path: Path,
    expected_duration: float,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    label: str,
) -> dict[str, Any]:
    probe = _probe_audio(
        path,
        command_runner=command_runner,
        label=label,
    )
    if (
        abs(probe["duration_seconds"] - expected_duration)
        > _DURATION_TOLERANCE_SECONDS
        or abs(probe["stream_duration_seconds"] - expected_duration)
        > _DURATION_TOLERANCE_SECONDS
        or probe["sample_rate"] != SAMPLE_RATE
        or probe["channels"] != CHANNELS
        or probe["codec_type"] != "audio"
        or probe["codec_name"] != "pcm_s16le"
        or probe["channel_layout"] != "stereo"
    ):
        raise PetSoundError(
            f"{label.capitalize()} must be exact-duration pcm_s16le "
            "audio-only 48 kHz stereo with a stereo channel layout."
        )
    return probe


def _same_audio_identity(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> bool:
    fields = (
        "duration_seconds",
        "stream_duration_seconds",
        "sample_rate",
        "channels",
        "codec_type",
        "codec_name",
        "channel_layout",
    )
    return all(first.get(field) == second.get(field) for field in fields)


def _copy_verified_snapshot(
    source: Path,
    destination: Path,
    expected_sha256: str,
) -> Path:
    _reject_symlinks(source, "Music source")
    _reject_symlinks(destination, "Music source snapshot")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = hashlib.sha256()
    try:
        source_descriptor = os.open(source, flags)
        try:
            if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
                raise PetSoundError(
                    "Music source changed during snapshot verification."
                )
            destination_descriptor = os.open(
                destination,
                destination_flags,
                0o600,
            )
            try:
                while True:
                    block = os.read(source_descriptor, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    view = memoryview(block)
                    while view:
                        written = os.write(destination_descriptor, view)
                        view = view[written:]
                os.fsync(destination_descriptor)
            finally:
                os.close(destination_descriptor)
        finally:
            os.close(source_descriptor)
    except PetSoundError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise PetSoundError(
            "Music source changed during snapshot copy."
        ) from exc
    _reject_symlinks(source, "Music source")
    _reject_symlinks(destination, "Music source snapshot")
    if (
        digest.hexdigest() != expected_sha256
        or _sha256(destination) != expected_sha256
        or _sha256(source) != expected_sha256
    ):
        destination.unlink(missing_ok=True)
        raise PetSoundError(
            "Music source changed during snapshot copy."
        )
    _fsync_dir(destination.parent)
    return destination


def _validate_plan(plan: PetSitcomPlan) -> None:
    try:
        _validate_plan_contract(plan)
    except PetSitcomError as exc:
        raise PetSoundError(
            f"Plan must match the approved pet sitcom contract: {exc}"
        ) from exc
    _reject_symlinks(plan.output_dir, "Plan output directory")
    for path, label in (
        (_manifest_path(plan), "Sound manifest"),
        (_sound_root(plan), "Sound stem directory"),
        (_versions_root(plan), "Sound versions directory"),
    ):
        _within(path, plan.output_dir, label)
        _reject_symlinks(path, label)


def _manifest_path(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "sound_design.json"


def _sound_root(plan: PetSitcomPlan) -> Path:
    return plan.output_dir / "audio" / "sound_design"


def _versions_root(plan: PetSitcomPlan) -> Path:
    return _sound_root(plan) / "versions"


def _version_dir(plan: PetSitcomPlan, content_root: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", content_root):
        raise PetSoundError("Sound content root must be a SHA-256 digest.")
    return _versions_root(plan) / content_root


def _stem_path(plan: PetSitcomPlan, content_root: str, name: str) -> Path:
    if name not in _STEM_NAMES:
        raise PetSoundError(f"Unknown sound stem: {name}")
    return _version_dir(plan, content_root) / f"{name}.wav"


def _canonical_project_file(
    raw: str,
    root: Path,
    label: str,
) -> Path:
    if not raw or "\x00" in raw or _URL_PATTERN.match(raw):
        raise PetSoundError(f"{label} must use a canonical local path.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise PetSoundError(f"{label} must use an absolute canonical path.")
    _reject_symlinks(candidate, label)
    try:
        canonical = candidate.resolve(strict=True)
        mode = canonical.stat().st_mode
    except OSError as exc:
        raise PetSoundError(f"{label} is missing or unreadable.") from exc
    if candidate != canonical:
        raise PetSoundError(f"{label} must use a canonical path.")
    if not stat.S_ISREG(mode):
        raise PetSoundError(f"{label} must be a regular local file.")
    _within(canonical, root, label)
    return canonical


def _canonical_local_file(path: str | Path, label: str) -> Path:
    raw = str(path)
    if not raw or "\x00" in raw or _URL_PATTERN.match(raw):
        raise PetSoundError(f"{label} must be a local file path.")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise PetSoundError(f"{label} must use an absolute canonical path.")
    _reject_symlinks(candidate, label)
    try:
        canonical = candidate.resolve(strict=True)
        mode = canonical.stat().st_mode
    except OSError as exc:
        raise PetSoundError(f"{label} is missing or unreadable.") from exc
    if not stat.S_ISREG(mode):
        raise PetSoundError(f"{label} must be a regular local file.")
    if candidate != canonical:
        raise PetSoundError(f"{label} must use an absolute canonical path.")
    return canonical


def _read_exact_json(
    path: Path,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    _reject_symlinks(path, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetSoundError(f"{label} is missing or invalid.") from exc
    if not isinstance(payload, dict):
        raise PetSoundError(f"{label} must be a JSON object.")
    _require_exact_fields(payload, fields, label)
    return payload


def _require_exact_fields(
    value: Mapping[str, Any],
    fields: frozenset[str],
    label: str,
) -> None:
    unknown = set(value) - fields
    if unknown:
        raise PetSoundError(
            f"{label} contains an unknown field: {sorted(unknown)[0]}"
        )
    missing = fields - set(value)
    if missing:
        raise PetSoundError(
            f"{label} is missing required field: {sorted(missing)[0]}"
        )


def _publish_sound_version_and_manifest(
    *,
    staging: Path,
    version: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    root: Path,
) -> None:
    _within(staging, root, "Sound staging directory")
    _within(version, root, "Sound version directory")
    if (
        version.parent
        != root / "audio" / "sound_design" / "versions"
        or not re.fullmatch(r"[0-9a-f]{64}", version.name)
        or manifest_path != root / "sound_design.json"
    ):
        raise PetSoundError(
            "Sound version and manifest must use their fixed canonical paths."
        )
    _ensure_directory(
        version.parent,
        root,
        "Sound versions directory",
    )
    _reject_symlinks(staging, "Sound staging directory")
    _reject_symlinks(version, "Sound version directory")
    expected_names = {f"{name}.wav" for name in _STEM_NAMES}
    if (
        not staging.is_dir()
        or {path.name for path in staging.iterdir()} != expected_names
        or any(not path.is_file() or path.is_symlink() for path in staging.iterdir())
    ):
        raise PetSoundError(
            "Sound staging directory must contain exactly the fixed stems."
        )
    for path in staging.iterdir():
        _fsync_file(path)
    _fsync_dir(staging)
    if version.exists():
        _validate_immutable_version(staging, version, expected_names)
        shutil.rmtree(staging)
        _fsync_dir(staging.parent)
    else:
        try:
            os.rename(staging, version)
        except OSError as exc:
            if version.exists():
                _validate_immutable_version(
                    staging,
                    version,
                    expected_names,
                )
            else:
                raise PetSoundError(
                    "Unable to publish immutable sound version."
                ) from exc
        _fsync_dir(version)
        _fsync_dir(version.parent)
    _write_json_atomic(manifest_path, manifest, root)


def _validate_immutable_version(
    staging: Path,
    version: Path,
    expected_names: set[str],
) -> None:
    _reject_symlinks(version, "Sound version directory")
    if (
        not version.is_dir()
        or {path.name for path in version.iterdir()} != expected_names
        or any(
            not path.is_file() or path.is_symlink()
            for path in version.iterdir()
        )
        or any(
            _sha256(staging / name) != _sha256(version / name)
            for name in expected_names
        )
    ):
        raise PetSoundError(
            "Published sound content version is immutable and does not "
            "match the rendered stems."
        )


def _write_json_atomic(
    path: Path,
    payload: Mapping[str, Any],
    root: Path,
) -> None:
    _within(path, root, "Sound manifest")
    _reject_symlinks(path, "Sound manifest")
    _ensure_directory(path.parent, root, "Sound manifest directory")
    try:
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PetSoundError(
            "Sound manifest values must be finite and serializable."
        ) from exc
    expected_sha256 = hashlib.sha256(serialized).hexdigest()
    descriptor, name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(name)
    committed = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.replace(temporary, path)
            committed = True
        except BaseException:
            committed = (
                path.is_file()
                and not path.is_symlink()
                and _sha256(path) == expected_sha256
            )
            if not committed:
                raise
        try:
            _fsync_dir(path.parent)
        except BaseException:
            # The visible replace is the commit point. Reporting failure after
            # it would falsely imply that callers may safely retry over a
            # still-old manifest.
            pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except BaseException:
            if not committed:
                raise


def _ensure_directory(path: Path, root: Path, label: str) -> None:
    _within(path, root, label)
    _reject_symlinks(path, label)
    if not root.is_dir() or root.is_symlink():
        raise PetSoundError(
            "Project output directory must exist as a regular directory."
        )
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PetSoundError(
            f"{label} must stay inside the project output directory."
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if not current.exists():
            try:
                current.mkdir(mode=0o755)
            except FileExistsError:
                pass
        _reject_symlinks(current, label)
        if not current.is_dir():
            raise PetSoundError(f"{label} must be a directory.")
        _fsync_dir(current)
        _fsync_dir(current.parent)


def _within(path: Path, root: Path, label: str) -> None:
    try:
        path.expanduser().absolute().resolve(strict=False).relative_to(
            root.expanduser().absolute().resolve(strict=False)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetSoundError(
            f"{label} must stay inside the project output directory."
        ) from exc


def _reject_symlinks(path: Path, label: str) -> None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PetSoundError(
                f"{label} may not use a symlink: {current}"
            )


def _run(
    runner: Callable[..., Any],
    command: list[str],
) -> Any:
    try:
        return runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300.0,
        )
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise PetSoundError(f"Sound command failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
        raise PetSoundError(f"Sound command failed: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise PetSoundError(f"Unable to hash local artifact: {path}") from exc
    return digest.hexdigest()


def _json_hash(payload: Mapping[str, Any]) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PetSoundError(
            "Sound binding values must be finite and serializable."
        ) from exc
    return hashlib.sha256(serialized).hexdigest()


def _valid_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or value != value.strip() or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _number(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
