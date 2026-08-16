from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from PIL import Image

from .gateway_image import (
    GatewayImageClient,
    GatewayImageConfig,
    is_valid_image_file,
)
from .gateway_video import (
    GatewayVideoClient,
    GatewayVideoConfig,
    is_valid_mp4_file,
)


SCHEMA_VERSION = "motion-comic-factory.source-locked-cat-replica.v1"
ANALYSIS_SCHEMA_VERSION = "motion-comic-factory.cat-replica-shot-analysis.v1"
STATE_SCHEMA_VERSION = "motion-comic-factory.cat-replica-state.v1"
FFMPEG = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")


class SourceLockedReplicaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivePicture:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ShotSpec:
    shot_id: str
    index: int
    start_s: float
    end_s: float
    start_frame: int
    end_frame: int

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / 30

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def provider_duration_s(self) -> int:
        return max(4, min(15, math.ceil(self.duration_s)))


@dataclass(frozen=True)
class VideoSegmentSpec:
    name: str
    duration_frames: int
    provider_duration_s: int
    anchor_label: str
    action: str


@dataclass(frozen=True)
class ReplicaProject:
    project_id: str
    title: str
    source_video: Path
    source_duration_s: float
    source_width: int
    source_height: int
    source_fps: int
    ending_fade_start_s: float
    ending_fade_duration_s: float
    active_picture: ActivePicture
    cat_name: str
    cat_identity: str
    cat_reference: Path
    image_model: str
    image_size: str
    video_model: str
    video_resolution: str
    video_ratio: str
    second_anchor_threshold_s: float
    motion_interpolation: bool
    fade_transition_frames: int
    fade_transition_after_shots: tuple[str, ...]
    output_root: Path
    shots: tuple[ShotSpec, ...]


def load_project(config_path: str | Path, output_root: str | Path) -> ReplicaProject:
    config_file = Path(config_path).expanduser().resolve()
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceLockedReplicaError(f"Unable to read replica config: {exc}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SourceLockedReplicaError("Unsupported source-locked replica config schema.")

    source = _mapping(payload.get("source"), "source")
    target = _mapping(payload.get("target"), "target")
    generation = _mapping(payload.get("generation"), "generation")
    active = _mapping(source.get("active_picture"), "source.active_picture")
    boundaries = payload.get("boundaries_s")
    if not isinstance(boundaries, list) or len(boundaries) < 2:
        raise SourceLockedReplicaError("Replica config requires at least two boundaries.")
    try:
        values = tuple(float(value) for value in boundaries)
    except (TypeError, ValueError) as exc:
        raise SourceLockedReplicaError("Replica boundaries must be numeric.") from exc
    if values[0] != 0 or any(right <= left for left, right in zip(values, values[1:])):
        raise SourceLockedReplicaError("Replica boundaries must be strictly increasing from zero.")

    fps = int(source.get("fps") or 0)
    if fps != 30:
        raise SourceLockedReplicaError("Source-locked replica currently requires 30 fps.")
    duration = float(source.get("duration_s") or 0)
    if abs(values[-1] - duration) > 1 / fps:
        raise SourceLockedReplicaError("Final boundary does not match source duration.")
    frames = tuple(round(value * fps) for value in values)
    shots = tuple(
        ShotSpec(
            shot_id=f"S{index:03d}",
            index=index,
            start_s=left_frame / fps,
            end_s=right_frame / fps,
            start_frame=left_frame,
            end_frame=right_frame,
        )
        for index, (left_frame, right_frame) in enumerate(
            zip(frames, frames[1:]), start=1
        )
    )
    if any(shot.frame_count <= 0 or shot.provider_duration_s > 15 for shot in shots):
        raise SourceLockedReplicaError("Replica contains an invalid provider shot duration.")

    project = ReplicaProject(
        project_id=str(payload.get("project_id") or "").strip(),
        title=str(payload.get("title") or "").strip(),
        source_video=Path(str(source.get("video_path") or "")).expanduser().resolve(),
        source_duration_s=duration,
        source_width=int(source.get("width") or 0),
        source_height=int(source.get("height") or 0),
        source_fps=fps,
        ending_fade_start_s=float(source.get("ending_fade_start_s") or 0),
        ending_fade_duration_s=float(source.get("ending_fade_duration_s") or 0),
        active_picture=ActivePicture(
            x=int(active.get("x") or 0),
            y=int(active.get("y") or 0),
            width=int(active.get("width") or 0),
            height=int(active.get("height") or 0),
        ),
        cat_name=str(target.get("name") or "").strip(),
        cat_identity=str(target.get("identity") or "").strip(),
        cat_reference=Path(str(target.get("reference_path") or "")).expanduser().resolve(),
        image_model=str(generation.get("image_model") or "").strip(),
        image_size=str(generation.get("image_size") or "").strip(),
        video_model=str(generation.get("video_model") or "").strip(),
        video_resolution=str(generation.get("video_resolution") or "").strip(),
        video_ratio=str(generation.get("video_ratio") or "").strip(),
        second_anchor_threshold_s=float(
            generation.get("long_shot_second_anchor_threshold_s") or 7
        ),
        motion_interpolation=generation.get("motion_interpolation") is True,
        fade_transition_frames=int(generation.get("fade_transition_frames") or 0),
        fade_transition_after_shots=tuple(
            str(value).strip()
            for value in generation.get("fade_transition_after_shots") or []
        ),
        output_root=Path(output_root).expanduser().resolve(),
        shots=shots,
    )
    _validate_project(project)
    return project


def prepare_evidence(project: ReplicaProject) -> Path:
    reference_root = project.output_root / "reference"
    shots_root = reference_root / "shots"
    audio_root = project.output_root / "audio" / "shots"
    shots_root.mkdir(parents=True, exist_ok=True)
    audio_root.mkdir(parents=True, exist_ok=True)
    crop = project.active_picture
    records: list[dict[str, Any]] = []
    for shot in project.shots:
        destination = shots_root / shot.shot_id
        destination.mkdir(parents=True, exist_ok=True)
        timestamps = {
            "start": shot.start_s + min(1 / 30, shot.duration_s / 4),
            "mid": (shot.start_s + shot.end_s) / 2,
            "end": shot.end_s - min(1 / 30, shot.duration_s / 4),
        }
        frame_paths: dict[str, str] = {}
        for label, timestamp in timestamps.items():
            output = destination / f"{label}.jpg"
            _run(
                [
                    str(FFMPEG),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    str(project.source_video),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y}",
                    "-q:v",
                    "2",
                    str(output),
                ],
                f"extract {shot.shot_id} {label}",
            )
            frame_paths[label] = str(output.relative_to(project.output_root))
        audio = audio_root / f"{shot.shot_id}.wav"
        _run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{shot.start_s:.6f}",
                "-t",
                f"{shot.duration_s:.6f}",
                "-i",
                str(project.source_video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            f"extract {shot.shot_id} audio",
        )
        records.append(
            {
                "shot_id": shot.shot_id,
                "index": shot.index,
                "start_s": shot.start_s,
                "end_s": shot.end_s,
                "duration_s": shot.duration_s,
                "frame_count": shot.frame_count,
                "provider_duration_s": shot.provider_duration_s,
                "frames": frame_paths,
                "audio": str(audio.relative_to(project.output_root)),
            }
        )
    manifest = reference_root / "shot_manifest.json"
    _write_json(
        manifest,
        {
            "schema_version": SCHEMA_VERSION,
            "project_id": project.project_id,
            "source_sha256": _sha256(project.source_video),
            "shots": records,
        },
    )
    return manifest


