from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .file_io import sha256_file
from .pipeline_contracts import (
    ProductionPackage,
    ProjectSpec,
    ReviewState,
    StageName,
    StageState,
)
from .pipeline_review import REVIEW_SCHEMA, REVISIONS_SCHEMA, StageRevision
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


def _path_uses_symlink(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            return True
    return False


def _safe_project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().absolute()
    if _path_uses_symlink(root):
        raise ValueError("Video preflight project path cannot use a symlink")
    if not root.is_dir():
        raise FileNotFoundError(root)
    return root.resolve()


def _safe_existing_file(path: Path, label: str) -> Path:
    if _path_uses_symlink(path) or path.is_symlink():
        raise ValueError(f"{label} cannot use a symlink")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _read_object(path: Path, label: str) -> dict[str, Any]:
    source = _safe_existing_file(path, label)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
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
    root: Path,
    stage: StageName,
) -> tuple[StageRevision | None, dict[str, Any] | None]:
    path = root / "reviews" / f"{stage.value}.revisions.json"
    if not path.exists():
        return None, None
    payload = _read_object(path, f"{stage.value} revision record")
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
    stage: StageName,
    revision: StageRevision,
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for index, artifact in enumerate(revision.artifacts, start=1):
        path = _safe_existing_file(Path(artifact.path), f"{stage.value} artifact")
        current = sha256_file(path)
        if current != artifact.sha256:
            raise ValueError(f"{stage.value} artifact changed")
        hashes[f"{stage.value}:{index}:{path.name}"] = current
    if not hashes:
        raise ValueError(f"{stage.value} revision has no artifacts")
    return hashes


def _approved_revision_is_current(
    root: Path,
    stage: StageName,
    revision: StageRevision,
    review_state: ReviewState,
) -> bool:
    if review_state is ReviewState.AUTO_APPROVED:
        return True
    if review_state is not ReviewState.APPROVED:
        return False
    path = root / "reviews" / f"{stage.value}.review.json"
    if not path.exists():
        return False
    payload = _read_object(path, f"{stage.value} review record")
    if (
        payload.get("schema_version") != REVIEW_SCHEMA
        or payload.get("stage") != stage.value
        or payload.get("state") != ReviewState.APPROVED.value
        or payload.get("revision") != revision.number
    ):
        return False
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        path_value = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path_value, str) or not isinstance(digest, str):
            return False
        path = _safe_existing_file(Path(path_value), f"{stage.value} review evidence")
        if sha256_file(path) != digest:
            return False
    return True


def _storyboard_rows(revision: StageRevision) -> tuple[dict[str, Any], ...]:
    for artifact in revision.artifacts:
        path = Path(artifact.path)
        if path.name != "episode.json":
            continue
        payload = _read_object(path, "storyboard episode")
        rows = payload.get("shots")
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return tuple(rows)
    raise ValueError("Storyboard revision has no episode shot snapshot")


def _provider_settings(spec: ProjectSpec) -> tuple[str, str, str, float | None]:
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
        spec.target.get("video_resolution")
        or default_video_resolution(provider)
    ).strip()
    raw_rate = spec.target.get("video_price_yuan_per_second")
    rate = float(raw_rate) if raw_rate is not None else None
    return provider, model, resolution, rate


@dataclass(frozen=True)
class VideoPreflight:
    project_id: str
    project_sha256: str
    package_sha256: str
    revision_hashes: Mapping[str, str]
    artifact_hashes: Mapping[str, str]
    repair_plan_sha256: str
    shot_ids: tuple[str, ...]
    provider: str
    model: str
    resolution: str
    output_seconds: int
    estimated_cost_yuan: float
    ready: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_ids", _safe_shot_ids(self.shot_ids))
        object.__setattr__(self, "revision_hashes", dict(self.revision_hashes))
        object.__setattr__(self, "artifact_hashes", dict(self.artifact_hashes))
        object.__setattr__(self, "blockers", tuple(map(str, self.blockers)))


