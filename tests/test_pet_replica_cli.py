from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from factory.pet_replica import build_pet_replica_plan, write_pet_replica_plan
from factory.pet_replica_assets import (
    ASSET_REVIEW_SCHEMA_VERSION,
    ASSET_SCHEMA_VERSION,
)
from factory.pet_replica_compose import (
    COMPOSITION_SCHEMA_VERSION,
    replica_composition_window,
)
from factory.pet_replica_review import MANUAL_REVIEW_GATES, REVIEW_SCHEMA_VERSION


CLI_SPEC = importlib.util.find_spec("factory.pet_replica_cli")
CLI = importlib.import_module("factory.pet_replica_cli") if CLI_SPEC else None
REFERENCE_SCHEMA = "motion-comic-factory.pet-replica-reference.v1"
ANNOTATION_SCHEMA = "motion-comic-factory.pet-replica-annotations.v2"
OCR_SCHEMA = "motion-comic-factory.pet-replica-ocr-evidence.v1"
AUDIO_SCHEMA = "motion-comic-factory.pet-replica-audio.v1"
SELECTION_SCHEMA = "motion-comic-factory.pet-replica-selection.v1"
SOURCE_BINDING_SCHEMA = "motion-comic-factory.pet-replica-cli-source-binding.v1"
TEST_GATEWAY_BASE_URL = "HTTPS://Gateway.Example.test:443/v1/../v1/"
TEST_GATEWAY_ENDPOINT_SHA256 = (
    "530252d41b045906bb3363a5efc0972dea707d572b20a7d27be2a06dfc757e61"
)


def _require_cli():
    assert CLI is not None, "factory.pet_replica_cli must exist"
    return CLI


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    (
        (
            "HTTPS://user:secret@Gateway.Example.test:443/v1/../v1/"
            "?api_key=secret#fragment",
            "530252d41b045906bb3363a5efc0972dea707d572b20a7d27be2a06dfc757e61",
        ),
        (
            "HTTP://Gateway.Example.test:80/api/../v1",
            "8d48f02502f5b16e60dcc5e20a60fffaed55dca1fed95267ff875380fa7d019e",
        ),
        (
            "https://Gateway.Example.test:8443/v1",
            "29341c5ed5d0c95b9b13727ba8624f4a220b339a4dcf9df81c1f78bbbefb3c01",
        ),
    ),
)
def test_gateway_endpoint_fingerprint_normalizes_credential_free_identity(
    endpoint,
    expected,
) -> None:
    from factory.gateway_endpoint import gateway_endpoint_fingerprint

    assert gateway_endpoint_fingerprint(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    (
        "ftp://gateway.example.test/v1",
        "/relative/v1",
        "https:///v1",
        "https://gateway.example.test:not-a-port/v1",
        "https://gateway.example.test:65536/v1",
    ),
)
def test_gateway_endpoint_fingerprint_rejects_invalid_endpoint(endpoint) -> None:
    from factory.gateway_endpoint import gateway_endpoint_fingerprint
    from factory.gateway_video import GatewayVideoError

    with pytest.raises(GatewayVideoError):
        gateway_endpoint_fingerprint(endpoint)


