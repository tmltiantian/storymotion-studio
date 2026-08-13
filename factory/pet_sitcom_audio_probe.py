from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from .gateway_video import GatewayVideoHTTPError, is_valid_mp4_file
from .gateway_video_batch import render_gateway_video_single
from .pet_sitcom import PetSitcomPlan, _validate_plan_contract
from .pet_sitcom_audio_first import (
    build_pet_drive_audio,
    load_pet_speech_assets,
)
from .pet_sitcom_generation import (
    PetSitcomGenerationError,
    sanitize_pet_sitcom_report,
)


PROBE_SOURCE_SHOT_ID = "shot_04"
PROBE_MODEL = "doubao-seedance-2-0"
PROBE_REVIEW_GATES = (
    "reference_audio_accepted",
    "correct_doubao_identity",
    "correct_speaker_only",
    "mouth_moves_during_dialogue",
    "mouth_stays_closed_outside_dialogue",
    "natural_feline_mouth",
    "onset_offset_within_0_25_seconds",
    "no_audio_retiming_or_repetition",
)
PROBE_FRAME_TIMESTAMPS = (
    0.20,
    0.55,
    0.80,
    1.10,
    1.40,
    1.80,
    2.20,
    3.00,
    4.50,
)

PROBE_SCHEMA = "motion-comic-factory.pet-sitcom-audio-probe.v1"
PROBE_REVIEW_SCHEMA = "motion-comic-factory.pet-sitcom-audio-probe-review.v1"
_CAPABILITIES = frozenset({"supported", "unsupported", "inconclusive"})
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_NO_DURABLE_TASK_ID = "no-durable-task-id"
_COMMON_OUTCOME_FIELDS = frozenset(
    {
        "schema_version",
        "capability",
        "success",
        "executed",
        "source_shot_id",
        "model",
        "prompt_sha256",
        "references",
        "source_tts_path",
        "source_tts_sha256",
        "audio_manifest_path",
        "audio_manifest_sha256",
        "drive_audio_path",
        "drive_audio_sha256",
        "gateway_report_path",
        "gateway_report_sha256",
    }
)
_OUTCOME_FIELDS = {
    "supported": _COMMON_OUTCOME_FIELDS
    | {"probe_mp4_path", "probe_mp4_sha256", "frame_evidence"},
    "unsupported": _COMMON_OUTCOME_FIELDS | {"http_status_code"},
    "inconclusive": _COMMON_OUTCOME_FIELDS | {"task_id", "task_id_status"},
}


def run_pet_audio_drive_probe(
    plan: PetSitcomPlan,
    *,
    video_client: Any,
    allow_network: bool = False,
) -> dict[str, Any]:
    """Run the one-time Seedance reference-audio capability probe."""
    _validate_plan_contract(plan)
    _require_probe_model(video_client)
    _preflight_probe_paths(plan)
    bindings = _current_source_bindings(plan)
    existing = _read_json(plan.audio_probe_path, plan, "probe report")
    if plan.audio_probe_path.exists() or plan.audio_probe_path.is_symlink():
        _validate_persisted_probe(plan, existing, bindings)
        return existing

    base_report: dict[str, Any] = {
        "schema_version": PROBE_SCHEMA,
        "capability": "inconclusive",
        "success": False,
        "executed": False,
        "source_shot_id": PROBE_SOURCE_SHOT_ID,
        **bindings,
    }
    if not allow_network:
        return {
            **base_report,
            "blocked_reasons": [
                "Live Seedance reference-audio capability probing is disabled."
            ],
        }

    output = _probe_video_path(plan)
    gateway_report = _probe_gateway_report_path(plan)
    _ensure_probe_directories(plan)
    _preflight_probe_paths(plan)
    report = dict(base_report)
    report["executed"] = True
    try:
        result = render_gateway_video_single(
            _probe_prompt(plan),
            output,
            video_client,
            gateway_report,
            images=[Path(item["path"]) for item in bindings["references"]],
            audio=Path(bindings["drive_audio_path"]),
            duration=5,
            ratio="9:16",
            resolution="1080p",
            generate_audio=True,
            allow_network=True,
            overwrite=False,
            report_sanitizer=lambda value: sanitize_pet_sitcom_report(
                value, _client_secrets(video_client)
            ),
        )
        _sanitize_gateway_report(plan, gateway_report, video_client)
        if result.get("success") is not True or not is_valid_mp4_file(output):
            if _gateway_http_status(gateway_report, plan) == 400:
                report.update(
                    {
                        "capability": "unsupported",
                        "success": False,
                        "http_status_code": 400,
                    }
                )
            else:
                task_id = _gateway_task_id(plan, gateway_report)
                report.update(_inconclusive_outcome(task_id))
        else:
            frame_evidence = _extract_probe_frames(
                output, _probe_frame_dir(plan), plan=plan
            )
            report.update(
                {
                    "capability": "supported",
                    "success": True,
                    "probe_mp4_path": str(output.resolve()),
                    "probe_mp4_sha256": _sha256(output),
                    "frame_evidence": frame_evidence,
                }
            )
    except GatewayVideoHTTPError as exc:
        _ensure_gateway_failure_report(plan, gateway_report, exc, video_client)
        if exc.status_code == 400:
            report.update(
                {
                    "capability": "unsupported",
                    "success": False,
                    "http_status_code": 400,
                }
            )
        else:
            report.update(
                _inconclusive_outcome(_gateway_task_id(plan, gateway_report))
            )
    except Exception as exc:
        _ensure_gateway_failure_report(plan, gateway_report, exc, video_client)
        report.update(
            _inconclusive_outcome(_gateway_task_id(plan, gateway_report))
        )

    _sanitize_gateway_report(plan, gateway_report, video_client)
    report["gateway_report_path"] = str(gateway_report.resolve())
    report["gateway_report_sha256"] = _sha256(gateway_report)
    _write_json(plan.audio_probe_path, report, plan)
    if report["capability"] == "supported" and report["success"] is True:
        write_pet_audio_probe_review_template(plan)
    return _read_json(plan.audio_probe_path, plan, "probe report")


