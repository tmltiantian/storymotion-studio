from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .doubao_tts import (
    DoubaoTTSClient,
    DoubaoTTSDefinitiveError,
    DoubaoTTSTask,
    resolve_doubao_tts_config,
)
from .dotenv import parse_dotenv
from .media_validation import probe_media, temporary_media_path
from .placeholder_renderer import episode_duration_seconds
from .schema import Episode, NARRATOR_ID, speaker_name


NARRATOR_VOICE = "Reed (中文（中国大陆）)"
CHARACTER_VOICES = [
    "Eddy (中文（中国大陆）)",
    "Flo (中文（中国大陆）)",
    "Sandy (中文（中国大陆）)",
]
LOCAL_SPEECH_RATE = 165
VOICEOVER_LEAD_SECONDS = 0.4
VOICEOVER_GAP_SECONDS = 0.3
VOICEOVER_TAIL_SECONDS = 0.3
VOICE_CHARS_PER_SECOND = 4.5
TTS_CUE_STATE_SCHEMA = "motion-comic-factory.tts-cue-state.v1"
DOUBAO_POSTPROCESS_PROFILE = "trim-boundaries-v1"
MediaValidator = Callable[[Path, str], bool]


class VoiceoverTimingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceoverCue:
    shot_id: str
    speaker_id: str
    speaker_name: str
    text: str
    voice: str
    start_seconds: float


def _voice_for_speaker(episode: Episode, speaker_id: str) -> str:
    if speaker_id == NARRATOR_ID:
        return NARRATOR_VOICE
    for index, character in enumerate(episode.characters):
        if character.id == speaker_id:
            return CHARACTER_VOICES[index % len(CHARACTER_VOICES)]
    return NARRATOR_VOICE


def build_voiceover_cues(episode: Episode) -> list[VoiceoverCue]:
    cues: list[VoiceoverCue] = []
    shot_start = 0.0
    for shot in episode.shots:
        cursor = shot_start + VOICEOVER_LEAD_SECONDS
        for line in shot.dialogue:
            resolved_speaker_name = speaker_name(episode, line.speaker_id)
            cues.append(
                VoiceoverCue(
                    shot_id=shot.id,
                    speaker_id=line.speaker_id,
                    speaker_name=resolved_speaker_name,
                    text=line.text,
                    voice=_voice_for_speaker(episode, line.speaker_id),
                    start_seconds=round(cursor, 3),
                )
            )
            cursor += _estimate_voice_duration(line.text) + VOICEOVER_GAP_SECONDS
        shot_start += shot.duration_seconds
    return cues


def _estimate_voice_duration(text: str, rate: int = LOCAL_SPEECH_RATE) -> float:
    compact_length = len("".join(text.split()))
    base = max(1.0, compact_length / VOICE_CHARS_PER_SECOND + 0.4)
    return base * LOCAL_SPEECH_RATE / max(1, rate)


def schedule_voiceover_cues(
    episode: Episode,
    cues: list[VoiceoverCue],
    clip_durations: list[float],
) -> tuple[list[VoiceoverCue], list[dict[str, Any]]]:
    if len(cues) != len(clip_durations):
        raise ValueError("cues and clip_durations must have the same length")

    scheduled: list[VoiceoverCue] = []
    timings: list[dict[str, Any]] = []
    cue_index = 0
    shot_start = 0.0
    previous_end = 0.0
    for shot in episode.shots:
        shot_end = shot_start + shot.duration_seconds
        cursor = shot_start + VOICEOVER_LEAD_SECONDS
        while cue_index < len(cues) and cues[cue_index].shot_id == shot.id:
            cue = cues[cue_index]
            duration = float(clip_durations[cue_index])
            if duration <= 0:
                raise VoiceoverTimingError(
                    f"Voiceover clip duration must be positive: {cue.shot_id}"
                )
            start = max(cursor, previous_end + VOICEOVER_GAP_SECONDS)
            end = start + duration
            if end + VOICEOVER_TAIL_SECONDS > shot_end + 0.001:
                raise VoiceoverTimingError(
                    f"Voiceover exceeds {shot.id}: end={end:.3f}s, "
                    f"shot_end={shot_end:.3f}s"
                )
            overlaps_previous = bool(timings and start < previous_end - 0.001)
            scheduled_cue = replace(cue, start_seconds=round(start, 3))
            scheduled.append(scheduled_cue)
            timings.append(
                {
                    "shot_id": cue.shot_id,
                    "speaker_id": cue.speaker_id,
                    "speaker_name": cue.speaker_name,
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                    "duration_seconds": round(duration, 3),
                    "overlaps_previous": overlaps_previous,
                }
            )
            previous_end = end
            cursor = end + VOICEOVER_GAP_SECONDS
            cue_index += 1
        shot_start = shot_end

    if cue_index != len(cues):
        raise VoiceoverTimingError("Voiceover cues contain an unknown or unsorted shot ID.")
    return scheduled, timings