@dataclass(frozen=True)
class VideoGenerationRequest:
    project_id: str
    project_sha256: str
    package_sha256: str
    revision_hashes: Mapping[str, str]
    artifact_hashes: Mapping[str, str]
    repair_plan_sha256: str
    shot_ids: tuple[str, ...]
    provider: str
    model: str
    resolution: str
    output_seconds: int
    estimated_cost_yuan: float
    schema_version: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "shot_ids", _safe_shot_ids(self.shot_ids))
        object.__setattr__(self, "revision_hashes", dict(self.revision_hashes))
        object.__setattr__(self, "artifact_hashes", dict(self.artifact_hashes))
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
            repair_plan_sha256=preflight.repair_plan_sha256,
            shot_ids=preflight.shot_ids,
            provider=preflight.provider,
            model=preflight.model,
            resolution=preflight.resolution,
            output_seconds=preflight.output_seconds,
            estimated_cost_yuan=preflight.estimated_cost_yuan,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "project_sha256": self.project_sha256,
            "package_sha256": self.package_sha256,
            "revision_hashes": dict(self.revision_hashes),
            "artifact_hashes": dict(self.artifact_hashes),
            "repair_plan_sha256": self.repair_plan_sha256,
            "shot_ids": list(self.shot_ids),
            "provider": self.provider,
            "model": self.model,
            "resolution": self.resolution,
            "output_seconds": self.output_seconds,
            "estimated_cost_yuan": round(self.estimated_cost_yuan, 4),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> VideoGenerationRequest:
        revision_hashes = value.get("revision_hashes")
        artifact_hashes = value.get("artifact_hashes")
        shot_ids = value.get("shot_ids")
        if (
            not isinstance(revision_hashes, dict)
            or not isinstance(artifact_hashes, dict)
            or not isinstance(shot_ids, list)
        ):
            raise ValueError("Video generation request is invalid")
        return cls(
            project_id=str(value["project_id"]),
            project_sha256=str(value["project_sha256"]),
            package_sha256=str(value["package_sha256"]),
            revision_hashes={str(key): str(item) for key, item in revision_hashes.items()},
            artifact_hashes={str(key): str(item) for key, item in artifact_hashes.items()},
            repair_plan_sha256=str(value.get("repair_plan_sha256") or ""),
            shot_ids=tuple(map(str, shot_ids)),
            provider=str(value["provider"]),
            model=str(value["model"]),
            resolution=str(value["resolution"]),
            output_seconds=int(value["output_seconds"]),
            estimated_cost_yuan=float(value["estimated_cost_yuan"]),
            schema_version=str(value.get("schema_version") or ""),
        )