def write_pet_audio_probe_review_template(plan: PetSitcomPlan) -> Path:
    """Write the manual review template without overwriting completed review work."""
    _validate_plan_contract(plan)
    _preflight_probe_paths(plan)
    report = _read_json(plan.audio_probe_path, plan, "probe report")
    bindings = _current_source_bindings(plan)
    _validate_persisted_probe(plan, report, bindings)
    if report.get("capability") != "supported" or report.get("success") is not True:
        raise _gate_error("the capability result is not supported")

    expected_bindings = _review_bindings(plan, report)
    existing = _read_json(
        plan.audio_probe_review_path, plan, "probe review"
    )
    if existing:
        if all(existing.get(key) == value for key, value in expected_bindings.items()):
            return plan.audio_probe_review_path
        raise _gate_error("the existing manual review has stale hash bindings")

    template: dict[str, Any] = {
        "schema_version": PROBE_REVIEW_SCHEMA,
        **expected_bindings,
        "completed": False,
        "approved": False,
        "audio_onset_seconds": None,
        "mouth_onset_seconds": None,
        "audio_offset_seconds": None,
        "mouth_offset_seconds": None,
        **{gate: False for gate in PROBE_REVIEW_GATES},
        "notes": "",
    }
    _write_json(plan.audio_probe_review_path, template, plan)
    return plan.audio_probe_review_path


def require_approved_pet_audio_probe(plan: PetSitcomPlan) -> dict[str, Any]:
    """Fail closed unless the exact successful probe has strict human approval."""
    try:
        _validate_plan_contract(plan)
        _preflight_probe_paths(plan)
        report = _read_json(plan.audio_probe_path, plan, "probe report")
        bindings = _current_source_bindings(plan)
        _validate_persisted_probe(plan, report, bindings)
        if (
            report.get("capability") != "supported"
            or report.get("success") is not True
        ):
            raise _gate_error("the capability result is not supported")
        review = _read_json(
            plan.audio_probe_review_path, plan, "probe review"
        )
        if review.get("schema_version") != PROBE_REVIEW_SCHEMA:
            raise _gate_error("manual review is missing")
        expected = _review_bindings(plan, report)
        for key, value in expected.items():
            if review.get(key) != value:
                raise _gate_error(f"{key} hash binding does not match")
        if review.get("completed") is not True or review.get("approved") is not True:
            raise _gate_error("manual review is incomplete")
        if any(review.get(gate) is not True for gate in PROBE_REVIEW_GATES):
            raise _gate_error("every manual review gate must be true")
        _validate_review_timing(review)
    except PetSitcomGenerationError:
        raise
    except Exception as exc:
        raise _gate_error("current probe assets or hashes cannot be verified") from exc
    return dict(review)


