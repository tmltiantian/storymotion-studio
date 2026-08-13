from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .gateway_endpoint import gateway_endpoint_fingerprint
from .gateway_video import GatewayVideoClient, GatewayVideoConfig, is_valid_mp4_file
from .gateway_video_batch import render_gateway_video_single
from .pet_replica import PetReplicaPlan, ReplicaShot, validate_pet_replica_plan
from .pet_replica_assets import ReplicaAssetManifest
from .pet_replica_audio import ReplicaAudioManifest
from .pet_replica_reference import ReplicaShotAnnotation


VIDEO_MODEL = "doubao-seedance-2-0"
VIDEO_RESOLUTION = "720p"
VIDEO_RATIO = "9:16"
MAX_CANDIDATES = 3
_SUPPORTED_PROVIDER_FRAME_RATES = (24.0, 25.0, 30.0)
GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION = (
    "motion-comic-factory.pet-replica-gateway-drive-audio.v1"
)
PROVIDER_REFERENCE_POLICY_VERSION = (
    "motion-comic-factory.pet-replica-provider-references.v2"
)
_PROVIDER_REFERENCE_RELATIVE_PATHS = frozenset(
    {
        "assets/characters/奶糖_reference.png",
        "assets/characters/豆包_reference.png",
        "assets/scenes/scene_sofa.png",
        "assets/scenes/scene_table.png",
        "assets/scenes/scene_phone.png",
    }
)
_SCHEMA_VERSION = "motion-comic-factory.pet-replica-generation.v1"
_SELECTION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-selection.v1"
_CREDENTIAL_KEY_PATTERN = (
    r"(?:access[_-]?token|refresh[_-]?token|x[_-]?api[_-]?key|api[_-]?key|"
    r"authorization|set[_-]?cookie|cookie|signature|credential|secret|password|token|key)"
)
_SECRET = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]+", re.I)
_QUERY_SECRET = re.compile(rf"(?P<prefix>[?&]{_CREDENTIAL_KEY_PATTERN}=)[^&#\s]*", re.I)
_QUOTED_SECRET_VALUE = re.compile(
    rf"(?P<prefix>[\"']?{_CREDENTIAL_KEY_PATTERN}[\"']?\s*(?::|=)\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.I,
)
_UNQUOTED_SECRET_VALUE = re.compile(
    rf"(?P<prefix>[\"']?{_CREDENTIAL_KEY_PATTERN}[\"']?\s*(?::|=)\s*)[^\r\n,;}}\]]+",
    re.I,
)
_TASK_ID = re.compile(
    r"[\"']?task[_-]?id[\"']?\s*(?::|=)\s*[\"']?([A-Za-z0-9_.-]+)", re.I
)
_UNICODE_ESCAPE = re.compile(r"(?<!\\)\\u([0-9A-Fa-f]{4})")
_JAVASCRIPT_HEX_ESCAPE = re.compile(r"(?<!\\)\\x([0-9A-Fa-f]{2})")
_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_MAX_MALFORMED_ESCAPE_PASSES = 4


class PetReplicaGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicaShotJob:
    shot_id: str
    index: int
    start_s: float
    end_s: float
    editorial_duration_s: float
    generation_duration_s: int
    candidate_number: int
    model: str
    resolution: str
    ratio: str
    prompt: str
    negative_contract: str
    reference_images: tuple[Path, ...]
    composition_path: Path
    audio_path: Path | None
    speaker_visible: bool
    generate_audio: bool
    output_path: Path
    gateway_report_path: Path


@dataclass(frozen=True)
class ReplicaCandidate:
    shot_id: str
    candidate_number: int
    video_path: Path
    provenance_path: Path
    gateway_report_path: Path
    editorial_duration_s: float
    generation_duration_s: int
    output_sha256: str


@dataclass(frozen=True)
class ReplicaCandidateVideoProbe:
    duration_s: float
    width: int
    height: int
    fps: float


@dataclass(frozen=True)
class _PromotionBackup:
    target: Path
    backup: Path
    original_sha256: str | None
    absence_marker_sha256: str | None


def build_replica_shot_jobs(
    plan: PetReplicaPlan,
    annotations: Sequence[ReplicaShotAnnotation],
    assets: ReplicaAssetManifest,
    audio: ReplicaAudioManifest,
    pilot_only: bool,
    shot_ids: Sequence[str] | None = None,
    candidate_number: int = 1,
    postprocess_lipsync: bool = False,
) -> tuple[ReplicaShotJob, ...]:
    """Compile source-controlled generation jobs without calling the gateway."""
    validate_pet_replica_plan(plan)
    _candidate_number(candidate_number)
    root = _output_root(plan)
    _validate_annotations(plan, annotations)
    _validate_assets(plan, assets, root)
    _validate_audio(plan, audio, root)
    requested = set(shot_ids or ())
    known_ids = {shot.shot_id for shot in plan.shots}
    unknown = requested - known_ids
    if unknown:
        raise PetReplicaGenerationError(f"Unknown replica shot IDs: {sorted(unknown)}")
    if shot_ids is not None and not requested:
        raise PetReplicaGenerationError("Shot ID selection must not be empty.")

    annotations_by_id = {annotation.shot_id: annotation for annotation in annotations}
    jobs: list[ReplicaShotJob] = []
    for shot in plan.shots:
        # A source shot that merely overlaps the pilot is generated whole; editorial
        # trimming is intentionally deferred to composition.
        if pilot_only and shot.start_s >= plan.pilot_end_s:
            continue
        if requested and shot.shot_id not in requested:
            continue
        if shot.duration_s > 15:
            raise PetReplicaGenerationError(
                f"{shot.shot_id} is longer than the provider's 15-second limit."
            )
        annotation = annotations_by_id[shot.shot_id]
        composition = _composition_path(root, shot.shot_id)
        _regular_inside(root, composition, "Composition evidence")
        references = _ordered_references(root, shot, annotation, assets, composition)
        audio_path = _drive_audio_for_annotation(root, shot, annotation, audio)
        speaker_visible = (
            bool(annotation.speaker) and annotation.speaker in annotation.characters
        )
        use_postprocess_lipsync = bool(audio_path is not None and postprocess_lipsync)
        generate_audio = bool(audio_path is not None and not use_postprocess_lipsync)
        duration = min(15, max(4, math.ceil(shot.duration_s)))
        prompt, negative = _prompt(
            shot,
            annotation,
            duration,
            voice_present=generate_audio,
            speaker_visible=speaker_visible,
            postprocess_lipsync=use_postprocess_lipsync,
        )
        candidate_root = _safe_path(
            root, root / "shots" / shot.shot_id / f"candidate_{candidate_number:02d}"
        )
        jobs.append(
            ReplicaShotJob(
                shot_id=shot.shot_id,
                index=shot.index,
                start_s=shot.start_s,
                end_s=shot.end_s,
                editorial_duration_s=shot.duration_s,
                generation_duration_s=duration,
                candidate_number=candidate_number,
                model=VIDEO_MODEL,
                resolution=VIDEO_RESOLUTION,
                ratio=VIDEO_RATIO,
                prompt=prompt,
                negative_contract=negative,
                reference_images=references,
                composition_path=composition,
                audio_path=audio_path,
                speaker_visible=speaker_visible,
                generate_audio=generate_audio,
                output_path=candidate_root.with_suffix(".mp4"),
                gateway_report_path=candidate_root.with_suffix(".gateway.json"),
            )
        )
    if not jobs:
        raise PetReplicaGenerationError("No replica generation jobs were selected.")
    return tuple(jobs)


