from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .edit_decisions import (
    EditDecisionError,
    EditDecisions,
    empty_edit_decisions,
    load_adjacent_edit_decisions,
)
from .gateway_video import is_valid_mp4_file
from .media_validation import probe_media, temporary_media_path
from .schema import Episode


class HybridPreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class HybridShotSource:
    shot_id: str
    index: int
    duration_seconds: float
    kind: str
    path: Path
    fallback_reason: str = ""
    source_end_seconds: float | None = None
    drop_ranges_seconds: tuple[tuple[float, float], ...] = ()
    edit_note: str = ""

    def to_report(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "index": self.index,
            "duration_seconds": self.duration_seconds,
            "source_kind": self.kind,
            "source_path": str(self.path),
            "fallback_reason": self.fallback_reason or None,
            "source_end_seconds": self.source_end_seconds,
            "drop_ranges_seconds": [
                [start, end] for start, end in self.drop_ranges_seconds
            ],
            "edit_note": self.edit_note or None,
        }


def select_hybrid_shot_sources(
    episode: Episode,
    package: dict[str, Any],
    cards: Sequence[str | Path],
    *,
    edit_decisions: EditDecisions | None = None,
) -> list[HybridShotSource]:
    if len(cards) != len(episode.shots):
        raise HybridPreviewError("Hybrid preview card count must match shot count.")

    timeline = package.get("timeline")
    if not isinstance(timeline, list):
        raise HybridPreviewError("OpenMontage package timeline must be a list.")
    entries: dict[str, dict[str, Any]] = {}
    for item in timeline:
        if not isinstance(item, dict):
            continue
        shot_id = str(item.get("shot_id") or "").strip()
        if shot_id:
            if shot_id in entries:
                raise HybridPreviewError(
                    f"OpenMontage package contains duplicate shot ID: {shot_id}"
                )
            entries[shot_id] = item

    decisions = edit_decisions or empty_edit_decisions(episode.project_id)
    sources: list[HybridShotSource] = []
    for shot, card_value in zip(episode.shots, cards):
        card = Path(card_value)
        if not card.is_file():
            raise HybridPreviewError(f"Hybrid preview card not found: {card}")
        entry = entries.get(shot.id)
        if entry is None:
            raise HybridPreviewError(
                f"OpenMontage package is missing timeline shot: {shot.id}"
            )
        expected_assets = entry.get("expected_assets")
        expected_assets = expected_assets if isinstance(expected_assets, dict) else {}
        raw_video = str(expected_assets.get("video_clip") or "").strip()
        video = Path(raw_video).expanduser() if raw_video else None
        if video is not None and is_valid_mp4_file(video):
            shot_edit = decisions.shots.get(shot.id)
            sources.append(
                HybridShotSource(
                    shot_id=shot.id,
                    index=shot.index,
                    duration_seconds=shot.duration_seconds,
                    kind="video",
                    path=video,
                    source_end_seconds=(
                        shot_edit.source_end_seconds if shot_edit else None
                    ),
                    drop_ranges_seconds=(
                        shot_edit.drop_ranges_seconds if shot_edit else ()
                    ),
                    edit_note=shot_edit.note if shot_edit else "",
                )
            )
            continue

        fallback_reason = (
            "video_invalid" if video is not None and video.exists() else "video_missing"
        )
        sources.append(
            HybridShotSource(
                shot_id=shot.id,
                index=shot.index,
                duration_seconds=shot.duration_seconds,
                kind="card",
                path=card,
                fallback_reason=fallback_reason,
            )
        )
    return sources