def _current_source_bindings(plan: PetSitcomPlan) -> dict[str, Any]:
    shot = next(
        (item for item in plan.shots if item.shot_id == PROBE_SOURCE_SHOT_ID),
        None,
    )
    if (
        shot is None
        or shot.speaker != "doubao"
        or shot.generation_duration_seconds != 5
    ):
        raise PetSitcomGenerationError(
            "The audio-drive probe requires the fixed shot_04 Doubao source."
        )
    assets = load_pet_speech_assets(plan)
    source_asset = next(
        (item for item in assets if item.shot_id == PROBE_SOURCE_SHOT_ID),
        None,
    )
    if source_asset is None or source_asset.speaker != "doubao":
        raise PetSitcomGenerationError(
            "The audio-drive probe requires the final shot_04 Doubao TTS asset."
        )
    source_tts = _require_safe_artifact_path(
        plan, source_asset.output_path, "source TTS"
    )
    manifest = _require_safe_artifact_path(
        plan, plan.audio_manifest_path, "audio manifest"
    )
    drive_audio = _require_safe_artifact_path(
        plan,
        build_pet_drive_audio(plan, PROBE_SOURCE_SHOT_ID),
        "drive audio",
    )
    references = _probe_references(plan)
    return {
        "model": PROBE_MODEL,
        "prompt_sha256": _hash_text(_probe_prompt(plan)),
        "references": references,
        "source_tts_path": str(source_tts),
        "source_tts_sha256": source_asset.output_sha256,
        "audio_manifest_path": str(manifest),
        "audio_manifest_sha256": _sha256(manifest),
        "drive_audio_path": str(drive_audio),
        "drive_audio_sha256": _sha256(drive_audio),
    }


def _probe_references(plan: PetSitcomPlan) -> list[dict[str, str]]:
    character = next(
        (item for item in plan.characters if item.slug == "doubao"), None
    )
    scene = next((item for item in plan.scenes if item.slug == "kitchen"), None)
    if character is None or scene is None:
        raise PetSitcomGenerationError(
            "The audio-drive probe requires the Doubao and kitchen references."
        )
    result = []
    for role, path in (
        ("doubao_character", character.reference_path),
        ("kitchen_scene", scene.anchor_path),
    ):
        safe_path = _require_png(plan, path, role)
        result.append(
            {
                "role": role,
                "path": str(safe_path),
                "sha256": _sha256(safe_path),
            }
        )
    return result


def _probe_prompt(plan: PetSitcomPlan) -> str:
    shot = next(
        item for item in plan.shots if item.shot_id == PROBE_SOURCE_SHOT_ID
    )
    return (
        f"{shot.base_prompt} Capability probe: use the supplied reference audio "
        "unchanged as the only dialogue performance. Doubao is the only speaker. "
        "Align restrained natural feline mouth movement to audible speech, keep the "
        "mouth closed outside the speech, and do not retime, repeat, replace, or "
        "supplement the supplied audio."
    )


def _validate_persisted_probe(
    plan: PetSitcomPlan,
    report: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> None:
    if report.get("schema_version") != PROBE_SCHEMA:
        raise _gate_error("probe report is missing")
    capability_value = report.get("capability")
    if (
        not isinstance(capability_value, str)
        or capability_value not in _CAPABILITIES
    ):
        raise _gate_error("probe capability state is invalid")
    capability = capability_value
    if set(report) != _OUTCOME_FIELDS[capability]:
        raise _gate_error("probe capability state fields are invalid")
    if report.get("executed") is not True:
        raise _gate_error("probe capability state must be executed")
    if capability == "supported":
        if report.get("success") is not True:
            raise _gate_error("supported probe capability state is invalid")
    elif report.get("success") is not False:
        raise _gate_error(f"{capability} probe capability state is invalid")
    if capability == "unsupported":
        status = report.get("http_status_code")
        if isinstance(status, bool) or status != 400:
            raise _gate_error("unsupported probe capability state requires HTTP 400")
    if capability == "inconclusive":
        _validate_inconclusive_task(report)
    if report.get("source_shot_id") != PROBE_SOURCE_SHOT_ID:
        raise _gate_error("source shot hash binding does not match")
    for key, value in bindings.items():
        if report.get(key) != value:
            raise _gate_error(f"{key} hash binding does not match")
    gateway_report = Path(str(report.get("gateway_report_path") or ""))
    _require_hash(
        plan,
        gateway_report,
        report.get("gateway_report_sha256"),
        "gateway report",
    )
    if capability != "supported":
        return
    probe_video = Path(str(report.get("probe_mp4_path") or ""))
    _require_hash(
        plan, probe_video, report.get("probe_mp4_sha256"), "probe MP4"
    )
    if not is_valid_mp4_file(probe_video):
        raise _gate_error("probe MP4 hash cannot be verified")
    evidence = report.get("frame_evidence")
    if not isinstance(evidence, list) or len(evidence) != len(
        PROBE_FRAME_TIMESTAMPS
    ):
        raise _gate_error("fixed frame evidence hashes are missing")
    for item, timestamp in zip(
        evidence, PROBE_FRAME_TIMESTAMPS, strict=True
    ):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"timestamp_seconds", "path", "sha256"}
            or item.get("timestamp_seconds") != timestamp
        ):
            raise _gate_error("fixed frame evidence timestamps do not match")
        _require_hash(
            plan,
            Path(str(item.get("path") or "")),
            item.get("sha256"),
            "frame evidence",
        )


