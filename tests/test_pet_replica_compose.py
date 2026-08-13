from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from factory.pet_replica import build_pet_replica_plan


FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")


def _media_command(*args: str) -> None:
    subprocess.run(
        [str(FFMPEG_FULL), "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )


def _color_video(path: Path, color: str, *, duration: float = 0.4) -> Path:
    _media_command(
        "-f", "lavfi", "-i", f"color=c={color}:s=720x1280:r=30:d={duration}",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(path),
    )
    return path


def _sine_av(path: Path, frequency: int, *, duration: float = 0.8) -> Path:
    _media_command(
        "-f", "lavfi", "-i", f"color=c=0x303030:s=720x1280:r=30:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", str(path),
    )
    return path


def _moving_av(
    path: Path,
    *,
    video_duration: float,
    audio_duration: float,
    frequency: int = 440,
) -> Path:
    _media_command(
        "-f", "lavfi", "-i", f"testsrc2=s=720x1280:r=30:d={video_duration}",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={audio_duration}",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ac", "2", str(path),
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replica_plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"reference-video")
    return build_pet_replica_plan(source, tmp_path / "output")


def complete_selection(tmp_path: Path, *, pilot_only: bool) -> dict[str, Path]:
    plan = replica_plan(tmp_path)
    selected: dict[str, Path] = {}
    for shot in plan.shots:
        if pilot_only and shot.start_s >= plan.pilot_end_s:
            continue
        path = plan.output_root / "shots" / shot.shot_id / "candidate_01.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"candidate:{shot.shot_id}".encode("ascii"))
        selected[shot.shot_id] = path
    audio = plan.output_root / "audio" / "source_audio.aac"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"source-audio")
    return selected


_AAC_PACKET_DURATION = 1024 / 44100


def _fake_audio_runner(plan):
    shot_durations = {shot.shot_id: shot.duration_s for shot in plan.shots}

    def runner(command, **_kwargs):
        executable = Path(command[0]).name
        if executable == "ffprobe":
            target = Path(command[-1])
            if "-show_packets" in command:
                if target == plan.source_video:
                    payload = {
                        "packets": [
                            {
                                "pts_time": f"{-_AAC_PACKET_DURATION:.12f}",
                                "duration_time": f"{_AAC_PACKET_DURATION:.12f}",
                                "side_data_list": [
                                    {
                                        "side_data_type": "Skip Samples",
                                        "skip_samples": 1024,
                                        "discard_padding": 0,
                                    }
                                ],
                            },
                            {
                                "pts_time": "0.000000000000",
                                "duration_time": "77.229569000000",
                            },
                        ]
                    }
                else:
                    payload = {
                        "packets": [
                            {
                                "pts_time": "0.000000000000",
                                "duration_time": f"{_AAC_PACKET_DURATION:.12f}",
                            },
                            {
                                "pts_time": f"{_AAC_PACKET_DURATION:.12f}",
                                "duration_time": "77.345669000000",
                            },
                        ]
                    }
            elif target == plan.source_video:
                payload = {
                    "format": {"duration": "77.229569"},
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            elif target.name == "source_audio.aac":
                payload = {
                    "format": {"duration": "77.229569"},
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "sample_rate": "44100",
                            "channels": 2,
                        }
                    ],
                }
            else:
                payload = {
                    "format": {"duration": f"{shot_durations[target.stem]:.9f}"},
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "pcm_s16le",
                            "sample_rate": "48000",
                            "channels": 2,
                        }
                    ],
                }
            return SimpleNamespace(stdout=json.dumps(payload), stderr="")

        destination = Path(command[-1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if "-f" in command and "data" in command:
            destination.write_bytes(b"normalized-aac-payload")
        else:
            destination.write_bytes(f"artifact:{destination.name}".encode("ascii"))
        return SimpleNamespace(stdout="", stderr="")

    return runner


def write_reviewed_ocr_annotations(plan) -> Path:
    from factory.pet_replica_reference import write_shot_annotation_template

    template = json.loads(write_shot_annotation_template(plan).read_text(encoding="utf-8"))
    for plan_shot, annotation in zip(plan.shots, template["shots"]):
        annotation.update(
            {
                "characters": ["source_woman"],
                "speaker": "source_woman",
                "scene_anchor_id": "scene_sofa",
                "location": "living_room_sofa",
                "framing": "medium",
                "action": "woman talks with two cats",
                "manual_review_required": False,
                "ocr_review": _write_ocr_detection_evidence(
                    plan,
                    plan_shot,
                    [],
                ),
            }
        )
    first = plan.shots[0]
    placement = {
        "x": 48,
        "y": 940,
        "width": 624,
        "height": 180,
        "alignment": "bottom_center",
    }
    template["shots"][0]["ocr_events"] = [
        {
            "event_id": "R001-OCR-001",
            "detection_id": "D001",
            "classification": "dialogue_subtitle",
            "reviewed_text": "你在看什么",
            "start_frame": round((first.start_s + 0.2) * plan.fps),
            "end_frame": round(
                min(first.end_s, first.start_s + 0.5) * plan.fps
            ),
            "start_s": first.start_s + 0.2,
            "end_s": min(first.end_s, first.start_s + 0.5),
            "placement": placement,
            "manual_reviewed": True,
        },
        {
            "event_id": "R001-OCR-002",
            "detection_id": "D002",
            "classification": "author_identity",
            "reviewed_text": "作者：source_creator",
            "start_frame": round((first.start_s + 0.1) * plan.fps),
            "end_frame": round(
                min(first.end_s, first.start_s + 0.6) * plan.fps
            ),
            "start_s": first.start_s + 0.1,
            "end_s": min(first.end_s, first.start_s + 0.6),
            "placement": placement,
            "manual_reviewed": True,
        },
    ]
    template["shots"][0]["ocr_review"] = _write_ocr_detection_evidence(
        plan,
        first,
        [
            _ocr_detection("D001", "你在看什么", start_s=0.2, end_s=0.5),
            _ocr_detection(
                "D002",
                "作者：source_creator",
                start_s=0.1,
                end_s=0.6,
            ),
        ],
    )
    path = plan.output_root / "reference" / "shot_annotations.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    return path


def _ocr_detection(
    detection_id: str,
    text: str,
    *,
    start_s: float,
    end_s: float,
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


def _write_ocr_detection_evidence(plan, shot, items: list[dict]) -> dict:
    payload = {
        "schema_version": "motion-comic-factory.pet-replica-ocr-evidence.v1",
        "source_sha256": _sha256(plan.source_video),
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


def _write_selection_source_evidence(plan, shots) -> None:
    source_sha256 = _sha256(plan.source_video)
    frames = []
    for shot in shots:
        for label, color in (
            ("start", "red"),
            ("middle", "green"),
            ("end", "blue"),
        ):
            path = (
                plan.output_root
                / "reference"
                / "shots"
                / shot.shot_id
                / f"{label}.jpg"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (72, 128), color).save(path)
            frames.append(
                {
                    "shot_id": shot.shot_id,
                    "label": label,
                    "image_path": str(path.relative_to(plan.output_root)),
                    "image_sha256": _sha256(path),
                    "source_sha256": source_sha256,
                }
            )
    manifest = plan.output_root / "reference" / "evidence_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-reference.v1",
                "source_sha256": source_sha256,
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )


def _review_probe(plan, path: Path) -> dict[str, float | int]:
    if path.suffix == ".wav":
        return {"speech_start_s": 0.1, "speech_end_s": 0.4}
    shot = next(shot for shot in plan.shots if shot.shot_id == path.parent.name)
    return {
        "duration_s": shot.duration_s,
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
    }


def _review_frame(_path: Path, timestamp_s: float) -> Image.Image:
    value = max(1, min(254, int(timestamp_s * 101) + 20))
    return Image.new("RGB", (72, 128), (value, 80, 160))


def _approved_candidate(plan, shot, number: int, *, create_video: bool):
    from factory.pet_replica_generation import ReplicaCandidate
    from factory.pet_replica_review import (
        MANUAL_REVIEW_GATES,
        approve_replica_candidate,
        review_replica_candidate,
    )

    video = (
        plan.output_root
        / "shots"
        / shot.shot_id
        / f"candidate_{number:02d}.mp4"
    )
    video.parent.mkdir(parents=True, exist_ok=True)
    if create_video:
        video.write_bytes(
            f"approved-candidate:{shot.shot_id}:{number}".encode("ascii")
        )
    drive_audio = (
        plan.output_root / "audio" / "drive" / f"{shot.shot_id}.wav"
    )
    provenance = video.with_suffix(".provenance.json")
    provenance.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-generation.v1",
                "shot_id": shot.shot_id,
                "candidate_number": number,
                "editorial_duration_s": shot.duration_s,
                "provider_duration_s": 4,
                "source_window": {
                    "start_s": shot.start_s,
                    "end_s": shot.end_s,
                },
                "source_sha256": _sha256(plan.source_video),
                "drive_audio_sha256": _sha256(drive_audio),
                "output_sha256": _sha256(video),
                "output_path": str(video.relative_to(plan.output_root)),
            }
        ),
        encoding="utf-8",
    )
    gateway = video.with_suffix(".gateway.json")
    gateway.write_text("{}", encoding="utf-8")
    candidate = ReplicaCandidate(
        shot_id=shot.shot_id,
        candidate_number=number,
        video_path=video,
        provenance_path=provenance,
        gateway_report_path=gateway,
        editorial_duration_s=shot.duration_s,
        generation_duration_s=4,
        output_sha256=_sha256(video),
    )
    result = review_replica_candidate(
        plan,
        shot,
        candidate,
        frame_reader=_review_frame,
        probe_runner=lambda path: _review_probe(plan, path),
    )
    assert result.passed, result.failures
    approve_replica_candidate(
        plan,
        candidate,
        {
            **{gate: True for gate in MANUAL_REVIEW_GATES},
            "note": (
                "The start frame shows the original woman and both cats in the "
                "planned sofa positions; the end frame keeps the phone visible."
            ),
        },
    )
    return candidate


def _prepare_valid_publication_inputs(plan, selected) -> None:
    from factory.pet_replica_audio import extract_replica_audio
    from factory.pet_replica_reference import load_reviewed_shot_annotations
    from factory.pet_replica_review import validate_replica_selection

    extract_replica_audio(
        plan,
        load_reviewed_shot_annotations(plan),
        runner=_fake_audio_runner(plan),
    )
    required_shots = tuple(
        shot for shot in plan.shots if shot.shot_id in selected
    )
    _write_selection_source_evidence(plan, required_shots)
    for shot in required_shots:
        _approved_candidate(plan, shot, 1, create_video=False)
    validate_replica_selection(plan, pilot_only=True)