def write_analysis_template(project: ReplicaProject) -> Path:
    path = project.output_root / "reference" / "shot_analysis.template.json"
    _write_json(
        path,
        {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "shots": [
                {
                    "shot_id": shot.shot_id,
                    "scene": "",
                    "framing": "",
                    "action": "",
                    "props_and_contacts": "",
                    "visible_others": "",
                    "speech_mode": "unknown",
                    "cat_speech_windows": [],
                    "subtitle": "",
                    "reviewed": False,
                }
                for shot in project.shots
            ],
        },
    )
    return path


def _selected_shots(
    project: ReplicaProject,
    shot_ids: Sequence[str] | None,
) -> tuple[ShotSpec, ...]:
    if not shot_ids:
        return project.shots
    requested = {str(shot_id).strip() for shot_id in shot_ids}
    known = {shot.shot_id for shot in project.shots}
    unknown = sorted(requested - known)
    if unknown:
        raise SourceLockedReplicaError(f"Unknown shot ids: {', '.join(unknown)}")
    return tuple(shot for shot in project.shots if shot.shot_id in requested)


def generate_anchors(
    project: ReplicaProject,
    concurrency: int = 4,
    shot_ids: Sequence[str] | None = None,
) -> tuple[Path, ...]:
    analysis = _load_analysis(project)
    jobs: list[tuple[ShotSpec, str, Path, Mapping[str, Any]]] = []
    for shot in _selected_shots(project, shot_ids):
        labels = ["start"]
        if shot.duration_s >= project.second_anchor_threshold_s:
            labels.append("end")
        for label in labels:
            source = project.output_root / "reference" / "shots" / shot.shot_id / f"{label}.jpg"
            jobs.append((shot, label, source, analysis[shot.shot_id]))

    def worker(job: tuple[ShotSpec, str, Path, Mapping[str, Any]]) -> Path:
        shot, label, source, annotation = job
        output = project.output_root / "assets" / "anchors" / shot.shot_id / f"{label}.png"
        state = output.with_suffix(".state.json")
        copy_source = _anchor_copy_source(project, annotation, label)
        if copy_source is not None:
            if not is_valid_image_file(copy_source):
                raise SourceLockedReplicaError(
                    f"Anchor copy source is invalid: {shot.shot_id} {label}: {copy_source}"
                )
            signature = _signature(
                "copy-anchor",
                _sha256(copy_source),
                annotation,
                label,
            )
            if is_valid_image_file(output) and _state_matches(state, signature, output):
                return output
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
            temporary.unlink(missing_ok=True)
            with Image.open(copy_source) as opened:
                opened.convert("RGB").save(temporary, format="PNG")
            if not is_valid_image_file(temporary):
                raise SourceLockedReplicaError(
                    f"Copied anchor is invalid: {shot.shot_id} {label}"
                )
            os.replace(temporary, output)
            _write_state(
                state,
                signature,
                output,
                extra={"mode": "copy-anchor", "source": str(copy_source)},
            )
            return output
        reference_paths = [source, project.cat_reference]
        continuity_value = str(annotation.get("anchor_continuity_reference") or "").strip()
        if continuity_value:
            continuity_reference = (project.output_root / continuity_value).resolve()
            try:
                continuity_reference.relative_to(project.output_root)
            except ValueError as exc:
                raise SourceLockedReplicaError(
                    f"Anchor continuity reference escapes the project: {shot.shot_id}"
                ) from exc
            if not is_valid_image_file(continuity_reference):
                raise SourceLockedReplicaError(
                    f"Anchor continuity reference is invalid: {continuity_reference}"
                )
            reference_paths.append(continuity_reference)
        signature = _signature(
            project.image_model,
            project.image_size,
            *(_sha256(path) for path in reference_paths),
            annotation,
            label,
        )
        if is_valid_image_file(output) and _state_matches(state, signature, output):
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        if annotation.get("anchor_mode") == "preserve_source_silhouette":
            temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
            temporary.unlink(missing_ok=True)
            with Image.open(source) as opened:
                opened.convert("RGB").save(temporary, format="PNG")
            if not is_valid_image_file(temporary):
                raise SourceLockedReplicaError(
                    f"Invalid silhouette anchor: {shot.shot_id} {label}"
                )
            os.replace(temporary, output)
            _write_state(
                state,
                signature,
                output,
                extra={"mode": "preserve_source_silhouette"},
            )
            return output
        prompt = _anchor_prompt(project, shot, label, annotation)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        temporary.unlink(missing_ok=True)
        client = GatewayImageClient(
            GatewayImageConfig(
                api_key=_required_env("GATEWAY_API_KEY"),
                base_url=_gateway_base_url(),
                model=project.image_model,
                timeout_seconds=300,
                download_timeout_seconds=180,
            )
        )
        client.generate(
            prompt,
            temporary,
            size=project.image_size,
            n=1,
            ref_image_paths=reference_paths,
        )
        if not is_valid_image_file(temporary):
            raise SourceLockedReplicaError(f"Invalid generated anchor: {shot.shot_id} {label}")
        os.replace(temporary, output)
        _write_state(state, signature, output)
        return output

    return tuple(_parallel_map(worker, jobs, concurrency))


