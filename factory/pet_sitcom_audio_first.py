from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .media_validation import probe_media
from .pet_sitcom import (
    DIALOGUE_TAIL_SECONDS,
    OWNER_AUDIO_MODEL,
    PLAN_SCHEMA_VERSION,
    PetShot,
    PetSitcomError,
    PetSitcomPlan,
    _validate_plan_contract,
)


AUDIO_FIRST_SCHEMA = "motion-comic-factory.pet-sitcom-audio-first.v1"
DRIVE_AUDIO_STATE_SCHEMA = (
    "motion-comic-factory.pet-sitcom-audio-first-drive.v1"
)
TTS_SAMPLE_RATE = 24000
OUTPUT_SAMPLE_RATE = 48000
OUTPUT_CHANNELS = 2
MINIMUM_DURATION_SECONDS = 0.20
CAT_DRIVE_SHOT_IDS = frozenset(
    {"shot_03", "shot_04", "shot_05", "shot_08", "shot_09", "shot_10"}
)
TRIM_FILTER = (
    "silenceremove=start_periods=1:start_duration=0.03:"
    "start_threshold=-42dB:start_silence=0.08,areverse,"
    "silenceremove=start_periods=1:start_duration=0.03:"
    "start_threshold=-42dB:start_silence=0.08,areverse"
)


class PetSitcomAudioFirstError(RuntimeError):
    pass


@dataclass(frozen=True)
class PetVoice:
    voice_id: str
    speech_rate: int


@dataclass(frozen=True)
class PetSpeechAsset:
    shot_id: str
    speaker: str
    text: str
    voice_id: str
    speech_rate: int
    output_path: Path
    output_sha256: str
    duration_seconds: float
    absolute_start_seconds: float
    absolute_end_seconds: float


PET_VOICES: Mapping[str, PetVoice] = MappingProxyType(
    {
        "owner": PetVoice("zh_female_vv_uranus_bigtts", -4),
        "naitang": PetVoice("saturn_zh_female_tiaopigongzhu_tob", 2),
        "doubao": PetVoice("zh_female_meilinvyou_saturn_bigtts", 4),
    }
)


