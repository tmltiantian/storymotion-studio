from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from factory import pet_replica_lipsync as lipsync_module
from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_generation import ReplicaCandidate
from factory.pet_replica_lipsync import (
    PetReplicaLipSyncError,
    mouth_blend_mask,
    mouth_blend_temporal_alpha,
    promote_replica_direct_timing_candidate,
    promote_replica_mouth_blend_candidate,
    promote_replica_lipsync_candidate,
    promote_replica_visual_timing_candidate,
    validate_replica_lipsync_provenance,
    validate_replica_postprocess_provenance,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(tmp_path: Path) -> tuple[object, ReplicaCandidate, Path, Path, Path]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    plan = build_pet_replica_plan(source, tmp_path / "output")
    video = plan.output_root / "shots/R001/candidate_03.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"raw provider video")
    provenance = video.with_suffix(".provenance.json")
    provenance.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-generation.v1",
                "shot_id": "R001",
                "candidate_number": 3,
                "source_sha256": _sha(source),
                "source_window": {"start_s": 0.0, "end_s": plan.shots[0].end_s},
                "editorial_duration_s": plan.shots[0].duration_s,
                "drive_audio_sha256": "placeholder",
                "output_path": "shots/R001/candidate_03.mp4",
                "output_sha256": _sha(video),
                "signature": {"test": True},
            }
        ),
        encoding="utf-8",
    )
    gateway = video.with_suffix(".gateway.json")
    gateway.write_text('{"success": true}', encoding="utf-8")
    audio = plan.output_root / "audio/drive/R001.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"drive audio")
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["drive_audio_sha256"] = _sha(audio)
    provenance.write_text(json.dumps(payload), encoding="utf-8")
    candidate = ReplicaCandidate(
        "R001",
        3,
        video,
        provenance,
        gateway,
        plan.shots[0].duration_s,
        4,
        _sha(video),
    )
    lipsynced = tmp_path / "lipsynced.mp4"
    lipsynced.write_bytes(b"postprocessed video")
    model_root = tmp_path / "Wav2Lip"
    checkpoint = model_root / "checkpoints/wav2lip_gan.pth"
    detector = model_root / "face_detection/detection/sfd/s3fd.pth"
    checkpoint.parent.mkdir(parents=True)
    detector.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"gan weights")
    detector.write_bytes(b"face detector")
    return plan, candidate, lipsynced, checkpoint, detector