def _anchor_copy_source(
    project: ReplicaProject,
    annotation: Mapping[str, Any],
    label: str,
) -> Path | None:
    copy_map = annotation.get("anchor_label_copy_from") or {}
    if not isinstance(copy_map, Mapping):
        return None
    value = str(copy_map.get(label) or "").strip()
    if not value:
        return None
    source = (project.output_root / value).resolve()
    anchors_root = (project.output_root / "assets" / "anchors").resolve()
    try:
        source.relative_to(anchors_root)
    except ValueError as exc:
        raise SourceLockedReplicaError(
            f"Anchor copy source escapes the anchors directory: {value}"
        ) from exc
    return source


def generate_videos(
    project: ReplicaProject,
    concurrency: int = 3,
    shot_ids: Sequence[str] | None = None,
) -> tuple[Path, ...]:
    analysis = _load_analysis(project)

    def worker(shot: ShotSpec) -> Path:
        annotation = analysis[shot.shot_id]
        segments = _video_segments(shot, annotation)
        if segments:
            return _generate_segmented_video(project, shot, annotation, segments)
        anchor_root = project.output_root / "assets" / "anchors" / shot.shot_id
        end_anchor = anchor_root / "end.png"
        anchor_labels = _video_anchor_labels(annotation, end_anchor.is_file())
        anchors = [anchor_root / f"{label}.png" for label in anchor_labels]
        anchors.append(project.cat_reference)
        for path in anchors:
            if not is_valid_image_file(path):
                raise SourceLockedReplicaError(f"Missing anchor for {shot.shot_id}: {path}")
        speech_mode = str(annotation.get("speech_mode") or "none")
        source_audio = _generation_audio_path(project, shot, annotation)
        use_audio = speech_mode in {"cat_visible", "mixed"}
        prompt = _video_prompt(project, shot, annotation, "end" in anchor_labels)
        output = project.output_root / "shots" / shot.shot_id / "selected.mp4"
        state = output.with_suffix(".state.json")
        signature = _signature(
            project.video_model,
            project.video_resolution,
            project.video_ratio,
            shot.provider_duration_s,
            *( _sha256(path) for path in anchors ),
            _sha256(source_audio) if use_audio else "no-audio",
            annotation,
        )
        if is_valid_mp4_file(output) and _state_matches(state, signature, output):
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        temporary.unlink(missing_ok=True)
        client = GatewayVideoClient(
            GatewayVideoConfig(
                api_key=_required_env("GATEWAY_API_KEY"),
                base_url=_gateway_base_url(),
                model=project.video_model,
                timeout_seconds=120,
                submit_timeout_seconds=300,
                download_timeout_seconds=240,
                poll_interval_seconds=5,
                max_wait_seconds=1200,
            )
        )
        result = client.generate(
            prompt,
            temporary,
            images=anchors,
            audio=source_audio if use_audio else None,
            duration=shot.provider_duration_s,
            ratio=project.video_ratio,
            resolution=project.video_resolution,
            generate_audio=use_audio,
            allow_network=True,
        )
        if not is_valid_mp4_file(temporary):
            raise SourceLockedReplicaError(f"Invalid generated video: {shot.shot_id}")
        os.replace(temporary, output)
        _write_state(state, signature, output, extra=result.to_report())
        return output

    return tuple(_parallel_map(worker, _selected_shots(project, shot_ids), concurrency))