def _materialize_manifest_outputs(manifest) -> None:
    paths = (
        manifest.manifest_path,
        manifest.subtitle_path,
        manifest.concat_list_path,
        manifest.picture_path,
        manifest.clean_master_path,
        manifest.captioned_master_path,
        manifest.side_by_side_path,
        *(shot.normalized_path for shot in manifest.shots),
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-artifact")


def _staged_publication_snapshot(tmp_path: Path, release_id: str):
    from factory.pet_replica_compose import (
        _final_qc_payload,
        _manifest_payload,
        _published_manifest,
        _stage_manifest,
        _write_json,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / release_id
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    release_target = plan.output_root / "final" / "releases" / release_id
    published = _published_manifest(plan, staged, release_target)
    _write_json(staged.manifest_path, _manifest_payload(published))
    media_qc = {
        "schema_version": "motion-comic-factory.pet-replica-composition.v2",
        "valid": True,
        "mode": "pilot",
        "master_path": str(staged.master_path),
        "master_sha256": _sha256(staged.master_path),
        "masters": {
            "clean": {
                "valid": True,
                "path": str(staged.clean_master_path),
                "frame_boundaries": {"matched": True},
                "audio": {"source_match": True},
                "blackdetect": {"detected": False},
                "freezedetect": {"detected": False},
            },
            "captioned": {
                "valid": True,
                "path": str(staged.captioned_master_path),
                "frame_boundaries": {"matched": True},
                "caption_effects": {"changed_frames": [6, 7, 8]},
                "audio": {"source_match": True},
                "blackdetect": {"detected": False},
                "freezedetect": {"detected": False},
            },
        },
        "frame_sequence": {
            "frame_count": 24,
            "cuts": [{"actual_frame_index": 12, "delta_frames": 0}],
        },
        "master_frame_boundaries": {"matched": True},
        "blackdetect": {"detected": False},
        "source_pixel_evidence": {
            "direct_copy_detected": False,
            "samples": [{"frame": 6, "delta": 0.2}],
        },
        "comparison": {
            "valid": True,
            "path": str(staged.side_by_side_path),
            "audio_source_match": True,
        },
        "cut_count": 1,
        "cut_timestamps_s": [
            {"planned": 0.4, "actual": 0.4, "delta_frames": 0}
        ],
        "audio": {"source_match": True, "bit_rate": 192000},
    }
    qc = _final_qc_payload(
        media_qc,
        release_id=release_id,
        published=published,
        staged=staged,
    )
    _write_json(staged.final_qc_path, qc)
    pointer = plan.output_root / "final" / "pilot_current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('{"release": "old"}\n', encoding="utf-8")
    return SimpleNamespace(
        plan=plan,
        transaction=transaction,
        staged=staged,
        release_id=release_id,
        release_target=release_target,
        published=published,
        qc=qc,
        pointer=pointer,
    )


def _validated_staged_publication_snapshot(
    tmp_path: Path,
    monkeypatch,
    release_id: str,
):
    import factory.pet_replica_compose as compose
    from factory.pet_replica_audio import validate_replica_audio_manifest

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    selected = complete_selection(tmp_path, pilot_only=True)
    _prepare_valid_publication_inputs(plan, selected)
    runner = _fake_audio_runner(plan)
    monkeypatch.setattr(
        compose,
        "validate_replica_audio_manifest",
        lambda current_plan, path: validate_replica_audio_manifest(
            current_plan,
            path,
            runner=runner,
        ),
    )

    from factory.pet_replica_compose import (
        _final_qc_payload,
        _manifest_payload,
        _published_manifest,
        _stage_manifest,
        _write_json,
        build_replica_composition,
    )

    manifest = build_replica_composition(
        plan,
        selected,
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / release_id
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    release_target = plan.output_root / "final" / "releases" / release_id
    published = _published_manifest(plan, staged, release_target)
    _write_json(staged.manifest_path, _manifest_payload(published))
    qc = _final_qc_payload(
        {
            "schema_version": "motion-comic-factory.pet-replica-composition.v2",
            "valid": True,
            "mode": "pilot",
            "master_path": str(staged.master_path),
            "master_sha256": _sha256(staged.master_path),
            "masters": {
                "clean": {"valid": True},
                "captioned": {"valid": True},
            },
            "frame_sequence": {"frame_count": 24, "cuts": []},
            "audio": {"source_match": True},
            "comparison": {"valid": True},
        },
        release_id=release_id,
        published=published,
        staged=staged,
    )
    _write_json(staged.final_qc_path, qc)
    pointer = plan.output_root / "final" / "pilot_current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    old_pointer_bytes = b'{"release": "old", "sentinel": "byte-identical"}\n'
    pointer.write_bytes(old_pointer_bytes)
    return SimpleNamespace(
        plan=plan,
        transaction=transaction,
        staged=staged,
        release_id=release_id,
        release_target=release_target,
        published=published,
        qc=qc,
        pointer=pointer,
        old_pointer_bytes=old_pointer_bytes,
    )


def _publish_snapshot(snapshot) -> None:
    from factory.pet_replica_compose import _publish_release

    _publish_release(
        snapshot.plan.output_root,
        snapshot.transaction,
        "pilot",
        release_id=snapshot.release_id,
        validate_before_pointer=lambda: _validate_expected_publication_snapshot(
            snapshot.plan,
            snapshot.published,
            snapshot.qc,
        ),
    )


def _assert_failed_publication_cleanup(snapshot) -> None:
    assert snapshot.pointer.read_bytes() == snapshot.old_pointer_bytes
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def _validate_expected_publication_snapshot(plan, published, expected_qc) -> None:
    from factory.pet_replica_compose import _validate_publication_snapshot

    _validate_publication_snapshot(
        plan,
        published,
        expected_qc=expected_qc,
    )


def test_final_qc_relocates_nested_media_paths_to_published_release(tmp_path) -> None:
    snapshot = _staged_publication_snapshot(tmp_path, "pilot-qc-paths")

    assert snapshot.qc["masters"]["clean"]["path"] == str(
        snapshot.published.clean_master_path
    )
    assert snapshot.qc["masters"]["captioned"]["path"] == str(
        snapshot.published.captioned_master_path
    )
    assert snapshot.qc["comparison"]["path"] == str(
        snapshot.published.side_by_side_path
    )


def _mutate_nested_qc(qc: dict, mutation: str) -> None:
    if mutation == "valid":
        qc["valid"] = False
    elif mutation == "dual_master":
        qc["masters"]["captioned"]["valid"] = False
    elif mutation == "frame_sequence":
        qc["frame_sequence"]["frame_count"] += 1
    elif mutation == "cut":
        qc["frame_sequence"]["cuts"][0]["delta_frames"] = 1
    elif mutation == "audio":
        qc["audio"]["source_match"] = False
    elif mutation == "black":
        qc["blackdetect"]["detected"] = True
    elif mutation == "freeze":
        qc["masters"]["captioned"]["freezedetect"]["detected"] = True
    elif mutation == "source_copy":
        qc["source_pixel_evidence"]["direct_copy_detected"] = True
    elif mutation == "comparison":
        qc["comparison"]["valid"] = False
    else:
        raise AssertionError(f"unknown mutation: {mutation}")


def _replica_subtitle(
    *,
    event_id: str,
    shot_id: str,
    start_frame: int,
    end_frame: int,
    text: str,
    placement,
):
    from factory.pet_replica_compose import ReplicaSubtitle

    return ReplicaSubtitle(
        event_id=event_id,
        shot_id=shot_id,
        start_frame=start_frame,
        end_frame=end_frame,
        text=text,
        placement=placement,
    )


def _caption_glyph_inner_densities(
    clean_frame: Path,
    captioned_frame: Path,
) -> list[float]:
    clean = Image.open(clean_frame).convert("RGB")
    captioned = Image.open(captioned_frame).convert("RGB")
    difference = ImageChops.difference(clean, captioned).convert("L")
    mask = difference.point(lambda value: 255 if value > 16 else 0)
    bbox = mask.getbbox()
    assert bbox is not None
    x0, y0, x1, y1 = bbox
    occupied_columns = [
        x
        for x in range(x0, x1)
        if mask.crop((x, y0, x + 1, y1)).getbbox() is not None
    ]
    runs: list[list[int]] = []
    for x in occupied_columns:
        if not runs or x > runs[-1][-1] + 1:
            runs.append([x])
        else:
            runs[-1].append(x)
    densities: list[float] = []
    for run in runs:
        glyph = mask.crop((run[0], y0, run[-1] + 1, y1))
        width, height = glyph.size
        inner = glyph.crop(
            (
                width // 4,
                height // 4,
                width - width // 4,
                height - height // 4,
            )
        )
        pixels = list(inner.get_flattened_data())
        densities.append(sum(bool(pixel) for pixel in pixels) / len(pixels))
    return densities


def _planned_two_shots(tmp_path: Path):
    from factory.pet_replica_compose import ReplicaCompositionShot

    return (
        ReplicaCompositionShot(
            shot_id="R001",
            source_path=tmp_path / "source-1.mp4",
            source_sha256="a" * 64,
            source_start_s=0.0,
            source_end_s=0.4,
            editorial_duration_s=0.4,
            timeline_start_s=0.0,
            timeline_end_s=0.4,
            normalized_path=tmp_path / "normalized-1.mp4",
        ),
        ReplicaCompositionShot(
            shot_id="R002",
            source_path=tmp_path / "source-2.mp4",
            source_sha256="b" * 64,
            source_start_s=0.0,
            source_end_s=0.4,
            editorial_duration_s=0.4,
            timeline_start_s=0.4,
            timeline_end_s=0.8,
            normalized_path=tmp_path / "normalized-2.mp4",
        ),
    )


def test_pilot_composition_extends_to_complete_shot_boundary(tmp_path):
    from factory.pet_replica_compose import (
        build_replica_composition,
        build_replica_ffmpeg_commands,
    )

    plan = replica_plan(tmp_path)
    manifest = build_replica_composition(
        plan=plan,
        selection=complete_selection(tmp_path, pilot_only=True),
        mode="pilot",
        validate_inputs=False,
    )

    assert manifest.start_s == 0.0
    expected_end_s = round(plan.shots[8].end_s * plan.fps) / plan.fps
    assert manifest.end_s == expected_end_s
    assert manifest.duration_s == expected_end_s
    assert manifest.shots[-1].shot_id == "R009"
    assert manifest.shots[-1].editorial_duration_s == pytest.approx(
        expected_end_s - round(plan.shots[8].start_s * plan.fps) / plan.fps
    )
    assert manifest.shots[-1].editorial_duration_s > 1.0
    command = " ".join(manifest.ffmpeg_command)
    assert "tpad" not in command
    assert "minterpolate" not in command
    assert "optical" not in command
    normalization_commands = build_replica_ffmpeg_commands(manifest)[
        : len(manifest.shots)
    ]
    for shot, normalization in zip(manifest.shots, normalization_commands):
        filter_graph = normalization[normalization.index("-vf") + 1]
        expected_frames = max(1, round(shot.editorial_duration_s * plan.fps))
        assert f"trim=end_frame={expected_frames}" in filter_graph
        assert "in_range=auto:out_range=tv" in filter_graph
        assert normalization[normalization.index("-bf") + 1] == "0"
        assert normalization[normalization.index("-refs") + 1] == "1"
        assert normalization[normalization.index("-g") + 1] == "30"
        assert normalization[normalization.index("-keyint_min") + 1] == "30"
        assert normalization[normalization.index("-sc_threshold") + 1] == "0"
        x264_params = normalization[normalization.index("-x264-params") + 1]
        assert "open-gop=0" in x264_params
        assert "repeat-headers=1" in x264_params
        assert normalization[normalization.index("-color_range") + 1] == "tv"
        assert normalization[normalization.index("-colorspace") + 1] == "bt709"
        assert normalization[normalization.index("-color_trc") + 1] == "bt709"
        assert normalization[normalization.index("-color_primaries") + 1] == "bt709"
    caption_filter = manifest.captioned_master_command[
        manifest.captioned_master_command.index("-vf") + 1
    ]
    assert caption_filter.startswith("setpts=N/(30*TB),ass=")
    assert "-fps_mode" in manifest.captioned_master_command
    assert manifest.captioned_master_command[
        manifest.captioned_master_command.index("-fps_mode") + 1
    ] == "passthrough"
    assert "-r" not in manifest.captioned_master_command


def test_final_composition_maps_source_audio_without_timeline_rewrite(tmp_path):
    from factory.pet_replica_compose import build_replica_composition

    plan = replica_plan(tmp_path)
    manifest = build_replica_composition(
        plan=plan,
        selection=complete_selection(tmp_path, pilot_only=False),
        mode="final",
        validate_inputs=False,
    )

    command = " ".join(manifest.ffmpeg_command)
    assert "-map" in command
    assert "source_audio.aac" in command
    assert "-c:a copy" in command
    assert manifest.duration_s == pytest.approx(77.229569)
    assert len(manifest.shots) == len(plan.shots)


def test_pcm_audio_fallback_applies_delivery_headroom_before_aac_encode():
    from factory.pet_replica_compose import _audio_command

    command = _audio_command("pcm_to_aac_192k_once")

    assert command[:2] == ("-af", "volume=-2dB")
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"


def test_manifest_models_reviewed_events_and_unambiguous_clean_captioned_masters(tmp_path):
    from factory.pet_replica_compose import build_replica_composition

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)

    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )

    assert manifest.clean_master_path != manifest.captioned_master_path
    assert manifest.master_path == manifest.captioned_master_path
    assert manifest.master_alias == "captioned_master_path"
    assert manifest.ffmpeg_command == manifest.captioned_master_command
    assert " ass=" not in f" {' '.join(manifest.clean_master_command)}"
    assert "ass=" in " ".join(manifest.captioned_master_command)
    assert tuple(event.classification for event in manifest.reviewed_ocr_events) == (
        "dialogue_subtitle",
        "author_identity",
    )
    assert len(manifest.ocr_evidence_bindings) == len(plan.shots)
    assert manifest.ocr_evidence_bindings[0].detected_item_count == 2
    assert manifest.ocr_evidence_bindings[0].evidence_path.startswith(
        "reference/ocr_evidence/R001/"
    )
    assert [subtitle.event_id for subtitle in manifest.subtitles] == ["R001-OCR-001"]
    assert hasattr(manifest.subtitles[0], "start_frame")
    assert manifest.subtitles[0].start_frame == 6
    assert manifest.subtitles[0].end_frame == 15
    assert manifest.subtitles[0].start_s == pytest.approx(0.2)
    assert manifest.subtitles[0].end_s == pytest.approx(0.5)


def test_composition_reloads_ocr_evidence_and_review_mapping_after_build(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _validate_current_ocr_evidence,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    annotations_path = write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    evidence_path = (
        plan.output_root / manifest.ocr_evidence_bindings[0].evidence_path
    )
    original_evidence = evidence_path.read_bytes()
    evidence_path.write_bytes(original_evidence + b"\n")
    with pytest.raises(PetReplicaCompositionError, match="OCR evidence"):
        _validate_current_ocr_evidence(plan, manifest)

    evidence_path.write_bytes(original_evidence)
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations["shots"][0]["ocr_events"][0]["reviewed_text"] = "篡改后的对白"
    annotations_path.write_text(
        json.dumps(annotations, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(PetReplicaCompositionError, match="snapshot changed"):
        _validate_current_ocr_evidence(plan, manifest)


def test_manifest_input_bundle_and_final_qc_persist_all_ocr_evidence_bindings(
    tmp_path,
):
    from factory.pet_replica_compose import (
        _final_qc_payload,
        _input_bundle,
        _manifest_payload,
        _published_manifest,
        _stage_manifest,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-persist"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    published = _published_manifest(
        plan,
        staged,
        plan.output_root / "final" / "releases" / "pilot-persist",
    )

    manifest_payload = _manifest_payload(published)
    input_bundle = _input_bundle(staged)
    final_qc = _final_qc_payload(
        {"valid": True},
        release_id="pilot-test",
        published=published,
        staged=staged,
    )

    expected = input_bundle["ocr_evidence"]
    assert len(expected) == len(plan.shots)
    assert manifest_payload["ocr_evidence_bindings"] == expected
    assert final_qc["ocr_evidence"] == expected
    assert final_qc["input_bundle"]["ocr_evidence"] == expected
    assert input_bundle["review_snapshot"]["staged"] is True
    assert final_qc["review_snapshot"] == input_bundle["review_snapshot"]
    assert (
        manifest_payload["reviewed_annotations_sha256"]
        == input_bundle["reviewed_annotations_sha256"]
    )


def test_input_bundle_rejects_reviewed_annotation_mutation_after_build(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _input_bundle,
        _stage_manifest,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    annotations_path = write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-mutation"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    captured_sha256 = _sha256(annotations_path)

    assert staged.reviewed_annotations_sha256 == captured_sha256
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations["shots"][0]["ocr_events"][0]["reviewed_text"] = "验证后篡改"
    annotations_path.write_text(
        json.dumps(annotations, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(PetReplicaCompositionError, match="reviewed annotation"):
        _input_bundle(staged)


def test_build_rejects_review_mutation_between_event_and_safe_region_reads(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose

    plan = replica_plan(tmp_path)
    annotations_path = write_reviewed_ocr_annotations(plan)
    original_loader = compose.load_reviewed_shot_annotations

    def load_then_mutate(*args, **kwargs):
        annotations = original_loader(*args, **kwargs)
        payload = json.loads(annotations_path.read_text(encoding="utf-8"))
        payload["caption_safe_region"]["x"] += 1
        annotations_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return annotations

    monkeypatch.setattr(
        compose,
        "load_reviewed_shot_annotations",
        load_then_mutate,
    )

    with pytest.raises(compose.PetReplicaCompositionError, match="invalid"):
        compose.build_replica_composition(
            plan,
            complete_selection(tmp_path, pilot_only=True),
            "pilot",
            validate_inputs=False,
        )


def test_pre_pointer_review_mutation_rolls_back_release_and_preserves_old_current(
    tmp_path,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _final_qc_payload,
        _manifest_payload,
        _publish_release,
        _published_manifest,
        _stage_manifest,
        _write_json,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    annotations_path = write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-race"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    release_id = "pilot-race"
    release_target = plan.output_root / "final" / "releases" / release_id
    published = _published_manifest(plan, staged, release_target)
    _write_json(staged.manifest_path, _manifest_payload(published))
    qc = _final_qc_payload(
        {
            "valid": True,
            "master_path": str(staged.master_path),
            "master_sha256": _sha256(staged.master_path),
        },
        release_id=release_id,
        published=published,
        staged=staged,
    )
    _write_json(staged.final_qc_path, qc)
    pointer = plan.output_root / "final" / "pilot_current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('{"release": "old"}\n', encoding="utf-8")

    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations["shots"][0]["ocr_events"][0]["reviewed_text"] = "验证后篡改"
    annotations_path.write_text(
        json.dumps(annotations, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(PetReplicaCompositionError, match="(?i)review"):
        _publish_release(
            plan.output_root,
            transaction,
            "pilot",
            release_id=release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                plan,
                published,
                qc,
            ),
        )

    assert json.loads(pointer.read_text(encoding="utf-8"))["release"] == "old"
    assert not release_target.exists()
    assert not transaction.exists()


def test_selected_clip_byte_mutation_before_pointer_rolls_back_exactly(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import PetReplicaCompositionError

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-selected-bytes",
    )
    snapshot.published.shots[0].source_path.write_bytes(
        b"mutated-after-final-qc"
    )

    with pytest.raises(
        PetReplicaCompositionError,
        match="Selected clip bytes changed after composition planning",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_source_aac_byte_mutation_before_pointer_rolls_back_exactly(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import PetReplicaCompositionError

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-source-aac-bytes",
    )
    snapshot.published.source_audio_path.write_bytes(
        b"mutated-aac-after-final-qc"
    )

    with pytest.raises(
        PetReplicaCompositionError,
        match="Source AAC bytes changed after composition planning",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_presentation_source_byte_mutation_before_pointer_is_independently_hashed(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-presentation-source-bytes",
    )
    monkeypatch.setattr(
        compose,
        "_validate_current_ocr_evidence",
        lambda *_args, **_kwargs: None,
    )
    snapshot.published.presentation_source_path.write_bytes(
        b"mutated-presentation-source-after-final-qc"
    )

    with pytest.raises(
        compose.PetReplicaCompositionError,
        match="Presentation source bytes changed after composition planning",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_selection_metadata_mutation_before_pointer_rolls_back_exactly(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import PetReplicaCompositionError

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-selection-metadata",
    )
    selection_path = (
        snapshot.plan.output_root
        / "shots"
        / snapshot.published.shots[0].shot_id
        / "selection.json"
    )
    payload = json.loads(selection_path.read_text(encoding="utf-8"))
    payload["quality_approved"] = False
    selection_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        PetReplicaCompositionError,
        match="Current approved selection validation failed before publication",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_audio_manifest_metadata_mutation_before_pointer_rolls_back_exactly(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import PetReplicaCompositionError

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-audio-metadata",
    )
    manifest_path = (
        snapshot.plan.output_root / "audio" / "audio_manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["usage_scope"] = "public_release"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        PetReplicaCompositionError,
        match="Current audio manifest validation failed before publication",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_different_valid_approved_candidate_path_cannot_rebind_rendered_release(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import PetReplicaCompositionError
    from factory.pet_replica_review import validate_replica_selection

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-valid-selection-switch",
    )
    alternate = (
        snapshot.plan.output_root
        / "shots"
        / snapshot.plan.shots[0].shot_id
        / "candidate_02.mp4"
    )
    alternate.write_bytes(
        snapshot.published.shots[0].source_path.read_bytes()
    )
    _approved_candidate(
        snapshot.plan,
        snapshot.plan.shots[0],
        2,
        create_video=False,
    )
    validate_replica_selection(snapshot.plan, pilot_only=True)
    assert _sha256(alternate) == snapshot.published.shots[0].source_sha256

    with pytest.raises(
        PetReplicaCompositionError,
        match="Current approved selection does not match the rendered release",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_different_validated_audio_asset_path_cannot_rebind_rendered_release(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose

    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-valid-audio-switch",
    )
    validate_audio = compose.validate_replica_audio_manifest
    alternate = snapshot.plan.output_root / "audio" / "alternate_source.aac"
    alternate.write_bytes(snapshot.published.source_audio_path.read_bytes())

    def validate_with_alternate_asset(plan, path):
        current = validate_audio(plan, path)
        return replace(
            current,
            full_source=replace(
                current.full_source,
                path=alternate,
                sha256=_sha256(alternate),
            ),
        )

    monkeypatch.setattr(
        compose,
        "validate_replica_audio_manifest",
        validate_with_alternate_asset,
    )

    with pytest.raises(
        compose.PetReplicaCompositionError,
        match="Current audio asset does not match the rendered release",
    ):
        _publish_snapshot(snapshot)

    _assert_failed_publication_cleanup(snapshot)


def test_unmodified_canonical_snapshot_publishes_exact_new_current_pointer(
    tmp_path,
    monkeypatch,
):
    snapshot = _validated_staged_publication_snapshot(
        tmp_path,
        monkeypatch,
        "pilot-canonical",
    )

    _publish_snapshot(snapshot)

    assert snapshot.release_target.is_dir()
    assert not snapshot.transaction.exists()
    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "schema_version": "motion-comic-factory.pet-replica-composition.v2",
        "mode": "pilot",
        "release": "pilot-canonical",
        "release_path": "releases/pilot-canonical",
    }


@pytest.mark.parametrize(
    "mutation",
    (
        "valid",
        "dual_master",
        "frame_sequence",
        "cut",
        "audio",
        "black",
        "freeze",
        "source_copy",
        "comparison",
    ),
)
def test_complete_expected_qc_mutation_rolls_back_and_preserves_old_current(
    tmp_path,
    mutation,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _publish_release,
        _write_json,
    )

    snapshot = _staged_publication_snapshot(
        tmp_path,
        f"pilot-qc-{mutation}",
    )
    mutated_qc = json.loads(
        snapshot.staged.final_qc_path.read_text(encoding="utf-8")
    )
    _mutate_nested_qc(mutated_qc, mutation)
    _write_json(snapshot.staged.final_qc_path, mutated_qc)

    with pytest.raises(PetReplicaCompositionError, match="Final QC"):
        _publish_release(
            snapshot.plan.output_root,
            snapshot.transaction,
            "pilot",
            release_id=snapshot.release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                snapshot.plan,
                snapshot.published,
                snapshot.qc,
            ),
        )

    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "release": "old"
    }
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def test_self_consistent_rendered_artifact_and_local_digest_forgery_rolls_back(
    tmp_path,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _publish_release,
        _write_json,
    )

    snapshot = _staged_publication_snapshot(tmp_path, "pilot-artifact-forgery")
    snapshot.staged.captioned_master_path.write_bytes(b"forged-captioned-master")
    forged_sha256 = _sha256(snapshot.staged.captioned_master_path)
    mutated_qc = json.loads(
        snapshot.staged.final_qc_path.read_text(encoding="utf-8")
    )
    artifact_key = snapshot.staged.captioned_master_path.relative_to(
        snapshot.staged.manifest_path.parent
    ).as_posix()
    mutated_qc["artifact_sha256"][artifact_key] = forged_sha256
    mutated_qc["master_sha256"] = forged_sha256
    _write_json(snapshot.staged.final_qc_path, mutated_qc)

    with pytest.raises(PetReplicaCompositionError, match="Final QC"):
        _publish_release(
            snapshot.plan.output_root,
            snapshot.transaction,
            "pilot",
            release_id=snapshot.release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                snapshot.plan,
                snapshot.published,
                snapshot.qc,
            ),
        )

    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "release": "old"
    }
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def test_self_consistent_manifest_and_local_digest_forgery_rolls_back(
    tmp_path,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _publish_release,
        _write_json,
    )

    snapshot = _staged_publication_snapshot(tmp_path, "pilot-manifest-forgery")
    mutated_manifest = json.loads(
        snapshot.staged.manifest_path.read_text(encoding="utf-8")
    )
    mutated_manifest["project_id"] = "forged-project"
    _write_json(snapshot.staged.manifest_path, mutated_manifest)
    mutated_qc = json.loads(
        snapshot.staged.final_qc_path.read_text(encoding="utf-8")
    )
    mutated_qc["composition_manifest_sha256"] = _sha256(
        snapshot.staged.manifest_path
    )
    _write_json(snapshot.staged.final_qc_path, mutated_qc)

    with pytest.raises(PetReplicaCompositionError, match="composition manifest"):
        _publish_release(
            snapshot.plan.output_root,
            snapshot.transaction,
            "pilot",
            release_id=snapshot.release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                snapshot.plan,
                snapshot.published,
                snapshot.qc,
            ),
        )

    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "release": "old"
    }
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def test_current_ocr_evidence_mutation_rolls_back_and_preserves_old_current(
    tmp_path,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _publish_release,
    )

    snapshot = _staged_publication_snapshot(tmp_path, "pilot-current-evidence")
    binding = snapshot.published.ocr_evidence_bindings[0]
    current_evidence = snapshot.plan.output_root / binding.evidence_path
    current_evidence.write_bytes(b"forged-current-evidence")

    with pytest.raises(PetReplicaCompositionError, match="OCR evidence"):
        _publish_release(
            snapshot.plan.output_root,
            snapshot.transaction,
            "pilot",
            release_id=snapshot.release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                snapshot.plan,
                snapshot.published,
                snapshot.qc,
            ),
        )

    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "release": "old"
    }
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def test_current_subtitle_font_mutation_rolls_back_and_preserves_old_current(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose

    snapshot = _staged_publication_snapshot(tmp_path, "pilot-current-font")
    original_sha256 = compose._sha256

    def changed_font_sha256(path):
        if Path(path) == snapshot.published.subtitle_font_path:
            return "0" * 64
        return original_sha256(path)

    monkeypatch.setattr(compose, "_sha256", changed_font_sha256)

    with pytest.raises(compose.PetReplicaCompositionError, match="font SHA-256"):
        compose._publish_release(
            snapshot.plan.output_root,
            snapshot.transaction,
            "pilot",
            release_id=snapshot.release_id,
            validate_before_pointer=lambda: _validate_expected_publication_snapshot(
                snapshot.plan,
                snapshot.published,
                snapshot.qc,
            ),
        )

    assert json.loads(snapshot.pointer.read_text(encoding="utf-8")) == {
        "release": "old"
    }
    assert not snapshot.release_target.exists()
    assert not snapshot.transaction.exists()


def test_manifest_and_qc_json_reject_non_finite_numbers(tmp_path):
    from factory.pet_replica_compose import (
        _final_qc_payload,
        _manifest_payload,
        _published_manifest,
        _stage_manifest,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-nan"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    published = _published_manifest(
        plan,
        staged,
        plan.output_root / "final" / "releases" / "pilot-nan",
    )

    with pytest.raises(ValueError, match="JSON compliant"):
        _manifest_payload(replace(published, duration_s=float("nan")))
    with pytest.raises(ValueError, match="JSON compliant"):
        _final_qc_payload(
            {"valid": True, "duration_s": float("nan")},
            release_id="pilot-nan",
            published=published,
            staged=staged,
        )


def test_manifest_binds_checked_cjk_font_in_command_bundle_and_qc(tmp_path):
    from factory.pet_replica_compose import (
        _final_qc_payload,
        _input_bundle,
        _manifest_payload,
        _published_manifest,
        _stage_manifest,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-font"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)
    _materialize_manifest_outputs(staged)
    published = _published_manifest(
        plan,
        staged,
        plan.output_root / "final" / "releases" / "pilot-font",
    )
    staged.manifest_path.write_text(
        json.dumps(_manifest_payload(published), ensure_ascii=False),
        encoding="utf-8",
    )
    bundle = _input_bundle(staged)
    qc = _final_qc_payload(
        {"valid": True},
        release_id="pilot-font",
        published=published,
        staged=staged,
    )

    assert manifest.subtitle_font_path == Path(
        "/System/Library/Fonts/STHeiti Medium.ttc"
    ).resolve()
    assert manifest.subtitle_font_family == "Heiti SC"
    assert manifest.subtitle_font_sha256 == _sha256(manifest.subtitle_font_path)
    command = " ".join(manifest.captioned_master_command)
    assert "fontsdir=" in command
    assert str(manifest.subtitle_font_path.parent) in command
    expected = {
        "path": str(manifest.subtitle_font_path),
        "family": manifest.subtitle_font_family,
        "sha256": manifest.subtitle_font_sha256,
    }
    assert bundle["subtitle_font"] == expected
    assert qc["subtitle_font"] == expected


def test_subtitle_font_binding_rejects_missing_symlink_changed_and_unreadable(
    tmp_path,
    monkeypatch,
):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _validate_subtitle_font_binding,
    )

    font_path = Path("/System/Library/Fonts/STHeiti Medium.ttc").resolve()
    font_sha256 = _sha256(font_path)
    missing = tmp_path / "missing.ttc"
    with pytest.raises(PetReplicaCompositionError, match="regular file"):
        _validate_subtitle_font_binding(
            missing,
            "Heiti SC",
            font_sha256,
            verify_hash=True,
        )

    linked = tmp_path / "linked.ttc"
    linked.symlink_to(font_path)
    with pytest.raises(PetReplicaCompositionError, match="symlink"):
        _validate_subtitle_font_binding(
            linked,
            "Heiti SC",
            font_sha256,
            verify_hash=True,
        )

    with pytest.raises(PetReplicaCompositionError, match="SHA-256 changed"):
        _validate_subtitle_font_binding(
            font_path,
            "Heiti SC",
            "0" * 64,
            verify_hash=True,
        )

    original_open = Path.open

    def reject_bound_font_open(path, *args, **kwargs):
        if path == font_path:
            raise PermissionError("injected unreadable font")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_bound_font_open)
    with pytest.raises(PetReplicaCompositionError, match="Unable to hash"):
        _validate_subtitle_font_binding(
            font_path,
            "Heiti SC",
            font_sha256,
            verify_hash=True,
        )


def test_ass_uses_reviewed_event_windows_and_explicit_lower_screen_placement(tmp_path):
    from factory.pet_replica_compose import _write_subtitles, build_replica_composition

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )

    _write_subtitles(manifest)

    ass = manifest.subtitle_path.read_text(encoding="utf-8-sig")
    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    assert "0:00:00.20,0:00:00.50" in dialogue
    assert r"{\an2\pos(360,1120)}" in dialogue
    assert "你在看什么" in dialogue
    assert "source_creator" not in ass


def test_pilot_complete_shot_preserves_authoritative_frame_indices(tmp_path):
    from factory.pet_replica_compose import build_replica_composition

    plan = replica_plan(tmp_path)
    annotations_path = write_reviewed_ocr_annotations(plan)
    payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    shot = plan.shots[8]
    start_frame = 368
    end_frame = 371
    placement = {
        "x": 48,
        "y": 940,
        "width": 624,
        "height": 180,
        "alignment": "bottom_center",
    }
    payload["shots"][8]["ocr_events"] = [
        {
            "event_id": "R009-OCR-CLIPPED",
            "detection_id": "D-R009-CLIPPED",
            "classification": "dialogue_subtitle",
            "reviewed_text": "试播边界",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "start_s": start_frame / plan.fps,
            "end_s": end_frame / plan.fps,
            "placement": placement,
            "manual_reviewed": True,
        }
    ]
    detection = _ocr_detection(
        "D-R009-CLIPPED",
        "试播边界",
        start_s=start_frame / plan.fps,
        end_s=end_frame / plan.fps,
    )
    detection["start_frame"] = start_frame
    detection["end_frame"] = end_frame
    payload["shots"][8]["ocr_review"] = _write_ocr_detection_evidence(
        plan,
        shot,
        [detection],
    )
    annotations_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    clipped = next(
        subtitle
        for subtitle in manifest.subtitles
        if subtitle.event_id == "R009-OCR-CLIPPED"
    )

    assert hasattr(clipped, "start_frame")
    assert (clipped.start_frame, clipped.end_frame) == (368, 371)
    assert clipped.start_s == clipped.start_frame / plan.fps
    assert clipped.end_s == clipped.end_frame / plan.fps


@pytest.mark.parametrize(
    ("start_frame", "end_frame", "expected_start", "expected_end"),
    (
        (164, 165, "0:00:05.46", "0:00:05.50"),
        (193, 194, "0:00:06.43", "0:00:06.46"),
        (164, 194, "0:00:05.46", "0:00:06.46"),
        (2316, 2317, "0:01:17.20", "0:01:17.23"),
    ),
)
def test_ass_uses_floor_mapped_authoritative_frame_boundaries(
    start_frame,
    end_frame,
    expected_start,
    expected_end,
):
    from factory.pet_replica_compose import _subtitle_document
    from factory.pet_replica_reference import ReplicaCaptionPlacement

    subtitle = _replica_subtitle(
        event_id="OCR-BOUNDARY",
        shot_id="R004",
        start_frame=start_frame,
        end_frame=end_frame,
        text="猫咪对白",
        placement=ReplicaCaptionPlacement(
            x=48,
            y=940,
            width=624,
            height=180,
            alignment="bottom_center",
        ),
    )

    dialogue = next(
        line
        for line in _subtitle_document((subtitle,), "Heiti SC").splitlines()
        if line.startswith("Dialogue:")
    )
    assert f"{expected_start},{expected_end}" in dialogue


@pytest.mark.parametrize(
    "changed_frames",
    (
        [2, 3, 4, 5],
        [3, 5],
    ),
)
def test_caption_qc_rejects_one_frame_grace_and_missing_intended_frames(
    tmp_path,
    monkeypatch,
    changed_frames,
):
    import factory.pet_replica_compose as compose
    from factory.pet_replica_reference import (
        ReplicaCaptionPlacement,
        ReplicaCaptionSafeRegion,
    )

    picture = tmp_path / "picture.mp4"
    captioned = tmp_path / "captioned.mp4"
    picture.write_bytes(b"picture")
    captioned.write_bytes(b"captioned")
    picture_hashes = [f"frame-{index}" for index in range(12)]
    captioned_hashes = list(picture_hashes)
    for index in changed_frames:
        captioned_hashes[index] = f"caption-{index}"
    hashes = iter((picture_hashes, captioned_hashes))
    monkeypatch.setattr(compose, "_video_frame_hashes", lambda *args, **kwargs: next(hashes))
    metrics = [(0.0, 0.0)] * 12
    for index in changed_frames:
        metrics[index] = (0.5, 170.0)
    monkeypatch.setattr(
        compose,
        "_frame_difference_metrics",
        lambda *args, **kwargs: metrics,
    )
    monkeypatch.setattr(
        compose,
        "_outside_safe_region_changed_frames",
        lambda *args, **kwargs: [],
    )
    subtitle = _replica_subtitle(
        event_id="OCR-EXACT",
        shot_id="R001",
        start_frame=3,
        end_frame=6,
        text="猫咪对白",
        placement=ReplicaCaptionPlacement(
            x=48,
            y=940,
            width=624,
            height=180,
            alignment="bottom_center",
        ),
    )

    with pytest.raises(
        compose.PetReplicaCompositionError,
        match="exact authoritative frame set",
    ):
        compose._validate_caption_effects(
            picture,
            captioned,
            subtitles=(subtitle,),
            caption_safe_region=ReplicaCaptionSafeRegion(
                x=36,
                y=880,
                width=648,
                height=320,
            ),
            tools=SimpleNamespace(),
            fps=30,
        )


def test_caption_qc_uses_exact_union_for_overlapping_windows(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose
    from factory.pet_replica_reference import (
        ReplicaCaptionPlacement,
        ReplicaCaptionSafeRegion,
    )

    picture = tmp_path / "picture.mp4"
    captioned = tmp_path / "captioned.mp4"
    picture.write_bytes(b"picture")
    captioned.write_bytes(b"captioned")
    picture_hashes = [f"frame-{index}" for index in range(12)]
    captioned_hashes = list(picture_hashes)
    for index in range(3, 10):
        captioned_hashes[index] = f"caption-{index}"
    hashes = iter((picture_hashes, captioned_hashes))
    monkeypatch.setattr(compose, "_video_frame_hashes", lambda *args, **kwargs: next(hashes))
    metrics = [(0.0, 0.0)] * 12
    for index in range(3, 10):
        metrics[index] = (0.5, 170.0)
    monkeypatch.setattr(
        compose,
        "_frame_difference_metrics",
        lambda *args, **kwargs: metrics,
    )
    monkeypatch.setattr(
        compose,
        "_outside_safe_region_changed_frames",
        lambda *args, **kwargs: [],
    )
    placement = ReplicaCaptionPlacement(
        x=48,
        y=940,
        width=624,
        height=180,
        alignment="bottom_center",
    )
    subtitles = (
        _replica_subtitle(
            event_id="OCR-OVERLAP-A",
            shot_id="R001",
            start_frame=3,
            end_frame=8,
            text="第一句",
            placement=placement,
        ),
        _replica_subtitle(
            event_id="OCR-OVERLAP-B",
            shot_id="R001",
            start_frame=6,
            end_frame=10,
            text="第二句",
            placement=placement,
        ),
    )

    proof = compose._validate_caption_effects(
        picture,
        captioned,
        subtitles=subtitles,
        caption_safe_region=ReplicaCaptionSafeRegion(
            x=36,
            y=880,
            width=648,
            height=320,
        ),
        tools=SimpleNamespace(),
        fps=30,
    )

    assert proof["changed_frames"] == list(range(3, 10))
    assert proof["events"][0]["changed_frames"] == list(range(3, 8))
    assert proof["events"][1]["changed_frames"] == list(range(6, 10))


def test_caption_qc_ignores_bounded_codec_noise_outside_window(
    tmp_path,
    monkeypatch,
):
    import factory.pet_replica_compose as compose
    from factory.pet_replica_reference import (
        ReplicaCaptionPlacement,
        ReplicaCaptionSafeRegion,
    )

    picture = tmp_path / "picture.mp4"
    captioned = tmp_path / "captioned.mp4"
    picture.write_bytes(b"picture")
    captioned.write_bytes(b"captioned")
    picture_hashes = [f"frame-{index}" for index in range(12)]
    captioned_hashes = list(picture_hashes)
    for index in (3, 4, 5, 8):
        captioned_hashes[index] = f"changed-{index}"
    hashes = iter((picture_hashes, captioned_hashes))
    monkeypatch.setattr(
        compose,
        "_video_frame_hashes",
        lambda *args, **kwargs: next(hashes),
    )
    metrics = [(0.0, 0.0)] * 12
    for index in (3, 4, 5):
        metrics[index] = (0.5, 170.0)
    metrics[8] = (0.001, 1.0)
    monkeypatch.setattr(
        compose,
        "_frame_difference_metrics",
        lambda *args, **kwargs: metrics,
        raising=False,
    )
    monkeypatch.setattr(
        compose,
        "_outside_safe_region_changed_frames",
        lambda *args, **kwargs: [],
    )
    subtitle = _replica_subtitle(
        event_id="OCR-CODEC-NOISE",
        shot_id="R001",
        start_frame=3,
        end_frame=6,
        text="猫咪对白",
        placement=ReplicaCaptionPlacement(
            x=48,
            y=940,
            width=624,
            height=180,
            alignment="bottom_center",
        ),
    )

    proof = compose._validate_caption_effects(
        picture,
        captioned,
        subtitles=(subtitle,),
        caption_safe_region=ReplicaCaptionSafeRegion(
            x=36,
            y=880,
            width=648,
            height=320,
        ),
        tools=SimpleNamespace(),
        fps=30,
    )

    assert proof["changed_frames"] == [3, 4, 5]
    assert proof["codec_noise_frames"] == [8]


def test_subtitle_validator_rejects_unreviewed_text_inside_a_valid_window(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _assert_subtitles_clean,
        _write_subtitles,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    write_reviewed_ocr_annotations(plan)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    _write_subtitles(manifest)
    ass = manifest.subtitle_path.read_text(encoding="utf-8-sig")
    manifest.subtitle_path.write_text(
        ass.replace("你在看什么", "未经审核的对白"),
        encoding="utf-8-sig",
    )

    with pytest.raises(PetReplicaCompositionError, match="reviewed OCR events"):
        _assert_subtitles_clean(manifest)


def test_composition_rejects_incomplete_selection_and_platform_branding(tmp_path):
    from factory.pet_replica_compose import PetReplicaCompositionError, build_replica_composition

    plan = replica_plan(tmp_path)
    selection = complete_selection(tmp_path, pilot_only=True)
    selection.pop("R009")
    with pytest.raises(PetReplicaCompositionError, match="selection"):
        build_replica_composition(plan, selection, "pilot", validate_inputs=False)

    selection = complete_selection(tmp_path, pilot_only=True)
    annotations = plan.output_root / "reference" / "shot_annotations.json"
    annotations.parent.mkdir(parents=True, exist_ok=True)
    annotations.write_text(
        '{"shots": [{"subtitle": "@source_creator"}]}', encoding="utf-8"
    )
    with pytest.raises(PetReplicaCompositionError, match="platform branding"):
        build_replica_composition(plan, selection, "pilot", validate_inputs=False)


def test_side_by_side_uses_only_reference_audio_and_no_transition_filters(tmp_path):
    from factory.pet_replica_compose import build_replica_composition

    plan = replica_plan(tmp_path)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )

    command = " ".join(manifest.side_by_side_command)
    assert str(plan.source_video) in command
    assert str(manifest.master_path) in command
    assert command.count("-map 0:a:0") == 1
    for forbidden in ("xfade", "tpad", "minterpolate", "optical", "blend"):
        assert forbidden not in command


def test_capability_checked_ffmpeg_full_renders_ass_and_drawtext(tmp_path):
    from factory.pet_replica_compose import _ffmpeg_tools, _probe_master, _run

    source = _color_video(tmp_path / "source.mp4", "0x202020")
    ass = tmp_path / "subtitle.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,1,0,2,20,20,20,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        "Dialogue: 0,0:00:00.00,0:00:00.40,Default,,0,0,0,,hello\n",
        encoding="utf-8",
    )
    output = tmp_path / "filtered.mp4"
    tools = _ffmpeg_tools()

    _run(
        [
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-vf", f"ass='{ass}':original_size=720x1280,drawtext=text='QC':x=12:y=12:fontcolor=white:fontsize=18",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output),
        ]
    )

    probe = _probe_master(output, tools)
    assert tools.ffmpeg == FFMPEG_FULL.resolve()
    assert tools.ffprobe == FFMPEG_FULL.with_name("ffprobe").resolve()
    assert {"ass", "drawtext"} <= tools.filters
    assert probe["width"] == 720 and probe["height"] == 1280


def test_probe_master_uses_declared_rate_when_container_average_is_fractional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import factory.pet_replica_compose as composition

    master = tmp_path / "clean.mp4"
    master.write_bytes(b"master")
    payload = {
        "format": {"duration": "12.300000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 720,
                "height": 1280,
                "r_frame_rate": "30/1",
                "avg_frame_rate": "944640/31657",
                "nb_read_frames": "369",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
            },
        ],
    }
    monkeypatch.setattr(
        composition,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    probe = composition._probe_master(
        master,
        SimpleNamespace(ffprobe=Path("ffprobe")),
    )

    assert probe["fps"] == 30.0


def test_real_short_clean_and_captioned_masters_validate_independently(tmp_path):
    from factory.pet_replica_compose import (
        _build_captioned_master_command,
        _build_clean_master_command,
        _ffmpeg_tools,
        _prove_frame_sequence,
        _run,
        _validate_dual_master_media,
        _write_subtitles,
    )
    from factory.pet_replica_reference import (
        ReplicaCaptionPlacement,
        ReplicaCaptionSafeRegion,
    )

    tools = _ffmpeg_tools()
    source = _moving_av(
        tmp_path / "source.mp4",
        video_duration=0.8,
        audio_duration=0.8,
    )
    picture = source
    safe_region = ReplicaCaptionSafeRegion(x=36, y=880, width=648, height=320)
    placement = ReplicaCaptionPlacement(
        x=48,
        y=940,
        width=624,
        height=180,
        alignment="bottom_center",
    )
    subtitle = _replica_subtitle(
        event_id="R001-OCR-001",
        shot_id="R001",
        start_frame=6,
        end_frame=15,
        text="hello cats",
        placement=placement,
    )
    subtitle_path = tmp_path / "reviewed.ass"
    _write_subtitles(
        SimpleNamespace(subtitles=(subtitle,), subtitle_path=subtitle_path)
    )
    clean = tmp_path / "clean.mp4"
    captioned = tmp_path / "captioned.mp4"
    _run(
        _build_clean_master_command(
            picture,
            source,
            clean,
            duration_s=0.8,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
        )
    )
    _run(
        _build_captioned_master_command(
            picture,
            source,
            subtitle_path,
            captioned,
            duration_s=0.8,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
        )
    )
    frame_proof = _prove_frame_sequence((picture,), picture, tools, fps=30)

    qc = _validate_dual_master_media(
        picture,
        clean,
        captioned,
        source,
        subtitles=(subtitle,),
        caption_safe_region=safe_region,
        frame_proof=frame_proof,
        expected_duration_s=0.8,
        audio_mode="pcm_to_aac_192k_once",
        tools=tools,
        fps=30,
        width=720,
        height=1280,
    )

    assert qc["clean"]["variant"] == "clean"
    assert qc["clean"]["picture_preserved"] is True
    assert qc["captioned"]["variant"] == "captioned"
    assert qc["captioned"]["caption_effects"]["changed_frame_count"] > 0
    assert qc["captioned"]["caption_effects"]["outside_window_changed_frames"] == []
    assert qc["captioned"]["caption_effects"]["outside_safe_region_changed_frames"] == []
    assert qc["clean"]["audio"]["source_match"] is True
    assert qc["captioned"]["audio"]["source_match"] is True


def test_real_dynamic_chinese_caption_renders_distinct_non_tofu_glyphs(tmp_path):
    from factory.pet_replica_compose import (
        _build_captioned_master_command,
        _build_clean_master_command,
        _ffmpeg_tools,
        _preflight_subtitle_font,
        _run,
        _write_subtitles,
    )
    from factory.pet_replica_reference import ReplicaCaptionPlacement

    tools = _ffmpeg_tools()
    source = _sine_av(tmp_path / "source.mp4", 440)
    placement = ReplicaCaptionPlacement(
        x=48,
        y=940,
        width=624,
        height=180,
        alignment="bottom_center",
    )
    subtitle = _replica_subtitle(
        event_id="R001-OCR-CJK",
        shot_id="R001",
        start_frame=3,
        end_frame=18,
        text="猫咪对白，。！？",
        placement=placement,
    )
    font_path = Path("/System/Library/Fonts/STHeiti Medium.ttc").resolve()
    font_sha256 = _sha256(font_path)
    _preflight_subtitle_font(
        font_path,
        "Heiti SC",
        font_sha256,
        (subtitle,),
        tools=tools,
    )
    subtitle_path = tmp_path / "chinese.ass"
    _write_subtitles(
        SimpleNamespace(
            subtitles=(subtitle,),
            subtitle_path=subtitle_path,
            subtitle_font_family="Heiti SC",
        )
    )
    clean = tmp_path / "clean.mp4"
    captioned = tmp_path / "captioned.mp4"
    _run(
        _build_clean_master_command(
            source,
            source,
            clean,
            duration_s=0.8,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
        )
    )
    _run(
        _build_captioned_master_command(
            source,
            source,
            subtitle_path,
            captioned,
            duration_s=0.8,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
            subtitle_font_path=font_path,
            subtitle_font_family="Heiti SC",
            subtitle_font_sha256=font_sha256,
        )
    )
    clean_frame = tmp_path / "clean.png"
    captioned_frame = tmp_path / "captioned.png"
    for source_path, output_path in (
        (clean, clean_frame),
        (captioned, captioned_frame),
    ):
        _run(
            [
                str(tools.ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.300000",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                str(output_path),
            ]
        )

    densities = _caption_glyph_inner_densities(clean_frame, captioned_frame)
    assert len(densities) >= 4
    assert all(density > 0.05 for density in densities[:4])
    assert len({round(density, 3) for density in densities[:4]}) >= 3


@pytest.mark.parametrize(
    ("shot_id", "start_frame", "end_frame"),
    (
        pytest.param("R004", 164, 165, id="r004-first-frame"),
        pytest.param("R004", 193, 194, id="r004-tail-frame"),
        pytest.param("R004", 164, 194, id="r004-full-window"),
        pytest.param("R037", 2316, 2317, id="r037-final-window"),
    ),
)
def test_real_production_caption_render_matches_exact_authoritative_frames_and_cjk(
    tmp_path,
    shot_id,
    start_frame,
    end_frame,
):
    from factory.pet_replica_compose import (
        _build_captioned_master_command,
        _ffmpeg_tools,
        _preflight_subtitle_font,
        _run,
        _video_frame_hashes,
        _write_subtitles,
    )
    from factory.pet_replica_reference import ReplicaCaptionPlacement

    tools = _ffmpeg_tools()
    duration_s = (end_frame + 2) / 30
    source = _moving_av(
        tmp_path / "source.mp4",
        video_duration=duration_s,
        audio_duration=duration_s,
    )
    subtitle = _replica_subtitle(
        event_id=f"{shot_id}-OCR-BOUNDARY",
        shot_id=shot_id,
        start_frame=start_frame,
        end_frame=end_frame,
        text="猫咪对白，。！？",
        placement=ReplicaCaptionPlacement(
            x=48,
            y=940,
            width=624,
            height=180,
            alignment="bottom_center",
        ),
    )
    font_path = Path("/System/Library/Fonts/STHeiti Medium.ttc").resolve()
    font_sha256 = _sha256(font_path)
    _preflight_subtitle_font(
        font_path,
        "Heiti SC",
        font_sha256,
        (subtitle,),
        tools=tools,
    )
    subtitle_path = tmp_path / "boundary.ass"
    _write_subtitles(
        SimpleNamespace(
            subtitles=(subtitle,),
            subtitle_path=subtitle_path,
            subtitle_font_family="Heiti SC",
        )
    )
    captioned = tmp_path / "captioned.mp4"
    _run(
        _build_captioned_master_command(
            source,
            source,
            subtitle_path,
            captioned,
            duration_s=duration_s,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
            subtitle_font_path=font_path,
            subtitle_font_family="Heiti SC",
            subtitle_font_sha256=font_sha256,
        )
    )

    source_hashes = _video_frame_hashes(source, tools)
    captioned_hashes = _video_frame_hashes(captioned, tools)
    assert len(source_hashes) == len(captioned_hashes)
    changed_frames = [
        index
        for index, hashes in enumerate(zip(source_hashes, captioned_hashes))
        if hashes[0] != hashes[1]
    ]
    assert changed_frames == list(range(start_frame, end_frame))

    clean_frame = tmp_path / "clean-frame.png"
    captioned_frame = tmp_path / "captioned-frame.png"
    for media_path, frame_path in (
        (source, clean_frame),
        (captioned, captioned_frame),
    ):
        _run(
            [
                str(tools.ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(media_path),
                "-vf",
                f"select=eq(n\\,{start_frame})",
                "-frames:v",
                "1",
                str(frame_path),
            ]
        )
    densities = _caption_glyph_inner_densities(clean_frame, captioned_frame)
    assert len(densities) >= 4
    assert all(density > 0.05 for density in densities[:4])
    assert len({round(density, 3) for density in densities[:4]}) >= 3


def test_subtitle_font_preflight_rejects_missing_reviewed_chinese_glyphs(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _ffmpeg_tools,
        _preflight_subtitle_font,
    )
    from factory.pet_replica_reference import ReplicaCaptionPlacement

    font_path = Path("/System/Library/Fonts/Helvetica.ttc").resolve()
    subtitle = _replica_subtitle(
        event_id="R001-OCR-CJK",
        shot_id="R001",
        start_frame=3,
        end_frame=18,
        text="猫咪对白",
        placement=ReplicaCaptionPlacement(
            x=48,
            y=940,
            width=624,
            height=180,
            alignment="bottom_center",
        ),
    )

    with pytest.raises(PetReplicaCompositionError, match="missing glyph"):
        _preflight_subtitle_font(
            font_path,
            "Helvetica",
            _sha256(font_path),
            (subtitle,),
            tools=_ffmpeg_tools(),
        )


def test_captioned_master_rejects_effects_outside_reviewed_event_window(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _build_captioned_master_command,
        _ffmpeg_tools,
        _run,
        _validate_caption_effects,
        _write_subtitles,
    )
    from factory.pet_replica_reference import (
        ReplicaCaptionPlacement,
        ReplicaCaptionSafeRegion,
    )

    tools = _ffmpeg_tools()
    picture = _moving_av(
        tmp_path / "picture.mp4",
        video_duration=0.8,
        audio_duration=0.8,
    )
    placement = ReplicaCaptionPlacement(
        x=48,
        y=940,
        width=624,
        height=180,
        alignment="bottom_center",
    )
    rendered_event = _replica_subtitle(
        event_id="R001-OCR-001",
        shot_id="R001",
        start_frame=3,
        end_frame=12,
        text="rendered early",
        placement=placement,
    )
    ass = tmp_path / "forged.ass"
    _write_subtitles(SimpleNamespace(subtitles=(rendered_event,), subtitle_path=ass))
    captioned = tmp_path / "captioned.mp4"
    _run(
        _build_captioned_master_command(
            picture,
            picture,
            ass,
            captioned,
            duration_s=0.8,
            audio_mode="pcm_to_aac_192k_once",
            tools=tools,
        )
    )
    reviewed_late = _replica_subtitle(
        event_id=rendered_event.event_id,
        shot_id=rendered_event.shot_id,
        start_frame=15,
        end_frame=21,
        text=rendered_event.text,
        placement=rendered_event.placement,
    )

    with pytest.raises(
        PetReplicaCompositionError,
        match="exact authoritative frame set",
    ):
        _validate_caption_effects(
            picture,
            captioned,
            subtitles=(reviewed_late,),
            caption_safe_region=ReplicaCaptionSafeRegion(
                x=36,
                y=880,
                width=648,
                height=320,
            ),
            tools=tools,
            fps=30,
        )


def test_blackdetect_calibration_accepts_dark_scene_and_rejects_true_black(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _assert_no_black_segments,
        _ffmpeg_tools,
    )

    tools = _ffmpeg_tools()
    normal_dark = _color_video(tmp_path / "dark.mp4", "0x202020")
    true_black = _color_video(tmp_path / "black.mp4", "black")

    evidence = _assert_no_black_segments(normal_dark, tools)
    assert evidence["pix_th"] == pytest.approx(0.10)
    with pytest.raises(PetReplicaCompositionError, match="black segment"):
        _assert_no_black_segments(true_black, tools)


def test_rendered_frame_boundaries_reject_continuous_impostor(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _ffmpeg_tools,
        _prove_frame_sequence,
        _run,
    )

    first = _color_video(tmp_path / "first.mp4", "red")
    second = _color_video(tmp_path / "second.mp4", "blue")
    concat = tmp_path / "concat.ffconcat"
    concat.write_text(f"ffconcat version 1.0\nfile '{first}'\nfile '{second}'\n", encoding="utf-8")
    picture = tmp_path / "picture.mp4"
    tools = _ffmpeg_tools()
    _run(
        [
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c:v", "copy", "-an", str(picture),
        ]
    )

    proof = _prove_frame_sequence((first, second), picture, tools, fps=30)
    assert proof["frame_count"] == 24
    assert proof["cuts"][0]["before_sha256"] != proof["cuts"][0]["after_sha256"]

    impostor = _color_video(tmp_path / "impostor.mp4", "green", duration=0.8)
    with pytest.raises(PetReplicaCompositionError, match="frame sequence"):
        _prove_frame_sequence((first, second), impostor, tools, fps=30)


def test_frame_sequence_proof_uses_exact_indices_at_fractional_h264_cut(tmp_path):
    from factory.pet_replica_compose import (
        _ffmpeg_tools,
        _frame_sha256_at_index,
        _prove_frame_sequence,
        _run,
    )

    first = _color_video(tmp_path / "first-52.mp4", "red", duration=52 / 30)
    second = _color_video(tmp_path / "second-41.mp4", "blue", duration=41 / 30)
    concat = tmp_path / "fractional.ffconcat"
    concat.write_text(
        f"ffconcat version 1.0\nfile '{first}'\nfile '{second}'\n",
        encoding="utf-8",
    )
    picture = tmp_path / "fractional.mp4"
    tools = _ffmpeg_tools()
    _run(
        [
            str(tools.ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c:v",
            "copy",
            "-an",
            str(picture),
        ]
    )

    proof = _prove_frame_sequence((first, second), picture, tools, fps=30)

    assert proof["clip_frame_counts"] == [52, 41]
    assert proof["cuts"][0]["offset_frames"] == 0
    expected_before = _frame_sha256_at_index(
        first, 51, tmp_path / "expected-before.png", tools
    )
    expected_after = _frame_sha256_at_index(
        second, 0, tmp_path / "expected-after.png", tools
    )
    assert _frame_sha256_at_index(
        picture, 51, tmp_path / "actual-before.png", tools
    ) == expected_before
    assert _frame_sha256_at_index(
        picture, 52, tmp_path / "actual-after.png", tools
    ) == expected_after


def test_rendered_cut_binding_rejects_three_frames_but_allows_plus_or_minus_two(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        ReplicaCompositionShot,
        _bind_frame_proof_to_planned_cuts,
        _ffmpeg_tools,
        _prove_frame_sequence,
        _run,
    )

    tools = _ffmpeg_tools()
    planned = (
        ReplicaCompositionShot(
            shot_id="R001",
            source_path=tmp_path / "source-1.mp4",
            source_sha256="a" * 64,
            source_start_s=0.0,
            source_end_s=0.4,
            editorial_duration_s=0.4,
            timeline_start_s=0.0,
            timeline_end_s=0.4,
            normalized_path=tmp_path / "normalized-1.mp4",
        ),
        ReplicaCompositionShot(
            shot_id="R002",
            source_path=tmp_path / "source-2.mp4",
            source_sha256="b" * 64,
            source_start_s=0.0,
            source_end_s=0.4,
            editorial_duration_s=0.4,
            timeline_start_s=0.4,
            timeline_end_s=0.8,
            normalized_path=tmp_path / "normalized-2.mp4",
        ),
    )

    def measured_proof(name: str, first_frames: int, second_frames: int):
        first = _color_video(
            tmp_path / f"{name}-first.mp4", "red", duration=first_frames / 30
        )
        second = _color_video(
            tmp_path / f"{name}-second.mp4", "blue", duration=second_frames / 30
        )
        concat = tmp_path / f"{name}.ffconcat"
        concat.write_text(
            f"ffconcat version 1.0\nfile '{first}'\nfile '{second}'\n",
            encoding="utf-8",
        )
        picture = tmp_path / f"{name}.mp4"
        _run([
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
            "-safe", "0", "-i", str(concat), "-c:v", "copy", "-an", str(picture),
        ])
        return _prove_frame_sequence((first, second), picture, tools, fps=30)

    three_frames_late = measured_proof("plus-3", 15, 9)
    with pytest.raises(PetReplicaCompositionError, match="planned cut"):
        _bind_frame_proof_to_planned_cuts(three_frames_late, planned, fps=30)

    for name, first_frames, second_frames, expected_delta in (
        ("plus-2", 14, 10, 2),
        ("minus-2", 10, 14, -2),
    ):
        bound = _bind_frame_proof_to_planned_cuts(
            measured_proof(name, first_frames, second_frames), planned, fps=30
        )
        cut = bound["cuts"][0]
        assert cut["planned_timestamp_s"] == pytest.approx(0.4)
        assert cut["actual_timestamp_s"] == pytest.approx(first_frames / 30)
        assert cut["planned_frame_index"] == 12
        assert cut["actual_frame_index"] == first_frames
        assert cut["delta_frames"] == expected_delta


def test_master_boundary_proof_rejects_a_replaced_continuous_master(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _ffmpeg_tools,
        _prove_master_frame_boundaries,
        _prove_frame_sequence,
        _run,
    )

    first = _color_video(tmp_path / "first.mp4", "red")
    second = _color_video(tmp_path / "second.mp4", "blue")
    concat = tmp_path / "concat.ffconcat"
    concat.write_text(f"ffconcat version 1.0\nfile '{first}'\nfile '{second}'\n", encoding="utf-8")
    tools = _ffmpeg_tools()
    picture = tmp_path / "picture.mp4"
    _run([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c:v", "copy", "-an", str(picture),
    ])
    proof = _prove_frame_sequence((first, second), picture, tools, fps=30)
    master = tmp_path / "master.mp4"
    _run([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(picture),
        "-vf", "drawtext=text='QC':x=12:y=1100:fontcolor=white:fontsize=18", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(master),
    ])

    master_proof = _prove_master_frame_boundaries(picture, master, proof, tools, fps=30)
    assert master_proof["cut_count"] == 1

    impostor = _color_video(tmp_path / "impostor.mp4", "green", duration=0.8)
    with pytest.raises(PetReplicaCompositionError, match="(?i)master frame boundaries"):
        _prove_master_frame_boundaries(picture, impostor, proof, tools, fps=30)


def test_master_boundary_proof_ignores_pixels_inside_caption_safe_region(tmp_path):
    from factory.pet_replica_compose import (
        _ffmpeg_tools,
        _prove_frame_sequence,
        _prove_master_frame_boundaries,
        _run,
    )

    first = _color_video(tmp_path / "caption-first.mp4", "red")
    second = _color_video(tmp_path / "caption-second.mp4", "blue")
    concat = tmp_path / "caption.ffconcat"
    concat.write_text(
        f"ffconcat version 1.0\nfile '{first}'\nfile '{second}'\n",
        encoding="utf-8",
    )
    tools = _ffmpeg_tools()
    picture = tmp_path / "caption-picture.mp4"
    _run(
        [
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat),
            "-c:v", "copy", "-an", str(picture),
        ]
    )
    proof = _prove_frame_sequence((first, second), picture, tools, fps=30)
    captioned = tmp_path / "captioned.mp4"
    _run(
        [
            str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(picture),
            "-vf", "drawbox=x=0:y=880:w=720:h=80:color=white:t=fill",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(captioned),
        ]
    )

    result = _prove_master_frame_boundaries(
        picture,
        captioned,
        proof,
        tools,
        fps=30,
    )

    assert result["cut_count"] == 1


def test_audio_content_proof_rejects_unrelated_track_and_records_fallback(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _ffmpeg_tools,
        _verify_audio_against_source,
    )

    source = _sine_av(tmp_path / "source.mp4", 440)
    matched = _sine_av(tmp_path / "matched.mp4", 440)
    unrelated = _sine_av(tmp_path / "unrelated.mp4", 997)
    tools = _ffmpeg_tools()

    proof = _verify_audio_against_source(
        source, matched, tools, expected_duration_s=0.8, audio_mode="pcm_to_aac_192k_once"
    )
    assert proof["fallback_used"] is True
    assert proof["bit_rate"] >= 180_000
    with pytest.raises(PetReplicaCompositionError, match="source audio"):
        _verify_audio_against_source(
            source, unrelated, tools, expected_duration_s=0.8, audio_mode="pcm_to_aac_192k_once"
        )


def test_publish_transaction_failure_keeps_old_release_and_recovers_abandoned_work(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _publish_release,
        _recover_abandoned_transactions,
    )

    root = tmp_path / "output"
    final = root / "final"
    final.mkdir(parents=True)
    pointer = final / "pilot_current.json"
    pointer.write_text('{"release": "old"}\n', encoding="utf-8")
    transaction = root / "work" / ".transactions" / "pilot-deadbeef"
    release = transaction / "release"
    release.mkdir(parents=True)
    (release / "pilot_qc.json").write_text('{"valid": true}\n', encoding="utf-8")
    (release / "pilot_clean_master.mp4").write_bytes(b"old-clean")
    (release / "pilot_captioned_master.mp4").write_bytes(b"old-captioned")

    def fail_pointer(source: Path, destination: Path) -> None:
        if destination == pointer:
            raise OSError("injected pointer failure")
        os.replace(source, destination)

    with pytest.raises(PetReplicaCompositionError, match="publish"):
        _publish_release(root, transaction, "pilot", release_id="deadbeef", replace_file=fail_pointer)
    assert json.loads(pointer.read_text(encoding="utf-8"))["release"] == "old"
    assert not (final / "releases" / "deadbeef").exists()

    abandoned = root / "work" / ".transactions" / "pilot-abandoned"
    abandoned.mkdir(parents=True)
    _recover_abandoned_transactions(root)
    assert not abandoned.exists()


@pytest.mark.parametrize(
    "release_id",
    (
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "nested/release",
        r"nested\release",
        "release\nname",
        "release\x00name",
        "%2e%2e",
        "release%2fremainder",
        "release.name",
        "版本一",
        "a" * 129,
    ),
)
def test_publish_rejects_unsafe_release_ids(tmp_path, release_id):
    from factory.pet_replica_compose import PetReplicaCompositionError, _publish_release

    root = tmp_path / "output"
    transaction = (
        root
        / "work"
        / ".transactions"
        / f"pilot-adversarial-{len(list((root / 'work').glob('*')))}"
    )
    release = transaction / "release"
    release.mkdir(parents=True)
    (release / "pilot_qc.json").write_text('{"valid": true}\n', encoding="utf-8")

    with pytest.raises(PetReplicaCompositionError, match="release identifier"):
        _publish_release(root, transaction, "pilot", release_id=release_id)
    assert not (root / "final" / "pilot_current.json").exists()


def test_publish_writes_only_canonical_release_pointer_values(tmp_path):
    from factory.pet_replica_compose import _publish_release

    root = tmp_path / "output"
    transaction = root / "work" / ".transactions" / "pilot-safe"
    release = transaction / "release"
    release.mkdir(parents=True)
    (release / "pilot_qc.json").write_text('{"valid": true}\n', encoding="utf-8")

    _publish_release(root, transaction, "pilot", release_id="pilot-ABC_123")

    target = root / "final" / "releases" / "pilot-ABC_123"
    assert (target / "pilot_qc.json").is_file()
    pointer = json.loads((root / "final" / "pilot_current.json").read_text(encoding="utf-8"))
    assert pointer == {
        "schema_version": "motion-comic-factory.pet-replica-composition.v2",
        "mode": "pilot",
        "release": "pilot-ABC_123",
        "release_path": "releases/pilot-ABC_123",
    }


def test_publish_rejects_symlinked_releases_directory(tmp_path):
    from factory.pet_replica_compose import PetReplicaCompositionError, _publish_release

    root = tmp_path / "output"
    outside = tmp_path / "outside-releases"
    outside.mkdir()
    final = root / "final"
    final.mkdir(parents=True)
    (final / "releases").symlink_to(outside, target_is_directory=True)
    transaction = root / "work" / ".transactions" / "pilot-safe"
    release = transaction / "release"
    release.mkdir(parents=True)
    (release / "pilot_qc.json").write_text('{"valid": true}\n', encoding="utf-8")

    with pytest.raises(PetReplicaCompositionError, match="symlink"):
        _publish_release(root, transaction, "pilot", release_id="pilot-safe")
    assert not list(outside.iterdir())


def test_staged_and_published_artifacts_are_bound_to_one_release_directory(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _published_manifest,
        _stage_manifest,
        _validate_manifest_paths,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    transaction = plan.output_root / "work" / ".transactions" / "pilot-safe"
    transaction.mkdir(parents=True)
    staged = _stage_manifest(plan, manifest, transaction)

    for field in ("picture_path", "clean_master_path", "captioned_master_path"):
        forged_staged = replace(
            staged,
            **{field: transaction / "other-release" / f"{field}.mp4"},
        )
        with pytest.raises(PetReplicaCompositionError, match="release directory"):
            _validate_manifest_paths(
                forged_staged,
                plan=plan,
                require_outputs=False,
            )

    for forged_target in (
        plan.output_root / "work" / "forged-release",
        plan.output_root / "final" / "forged-release",
        plan.output_root / "final" / "releases" / "nested" / "release",
    ):
        with pytest.raises(PetReplicaCompositionError, match="release target"):
            _published_manifest(plan, staged, forged_target)

    release_target = plan.output_root / "final" / "releases" / "pilot-safe"
    published = _published_manifest(plan, staged, release_target)
    assert published.manifest_path.parent == release_target
    for path in (
        published.subtitle_path,
        published.concat_list_path,
        published.picture_path,
        published.clean_master_path,
        published.captioned_master_path,
        published.master_path,
        published.side_by_side_path,
        published.final_qc_path,
        *(shot.normalized_path for shot in published.shots),
    ):
        assert path.is_relative_to(release_target)


def test_dense_source_evidence_catches_copy_outside_old_three_global_samples(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _ffmpeg_tools,
        _prove_no_direct_source_pixels,
        _run,
    )

    tools = _ffmpeg_tools()
    source = _moving_av(
        tmp_path / "source.mp4", video_duration=0.8, audio_duration=0.8
    )
    clean_picture = _color_video(tmp_path / "clean.mp4", "green", duration=0.8)
    shots = _planned_two_shots(tmp_path)

    evidence = _prove_no_direct_source_pixels(
        clean_picture, source, shots, start_s=0.0, duration_s=0.8, tools=tools, fps=30
    )
    assert len(evidence["samples"]) >= 9
    assert {"shot_start", "shot_middle", "shot_end", "cut_before", "cut_at", "cut_after"} <= {
        item["kind"] for item in evidence["samples"]
    }
    for item in evidence["samples"]:
        assert {
            "timestamp_s", "source_timestamp_s", "picture_frame_index", "shot_id",
            "picture_sha256", "source_sha256", "perceptual_delta",
        } <= set(item)
        assert 0 <= item["picture_frame_index"] < 24

    before = _color_video(tmp_path / "before.mp4", "green", duration=6 / 30)
    copied = tmp_path / "copied.mp4"
    _run([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vf", "select=eq(n\\,6),setpts=N/(30*TB)", "-frames:v", "1", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(copied),
    ])
    after = _color_video(tmp_path / "after.mp4", "green", duration=17 / 30)
    concat = tmp_path / "injected.ffconcat"
    concat.write_text(
        f"ffconcat version 1.0\nfile '{before}'\nfile '{copied}'\nfile '{after}'\n",
        encoding="utf-8",
    )
    injected = tmp_path / "injected.mp4"
    _run([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
        "-safe", "0", "-i", str(concat), "-c:v", "copy", "-an", str(injected),
    ])

    with pytest.raises(PetReplicaCompositionError, match="direct source-frame"):
        _prove_no_direct_source_pixels(
            injected, source, shots, start_s=0.0, duration_s=0.8, tools=tools, fps=30
        )


def test_source_evidence_clamps_to_last_decodable_reference_frame(tmp_path):
    from factory.pet_replica_compose import (
        _ffmpeg_tools,
        _prove_no_direct_source_pixels,
    )

    tools = _ffmpeg_tools()
    source = _moving_av(
        tmp_path / "source.mp4", video_duration=0.8, audio_duration=0.9
    )
    picture = _color_video(tmp_path / "picture.mp4", "green", duration=0.9)
    first, second = _planned_two_shots(tmp_path)
    shots = (
        first,
        replace(
            second,
            source_end_s=0.5,
            editorial_duration_s=0.5,
            timeline_end_s=0.9,
        ),
    )

    evidence = _prove_no_direct_source_pixels(
        picture,
        source,
        shots,
        start_s=0.0,
        duration_s=0.9,
        tools=tools,
        fps=30,
    )

    assert evidence["reference_video_duration_s"] == pytest.approx(0.8)
    assert evidence["tail_clamped_sample_count"] > 0
    assert max(item["source_timestamp_s"] for item in evidence["samples"]) == pytest.approx(
        23 / 30
    )


def test_comparison_clamps_to_reference_video_end_and_validates_independently(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _build_comparison_command,
        _comparison_policy_duration,
        _ffmpeg_tools,
        _run,
        _validate_comparison_master,
    )

    tools = _ffmpeg_tools()
    reference = _moving_av(
        tmp_path / "reference.mp4", video_duration=0.8, audio_duration=0.9
    )
    remake = _moving_av(
        tmp_path / "remake.mp4", video_duration=0.9, audio_duration=0.9, frequency=997
    )
    policy_duration = _comparison_policy_duration(
        reference, start_s=0.0, requested_duration_s=0.9, tools=tools, fps=30
    )
    assert policy_duration == pytest.approx(0.8, abs=1 / 30)

    comparison = tmp_path / "comparison.mp4"
    command = _build_comparison_command(
        reference, remake, comparison, start_s=0.0, duration_s=policy_duration, tools=tools
    )
    assert any(
        "atrim=start=0.000000000:duration=0.800000000" in part
        for part in command
    )
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-ac") + 1] == "2"
    _run(command)
    qc = _validate_comparison_master(
        reference, comparison, expected_duration_s=policy_duration, tools=tools, fps=30
    )
    assert qc["tail_policy"] == "clamp_to_last_reference_video_frame"
    assert qc["width"] == 1440 and qc["height"] == 1280
    assert qc["fps"] == pytest.approx(30)
    assert qc["video_codec"] == "h264"
    assert qc["audio_codec"] == "aac"
    assert qc["audio_channels"] == 2
    assert qc["audio_source_match"] is True
    assert qc["audio_start_s"] == pytest.approx(0.0, abs=1 / 30)
    assert qc["freezedetect"] == {"detected": False, "duration_threshold_s": 0.366}

    frozen_tail = tmp_path / "frozen-tail.mp4"
    _run(_build_comparison_command(
        reference, remake, frozen_tail, start_s=0.0, duration_s=0.9, tools=tools
    ))
    with pytest.raises(PetReplicaCompositionError, match="(?i)comparison duration"):
        _validate_comparison_master(
            reference, frozen_tail, expected_duration_s=policy_duration, tools=tools, fps=30
        )


def test_manifest_paths_reject_external_and_symlink_artifacts(tmp_path):
    from factory.pet_replica_compose import (
        PetReplicaCompositionError,
        _validate_manifest_paths,
        build_replica_composition,
    )

    plan = replica_plan(tmp_path)
    manifest = build_replica_composition(
        plan,
        complete_selection(tmp_path, pilot_only=True),
        "pilot",
        validate_inputs=False,
    )
    external = tmp_path / "external.mp4"
    external.write_bytes(b"external")

    for field in (
        "manifest_path", "source_audio_path", "subtitle_path", "concat_list_path",
        "picture_path", "clean_master_path", "captioned_master_path", "master_path",
        "side_by_side_path", "final_qc_path",
        "current_pointer_path",
    ):
        with pytest.raises(PetReplicaCompositionError, match="output root"):
            _validate_manifest_paths(
                replace(manifest, **{field: external}), plan=plan, require_outputs=False
            )

    for shot_field in ("source_path", "normalized_path"):
        forged_shot = replace(manifest.shots[0], **{shot_field: external})
        with pytest.raises(PetReplicaCompositionError, match="output root"):
            _validate_manifest_paths(
                replace(manifest, shots=(forged_shot, *manifest.shots[1:])),
                plan=plan,
                require_outputs=False,
            )

    other_source = tmp_path / "other-reference.mp4"
    other_source.write_bytes(b"other")
    with pytest.raises(PetReplicaCompositionError, match="canonical source"):
        _validate_manifest_paths(
            replace(manifest, presentation_source_path=other_source),
            plan=plan,
            require_outputs=False,
        )

    linked = plan.output_root / "work" / "linked.mp4"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(external)
    with pytest.raises(PetReplicaCompositionError, match="symlink"):
        _validate_manifest_paths(
            replace(manifest, picture_path=linked), plan=plan, require_outputs=False
        )

    linked_root = tmp_path / "linked-output"
    linked_root.symlink_to(plan.output_root, target_is_directory=True)
    with pytest.raises(PetReplicaCompositionError, match="symlink"):
        _validate_manifest_paths(
            replace(manifest, output_root=linked_root), require_outputs=False
        )
