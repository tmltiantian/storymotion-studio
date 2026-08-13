from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_reference import (
    PetReplicaReferenceError,
    ReplicaShotAnnotation,
    extract_reference_evidence,
    load_reviewed_shot_annotations,
    probe_reference_media,
    write_shot_annotation_template,
)


def _probe_payload() -> dict:
    return {
        "format": {"duration": "77.229569"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 720,
                "height": 1280,
                "avg_frame_rate": "30/1",
                "r_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            },
        ],
    }


def replica_plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    return build_pet_replica_plan(source, tmp_path / "output")


def reviewed_annotation_payload(plan) -> dict:
    payload = json.loads(write_shot_annotation_template(plan).read_text(encoding="utf-8"))
    for plan_shot, annotation in zip(plan.shots, payload["shots"]):
        annotation.update(
            {
                "characters": ["source_woman"],
                "speaker": "source_woman",
                "scene_anchor_id": "scene_sofa",
                "location": "living_room_sofa",
                "framing": "tight_face_closeup",
                "action": "woman looks up and complains to camera",
                "manual_review_required": False,
                "ocr_review": write_ocr_detection_evidence(
                    plan,
                    plan_shot,
                    [],
                ),
            }
        )
    return payload


def reviewed_event(plan, **overrides) -> dict:
    shot = plan.shots[0]
    event = {
        "event_id": f"{shot.shot_id}-OCR-001",
        "detection_id": "D001",
        "classification": "dialogue_subtitle",
        "reviewed_text": "你在看什么",
        "start_s": shot.start_s + 0.1,
        "end_s": min(shot.end_s, shot.start_s + 0.5),
        "placement": {
            "x": 48,
            "y": 940,
            "width": 624,
            "height": 180,
            "alignment": "bottom_center",
        },
        "manual_reviewed": True,
    }
    event.update(overrides)
    event.setdefault("start_frame", round(event["start_s"] * plan.fps))
    event.setdefault("end_frame", round(event["end_s"] * plan.fps))
    return event


def write_reviewed_annotations(plan, payload: dict) -> Path:
    reviewed = plan.output_root / "reference" / "shot_annotations.json"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return reviewed


def write_ocr_detection_evidence(
    plan,
    shot,
    items: list[dict],
    *,
    payload_overrides: dict | None = None,
) -> dict:
    payload = {
        "schema_version": "motion-comic-factory.pet-replica-ocr-evidence.v1",
        "source_sha256": hashlib.sha256(plan.source_video.read_bytes()).hexdigest(),
        "shot_id": shot.shot_id,
        "source_window": {
            "start_frame": round(shot.start_s * plan.fps),
            "end_frame": round(shot.end_s * plan.fps),
            "start_s": round(shot.start_s * plan.fps) / plan.fps,
            "end_s": round(shot.end_s * plan.fps) / plan.fps,
        },
        "detected_items": items,
        "reviewed_zero": not items,
    }
    payload.update(payload_overrides or {})
    contents = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(contents).hexdigest()
    path = (
        plan.output_root
        / "reference"
        / "ocr_evidence"
        / shot.shot_id
        / f"{digest}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return {
        "evidence_path": str(path.relative_to(plan.output_root)),
        "evidence_sha256": digest,
        "detected_item_count": len(items),
        "review_complete": True,
    }


def detected_item(
    detection_id: str,
    text: str,
    *,
    start_s: float = 0.1,
    end_s: float = 0.5,
) -> dict:
    return {
        "detection_id": detection_id,
        "detected_text": text,
        "start_frame": round(start_s * 30),
        "end_frame": round(end_s * 30),
        "start_s": start_s,
        "end_s": end_s,
        "source_bbox": {
            "x": 48,
            "y": 940,
            "width": 624,
            "height": 180,
        },
    }


def bind_detected_items(
    plan,
    payload: dict,
    items: list[dict],
    *,
    shot_index: int = 0,
) -> None:
    payload["shots"][shot_index]["ocr_review"] = write_ocr_detection_evidence(
        plan,
        plan.shots[shot_index],
        items,
    )