def _video_anchor_labels(
    annotation: Mapping[str, Any],
    end_anchor_available: bool,
) -> tuple[str, ...]:
    configured = annotation.get("video_anchor_labels")
    if configured is None:
        return ("start", "end") if end_anchor_available else ("start",)
    if not isinstance(configured, list) or not configured:
        raise SourceLockedReplicaError("video_anchor_labels must be a non-empty list.")
    labels = tuple(str(value).strip() for value in configured)
    if len(set(labels)) != len(labels) or any(label not in {"start", "end"} for label in labels):
        raise SourceLockedReplicaError("video_anchor_labels may contain start/end exactly once.")
    if "end" in labels and not end_anchor_available:
        raise SourceLockedReplicaError("Configured end video anchor is unavailable.")
    return labels


def _generation_audio_path(
    project: ReplicaProject,
    shot: ShotSpec,
    annotation: Mapping[str, Any],
) -> Path:
    configured = str(annotation.get("generation_audio_path") or "").strip()
    if not configured:
        return project.output_root / "audio" / "shots" / f"{shot.shot_id}.wav"
    path = (project.output_root / configured).resolve()
    try:
        path.relative_to(project.output_root)
    except ValueError as exc:
        raise SourceLockedReplicaError(
            f"Generation audio escapes the project: {shot.shot_id}"
        ) from exc
    if not path.is_file() or path.is_symlink():
        raise SourceLockedReplicaError(
            f"Generation audio is unavailable: {shot.shot_id}: {path}"
        )
    return path


def _video_segments(
    shot: ShotSpec,
    annotation: Mapping[str, Any],
) -> tuple[VideoSegmentSpec, ...]:
    configured = annotation.get("video_segments")
    if configured is None:
        return ()
    if not isinstance(configured, list) or len(configured) < 2:
        raise SourceLockedReplicaError(
            f"Segmented shot {shot.shot_id} requires at least two video segments."
        )
    segments: list[VideoSegmentSpec] = []
    for item in configured:
        if not isinstance(item, Mapping):
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} contains a non-object segment."
            )
        name = str(item.get("name") or "").strip()
        action = str(item.get("action") or "").strip()
        anchor_label = str(item.get("anchor_label") or "").strip()
        try:
            duration_frames = int(item.get("duration_frames") or 0)
            provider_duration_s = int(item.get("provider_duration_s") or 0)
        except (TypeError, ValueError) as exc:
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} has non-numeric duration fields."
            ) from exc
        if not name or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} has an unsafe segment name."
            )
        if not action or anchor_label not in {"start", "end"}:
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} has an invalid action or anchor label."
            )
        if duration_frames <= 0 or not 4 <= provider_duration_s <= 15:
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} has an invalid duration."
            )
        if provider_duration_s * 30 < duration_frames:
            raise SourceLockedReplicaError(
                f"Segmented shot {shot.shot_id} provider duration is too short."
            )
        segments.append(
            VideoSegmentSpec(
                name=name,
                duration_frames=duration_frames,
                provider_duration_s=provider_duration_s,
                anchor_label=anchor_label,
                action=action,
            )
        )
    if len({segment.name for segment in segments}) != len(segments):
        raise SourceLockedReplicaError(
            f"Segmented shot {shot.shot_id} contains duplicate segment names."
        )
    if sum(segment.duration_frames for segment in segments) != shot.frame_count:
        raise SourceLockedReplicaError(
            f"Segmented shot {shot.shot_id} does not cover its exact editorial frames."
        )
    if str(annotation.get("speech_mode") or "none") in {"cat_visible", "mixed"}:
        raise SourceLockedReplicaError(
            f"Segmented talking shot {shot.shot_id} requires explicit audio slicing."
        )
    return tuple(segments)