def build_video_preflight(
    project_dir: str | Path,
    shot_ids: tuple[str, ...],
) -> VideoPreflight:
    root = _safe_project_root(project_dir)
    requested = _safe_shot_ids(shot_ids)
    spec_path = _safe_existing_file(root / "project.json", "project spec")
    package_path = _safe_existing_file(
        root / "production_package.json",
        "production package",
    )
    spec = ProjectSpec.from_dict(_read_object(spec_path, "project spec"))
    package = ProductionPackage.from_dict(
        _read_object(package_path, "production package")
    )
    blockers: list[str] = []
    revision_hashes: dict[str, str] = {}
    artifact_hashes: dict[str, str] = {}
    revisions: dict[StageName, StageRevision] = {}
    for stage in PREFLIGHT_STAGES:
        record = next(item for item in package.stages if item.stage is stage)
        try:
            revision, raw_revision = _latest_revision(root, stage)
            if revision is None or raw_revision is None:
                blockers.append(f"{stage.value} has no current revision.")
                continue
            revisions[stage] = revision
            revision_hashes[stage.value] = _canonical_hash(raw_revision)
            artifact_hashes.update(_artifact_hashes(stage, revision))
            if record.state is not StageState.PASSED or record.revision != revision.number:
                blockers.append(f"{stage.value} package revision is not current.")
            elif not _approved_revision_is_current(
                root,
                stage,
                revision,
                record.review_state,
            ):
                blockers.append(f"{stage.value} revision is not approved.")
        except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
            blockers.append(f"{stage.value} preflight failed: {exc}")
    storyboard = revisions.get(StageName.STORYBOARD)
    if storyboard is None:
        raise ValueError("Video preflight requires a storyboard revision")
    rows = _storyboard_rows(storyboard)
    row_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        shot_id = str(row.get("id") or "").strip()
        if not shot_id or shot_id in row_by_id:
            raise ValueError("Storyboard shot IDs are invalid")
        row_by_id[shot_id] = row
    unknown = tuple(shot_id for shot_id in requested if shot_id not in row_by_id)
    if unknown:
        raise ValueError("Unknown requested shot IDs: " + ", ".join(unknown))
    output_seconds = 0
    for shot_id in requested:
        value = row_by_id[shot_id].get("duration_seconds")
        if isinstance(value, bool):
            raise ValueError(f"Storyboard shot duration is invalid: {shot_id}")
        duration = int(round(float(value)))
        if duration <= 0:
            raise ValueError(f"Storyboard shot duration is invalid: {shot_id}")
        output_seconds += duration
    provider, model, resolution, rate = _provider_settings(spec)
    if provider not in {"gateway", "minimax"}:
        blockers.append("A paid gateway or minimax video provider is required.")
    if not model:
        blockers.append("Video model is not configured.")
    if not resolution:
        blockers.append("Video resolution is not configured.")
    try:
        estimate = estimate_video_cost_yuan(
            provider,
            resolution=resolution,
            output_seconds=output_seconds,
            price_yuan_per_second=rate,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        blockers.append(str(exc))
        estimate = 0.0
    repair_path = root / "impact_plans" / "active.json"
    repair_hash = ""
    if repair_path.exists():
        _safe_existing_file(repair_path, "active repair plan")
        repair_hash = sha256_file(repair_path)
        active = _read_object(repair_path, "active repair plan")
        affected = active.get("affected")
        if isinstance(affected, dict) and affected.get(StageName.VIDEO.value):
            repair_ids = tuple(map(str, affected[StageName.VIDEO.value]))
            if any(shot_id not in repair_ids for shot_id in requested):
                blockers.append("Requested shots are outside the active video repair scope.")
    project_binding = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return VideoPreflight(
        project_id=package.project_id,
        project_sha256=project_binding,
        package_sha256=sha256_file(package_path),
        revision_hashes=revision_hashes,
        artifact_hashes=artifact_hashes,
        repair_plan_sha256=repair_hash,
        shot_ids=requested,
        provider=provider,
        model=model,
        resolution=resolution,
        output_seconds=output_seconds,
        estimated_cost_yuan=estimate,
        ready=not blockers,
        blockers=tuple(blockers),
    )


def _token_directory(root: Path, *, create: bool) -> Path:
    directory = root / "runs" / ".workbench" / "tokens"
    if _path_uses_symlink(directory):
        raise ValueError("Generation token path cannot use a symlink")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if _path_uses_symlink(directory):
        raise ValueError("Generation token path cannot use a symlink")
    if not directory.is_dir():
        raise ValueError("Generation token directory is invalid")
    return directory


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_token_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    if _path_uses_symlink(path) or path.is_symlink():
        raise ValueError("Generation token path cannot use a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


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
    root = _safe_project_root(project_dir)
    current = build_video_preflight(root, preflight.shot_ids)
    if current != preflight:
        raise GenerationTokenError("Video preflight changed before token issue")
    if not preflight.ready:
        raise GenerationTokenError("Video preflight is not ready")
    request = VideoGenerationRequest.from_preflight(preflight)
    token_id = uuid4().hex
    secret = secrets.token_urlsafe(32)
    token_dir = _token_directory(root, create=True)
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
    )
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
    root = _safe_project_root(project_dir)
    token_id, secret = _parse_token(token)
    try:
        token_dir = _token_directory(root, create=False)
    except (FileNotFoundError, ValueError) as exc:
        raise GenerationTokenError("Generation token storage is invalid") from exc
    path = token_dir / f"{token_id}.json"
    lock_path = token_dir / f"{token_id}.lock"
    if lock_path.is_symlink():
        raise GenerationTokenError("Generation token lock is invalid")
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GenerationTokenError("Generation token lock is invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            try:
                record = _read_object(path, "generation token")
            except (FileNotFoundError, ValueError) as exc:
                raise GenerationTokenError("Generation token record is invalid") from exc
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
                raise GenerationTokenError("Generation token does not match the request")
            expected_digest = _token_digest(secret, request)
            if not hmac.compare_digest(
                str(record.get("request_digest") or ""),
                expected_digest,
            ):
                raise GenerationTokenError("Generation token does not match the request")
            try:
                current_preflight = build_video_preflight(root, request.shot_ids)
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
            _write_token_atomic(path, record)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
