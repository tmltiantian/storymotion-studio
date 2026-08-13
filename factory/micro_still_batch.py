from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .gateway_image import (
    GatewayImageClient,
    GatewayImageConfig,
    is_valid_image_bytes,
)
from .prompt_compiler import compile_still_prompt
from .prompt_safety import PREVIOUS_SHOT_CONTINUITY
from .schema import Episode
from .visual_timeline import MicroShot, VisualTimeline, validate_visual_timeline


SUPPORTED_STILL_MODELS = frozenset(
    {"doubao-seedream-4-5", "gpt-image-2"}
)
PRODUCTION_STILL_MODELS = frozenset({"doubao-seedream-4-5"})
PRODUCTION_STILL_SIZES = {
    "doubao-seedream-4-5": "1440x2560",
    "gpt-image-2": "1024x1536",
}
MICRO_STILL_BATCH_SCHEMA = "motion-comic-factory.micro-still-batch.v1"
_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_REPORT_SECRET_EXPRESSION = re.compile(
    r"""
    (?P<label>
        ["']?(?:authorization|(?:x[-_ ]?)?api[-_ ]?key|b64_json|base64|image_bytes)["']?
    )
    \s*[:=]\s*
    (?P<value>'[^']*'|"[^"]*"|(?:bearer\s+)?[^\s,}\]]+)
    """,
    re.IGNORECASE | re.VERBOSE,
)


class MicroStillBatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class MicroStillJob:
    micro_shot_id: str
    model: str
    prompt: str
    character_ids: tuple[str, ...]
    size: str
    output_path: str
    report_path: str

    def to_report(self) -> dict[str, Any]:
        return {
            "micro_shot_id": self.micro_shot_id,
            "model": self.model,
            "prompt": self.prompt,
            "character_ids": list(self.character_ids),
            "size": self.size,
            "output_path": self.output_path,
            "report_path": self.report_path,
        }


ImageClientFactory = Callable[[GatewayImageConfig], GatewayImageClient]


@dataclass
class _SafeOutputTarget:
    root_fd: int
    directory_fd: int
    components: tuple[str, ...]
    filename: str

    def close(self) -> None:
        os.close(self.directory_fd)
        os.close(self.root_fd)


def build_micro_still_jobs(
    episode: Episode,
    timeline: VisualTimeline,
    *,
    model: str,
    run_dir: str | Path,
    candidate_number: int,
    micro_shot_ids: Sequence[str] | None = None,
) -> list[MicroStillJob]:
    """Plan reference-free environment and object stills from a valid timeline."""
    timeline_errors = validate_visual_timeline(timeline, episode)
    if timeline_errors:
        raise MicroStillBatchError(
            "Visual timeline is invalid: " + "; ".join(timeline_errors)
        )
    if model not in SUPPORTED_STILL_MODELS:
        raise MicroStillBatchError(f"Unsupported gateway still model: {model}")
    if (
        not isinstance(candidate_number, int)
        or isinstance(candidate_number, bool)
        or not 1 <= candidate_number <= 3
    ):
        raise MicroStillBatchError(
            "A micro-shot may submit at most 3 paid still candidates."
        )

    root = _run_directory(run_dir)
    selected = _selected_ids(micro_shot_ids, timeline)
    contexts = _resolved_scene_contexts(timeline)
    jobs: list[MicroStillJob] = []
    for shot in timeline.micro_shots:
        explicitly_selected = selected is not None and shot.id in selected
        eligible, reason = _still_eligibility(shot)
        if explicitly_selected and not eligible:
            raise MicroStillBatchError(f"{shot.id} {reason}")
        if selected is not None and not explicitly_selected:
            continue
        if not eligible:
            continue
        output = _output_path(root, shot.id, model, candidate_number)
        _assert_safe_output_path(root, output)
        jobs.append(
            MicroStillJob(
                micro_shot_id=shot.id,
                model=model,
                prompt=compile_still_prompt(
                    episode,
                    shot,
                    previous_scene_context=(
                        contexts[shot.index]
                        if shot.scene_context == PREVIOUS_SHOT_CONTINUITY
                        else None
                    ),
                ),
                character_ids=(),
                size=PRODUCTION_STILL_SIZES[model],
                output_path=str(output),
                report_path=str(root / "micro_still_batch.json"),
            )
        )
    return jobs