def _generate_segmented_video(
    project: ReplicaProject,
    shot: ShotSpec,
    annotation: Mapping[str, Any],
    segments: Sequence[VideoSegmentSpec],
) -> Path:
    shot_root = project.output_root / "shots" / shot.shot_id
    segment_root = shot_root / "segments"
    anchor_root = project.output_root / "assets" / "anchors" / shot.shot_id
    output = shot_root / "selected.mp4"
    state = output.with_suffix(".state.json")
    anchors = [anchor_root / f"{segment.anchor_label}.png" for segment in segments]
    for path in (*anchors, project.cat_reference):
        if not is_valid_image_file(path):
            raise SourceLockedReplicaError(f"Missing segmented anchor for {shot.shot_id}: {path}")
    signature = _signature(
        "segmented-video-v1",
        project.video_model,
        project.video_resolution,
        project.video_ratio,
        *(_sha256(path) for path in anchors),
        _sha256(project.cat_reference),
        annotation,
    )
    if is_valid_mp4_file(output) and _state_matches(state, signature, output):
        return output

    segment_root.mkdir(parents=True, exist_ok=True)
    client = GatewayVideoClient(
        GatewayVideoConfig(
            api_key=_required_env("GATEWAY_API_KEY"),
            base_url=_gateway_base_url(),
            model=project.video_model,
            timeout_seconds=120,
            submit_timeout_seconds=300,
            download_timeout_seconds=240,
            poll_interval_seconds=5,
            max_wait_seconds=1200,
        )
    )
    normalized: list[Path] = []
    reports: dict[str, Any] = {}
    for segment, anchor in zip(segments, anchors, strict=True):
        raw = segment_root / f"{segment.name}.source.mp4"
        raw_state = raw.with_suffix(".state.json")
        segment_annotation = dict(annotation)
        segment_annotation["action"] = segment.action
        prompt = _video_prompt(
            project,
            shot,
            segment_annotation,
            False,
            provider_duration_s=segment.provider_duration_s,
        )
        raw_signature = _signature(
            "segmented-video-source-v1",
            project.video_model,
            project.video_resolution,
            project.video_ratio,
            {
                "name": segment.name,
                "duration_frames": segment.duration_frames,
                "provider_duration_s": segment.provider_duration_s,
                "anchor_label": segment.anchor_label,
                "action": segment.action,
            },
            _sha256(anchor),
            _sha256(project.cat_reference),
            segment_annotation,
        )
        if not (is_valid_mp4_file(raw) and _state_matches(raw_state, raw_signature, raw)):
            temporary_raw = raw.with_name(f".{raw.stem}.tmp{raw.suffix}")
            temporary_raw.unlink(missing_ok=True)
            result = client.generate(
                prompt,
                temporary_raw,
                images=[anchor, project.cat_reference],
                audio=None,
                duration=segment.provider_duration_s,
                ratio=project.video_ratio,
                resolution=project.video_resolution,
                generate_audio=False,
                allow_network=True,
            )
            if not is_valid_mp4_file(temporary_raw):
                raise SourceLockedReplicaError(
                    f"Invalid generated segment: {shot.shot_id}/{segment.name}"
                )
            os.replace(temporary_raw, raw)
            reports[segment.name] = result.to_report()
            _write_state(raw_state, raw_signature, raw, extra=result.to_report())
        normalized_segment = segment_root / f"{segment.name}.mp4"
        _run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(raw),
                "-an",
                "-vf",
                (
                    "scale=1280:720:force_original_aspect_ratio=increase:flags=lanczos,"
                    "crop=1280:720,fps=30,format=yuv420p,setpts=N/(30*TB)"
                ),
                "-frames:v",
                str(segment.duration_frames),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-movflags",
                "+faststart",
                str(normalized_segment),
            ],
            f"normalize {shot.shot_id}/{segment.name}",
        )
        normalized.append(normalized_segment)

    concat_list = segment_root / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in normalized),
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    temporary.unlink(missing_ok=True)
    _run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(temporary),
        ],
        f"concatenate segmented shot {shot.shot_id}",
    )
    if not is_valid_mp4_file(temporary):
        raise SourceLockedReplicaError(f"Invalid segmented video: {shot.shot_id}")
    os.replace(temporary, output)
    _write_state(state, signature, output, extra={"segments": reports})
    return output


def _video_frame_rate(path: Path) -> float:
    result = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SourceLockedReplicaError(f"Unable to inspect video frame rate: {path}")
    value = result.stdout.strip()
    try:
        numerator, denominator = value.split("/", 1)
        fps = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise SourceLockedReplicaError(f"Invalid video frame rate for {path}: {value}") from exc
    if fps <= 0:
        raise SourceLockedReplicaError(f"Invalid video frame rate for {path}: {value}")
    return fps


def _normalization_filter(
    *,
    input_fps: float,
    output_fps: int,
    frame_count: int,
    motion_interpolation: bool,
    fade_in: bool,
    fade_out: bool,
    transition_frames: int,
) -> str:
    filters = ["scale=1072:603:flags=lanczos", "crop=1072:592:0:5"]
    if motion_interpolation and input_fps < output_fps - 0.01:
        filters.append(
            f"minterpolate=fps={output_fps}:mi_mode=mci:mc_mode=aobmc:"
            "me_mode=bidir:vsbmc=1"
        )
    else:
        filters.append(f"fps={output_fps}")
    transition_duration = transition_frames / output_fps
    if fade_in and transition_frames:
        filters.append(
            f"fade=t=in:st=0:d={transition_duration:.6f}:color=black"
        )
    if fade_out and transition_frames:
        fade_start = max(0.0, (frame_count - transition_frames) / output_fps)
        filters.append(
            f"fade=t=out:st={fade_start:.6f}:d={transition_duration:.6f}:color=black"
        )
    filters.extend(("format=yuv420p", f"setpts=N/({output_fps}*TB)"))
    return ",".join(filters)


