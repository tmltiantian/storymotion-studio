from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from . import pet_replica_compose as composition_contract
from . import pet_replica_generation as generation_contract
from . import pet_replica_reference as reference_contract
from . import pet_replica_review as review_contract
from .dotenv import parse_dotenv
from .gateway_endpoint import gateway_endpoint_fingerprint
from .gateway_image import GatewayImageClient
from .gateway_video import GatewayVideoConfig
from .pet_replica import (
    PetReplicaPlan,
    build_pet_replica_plan,
    write_pet_replica_plan,
)
from .pet_replica_assets import (
    APPROVED_CAT_REFERENCE_ROOT,
    ASSET_REVIEW_SCHEMA_VERSION,
    ASSET_SCHEMA_VERSION,
    _expected_jobs as _expected_asset_jobs,
    generate_replica_assets,
    load_approved_replica_assets,
    prepare_replica_asset_jobs,
    write_replica_asset_review_template,
)
from .pet_replica_audio import (
    extract_replica_audio,
    validate_replica_audio_manifest,
)
from .pet_replica_compose import (
    COMPOSITION_SCHEMA_VERSION,
    compose_replica_final,
    compose_replica_pilot,
)
from .pet_replica_generation import (
    GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
    PROVIDER_REFERENCE_POLICY_VERSION,
    VIDEO_MODEL,
    ReplicaCandidate,
    build_replica_shot_jobs,
    generate_replica_candidates,
)
from .pet_replica_lipsync import validate_replica_postprocess_provenance
from .pet_replica_reference import (
    extract_reference_evidence,
    load_reviewed_shot_annotations,
    write_shot_annotation_template,
)
from .pet_replica_review import (
    MANUAL_REVIEW_GATES,
    REVIEW_SCHEMA_VERSION,
    review_replica_candidate,
)


STAGE_ORDER = (
    "plan",
    "reference",
    "audio",
    "assets",
    "generate",
    "review",
    "compose",
    "status",
    "run",
)
REFERENCE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-reference.v1"
TIMELINE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-shot-timeline.v1"
ANNOTATION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-annotations.v2"
OCR_EVIDENCE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-ocr-evidence.v1"
AUDIO_SCHEMA_VERSION = "motion-comic-factory.pet-replica-audio.v1"
SELECTION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-selection.v1"
GENERATION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-generation.v1"
SOURCE_BINDING_SCHEMA_VERSION = "motion-comic-factory.pet-replica-cli-source-binding.v1"
EXPECTED_REFERENCE_FRAMES = 163
EXPECTED_CONTACT_SHEETS = 2
EXPECTED_ASSET_IDS = frozenset(
    {
        "naitang_reference",
        "doubao_reference",
        "woman_master",
        "scene_master",
        "woman_front",
        "woman_half_body",
        "woman_left_three_quarter",
        "woman_right_three_quarter",
        "woman_full_body",
        "scene_sofa",
        "scene_table",
        "scene_phone",
    }
)
EXPECTED_ASSET_JOB_IDS = frozenset(
    {
        "woman_front",
        "woman_half_body",
        "woman_left_three_quarter",
        "woman_right_three_quarter",
        "woman_full_body",
        "scene_sofa",
        "scene_table",
        "scene_phone",
    }
)
EXPECTED_ASSET_GATES = frozenset(
    {
        "original_woman_identity",
        "woman_identity_consistent",
        "woman_costume_consistent",
        "naitang_identity_match",
        "doubao_identity_match",
        "scene_geometry_match",
        "scene_light_direction_match",
        "no_source_person_identity",
        "no_platform_branding",
        "no_generated_text",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SAFE_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_SECRET = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]+", re.I)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:[^ \t\r\n\"']+/)+[^ \t\r\n\"']*")
_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|authorization|cookie|signature|credential)",
    re.I,
)
_TERMINAL_OUTCOMES = frozenset(
    {
        "succeeded",
        "success",
        "completed",
        "failed",
        "rejected",
        "cancelled",
        "canceled",
        "not_submitted",
    }
)
FACTORY_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


class PetReplicaCLIError(RuntimeError):
    pass


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage", choices=STAGE_ORDER, required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--shot", action="append", default=[])
    parser.add_argument("--candidate", choices=(1, 2, 3), type=int, default=1)
    parser.add_argument("--pilot-only", action="store_true")
    parser.add_argument("--enable-live", action="store_true")
    parser.add_argument("--replace-stale", action="store_true")
    parser.add_argument("--postprocess-lipsync", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pet-replica")
    _add_arguments(parser)
    return parser


def add_pet_replica_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "pet-replica",
        help="Run the source-locked human-and-cat shot replica workflow",
    )
    _add_arguments(parser)
    parser.set_defaults(func=pet_replica_command)
    return parser


def pet_replica_status(plan: PetReplicaPlan) -> dict[str, Any]:
    """Inspect persisted evidence without invoking media, provider, or write APIs."""
    root = Path(plan.output_root)
    source = Path(plan.source_video)
    source_hash = _safe_file_sha256(source)
    source_binding_current = _source_binding_is_current(plan)
    reference_manifest = _read_json_object(
        root / "reference" / "reference_manifest.json"
    )
    stored_source_hash = _sha_string(reference_manifest.get("source_sha256"))
    source_current = bool(
        source_binding_current
        and source_hash
        and stored_source_hash
        and source_hash == stored_source_hash
    )

    plan_paths = (
        root / "reference" / "shot_timeline.json",
        root / "story_contract.md",
    )
    plan_valid = source_current and _timeline_is_current(
        plan,
        stored_source_hash,
    )
    plan_state = _state_for_paths(plan_paths, plan_valid)

    reference_details = _inspect_reference(
        plan,
        stored_source_hash,
        reference_manifest,
    )
    reference_valid = plan_valid and reference_details["valid"]
    reference_state = _state_for_paths(
        (
            root / "reference" / "reference_manifest.json",
            root / "reference" / "evidence_manifest.json",
            root / "reference" / "shot_annotations.json",
        ),
        reference_valid,
    )

    audio_details = _inspect_audio(plan, stored_source_hash)
    audio_valid = reference_valid and audio_details["valid"]
    audio_state = _state_for_paths(
        (root / "audio" / "audio_manifest.json",),
        audio_valid,
    )

    asset_details = _inspect_assets(
        plan,
        stored_source_hash,
        reference_details,
    )
    assets_valid = reference_valid and asset_details["valid"]
    assets_state = _state_for_paths(
        (
            root / "assets" / "asset_manifest.json",
            root / "assets" / "asset_review.json",
        ),
        assets_valid,
    )

    candidate_details = _inspect_candidates(
        plan,
        stored_source_hash,
        reference_details,
    )
    selection_details = _inspect_selections(
        plan,
        stored_source_hash,
        reference_details,
    )
    ambiguous_count = _ambiguous_submission_count(root)
    pilot_expected_ids = tuple(
        shot.shot_id for shot in plan.shots if shot.start_s < plan.pilot_end_s
    )
    approved_ids = selection_details["approved_ids"]
    expected_ids = {shot.shot_id for shot in plan.shots}
    missing_candidate_ids = expected_ids - candidate_details["current_shot_ids"]
    pilot_expected_set = set(pilot_expected_ids)
    pilot_missing_candidate_ids = (
        pilot_expected_set - candidate_details["current_shot_ids"]
    )
    pilot_selection_stale = bool(pilot_expected_set & selection_details["stale_ids"])
    pilot_approved = sum(shot_id in approved_ids for shot_id in pilot_expected_ids)
    pilot_ready = bool(
        audio_valid
        and assets_valid
        and pilot_approved == len(pilot_expected_ids)
        and not pilot_selection_stale
    )
    full_ready = bool(
        audio_valid
        and assets_valid
        and len(approved_ids) == len(plan.shots)
        and not selection_details["stale"]
    )

    blocking_candidate_stale = bool(
        candidate_details["stale_shot_ids"]
        - candidate_details["current_shot_ids"]
    )
    if blocking_candidate_stale:
        generate_state = "stale"
    elif candidate_details["current_count"] == 0:
        generate_state = "missing"
    elif candidate_details["current_count"] == len(plan.shots):
        generate_state = "current"
    else:
        generate_state = "partial"
    if selection_details["stale"]:
        review_state = "stale"
    elif not approved_ids:
        review_state = "missing"
    elif len(approved_ids) == len(plan.shots):
        review_state = "current"
    else:
        review_state = "partial"

    pilot_release = _inspect_release(
        plan,
        "pilot",
        selection_details["selection_hashes"],
        audio_details,
        reference_details,
    )
    final_release = _inspect_release(
        plan,
        "final",
        selection_details["selection_hashes"],
        audio_details,
        reference_details,
    )
    current_pilot = pilot_ready and pilot_release["valid"]
    current_final = full_ready and final_release["valid"]
    if full_ready:
        if current_final:
            compose_state = "current"
        elif final_release["exists"]:
            compose_state = "stale"
        else:
            compose_state = "missing"
    elif pilot_ready:
        if current_pilot:
            compose_state = "current"
        elif pilot_release["exists"]:
            compose_state = "stale"
        else:
            compose_state = "missing"
    else:
        compose_state = (
            "stale"
            if pilot_release["exists"] or final_release["exists"]
            else "missing"
        )

    if plan_state != "current":
        first_missing_gate = "plan"
    elif reference_state != "current":
        first_missing_gate = "reference"
    elif audio_state != "current":
        first_missing_gate = "audio"
    elif assets_state != "current":
        first_missing_gate = "assets"
    elif ambiguous_count:
        first_missing_gate = "generate"
    elif missing_candidate_ids:
        first_missing_gate = "generate"
    elif not full_ready:
        first_missing_gate = "review"
    elif not current_final:
        first_missing_gate = "compose"
    else:
        first_missing_gate = "complete"

    if plan_state != "current":
        pilot_first_missing_gate = "plan"
    elif reference_state != "current":
        pilot_first_missing_gate = "reference"
    elif audio_state != "current":
        pilot_first_missing_gate = "audio"
    elif assets_state != "current":
        pilot_first_missing_gate = "assets"
    elif ambiguous_count or pilot_missing_candidate_ids:
        pilot_first_missing_gate = "generate"
    elif not pilot_ready:
        pilot_first_missing_gate = "review"
    elif not current_pilot:
        pilot_first_missing_gate = "compose"
    else:
        pilot_first_missing_gate = "complete"

    if ambiguous_count:
        state = "blocked_ambiguous_submission"
    elif full_ready:
        state = "full_ready"
    elif pilot_ready:
        state = "pilot_ready"
    elif not source_current:
        state = "stale_source"
    else:
        state = f"needs_{first_missing_gate}"

    public_release_audio_blocked = audio_details["public_release_blocked"]
    release_state = (
        "public_release_audio_blocked"
        if public_release_audio_blocked
        else "public_release_audio_missing"
    )
    return {
        "success": True,
        "project_id": plan.project_id,
        "state": state,
        "source_current": source_current,
        "stages": {
            "plan": plan_state,
            "reference": reference_state,
            "audio": audio_state,
            "assets": assets_state,
            "generate": generate_state,
            "review": review_state,
            "compose": compose_state,
        },
        "counts": {
            "shots_expected": len(plan.shots),
            "reference_frames": reference_details["frame_count"],
            "reference_frames_expected": EXPECTED_REFERENCE_FRAMES,
            "ocr_evidence": reference_details["ocr_evidence_count"],
            "ocr_events": reference_details["ocr_event_count"],
            "audio_shots": audio_details["shot_count"],
            "assets": asset_details["asset_count"],
            "candidates": candidate_details["current_count"],
            "approved": len(approved_ids),
            "pilot_expected": len(pilot_expected_ids),
            "pilot_approved": pilot_approved,
        },
        "pilot_ready": pilot_ready,
        "full_ready": full_ready,
        "current_pilot": current_pilot,
        "current_final": current_final,
        "audio_technical_ready": audio_valid,
        "release_state": release_state,
        "public_release_audio_blocked": public_release_audio_blocked,
        "ambiguous_submission_count": ambiguous_count,
        "first_missing_gate": first_missing_gate,
        "pilot_first_missing_gate": pilot_first_missing_gate,
        "next_stage": first_missing_gate,
    }


def _task_1_media_contract(plan: PetReplicaPlan) -> dict[str, Any]:
    return {
        "duration_s": plan.duration_s,
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
    }


def _expected_task_1_timeline(
    plan: PetReplicaPlan,
    source_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "source_sha256": source_hash,
        "media_contract": _task_1_media_contract(plan),
        "pilot_end_s": plan.pilot_end_s,
        "shots": [
            {
                "shot_id": shot.shot_id,
                "index": shot.index,
                "start_s": shot.start_s,
                "end_s": shot.end_s,
                "duration_s": shot.duration_s,
                "characters": list(shot.characters),
                "speaker": shot.speaker,
                "location": shot.location,
                "framing": shot.framing,
                "action": shot.action,
                "subtitle": shot.subtitle,
                "source_audio": shot.source_audio,
            }
            for shot in plan.shots
        ],
    }


def _expected_task_1_story_contract(plan: PetReplicaPlan, source_hash: str) -> str:
    return "\n".join(
        (
            "# Source-Locked Pet Replica Story Contract",
            "",
            f"- Source SHA-256: `{source_hash}`",
            (
                "- Media contract: "
                f"{plan.width}x{plan.height}, {plan.fps} fps, "
                f"{plan.duration_s:.6f} s"
            ),
            f"- Pilot range: 0.000-{plan.pilot_end_s:.3f} s",
            "- Source audio usage: local evaluation only.",
            "",
            "| Shot | Start (s) | End (s) | Duration (s) |",
            "| --- | ---: | ---: | ---: |",
            *(
                f"| {shot.shot_id} | {shot.start_s:.6f} | "
                f"{shot.end_s:.6f} | {shot.duration_s:.6f} |"
                for shot in plan.shots
            ),
            "",
        )
    )


def _expected_initial_reference_manifest(
    plan: PetReplicaPlan,
    source_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "source_sha256": source_hash,
        "media_contract": _task_1_media_contract(plan),
    }