def render_micro_still_batch(
    episode: Episode,
    timeline: VisualTimeline,
    *,
    model: str,
    run_dir: str | Path,
    candidate_number: int = 1,
    micro_shot_ids: Sequence[str] | None = None,
    config: GatewayImageConfig | None = None,
    client_factory: ImageClientFactory = GatewayImageClient,
    allow_network: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a no-charge plan or render a resumable batch of still candidates."""
    jobs = build_micro_still_jobs(
        episode,
        timeline,
        model=model,
        run_dir=run_dir,
        candidate_number=candidate_number,
        micro_shot_ids=micro_shot_ids,
    )
    report_path = (
        Path(jobs[0].report_path)
        if jobs
        else _run_directory(run_dir) / "micro_still_batch.json"
    )
    report = _initial_report(jobs, allow_network=allow_network, overwrite=overwrite)
    if not jobs:
        report["success"] = True
        api_key = config.api_key if config is not None else ""
        _write_report(report_path, report, api_key=api_key)
        return _sanitize_report(report, api_key)
    if not allow_network:
        report["blocked_count"] = len(jobs)
        report["blocked_reasons"] = ["Live image generation is disabled."]
        report["success"] = True
        _write_report(report_path, report, api_key="")
        return _sanitize_report(report, "")

    if config is None:
        report["failed_count"] = len(jobs)
        report["errors"].append(
            {"error": "Gateway image configuration is required for live generation."}
        )
        _write_report(report_path, report, api_key="")
        return _sanitize_report(report, "")

    root = _run_directory(run_dir)
    for job in jobs:
        output = Path(job.output_path)
        target: _SafeOutputTarget | None = None
        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        try:
            target = _open_safe_output_target(root, output)
            existing_output = _existing_output(target)
            if existing_output is not None and not overwrite:
                valid, size = existing_output
                if valid:
                    report["results"].append(
                        {
                            "micro_shot_id": job.micro_shot_id,
                            "status": "skipped_existing",
                            "output_path": str(output),
                            "output_size_bytes": size,
                        }
                    )
                    report["skipped_count"] += 1
                    continue
                raise MicroStillBatchError(
                    "Existing micro still output is not a valid PNG, JPEG, or WebP; "
                    "pass overwrite=True to regenerate it."
                )

            temporary_name, temporary_descriptor = _create_safe_temporary_output(target)
            client = client_factory(replace(config, model=job.model))
            try:
                result = client.generate(
                    job.prompt,
                    Path(temporary_name),
                    size=job.size,
                    n=1,
                    ref_image_path=None,
                    ref_image_paths=None,
                    output_file_descriptor=temporary_descriptor,
                )
            finally:
                os.close(temporary_descriptor)
                temporary_descriptor = None
            _require_current_target(target)
            if not _valid_temporary_output(target, temporary_name):
                raise MicroStillBatchError(
                    "Generated micro still is not a valid PNG, JPEG, or WebP image."
                )
            _install_temporary_output(target, temporary_name)
            temporary_name = None
            report["executed"] = True
            report["completed_count"] += 1
            report["results"].append(
                {
                    "micro_shot_id": job.micro_shot_id,
                    "status": "completed",
                    "output_path": str(output),
                    "output_size_bytes": _output_size(target),
                    "model": job.model,
                    "size": job.size,
                    "response_format": getattr(result, "response_format", ""),
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            report["failed_count"] += 1
            report["errors"].append(
                {"micro_shot_id": job.micro_shot_id, "error": str(exc)}
            )
            break
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if target is not None and temporary_name is not None:
                _unlink_at(target.directory_fd, temporary_name)
            if target is not None:
                target.close()

    report["success"] = (
        report["completed_count"] + report["skipped_count"] == report["planned_count"]
        and report["failed_count"] == 0
    )
    _write_report(report_path, report, api_key=config.api_key)
    return _sanitize_report(report, config.api_key)


def _initial_report(
    jobs: list[MicroStillJob], *, allow_network: bool, overwrite: bool
) -> dict[str, Any]:
    return {
        "schema_version": MICRO_STILL_BATCH_SCHEMA,
        "provider": "gateway",
        "plan_ready": True,
        "executed": False,
        "allow_network": allow_network,
        "overwrite": overwrite,
        "planned_count": len(jobs),
        "completed_count": 0,
        "skipped_count": 0,
        "blocked_count": 0,
        "failed_count": 0,
        "success": False,
        "blocked_reasons": [],
        "jobs": [job.to_report() for job in jobs],
        "results": [],
        "errors": [],
    }


def _selected_ids(
    micro_shot_ids: Sequence[str] | None, timeline: VisualTimeline
) -> set[str] | None:
    if micro_shot_ids is None:
        return None
    requested = tuple(micro_shot_ids)
    if any(not isinstance(micro_shot_id, str) for micro_shot_id in requested):
        raise MicroStillBatchError("Explicit micro-shot selection must contain IDs.")
    selected = set(requested)
    if len(selected) != len(requested):
        raise MicroStillBatchError("Explicit micro-shot selection has duplicate IDs.")
    known = {shot.id for shot in timeline.micro_shots}
    unknown = sorted(selected - known)
    if unknown:
        raise MicroStillBatchError(
            "Explicit micro-shot selection is not eligible: " + ", ".join(unknown)
        )
    return selected


def _still_eligibility(shot: MicroShot) -> tuple[bool, str]:
    if shot.character_ids:
        return False, "requires a character reference and cannot use the still route."
    if shot.camera_mode == "object_insert" or shot.purpose in {
        "establishing",
        "object",
    }:
        return True, ""
    return False, "is not eligible for the character-free still route."


def _resolved_scene_contexts(timeline: VisualTimeline) -> dict[int, str]:
    contexts: dict[int, str] = {}
    for shot in timeline.micro_shots:
        if shot.scene_context == PREVIOUS_SHOT_CONTINUITY:
            contexts[shot.index] = contexts[shot.index - 1]
        else:
            contexts[shot.index] = shot.scene_context
    return contexts


def _run_directory(run_dir: str | Path) -> Path:
    root = Path(run_dir).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise MicroStillBatchError(f"Run directory is not a directory: {run_dir}")
    return root


def _output_path(
    root: Path, micro_shot_id: str, model: str, candidate_number: int
) -> Path:
    _safe_component(micro_shot_id)
    _safe_component(model)
    return (
        root
        / "micro_stills"
        / micro_shot_id
        / model
        / f"candidate_{candidate_number:03d}.png"
    )


def _safe_component(value: str) -> None:
    if value in {".", ".."} or not _SAFE_PATH_COMPONENT.fullmatch(value):
        raise MicroStillBatchError(
            f"Micro-still ID is not a safe path component: {value!r}"
        )


def _assert_safe_output_path(root: Path, output: Path) -> None:
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise MicroStillBatchError(
            f"Micro-still output escapes run directory: {output}"
        ) from exc
    current = root
    for component in output.relative_to(root).parts[:-1]:
        current = current / component
        if current.exists() or current.is_symlink():
            try:
                current.resolve().relative_to(root)
            except ValueError as exc:
                raise MicroStillBatchError(
                    f"Micro-still output escapes run directory through symlink: {current}"
                ) from exc


def _open_safe_output_target(root: Path, output: Path) -> _SafeOutputTarget:
    _assert_safe_output_path(root, output)
    root.mkdir(parents=True, exist_ok=True)
    try:
        root_fd = os.open(root, _DIRECTORY_OPEN_FLAGS)
    except OSError as exc:
        raise MicroStillBatchError(
            f"Unable to open micro-still run directory safely: {root}"
        ) from exc
    current_fd = os.dup(root_fd)
    try:
        components = output.relative_to(root).parts[:-1]
        for component in components:
            next_fd = _open_or_create_directory_at(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return _SafeOutputTarget(
            root_fd=root_fd,
            directory_fd=current_fd,
            components=components,
            filename=output.name,
        )
    except Exception:
        os.close(current_fd)
        os.close(root_fd)
        raise


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise MicroStillBatchError(
                f"Unable to create micro-still directory safely: {name}"
            ) from exc
        try:
            return os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise MicroStillBatchError(
                f"Unable to open micro-still directory safely: {name}"
            ) from exc
    except OSError as exc:
        raise MicroStillBatchError(
            f"Unable to open micro-still directory safely: {name}"
        ) from exc


def _open_existing_directory_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise MicroStillBatchError(
            f"Micro-still output directory changed during generation: {name}"
        ) from exc


def _require_current_target(target: _SafeOutputTarget) -> None:
    current_fd = os.dup(target.root_fd)
    try:
        for component in target.components:
            next_fd = _open_existing_directory_at(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        current = os.fstat(current_fd)
        expected = os.fstat(target.directory_fd)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise MicroStillBatchError(
                "Micro-still output directory changed during generation."
            )
    finally:
        os.close(current_fd)


def _existing_output(target: _SafeOutputTarget) -> tuple[bool, int] | None:
    try:
        descriptor = os.open(
            target.filename,
            _FILE_READ_FLAGS,
            dir_fd=target.directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MicroStillBatchError(
            "Existing micro-still output cannot be opened safely; pass overwrite=True "
            "only after removing it."
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise MicroStillBatchError(
                "Existing micro-still output is not a regular file."
            )
        return is_valid_image_bytes(os.read(descriptor, 12)), metadata.st_size
    finally:
        os.close(descriptor)


def _create_safe_temporary_output(target: _SafeOutputTarget) -> tuple[str, int]:
    for _ in range(10):
        name = f".{target.filename}.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target.directory_fd,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise MicroStillBatchError(
                "Unable to create micro-still temporary output safely."
            ) from exc
        return name, descriptor
    raise MicroStillBatchError(
        "Unable to allocate a unique micro-still temporary output."
    )


def _valid_temporary_output(target: _SafeOutputTarget, name: str) -> bool:
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=target.directory_fd)
    except OSError as exc:
        raise MicroStillBatchError(
            "Generated micro-still temporary output cannot be opened safely."
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        return stat.S_ISREG(metadata.st_mode) and is_valid_image_bytes(
            os.read(descriptor, 12)
        )
    finally:
        os.close(descriptor)


def _install_temporary_output(target: _SafeOutputTarget, name: str) -> None:
    _require_current_target(target)
    try:
        os.rename(
            name,
            target.filename,
            src_dir_fd=target.directory_fd,
            dst_dir_fd=target.directory_fd,
        )
    except OSError as exc:
        raise MicroStillBatchError(
            "Unable to install micro-still output safely."
        ) from exc
    _require_current_target(target)


def _output_size(target: _SafeOutputTarget) -> int:
    existing = _existing_output(target)
    if existing is None:
        raise MicroStillBatchError("Installed micro-still output is missing.")
    return existing[1]


def _unlink_at(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError:
        return


def _write_report(path: Path, report: dict[str, Any], *, api_key: str) -> None:
    root = path.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MicroStillBatchError(
            f"Micro-still report path must not be a symlink: {path}"
        )
    safe_report = _sanitize_report(report, api_key)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=root,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(safe_report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _sanitize_report(value: Any, api_key: str) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            label = str(key)
            normalized_label = re.sub(r"[-_ ]", "", label).lower()
            if (
                "apikey" in normalized_label
                or "authorization" in normalized_label
                or "b64" in normalized_label
                or "base64" in normalized_label
                or "imagebytes" in normalized_label
            ):
                sanitized[label] = "[redacted]"
            else:
                sanitized[label] = _sanitize_report(item, api_key)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_report(item, api_key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_report(item, api_key) for item in value]
    if not isinstance(value, str):
        return value
    sanitized = value.replace(api_key, "[redacted]") if api_key else value
    sanitized = _REPORT_SECRET_EXPRESSION.sub(
        _redact_report_secret_expression,
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+",
        r"\1[redacted]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b(?:x[-_ ]?)?api[-_ ]?key\s*[:=]\s*[^\s,;]+",
        "[redacted]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b(?:b64_json|base64|image_bytes)\s*[:=]\s*[^\s,;]+",
        "[inline-image]",
        sanitized,
    )
    sanitized = re.sub(
        r"data:image/[^\s,;]+(?:;base64)?,[^\s]+", "[inline-image]", sanitized
    )
    return re.sub(r"https?://[^\s\"']+", "[remote-url]", sanitized)


def _redact_report_secret_expression(match: re.Match[str]) -> str:
    label = match.group("label")
    normalized_label = re.sub(r"[-_ ]", "", label).lower()
    replacement = (
        "[inline-image]"
        if "b64" in normalized_label or "base64" in normalized_label
        else "[redacted]"
    )
    return f"{label}: {replacement}"