@pytest.fixture(autouse=True)
def _isolated_approved_cat_root(tmp_path, monkeypatch) -> None:
    cli = _require_cli()
    import factory.pet_replica_assets as asset_contract

    root = (tmp_path / "approved-cat-root").resolve()
    root.mkdir(parents=True)
    monkeypatch.setattr(cli, "APPROVED_CAT_REFERENCE_ROOT", root)
    monkeypatch.setattr(asset_contract, "APPROVED_CAT_REFERENCE_ROOT", root)
    monkeypatch.setenv("OPENAI_BASE_URL", TEST_GATEWAY_BASE_URL)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_canonical_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _write_bound_file(root: Path, relative: str, contents: bytes) -> dict[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return {"path": relative, "sha256": _sha256(path)}


def _plan(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = (tmp_path / "reference.mp4").resolve()
    source.write_bytes(b"source-video-v1")
    output = (tmp_path / "output").resolve()
    return build_pet_replica_plan(source, output)


def _sample_timestamps(
    start_s: float,
    end_s: float,
    count: int,
    fps: int,
    last_video_frame_s: float,
) -> tuple[float, ...]:
    final = min(max(start_s, end_s - 1 / fps), last_video_frame_s)
    increment = (final - start_s) / (count - 1)
    return tuple(start_s + index * increment for index in range(count))


def _shot_id_at(plan, timestamp_s: float) -> str:
    for shot in plan.shots:
        if shot.start_s <= timestamp_s < shot.end_s:
            return shot.shot_id
    return plan.shots[-1].shot_id


def _write_source_binding_fixture(plan) -> None:
    _write_json(
        plan.output_root / "reference" / "source_binding.json",
        {
            "schema_version": SOURCE_BINDING_SCHEMA,
            "source_path": str(plan.source_video),
            "source_sha256": _sha256(plan.source_video),
        },
    )


def _write_reference_fixture(plan) -> str:
    write_pet_replica_plan(plan)
    root = plan.output_root
    source_sha256 = _sha256(plan.source_video)
    _write_json(
        root / "reference" / "reference_manifest.json",
        {
            "schema_version": REFERENCE_SCHEMA,
            "source_sha256": source_sha256,
            "duration_s": plan.duration_s,
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_sample_rate": 44100,
            "audio_channels": 2,
            "last_video_frame_s": 77.133333,
        },
    )

    last_video_frame_s = 77.133333
    frames = []
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
            relative = f"reference/shots/{shot.shot_id}/{label}.jpg"
            binding = _write_bound_file(
                root,
                relative,
                f"{shot.shot_id}-{label}".encode(),
            )
            frames.append(
                {
                    "command": "ffmpeg <redacted-source>",
                    "image_path": binding["path"],
                    "image_sha256": binding["sha256"],
                    "label": label,
                    "shot_id": shot.shot_id,
                    "source_sha256": source_sha256,
                    "timestamp_s": timestamp_s,
                }
            )
    for prefix, count, end_s in (
        ("pilot", 12, plan.pilot_end_s),
        ("full_01", 40, plan.duration_s),
    ):
        for index, timestamp_s in enumerate(
            _sample_timestamps(
                0.0,
                end_s,
                count,
                plan.fps,
                last_video_frame_s,
            ),
            start=1,
        ):
            relative = f"reference/contact_sheets/{prefix}_frames/{index:03d}.jpg"
            binding = _write_bound_file(
                root,
                relative,
                f"{prefix}-contact-{index}".encode(),
            )
            frames.append(
                {
                    "command": "ffmpeg <redacted-source>",
                    "image_path": binding["path"],
                    "image_sha256": binding["sha256"],
                    "label": f"{prefix}_{index:03d}",
                    "shot_id": _shot_id_at(plan, timestamp_s),
                    "source_sha256": source_sha256,
                    "timestamp_s": timestamp_s,
                }
            )
    contacts = []
    for relative, layout in (
        ("reference/contact_sheets/pilot_4x3.jpg", "4x3"),
        ("reference/contact_sheets/full_01_5x8.jpg", "5x8"),
    ):
        binding = _write_bound_file(root, relative, layout.encode())
        contacts.append(
            {
                "image_path": binding["path"],
                "image_sha256": binding["sha256"],
                "layout": layout,
                "source_sha256": source_sha256,
            }
        )
    _write_canonical_json(
        root / "reference" / "evidence_manifest.json",
        {
            "schema_version": REFERENCE_SCHEMA,
            "source_sha256": source_sha256,
            "last_video_frame_s": last_video_frame_s,
            "frames": frames,
            "contact_sheets": contacts,
        },
    )

    annotations = []
    for shot in plan.shots:
        start_frame = round(shot.start_s * plan.fps)
        end_frame = round(shot.end_s * plan.fps)
        detection_id = f"{shot.shot_id}-DETECTION-001"
        evidence_payload = {
            "schema_version": OCR_SCHEMA,
            "source_sha256": source_sha256,
            "shot_id": shot.shot_id,
            "source_window": {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "start_s": start_frame / plan.fps,
                "end_s": end_frame / plan.fps,
            },
            "detected_items": [
                {
                    "detection_id": detection_id,
                    "detected_text": f"line {shot.shot_id}",
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_s": start_frame / plan.fps,
                    "end_s": end_frame / plan.fps,
                    "source_bbox": {
                        "x": 48,
                        "y": 940,
                        "width": 624,
                        "height": 180,
                    },
                }
            ],
            "reviewed_zero": False,
        }
        staging = root / "reference" / "ocr_evidence" / shot.shot_id / "evidence.json"
        _write_json(staging, evidence_payload)
        evidence_sha256 = _sha256(staging)
        evidence_path = staging.with_name(f"{evidence_sha256}.json")
        staging.replace(evidence_path)
        event_id = f"{shot.shot_id}-OCR-DIALOGUE-001"
        annotations.append(
            {
                "shot_id": shot.shot_id,
                "characters": ["source_woman"],
                "speaker": "source_woman",
                "scene_anchor_id": "scene_sofa",
                "location": "living room",
                "framing": "source aligned",
                "action": "reviewed source action",
                "source_audio": True,
                "manual_review_required": False,
                "ocr_review": {
                    "evidence_path": str(evidence_path.relative_to(root)),
                    "evidence_sha256": evidence_sha256,
                    "detected_item_count": 1,
                    "review_complete": True,
                },
                "ocr_events": [
                    {
                        "event_id": event_id,
                        "detection_id": detection_id,
                        "classification": "dialogue_subtitle",
                        "reviewed_text": f"line {shot.shot_id}",
                        "start_frame": start_frame,
                        "end_frame": end_frame,
                        "start_s": start_frame / plan.fps,
                        "end_s": end_frame / plan.fps,
                        "manual_reviewed": True,
                        "placement": {
                            "x": 48,
                            "y": 940,
                            "width": 624,
                            "height": 180,
                            "alignment": "bottom_center",
                        },
                    }
                ],
            }
        )
    _write_json(
        root / "reference" / "shot_annotations.json",
        {
            "schema_version": ANNOTATION_SCHEMA,
            "caption_safe_region": {
                "x": 36,
                "y": 880,
                "width": 648,
                "height": 320,
            },
            "shots": annotations,
        },
    )
    _write_source_binding_fixture(plan)
    return source_sha256


def _write_audio_fixture(plan, source_sha256: str) -> None:
    root = plan.output_root
    full = _write_bound_file(root, "audio/source_audio.aac", b"source-aac")
    packet_duration_s = plan.duration_s / 2
    source_timeline = {
        "sample_rate": 44100,
        "packet_count": 2,
        "first_packet_pts_s": 0.0,
        "first_packet_duration_s": packet_duration_s,
        "last_packet_pts_s": packet_duration_s,
        "last_packet_duration_s": packet_duration_s,
        "last_packet_end_s": plan.duration_s,
        "packet_span_s": plan.duration_s,
        "skip_samples": None,
        "discard_padding": None,
        "logical_duration_s": plan.duration_s,
    }
    raw_aac_timeline = {
        **source_timeline,
        "logical_duration_s": None,
    }
    shots = {}
    for shot in plan.shots:
        binding = _write_bound_file(
            root,
            f"audio/drive/{shot.shot_id}.wav",
            f"audio-{shot.shot_id}".encode(),
        )
        shots[shot.shot_id] = {
            "shot_id": shot.shot_id,
            **binding,
            "duration_s": shot.duration_s,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "source_start_s": shot.start_s,
            "source_end_s": shot.end_s,
        }
    _write_json(
        root / "audio" / "audio_manifest.json",
        {
            "schema_version": AUDIO_SCHEMA,
            "source_sha256": source_sha256,
            "full_source": {
                "shot_id": None,
                **full,
                "duration_s": plan.duration_s,
                "sample_rate": 44100,
                "channels": 2,
                "codec": "aac",
                "source_start_s": 0,
                "source_end_s": plan.duration_s,
            },
            "shots": shots,
            "source_timeline": source_timeline,
            "raw_aac_timeline": raw_aac_timeline,
            "normalized_payload": {
                "sha256": full["sha256"],
                "byte_count": len(b"source-aac"),
            },
            "usage_scope": "local_evaluation_only",
            "public_release_ready": False,
            "public_release_blocker": "Replace or license the source audio.",
        },
    )


def _write_assets_fixture(plan, source_sha256: str) -> None:
    from factory.pet_replica_assets import _expected_jobs

    root = plan.output_root
    jobs_contract = _expected_jobs(plan)

    assets = []
    for asset_id, filename in (
        ("naitang_reference", "奶糖_reference.png"),
        ("doubao_reference", "豆包_reference.png"),
    ):
        contents = f"asset-{asset_id}".encode()
        approved_source = Path(_require_cli().APPROVED_CAT_REFERENCE_ROOT) / filename
        approved_source.write_bytes(contents)
        relative = f"assets/characters/{filename}"
        binding = _write_bound_file(root, relative, contents)
        assets.append(
            {
                "asset_id": asset_id,
                "kind": "cat_reference",
                **binding,
                "width": 1440,
                "height": 2560,
                "provenance": "approved_pet_output",
                "source_sha256": binding["sha256"],
                "provider": "local",
                "model": "approved_pet_reference",
                "prompt": (
                    "Immutable approved cat identity reference copied without "
                    "modification."
                ),
                "creation_mode": "copied_approved_cat_reference",
                "reference_asset_id": None,
                "reference_path": None,
                "reference_sha256": None,
                "source_id": filename,
            }
        )

    master_records = []
    for asset_id, source_relative, provenance, prompt in (
        (
            "woman_master",
            "assets/inputs/woman_master.png",
            "project_original_woman_master",
            "Project-original photorealistic adult woman identity master.",
        ),
        (
            "scene_master",
            "assets/inputs/scene_master.png",
            "project_empty_scene_master",
            "Project-original empty photographed apartment geometry master.",
        ),
    ):
        source = _write_bound_file(
            root,
            source_relative,
            f"source-{asset_id}".encode(),
        )
        installed = _write_bound_file(
            root,
            f"assets/masters/{asset_id}.png",
            f"source-{asset_id}".encode(),
        )
        master_records.append(
            {
                "asset_id": asset_id,
                "kind": "master_reference",
                **installed,
                "width": 1440,
                "height": 2560,
                "provenance": provenance,
                "source_sha256": source["sha256"],
                "provider": "local",
                "model": "project_master_reference",
                "prompt": prompt,
                "creation_mode": "copied_project_master",
                "reference_asset_id": None,
                "reference_path": None,
                "reference_sha256": None,
                "source_id": source_relative,
            }
        )
    assets.extend(master_records)

    for job in jobs_contract:
        relative = job.output_path.relative_to(root).as_posix()
        binding = _write_bound_file(
            root,
            relative,
            f"asset-{job.asset_id}".encode(),
        )
        reference = master_records[0] if job.kind == "woman" else master_records[1]
        assets.append(
            {
                "asset_id": job.asset_id,
                "kind": job.kind,
                **binding,
                "width": 1440,
                "height": 2560,
                "provenance": "gateway_generated",
                "source_sha256": None,
                "provider": "gateway",
                "model": job.model,
                "prompt": job.full_prompt,
                "creation_mode": "generated_anchor",
                "reference_asset_id": reference["asset_id"],
                "reference_path": reference["path"],
                "reference_sha256": reference["sha256"],
                "source_id": None,
            }
        )

    jobs = []
    for job in jobs_contract:
        payload = asdict(job)
        payload["output_path"] = job.output_path.relative_to(root).as_posix()
        jobs.append(payload)
    evidence_manifest = root / "reference" / "evidence_manifest.json"
    evidence_payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    evidence_sha256 = _json_sha256(evidence_payload)
    manifest_path = _write_json(
        root / "assets" / "asset_manifest.json",
        {
            "schema_version": ASSET_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "assets": assets,
            "jobs": jobs,
            "live_generation_enabled": True,
            "evidence_manifest_sha256": evidence_sha256,
            "evidence_frame_count": 163,
            "evidence_contact_sheet_count": 2,
        },
    )
    snapshots = [
        {key: value for key, value in item.items() if key != "kind"} for item in assets
    ]
    _write_json(
        root / "assets" / "asset_review.json",
        {
            "schema_version": ASSET_REVIEW_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "asset_manifest_sha256": _sha256(manifest_path),
            "evidence_manifest_sha256": evidence_sha256,
            "evidence_frame_count": 163,
            "evidence_contact_sheet_count": 2,
            "manual_review_required": False,
            "gates": {
                "original_woman_identity": True,
                "woman_identity_consistent": True,
                "woman_costume_consistent": True,
                "naitang_identity_match": True,
                "doubao_identity_match": True,
                "scene_geometry_match": True,
                "scene_light_direction_match": True,
                "no_source_person_identity": True,
                "no_platform_branding": True,
                "no_generated_text": True,
            },
            "assets": snapshots,
        },
    )


def _write_selection_fixture(plan, count: int) -> None:
    from factory.pet_replica_generation import (
        GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
        PROVIDER_REFERENCE_POLICY_VERSION,
        _prompt,
    )
    from factory.pet_replica_reference import load_reviewed_shot_annotations

    root = plan.output_root
    source_sha256 = _sha256(plan.source_video)
    evidence_manifest = root / "reference" / "evidence_manifest.json"
    evidence_payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    annotations = {
        annotation.shot_id: annotation
        for annotation in load_reviewed_shot_annotations(
            plan,
            require_ocr_events=True,
        )
    }
    for shot in plan.shots[:count]:
        candidate = root / "shots" / shot.shot_id / "candidate_01.mp4"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"candidate-{shot.shot_id}".encode())
        drive_audio_sha256 = _sha256(root / "audio" / "drive" / f"{shot.shot_id}.wav")
        annotation = annotations[shot.shot_id]
        provider_duration_s = max(4, math.ceil(shot.duration_s))
        prompt, _negative = _prompt(
            shot,
            annotation,
            provider_duration_s,
            voice_present=True,
            speaker_visible=True,
        )
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        composition_sha256 = _sha256(
            root / "reference" / "shots" / shot.shot_id / "start.jpg"
        )
        anchor_sha256 = [
            _sha256(root / "assets" / "scenes" / "scene_sofa.png"),
        ]
        endpoint_sha256 = TEST_GATEWAY_ENDPOINT_SHA256
        signature = {
            "source_sha256": source_sha256,
            "prompt_sha256": prompt_sha256,
            "anchor_sha256": anchor_sha256,
            "composition_sha256": composition_sha256,
            "reference_policy": PROVIDER_REFERENCE_POLICY_VERSION,
            "audio_sha256": drive_audio_sha256,
            "generate_audio": True,
            "audio_transport_contract": GATEWAY_DRIVE_AUDIO_SCHEMA_VERSION,
            "model": "doubao-seedance-2-0",
            "endpoint_fingerprint_sha256": endpoint_sha256,
            "candidate_number": 1,
            "editorial_duration_s": shot.duration_s,
            "provider_duration_s": provider_duration_s,
            "source_window": {
                "start_s": shot.start_s,
                "end_s": shot.end_s,
            },
        }
        gateway_report_path = candidate.with_suffix(".gateway.json")
        gateway_result = {
            "schema_version": "motion-comic-factory.gateway-video.v2",
            "provider": "gateway",
            "model": "doubao-seedance-2-0",
            "output_path": str(candidate),
            "state_path": str(candidate.with_suffix(".mp4.gateway.json")),
            "reference_image_count": 1,
            "plan_ready": True,
            "planned_count": 1,
            "executed": True,
            "success": True,
            "completed_count": 1,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "overwrite": False,
            "replace_stale": False,
            "blocked_reasons": [],
            "jobs": [{"shot_id": "single", "index": 1}],
            "results": [{"shot_id": "single", "status": "completed"}],
            "errors": [],
            "error": "",
        }
        _write_json(gateway_report_path, gateway_result)
        provenance = _write_json(
            candidate.with_suffix(".provenance.json"),
            {
                "schema_version": "motion-comic-factory.pet-replica-generation.v1",
                "provider": "gateway",
                "model": "doubao-seedance-2-0",
                "endpoint_fingerprint_sha256": endpoint_sha256,
                "shot_id": shot.shot_id,
                "candidate_number": 1,
                "source_sha256": source_sha256,
                "source_window": {
                    "start_s": shot.start_s,
                    "end_s": shot.end_s,
                },
                "editorial_duration_s": shot.duration_s,
                "provider_duration_s": provider_duration_s,
                "prompt_sha256": prompt_sha256,
                "anchor_sha256": anchor_sha256,
                "composition_sha256": composition_sha256,
                "output_path": str(candidate.relative_to(root)),
                "output_sha256": _sha256(candidate),
                "drive_audio_sha256": drive_audio_sha256,
                "gateway_report_path": str(gateway_report_path.relative_to(root)),
                "gateway_result": gateway_result,
                "signature": signature,
            },
        )
        contact = _write_bound_file(
            root,
            (
                f"shots/{shot.shot_id}/reviews/candidate_01/"
                f"{_sha256(candidate)}/attempt-current/contact_4x3.jpg"
            ),
            f"contact-{shot.shot_id}".encode(),
        )
        comparison = _write_bound_file(
            root,
            (
                f"shots/{shot.shot_id}/reviews/candidate_01/"
                f"{_sha256(candidate)}/attempt-current/"
                "source_candidate_start_middle_end.jpg"
            ),
            f"comparison-{shot.shot_id}".encode(),
        )
        mouth = _write_bound_file(
            root,
            (
                f"shots/{shot.shot_id}/reviews/candidate_01/"
                f"{_sha256(candidate)}/attempt-current/mouth_8fps.jpg"
            ),
            f"mouth-{shot.shot_id}".encode(),
        )
        source_records = []
        for label in ("start", "middle", "end"):
            record = next(
                item
                for item in evidence_payload["frames"]
                if item["shot_id"] == shot.shot_id and item["label"] == label
            )
            source_records.append(
                {
                    "label": label,
                    "path": record["image_path"],
                    "sha256": record["image_sha256"],
                }
            )
        drive_audio = {
            "path": f"audio/drive/{shot.shot_id}.wav",
            "sha256": _sha256(root / "audio" / "drive" / f"{shot.shot_id}.wav"),
        }
        bindings = {
            "candidate": {
                "path": str(candidate.relative_to(root)),
                "sha256": _sha256(candidate),
            },
            "provenance": {
                "path": str(provenance.relative_to(root)),
                "sha256": _sha256(provenance),
            },
            "source_evidence": {
                "manifest_path": str(evidence_manifest.relative_to(root)),
                "manifest_sha256": _sha256(evidence_manifest),
                "records": source_records,
            },
            "drive_audio": drive_audio,
            "evidence": {
                "contact_sheet": contact,
                "comparison_sheet": comparison,
                "mouth_sheet": mouth,
            },
        }
        review = _write_json(
            root / "shots" / shot.shot_id / "reviews" / "candidate_01.review.json",
            {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "shot_id": shot.shot_id,
                "candidate_number": 1,
                "candidate_sha256": _sha256(candidate),
                "passed": True,
                "failures": [],
                "attempt_id": "attempt-current",
                "review_path": (
                    f"shots/{shot.shot_id}/reviews/candidate_01.review.json"
                ),
                "evidence": {
                    name: binding["path"]
                    for name, binding in bindings["evidence"].items()
                },
                "bindings": bindings,
            },
        )
        _write_json(
            root / "shots" / shot.shot_id / "selection.json",
            {
                "schema_version": SELECTION_SCHEMA,
                "shot_id": shot.shot_id,
                "candidate_number": 1,
                "candidate_path": str(candidate.relative_to(root)),
                "candidate_sha256": _sha256(candidate),
                "manual_review_note": "Reviewed start, middle, and end frames.",
                "manual_gates": {gate: True for gate in MANUAL_REVIEW_GATES},
                "quality_approved": True,
                "quality_review_path": str(review.relative_to(root)),
                "quality_review_sha256": _sha256(review),
                "quality_bindings_sha256": _json_sha256(bindings),
                "quality_provenance_path": bindings["provenance"]["path"],
                "quality_provenance_sha256": bindings["provenance"]["sha256"],
                "quality_source_evidence_sha256": bindings["source_evidence"][
                    "manifest_sha256"
                ],
                "quality_drive_audio": drive_audio,
                "quality_evidence": bindings["evidence"],
            },
        )


def _write_release_fixture(
    plan,
    mode: str,
    *,
    audio_mode: str = "source_aac_stream_copy",
) -> None:
    root = plan.output_root
    release_id = f"{mode}-current"
    release = root / "final" / "releases" / release_id
    _, end_s = replica_composition_window(plan, mode)
    shots = [
        shot
        for shot in plan.shots
        if mode == "final" or shot.start_s < plan.pilot_end_s
    ]
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
    artifacts = {}
    for relative in (
        "subtitles.ass",
        "concat.ffconcat",
        "picture.mp4",
        clean_name,
        captioned_name,
        comparison_name,
    ):
        binding = _write_bound_file(
            release,
            relative,
            f"{mode}-{relative}".encode(),
        )
        artifacts[relative] = binding["sha256"]
    manifest_shots = []
    for shot in shots:
        shot_start = round(shot.start_s * plan.fps) / plan.fps
        shot_end = (
            plan.duration_s
            if shot.end_s == plan.duration_s
            else round(shot.end_s * plan.fps) / plan.fps
        )
        editorial_start = max(0.0, shot_start)
        editorial_end = min(end_s, shot_end)
        normalized = f"normalized/{shot.shot_id}.mp4"
        binding = _write_bound_file(
            release,
            normalized,
            f"normalized-{mode}-{shot.shot_id}".encode(),
        )
        artifacts[normalized] = binding["sha256"]
        candidate = root / "shots" / shot.shot_id / "candidate_01.mp4"
        manifest_shots.append(
            {
                "shot_id": shot.shot_id,
                "source_path": str(candidate),
                "source_sha256": _sha256(candidate),
                "source_start_s": editorial_start - shot_start,
                "source_end_s": editorial_end - shot_start,
                "editorial_duration_s": editorial_end - editorial_start,
                "timeline_start_s": editorial_start,
                "timeline_end_s": editorial_end,
                "normalized_path": str(release / normalized),
            }
        )

    annotations_path = root / "reference" / "shot_annotations.json"
    annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations_sha256 = _sha256(annotations_path)
    snapshot_path = release / "review_snapshot" / "shot_annotations.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(annotations_path.read_bytes())

    ocr_bindings = []
    ocr_snapshot_paths = []
    review_snapshot_evidence = []
    reviewed_events = []
    subtitles = []
    composition_end_frame = round(end_s * plan.fps)
    for annotation in annotations_payload["shots"]:
        review = annotation["ocr_review"]
        source_evidence = json.loads(
            (root / review["evidence_path"]).read_text(encoding="utf-8")
        )
        binding = {
            "shot_id": annotation["shot_id"],
            "evidence_path": review["evidence_path"],
            "evidence_sha256": review["evidence_sha256"],
            "detected_item_count": review["detected_item_count"],
            "review_complete": True,
            "reviewed_zero": source_evidence["reviewed_zero"],
        }
        ocr_bindings.append(binding)
        snapshot_relative = (
            f"review_snapshot/ocr_evidence/{annotation['shot_id']}/"
            f"{review['evidence_sha256']}.json"
        )
        snapshot = release / snapshot_relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((root / review["evidence_path"]).read_bytes())
        ocr_snapshot_paths.append(str(snapshot))
        review_snapshot_evidence.append(
            {
                "shot_id": annotation["shot_id"],
                "path": snapshot_relative,
                "sha256": review["evidence_sha256"],
                "detected_item_count": review["detected_item_count"],
            }
        )
        for event in annotation["ocr_events"]:
            overlap_start = max(0, event["start_frame"])
            overlap_end = min(composition_end_frame, event["end_frame"])
            if overlap_end <= overlap_start:
                continue
            reviewed_events.append(
                {
                    **event,
                    "shot_id": annotation["shot_id"],
                    "renderable": event["classification"] == "dialogue_subtitle",
                }
            )
            if event["classification"] == "dialogue_subtitle":
                subtitles.append(
                    {
                        "event_id": event["event_id"],
                        "shot_id": annotation["shot_id"],
                        "start_frame": overlap_start,
                        "end_frame": overlap_end,
                        "text": event["reviewed_text"],
                        "placement": event["placement"],
                        "start_s": overlap_start / plan.fps,
                        "end_s": overlap_end / plan.fps,
                    }
                )

    font_candidates = (
        (Path("/System/Library/Fonts/STHeiti Medium.ttc"), "Heiti SC"),
        (Path("/System/Library/Fonts/STHeiti Light.ttc"), "Heiti SC"),
    )
    font_path, font_family = next(
        (path, family) for path, family in font_candidates if path.is_file()
    )
    font_sha256 = _sha256(font_path)
    font_binding = {
        "path": str(font_path),
        "family": font_family,
        "sha256": font_sha256,
    }
    review_snapshot = {
        "annotations_path": "review_snapshot/shot_annotations.json",
        "annotations_sha256": annotations_sha256,
        "ocr_evidence": review_snapshot_evidence,
        "staged": True,
    }
    manifest_path = release / f"{mode}_composition_manifest.json"
    clean_path = release / clean_name
    captioned_path = release / captioned_name
    comparison_path = release / comparison_name
    source_audio_path = root / "audio" / "source_audio.aac"
    pointer_path = root / "final" / f"{mode}_current.json"
    comparison_duration_s = min(
        end_s,
        77.133333 + 1 / plan.fps,
    )
    clean_command = ["ffmpeg", "-i", str(release / "picture.mp4"), str(clean_path)]
    captioned_command = [
        "ffmpeg",
        "-i",
        str(release / "picture.mp4"),
        str(captioned_path),
    ]
    comparison_command = [
        "ffmpeg",
        "-i",
        str(captioned_path),
        str(comparison_path),
    ]
    manifest = {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "project_id": plan.project_id,
        "mode": mode,
        "output_root": str(root),
        "manifest_path": str(manifest_path),
        "start_s": 0.0,
        "end_s": end_s,
        "duration_s": end_s,
        "shots": manifest_shots,
        "ocr_evidence_bindings": ocr_bindings,
        "reviewed_ocr_events": reviewed_events,
        "subtitles": subtitles,
        "caption_safe_region": annotations_payload["caption_safe_region"],
        "reviewed_annotations_sha256": annotations_sha256,
        "reviewed_annotations_snapshot_path": str(snapshot_path),
        "ocr_evidence_snapshot_paths": ocr_snapshot_paths,
        "subtitle_font_path": str(font_path),
        "subtitle_font_family": font_family,
        "subtitle_font_sha256": font_sha256,
        "source_audio_path": str(source_audio_path),
        "source_audio_sha256": _sha256(source_audio_path),
        "presentation_source_path": str(plan.source_video),
        "presentation_source_sha256": _sha256(plan.source_video),
        "subtitle_path": str(release / "subtitles.ass"),
        "concat_list_path": str(release / "concat.ffconcat"),
        "picture_path": str(release / "picture.mp4"),
        "clean_master_path": str(clean_path),
        "captioned_master_path": str(captioned_path),
        "master_path": str(captioned_path),
        "master_alias": "captioned_master_path",
        "side_by_side_path": str(comparison_path),
        "final_qc_path": str(release / f"{mode}_qc.json"),
        "current_pointer_path": str(pointer_path),
        "audio_mode": audio_mode,
        "comparison_duration_s": comparison_duration_s,
        "comparison_tail_policy": "clamp_to_last_reference_video_frame",
        "clean_master_command": clean_command,
        "captioned_master_command": captioned_command,
        "ffmpeg_command": captioned_command,
        "side_by_side_command": comparison_command,
    }
    _write_json(manifest_path, manifest)
    input_bundle = {
        "source_audio_sha256": manifest["source_audio_sha256"],
        "presentation_source_sha256": manifest["presentation_source_sha256"],
        "selection": {
            item["shot_id"]: item["source_sha256"] for item in manifest_shots
        },
        "reviewed_annotations_sha256": annotations_sha256,
        "ocr_evidence": ocr_bindings,
        "review_snapshot": review_snapshot,
        "subtitle_font": font_binding,
    }
    clip_frame_counts = [
        round(item["editorial_duration_s"] * plan.fps) for item in manifest_shots
    ]
    cuts = []
    for item in manifest_shots[:-1]:
        frame_index = round(item["timeline_end_s"] * plan.fps)
        cuts.append(
            {
                "timestamp_s": frame_index / plan.fps,
                "offset_frames": 0,
                "before_sha256": hashlib.sha256(
                    f"{mode}-{item['shot_id']}-before".encode()
                ).hexdigest(),
                "after_sha256": hashlib.sha256(
                    f"{mode}-{item['shot_id']}-after".encode()
                ).hexdigest(),
                "planned_timestamp_s": item["timeline_end_s"],
                "actual_timestamp_s": frame_index / plan.fps,
                "planned_frame_index": frame_index,
                "actual_frame_index": frame_index,
                "delta_frames": 0,
            }
        )
    frame_sequence = {
        "frame_count": round(end_s * plan.fps),
        "clip_frame_counts": clip_frame_counts,
        "cuts": cuts,
    }
    fallback_used = audio_mode == "pcm_to_aac_192k_once"
    audio_proof = {
        "mode": audio_mode,
        "source_match": True,
        "fallback_used": fallback_used,
        "duration_s": end_s,
        "presentation_start_s": 0.0,
        "bit_rate": 192000,
        "source_skip_samples": 5058 if fallback_used else 0,
        "master_skip_samples": 1024 if fallback_used else 0,
        "pcm_correlation": 0.9998 if fallback_used else 1.0,
    }
    master_frame_boundaries = {
        "frame_count": frame_sequence["frame_count"],
        "cut_count": len(cuts),
        "cuts": [
            {
                "timestamp_s": item["timestamp_s"],
                "offset_frames": item["offset_frames"],
                "before_distance": 0.01,
                "after_distance": 0.01,
            }
            for item in cuts
        ],
    }
    blackdetect = {"pix_th": 0.10, "pic_th": 0.98, "detected": False}
    freezedetect = {"detected": False, "duration_threshold_s": 0.366}
    changed_frames = sorted(
        {
            frame
            for subtitle in subtitles
            for frame in range(subtitle["start_frame"], subtitle["end_frame"])
        }
    )
    caption_effects = {
        "method": (
            "decoded_rgb_framemd5_with_signalstats_threshold_and_safe_region_crops"
        ),
        "raw_changed_frame_count": len(changed_frames),
        "changed_frame_count": len(changed_frames),
        "changed_frames": changed_frames,
        "codec_noise_frames": [],
        "outside_window_changed_frames": [],
        "outside_safe_region_changed_frames": [],
        "events": [
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
        ],
    }
    source_pixel_samples = []
    requested_samples = []
    for item in manifest_shots:
        shot_start = max(0.0, item["timeline_start_s"])
        shot_end = min(end_s, item["timeline_end_s"])
        middle_frame = round(((shot_start + shot_end) / 2) * plan.fps)
        requested_samples.extend(
            (
                (shot_start, item["shot_id"], "shot_start"),
                (middle_frame / plan.fps, item["shot_id"], "shot_middle"),
                (shot_end - 1 / plan.fps, item["shot_id"], "shot_end"),
            )
        )
    for item in manifest_shots[:-1]:
        cut = item["timeline_end_s"]
        requested_samples.extend(
            (
                (cut - 1 / plan.fps, item["shot_id"], "cut_before"),
                (cut, item["shot_id"], "cut_at"),
                (cut + 1 / plan.fps, item["shot_id"], "cut_after"),
            )
        )
    last_frame_s = max(0.0, end_s - 1 / plan.fps)
    source_last_frame_s = max(0.0, comparison_duration_s - 1 / plan.fps)
    picture_frame_count = round(end_s * plan.fps)
    for index, (requested_time, shot_id, kind) in enumerate(requested_samples):
        timestamp_s = min(last_frame_s, max(0.0, requested_time))
        picture_frame_index = min(
            picture_frame_count - 1,
            max(0, round(timestamp_s * plan.fps)),
        )
        timestamp_s = picture_frame_index / plan.fps
        source_timestamp_s = min(source_last_frame_s, timestamp_s)
        source_pixel_samples.append(
            {
                "timestamp_s": timestamp_s,
                "source_timestamp_s": source_timestamp_s,
                "source_tail_clamped": source_timestamp_s < timestamp_s,
                "picture_frame_index": picture_frame_index,
                "shot_id": shot_id,
                "kind": kind,
                "picture_sha256": hashlib.sha256(
                    f"{mode}-picture-pixels-{index}".encode()
                ).hexdigest(),
                "source_sha256": hashlib.sha256(
                    f"{mode}-source-pixels-{index}".encode()
                ).hexdigest(),
                "perceptual_delta": 0.25,
            }
        )
    source_pixel_evidence = {
        "method": "shot_start_middle_end_and_cut_neighborhood",
        "threshold": 0.015,
        "reference_video_duration_s": comparison_duration_s,
        "tail_clamped_sample_count": sum(
            bool(item["source_tail_clamped"]) for item in source_pixel_samples
        ),
        "samples": source_pixel_samples,
    }
    masters = {}
    for variant, path in (
        ("clean", clean_path),
        ("captioned", captioned_path),
    ):
        masters[variant] = {
            "valid": True,
            "variant": variant,
            "path": str(path),
            "sha256": _sha256(path),
            "duration_s": end_s,
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "video_frames": frame_sequence["frame_count"],
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_channels": 2,
            "frame_boundaries": master_frame_boundaries,
            "audio": audio_proof,
            "blackdetect": blackdetect,
            "freezedetect": freezedetect,
        }
    masters["clean"].update(
        {
            "picture_preserved": True,
            "picture_proof": {
                "method": "decoded_rgb_framemd5_all_frames",
                "frame_count": frame_sequence["frame_count"],
                "picture_sha256": _sha256(release / "picture.mp4"),
                "clean_sha256": _sha256(clean_path),
            },
        }
    )
    masters["captioned"]["caption_effects"] = caption_effects
    comparison = {
        "valid": True,
        "tail_policy": "clamp_to_last_reference_video_frame",
        "path": str(comparison_path),
        "sha256": _sha256(comparison_path),
        "duration_s": comparison_duration_s,
        "policy_duration_s": comparison_duration_s,
        "width": plan.width * 2,
        "height": plan.height,
        "fps": plan.fps,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_channels": 2,
        "audio_source_match": True,
        "audio_start_s": 0.0,
        "audio_pcm_correlation": 1.0,
        "blackdetect": blackdetect,
        "freezedetect": freezedetect,
    }
    _write_json(
        release / f"{mode}_qc.json",
        {
            "schema_version": COMPOSITION_SCHEMA_VERSION,
            "valid": True,
            "mode": mode,
            "release_id": release_id,
            "master_path": manifest["master_path"],
            "master_sha256": _sha256(Path(manifest["master_path"])),
            "master_alias": "captioned_master_path",
            "clean_master_path": manifest["clean_master_path"],
            "captioned_master_path": manifest["captioned_master_path"],
            "duration_s": end_s,
            "composition_manifest_sha256": _sha256(manifest_path),
            "artifact_sha256": artifacts,
            "input_bundle": input_bundle,
            "ocr_evidence": ocr_bindings,
            "review_snapshot": review_snapshot,
            "subtitle_font": font_binding,
            "masters": masters,
            "frame_sequence": frame_sequence,
            "master_frame_boundaries": master_frame_boundaries,
            "blackdetect": blackdetect,
            "source_pixel_evidence": source_pixel_evidence,
            "comparison": comparison,
            "cut_count": len(cuts),
            "cut_timestamps_s": [
                {
                    "planned": item["planned_timestamp_s"],
                    "actual": item["actual_timestamp_s"],
                    "delta_frames": item["delta_frames"],
                }
                for item in cuts
            ],
            "audio": audio_proof,
            "audio_mode": audio_mode,
            "fallback_used": fallback_used,
        },
    )
    _write_json(
        root / "final" / f"{mode}_current.json",
        {
            "schema_version": COMPOSITION_SCHEMA_VERSION,
            "mode": mode,
            "release": release_id,
            "release_path": f"releases/{release_id}",
        },
    )


def _current_fixture(tmp_path: Path, *, selections: int = 0):
    plan = _plan(tmp_path)
    source_sha256 = _write_reference_fixture(plan)
    _write_audio_fixture(plan, source_sha256)
    _write_assets_fixture(plan, source_sha256)
    if selections:
        _write_selection_fixture(plan, selections)
    return plan


def _reseal_asset_contract(
    plan, manifest: dict[str, Any], review: dict[str, Any]
) -> None:
    manifest_path = _write_json(
        plan.output_root / "assets" / "asset_manifest.json",
        manifest,
    )
    review["asset_manifest_sha256"] = _sha256(manifest_path)
    review["assets"] = [
        {key: value for key, value in item.items() if key != "kind"}
        for item in manifest["assets"]
    ]
    _write_json(plan.output_root / "assets" / "asset_review.json", review)


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, int, str]]:
    snapshot = {}
    for path in sorted((root, *root.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == root else str(path.relative_to(root))
        digest = _sha256(path) if path.is_file() else ""
        snapshot[relative] = (
            stat.S_IFMT(metadata.st_mode),
            metadata.st_size,
            metadata.st_mtime_ns,
            digest,
        )
    return snapshot


def _file_byte_snapshot(root: Path) -> dict[str, tuple[bytes, str]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), _sha256(path))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_pet_replica_cli_module_exists() -> None:
    assert CLI_SPEC is not None


def test_factory_cli_pet_replica_plan_writes_source_locked_contract(
    tmp_path,
) -> None:
    source = (tmp_path / "reference.mp4").resolve()
    source.write_bytes(b"source-video")
    output = (tmp_path / "output").resolve()
    result = subprocess.run(
        [
            sys.executable,
            "factory_cli.py",
            "pet-replica",
            "--stage",
            "plan",
            "--source",
            str(source),
            "--output-dir",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["duration_s"] == pytest.approx(77.229569)
    assert payload["next_stage"] == "reference"
    binding = json.loads(
        (output / "reference" / "source_binding.json").read_text(encoding="utf-8")
    )
    assert binding["source_sha256"] == _sha256(source)


def test_plan_rerun_preserves_completed_flat_probe_project_byte_for_byte(
    tmp_path,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    current_before = cli.pet_replica_status(plan)
    binding_path = plan.output_root / "reference" / "source_binding.json"
    binding_path.unlink()
    before = _file_byte_snapshot(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "factory_cli.py",
            "pet-replica",
            "--stage",
            "plan",
            "--source",
            str(plan.source_video),
            "--output-dir",
            str(plan.output_root),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    after = _file_byte_snapshot(tmp_path)
    binding_relative = str(binding_path.relative_to(tmp_path))

    assert payload["next_stage"] == "reference"
    assert set(after) - set(before) == {binding_relative}
    assert {path: after[path] for path in before} == before
    current_after = cli.pet_replica_status(plan)
    for stage in ("plan", "reference", "audio", "assets"):
        assert current_before["stages"][stage] == "current"
        assert current_after["stages"][stage] == "current"


def test_plan_rerun_preserves_exact_initial_manifest_without_calling_writer(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    cli = _require_cli()
    plan = _plan(tmp_path)
    manifest_path, timeline_path, contract_path = write_pet_replica_plan(plan)
    before = {
        path: (path.read_bytes(), _sha256(path))
        for path in (manifest_path, timeline_path, contract_path)
    }

    def forbidden(_plan):
        pytest.fail("plan rewrote an exact current Task 1 contract")

    monkeypatch.setattr(cli, "write_pet_replica_plan", forbidden)

    assert (
        cli.main(
            [
                "--stage",
                "plan",
                "--source",
                str(plan.source_video),
                "--output-dir",
                str(plan.output_root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert {path: (path.read_bytes(), _sha256(path)) for path in before} == before


@pytest.mark.parametrize("failure_mode", ("source_changed", "final_read_failed"))
def test_plan_rejects_source_instability_without_replacing_binding(
    tmp_path,
    monkeypatch,
    capsys,
    failure_mode,
) -> None:
    cli = _require_cli()
    plan = _plan(tmp_path)
    write_pet_replica_plan(plan)
    _write_source_binding_fixture(plan)
    binding_path = plan.output_root / "reference" / "source_binding.json"
    binding_before = binding_path.read_bytes()
    real_sha256 = cli._safe_file_sha256
    source_reads = 0

    def unstable_source_sha256(path):
        nonlocal source_reads
        if Path(path) != plan.source_video:
            return real_sha256(path)
        source_reads += 1
        if source_reads == 1:
            digest = real_sha256(path)
            if failure_mode == "source_changed":
                plan.source_video.write_bytes(b"changed-after-first-hash")
            return digest
        if failure_mode == "final_read_failed":
            return ""
        return real_sha256(path)

    monkeypatch.setattr(cli, "_safe_file_sha256", unstable_source_sha256)

    assert (
        cli.main(
            [
                "--stage",
                "plan",
                "--source",
                str(plan.source_video),
                "--output-dir",
                str(plan.output_root),
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "error": "Pet replica stage failed.",
        "error_code": "invalid_request",
        "stage": "plan",
        "success": False,
    }
    assert source_reads == 2
    assert binding_path.read_bytes() == binding_before


@pytest.mark.parametrize(
    "mutation",
    (
        "reference_missing",
        "reference_malformed",
        "reference_wrong_type",
        "reference_unexpected_field",
        "reference_other_source",
        "flat_last_frame_wrong_type",
        "flat_last_frame_unaligned",
        "timeline_missing",
        "timeline_malformed",
        "timeline_reordered",
        "timeline_wrong_type",
        "timeline_unexpected_field",
        "story_missing",
        "story_prefix_with_drift",
    ),
)
def test_plan_rebuilds_complete_task_1_when_existing_contract_is_not_exact(
    tmp_path,
    capsys,
    mutation,
) -> None:
    cli = _require_cli()
    plan = _plan(tmp_path)
    manifest_path, timeline_path, contract_path = write_pet_replica_plan(plan)
    task_1_paths = (manifest_path, timeline_path, contract_path)
    expected = {path: path.read_bytes() for path in task_1_paths}

    if mutation == "reference_missing":
        manifest_path.unlink()
    elif mutation == "reference_malformed":
        manifest_path.write_text("{", encoding="utf-8")
    elif mutation.startswith("reference_"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if mutation == "reference_wrong_type":
            manifest["media_contract"]["fps"] = float(plan.fps)
        elif mutation == "reference_unexpected_field":
            manifest["unexpected"] = True
        else:
            manifest["source_sha256"] = "0" * 64
        _write_json(manifest_path, manifest)
    elif mutation.startswith("flat_last_frame_"):
        manifest = {
            "schema_version": REFERENCE_SCHEMA,
            "source_sha256": _sha256(plan.source_video),
            "duration_s": plan.duration_s,
            "width": plan.width,
            "height": plan.height,
            "fps": plan.fps,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_sample_rate": 44100,
            "audio_channels": 2,
            "last_video_frame_s": (
                77 if mutation == "flat_last_frame_wrong_type" else 77.14
            ),
        }
        _write_json(manifest_path, manifest)
    elif mutation == "timeline_missing":
        timeline_path.unlink()
    elif mutation == "timeline_malformed":
        timeline_path.write_text("[]", encoding="utf-8")
    elif mutation.startswith("timeline_"):
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        if mutation == "timeline_reordered":
            timeline["shots"][0], timeline["shots"][1] = (
                timeline["shots"][1],
                timeline["shots"][0],
            )
        elif mutation == "timeline_wrong_type":
            timeline["shots"][0]["index"] = 1.0
        else:
            timeline["unexpected"] = True
        _write_json(timeline_path, timeline)
    elif mutation == "story_missing":
        contract_path.unlink()
    else:
        contract_path.write_bytes(contract_path.read_bytes() + b"drift\n")

    assert (
        cli.main(
            [
                "--stage",
                "plan",
                "--source",
                str(plan.source_video),
                "--output-dir",
                str(plan.output_root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert {path: path.read_bytes() for path in task_1_paths} == expected


def test_status_is_pure_read_under_a_read_only_tree(tmp_path, monkeypatch) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    before = _tree_snapshot(tmp_path)

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "status called a provider, decoder, subprocess, or write entry point"
        )

    import factory.pet_replica_assets as assets
    import factory.pet_replica_audio as audio
    import factory.pet_replica_compose as compose
    import factory.pet_replica_generation as generation
    import factory.pet_replica_reference as reference
    import factory.pet_replica_review as review

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Image, "open", forbidden)
    monkeypatch.setattr(tempfile, "mkstemp", forbidden)
    monkeypatch.setattr(tempfile, "mkdtemp", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    monkeypatch.setattr(reference, "probe_reference_media", forbidden)
    monkeypatch.setattr(reference, "extract_reference_evidence", forbidden)
    monkeypatch.setattr(audio, "extract_replica_audio", forbidden)
    monkeypatch.setattr(audio, "validate_replica_audio_manifest", forbidden)
    monkeypatch.setattr(assets, "load_approved_replica_assets", forbidden)
    monkeypatch.setattr(assets, "generate_replica_assets", forbidden)
    monkeypatch.setattr(generation, "generate_replica_candidates", forbidden)
    monkeypatch.setattr(review, "validate_replica_selection", forbidden)
    monkeypatch.setattr(compose, "validate_replica_master", forbidden)
    monkeypatch.setattr(compose, "compose_replica_pilot", forbidden)
    monkeypatch.setattr(compose, "compose_replica_final", forbidden)

    original_modes = {
        path: stat.S_IMODE(path.lstat().st_mode)
        for path in (tmp_path, *tmp_path.rglob("*"))
    }
    try:
        for path in sorted(
            original_modes, key=lambda item: len(item.parts), reverse=True
        ):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        payload = cli.pet_replica_status(plan)
    finally:
        for path, mode in sorted(
            original_modes.items(), key=lambda item: len(item[0].parts)
        ):
            os.chmod(path, mode)

    assert payload["project_id"] == plan.project_id
    assert payload["pilot_ready"] is True
    assert payload["current_pilot"] is True
    assert _tree_snapshot(tmp_path) == before


def test_status_detects_stale_source_and_truncated_reference_evidence(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    plan.source_video.write_bytes(b"changed-source")
    stale = cli.pet_replica_status(plan)
    assert stale["source_current"] is False
    assert stale["first_missing_gate"] == "plan"

    plan = _current_fixture(tmp_path / "truncated")
    manifest_path = plan.output_root / "reference" / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frames"].pop()
    _write_json(manifest_path, manifest)
    truncated = cli.pet_replica_status(plan)
    assert truncated["stages"]["reference"] == "stale"
    assert truncated["counts"]["reference_frames"] == 162
    assert truncated["first_missing_gate"] == "reference"


@pytest.mark.parametrize("stage", ("audio", "assets", "selection", "review"))
def test_status_rejects_stale_bound_artifacts(tmp_path, stage) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    if stage == "audio":
        (plan.output_root / "audio" / "drive" / "R001.wav").write_bytes(b"stale")
    elif stage == "assets":
        (plan.output_root / "assets" / "characters" / "woman_front.png").write_bytes(
            b"stale"
        )
    elif stage == "selection":
        (plan.output_root / "shots" / "R001" / "candidate_01.mp4").write_bytes(b"stale")
    else:
        review = (
            plan.output_root / "shots" / "R001" / "reviews" / "candidate_01.review.json"
        )
        review.write_bytes(review.read_bytes() + b"\n")

    payload = cli.pet_replica_status(plan)
    expected = "review" if stage in {"selection", "review"} else stage
    assert payload["stages"][expected] == "stale"
    assert payload["pilot_ready"] is False


def test_status_distinguishes_incomplete_pilot_and_full_selection(tmp_path) -> None:
    cli = _require_cli()
    incomplete = _current_fixture(tmp_path / "incomplete", selections=8)
    partial = cli.pet_replica_status(incomplete)
    assert partial["counts"]["pilot_approved"] == 8
    assert partial["pilot_ready"] is False
    assert partial["full_ready"] is False

    pilot = _current_fixture(tmp_path / "pilot", selections=9)
    pilot_payload = cli.pet_replica_status(pilot)
    assert pilot_payload["counts"]["pilot_expected"] == 9
    assert pilot_payload["pilot_ready"] is True
    assert pilot_payload["full_ready"] is False

    full = _current_fixture(tmp_path / "full", selections=37)
    full_payload = cli.pet_replica_status(full)
    assert full_payload["pilot_ready"] is True
    assert full_payload["full_ready"] is True
    assert full_payload["release_state"] == "public_release_audio_blocked"


def test_status_accepts_postprocess_lipsync_generation_contract(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=1)
    from factory.pet_replica_generation import _prompt
    from factory.pet_replica_reference import load_reviewed_shot_annotations

    shot = plan.shots[0]
    annotation = load_reviewed_shot_annotations(
        plan,
        require_ocr_events=True,
    )[0]
    prompt, _negative = _prompt(
        shot,
        annotation,
        max(4, math.ceil(shot.duration_s)),
        voice_present=False,
        speaker_visible=True,
        postprocess_lipsync=True,
    )
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    provenance_path = plan.output_root / "shots/R001/candidate_01.provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["prompt_sha256"] = prompt_sha256
    provenance["signature"]["prompt_sha256"] = prompt_sha256
    provenance["signature"]["generate_audio"] = False
    _write_json(provenance_path, provenance)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["candidates"] == 1


def test_status_blocks_legacy_root_level_ambiguous_submission(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    archive = (
        plan.output_root
        / "rejected"
        / "generation_attempts"
        / "R001"
        / "R001_compact_probe.mp4.gateway.json"
    )
    _write_json(
        archive,
        {
            "schema_version": "motion-comic-factory.gateway-video-clip-state.v1",
            "status": "submitting",
            "shot_id": "single",
            "output_path": "/crawler/accounts/private/source.mp4",
            "api_key": "sk-do-not-print",
            "result_url": "https://provider.test/result?token=signed-secret",
            "preview": "data:image/png;base64,secret",
        },
    )
    payload = cli.pet_replica_status(plan)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["state"] == "blocked_ambiguous_submission"
    assert payload["ambiguous_submission_count"] == 1
    assert payload["first_missing_gate"] == "generate"
    assert "crawler" not in serialized.lower()
    assert "sk-do-not-print" not in serialized
    assert "signed-secret" not in serialized
    assert "data:image" not in serialized


def test_status_validates_current_pilot_and_final_hashes(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=37)
    _write_release_fixture(plan, "pilot")
    _write_release_fixture(plan, "final")
    current = cli.pet_replica_status(plan)
    assert current["current_pilot"] is True
    assert current["current_final"] is True
    assert current["stages"]["compose"] == "current"

    pilot_master = (
        plan.output_root
        / "final"
        / "releases"
        / "pilot-current"
        / "pilot_captioned_master.mp4"
    )
    pilot_master.write_bytes(b"changed-pilot-master")
    current_final_with_stale_pilot = cli.pet_replica_status(plan)
    assert current_final_with_stale_pilot["current_pilot"] is False
    assert current_final_with_stale_pilot["current_final"] is True
    assert current_final_with_stale_pilot["stages"]["compose"] == "current"

    master = (
        plan.output_root
        / "final"
        / "releases"
        / "final-current"
        / "replica_captioned_master.mp4"
    )
    master.write_bytes(b"changed-master")
    stale = cli.pet_replica_status(plan)
    assert stale["current_final"] is False
    assert stale["stages"]["compose"] == "stale"


def test_status_accepts_verified_single_encode_audio_fallback(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(
        plan,
        "pilot",
        audio_mode="pcm_to_aac_192k_once",
    )

    current = cli.pet_replica_status(plan)

    assert current["current_pilot"] is True
    assert current["stages"]["compose"] == "current"


def test_generate_defaults_to_dry_run_without_gateway_client_construction(
    tmp_path,
    monkeypatch,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    args = cli.build_parser().parse_args(
        ["--stage", "generate", "--output-dir", str(plan.output_root)]
    )
    monkeypatch.setattr(cli, "_plan_from_args", lambda _args: plan)
    monkeypatch.setattr(cli, "load_reviewed_shot_annotations", lambda _plan: ())
    monkeypatch.setattr(cli, "load_approved_replica_assets", lambda _plan: object())
    monkeypatch.setattr(cli, "validate_replica_audio_manifest", lambda *_args: object())
    monkeypatch.setattr(
        cli, "build_replica_shot_jobs", lambda *_args, **_kwargs: ("job",)
    )
    monkeypatch.setattr(
        cli,
        "_write_job_plan",
        lambda *_args, **_kwargs: plan.output_root / "shots" / "jobs.json",
    )
    import factory.pet_replica_generation as generation

    def forbidden_client(*_args, **_kwargs):
        pytest.fail("dry-run constructed a gateway client")

    monkeypatch.setattr(generation, "GatewayVideoClient", forbidden_client)
    monkeypatch.setattr(generation, "_validate_jobs", lambda *_args, **_kwargs: None)
    called = {}

    def observed_generate(_plan, jobs, config, enable_live, replace_stale):
        called.update(
            jobs=jobs,
            config=config,
            enable_live=enable_live,
            replace_stale=replace_stale,
        )
        return generation.generate_replica_candidates(
            _plan,
            jobs,
            config,
            enable_live,
            replace_stale,
        )

    monkeypatch.setattr(cli, "generate_replica_candidates", observed_generate)
    assert cli.pet_replica_command(args) == 0
    assert called["jobs"] == ("job",)
    assert called["enable_live"] is False
    assert called["replace_stale"] is False


def test_generate_threads_postprocess_lipsync_flag_to_selected_jobs(
    tmp_path,
    monkeypatch,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    args = cli.build_parser().parse_args(
        [
            "--stage",
            "generate",
            "--output-dir",
            str(plan.output_root),
            "--shot",
            "R003",
            "--postprocess-lipsync",
        ]
    )
    monkeypatch.setattr(cli, "_plan_from_args", lambda _args: plan)
    monkeypatch.setattr(
        cli, "pet_replica_status", lambda _plan: {"ambiguous_submission_count": 0}
    )
    monkeypatch.setattr(cli, "load_reviewed_shot_annotations", lambda _plan: ())
    monkeypatch.setattr(cli, "load_approved_replica_assets", lambda _plan: object())
    monkeypatch.setattr(cli, "validate_replica_audio_manifest", lambda *_args: object())
    called = {}

    def observed_build(*_args, **kwargs):
        called.update(kwargs)
        return ("job",)

    monkeypatch.setattr(cli, "build_replica_shot_jobs", observed_build)
    monkeypatch.setattr(
        cli,
        "_write_job_plan",
        lambda *_args, **_kwargs: plan.output_root / "shots" / "jobs.json",
    )
    monkeypatch.setattr(cli, "generate_replica_candidates", lambda *_args: ())

    assert cli.pet_replica_command(args) == 0
    assert called["shot_ids"] == ("R003",)
    assert called["postprocess_lipsync"] is True


def test_generate_builds_safe_gateway_video_config_from_factory_dotenv(
    tmp_path,
    monkeypatch,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GATEWAY_API_KEY=dotenv-gateway-key\n"
        "OPENAI_BASE_URL=https://dotenv-gateway.example.test/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "FACTORY_ENV_PATH", dotenv, raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
    args = cli.build_parser().parse_args(
        ["--stage", "generate", "--output-dir", str(plan.output_root)]
    )
    monkeypatch.setattr(cli, "_plan_from_args", lambda _args: plan)
    monkeypatch.setattr(
        cli, "pet_replica_status", lambda _plan: {"ambiguous_submission_count": 0}
    )
    monkeypatch.setattr(cli, "load_reviewed_shot_annotations", lambda _plan: ())
    monkeypatch.setattr(cli, "load_approved_replica_assets", lambda _plan: object())
    monkeypatch.setattr(cli, "validate_replica_audio_manifest", lambda *_args: object())
    monkeypatch.setattr(
        cli, "build_replica_shot_jobs", lambda *_args, **_kwargs: ("job",)
    )
    monkeypatch.setattr(
        cli,
        "_write_job_plan",
        lambda *_args, **_kwargs: plan.output_root / "shots" / "jobs.json",
    )
    called = {}

    def observed_generate(_plan, jobs, config, enable_live, replace_stale):
        called.update(
            jobs=jobs,
            config=config,
            enable_live=enable_live,
            replace_stale=replace_stale,
        )
        return ()

    monkeypatch.setattr(cli, "generate_replica_candidates", observed_generate)

    assert cli.pet_replica_command(args) == 0
    assert called["config"].api_key == "dotenv-gateway-key"
    assert called["config"].base_url == "https://dotenv-gateway.example.test/v1"
    assert called["config"].send_idempotency_key is True


def test_run_stops_at_manual_or_paid_gate_and_never_approves(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    args = cli.build_parser().parse_args(
        ["--stage", "run", "--output-dir", str(plan.output_root), "--enable-live"]
    )
    monkeypatch.setattr(cli, "_plan_from_args", lambda _args: plan)

    def forbidden(*_args, **_kwargs):
        pytest.fail("run crossed a paid/manual gate or auto-approved")

    monkeypatch.setattr(cli, "generate_replica_assets", forbidden)
    monkeypatch.setattr(cli, "generate_replica_candidates", forbidden)
    import factory.pet_replica_review as review

    monkeypatch.setattr(review, "approve_replica_candidate", forbidden)
    assert cli.pet_replica_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "run"
    assert payload["stopped_at"] == "generate"
    assert payload["auto_approved"] is False


def test_run_advances_safe_setup_then_stops_at_manual_reference_gate(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    cli = _require_cli()
    source = (tmp_path / "reference.mp4").resolve()
    source.write_bytes(b"source-video")
    output = (tmp_path / "output").resolve()
    args = cli.build_parser().parse_args(
        [
            "--stage",
            "run",
            "--source",
            str(source),
            "--output-dir",
            str(output),
        ]
    )
    called = {}

    def stop_after_reference(_args):
        called["reference"] = True
        return 1, {
            "success": False,
            "stage": "reference",
            "blocked_reasons": ["Manual source annotation review is required."],
            "next_stage": "reference",
        }

    monkeypatch.setattr(cli, "_reference_command", stop_after_reference)
    assert cli.pet_replica_command(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert called["reference"] is True
    assert payload["stopped_at"] == "reference"
    assert payload["auto_approved"] is False
    assert (output / "reference" / "source_binding.json").is_file()


def test_cli_status_stdout_is_sanitized_and_plan_requires_absolute_paths(
    tmp_path,
    capsys,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    archive = (
        plan.output_root
        / "rejected"
        / "generation_attempts"
        / "R001"
        / "legacy.mp4.gateway.json"
    )
    _write_json(
        archive,
        {
            "status": "submitting",
            "api_key": "sk-output-secret",
            "url": "https://provider.test/file?X-Signature=signed",
            "source": "/crawler/account/private.mp4",
            "image": "data:image/png;base64,private",
        },
    )
    code = cli.main(
        [
            "--stage",
            "status",
            "--source",
            str(plan.source_video),
            "--output-dir",
            str(plan.output_root),
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert code == 0
    assert payload["state"] == "blocked_ambiguous_submission"
    for forbidden in (
        "sk-output-secret",
        "signed",
        "data:image",
        "/crawler/",
        str(plan.source_video),
    ):
        assert forbidden not in stdout

    relative_source = Path("relative.mp4")
    code = cli.main(
        [
            "--stage",
            "plan",
            "--source",
            str(relative_source),
            "--output-dir",
            str(plan.output_root),
        ]
    )
    error = json.loads(capsys.readouterr().out)
    assert code == 1
    assert error["success"] is False
    assert error["error"] == "Pet replica stage failed."
    assert error["error_code"] == "invalid_request"


def test_status_rejects_duplicate_reference_frame_identities(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    manifest_path = plan.output_root / "reference" / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["frames"] = [dict(manifest["frames"][0]) for _ in manifest["frames"]]
    _write_canonical_json(manifest_path, manifest)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["reference"] == "stale"
    assert payload["first_missing_gate"] == "reference"


def test_status_rejects_duplicate_cross_shot_ocr_event_ids(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    annotations_path = plan.output_root / "reference" / "shot_annotations.json"
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    for shot in annotations["shots"]:
        shot["ocr_events"][0]["event_id"] = "DUPLICATE"
    _write_json(annotations_path, annotations)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["reference"] == "stale"


@pytest.mark.parametrize(
    "mutation",
    (
        "top_level_extra",
        "shot_missing_duration_s",
        "shot_extra",
        "shots_reordered",
        "duration_s",
        "characters",
        "speaker",
        "location",
        "framing",
        "action",
        "subtitle",
        "source_audio",
    ),
)
def test_status_rejects_exact_task_1_timeline_contract_drift(
    tmp_path,
    mutation,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    timeline_path = plan.output_root / "reference" / "shot_timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    if mutation == "top_level_extra":
        timeline["attacker"] = True
    elif mutation == "shot_missing_duration_s":
        timeline["shots"][0].pop("duration_s")
    elif mutation == "shot_extra":
        timeline["shots"][0]["attacker"] = True
    elif mutation == "shots_reordered":
        timeline["shots"][0], timeline["shots"][1] = (
            timeline["shots"][1],
            timeline["shots"][0],
        )
    elif mutation == "duration_s":
        timeline["shots"][0][mutation] += 1.0
    elif mutation == "characters":
        timeline["shots"][0][mutation] = ["source_woman"]
    elif mutation == "source_audio":
        timeline["shots"][0][mutation] = False
    else:
        timeline["shots"][0][mutation] = "drifted"
    _write_json(timeline_path, timeline)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["plan"] == "stale"
    assert payload["first_missing_gate"] == "plan"


def test_status_rejects_exact_task_1_story_contract_drift(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    contract_path = plan.output_root / "story_contract.md"
    contract_path.write_bytes(contract_path.read_bytes() + b"drift\n")

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["plan"] == "stale"
    assert payload["first_missing_gate"] == "plan"


@pytest.mark.parametrize(
    "substitution",
    (
        "source_audio_integer",
        "index_boolean",
        "index_float",
        "start_boolean",
        "start_integer",
        "fps_float",
        "width_float",
    ),
)
def test_status_rejects_task_1_json_type_aliases(tmp_path, substitution) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    timeline_path = plan.output_root / "reference" / "shot_timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    if substitution == "source_audio_integer":
        timeline["shots"][0]["source_audio"] = 1
    elif substitution == "index_boolean":
        timeline["shots"][0]["index"] = True
    elif substitution == "index_float":
        timeline["shots"][0]["index"] = 1.0
    elif substitution == "start_boolean":
        timeline["shots"][0]["start_s"] = False
    elif substitution == "start_integer":
        timeline["shots"][0]["start_s"] = 0
    elif substitution == "fps_float":
        timeline["media_contract"]["fps"] = 30.0
    else:
        timeline["media_contract"]["width"] = 720.0
    _write_json(timeline_path, timeline)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["plan"] == "stale"
    assert payload["first_missing_gate"] == "plan"


@pytest.mark.parametrize(
    "mutation",
    ("safe_region", "detection_mapping", "classification", "frame_window"),
)
def test_status_rejects_invalid_annotation_v2_contract(tmp_path, mutation) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    annotations_path = plan.output_root / "reference" / "shot_annotations.json"
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    event = annotations["shots"][0]["ocr_events"][0]
    if mutation == "safe_region":
        annotations["caption_safe_region"]["y"] = 0
    elif mutation == "detection_mapping":
        event["detection_id"] = "UNBOUND-DETECTION"
    elif mutation == "classification":
        event["classification"] = "attacker_renderable_text"
    else:
        event["end_frame"] = round(plan.shots[0].end_s * plan.fps) + 1
        event["end_s"] = event["end_frame"] / plan.fps
    _write_json(annotations_path, annotations)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["reference"] == "stale"


def test_status_rejects_unlocked_audio_metadata(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    manifest_path = plan.output_root / "audio" / "audio_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["shots"]["R001"].update(
        {
            "duration_s": -999,
            "sample_rate": 1,
            "channels": 99,
            "codec": "attacker-codec",
        }
    )
    _write_json(manifest_path, manifest)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["audio"] == "stale"
    assert payload["audio_technical_ready"] is False


def test_status_rejects_rebound_duplicate_assets_and_jobs(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    review_path = plan.output_root / "assets" / "asset_review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    woman = next(
        item for item in manifest["assets"] if item["asset_id"] == "woman_front"
    )
    scene = next(
        item for item in manifest["assets"] if item["asset_id"] == "scene_sofa"
    )
    woman["path"] = scene["path"]
    woman["sha256"] = scene["sha256"]
    manifest["assets"].append(dict(woman))
    manifest["jobs"].append(dict(manifest["jobs"][0]))
    _write_json(manifest_path, manifest)

    review_woman = next(
        item for item in review["assets"] if item["asset_id"] == "woman_front"
    )
    review_woman["path"] = scene["path"]
    review_woman["sha256"] = scene["sha256"]
    review["assets"].append(dict(review_woman))
    review["asset_manifest_sha256"] = _sha256(manifest_path)
    _write_json(review_path, review)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["assets"] == 13
    assert payload["stages"]["assets"] == "stale"


def test_status_accepts_task_4_asset_sources_canonical_digest_and_review_metadata(
    tmp_path,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    root = plan.output_root
    evidence_path = root / "reference" / "evidence_manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    _write_json(evidence_path, evidence)
    manifest_path = root / "assets" / "asset_manifest.json"
    review_path = root / "assets" / "asset_review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    source_ids = {
        "woman_master": "rejected/architecture_probes/woman_reference_probe.png",
        "scene_master": "rejected/assets_round_3/scenes/scene_sofa.png",
    }
    for item in manifest["assets"][2:4]:
        source_id = source_ids[item["asset_id"]]
        source = root / source_id
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes((root / item["path"]).read_bytes())
        item["source_id"] = source_id
    review.update(
        {
            "contact_sheet": "assets/asset_contact_sheet_round5.jpg",
            "review_method": "manual full-resolution inspection",
            "review_notes": ["All locked gates reviewed."],
            "reviewed_at": "2026-07-30",
        }
    )
    _reseal_asset_contract(plan, manifest, review)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["assets"] == 12
    assert payload["stages"]["assets"] == "current"


@pytest.mark.parametrize(
    ("asset_id", "filename"),
    (
        ("naitang_reference", "奶糖_reference.png"),
        ("doubao_reference", "豆包_reference.png"),
    ),
)
def test_status_rejects_self_consistent_approved_cat_identity_replacement(
    tmp_path,
    asset_id,
    filename,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    root = plan.output_root
    manifest_path = root / "assets" / "asset_manifest.json"
    review_path = root / "assets" / "asset_review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    installed = root / "assets" / "characters" / filename
    installed.write_bytes(f"forged-{asset_id}".encode())
    digest = _sha256(installed)
    item = next(
        record for record in manifest["assets"] if record["asset_id"] == asset_id
    )
    item["sha256"] = digest
    item["source_sha256"] = digest
    _reseal_asset_contract(plan, manifest, review)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["assets"] == 12
    assert payload["stages"]["assets"] == "stale"


@pytest.mark.parametrize("source_kind", ("installed_master", "trusted_evidence"))
def test_status_rejects_task_4_forbidden_project_master_sources(
    tmp_path,
    source_kind,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    root = plan.output_root
    manifest_path = root / "assets" / "asset_manifest.json"
    review_path = root / "assets" / "asset_review.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    item = next(
        record for record in manifest["assets"] if record["asset_id"] == "woman_master"
    )
    installed = root / item["path"]
    if source_kind == "installed_master":
        item["source_id"] = item["path"]
    else:
        source_id = "reference/shots/R001/start.jpg"
        installed.write_bytes((root / source_id).read_bytes())
        digest = _sha256(installed)
        item.update(
            {
                "source_id": source_id,
                "source_sha256": digest,
                "sha256": digest,
            }
        )
    _reseal_asset_contract(plan, manifest, review)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["assets"] == "stale"


def test_status_rejects_forged_selection_review_bindings(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=1)
    root = plan.output_root
    shot = plan.shots[0]
    selection_path = root / "shots" / shot.shot_id / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    review_path = root / selection["quality_review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))

    provenance_payload = json.loads(
        (root / review["bindings"]["provenance"]["path"]).read_text(encoding="utf-8")
    )
    forged_provenance = _write_json(
        root / "reference" / "reference_manifest.json",
        provenance_payload,
    )
    attacker_audio = _write_bound_file(
        root,
        "attacker/audio.wav",
        b"unchecked-audio",
    )
    attacker_evidence = _write_bound_file(
        root,
        "attacker/evidence.jpg",
        b"unchecked-evidence",
    )
    bindings = review["bindings"]
    bindings["provenance"] = {
        "path": str(forged_provenance.relative_to(root)),
        "sha256": _sha256(forged_provenance),
    }
    bindings["source_evidence"] = {
        "manifest_sha256": _sha256(root / "reference" / "evidence_manifest.json")
    }
    bindings["drive_audio"] = attacker_audio
    bindings["evidence"] = {"attacker": attacker_evidence}
    review["evidence"] = {"attacker": attacker_evidence["path"]}
    _write_json(review_path, review)
    selection.update(
        {
            "quality_review_sha256": _sha256(review_path),
            "quality_bindings_sha256": _json_sha256(bindings),
            "quality_provenance_path": bindings["provenance"]["path"],
            "quality_provenance_sha256": bindings["provenance"]["sha256"],
            "quality_source_evidence_sha256": bindings["source_evidence"][
                "manifest_sha256"
            ],
            "quality_drive_audio": attacker_audio,
            "quality_evidence": bindings["evidence"],
        }
    )
    _write_json(selection_path, selection)

    valid, _candidate_hash = cli._selection_is_current(
        root,
        shot,
        selection,
        _sha256(plan.source_video),
        {
            "evidence_manifest_sha256": _sha256(
                root / "reference" / "evidence_manifest.json"
            )
        },
    )

    assert valid is False


def test_status_rejects_self_consistent_forged_candidate_provenance(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=1)
    root = plan.output_root
    shot = plan.shots[0]
    selection_path = root / "shots" / shot.shot_id / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    review_path = root / selection["quality_review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    provenance_path = root / review["bindings"]["provenance"]["path"]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["model"] = "attacker-video-model"
    provenance["signature"]["model"] = "attacker-video-model"
    _write_json(provenance_path, provenance)

    bindings = review["bindings"]
    bindings["provenance"]["sha256"] = _sha256(provenance_path)
    _write_json(review_path, review)
    selection.update(
        {
            "quality_review_sha256": _sha256(review_path),
            "quality_bindings_sha256": _json_sha256(bindings),
            "quality_provenance_sha256": bindings["provenance"]["sha256"],
        }
    )
    _write_json(selection_path, selection)

    valid, _candidate_hash = cli._selection_is_current(
        root,
        shot,
        selection,
        _sha256(plan.source_video),
        {
            "evidence_manifest_sha256": _sha256(
                root / "reference" / "evidence_manifest.json"
            ),
            "annotations": (),
        },
    )

    assert valid is False


def test_candidate_status_accepts_bound_lipsync_and_rejects_raw_archive_tamper(
    monkeypatch,
    tmp_path,
) -> None:
    cli = _require_cli()
    from factory.pet_replica_generation import ReplicaCandidate
    from factory.pet_replica_lipsync import promote_replica_lipsync_candidate
    from factory.pet_replica_reference import load_reviewed_shot_annotations

    plan = _current_fixture(tmp_path, selections=1)
    root = plan.output_root
    shot = plan.shots[0]
    video = root / "shots/R001/candidate_01.mp4"
    provenance_path = video.with_suffix(".provenance.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    candidate = ReplicaCandidate(
        "R001",
        1,
        video,
        provenance_path,
        video.with_suffix(".gateway.json"),
        shot.duration_s,
        provenance["provider_duration_s"],
        _sha256(video),
    )
    lipsynced = tmp_path / "lipsynced.mp4"
    lipsynced.write_bytes(b"bound lipsync output")
    checkpoint = tmp_path / "Wav2Lip/checkpoints/wav2lip_gan.pth"
    detector = tmp_path / "Wav2Lip/face_detection/detection/sfd/s3fd.pth"
    checkpoint.parent.mkdir(parents=True)
    detector.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"gan")
    detector.write_bytes(b"sfd")
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )
    promoted = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="d" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    annotation = load_reviewed_shot_annotations(
        plan, require_ocr_events=True
    )[0]
    drive_audio = {
        "path": "audio/drive/R001.wav",
        "sha256": _sha256(root / "audio/drive/R001.wav"),
    }

    assert cli._selection_provenance_is_current(
        root,
        shot,
        1,
        promoted.output_sha256,
        "shots/R001/candidate_01.mp4",
        _sha256(plan.source_video),
        annotation,
        drive_audio,
        json.loads(provenance_path.read_text(encoding="utf-8")),
    )

    stale_alternate = root / "shots/R001/candidate_02.mp4"
    stale_alternate.write_bytes(b"rejected alternate")
    stale_alternate.with_suffix(".provenance.json").write_text(
        '{"schema_version": "stale"}', encoding="utf-8"
    )
    payload = cli.pet_replica_status(plan)
    assert payload["counts"]["candidates"] == 1
    assert payload["stages"]["generate"] == "partial"

    postprocess = json.loads(provenance_path.read_text(encoding="utf-8"))[
        "postprocess"
    ]
    (root / postprocess["source_candidate_path"]).write_bytes(b"tampered")
    assert not cli._selection_provenance_is_current(
        root,
        shot,
        1,
        promoted.output_sha256,
        "shots/R001/candidate_01.mp4",
        _sha256(plan.source_video),
        annotation,
        drive_audio,
        json.loads(provenance_path.read_text(encoding="utf-8")),
    )


def test_status_rejects_thirty_seven_stripped_candidate_provenance_records(
    tmp_path,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    root = plan.output_root
    source_sha256 = _sha256(plan.source_video)
    for shot in plan.shots:
        candidate = root / "shots" / shot.shot_id / "candidate_01.mp4"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"stripped-{shot.shot_id}".encode())
        _write_json(
            candidate.with_suffix(".provenance.json"),
            {
                "schema_version": "motion-comic-factory.pet-replica-generation.v1",
                "shot_id": shot.shot_id,
                "candidate_number": 1,
                "source_sha256": source_sha256,
                "source_window": {
                    "start_s": shot.start_s,
                    "end_s": shot.end_s,
                },
                "output_path": str(candidate.relative_to(root)),
                "output_sha256": _sha256(candidate),
            },
        )

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["candidates"] == 0
    assert payload["stages"]["generate"] == "stale"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_gateway_report",
        "failed_gateway_report",
        "signature_extra",
        "anchor_drift",
    ),
)
def test_status_rejects_incomplete_task_5_candidate_provenance(
    tmp_path,
    mutation,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=1)
    root = plan.output_root
    candidate = root / "shots" / "R001" / "candidate_01.mp4"
    provenance_path = candidate.with_suffix(".provenance.json")
    gateway_path = candidate.with_suffix(".gateway.json")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if mutation == "missing_gateway_report":
        gateway_path.unlink()
    elif mutation == "failed_gateway_report":
        gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
        gateway.update(
            {
                "success": False,
                "completed_count": 0,
                "failed_count": 1,
                "error": "failed",
                "errors": [{"error": "failed"}],
                "results": [{"status": "failed"}],
            }
        )
        _write_json(gateway_path, gateway)
    elif mutation == "signature_extra":
        provenance["signature"]["attacker"] = True
        _write_json(provenance_path, provenance)
    else:
        provenance["anchor_sha256"][0] = "f" * 64
        provenance["signature"]["anchor_sha256"][0] = "f" * 64
        _write_json(provenance_path, provenance)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["candidates"] == 0
    assert payload["stages"]["generate"] == "stale"


def test_status_rejects_self_consistent_candidate_endpoint_replacement(
    tmp_path,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=1)
    provenance_path = (
        plan.output_root / "shots" / "R001" / "candidate_01.provenance.json"
    )
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["endpoint_fingerprint_sha256"] == TEST_GATEWAY_ENDPOINT_SHA256
    provenance["endpoint_fingerprint_sha256"] = "f" * 64
    provenance["signature"]["endpoint_fingerprint_sha256"] = "f" * 64
    _write_json(provenance_path, provenance)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["candidates"] == 0
    assert payload["stages"]["generate"] == "stale"


@pytest.mark.parametrize(
    "value",
    (
        "audio/./source_audio.aac",
        "audio//source_audio.aac",
        "audio/drive/../source_audio.aac",
        "audio\\source_audio.aac",
        "/absolute/source_audio.aac",
    ),
)
def test_relative_file_rejects_noncanonical_persisted_path_spellings(
    tmp_path,
    value,
) -> None:
    cli = _require_cli()
    root = tmp_path.resolve()
    _write_bound_file(root, "audio/source_audio.aac", b"audio")

    assert cli._relative_file(root, value) is None


def test_fixed_metadata_reader_rejects_symlinked_parent_before_open(tmp_path) -> None:
    cli = _require_cli()
    root = (tmp_path / "output").resolve()
    outside = (tmp_path / "outside" / "reference").resolve()
    leaf = _write_json(outside / "reference_manifest.json", {"forged": True})
    root.mkdir()
    (root / "reference").symlink_to(outside, target_is_directory=True)
    linked_leaf = root / "reference" / leaf.name

    assert linked_leaf.is_symlink() is False
    assert cli._regular_file(linked_leaf) is False
    assert cli._read_json_object(linked_leaf) == {}


def test_status_rejects_stripped_composition_v2_release(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    release = plan.output_root / "final" / "releases" / "pilot-current"
    manifest_path = release / "pilot_composition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retained_manifest_keys = {
        "schema_version",
        "project_id",
        "mode",
        "output_root",
        "manifest_path",
        "presentation_source_sha256",
        "source_audio_sha256",
        "reviewed_annotations_sha256",
        "shots",
        "master_path",
    }
    stripped_manifest = {
        key: value for key, value in manifest.items() if key in retained_manifest_keys
    }
    stripped_manifest["shots"] = [
        {
            key: value
            for key, value in item.items()
            if key in {"shot_id", "source_path", "source_sha256", "normalized_path"}
        }
        for item in manifest["shots"]
    ]
    _write_json(manifest_path, stripped_manifest)

    qc_path = release / "pilot_qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    arbitrary_path = "pilot_captioned_master.mp4"
    stripped_qc = {
        "schema_version": COMPOSITION_SCHEMA_VERSION,
        "valid": True,
        "mode": "pilot",
        "release_id": "pilot-current",
        "master_path": stripped_manifest["master_path"],
        "master_sha256": _sha256(Path(stripped_manifest["master_path"])),
        "composition_manifest_sha256": _sha256(manifest_path),
        "artifact_sha256": {
            arbitrary_path: _sha256(release / arbitrary_path),
        },
        "input_bundle": {
            key: qc["input_bundle"][key]
            for key in (
                "source_audio_sha256",
                "presentation_source_sha256",
                "reviewed_annotations_sha256",
                "selection",
            )
        },
    }
    _write_json(qc_path, stripped_qc)

    payload = cli.pet_replica_status(plan)

    assert payload["current_pilot"] is False
    assert payload["stages"]["compose"] == "stale"


@pytest.mark.parametrize(
    "mutation",
    (
        "black_detected",
        "invalid_frame_boundaries",
        "source_pixel_copy",
        "missing_clean_freeze",
        "missing_caption_freeze",
        "missing_clean_picture",
        "missing_caption_effects",
        "missing_comparison_key",
        "missing_audio_key",
    ),
)
def test_status_rejects_failed_or_incomplete_nested_task_7_qc(
    tmp_path,
    mutation,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    qc_path = (
        plan.output_root / "final" / "releases" / "pilot-current" / "pilot_qc.json"
    )
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    if mutation == "black_detected":
        qc["masters"]["captioned"]["blackdetect"]["detected"] = True
        qc["blackdetect"] = qc["masters"]["captioned"]["blackdetect"]
    elif mutation == "invalid_frame_boundaries":
        qc["masters"]["captioned"]["frame_boundaries"]["frame_count"] += 1
        qc["master_frame_boundaries"] = qc["masters"]["captioned"]["frame_boundaries"]
    elif mutation == "source_pixel_copy":
        sample = qc["source_pixel_evidence"]["samples"][0]
        sample["picture_sha256"] = sample["source_sha256"]
        sample["perceptual_delta"] = 0.0
    elif mutation == "missing_clean_freeze":
        qc["masters"]["clean"].pop("freezedetect")
    elif mutation == "missing_caption_freeze":
        qc["masters"]["captioned"].pop("freezedetect")
    elif mutation == "missing_clean_picture":
        qc["masters"]["clean"].pop("picture_proof")
    elif mutation == "missing_caption_effects":
        qc["masters"]["captioned"].pop("caption_effects")
    elif mutation == "missing_comparison_key":
        qc["comparison"].pop("audio_pcm_correlation")
    else:
        qc["masters"]["captioned"]["audio"].pop("pcm_correlation")
        qc["audio"].pop("pcm_correlation")
    _write_json(qc_path, qc)

    payload = cli.pet_replica_status(plan)

    assert payload["current_pilot"] is False
    assert payload["stages"]["compose"] == "stale"


def test_status_rejects_cut_offset_contradicting_measured_frame_cursor(
    tmp_path,
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    qc_path = (
        plan.output_root / "final" / "releases" / "pilot-current" / "pilot_qc.json"
    )
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    first_cut = qc["frame_sequence"]["cuts"][0]
    assert (
        first_cut["actual_frame_index"] == qc["frame_sequence"]["clip_frame_counts"][0]
    )
    first_cut["offset_frames"] = 2
    _write_json(qc_path, qc)

    payload = cli.pet_replica_status(plan)

    assert payload["current_pilot"] is False
    assert payload["stages"]["compose"] == "stale"


def test_status_fails_closed_on_release_with_invalid_reference_tail(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    manifest_path = plan.output_root / "reference" / "reference_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["last_video_frame_s"] = "not-a-number"
    _write_json(manifest_path, manifest)

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["reference"] == "stale"
    assert payload["current_pilot"] is False
    assert payload["stages"]["compose"] == "stale"


def test_status_accepts_task_7_one_frame_qc_duration_tolerance(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    _write_release_fixture(plan, "pilot")
    qc_path = (
        plan.output_root / "final" / "releases" / "pilot-current" / "pilot_qc.json"
    )
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["duration_s"] += 0.5 / plan.fps
    _write_json(qc_path, qc)

    payload = cli.pet_replica_status(plan)

    assert payload["current_pilot"] is True


@pytest.mark.parametrize(
    "message",
    (
        "payload=data:image/png;base64,SENTINEL_DATA",
        "api_key=SENTINEL_API_KEY",
        "Authorization: Bearer SENTINEL_BEARER",
        "https://provider.test/result?X-Signature=SENTINEL_SIGNATURE",
        "/account-private",
    ),
)
def test_cli_exception_stdout_uses_stable_public_error(
    tmp_path,
    monkeypatch,
    capsys,
    message,
) -> None:
    cli = _require_cli()
    args = cli.build_parser().parse_args(
        ["--stage", "status", "--output-dir", str(tmp_path.resolve())]
    )

    def fail(_args):
        raise RuntimeError(message)

    monkeypatch.setattr(cli, "_status_command", fail)

    assert cli.pet_replica_command(args) == 1
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert "SENTINEL" not in stdout
    assert payload["error"] == "Pet replica stage failed."
    assert payload["error_code"] == "stage_failed"


def test_run_pilot_only_uses_pilot_gate_and_completes_after_r009(
    tmp_path, monkeypatch, capsys
) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=9)
    args = cli.build_parser().parse_args(
        [
            "--stage",
            "run",
            "--output-dir",
            str(plan.output_root),
            "--pilot-only",
        ]
    )
    monkeypatch.setattr(cli, "_plan_from_args", lambda _args: plan)
    called = []

    def compose(_args):
        called.append("pilot")
        _write_release_fixture(plan, "pilot")
        return 0, {"success": True, "stage": "compose"}

    monkeypatch.setattr(cli, "_compose_command", compose)

    assert cli.pet_replica_command(args) == 0
    payload = json.loads(capsys.readouterr().out)
    manifest = json.loads(
        (
            plan.output_root
            / "final"
            / "releases"
            / "pilot-current"
            / "pilot_composition_manifest.json"
        ).read_text(encoding="utf-8")
    )
    r009 = next(item for item in manifest["shots"] if item["shot_id"] == "R009")
    assert called == ["pilot"]
    assert payload["success"] is True
    assert payload["stopped_at"] == "complete"
    expected_duration = (
        round(plan.shots[8].end_s * plan.fps)
        - round(plan.shots[8].start_s * plan.fps)
    ) / plan.fps
    assert r009["editorial_duration_s"] == pytest.approx(expected_duration)


def test_later_stage_explicit_source_cannot_replace_missing_binding(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    (plan.output_root / "reference" / "source_binding.json").unlink()
    args = cli.build_parser().parse_args(
        [
            "--stage",
            "audio",
            "--source",
            str(plan.source_video),
            "--output-dir",
            str(plan.output_root),
        ]
    )

    with pytest.raises(cli.PetReplicaCLIError, match="persisted source binding"):
        cli._plan_from_args(args)


def test_status_requires_current_persisted_source_binding(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    (plan.output_root / "reference" / "source_binding.json").unlink()

    payload = cli.pet_replica_status(plan)

    assert payload["source_current"] is False
    assert payload["first_missing_gate"] == "plan"


def test_terminal_submission_adjudications_are_not_ambiguous(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    archive = plan.output_root / "rejected" / "generation_attempts" / "R001"
    for index, outcome in enumerate(("succeeded", "success", "completed", "failed")):
        _write_json(
            archive / f"terminal-{index}.gateway.json",
            {
                "status": "submitting",
                "adjudication": {"outcome": outcome},
            },
        )
    _write_json(
        archive / "top-level.gateway.json",
        {
            "status": "submitting",
            "adjudication": {},
            "adjudicated_outcome": "completed",
        },
    )

    payload = cli.pet_replica_status(plan)

    assert payload["ambiguous_submission_count"] == 0
    assert payload["state"] != "blocked_ambiguous_submission"


def test_generation_coverage_counts_distinct_current_shot_ids(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path, selections=13)
    root = plan.output_root
    for shot in plan.shots[:12]:
        base = root / "shots" / shot.shot_id / "candidate_01.mp4"
        base_provenance = json.loads(
            base.with_suffix(".provenance.json").read_text(encoding="utf-8")
        )
        base_gateway = json.loads(
            base.with_suffix(".gateway.json").read_text(encoding="utf-8")
        )
        for number in (2, 3):
            candidate = root / "shots" / shot.shot_id / f"candidate_{number:02d}.mp4"
            candidate.write_bytes(f"{shot.shot_id}-{number}".encode())
            gateway = candidate.with_suffix(".gateway.json")
            _write_json(gateway, base_gateway)
            provenance = json.loads(json.dumps(base_provenance))
            provenance.update(
                {
                    "candidate_number": number,
                    "output_path": candidate.relative_to(root).as_posix(),
                    "output_sha256": _sha256(candidate),
                    "gateway_report_path": gateway.relative_to(root).as_posix(),
                    "gateway_result": base_gateway,
                }
            )
            provenance["signature"]["candidate_number"] = number
            _write_json(candidate.with_suffix(".provenance.json"), provenance)

    payload = cli.pet_replica_status(plan)

    assert payload["counts"]["candidates"] == 13
    assert payload["stages"]["generate"] == "partial"
    assert payload["first_missing_gate"] == "generate"


def test_stale_local_audio_remains_public_release_blocked(tmp_path) -> None:
    cli = _require_cli()
    plan = _current_fixture(tmp_path)
    (plan.output_root / "audio" / "drive" / "R001.wav").write_bytes(b"stale")

    payload = cli.pet_replica_status(plan)

    assert payload["stages"]["audio"] == "stale"
    assert payload["audio_technical_ready"] is False
    assert payload["release_state"] == "public_release_audio_blocked"
    assert payload["public_release_audio_blocked"] is True
