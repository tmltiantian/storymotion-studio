from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from .pet_replica import PetReplicaPlan, validate_pet_replica_plan
from .pet_replica_audio import validate_replica_audio_manifest
from .pet_replica_reference import (
    PetReplicaReferenceError,
    ReplicaCaptionPlacement,
    ReplicaCaptionSafeRegion,
    ReplicaOCREvidenceBinding,
    ReplicaOCREvent,
    load_reviewed_caption_safe_region,
    load_reviewed_shot_annotations,
)
from .pet_replica_review import PetReplicaReviewError, validate_replica_selection


COMPOSITION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-composition.v2"
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
_REQUIRED_FILTERS = frozenset({"ass", "drawtext"})
_COMMAND_TIMEOUT_S = 300
_PROBE_TIMEOUT_S = 30
_BLACK_PIXEL_THRESHOLD = 0.10
_BLACK_PICTURE_RATIO = 0.98
_SOURCE_COPY_DELTA_MAX = 0.015
_CAPTION_DIFF_YAVG_MIN = 0.02
_CAPTION_DIFF_YMAX_MIN = 8.0
_SAFE_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CJK_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/STHeiti Medium.ttc"), "Heiti SC"),
    (Path("/System/Library/Fonts/STHeiti Light.ttc"), "Heiti SC"),
)
_FORBIDDEN_FILTERS = (
    "xfade",
    "tpad",
    "minterpolate",
    "optical",
    "blend",
    "framerate",
)
_BRANDING_KEYS = frozenset(
    {
        "platform_watermark",
        "watermark",
        "account_identity",
        "author_identity",
        "username",
        "avatar",
        "creator_label",
        "source_end_card",
        "end_card",
    }
)
_EXCLUDED_OCR_CLASSIFICATIONS = frozenset(
    {
        "platform_watermark",
        "account_identity",
        "author_identity",
        "avatar",
        "creator_label",
        "decorative_caption",
        "source_end_card",
    }
)
_BRANDING_TEXT = re.compile(
    (
        r"(?:@\S+|原作者|作者|账号|帐号|用户名|用户\s*ID|抖音号|小红书号|UP主|"
        r"抖音|小红书|快手|bilibili|watermark|creator|account|username|"
        r"platform[_\s-]*id)"
    ),
    re.I,
)
_DEFAULT_CAPTION_SAFE_REGION = ReplicaCaptionSafeRegion(
    x=36,
    y=880,
    width=648,
    height=320,
)


class PetReplicaCompositionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path
    filters: frozenset[str]


@dataclass(frozen=True)
class ReplicaCompositionShot:
    shot_id: str
    source_path: Path
    source_sha256: str
    source_start_s: float
    source_end_s: float
    editorial_duration_s: float
    timeline_start_s: float
    timeline_end_s: float
    normalized_path: Path


@dataclass(frozen=True)
class ReplicaSubtitle:
    event_id: str
    shot_id: str
    start_frame: int
    end_frame: int
    text: str
    placement: ReplicaCaptionPlacement
    start_s: float = field(init=False)
    end_s: float = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.start_frame, bool)
            or isinstance(self.end_frame, bool)
            or not isinstance(self.start_frame, int)
            or not isinstance(self.end_frame, int)
            or self.start_frame < 0
            or self.end_frame <= self.start_frame
        ):
            raise ValueError("Replica subtitle frame window is invalid.")
        object.__setattr__(self, "start_s", self.start_frame / 30)
        object.__setattr__(self, "end_s", self.end_frame / 30)


@dataclass(frozen=True)
class ReplicaCompositionManifest:
    schema_version: str
    project_id: str
    mode: str
    output_root: Path
    manifest_path: Path
    start_s: float
    end_s: float
    duration_s: float
    shots: tuple[ReplicaCompositionShot, ...]
    ocr_evidence_bindings: tuple[ReplicaOCREvidenceBinding, ...]
    reviewed_ocr_events: tuple[ReplicaOCREvent, ...]
    subtitles: tuple[ReplicaSubtitle, ...]
    caption_safe_region: ReplicaCaptionSafeRegion
    reviewed_annotations_sha256: str
    reviewed_annotations_snapshot_path: Path
    ocr_evidence_snapshot_paths: tuple[Path, ...]
    subtitle_font_path: Path
    subtitle_font_family: str
    subtitle_font_sha256: str
    source_audio_path: Path
    source_audio_sha256: str
    presentation_source_path: Path
    presentation_source_sha256: str
    subtitle_path: Path
    concat_list_path: Path
    picture_path: Path
    clean_master_path: Path
    captioned_master_path: Path
    master_path: Path
    master_alias: str
    side_by_side_path: Path
    final_qc_path: Path
    current_pointer_path: Path
    audio_mode: str
    comparison_duration_s: float
    comparison_tail_policy: str
    clean_master_command: tuple[str, ...]
    captioned_master_command: tuple[str, ...]
    ffmpeg_command: tuple[str, ...]
    side_by_side_command: tuple[str, ...]


def build_replica_composition(
    plan: PetReplicaPlan,
    selection: Mapping[str, Any] | Sequence[Any] | None,
    mode: str,
    *,
    validate_inputs: bool = True,
) -> ReplicaCompositionManifest:
    """Build a source-aligned composition without rendering or making network calls."""
    validate_pet_replica_plan(plan)
    _require_regular_no_symlink(plan.source_video, "canonical source")
    start_s, end_s = replica_composition_window(plan, mode)
    root = _output_root(plan)
    if validate_inputs:
        try:
            validate_replica_selection(plan, pilot_only=(mode == "pilot"))
            audio = validate_replica_audio_manifest(
                plan, root / "audio" / "audio_manifest.json"
            )
        except (PetReplicaReviewError, ValueError, OSError, RuntimeError) as exc:
            raise PetReplicaCompositionError(f"Current replica inputs are invalid: {exc}") from exc
        audio_path = audio.full_source.path
        audio_sha256 = audio.full_source.sha256
    else:
        audio_path = root / "audio" / "source_audio.aac"
        audio_sha256 = _require_file(root, audio_path, "source audio")

    selected = _selection_paths(plan, selection, required_window=(start_s, end_s))
    if validate_inputs and selection is not None:
        required = {
            shot.shot_id
            for shot in plan.shots
            if replica_shot_overlaps_window(plan, shot, start_s, end_s)
        }
        approved = _disk_selection_paths(plan, required)
        if selected != approved:
            raise PetReplicaCompositionError(
                "Explicit composition selection does not match the current approved selection."
            )
    composition_shots: list[ReplicaCompositionShot] = []
    for shot in plan.shots:
        shot_start, shot_end = _frame_aligned_window(plan, shot)
        editorial_start = max(start_s, shot_start)
        editorial_end = min(end_s, shot_end)
        if editorial_end <= editorial_start:
            continue
        source_path = selected.get(shot.shot_id)
        if source_path is None:
            raise PetReplicaCompositionError(
                f"Current selection is incomplete for {mode}: {shot.shot_id}."
            )
        source_sha256 = _require_file(root, source_path, f"selected clip {shot.shot_id}")
        duration = editorial_end - editorial_start
        composition_shots.append(
            ReplicaCompositionShot(
                shot_id=shot.shot_id,
                source_path=source_path,
                source_sha256=source_sha256,
                source_start_s=editorial_start - shot_start,
                source_end_s=editorial_end - shot_start,
                editorial_duration_s=duration,
                timeline_start_s=editorial_start - start_s,
                timeline_end_s=editorial_end - start_s,
                normalized_path=root / "work" / mode / "normalized" / f"{shot.shot_id}.mp4",
            )
        )
    if not composition_shots:
        raise PetReplicaCompositionError("Composition selection has no source-aligned shots.")

    _validate_shot_timeline(plan, composition_shots, start_s, end_s)
    annotations_path = root / "reference" / "shot_annotations.json"
    reviewed_annotations_sha256 = ""
    if annotations_path.exists():
        _require_regular_no_symlink(annotations_path, "reviewed annotations")
        reviewed_annotations_sha256 = _sha256(annotations_path)
    (
        ocr_evidence_bindings,
        reviewed_ocr_events,
        subtitles,
        caption_safe_region,
    ) = _reviewed_subtitles(
        plan,
        start_s,
        end_s,
        validate_inputs=validate_inputs,
        expected_annotations_sha256=reviewed_annotations_sha256 or None,
    )
    comparison_duration = (
        _comparison_policy_duration(
            plan.source_video,
            start_s=start_s,
            requested_duration_s=end_s - start_s,
            tools=_ffmpeg_tools(),
            fps=plan.fps,
        )
        if validate_inputs
        else end_s - start_s
    )
    work = root / "work" / mode
    final = root / "final"
    clean_master_name = (
        "pilot_clean_master.mp4" if mode == "pilot" else "replica_clean_master.mp4"
    )
    captioned_master_name = (
        "pilot_captioned_master.mp4"
        if mode == "pilot"
        else "replica_captioned_master.mp4"
    )
    comparison_name = "pilot_side_by_side.mp4" if mode == "pilot" else "replica_side_by_side.mp4"
    subtitle_font_path, subtitle_font_family, subtitle_font_sha256 = (
        _default_subtitle_font_binding()
    )
    manifest = ReplicaCompositionManifest(
        schema_version=COMPOSITION_SCHEMA_VERSION,
        project_id=plan.project_id,
        mode=mode,
        output_root=root,
        manifest_path=final / f"{mode}_composition_manifest.json",
        start_s=start_s,
        end_s=end_s,
        duration_s=end_s - start_s,
        shots=tuple(composition_shots),
        ocr_evidence_bindings=ocr_evidence_bindings,
        reviewed_ocr_events=reviewed_ocr_events,
        subtitles=tuple(subtitles),
        caption_safe_region=caption_safe_region,
        reviewed_annotations_sha256=reviewed_annotations_sha256,
        reviewed_annotations_snapshot_path=(
            final / "review_snapshot" / "shot_annotations.json"
        ),
        ocr_evidence_snapshot_paths=tuple(
            final
            / "review_snapshot"
            / "ocr_evidence"
            / binding.shot_id
            / f"{binding.evidence_sha256}.json"
            for binding in ocr_evidence_bindings
        ),
        subtitle_font_path=subtitle_font_path,
        subtitle_font_family=subtitle_font_family,
        subtitle_font_sha256=subtitle_font_sha256,
        source_audio_path=audio_path,
        source_audio_sha256=audio_sha256,
        presentation_source_path=plan.source_video,
        presentation_source_sha256=_sha256(plan.source_video),
        subtitle_path=work / "subtitles.ass",
        concat_list_path=work / "concat.ffconcat",
        picture_path=work / "picture.mp4",
        clean_master_path=final / clean_master_name,
        captioned_master_path=final / captioned_master_name,
        master_path=final / captioned_master_name,
        master_alias="captioned_master_path",
        side_by_side_path=final / comparison_name,
        final_qc_path=final / f"{mode}_qc.json",
        current_pointer_path=final / f"{mode}_current.json",
        audio_mode="source_aac_stream_copy",
        comparison_duration_s=comparison_duration,
        comparison_tail_policy="clamp_to_last_reference_video_frame",
        clean_master_command=(),
        captioned_master_command=(),
        ffmpeg_command=(),
        side_by_side_command=(),
    )
    _validate_manifest_paths(manifest, plan=plan, require_outputs=False)
    manifest = _with_commands(plan, manifest)
    return manifest


def compose_replica_pilot(plan: PetReplicaPlan) -> ReplicaCompositionManifest:
    return _compose(plan, "pilot")


def compose_replica_final(plan: PetReplicaPlan) -> ReplicaCompositionManifest:
    return _compose(plan, "final")


def build_replica_ffmpeg_commands(manifest: ReplicaCompositionManifest) -> tuple[tuple[str, ...], ...]:
    """Return the deterministic local commands used by the composition stage."""
    _validate_manifest_paths(manifest, require_outputs=False)
    tools = _ffmpeg_tools()
    commands: list[tuple[str, ...]] = []
    for shot in manifest.shots:
        target_frames = max(1, round(shot.editorial_duration_s * 30))
        filter_graph = (
            f"trim=start={shot.source_start_s:.9f}:end={shot.source_end_s:.9f},"
            "setpts=PTS-STARTPTS,"
            "scale=720:1280:force_original_aspect_ratio=increase:"
            "in_range=auto:out_range=tv,"
            "crop=720:1280,"
            "fps=fps=30:round=near,"
            f"trim=end_frame={target_frames},setpts=PTS-STARTPTS"
        )
        commands.append(
            (
                str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(shot.source_path), "-map", "0:v:0", "-an",
                "-vf", filter_graph, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-bf", "0", "-refs", "1", "-g", "30", "-keyint_min", "30",
                "-sc_threshold", "0", "-x264-params", "open-gop=0:repeat-headers=1",
                "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709",
                "-color_primaries", "bt709",
                "-r", "30", "-movflags", "+faststart", str(shot.normalized_path),
            )
        )
    commands.append(
        (
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
            "-safe", "0", "-i", str(manifest.concat_list_path), "-map", "0:v:0",
            "-an", "-c:v", "copy", str(manifest.picture_path),
        )
    )
    commands.append(manifest.clean_master_command)
    commands.append(manifest.captioned_master_command)
    commands.append(manifest.side_by_side_command)
    _reject_forbidden_commands(commands)
    return tuple(commands)