def build_hybrid_preview_ffmpeg_command(
    *,
    sources: Sequence[HybridShotSource],
    resolution: str,
    fps: int,
    motion_cadence_fps: int | None = None,
    output_path: str | Path,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    if not sources:
        raise HybridPreviewError("Hybrid preview requires at least one shot.")
    width, height = _parse_resolution(resolution)
    if fps <= 0:
        raise HybridPreviewError("Hybrid preview FPS must be positive.")
    cadence_fps = motion_cadence_fps or fps
    if cadence_fps <= 0 or cadence_fps > fps:
        raise HybridPreviewError(
            "Hybrid preview motion cadence FPS must be between 1 and target FPS."
        )

    command = [ffmpeg_bin, "-y"]
    for source in sources:
        if source.duration_seconds <= 0:
            raise HybridPreviewError(
                f"Hybrid preview shot duration must be positive: {source.shot_id}"
            )
        if source.kind == "card":
            command.extend(["-loop", "1", "-framerate", str(fps)])
        elif source.kind != "video":
            raise HybridPreviewError(
                f"Unsupported hybrid preview source kind: {source.kind}"
            )
        command.extend(["-i", str(source.path)])

    filters: list[str] = []
    labels: list[str] = []
    for input_index, source in enumerate(sources):
        duration = f"{source.duration_seconds:.3f}"
        label = f"v{input_index}"
        input_label = _build_source_edit_filters(input_index, source, filters)
        filters.append(
            f"{input_label}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={cadence_fps},fps={fps},"
            f"tpad=stop_mode=clone:stop_duration={duration},"
            f"trim=duration={duration},setpts=PTS-STARTPTS[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        f"{''.join(labels)}concat=n={len(sources)}:v=1:a=0,format=yuv420p[vout]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def render_hybrid_preview_video(
    episode: Episode,
    *,
    package_path: str | Path,
    cards: Sequence[str | Path],
    output_path: str | Path,
    report_path: str | Path,
    command_runner: Callable[..., Any] = subprocess.run,
    media_validator: Callable[[Path], bool] | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    package_source = Path(package_path)
    try:
        package = json.loads(package_source.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HybridPreviewError(
            f"OpenMontage package not found: {package_source}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HybridPreviewError(
            f"Unable to read OpenMontage package: {package_source}"
        ) from exc
    if not isinstance(package, dict):
        raise HybridPreviewError("OpenMontage package must contain a JSON object.")

    package_project_id = str(package.get("project_id") or episode.project_id).strip()
    try:
        edit_decisions = load_adjacent_edit_decisions(
            package_source,
            expected_project_id=package_project_id,
            valid_shot_ids={shot.id for shot in episode.shots},
        )
    except EditDecisionError as exc:
        raise HybridPreviewError(str(exc)) from exc

    target = package.get("target")
    target = target if isinstance(target, dict) else {}
    resolution = str(target.get("resolution") or episode.target_resolution).strip()
    try:
        fps = int(target.get("fps") or 30)
    except (TypeError, ValueError) as exc:
        raise HybridPreviewError("OpenMontage target FPS must be an integer.") from exc
    try:
        motion_cadence_fps = int(target.get("motion_cadence_fps") or fps)
    except (TypeError, ValueError) as exc:
        raise HybridPreviewError(
            "OpenMontage motion cadence FPS must be an integer."
        ) from exc
    sources = select_hybrid_shot_sources(
        episode,
        package,
        cards,
        edit_decisions=edit_decisions,
    )
    output = Path(output_path)
    destination = Path(report_path)
    if output.resolve() == destination.resolve():
        raise HybridPreviewError(
            "Hybrid preview report path must differ from the video output path."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_media_path(output)
    command = build_hybrid_preview_ffmpeg_command(
        sources=sources,
        resolution=resolution,
        fps=fps,
        motion_cadence_fps=motion_cadence_fps,
        output_path=temporary_output,
        ffmpeg_bin=ffmpeg_bin,
    )
    try:
        command_runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        temporary_output.unlink(missing_ok=True)
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise HybridPreviewError(
            f"Hybrid preview FFmpeg render failed: {detail}"
        ) from exc
    except OSError as exc:
        temporary_output.unlink(missing_ok=True)
        raise HybridPreviewError(
            f"Unable to run hybrid preview FFmpeg: {exc}"
        ) from exc
    validator = media_validator or (
        lambda path: probe_media(path, required_stream="video").valid
    )
    if not validator(temporary_output):
        temporary_output.unlink(missing_ok=True)
        raise HybridPreviewError(
            f"Hybrid preview output is missing or has no valid video stream: {output}"
        )
    temporary_output.replace(output)

    dynamic_count = sum(source.kind == "video" for source in sources)
    edit_count = sum(
        bool(source.source_end_seconds is not None or source.drop_ranges_seconds)
        for source in sources
    )
    report = {
        "schema_version": "motion-comic-factory.hybrid-preview.v1",
        "success": True,
        "output_path": str(output),
        "output_size_bytes": output.stat().st_size,
        "resolution": resolution,
        "fps": fps,
        "motion_cadence_fps": motion_cadence_fps,
        "shot_count": len(sources),
        "dynamic_shot_count": dynamic_count,
        "fallback_shot_count": len(sources) - dynamic_count,
        "edit_decisions_path": (
            str(edit_decisions.source_path) if edit_decisions.source_path else ""
        ),
        "edit_decisions_applied": bool(edit_count),
        "edit_decision_count": edit_count,
        "shots": [source.to_report() for source in sources],
    }
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _parse_resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", value.strip())
    if not match:
        raise HybridPreviewError(
            "Hybrid preview resolution must use WIDTHxHEIGHT format."
        )
    return int(match.group(1)), int(match.group(2))


def _build_source_edit_filters(
    input_index: int,
    source: HybridShotSource,
    filters: list[str],
) -> str:
    if source.kind != "video" or (
        source.source_end_seconds is None and not source.drop_ranges_seconds
    ):
        return f"[{input_index}:v]"

    keep_ranges: list[tuple[float, float | None]] = []
    cursor = 0.0
    for start, end in source.drop_ranges_seconds:
        if start > cursor:
            keep_ranges.append((cursor, start))
        cursor = end
    if source.source_end_seconds is None or cursor < source.source_end_seconds:
        keep_ranges.append((cursor, source.source_end_seconds))
    if not keep_ranges:
        raise HybridPreviewError(
            f"Edit decisions remove the entire source for {source.shot_id}."
        )

    segment_labels: list[str] = []
    for segment_index, (start, end) in enumerate(keep_ranges):
        segment_label = f"ve{input_index}s{segment_index}"
        trim = f"trim=start={start:.3f}"
        if end is not None:
            trim += f":end={end:.3f}"
        filters.append(
            f"[{input_index}:v]{trim},setpts=PTS-STARTPTS[{segment_label}]"
        )
        segment_labels.append(f"[{segment_label}]")
    if len(segment_labels) == 1:
        return segment_labels[0]

    edited_label = f"ve{input_index}"
    filters.append(
        f"{''.join(segment_labels)}"
        f"concat=n={len(segment_labels)}:v=1:a=0[{edited_label}]"
    )
    return f"[{edited_label}]"