def nonzero_review_payload(plan) -> dict:
    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(plan),
        reviewed_event(
            plan,
            event_id="R001-OCR-002",
            detection_id="D002",
            classification="platform_watermark",
            reviewed_text="作者：source_creator",
            start_s=0.1,
            end_s=0.6,
        ),
    ]
    bind_detected_items(
        plan,
        payload,
        [
            detected_item("D001", "你在看什么"),
            detected_item(
                "D002",
                "作者：source_creator",
                start_s=0.1,
                end_s=0.6,
            ),
        ],
    )
    return payload


@pytest.fixture
def fake_ffprobe():
    def run(command, **_kwargs):
        assert Path(command[0]).name == "ffprobe"
        if "frame=best_effort_timestamp_time" in command:
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "frames": [
                            {"best_effort_timestamp_time": "0.0"},
                            {"best_effort_timestamp_time": "77.133333"},
                        ]
                    }
                ),
                stderr="",
            )
        return SimpleNamespace(stdout=json.dumps(_probe_payload()), stderr="")

    return run


def test_probe_reference_requires_exact_media_contract(tmp_path, fake_ffprobe):
    plan = replica_plan(tmp_path)

    probe = probe_reference_media(plan, runner=fake_ffprobe)

    assert probe.duration_s == pytest.approx(77.229569)
    assert (probe.width, probe.height, probe.fps) == (720, 1280, 30)
    assert probe.audio_codec == "aac"
    assert probe.audio_sample_rate == 44100
    assert probe.audio_channels == 2
    assert probe.last_video_frame_s == pytest.approx(77.133333)
    manifest = json.loads(
        (plan.output_root / "reference" / "reference_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest == {
        "schema_version": "motion-comic-factory.pet-replica-reference.v1",
        "source_sha256": hashlib.sha256(plan.source_video.read_bytes()).hexdigest(),
        "duration_s": 77.229569,
        "width": 720,
        "height": 1280,
        "fps": 30,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_sample_rate": 44100,
        "audio_channels": 2,
        "last_video_frame_s": 77.133333,
    }


def test_probe_rejects_mismatched_video_codec(tmp_path, fake_ffprobe):
    plan = replica_plan(tmp_path)

    def mismatched_probe(command, **kwargs):
        result = fake_ffprobe(command, **kwargs)
        payload = json.loads(result.stdout)
        if "streams" in payload:
            payload["streams"][0]["codec_name"] = "hevc"
        return SimpleNamespace(stdout=json.dumps(payload), stderr="")

    with pytest.raises(PetReplicaReferenceError, match="video codec"):
        probe_reference_media(plan, runner=mismatched_probe)


def test_evidence_paths_cannot_escape_output_root(tmp_path):
    plan = replica_plan(tmp_path)

    with pytest.raises(PetReplicaReferenceError, match="output root"):
        extract_reference_evidence(
            plan,
            destination=tmp_path.parent / "escape",
            runner=lambda *args, **kwargs: None,
        )


def test_evidence_writes_source_frames_contact_sheets_and_bound_records(tmp_path):
    plan = replica_plan(tmp_path)
    source_before = plan.source_video.read_bytes()

    def fake_ffmpeg(command, **_kwargs):
        if Path(command[0]).name == "ffprobe":
            if "frame=best_effort_timestamp_time" in command:
                return SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "frames": [
                                {"best_effort_timestamp_time": "0.0"},
                                {"best_effort_timestamp_time": "77.133333"},
                            ]
                        }
                    ),
                    stderr="",
                )
            return SimpleNamespace(stdout=json.dumps(_probe_payload()), stderr="")
        destination = Path(command[-1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (72, 128), "red").save(destination, format="JPEG")
        return SimpleNamespace(stdout="", stderr="")

    manifest_path = extract_reference_evidence(plan, runner=fake_ffmpeg)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first = plan.shots[0]
    assert manifest_path == plan.output_root / "reference" / "evidence_manifest.json"
    assert all(
        (plan.output_root / "reference" / "shots" / first.shot_id / f"{name}.jpg").is_file()
        for name in ("start", "middle", "end")
    )
    assert (plan.output_root / "reference" / "contact_sheets" / "pilot_4x3.jpg").is_file()
    assert (plan.output_root / "reference" / "contact_sheets" / "full_01_5x8.jpg").is_file()
    assert len(manifest["frames"]) >= len(plan.shots) * 3
    assert all(record["source_sha256"] == manifest["source_sha256"] for record in manifest["frames"])
    assert all(record["shot_id"].startswith("R") for record in manifest["frames"])
    assert all("reference.mp4" not in record["command"] for record in manifest["frames"])
    assert plan.source_video.read_bytes() == source_before


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for reference evidence integration",
)
def test_evidence_clamps_to_last_video_frame_before_audio_container_tail(tmp_path):
    source = tmp_path / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=720x1280:r=30:d=77.166667",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo:d=77.229569",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            source.as_posix(),
        ],
        check=True,
    )
    plan = build_pet_replica_plan(source, tmp_path / "output")
    probe = probe_reference_media(plan)
    assert probe.duration_s > probe.last_video_frame_s

    manifest_path = extract_reference_evidence(plan)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    r037_end = next(
        record
        for record in manifest["frames"]
        if record["shot_id"] == "R037" and record["label"] == "end"
    )
    assert (plan.output_root / "reference" / "shots" / "R037" / "end.jpg").is_file()
    assert (plan.output_root / "reference" / "contact_sheets" / "full_01_5x8.jpg").is_file()
    assert manifest_path.is_file()
    assert manifest["last_video_frame_s"] == pytest.approx(77.133333)
    assert r037_end["timestamp_s"] == pytest.approx(77.133333)