def validate_replica_master(
    plan: PetReplicaPlan,
    manifest: ReplicaCompositionManifest,
) -> dict[str, Any]:
    """Fail closed unless a rendered master still satisfies the locked contract."""
    validate_pet_replica_plan(plan)
    _validate_manifest(plan, manifest)
    try:
        validate_replica_selection(plan, pilot_only=(manifest.mode == "pilot"))
        validate_replica_audio_manifest(plan, manifest.output_root / "audio" / "audio_manifest.json")
    except (PetReplicaReviewError, ValueError, OSError, RuntimeError) as exc:
        raise PetReplicaCompositionError(f"Current selection/audio review is invalid: {exc}") from exc

    tools = _ffmpeg_tools()
    frame_proof = _prove_frame_sequence(
        tuple(shot.normalized_path for shot in manifest.shots),
        manifest.picture_path,
        tools,
        fps=plan.fps,
    )
    frame_proof = _bind_frame_proof_to_planned_cuts(
        frame_proof, manifest.shots, fps=plan.fps
    )
    masters_qc = _validate_dual_master_media(
        manifest.picture_path,
        manifest.clean_master_path,
        manifest.captioned_master_path,
        manifest.presentation_source_path,
        subtitles=manifest.subtitles,
        caption_safe_region=manifest.caption_safe_region,
        frame_proof=frame_proof,
        expected_duration_s=manifest.duration_s,
        audio_mode=manifest.audio_mode,
        tools=tools,
        fps=plan.fps,
        width=plan.width,
        height=plan.height,
    )
    source_pixel_evidence = _prove_no_direct_source_pixels(
        manifest.picture_path,
        manifest.presentation_source_path,
        manifest.shots,
        start_s=manifest.start_s,
        duration_s=manifest.duration_s,
        tools=tools,
        fps=plan.fps,
    )
    comparison_qc = _validate_comparison_master(
        manifest.presentation_source_path,
        manifest.side_by_side_path,
        expected_duration_s=manifest.comparison_duration_s,
        tools=tools,
        fps=plan.fps,
    )
    _assert_subtitles_clean(manifest)
    return {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "valid": True,
        "mode": manifest.mode,
        "master_path": str(manifest.master_path),
        "master_sha256": _sha256(manifest.master_path),
        "master_alias": manifest.master_alias,
        "clean_master_path": str(manifest.clean_master_path),
        "captioned_master_path": str(manifest.captioned_master_path),
        "duration_s": masters_qc["captioned"]["duration_s"],
        "masters": masters_qc,
        "frame_sequence": frame_proof,
        "master_frame_boundaries": masters_qc["captioned"]["frame_boundaries"],
        "blackdetect": masters_qc["captioned"]["blackdetect"],
        "source_pixel_evidence": source_pixel_evidence,
        "comparison": comparison_qc,
        "cut_count": len(frame_proof["cuts"]),
        "cut_timestamps_s": [
            {
                "planned": item["planned_timestamp_s"],
                "actual": item["actual_timestamp_s"],
                "delta_frames": item["delta_frames"],
            }
            for item in frame_proof["cuts"]
        ],
        "audio": masters_qc["captioned"]["audio"],
        "audio_mode": manifest.audio_mode,
        "fallback_used": manifest.audio_mode == "pcm_to_aac_192k_once",
    }


def _compose(plan: PetReplicaPlan, mode: str) -> ReplicaCompositionManifest:
    manifest = build_replica_composition(plan, selection=None, mode=mode)
    root = _output_root(plan)
    _recover_abandoned_transactions(root)
    (root / "work" / ".transactions").mkdir(parents=True, exist_ok=True)
    transaction = Path(
        tempfile.mkdtemp(prefix=f"{mode}-", dir=root / "work" / ".transactions")
    )
    staged = _stage_manifest(plan, manifest, transaction)
    release_id = transaction.name
    try:
        _preflight_subtitle_font(
            staged.subtitle_font_path,
            staged.subtitle_font_family,
            staged.subtitle_font_sha256,
            staged.subtitles,
            tools=_ffmpeg_tools(),
        )
        _write_subtitles(staged)
        _write_concat_list(staged)
        commands = build_replica_ffmpeg_commands(staged)
        for command in commands[:-1]:
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            _run(command)
        tools = _ffmpeg_tools()
        try:
            for master_path in (
                staged.clean_master_path,
                staged.captioned_master_path,
            ):
                _verify_audio_against_source(
                    staged.presentation_source_path,
                    master_path,
                    tools,
                    expected_duration_s=staged.duration_s,
                    audio_mode="source_aac_stream_copy",
                    frame_tolerance_s=1 / plan.fps,
                )
        except PetReplicaCompositionError:
            staged.clean_master_path.unlink(missing_ok=True)
            staged.captioned_master_path.unlink(missing_ok=True)
            staged = _with_commands(
                plan, replace(staged, audio_mode="pcm_to_aac_192k_once")
            )
            _run(staged.clean_master_command)
            _run(staged.captioned_master_command)
        _run(staged.side_by_side_command)
        media_qc = validate_replica_master(plan, staged)
        published = _published_manifest(
            plan, staged, root / "final" / "releases" / release_id
        )
        _write_json(staged.manifest_path, _manifest_payload(published))
        expected_qc = _final_qc_payload(
            media_qc,
            release_id=release_id,
            published=published,
            staged=staged,
        )
        _write_json(staged.final_qc_path, expected_qc)
        _publish_release(
            root,
            transaction,
            mode,
            release_id=release_id,
            validate_before_pointer=lambda: _validate_publication_snapshot(
                plan,
                published,
                expected_qc=expected_qc,
            ),
        )
        return published
    except Exception:
        shutil.rmtree(transaction, ignore_errors=True)
        raise


def _stage_manifest(
    plan: PetReplicaPlan, manifest: ReplicaCompositionManifest, transaction: Path
) -> ReplicaCompositionManifest:
    release = transaction / "release"
    staged_shots = tuple(
        replace(shot, normalized_path=release / "normalized" / shot.normalized_path.name)
        for shot in manifest.shots
    )
    staged = replace(
        manifest,
        manifest_path=release / manifest.manifest_path.name,
        subtitle_path=release / "subtitles.ass",
        concat_list_path=release / "concat.ffconcat",
        picture_path=release / "picture.mp4",
        clean_master_path=release / manifest.clean_master_path.name,
        captioned_master_path=release / manifest.captioned_master_path.name,
        master_path=release / manifest.captioned_master_path.name,
        side_by_side_path=release / manifest.side_by_side_path.name,
        final_qc_path=release / manifest.final_qc_path.name,
        reviewed_annotations_snapshot_path=(
            release / "review_snapshot" / "shot_annotations.json"
        ),
        ocr_evidence_snapshot_paths=tuple(
            release
            / "review_snapshot"
            / "ocr_evidence"
            / binding.shot_id
            / f"{binding.evidence_sha256}.json"
            for binding in manifest.ocr_evidence_bindings
        ),
        shots=staged_shots,
    )
    _stage_review_snapshot(manifest, staged)
    _validate_manifest_paths(
        staged,
        plan=plan,
        require_outputs=False,
        artifact_release_dir=release,
    )
    return _with_commands(plan, staged)


def _stage_review_snapshot(
    source: ReplicaCompositionManifest,
    staged: ReplicaCompositionManifest,
) -> None:
    if not source.reviewed_annotations_sha256:
        if source.ocr_evidence_bindings:
            raise PetReplicaCompositionError(
                "OCR evidence cannot be staged without reviewed annotations."
            )
        return
    annotations = source.output_root / "reference" / "shot_annotations.json"
    _require_regular_no_symlink(annotations, "reviewed annotations")
    annotations_bytes = annotations.read_bytes()
    if hashlib.sha256(annotations_bytes).hexdigest() != source.reviewed_annotations_sha256:
        raise PetReplicaCompositionError(
            "Reviewed annotations changed before transaction staging."
        )
    _write_bytes(staged.reviewed_annotations_snapshot_path, annotations_bytes)
    if len(source.ocr_evidence_bindings) != len(staged.ocr_evidence_snapshot_paths):
        raise PetReplicaCompositionError(
            "OCR evidence snapshot paths do not cover every binding."
        )
    for binding, destination in zip(
        source.ocr_evidence_bindings,
        staged.ocr_evidence_snapshot_paths,
    ):
        evidence = source.output_root / binding.evidence_path
        _require_regular_no_symlink(evidence, f"OCR evidence {binding.shot_id}")
        contents = evidence.read_bytes()
        if hashlib.sha256(contents).hexdigest() != binding.evidence_sha256:
            raise PetReplicaCompositionError(
                f"OCR evidence changed before staging: {binding.shot_id}."
            )
        _write_bytes(destination, contents)


def _published_manifest(
    plan: PetReplicaPlan, staged: ReplicaCompositionManifest, release_target: Path
) -> ReplicaCompositionManifest:
    root = _output_root(plan)
    release = staged.manifest_path.parent
    _validate_manifest_paths(
        staged,
        plan=plan,
        require_outputs=False,
        artifact_release_dir=release,
    )
    release_target = _require_canonical_release_target(root, release_target)

    def published_path(path: Path) -> Path:
        try:
            return release_target / path.relative_to(release)
        except ValueError:
            return path

    published_shots = tuple(
        replace(shot, normalized_path=published_path(shot.normalized_path))
        for shot in staged.shots
    )
    published = replace(
        staged,
        manifest_path=published_path(staged.manifest_path),
        subtitle_path=published_path(staged.subtitle_path),
        concat_list_path=published_path(staged.concat_list_path),
        picture_path=published_path(staged.picture_path),
        clean_master_path=published_path(staged.clean_master_path),
        captioned_master_path=published_path(staged.captioned_master_path),
        master_path=published_path(staged.captioned_master_path),
        side_by_side_path=published_path(staged.side_by_side_path),
        final_qc_path=published_path(staged.final_qc_path),
        reviewed_annotations_snapshot_path=published_path(
            staged.reviewed_annotations_snapshot_path
        ),
        ocr_evidence_snapshot_paths=tuple(
            published_path(path)
            for path in staged.ocr_evidence_snapshot_paths
        ),
        shots=published_shots,
    )
    _validate_manifest_paths(
        published,
        plan=plan,
        require_outputs=False,
        artifact_release_dir=release_target,
    )
    return _with_commands(plan, published)


def _current_media_input_hashes(
    manifest: ReplicaCompositionManifest,
) -> dict[str, Any]:
    source_audio_sha256 = _require_file(
        manifest.output_root,
        manifest.source_audio_path,
        "source audio",
    )
    if source_audio_sha256 != manifest.source_audio_sha256:
        raise PetReplicaCompositionError(
            "Source AAC bytes changed after composition planning."
        )

    _require_regular_no_symlink(
        manifest.presentation_source_path,
        "presentation source",
    )
    presentation_source_sha256 = _sha256(
        manifest.presentation_source_path
    )
    if presentation_source_sha256 != manifest.presentation_source_sha256:
        raise PetReplicaCompositionError(
            "Presentation source bytes changed after composition planning."
        )

    selection: dict[str, str] = {}
    for shot in manifest.shots:
        source_sha256 = _require_file(
            manifest.output_root,
            shot.source_path,
            f"selected clip {shot.shot_id}",
        )
        if source_sha256 != shot.source_sha256:
            raise PetReplicaCompositionError(
                "Selected clip bytes changed after composition planning: "
                f"{shot.shot_id}."
            )
        selection[shot.shot_id] = source_sha256
    return {
        "source_audio_sha256": source_audio_sha256,
        "presentation_source_sha256": presentation_source_sha256,
        "selection": selection,
    }


def _input_bundle(manifest: ReplicaCompositionManifest) -> dict[str, Any]:
    _validate_manifest_paths(manifest, require_outputs=True)
    media_inputs = _current_media_input_hashes(manifest)
    annotations = manifest.output_root / "reference" / "shot_annotations.json"
    _require_inside(manifest.output_root, annotations, "reviewed annotations")
    if manifest.reviewed_annotations_sha256:
        _require_regular_no_symlink(annotations, "reviewed annotations")
        current_annotations_sha256 = _sha256(annotations)
        if current_annotations_sha256 != manifest.reviewed_annotations_sha256:
            raise PetReplicaCompositionError(
                "Current reviewed annotation does not match the captured build snapshot."
            )
    else:
        current_annotations_sha256 = None
        if annotations.exists():
            raise PetReplicaCompositionError(
                "Composition manifest omitted an existing reviewed annotation."
            )
    evidence = _ocr_evidence_bundle(manifest)
    subtitle_font = _subtitle_font_bundle(manifest)
    return {
        **media_inputs,
        "reviewed_annotations_sha256": current_annotations_sha256,
        "ocr_evidence": evidence,
        "review_snapshot": _review_snapshot_bundle(manifest),
        "subtitle_font": subtitle_font,
    }