def generate_pet_speech_assets(
    plan: PetSitcomPlan,
    *,
    tts_client: Any,
    allow_network: bool = False,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Generate the immutable audio-first timeline for every spoken shot."""
    _validate_plan(plan)
    shots = tuple(shot for shot in plan.shots if shot.dialogue)
    report: dict[str, Any] = {
        "schema_version": AUDIO_FIRST_SCHEMA,
        "success": False,
        "executed": False,
        "planned_count": len(shots),
        "completed_count": 0,
        "reused_count": 0,
        "assets": [],
        "commands": [],
        "errors": [],
    }
    try:
        assets = load_pet_speech_assets(plan)
    except PetSitcomAudioFirstError:
        assets = ()
    if len(assets) == len(shots):
        report.update(
            {
                "success": True,
                "completed_count": len(assets),
                "reused_count": len(assets),
                "assets": [
                    {**_asset_record(asset), "status": "reused"}
                    for asset in assets
                ],
            }
        )
        return report
    if not allow_network:
        report["blocked_reasons"] = [
            "Live pet sitcom TTS execution is disabled."
        ]
        return report
    model = str(
        getattr(getattr(tts_client, "config", None), "resource_id", "")
    ).strip()
    if model != OWNER_AUDIO_MODEL:
        raise PetSitcomAudioFirstError(
            f"Pet TTS must use {OWNER_AUDIO_MODEL}."
        )

    generated: list[PetSpeechAsset] = []
    shot_starts = _shot_start_times(plan)
    for shot in shots:
        voice = PET_VOICES.get(str(shot.speaker))
        if voice is None:
            raise PetSitcomAudioFirstError(
                f"{shot.shot_id} has no fixed pet sitcom voice."
            )
        output = _speech_output(plan, shot)
        _require_safe_output(output, plan.output_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="pet-audio-first-", dir=plan.output_dir
        ) as directory:
            source = Path(directory) / "provider.wav"
            result = tts_client.synthesize(
                shot.dialogue,
                source,
                voice_id=voice.voice_id,
                speech_rate=voice.speech_rate,
                sample_rate=TTS_SAMPLE_RATE,
            )
            source = Path(getattr(result, "output_path", source))
            _require_provider_source(
                source,
                temporary_dir=Path(directory),
                output_dir=plan.output_dir,
            )
            _require_tts_source(
                source, ffprobe_bin=_ffprobe_for(ffmpeg_bin)
            )
            temporary = Path(directory) / "trimmed.wav"
            command = _trim_command(source, temporary, ffmpeg_bin)
            _run(command_runner, command)
            _require_output_wav(temporary, "trimmed pet TTS")
            probe = _require_audio_probe(temporary, "trimmed pet TTS")
            duration = probe.duration_seconds
            absolute_start = (
                shot_starts[shot.shot_id] + shot.dialogue_offset_seconds
            )
            absolute_end = absolute_start + duration
            available_end = (
                shot_starts[shot.shot_id]
                + shot.duration_seconds
                - DIALOGUE_TAIL_SECONDS
            )
            if duration < MINIMUM_DURATION_SECONDS:
                raise PetSitcomAudioFirstError(
                    f"{shot.shot_id} trimmed TTS is shorter than 0.20 seconds."
                )
            if absolute_start < 0 or absolute_end > available_end + 1e-9:
                raise PetSitcomAudioFirstError(
                    f"{shot.shot_id} TTS overruns its dialogue window."
                )
            os.replace(temporary, output)
        asset = PetSpeechAsset(
            shot_id=shot.shot_id,
            speaker=str(shot.speaker),
            text=shot.dialogue,
            voice_id=voice.voice_id,
            speech_rate=voice.speech_rate,
            output_path=output,
            output_sha256=_sha256(output),
            duration_seconds=duration,
            absolute_start_seconds=absolute_start,
            absolute_end_seconds=absolute_end,
        )
        generated.append(asset)
        report["commands"].append(" ".join(command))
        report["assets"].append(
            {**_asset_record(asset), "status": "generated"}
        )
        report["completed_count"] += 1
        report["executed"] = True

    _write_manifest(plan, tuple(generated))
    report["success"] = True
    return report


def load_pet_speech_assets(
    plan: PetSitcomPlan,
) -> tuple[PetSpeechAsset, ...]:
    """Load and verify the immutable audio-first manifest and WAV hashes."""
    _validate_plan(plan)
    _require_safe_output(plan.audio_manifest_path, plan.output_dir)
    document = _read_json(plan.audio_manifest_path)
    if document.get("schema_version") != AUDIO_FIRST_SCHEMA:
        raise PetSitcomAudioFirstError(
            "Pet speech assets require the audio-first manifest schema."
        )
    if (
        document.get("plan_schema_version") != PLAN_SCHEMA_VERSION
        or document.get("project_id") != plan.project_id
        or document.get("plan_sha256") != _plan_hash(plan)
    ):
        raise PetSitcomAudioFirstError(
            "Pet speech manifest is not bound to the current plan."
        )
    records = document.get("assets")
    shots = tuple(shot for shot in plan.shots if shot.dialogue)
    if not isinstance(records, list) or len(records) != len(shots):
        raise PetSitcomAudioFirstError(
            "Pet speech manifest must contain every spoken shot."
        )
    shot_starts = _shot_start_times(plan)
    assets: list[PetSpeechAsset] = []
    for shot, record in zip(shots, records, strict=True):
        if not isinstance(record, Mapping):
            raise PetSitcomAudioFirstError(
                "Pet speech manifest assets must be objects."
            )
        asset = _asset_from_record(record)
        voice = PET_VOICES.get(str(shot.speaker))
        expected_output = _speech_output(plan, shot)
        _require_safe_output(expected_output, plan.output_dir)
        if (
            voice is None
            or asset.shot_id != shot.shot_id
            or asset.speaker != shot.speaker
            or asset.text != shot.dialogue
            or asset.voice_id != voice.voice_id
            or asset.speech_rate != voice.speech_rate
            or asset.output_path != expected_output
            or not expected_output.is_file()
            or expected_output.is_symlink()
            or asset.output_sha256 != _sha256(expected_output)
        ):
            raise PetSitcomAudioFirstError(
                f"{shot.shot_id} speech asset is missing or stale."
            )
        _require_output_wav(expected_output, f"{shot.shot_id} pet TTS")
        probe = _require_audio_probe(
            expected_output, f"{shot.shot_id} pet TTS"
        )
        if abs(probe.duration_seconds - asset.duration_seconds) > 0.001:
            raise PetSitcomAudioFirstError(
                f"{shot.shot_id} speech duration no longer matches its WAV."
            )
        expected_start = (
            shot_starts[shot.shot_id] + shot.dialogue_offset_seconds
        )
        expected_end = expected_start + asset.duration_seconds
        available_end = (
            shot_starts[shot.shot_id]
            + shot.duration_seconds
            - DIALOGUE_TAIL_SECONDS
        )
        if (
            not math.isfinite(asset.duration_seconds)
            or not math.isfinite(asset.absolute_start_seconds)
            or not math.isfinite(asset.absolute_end_seconds)
            or asset.duration_seconds < MINIMUM_DURATION_SECONDS
            or abs(asset.absolute_start_seconds - expected_start) > 1e-6
            or abs(asset.absolute_end_seconds - expected_end) > 1e-6
            or asset.absolute_start_seconds < 0
            or asset.absolute_end_seconds > available_end + 1e-9
        ):
            raise PetSitcomAudioFirstError(
                f"{shot.shot_id} absolute timing must be finite and match the plan."
            )
        assets.append(asset)
    return tuple(assets)


def build_pet_drive_audio(
    plan: PetSitcomPlan,
    shot_id: str,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Pad one cat TTS WAV to the provider generation duration."""
    _validate_plan(plan)
    if shot_id not in CAT_DRIVE_SHOT_IDS:
        raise PetSitcomAudioFirstError(
            f"{shot_id} is not an approved cat audio-drive shot."
        )
    shot = next(
        (item for item in plan.shots if item.shot_id == shot_id), None
    )
    if shot is None or shot.speaker not in {"naitang", "doubao"}:
        raise PetSitcomAudioFirstError(
            f"{shot_id} is not an approved cat audio-drive shot."
        )
    asset = next(
        (
            item
            for item in load_pet_speech_assets(plan)
            if item.shot_id == shot_id
        ),
        None,
    )
    if asset is None:
        raise PetSitcomAudioFirstError(
            f"{shot_id} has no immutable speech asset."
        )
    output = (
        plan.output_dir / "audio" / "drive" / f"{shot_id}_drive.wav"
    )
    _require_safe_output(output, plan.output_dir)
    state_path = output.with_suffix(".state.json")
    _require_safe_output(state_path, plan.output_dir)
    signature = _drive_signature(shot, asset)
    state = _read_json(state_path)
    if _reusable_drive(output, state, signature, shot):
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = int(round(shot.dialogue_offset_seconds * 1000))
    duration = _number(shot.generation_duration_seconds)
    audio_filter = (
        "aresample=48000,aformat=channel_layouts=stereo,"
        f"adelay={delay_ms}|{delay_ms},apad=pad_dur={duration},"
        f"atrim=duration={duration}"
    )
    command = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(asset.output_path),
        "-af",
        audio_filter,
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(OUTPUT_SAMPLE_RATE),
        "-ac",
        str(OUTPUT_CHANNELS),
        str(output),
    ]
    if "atempo" in " ".join(command):
        raise PetSitcomAudioFirstError("Drive audio cannot retime TTS.")
    _run(command_runner, command)
    _require_exact_drive(output, shot.generation_duration_seconds)
    _write_json(
        state_path,
        {
            "schema_version": DRIVE_AUDIO_STATE_SCHEMA,
            "status": "completed",
            "signature": signature,
            "shot_id": shot.shot_id,
            "dialogue_offset_seconds": shot.dialogue_offset_seconds,
            "generation_duration_seconds": shot.generation_duration_seconds,
            "source_path": str(asset.output_path),
            "source_sha256": asset.output_sha256,
            "source_duration_seconds": asset.duration_seconds,
            "absolute_start_seconds": asset.absolute_start_seconds,
            "absolute_end_seconds": asset.absolute_end_seconds,
            "output_path": str(output),
            "output_sha256": _sha256(output),
        },
        plan.output_dir,
    )
    return output