def _measure_clip_durations(
    clip_paths: list[Path],
    cues: list[VoiceoverCue],
    *,
    fallback_rate: int,
) -> list[float]:
    durations: list[float] = []
    for clip, cue in zip(clip_paths, cues):
        probe = probe_media(clip, required_stream="audio")
        durations.append(
            probe.duration_seconds
            if probe.valid
            else _estimate_voice_duration(cue.text, rate=fallback_rate)
        )
    return durations


def write_voiceover_script(cues: list[VoiceoverCue], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{cue.start_seconds:05.2f}s {cue.speaker_name}：{cue.text} [voice={cue.voice}]"
        for cue in cues
    ]
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return output


def _doubao_voice_for_cue(
    episode: Episode,
    cue: VoiceoverCue,
    voice_map: Mapping[str, str],
    default_voice: str,
) -> str:
    keys = [cue.speaker_id, cue.speaker_name]
    if cue.speaker_id == NARRATOR_ID:
        keys.extend(["narrator", "旁白"])
    else:
        for index, character in enumerate(episode.characters, start=1):
            if character.id == cue.speaker_id:
                keys.extend([f"character_{index}", f"role_{index}"])
                break
    for key in keys:
        value = str(voice_map.get(key) or "").strip()
        if value:
            return value
    return default_voice


def _cue_signature(
    cue: VoiceoverCue,
    *,
    voice_id: str,
    resource_id: str,
    speech_rate: int = 0,
    context_text: str = "",
) -> str:
    payload = {
        "speaker_id": cue.speaker_id,
        "text": cue.text,
        "voice_id": voice_id,
        "resource_id": resource_id,
        "speech_rate": speech_rate,
        "sample_rate": 24000,
        "context_text": context_text,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_cue_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cue_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _completed_cue_is_reusable(
    state: dict[str, Any],
    *,
    signature: str,
    output_path: Path,
) -> bool:
    return (
        state.get("schema_version") == TTS_CUE_STATE_SCHEMA
        and state.get("signature") == signature
        and state.get("status") == "completed"
        and output_path.is_file()
        and output_path.stat().st_size > 0
    )


def _actual_voiceover_provider(doubao_count: int, local_count: int) -> str:
    if doubao_count and local_count:
        return "mixed"
    if doubao_count:
        return "doubao"
    return "local"


def build_say_command(
    *,
    text: str,
    voice: str,
    output_path: str | Path,
    rate: int = LOCAL_SPEECH_RATE,
    say_bin: str = "say",
) -> list[str]:
    return [say_bin, "-v", voice, "-r", str(rate), "-o", str(output_path), text]


def build_trim_voiceover_clip_command(
    *,
    input_path: str | Path,
    output_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    boundary_trim = (
        "silenceremove=start_periods=1:start_duration=0.03:"
        "start_threshold=-42dB:start_silence=0.08,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.03:"
        "start_threshold=-42dB:start_silence=0.08,"
        "areverse"
    )
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_path),
        "-af",
        boundary_trim,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-ar",
        "24000",
        "-ac",
        "1",
        str(output_path),
    ]


def build_mix_voiceover_audio_command(
    *,
    clip_paths: list[Path],
    starts_seconds: list[float],
    duration_seconds: float,
    output_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    if len(clip_paths) != len(starts_seconds):
        raise ValueError("clip_paths and starts_seconds must have the same length")

    output = str(output_path)
    if not clip_paths:
        return [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:a",
            "aac",
            output,
        ]

    cmd = [ffmpeg_bin, "-y"]
    for clip in clip_paths:
        cmd.extend(["-i", str(clip)])

    filters: list[str] = []
    labels: list[str] = []
    for index, start in enumerate(starts_seconds):
        delay_ms = max(0, int(round(start * 1000)))
        label = f"a{index}"
        filters.append(f"[{index}:a]adelay={delay_ms}|{delay_ms}[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"apad=pad_dur={duration_seconds:.3f}[aout]"
    )

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[aout]",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            output,
        ]
    )
    return cmd