def test_promote_lipsync_archives_provider_output_and_binds_all_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )

    promoted = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="a" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )

    assert promoted.video_path.read_bytes() == b"postprocessed video"
    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    raw = plan.output_root / record["source_candidate_path"]
    raw_provenance = plan.output_root / record["source_provenance_path"]
    assert raw.read_bytes() == b"raw provider video"
    assert json.loads(raw_provenance.read_text(encoding="utf-8"))["output_sha256"] == _sha(raw)
    assert provenance["output_sha256"] == promoted.output_sha256 == _sha(promoted.video_path)
    assert record["output_sha256"] == promoted.output_sha256
    assert record["drive_audio_sha256"] == _sha(plan.output_root / "audio/drive/R001.wav")
    assert record["checkpoint_sha256"] == _sha(checkpoint)
    assert record["face_detector_sha256"] == _sha(detector)
    assert validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )

    again = promote_replica_lipsync_candidate(
        plan,
        promoted,
        lipsynced,
        repository_commit="a" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    assert again == promoted


def test_promote_visual_timing_archives_provider_output_and_records_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, _lipsynced, _checkpoint, _detector = _candidate(tmp_path)
    timed = tmp_path / "cat-mouth-timed.mp4"
    timed.write_bytes(b"visually timed cat mouth")
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_visual_timing_media",
        lambda *_args: None,
    )

    promoted = promote_replica_visual_timing_candidate(
        plan,
        candidate,
        timed,
        ffmpeg_version="ffmpeg version test",
        source_start_s=1.25,
        forward_end_s=0.4,
    )

    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    assert record["engine"] == "ffmpeg_visual_timing"
    assert record["model"] == "forward_then_reverse_close"
    assert record["source_start_s"] == 1.25
    assert record["forward_end_s"] == 0.4
    assert (plan.output_root / record["source_candidate_path"]).read_bytes() == b"raw provider video"
    assert validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )
    assert not validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )
    record["source_start_s"] = -0.01
    assert not validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_promote_direct_timing_records_continuous_source_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, _lipsynced, _checkpoint, _detector = _candidate(tmp_path)
    timed = tmp_path / "continuous-window.mp4"
    timed.write_bytes(b"continuous source window")
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_visual_timing_media",
        lambda *_args: None,
    )

    promoted = promote_replica_direct_timing_candidate(
        plan,
        candidate,
        timed,
        ffmpeg_version="ffmpeg version test",
        source_start_s=0.4,
        source_duration_s=0.8,
    )

    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    assert record["engine"] == "ffmpeg_visual_timing"
    assert record["model"] == "continuous_source_window"
    assert record["filter_contract"] == (
        "trim[start,start+duration),retime_to_editorial_duration"
    )
    assert record["source_start_s"] == 0.4
    assert record["source_duration_s"] == 0.8
    assert validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )
    record["source_duration_s"] = -0.01
    assert not validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_promote_piecewise_timing_records_monotonic_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, _lipsynced, _checkpoint, _detector = _candidate(tmp_path)
    timed = tmp_path / "piecewise-window.mp4"
    timed.write_bytes(b"piecewise monotonic window")
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_visual_timing_media",
        lambda *_args: None,
    )
    editorial = candidate.editorial_duration_s
    segments = (
        {
            "source_start_s": 0.0,
            "source_duration_s": 0.7,
            "output_duration_s": editorial * 0.6,
        },
        {
            "source_start_s": 0.7,
            "source_duration_s": 0.8,
            "output_duration_s": editorial * 0.4,
        },
    )

    promoted = lipsync_module.promote_replica_piecewise_timing_candidate(
        plan,
        candidate,
        timed,
        ffmpeg_version="ffmpeg version test",
        segments=segments,
    )

    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    assert record["engine"] == "ffmpeg_visual_timing"
    assert record["model"] == "piecewise_monotonic_retime"
    assert record["filter_contract"] == (
        "concat_monotonic_trim_segments,retime_to_segment_output_durations"
    )
    assert record["segments"] == list(segments)
    assert validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )
    record["segments"][1]["source_start_s"] = 0.6
    assert not validate_replica_postprocess_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_lipsync_provenance_records_horizontal_frame_transform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
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
        frame_transform="hflip",
    )

    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    assert record["frame_transform"] == "hflip"
    assert validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )

    record["frame_transform"] = "crop=480:852:20:80,scale=720:1280"
    assert validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_lipsync_provenance_fails_closed_after_raw_archive_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )
    promoted = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="b" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    provenance = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = provenance["postprocess"]
    (plan.output_root / record["source_candidate_path"]).write_bytes(b"tampered")

    assert not validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_lipsync_promotion_preserves_candidate_when_output_media_is_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    before_video = candidate.video_path.read_bytes()
    before_provenance = candidate.provenance_path.read_bytes()

    def reject(*_args):
        raise PetReplicaLipSyncError("invalid lip-sync media")

    monkeypatch.setattr("factory.pet_replica_lipsync._validate_lipsync_media", reject)
    with pytest.raises(PetReplicaLipSyncError, match="invalid lip-sync media"):
        promote_replica_lipsync_candidate(
            plan,
            candidate,
            lipsynced,
            repository_commit="c" * 40,
            checkpoint_path=checkpoint,
            face_detector_path=detector,
        )

    assert candidate.video_path.read_bytes() == before_video
    assert candidate.provenance_path.read_bytes() == before_provenance


def test_lipsync_replacement_keeps_original_provider_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )
    first = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="e" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    first_payload = json.loads(first.provenance_path.read_text(encoding="utf-8"))
    raw_path = plan.output_root / first_payload["postprocess"]["source_candidate_path"]
    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"corrected postprocessed video")

    replaced = promote_replica_lipsync_candidate(
        plan,
        first,
        replacement,
        repository_commit="e" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
        replace_existing=True,
    )

    assert replaced.video_path.read_bytes() == replacement.read_bytes()
    assert raw_path.read_bytes() == b"raw provider video"
    history = (
        plan.output_root
        / "shots/R001/postprocess/history"
        / f"{first.output_sha256}.mp4"
    )
    assert history.read_bytes() == lipsynced.read_bytes()
    payload = json.loads(replaced.provenance_path.read_text(encoding="utf-8"))
    assert payload["postprocess"]["source_candidate_path"] == str(
        raw_path.relative_to(plan.output_root)
    )
    assert payload["postprocess"]["output_sha256"] == replaced.output_sha256


