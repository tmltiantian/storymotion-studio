from __future__ import annotations

import fcntl
import hashlib
import json
import os
import posixpath
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .character_assets import is_supported_image_file
from .gateway_video import (
    GatewayVideoClient,
    GatewayVideoError,
    GatewayVideoHTTPError,
    GatewayVideoTask,
    is_valid_mp4_file,
    prepare_gateway_video_output_target,
    validate_gateway_video_generation_settings,
)


class GatewayVideoBatchError(RuntimeError):
    pass


CLIP_STATE_SCHEMA = "motion-comic-factory.gateway-video-clip-state.v1"
LUMENX_HANDOFF_SCHEMA = "motion-comic-factory.lumenx-handoff.v1"
VIDEO_HANDOFF_SCHEMA = "motion-comic-factory.video-handoff.v1"
OPENMONTAGE_PACKAGE_SCHEMA = "motion-comic-factory.openmontage.v1"
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SAFE_TASK_STATUS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class GatewayVideoJob:
    shot_id: str
    index: int
    prompt: str
    images: tuple[str, ...]
    duration: int
    ratio: str
    resolution: str
    output_path: str
    image_roles: tuple[str, ...] = ()
    audio_path: str = ""

    def __post_init__(self) -> None:
        roles = self.image_roles or ("reference_image",) * len(self.images)
        if len(roles) != len(self.images):
            raise GatewayVideoBatchError(
                "Gateway video image roles must match reference images."
            )
        object.__setattr__(self, "image_roles", tuple(roles))

    def to_report(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "index": self.index,
            "prompt": self.prompt,
            "reference_image_count": len(self.images),
            "reference_images": [_reference_label(image) for image in self.images],
            "reference_image_roles": list(self.image_roles),
            "duration": self.duration,
            "ratio": self.ratio,
            "resolution": self.resolution,
            "output_path": self.output_path,
            "reference_audio_present": bool(self.audio_path),
        }


def _reference_label(value: str) -> str:
    if value.startswith("data:image/"):
        return "[inline-image]"
    parsed = urlsplit(value)
    if parsed.scheme.lower() in {"http", "https"}:
        return parsed.hostname or "[remote-image]"
    return Path(value).name


def _resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def gateway_endpoint_fingerprint(base_url: str) -> str:
    """Hash a normalized endpoint identity without retaining URL secrets."""
    raw_base_url = str(base_url).strip()
    try:
        parsed = urlsplit(raw_base_url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise GatewayVideoError("Gateway video endpoint is invalid.") from exc
    if scheme not in {"http", "https"} or not hostname:
        raise GatewayVideoError(
            "Gateway video endpoint must be an HTTP(S) URL with a host."
        )
    if port is not None and not 1 <= port <= 65535:
        raise GatewayVideoError("Gateway video endpoint port is invalid.")
    normalized_path = posixpath.normpath(f"/{parsed.path.lstrip('/')}")
    if normalized_path == "/.":
        normalized_path = "/"
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    port_suffix = "" if port is None or default_port else f":{port}"
    normalized = (
        f"{scheme}://{display_host}{port_suffix}{normalized_path}"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_report_destination(
    destination: Path,
    jobs: list[GatewayVideoJob],
    *,
    protected_paths: tuple[str | Path, ...] = (),
) -> None:
    resolved_destination = _resolved_path(destination)
    if resolved_destination.exists() and not resolved_destination.is_file():
        raise GatewayVideoBatchError(
            f"Gateway video report path is not a file: {destination}"
        )

    conflicts: list[tuple[str, Path]] = [
        ("input", _resolved_path(path)) for path in protected_paths
    ]
    for job in jobs:
        output = _resolved_path(job.output_path)
        conflicts.extend(
            [
                ("video output", output),
                ("resume state", _resolved_path(_clip_state_path(output))),
                ("process lock", _resolved_path(_clip_lock_path(output))),
                ("partial download", _resolved_path(output.with_suffix(output.suffix + ".part"))),
            ]
        )
        for image in job.images:
            if image.startswith("data:image/"):
                continue
            parsed = urlsplit(image)
            if parsed.scheme.lower() in {"http", "https"}:
                continue
            conflicts.append(("reference image", _resolved_path(image)))
        if job.audio_path:
            parsed_audio = urlsplit(job.audio_path)
            if parsed_audio.scheme.lower() not in {"http", "https", "data"}:
                conflicts.append(("reference audio", _resolved_path(job.audio_path)))

    for label, conflict in conflicts:
        if resolved_destination == conflict:
            raise GatewayVideoBatchError(
                f"Gateway video report path conflicts with {label}: {destination}"
            )

    probe_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.parent.is_dir():
            raise OSError("report parent is not a directory")
        descriptor, probe_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.gateway-write-test-",
        )
        probe_path = Path(probe_name)
        os.close(descriptor)
        probe_path.unlink()
    except OSError as exc:
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)
        raise GatewayVideoBatchError(
            f"Gateway video report directory is not writable: {destination.parent}"
        ) from exc