def test_annotation_template_is_blank_and_loader_requires_review(tmp_path):
    plan = replica_plan(tmp_path)

    template_path = write_shot_annotation_template(plan)

    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template_path == plan.output_root / "reference" / "shot_annotations.template.json"
    assert len(template["shots"]) == len(plan.shots)
    assert template["shots"][0]["characters"] == []
    assert template["shots"][0]["scene_anchor_id"] == ""
    assert template["shots"][0]["framing"] == ""
    assert template["shots"][0]["action"] == ""
    assert template["shots"][0]["ocr_review"] == {
        "evidence_path": "",
        "evidence_sha256": "",
        "detected_item_count": 0,
        "review_complete": False,
    }
    assert template["shots"][0]["ocr_events"] == []
    assert template["caption_safe_region"] == {
        "x": 36,
        "y": 880,
        "width": 648,
        "height": 320,
    }
    assert template["shots"][0]["manual_review_required"] is True
    with pytest.raises(PetReplicaReferenceError, match="manual review"):
        load_reviewed_shot_annotations(plan)

    template = reviewed_annotation_payload(plan)
    template["shots"][0]["ocr_events"] = [reviewed_event(plan)]
    bind_detected_items(
        plan,
        template,
        [detected_item("D001", "你在看什么")],
    )
    write_reviewed_annotations(plan, template)

    annotations = load_reviewed_shot_annotations(plan)

    assert annotations[0].shot_id == "R001"
    assert annotations[0].framing == "tight_face_closeup"
    assert annotations[0].scene_anchor_id == "scene_sofa"
    assert annotations[0].ocr_events[0].classification == "dialogue_subtitle"
    assert annotations[0].ocr_events[0].reviewed_text == "你在看什么"
    assert annotations[0].ocr_events[0].placement.alignment == "bottom_center"


def test_annotation_dataclass_accepts_explicit_legacy_subtitle_compatibility_view():
    annotation = ReplicaShotAnnotation(
        shot_id="R001",
        characters=("source_woman",),
        speaker="source_woman",
        scene_anchor_id="scene_sofa",
        location="living_room_sofa",
        framing="medium",
        action="woman talks with two cats",
        source_audio=True,
        manual_review_required=False,
        subtitle="legacy caller text",
    )

    assert annotation.subtitle == "legacy caller text"