def _timeline_is_current(plan: PetReplicaPlan, source_hash: str) -> bool:
    root = plan.output_root
    return bool(
        _exact_json_file(
            root / "reference" / "shot_timeline.json",
            _expected_task_1_timeline(plan, source_hash),
        )
        and _exact_text_file(
            root / "story_contract.md",
            _expected_task_1_story_contract(plan, source_hash),
        )
    )


def _probed_reference_manifest_is_current(
    plan: PetReplicaPlan,
    source_hash: str,
    manifest: Mapping[str, Any],
) -> bool:
    exact_fields = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "source_sha256": source_hash,
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_sample_rate": 44100,
        "audio_channels": 2,
    }
    duration_s = manifest.get("duration_s")
    last_video_frame_s = manifest.get("last_video_frame_s")
    return bool(
        set(manifest) == {*exact_fields, "duration_s", "last_video_frame_s"}
        and all(
            _exact_json_value(manifest.get(key), value)
            for key, value in exact_fields.items()
        )
        and type(duration_s) is float
        and math.isfinite(duration_s)
        and abs(duration_s - plan.duration_s) <= 1 / plan.fps
        and type(last_video_frame_s) is float
        and math.isfinite(last_video_frame_s)
        and plan.shots[-1].start_s <= last_video_frame_s <= plan.duration_s
        and abs(last_video_frame_s * plan.fps - round(last_video_frame_s * plan.fps))
        <= 0.001
    )


def _task_1_artifacts_are_current(
    plan: PetReplicaPlan,
    source_hash: str,
) -> bool:
    manifest_path = plan.output_root / "reference" / "reference_manifest.json"
    manifest = _read_json_object(manifest_path)
    manifest_current = _exact_json_file(
        manifest_path,
        _expected_initial_reference_manifest(plan, source_hash),
    ) or bool(
        _probed_reference_manifest_is_current(plan, source_hash, manifest)
        and _exact_json_file(manifest_path, manifest)
    )
    return bool(
        source_hash and manifest_current and _timeline_is_current(plan, source_hash)
    )


def _exact_json_value(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            _exact_json_value(value[key], expected_item)
            for key, expected_item in expected.items()
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _exact_json_value(item, expected_item)
            for item, expected_item in zip(value, expected)
        )
    return value == expected