def compose(project: ReplicaProject, subtitle_path: str | Path | None = None) -> Path:
    work = project.output_root / "work" / "compose"
    normalized_root = work / "normalized"
    delivery_root = project.output_root / "deliveries"
    normalized_root.mkdir(parents=True, exist_ok=True)
    delivery_root.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    transition_after = set(project.fade_transition_after_shots)
    for position, shot in enumerate(project.shots):
        source = project.output_root / "shots" / shot.shot_id / "selected.mp4"
        if not is_valid_mp4_file(source):
            raise SourceLockedReplicaError(f"Missing selected video: {shot.shot_id}")
        output = normalized_root / f"{shot.shot_id}.mp4"
        fade_in = position > 0 and project.shots[position - 1].shot_id in transition_after
        fade_out = shot.shot_id in transition_after
        video_filter = _normalization_filter(
            input_fps=_video_frame_rate(source),
            output_fps=project.source_fps,
            frame_count=shot.frame_count,
            motion_interpolation=project.motion_interpolation,
            fade_in=fade_in,
            fade_out=fade_out,
            transition_frames=project.fade_transition_frames,
        )
        _run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-an",
                "-vf",
                video_filter,
                "-frames:v",
                str(shot.frame_count),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-movflags",
                "+faststart",
                str(output),
            ],
            f"normalize {shot.shot_id}",
        )
        normalized.append(output)
    concat_list = work / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in normalized),
        encoding="utf-8",
    )
    active_picture = work / "active_picture.mp4"
    _run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(active_picture),
        ],
        "concatenate replica shots",
    )
    video_filters: list[str] = []
    if project.ending_fade_duration_s > 0:
        video_filters.append(
            "fade=t=out:"
            f"st={project.ending_fade_start_s:.6f}:"
            f"d={project.ending_fade_duration_s:.6f}:color=black"
        )
    if subtitle_path is not None:
        subtitle = Path(subtitle_path).expanduser().resolve()
        if not subtitle.is_file():
            raise SourceLockedReplicaError(f"Subtitle file not found: {subtitle}")
        escaped = str(subtitle).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        video_filters.append(
            "subtitles='"
            + escaped
            + "':force_style='FontName=Heiti SC,FontSize=18,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=26'"
        )
    video_filters.append("pad=1076:1920:2:664:black")
    video_filter = ",".join(video_filters)
    final = delivery_root / "final_master.mp4"
    _run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(active_picture),
            "-i",
            str(project.source_video),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            video_filter,
            "-frames:v",
            str(round(project.source_duration_s * project.source_fps)),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(final),
        ],
        "compose final master",
    )
    _write_delivery_tables(project, final)
    return final


def _anchor_prompt(
    project: ReplicaProject,
    shot: ShotSpec,
    label: str,
    annotation: Mapping[str, Any],
) -> str:
    continuity = (
        "Reference image 3 is the immutable clothing continuity master; reproduce its garment cut, trim, patch count and absence of hood or sleeves exactly."
        if annotation.get("anchor_continuity_reference")
        else ""
    )
    extra = str(annotation.get("extra_anchor_constraints") or "").strip()
    label_constraints = annotation.get("anchor_label_constraints") or {}
    label_extra = (
        str(label_constraints.get(label) or "").strip()
        if isinstance(label_constraints, Mapping)
        else ""
    )
    preserve_source_ui_text = annotation.get("preserve_source_ui_text") is True
    ui_text_instruction = (
        "Preserve source in-device UI text exactly, including its wording, position, color and screen perspective; it is photographed content on the device, not an overlay."
        if preserve_source_ui_text
        else ""
    )
    negative_constraints = (
        "No orange, ginger, cream or tabby fur on the main cat; no extra limb, duplicate cat, floating paw, subtitle, caption, logo, watermark or border. Remove all source captions and reconstruct the covered background naturally."
        if preserve_source_ui_text
        else "No orange, ginger, cream or tabby fur on the main cat; no extra limb, duplicate cat, floating paw, text, subtitle, caption, logo, watermark, border or platform UI. Remove all source captions and reconstruct the covered background naturally."
    )
    return " ".join(
        (
            "Reference image 1 is the exact horizontal shot composition; reference image 2 is the only character identity.",
            continuity,
            f"Replace the orange-and-white source cat completely with the same {project.cat_name}: {project.cat_identity}.",
            f"Shot {shot.shot_id} {label} frame. Scene: {annotation['scene']}. Framing: {annotation['framing']}.",
            f"The action description is context only: {annotation['action']}. Render only the single instant visibly shown in reference image 1; never combine start, middle and end poses in one still. Props and contacts: {annotation['props_and_contacts']}.",
            f"Preserve every non-cat element, lens, perspective, camera height, light direction, prop position, other person or animal, and physical contact from reference image 1. Visible others: {annotation['visible_others']}.",
            "Only the cat identity and coat may change. Keep exact pose and gaze. Exactly one main cat.",
            ui_text_instruction,
            extra,
            label_extra,
            "Photorealistic live-action frame, natural anatomy and contact shadows, 16:9.",
            negative_constraints,
        )
    )