def test_v1_loader_preserves_legacy_subtitle_compatibility_view(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    payload["schema_version"] = "motion-comic-factory.pet-replica-annotations.v1"
    payload["shots"][0]["subtitle"] = "legacy whole-shot subtitle"
    write_reviewed_annotations(plan, payload)

    annotations = load_reviewed_shot_annotations(plan)

    assert annotations[0].subtitle == "legacy whole-shot subtitle"


def test_v2_loader_derives_stable_legacy_subtitle_from_renderable_dialogue_only(
    tmp_path,
):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["subtitle"] = "forged free-form bypass"
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(
            plan,
            event_id="R001-OCR-LATE",
            detection_id="D-LATE",
            reviewed_text="后一句",
            start_s=0.4,
            end_s=0.6,
        ),
        reviewed_event(
            plan,
            event_id="R001-OCR-DECORATIVE",
            detection_id="D-DECORATIVE",
            classification="decorative_caption",
            reviewed_text="罐罐",
            start_s=0.2,
            end_s=0.3,
        ),
        reviewed_event(
            plan,
            event_id="R001-OCR-WATERMARK",
            detection_id="D-WATERMARK",
            classification="account_identity",
            reviewed_text="作者：source_creator",
            start_s=0.1,
            end_s=0.2,
        ),
        reviewed_event(
            plan,
            event_id="R001-OCR-EARLY",
            detection_id="D-EARLY",
            reviewed_text="前一句",
            start_s=0.1,
            end_s=0.2,
        ),
    ]
    bind_detected_items(
        plan,
        payload,
        [
            detected_item("D-LATE", "后一句", start_s=0.4, end_s=0.6),
            detected_item(
                "D-DECORATIVE",
                "罐罐",
                start_s=0.2,
                end_s=0.3,
            ),
            detected_item(
                "D-WATERMARK",
                "作者：source_creator",
                start_s=0.1,
                end_s=0.2,
            ),
            detected_item("D-EARLY", "前一句", start_s=0.1, end_s=0.2),
        ],
    )
    write_reviewed_annotations(plan, payload)

    annotation = load_reviewed_shot_annotations(
        plan,
        require_ocr_events=True,
    )[0]

    assert annotation.subtitle == "前一句\n后一句"
    assert "forged" not in annotation.subtitle
    assert "罐罐" not in annotation.subtitle
    assert "source_creator" not in annotation.subtitle


@pytest.mark.parametrize("scene_anchor_id", ("", "scene_window", "scene_sofa "))
def test_annotation_loader_requires_explicit_known_scene_anchor(tmp_path, scene_anchor_id):
    plan = replica_plan(tmp_path)
    template = reviewed_annotation_payload(plan)
    for shot in template["shots"]:
        shot["scene_anchor_id"] = scene_anchor_id
    reviewed = write_reviewed_annotations(plan, template)
    with pytest.raises(PetReplicaReferenceError, match="scene_anchor_id"):
        load_reviewed_shot_annotations(plan)

    template["shots"][0]["action"] = ""
    reviewed.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(PetReplicaReferenceError, match="action"):
        load_reviewed_shot_annotations(plan)


def test_loader_retains_all_recognized_exclusions_without_rendering_them_as_dialogue(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    classifications = (
        "platform_watermark",
        "account_identity",
        "author_identity",
        "avatar",
        "creator_label",
        "source_end_card",
    )
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(
            plan,
            event_id=f"R001-OCR-{index:03d}",
            detection_id=f"D{index:03d}",
            classification=classification,
            reviewed_text=f"review evidence {classification}",
        )
        for index, classification in enumerate(classifications, start=1)
    ]
    bind_detected_items(
        plan,
        payload,
        [
            detected_item(
                f"D{index:03d}",
                f"review evidence {classification}",
            )
            for index, classification in enumerate(classifications, start=1)
        ],
    )
    write_reviewed_annotations(plan, payload)

    events = load_reviewed_shot_annotations(plan)[0].ocr_events

    assert tuple(event.classification for event in events) == classifications
    assert all(event.renderable is False for event in events)


@pytest.mark.parametrize(
    "branding",
    (
        "作者：某某",
        "原作者 某某",
        "账号：123",
        "帐号 123",
        "用户名 abc",
        "用户ID 123",
        "抖音号 abc",
        "小红书号 abc",
        "UP主 abc",
        "creator: abc",
        "account abc",
        "username abc",
        "platform_id abc",
    ),
)
def test_loader_rejects_branding_disguised_as_dialogue(tmp_path, branding):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(plan, reviewed_text=branding)
    ]
    bind_detected_items(
        plan,
        payload,
        [detected_item("D001", branding)],
    )
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="branding"):
        load_reviewed_shot_annotations(plan)


