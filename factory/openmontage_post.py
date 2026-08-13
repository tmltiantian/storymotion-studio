from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .edit_decisions import (
    EditDecisionError,
    load_adjacent_edit_decisions,
    prepare_render_subtitles,
)
from .media_validation import probe_media, temporary_media_path


TOOL_CANDIDATES = [
    "tools/video/video_compose.py",
    "tools/video/remotion_caption_burn.py",
    "tools/audio/audio_mixer.py",
    "tools/subtitle/subtitle_gen.py",
    "remotion-composer/package.json",
]

CaptionBurnRunner = Callable[..., dict[str, Any]]
CopyRunner = Callable[[list[str]], None]


class OpenMontagePostError(RuntimeError):
    pass


def build_openmontage_finalize_command(
    *,
    source_video_path: str | Path,
    output_video_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-i",
        str(source_video_path),
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_video_path),
    ]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _available_tool_candidates(openmontage_path: Path) -> list[str]:
    return [relative for relative in TOOL_CANDIDATES if (openmontage_path / relative).exists()]


def _parse_srt_timestamp(value: str) -> float:
    match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    hours, minutes, seconds, milliseconds = [int(part) for part in match.groups()]
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _split_caption_text(text: str, max_chars: int) -> list[str]:
    normalized = re.sub(r"\s+", "", text.strip())
    if not normalized:
        return []

    chunks: list[str] = []
    current = ""
    punctuation = "，。！？；：,.!?;:"
    for char in normalized:
        current += char
        should_break = len(current) >= max_chars or (char in punctuation and len(current) >= 6)
        if should_break:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    if len(chunks) > 1 and len(chunks[-1]) < 4:
        combined = chunks[-2] + chunks[-1]
        midpoint = (len(combined) + 1) // 2
        chunks[-2:] = [combined[:midpoint], combined[midpoint:]]
    return chunks


def build_caption_segments_from_srt(srt_path: str | Path, *, max_chars: int = 18) -> list[dict[str, Any]]:
    content = Path(srt_path).read_text(encoding="utf-8")
    words: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        timing = lines[1]
        if "-->" not in timing:
            continue
        start_raw, end_raw = [part.strip() for part in timing.split("-->", 1)]
        start = _parse_srt_timestamp(start_raw)
        end = _parse_srt_timestamp(end_raw)
        chunks = _split_caption_text("".join(lines[2:]), max_chars=max_chars)
        if not chunks:
            continue
        total_weight = sum(max(1, len(chunk)) for chunk in chunks)
        cursor = start
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                chunk_end = end
            else:
                chunk_end = cursor + ((end - start) * max(1, len(chunk)) / total_weight)
            words.append({"word": chunk, "start": round(cursor, 3), "end": round(chunk_end, 3)})
            cursor = chunk_end
    return [{"words": words}] if words else []