def _read_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GatewayVideoBatchError(f"{label} not found: {source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GatewayVideoBatchError(f"Unable to read {label}: {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise GatewayVideoBatchError(f"{label} must contain a JSON object: {source}")
    return data


def _frame_index(frame: dict[str, Any], fallback: int) -> int:
    value = frame.get("index")
    if isinstance(value, int) and value > 0:
        return value
    match = re.search(r"(\d+)$", str(frame.get("id") or ""))
    return int(match.group(1)) if match else fallback


def _duration(value: Any) -> int:
    if isinstance(value, bool):
        raise GatewayVideoBatchError(f"Invalid gateway video duration: {value}")
    try:
        duration = int(round(float(value)))
    except (OverflowError, TypeError, ValueError) as exc:
        raise GatewayVideoBatchError(f"Invalid gateway video duration: {value}") from exc
    if not 1 <= duration <= 3600:
        raise GatewayVideoBatchError(
            f"Gateway video duration must be between 1 and 3600 seconds: {duration}"
        )
    return duration


def _timeline_index(shot: dict[str, Any]) -> int:
    value = shot.get("index")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise GatewayVideoBatchError(f"Invalid OpenMontage timeline index: {value}")
    try:
        index = int(value.strip()) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise GatewayVideoBatchError(
            f"Invalid OpenMontage timeline index: {value}"
        ) from exc
    if index <= 0:
        raise GatewayVideoBatchError(f"Invalid OpenMontage timeline index: {value}")
    return index


def _shot_duration(frame: dict[str, Any], shot: dict[str, Any]) -> int:
    if "duration" in frame and frame["duration"] is not None:
        return _duration(frame["duration"])
    if "duration_seconds" in shot and shot["duration_seconds"] is not None:
        return _duration(shot["duration_seconds"])
    return 5


def build_gateway_video_jobs(
    handoff_path: str | Path,
    package_path: str | Path,
    *,
    limit: int = 0,
    resolution: str = "720p",
) -> list[GatewayVideoJob]:
    if limit < 0:
        raise GatewayVideoBatchError("Gateway video job limit cannot be negative.")
    if not resolution.strip():
        raise GatewayVideoBatchError("Gateway video resolution is empty.")

    handoff = _read_json(handoff_path, "video handoff")
    package = _read_json(package_path, "OpenMontage package")
    handoff_schema = handoff.get("schema_version")
    if handoff_schema not in {VIDEO_HANDOFF_SCHEMA, LUMENX_HANDOFF_SCHEMA}:
        raise GatewayVideoBatchError(
            f"Unsupported video handoff schema: {handoff.get('schema_version')}"
        )
    if package.get("schema_version") != OPENMONTAGE_PACKAGE_SCHEMA:
        raise GatewayVideoBatchError(
            f"Unsupported OpenMontage package schema: {package.get('schema_version')}"
        )
    handoff_project = str(handoff.get("project_id") or "").strip()
    package_project = str(package.get("project_id") or "").strip()
    if not handoff_project or not package_project:
        raise GatewayVideoBatchError(
            "Video handoff and OpenMontage package must include project IDs."
        )
    if handoff_project != package_project:
        raise GatewayVideoBatchError(
            "Video handoff and OpenMontage package project IDs do not match: "
            f"{handoff_project} != {package_project}"
        )

    package_assets = package.get("character_assets")
    if not isinstance(package_assets, dict) or not package_assets.get(
        "production_ready"
    ):
        raise GatewayVideoBatchError(
            "OpenMontage package character assets are not production-ready."
        )
    confirmed_reference_by_id: dict[str, str] = {}
    package_characters = package_assets.get("characters")
    if not isinstance(package_characters, list):
        raise GatewayVideoBatchError(
            "OpenMontage package character assets are missing a characters list."
        )
    package_character_ids: set[str] = set()
    for position, character in enumerate(package_characters, start=1):
        if not isinstance(character, dict):
            raise GatewayVideoBatchError(
                f"OpenMontage character asset item {position} must be an object."
            )
        character_id = str(
            character.get("character_id") or character.get("id") or ""
        ).strip()
        if character_id in package_character_ids:
            raise GatewayVideoBatchError(
                f"Duplicate OpenMontage character ID: {character_id}"
            )
        if character_id:
            package_character_ids.add(character_id)
        reference = str(character.get("reference_image_path") or "").strip()
        if character_id and reference and character.get("production_ready"):
            confirmed_reference_by_id[character_id] = reference

    if handoff_schema == VIDEO_HANDOFF_SCHEMA:
        characters = handoff.get("characters")
        frames = handoff.get("shots")
        handoff_label = "Video handoff"
    else:
        script_like = handoff.get("script_like")
        if not isinstance(script_like, dict):
            raise GatewayVideoBatchError("LumenX handoff is missing script_like.")
        characters = script_like.get("characters")
        frames = script_like.get("frames")
        handoff_label = "LumenX"
    timeline = package.get("timeline")
    if not isinstance(frames, list) or not frames:
        raise GatewayVideoBatchError(f"{handoff_label} handoff contains no frames.")
    if not isinstance(timeline, list) or not timeline:
        raise GatewayVideoBatchError("OpenMontage package contains no timeline shots.")

    reference_by_id: dict[str, str] = {}
    if isinstance(characters, list):
        handoff_character_ids: set[str] = set()
        for position, character in enumerate(characters, start=1):
            if not isinstance(character, dict):
                raise GatewayVideoBatchError(
                    f"{handoff_label} character item {position} must be an object."
                )
            character_id = str(character.get("id") or "").strip()
            if character_id in handoff_character_ids:
                raise GatewayVideoBatchError(
                    f"Duplicate {handoff_label} character ID: {character_id}"
                )
            if character_id:
                handoff_character_ids.add(character_id)
            reference = str(character.get("reference_image_path") or "").strip()
            if (
                character_id
                and reference
                and bool(character.get("reference_image_exists"))
                and Path(reference).is_file()
            ):
                if not is_supported_image_file(reference):
                    raise GatewayVideoBatchError(
                        "Invalid production character reference for "
                        f"{character_id}: {reference}"
                    )
                confirmed_reference = confirmed_reference_by_id.get(character_id)
                if not confirmed_reference or (
                    Path(confirmed_reference).expanduser().resolve()
                    != Path(reference).expanduser().resolve()
                ):
                    raise GatewayVideoBatchError(
                        f"{handoff_label} character reference does not match the production-ready "
                        f"OpenMontage asset for {character_id}."
                    )
                reference_by_id[character_id] = reference

    frames_by_index: dict[int, dict[str, Any]] = {}
    for offset, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            raise GatewayVideoBatchError(
                f"{handoff_label} frame item {offset} must be an object."
            )
        index = _frame_index(frame, offset)
        if index in frames_by_index:
            raise GatewayVideoBatchError(
                f"Duplicate {handoff_label} frame index: {index}"
            )
        frames_by_index[index] = frame

    target = package.get("target") if isinstance(package.get("target"), dict) else {}
    ratio_value = target.get("aspect_ratio", "9:16")
    ratio = "9:16" if ratio_value is None else str(ratio_value).strip()
    if not ratio:
        raise GatewayVideoBatchError("OpenMontage aspect ratio is empty.")
    jobs: list[GatewayVideoJob] = []
    output_paths: set[str] = set()
    timeline_shots: list[tuple[int, dict[str, Any]]] = []
    for position, item in enumerate(timeline, start=1):
        if not isinstance(item, dict):
            raise GatewayVideoBatchError(
                f"OpenMontage timeline item {position} must be an object."
            )
        timeline_shots.append((_timeline_index(item), item))
    seen_timeline_indexes: set[int] = set()
    seen_shot_ids: set[str] = set()
    for index, shot in sorted(timeline_shots, key=lambda item: item[0]):
        if index in seen_timeline_indexes:
            raise GatewayVideoBatchError(
                f"Duplicate OpenMontage timeline index: {index}"
            )
        seen_timeline_indexes.add(index)
        shot_id = str(shot.get("shot_id") or f"shot_{index:03d}").strip()
        if shot_id in seen_shot_ids:
            raise GatewayVideoBatchError(f"Duplicate OpenMontage shot ID: {shot_id}")
        seen_shot_ids.add(shot_id)
        frame = frames_by_index.get(index)
        if frame is None:
            raise GatewayVideoBatchError(
                f"No {handoff_label} frame matches OpenMontage shot {shot_id} at index {index}."
            )
        prompt = str(frame.get("video_prompt") or shot.get("visual_prompt") or "").strip()
        if not prompt:
            raise GatewayVideoBatchError(f"Gateway video prompt is empty for {shot_id}.")

        character_ids = frame.get("character_ids")
        if character_ids is None:
            character_ids = []
        elif not isinstance(character_ids, list):
            raise GatewayVideoBatchError(
                f"{handoff_label} character_ids must be a list for {shot_id}."
            )
        normalized_character_ids = [str(character_id) for character_id in character_ids]
        if len(set(normalized_character_ids)) != len(normalized_character_ids):
            raise GatewayVideoBatchError(
                f"{handoff_label} character_ids contains duplicates for {shot_id}."
            )
        missing_references = [
            character_id
            for character_id in normalized_character_ids
            if character_id not in reference_by_id
        ]
        if missing_references:
            raise GatewayVideoBatchError(
                f"Missing production character references for {shot_id}: "
                f"{', '.join(missing_references)}"
            )
        character_images = tuple(
            reference_by_id[character_id]
            for character_id in normalized_character_ids
        )
        expected_assets = shot.get("expected_assets")
        expected_assets = expected_assets if isinstance(expected_assets, dict) else {}
        keyframes: list[tuple[str, str]] = []
        for field, role in (("first_frame", "first_frame"), ("last_frame", "last_frame")):
            value = str(expected_assets.get(field) or "").strip()
            if value and Path(value).is_file():
                if not is_supported_image_file(value):
                    raise GatewayVideoBatchError(
                        f"Invalid {role} image for {shot_id}: {value}"
                    )
                keyframes.append((value, role))
        images = tuple(value for value, _role in keyframes) + character_images
        image_roles = tuple(role for _value, role in keyframes) + (
            "reference_image",
        ) * len(character_images)
        output_path = str(expected_assets.get("video_clip") or "").strip()
        audio_path = str(expected_assets.get("voice_audio") or "").strip()
        if audio_path and not audio_path.lower().startswith(("http://", "https://", "data:")):
            audio_path = audio_path if Path(audio_path).is_file() else ""
        if not output_path:
            raise GatewayVideoBatchError(
                f"OpenMontage video output path is missing for {shot_id}."
            )
        if Path(output_path).suffix.lower() != ".mp4":
            raise GatewayVideoBatchError(
                f"OpenMontage video output must use an .mp4 path for {shot_id}: "
                f"{output_path}"
            )
        normalized_output = str(Path(output_path).expanduser().resolve())
        if normalized_output in output_paths:
            raise GatewayVideoBatchError(
                f"Duplicate OpenMontage video output path: {output_path}"
            )
        output_paths.add(normalized_output)
        jobs.append(
            GatewayVideoJob(
                shot_id=shot_id,
                index=index,
                prompt=prompt,
                images=images,
                duration=_shot_duration(frame, shot),
                ratio=ratio,
                resolution=resolution.strip(),
                output_path=output_path,
                image_roles=image_roles,
                audio_path=audio_path,
            )
        )
    if limit:
        jobs = jobs[:limit]
    if not jobs:
        raise GatewayVideoBatchError("No gateway video jobs were planned.")
    return jobs


def write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


# Kept for the existing batch implementation and its callers.
_write_json = write_atomic_json


def _clip_state_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".gateway.json")


def _clip_lock_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".gateway.lock")