def test_loader_fails_closed_for_unknown_incomplete_or_legacy_ocr_events(tmp_path):
    plan = replica_plan(tmp_path)

    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(plan, classification="unreviewed_guess")
    ]
    bind_detected_items(
        plan,
        payload,
        [detected_item("D001", "你在看什么")],
    )
    write_reviewed_annotations(plan, payload)
    with pytest.raises(PetReplicaReferenceError, match="classification"):
        load_reviewed_shot_annotations(plan)

    payload = reviewed_annotation_payload(plan)
    event = reviewed_event(plan)
    event.pop("start_frame")
    payload["shots"][0]["ocr_events"] = [event]
    bind_detected_items(
        plan,
        payload,
        [detected_item("D001", "你在看什么")],
    )
    write_reviewed_annotations(plan, payload)
    with pytest.raises(PetReplicaReferenceError, match="timing"):
        load_reviewed_shot_annotations(plan)

    payload = reviewed_annotation_payload(plan)
    payload["schema_version"] = "motion-comic-factory.pet-replica-annotations.v1"
    payload["shots"][0]["subtitle"] = "legacy whole-shot subtitle"
    write_reviewed_annotations(plan, payload)
    with pytest.raises(PetReplicaReferenceError, match="schema"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


def test_loader_rejects_event_timing_or_placement_outside_reviewed_bounds(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(plan, end_s=plan.shots[0].end_s + 0.1)
    ]
    bind_detected_items(
        plan,
        payload,
        [detected_item("D001", "你在看什么")],
    )
    write_reviewed_annotations(plan, payload)
    with pytest.raises(PetReplicaReferenceError, match="source shot"):
        load_reviewed_shot_annotations(plan)

    payload = reviewed_annotation_payload(plan)
    payload["shots"][0]["ocr_events"] = [
        reviewed_event(
            plan,
            placement={
                "x": 48,
                "y": 600,
                "width": 624,
                "height": 180,
                "alignment": "bottom_center",
            },
        )
    ]
    bind_detected_items(
        plan,
        payload,
        [detected_item("D001", "你在看什么")],
    )
    write_reviewed_annotations(plan, payload)
    with pytest.raises(PetReplicaReferenceError, match="safe region"):
        load_reviewed_shot_annotations(plan)


def test_strict_loader_rejects_empty_events_against_nonzero_detection_evidence(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    shot = plan.shots[0]
    payload["shots"][0]["ocr_review"] = write_ocr_detection_evidence(
        plan,
        shot,
        [
            detected_item("D001", "你在看什么"),
            detected_item("D002", "作者：source_creator", start_s=0.1, end_s=0.6),
        ],
    )
    payload["shots"][0]["ocr_events"] = []
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="complete OCR review"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


def test_strict_loader_rejects_missing_duplicate_and_extra_detection_mappings(tmp_path):
    plan = replica_plan(tmp_path)

    missing = nonzero_review_payload(plan)
    missing["shots"][0]["ocr_events"].pop()
    write_reviewed_annotations(plan, missing)
    with pytest.raises(PetReplicaReferenceError, match="missing OCR detection"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)

    duplicate = nonzero_review_payload(plan)
    duplicate["shots"][0]["ocr_events"].append(
        {
            **duplicate["shots"][0]["ocr_events"][0],
            "event_id": "R001-OCR-003",
        }
    )
    write_reviewed_annotations(plan, duplicate)
    with pytest.raises(PetReplicaReferenceError, match="duplicate OCR detection"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)

    extra = nonzero_review_payload(plan)
    extra["shots"][0]["ocr_events"].append(
        reviewed_event(
            plan,
            event_id="R001-OCR-003",
            detection_id="D999",
            reviewed_text="forged extra",
        )
    )
    write_reviewed_annotations(plan, extra)
    with pytest.raises(PetReplicaReferenceError, match="extra OCR detection"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


def test_strict_loader_rejects_cross_shot_reuse_of_a_detection_identifier(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    placement = {
        "x": 48,
        "y": 940,
        "width": 624,
        "height": 180,
        "alignment": "bottom_center",
    }
    for shot_index in (0, 1):
        shot = plan.shots[shot_index]
        start_frame = round(shot.start_s * plan.fps) + 1
        end_frame = start_frame + 3
        start_s = start_frame / plan.fps
        end_s = end_frame / plan.fps
        payload["shots"][shot_index]["ocr_events"] = [
            {
                "event_id": f"{shot.shot_id}-OCR-001",
                "detection_id": "DUPLICATE",
                "classification": "dialogue_subtitle",
                "reviewed_text": f"{shot.shot_id} dialogue",
                "start_frame": start_frame,
                "end_frame": end_frame,
                "start_s": start_s,
                "end_s": end_s,
                "placement": placement,
                "manual_reviewed": True,
            }
        ]
        bind_detected_items(
            plan,
            payload,
            [
                detected_item(
                    "DUPLICATE",
                    f"{shot.shot_id} dialogue",
                    start_s=start_s,
                    end_s=end_s,
                )
            ],
            shot_index=shot_index,
        )
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="unique across shots"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("detected_item_count", 3, "count"),
        ("evidence_sha256", "0" * 64, "SHA-256"),
        ("review_complete", False, "complete OCR review"),
    ),
)
def test_strict_loader_rejects_forged_evidence_bindings(
    tmp_path,
    field,
    value,
    message,
):
    plan = replica_plan(tmp_path)
    payload = nonzero_review_payload(plan)
    payload["shots"][0]["ocr_review"][field] = value
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match=message):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"source_sha256": "0" * 64}, "source"),
        ({"shot_id": "R002"}, "shot"),
        ({"source_window": {"start_s": 0.0, "end_s": 0.8}}, "window"),
        (
            {
                "detected_items": [
                    detected_item("D001", "你在看什么", start_s=0.21, end_s=0.5),
                    detected_item(
                        "D002",
                        "作者：source_creator",
                        start_s=0.1,
                        end_s=0.6,
                    ),
                ]
            },
            "derived frame",
        ),
    ),
)
def test_strict_loader_rejects_forged_evidence_content(
    tmp_path,
    overrides,
    message,
):
    plan = replica_plan(tmp_path)
    payload = nonzero_review_payload(plan)
    shot = plan.shots[0]
    items = [
        detected_item("D001", "你在看什么"),
        detected_item("D002", "作者：source_creator", start_s=0.1, end_s=0.6),
    ]
    payload["shots"][0]["ocr_review"] = write_ocr_detection_evidence(
        plan,
        shot,
        items,
        payload_overrides=overrides,
    )
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match=message):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