def _speech_output(plan: PetSitcomPlan, shot: PetShot) -> Path:
    role = "owner" if shot.speaker == "owner" else "cats"
    return plan.output_dir / "audio" / role / f"{shot.shot_id}.wav"


def _shot_start_times(plan: PetSitcomPlan) -> dict[str, float]:
    starts: dict[str, float] = {}
    current = 0.0
    for shot in plan.shots:
        starts[shot.shot_id] = current
        current += shot.duration_seconds
    if abs(current - plan.duration_seconds) > 1e-9:
        raise PetSitcomAudioFirstError(
            "Pet shot durations do not match the plan duration."
        )
    return starts


def _trim_command(source: Path, output: Path, binary: str) -> list[str]:
    return [
        binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-af",
        TRIM_FILTER,
        "-c:a",
        "pcm_s16le",
        "-ar",
        str(OUTPUT_SAMPLE_RATE),
        "-ac",
        str(OUTPUT_CHANNELS),
        str(output),
    ]


def _require_tts_source(path: Path, *, ffprobe_bin: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise PetSitcomAudioFirstError("Pet TTS returned no audio.")
    sample_rate = _probe_audio_sample_rate(
        path, ffprobe_bin=ffprobe_bin
    )
    if sample_rate < TTS_SAMPLE_RATE:
        raise PetSitcomAudioFirstError(
            "Pet TTS source must be at least 24 kHz."
        )
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            frames = audio.getnframes()
    except (OSError, wave.Error):
        probe = _require_audio_probe(path, "pet TTS source")
        if probe.duration_seconds <= 0:
            raise PetSitcomAudioFirstError(
                "Pet TTS source must contain valid audio."
            )
        return
    if (
        channels not in {1, 2}
        or sample_width < 2
        or frames <= 0
    ):
        raise PetSitcomAudioFirstError(
            "Pet TTS source must be at least 24 kHz mono or stereo PCM WAV."
        )


def _probe_audio_sample_rate(path: Path, *, ffprobe_bin: str) -> int:
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30.0,
        )
        payload = json.loads(completed.stdout or "{}")
        streams = payload.get("streams")
        raw_rate = streams[0].get("sample_rate")
        sample_rate = int(raw_rate)
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        AttributeError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise PetSitcomAudioFirstError(
            "Pet TTS source sample rate could not be verified."
        ) from exc
    if (
        not isinstance(streams, list)
        or len(streams) != 1
        or isinstance(raw_rate, bool)
        or sample_rate <= 0
    ):
        raise PetSitcomAudioFirstError(
            "Pet TTS source sample rate could not be verified."
        )
    return sample_rate