def _review_bindings(
    plan: PetSitcomPlan, report: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "probe_report_path": str(plan.audio_probe_path.resolve()),
        "probe_report_sha256": _sha256(plan.audio_probe_path),
        "model": report["model"],
        "prompt_sha256": report["prompt_sha256"],
        "references": report["references"],
        "source_tts_path": report["source_tts_path"],
        "source_tts_sha256": report["source_tts_sha256"],
        "drive_audio_path": report["drive_audio_path"],
        "drive_audio_sha256": report["drive_audio_sha256"],
        "gateway_report_path": report["gateway_report_path"],
        "gateway_report_sha256": report["gateway_report_sha256"],
        "probe_mp4_path": report["probe_mp4_path"],
        "probe_mp4_sha256": report["probe_mp4_sha256"],
        "frame_evidence": report["frame_evidence"],
    }


def _validate_review_timing(review: Mapping[str, Any]) -> None:
    values = {}
    for key in (
        "audio_onset_seconds",
        "mouth_onset_seconds",
        "audio_offset_seconds",
        "mouth_offset_seconds",
    ):
        value = review.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _gate_error("manual onset and offset timing is incomplete")
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 5:
            raise _gate_error("manual onset and offset timing is invalid")
        values[key] = number
    if (
        values["audio_onset_seconds"] >= values["audio_offset_seconds"]
        or values["mouth_onset_seconds"] >= values["mouth_offset_seconds"]
    ):
        raise _gate_error("manual onset and offset timing is invalid")
    if (
        abs(
            values["audio_onset_seconds"]
            - values["mouth_onset_seconds"]
        )
        > 0.25
        or abs(
            values["audio_offset_seconds"]
            - values["mouth_offset_seconds"]
        )
        > 0.25
    ):
        raise _gate_error(
            "manual mouth onset and offset errors must each be <= 0.25 seconds"
        )


def _extract_probe_frames(
    video_path: Path,
    evidence_dir: Path,
    *,
    plan: PetSitcomPlan,
) -> list[dict[str, Any]]:
    video_path = _require_safe_artifact_path(plan, video_path, "probe MP4")
    evidence_dir = _ensure_safe_directory(
        plan, evidence_dir, "probe frame directory"
    )
    frames = []
    for index, timestamp in enumerate(PROBE_FRAME_TIMESTAMPS, start=1):
        destination = _require_safe_artifact_path(
            plan,
            evidence_dir / f"frame_{index:02d}.png",
            "probe frame evidence",
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.",
            suffix=".png",
            dir=evidence_dir,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(temporary),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60.0,
            )
            _require_png(plan, temporary, "probe frame temporary file")
            _require_safe_artifact_path(
                plan, destination, "probe frame evidence"
            )
            os.replace(temporary, destination)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PetSitcomGenerationError(
                f"Unable to extract audio-drive probe frame at {timestamp:.2f}s."
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)
        _require_png(plan, destination, "probe frame evidence")
        frames.append(
            {
                "timestamp_seconds": timestamp,
                "path": str(destination.resolve()),
                "sha256": _sha256(destination),
            }
        )
    return frames