@pytest.mark.parametrize(
    "forged_path",
    (
        "../ocr-evidence.json",
        "/tmp/ocr-evidence.json",
        "reference/ocr_evidence/R001/../R001/evidence.json",
    ),
)
def test_strict_loader_rejects_noncanonical_evidence_paths(tmp_path, forged_path):
    plan = replica_plan(tmp_path)
    payload = nonzero_review_payload(plan)
    payload["shots"][0]["ocr_review"]["evidence_path"] = forged_path
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="evidence path"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


def test_strict_loader_rejects_symlinked_evidence_file(tmp_path):
    plan = replica_plan(tmp_path)
    payload = nonzero_review_payload(plan)
    binding = payload["shots"][0]["ocr_review"]
    evidence = plan.output_root / binding["evidence_path"]
    outside = tmp_path / "outside-evidence.json"
    outside.write_bytes(evidence.read_bytes())
    evidence.unlink()
    evidence.symlink_to(outside)
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="symlink"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)


def test_strict_loader_accepts_content_addressed_reviewed_zero_for_every_shot(tmp_path):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    write_reviewed_annotations(plan, payload)

    annotations = load_reviewed_shot_annotations(
        plan,
        require_ocr_events=True,
    )

    assert all(annotation.ocr_events == () for annotation in annotations)
    assert all(annotation.ocr_evidence is not None for annotation in annotations)
    assert all(annotation.ocr_evidence.detected_item_count == 0 for annotation in annotations)
    assert all(annotation.ocr_evidence.reviewed_zero is True for annotation in annotations)