def build_openmontage_post_report(
    *,
    package_path: str | Path,
    source_video_path: str | Path,
    subtitles_path: str | Path | None = None,
    output_video_path: str | Path,
    command: list[str],
    mode: str = "factory_ffmpeg_finalization_with_openmontage_handoff",
    openmontage_execution: dict[str, Any] | None = None,
    fallback_reason: str | None = None,
    subtitle_edit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_file = Path(package_path)
    package = _read_json(package_file)
    source = Path(source_video_path)
    subtitles = Path(subtitles_path) if subtitles_path else None
    output = Path(output_video_path)
    openmontage_path = Path(package.get("openmontage_path", ""))
    output_exists = output.exists() and output.stat().st_size > 0

    return {
        "schema_version": "motion-comic-factory.openmontage-post.v1",
        "project_id": package.get("project_id"),
        "title": package.get("title"),
        "mode": mode,
        "success": output_exists,
        "package_path": str(package_file),
        "source_video_path": str(source),
        "source_video_exists": source.exists(),
        "subtitles_path": str(subtitles) if subtitles else "",
        "subtitles_exists": subtitles.exists() if subtitles else False,
        "final_preview_path": str(output),
        "final_preview_exists": output_exists,
        "final_preview_bytes": output.stat().st_size if output.exists() else 0,
        "openmontage_path": str(openmontage_path),
        "openmontage_available": openmontage_path.exists(),
        "openmontage_tool_candidates": _available_tool_candidates(openmontage_path),
        "target": package.get("target", {}),
        "timeline_count": len(package.get("timeline") or []),
        "command": command,
        "openmontage_execution": openmontage_execution,
        "fallback_reason": fallback_reason,
        "subtitle_edit": subtitle_edit,
        "notes": [
            "This finalizes the factory voiced preview as the current deliverable.",
            "When available, OpenMontage remotion_caption_burn renders the final preview.",
            "If that renderer is unavailable, the factory FFmpeg stream-copy finalizer preserves delivery output.",
        ],
    }


def write_openmontage_post_report(
    *,
    package_path: str | Path,
    source_video_path: str | Path,
    subtitles_path: str | Path | None = None,
    output_video_path: str | Path,
    command: list[str],
    output_path: str | Path,
    mode: str = "factory_ffmpeg_finalization_with_openmontage_handoff",
    openmontage_execution: dict[str, Any] | None = None,
    fallback_reason: str | None = None,
    subtitle_edit: dict[str, Any] | None = None,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = build_openmontage_post_report(
        package_path=package_path,
        source_video_path=source_video_path,
        subtitles_path=subtitles_path,
        output_video_path=output_video_path,
        command=command,
        mode=mode,
        openmontage_execution=openmontage_execution,
        fallback_reason=fallback_reason,
        subtitle_edit=subtitle_edit,
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _run_copy_command(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _run_openmontage_caption_burn(
    *,
    package_path: str | Path,
    source_video_path: str | Path,
    subtitles_path: str | Path,
    output_video_path: str | Path,
) -> dict[str, Any]:
    package = _read_json(Path(package_path))
    openmontage_path = Path(str(package.get("openmontage_path", "")))
    if not openmontage_path.exists():
        return {
            "tool": "remotion_caption_burn",
            "success": False,
            "error": f"OpenMontage path not found: {openmontage_path}",
        }

    subtitles = Path(subtitles_path)
    if not subtitles.is_file():
        return {
            "tool": "remotion_caption_burn",
            "success": False,
            "error": f"SRT subtitles not found: {subtitles}",
        }

    sys.path.insert(0, str(openmontage_path))
    try:
        from tools.video.remotion_caption_burn import RemotionCaptionBurn

        segments = build_caption_segments_from_srt(subtitles)
        result = RemotionCaptionBurn().execute(
            {
                "input_path": str(source_video_path),
                "output_path": str(output_video_path),
                "segments": segments,
                "force_ffmpeg": False,
                "words_per_page": 1,
                "font_size": 44,
                "highlight_color": "#22D3EE",
            }
        )
        method = result.data.get("method") if result.data else None
        return {
            "tool": "remotion_caption_burn",
            "success": result.success,
            "method": method,
            "data": result.data,
            "artifacts": result.artifacts,
            "error": result.error,
        }
    except Exception as exc:
        return {
            "tool": "remotion_caption_burn",
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        try:
            sys.path.remove(str(openmontage_path))
        except ValueError:
            pass


def _openmontage_mode(openmontage_execution: dict[str, Any]) -> str:
    method = openmontage_execution.get("method")
    return f"openmontage_{method}_caption_burn" if method else "openmontage_caption_burn"


def finalize_openmontage_preview(
    *,
    package_path: str | Path,
    source_video_path: str | Path,
    subtitles_path: str | Path | None = None,
    output_video_path: str | Path,
    report_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
    caption_burn_runner: CaptionBurnRunner | None = None,
    copy_runner: CopyRunner | None = None,
    media_validator: Callable[[Path], bool] | None = None,
) -> Path:
    output = Path(output_video_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_media_path(output)
    command = build_openmontage_finalize_command(
        source_video_path=source_video_path,
        output_video_path=temporary_output,
        ffmpeg_bin=ffmpeg_bin,
    )
    validator = media_validator or (
        lambda path: probe_media(path, required_stream="video").valid
    )
    openmontage_execution = None
    fallback_reason = None
    mode = "factory_ffmpeg_finalization_with_openmontage_handoff"
    render_subtitles_path = Path(subtitles_path) if subtitles_path else None
    subtitle_edit: dict[str, Any] | None = None

    if render_subtitles_path is not None:
        try:
            package = _read_json(Path(package_path))
            timeline = package.get("timeline")
            shot_ids = {
                str(item.get("shot_id") or "").strip()
                for item in timeline or []
                if isinstance(item, dict) and str(item.get("shot_id") or "").strip()
            }
            decisions = load_adjacent_edit_decisions(
                package_path,
                expected_project_id=str(package.get("project_id") or "").strip(),
                valid_shot_ids=shot_ids,
            )
            subtitle_plan = prepare_render_subtitles(
                render_subtitles_path,
                decisions,
            )
            render_subtitles_path = subtitle_plan.path
            subtitle_edit = subtitle_plan.to_report()
        except (EditDecisionError, OSError, json.JSONDecodeError) as exc:
            temporary_output.unlink(missing_ok=True)
            raise OpenMontagePostError(str(exc)) from exc

    if render_subtitles_path is not None:
        runner = caption_burn_runner or _run_openmontage_caption_burn
        try:
            openmontage_execution = runner(
                package_path=package_path,
                source_video_path=source_video_path,
                subtitles_path=render_subtitles_path,
                output_video_path=temporary_output,
            )
        except Exception as exc:
            openmontage_execution = {
                "tool": "caption_burn_runner",
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if openmontage_execution.get("success") and validator(temporary_output):
            temporary_output.replace(output)
            mode = _openmontage_mode(openmontage_execution)
            return write_openmontage_post_report(
                package_path=package_path,
                source_video_path=source_video_path,
                subtitles_path=render_subtitles_path,
                output_video_path=output,
                command=[],
                output_path=report_path,
                mode=mode,
                openmontage_execution=openmontage_execution,
                subtitle_edit=subtitle_edit,
            )
        fallback_reason = str(openmontage_execution.get("error") or "OpenMontage caption burn did not produce output")
        temporary_output.unlink(missing_ok=True)
    else:
        fallback_reason = "subtitles_path missing; OpenMontage caption burn skipped"

    runner = copy_runner or _run_copy_command
    try:
        runner(command)
    except subprocess.CalledProcessError as exc:
        temporary_output.unlink(missing_ok=True)
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise OpenMontagePostError(
            f"OpenMontage fallback finalization failed: {detail}"
        ) from exc
    except OSError as exc:
        temporary_output.unlink(missing_ok=True)
        raise OpenMontagePostError(
            f"Unable to run OpenMontage fallback finalization: {exc}"
        ) from exc
    if not validator(temporary_output):
        temporary_output.unlink(missing_ok=True)
        raise OpenMontagePostError(
            "OpenMontage fallback finalization did not produce a valid video stream."
        )
    temporary_output.replace(output)
    return write_openmontage_post_report(
        package_path=package_path,
        source_video_path=source_video_path,
        subtitles_path=render_subtitles_path,
        output_video_path=output,
        command=command,
        output_path=report_path,
        mode=mode,
        openmontage_execution=openmontage_execution,
        fallback_reason=fallback_reason,
        subtitle_edit=subtitle_edit,
    )