def _probe_video_path(plan: PetSitcomPlan) -> Path:
    return (
        plan.output_dir
        / "tests"
        / "audio_drive_probe"
        / f"{PROBE_SOURCE_SHOT_ID}.mp4"
    )


def _probe_gateway_report_path(plan: PetSitcomPlan) -> Path:
    return _probe_video_path(plan).with_suffix(".gateway.json")


def _probe_frame_dir(plan: PetSitcomPlan) -> Path:
    return _probe_video_path(plan).parent / "frames"


def _inconclusive_outcome(task_id: str) -> dict[str, Any]:
    if task_id and _SAFE_TASK_ID.fullmatch(task_id):
        safe_task_id = task_id
        task_id_status = "durable"
    else:
        safe_task_id = _NO_DURABLE_TASK_ID
        task_id_status = "unavailable"
    return {
        "capability": "inconclusive",
        "success": False,
        "task_id": safe_task_id,
        "task_id_status": task_id_status,
    }


def _validate_inconclusive_task(report: Mapping[str, Any]) -> None:
    task_id = report.get("task_id")
    task_id_status = report.get("task_id_status")
    if not isinstance(task_id, str) or not _SAFE_TASK_ID.fullmatch(task_id):
        raise _gate_error("inconclusive probe capability state has unsafe task ID")
    if task_id_status == "durable":
        if task_id == _NO_DURABLE_TASK_ID:
            raise _gate_error(
                "inconclusive durable task ID must identify a real task"
            )
    elif task_id_status == "unavailable":
        if task_id != _NO_DURABLE_TASK_ID:
            raise _gate_error(
                "inconclusive unavailable task ID state is invalid"
            )
    else:
        raise _gate_error(
            "inconclusive task ID status must be durable or unavailable"
        )


def _gateway_task_id(plan: PetSitcomPlan, path: Path) -> str:
    value = str(
        _read_json(path, plan, "gateway report").get("task_id") or ""
    )
    return value if _SAFE_TASK_ID.fullmatch(value) else ""


def _gateway_http_status(path: Path, plan: PetSitcomPlan) -> int | None:
    state_value = _read_json(path, plan, "gateway report").get("state_path")
    if not isinstance(state_value, str) or not state_value.strip():
        return None
    try:
        state_path = _require_safe_artifact_path(
            plan, Path(state_value), "gateway clip state"
        )
    except PetSitcomGenerationError:
        return None
    if not state_path.is_file() or state_path.is_symlink():
        return None
    state = _read_json(state_path, plan, "gateway clip state")
    status = state.get("http_status_code")
    if (
        state.get("status") == "rejected"
        and isinstance(status, int)
        and not isinstance(status, bool)
    ):
        return status
    return None


def _ensure_gateway_failure_report(
    plan: PetSitcomPlan,
    path: Path,
    error: Exception,
    video_client: Any,
) -> None:
    payload = _read_json(path, plan, "gateway report")
    if not payload:
        payload = {
            "success": False,
            "error": str(error),
        }
    _write_raw_json(
        plan,
        path,
        sanitize_pet_sitcom_report(payload, _client_secrets(video_client)),
        "gateway report",
    )


def _sanitize_gateway_report(
    plan: PetSitcomPlan, path: Path, video_client: Any
) -> None:
    payload = _read_json(path, plan, "gateway report")
    if not payload:
        payload = {"success": False, "error": "Gateway report was not produced."}
    _write_raw_json(
        plan,
        path,
        sanitize_pet_sitcom_report(payload, _client_secrets(video_client)),
        "gateway report",
    )


def _require_probe_model(video_client: Any) -> None:
    model = str(
        getattr(getattr(video_client, "config", None), "model", "")
    ).strip()
    if model != PROBE_MODEL:
        raise PetSitcomGenerationError(
            f"Audio-drive probe video client must use {PROBE_MODEL}."
        )


def _require_png(
    plan: PetSitcomPlan, path: Path, label: str
) -> Path:
    path = _require_safe_artifact_path(plan, path, label)
    if not path.is_file() or path.is_symlink():
        raise PetSitcomGenerationError(f"{label} must be a local PNG file.")
    try:
        with Image.open(path) as image:
            valid = image.format == "PNG" and image.width > 0 and image.height > 0
    except (OSError, ValueError) as exc:
        raise PetSitcomGenerationError(
            f"{label} must be a local PNG file."
        ) from exc
    if not valid:
        raise PetSitcomGenerationError(f"{label} must be a local PNG file.")
    return path