def _video_prompt(
    project: ReplicaProject,
    shot: ShotSpec,
    annotation: Mapping[str, Any],
    has_end_anchor: bool,
    *,
    provider_duration_s: int | None = None,
) -> str:
    speech_mode = str(annotation.get("speech_mode") or "none")
    windows = annotation.get("cat_speech_windows") or []
    if speech_mode == "cat_visible":
        mouth = (
            "Reference audio 1 is the visible cat's speech. Animate only the lower jaw with small natural syllabic openings during audible speech; close the mouth within 0.20s after speech and at the final frame."
        )
    elif speech_mode == "mixed":
        mouth = (
            f"Reference audio 1 contains an off-screen interviewer and the visible cat. Keep the cat mouth closed except in these cat-answer windows measured from this shot start: {windows}. During those windows use small natural lower-jaw syllabic motion; close between speakers and at the final frame."
        )
    elif speech_mode == "offscreen_only":
        mouth = "All speech is off-screen. Keep every visible mouth naturally closed for the entire shot."
    else:
        mouth = "No visible character speaks. Keep every visible mouth naturally closed."
    anchor_contract = (
        "Reference image 1 is the exact start state; reference image 2 is the exact end state; the final identity sheet is immutable."
        if has_end_anchor
        else "Reference image 1 is the exact start state; the final identity sheet is immutable."
    )
    extra = str(annotation.get("extra_video_constraints") or "").strip()
    return " ".join(
        (
            f"Create one {provider_duration_s or shot.provider_duration_s}-second horizontal 16:9 photorealistic live-action pet-drama shot at natural 1x physical speed.",
            anchor_contract,
            f"The only main cat is {project.cat_name}: {project.cat_identity}; never drift to orange, ginger, cream or tabby fur.",
            f"Source-locked shot {shot.shot_id}, editorial duration {shot.duration_s:.3f}s. Scene: {annotation['scene']}. Camera and framing: {annotation['framing']}.",
            f"Perform this exact start-to-middle-to-end action without inventing an entrance or extra beat: {annotation['action']}.",
            f"Maintain these prop and body contacts continuously: {annotation['props_and_contacts']}. Preserve visible others: {annotation['visible_others']}.",
            mouth,
            extra,
            "Keep paws and feet grounded, held objects continuously supported, object counts fixed, background geometry stable, and cuts out of this single shot. Subtle handheld micro-motion only when present in the reference; no floating camera or hyper-smooth push-in.",
            "No subtitle, text, logo, watermark, platform UI, extra person, extra animal, duplicate subject, extra limb, anatomy mutation, object teleportation, morphing prop, floating paw, human lips, visible human teeth or protruding tongue.",
        )
    )


def _load_analysis(project: ReplicaProject) -> dict[str, Mapping[str, Any]]:
    path = project.output_root / "reference" / "shot_analysis.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceLockedReplicaError(f"Reviewed shot analysis is unavailable: {exc}") from exc
    if payload.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise SourceLockedReplicaError("Unsupported shot analysis schema.")
    items = payload.get("shots")
    if not isinstance(items, list) or len(items) != len(project.shots):
        raise SourceLockedReplicaError("Shot analysis must cover every source shot.")
    by_id: dict[str, Mapping[str, Any]] = {}
    for shot, item in zip(project.shots, items, strict=True):
        if not isinstance(item, dict) or item.get("shot_id") != shot.shot_id:
            raise SourceLockedReplicaError("Shot analysis order does not match the timeline.")
        if item.get("reviewed") is not True:
            raise SourceLockedReplicaError(f"Shot analysis requires review: {shot.shot_id}")
        for key in ("scene", "framing", "action", "props_and_contacts", "visible_others"):
            if not str(item.get(key) or "").strip():
                raise SourceLockedReplicaError(f"Shot analysis field is empty: {shot.shot_id}.{key}")
        if item.get("speech_mode") not in {"none", "cat_visible", "mixed", "offscreen_only"}:
            raise SourceLockedReplicaError(f"Invalid speech mode: {shot.shot_id}")
        by_id[shot.shot_id] = item
    return by_id