def test_mouth_blend_mask_preserves_eyes_and_softens_mouth_boundary() -> None:
    face = np.array(
        [
            100,
            120,
            300,
            400,
            180,
            250,
            320,
            250,
            250,
            330,
            190,
            430,
            310,
            430,
            0.9,
        ],
        dtype=np.float32,
    )

    mask = mouth_blend_mask(face, width=720, height=1280)

    assert mask.shape == (1280, 720)
    assert mask.dtype == np.float32
    assert mask[250, 180] == 0
    assert mask[430, 250] > 0.85
    assert 0 < mask[370, 250] < mask[430, 250]
    assert float(mask.max()) <= 0.92


def test_mouth_blend_temporal_alpha_closes_only_final_three_frames() -> None:
    values = [
        mouth_blend_temporal_alpha(index, frame_count=10, tail_close_frames=3)
        for index in range(10)
    ]

    assert values[:7] == [1.0] * 7
    assert values[7:] == pytest.approx([2 / 3, 1 / 3, 0.0])
    assert mouth_blend_temporal_alpha(9, frame_count=10, tail_close_frames=0) == 1.0


def test_promote_mouth_blend_archives_full_face_lipsync_and_binds_detector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )
    full_face = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="f" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    blend_detector = tmp_path / "face_detection_yunet_2026may.onnx"
    blend_detector.write_bytes(b"yunet detector")
    blended = tmp_path / "mouth-blended.mp4"
    blended.write_bytes(b"mouth-only composite")

    promoted = promote_replica_mouth_blend_candidate(
        plan,
        full_face,
        blended,
        detector_path=blend_detector,
    )

    payload = json.loads(promoted.provenance_path.read_text(encoding="utf-8"))
    record = payload["postprocess"]
    blend = record["mouth_blend"]
    archived_lipsync = plan.output_root / blend["source_lipsync_path"]
    archived_detector = plan.output_root / blend["detector_path"]
    assert archived_lipsync.read_bytes() == lipsynced.read_bytes()
    assert archived_detector.read_bytes() == blend_detector.read_bytes()
    assert blend["source_lipsync_sha256"] == full_face.output_sha256
    assert blend["detector_sha256"] == _sha(blend_detector)
    assert promoted.video_path.read_bytes() == blended.read_bytes()
    assert validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )

    archived_lipsync.write_bytes(b"tampered")
    assert not validate_replica_lipsync_provenance(
        plan.output_root,
        record,
        candidate_sha256=promoted.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=record["drive_audio_sha256"],
    )


def test_replace_mouth_blend_records_tail_close_without_losing_full_face_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan, candidate, lipsynced, checkpoint, detector = _candidate(tmp_path)
    monkeypatch.setattr(
        "factory.pet_replica_lipsync._validate_lipsync_media", lambda *_args: None
    )
    full_face = promote_replica_lipsync_candidate(
        plan,
        candidate,
        lipsynced,
        repository_commit="1" * 40,
        checkpoint_path=checkpoint,
        face_detector_path=detector,
    )
    blend_detector = tmp_path / "face_detection_yunet_2026may.onnx"
    blend_detector.write_bytes(b"yunet detector")
    first_blend = tmp_path / "first-mouth-blend.mp4"
    first_blend.write_bytes(b"first mouth composite")
    promoted = promote_replica_mouth_blend_candidate(
        plan,
        full_face,
        first_blend,
        detector_path=blend_detector,
    )
    corrected = tmp_path / "tail-closed.mp4"
    corrected.write_bytes(b"tail closed composite")

    replaced = promote_replica_mouth_blend_candidate(
        plan,
        promoted,
        corrected,
        detector_path=blend_detector,
        tail_close_frames=3,
        replace_existing=True,
    )

    payload = json.loads(replaced.provenance_path.read_text(encoding="utf-8"))
    blend = payload["postprocess"]["mouth_blend"]
    assert blend["tail_close_frames"] == 3
    assert (plan.output_root / blend["source_lipsync_path"]).read_bytes() == lipsynced.read_bytes()
    assert validate_replica_lipsync_provenance(
        plan.output_root,
        payload["postprocess"],
        candidate_sha256=replaced.output_sha256,
        expected_candidate_path="shots/R001/candidate_03.mp4",
        drive_audio_sha256=payload["postprocess"]["drive_audio_sha256"],
    )