def generate_replica_candidates(
    plan: PetReplicaPlan,
    jobs: Sequence[ReplicaShotJob],
    config: GatewayVideoConfig | GatewayVideoClient,
    enable_live: bool,
    replace_stale: bool,
) -> tuple[ReplicaCandidate, ...]:
    """Render jobs transactionally. Disabled runs never instantiate or call a client."""
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    if not jobs:
        raise PetReplicaGenerationError("Replica generation needs at least one job.")
    _validate_jobs(plan, jobs, root)
    if not enable_live:
        return ()
    client = (
        config
        if isinstance(config, GatewayVideoClient)
        else GatewayVideoClient(_video_config(config))
    )
    if client.config.model != VIDEO_MODEL:
        raise PetReplicaGenerationError(f"Replica video model must be {VIDEO_MODEL}.")
    candidates: list[ReplicaCandidate] = []
    for job in jobs:
        candidates.append(_generate_one(plan, root, job, client, replace_stale))
    return tuple(candidates)


def select_replica_candidate(
    plan: PetReplicaPlan,
    shot_id: str,
    candidate_number: int,
    manual_review_note: str,
) -> Path:
    """Record a human choice only; Task 6 performs quality approval."""
    validate_pet_replica_plan(plan)
    _candidate_number(candidate_number)
    root = _output_root(plan)
    if shot_id not in {shot.shot_id for shot in plan.shots}:
        raise PetReplicaGenerationError(f"Unknown replica shot: {shot_id}")
    note = str(manual_review_note).strip()
    if not note:
        raise PetReplicaGenerationError(
            "Candidate selection requires a manual review note."
        )
    video = _safe_path(
        root, root / "shots" / shot_id / f"candidate_{candidate_number:02d}.mp4"
    )
    provenance = video.with_suffix(".provenance.json")
    _regular_inside(root, video, "Candidate video")
    _regular_inside(root, provenance, "Candidate provenance")
    selection = _safe_path(root, root / "shots" / shot_id / "selection.json")
    _write_json_atomic(
        selection,
        {
            "schema_version": _SELECTION_SCHEMA_VERSION,
            "shot_id": shot_id,
            "candidate_number": candidate_number,
            "candidate_path": str(video.relative_to(root)),
            "candidate_sha256": _sha256(video),
            "manual_review_note": _redact(note),
            "quality_approved": False,
            "quality_approval_stage": "task_6_required",
        },
    )
    return selection


def _generate_one(
    plan: PetReplicaPlan,
    root: Path,
    job: ReplicaShotJob,
    client: GatewayVideoClient,
    replace_stale: bool,
) -> ReplicaCandidate:
    provenance_path = job.output_path.with_suffix(".provenance.json")
    signature = _job_signature(plan, job, client)
    existing = _existing_candidate(root, job, provenance_path, signature)
    if existing is not None:
        return existing
    if (
        job.output_path.exists()
        or provenance_path.exists()
        or job.gateway_report_path.exists()
    ):
        if not replace_stale:
            raise PetReplicaGenerationError(
                f"{job.shot_id} candidate state is stale; pass replace_stale=True to replace it."
            )
    for path in (job.output_path, provenance_path, job.gateway_report_path):
        _safe_path(root, path)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(root, job.output_path.parent)
    staged_video = _staging_path(job.output_path)
    staged_report = _staging_path(job.gateway_report_path)
    staged_provenance = _staging_path(provenance_path)
    staging_artifacts = _staging_artifacts(
        staged_video,
        staged_report,
        staged_provenance,
    )
    backups: list[_PromotionBackup] = []
    promoted: list[Path] = []
    try:
        _remove_staging_files(root, *staging_artifacts)
        gateway_audio = _prepare_gateway_drive_audio(root, job)
        report = render_gateway_video_single(
            job.prompt,
            staged_video,
            client,
            staged_report,
            images=job.reference_images,
            audio=gateway_audio,
            duration=job.generation_duration_s,
            ratio=job.ratio,
            resolution=job.resolution,
            generate_audio=job.generate_audio,
            allow_network=True,
            replace_stale=False,
            report_sanitizer=_sanitize_report,
        )
        _require_completed_gateway_report(report)
        _validate_staged_candidate(root, staged_video, job)
        if not staged_report.is_file() or staged_report.is_symlink():
            raise PetReplicaGenerationError("Gateway did not write a candidate report.")
        _require_completed_gateway_report(_read_report(staged_report))
        _sanitize_report_file(staged_report)
        provenance = _provenance(
            plan, root, job, client, report, signature, staged_video
        )
        _write_json_atomic(staged_provenance, provenance)
        targets = (job.output_path, job.gateway_report_path, provenance_path)
        _create_promotion_backups(root, targets, backups)
        _promote_staged_artifacts(
            root,
            (staged_video, staged_report, staged_provenance),
            targets,
            promoted,
        )
        output_sha256 = _sha256(job.output_path)
        _remove_staging_files(root, *staging_artifacts)
        _cleanup_backups_best_effort(root, backups)
        return ReplicaCandidate(
            shot_id=job.shot_id,
            candidate_number=job.candidate_number,
            video_path=job.output_path,
            provenance_path=provenance_path,
            gateway_report_path=job.gateway_report_path,
            editorial_duration_s=job.editorial_duration_s,
            generation_duration_s=job.generation_duration_s,
            output_sha256=output_sha256,
        )
    except Exception as exc:
        archive_errors: tuple[str, ...] = ()
        try:
            _attempt, archive_errors = _archive_failed_attempt(
                root,
                job,
                signature,
                staged_report,
                staged_video.with_suffix(staged_video.suffix + ".gateway.json"),
                staged_video,
                exc,
            )
        except Exception as archive_exc:
            archive_errors = (
                f"failure record write failed: {_redact(str(archive_exc))}",
            )
        cleanup_errors = _cleanup_staging_best_effort(root, *staging_artifacts)
        rollback_errors = _rollback_promoted_artifacts(root, backups, promoted)
        message = f"Replica candidate generation failed: {_redact(str(exc))}"
        if archive_errors:
            message += f"; diagnostic archive errors: {' | '.join(archive_errors)}"
        if cleanup_errors:
            message += f"; staging cleanup errors: {' | '.join(cleanup_errors)}"
        if rollback_errors:
            message += f"; rollback errors: {' | '.join(rollback_errors)}"
        raise PetReplicaGenerationError(message) from exc