def _exact_json_file(path: Path, expected: Any) -> bool:
    if not _regular_file(path):
        return False
    try:
        actual = path.read_bytes()
        encoded = (
            json.dumps(
                expected,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (OSError, TypeError, ValueError):
        return False
    return actual == encoded


def _exact_text_file(path: Path, expected: str) -> bool:
    if not _regular_file(path):
        return False
    try:
        return path.read_bytes() == expected.encode("utf-8")
    except OSError:
        return False


def _inspect_reference(
    plan: PetReplicaPlan,
    source_hash: str,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    root = plan.output_root
    manifest_path = root / "reference" / "reference_manifest.json"
    last_video_frame_s = manifest.get("last_video_frame_s")
    manifest_valid = bool(
        source_hash
        and _probed_reference_manifest_is_current(plan, source_hash, manifest)
        and _exact_json_file(manifest_path, manifest)
    )
    evidence_path = root / "reference" / "evidence_manifest.json"
    evidence = _read_json_object(evidence_path)
    frames = evidence.get("frames")
    contacts = evidence.get("contact_sheets")
    frame_count = len(frames) if isinstance(frames, list) else 0
    contact_count = len(contacts) if isinstance(contacts, list) else 0
    evidence_valid = bool(
        manifest_valid
        and set(evidence)
        == {
            "schema_version",
            "source_sha256",
            "last_video_frame_s",
            "frames",
            "contact_sheets",
        }
        and evidence.get("schema_version") == REFERENCE_SCHEMA_VERSION
        and evidence.get("source_sha256") == source_hash
        and evidence.get("last_video_frame_s") == last_video_frame_s
        and frame_count == EXPECTED_REFERENCE_FRAMES
        and contact_count == EXPECTED_CONTACT_SHEETS
    )
    if evidence_valid:
        expected_frames = _expected_reference_frames(
            plan,
            float(last_video_frame_s),
        )
        frame_keys = {
            "command",
            "image_path",
            "image_sha256",
            "label",
            "shot_id",
            "source_sha256",
            "timestamp_s",
        }
        for item, expected in zip(frames, expected_frames):
            expected_path, expected_shot_id, expected_label, expected_timestamp = (
                expected
            )
            timestamp = item.get("timestamp_s") if isinstance(item, Mapping) else None
            if not (
                isinstance(item, Mapping)
                and set(item) == frame_keys
                and item.get("image_path") == expected_path
                and item.get("shot_id") == expected_shot_id
                and item.get("label") == expected_label
                and _finite_number(timestamp)
                and math.isclose(
                    float(timestamp),
                    expected_timestamp,
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
                and isinstance(item.get("command"), str)
                and bool(item.get("command"))
                and item.get("source_sha256") == source_hash
                and _relative_binding_is_current(
                    root,
                    item,
                    path_key="image_path",
                    hash_key="image_sha256",
                )
            ):
                evidence_valid = False
                break
        expected_contacts = (
            ("reference/contact_sheets/pilot_4x3.jpg", "4x3"),
            ("reference/contact_sheets/full_01_5x8.jpg", "5x8"),
        )
        contact_keys = {
            "image_path",
            "image_sha256",
            "layout",
            "source_sha256",
        }
        if evidence_valid:
            for item, (expected_path, expected_layout) in zip(
                contacts,
                expected_contacts,
            ):
                if not (
                    isinstance(item, Mapping)
                    and set(item) == contact_keys
                    and item.get("image_path") == expected_path
                    and item.get("layout") == expected_layout
                    and item.get("source_sha256") == source_hash
                    and _relative_binding_is_current(
                        root,
                        item,
                        path_key="image_path",
                        hash_key="image_sha256",
                    )
                ):
                    evidence_valid = False
                    break

    annotations_path = root / "reference" / "shot_annotations.json"
    annotations = _read_json_object(annotations_path)
    annotation_items = annotations.get("shots")
    annotations_valid = bool(
        annotations.get("schema_version") == ANNOTATION_SCHEMA_VERSION
        and set(annotations) == {"schema_version", "caption_safe_region", "shots"}
        and isinstance(annotation_items, list)
        and len(annotation_items) == len(plan.shots)
    )
    ocr_evidence_count = 0
    ocr_event_count = 0
    parsed_annotations: tuple[Any, ...] = ()
    if annotations_valid:
        try:
            safe_region = reference_contract._caption_safe_region(
                annotations.get("caption_safe_region"),
                plan,
            )
            parsed_annotations = tuple(
                reference_contract._annotation_from_payload(
                    item,
                    plan,
                    index,
                    safe_region,
                    require_ocr_events=True,
                    require_ocr_evidence=True,
                    source_sha256=source_hash,
                )
                for index, item in enumerate(annotation_items)
            )
            event_ids = [
                event.event_id
                for annotation in parsed_annotations
                for event in annotation.ocr_events
            ]
            detection_ids = [
                event.detection_id
                for annotation in parsed_annotations
                for event in annotation.ocr_events
            ]
            annotations_valid = bool(
                len(event_ids) == len(set(event_ids))
                and len(detection_ids) == len(set(detection_ids))
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            annotations_valid = False
        if annotations_valid:
            ocr_evidence_count = sum(
                annotation.ocr_evidence is not None for annotation in parsed_annotations
            )
            ocr_event_count = sum(
                len(annotation.ocr_events) for annotation in parsed_annotations
            )
    annotations_sha256 = (
        _safe_file_sha256(annotations_path) if annotations_valid else ""
    )
    return {
        "valid": manifest_valid and evidence_valid and annotations_valid,
        "manifest_valid": manifest_valid,
        "evidence_valid": evidence_valid,
        "annotations_valid": annotations_valid,
        "evidence_manifest_sha256": (
            _safe_file_sha256(evidence_path) if evidence_valid else ""
        ),
        "evidence_inventory_sha256": (_json_sha256(evidence) if evidence_valid else ""),
        "evidence_image_sha256s": (
            frozenset(
                item["image_sha256"]
                for item in (*frames, *contacts)
                if isinstance(item, Mapping)
            )
            if evidence_valid
            else frozenset()
        ),
        "annotations_sha256": annotations_sha256,
        "annotations": parsed_annotations if annotations_valid else (),
        "last_video_frame_s": (float(last_video_frame_s) if manifest_valid else None),
        "frame_count": frame_count,
        "contact_count": contact_count,
        "ocr_evidence_count": ocr_evidence_count,
        "ocr_event_count": ocr_event_count,
    }


def _expected_reference_frames(
    plan: PetReplicaPlan,
    last_video_frame_s: float,
) -> tuple[tuple[str, str, str, float], ...]:
    records: list[tuple[str, str, str, float]] = []
    for shot in plan.shots:
        for label, timestamp_s in (
            ("start", shot.start_s),
            ("middle", (shot.start_s + shot.end_s) / 2),
            (
                "end",
                min(
                    max(shot.start_s, shot.end_s - 1 / plan.fps),
                    last_video_frame_s,
                ),
            ),
        ):
            records.append(
                (
                    f"reference/shots/{shot.shot_id}/{label}.jpg",
                    shot.shot_id,
                    label,
                    timestamp_s,
                )
            )
    for prefix, count, end_s in (
        ("pilot", 12, plan.pilot_end_s),
        ("full_01", 40, plan.duration_s),
    ):
        for index, timestamp_s in enumerate(
            _reference_sample_timestamps(
                0.0,
                end_s,
                count,
                plan.fps,
                last_video_frame_s,
            ),
            start=1,
        ):
            records.append(
                (
                    f"reference/contact_sheets/{prefix}_frames/{index:03d}.jpg",
                    _reference_shot_id_at(plan, timestamp_s),
                    f"{prefix}_{index:03d}",
                    timestamp_s,
                )
            )
    return tuple(records)


def _reference_sample_timestamps(
    start_s: float,
    end_s: float,
    count: int,
    fps: int,
    last_video_frame_s: float,
) -> tuple[float, ...]:
    final = min(max(start_s, end_s - 1 / fps), last_video_frame_s)
    increment = (final - start_s) / (count - 1)
    return tuple(start_s + index * increment for index in range(count))


def _reference_shot_id_at(plan: PetReplicaPlan, timestamp_s: float) -> str:
    for shot in plan.shots:
        if shot.start_s <= timestamp_s < shot.end_s:
            return shot.shot_id
    return plan.shots[-1].shot_id


def _finite_number(value: Any) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _inspect_audio(plan: PetReplicaPlan, source_hash: str) -> dict[str, Any]:
    root = plan.output_root
    payload = _read_json_object(root / "audio" / "audio_manifest.json")
    shots = payload.get("shots")
    shot_count = len(shots) if isinstance(shots, Mapping) else 0
    source_timeline = payload.get("source_timeline")
    raw_aac_timeline = payload.get("raw_aac_timeline")
    normalized_payload = payload.get("normalized_payload")
    top_level_keys = {
        "schema_version",
        "source_sha256",
        "full_source",
        "shots",
        "source_timeline",
        "raw_aac_timeline",
        "normalized_payload",
        "usage_scope",
        "public_release_ready",
        "public_release_blocker",
    }
    valid = bool(
        set(payload) == top_level_keys
        and payload.get("schema_version") == AUDIO_SCHEMA_VERSION
        and payload.get("source_sha256") == source_hash
        and payload.get("usage_scope") == "local_evaluation_only"
        and payload.get("public_release_ready") is False
        and payload.get("public_release_blocker")
        == "Replace or license the source audio."
        and isinstance(shots, Mapping)
        and set(shots) == {shot.shot_id for shot in plan.shots}
        and _audio_record_is_current(
            root,
            payload.get("full_source"),
            expected_path="audio/source_audio.aac",
            expected_shot_id=None,
            expected_duration_s=plan.duration_s,
            expected_sample_rate=44100,
            expected_channels=2,
            expected_codec="aac",
            expected_start_s=0.0,
            expected_end_s=plan.duration_s,
            fps=plan.fps,
        )
        and _audio_timeline_is_current(
            source_timeline,
            expected_logical_duration_s=plan.duration_s,
            expected_end_s=plan.duration_s,
            require_zero_start=False,
        )
        and _audio_timeline_is_current(
            raw_aac_timeline,
            expected_logical_duration_s=None,
            expected_end_s=None,
            require_zero_start=True,
        )
        and isinstance(source_timeline, Mapping)
        and isinstance(raw_aac_timeline, Mapping)
        and raw_aac_timeline.get("sample_rate") == source_timeline.get("sample_rate")
        and raw_aac_timeline.get("packet_count") == source_timeline.get("packet_count")
        and _payload_evidence_is_current(normalized_payload)
    )
    if valid:
        for shot in plan.shots:
            record = shots.get(shot.shot_id)
            if not _audio_record_is_current(
                root,
                record,
                expected_path=f"audio/drive/{shot.shot_id}.wav",
                expected_shot_id=shot.shot_id,
                expected_duration_s=shot.duration_s,
                expected_sample_rate=48000,
                expected_channels=2,
                expected_codec="pcm_s16le",
                expected_start_s=shot.start_s,
                expected_end_s=shot.end_s,
                fps=plan.fps,
            ):
                valid = False
                break
    full_source = payload.get("full_source")
    local_evaluation_only = bool(
        payload.get("usage_scope") == "local_evaluation_only"
        and payload.get("public_release_ready") is False
        and payload.get("public_release_blocker")
        == "Replace or license the source audio."
    )
    return {
        "valid": valid,
        "shot_count": shot_count,
        "manifest_sha256": (
            _safe_file_sha256(root / "audio" / "audio_manifest.json") if valid else ""
        ),
        "full_source_sha256": (
            str(full_source.get("sha256"))
            if valid and isinstance(full_source, Mapping)
            else ""
        ),
        "local_evaluation_only": local_evaluation_only,
        "public_release_blocked": not valid or local_evaluation_only,
    }


def _audio_record_is_current(
    root: Path,
    value: Any,
    *,
    expected_path: str,
    expected_shot_id: str | None,
    expected_duration_s: float,
    expected_sample_rate: int,
    expected_channels: int,
    expected_codec: str,
    expected_start_s: float,
    expected_end_s: float,
    fps: int,
) -> bool:
    record_keys = {
        "shot_id",
        "path",
        "sha256",
        "duration_s",
        "sample_rate",
        "channels",
        "codec",
        "source_start_s",
        "source_end_s",
    }
    return bool(
        isinstance(value, Mapping)
        and set(value) == record_keys
        and value.get("path") == expected_path
        and value.get("shot_id") == expected_shot_id
        and _finite_number(value.get("duration_s"))
        and float(value["duration_s"]) > 0
        and abs(float(value["duration_s"]) - expected_duration_s) <= 1 / fps
        and value.get("sample_rate") == expected_sample_rate
        and not isinstance(value.get("sample_rate"), bool)
        and value.get("channels") == expected_channels
        and not isinstance(value.get("channels"), bool)
        and value.get("codec") == expected_codec
        and _finite_number(value.get("source_start_s"))
        and _finite_number(value.get("source_end_s"))
        and math.isclose(
            float(value["source_start_s"]),
            expected_start_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        and math.isclose(
            float(value["source_end_s"]),
            expected_end_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        and float(value["source_end_s"]) > float(value["source_start_s"])
        and _relative_binding_is_current(root, value)
    )


def _audio_timeline_is_current(
    value: Any,
    *,
    expected_logical_duration_s: float | None,
    expected_end_s: float | None,
    require_zero_start: bool,
) -> bool:
    keys = {
        "sample_rate",
        "packet_count",
        "first_packet_pts_s",
        "first_packet_duration_s",
        "last_packet_pts_s",
        "last_packet_duration_s",
        "last_packet_end_s",
        "packet_span_s",
        "skip_samples",
        "discard_padding",
        "logical_duration_s",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        return False
    sample_rate = value.get("sample_rate")
    packet_count = value.get("packet_count")
    skip_samples = value.get("skip_samples")
    discard_padding = value.get("discard_padding")
    numbers = (
        value.get("first_packet_pts_s"),
        value.get("first_packet_duration_s"),
        value.get("last_packet_pts_s"),
        value.get("last_packet_duration_s"),
        value.get("last_packet_end_s"),
        value.get("packet_span_s"),
    )
    if not (
        sample_rate == 44100
        and not isinstance(sample_rate, bool)
        and isinstance(packet_count, int)
        and not isinstance(packet_count, bool)
        and packet_count > 0
        and all(_finite_number(item) for item in numbers)
        and float(value["first_packet_duration_s"]) > 0
        and float(value["last_packet_duration_s"]) > 0
        and float(value["packet_span_s"]) > 0
        and (
            skip_samples is None
            or (
                isinstance(skip_samples, int)
                and not isinstance(skip_samples, bool)
                and skip_samples >= 0
            )
        )
        and (
            discard_padding is None
            or (
                isinstance(discard_padding, int)
                and not isinstance(discard_padding, bool)
                and discard_padding >= 0
            )
        )
    ):
        return False
    tolerance = 1 / sample_rate
    first_pts = float(value["first_packet_pts_s"])
    last_pts = float(value["last_packet_pts_s"])
    last_duration = float(value["last_packet_duration_s"])
    last_end = float(value["last_packet_end_s"])
    packet_span = float(value["packet_span_s"])
    if not (
        math.isclose(last_end, last_pts + last_duration, abs_tol=tolerance)
        and math.isclose(packet_span, last_end - first_pts, abs_tol=tolerance)
    ):
        return False
    if require_zero_start:
        if not math.isclose(first_pts, 0.0, abs_tol=tolerance):
            return False
    elif first_pts > 0:
        return False
    elif first_pts < 0 and not (
        isinstance(skip_samples, int)
        and skip_samples > 0
        and math.isclose(
            -first_pts,
            skip_samples / sample_rate,
            abs_tol=tolerance,
        )
    ):
        return False
    elif first_pts == 0 and skip_samples not in (None, 0):
        return False
    logical_duration = value.get("logical_duration_s")
    if expected_logical_duration_s is None:
        if logical_duration is not None:
            return False
    elif not (
        _finite_number(logical_duration)
        and math.isclose(
            float(logical_duration),
            expected_logical_duration_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    ):
        return False
    return bool(
        expected_end_s is None
        or math.isclose(last_end, expected_end_s, abs_tol=tolerance)
    )


def _payload_evidence_is_current(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"sha256", "byte_count"}:
        return False
    byte_count = value.get("byte_count")
    return bool(
        _valid_sha256(value.get("sha256"))
        and isinstance(byte_count, int)
        and not isinstance(byte_count, bool)
        and byte_count > 0
    )


def _inspect_assets(
    plan: PetReplicaPlan,
    source_hash: str,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    root = plan.output_root
    manifest_path = root / "assets" / "asset_manifest.json"
    manifest = _read_json_object(manifest_path)
    assets = manifest.get("assets")
    jobs = manifest.get("jobs")
    asset_count = len(assets) if isinstance(assets, list) else 0
    expected_jobs = _expected_asset_jobs(plan)
    expected_job_payloads = []
    for job in expected_jobs:
        expected_job_payloads.append(
            {
                "asset_id": job.asset_id,
                "kind": job.kind,
                "output_path": job.output_path.relative_to(root).as_posix(),
                "prompt": job.prompt,
                "negative_prompt": job.negative_prompt,
                "model": job.model,
                "size": job.size,
            }
        )
    expected_asset_ids = (
        "naitang_reference",
        "doubao_reference",
        "woman_master",
        "scene_master",
        *(job.asset_id for job in expected_jobs),
    )
    manifest_keys = {
        "schema_version",
        "source_sha256",
        "assets",
        "jobs",
        "live_generation_enabled",
        "evidence_manifest_sha256",
        "evidence_frame_count",
        "evidence_contact_sheet_count",
    }
    valid = bool(
        set(manifest) == manifest_keys
        and manifest.get("schema_version") == ASSET_SCHEMA_VERSION
        and manifest.get("source_sha256") == source_hash
        and manifest.get("live_generation_enabled") is True
        and isinstance(assets, list)
        and isinstance(jobs, list)
        and len(assets) == len(expected_asset_ids)
        and [
            item.get("asset_id") if isinstance(item, Mapping) else None
            for item in assets
        ]
        == list(expected_asset_ids)
        and jobs == expected_job_payloads
        and manifest.get("evidence_manifest_sha256")
        == reference.get("evidence_inventory_sha256")
        and manifest.get("evidence_frame_count") == EXPECTED_REFERENCE_FRAMES
        and manifest.get("evidence_contact_sheet_count") == EXPECTED_CONTACT_SHEETS
    )
    record_keys = {
        "asset_id",
        "kind",
        "path",
        "sha256",
        "width",
        "height",
        "provenance",
        "source_sha256",
        "provider",
        "model",
        "prompt",
        "creation_mode",
        "reference_asset_id",
        "reference_path",
        "reference_sha256",
        "source_id",
    }
    if valid:
        valid = all(
            isinstance(item, Mapping)
            and set(item) == record_keys
            and item.get("width") == 1440
            and item.get("height") == 2560
            and _relative_binding_is_current(root, item)
            for item in assets
        )
    if valid:
        for index, (asset_id, filename) in enumerate(
            (
                ("naitang_reference", "奶糖_reference.png"),
                ("doubao_reference", "豆包_reference.png"),
            )
        ):
            item = assets[index]
            approved_source = _approved_cat_source_file(item.get("source_id"))
            approved_source_sha256 = _safe_file_sha256(approved_source)
            if not (
                item.get("asset_id") == asset_id
                and item.get("kind") == "cat_reference"
                and item.get("path") == f"assets/characters/{filename}"
                and item.get("provenance") == "approved_pet_output"
                and approved_source is not None
                and approved_source_sha256
                and item.get("source_sha256") == approved_source_sha256
                and item.get("sha256") == approved_source_sha256
                and item.get("provider") == "local"
                and item.get("model") == "approved_pet_reference"
                and item.get("prompt")
                == (
                    "Immutable approved cat identity reference copied without "
                    "modification."
                )
                and item.get("creation_mode") == "copied_approved_cat_reference"
                and item.get("reference_asset_id") is None
                and item.get("reference_path") is None
                and item.get("reference_sha256") is None
            ):
                valid = False
                break
    master_contracts = (
        (
            "woman_master",
            "assets/masters/woman_master.png",
            "project_original_woman_master",
            "Project-original photorealistic adult woman identity master.",
        ),
        (
            "scene_master",
            "assets/masters/scene_master.png",
            "project_empty_scene_master",
            "Project-original empty photographed apartment geometry master.",
        ),
    )
    if valid:
        for item, contract in zip(assets[2:4], master_contracts):
            asset_id, path, provenance, prompt = contract
            source = _project_master_source_file(
                root,
                item.get("source_id"),
                reference.get("evidence_image_sha256s"),
            )
            if not (
                item.get("asset_id") == asset_id
                and item.get("kind") == "master_reference"
                and item.get("path") == path
                and item.get("provenance") == provenance
                and source is not None
                and item.get("source_sha256") == _safe_file_sha256(source)
                and item.get("sha256") == item.get("source_sha256")
                and item.get("provider") == "local"
                and item.get("model") == "project_master_reference"
                and item.get("prompt") == prompt
                and item.get("creation_mode") == "copied_project_master"
                and item.get("reference_asset_id") is None
                and item.get("reference_path") is None
                and item.get("reference_sha256") is None
            ):
                valid = False
                break
    if valid:
        master_by_kind = {
            "woman": assets[2],
            "scene": assets[3],
        }
        for item, job in zip(assets[4:], expected_jobs):
            reference_record = master_by_kind[job.kind]
            if not (
                item.get("asset_id") == job.asset_id
                and item.get("kind") == job.kind
                and item.get("path") == job.output_path.relative_to(root).as_posix()
                and item.get("provenance") == "gateway_generated"
                and item.get("source_id") is None
                and item.get("source_sha256") is None
                and item.get("provider") == "gateway"
                and item.get("model") == job.model
                and item.get("prompt") == job.full_prompt
                and item.get("creation_mode") == "generated_anchor"
                and item.get("reference_asset_id") == reference_record.get("asset_id")
                and item.get("reference_path") == reference_record.get("path")
                and item.get("reference_sha256") == reference_record.get("sha256")
            ):
                valid = False
                break
    review = _read_json_object(root / "assets" / "asset_review.json")
    gates = review.get("gates")
    snapshots = review.get("assets")
    expected_snapshots = (
        [
            {key: value for key, value in item.items() if key != "kind"}
            for item in assets
        ]
        if isinstance(assets, list)
        and all(isinstance(item, Mapping) for item in assets)
        else []
    )
    required_review_keys = {
        "schema_version",
        "source_sha256",
        "asset_manifest_sha256",
        "evidence_manifest_sha256",
        "evidence_frame_count",
        "evidence_contact_sheet_count",
        "manual_review_required",
        "gates",
        "assets",
    }
    review_valid = bool(
        valid
        and required_review_keys <= set(review)
        and review.get("schema_version") == ASSET_REVIEW_SCHEMA_VERSION
        and review.get("source_sha256") == source_hash
        and review.get("asset_manifest_sha256") == _safe_file_sha256(manifest_path)
        and review.get("evidence_manifest_sha256")
        == reference.get("evidence_inventory_sha256")
        and review.get("evidence_frame_count") == EXPECTED_REFERENCE_FRAMES
        and review.get("evidence_contact_sheet_count") == EXPECTED_CONTACT_SHEETS
        and review.get("manual_review_required") is False
        and isinstance(gates, Mapping)
        and set(gates) == EXPECTED_ASSET_GATES
        and all(gates.get(gate) is True for gate in EXPECTED_ASSET_GATES)
        and snapshots == expected_snapshots
    )
    return {
        "valid": valid and review_valid,
        "asset_count": asset_count,
    }


def _approved_cat_source_file(value: Any) -> Path | None:
    relative = _canonical_relative_path(value)
    if relative is None or relative.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }:
        return None
    root = APPROVED_CAT_REFERENCE_ROOT.expanduser().resolve(strict=False)
    return _relative_file(root, value)


def _project_master_source_file(
    root: Path,
    value: Any,
    trusted_evidence_hashes: Any,
) -> Path | None:
    relative = _canonical_relative_path(value)
    if relative is None or any(
        part.lower() in {"reference", "frames", "source_frames", "source-frames"}
        for part in relative.parts[:-1]
    ):
        return None
    source = _relative_file(root, value)
    if source is None or _is_inside(root / "assets" / "masters", source):
        return None
    trusted = (
        trusted_evidence_hashes
        if isinstance(trusted_evidence_hashes, (set, frozenset))
        else frozenset()
    )
    return None if _safe_file_sha256(source) in trusted else source


def _inspect_candidates(
    plan: PetReplicaPlan,
    source_hash: str,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    root = plan.output_root
    current_shot_ids: set[str] = set()
    stale_shot_ids: set[str] = set()
    candidate_count = 0
    stale = False
    for shot in plan.shots:
        shot_root = root / "shots" / shot.shot_id
        if not shot_root.is_dir() or shot_root.is_symlink():
            continue
        for number in (1, 2, 3):
            candidate = shot_root / f"candidate_{number:02d}.mp4"
            provenance_path = candidate.with_suffix(".provenance.json")
            if not candidate.exists() and not provenance_path.exists():
                continue
            provenance = _read_json_object(provenance_path)
            output_hash = _safe_file_sha256(candidate)
            annotation = _selection_annotation(reference, shot.shot_id)
            drive_audio = _expected_review_drive_audio(root, shot, annotation)
            valid = bool(
                output_hash
                and _selection_provenance_is_current(
                    root,
                    shot,
                    number,
                    output_hash,
                    candidate.relative_to(root).as_posix(),
                    source_hash,
                    annotation,
                    drive_audio,
                    provenance,
                )
            )
            if valid:
                current_shot_ids.add(shot.shot_id)
                candidate_count += 1
            else:
                stale = True
                stale_shot_ids.add(shot.shot_id)
    return {
        "current_count": len(current_shot_ids),
        "current_shot_ids": current_shot_ids,
        "candidate_count": candidate_count,
        "stale": stale,
        "stale_shot_ids": stale_shot_ids,
    }


def _inspect_selections(
    plan: PetReplicaPlan,
    source_hash: str,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    root = plan.output_root
    approved_ids: set[str] = set()
    selection_hashes: dict[str, str] = {}
    stale_ids: set[str] = set()
    stale = False
    for shot in plan.shots:
        path = root / "shots" / shot.shot_id / "selection.json"
        if not path.exists():
            continue
        selection = _read_json_object(path)
        valid, candidate_hash = _selection_is_current(
            root,
            shot,
            selection,
            source_hash,
            reference,
        )
        if valid:
            approved_ids.add(shot.shot_id)
            selection_hashes[shot.shot_id] = candidate_hash
        else:
            stale = True
            stale_ids.add(shot.shot_id)
    return {
        "approved_ids": approved_ids,
        "selection_hashes": selection_hashes,
        "stale": stale,
        "stale_ids": stale_ids,
    }


def _selection_is_current(
    root: Path,
    shot: Any,
    selection: Mapping[str, Any],
    source_hash: str,
    reference: Mapping[str, Any],
) -> tuple[bool, str]:
    selection_keys = {
        "schema_version",
        "shot_id",
        "candidate_number",
        "candidate_path",
        "candidate_sha256",
        "manual_review_note",
        "manual_gates",
        "quality_approved",
        "quality_review_path",
        "quality_review_sha256",
        "quality_bindings_sha256",
        "quality_provenance_path",
        "quality_provenance_sha256",
        "quality_source_evidence_sha256",
        "quality_drive_audio",
        "quality_evidence",
    }
    number = selection.get("candidate_number")
    if (
        set(selection) != selection_keys
        or selection.get("schema_version") != SELECTION_SCHEMA_VERSION
        or selection.get("shot_id") != shot.shot_id
        or selection.get("quality_approved") is not True
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number not in (1, 2, 3)
    ):
        return False, ""
    candidate = root / "shots" / shot.shot_id / f"candidate_{number:02d}.mp4"
    candidate_hash = _safe_file_sha256(candidate)
    expected_candidate = str(candidate.relative_to(root))
    review_path = (
        root
        / "shots"
        / shot.shot_id
        / "reviews"
        / f"candidate_{number:02d}.review.json"
    )
    expected_review = str(review_path.relative_to(root))
    gates = selection.get("manual_gates")
    if not (
        candidate_hash
        and selection.get("candidate_path") == expected_candidate
        and selection.get("candidate_sha256") == candidate_hash
        and selection.get("quality_review_path") == expected_review
        and selection.get("quality_review_sha256") == _safe_file_sha256(review_path)
        and isinstance(gates, Mapping)
        and set(gates) == set(MANUAL_REVIEW_GATES)
        and all(gates.get(gate) is True for gate in MANUAL_REVIEW_GATES)
        and _manual_review_is_valid(selection)
    ):
        return False, ""

    review = _read_json_object(review_path)
    bindings = review.get("bindings")
    review_keys = {
        "schema_version",
        "attempt_id",
        "shot_id",
        "candidate_number",
        "candidate_sha256",
        "passed",
        "failures",
        "review_path",
        "evidence",
        "bindings",
    }
    binding_keys = {
        "candidate",
        "provenance",
        "source_evidence",
        "drive_audio",
        "evidence",
    }
    attempt_id = review.get("attempt_id")
    if not (
        set(review) == review_keys
        and review.get("schema_version") == REVIEW_SCHEMA_VERSION
        and review.get("shot_id") == shot.shot_id
        and review.get("candidate_number") == number
        and review.get("candidate_sha256") == candidate_hash
        and review.get("passed") is True
        and review.get("failures") == []
        and review.get("review_path") == expected_review
        and isinstance(attempt_id, str)
        and bool(attempt_id)
        and Path(attempt_id).name == attempt_id
        and attempt_id not in {".", ".."}
        and isinstance(bindings, Mapping)
        and set(bindings) == binding_keys
        and selection.get("quality_bindings_sha256") == _json_sha256(bindings)
    ):
        return False, ""
    candidate_binding = bindings.get("candidate")
    provenance_binding = bindings.get("provenance")
    source_binding = bindings.get("source_evidence")
    drive_audio = bindings.get("drive_audio")
    evidence = bindings.get("evidence")
    expected_provenance = (
        candidate.with_suffix(".provenance.json").relative_to(root).as_posix()
    )
    expected_source_binding = _expected_review_source_binding(root, shot, reference)
    annotation = _selection_annotation(reference, shot.shot_id)
    expected_drive_audio = _expected_review_drive_audio(root, shot, annotation)
    expected_evidence = _expected_review_evidence(
        root,
        shot,
        number,
        candidate_hash,
        attempt_id,
        speaking=bool(annotation and annotation.speaker),
    )
    if not (
        _binding_matches(candidate_binding, expected_candidate, candidate_hash)
        and _binding_matches(
            provenance_binding,
            expected_provenance,
            _safe_file_sha256(root / expected_provenance),
        )
        and _relative_binding_is_current(root, provenance_binding)
        and source_binding == expected_source_binding
        and drive_audio == expected_drive_audio
        and bool(expected_evidence)
        and evidence == expected_evidence
        and review.get("evidence")
        == {name: binding["path"] for name, binding in expected_evidence.items()}
    ):
        return False, ""
    provenance_path = _relative_file(root, provenance_binding.get("path"))
    provenance = _read_json_object(provenance_path)
    if not _selection_provenance_is_current(
        root,
        shot,
        number,
        candidate_hash,
        expected_candidate,
        source_hash,
        annotation,
        expected_drive_audio,
        provenance,
    ):
        return False, ""
    expected_selection_bindings = {
        "quality_provenance_path": provenance_binding.get("path"),
        "quality_provenance_sha256": provenance_binding.get("sha256"),
        "quality_source_evidence_sha256": source_binding.get("manifest_sha256"),
        "quality_drive_audio": bindings.get("drive_audio"),
        "quality_evidence": evidence,
    }
    if any(
        selection.get(key) != value
        for key, value in expected_selection_bindings.items()
    ):
        return False, ""
    return True, candidate_hash


def _manual_review_is_valid(selection: Mapping[str, Any]) -> bool:
    review = {
        **dict(selection.get("manual_gates") or {}),
        "note": selection.get("manual_review_note"),
    }
    try:
        review_contract._validate_manual_review(review)
    except (RuntimeError, TypeError, ValueError):
        return False
    return True


def _selection_annotation(reference: Mapping[str, Any], shot_id: str) -> Any | None:
    annotations = reference.get("annotations")
    if not isinstance(annotations, (list, tuple)):
        return None
    matches = [
        annotation
        for annotation in annotations
        if getattr(annotation, "shot_id", None) == shot_id
    ]
    return matches[0] if len(matches) == 1 else None


def _expected_review_source_binding(
    root: Path,
    shot: Any,
    reference: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    manifest_sha256 = reference.get("evidence_manifest_sha256")
    if not _valid_sha256(manifest_sha256):
        return None
    records = []
    for label in ("start", "middle", "end"):
        relative = f"reference/shots/{shot.shot_id}/{label}.jpg"
        digest = _safe_file_sha256(root / relative)
        if not digest:
            return None
        records.append({"label": label, "path": relative, "sha256": digest})
    return {
        "manifest_path": "reference/evidence_manifest.json",
        "manifest_sha256": manifest_sha256,
        "records": records,
    }


def _expected_review_drive_audio(
    root: Path,
    shot: Any,
    annotation: Any | None,
) -> Mapping[str, str] | None:
    if annotation is None:
        return None
    if not annotation.speaker:
        return None
    if annotation.source_audio is not True:
        return None
    relative = f"audio/drive/{shot.shot_id}.wav"
    digest = _safe_file_sha256(root / relative)
    return {"path": relative, "sha256": digest} if digest else None


def _expected_review_evidence(
    root: Path,
    shot: Any,
    number: int,
    candidate_hash: str,
    attempt_id: str,
    *,
    speaking: bool,
) -> Mapping[str, Mapping[str, str]]:
    filenames = {
        "contact_sheet": "contact_4x3.jpg",
        "comparison_sheet": "source_candidate_start_middle_end.jpg",
    }
    if speaking:
        filenames["mouth_sheet"] = "mouth_8fps.jpg"
    records = {}
    for name, filename in filenames.items():
        relative = (
            f"shots/{shot.shot_id}/reviews/candidate_{number:02d}/"
            f"{candidate_hash}/{attempt_id}/{filename}"
        )
        digest = _safe_file_sha256(root / relative)
        if not digest:
            return {}
        records[name] = {"path": relative, "sha256": digest}
    return records


def _selection_provenance_is_current(
    root: Path,
    shot: Any,
    number: int,
    candidate_hash: str,
    expected_candidate: str,
    source_hash: str,
    annotation: Any | None,
    drive_audio: Mapping[str, str] | None,
    provenance: Mapping[str, Any],
) -> bool:
    if annotation is None:
        return False
    provenance_keys = {
        "schema_version",
        "provider",
        "model",
        "endpoint_fingerprint_sha256",
        "shot_id",
        "candidate_number",
        "editorial_duration_s",
        "provider_duration_s",
        "source_window",
        "source_sha256",
        "prompt_sha256",
        "anchor_sha256",
        "composition_sha256",
        "drive_audio_sha256",
        "output_sha256",
        "output_path",
        "gateway_report_path",
        "gateway_result",
        "signature",
    }
    provider_duration_s = min(15, max(4, math.ceil(shot.duration_s)))
    drive_audio_sha256 = drive_audio.get("sha256") if drive_audio else None
    speaker_visible = bool(annotation.speaker) and (
        annotation.speaker in annotation.characters
    )
    composition_sha256 = _safe_file_sha256(
        root / "reference" / "shots" / shot.shot_id / "start.jpg"
    )
    anchor_paths = []
    asset_paths = {
        "source_orange_cat": "assets/characters/奶糖_reference.png",
        "source_tabby_cat": "assets/characters/豆包_reference.png",
    }
    for role in annotation.characters:
        if role == "source_woman":
            continue
        relative = asset_paths.get(role)
        if relative is None:
            return False
        anchor_paths.append(relative)
    anchor_paths.append(f"assets/scenes/{annotation.scene_anchor_id}.png")
    ordered_anchor_paths = tuple(dict.fromkeys(anchor_paths))
    anchor_sha256 = [_safe_file_sha256(root / path) for path in ordered_anchor_paths]
    source_window = {"start_s": shot.start_s, "end_s": shot.end_s}
    endpoint_sha256 = _configured_gateway_endpoint_fingerprint()
    generation_contracts = []
    postprocess_modes = (False, True) if drive_audio is not None else (False,)
    for postprocess_lipsync in postprocess_modes:
        prompt, _negative = generation_contract._prompt(
            shot,
            annotation,
            provider_duration_s,
            voice_present=drive_audio is not None and not postprocess_lipsync,
            speaker_visible=speaker_visible,
            postprocess_lipsync=postprocess_lipsync,
        )
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        generation_contracts.append(
            (
                prompt_sha256,
                {
                    "source_sha256": source_hash,
                    "prompt_sha256": prompt_sha256,
                    "anchor_sha256": anchor_sha256,
                    "composition_sha256": composition_sha256,
                    "reference_policy": PROVIDER_REFERENCE_POLICY_VERSION,
                    "audio_sha256": drive_audio_sha256,
                    "generate_audio": bool(
                        drive_audio is not None and not postprocess_lipsync
                    ),
                    "audio_transport_contract": GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
                    "model": VIDEO_MODEL,
                    "endpoint_fingerprint_sha256": endpoint_sha256,
                    "candidate_number": number,
                    "editorial_duration_s": shot.duration_s,
                    "provider_duration_s": provider_duration_s,
                    "source_window": source_window,
                },
            )
        )
    expected_gateway_report = str(
        (root / expected_candidate).with_suffix(".gateway.json").relative_to(root)
    )
    gateway_report_path = _relative_file(root, expected_gateway_report)
    gateway_report = _read_json_object(gateway_report_path)
    postprocess = provenance.get("postprocess")
    provenance_inventory_valid = set(provenance) == provenance_keys
    postprocess_valid = postprocess is None
    if postprocess is not None:
        provenance_inventory_valid = set(provenance) == provenance_keys | {
            "postprocess"
        }
        postprocess_valid = validate_replica_postprocess_provenance(
            root,
            postprocess,
            candidate_sha256=candidate_hash,
            expected_candidate_path=expected_candidate,
            drive_audio_sha256=drive_audio_sha256 or "",
        )
    return bool(
        provenance_inventory_valid
        and postprocess_valid
        and provenance.get("schema_version") == GENERATION_SCHEMA_VERSION
        and provenance.get("provider") == "gateway"
        and provenance.get("model") == VIDEO_MODEL
        and endpoint_sha256
        and provenance.get("endpoint_fingerprint_sha256") == endpoint_sha256
        and provenance.get("shot_id") == shot.shot_id
        and provenance.get("candidate_number") == number
        and provenance.get("editorial_duration_s") == shot.duration_s
        and provenance.get("provider_duration_s") == provider_duration_s
        and provenance.get("source_window") == source_window
        and provenance.get("source_sha256") == source_hash
        and any(
            provenance.get("prompt_sha256") == expected_prompt_sha256
            and provenance.get("signature") == expected_signature
            for expected_prompt_sha256, expected_signature in generation_contracts
        )
        and anchor_sha256
        and all(anchor_sha256)
        and provenance.get("anchor_sha256") == anchor_sha256
        and composition_sha256
        and provenance.get("composition_sha256") == composition_sha256
        and provenance.get("drive_audio_sha256") == drive_audio_sha256
        and provenance.get("output_sha256") == candidate_hash
        and provenance.get("output_path") == expected_candidate
        and provenance.get("gateway_report_path") == expected_gateway_report
        and gateway_report_path is not None
        and provenance.get("gateway_result") == gateway_report
        and _gateway_report_is_completed(gateway_report)
    )


def _configured_gateway_base_url() -> str:
    value = _configured_gateway_value(
        "OPENAI_BASE_URL",
        "GATEWAY_BASE_URL",
    )
    if not value:
        raise PetReplicaCLIError("GATEWAY_BASE_URL is required for live generation.")
    return value.rstrip("/")


def _configured_gateway_api_key() -> str:
    return _configured_gateway_value("GATEWAY_API_KEY", "NEW_API_KEY")


def _configured_gateway_value(*names: str, default: str = "") -> str:
    dotenv = parse_dotenv(FACTORY_ENV_PATH)
    for values in (os.environ, dotenv):
        for name in names:
            value = values.get(name, "").strip()
            if value:
                return value
    return default


def _configured_gateway_endpoint_fingerprint() -> str:
    try:
        return gateway_endpoint_fingerprint(_configured_gateway_base_url())
    except (RuntimeError, TypeError, ValueError):
        return ""


def _gateway_report_is_completed(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    errors = value.get("errors")
    results = value.get("results")
    return bool(
        value.get("success") is True
        and value.get("planned_count") == 1
        and value.get("completed_count") == 1
        and value.get("failed_count") == 0
        and value.get("error") in ("", None)
        and isinstance(errors, list)
        and not errors
        and isinstance(results, list)
        and len(results) == 1
        and isinstance(results[0], Mapping)
        and results[0].get("status") == "completed"
    )


def _inspect_release(
    plan: PetReplicaPlan,
    mode: str,
    selection_hashes: Mapping[str, str],
    audio: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    root = plan.output_root
    pointer_path = root / "final" / f"{mode}_current.json"
    if not pointer_path.exists():
        return {"exists": False, "valid": False}
    pointer = _read_json_object(pointer_path)
    release_id = pointer.get("release")
    release_relative = pointer.get("release_path")
    if not (
        set(pointer) == {"schema_version", "mode", "release", "release_path"}
        and pointer.get("schema_version") == COMPOSITION_SCHEMA_VERSION
        and pointer.get("mode") == mode
        and isinstance(release_id, str)
        and _SAFE_RELEASE.fullmatch(release_id)
        and release_relative == f"releases/{release_id}"
    ):
        return {"exists": True, "valid": False}
    release = _relative_directory(root / "final", release_relative)
    if release is None:
        return {"exists": True, "valid": False}
    manifest_path = release / f"{mode}_composition_manifest.json"
    qc_path = release / f"{mode}_qc.json"
    manifest = _read_json_object(manifest_path)
    qc = _read_json_object(qc_path)
    shots = manifest.get("shots")
    start_s = 0.0
    _, end_s = composition_contract.replica_composition_window(plan, mode)
    expected_plan_shots = tuple(
        shot
        for shot in plan.shots
        if composition_contract.replica_shot_overlaps_window(
            plan,
            shot,
            start_s,
            end_s,
        )
    )
    expected_ids = [shot.shot_id for shot in expected_plan_shots]
    manifest_keys = {
        "schema_version",
        "project_id",
        "mode",
        "output_root",
        "manifest_path",
        "start_s",
        "end_s",
        "duration_s",
        "shots",
        "ocr_evidence_bindings",
        "reviewed_ocr_events",
        "subtitles",
        "caption_safe_region",
        "reviewed_annotations_sha256",
        "reviewed_annotations_snapshot_path",
        "ocr_evidence_snapshot_paths",
        "subtitle_font_path",
        "subtitle_font_family",
        "subtitle_font_sha256",
        "source_audio_path",
        "source_audio_sha256",
        "presentation_source_path",
        "presentation_source_sha256",
        "subtitle_path",
        "concat_list_path",
        "picture_path",
        "clean_master_path",
        "captioned_master_path",
        "master_path",
        "master_alias",
        "side_by_side_path",
        "final_qc_path",
        "current_pointer_path",
        "audio_mode",
        "comparison_duration_s",
        "comparison_tail_policy",
        "clean_master_command",
        "captioned_master_command",
        "ffmpeg_command",
        "side_by_side_command",
    }
    if not (
        set(manifest) == manifest_keys
        and manifest.get("schema_version") == COMPOSITION_SCHEMA_VERSION
        and manifest.get("project_id") == plan.project_id
        and manifest.get("mode") == mode
        and manifest.get("output_root") == str(root)
        and manifest.get("manifest_path") == str(manifest_path)
        and manifest.get("start_s") == start_s
        and manifest.get("end_s") == end_s
        and manifest.get("duration_s") == end_s - start_s
        and manifest.get("presentation_source_sha256")
        == _safe_file_sha256(plan.source_video)
        and manifest.get("source_audio_sha256") == audio.get("full_source_sha256")
        and manifest.get("reviewed_annotations_sha256")
        == reference.get("annotations_sha256")
        and isinstance(shots, list)
        and [item.get("shot_id") for item in shots if isinstance(item, Mapping)]
        == expected_ids
    ):
        return {"exists": True, "valid": False}
    if not _release_shots_are_current(
        root,
        release,
        plan,
        expected_plan_shots,
        shots,
        selection_hashes,
        start_s=start_s,
        end_s=end_s,
    ):
        return {"exists": True, "valid": False}

    review_contract = _expected_release_review_contract(
        plan,
        reference,
        start_s=start_s,
        end_s=end_s,
    )
    if review_contract is None:
        return {"exists": True, "valid": False}
    ocr_bindings, reviewed_events, subtitles, safe_region = review_contract
    annotations_snapshot = release / "review_snapshot" / "shot_annotations.json"
    expected_ocr_snapshots = [
        release
        / "review_snapshot"
        / "ocr_evidence"
        / binding["shot_id"]
        / f"{binding['evidence_sha256']}.json"
        for binding in ocr_bindings
    ]
    if not (
        manifest.get("ocr_evidence_bindings") == ocr_bindings
        and manifest.get("reviewed_ocr_events") == reviewed_events
        and manifest.get("subtitles") == subtitles
        and manifest.get("caption_safe_region") == safe_region
        and manifest.get("reviewed_annotations_snapshot_path")
        == str(annotations_snapshot)
        and _safe_file_sha256(annotations_snapshot)
        == reference.get("annotations_sha256")
        and manifest.get("ocr_evidence_snapshot_paths")
        == [str(path) for path in expected_ocr_snapshots]
        and all(
            _safe_file_sha256(path) == binding["evidence_sha256"]
            for path, binding in zip(expected_ocr_snapshots, ocr_bindings)
        )
    ):
        return {"exists": True, "valid": False}

    clean_name = (
        "pilot_clean_master.mp4" if mode == "pilot" else "replica_clean_master.mp4"
    )
    captioned_name = (
        "pilot_captioned_master.mp4"
        if mode == "pilot"
        else "replica_captioned_master.mp4"
    )
    comparison_name = (
        "pilot_side_by_side.mp4" if mode == "pilot" else "replica_side_by_side.mp4"
    )
    release_paths = {
        "subtitle_path": release / "subtitles.ass",
        "concat_list_path": release / "concat.ffconcat",
        "picture_path": release / "picture.mp4",
        "clean_master_path": release / clean_name,
        "captioned_master_path": release / captioned_name,
        "master_path": release / captioned_name,
        "side_by_side_path": release / comparison_name,
        "final_qc_path": qc_path,
    }
    if not all(
        manifest.get(key) == str(path) and _absolute_file(str(path)) == path
        for key, path in release_paths.items()
    ):
        return {"exists": True, "valid": False}
    source_audio_path = root / "audio" / "source_audio.aac"
    if not (
        manifest.get("source_audio_path") == str(source_audio_path)
        and _absolute_file(manifest.get("source_audio_path")) == source_audio_path
        and manifest.get("presentation_source_path") == str(plan.source_video)
        and _absolute_file(manifest.get("presentation_source_path"))
        == plan.source_video
        and manifest.get("master_alias") == "captioned_master_path"
        and manifest.get("current_pointer_path") == str(pointer_path)
        and _absolute_file(manifest.get("current_pointer_path")) == pointer_path
        and manifest.get("audio_mode")
        in {"source_aac_stream_copy", "pcm_to_aac_192k_once"}
        and manifest.get("comparison_tail_policy")
        == "clamp_to_last_reference_video_frame"
    ):
        return {"exists": True, "valid": False}
    comparison_duration_s = min(
        end_s - start_s,
        float(reference.get("last_video_frame_s")) + 1 / plan.fps,
    )
    if not (
        _finite_number(manifest.get("comparison_duration_s"))
        and math.isclose(
            float(manifest["comparison_duration_s"]),
            comparison_duration_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    ):
        return {"exists": True, "valid": False}
    font_binding = _release_font_binding(manifest)
    if font_binding is None:
        return {"exists": True, "valid": False}
    commands = (
        (manifest.get("clean_master_command"), release_paths["clean_master_path"]),
        (
            manifest.get("captioned_master_command"),
            release_paths["captioned_master_path"],
        ),
        (
            manifest.get("side_by_side_command"),
            release_paths["side_by_side_path"],
        ),
    )
    if not (
        manifest.get("ffmpeg_command") == manifest.get("captioned_master_command")
        and all(
            _release_command_is_safe(command, output) for command, output in commands
        )
    ):
        return {"exists": True, "valid": False}

    expected_artifacts = {
        path.relative_to(release).as_posix(): _safe_file_sha256(path)
        for path in (
            release_paths["subtitle_path"],
            release_paths["concat_list_path"],
            release_paths["picture_path"],
            release_paths["clean_master_path"],
            release_paths["captioned_master_path"],
            release_paths["side_by_side_path"],
            *(_absolute_file(item["normalized_path"]) for item in shots),
        )
        if path is not None
    }
    if len(expected_artifacts) != 6 + len(shots) or not all(
        expected_artifacts.values()
    ):
        return {"exists": True, "valid": False}
    review_snapshot = {
        "annotations_path": "review_snapshot/shot_annotations.json",
        "annotations_sha256": reference.get("annotations_sha256"),
        "ocr_evidence": [
            {
                "shot_id": binding["shot_id"],
                "path": path.relative_to(release).as_posix(),
                "sha256": binding["evidence_sha256"],
                "detected_item_count": binding["detected_item_count"],
            }
            for path, binding in zip(expected_ocr_snapshots, ocr_bindings)
        ],
        "staged": True,
    }
    input_bundle = {
        "source_audio_sha256": audio.get("full_source_sha256"),
        "presentation_source_sha256": _safe_file_sha256(plan.source_video),
        "selection": {
            shot_id: selection_hashes.get(shot_id) for shot_id in expected_ids
        },
        "reviewed_annotations_sha256": reference.get("annotations_sha256"),
        "ocr_evidence": ocr_bindings,
        "review_snapshot": review_snapshot,
        "subtitle_font": font_binding,
    }
    artifact_hashes = qc.get("artifact_sha256")
    qc_keys = {
        "schema_version",
        "valid",
        "mode",
        "release_id",
        "master_path",
        "master_sha256",
        "master_alias",
        "clean_master_path",
        "captioned_master_path",
        "duration_s",
        "composition_manifest_sha256",
        "artifact_sha256",
        "input_bundle",
        "ocr_evidence",
        "review_snapshot",
        "subtitle_font",
        "masters",
        "frame_sequence",
        "master_frame_boundaries",
        "blackdetect",
        "source_pixel_evidence",
        "comparison",
        "cut_count",
        "cut_timestamps_s",
        "audio",
        "audio_mode",
        "fallback_used",
    }
    if not (
        set(qc) == qc_keys
        and qc.get("schema_version") == COMPOSITION_SCHEMA_VERSION
        and qc.get("valid") is True
        and qc.get("mode") == mode
        and qc.get("release_id") == release_id
        and qc.get("master_alias") == "captioned_master_path"
        and qc.get("clean_master_path") == str(release_paths["clean_master_path"])
        and qc.get("captioned_master_path")
        == str(release_paths["captioned_master_path"])
        and _finite_number(qc.get("duration_s"))
        and abs(float(qc["duration_s"]) - (end_s - start_s)) <= 1 / plan.fps
        and qc.get("composition_manifest_sha256") == _safe_file_sha256(manifest_path)
        and artifact_hashes == expected_artifacts
        and qc.get("input_bundle") == input_bundle
        and qc.get("ocr_evidence") == ocr_bindings
        and qc.get("review_snapshot") == review_snapshot
        and qc.get("subtitle_font") == font_binding
        and qc.get("audio_mode") == manifest.get("audio_mode")
        and qc.get("fallback_used")
        is (manifest.get("audio_mode") == "pcm_to_aac_192k_once")
    ):
        return {"exists": True, "valid": False}
    master = _absolute_file(manifest.get("master_path"))
    if (
        master is None
        or not _is_inside(release, master)
        or qc.get("master_path") != str(master)
        or qc.get("master_sha256") != _safe_file_sha256(master)
        or not _release_qc_is_current(
            qc,
            shots,
            release_paths,
            end_s=end_s,
            fps=plan.fps,
            comparison_duration_s=comparison_duration_s,
            subtitles=manifest.get("subtitles"),
        )
    ):
        return {"exists": True, "valid": False}
    return {"exists": True, "valid": True}


def _release_shots_are_current(
    root: Path,
    release: Path,
    plan: PetReplicaPlan,
    expected_shots: Sequence[Any],
    records: Any,
    selection_hashes: Mapping[str, str],
    *,
    start_s: float,
    end_s: float,
) -> bool:
    if not isinstance(records, list) or len(records) != len(expected_shots):
        return False
    record_keys = {
        "shot_id",
        "source_path",
        "source_sha256",
        "source_start_s",
        "source_end_s",
        "editorial_duration_s",
        "timeline_start_s",
        "timeline_end_s",
        "normalized_path",
    }
    for shot, record in zip(expected_shots, records):
        selection = _read_json_object(root / "shots" / shot.shot_id / "selection.json")
        number = selection.get("candidate_number")
        if isinstance(number, bool) or not isinstance(number, int):
            return False
        source_path = root / "shots" / shot.shot_id / f"candidate_{number:02d}.mp4"
        normalized_path = release / "normalized" / f"{shot.shot_id}.mp4"
        shot_start = round(shot.start_s * plan.fps) / plan.fps
        shot_end = (
            plan.duration_s
            if shot.end_s == plan.duration_s
            else round(shot.end_s * plan.fps) / plan.fps
        )
        editorial_start = max(start_s, shot_start)
        editorial_end = min(end_s, shot_end)
        expected = {
            "shot_id": shot.shot_id,
            "source_path": str(source_path),
            "source_sha256": selection_hashes.get(shot.shot_id),
            "source_start_s": editorial_start - shot_start,
            "source_end_s": editorial_end - shot_start,
            "editorial_duration_s": editorial_end - editorial_start,
            "timeline_start_s": editorial_start - start_s,
            "timeline_end_s": editorial_end - start_s,
            "normalized_path": str(normalized_path),
        }
        numeric_keys = {
            "source_start_s",
            "source_end_s",
            "editorial_duration_s",
            "timeline_start_s",
            "timeline_end_s",
        }
        if not (
            isinstance(record, Mapping)
            and set(record) == record_keys
            and record == expected
            and all(_finite_number(record.get(key)) for key in numeric_keys)
            and _absolute_file(record.get("source_path")) == source_path
            and _absolute_file(record.get("normalized_path")) == normalized_path
            and _safe_file_sha256(source_path) == expected["source_sha256"]
        ):
            return False
    return True


def _expected_release_review_contract(
    plan: PetReplicaPlan,
    reference: Mapping[str, Any],
    *,
    start_s: float,
    end_s: float,
) -> tuple[list[Any], list[Any], list[Any], Any] | None:
    annotations = reference.get("annotations")
    if not isinstance(annotations, (list, tuple)) or len(annotations) != len(
        plan.shots
    ):
        return None
    raw_annotations = _read_json_object(
        plan.output_root / "reference" / "shot_annotations.json"
    )
    safe_region = raw_annotations.get("caption_safe_region")
    if not isinstance(safe_region, Mapping):
        return None
    bindings = []
    events = []
    subtitles = []
    start_frame = round(start_s * plan.fps)
    end_frame = round(end_s * plan.fps)
    for annotation in annotations:
        if annotation.ocr_evidence is None:
            return None
        bindings.append(asdict(annotation.ocr_evidence))
        for event in annotation.ocr_events:
            overlap_start = max(start_frame, event.start_frame)
            overlap_end = min(end_frame, event.end_frame)
            if overlap_end <= overlap_start:
                continue
            events.append(asdict(event))
            if event.renderable:
                relative_start = overlap_start - start_frame
                relative_end = overlap_end - start_frame
                subtitles.append(
                    {
                        "event_id": event.event_id,
                        "shot_id": event.shot_id,
                        "start_frame": relative_start,
                        "end_frame": relative_end,
                        "text": event.reviewed_text,
                        "placement": asdict(event.placement),
                        "start_s": relative_start / plan.fps,
                        "end_s": relative_end / plan.fps,
                    }
                )
    return bindings, events, subtitles, dict(safe_region)


def _release_font_binding(manifest: Mapping[str, Any]) -> Mapping[str, str] | None:
    path = _absolute_file(manifest.get("subtitle_font_path"))
    family = manifest.get("subtitle_font_family")
    digest = manifest.get("subtitle_font_sha256")
    approved = {
        (Path("/System/Library/Fonts/STHeiti Medium.ttc"), "Heiti SC"),
        (Path("/System/Library/Fonts/STHeiti Light.ttc"), "Heiti SC"),
    }
    if (
        path is None
        or (path, family) not in approved
        or not _valid_sha256(digest)
        or _safe_file_sha256(path) != digest
    ):
        return None
    return {"path": str(path), "family": family, "sha256": digest}


def _release_command_is_safe(value: Any, expected_output: Path) -> bool:
    if not (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
        and Path(value[0]).name.startswith("ffmpeg")
        and value[-1] == str(expected_output)
    ):
        return False
    joined = " ".join(value).lower()
    return not any(
        forbidden in joined
        for forbidden in (
            "xfade",
            "tpad",
            "minterpolate",
            "optical",
            "blend",
            "framerate",
        )
    )


def _release_qc_is_current(
    qc: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    release_paths: Mapping[str, Path],
    *,
    end_s: float,
    fps: int,
    comparison_duration_s: float,
    subtitles: Any,
) -> bool:
    frame_sequence = qc.get("frame_sequence")
    if not _release_frame_sequence_is_current(
        frame_sequence,
        shots,
        end_s=end_s,
        fps=fps,
    ):
        return False
    cuts = frame_sequence["cuts"]
    cut_timestamps = [
        {
            "planned": item["planned_timestamp_s"],
            "actual": item["actual_timestamp_s"],
            "delta_frames": item["delta_frames"],
        }
        for item in cuts
    ]
    masters = qc.get("masters")
    if not isinstance(masters, Mapping) or set(masters) != {"clean", "captioned"}:
        return False
    for variant, path_key in (
        ("clean", "clean_master_path"),
        ("captioned", "captioned_master_path"),
    ):
        if not _release_master_qc_is_current(
            masters.get(variant),
            variant=variant,
            path=release_paths[path_key],
            picture_path=release_paths["picture_path"],
            frame_sequence=frame_sequence,
            expected_duration_s=end_s,
            fps=fps,
            subtitles=subtitles,
        ):
            return False
    captioned = masters["captioned"]
    audio = qc.get("audio")
    return bool(
        qc.get("master_frame_boundaries") == captioned.get("frame_boundaries")
        and qc.get("blackdetect") == captioned.get("blackdetect")
        and qc.get("cut_count") == len(cuts)
        and qc.get("cut_timestamps_s") == cut_timestamps
        and isinstance(audio, Mapping)
        and audio == captioned.get("audio")
        and _release_audio_proof_is_current(
            audio,
            expected_duration_s=end_s,
            fps=fps,
        )
        and _release_source_pixel_proof_is_current(
            qc.get("source_pixel_evidence"),
            shots,
            end_s=end_s,
            fps=fps,
            reference_video_duration_s=comparison_duration_s,
        )
        and _release_comparison_qc_is_current(
            qc.get("comparison"),
            release_paths["side_by_side_path"],
            expected_duration_s=comparison_duration_s,
            fps=fps,
        )
    )


def _release_master_qc_is_current(
    value: Any,
    *,
    variant: str,
    path: Path,
    picture_path: Path,
    frame_sequence: Mapping[str, Any],
    expected_duration_s: float,
    fps: int,
    subtitles: Any,
) -> bool:
    common_keys = {
        "valid",
        "variant",
        "path",
        "sha256",
        "duration_s",
        "width",
        "height",
        "fps",
        "video_frames",
        "video_codec",
        "audio_codec",
        "audio_channels",
        "frame_boundaries",
        "audio",
        "blackdetect",
        "freezedetect",
    }
    variant_keys = (
        {"picture_preserved", "picture_proof"}
        if variant == "clean"
        else {"caption_effects"}
    )
    if not (
        isinstance(value, Mapping)
        and set(value) == common_keys | variant_keys
        and value.get("valid") is True
        and value.get("variant") == variant
        and value.get("path") == str(path)
        and value.get("sha256") == _safe_file_sha256(path)
        and _finite_number(value.get("duration_s"))
        and abs(float(value["duration_s"]) - expected_duration_s) <= 1 / fps
        and value.get("width") == 720
        and value.get("height") == 1280
        and value.get("fps") == fps
        and value.get("video_frames") == frame_sequence.get("frame_count")
        and value.get("video_codec") == "h264"
        and value.get("audio_codec") == "aac"
        and value.get("audio_channels") == 2
        and _release_master_boundaries_are_current(
            value.get("frame_boundaries"),
            frame_sequence,
            fps=fps,
        )
        and _release_audio_proof_is_current(
            value.get("audio"),
            expected_duration_s=expected_duration_s,
            fps=fps,
        )
        and _release_blackdetect_is_current(value.get("blackdetect"))
        and _release_freezedetect_is_current(value.get("freezedetect"))
    ):
        return False
    if variant == "clean":
        proof = value.get("picture_proof")
        return bool(
            value.get("picture_preserved") is True
            and isinstance(proof, Mapping)
            and set(proof)
            == {"method", "frame_count", "picture_sha256", "clean_sha256"}
            and proof.get("method") == "decoded_rgb_framemd5_all_frames"
            and proof.get("frame_count") == frame_sequence.get("frame_count")
            and proof.get("picture_sha256") == _safe_file_sha256(picture_path)
            and proof.get("clean_sha256") == _safe_file_sha256(path)
        )
    return _release_caption_effects_are_current(
        value.get("caption_effects"),
        subtitles,
    )


def _release_master_boundaries_are_current(
    value: Any,
    frame_sequence: Mapping[str, Any],
    *,
    fps: int,
) -> bool:
    source_cuts = frame_sequence.get("cuts")
    if not (
        isinstance(value, Mapping)
        and set(value) == {"frame_count", "cut_count", "cuts"}
        and value.get("frame_count") == frame_sequence.get("frame_count")
        and isinstance(source_cuts, list)
        and value.get("cut_count") == len(source_cuts)
        and isinstance(value.get("cuts"), list)
        and len(value["cuts"]) == len(source_cuts)
    ):
        return False
    cut_keys = {
        "timestamp_s",
        "offset_frames",
        "before_distance",
        "after_distance",
    }
    for source_cut, cut in zip(source_cuts, value["cuts"]):
        offset = cut.get("offset_frames") if isinstance(cut, Mapping) else None
        source_timestamp = (
            source_cut.get("timestamp_s") if isinstance(source_cut, Mapping) else None
        )
        if not (
            isinstance(cut, Mapping)
            and set(cut) == cut_keys
            and _finite_number(source_timestamp)
            and isinstance(offset, int)
            and not isinstance(offset, bool)
            and abs(offset) <= 2
            and _finite_number(cut.get("timestamp_s"))
            and math.isclose(
                float(cut["timestamp_s"]),
                (round(float(source_timestamp) * fps) + offset) / fps,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            and all(
                _finite_number(cut.get(key)) and 0.0 <= float(cut[key]) <= 0.035
                for key in ("before_distance", "after_distance")
            )
        ):
            return False
    return True


def _release_blackdetect_is_current(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"pix_th", "pic_th", "detected"}
        and value.get("pix_th") == composition_contract._BLACK_PIXEL_THRESHOLD
        and value.get("pic_th") == composition_contract._BLACK_PICTURE_RATIO
        and value.get("detected") is False
    )


def _release_freezedetect_is_current(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"detected", "duration_threshold_s"}
        and value.get("detected") is False
        and value.get("duration_threshold_s") == 0.366
    )


def _release_audio_proof_is_current(
    value: Any,
    *,
    expected_duration_s: float,
    fps: int,
) -> bool:
    keys = {
        "mode",
        "source_match",
        "fallback_used",
        "duration_s",
        "presentation_start_s",
        "bit_rate",
        "source_skip_samples",
        "master_skip_samples",
        "pcm_correlation",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        return False
    mode = value.get("mode")
    bit_rate = value.get("bit_rate")
    correlation = value.get("pcm_correlation")
    return bool(
        mode in {"source_aac_stream_copy", "pcm_to_aac_192k_once"}
        and value.get("source_match") is True
        and value.get("fallback_used")
        is (mode == "pcm_to_aac_192k_once")
        and _finite_number(value.get("duration_s"))
        and abs(float(value["duration_s"]) - expected_duration_s) <= 1 / fps
        and _finite_number(value.get("presentation_start_s"))
        and abs(float(value["presentation_start_s"])) <= 1 / fps
        and isinstance(bit_rate, int)
        and not isinstance(bit_rate, bool)
        and bit_rate >= (180_000 if mode == "pcm_to_aac_192k_once" else 0)
        and all(
            isinstance(value.get(key), int)
            and not isinstance(value.get(key), bool)
            and value[key] >= 0
            for key in ("source_skip_samples", "master_skip_samples")
        )
        and _finite_number(correlation)
        and 0.98 <= float(correlation) <= 1.0
    )


def _release_caption_effects_are_current(value: Any, subtitles: Any) -> bool:
    if not isinstance(subtitles, list) or not all(
        isinstance(item, Mapping) for item in subtitles
    ):
        return False
    changed_frames = sorted(
        {
            frame
            for subtitle in subtitles
            for frame in range(subtitle["start_frame"], subtitle["end_frame"])
        }
    )
    expected_events = [
        {
            "event_id": subtitle["event_id"],
            "shot_id": subtitle["shot_id"],
            "start_frame": subtitle["start_frame"],
            "end_frame": subtitle["end_frame"],
            "start_s": subtitle["start_s"],
            "end_s": subtitle["end_s"],
            "placement": subtitle["placement"],
            "changed_frames": list(
                range(subtitle["start_frame"], subtitle["end_frame"])
            ),
        }
        for subtitle in subtitles
    ]
    codec_noise_frames = value.get("codec_noise_frames") if isinstance(value, Mapping) else None
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {
            "method",
            "raw_changed_frame_count",
            "changed_frame_count",
            "changed_frames",
            "codec_noise_frames",
            "outside_window_changed_frames",
            "outside_safe_region_changed_frames",
            "events",
        }
        and value.get("method")
        == "decoded_rgb_framemd5_with_signalstats_threshold_and_safe_region_crops"
        and isinstance(codec_noise_frames, list)
        and all(
            isinstance(frame, int) and not isinstance(frame, bool) and frame >= 0
            for frame in codec_noise_frames
        )
        and codec_noise_frames == sorted(set(codec_noise_frames))
        and not (set(codec_noise_frames) & set(changed_frames))
        and value.get("raw_changed_frame_count")
        == len(changed_frames) + len(codec_noise_frames)
        and value.get("changed_frame_count") == len(changed_frames)
        and value.get("changed_frames") == changed_frames
        and value.get("outside_window_changed_frames") == []
        and value.get("outside_safe_region_changed_frames") == []
        and value.get("events") == expected_events
    )


def _release_source_pixel_proof_is_current(
    value: Any,
    shots: Sequence[Mapping[str, Any]],
    *,
    end_s: float,
    fps: int,
    reference_video_duration_s: float,
) -> bool:
    threshold = composition_contract._SOURCE_COPY_DELTA_MAX
    if not (
        isinstance(value, Mapping)
        and set(value)
        == {
            "method",
            "threshold",
            "reference_video_duration_s",
            "tail_clamped_sample_count",
            "samples",
        }
        and value.get("method") == "shot_start_middle_end_and_cut_neighborhood"
        and value.get("threshold") == threshold
        and _finite_number(value.get("reference_video_duration_s"))
        and math.isclose(
            float(value["reference_video_duration_s"]),
            reference_video_duration_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        and isinstance(value.get("tail_clamped_sample_count"), int)
        and not isinstance(value.get("tail_clamped_sample_count"), bool)
        and value["tail_clamped_sample_count"] >= 0
        and isinstance(value.get("samples"), list)
    ):
        return False
    requested: list[tuple[float, str, str]] = []
    for shot in shots:
        shot_start = max(0.0, float(shot["timeline_start_s"]))
        shot_end = min(end_s, float(shot["timeline_end_s"]))
        middle_frame = round(((shot_start + shot_end) / 2) * fps)
        requested.extend(
            (
                (shot_start, str(shot["shot_id"]), "shot_start"),
                (middle_frame / fps, str(shot["shot_id"]), "shot_middle"),
                (shot_end - 1 / fps, str(shot["shot_id"]), "shot_end"),
            )
        )
    for shot in shots[:-1]:
        cut = float(shot["timeline_end_s"])
        requested.extend(
            (
                (cut - 1 / fps, str(shot["shot_id"]), "cut_before"),
                (cut, str(shot["shot_id"]), "cut_at"),
                (cut + 1 / fps, str(shot["shot_id"]), "cut_after"),
            )
        )
    if len(value["samples"]) != len(requested):
        return False
    last_frame_s = max(0.0, end_s - 1 / fps)
    source_last_frame_s = max(0.0, reference_video_duration_s - 1 / fps)
    picture_frame_count = round(end_s * fps)
    sample_keys = {
        "timestamp_s",
        "source_timestamp_s",
        "source_tail_clamped",
        "picture_frame_index",
        "shot_id",
        "kind",
        "picture_sha256",
        "source_sha256",
        "perceptual_delta",
    }
    for sample, (requested_time, shot_id, kind) in zip(value["samples"], requested):
        timestamp = min(last_frame_s, max(0.0, requested_time))
        picture_frame_index = min(
            picture_frame_count - 1,
            max(0, round(timestamp * fps)),
        )
        timestamp = picture_frame_index / fps
        source_timestamp = min(source_last_frame_s, timestamp)
        source_tail_clamped = source_timestamp < timestamp
        delta = sample.get("perceptual_delta") if isinstance(sample, Mapping) else None
        if not (
            isinstance(sample, Mapping)
            and set(sample) == sample_keys
            and _finite_number(sample.get("timestamp_s"))
            and math.isclose(
                float(sample["timestamp_s"]),
                timestamp,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            and _finite_number(sample.get("source_timestamp_s"))
            and math.isclose(
                float(sample["source_timestamp_s"]),
                source_timestamp,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            and sample.get("source_tail_clamped") is source_tail_clamped
            and isinstance(sample.get("picture_frame_index"), int)
            and not isinstance(sample.get("picture_frame_index"), bool)
            and sample.get("picture_frame_index") == picture_frame_index
            and sample.get("shot_id") == shot_id
            and sample.get("kind") == kind
            and _valid_sha256(sample.get("picture_sha256"))
            and _valid_sha256(sample.get("source_sha256"))
            and sample.get("picture_sha256") != sample.get("source_sha256")
            and _finite_number(delta)
            and threshold < float(delta) <= 1.0
        ):
            return False
    return value["tail_clamped_sample_count"] == sum(
        bool(sample["source_tail_clamped"]) for sample in value["samples"]
    )


def _release_comparison_qc_is_current(
    value: Any,
    path: Path,
    *,
    expected_duration_s: float,
    fps: int,
) -> bool:
    keys = {
        "valid",
        "tail_policy",
        "path",
        "sha256",
        "duration_s",
        "policy_duration_s",
        "width",
        "height",
        "fps",
        "video_codec",
        "audio_codec",
        "audio_channels",
        "audio_source_match",
        "audio_start_s",
        "audio_pcm_correlation",
        "blackdetect",
        "freezedetect",
    }
    correlation = (
        value.get("audio_pcm_correlation") if isinstance(value, Mapping) else None
    )
    return bool(
        isinstance(value, Mapping)
        and set(value) == keys
        and value.get("valid") is True
        and value.get("tail_policy") == "clamp_to_last_reference_video_frame"
        and value.get("path") == str(path)
        and value.get("sha256") == _safe_file_sha256(path)
        and _finite_number(value.get("duration_s"))
        and abs(float(value["duration_s"]) - expected_duration_s) <= 1 / fps
        and _finite_number(value.get("policy_duration_s"))
        and math.isclose(
            float(value["policy_duration_s"]),
            expected_duration_s,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
        and value.get("width") == 1440
        and value.get("height") == 1280
        and value.get("fps") == fps
        and value.get("video_codec") == "h264"
        and value.get("audio_codec") == "aac"
        and value.get("audio_channels") == 2
        and value.get("audio_source_match") is True
        and _finite_number(value.get("audio_start_s"))
        and abs(float(value["audio_start_s"])) <= 1 / fps
        and _finite_number(correlation)
        and 0.98 <= float(correlation) <= 1.0
        and _release_blackdetect_is_current(value.get("blackdetect"))
        and _release_freezedetect_is_current(value.get("freezedetect"))
    )


def _release_frame_sequence_is_current(
    value: Any,
    shots: Sequence[Mapping[str, Any]],
    *,
    end_s: float,
    fps: int,
) -> bool:
    if not (
        isinstance(value, Mapping)
        and set(value) == {"frame_count", "clip_frame_counts", "cuts"}
        and value.get("frame_count") == round(end_s * fps)
        and isinstance(value.get("clip_frame_counts"), list)
        and len(value["clip_frame_counts"]) == len(shots)
        and all(
            isinstance(count, int) and not isinstance(count, bool) and count > 0
            for count in value["clip_frame_counts"]
        )
        and sum(value["clip_frame_counts"]) == value["frame_count"]
        and isinstance(value.get("cuts"), list)
        and len(value["cuts"]) == max(0, len(shots) - 1)
    ):
        return False
    for shot, measured_frames in zip(shots, value["clip_frame_counts"]):
        if abs(measured_frames - round(shot["editorial_duration_s"] * fps)) > 2:
            return False
    cut_keys = {
        "timestamp_s",
        "offset_frames",
        "before_sha256",
        "after_sha256",
        "planned_timestamp_s",
        "actual_timestamp_s",
        "planned_frame_index",
        "actual_frame_index",
        "delta_frames",
    }
    frame_cursor = 0
    for shot, measured_frames, cut in zip(
        shots[:-1],
        value["clip_frame_counts"][:-1],
        value["cuts"],
    ):
        frame_cursor += measured_frames
        planned_frame = round(shot["timeline_end_s"] * fps)
        offset = cut.get("offset_frames") if isinstance(cut, Mapping) else None
        actual_frame = (
            cut.get("actual_frame_index") if isinstance(cut, Mapping) else None
        )
        expected_actual_frame = (
            frame_cursor + offset
            if isinstance(offset, int) and not isinstance(offset, bool)
            else None
        )
        if not (
            isinstance(cut, Mapping)
            and set(cut) == cut_keys
            and _finite_number(cut.get("timestamp_s"))
            and isinstance(offset, int)
            and not isinstance(offset, bool)
            and abs(offset) <= 2
            and _valid_sha256(cut.get("before_sha256"))
            and _valid_sha256(cut.get("after_sha256"))
            and cut.get("planned_timestamp_s") == shot["timeline_end_s"]
            and isinstance(cut.get("planned_frame_index"), int)
            and not isinstance(cut.get("planned_frame_index"), bool)
            and cut.get("planned_frame_index") == planned_frame
            and isinstance(actual_frame, int)
            and not isinstance(actual_frame, bool)
            and actual_frame == expected_actual_frame
            and round(float(cut["timestamp_s"]) * fps) == expected_actual_frame
            and math.isclose(
                float(cut["timestamp_s"]),
                expected_actual_frame / fps,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            and _finite_number(cut.get("actual_timestamp_s"))
            and math.isclose(
                float(cut["actual_timestamp_s"]),
                expected_actual_frame / fps,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            and isinstance(cut.get("delta_frames"), int)
            and not isinstance(cut.get("delta_frames"), bool)
            and cut.get("delta_frames") == actual_frame - planned_frame
            and abs(cut["delta_frames"]) <= 2
        ):
            return False
    return True


def _ambiguous_submission_count(root: Path) -> int:
    candidates: set[Path] = set()
    for directory in (
        root / "rejected" / "generation_attempts",
        root / "shots",
    ):
        if not directory.is_dir() or directory.is_symlink():
            continue
        candidates.update(directory.rglob("*.gateway.json"))
        candidates.update(directory.rglob("gateway_state.json"))
    count = 0
    for path in candidates:
        if not _regular_file(path):
            continue
        payload = _read_json_object(path)
        if payload.get("status") != "submitting":
            continue
        adjudication = payload.get("adjudication")
        nested_outcome = (
            adjudication.get("outcome") if isinstance(adjudication, Mapping) else None
        )
        top_level_outcome = payload.get("adjudicated_outcome")
        if not any(
            isinstance(outcome, str) and outcome.strip().lower() in _TERMINAL_OUTCOMES
            for outcome in (nested_outcome, top_level_outcome)
        ):
            count += 1
    return count


def _state_for_paths(paths: Sequence[Path], valid: bool) -> str:
    if valid:
        return "current"
    return "stale" if any(path.exists() for path in paths) else "missing"


def _read_json_object(path: Path | None) -> Mapping[str, Any]:
    if path is None or not _regular_file(path):
        return {}
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _regular_file(path: Path) -> bool:
    try:
        absolute = path.expanduser().absolute()
        cursor = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            cursor /= part
            if cursor.is_symlink():
                return False
        return absolute.is_file() and not absolute.is_symlink()
    except OSError:
        return False


def _safe_file_sha256(path: Path | None) -> str:
    if path is None or not _regular_file(path):
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _relative_file(root: Path, value: Any) -> Path | None:
    relative = _canonical_relative_path(value)
    if relative is None:
        return None
    path = root / relative
    if not _is_inside(root, path) or _path_uses_symlink(root, path):
        return None
    return path if _regular_file(path) else None


def _relative_directory(root: Path, value: Any) -> Path | None:
    relative = _canonical_relative_path(value)
    if relative is None:
        return None
    path = root / relative
    if (
        not _is_inside(root, path)
        or _path_uses_symlink(root, path)
        or not path.is_dir()
        or path.is_symlink()
    ):
        return None
    return path


def _absolute_file(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = Path(value)
    if (
        not path.is_absolute()
        or str(path) != value
        or path != path.resolve(strict=False)
    ):
        return None
    return path if _regular_file(path) else None


def _canonical_relative_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    return relative


def _relative_binding_is_current(
    root: Path,
    value: Any,
    *,
    path_key: str = "path",
    hash_key: str = "sha256",
) -> bool:
    if not isinstance(value, Mapping):
        return False
    path = _relative_file(root, value.get(path_key))
    expected_hash = value.get(hash_key)
    return bool(
        path is not None
        and _valid_sha256(expected_hash)
        and _safe_file_sha256(path) == expected_hash
    )


def _binding_matches(value: Any, path: str, sha256: str) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"path", "sha256"}
        and value.get("path") == path
        and value.get("sha256") == sha256
    )


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def _path_uses_symlink(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    cursor = root
    try:
        if cursor.is_symlink():
            return True
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                return True
    except OSError:
        return True
    return False


def _sha_string(value: Any) -> str:
    return value if _valid_sha256(value) else ""


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _json_sha256(value: Any) -> str:
    contents = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(contents).hexdigest()


def _plan_from_args(args: argparse.Namespace) -> PetReplicaPlan:
    root = _canonical_output_root(args.output_dir)
    source_arg = str(getattr(args, "source", "") or "")
    source = _source_from_binding(root)
    if source is None:
        raise PetReplicaCLIError(
            "A persisted source binding is required; run the plan stage first."
        )
    if source_arg and _canonical_source(source_arg) != source:
        raise PetReplicaCLIError("Later stages must use the persisted source binding.")
    return build_pet_replica_plan(source, root)


def _canonical_source(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise PetReplicaCLIError("--source must be an absolute MP4 path.")
    if raw.suffix.lower() != ".mp4":
        raise PetReplicaCLIError("--source must identify an MP4 file.")
    if raw.is_symlink():
        raise PetReplicaCLIError("--source may not be a symlink.")
    try:
        source = raw.resolve(strict=True)
    except OSError as exc:
        raise PetReplicaCLIError("--source must be an existing regular file.") from exc
    if raw != source or not source.is_file():
        raise PetReplicaCLIError("--source must be an absolute canonical file.")
    return source


def _canonical_output_root(value: str | Path) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise PetReplicaCLIError("--output-dir must be an absolute path.")
    root = raw.resolve(strict=False)
    if raw != root:
        raise PetReplicaCLIError("--output-dir must be resolved and canonical.")
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise PetReplicaCLIError("--output-dir must be a regular directory.")
    return root


def _source_binding_path(root: Path) -> Path:
    return root / "reference" / "source_binding.json"


def _source_from_binding(root: Path) -> Path | None:
    binding_path = _source_binding_path(root)
    payload = _read_json_object(binding_path)
    if not payload:
        return None
    if (
        set(payload) != {"schema_version", "source_path", "source_sha256"}
        or payload.get("schema_version") != SOURCE_BINDING_SCHEMA_VERSION
    ):
        raise PetReplicaCLIError("The persisted source binding schema is invalid.")
    source = _canonical_source(str(payload.get("source_path") or ""))
    source_sha256 = payload.get("source_sha256")
    reference = _read_json_object(root / "reference" / "reference_manifest.json")
    if (
        not _valid_sha256(source_sha256)
        or source_sha256 != _safe_file_sha256(source)
        or reference.get("source_sha256") != source_sha256
    ):
        raise PetReplicaCLIError("The persisted source binding is stale.")
    return source


def _source_binding_is_current(plan: PetReplicaPlan) -> bool:
    payload = _read_json_object(_source_binding_path(plan.output_root))
    expected_keys = {"schema_version", "source_path", "source_sha256"}
    source_sha256 = payload.get("source_sha256")
    return bool(
        set(payload) == expected_keys
        and payload.get("schema_version") == SOURCE_BINDING_SCHEMA_VERSION
        and payload.get("source_path") == str(plan.source_video)
        and _valid_sha256(source_sha256)
        and source_sha256 == _safe_file_sha256(plan.source_video)
    )


def _write_source_binding(plan: PetReplicaPlan, source_sha256: str) -> Path:
    if not _valid_sha256(source_sha256):
        raise PetReplicaCLIError("The reference source hash is unavailable.")
    path = _source_binding_path(plan.output_root)
    _write_json_atomic(
        path,
        {
            "schema_version": SOURCE_BINDING_SCHEMA_VERSION,
            "source_path": str(plan.source_video),
            "source_sha256": source_sha256,
        },
    )
    return path


def _plan_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    source = _canonical_source(args.source)
    root = _canonical_output_root(args.output_dir)
    plan = build_pet_replica_plan(source, root)
    source_hash = _safe_file_sha256(source)
    if not _valid_sha256(source_hash):
        raise PetReplicaCLIError("The reference source hash is unavailable.")
    if not _task_1_artifacts_are_current(plan, source_hash):
        write_pet_replica_plan(plan)
    final_source_hash = _safe_file_sha256(source)
    if not _valid_sha256(final_source_hash) or final_source_hash != source_hash:
        raise PetReplicaCLIError("The reference source changed while writing the plan.")
    _write_source_binding(plan, source_hash)
    return 0, {
        "success": True,
        "stage": "plan",
        "project_id": plan.project_id,
        "duration_s": plan.duration_s,
        "shot_count": len(plan.shots),
        "artifacts": [
            "reference/reference_manifest.json",
            "reference/shot_timeline.json",
            "reference/source_binding.json",
            "story_contract.md",
        ],
        "next_stage": "reference",
    }


def _reference_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    evidence = extract_reference_evidence(plan)
    template = plan.output_root / "reference" / "shot_annotations.template.json"
    if not template.is_file():
        write_shot_annotation_template(plan)
    annotations_reviewed = False
    try:
        load_reviewed_shot_annotations(plan, require_ocr_events=True)
        annotations_reviewed = True
    except Exception:
        annotations_reviewed = False
    return (0 if annotations_reviewed else 1), {
        "success": annotations_reviewed,
        "stage": "reference",
        "evidence_manifest": str(evidence.relative_to(plan.output_root)),
        "annotations_reviewed": annotations_reviewed,
        "blocked_reasons": (
            []
            if annotations_reviewed
            else ["All source shots and OCR evidence require manual review."]
        ),
        "next_stage": "audio" if annotations_reviewed else "reference",
    }


def _audio_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    annotations = load_reviewed_shot_annotations(plan, require_ocr_events=True)
    manifest = extract_replica_audio(plan, annotations)
    return 0, {
        "success": True,
        "stage": "audio",
        "shot_count": len(manifest.shots),
        "usage_scope": manifest.usage_scope,
        "public_release_ready": manifest.public_release_ready,
        "next_stage": "assets",
    }


def _assets_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    try:
        approved = load_approved_replica_assets(plan)
    except Exception:
        approved = None
    if approved is not None:
        return 0, {
            "success": True,
            "stage": "assets",
            "approved": True,
            "asset_count": len(approved.assets),
            "next_stage": "generate",
        }

    cat_root = APPROVED_CAT_REFERENCE_ROOT
    jobs = prepare_replica_asset_jobs(
        plan,
        cat_root / "奶糖_reference.png",
        cat_root / "豆包_reference.png",
    )
    if not args.enable_live:
        return 0, {
            "success": True,
            "stage": "assets",
            "executed": False,
            "planned_count": len(jobs),
            "blocked_reasons": [
                "Paid asset generation requires explicit --enable-live."
            ],
            "next_stage": "assets",
        }
    manifest = generate_replica_assets(
        plan,
        jobs,
        GatewayImageClient,
        True,
        woman_master_reference=(
            plan.output_root / "assets" / "inputs" / "woman_master.png"
        ),
        scene_master_reference=(
            plan.output_root / "assets" / "inputs" / "scene_master.png"
        ),
    )
    review_template = write_replica_asset_review_template(plan)
    return 1, {
        "success": False,
        "stage": "assets",
        "executed": True,
        "asset_count": len(manifest.assets),
        "review_template": str(review_template.relative_to(plan.output_root)),
        "blocked_reasons": ["Generated assets require manual review and approval."],
        "next_stage": "assets",
    }


def _generate_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    current = pet_replica_status(plan)
    if current["ambiguous_submission_count"]:
        return 1, {
            "success": False,
            "stage": "generate",
            "executed": False,
            "state": "blocked_ambiguous_submission",
            "ambiguous_submission_count": current["ambiguous_submission_count"],
            "blocked_reasons": [
                "An archived submitting attempt requires task resume or explicit adjudication."
            ],
            "next_stage": "generate",
        }
    annotations = load_reviewed_shot_annotations(plan)
    assets = load_approved_replica_assets(plan)
    audio = validate_replica_audio_manifest(
        plan,
        plan.output_root / "audio" / "audio_manifest.json",
    )
    jobs = build_replica_shot_jobs(
        plan,
        annotations,
        assets,
        audio,
        args.pilot_only,
        shot_ids=tuple(args.shot) or None,
        candidate_number=args.candidate,
        postprocess_lipsync=args.postprocess_lipsync,
    )
    job_plan = _write_job_plan(plan, jobs, pilot_only=args.pilot_only)
    config = GatewayVideoConfig(
        api_key=_configured_gateway_api_key(),
        base_url=_configured_gateway_base_url(),
        model=VIDEO_MODEL,
        send_idempotency_key=True,
    )
    candidates = generate_replica_candidates(
        plan,
        jobs,
        config,
        args.enable_live,
        args.replace_stale,
    )
    return 0, {
        "success": True,
        "stage": "generate",
        "executed": bool(args.enable_live),
        "dry_run": not args.enable_live,
        "planned_count": len(jobs),
        "completed_count": len(candidates),
        "job_plan": str(job_plan.relative_to(plan.output_root)),
        "next_stage": "review" if candidates else "generate",
    }


def _write_job_plan(
    plan: PetReplicaPlan,
    jobs: Sequence[Any],
    *,
    pilot_only: bool,
) -> Path:
    path = (
        plan.output_root
        / "shots"
        / ("pilot_jobs.json" if pilot_only else "full_jobs.json")
    )
    records = []
    for job in jobs:
        payload = asdict(job)
        for key in (
            "output_path",
            "gateway_report_path",
            "composition_path",
            "audio_path",
        ):
            value = payload.get(key)
            payload[key] = (
                str(Path(value).relative_to(plan.output_root)) if value else None
            )
        payload["reference_images"] = [
            str(Path(value).relative_to(plan.output_root))
            for value in payload.get("reference_images", ())
        ]
        records.append(payload)
    _write_json_atomic(
        path,
        {
            "schema_version": "motion-comic-factory.pet-replica-jobs.v1",
            "source_sha256": _safe_file_sha256(plan.source_video),
            "pilot_only": pilot_only,
            "jobs": records,
        },
    )
    return path


def _review_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    requested = set(args.shot)
    shots = [
        shot
        for shot in plan.shots
        if (not args.pilot_only or shot.start_s < plan.pilot_end_s)
        and (not requested or shot.shot_id in requested)
    ]
    if not shots:
        raise PetReplicaCLIError("No replica shots were selected for review.")
    results = []
    for shot in shots:
        candidate_path = (
            plan.output_root
            / "shots"
            / shot.shot_id
            / f"candidate_{args.candidate:02d}.mp4"
        )
        provenance_path = candidate_path.with_suffix(".provenance.json")
        provenance = _read_json_object(provenance_path)
        candidate_hash = _safe_file_sha256(candidate_path)
        if not candidate_hash:
            raise PetReplicaCLIError(
                f"{shot.shot_id} candidate {args.candidate} is missing."
            )
        candidate = ReplicaCandidate(
            shot_id=shot.shot_id,
            candidate_number=args.candidate,
            video_path=candidate_path,
            provenance_path=provenance_path,
            gateway_report_path=candidate_path.with_suffix(".gateway.json"),
            editorial_duration_s=shot.duration_s,
            generation_duration_s=int(provenance.get("provider_duration_s") or 0),
            output_sha256=candidate_hash,
        )
        results.append(review_replica_candidate(plan, shot, candidate))
    passing = sum(result.passed for result in results)
    return 1, {
        "success": False,
        "stage": "review",
        "reviewed_count": len(results),
        "automatic_pass_count": passing,
        "auto_approved": False,
        "blocked_reasons": [
            "Passing candidates still require all manual review gates and explicit approval."
        ],
        "next_stage": "review",
    }


def _compose_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    plan = _plan_from_args(args)
    manifest = (
        compose_replica_pilot(plan) if args.pilot_only else compose_replica_final(plan)
    )
    return 0, {
        "success": True,
        "stage": "compose",
        "mode": manifest.mode,
        "shot_count": len(manifest.shots),
        "current_pointer": f"final/{manifest.mode}_current.json",
        "next_stage": "status",
    }


def _status_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    return 0, pet_replica_status(_plan_from_args(args))


def _run_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    root = _canonical_output_root(args.output_dir)
    project_artifacts = (
        root / "reference" / "reference_manifest.json",
        root / "reference" / "shot_timeline.json",
        root / "story_contract.md",
    )
    actions: list[str] = []
    if not _source_binding_path(root).is_file() and not any(
        path.exists() for path in project_artifacts
    ):
        _plan_command(args)
        actions.append("plan")
    plan = _plan_from_args(args)
    current = pet_replica_status(plan)
    gate_key = "pilot_first_missing_gate" if args.pilot_only else "first_missing_gate"
    stopped_at = current[gate_key]

    if stopped_at == "reference":
        reference_artifacts = (
            root / "reference" / "evidence_manifest.json",
            root / "reference" / "shot_annotations.json",
        )
        if not any(path.exists() for path in reference_artifacts):
            code, detail = _reference_command(args)
            actions.append("reference")
            return code, {
                "success": False,
                "stage": "run",
                "stopped_at": "reference",
                "auto_approved": False,
                "actions": actions,
                "blocked_reasons": detail.get("blocked_reasons", []),
            }
    elif stopped_at == "audio":
        code, detail = _audio_command(args)
        actions.append("audio")
        if code != 0:
            return code, {
                "success": False,
                "stage": "run",
                "stopped_at": "audio",
                "auto_approved": False,
                "actions": actions,
                "blocked_reasons": detail.get("blocked_reasons", []),
            }
        current = pet_replica_status(plan)
        stopped_at = current[gate_key]
    elif stopped_at == "compose" and (
        current["full_ready"] or (args.pilot_only and current["pilot_ready"])
    ):
        code, detail = _compose_command(args)
        actions.append("compose")
        if code != 0:
            return code, {
                "success": False,
                "stage": "run",
                "stopped_at": "compose",
                "auto_approved": False,
                "actions": actions,
                "blocked_reasons": detail.get("blocked_reasons", []),
            }
        current = pet_replica_status(plan)
        stopped_at = current[gate_key]

    if stopped_at == "complete":
        return 0, {
            "success": True,
            "stage": "run",
            "stopped_at": "complete",
            "auto_approved": False,
            "actions": actions,
            "status": current,
        }
    return 1, {
        "success": False,
        "stage": "run",
        "stopped_at": stopped_at,
        "auto_approved": False,
        "actions": actions,
        "status": current,
        "blocked_reasons": [
            (
                "Run stopped at a paid or manual gate; invoke the explicit stage "
                "after operator review."
            )
        ],
    }


def execute_pet_replica_stage(
    args: argparse.Namespace,
    stage: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one replica operation without CLI parsing, printing, or subprocess hops."""
    handlers = {
        "plan": _plan_command,
        "reference": _reference_command,
        "audio": _audio_command,
        "assets": _assets_command,
        "generate": _generate_command,
        "review": _review_command,
        "compose": _compose_command,
        "status": _status_command,
        "run": _run_command,
    }
    selected = str(stage or args.stage)
    try:
        handler = handlers[selected]
    except KeyError as exc:
        raise PetReplicaCLIError(f"Unknown pet replica stage: {selected}") from exc
    return handler(args)


def pet_replica_command(args: argparse.Namespace) -> int:
    try:
        code, payload = execute_pet_replica_stage(args)
    except PetReplicaCLIError:
        code = 1
        payload = {
            "success": False,
            "stage": args.stage,
            "error": "Pet replica stage failed.",
            "error_code": "invalid_request",
        }
    except Exception:
        code = 1
        payload = {
            "success": False,
            "stage": args.stage,
            "error": "Pet replica stage failed.",
            "error_code": "stage_failed",
        }
    _print_json(payload)
    return code


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _print_json(payload: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            _sanitize_json(payload),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _sanitize_json(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_json(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item, key=key) for item in value]
    return _sanitize_text(value) if isinstance(value, str) else value


def _sanitize_text(value: str) -> str:
    text = _SECRET.sub("[redacted]", value)
    if text.lower().startswith("data:"):
        return "[redacted-data-uri]"
    if text.startswith(("http://", "https://")):
        parsed = urlsplit(text)
        text = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    text = _ABSOLUTE_PATH.sub("[local-path]", text)
    for name, secret in os.environ.items():
        if secret and _SENSITIVE_KEY.search(name):
            text = text.replace(secret, "[redacted]")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    return pet_replica_command(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