def _acquire_clip_lock(output: Path) -> int:
    path = _clip_lock_path(output)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise GatewayVideoError(
            f"Unable to open gateway video clip lock: {path}"
        ) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = json.dumps({"pid": os.getpid()}).encode("utf-8")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, metadata)
        os.fsync(descriptor)
        return descriptor
    except BlockingIOError as exc:
        os.close(descriptor)
        raise GatewayVideoError(
            "Gateway video clip is locked by another process."
        ) from exc
    except OSError as exc:
        os.close(descriptor)
        raise GatewayVideoError(
            f"Unable to acquire gateway video clip lock: {path}"
        ) from exc


def _release_clip_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(descriptor)
    except OSError:
        pass


def _read_clip_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except (OSError, json.JSONDecodeError):
        return {"_invalid_state": True}
    return data if isinstance(data, dict) else {"_invalid_state": True}


def _path_uses_symlink(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _require_safe_clip_paths(output: Path) -> None:
    for label, path in (
        ("output", output),
        ("state", _clip_state_path(output)),
        ("lock", _clip_lock_path(output)),
        ("partial download", output.with_suffix(output.suffix + ".part")),
    ):
        if _path_uses_symlink(path):
            raise GatewayVideoError(
                f"Gateway video {label} path must not use a symlink."
            )


def _prepare_clip_lock_parent(output: Path) -> None:
    _require_safe_clip_paths(output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.parent.is_dir():
            raise OSError("output parent is not a directory")
    except OSError as exc:
        raise GatewayVideoError(
            f"Gateway video output directory is not writable: {output.parent}"
        ) from exc
    _require_safe_clip_paths(output)


def _canonical_output_path(output: Path) -> str:
    if _path_uses_symlink(output):
        raise GatewayVideoError(
            "Gateway video output path must not use a symlink."
        )
    return str(output.expanduser().resolve())


def _state_matches_job(
    state: dict[str, Any],
    *,
    job: GatewayVideoJob,
    output: Path,
    signature: str,
    endpoint_fingerprint: str,
    model: str,
) -> bool:
    state_output = state.get("output_path")
    if not isinstance(state_output, str) or not state_output:
        return False
    state_output_path = Path(state_output).expanduser()
    if _path_uses_symlink(state_output_path):
        return False
    try:
        canonical_output = _canonical_output_path(output)
        canonical_state_output = str(state_output_path.resolve())
    except (GatewayVideoError, OSError):
        return False
    return bool(
        state.get("schema_version") == CLIP_STATE_SCHEMA
        and state.get("signature") == signature
        and state.get("endpoint_fingerprint_sha256")
        == endpoint_fingerprint
        and state.get("model") == model
        and state.get("shot_id") == job.shot_id
        and state_output == canonical_output
        and canonical_state_output == canonical_output
    )


def _safe_task_id(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and _SAFE_TASK_ID.fullmatch(value)
    )


def _safe_task_status(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and _SAFE_TASK_STATUS.fullmatch(value)
    )


def _resumable_task_from_state(
    state: dict[str, Any],
) -> GatewayVideoTask | None:
    if (
        state.get("status") != "submitted"
        or not _safe_task_id(state.get("task_id"))
        or not _safe_task_status(state.get("task_status"))
    ):
        return None
    return GatewayVideoTask(
        task_id=state["task_id"],
        status=state["task_status"],
    )


def _completed_state_matches_output(
    state: dict[str, Any],
    output: Path,
) -> bool:
    output_size = state.get("output_size_bytes")
    task_id = state.get("task_id")
    return bool(
        state.get("status") == "completed"
        and output.is_file()
        and not output.is_symlink()
        and is_valid_mp4_file(output)
        and isinstance(output_size, int)
        and not isinstance(output_size, bool)
        and output_size > 0
        and output_size == output.stat().st_size
        and isinstance(task_id, str)
        and (task_id == "" or _safe_task_id(task_id))
    )


def _clip_state_base(
    job: GatewayVideoJob,
    output: Path,
    *,
    signature: str,
    endpoint_fingerprint: str,
    model: str,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": CLIP_STATE_SCHEMA,
        "signature": signature,
        "endpoint_fingerprint_sha256": endpoint_fingerprint,
        "status": status,
        "model": model,
        "shot_id": job.shot_id,
        "output_path": _canonical_output_path(output),
        "duration": job.duration,
        "resolution": job.resolution,
        "reference_image_count": len(job.images),
    }


def _job_signature(
    job: GatewayVideoJob,
    *,
    model: str,
    generate_audio: bool,
    endpoint_fingerprint: str | None = None,
    reference_audio: dict[str, str] | None = None,
) -> str:
    if endpoint_fingerprint is None:
        output = Path(job.output_path)
        state_path = _clip_state_path(output)
        if _path_uses_symlink(state_path):
            raise GatewayVideoError(
                "Gateway video endpoint fingerprint is required."
            )
        state = _read_clip_state(state_path)
        state_output = state.get("output_path")
        try:
            canonical_output = _canonical_output_path(output)
        except GatewayVideoError:
            canonical_output = ""
        endpoint_fingerprint = state.get(
            "endpoint_fingerprint_sha256"
        )
        if not (
            state.get("schema_version") == CLIP_STATE_SCHEMA
            and state.get("model") == model
            and state.get("shot_id") == job.shot_id
            and isinstance(state_output, str)
            and state_output == canonical_output
            and isinstance(endpoint_fingerprint, str)
        ):
            raise GatewayVideoError(
                "Gateway video endpoint fingerprint is required."
            )
    if not isinstance(endpoint_fingerprint, str) or not _SHA256.fullmatch(
        endpoint_fingerprint
    ):
        raise GatewayVideoError(
            "Gateway video endpoint fingerprint is invalid."
        )
    references: list[dict[str, Any]] = []
    for value in job.images:
        path = Path(value)
        if path.is_file():
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as exc:
                raise GatewayVideoError(
                    f"Unable to hash gateway video reference image: {path}"
                ) from exc
            references.append(
                {
                    "path": str(path.resolve()),
                    "sha256": digest.hexdigest(),
                }
            )
        else:
            references.append(
                {
                    "value_sha256": hashlib.sha256(
                        value.encode("utf-8")
                    ).hexdigest()
                }
            )
    payload = {
        "model": model,
        "shot_id": job.shot_id,
        "prompt": job.prompt,
        "references": references,
        "reference_roles": list(job.image_roles),
        "duration": job.duration,
        "ratio": job.ratio,
        "resolution": job.resolution,
        "generate_audio": bool(generate_audio),
        "endpoint_fingerprint_sha256": endpoint_fingerprint,
    }
    if reference_audio:
        payload["reference_audio"] = reference_audio
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sanitize(message: str, api_key: str) -> str:
    sanitized = message.replace(api_key, "[redacted]") if api_key else message
    return re.sub(
        r"data:audio/[A-Za-z0-9.+-]+;base64,[^\s<'\"]+",
        "[redacted-audio]",
        sanitized,
    )


def _reference_audio_evidence(audio: str | Path | None) -> dict[str, str]:
    if audio is None:
        return {}
    value = str(audio).strip()
    if not value:
        raise GatewayVideoBatchError(
            "Gateway video single-shot reference audio is empty."
        )
    if value.lower().startswith(("https://", "http://", "data:")):
        return {
            "reference_audio_value_sha256": hashlib.sha256(
                value.encode("utf-8")
            ).hexdigest()
        }
    path = Path(value).expanduser()
    return {
        "reference_audio_path": str(path.resolve()),
        "reference_audio_sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise GatewayVideoBatchError(
            "Unable to hash gateway video reference audio."
        ) from exc
    return digest.hexdigest()


def _state_with_reference_audio(
    payload: dict[str, Any], reference_audio: dict[str, str]
) -> dict[str, Any]:
    return {**payload, **reference_audio}


def _write_report(
    destination: Path,
    report: dict[str, Any],
    report_sanitizer: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    safe_report = report_sanitizer(report) if report_sanitizer else report
    _write_json(destination, safe_report)
    return safe_report


def _validate_replacement_mode(
    *,
    overwrite: bool,
    replace_stale: bool,
) -> None:
    if overwrite and replace_stale:
        raise GatewayVideoBatchError(
            "Gateway video overwrite and replace_stale are mutually exclusive."
        )


def _validate_client_generation_settings(
    client: GatewayVideoClient,
    job: GatewayVideoJob,
) -> None:
    provider_validator = getattr(client, "validate_generation_settings", None)
    if callable(provider_validator):
        provider_validator(
            duration=job.duration,
            ratio=job.ratio,
            resolution=job.resolution,
            image_count=len(job.images),
        )
        return
    validate_gateway_video_generation_settings(
        model=client.config.model,
        duration=job.duration,
        ratio=job.ratio,
        resolution=job.resolution,
        image_count=len(job.images),
    )


def _execute_gateway_video_jobs(
    jobs: list[GatewayVideoJob],
    client: GatewayVideoClient,
    destination: Path,
    report: dict[str, Any],
    *,
    generate_audio: bool,
    allow_network: bool,
    overwrite: bool,
    replace_stale: bool = False,
    audio: str | Path | None = None,
    reference_audio: dict[str, str] | None = None,
    report_sanitizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_replacement_mode(
        overwrite=overwrite,
        replace_stale=replace_stale,
    )
    if not allow_network:
        report["blocked_reasons"] = ["Live gateway video generation is disabled."]
        return _write_report(destination, report, report_sanitizer)

    for job in jobs:
        output = Path(job.output_path)
        state_path = _clip_state_path(output)
        lock_descriptor: int | None = None
        try:
            job_audio: str | Path | None = job.audio_path or audio
            job_reference_audio = (
                _reference_audio_evidence(job_audio) if job_audio is not None else {}
            )
            endpoint_fingerprint = gateway_endpoint_fingerprint(
                client.config.base_url
            )
            signature = _job_signature(
                job,
                model=client.config.model,
                generate_audio=generate_audio,
                endpoint_fingerprint=endpoint_fingerprint,
                reference_audio=job_reference_audio or reference_audio,
            )
            _prepare_clip_lock_parent(output)
            lock_descriptor = _acquire_clip_lock(output)

            state = _read_clip_state(state_path)
            matching_state = _state_matches_job(
                state,
                job=job,
                output=output,
                signature=signature,
                endpoint_fingerprint=endpoint_fingerprint,
                model=client.config.model,
            )
            if state.get("status") == "submitting":
                raise GatewayVideoError(
                    "Gateway video candidate has an ambiguous in-flight "
                    "submission; manual resolution is required."
                )
            completed_state = bool(
                matching_state
                and _completed_state_matches_output(state, output)
            )
            if not overwrite and completed_state:
                report["results"].append(
                    {
                        "shot_id": job.shot_id,
                        "index": job.index,
                        "status": "skipped_existing",
                        "output_path": str(output),
                        "output_size_bytes": output.stat().st_size,
                        "state_path": str(state_path),
                    }
                )
                report["skipped_count"] += 1
                continue
            if (
                not overwrite
                and not replace_stale
                and matching_state
                and state.get("status") == "completed"
                and output.exists()
                and not is_valid_mp4_file(output)
            ):
                raise GatewayVideoError(
                    (
                        "Existing gateway video output is not a valid MP4; pass "
                        "--overwrite to regenerate it."
                    )
                )
            resumable_task = (
                _resumable_task_from_state(state)
                if matching_state
                else None
            )
            existing_artifacts = output.exists() or state_path.exists()
            replace_existing = False
            if overwrite:
                resumable_task = None
            elif resumable_task is None and existing_artifacts:
                if not replace_stale:
                    raise GatewayVideoError(
                        (
                            "Existing gateway video output or state does not match the "
                            "current model and settings; pass --overwrite to regenerate it."
                        )
                    )
                replace_existing = True

            prepare_gateway_video_output_target(
                output,
                overwrite=(
                    overwrite
                    or replace_existing
                    or resumable_task is not None
                ),
            )
            if resumable_task:
                report["executed"] = True
                task = resumable_task
                report["resumed_count"] += 1
                restore_settings = getattr(
                    client,
                    "restore_task_settings",
                    None,
                )
                if callable(restore_settings):
                    restore_settings(
                        task.task_id,
                        resolution=str(state.get("resolution") or job.resolution),
                        duration=int(state.get("duration") or job.duration),
                        image_count=int(
                            state.get("reference_image_count")
                            if isinstance(
                                state.get("reference_image_count"),
                                int,
                            )
                            else len(job.images)
                        ),
                    )
                result = client.complete_task(
                    task,
                    job.output_path,
                    allow_network=True,
                    overwrite=True,
                )
            else:
                submission = client.prepare_submission(
                    job.prompt,
                    images=list(job.images),
                    image_roles=list(job.image_roles),
                    audio=job_audio,
                    duration=job.duration,
                    ratio=job.ratio,
                    resolution=job.resolution,
                    generate_audio=generate_audio,
                    allow_network=True,
                )
                _write_json(
                    state_path,
                    _state_with_reference_audio(
                        _clip_state_base(
                            job,
                            output,
                            signature=signature,
                            endpoint_fingerprint=endpoint_fingerprint,
                            model=client.config.model,
                            status="submitting",
                        ),
                        job_reference_audio or reference_audio or {},
                    ),
                )
                report["executed"] = True
                try:
                    task = client.submit_prepared(
                        submission,
                        allow_network=True,
                    )
                except GatewayVideoHTTPError as exc:
                    _write_json(
                        state_path,
                        _state_with_reference_audio(
                            {
                                **_clip_state_base(
                                    job,
                                    output,
                                    signature=signature,
                                    endpoint_fingerprint=endpoint_fingerprint,
                                    model=client.config.model,
                                    status="rejected",
                                ),
                                "http_status_code": exc.status_code,
                                "task_id": "",
                            },
                            job_reference_audio or reference_audio or {},
                        ),
                    )
                    raise
                if task.task_id and not _safe_task_id(task.task_id):
                    raise GatewayVideoError(
                        "Gateway video provider returned an unsafe task ID; "
                        "submission remains ambiguous."
                    )
                if not _safe_task_status(task.status):
                    raise GatewayVideoError(
                        "Gateway video provider returned an unsafe task status; "
                        "submission remains ambiguous."
                    )
                _write_json(
                    state_path,
                    _state_with_reference_audio(
                        {
                            **_clip_state_base(
                                job,
                                output,
                                signature=signature,
                                endpoint_fingerprint=endpoint_fingerprint,
                                model=client.config.model,
                                status="submitted",
                            ),
                            "task_id": task.task_id,
                            "task_status": task.status,
                        },
                        job_reference_audio or reference_audio or {},
                    ),
                )
                result = client.complete_task(
                    task,
                    job.output_path,
                    allow_network=True,
                    overwrite=overwrite or replace_existing,
                )
            if not is_valid_mp4_file(output):
                raise GatewayVideoError(
                    f"Gateway video output is missing or is not a valid MP4 for "
                    f"{job.shot_id}: {output}"
                )
            if result.task_id and not _safe_task_id(result.task_id):
                raise GatewayVideoError(
                    "Gateway video provider returned an unsafe completed task ID."
                )
            result_report = result.to_report()
            result_report["shot_id"] = job.shot_id
            result_report["index"] = job.index
            result_report["state_path"] = str(state_path)
            _write_json(
                state_path,
                _state_with_reference_audio(
                    {
                        **_clip_state_base(
                            job,
                            output,
                            signature=signature,
                            endpoint_fingerprint=endpoint_fingerprint,
                            model=client.config.model,
                            status="completed",
                        ),
                        "output_size_bytes": output.stat().st_size,
                        "task_id": result.task_id,
                    },
                    job_reference_audio or reference_audio or {},
                ),
            )
            report["results"].append(result_report)
            report["completed_count"] += 1
        except (GatewayVideoError, OSError, ValueError) as exc:
            report["errors"].append(
                {
                    "shot_id": job.shot_id,
                    "index": job.index,
                    "error": _sanitize(str(exc), client.config.api_key),
                }
            )
            report["failed_count"] += 1
            break
        finally:
            _release_clip_lock(lock_descriptor)

    report["success"] = (
        report["completed_count"] + report["skipped_count"]
        == report["planned_count"]
        and report["failed_count"] == 0
    )
    return _write_report(destination, report, report_sanitizer)


def render_gateway_video_batch(
    handoff_path: str | Path,
    package_path: str | Path,
    client: GatewayVideoClient,
    report_path: str | Path,
    *,
    limit: int = 0,
    resolution: str = "720p",
    generate_audio: bool = False,
    allow_network: bool = False,
    overwrite: bool = False,
    replace_stale: bool = False,
) -> dict[str, Any]:
    _validate_replacement_mode(
        overwrite=overwrite,
        replace_stale=replace_stale,
    )
    jobs = build_gateway_video_jobs(
        handoff_path,
        package_path,
        limit=limit,
        resolution=resolution,
    )
    destination = Path(report_path)
    _validate_report_destination(
        destination,
        jobs,
        protected_paths=(handoff_path, package_path),
    )
    try:
        for job in jobs:
            _validate_client_generation_settings(client, job)
    except GatewayVideoError as exc:
        raise GatewayVideoBatchError(str(exc)) from exc
    report: dict[str, Any] = {
        "schema_version": "motion-comic-factory.gateway-video-batch.v1",
        "provider": getattr(client, "provider", "gateway"),
        "model": client.config.model,
        "handoff_path": str(Path(handoff_path)),
        "openmontage_package_path": str(Path(package_path)),
        "plan_ready": True,
        "planned_count": len(jobs),
        "executed": False,
        "success": False,
        "completed_count": 0,
        "skipped_count": 0,
        "resumed_count": 0,
        "failed_count": 0,
        "overwrite": overwrite,
        "replace_stale": replace_stale,
        "blocked_reasons": [],
        "jobs": [job.to_report() for job in jobs],
        "results": [],
        "errors": [],
    }
    return _execute_gateway_video_jobs(
        jobs,
        client,
        destination,
        report,
        generate_audio=generate_audio,
        allow_network=allow_network,
        overwrite=overwrite,
        replace_stale=replace_stale,
    )


def render_gateway_video_single(
    prompt: str,
    output_path: str | Path,
    client: GatewayVideoClient,
    report_path: str | Path,
    *,
    images: list[str | Path] | tuple[str | Path, ...] | None = None,
    audio: str | Path | None = None,
    duration: int = 5,
    ratio: str = "9:16",
    resolution: str = "720p",
    generate_audio: bool = False,
    allow_network: bool = False,
    overwrite: bool = False,
    replace_stale: bool = False,
    report_sanitizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_replacement_mode(
        overwrite=overwrite,
        replace_stale=replace_stale,
    )
    normalized_prompt = prompt.strip()
    normalized_ratio = ratio.strip()
    normalized_resolution = resolution.strip()
    normalized_output = str(output_path).strip()
    if not normalized_prompt:
        raise GatewayVideoBatchError("Gateway video prompt is empty.")
    if not normalized_output:
        raise GatewayVideoBatchError("Gateway video output path is empty.")
    if Path(normalized_output).suffix.lower() != ".mp4":
        raise GatewayVideoBatchError(
            f"Gateway video output must use an .mp4 path: {normalized_output}"
        )

    image_values_list: list[str] = []
    image_roles: list[str] = []
    for image in images or ():
        source = getattr(image, "source", image)
        role = getattr(image, "role", "reference_image")
        image_values_list.append(str(source))
        image_roles.append(str(role))
    image_values = tuple(image_values_list)
    job = GatewayVideoJob(
        shot_id="single",
        index=1,
        prompt=normalized_prompt,
        images=image_values,
        duration=_duration(duration),
        ratio=normalized_ratio,
        resolution=normalized_resolution,
        output_path=normalized_output,
        image_roles=tuple(image_roles),
    )
    destination = Path(report_path)
    _validate_report_destination(destination, [job])
    try:
        _validate_client_generation_settings(client, job)
        client.validate_reference_images(job.images)
        if audio is not None:
            client.validate_reference_audio(audio)
    except GatewayVideoError as exc:
        raise GatewayVideoBatchError(str(exc)) from exc
    reference_audio = _reference_audio_evidence(audio)

    report: dict[str, Any] = {
        "schema_version": "motion-comic-factory.gateway-video.v2",
        "provider": getattr(client, "provider", "gateway"),
        "model": client.config.model,
        "output_path": job.output_path,
        "state_path": str(_clip_state_path(Path(job.output_path))),
        "reference_image_count": len(job.images),
        "plan_ready": True,
        "planned_count": 1,
        "executed": False,
        "success": False,
        "completed_count": 0,
        "skipped_count": 0,
        "resumed_count": 0,
        "failed_count": 0,
        "overwrite": overwrite,
        "replace_stale": replace_stale,
        "blocked_reasons": [],
        "jobs": [job.to_report()],
        "results": [],
        "errors": [],
        "error": "",
    }
    result = _execute_gateway_video_jobs(
        [job],
        client,
        destination,
        report,
        generate_audio=generate_audio,
        allow_network=allow_network,
        overwrite=overwrite,
        replace_stale=replace_stale,
        audio=audio,
        reference_audio=reference_audio,
        report_sanitizer=report_sanitizer,
    )
    if result["errors"]:
        result["error"] = str(result["errors"][0].get("error") or "")
        result = _write_report(destination, result, report_sanitizer)
    return result