def _validate_current_approved_inputs(
    plan: PetReplicaPlan,
    published: ReplicaCompositionManifest,
) -> None:
    try:
        validate_replica_selection(
            plan,
            pilot_only=(published.mode == "pilot"),
        )
        approved_selection = _disk_selection_paths(
            plan,
            {shot.shot_id for shot in published.shots},
        )
    except (PetReplicaReviewError, ValueError, OSError, RuntimeError) as exc:
        raise PetReplicaCompositionError(
            "Current approved selection validation failed before publication: "
            f"{exc}"
        ) from exc

    for shot in published.shots:
        current_path = approved_selection.get(shot.shot_id)
        current_sha256 = (
            _require_file(
                published.output_root,
                current_path,
                f"current selected clip {shot.shot_id}",
            )
            if current_path is not None
            else None
        )
        if (
            current_path != shot.source_path
            or current_sha256 != shot.source_sha256
        ):
            raise PetReplicaCompositionError(
                "Current approved selection does not match the rendered release: "
                f"{shot.shot_id}."
            )

    try:
        audio = validate_replica_audio_manifest(
            plan,
            published.output_root / "audio" / "audio_manifest.json",
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise PetReplicaCompositionError(
            "Current audio manifest validation failed before publication: "
            f"{exc}"
        ) from exc
    try:
        audio_path = Path(audio.full_source.path).expanduser().absolute()
        audio_sha256 = _require_file(
            published.output_root,
            audio_path,
            "current source audio",
        )
    except (AttributeError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise PetReplicaCompositionError(
            "Current audio manifest validation failed before publication: "
            f"{exc}"
        ) from exc
    if (
        audio_path != published.source_audio_path
        or audio.full_source.sha256 != audio_sha256
        or audio_sha256 != published.source_audio_sha256
        or audio.source_sha256 != published.presentation_source_sha256
    ):
        raise PetReplicaCompositionError(
            "Current audio asset does not match the rendered release."
        )


def _final_qc_payload(
    qc: Mapping[str, Any],
    *,
    release_id: str,
    published: ReplicaCompositionManifest,
    staged: ReplicaCompositionManifest,
) -> dict[str, Any]:
    evidence = _ocr_evidence_bundle(staged)
    subtitle_font = _subtitle_font_bundle(staged)
    relocated_qc = _relocate_qc_media_paths(qc, published)
    return _json_normalized(
        {
            **relocated_qc,
            "release_id": release_id,
            "master_path": str(published.master_path),
            "clean_master_path": str(published.clean_master_path),
            "captioned_master_path": str(published.captioned_master_path),
            "composition_manifest_sha256": _sha256(staged.manifest_path),
            "ocr_evidence": evidence,
            "review_snapshot": _review_snapshot_bundle(staged),
            "subtitle_font": subtitle_font,
            "artifact_sha256": _artifact_hash_bundle(staged),
            "input_bundle": _input_bundle(staged),
        }
    )


def _relocate_qc_media_paths(
    qc: Mapping[str, Any],
    published: ReplicaCompositionManifest,
) -> dict[str, Any]:
    relocated = _json_normalized(qc)
    masters = relocated.get("masters")
    if isinstance(masters, dict):
        for variant, path in (
            ("clean", published.clean_master_path),
            ("captioned", published.captioned_master_path),
        ):
            record = masters.get(variant)
            if isinstance(record, dict) and "path" in record:
                record["path"] = str(path)
    comparison = relocated.get("comparison")
    if isinstance(comparison, dict) and "path" in comparison:
        comparison["path"] = str(published.side_by_side_path)
    return relocated


def _validate_publication_snapshot(
    plan: PetReplicaPlan,
    published: ReplicaCompositionManifest,
    *,
    expected_qc: Mapping[str, Any],
) -> None:
    _validate_manifest_paths(
        published,
        plan=plan,
        require_outputs=True,
        artifact_release_dir=published.manifest_path.parent,
    )
    _current_media_input_hashes(published)
    _validate_current_ocr_evidence(plan, published)
    _subtitle_font_bundle(published)
    try:
        manifest_bytes = published.manifest_path.read_bytes()
        qc_bytes = published.final_qc_path.read_bytes()
        serialized_manifest = json.loads(manifest_bytes)
        qc = json.loads(qc_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError(
            "Published composition snapshot metadata is unreadable."
        ) from exc
    expected_manifest = _manifest_payload(published)
    if (
        serialized_manifest != expected_manifest
        or manifest_bytes != _canonical_json_bytes(expected_manifest)
    ):
        raise PetReplicaCompositionError(
            "Serialized composition manifest does not match the in-memory snapshot."
        )
    if (
        qc.get("valid") is not True
        or qc_bytes != _canonical_json_bytes(expected_qc)
    ):
        raise PetReplicaCompositionError(
            "Final QC does not match the complete expected validation snapshot."
        )
    _validate_current_approved_inputs(plan, published)
    manifest_sha256 = _sha256(published.manifest_path)
    expected_input_bundle = _input_bundle(published)
    expected_evidence = _ocr_evidence_bundle(published)
    expected_snapshot = _review_snapshot_bundle(published)
    expected_font = _subtitle_font_bundle(published)
    expected_artifacts = _artifact_hash_bundle(published)
    if (
        qc.get("composition_manifest_sha256") != manifest_sha256
        or qc.get("input_bundle") != expected_input_bundle
        or qc.get("ocr_evidence") != expected_evidence
        or qc.get("review_snapshot") != expected_snapshot
        or qc.get("subtitle_font") != expected_font
        or qc.get("artifact_sha256") != expected_artifacts
        or qc.get("master_sha256") != _sha256(published.master_path)
    ):
        raise PetReplicaCompositionError(
            "Final QC does not match the sealed composition review snapshot."
        )


def _ocr_evidence_bundle(
    manifest: ReplicaCompositionManifest,
) -> list[dict[str, Any]]:
    _validate_ocr_evidence_bindings(
        manifest.output_root,
        manifest.ocr_evidence_bindings,
    )
    return [asdict(binding) for binding in manifest.ocr_evidence_bindings]


def _subtitle_font_bundle(
    manifest: ReplicaCompositionManifest,
) -> dict[str, str]:
    _validate_subtitle_font_binding(
        manifest.subtitle_font_path,
        manifest.subtitle_font_family,
        manifest.subtitle_font_sha256,
        verify_hash=True,
        require_approved=True,
    )
    return {
        "path": str(manifest.subtitle_font_path),
        "family": manifest.subtitle_font_family,
        "sha256": manifest.subtitle_font_sha256,
    }


def _review_snapshot_bundle(
    manifest: ReplicaCompositionManifest,
) -> dict[str, Any] | None:
    if not manifest.reviewed_annotations_sha256:
        if manifest.ocr_evidence_bindings or manifest.ocr_evidence_snapshot_paths:
            raise PetReplicaCompositionError(
                "Review snapshot is incomplete without reviewed annotations."
            )
        return None
    annotations = manifest.reviewed_annotations_snapshot_path
    if not annotations.is_file():
        if _manifest_release_directory(manifest, manifest.output_root) is None:
            return {
                "annotations_path": str(annotations.relative_to(manifest.output_root)),
                "annotations_sha256": manifest.reviewed_annotations_sha256,
                "ocr_evidence": [],
                "staged": False,
            }
        raise PetReplicaCompositionError(
            "Release-local reviewed annotation snapshot is missing."
        )
    _require_regular_no_symlink(annotations, "reviewed annotation snapshot")
    if _sha256(annotations) != manifest.reviewed_annotations_sha256:
        raise PetReplicaCompositionError(
            "Release-local reviewed annotation snapshot changed."
        )
    if len(manifest.ocr_evidence_bindings) != len(
        manifest.ocr_evidence_snapshot_paths
    ):
        raise PetReplicaCompositionError(
            "Release-local OCR evidence snapshot is incomplete."
        )
    release = manifest.manifest_path.parent
    evidence_records: list[dict[str, Any]] = []
    for binding, path in zip(
        manifest.ocr_evidence_bindings,
        manifest.ocr_evidence_snapshot_paths,
    ):
        _require_inside_release(release, path, f"OCR evidence snapshot {binding.shot_id}")
        _require_regular_no_symlink(path, f"OCR evidence snapshot {binding.shot_id}")
        if _sha256(path) != binding.evidence_sha256:
            raise PetReplicaCompositionError(
                f"Release-local OCR evidence snapshot changed: {binding.shot_id}."
            )
        evidence_records.append(
            {
                "shot_id": binding.shot_id,
                "path": path.relative_to(release).as_posix(),
                "sha256": binding.evidence_sha256,
                "detected_item_count": binding.detected_item_count,
            }
        )
    return {
        "annotations_path": annotations.relative_to(release).as_posix(),
        "annotations_sha256": manifest.reviewed_annotations_sha256,
        "ocr_evidence": evidence_records,
        "staged": True,
    }


def _artifact_hash_bundle(
    manifest: ReplicaCompositionManifest,
) -> dict[str, str]:
    release = manifest.manifest_path.parent
    artifacts = {
        "subtitles": manifest.subtitle_path,
        "concat_list": manifest.concat_list_path,
        "picture": manifest.picture_path,
        "clean_master": manifest.clean_master_path,
        "captioned_master": manifest.captioned_master_path,
        "comparison": manifest.side_by_side_path,
        **{
            f"normalized_{shot.shot_id}": shot.normalized_path
            for shot in manifest.shots
        },
    }
    result: dict[str, str] = {}
    for label, path in artifacts.items():
        _require_inside_release(release, path, label)
        _require_regular_no_symlink(path, label)
        result[path.relative_to(release).as_posix()] = _sha256(path)
    return result


def _publish_release(
    root: Path,
    transaction: Path,
    mode: str,
    *,
    release_id: str,
    replace_file: Callable[[Path, Path], None] = os.replace,
    validate_before_pointer: Callable[[], None] | None = None,
) -> None:
    root = Path(root).expanduser().absolute()
    transaction = Path(transaction).expanduser().absolute()
    release_id = _validate_release_id(release_id)
    _require_inside(root, transaction, "transaction")
    release = transaction / "release"
    _require_inside(root, release, "staged release")
    _require_regular_directory(release, "staged release")
    final = root / "final"
    target = _canonical_release_target(root, release_id)
    if target.exists():
        raise PetReplicaCompositionError("Replica release identifier already exists.")
    final.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _canonical_release_target(root, release_id)
    pointer_source = transaction / f"{mode}_current.json"
    _require_inside(root, pointer_source, "staged current pointer")
    _write_json(
        pointer_source,
        _current_pointer_payload(root, mode, release_id, target),
    )
    pointer_target = final / f"{mode}_current.json"
    _require_inside(root, pointer_target, "current pointer")
    moved_release = False
    try:
        replace_file(release, target)
        moved_release = True
        if validate_before_pointer is not None:
            validate_before_pointer()
        replace_file(pointer_source, pointer_target)
    except Exception as exc:
        if moved_release:
            shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, PetReplicaCompositionError):
            raise
        raise PetReplicaCompositionError("Replica release publish failed; the prior release remains current.") from exc
    finally:
        shutil.rmtree(transaction, ignore_errors=True)


def _validate_release_id(release_id: str) -> str:
    if not isinstance(release_id, str) or _SAFE_RELEASE_ID.fullmatch(release_id) is None:
        raise PetReplicaCompositionError(
            "Replica release identifier must be one safe ASCII path component."
        )
    return release_id


def _canonical_releases_directory(root: Path) -> Path:
    root = Path(root).expanduser().absolute()
    releases = root / "final" / "releases"
    _require_inside(root, releases, "release directory")
    if releases.exists():
        _require_regular_directory(releases, "release directory")
    expected = root.resolve(strict=False) / "final" / "releases"
    if releases.resolve(strict=False) != expected:
        raise PetReplicaCompositionError("Replica release directory is not canonical.")
    return releases


def _canonical_release_target(root: Path, release_id: str) -> Path:
    release_id = _validate_release_id(release_id)
    releases = _canonical_releases_directory(root)
    target = releases / release_id
    _require_inside(root, target, "release target")
    if target.resolve(strict=False).parent != releases.resolve(strict=False):
        raise PetReplicaCompositionError(
            "Replica release target parent is not the canonical release directory."
        )
    return target


def _require_canonical_release_target(root: Path, release_target: Path) -> Path:
    candidate = Path(release_target).expanduser().absolute()
    try:
        release_id = _validate_release_id(candidate.name)
    except PetReplicaCompositionError as exc:
        raise PetReplicaCompositionError("Replica release target is not canonical.") from exc
    expected = _canonical_release_target(root, release_id)
    if (
        candidate != expected.absolute()
        or candidate.resolve(strict=False) != expected.resolve(strict=False)
    ):
        raise PetReplicaCompositionError("Replica release target is not canonical.")
    return expected


def _current_pointer_payload(
    root: Path,
    mode: str,
    release_id: str,
    release_target: Path,
) -> dict[str, str]:
    release_id = _validate_release_id(release_id)
    target = _require_canonical_release_target(root, release_target)
    relative = target.relative_to(Path(root).expanduser().absolute() / "final")
    if relative.parts != ("releases", release_id):
        raise PetReplicaCompositionError("Replica current pointer release path is not canonical.")
    return {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "mode": mode,
        "release": release_id,
        "release_path": relative.as_posix(),
    }


def _recover_abandoned_transactions(root: Path) -> None:
    transactions = root / "work" / ".transactions"
    if not transactions.exists():
        return
    if transactions.is_symlink() or not transactions.is_dir():
        raise PetReplicaCompositionError("Replica transaction root must be a regular directory.")
    for child in transactions.iterdir():
        if child.is_symlink():
            raise PetReplicaCompositionError("Replica transaction recovery refuses symlinks.")
        if child.is_dir():
            shutil.rmtree(child)
        elif child.is_file():
            child.unlink()


def _require_regular_directory(path: Path, label: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise PetReplicaCompositionError(f"{label} must be a regular directory.")


def _with_commands(plan: PetReplicaPlan, manifest: ReplicaCompositionManifest) -> ReplicaCompositionManifest:
    _validate_manifest_paths(manifest, plan=plan, require_outputs=False)
    tools = _ffmpeg_tools()
    audio_input = (
        manifest.source_audio_path
        if manifest.audio_mode == "source_aac_stream_copy"
        else manifest.presentation_source_path
    )
    clean_master = _build_clean_master_command(
        manifest.picture_path,
        audio_input,
        manifest.clean_master_path,
        duration_s=manifest.duration_s,
        audio_mode=manifest.audio_mode,
        tools=tools,
    )
    captioned_master = _build_captioned_master_command(
        manifest.picture_path,
        audio_input,
        manifest.subtitle_path,
        manifest.captioned_master_path,
        duration_s=manifest.duration_s,
        audio_mode=manifest.audio_mode,
        tools=tools,
        subtitle_font_path=manifest.subtitle_font_path,
        subtitle_font_family=manifest.subtitle_font_family,
        subtitle_font_sha256=manifest.subtitle_font_sha256,
    )
    side_by_side = _build_comparison_command(
        manifest.presentation_source_path,
        manifest.captioned_master_path,
        manifest.side_by_side_path,
        start_s=manifest.start_s,
        duration_s=manifest.comparison_duration_s,
        tools=tools,
    )
    return replace(
        manifest,
        clean_master_command=clean_master,
        captioned_master_command=captioned_master,
        ffmpeg_command=captioned_master,
        side_by_side_command=side_by_side,
    )


def _build_clean_master_command(
    picture_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    duration_s: float,
    audio_mode: str,
    tools: FFmpegTools,
) -> tuple[str, ...]:
    return (
        str(tools.ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(picture_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-t",
        f"{duration_s:.9f}",
        "-c:v",
        "copy",
        *_audio_command(audio_mode),
        "-movflags",
        "+faststart",
        str(output_path),
    )


def _build_captioned_master_command(
    picture_path: Path,
    audio_path: Path,
    subtitle_path: Path,
    output_path: Path,
    *,
    duration_s: float,
    audio_mode: str,
    tools: FFmpegTools,
    subtitle_font_path: Path | None = None,
    subtitle_font_family: str | None = None,
    subtitle_font_sha256: str | None = None,
) -> tuple[str, ...]:
    if subtitle_font_path is None:
        (
            subtitle_font_path,
            selected_family,
            selected_sha256,
        ) = _default_subtitle_font_binding()
        subtitle_font_family = subtitle_font_family or selected_family
        subtitle_font_sha256 = subtitle_font_sha256 or selected_sha256
    if subtitle_font_family is None or subtitle_font_sha256 is None:
        raise PetReplicaCompositionError(
            "Captioned master requires a complete subtitle font binding."
        )
    _validate_subtitle_font_binding(
        subtitle_font_path,
        subtitle_font_family,
        subtitle_font_sha256,
        verify_hash=True,
    )
    subtitle_filter = _escape_filter_path(subtitle_path)
    fonts_directory = _escape_filter_path(subtitle_font_path.parent)
    return (
        str(tools.ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(picture_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        f"setpts=N/(30*TB),ass='{subtitle_filter}':fontsdir='{fonts_directory}'",
        "-t",
        f"{duration_s:.9f}",
        "-c:v",
        "libx264",
        "-qp",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "passthrough",
        *_audio_command(audio_mode),
        "-movflags",
        "+faststart",
        str(output_path),
    )


def _audio_command(audio_mode: str) -> tuple[str, ...]:
    if audio_mode == "source_aac_stream_copy":
        return "-c:a", "copy"
    if audio_mode == "pcm_to_aac_192k_once":
        return (
            "-af",
            "volume=-2dB",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
        )
    raise PetReplicaCompositionError("Replica master has an unknown audio mode.")


def _build_comparison_command(
    reference_path: Path,
    remake_path: Path,
    output_path: Path,
    *,
    start_s: float,
    duration_s: float,
    tools: FFmpegTools,
) -> tuple[str, ...]:
    label_left = _comparison_filter("REFERENCE", 0, start_s, duration_s)
    label_right = _comparison_filter("REMAKE", 1, 0.0, duration_s)
    return (
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(reference_path),
        "-i", str(remake_path), "-filter_complex",
        f"{label_left};{label_right};[left][right]hstack=inputs=2[v]", "-map", "[v]",
        "-map", "0:a:0", "-af",
        f"atrim=start={start_s:.9f}:duration={duration_s:.9f},asetpts=PTS-STARTPTS",
        "-t", f"{duration_s:.9f}", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k",
        "-ac", "2", "-movflags", "+faststart",
        str(output_path),
    )


def _comparison_filter(label: str, input_index: int, start_s: float, duration_s: float) -> str:
    return (
        f"[{input_index}:v]trim=start={start_s:.9f}:duration={duration_s:.9f},"
        "setpts=PTS-STARTPTS,scale=720:1236:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:40:color=black,"
        f"drawtext=text='{label}':x=12:y=12:fontcolor=white:fontsize=18[left]"
        if input_index == 0
        else f"[{input_index}:v]trim=start={start_s:.9f}:duration={duration_s:.9f},"
        "setpts=PTS-STARTPTS,scale=720:1236:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:40:color=black,"
        f"drawtext=text='{label}':x=12:y=12:fontcolor=white:fontsize=18[right]"
    )


def replica_composition_window(
    plan: PetReplicaPlan,
    mode: str,
) -> tuple[float, float]:
    if mode == "pilot":
        requested_end_frame = round(plan.pilot_end_s * plan.fps)
        for shot in plan.shots:
            shot_start_frame = round(shot.start_s * plan.fps)
            shot_end_frame = round(shot.end_s * plan.fps)
            if shot_start_frame < requested_end_frame < shot_end_frame:
                return 0.0, shot_end_frame / plan.fps
        return 0.0, requested_end_frame / plan.fps
    if mode == "final":
        return 0.0, plan.duration_s
    raise PetReplicaCompositionError("Composition mode must be 'pilot' or 'final'.")


def replica_shot_overlaps_window(
    plan: PetReplicaPlan,
    shot: Any,
    start_s: float,
    end_s: float,
) -> bool:
    start_frame = round(start_s * plan.fps)
    end_frame = round(end_s * plan.fps)
    shot_start_frame = round(shot.start_s * plan.fps)
    shot_end_frame = round(shot.end_s * plan.fps)
    return min(end_frame, shot_end_frame) > max(start_frame, shot_start_frame)


def _frame_aligned_window(plan: PetReplicaPlan, shot: Any) -> tuple[float, float]:
    """Use integer source-frame boundaries rather than rounded JSON decimals."""
    start = round(shot.start_s * plan.fps) / plan.fps
    end = plan.duration_s if shot.end_s == plan.duration_s else round(shot.end_s * plan.fps) / plan.fps
    return start, end


def _selection_paths(
    plan: PetReplicaPlan,
    selection: Mapping[str, Any] | Sequence[Any] | None,
    *,
    required_window: tuple[float, float],
) -> dict[str, Path]:
    root = _output_root(plan)
    required = {
        shot.shot_id
        for shot in plan.shots
        if replica_shot_overlaps_window(
            plan,
            shot,
            required_window[0],
            required_window[1],
        )
    }
    if selection is None:
        values: Mapping[str, Any] = _disk_selection_paths(plan, required)
    elif isinstance(selection, Mapping):
        values = selection
    else:
        values = {str(getattr(item, "shot_id", "")): getattr(item, "video_path", None) for item in selection}
    if set(values) != required:
        missing = sorted(required - set(values))
        extra = sorted(set(values) - required)
        raise PetReplicaCompositionError(
            f"Composition selection must exactly cover the requested shots; missing={missing}, extra={extra}."
        )
    paths: dict[str, Path] = {}
    for shot_id, value in values.items():
        candidate = getattr(value, "video_path", value)
        if not isinstance(candidate, (str, Path)):
            raise PetReplicaCompositionError(f"Composition selection path is invalid: {shot_id}.")
        path = Path(candidate).expanduser().absolute()
        _require_file(root, path, f"selected clip {shot_id}")
        paths[shot_id] = path
    return paths


def _disk_selection_paths(plan: PetReplicaPlan, required: set[str]) -> dict[str, Path]:
    root = _output_root(plan)
    result: dict[str, Path] = {}
    for shot_id in required:
        path = root / "shots" / shot_id / "selection.json"
        _require_file(root, path, f"selection {shot_id}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PetReplicaCompositionError(f"Selection is invalid: {shot_id}.") from exc
        relative = payload.get("candidate_path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PetReplicaCompositionError(f"Selection candidate path is invalid: {shot_id}.")
        candidate = (root / relative).absolute()
        digest = _require_file(root, candidate, f"selected clip {shot_id}")
        if payload.get("candidate_sha256") != digest or payload.get("quality_approved") is not True:
            raise PetReplicaCompositionError(f"Selection is stale or unapproved: {shot_id}.")
        result[shot_id] = candidate
    return result


def _reviewed_subtitles(
    plan: PetReplicaPlan,
    start_s: float,
    end_s: float,
    *,
    validate_inputs: bool,
    expected_annotations_sha256: str | None = None,
) -> tuple[
    tuple[ReplicaOCREvidenceBinding, ...],
    tuple[ReplicaOCREvent, ...],
    tuple[ReplicaSubtitle, ...],
    ReplicaCaptionSafeRegion,
]:
    raw_path = _output_root(plan) / "reference" / "shot_annotations.json"
    if not raw_path.exists():
        if validate_inputs:
            raise PetReplicaCompositionError(
                "Reviewed OCR event annotations require manual review."
            )
        return (), (), (), _DEFAULT_CAPTION_SAFE_REGION
    try:
        raw_bytes = raw_path.read_bytes()
        if (
            expected_annotations_sha256 is not None
            and hashlib.sha256(raw_bytes).hexdigest()
            != expected_annotations_sha256
        ):
            raise PetReplicaCompositionError(
                "Reviewed annotation snapshot changed during composition build."
            )
        raw = json.loads(raw_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError("Reviewed subtitle manifest is invalid.") from exc
    _reject_branding(raw)
    try:
        annotations = load_reviewed_shot_annotations(
            plan,
            require_ocr_events=True,
            expected_annotations_sha256=expected_annotations_sha256,
        )
        safe_region = load_reviewed_caption_safe_region(
            plan,
            expected_annotations_sha256=expected_annotations_sha256,
        )
    except PetReplicaReferenceError as exc:
        raise PetReplicaCompositionError(
            "Reviewed OCR event annotations are invalid."
        ) from exc
    evidence_bindings: list[ReplicaOCREvidenceBinding] = []
    reviewed_events: list[ReplicaOCREvent] = []
    subtitles: list[ReplicaSubtitle] = []
    composition_start_frame = round(start_s * plan.fps)
    composition_end_frame = round(end_s * plan.fps)
    for annotation in annotations:
        if annotation.ocr_evidence is None:
            raise PetReplicaCompositionError(
                "Reviewed OCR event annotations lack bound detection evidence."
            )
        evidence_bindings.append(annotation.ocr_evidence)
        for event in annotation.ocr_events:
            overlap_start_frame = max(composition_start_frame, event.start_frame)
            overlap_end_frame = min(composition_end_frame, event.end_frame)
            if overlap_end_frame <= overlap_start_frame:
                continue
            reviewed_events.append(event)
            if not event.renderable:
                continue
            if _BRANDING_TEXT.search(event.reviewed_text):
                raise PetReplicaCompositionError(
                    "Reviewed subtitles contain platform branding."
                )
            subtitles.append(
                ReplicaSubtitle(
                    event_id=event.event_id,
                    shot_id=event.shot_id,
                    start_frame=overlap_start_frame - composition_start_frame,
                    end_frame=overlap_end_frame - composition_start_frame,
                    text=event.reviewed_text,
                    placement=event.placement,
                )
            )
    return (
        tuple(evidence_bindings),
        tuple(reviewed_events),
        tuple(subtitles),
        safe_region,
    )


def _reject_branding(value: Any) -> None:
    if isinstance(value, Mapping):
        classification = value.get("classification") or value.get("category") or value.get("kind")
        if (
            isinstance(classification, str)
            and classification.lower() in _EXCLUDED_OCR_CLASSIFICATIONS
        ):
            return
        for key, item in value.items():
            if str(key).lower() in _BRANDING_KEYS:
                raise PetReplicaCompositionError("Reviewed subtitle manifest contains platform branding.")
            _reject_branding(item)
    elif isinstance(value, list):
        for item in value:
            _reject_branding(item)
    elif isinstance(value, str) and _BRANDING_TEXT.search(value):
        raise PetReplicaCompositionError(
            "Reviewed subtitle manifest contains platform branding."
        )


def _write_subtitles(manifest: ReplicaCompositionManifest) -> None:
    _write_text(
        manifest.subtitle_path,
        "\ufeff"
        + _subtitle_document(
            manifest.subtitles,
            getattr(
                manifest,
                "subtitle_font_family",
                _default_subtitle_font_binding()[1],
            ),
        ),
    )


def _subtitle_document(
    subtitles: Sequence[ReplicaSubtitle],
    font_family: str | None = None,
) -> str:
    if font_family is None:
        font_family = _default_subtitle_font_binding()[1]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "",
        "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{font_family},42,&H00FFFFFF,&H000000FF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,2,1,2,48,48,100,1", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for item in subtitles:
        text = (
            item.text.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\n", r"\N")
        )
        placement = item.placement
        center_x = placement.x + placement.width // 2
        bottom_y = placement.y + placement.height
        override = rf"{{\an2\pos({center_x},{bottom_y})}}"
        lines.append(
            f"Dialogue: 0,{_ass_frame_time(item.start_frame)},{_ass_frame_time(item.end_frame)},"
            f"Default,,0,0,0,,{override}{text}"
        )
    return "\n".join(lines) + "\n"


def _write_concat_list(manifest: ReplicaCompositionManifest) -> None:
    lines = [f"file '{_ffconcat_escape(shot.normalized_path)}'" for shot in manifest.shots]
    _write_text(manifest.concat_list_path, "ffconcat version 1.0\n" + "\n".join(lines) + "\n")


def _validate_shot_timeline(
    plan: PetReplicaPlan, shots: Sequence[ReplicaCompositionShot], start_s: float, end_s: float
) -> None:
    cursor = 0.0
    tolerance = 2 / plan.fps
    for shot in shots:
        if shot.editorial_duration_s <= 0 or abs(shot.timeline_start_s - cursor) > tolerance:
            raise PetReplicaCompositionError("Composition cut timing is not contiguous within two frames.")
        if abs((shot.timeline_end_s - shot.timeline_start_s) - shot.editorial_duration_s) > 1e-6:
            raise PetReplicaCompositionError("Composition shot duration does not match its real-motion trim.")
        cursor = shot.timeline_end_s
    if abs(cursor - (end_s - start_s)) > 1 / plan.fps:
        raise PetReplicaCompositionError("Composition timing does not reach its locked endpoint.")


def _validate_manifest_paths(
    manifest: ReplicaCompositionManifest,
    *,
    plan: PetReplicaPlan | None = None,
    require_outputs: bool,
    artifact_release_dir: Path | None = None,
) -> None:
    manifest_root_path = Path(manifest.output_root).expanduser().absolute()
    if manifest_root_path.is_symlink():
        raise PetReplicaCompositionError("Composition output root may not use symlinks.")
    root = _output_root(plan) if plan is not None else manifest_root_path.resolve(strict=False)
    manifest_root = manifest_root_path.resolve(strict=False)
    if manifest_root != root:
        raise PetReplicaCompositionError("Composition output root is not canonical.")
    if plan is not None:
        expected_source = Path(plan.source_video).expanduser().resolve(strict=False)
        actual_source = Path(manifest.presentation_source_path).expanduser().resolve(strict=False)
        if actual_source != expected_source:
            raise PetReplicaCompositionError("Composition presentation source is not the canonical source.")
    _require_regular_no_symlink(manifest.presentation_source_path, "canonical source")

    artifacts = {
        "composition manifest": manifest.manifest_path,
        "source audio": manifest.source_audio_path,
        "subtitle": manifest.subtitle_path,
        "concat list": manifest.concat_list_path,
        "picture": manifest.picture_path,
        "clean master": manifest.clean_master_path,
        "captioned master": manifest.captioned_master_path,
        "master": manifest.master_path,
        "comparison": manifest.side_by_side_path,
        "final QC": manifest.final_qc_path,
        "current pointer": manifest.current_pointer_path,
    }
    for label, path in artifacts.items():
        _require_inside(root, path, label)
    _require_inside(
        root,
        manifest.reviewed_annotations_snapshot_path,
        "reviewed annotation snapshot",
    )
    if len(manifest.ocr_evidence_bindings) != len(
        manifest.ocr_evidence_snapshot_paths
    ):
        raise PetReplicaCompositionError(
            "OCR evidence snapshot paths do not cover every binding."
        )
    for binding, path in zip(
        manifest.ocr_evidence_bindings,
        manifest.ocr_evidence_snapshot_paths,
    ):
        _require_inside(root, path, f"OCR evidence snapshot {binding.shot_id}")
    _validate_subtitle_font_binding(
        manifest.subtitle_font_path,
        manifest.subtitle_font_family,
        manifest.subtitle_font_sha256,
        verify_hash=False,
        require_approved=True,
    )
    expected_pointer = root / "final" / f"{manifest.mode}_current.json"
    if Path(manifest.current_pointer_path).expanduser().absolute() != expected_pointer.absolute():
        raise PetReplicaCompositionError("Current pointer is not the canonical release pointer.")

    manifest_release_dir = _manifest_release_directory(manifest, root)
    if artifact_release_dir is not None:
        active_release_dir = Path(artifact_release_dir).expanduser().absolute()
        _require_inside(root, active_release_dir, "active release directory")
        if (
            manifest_release_dir is None
            or active_release_dir != manifest_release_dir.absolute()
            or active_release_dir.resolve(strict=False)
            != manifest_release_dir.resolve(strict=False)
        ):
            raise PetReplicaCompositionError(
                "Composition artifacts are not bound to the active release directory."
            )
    if manifest_release_dir is not None:
        release_artifacts = {
            "composition manifest": manifest.manifest_path,
            "subtitle": manifest.subtitle_path,
            "concat list": manifest.concat_list_path,
            "picture": manifest.picture_path,
            "clean master": manifest.clean_master_path,
            "captioned master": manifest.captioned_master_path,
            "master": manifest.master_path,
            "comparison": manifest.side_by_side_path,
            "final QC": manifest.final_qc_path,
        }
        for label, path in release_artifacts.items():
            _require_inside_release(manifest_release_dir, path, label)
        for shot in manifest.shots:
            _require_inside_release(
                manifest_release_dir,
                shot.normalized_path,
                f"normalized clip {shot.shot_id}",
            )
        _require_inside_release(
            manifest_release_dir,
            manifest.reviewed_annotations_snapshot_path,
            "reviewed annotation snapshot",
        )
        for binding, path in zip(
            manifest.ocr_evidence_bindings,
            manifest.ocr_evidence_snapshot_paths,
        ):
            _require_inside_release(
                manifest_release_dir,
                path,
                f"OCR evidence snapshot {binding.shot_id}",
            )

    _require_regular(manifest.source_audio_path, "source audio")
    _validate_ocr_evidence_bindings(root, manifest.ocr_evidence_bindings)
    for shot in manifest.shots:
        _require_inside(root, shot.source_path, f"selected clip {shot.shot_id}")
        _require_inside(root, shot.normalized_path, f"normalized clip {shot.shot_id}")
        _require_regular(shot.source_path, f"selected clip {shot.shot_id}")
    if require_outputs:
        for label in (
            "subtitle",
            "concat list",
            "picture",
            "clean master",
            "captioned master",
            "master",
            "comparison",
        ):
            _require_regular(artifacts[label], label)
        for shot in manifest.shots:
            _require_regular(shot.normalized_path, f"normalized clip {shot.shot_id}")
        if manifest.reviewed_annotations_sha256:
            _require_regular(
                manifest.reviewed_annotations_snapshot_path,
                "reviewed annotation snapshot",
            )
            for binding, path in zip(
                manifest.ocr_evidence_bindings,
                manifest.ocr_evidence_snapshot_paths,
            ):
                _require_regular(path, f"OCR evidence snapshot {binding.shot_id}")


def _validate_ocr_evidence_bindings(
    root: Path,
    bindings: Sequence[ReplicaOCREvidenceBinding],
) -> None:
    for binding in bindings:
        if (
            not isinstance(binding.shot_id, str)
            or not binding.shot_id
            or not isinstance(binding.evidence_path, str)
            or not isinstance(binding.evidence_sha256, str)
            or _SHA256.fullmatch(binding.evidence_sha256) is None
            or isinstance(binding.detected_item_count, bool)
            or not isinstance(binding.detected_item_count, int)
            or binding.detected_item_count < 0
            or binding.review_complete is not True
            or not isinstance(binding.reviewed_zero, bool)
        ):
            raise PetReplicaCompositionError(
                "Composition OCR evidence binding is invalid."
            )
        relative = Path(binding.evidence_path)
        expected = (
            Path("reference")
            / "ocr_evidence"
            / binding.shot_id
            / f"{binding.evidence_sha256}.json"
        )
        if (
            not binding.evidence_path
            or "\\" in binding.evidence_path
            or relative.is_absolute()
            or relative.parts != expected.parts
            or relative.as_posix() != binding.evidence_path
        ):
            raise PetReplicaCompositionError(
                "Composition OCR evidence path is not canonical."
            )
        evidence = Path(root).expanduser().absolute() / expected
        _require_inside(root, evidence, f"OCR evidence {binding.shot_id}")
        _require_regular_no_symlink(evidence, f"OCR evidence {binding.shot_id}")
        if _sha256(evidence) != binding.evidence_sha256:
            raise PetReplicaCompositionError(
                f"Composition OCR evidence SHA-256 changed for {binding.shot_id}."
            )


def _manifest_release_directory(
    manifest: ReplicaCompositionManifest,
    root: Path,
) -> Path | None:
    manifest_path = Path(manifest.manifest_path).expanduser().absolute()
    manifest_name = f"{manifest.mode}_composition_manifest.json"
    planning_path = root / "final" / manifest_name
    if manifest_path == planning_path.absolute():
        return None

    releases = _canonical_releases_directory(root)
    try:
        relative = manifest_path.relative_to(releases)
    except ValueError:
        relative = None
    if relative is not None:
        if len(relative.parts) != 2 or relative.parts[1] != manifest_name:
            raise PetReplicaCompositionError(
                "Composition manifest is not in one canonical release directory."
            )
        release_target = _canonical_release_target(root, relative.parts[0])
        expected = release_target / manifest_name
        if (
            manifest_path != expected.absolute()
            or manifest_path.resolve(strict=False) != expected.resolve(strict=False)
        ):
            raise PetReplicaCompositionError(
                "Composition manifest is not in one canonical release directory."
            )
        return release_target

    transactions = root / "work" / ".transactions"
    try:
        relative = manifest_path.relative_to(transactions)
    except ValueError as exc:
        raise PetReplicaCompositionError(
            "Composition manifest is not in a canonical release directory."
        ) from exc
    if (
        len(relative.parts) != 3
        or relative.parts[1] != "release"
        or relative.parts[2] != manifest_name
    ):
        raise PetReplicaCompositionError(
            "Composition manifest is not in the active transaction release directory."
        )
    transaction_id = _validate_release_id(relative.parts[0])
    release = transactions / transaction_id / "release"
    _require_inside(root, release, "active release directory")
    if release.exists():
        _require_regular_directory(release, "active release directory")
    return release


def _require_inside_release(release: Path, path: Path, label: str) -> None:
    try:
        _require_inside(release, path, label)
    except PetReplicaCompositionError as exc:
        raise PetReplicaCompositionError(
            f"{label} must stay inside its canonical release directory."
        ) from exc


def _validate_manifest(plan: PetReplicaPlan, manifest: ReplicaCompositionManifest) -> None:
    _validate_manifest_paths(manifest, plan=plan, require_outputs=True)
    if manifest.schema_version != COMPOSITION_SCHEMA_VERSION or manifest.project_id != plan.project_id:
        raise PetReplicaCompositionError("Composition manifest identity is invalid.")
    if (
        manifest.master_alias != "captioned_master_path"
        or manifest.master_path != manifest.captioned_master_path
        or manifest.clean_master_path == manifest.captioned_master_path
        or manifest.ffmpeg_command != manifest.captioned_master_command
    ):
        raise PetReplicaCompositionError(
            "Composition master compatibility alias is ambiguous."
        )
    start_s, end_s = replica_composition_window(plan, manifest.mode)
    if manifest.start_s != start_s or manifest.end_s != end_s or manifest.duration_s != end_s - start_s:
        raise PetReplicaCompositionError("Composition manifest timing is invalid.")
    _validate_current_ocr_evidence(plan, manifest)
    _preflight_subtitle_font(
        manifest.subtitle_font_path,
        manifest.subtitle_font_family,
        manifest.subtitle_font_sha256,
        manifest.subtitles,
        tools=_ffmpeg_tools(),
    )
    if _sha256(manifest.source_audio_path) != manifest.source_audio_sha256:
        raise PetReplicaCompositionError("Source AAC bytes changed after composition planning.")
    if _sha256(manifest.presentation_source_path) != manifest.presentation_source_sha256:
        raise PetReplicaCompositionError("Presentation source bytes changed after composition planning.")
    expected_comparison_duration = _comparison_policy_duration(
        manifest.presentation_source_path,
        start_s=manifest.start_s,
        requested_duration_s=manifest.duration_s,
        tools=_ffmpeg_tools(),
        fps=plan.fps,
    )
    if (
        manifest.comparison_tail_policy != "clamp_to_last_reference_video_frame"
        or abs(manifest.comparison_duration_s - expected_comparison_duration) > 1 / plan.fps
    ):
        raise PetReplicaCompositionError("Comparison tail policy is not bound to the reference video.")
    _reject_forbidden_commands(build_replica_ffmpeg_commands(manifest))


def _validate_current_ocr_evidence(
    plan: PetReplicaPlan,
    manifest: ReplicaCompositionManifest,
) -> None:
    _validate_ocr_evidence_bindings(
        manifest.output_root,
        manifest.ocr_evidence_bindings,
    )
    (
        evidence_bindings,
        reviewed_events,
        subtitles,
        safe_region,
    ) = _reviewed_subtitles(
        plan,
        manifest.start_s,
        manifest.end_s,
        validate_inputs=True,
        expected_annotations_sha256=manifest.reviewed_annotations_sha256,
    )
    if (
        manifest.ocr_evidence_bindings != evidence_bindings
        or manifest.reviewed_ocr_events != reviewed_events
        or manifest.subtitles != subtitles
        or manifest.caption_safe_region != safe_region
    ):
        raise PetReplicaCompositionError(
            "Composition OCR review evidence is stale or incomplete."
        )


@lru_cache(maxsize=1)
def _default_subtitle_font_binding() -> tuple[Path, str, str]:
    failures: list[str] = []
    for candidate, family in _CJK_FONT_CANDIDATES:
        path = candidate.expanduser().resolve(strict=False)
        try:
            _validate_subtitle_font_binding(
                path,
                family,
                _sha256(path),
                verify_hash=True,
            )
        except PetReplicaCompositionError as exc:
            failures.append(str(exc))
            continue
        return path, family, _sha256(path)
    detail = "; ".join(failures) or "no configured candidate exists"
    raise PetReplicaCompositionError(
        f"No approved readable CJK subtitle font is available: {detail}."
    )


def _validate_subtitle_font_binding(
    path: Path,
    family: str,
    expected_sha256: str,
    *,
    verify_hash: bool,
    require_approved: bool = False,
) -> None:
    path = Path(path).expanduser().absolute()
    _require_regular_no_symlink(path, "subtitle font")
    if not isinstance(family, str) or not family.strip():
        raise PetReplicaCompositionError("Subtitle font family is invalid.")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise PetReplicaCompositionError("Subtitle font SHA-256 is invalid.")
    if require_approved:
        approved = {
            (candidate.expanduser().resolve(strict=False), candidate_family)
            for candidate, candidate_family in _CJK_FONT_CANDIDATES
        }
        if (path.resolve(strict=False), family) not in approved:
            raise PetReplicaCompositionError(
                "Subtitle font is not an approved canonical CJK binding."
            )
    if verify_hash and _sha256(path) != expected_sha256:
        raise PetReplicaCompositionError("Subtitle font SHA-256 changed.")
    fc_query = shutil.which("fc-query")
    if not fc_query:
        raise PetReplicaCompositionError("fc-query is required for subtitle font validation.")
    try:
        completed = subprocess.run(
            [fc_query, "--format=%{family}\\n", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise PetReplicaCompositionError(
            f"Subtitle font family probe failed: {exc}."
        ) from exc
    families = {
        item.strip()
        for line in completed.stdout.splitlines()
        for item in line.split(",")
        if item.strip()
    }
    if family not in families:
        raise PetReplicaCompositionError(
            f"Subtitle font does not expose the bound family {family}."
        )


def _preflight_subtitle_font(
    font_path: Path,
    font_family: str,
    font_sha256: str,
    subtitles: Sequence[ReplicaSubtitle],
    *,
    tools: FFmpegTools,
) -> dict[str, Any]:
    _validate_subtitle_font_binding(
        font_path,
        font_family,
        font_sha256,
        verify_hash=True,
    )
    characters = "".join(
        sorted(
            {
                character
                for subtitle in subtitles
                for character in subtitle.text
                if not character.isspace()
            }
        )
    )
    if not characters:
        return {
            "path": str(font_path),
            "family": font_family,
            "sha256": font_sha256,
            "characters": "",
            "glyph_count": 0,
            "libass_preflight": True,
        }
    hb_shape = shutil.which("hb-shape")
    if not hb_shape:
        raise PetReplicaCompositionError(
            "hb-shape is required for subtitle glyph preflight."
        )
    try:
        shaped = subprocess.run(
            [hb_shape, "--output-format=json", str(font_path), characters],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
        )
        glyphs = json.loads(shaped.stdout)
    except (
        OSError,
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as exc:
        raise PetReplicaCompositionError(
            f"Subtitle glyph preflight failed: {exc}."
        ) from exc
    if (
        not isinstance(glyphs, list)
        or len(glyphs) != len(characters)
        or any(
            not isinstance(item, Mapping)
            or item.get("g") in {".notdef", "gid0", None}
            for item in glyphs
        )
    ):
        raise PetReplicaCompositionError(
            "Subtitle font has a missing glyph required by reviewed text."
        )

    with tempfile.TemporaryDirectory(prefix="replica-font-preflight-") as directory:
        root = Path(directory)
        ass_path = root / "preflight.ass"
        ass_path.write_text(
            "\ufeff"
            + _subtitle_document(
                (
                    ReplicaSubtitle(
                        event_id="FONT-PREFLIGHT",
                        shot_id="FONT-PREFLIGHT",
                        start_frame=0,
                        end_frame=6,
                        text=characters,
                        placement=ReplicaCaptionPlacement(
                            x=48,
                            y=940,
                            width=624,
                            height=180,
                            alignment="bottom_center",
                        ),
                    ),
                ),
                font_family,
            ),
            encoding="utf-8",
        )
        filter_graph = (
            f"ass='{_escape_filter_path(ass_path)}':"
            f"fontsdir='{_escape_filter_path(font_path.parent)}'"
        )
        completed = _run(
            [
                str(tools.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "verbose",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x303030:s=720x1280:r=30:d=0.2",
                "-vf",
                filter_graph,
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            timeout_s=_PROBE_TIMEOUT_S,
        )
    log = (completed.stderr or "").lower()
    forbidden = (
        "glyph 0x",
        "missing glyph",
        "failed to find any fallback",
        "error opening font",
        "failed to load font",
        "fontselect: failed",
    )
    if any(marker in log for marker in forbidden):
        raise PetReplicaCompositionError(
            "libass subtitle font preflight reported a missing glyph or font load error."
        )
    return {
        "path": str(font_path),
        "family": font_family,
        "sha256": font_sha256,
        "characters": characters,
        "glyph_count": len(glyphs),
        "libass_preflight": True,
    }


@lru_cache(maxsize=1)
def _ffmpeg_tools() -> FFmpegTools:
    ffmpeg = _FFMPEG_FULL.expanduser().resolve(strict=False)
    ffprobe = ffmpeg.with_name("ffprobe")
    if not ffmpeg.is_file() or not os.access(ffmpeg, os.X_OK):
        raise PetReplicaCompositionError("ffmpeg-full is required for replica composition.")
    if not ffprobe.is_file() or not os.access(ffprobe, os.X_OK):
        raise PetReplicaCompositionError("Matching ffprobe for ffmpeg-full is required.")
    completed = _run([str(ffmpeg), "-hide_banner", "-filters"], timeout_s=_PROBE_TIMEOUT_S)
    filters = frozenset(
        parts[1]
        for line in completed.stdout.splitlines()
        if len(parts := line.split()) >= 3 and parts[0].startswith(("T", ".", "S"))
    )
    missing = sorted(_REQUIRED_FILTERS - filters)
    if missing:
        raise PetReplicaCompositionError(
            f"ffmpeg-full lacks required replica filters: {', '.join(missing)}."
        )
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe, filters=filters)


def _probe_master(path: Path, tools: FFmpegTools) -> dict[str, Any]:
    _require_regular(path, "master")
    command = [
        str(tools.ffprobe), "-v", "error", "-count_frames", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,channels,nb_read_frames",
        "-of", "json", str(path),
    ]
    completed = _run(command, timeout_s=_PROBE_TIMEOUT_S)
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        video = [item for item in streams if item.get("codec_type") == "video"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        rate = str(video[0]["r_frame_rate"])
        numerator, denominator = rate.split("/", 1)
        fps = float(numerator) / float(denominator)
        return {
            "duration_s": float(payload["format"]["duration"]), "width": int(video[0]["width"]),
            "height": int(video[0]["height"]), "fps": fps, "video_streams": len(video),
            "audio_streams": len(audio), "video_codec": video[0].get("codec_name"),
            "audio_codec": audio[0].get("codec_name") if audio else None,
            "audio_channels": int(audio[0].get("channels", 0)) if audio else 0,
            "video_frames": int(video[0]["nb_read_frames"]),
        }
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError("Master ffprobe response is invalid.") from exc


def _validate_dual_master_media(
    picture_path: Path,
    clean_master_path: Path,
    captioned_master_path: Path,
    source_path: Path,
    *,
    subtitles: Sequence[ReplicaSubtitle],
    caption_safe_region: ReplicaCaptionSafeRegion,
    frame_proof: Mapping[str, Any],
    expected_duration_s: float,
    audio_mode: str,
    tools: FFmpegTools,
    fps: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    clean = _validate_master_variant(
        "clean",
        picture_path,
        clean_master_path,
        source_path,
        frame_proof=frame_proof,
        expected_duration_s=expected_duration_s,
        audio_mode=audio_mode,
        tools=tools,
        fps=fps,
        width=width,
        height=height,
    )
    clean_picture = _validate_clean_picture(
        picture_path,
        clean_master_path,
        tools,
    )
    clean = {
        **clean,
        "picture_preserved": True,
        "picture_proof": clean_picture,
    }
    captioned = _validate_master_variant(
        "captioned",
        picture_path,
        captioned_master_path,
        source_path,
        frame_proof=frame_proof,
        expected_duration_s=expected_duration_s,
        audio_mode=audio_mode,
        tools=tools,
        fps=fps,
        width=width,
        height=height,
    )
    caption_effects = _validate_caption_effects(
        picture_path,
        captioned_master_path,
        subtitles=subtitles,
        caption_safe_region=caption_safe_region,
        tools=tools,
        fps=fps,
    )
    captioned = {
        **captioned,
        "caption_effects": caption_effects,
    }
    return {"clean": clean, "captioned": captioned}


def _validate_master_variant(
    variant: str,
    picture_path: Path,
    master_path: Path,
    source_path: Path,
    *,
    frame_proof: Mapping[str, Any],
    expected_duration_s: float,
    audio_mode: str,
    tools: FFmpegTools,
    fps: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    probe = _probe_master(master_path, tools)
    tolerance = 1 / fps
    if probe["width"] != width or probe["height"] != height:
        raise PetReplicaCompositionError(
            f"{variant.title()} master dimensions do not match the replica contract."
        )
    if abs(probe["fps"] - fps) > 0.001:
        raise PetReplicaCompositionError(
            f"{variant.title()} master frame rate does not match the replica contract."
        )
    if abs(probe["duration_s"] - expected_duration_s) > tolerance:
        raise PetReplicaCompositionError(
            f"{variant.title()} master duration exceeds the one-frame tolerance."
        )
    if probe["video_streams"] != 1 or probe["audio_streams"] != 1:
        raise PetReplicaCompositionError(
            f"{variant.title()} master must contain one video and one audio stream."
        )
    if (
        probe["video_codec"] != "h264"
        or probe["audio_codec"] != "aac"
        or probe["audio_channels"] != 2
    ):
        raise PetReplicaCompositionError(
            f"{variant.title()} master codecs must be H.264 and AAC stereo."
        )
    if probe["video_frames"] != frame_proof["frame_count"]:
        raise PetReplicaCompositionError(
            f"{variant.title()} master frame count does not match the picture timeline."
        )
    boundaries = _prove_master_frame_boundaries(
        picture_path,
        master_path,
        frame_proof,
        tools,
        fps=fps,
    )
    black = _assert_no_black_segments(master_path, tools)
    freeze = _assert_no_unapproved_freeze(master_path, tools)
    audio = _verify_audio_against_source(
        source_path,
        master_path,
        tools,
        expected_duration_s=expected_duration_s,
        audio_mode=audio_mode,
        frame_tolerance_s=tolerance,
    )
    return {
        "valid": True,
        "variant": variant,
        "path": str(master_path),
        "sha256": _sha256(master_path),
        "duration_s": probe["duration_s"],
        "width": probe["width"],
        "height": probe["height"],
        "fps": probe["fps"],
        "video_frames": probe["video_frames"],
        "video_codec": probe["video_codec"],
        "audio_codec": probe["audio_codec"],
        "audio_channels": probe["audio_channels"],
        "frame_boundaries": boundaries,
        "audio": audio,
        "blackdetect": black,
        "freezedetect": freeze,
    }


def _validate_clean_picture(
    picture_path: Path,
    clean_master_path: Path,
    tools: FFmpegTools,
) -> dict[str, Any]:
    picture_hashes = _video_frame_hashes(picture_path, tools)
    clean_hashes = _video_frame_hashes(clean_master_path, tools)
    if picture_hashes != clean_hashes:
        raise PetReplicaCompositionError(
            "Clean master does not preserve the pre-subtitle picture."
        )
    return {
        "method": "decoded_rgb_framemd5_all_frames",
        "frame_count": len(picture_hashes),
        "picture_sha256": _sha256(picture_path),
        "clean_sha256": _sha256(clean_master_path),
    }


def _validate_caption_effects(
    picture_path: Path,
    captioned_master_path: Path,
    *,
    subtitles: Sequence[ReplicaSubtitle],
    caption_safe_region: ReplicaCaptionSafeRegion,
    tools: FFmpegTools,
    fps: int,
) -> dict[str, Any]:
    picture_hashes = _video_frame_hashes(picture_path, tools)
    captioned_hashes = _video_frame_hashes(captioned_master_path, tools)
    if len(picture_hashes) != len(captioned_hashes):
        raise PetReplicaCompositionError(
            "Captioned master has a different frame count from the picture."
        )
    raw_changed_frames = [
        index
        for index, (picture_hash, captioned_hash) in enumerate(
            zip(picture_hashes, captioned_hashes)
        )
        if picture_hash != captioned_hash
    ]
    metrics = _frame_difference_metrics(
        picture_path,
        captioned_master_path,
        tools,
    )
    if len(metrics) != len(picture_hashes):
        raise PetReplicaCompositionError(
            "Caption difference proof has an inconsistent frame count."
        )
    changed_frames = [
        frame
        for frame in raw_changed_frames
        if _is_meaningful_caption_difference(metrics[frame])
    ]
    codec_noise_frames = sorted(set(raw_changed_frames) - set(changed_frames))
    allowed_frames: set[int] = set()
    event_changed_frames: dict[str, list[int]] = {}
    for subtitle in subtitles:
        if (
            fps != 30
            or subtitle.start_frame < 0
            or subtitle.end_frame <= subtitle.start_frame
            or subtitle.end_frame > len(picture_hashes)
        ):
            raise PetReplicaCompositionError(
                "Caption subtitle frame window is invalid."
            )
        intended_frames = set(
            range(subtitle.start_frame, subtitle.end_frame)
        )
        allowed_frames.update(intended_frames)
        event_changed_frames[subtitle.event_id] = [
            frame
            for frame in changed_frames
            if frame in intended_frames
        ]
    changed_frame_set = set(changed_frames)
    outside_window = sorted(changed_frame_set - allowed_frames)
    missing_frames = sorted(allowed_frames - changed_frame_set)
    if outside_window or missing_frames:
        raise PetReplicaCompositionError(
            "Captioned master does not match the exact authoritative frame set."
        )
    outside_safe_region = _outside_safe_region_changed_frames(
        picture_path,
        captioned_master_path,
        caption_safe_region,
        tools,
    )
    if outside_safe_region:
        raise PetReplicaCompositionError(
            "Captioned master changes pixels outside the caption safe region."
        )
    return {
        "method": (
            "decoded_rgb_framemd5_with_signalstats_threshold_and_safe_region_crops"
        ),
        "raw_changed_frame_count": len(raw_changed_frames),
        "changed_frame_count": len(changed_frames),
        "changed_frames": changed_frames,
        "codec_noise_frames": codec_noise_frames,
        "outside_window_changed_frames": outside_window,
        "outside_safe_region_changed_frames": outside_safe_region,
        "events": [
            {
                "event_id": subtitle.event_id,
                "shot_id": subtitle.shot_id,
                "start_frame": subtitle.start_frame,
                "end_frame": subtitle.end_frame,
                "start_s": subtitle.start_s,
                "end_s": subtitle.end_s,
                "placement": asdict(subtitle.placement),
                "changed_frames": event_changed_frames[subtitle.event_id],
            }
            for subtitle in subtitles
        ],
    }


def _outside_safe_region_changed_frames(
    picture_path: Path,
    captioned_master_path: Path,
    safe_region: ReplicaCaptionSafeRegion,
    tools: FFmpegTools,
) -> list[int]:
    width = 720
    height = 1280
    crops = (
        (0, 0, width, safe_region.y),
        (
            0,
            safe_region.y + safe_region.height,
            width,
            height - safe_region.y - safe_region.height,
        ),
        (0, safe_region.y, safe_region.x, safe_region.height),
        (
            safe_region.x + safe_region.width,
            safe_region.y,
            width - safe_region.x - safe_region.width,
            safe_region.height,
        ),
    )
    changed: set[int] = set()
    for x, y, crop_width, crop_height in crops:
        if crop_width <= 0 or crop_height <= 0:
            continue
        video_filter = f"crop={crop_width}:{crop_height}:{x}:{y}"
        metrics = _frame_difference_metrics(
            picture_path,
            captioned_master_path,
            tools,
            video_filter=video_filter,
        )
        changed.update(
            index
            for index, metric in enumerate(metrics)
            if _is_meaningful_caption_difference(metric)
        )
    return sorted(changed)


def _is_meaningful_caption_difference(metric: tuple[float, float]) -> bool:
    yavg, ymax = metric
    return yavg >= _CAPTION_DIFF_YAVG_MIN and ymax >= _CAPTION_DIFF_YMAX_MIN


def _frame_difference_metrics(
    picture_path: Path,
    captioned_master_path: Path,
    tools: FFmpegTools,
    *,
    video_filter: str | None = None,
) -> list[tuple[float, float]]:
    if video_filter:
        filter_graph = (
            f"[0:v:0]{video_filter}[picture];"
            f"[1:v:0]{video_filter}[captioned];"
            "[picture][captioned]blend=all_mode=difference,signalstats,"
            "metadata=print:file=-"
        )
    else:
        filter_graph = (
            "[0:v:0][1:v:0]blend=all_mode=difference,signalstats,"
            "metadata=print:file=-"
        )
    completed = _run(
        [
            str(tools.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(picture_path),
            "-i",
            str(captioned_master_path),
            "-filter_complex",
            filter_graph,
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout_s=_COMMAND_TIMEOUT_S,
    )
    records: dict[int, dict[str, float]] = {}
    current: int | None = None
    for line in completed.stdout.splitlines():
        frame_match = re.match(r"frame:(\d+)\b", line)
        if frame_match:
            current = int(frame_match.group(1))
            records.setdefault(current, {})
            continue
        if current is None:
            continue
        for key in ("YAVG", "YMAX"):
            prefix = f"lavfi.signalstats.{key}="
            if line.startswith(prefix):
                try:
                    records[current][key] = float(line[len(prefix) :])
                except ValueError as exc:
                    raise PetReplicaCompositionError(
                        "Caption difference proof contains a non-numeric metric."
                    ) from exc
    if not records or sorted(records) != list(range(len(records))):
        raise PetReplicaCompositionError(
            "Caption difference proof does not cover a contiguous frame sequence."
        )
    try:
        metrics = [
            (records[index]["YAVG"], records[index]["YMAX"])
            for index in range(len(records))
        ]
    except KeyError as exc:
        raise PetReplicaCompositionError(
            "Caption difference proof is missing required signal statistics."
        ) from exc
    if any(
        not all(value >= 0 and math.isfinite(value) for value in metric)
        for metric in metrics
    ):
        raise PetReplicaCompositionError(
            "Caption difference proof contains an invalid metric."
        )
    return metrics


def _video_frame_hashes(
    path: Path,
    tools: FFmpegTools,
    *,
    video_filter: str | None = None,
) -> list[str]:
    filters = "format=rgb24"
    if video_filter:
        filters = f"{video_filter},{filters}"
    completed = _run(
        [
            str(tools.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            filters,
            "-an",
            "-c:v",
            "rawvideo",
            "-f",
            "framemd5",
            "-",
        ],
        timeout_s=_COMMAND_TIMEOUT_S,
    )
    hashes = [
        parts[-1].strip()
        for line in completed.stdout.splitlines()
        if line and not line.startswith("#") and len(parts := line.split(",")) >= 6
    ]
    if not hashes:
        raise PetReplicaCompositionError(
            "Master frame hash proof did not decode any video frames."
        )
    return hashes


def _comparison_policy_duration(
    reference_path: Path,
    *,
    start_s: float,
    requested_duration_s: float,
    tools: FFmpegTools,
    fps: int,
) -> float:
    completed = _run(
        [
            str(tools.ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=start_time,duration,nb_frames,avg_frame_rate", "-of", "json",
            str(reference_path),
        ],
        timeout_s=_PROBE_TIMEOUT_S,
    )
    try:
        stream = json.loads(completed.stdout)["streams"][0]
        stream_start = _as_float(stream.get("start_time"), 0.0)
        stream_duration = _as_float(stream.get("duration"), -1.0)
        if stream_duration <= 0:
            numerator, denominator = str(stream["avg_frame_rate"]).split("/", 1)
            stream_duration = int(stream["nb_frames"]) / (float(numerator) / float(denominator))
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError("Reference video duration probe is invalid.") from exc
    available = stream_start + stream_duration - start_s
    usable_frames = min(round(requested_duration_s * fps), int(available * fps + 1e-6))
    if usable_frames <= 0:
        raise PetReplicaCompositionError("Reference video has no usable comparison frames.")
    return usable_frames / fps


def _validate_comparison_master(
    source_path: Path,
    comparison_path: Path,
    *,
    expected_duration_s: float,
    tools: FFmpegTools,
    fps: int,
) -> dict[str, Any]:
    probe = _probe_master(comparison_path, tools)
    tolerance = 1 / fps
    if probe["width"] != 1440 or probe["height"] != 1280:
        raise PetReplicaCompositionError("Comparison dimensions must be 1440x1280.")
    if abs(probe["fps"] - fps) > 0.001:
        raise PetReplicaCompositionError("Comparison frame rate must be 30 fps.")
    if abs(probe["duration_s"] - expected_duration_s) > tolerance:
        raise PetReplicaCompositionError("Comparison duration exceeds the tail-policy tolerance.")
    if probe["video_streams"] != 1 or probe["audio_streams"] != 1:
        raise PetReplicaCompositionError("Comparison must contain one video and one source audio stream.")
    if (
        probe["video_codec"] != "h264"
        or probe["audio_codec"] != "aac"
        or probe["audio_channels"] != 2
    ):
        raise PetReplicaCompositionError("Comparison codecs must be H.264 and AAC stereo.")
    black = _assert_no_black_segments(comparison_path, tools)
    freeze = _assert_no_unapproved_freeze(comparison_path, tools)
    source_pcm = _decode_audio_pcm(source_path, tools, expected_duration_s)
    comparison_pcm = _decode_audio_pcm(comparison_path, tools, expected_duration_s)
    correlation = _pcm_correlation(source_pcm, comparison_pcm)
    if correlation < 0.98:
        raise PetReplicaCompositionError("Comparison audio does not match the source audio.")
    audio = _probe_audio(comparison_path, tools)
    if abs(audio["duration_s"] - expected_duration_s) > tolerance:
        raise PetReplicaCompositionError("Comparison source audio misses the tail-policy duration.")
    if abs(audio["start_s"]) > tolerance:
        raise PetReplicaCompositionError("Comparison source audio misses the tail-policy start.")
    return {
        "valid": True,
        "tail_policy": "clamp_to_last_reference_video_frame",
        "path": str(comparison_path),
        "sha256": _sha256(comparison_path),
        "duration_s": probe["duration_s"],
        "policy_duration_s": expected_duration_s,
        "width": probe["width"],
        "height": probe["height"],
        "fps": probe["fps"],
        "video_codec": probe["video_codec"],
        "audio_codec": probe["audio_codec"],
        "audio_channels": probe["audio_channels"],
        "audio_source_match": True,
        "audio_start_s": audio["start_s"],
        "audio_pcm_correlation": correlation,
        "blackdetect": black,
        "freezedetect": freeze,
    }


def _assert_no_black_segments(path: Path, tools: FFmpegTools) -> dict[str, Any]:
    completed = _run(
        [
            str(tools.ffmpeg), "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(path),
            "-vf", f"blackdetect=d=0.033:pix_th={_BLACK_PIXEL_THRESHOLD}:pic_th={_BLACK_PICTURE_RATIO}",
            "-an", "-f", "null", "-",
        ],
        timeout_s=_COMMAND_TIMEOUT_S,
    )
    if "black_start:" in (completed.stderr or ""):
        raise PetReplicaCompositionError("Master contains a black segment.")
    return {"pix_th": _BLACK_PIXEL_THRESHOLD, "pic_th": _BLACK_PICTURE_RATIO, "detected": False}


def _assert_no_unapproved_freeze(path: Path, tools: FFmpegTools) -> dict[str, Any]:
    completed = _run(
        [
            str(tools.ffmpeg), "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(path),
            "-vf", "freezedetect=n=0.001:d=0.366", "-an", "-f", "null", "-",
        ],
        timeout_s=_COMMAND_TIMEOUT_S,
    )
    if "freeze_start:" in (completed.stderr or ""):
        raise PetReplicaCompositionError("Master contains an unapproved freeze.")
    return {"detected": False, "duration_threshold_s": 0.366}


def _prove_no_direct_source_pixels(
    picture_path: Path,
    source_path: Path,
    shots: Sequence[ReplicaCompositionShot],
    *,
    start_s: float,
    duration_s: float,
    tools: FFmpegTools,
    fps: int,
) -> dict[str, Any]:
    picture_frame_count = _video_frame_count(picture_path, tools)
    last_frame_s = max(0.0, duration_s - 1 / fps)
    reference_video_duration_s = _comparison_policy_duration(
        source_path,
        start_s=start_s,
        requested_duration_s=duration_s,
        tools=tools,
        fps=fps,
    )
    source_last_frame_s = max(0.0, reference_video_duration_s - 1 / fps)
    requested: list[tuple[float, str, str]] = []
    for shot in shots:
        shot_start = max(0.0, shot.timeline_start_s)
        shot_end = min(duration_s, shot.timeline_end_s)
        shot_middle_frame = round(((shot_start + shot_end) / 2) * fps)
        requested.extend(
            (
                (shot_start, shot.shot_id, "shot_start"),
                (shot_middle_frame / fps, shot.shot_id, "shot_middle"),
                (shot_end - 1 / fps, shot.shot_id, "shot_end"),
            )
        )
    for shot in shots[:-1]:
        cut = shot.timeline_end_s
        requested.extend(
            (
                (cut - 1 / fps, shot.shot_id, "cut_before"),
                (cut, shot.shot_id, "cut_at"),
                (cut + 1 / fps, shot.shot_id, "cut_after"),
            )
        )

    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pet-replica-source-proof-") as directory:
        proof_root = Path(directory)
        for index, (requested_time, shot_id, kind) in enumerate(requested):
            timestamp = min(last_frame_s, max(0.0, requested_time))
            picture_frame_index = min(
                picture_frame_count - 1,
                max(0, round(timestamp * fps)),
            )
            timestamp = picture_frame_index / fps
            source_relative_timestamp = min(source_last_frame_s, timestamp)
            source_timestamp = start_s + source_relative_timestamp
            source_tail_clamped = source_relative_timestamp < timestamp
            picture = _frame_pixels_at_index(
                picture_path,
                picture_frame_index,
                proof_root / f"picture-{index}.png",
                tools,
            )
            source = _frame_pixels(
                source_path, source_timestamp, proof_root / f"source-{index}.png", tools
            )
            picture_bytes = picture.tobytes()
            source_bytes = source.tobytes()
            delta = _fingerprint_distance(
                _source_visual_fingerprint(picture), _source_visual_fingerprint(source)
            )
            item = {
                "timestamp_s": timestamp,
                "source_timestamp_s": source_timestamp,
                "source_tail_clamped": source_tail_clamped,
                "picture_frame_index": picture_frame_index,
                "shot_id": shot_id,
                "kind": kind,
                "picture_sha256": hashlib.sha256(picture_bytes).hexdigest(),
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "perceptual_delta": delta,
            }
            evidence.append(item)
            if picture_bytes == source_bytes or delta <= _SOURCE_COPY_DELTA_MAX:
                raise PetReplicaCompositionError(
                    f"Pre-subtitle picture contains a direct source-frame match at {timestamp:.6f}s."
                )
    return {
        "method": "shot_start_middle_end_and_cut_neighborhood",
        "threshold": _SOURCE_COPY_DELTA_MAX,
        "reference_video_duration_s": reference_video_duration_s,
        "tail_clamped_sample_count": sum(
            bool(item["source_tail_clamped"]) for item in evidence
        ),
        "samples": evidence,
    }


def _source_visual_fingerprint(image: Image.Image) -> bytes:
    resampling = getattr(Image, "Resampling", Image).BILINEAR
    return image.resize((24, 42), resampling).tobytes()


def _frame_pixels(video: Path, timestamp_s: float, output: Path, tools: FFmpegTools) -> Image.Image:
    _run([str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp_s:.9f}", "-i", str(video), "-frames:v", "1", str(output)])
    try:
        with Image.open(output) as image:
            return image.convert("RGB")
    except OSError as exc:
        raise PetReplicaCompositionError("Unable to decode master QC frame.") from exc


def _frame_sha256(video: Path, timestamp_s: float, output: Path, tools: FFmpegTools) -> str:
    image = _frame_pixels(video, timestamp_s, output, tools)
    return hashlib.sha256(image.tobytes()).hexdigest()


def _frame_sha256_at_index(
    video: Path,
    frame_index: int,
    output: Path,
    tools: FFmpegTools,
) -> str:
    image = _frame_pixels_at_index(video, frame_index, output, tools)
    return hashlib.sha256(image.tobytes()).hexdigest()


def _frame_pixels_at_index(
    video: Path,
    frame_index: int,
    output: Path,
    tools: FFmpegTools,
) -> Image.Image:
    if frame_index < 0:
        raise PetReplicaCompositionError("Frame index must not be negative.")
    _run(
        [
            str(tools.ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{frame_index})",
            "-frames:v",
            "1",
            str(output),
        ]
    )
    try:
        with Image.open(output) as image:
            return image.convert("RGB")
    except OSError as exc:
        raise PetReplicaCompositionError(
            "Unable to decode indexed master QC frame."
        ) from exc


def _video_frame_count(path: Path, tools: FFmpegTools) -> int:
    completed = _run(
        [
            str(tools.ffprobe), "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "json", str(path),
        ],
        timeout_s=_PROBE_TIMEOUT_S,
    )
    try:
        count = int(json.loads(completed.stdout)["streams"][0]["nb_read_frames"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError("Rendered video does not expose an exact frame count.") from exc
    if count <= 0:
        raise PetReplicaCompositionError("Rendered video contains no frames.")
    return count


def _prove_frame_sequence(
    expected_clips: Sequence[Path],
    picture_path: Path,
    tools: FFmpegTools,
    *,
    fps: int,
) -> dict[str, Any]:
    if not expected_clips:
        raise PetReplicaCompositionError("Frame sequence proof requires at least one selected clip.")
    clip_frames = tuple(_video_frame_count(path, tools) for path in expected_clips)
    frame_count = _video_frame_count(picture_path, tools)
    if frame_count != sum(clip_frames):
        raise PetReplicaCompositionError("Rendered frame sequence has the wrong total frame count.")
    cuts: list[dict[str, Any]] = []
    cursor = 0
    with tempfile.TemporaryDirectory(prefix="pet-replica-frame-proof-") as directory:
        proof_root = Path(directory)
        for index, (clip, count) in enumerate(zip(expected_clips[:-1], clip_frames[:-1])):
            cursor += count
            expected_before = _frame_sha256_at_index(
                clip,
                count - 1,
                proof_root / f"expected-before-{index}.png",
                tools,
            )
            expected_after = _frame_sha256_at_index(
                expected_clips[index + 1],
                0,
                proof_root / f"expected-after-{index}.png",
                tools,
            )
            matched: tuple[int, str, str] | None = None
            for offset in range(-2, 3):
                before_frame = cursor - 1 + offset
                after_frame = cursor + offset
                if before_frame < 0 or after_frame >= frame_count:
                    continue
                actual_before = _frame_sha256_at_index(
                    picture_path,
                    before_frame,
                    proof_root / f"actual-before-{index}-{offset}.png",
                    tools,
                )
                actual_after = _frame_sha256_at_index(
                    picture_path,
                    after_frame,
                    proof_root / f"actual-after-{index}-{offset}.png",
                    tools,
                )
                if actual_before == expected_before and actual_after == expected_after:
                    matched = (offset, actual_before, actual_after)
                    break
            if matched is None:
                raise PetReplicaCompositionError("Rendered frame sequence does not prove a planned cut within two frames.")
            offset, before_sha256, after_sha256 = matched
            cuts.append(
                {
                    "timestamp_s": (cursor + offset) / fps,
                    "offset_frames": offset,
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                }
            )
    return {
        "frame_count": frame_count,
        "clip_frame_counts": list(clip_frames),
        "cuts": cuts,
    }


def _bind_frame_proof_to_planned_cuts(
    frame_proof: Mapping[str, Any],
    shots: Sequence[ReplicaCompositionShot],
    *,
    fps: int,
) -> dict[str, Any]:
    clip_frame_counts = frame_proof.get("clip_frame_counts")
    cuts = frame_proof.get("cuts")
    if (
        not isinstance(clip_frame_counts, list)
        or len(clip_frame_counts) != len(shots)
        or not isinstance(cuts, list)
        or len(cuts) != max(0, len(shots) - 1)
    ):
        raise PetReplicaCompositionError("Rendered frame proof cannot be bound to the planned cuts.")

    bound_cuts: list[dict[str, Any]] = []
    for shot, measured in zip(shots[:-1], cuts):
        planned_frame = round(shot.timeline_end_s * fps)
        actual_frame = round(float(measured["timestamp_s"]) * fps)
        delta_frames = actual_frame - planned_frame
        if abs(delta_frames) > 2:
            raise PetReplicaCompositionError(
                f"Rendered cut after {shot.shot_id} exceeds the planned cut tolerance."
            )
        bound_cuts.append(
            {
                **measured,
                "planned_timestamp_s": shot.timeline_end_s,
                "actual_timestamp_s": actual_frame / fps,
                "planned_frame_index": planned_frame,
                "actual_frame_index": actual_frame,
                "delta_frames": delta_frames,
            }
        )

    for shot, actual_frames in zip(shots, clip_frame_counts):
        planned_frames = round(shot.editorial_duration_s * fps)
        if abs(int(actual_frames) - planned_frames) > 2:
            raise PetReplicaCompositionError(
                f"Rendered clip {shot.shot_id} exceeds the planned cut tolerance."
            )
    return {**frame_proof, "cuts": bound_cuts}


def _prove_master_frame_boundaries(
    picture_path: Path,
    master_path: Path,
    frame_proof: Mapping[str, Any],
    tools: FFmpegTools,
    *,
    fps: int,
) -> dict[str, Any]:
    picture_frames = _video_frame_count(picture_path, tools)
    master_frames = _video_frame_count(master_path, tools)
    if picture_frames != master_frames:
        raise PetReplicaCompositionError("Master frame boundaries have a different frame count than picture.")
    verified: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pet-replica-master-boundaries-") as directory:
        proof_root = Path(directory)
        for index, cut in enumerate(frame_proof["cuts"]):
            timestamp = float(cut["timestamp_s"])
            base_frame = round(timestamp * fps)
            if base_frame <= 0 or base_frame >= picture_frames:
                raise PetReplicaCompositionError(
                    "Rendered cut frame index is outside the picture timeline."
                )
            expected_before = _visual_fingerprint(
                _frame_pixels_at_index(
                    picture_path,
                    base_frame - 1,
                    proof_root / f"picture-before-{index}.png",
                    tools,
                )
            )
            expected_after = _visual_fingerprint(
                _frame_pixels_at_index(
                    picture_path,
                    base_frame,
                    proof_root / f"picture-after-{index}.png",
                    tools,
                )
            )
            matched: tuple[int, float, float] | None = None
            for offset in range(-2, 3):
                before_frame = base_frame - 1 + offset
                after_frame = base_frame + offset
                if before_frame < 0 or after_frame >= master_frames:
                    continue
                actual_before = _visual_fingerprint(
                    _frame_pixels_at_index(
                        master_path,
                        before_frame,
                        proof_root / f"master-before-{index}-{offset}.png",
                        tools,
                    )
                )
                actual_after = _visual_fingerprint(
                    _frame_pixels_at_index(
                        master_path,
                        after_frame,
                        proof_root / f"master-after-{index}-{offset}.png",
                        tools,
                    )
                )
                before_distance = _fingerprint_distance(expected_before, actual_before)
                after_distance = _fingerprint_distance(expected_after, actual_after)
                if before_distance <= 0.035 and after_distance <= 0.035:
                    matched = (offset, before_distance, after_distance)
                    break
            if matched is None:
                raise PetReplicaCompositionError("Master frame boundaries do not match the rendered cut timeline.")
            offset, before_distance, after_distance = matched
            verified.append(
                {
                    "timestamp_s": (base_frame + offset) / fps,
                    "offset_frames": offset,
                    "before_distance": before_distance,
                    "after_distance": after_distance,
                }
            )
    return {"frame_count": master_frames, "cut_count": len(verified), "cuts": verified}


def _visual_fingerprint(image: Image.Image) -> bytes:
    top = image.crop(
        (0, 0, image.width, min(image.height, _DEFAULT_CAPTION_SAFE_REGION.y))
    )
    resampling = getattr(Image, "Resampling", Image).BILINEAR
    return top.resize((24, 42), resampling).tobytes()


def _fingerprint_distance(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    return sum(abs(a - b) for a, b in zip(left, right)) / (255 * len(left))


def _verify_audio_against_source(
    source_path: Path,
    master_path: Path,
    tools: FFmpegTools,
    *,
    expected_duration_s: float,
    audio_mode: str,
    frame_tolerance_s: float = 1 / 30,
) -> dict[str, Any]:
    if audio_mode not in {"source_aac_stream_copy", "pcm_to_aac_192k_once"}:
        raise PetReplicaCompositionError("Replica master has an unknown audio mode.")
    source = _probe_audio(source_path, tools)
    master = _probe_audio(master_path, tools)
    if master["codec"] != "aac" or master["channels"] != 2:
        raise PetReplicaCompositionError("Master source audio is not AAC stereo.")
    if abs(master["duration_s"] - expected_duration_s) > frame_tolerance_s:
        raise PetReplicaCompositionError("Master source audio duration misses the presentation contract.")
    if abs(master["start_s"]) > frame_tolerance_s:
        raise PetReplicaCompositionError("Master source audio does not start on the presentation timeline.")
    source_pcm = _decode_audio_pcm(source_path, tools, expected_duration_s)
    master_pcm = _decode_audio_pcm(master_path, tools, expected_duration_s)
    correlation = _pcm_correlation(source_pcm, master_pcm)
    if correlation < 0.98:
        raise PetReplicaCompositionError("Master source audio content does not match the reviewed source audio.")
    if audio_mode == "source_aac_stream_copy" and source_pcm != master_pcm:
        raise PetReplicaCompositionError("AAC stream copy did not preserve source audio presentation; fallback is required.")
    bit_rate = master["bit_rate"]
    if audio_mode == "pcm_to_aac_192k_once" and bit_rate < 180_000:
        raise PetReplicaCompositionError("AAC fallback does not meet the 192 kbps contract.")
    return {
        "mode": audio_mode,
        "source_match": True,
        "fallback_used": audio_mode == "pcm_to_aac_192k_once",
        "duration_s": master["duration_s"],
        "presentation_start_s": master["start_s"],
        "bit_rate": bit_rate,
        "source_skip_samples": source["skip_samples"],
        "master_skip_samples": master["skip_samples"],
        "pcm_correlation": correlation,
    }


def _probe_audio(path: Path, tools: FFmpegTools) -> dict[str, Any]:
    completed = _run(
        [
            str(tools.ffprobe), "-v", "error", "-show_packets", "-show_entries",
            "format=duration:stream=codec_type,codec_name,channels,start_time,duration,bit_rate:"
            "packet=side_data_list", "-of", "json", str(path),
        ],
        timeout_s=_PROBE_TIMEOUT_S,
    )
    try:
        payload = json.loads(completed.stdout)
        audio = next(item for item in payload["streams"] if item.get("codec_type") == "audio")
        skip_samples = 0
        for packet in payload.get("packets", []):
            for side_data in packet.get("side_data_list", []):
                if side_data.get("side_data_type") == "Skip Samples":
                    skip_samples = max(skip_samples, int(side_data.get("skip_samples", 0)))
        return {
            "codec": audio.get("codec_name"),
            "channels": int(audio.get("channels", 0)),
            "duration_s": _as_float(audio.get("duration"), _as_float(payload["format"]["duration"], -1.0)),
            "start_s": _as_float(audio.get("start_time"), 0.0),
            "bit_rate": int(audio.get("bit_rate") or 0),
            "skip_samples": skip_samples,
        }
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PetReplicaCompositionError("Master source audio probe is invalid.") from exc


def _decode_audio_pcm(path: Path, tools: FFmpegTools, duration_s: float) -> bytes:
    command = [
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:a:0",
        "-t", f"{duration_s:.9f}", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        detail = (getattr(exc, "stderr", b"") or b"").decode("utf-8", "replace").strip()[-1200:]
        raise PetReplicaCompositionError(f"Unable to decode source audio evidence: {detail or exc}") from exc
    if not completed.stdout:
        raise PetReplicaCompositionError("Decoded source audio evidence is empty.")
    return completed.stdout


def _pcm_correlation(left: bytes, right: bytes) -> float:
    count = min(len(left), len(right)) // 2
    if count < 1600 or abs(len(left) - len(right)) > 3200:
        return -1.0
    left_values = memoryview(left[: count * 2]).cast("h")
    right_values = memoryview(right[: count * 2]).cast("h")
    dot = sum(int(a) * int(b) for a, b in zip(left_values, right_values))
    left_energy = sum(int(a) * int(a) for a in left_values)
    right_energy = sum(int(b) * int(b) for b in right_values)
    if not left_energy or not right_energy:
        return -1.0
    return dot / ((left_energy * right_energy) ** 0.5)


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _assert_subtitles_clean(manifest: ReplicaCompositionManifest) -> None:
    _require_regular(manifest.subtitle_path, "subtitle")
    try:
        contents = manifest.subtitle_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise PetReplicaCompositionError("Subtitle master is unavailable.") from exc
    if contents != _subtitle_document(
        manifest.subtitles,
        manifest.subtitle_font_family,
    ):
        raise PetReplicaCompositionError(
            "Subtitle master is not bound to the reviewed OCR events."
        )
    if _BRANDING_TEXT.search(contents):
        raise PetReplicaCompositionError("Subtitle master contains platform branding.")


def _reject_forbidden_commands(commands: Sequence[Sequence[str]]) -> None:
    joined = "\n".join(" ".join(command).lower() for command in commands)
    for forbidden in _FORBIDDEN_FILTERS:
        if forbidden in joined:
            raise PetReplicaCompositionError(f"Composition command contains forbidden filter: {forbidden}.")


def _require_file(root: Path, path: Path, label: str) -> str:
    path = Path(path).expanduser().absolute()
    _require_inside(root, path, label)
    _require_regular(path, label)
    return _sha256(path)


def _require_regular(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise PetReplicaCompositionError(f"{label} must be a regular file.")


def _require_regular_no_symlink(path: Path, label: str) -> None:
    candidate = Path(path).expanduser().absolute()
    if not candidate.is_file():
        raise PetReplicaCompositionError(f"{label} must be a regular file.")
    for current in (candidate, *candidate.parents):
        if current.is_symlink():
            raise PetReplicaCompositionError(f"{label} may not use symlinks.")


def _require_inside(root: Path, path: Path, label: str) -> None:
    root_absolute = Path(root).expanduser().absolute()
    candidate_absolute = Path(path).expanduser().absolute()
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise PetReplicaCompositionError(f"{label} must stay inside the output root.") from exc
    if root_absolute.is_symlink():
        raise PetReplicaCompositionError(f"{label} may not use symlinks.")
    current = root_absolute
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PetReplicaCompositionError(f"{label} may not use symlinks.")
    root_resolved = root_absolute.resolve(strict=False)
    candidate_resolved = candidate_absolute.resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PetReplicaCompositionError(f"{label} must stay inside the output root.") from exc


def _output_root(plan: PetReplicaPlan) -> Path:
    root = plan.output_root.expanduser().absolute()
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise PetReplicaCompositionError("Replica output root must be a regular directory.")
    return root.resolve(strict=False)


def _ass_frame_time(frame_index: int, fps: int = 30) -> str:
    if (
        isinstance(frame_index, bool)
        or not isinstance(frame_index, int)
        or frame_index < 0
        or fps != 30
    ):
        raise PetReplicaCompositionError(
            "ASS subtitle frame boundary is invalid."
        )
    hundredths = frame_index * 100 // fps
    hours, remainder = divmod(hundredths, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def _ffconcat_escape(path: Path) -> str:
    return str(path).replace("'", r"'\\''")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PetReplicaCompositionError(f"Unable to hash artifact: {path}") from exc
    return digest.hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _json_normalized(payload: Any) -> Any:
    return json.loads(_canonical_json_bytes(payload))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_bytes(path, _canonical_json_bytes(payload))


def _write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(contents)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(contents, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_payload(manifest: ReplicaCompositionManifest) -> dict[str, Any]:
    payload = asdict(manifest)
    for key in (
        "output_root", "manifest_path", "source_audio_path", "presentation_source_path",
        "subtitle_path", "concat_list_path", "picture_path", "master_path", "side_by_side_path",
        "clean_master_path", "captioned_master_path", "final_qc_path", "current_pointer_path",
        "reviewed_annotations_snapshot_path", "subtitle_font_path",
    ):
        payload[key] = str(payload[key])
    payload["ocr_evidence_snapshot_paths"] = [
        str(path) for path in manifest.ocr_evidence_snapshot_paths
    ]
    payload["shots"] = [
        {**asdict(shot), "source_path": str(shot.source_path), "normalized_path": str(shot.normalized_path)}
        for shot in manifest.shots
    ]
    payload["ocr_evidence_bindings"] = [
        asdict(binding) for binding in manifest.ocr_evidence_bindings
    ]
    payload["ffmpeg_command"] = list(manifest.ffmpeg_command)
    payload["clean_master_command"] = list(manifest.clean_master_command)
    payload["captioned_master_command"] = list(manifest.captioned_master_command)
    payload["side_by_side_command"] = list(manifest.side_by_side_command)
    return _json_normalized(payload)


def _run(
    command: Sequence[str], *, timeout_s: int = _COMMAND_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        detail = str(getattr(exc, "stderr", "") or getattr(exc, "stdout", "") or exc).strip()[-1200:]
        raise PetReplicaCompositionError(f"Composition command failed: {detail}") from exc