def _existing_candidate(
    root: Path, job: ReplicaShotJob, provenance_path: Path, signature: Mapping[str, Any]
) -> ReplicaCandidate | None:
    if not job.output_path.exists() and not provenance_path.exists():
        return None
    if not (
        job.output_path.is_file()
        and provenance_path.is_file()
        and job.gateway_report_path.is_file()
    ):
        return None
    _regular_inside(root, job.output_path, "Candidate video")
    _regular_inside(root, provenance_path, "Candidate provenance")
    _regular_inside(root, job.gateway_report_path, "Gateway candidate report")
    try:
        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("signature") != signature or payload.get("output_sha256") != _sha256(
        job.output_path
    ):
        return None
    return ReplicaCandidate(
        job.shot_id,
        job.candidate_number,
        job.output_path,
        provenance_path,
        job.gateway_report_path,
        job.editorial_duration_s,
        job.generation_duration_s,
        _sha256(job.output_path),
    )


def _video_config(config: GatewayVideoConfig) -> GatewayVideoConfig:
    if not isinstance(config, GatewayVideoConfig):
        raise PetReplicaGenerationError(
            "Replica generation requires GatewayVideoConfig or GatewayVideoClient."
        )
    if config.model != VIDEO_MODEL:
        raise PetReplicaGenerationError(f"Replica video model must be {VIDEO_MODEL}.")
    return config


def _validate_jobs(
    plan: PetReplicaPlan, jobs: Sequence[ReplicaShotJob], root: Path
) -> None:
    seen: set[tuple[str, int]] = set()
    source_hash = _sha256(plan.source_video)
    for job in jobs:
        _candidate_number(job.candidate_number)
        if (job.shot_id, job.candidate_number) in seen:
            raise PetReplicaGenerationError(
                "Replica generation jobs may not duplicate a candidate."
            )
        seen.add((job.shot_id, job.candidate_number))
        shot = next((item for item in plan.shots if item.shot_id == job.shot_id), None)
        if shot is None or job.editorial_duration_s != shot.duration_s:
            raise PetReplicaGenerationError(
                "Replica job editorial duration no longer matches its source shot."
            )
        expected = min(15, max(4, math.ceil(job.editorial_duration_s)))
        if job.generation_duration_s != expected or job.generation_duration_s > 15:
            raise PetReplicaGenerationError("Replica job provider duration is invalid.")
        if (job.model, job.resolution, job.ratio) != (
            VIDEO_MODEL,
            VIDEO_RESOLUTION,
            VIDEO_RATIO,
        ):
            raise PetReplicaGenerationError(
                "Replica job violates the locked video generation contract."
            )
        if job.generate_audio and job.audio_path is None:
            raise PetReplicaGenerationError(
                "Replica native audio requires a reference-audio binding."
            )
        _regular_inside(root, job.composition_path, "Composition evidence")
        if not job.reference_images or job.composition_path in job.reference_images:
            raise PetReplicaGenerationError(
                "Raw source composition evidence may not be sent to the provider."
            )
        for image in job.reference_images:
            _regular_inside(root, image, "Replica reference image")
            if image.relative_to(root).as_posix() not in _PROVIDER_REFERENCE_RELATIVE_PATHS:
                raise PetReplicaGenerationError(
                    "Replica provider references may contain only approved cats and scenes."
                )
        if job.audio_path is not None:
            _regular_inside(root, job.audio_path, "Drive audio")
        _safe_path(root, job.output_path)
        _safe_path(root, job.gateway_report_path)
    if not source_hash:
        raise PetReplicaGenerationError("Reference source hash is unavailable.")


def _validate_annotations(
    plan: PetReplicaPlan, annotations: Sequence[ReplicaShotAnnotation]
) -> None:
    if len(annotations) != len(plan.shots):
        raise PetReplicaGenerationError(
            "Replica generation requires reviewed annotations for every shot."
        )
    for shot, annotation in zip(plan.shots, annotations):
        if annotation.shot_id != shot.shot_id or annotation.manual_review_required:
            raise PetReplicaGenerationError(
                "Replica generation requires reviewed annotations in source order."
            )
        if not annotation.framing.strip() or not annotation.action.strip():
            raise PetReplicaGenerationError(
                f"{shot.shot_id} requires framing and action annotations."
            )
        if annotation.speaker and annotation.speaker not in plan.characters:
            raise PetReplicaGenerationError(f"{shot.shot_id} has an unknown speaker.")
        if annotation.scene_anchor_id not in {
            "scene_sofa",
            "scene_table",
            "scene_phone",
        }:
            raise PetReplicaGenerationError(
                f"{shot.shot_id} has an invalid scene anchor."
            )