def test_loader_keeps_r012_decorative_caption_bbox_separate_from_final_placement(
    tmp_path,
):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    shot_index = 11
    shot = plan.shots[shot_index]
    start_s = (round(shot.start_s * plan.fps) + 3) / plan.fps
    end_s = start_s + 9 / plan.fps
    source_bbox = {
        "x": 126,
        "y": 212,
        "width": 188,
        "height": 72,
    }
    placement = {
        "x": 48,
        "y": 940,
        "width": 624,
        "height": 180,
        "alignment": "bottom_center",
    }
    item = detected_item(
        "R012-D001",
        "罐罐",
        start_s=start_s,
        end_s=end_s,
    )
    item["source_bbox"] = source_bbox
    payload["shots"][shot_index]["ocr_events"] = [
        reviewed_event(
            plan,
            event_id="R012-OCR-001",
            detection_id="R012-D001",
            classification="decorative_caption",
            reviewed_text="罐罐",
            start_s=start_s,
            end_s=end_s,
            placement=placement,
        )
    ]
    bind_detected_items(plan, payload, [item], shot_index=shot_index)
    write_reviewed_annotations(plan, payload)

    annotation = load_reviewed_shot_annotations(
        plan,
        require_ocr_events=True,
    )[shot_index]
    evidence_path = plan.output_root / annotation.ocr_evidence.evidence_path
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["detected_items"][0]["source_bbox"] == source_bbox
    assert asdict(annotation.ocr_events[0].placement) == placement
    assert annotation.ocr_events[0].classification == "decorative_caption"
    assert annotation.ocr_events[0].renderable is False


def test_loader_accepts_actual_rounded_first_and_end_exclusive_shot_frames(
    tmp_path,
):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    placement = {
        "x": 48,
        "y": 940,
        "width": 624,
        "height": 180,
        "alignment": "bottom_center",
    }
    cases = (
        (3, 164, 194, "R004-FIRST-THROUGH-END"),
        (len(plan.shots) - 1, 2316, 2317, "R037-FINAL"),
    )
    for shot_index, start_frame, end_frame, detection_id in cases:
        shot = plan.shots[shot_index]
        start_s = start_frame / plan.fps
        end_s = end_frame / plan.fps
        payload["shots"][shot_index]["ocr_events"] = [
            {
                "event_id": f"{shot.shot_id}-OCR-BOUNDARY",
                "detection_id": detection_id,
                "classification": "dialogue_subtitle",
                "reviewed_text": f"{shot.shot_id} boundary",
                "start_frame": start_frame,
                "end_frame": end_frame,
                "start_s": start_s,
                "end_s": end_s,
                "placement": placement,
                "manual_reviewed": True,
            }
        ]
        item = detected_item(
            detection_id,
            f"{shot.shot_id} boundary",
            start_s=start_s,
            end_s=end_s,
        )
        item["start_frame"] = start_frame
        item["end_frame"] = end_frame
        bind_detected_items(
            plan,
            payload,
            [item],
            shot_index=shot_index,
        )
    write_reviewed_annotations(plan, payload)

    annotations = load_reviewed_shot_annotations(
        plan,
        require_ocr_events=True,
    )

    for shot_index, start_frame, end_frame, _ in cases:
        event = annotations[shot_index].ocr_events[0]
        assert event.start_frame == start_frame
        assert event.end_frame == end_frame
        assert event.start_s == start_frame / plan.fps
        assert event.end_s == end_frame / plan.fps


def test_loader_rejects_seconds_that_do_not_match_authoritative_frame_indices(
    tmp_path,
):
    plan = replica_plan(tmp_path)
    payload = reviewed_annotation_payload(plan)
    event = reviewed_event(plan)
    event.update(
        {
            "start_frame": 3,
            "end_frame": 15,
            "start_s": 0.2,
            "end_s": 0.5,
        }
    )
    payload["shots"][0]["ocr_events"] = [event]
    item = detected_item("D001", "你在看什么", start_s=0.2, end_s=0.5)
    item.update({"start_frame": 3, "end_frame": 15})
    bind_detected_items(plan, payload, [item])
    write_reviewed_annotations(plan, payload)

    with pytest.raises(PetReplicaReferenceError, match="derived frame"):
        load_reviewed_shot_annotations(plan, require_ocr_events=True)
