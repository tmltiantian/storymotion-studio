from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .pipeline_contracts import (
    ProductionPackage,
    ProjectSpec,
    ReviewState,
    StageName,
    StageState,
)
from .pipeline_review import REVIEW_SCHEMA, REVISIONS_SCHEMA, StageRevision
from .pipeline_store import ACTIVE_REPAIR_SCHEMA
from .secure_posix import AnchoredDirectory
from .video_provider import default_video_resolution, estimate_video_cost_yuan


TOKEN_SCHEMA = "motion-comic-factory.video-generation-token.v1"
REQUEST_SCHEMA = "motion-comic-factory.video-generation-request.v1"
PREFLIGHT_STAGES = (
    StageName.SCRIPT,
    StageName.STORYBOARD,
    StageName.ASSETS,
    StageName.AUDIO,
)
_TOKEN_ID = re.compile(r"[0-9a-f]{32}")
_TOKEN_SECRET = re.compile(r"[A-Za-z0-9_-]{32,128}")


class GenerationTokenError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_bytes_secure(
    path: Path,
    label: str,
    *,
    anchor: AnchoredDirectory | None = None,
) -> bytes:
    if anchor is not None:
        try:
            return anchor.read_bytes(anchor.relative_path(path))
        except ValueError as exc:
            if not str(exc).startswith("Path is outside"):
                raise
    absolute = path.expanduser().absolute()
    with AnchoredDirectory.open(absolute.parent, label=f"{label} directory") as parent:
        return parent.read_bytes(absolute.name)


def _sha256_secure(
    path: Path,
    label: str,
    *,
    anchor: AnchoredDirectory | None = None,
) -> str:
    return hashlib.sha256(_read_bytes_secure(path, label, anchor=anchor)).hexdigest()


def _safe_existing_file(
    path: Path,
    label: str,
    *,
    anchor: AnchoredDirectory | None = None,
) -> Path:
    _read_bytes_secure(path, label, anchor=anchor)
    return path