def _require_output_wav(path: Path, label: str) -> None:
    try:
        with wave.open(str(path), "rb") as audio:
            valid = (
                audio.getframerate() == OUTPUT_SAMPLE_RATE
                and audio.getnchannels() == OUTPUT_CHANNELS
                and audio.getsampwidth() == 2
                and audio.getnframes() > 0
            )
    except (OSError, wave.Error) as exc:
        raise PetSitcomAudioFirstError(
            f"{label.capitalize()} must be a readable PCM WAV."
        ) from exc
    if not valid:
        raise PetSitcomAudioFirstError(
            f"{label.capitalize()} must be 48 kHz stereo PCM s16le."
        )


def _require_audio_probe(path: Path, label: str) -> Any:
    probe = probe_media(path, required_stream="audio")
    if (
        not probe.valid
        or probe.audio_stream_count != 1
        or probe.video_stream_count != 0
        or probe.duration_seconds <= 0
    ):
        raise PetSitcomAudioFirstError(
            f"{label.capitalize()} must contain one valid audio stream."
        )
    return probe


def _require_exact_drive(path: Path, duration_seconds: int) -> None:
    _require_output_wav(path, "pet drive audio")
    with wave.open(str(path), "rb") as audio:
        if audio.getnframes() != duration_seconds * OUTPUT_SAMPLE_RATE:
            raise PetSitcomAudioFirstError(
                "Pet drive WAV must exactly match generation duration."
            )


def _write_manifest(
    plan: PetSitcomPlan, assets: tuple[PetSpeechAsset, ...]
) -> None:
    _write_json(
        plan.audio_manifest_path,
        {
            "schema_version": AUDIO_FIRST_SCHEMA,
            "plan_schema_version": PLAN_SCHEMA_VERSION,
            "project_id": plan.project_id,
            "plan_sha256": _plan_hash(plan),
            "duration_seconds": plan.duration_seconds,
            "assets": [_asset_record(asset) for asset in assets],
        },
        plan.output_dir,
    )


def _asset_record(asset: PetSpeechAsset) -> dict[str, Any]:
    record = asdict(asset)
    record["output_path"] = str(asset.output_path)
    return record