def _validate_assets(
    plan: PetReplicaPlan, assets: ReplicaAssetManifest, root: Path
) -> None:
    if assets.source_sha256 != _sha256(plan.source_video):
        raise PetReplicaGenerationError(
            "Approved asset manifest source hash does not match the reference."
        )
    if assets.output_root != root or not assets.live_generation_enabled:
        raise PetReplicaGenerationError(
            "Replica assets must be approved generated assets."
        )
    required = {
        "woman_front",
        "woman_half_body",
        "naitang_reference",
        "doubao_reference",
        "scene_sofa",
        "scene_table",
        "scene_phone",
    }
    actual = {asset.asset_id for asset in assets.assets}
    if not required <= actual:
        raise PetReplicaGenerationError(
            "Approved assets are missing required identity or scene anchors."
        )
    for asset in assets.assets:
        _regular_inside(root, asset.path, "Approved asset")
        if asset.sha256 != _sha256(asset.path):
            raise PetReplicaGenerationError(
                "Approved asset hash does not match its manifest."
            )


def _validate_audio(
    plan: PetReplicaPlan, audio: ReplicaAudioManifest, root: Path
) -> None:
    if audio.source_sha256 != _sha256(plan.source_video):
        raise PetReplicaGenerationError(
            "Drive audio manifest source hash does not match the reference."
        )
    if set(audio.shots) != {shot.shot_id for shot in plan.shots}:
        raise PetReplicaGenerationError(
            "Drive audio manifest must contain every source shot."
        )
    for shot in plan.shots:
        asset = audio.shots[shot.shot_id]
        if (
            asset.sample_rate != 48000
            or asset.channels != 2
            or asset.codec != "pcm_s16le"
        ):
            raise PetReplicaGenerationError("Drive audio must be 48kHz stereo PCM WAV.")
        if asset.source_start_s != shot.start_s or asset.source_end_s != shot.end_s:
            raise PetReplicaGenerationError(
                "Drive audio source window does not match the source shot."
            )
        _regular_inside(root, asset.path, "Drive audio")
        if asset.sha256 != _sha256(asset.path):
            raise PetReplicaGenerationError(
                "Drive audio hash does not match its manifest."
            )


def _ordered_references(
    root: Path,
    shot: ReplicaShot,
    annotation: ReplicaShotAnnotation,
    assets: ReplicaAssetManifest,
    composition: Path,
) -> tuple[Path, ...]:
    by_id = {asset.asset_id: asset for asset in assets.assets}
    anchors: list[Path] = []
    for role in annotation.characters:
        asset_id = {
            "source_woman": None,
            "source_orange_cat": "naitang_reference",
            "source_tabby_cat": "doubao_reference",
        }.get(role)
        if role == "source_woman":
            continue
        if asset_id is None:
            raise PetReplicaGenerationError(
                f"{shot.shot_id} has an unsupported character role: {role}"
            )
        anchors.append(by_id[asset_id].path)
    scene = by_id[annotation.scene_anchor_id].path
    ordered = tuple(dict.fromkeys([*anchors, scene]))
    if composition in ordered:
        raise PetReplicaGenerationError(
            "Raw source composition evidence may not be sent to the provider."
        )
    return ordered


def _drive_audio_for_annotation(
    root: Path,
    shot: ReplicaShot,
    annotation: ReplicaShotAnnotation,
    audio: ReplicaAudioManifest,
) -> Path | None:
    if not annotation.speaker:
        return None
    if not annotation.source_audio:
        raise PetReplicaGenerationError(
            f"{shot.shot_id} speaking shot requires source audio."
        )
    path = audio.shots[shot.shot_id].path
    _regular_inside(root, path, "Drive audio")
    return path