def _read_object(
    path: Path,
    label: str,
    *,
    anchor: AnchoredDirectory | None = None,
) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bytes_secure(path, label, anchor=anchor).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_shot_ids(shot_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = tuple(str(shot_id).strip() for shot_id in shot_ids)
    if (
        not normalized
        or len(set(normalized)) != len(normalized)
        or any(
            not shot_id
            or shot_id in {".", ".."}
            or "/" in shot_id
            or "\\" in shot_id
            or any(ord(character) < 32 for character in shot_id)
            for shot_id in normalized
        )
    ):
        raise ValueError("Requested shot IDs must be non-empty, unique, safe values")
    return normalized


def _latest_revision(
    anchor: AnchoredDirectory,
    stage: StageName,
) -> tuple[StageRevision | None, dict[str, Any] | None]:
    root = anchor.canonical_path
    path = root / "reviews" / f"{stage.value}.revisions.json"
    try:
        payload = _read_object(
            path,
            f"{stage.value} revision record",
            anchor=anchor,
        )
    except FileNotFoundError:
        return None, None
    if (
        payload.get("schema_version") != REVISIONS_SCHEMA
        or payload.get("stage") != stage.value
        or not isinstance(payload.get("revisions"), list)
        or not payload["revisions"]
    ):
        raise ValueError(f"{stage.value} revision record is invalid")
    raw_revision = payload["revisions"][-1]
    if not isinstance(raw_revision, dict):
        raise ValueError(f"{stage.value} revision record is invalid")
    return StageRevision.from_dict(raw_revision), raw_revision


def _artifact_hashes(
    anchor: AnchoredDirectory,
    stage: StageName,
    revision: StageRevision,
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for index, artifact in enumerate(revision.artifacts, start=1):
        path = _safe_existing_file(
            Path(artifact.path),
            f"{stage.value} artifact",
            anchor=anchor,
        )
        current = _sha256_secure(
            path,
            f"{stage.value} artifact",
            anchor=anchor,
        )
        if current != artifact.sha256:
            raise ValueError(f"{stage.value} artifact changed")
        hashes[f"{stage.value}:{index}:{path.name}"] = current
    if not hashes:
        raise ValueError(f"{stage.value} revision has no artifacts")
    return hashes


def _approved_revision_hash(
    anchor: AnchoredDirectory,
    stage: StageName,
    revision: StageRevision,
    review_state: ReviewState,
) -> str | None:
    if review_state is ReviewState.AUTO_APPROVED:
        return _canonical_hash(
            {
                "stage": stage.value,
                "revision": revision.number,
                "review_state": review_state.value,
            }
        )
    if review_state is not ReviewState.APPROVED:
        return None
    path = anchor.canonical_path / "reviews" / f"{stage.value}.review.json"
    try:
        payload = _read_object(
            path,
            f"{stage.value} review record",
            anchor=anchor,
        )
    except FileNotFoundError:
        return None
    if (
        payload.get("schema_version") != REVIEW_SCHEMA
        or payload.get("stage") != stage.value
        or payload.get("state") != ReviewState.APPROVED.value
        or payload.get("revision") != revision.number
    ):
        return None
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return None
    for item in evidence:
        if not isinstance(item, dict):
            return None
        path_value = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path_value, str) or not isinstance(digest, str):
            return None
        path = _safe_existing_file(
            Path(path_value),
            f"{stage.value} review evidence",
            anchor=anchor,
        )
        if (
            _sha256_secure(
                path,
                f"{stage.value} review evidence",
                anchor=anchor,
            )
            != digest
        ):
            return None
    return _canonical_hash(payload)


def _storyboard_rows(
    anchor: AnchoredDirectory,
    revision: StageRevision,
) -> tuple[dict[str, Any], ...]:
    for artifact in revision.artifacts:
        path = Path(artifact.path)
        if path.name != "episode.json":
            continue
        payload = _read_object(path, "storyboard episode", anchor=anchor)
        rows = payload.get("shots")
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return tuple(rows)
    raise ValueError("Storyboard revision has no episode shot snapshot")


def _provider_settings(spec: ProjectSpec) -> tuple[str, str, str, Any]:
    raw_video = str(spec.providers.get("video") or "").strip()
    provider = str(spec.providers.get("video_provider") or "").strip().lower()
    model = str(spec.providers.get("video_model") or "").strip()
    if not provider and raw_video.lower() in {"gateway", "minimax"}:
        provider = raw_video.lower()
    if not model and raw_video.lower() not in {"", "gateway", "minimax"}:
        model = raw_video
    if not provider and model:
        provider = "minimax" if model.lower() == "minimax-h3" else "gateway"
    resolution = str(
        spec.target.get("video_resolution") or default_video_resolution(provider)
    ).strip()
    raw_rate = spec.target.get("video_price_yuan_per_second")
    return provider, model, resolution, raw_rate


def _strict_price_rate(provider: str, resolution: str, raw_rate: Any) -> float:
    estimate_video_cost_yuan(
        provider,
        resolution=resolution,
        output_seconds=1,
        price_yuan_per_second=raw_rate,
    )
    if raw_rate is None:
        from .minimax_h3_video import H3_OUTPUT_PRICE_YUAN_PER_SECOND

        raw_rate = H3_OUTPUT_PRICE_YUAN_PER_SECOND.get(resolution.upper())
    if isinstance(raw_rate, bool):
        raise ValueError("Video price per output second must be positive.")
    rate = float(raw_rate)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Video price per output second must be positive.")
    return rate


@dataclass(frozen=True)
class VideoShotRequest:
    shot_id: str
    duration: int
    resolution: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_id", _safe_shot_ids((self.shot_id,))[0])
        if isinstance(self.duration, bool) or self.duration <= 0:
            raise ValueError("Video shot duration must be positive")
        if not str(self.resolution).strip():
            raise ValueError("Video shot resolution is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "duration": self.duration,
            "resolution": self.resolution,
        }


@dataclass(frozen=True)
class VideoPreflight:
    project_id: str
    project_sha256: str
    package_sha256: str
    revision_hashes: Mapping[str, str]
    artifact_hashes: Mapping[str, str]
    approval_hashes: Mapping[str, str]
    repair_plan_sha256: str
    shot_ids: tuple[str, ...]
    shots: tuple[VideoShotRequest, ...]
    provider: str
    model: str
    resolution: str
    output_seconds: int
    estimated_cost_yuan: float
    price_yuan_per_second: float
    ready: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_ids", _safe_shot_ids(self.shot_ids))
        object.__setattr__(self, "revision_hashes", dict(self.revision_hashes))
        object.__setattr__(self, "artifact_hashes", dict(self.artifact_hashes))
        object.__setattr__(self, "approval_hashes", dict(self.approval_hashes))
        object.__setattr__(self, "shots", tuple(self.shots))
        object.__setattr__(self, "blockers", tuple(map(str, self.blockers)))


@dataclass(frozen=True)
class VideoGenerationRequest:
    project_id: str
    project_sha256: str
    package_sha256: str
    revision_hashes: Mapping[str, str]
    artifact_hashes: Mapping[str, str]
    approval_hashes: Mapping[str, str]
    repair_plan_sha256: str
    shot_ids: tuple[str, ...]
    shots: tuple[VideoShotRequest, ...]
    provider: str
    model: str
    resolution: str
    output_seconds: int
    estimated_cost_yuan: float
    price_yuan_per_second: float
    schema_version: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_ids", _safe_shot_ids(self.shot_ids))
        object.__setattr__(self, "revision_hashes", dict(self.revision_hashes))
        object.__setattr__(self, "artifact_hashes", dict(self.artifact_hashes))
        object.__setattr__(self, "approval_hashes", dict(self.approval_hashes))
        object.__setattr__(self, "shots", tuple(self.shots))
        if self.schema_version != REQUEST_SCHEMA:
            raise ValueError("Unsupported video generation request schema")

    @classmethod
    def from_preflight(cls, preflight: VideoPreflight) -> VideoGenerationRequest:
        return cls(
            project_id=preflight.project_id,
            project_sha256=preflight.project_sha256,
            package_sha256=preflight.package_sha256,
            revision_hashes=preflight.revision_hashes,
            artifact_hashes=preflight.artifact_hashes,
            approval_hashes=preflight.approval_hashes,
            repair_plan_sha256=preflight.repair_plan_sha256,
            shot_ids=preflight.shot_ids,
            shots=preflight.shots,
            provider=preflight.provider,
            model=preflight.model,
            resolution=preflight.resolution,
            output_seconds=preflight.output_seconds,
            estimated_cost_yuan=preflight.estimated_cost_yuan,
            price_yuan_per_second=preflight.price_yuan_per_second,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_sha256": self.project_sha256,
            "package_sha256": self.package_sha256,
            "revision_hashes": dict(self.revision_hashes),
            "artifact_hashes": dict(self.artifact_hashes),
            "approval_hashes": dict(self.approval_hashes),
            "repair_plan_sha256": self.repair_plan_sha256,
            "shot_ids": list(self.shot_ids),
            "shots": [shot.to_dict() for shot in self.shots],
            "provider": self.provider,
            "model": self.model,
            "resolution": self.resolution,
            "output_seconds": self.output_seconds,
            "estimated_cost_yuan": round(self.estimated_cost_yuan, 4),
            "price_yuan_per_second": self.price_yuan_per_second,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VideoGenerationRequest:
        revision_hashes = value.get("revision_hashes")
        artifact_hashes = value.get("artifact_hashes")
        approval_hashes = value.get("approval_hashes")
        shot_ids = value.get("shot_ids")
        shots = value.get("shots")
        if (
            not isinstance(revision_hashes, dict)
            or not isinstance(artifact_hashes, dict)
            or not isinstance(approval_hashes, dict)
            or not isinstance(shot_ids, list)
            or not isinstance(shots, list)
        ):
            raise ValueError("Video generation request is invalid")
        return cls(
            project_id=str(value["project_id"]),
            project_sha256=str(value["project_sha256"]),
            package_sha256=str(value["package_sha256"]),
            revision_hashes={
                str(key): str(item) for key, item in revision_hashes.items()
            },
            artifact_hashes={
                str(key): str(item) for key, item in artifact_hashes.items()
            },
            approval_hashes={
                str(key): str(item) for key, item in approval_hashes.items()
            },
            repair_plan_sha256=str(value.get("repair_plan_sha256") or ""),
            shot_ids=tuple(map(str, shot_ids)),
            shots=tuple(
                VideoShotRequest(
                    shot_id=str(item["shot_id"]),
                    duration=int(item["duration"]),
                    resolution=str(item["resolution"]),
                )
                for item in shots
                if isinstance(item, dict)
            ),
            provider=str(value["provider"]),
            model=str(value["model"]),
            resolution=str(value["resolution"]),
            output_seconds=int(value["output_seconds"]),
            estimated_cost_yuan=float(value["estimated_cost_yuan"]),
            price_yuan_per_second=float(value["price_yuan_per_second"]),
            schema_version=str(value.get("schema_version") or ""),
        )

    def paid_description(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "shots": [shot.to_dict() for shot in self.shots],
            "output_seconds": self.output_seconds,
            "price_yuan_per_second": self.price_yuan_per_second,
            "estimated_cost_yuan": self.estimated_cost_yuan,
        }


def describe_submitted_video_request(
    request: VideoGenerationRequest,
    *,
    provider: str,
    model: str,
    jobs: list[Any],
) -> dict[str, Any]:
    shots = tuple(
        VideoShotRequest(
            shot_id=str(job.shot_id),
            duration=job.duration,
            resolution=str(job.resolution),
        )
        for job in jobs
    )
    output_seconds = sum(shot.duration for shot in shots)
    resolutions = {shot.resolution for shot in shots}
    resolution = next(iter(resolutions)) if len(resolutions) == 1 else ""
    estimate = estimate_video_cost_yuan(
        provider,
        resolution=resolution,
        output_seconds=output_seconds,
        price_yuan_per_second=request.price_yuan_per_second,
    )
    return {
        "provider": str(provider).strip().lower(),
        "model": str(model).strip(),
        "shots": [shot.to_dict() for shot in shots],
        "output_seconds": output_seconds,
        "price_yuan_per_second": request.price_yuan_per_second,
        "estimated_cost_yuan": estimate,
    }


def _validate_active_repair(value: Mapping[str, Any]) -> tuple[str, ...]:
    required = {
        "schema_version",
        "plan_id",
        "request_stage",
        "affected",
        "preserved_artifacts",
        "source_package_sha256",
        "target_package_sha256",
    }
    if set(value) != required or value.get("schema_version") != ACTIVE_REPAIR_SCHEMA:
        raise ValueError("Active repair state is invalid")
    if not isinstance(value.get("plan_id"), str) or not value["plan_id"]:
        raise ValueError("Active repair state is invalid")
    try:
        StageName(str(value.get("request_stage") or ""))
    except ValueError as exc:
        raise ValueError("Active repair state is invalid") from exc
    affected = value.get("affected")
    preserved = value.get("preserved_artifacts")
    if not isinstance(affected, dict) or not isinstance(preserved, list):
        raise ValueError("Active repair state is invalid")
    for key, item_ids in affected.items():
        if not isinstance(key, str) or not key or not isinstance(item_ids, list):
            raise ValueError("Active repair state is invalid")
        _safe_shot_ids((key,))
        if any(not isinstance(item, str) for item in item_ids):
            raise ValueError("Active repair state is invalid")
        if item_ids:
            _safe_shot_ids(item_ids)
    video_ids = affected.get(StageName.VIDEO.value)
    if not isinstance(video_ids, list) or not video_ids:
        raise ValueError("Active repair state has no video scope")
    if any(not isinstance(item, str) for item in preserved):
        raise ValueError("Active repair state is invalid")
    for name in ("source_package_sha256", "target_package_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Active repair state is invalid")
    return _safe_shot_ids(video_ids)


def build_video_preflight(
    project_dir: str | Path,
    shot_ids: tuple[str, ...],
) -> VideoPreflight:
    with AnchoredDirectory.open(
        project_dir,
        label="Video preflight project directory",
    ) as anchor:
        return _build_video_preflight(anchor, shot_ids)


def _build_video_preflight(
    anchor: AnchoredDirectory,
    shot_ids: tuple[str, ...],
) -> VideoPreflight:
    root = anchor.canonical_path
    requested = _safe_shot_ids(shot_ids)
    spec_path = _safe_existing_file(
        root / "project.json",
        "project spec",
        anchor=anchor,
    )
    package_path = _safe_existing_file(
        root / "production_package.json",
        "production package",
        anchor=anchor,
    )
    spec = ProjectSpec.from_dict(_read_object(spec_path, "project spec", anchor=anchor))
    package = ProductionPackage.from_dict(
        _read_object(package_path, "production package", anchor=anchor)
    )
    blockers: list[str] = []
    revision_hashes: dict[str, str] = {}
    artifact_hashes: dict[str, str] = {}
    approval_hashes: dict[str, str] = {}
    revisions: dict[StageName, StageRevision] = {}
    for stage in PREFLIGHT_STAGES:
        record = next(item for item in package.stages if item.stage is stage)
        try:
            revision, raw_revision = _latest_revision(anchor, stage)
            if revision is None or raw_revision is None:
                blockers.append(f"{stage.value} has no current revision.")
                continue
            revisions[stage] = revision
            revision_hashes[stage.value] = _canonical_hash(raw_revision)
            artifact_hashes.update(_artifact_hashes(anchor, stage, revision))
            if (
                record.state is not StageState.PASSED
                or record.revision != revision.number
            ):
                blockers.append(f"{stage.value} package revision is not current.")
            else:
                approval_hash = _approved_revision_hash(
                    anchor,
                    stage,
                    revision,
                    record.review_state,
                )
                if approval_hash is None:
                    blockers.append(f"{stage.value} revision is not approved.")
                else:
                    approval_hashes[stage.value] = approval_hash
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            blockers.append(f"{stage.value} preflight failed: {exc}")
    storyboard = revisions.get(StageName.STORYBOARD)
    if storyboard is None:
        raise ValueError("Video preflight requires a storyboard revision")
    rows = _storyboard_rows(anchor, storyboard)
    row_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        shot_id = str(row.get("id") or "").strip()
        if not shot_id or shot_id in row_by_id:
            raise ValueError("Storyboard shot IDs are invalid")
        row_by_id[shot_id] = row
    unknown = tuple(shot_id for shot_id in requested if shot_id not in row_by_id)
    if unknown:
        raise ValueError("Unknown requested shot IDs: " + ", ".join(unknown))
    provider, model, resolution, raw_rate = _provider_settings(spec)
    output_seconds = 0
    shot_requests: list[VideoShotRequest] = []
    for shot_id in requested:
        value = row_by_id[shot_id].get("duration_seconds")
        if isinstance(value, bool):
            raise ValueError(f"Storyboard shot duration is invalid: {shot_id}")
        duration = int(round(float(value)))
        if duration <= 0:
            raise ValueError(f"Storyboard shot duration is invalid: {shot_id}")
        output_seconds += duration
        shot_requests.append(
            VideoShotRequest(
                shot_id=shot_id,
                duration=duration,
                resolution=resolution,
            )
        )
    if provider not in {"gateway", "minimax"}:
        blockers.append("A paid gateway or minimax video provider is required.")
    if not model:
        blockers.append("Video model is not configured.")
    if not resolution:
        blockers.append("Video resolution is not configured.")
    try:
        rate = _strict_price_rate(provider, resolution, raw_rate)
        estimate = estimate_video_cost_yuan(
            provider,
            resolution=resolution,
            output_seconds=output_seconds,
            price_yuan_per_second=rate,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        blockers.append(str(exc))
        estimate = 0.0
        rate = 0.0
    repair_path = root / "impact_plans" / "active.json"
    repair_hash = ""
    try:
        active = _read_object(
            repair_path,
            "active repair plan",
            anchor=anchor,
        )
    except FileNotFoundError:
        active = None
    if active is not None:
        repair_hash = _sha256_secure(
            repair_path,
            "active repair plan",
            anchor=anchor,
        )
        repair_ids = _validate_active_repair(active)
        if any(shot_id not in repair_ids for shot_id in requested):
            blockers.append(
                "Requested shots are outside the active video repair scope."
            )
    project_binding = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return VideoPreflight(
        project_id=package.project_id,
        project_sha256=project_binding,
        package_sha256=_sha256_secure(
            package_path,
            "production package",
            anchor=anchor,
        ),
        revision_hashes=revision_hashes,
        artifact_hashes=artifact_hashes,
        approval_hashes=approval_hashes,
        repair_plan_sha256=repair_hash,
        shot_ids=requested,
        shots=tuple(shot_requests),
        provider=provider,
        model=model,
        resolution=resolution,
        output_seconds=output_seconds,
        estimated_cost_yuan=estimate,
        price_yuan_per_second=rate,
        ready=not blockers,
        blockers=tuple(blockers),
    )


def _token_directory(
    anchor: AnchoredDirectory,
    *,
    create: bool,
) -> tuple[Path, int]:
    directory = anchor.canonical_path / "runs" / ".workbench" / "tokens"
    try:
        descriptor = anchor.open_directory(
            Path("runs") / ".workbench" / "tokens",
            create=create,
        )
    except OSError as exc:
        raise ValueError("Generation token path cannot use a symlink") from exc
    return directory, descriptor


def _write_token_at(
    directory: int,
    filename: str,
    payload: Mapping[str, Any],
) -> None:
    temporary = f".{filename}.{uuid4().hex}.tmp"
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            remaining = memoryview(content)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("Unable to write generation token")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(
            temporary,
            filename,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _write_token_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    directory_fd: int | None = None,
) -> None:
    if directory_fd is not None:
        _write_token_at(directory_fd, path.name, payload)
        return
    with AnchoredDirectory.open(
        path.parent,
        label="Generation token directory",
    ) as directory:
        _write_token_at(directory.descriptor, path.name, payload)


def _read_token_object(directory: int, filename: str) -> dict[str, Any]:
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory,
        )
    except OSError as exc:
        raise GenerationTokenError("Generation token record is invalid") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GenerationTokenError("Generation token record is invalid")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationTokenError("Generation token record is invalid") from exc
    if not isinstance(value, dict):
        raise GenerationTokenError("Generation token record is invalid")
    return value


def _token_digest(secret: str, request: VideoGenerationRequest) -> str:
    digest = hashlib.sha256()
    digest.update(secret.encode("ascii"))
    digest.update(b"\0")
    digest.update(_canonical_bytes(request.to_dict()))
    return digest.hexdigest()


def issue_generation_token(
    project_dir: str | Path,
    preflight: VideoPreflight,
) -> str:
    with AnchoredDirectory.open(
        project_dir,
        label="Video preflight project directory",
    ) as anchor:
        current = _build_video_preflight(anchor, preflight.shot_ids)
        if current != preflight:
            raise GenerationTokenError("Video preflight changed before token issue")
        if not preflight.ready:
            raise GenerationTokenError("Video preflight is not ready")
        request = VideoGenerationRequest.from_preflight(preflight)
        token_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        token_dir, token_directory = _token_directory(anchor, create=True)
        try:
            _write_token_atomic(
                token_dir / f"{token_id}.json",
                {
                    "schema_version": TOKEN_SCHEMA,
                    "token_id": token_id,
                    "request": request.to_dict(),
                    "request_digest": _token_digest(secret, request),
                    "issued_at": _utc_now(),
                    "consumed_at": "",
                },
                directory_fd=token_directory,
            )
            anchor.verify_directory(
                Path("runs") / ".workbench" / "tokens",
                token_directory,
            )
        finally:
            os.close(token_directory)
        return f"{token_id}.{secret}"


def _parse_token(token: str) -> tuple[str, str]:
    try:
        token_id, secret = str(token).split(".", 1)
    except ValueError as exc:
        raise GenerationTokenError("Generation token is invalid") from exc
    if not _TOKEN_ID.fullmatch(token_id) or not _TOKEN_SECRET.fullmatch(secret):
        raise GenerationTokenError("Generation token is invalid")
    return token_id, secret


def consume_generation_token(
    project_dir: str | Path,
    token: str,
    request: VideoGenerationRequest,
) -> None:
    token_id, secret = _parse_token(token)
    with AnchoredDirectory.open(
        project_dir,
        label="Video preflight project directory",
    ) as anchor:
        try:
            token_dir, token_directory = _token_directory(anchor, create=False)
        except (FileNotFoundError, ValueError) as exc:
            raise GenerationTokenError("Generation token storage is invalid") from exc
        path = token_dir / f"{token_id}.json"
        descriptor = -1
        try:
            try:
                descriptor = os.open(
                    f"{token_id}.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=token_directory,
                )
            except OSError as exc:
                raise GenerationTokenError("Generation token lock is invalid") from exc
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise GenerationTokenError("Generation token lock is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            record = _read_token_object(token_directory, path.name)
            if (
                record.get("schema_version") != TOKEN_SCHEMA
                or record.get("token_id") != token_id
            ):
                raise GenerationTokenError("Generation token record is invalid")
            if record.get("consumed_at"):
                raise GenerationTokenError("Generation token was already consumed")
            raw_request = record.get("request")
            if not isinstance(raw_request, dict):
                raise GenerationTokenError("Generation token request is invalid")
            try:
                stored_request = VideoGenerationRequest.from_dict(raw_request)
            except (KeyError, TypeError, ValueError) as exc:
                raise GenerationTokenError(
                    "Generation token request is invalid"
                ) from exc
            if stored_request != request:
                raise GenerationTokenError(
                    "Generation token does not match the request"
                )
            expected_digest = _token_digest(secret, request)
            if not hmac.compare_digest(
                str(record.get("request_digest") or ""),
                expected_digest,
            ):
                raise GenerationTokenError(
                    "Generation token does not match the request"
                )
            try:
                current_preflight = _build_video_preflight(anchor, request.shot_ids)
                if not current_preflight.ready:
                    raise GenerationTokenError(
                        "Project changed after generation token issue"
                    )
                current = VideoGenerationRequest.from_preflight(current_preflight)
            except GenerationTokenError:
                raise
            except (FileNotFoundError, TypeError, ValueError) as exc:
                raise GenerationTokenError(
                    "Project changed after generation token issue"
                ) from exc
            if current != request:
                raise GenerationTokenError(
                    "Project changed after generation token issue"
                )
            record["consumed_at"] = _utc_now()
            _write_token_atomic(path, record, directory_fd=token_directory)
            anchor.verify_directory(
                Path("runs") / ".workbench" / "tokens",
                token_directory,
            )
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
            os.close(token_directory)
