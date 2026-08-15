from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence


CommandRunner = Callable[..., object]


def _duration(value: float) -> str:
    duration = float(value)
    if duration <= 0:
        raise ValueError("Media duration must be positive")
    return f"{duration:.3f}"


def _run(command: list[str], *, command_runner: CommandRunner) -> None:
    command_runner(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _normalize_command(
    source: Path,
    output: Path,
    *,
    duration_seconds: float,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
) -> list[str]:
    duration = _duration(duration_seconds)
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},setsar=1,format=yuv420p,"
        f"tpad=stop_mode=clone:stop_duration={duration},"
        f"trim=duration={duration},setpts=PTS-STARTPTS"
    )
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source),
        "-vf",
        video_filter,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-r",
        str(fps),
        "-movflags",
        "+faststart",
        "-t",
        duration,
        str(output),
    ]


def assemble_visual_track(
    clips: Sequence[str | Path],
    durations_seconds: Sequence[float],
    output_path: str | Path,
    *,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str = "ffmpeg",
    command_runner: CommandRunner = subprocess.run,
) -> Path:
    if not clips or len(clips) != len(durations_seconds):
        raise ValueError("Clips and durations must be non-empty and have equal length")
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("Media dimensions and FPS must be positive")
    output = Path(output_path).expanduser()
    if output.is_symlink():
        raise ValueError(f"Visual assembly output cannot be a symlink: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / f".{output.stem}.assembly"
    if work.is_symlink():
        raise ValueError(f"Visual assembly work directory cannot be a symlink: {work}")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    normalized: list[Path] = []
    try:
        for index, (raw_clip, duration) in enumerate(
            zip(clips, durations_seconds, strict=True), start=1
        ):
            source = Path(raw_clip).expanduser().resolve()
            if source.is_symlink() or not source.is_file():
                raise FileNotFoundError(f"Video clip is missing: {source}")
            destination = work / f"clip_{index:03d}.mp4"
            _run(
                _normalize_command(
                    source,
                    destination,
                    duration_seconds=duration,
                    width=width,
                    height=height,
                    fps=fps,
                    ffmpeg_bin=ffmpeg_bin,
                ),
                command_runner=command_runner,
            )
            if not destination.is_file():
                raise RuntimeError(f"Normalized video clip was not created: {destination}")
            normalized.append(destination)

        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        if len(normalized) == 1:
            os.replace(normalized[0], temporary)
        else:
            command = [ffmpeg_bin, "-y"]
            for clip in normalized:
                command.extend(["-i", str(clip)])
            inputs = "".join(f"[{index}:v:0]" for index in range(len(normalized)))
            command.extend(
                [
                    "-filter_complex",
                    f"{inputs}concat=n={len(normalized)}:v=1:a=0[v]",
                    "-map",
                    "[v]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
                    "-r",
                    str(fps),
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-t",
                    _duration(sum(float(value) for value in durations_seconds)),
                    str(temporary),
                ]
            )
            _run(command, command_runner=command_runner)
        if not temporary.is_file():
            raise RuntimeError("Visual assembly did not create an output")
        os.replace(temporary, output)
        return output
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_final_mux_command(
    video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    *,
    duration_seconds: float,
    subtitles: str | Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    duration = _duration(duration_seconds)
    command = [ffmpeg_bin, "-y", "-i", str(video_path), "-i", str(audio_path)]
    if subtitles is not None:
        command.extend(["-i", str(subtitles)])
    command.extend(
        [
            "-filter_complex",
            (
                f"[0:v:0]tpad=stop_mode=clone:stop_duration={duration},"
                f"trim=duration={duration},setpts=PTS-STARTPTS[v];"
                f"[1:a:0]apad=pad_dur={duration},atrim=duration={duration},"
                "asetpts=PTS-STARTPTS[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    )
    if subtitles is not None:
        command.extend(["-map", "2:s:0", "-c:s", "mov_text"])
    command.extend(["-movflags", "+faststart", "-t", duration, str(output_path)])
    return command


def mux_final_audio(
    video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    *,
    duration_seconds: float,
    subtitles: str | Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
    command_runner: CommandRunner = subprocess.run,
) -> Path:
    video = Path(video_path).expanduser().resolve()
    audio = Path(audio_path).expanduser().resolve()
    if video.is_symlink() or not video.is_file():
        raise FileNotFoundError(f"Visual track is missing: {video}")
    if audio.is_symlink() or not audio.is_file():
        raise FileNotFoundError(f"Voiceover audio is missing: {audio}")
    subtitle_path = Path(subtitles).expanduser().resolve() if subtitles else None
    if subtitle_path is not None and (
        subtitle_path.is_symlink() or not subtitle_path.is_file()
    ):
        raise FileNotFoundError(f"Subtitles are missing: {subtitle_path}")
    output = Path(output_path).expanduser()
    if output.is_symlink():
        raise ValueError(f"Final media output cannot be a symlink: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    _run(
        build_final_mux_command(
            video,
            audio,
            temporary,
            duration_seconds=duration_seconds,
            subtitles=subtitle_path,
            ffmpeg_bin=ffmpeg_bin,
        ),
        command_runner=command_runner,
    )
    if not temporary.is_file():
        raise RuntimeError("Final media mux did not create an output")
    os.replace(temporary, output)
    return output