def _prepare_gateway_drive_audio(
    root: Path,
    job: ReplicaShotJob,
) -> Path | None:
    source = job.audio_path
    if source is None:
        if job.generate_audio:
            raise PetReplicaGenerationError(
                "Replica native audio requires reference audio."
            )
        return None
    if not job.generate_audio:
        return None
    _regular_inside(root, source, "Drive audio")
    output = _safe_path(
        root,
        root
        / "audio"
        / "transport"
        / f"{job.shot_id}_{job.generation_duration_s}s.wav",
    )
    state_path = _safe_path(root, output.with_suffix(".state.json"))
    signature = {
        "schema_version": GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
        "shot_id": job.shot_id,
        "source_path": str(source.relative_to(root)),
        "source_sha256": _sha256(source),
        "generation_duration_s": job.generation_duration_s,
        "sample_rate": 48_000,
        "channels": 2,
        "sample_width_bytes": 2,
        "padding": "trailing_silence_only",
        "retiming": False,
    }
    if _gateway_drive_audio_is_current(
        root,
        output,
        state_path,
        signature,
        job,
    ):
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(root, output.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".wav",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        target_frames = job.generation_duration_s * 48_000
        with wave.open(str(source), "rb") as opened:
            if (
                opened.getnchannels() != 2
                or opened.getsampwidth() != 2
                or opened.getframerate() != 48_000
                or opened.getcomptype() != "NONE"
            ):
                raise PetReplicaGenerationError(
                    "Replica drive audio must be uncompressed 48kHz stereo 16-bit PCM."
                )
            source_frames = opened.readframes(target_frames)
        frame_size = 2 * 2
        if len(source_frames) % frame_size:
            raise PetReplicaGenerationError(
                "Replica drive audio ended inside a PCM sample frame."
            )
        actual_frames = len(source_frames) // frame_size
        rendered = source_frames + bytes(
            max(0, target_frames - actual_frames) * frame_size
        )
        with wave.open(str(temporary), "wb") as encoded:
            encoded.setnchannels(2)
            encoded.setsampwidth(2)
            encoded.setframerate(48_000)
            encoded.writeframes(rendered)
        _validate_gateway_drive_audio(temporary, job.generation_duration_s)
        os.replace(temporary, output)
        _write_json_atomic(
            state_path,
            {
                **signature,
                "output_path": str(output.relative_to(root)),
                "output_sha256": _sha256(output),
            },
        )
    except (OSError, EOFError, wave.Error) as exc:
        raise PetReplicaGenerationError(
            "Unable to prepare gateway drive audio."
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _gateway_drive_audio_is_current(
    root: Path,
    output: Path,
    state_path: Path,
    signature: Mapping[str, Any],
    job: ReplicaShotJob,
) -> bool:
    if not output.is_file() or output.is_symlink():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        _validate_gateway_drive_audio(output, job.generation_duration_s)
    except (OSError, EOFError, ValueError, wave.Error, json.JSONDecodeError):
        return False
    return bool(
        isinstance(state, Mapping)
        and all(state.get(key) == value for key, value in signature.items())
        and state.get("output_path") == str(output.relative_to(root))
        and state.get("output_sha256") == _sha256(output)
    )


def _validate_gateway_drive_audio(path: Path, duration_s: int) -> None:
    with wave.open(str(path), "rb") as opened:
        if (
            opened.getnchannels() != 2
            or opened.getsampwidth() != 2
            or opened.getframerate() != 48_000
            or opened.getnframes() != duration_s * 48_000
            or opened.getcomptype() != "NONE"
        ):
            raise ValueError("Gateway drive audio contract mismatch.")


def _prompt(
    shot: ReplicaShot,
    annotation: ReplicaShotAnnotation,
    generation_duration: int,
    *,
    voice_present: bool,
    speaker_visible: bool,
    postprocess_lipsync: bool = False,
) -> tuple[str, str]:
    subjects = ", ".join(annotation.characters)
    checkpoints = (
        f"0.00s: establish the exact starting pose and prop state; "
        f"{shot.duration_s / 2:.2f}s: {annotation.action}; "
        f"{max(0.0, shot.duration_s - 0.25):.2f}s: reach the end state; "
        f"only the final 0.25s may settle naturally."
    )
    if postprocess_lipsync and speaker_visible and annotation.speaker in {
        "source_orange_cat",
        "source_tabby_cat",
    }:
        mouth = (
            f"{annotation.speaker} receives no provider audio. During the reviewed "
            "speech window, animate only the cat's lower jaw with small natural "
            "openings that follow the action checkpoints; keep mouth closed at "
            "frame 0 and close it again by the end. Do not add human lips, visible "
            "human teeth, or a protruding tongue. Non-speakers keep a silent closed "
            "mouth."
        )
    elif postprocess_lipsync and speaker_visible:
        mouth = (
            f"{annotation.speaker} does not speak during provider generation. "
            "Keep a relaxed closed mouth and a stable, unobstructed face for "
            "post-production lip-sync. Non-speakers keep a silent closed mouth."
        )
    elif postprocess_lipsync:
        mouth = (
            f"{annotation.speaker} remains off-screen during provider generation. "
            "No visible speaking mouth; all visible mouths remain naturally closed "
            "for post-production audio."
        )
    else:
        mouth = (
        f"{annotation.speaker} alone speaks to reference audio 1: mouth stays closed before the first audible syllable, mouth begins within 0.20s of speech onset, forms natural syllabic openings, and mouth closes within 0.20s of speech offset. Keep the visible speaker face large and unobstructed enough for lip-sync review throughout the speech window. Non-speakers keep a silent closed mouth."
        if voice_present and speaker_visible
        else (
            f"off-screen voice: {annotation.speaker} speaks only from outside the frame to reference audio 1. no visible speaking mouth; all visible mouths remain naturally closed."
            if voice_present
            else "No one speaks. Every visible face has a silent closed mouth for the entire shot."
        )
        )
    negative = "Negative contract: no source woman face or recognizable likeness, no source cat fur markings as identity anchors, no text, subtitle, watermark, logo, platform UI, username, extra person, extra animal, duplicate subject, anatomy mutation, identity drift, camera drift, object teleportation, floating feet or paws."
    if "source_woman" in annotation.characters:
        human_identity = (
            "Render source_woman as the same wholly fictional adult woman in every shot: shoulder-length burgundy hair, round black eyeglasses, beige sleeveless ribbed lounge top, natural face and proportions. No input image provides a human identity."
        )
    elif annotation.speaker == "source_woman":
        human_identity = (
            "source_woman remains off-screen. If the reviewed action requires direct prop handling, only one hand and forearm may enter; show no face or complete body and no visible speaking mouth."
        )
    else:
        human_identity = "No human character is present."
    cat_references = (
        "Use the approved original cat identity references plus the empty-scene reference."
        if {"source_orange_cat", "source_tabby_cat"} & set(annotation.characters)
        else "Use only the empty-scene reference; it provides no character identity."
    )
    cat_identity_parts: list[str] = []
    if "source_orange_cat" in annotation.characters:
        cat_identity_parts.append(
            "Naitang is a round orange-and-white shorthair with an orange crown and back, white muzzle, chest, belly, and paws, amber eyes, and sharp unchanged coat boundaries."
        )
    if "source_tabby_cat" in annotation.characters:
        cat_identity_parts.append(
            "Doubao is a slender black-and-white tuxedo cat with green eyes, a white facial blaze, chest bib, and four white paws; never simplify Doubao into a solid-black cat."
        )
    cat_identity = " ".join(cat_identity_parts)
    prompt = " ".join(
        (
            "Generate one original vertical live-action-style pet drama shot at natural physical speed.",
            f"Source shot {shot.shot_id}; editorial duration {shot.duration_s:.6f}s; provider duration {generation_duration}s.",
            f"Characters in this shot: {subjects}. {cat_references} {cat_identity} {human_identity}",
            f"Camera height and framing: {annotation.framing}. Frame 0 must already match the reviewed shot setup; do not invent a preceding entrance or pose reset. Follow the reviewed text blocking and retain the room geometry at {annotation.location}.",
            f"Action checkpoints: {checkpoints}",
            mouth,
            "Props must maintain continuous hand or paw contact where held, with explicit start and end states; feet and paws stay grounded; preserve plausible gravity, motion, and spatial continuity; no camera drift or object teleportation.",
            negative,
        )
    )
    return prompt, negative


def _provenance(
    plan: PetReplicaPlan,
    root: Path,
    job: ReplicaShotJob,
    client: GatewayVideoClient,
    report: Mapping[str, Any],
    signature: Mapping[str, Any],
    staged_video: Path,
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "provider": "gateway",
        "model": client.config.model,
        "endpoint_fingerprint_sha256": gateway_endpoint_fingerprint(
            client.config.base_url
        ),
        "shot_id": job.shot_id,
        "candidate_number": job.candidate_number,
        "editorial_duration_s": job.editorial_duration_s,
        "provider_duration_s": job.generation_duration_s,
        "source_window": {"start_s": job.start_s, "end_s": job.end_s},
        "source_sha256": _sha256(plan.source_video),
        "prompt_sha256": _hash_text(job.prompt),
        "anchor_sha256": [_sha256(path) for path in job.reference_images],
        "composition_sha256": _sha256(job.composition_path),
        "drive_audio_sha256": _sha256(job.audio_path) if job.audio_path else None,
        "output_sha256": _sha256(staged_video),
        "output_path": str(job.output_path.relative_to(root)),
        "gateway_report_path": str(job.gateway_report_path.relative_to(root)),
        "gateway_result": _sanitize_report(dict(report)),
        "signature": dict(signature),
    }


def _job_signature(
    plan: PetReplicaPlan, job: ReplicaShotJob, client: GatewayVideoClient
) -> dict[str, Any]:
    return {
        "source_sha256": _sha256(plan.source_video),
        "prompt_sha256": _hash_text(job.prompt),
        "anchor_sha256": [_sha256(path) for path in job.reference_images],
        "composition_sha256": _sha256(job.composition_path),
        "reference_policy": PROVIDER_REFERENCE_POLICY_VERSION,
        "audio_sha256": _sha256(job.audio_path) if job.audio_path else None,
        "generate_audio": job.generate_audio,
        "audio_transport_contract": GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
        "model": client.config.model,
        "endpoint_fingerprint_sha256": gateway_endpoint_fingerprint(
            client.config.base_url
        ),
        "candidate_number": job.candidate_number,
        "editorial_duration_s": job.editorial_duration_s,
        "provider_duration_s": job.generation_duration_s,
        "source_window": {"start_s": job.start_s, "end_s": job.end_s},
    }


def _composition_path(root: Path, shot_id: str) -> Path:
    return _safe_path(root, root / "reference" / "shots" / shot_id / "start.jpg")


def _output_root(plan: PetReplicaPlan) -> Path:
    root = plan.output_root
    if not root.is_absolute() or root != root.resolve():
        raise PetReplicaGenerationError("Replica output root must be resolved.")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise PetReplicaGenerationError(
            "Replica output root must be a regular directory."
        )
    return root


def _safe_path(root: Path, value: Path) -> Path:
    raw = Path(value).expanduser()
    path = raw if raw.is_absolute() else root / raw
    if path.is_symlink():
        raise PetReplicaGenerationError(
            "Replica generation paths may not use symlinks."
        )
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PetReplicaGenerationError(
            "Replica generation paths must remain inside the output root."
        ) from exc
    _assert_no_symlinks(root, path.parent)
    return resolved


def _regular_inside(root: Path, path: Path, label: str) -> None:
    safe = _safe_path(root, path)
    if not safe.is_file() or safe.is_symlink() or safe.stat().st_size <= 0:
        raise PetReplicaGenerationError(
            f"{label} must be a non-empty regular file inside the output root."
        )


def _assert_no_symlinks(root: Path, directory: Path) -> None:
    root = root.resolve()
    target = directory.absolute()
    try:
        parts = target.relative_to(root).parts
    except ValueError as exc:
        raise PetReplicaGenerationError(
            "Replica generation paths must remain inside the output root."
        ) from exc
    cursor = root
    if cursor.is_symlink():
        raise PetReplicaGenerationError("Replica output root may not use symlinks.")
    for part in parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise PetReplicaGenerationError(
                "Replica generation paths may not use symlinks."
            )


def _candidate_number(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_CANDIDATES
    ):
        raise PetReplicaGenerationError(
            "Replica candidate number must be between 1 and 3."
        )


def _staging_path(target: Path) -> Path:
    return target.with_name(f".{target.stem}.stage{target.suffix}")


def _backup_path(target: Path) -> Path:
    return target.with_name(f".{target.stem}.backup{target.suffix}")


def _create_promotion_backups(
    root: Path,
    targets: Sequence[Path],
    backups: list[_PromotionBackup],
) -> None:
    try:
        for target in targets:
            _safe_path(root, target)
            backup = _safe_path(root, _backup_path(target))
            if backup.exists() or backup.is_symlink():
                raise PetReplicaGenerationError(
                    "Replica promotion backup already exists."
                )
            if target.exists():
                _regular_inside(root, target, "Existing candidate artifact")
                original_sha256 = _sha256(target)
                os.replace(target, backup)
                record = _PromotionBackup(target, backup, original_sha256, None)
            else:
                marker = {
                    "schema_version": "motion-comic-factory.pet-replica-absence-backup.v1",
                    "target": str(target.relative_to(root)),
                    "state": "absent",
                }
                _write_json_atomic(backup, marker)
                record = _PromotionBackup(target, backup, None, _sha256(backup))
            backups.append(record)
            if not _backup_is_verified(root, record):
                raise PetReplicaGenerationError(
                    "Replica promotion backup could not be verified."
                )
    except Exception as backup_exc:
        rollback_errors = _rollback_promoted_artifacts(root, backups, ())
        message = _redact(str(backup_exc))
        if rollback_errors:
            message += (
                f"; backup creation rollback errors: {' | '.join(rollback_errors)}"
            )
        raise PetReplicaGenerationError(message) from backup_exc


def _backup_is_verified(root: Path, record: _PromotionBackup) -> bool:
    try:
        _regular_inside(root, record.backup, "Replica promotion backup")
        if record.original_sha256 is not None:
            return _sha256(record.backup) == record.original_sha256
        return (
            record.absence_marker_sha256 is not None
            and _sha256(record.backup) == record.absence_marker_sha256
        )
    except (OSError, PetReplicaGenerationError):
        return False


def _promote_staged_artifacts(
    root: Path,
    staged: Sequence[Path],
    targets: Sequence[Path],
    promoted: list[Path],
) -> None:
    for source, target in zip(staged, targets, strict=True):
        _regular_inside(root, source, "Staged candidate artifact")
        _safe_path(root, target)
        os.replace(source, target)
        promoted.append(target)


def _rollback_promoted_artifacts(
    root: Path,
    backups: list[_PromotionBackup],
    promoted: Sequence[Path],
) -> tuple[str, ...]:
    promoted_set = set(promoted)
    errors: list[str] = []
    for record in reversed(tuple(backups)):
        try:
            if not _backup_is_verified(root, record):
                raise PetReplicaGenerationError(
                    "Replica promotion backup is missing or changed; target was not removed."
                )
            if record.original_sha256 is not None:
                if record.target.exists():
                    _regular_inside(root, record.target, "Promoted candidate artifact")
                    record.target.unlink()
                os.replace(record.backup, record.target)
                if _sha256(record.target) != record.original_sha256:
                    raise PetReplicaGenerationError(
                        "Replica promotion backup restore verification failed."
                    )
            else:
                if record.target in promoted_set and record.target.exists():
                    _regular_inside(root, record.target, "Promoted candidate artifact")
                    record.target.unlink()
                record.backup.unlink(missing_ok=True)
        except Exception as rollback_exc:
            errors.append(_redact(str(rollback_exc)))
        else:
            backups.remove(record)
    return tuple(errors)


def _cleanup_backups_best_effort(
    root: Path, backups: Sequence[_PromotionBackup]
) -> tuple[str, ...]:
    return _cleanup_staging_best_effort(root, *(record.backup for record in backups))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _redact(value: str) -> str:
    value = _QUERY_SECRET.sub(r"\g<prefix>[redacted]", value)

    def quoted(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{match.group('quote')}[redacted]{match.group('quote')}"

    value = _QUOTED_SECRET_VALUE.sub(quoted, value)
    value = _UNQUOTED_SECRET_VALUE.sub(r"\g<prefix>[redacted]", value)
    return _SECRET.sub("[redacted]", value)


def _is_credential_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return normalized in {
        "accesstoken",
        "refreshtoken",
        "xapikey",
        "apikey",
        "authorization",
        "setcookie",
        "cookie",
        "signature",
        "credential",
        "secret",
        "password",
        "token",
        "key",
    }


def _normalize_malformed_escapes(value: str) -> str:
    for _pass in range(_MAX_MALFORMED_ESCAPE_PASSES):
        normalized = value.replace("\\\\", "\\")
        normalized = _UNICODE_ESCAPE.sub(
            lambda match: chr(int(match.group(1), 16)), normalized
        )
        normalized = _JAVASCRIPT_HEX_ESCAPE.sub(
            lambda match: chr(int(match.group(1), 16)), normalized
        )
        normalized = _PERCENT_ESCAPE.sub(
            lambda match: chr(int(match.group(1), 16)), normalized
        )
        if normalized == value:
            break
        value = normalized
    return value


def _sanitize_report(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if _is_credential_key(key)
            else _sanitize_report(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_report(item) for item in value]
    return _redact(value) if isinstance(value, str) else value


def _sanitize_report_file(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaGenerationError(
            "Gateway candidate report is not valid JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaGenerationError(
            "Gateway candidate report must be a JSON object."
        )
    _write_json_atomic(path, _sanitize_report(payload))


def _archive_failed_attempt(
    root: Path,
    job: ReplicaShotJob,
    signature: Mapping[str, Any],
    staged_report: Path,
    staged_state: Path,
    staged_video: Path,
    error: Exception,
) -> tuple[Path, tuple[str, ...]]:
    attempt_root = _safe_path(
        root,
        root
        / "rejected"
        / "generation_attempts"
        / job.shot_id
        / f"candidate_{job.candidate_number:02d}",
    )
    attempt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(attempt_root, 0o700)
    _assert_no_symlinks(root, attempt_root)
    attempt = _next_attempt_path(root, attempt_root)
    diagnostics: list[str] = []
    diagnostic_errors: list[str] = []
    for source, name in (
        (staged_report, "gateway_report.json"),
        (staged_state, "gateway_state.json"),
    ):
        try:
            artifact = _archive_failure_diagnostic(root, attempt, source, name)
        except Exception as diagnostic_exc:
            diagnostic_errors.append(f"{name}: {_redact(str(diagnostic_exc))}")
        else:
            if artifact is not None:
                diagnostics.append(artifact)
    try:
        media_artifact = _archive_failure_media(root, attempt, staged_video)
    except Exception as diagnostic_exc:
        diagnostic_errors.append(
            f"gateway_output.mp4: {_redact(str(diagnostic_exc))}"
        )
    else:
        if media_artifact is not None:
            diagnostics.append(media_artifact)
    signature_text = json.dumps(
        _sanitize_report(signature),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    _write_json_atomic(
        _safe_path(root, attempt / "failure.json"),
        {
            "schema_version": "motion-comic-factory.pet-replica-generation-attempt.v1",
            "shot_id": job.shot_id,
            "candidate_number": job.candidate_number,
            "signature_sha256": _hash_text(signature_text),
            "error": _redact(str(error)),
            "diagnostics": diagnostics,
            "diagnostic_errors": diagnostic_errors,
            "promoted": False,
        },
    )
    return attempt, tuple(diagnostic_errors)


def _archive_failure_media(
    root: Path,
    attempt: Path,
    source: Path,
) -> str | None:
    if source.is_symlink():
        raise PetReplicaGenerationError(
            "Gateway failure output is a symlink and was rejected."
        )
    if not source.exists():
        return None
    _regular_inside(root, source, "Gateway failure output")
    artifact = "gateway_output.mp4"
    destination = _safe_path(root, attempt / artifact)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{artifact}.", suffix=".tmp", dir=attempt
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_handle, source.open(
            "rb"
        ) as input_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        if _sha256(destination) != _sha256(source):
            raise PetReplicaGenerationError(
                "Archived gateway failure output hash mismatch."
            )
    finally:
        temporary.unlink(missing_ok=True)
    return artifact


def _archive_failure_diagnostic(
    root: Path, attempt: Path, source: Path, name: str
) -> str | None:
    if source.is_symlink():
        raise PetReplicaGenerationError(
            "Gateway failure diagnostic is a symlink and was rejected."
        )
    if not source.exists():
        return None
    _regular_inside(root, source, "Gateway failure diagnostic")
    raw = source.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        artifact = f"{name.removesuffix('.json')}.invalid.json"
        malformed_text = raw.decode("utf-8", errors="replace")
        sanitized_text = _redact(_normalize_malformed_escapes(malformed_text))
        _write_json_atomic(
            _safe_path(root, attempt / artifact),
            {
                "schema_version": "motion-comic-factory.pet-replica-malformed-diagnostic.v1",
                "source_name": name,
                "original_sha256": hashlib.sha256(raw).hexdigest(),
                "original_byte_length": len(raw),
                "recoverable_task_ids": sorted(set(_TASK_ID.findall(sanitized_text))),
                "sanitized_utf8_base64": base64.b64encode(
                    sanitized_text.encode("utf-8")
                ).decode("ascii"),
            },
        )
        return artifact
    _write_json_atomic(_safe_path(root, attempt / name), _sanitize_report(payload))
    return name


def _next_attempt_path(root: Path, attempt_root: Path) -> Path:
    for number in range(1, 10_000):
        attempt = _safe_path(root, attempt_root / f"attempt_{number:03d}")
        try:
            attempt.mkdir()
        except FileExistsError:
            continue
        return attempt
    raise PetReplicaGenerationError("Replica generation attempt archive is full.")


def _read_report(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaGenerationError(
            "Gateway candidate report is not valid JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaGenerationError(
            "Gateway candidate report must be a JSON object."
        )
    return payload


def _require_completed_gateway_report(report: Mapping[str, Any]) -> None:
    errors = report.get("errors")
    results = report.get("results")
    if (
        report.get("success") is not True
        or report.get("planned_count") != 1
        or report.get("completed_count") != 1
        or report.get("failed_count") != 0
        or report.get("error") not in ("", None)
        or not isinstance(errors, list)
        or errors
        or not isinstance(results, list)
        or len(results) != 1
        or not isinstance(results[0], Mapping)
        or results[0].get("status") != "completed"
    ):
        raise PetReplicaGenerationError(
            "Gateway candidate report is not fully completed."
        )


def _validate_staged_candidate(root: Path, path: Path, job: ReplicaShotJob) -> None:
    _regular_inside(root, path, "Generated candidate")
    if not is_valid_mp4_file(path):
        raise PetReplicaGenerationError("Generated candidate is not a valid MP4.")
    probe = _probe_candidate_video(path)
    if (probe.width, probe.height) != (720, 1280):
        raise PetReplicaGenerationError(
            "Generated candidate dimensions must be 720x1280."
        )
    if not any(
        abs(probe.fps - supported) <= 0.01
        for supported in _SUPPORTED_PROVIDER_FRAME_RATES
    ):
        raise PetReplicaGenerationError(
            "Generated candidate must use a supported frame rate (24, 25, or 30 fps)."
        )
    if probe.duration_s + (1 / 30) < job.generation_duration_s:
        raise PetReplicaGenerationError(
            "Generated candidate is shorter than the provider duration."
        )


def _probe_candidate_video(path: Path) -> ReplicaCandidateVideoProbe:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
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
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PetReplicaGenerationError(
            "Unable to probe generated candidate video."
        ) from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else None
    if not isinstance(streams, list):
        raise PetReplicaGenerationError(
            "Generated candidate probe has no stream metadata."
        )
    videos = [
        stream
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "video"
    ]
    if len(videos) != 1:
        raise PetReplicaGenerationError(
            "Generated candidate must contain exactly one video stream."
        )
    video = videos[0]
    try:
        width = int(video["width"])
        height = int(video["height"])
        duration = float(payload["format"]["duration"])
        fps = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise PetReplicaGenerationError(
            "Generated candidate probe has invalid media metadata."
        ) from exc
    if (
        not math.isfinite(duration)
        or duration <= 0
        or not math.isfinite(fps)
        or fps <= 0
    ):
        raise PetReplicaGenerationError(
            "Generated candidate probe has invalid media metadata."
        )
    return ReplicaCandidateVideoProbe(duration, width, height, fps)


def _frame_rate(value: Any) -> float:
    text = str(value).strip()
    if "/" not in text:
        return float(text)
    numerator, denominator = text.split("/", 1)
    return float(numerator) / float(denominator)


def _remove_staging_files(root: Path, *paths: Path) -> None:
    for path in paths:
        _safe_path(root, path)
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise PetReplicaGenerationError(
                    "Replica staging path is not a regular file."
                )
            path.unlink()


def _cleanup_staging_best_effort(root: Path, *paths: Path) -> tuple[str, ...]:
    errors: list[str] = []
    for path in paths:
        try:
            _cleanup_path_without_following_symlinks(root, path)
        except Exception as cleanup_exc:
            errors.append(_redact(str(cleanup_exc)))
    return tuple(errors)


def _cleanup_path_without_following_symlinks(root: Path, path: Path) -> None:
    raw = Path(path)
    absolute = raw if raw.is_absolute() else root / raw
    try:
        absolute.absolute().relative_to(root)
    except ValueError as exc:
        raise PetReplicaGenerationError(
            "Replica cleanup path must remain inside the output root."
        ) from exc
    _assert_no_symlinks(root, absolute.parent)
    if absolute.is_symlink():
        absolute.unlink()
        return
    if absolute.exists():
        if not absolute.is_file():
            raise PetReplicaGenerationError(
                "Replica cleanup path is not a regular file."
            )
        absolute.unlink()


def _staging_artifacts(
    staged_video: Path,
    staged_report: Path,
    staged_provenance: Path,
) -> tuple[Path, ...]:
    return (
        staged_video,
        staged_report,
        staged_provenance,
        staged_video.with_suffix(staged_video.suffix + ".gateway.json"),
        staged_video.with_suffix(staged_video.suffix + ".gateway.lock"),
        staged_video.with_suffix(staged_video.suffix + ".part"),
    )