def _asset_from_record(record: Mapping[str, Any]) -> PetSpeechAsset:
    try:
        return PetSpeechAsset(
            shot_id=str(record["shot_id"]),
            speaker=str(record["speaker"]),
            text=str(record["text"]),
            voice_id=str(record["voice_id"]),
            speech_rate=int(record["speech_rate"]),
            output_path=Path(str(record["output_path"])),
            output_sha256=str(record["output_sha256"]),
            duration_seconds=float(record["duration_seconds"]),
            absolute_start_seconds=float(
                record["absolute_start_seconds"]
            ),
            absolute_end_seconds=float(record["absolute_end_seconds"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PetSitcomAudioFirstError(
            "Pet speech manifest contains an invalid asset."
        ) from exc


def _drive_signature(shot: PetShot, asset: PetSpeechAsset) -> str:
    return _hash(
        {
            "schema_version": DRIVE_AUDIO_STATE_SCHEMA,
            "shot_id": shot.shot_id,
            "dialogue_offset_seconds": shot.dialogue_offset_seconds,
            "generation_duration_seconds": shot.generation_duration_seconds,
            "source_sha256": asset.output_sha256,
            "source_duration_seconds": asset.duration_seconds,
            "absolute_start_seconds": asset.absolute_start_seconds,
            "absolute_end_seconds": asset.absolute_end_seconds,
        }
    )


def _reusable_drive(
    path: Path,
    state: Mapping[str, Any],
    signature: str,
    shot: PetShot,
) -> bool:
    if not (
        state.get("schema_version") == DRIVE_AUDIO_STATE_SCHEMA
        and state.get("status") == "completed"
        and state.get("signature") == signature
        and path.is_file()
        and not path.is_symlink()
        and state.get("output_sha256") == _sha256(path)
    ):
        return False
    try:
        _require_exact_drive(path, shot.generation_duration_seconds)
    except PetSitcomAudioFirstError:
        return False
    return True


def _validate_plan(plan: PetSitcomPlan) -> None:
    try:
        _validate_plan_contract(plan)
    except PetSitcomError as exc:
        raise PetSitcomAudioFirstError(str(exc)) from exc


def _require_safe_output(path: Path, root: Path) -> None:
    candidate = path.expanduser().absolute()
    project = root.expanduser().absolute()
    try:
        relative = candidate.relative_to(project)
    except ValueError as exc:
        raise PetSitcomAudioFirstError(
            "Pet audio output must stay inside output_dir."
        ) from exc
    current = project
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PetSitcomAudioFirstError(
                "Pet audio output must not traverse a symlink."
            )
    try:
        candidate.resolve(strict=False).relative_to(
            project.resolve(strict=False)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetSitcomAudioFirstError(
            "Pet audio output must stay inside output_dir."
        ) from exc


def _require_provider_source(
    path: Path,
    *,
    temporary_dir: Path,
    output_dir: Path,
) -> None:
    _require_safe_output(temporary_dir, output_dir)
    try:
        path.expanduser().absolute().resolve(strict=False).relative_to(
            temporary_dir.expanduser().absolute().resolve(strict=False)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetSitcomAudioFirstError(
            "Pet TTS provider output must stay in the current temporary directory."
        ) from exc
    _require_safe_output(path, temporary_dir)
    if not path.is_file() or path.is_symlink():
        raise PetSitcomAudioFirstError(
            "Pet TTS provider output must be a non-symlink file "
            "in the current temporary directory."
        )


def _plan_hash(plan: PetSitcomPlan) -> str:
    return _hash(plan.to_report())


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: int | float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def _ffprobe_for(binary: str) -> str:
    path = Path(binary)
    return (
        str(path.with_name("ffprobe"))
        if path.name == "ffmpeg" and path.parent != Path(".")
        else "ffprobe"
    )


def _run(runner: Callable[..., Any], command: list[str]) -> None:
    try:
        runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise PetSitcomAudioFirstError(
            f"Pet audio command failed: {detail}"
        ) from exc
    except OSError as exc:
        raise PetSitcomAudioFirstError(
            f"Pet audio command failed: {exc}"
        ) from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(
    path: Path, payload: Mapping[str, Any], root: Path
) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PetSitcomAudioFirstError(
            "Pet audio state path must stay inside output_dir."
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PetSitcomAudioFirstError(
            "Pet audio state path must not be a symlink."
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(
                payload,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