def _write_delivery_tables(project: ReplicaProject, final: Path) -> None:
    delivery = project.output_root / "deliveries"
    with (delivery / "cut_plan.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("shot_id", "start_s", "end_s", "duration_s", "selected_video"))
        for shot in project.shots:
            writer.writerow(
                (
                    shot.shot_id,
                    f"{shot.start_s:.6f}",
                    f"{shot.end_s:.6f}",
                    f"{shot.duration_s:.6f}",
                    f"shots/{shot.shot_id}/selected.mp4",
                )
            )
    with (delivery / "source_routing.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("stream", "source", "usage"))
        writer.writerow(("video", "gateway-generated shots", "cat-replaced picture"))
        writer.writerow(("audio", str(project.source_video), "local evaluation only"))
    (delivery / "quality_report.md").write_text(
        "\n".join(
            (
                "# Quality Report",
                "",
                f"- Candidate: `{final.name}`",
                f"- Expected duration: {project.source_duration_s:.6f} s",
                f"- Timeline: {len(project.shots)} source-locked shots at 30 fps",
                "- Picture: AI-regenerated; main cat replaced with the approved Doubao identity.",
                "- Audio: original reference audio retained without re-encoding; local evaluation only.",
                "- Delivery Eval: pending semantic review and sealing.",
                "",
            )
        ),
        encoding="utf-8",
    )


def _parallel_map(function: Any, jobs: Iterable[Any], concurrency: int) -> list[Path]:
    if concurrency < 1:
        raise SourceLockedReplicaError("Concurrency must be positive.")
    ordered = list(jobs)
    results: dict[int, Path] = {}
    failures: list[tuple[int, Exception]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(function, job): index for index, job in enumerate(ordered)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # Preserve every independent completed job.
                failures.append((index, exc))
                print(
                    f"failed {index + 1}/{len(ordered)}: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            print(
                f"completed {index + 1}/{len(ordered)}: {results[index]}",
                flush=True,
            )
    if failures:
        summary = "; ".join(
            f"job {index + 1}: {type(exc).__name__}: {exc}"
            for index, exc in failures
        )
        raise SourceLockedReplicaError(
            f"{len(failures)} of {len(ordered)} replica jobs failed: {summary}"
        )
    return [results[index] for index in range(len(ordered))]


def _validate_project(project: ReplicaProject) -> None:
    if not project.project_id or not project.title:
        raise SourceLockedReplicaError("Replica project identity is incomplete.")
    for label, path in (("source video", project.source_video), ("cat reference", project.cat_reference)):
        if not path.is_file() or path.is_symlink():
            raise SourceLockedReplicaError(f"Replica {label} is missing: {path}")
    if (project.source_width, project.source_height) != (1076, 1920):
        raise SourceLockedReplicaError("Unexpected source canvas size.")
    active = project.active_picture
    if active.width <= 0 or active.height <= 0:
        raise SourceLockedReplicaError("Active picture dimensions are invalid.")
    if active.x + active.width > project.source_width or active.y + active.height > project.source_height:
        raise SourceLockedReplicaError("Active picture exceeds the source canvas.")
    fade_start = project.ending_fade_start_s
    fade_duration = project.ending_fade_duration_s
    if (fade_start > 0) != (fade_duration > 0):
        raise SourceLockedReplicaError("Ending fade requires both start and duration.")
    if fade_start + fade_duration > project.source_duration_s + 1 / project.source_fps:
        raise SourceLockedReplicaError("Ending fade exceeds the source duration.")
    if not project.image_model.startswith("doubao-seedream-"):
        raise SourceLockedReplicaError("Replica anchors require a Seedream reference-image model.")
    if "seedance-2-0" not in project.video_model:
        raise SourceLockedReplicaError("Replica videos require Seedance 2.0.")
    if not 0 <= project.fade_transition_frames <= 6:
        raise SourceLockedReplicaError("Fade transition frames must be between 0 and 6.")
    known_shots = {shot.shot_id for shot in project.shots[:-1]}
    unknown_transitions = sorted(set(project.fade_transition_after_shots) - known_shots)
    if unknown_transitions:
        raise SourceLockedReplicaError(
            "Fade transitions reference unknown or final shots: "
            + ", ".join(unknown_transitions)
        )
    if project.fade_transition_after_shots and project.fade_transition_frames == 0:
        raise SourceLockedReplicaError("Fade transition shots require a positive frame count.")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SourceLockedReplicaError(f"Replica config field must be an object: {label}")
    return value


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SourceLockedReplicaError(f"Required environment variable is missing: {name}")
    return value


def _gateway_base_url() -> str:
    value = (
        os.environ.get("OPENAI_BASE_URL", "").strip()
        or os.environ.get("GATEWAY_BASE_URL", "").strip()
    )
    if not value:
        raise SourceLockedReplicaError(
            "GATEWAY_BASE_URL is required for live generation."
        )
    return value.rstrip("/")


def _run(command: Sequence[str], label: str) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise SourceLockedReplicaError(f"{label} failed: {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signature(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_matches(path: Path, signature: str, output: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("schema_version") == STATE_SCHEMA_VERSION
        and payload.get("signature") == signature
        and payload.get("output_sha256") == _sha256(output)
    )


def _write_state(
    path: Path,
    signature: str,
    output: Path,
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "signature": signature,
        "output_sha256": _sha256(output),
    }
    if extra:
        payload["provider"] = dict(extra)
    _write_json(path, payload)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Source-locked single-cat video replica")
    parser.add_argument("command", choices=("prepare", "anchors", "videos", "compose"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--shots", nargs="+")
    parser.add_argument("--subtitles")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = load_project(args.config, args.output)
    if args.command == "prepare":
        print(prepare_evidence(project))
        print(write_analysis_template(project))
    elif args.command == "anchors":
        generate_anchors(project, concurrency=args.concurrency, shot_ids=args.shots)
    elif args.command == "videos":
        generate_videos(project, concurrency=args.concurrency, shot_ids=args.shots)
    else:
        print(compose(project, subtitle_path=args.subtitles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