def build_mux_voiced_preview_command(
    *,
    source_video_path: str | Path,
    voiceover_audio_path: str | Path,
    output_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_video_path),
        "-i",
        str(voiceover_audio_path),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "0:s?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-c:s",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def _run_atomic_media_command(
    command: list[str],
    *,
    required_stream: str,
    command_runner: Callable[..., Any],
    media_validator: MediaValidator | None,
) -> Path:
    if not command:
        raise ValueError("Media command cannot be empty.")
    output = Path(command[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_media_path(output)
    temporary_command = [*command[:-1], str(temporary_output)]
    try:
        command_runner(
            temporary_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        valid = (
            media_validator(temporary_output, required_stream)
            if media_validator is not None
            else probe_media(
                temporary_output,
                required_stream=required_stream,
            ).valid
        )
        if not valid:
            raise RuntimeError(
                f"Media command did not produce a valid {required_stream} stream."
            )
        temporary_output.replace(output)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise
    return output


def _postprocess_doubao_clip(
    clip_path: Path,
    state_path: Path,
    state: dict[str, Any],
    *,
    command_runner: Callable[..., Any],
    media_validator: MediaValidator | None,
    ffmpeg_bin: str,
) -> None:
    if state.get("postprocess_profile") == DOUBAO_POSTPROCESS_PROFILE:
        return
    _run_atomic_media_command(
        build_trim_voiceover_clip_command(
            input_path=clip_path,
            output_path=clip_path,
            ffmpeg_bin=ffmpeg_bin,
        ),
        required_stream="audio",
        command_runner=command_runner,
        media_validator=media_validator,
    )
    _write_cue_state(
        state_path,
        {
            **state,
            "postprocess_profile": DOUBAO_POSTPROCESS_PROFILE,
            "output_size_bytes": clip_path.stat().st_size,
        },
    )


def render_local_voiceover_preview(
    episode: Episode,
    source_video_path: str | Path,
    output_path: str | Path,
    work_dir: str | Path,
    say_bin: str = "say",
    ffmpeg_bin: str = "ffmpeg",
    command_runner: Callable[..., Any] = subprocess.run,
    media_validator: MediaValidator | None = None,
) -> dict[str, Path | int]:
    work = Path(work_dir)
    clips_dir = work / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cues = build_voiceover_cues(episode)

    clip_paths: list[Path] = []
    for index, cue in enumerate(cues):
        clip_path = clips_dir / f"{index:03d}_{cue.speaker_id}.aiff"
        command_runner(
            build_say_command(
                text=cue.text,
                voice=cue.voice,
                output_path=clip_path,
                rate=LOCAL_SPEECH_RATE,
                say_bin=say_bin,
            ),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        clip_paths.append(clip_path)

    clip_durations = _measure_clip_durations(
        clip_paths,
        cues,
        fallback_rate=LOCAL_SPEECH_RATE,
    )
    cues, timings = schedule_voiceover_cues(episode, cues, clip_durations)
    script_path = write_voiceover_script(cues, work / "voiceover_script.txt")

    voiceover_audio = work / "voiceover.m4a"
    _run_atomic_media_command(
        build_mix_voiceover_audio_command(
            clip_paths=clip_paths,
            starts_seconds=[cue.start_seconds for cue in cues],
            duration_seconds=episode_duration_seconds(episode),
            output_path=voiceover_audio,
            ffmpeg_bin=ffmpeg_bin,
        ),
        required_stream="audio",
        command_runner=command_runner,
        media_validator=media_validator,
    )
    _run_atomic_media_command(
        build_mux_voiced_preview_command(
            source_video_path=source_video_path,
            voiceover_audio_path=voiceover_audio,
            output_path=output,
            ffmpeg_bin=ffmpeg_bin,
        ),
        required_stream="video",
        command_runner=command_runner,
        media_validator=media_validator,
    )

    return {
        "voiceover_script": script_path,
        "voiceover_audio": voiceover_audio,
        "voiced_preview_video": output,
        "voiceover_clip_count": len(clip_paths),
        "voiceover_timings": timings,
    }


def render_voiceover_preview(
    episode: Episode,
    source_video_path: str | Path,
    output_path: str | Path,
    work_dir: str | Path,
    *,
    config: dict,
    process_env: Mapping[str, str] | None = None,
    doubao_client: Any | None = None,
    command_runner: Callable[..., Any] = subprocess.run,
    media_validator: MediaValidator | None = None,
    say_bin: str = "say",
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    env = process_env if process_env is not None else os.environ
    factory_values = parse_dotenv(Path(config["workspace"]) / ".env")
    process_provider = env.get("TTS_PROVIDER")
    factory_provider = factory_values.get("TTS_PROVIDER")
    if process_provider:
        provider_selection_source = "process_env"
        requested_provider = process_provider
    elif factory_provider:
        provider_selection_source = "factory.env"
        requested_provider = factory_provider
    else:
        provider_selection_source = "default"
        requested_provider = "auto"
    requested_provider = requested_provider.strip().lower()
    if requested_provider not in {"auto", "doubao", "local"}:
        raise ValueError(f"Unsupported TTS_PROVIDER: {requested_provider}")
    raw_local_rate = (
        env.get("LOCAL_TTS_RATE")
        or factory_values.get("LOCAL_TTS_RATE")
        or str(LOCAL_SPEECH_RATE)
    )
    try:
        local_speech_rate = int(raw_local_rate)
    except (TypeError, ValueError) as exc:
        raise ValueError("LOCAL_TTS_RATE must be an integer.") from exc
    if not 100 <= local_speech_rate <= 240:
        raise ValueError("LOCAL_TTS_RATE must be between 100 and 240.")

    doubao_config = resolve_doubao_tts_config(config, process_env=env)
    if requested_provider == "doubao" and doubao_config is None:
        raise RuntimeError(
            "TTS_PROVIDER=doubao requires either DOUBAO_SPEECH_API_KEY or "
            "DOUBAO_TTS_APPID plus DOUBAO_TTS_ACCESS_KEY, and a Doubao voice."
        )
    selected_provider = (
        "doubao" if doubao_config and requested_provider != "local" else "local"
    )
    if selected_provider == "doubao" and doubao_client is None:
        doubao_client = DoubaoTTSClient(doubao_config)

    work = Path(work_dir)
    clips_dir = work / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cues = build_voiceover_cues(episode)
    clip_paths: list[Path] = []
    metadata_paths: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []
    doubao_clip_count = 0
    local_clip_count = 0
    reused_clip_count = 0
    resumed_clip_count = 0
    voice_assignments: dict[str, dict[str, str]] = {}

    for index, cue in enumerate(cues):
        if selected_provider == "doubao" and doubao_config is not None:
            clip_path = clips_dir / f"{index:03d}_{cue.speaker_id}.mp3"
            metadata_path = clip_path.with_suffix(clip_path.suffix + ".json")
            state_path = clip_path.with_suffix(".tts.json")
            voice_id = _doubao_voice_for_cue(
                episode,
                cue,
                doubao_config.voice_map,
                doubao_config.voice_type,
            )
            voice_assignments[cue.speaker_id] = {
                "speaker_name": cue.speaker_name,
                "voice_id": voice_id,
            }
            signature = _cue_signature(
                cue,
                voice_id=voice_id,
                resource_id=(
                    f"{doubao_config.resource_id}:{doubao_config.auth_mode}"
                ),
                speech_rate=doubao_config.speech_rate,
                context_text=doubao_config.context_text,
            )
            state = _read_cue_state(state_path)
            try:
                if _completed_cue_is_reusable(
                    state,
                    signature=signature,
                    output_path=clip_path,
                ):
                    _postprocess_doubao_clip(
                        clip_path,
                        state_path,
                        state,
                        command_runner=command_runner,
                        media_validator=media_validator,
                        ffmpeg_bin=ffmpeg_bin,
                    )
                    clip_paths.append(clip_path)
                    if metadata_path.is_file():
                        metadata_paths.append(str(metadata_path))
                    doubao_clip_count += 1
                    reused_clip_count += 1
                    continue

                state_matches = (
                    state.get("schema_version") == TTS_CUE_STATE_SCHEMA
                    and state.get("signature") == signature
                )
                if state_matches and state.get("status") == "submitting":
                    raise RuntimeError(
                        "Doubao cue has an ambiguous submitting state; automatic "
                        "resubmission is disabled to prevent duplicate billing."
                    )

                supports_async_tasks = bool(
                    getattr(doubao_client, "supports_async_tasks", True)
                )
                if (
                    state_matches
                    and state.get("status") == "submitted"
                    and state.get("task_id")
                    and supports_async_tasks
                    and hasattr(doubao_client, "complete_task")
                ):
                    task = DoubaoTTSTask(
                        task_id=str(state["task_id"]),
                        request_id=str(state.get("request_id") or "resume"),
                    )
                    resumed_clip_count += 1
                    result = doubao_client.complete_task(
                        task,
                        clip_path,
                        metadata_path=metadata_path,
                    )
                elif supports_async_tasks and hasattr(
                    doubao_client, "submit"
                ) and hasattr(
                    doubao_client, "complete_task"
                ):
                    request_id = str(uuid.uuid4())
                    base_state = {
                        "schema_version": TTS_CUE_STATE_SCHEMA,
                        "signature": signature,
                        "status": "submitting",
                        "request_id": request_id,
                        "speaker_id": cue.speaker_id,
                        "voice_id": voice_id,
                        "output_path": str(clip_path),
                        "metadata_path": str(metadata_path),
                    }
                    _write_cue_state(state_path, base_state)
                    task = doubao_client.submit(
                        cue.text,
                        voice_id=voice_id,
                        request_id=request_id,
                        speech_rate=doubao_config.speech_rate,
                    )
                    submitted_state = {
                        **base_state,
                        "status": "submitted",
                        "task_id": task.task_id,
                    }
                    _write_cue_state(state_path, submitted_state)
                    result = doubao_client.complete_task(
                        task,
                        clip_path,
                        metadata_path=metadata_path,
                    )
                else:
                    request_id = str(uuid.uuid4())
                    _write_cue_state(
                        state_path,
                        {
                            "schema_version": TTS_CUE_STATE_SCHEMA,
                            "signature": signature,
                            "status": "submitting",
                            "request_id": request_id,
                            "speaker_id": cue.speaker_id,
                            "voice_id": voice_id,
                            "output_path": str(clip_path),
                            "metadata_path": str(metadata_path),
                        },
                    )
                    result = doubao_client.synthesize(
                        cue.text,
                        clip_path,
                        voice_id=voice_id,
                        metadata_path=metadata_path,
                        speech_rate=doubao_config.speech_rate,
                    )

                result_output = Path(result.output_path)
                if not result_output.is_file() or result_output.stat().st_size <= 0:
                    raise RuntimeError("Doubao TTS completed without a non-empty audio file.")
                completed_state = {
                    "schema_version": TTS_CUE_STATE_SCHEMA,
                    "signature": signature,
                    "status": "completed",
                    "request_id": str(
                        getattr(result, "request_id", "")
                        or state.get("request_id")
                        or ""
                    ),
                    "task_id": str(getattr(result, "task_id", "") or ""),
                    "speaker_id": cue.speaker_id,
                    "voice_id": voice_id,
                    "output_path": str(result_output),
                    "metadata_path": str(result.metadata_path),
                    "output_size_bytes": result_output.stat().st_size,
                }
                _write_cue_state(
                    state_path,
                    completed_state,
                )
                _postprocess_doubao_clip(
                    result_output,
                    state_path,
                    completed_state,
                    command_runner=command_runner,
                    media_validator=media_validator,
                    ffmpeg_bin=ffmpeg_bin,
                )
                clip_paths.append(result_output)
                metadata_paths.append(str(result.metadata_path))
                doubao_clip_count += 1
                continue
            except Exception as exc:
                if isinstance(exc, DoubaoTTSDefinitiveError):
                    failed_state = _read_cue_state(state_path)
                    if (
                        failed_state.get("schema_version") == TTS_CUE_STATE_SCHEMA
                        and failed_state.get("signature") == signature
                    ):
                        _write_cue_state(
                            state_path,
                            {
                                **failed_state,
                                "status": "failed",
                                "retryable": True,
                                "error_type": type(exc).__name__,
                            },
                        )
                message = str(exc)
                if doubao_config:
                    for secret in (
                        doubao_config.api_key,
                        doubao_config.app_id,
                        doubao_config.access_key,
                    ):
                        if secret:
                            message = message.replace(secret, "[redacted]")
                errors.append(f"{cue.shot_id}/{cue.speaker_id}: {message}")

        clip_path = clips_dir / f"{index:03d}_{cue.speaker_id}.aiff"
        voice_assignments[cue.speaker_id] = {
            "speaker_name": cue.speaker_name,
            "voice_id": cue.voice,
        }
        command_runner(
            build_say_command(
                text=cue.text,
                voice=cue.voice,
                output_path=clip_path,
                rate=local_speech_rate,
                say_bin=say_bin,
            ),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        clip_paths.append(clip_path)
        local_clip_count += 1

    clip_durations = _measure_clip_durations(
        clip_paths,
        cues,
        fallback_rate=local_speech_rate,
    )
    cues, timings = schedule_voiceover_cues(episode, cues, clip_durations)
    script_path = write_voiceover_script(cues, work / "voiceover_script.txt")

    assigned_voice_ids = {
        assignment["voice_id"] for assignment in voice_assignments.values()
    }
    role_voice_distinct = (
        len(voice_assignments) <= 1
        or len(assigned_voice_ids) == len(voice_assignments)
    )
    if selected_provider == "doubao" and not role_voice_distinct:
        warnings.append(
            "Multiple speakers share one Doubao voice. Configure "
            "DOUBAO_SPEECH_VOICE_MAP for distinct role voices."
        )

    voiceover_audio = work / "voiceover.m4a"
    _run_atomic_media_command(
        build_mix_voiceover_audio_command(
            clip_paths=clip_paths,
            starts_seconds=[cue.start_seconds for cue in cues],
            duration_seconds=episode_duration_seconds(episode),
            output_path=voiceover_audio,
            ffmpeg_bin=ffmpeg_bin,
        ),
        required_stream="audio",
        command_runner=command_runner,
        media_validator=media_validator,
    )
    _run_atomic_media_command(
        build_mux_voiced_preview_command(
            source_video_path=source_video_path,
            voiceover_audio_path=voiceover_audio,
            output_path=output,
            ffmpeg_bin=ffmpeg_bin,
        ),
        required_stream="video",
        command_runner=command_runner,
        media_validator=media_validator,
    )

    report_path = work / "voiceover_provider_report.json"
    actual_provider = _actual_voiceover_provider(
        doubao_clip_count,
        local_clip_count,
    )
    doubao_configuration_source = doubao_config.source if doubao_config else None
    configuration_source = (
        doubao_configuration_source
        if actual_provider in {"doubao", "mixed"}
        else provider_selection_source
    )
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.voiceover-provider.v1",
                "requested_provider": requested_provider,
                "selected_provider": selected_provider,
                "provider": actual_provider,
                "configuration_source": configuration_source,
                "provider_selection_source": provider_selection_source,
                "doubao_configuration_source": doubao_configuration_source,
                "doubao_speech_rate": (
                    doubao_config.speech_rate if doubao_config else None
                ),
                "local_speech_rate": local_speech_rate,
                "doubao_clip_count": doubao_clip_count,
                "local_clip_count": local_clip_count,
                "reused_clip_count": reused_clip_count,
                "resumed_clip_count": resumed_clip_count,
                "role_voice_distinct": role_voice_distinct,
                "voice_assignments": list(voice_assignments.values()),
                "metadata_paths": metadata_paths,
                "timings": timings,
                "timing_overlap_count": sum(
                    bool(item["overlaps_previous"]) for item in timings
                ),
                "errors": errors,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "voiceover_script": script_path,
        "voiceover_audio": voiceover_audio,
        "voiced_preview_video": output,
        "voiceover_clip_count": len(clip_paths),
        "voiceover_provider": actual_provider,
        "voiceover_provider_report": report_path,
        "doubao_clip_count": doubao_clip_count,
        "local_clip_count": local_clip_count,
        "reused_clip_count": reused_clip_count,
        "resumed_clip_count": resumed_clip_count,
        "voiceover_timings": timings,
    }