def _require_hash(
    plan: PetSitcomPlan, path: Path, expected: Any, label: str
) -> None:
    path = _require_safe_artifact_path(plan, path, label)
    if (
        not path.is_file()
        or path.is_symlink()
        or not isinstance(expected, str)
        or not expected
        or _sha256(path) != expected
    ):
        raise _gate_error(f"{label} hash does not match")


def _gate_error(detail: str) -> PetSitcomGenerationError:
    return PetSitcomGenerationError(
        f"Production shots require an approved audio-drive probe: {detail}."
    )


def _client_secrets(client: Any) -> tuple[str, ...]:
    value = getattr(getattr(client, "config", None), "api_key", "")
    return (value,) if isinstance(value, str) and value else ()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(
    path: Path, plan: PetSitcomPlan, label: str
) -> dict[str, Any]:
    path = _require_safe_artifact_path(plan, path, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(
    path: Path, payload: Mapping[str, Any], plan: PetSitcomPlan
) -> None:
    _write_raw_json(plan, path, payload, "probe JSON")


def _write_raw_json(
    plan: PetSitcomPlan,
    path: Path,
    payload: Mapping[str, Any],
    label: str,
) -> None:
    path = _require_safe_artifact_path(plan, path, label)
    parent = _ensure_safe_directory(plan, path.parent, f"{label} directory")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        _require_safe_artifact_path(plan, temporary, f"{label} temporary file")
        _require_safe_artifact_path(plan, path, label)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _preflight_probe_paths(plan: PetSitcomPlan) -> None:
    output = _probe_video_path(plan)
    paths = [
        plan.audio_probe_path,
        plan.audio_probe_review_path,
        output,
        _probe_gateway_report_path(plan),
        output.with_suffix(output.suffix + ".gateway.json"),
        output.with_suffix(output.suffix + ".gateway.lock"),
        output.with_suffix(output.suffix + ".part"),
        *(
            _probe_frame_dir(plan) / f"frame_{index:02d}.png"
            for index in range(1, len(PROBE_FRAME_TIMESTAMPS) + 1)
        ),
    ]
    for path in paths:
        _require_safe_artifact_path(plan, path, "probe artifact")
    for path in (
        output.parent,
        _probe_frame_dir(plan),
    ):
        _require_safe_artifact_path(plan, path, "probe artifact directory")


def _ensure_probe_directories(plan: PetSitcomPlan) -> None:
    _ensure_safe_directory(
        plan, _probe_video_path(plan).parent, "probe artifact directory"
    )
    _ensure_safe_directory(
        plan, _probe_frame_dir(plan), "probe frame directory"
    )


def _ensure_safe_directory(
    plan: PetSitcomPlan, path: Path, label: str
) -> Path:
    target = _require_safe_artifact_path(plan, path, label)
    root = _canonical_output_root(plan)
    current = root
    for part in target.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise PetSitcomGenerationError(
                f"{label} must not contain symlink components."
            )
        if current.exists():
            if not current.is_dir():
                raise PetSitcomGenerationError(
                    f"{label} must be a directory."
                )
        else:
            current.mkdir(mode=0o700)
    return _require_safe_artifact_path(plan, target, label)


def _require_safe_artifact_path(
    plan: PetSitcomPlan, path: Path, label: str
) -> Path:
    root = _canonical_output_root(plan)
    target = Path(os.path.abspath(path.expanduser()))
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise PetSitcomGenerationError(
            f"{label} must stay inside the canonical output directory."
        ) from exc
    current = root
    if current.is_symlink():
        raise PetSitcomGenerationError(
            f"{label} must not contain symlink components."
        )
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise PetSitcomGenerationError(
                f"{label} must not contain symlink components."
            )
    try:
        target.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise PetSitcomGenerationError(
            f"{label} must stay inside the canonical output directory."
        ) from exc
    return target


def _canonical_output_root(plan: PetSitcomPlan) -> Path:
    root = Path(os.path.abspath(plan.output_dir.expanduser()))
    try:
        resolved = root.resolve(strict=False)
    except OSError as exc:
        raise PetSitcomGenerationError(
            "Probe output directory cannot be canonicalized."
        ) from exc
    if root != resolved or root.is_symlink():
        raise PetSitcomGenerationError(
            "Probe output directory must be canonical and must not be a symlink."
        )
    return root
