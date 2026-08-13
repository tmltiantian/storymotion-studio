from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_generation import ReplicaCandidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replica_plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"source-video")
    return build_pet_replica_plan(source, tmp_path / "output")


def pilot_shot(tmp_path: Path):
    return replace(replica_plan(tmp_path).shots[0], action="cat turns toward woman")


def _source_evidence(plan, shot) -> None:
    source_sha256 = _sha256(plan.source_video)
    frames = []
    for label, color in (("start", "red"), ("middle", "green"), ("end", "blue")):
        path = plan.output_root / "reference" / "shots" / shot.shot_id / f"{label}.jpg"
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


def _reviewed_annotations(plan, *, speaker: str = "source_woman") -> None:
    shots = []
    for item in plan.shots:
        shots.append(
            {
                "shot_id": item.shot_id,
                "characters": ["source_woman"],
                "speaker": speaker if item.shot_id == plan.shots[0].shot_id else "",
                "scene_anchor_id": "scene_sofa",
                "location": "sofa",
                "framing": "medium framing",
                "action": "visible action",
                "subtitle": "",
                "source_audio": True,
                "manual_review_required": False,
            }
        )
    path = plan.output_root / "reference" / "shot_annotations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-annotations.v1",
                "shots": shots,
            }
        ),
        encoding="utf-8",
    )


def _replace_start_evidence_path(plan, *, escape: bool) -> None:
    manifest_path = plan.output_root / "reference" / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = next(
        item
        for item in manifest["frames"]
        if item["shot_id"] == "R001" and item["label"] == "start"
    )
    original = plan.output_root / record["image_path"]
    target = (
        plan.output_root.parent / "outside-source-start.jpg"
        if escape
        else plan.output_root / "reference" / "unrelated" / "renamed-start.jpg"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original.read_bytes())
    record["image_path"] = "../outside-source-start.jpg" if escape else str(target.relative_to(plan.output_root))
    record["image_sha256"] = _sha256(target)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def valid_candidate(tmp_path: Path, *, number: int = 1) -> ReplicaCandidate:
    plan = replica_plan(tmp_path)
    shot = plan.shots[0]
    _source_evidence(plan, shot)
    _reviewed_annotations(plan)
    video = plan.output_root / "shots" / shot.shot_id / f"candidate_{number:02d}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(f"candidate-{number}".encode("ascii"))
    audio = plan.output_root / "audio" / "drive" / f"{shot.shot_id}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"spoken drive audio")
    provenance = video.with_suffix(".provenance.json")
    provenance.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-generation.v1",
                "shot_id": shot.shot_id,
                "candidate_number": number,
                "editorial_duration_s": shot.duration_s,
                "provider_duration_s": 4,
                "source_window": {"start_s": shot.start_s, "end_s": shot.end_s},
                "source_sha256": _sha256(plan.source_video),
                "drive_audio_sha256": _sha256(audio),
                "output_sha256": _sha256(video),
                "output_path": str(video.relative_to(plan.output_root)),
            }
        ),
        encoding="utf-8",
    )
    gateway = video.with_suffix(".gateway.json")
    gateway.write_text("{}", encoding="utf-8")
    return ReplicaCandidate(
        shot_id=shot.shot_id,
        candidate_number=number,
        video_path=video,
        provenance_path=provenance,
        gateway_report_path=gateway,
        editorial_duration_s=shot.duration_s,
        generation_duration_s=4,
        output_sha256=_sha256(video),
    )


def fake_good_frames(_path: Path, timestamp_s: float) -> Image.Image:
    value = max(1, min(254, int(timestamp_s * 101) + 20))
    return Image.new("RGB", (72, 128), (value, 80, 160))


def fake_bad_frames(_path: Path, _timestamp_s: float) -> Image.Image:
    return Image.new("RGB", (72, 128), "white")


def fake_copied_frames(_path: Path, timestamp_s: float) -> Image.Image:
    if timestamp_s < 0.4:
        return Image.new("RGB", (72, 128), "red")
    if timestamp_s < 1.3:
        return Image.new("RGB", (72, 128), "green")
    return Image.new("RGB", (72, 128), "blue")


def fake_partially_copied_frames(_path: Path, timestamp_s: float) -> Image.Image:
    if timestamp_s == 0:
        return Image.new("RGB", (72, 128), "red")
    value = max(1, min(254, int(timestamp_s * 101) + 20))
    return Image.new("RGB", (72, 128), (value, 80, 160))


def test_audio_activity_uses_exact_wav_frame_duration(tmp_path: Path) -> None:
    from factory.pet_replica_review import _probe_audio_activity

    path = tmp_path / "non_centisecond.wav"
    frame_count = 65_600
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(b"\x10\x27" * frame_count)

    activity = _probe_audio_activity(path)

    assert activity["speech_start_s"] == 0
    assert activity["speech_end_s"] == pytest.approx(frame_count / 48_000)
    assert activity["speech_end_s"] < 1.37


def fake_black_cut_frames(_path: Path, timestamp_s: float) -> Image.Image:
    if timestamp_s == 0:
        return Image.new("RGB", (72, 128), "black")
    return Image.new("RGB", (72, 128), "white" if timestamp_s > 0.2 else "gray")


def fake_good_probe(path: Path):
    if path.suffix == ".wav":
        return {"speech_start_s": 0.0, "speech_end_s": 1.0}
    return {"duration_s": 4.0, "width": 720, "height": 1280, "fps": 30.0}


def fake_bad_probe(path: Path):
    if path.suffix == ".wav":
        return {"speech_start_s": 0.0, "speech_end_s": 1.0}
    return {"duration_s": 0.1, "width": 640, "height": 1280, "fps": 30.0}


def fake_rounding_probe(path: Path):
    if path.suffix == ".wav":
        return {"speech_start_s": 0.0, "speech_end_s": 1.7333335}
    return fake_good_probe(path)


def _manual_review() -> dict[str, object]:
    from factory.pet_replica_review import MANUAL_REVIEW_GATES

    return {
        **{gate: True for gate in MANUAL_REVIEW_GATES},
        "note": "Start frame shows the original woman and both cats in the planned sofa positions; the final frame keeps the phone on the table.",
    }


def test_review_rejects_wrong_dimensions_short_source_and_long_freeze(tmp_path):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    shot = plan.shots[0]
    candidate = valid_candidate(tmp_path)

    result = review_replica_candidate(
        plan=plan,
        shot=shot,
        candidate=candidate,
        frame_reader=fake_bad_frames,
        probe_runner=fake_bad_probe,
    )

    assert result.passed is False
    assert "resolution" in result.failures
    assert "shorter than editorial window" in result.failures
    assert "freeze" in result.failures
    unannotated = review_replica_candidate(
        plan, plan.shots[0], candidate, fake_bad_frames, fake_bad_probe
    )
    assert "freeze" in unannotated.failures


def test_selection_requires_all_manual_identity_and_action_gates(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate

    with pytest.raises(PetReplicaReviewError, match="manual gates"):
        approve_replica_candidate(
            candidate=valid_candidate(tmp_path),
            manual_review={
                "new_identity_match": True,
                "source_identity_absent": False,
            },
        )


def test_review_rejects_black_cut_and_source_frame_copy(tmp_path):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    shot = plan.shots[0]
    candidate = valid_candidate(tmp_path)
    unsafe = review_replica_candidate(plan, shot, candidate, fake_black_cut_frames, fake_good_probe)
    copied = review_replica_candidate(plan, shot, candidate, fake_copied_frames, fake_good_probe)
    partly_copied = review_replica_candidate(
        plan, shot, candidate, fake_partially_copied_frames, fake_good_probe
    )

    assert "black frame" in unsafe.failures
    assert "unexpected internal cut" in unsafe.failures
    assert "sampled source frame copy" in copied.failures
    assert "sampled source frame copy" in partly_copied.failures


def test_review_writes_bound_evidence_and_rejects_stale_candidate_bytes(tmp_path):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    shot = plan.shots[0]
    candidate = valid_candidate(tmp_path)
    result = review_replica_candidate(plan, shot, candidate, fake_good_frames, fake_good_probe)

    assert result.passed is True
    payload = json.loads(result.review_path.read_text(encoding="utf-8"))
    assert payload["candidate_sha256"] == candidate.output_sha256
    assert payload["evidence"]["contact_sheet"].endswith("contact_4x3.jpg")
    assert (plan.output_root / payload["evidence"]["comparison_sheet"]).is_file()
    assert (plan.output_root / payload["evidence"]["mouth_sheet"]).is_file()
    candidate.video_path.write_bytes(b"replaced after review")
    stale = review_replica_candidate(plan, shot, candidate, fake_good_frames, fake_good_probe)
    assert stale.passed is False
    assert "candidate bytes changed" in stale.failures


def test_approval_requires_current_passing_review_archives_failures_and_validates_selection(tmp_path):
    from factory.pet_replica_review import (
        PetReplicaReviewError,
        approve_replica_candidate,
        review_replica_candidate,
        validate_replica_selection,
    )

    plan = replica_plan(tmp_path)
    shot = plan.shots[0]
    failed = valid_candidate(tmp_path, number=1)
    review_replica_candidate(plan, shot, failed, fake_bad_frames, fake_bad_probe)
    candidate = valid_candidate(tmp_path, number=2)
    with pytest.raises(PetReplicaReviewError, match="automatic review"):
        approve_replica_candidate(plan, candidate, _manual_review())
    review_replica_candidate(plan, shot, candidate, fake_good_frames, fake_good_probe)
    selection = approve_replica_candidate(plan, candidate, _manual_review())

    assert selection == plan.output_root / "shots" / shot.shot_id / "selection.json"
    archive = plan.output_root / "rejected" / "reviews" / shot.shot_id / "candidate_01"
    assert len(list(archive.rglob("review.json"))) == 1
    with pytest.raises(PetReplicaReviewError, match="R002"):
        validate_replica_selection(plan, pilot_only=True)
    with pytest.raises(PetReplicaReviewError, match="visible evidence"):
        approve_replica_candidate(plan, candidate, {**_manual_review(), "note": "pass"})
    with pytest.raises(PetReplicaReviewError, match="visible evidence"):
        approve_replica_candidate(
            plan,
            candidate,
            {**_manual_review(), "note": "Candidate quality looks excellent today"},
        )


def test_speaking_contract_requires_current_audio_provenance_and_mouth_evidence(tmp_path):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    provenance = json.loads(candidate.provenance_path.read_text(encoding="utf-8"))
    del provenance["drive_audio_sha256"]
    candidate.provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)

    assert result.passed is False
    assert "drive audio provenance is missing" in result.failures
    assert (plan.output_root / result.evidence["mouth_sheet"]).is_file()


def test_speaking_activity_allows_sub_microsecond_duration_rounding(tmp_path):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)

    result = review_replica_candidate(
        plan,
        plan.shots[0],
        candidate,
        fake_good_frames,
        fake_rounding_probe,
    )

    assert result.passed is True


def test_approval_revalidates_provenance_source_and_generated_evidence(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate, review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    assert result.passed is True
    candidate.provenance_path.write_text("{}", encoding="utf-8")

    with pytest.raises(PetReplicaReviewError, match="automatic review"):
        approve_replica_candidate(plan, candidate, _manual_review())


def test_approval_revalidates_source_manifest_and_rendered_evidence(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate, review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    manifest = plan.output_root / "reference" / "evidence_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(PetReplicaReviewError, match="automatic review"):
        approve_replica_candidate(plan, candidate, _manual_review())

    _source_evidence(plan, plan.shots[0])
    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    (plan.output_root / result.evidence["contact_sheet"]).unlink()
    with pytest.raises(PetReplicaReviewError, match="automatic review"):
        approve_replica_candidate(plan, candidate, _manual_review())


def test_validation_rejects_substituted_identity_paths_and_symlinks(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate, review_replica_candidate, validate_replica_selection

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    assert review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe).passed
    selection_path = approve_replica_candidate(plan, candidate, _manual_review())
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["candidate_number"] = 99
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(PetReplicaReviewError, match="R001"):
        validate_replica_selection(plan, pilot_only=True)


def test_validation_rejects_substituted_review_identity_and_selection_symlink(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate, review_replica_candidate, validate_replica_selection

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    selection_path = approve_replica_candidate(plan, candidate, _manual_review())
    review_path = result.review_path
    record = json.loads(review_path.read_text(encoding="utf-8"))
    record["shot_id"] = "R999"
    review_path.write_text(json.dumps(record), encoding="utf-8")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["quality_review_sha256"] = _sha256(review_path)
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(PetReplicaReviewError, match="R001"):
        validate_replica_selection(plan, pilot_only=True)

    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    selection_path = approve_replica_candidate(plan, candidate, _manual_review())
    replacement = selection_path.with_name("selection-copy.json")
    replacement.write_bytes(selection_path.read_bytes())
    selection_path.unlink()
    selection_path.symlink_to(replacement)
    with pytest.raises(PetReplicaReviewError, match="R001"):
        validate_replica_selection(plan, pilot_only=True)

    selection["candidate_number"] = 1
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    target = candidate.video_path.with_name("substitute.mp4")
    target.write_bytes(candidate.video_path.read_bytes())
    candidate.video_path.unlink()
    candidate.video_path.symlink_to(target)
    with pytest.raises(PetReplicaReviewError, match="R001"):
        validate_replica_selection(plan, pilot_only=True)


def test_review_rejects_forged_shot_contract_and_one_frame_short_duration(tmp_path):
    from factory.pet_replica_review import PetReplicaReviewError, review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    forged = replace(plan.shots[0], end_s=plan.shots[0].end_s + 1 / 30)
    with pytest.raises(PetReplicaReviewError, match="canonical"):
        review_replica_candidate(plan, forged, candidate, fake_good_frames, fake_good_probe)

    def one_frame_short(path: Path):
        if path.suffix == ".wav":
            return fake_good_probe(path)
        return {
            "duration_s": plan.shots[0].duration_s - 1 / 30,
            "width": 720,
            "height": 1280,
            "fps": 30.0,
        }

    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, one_frame_short)
    assert "shorter than editorial window" in result.failures


def test_evidence_failure_invalidates_old_pass_and_archives_each_failed_attempt(tmp_path, monkeypatch):
    import factory.pet_replica_review as review

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    assert review.review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe).passed

    monkeypatch.setattr(review, "_write_sheet", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    result = review.review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)
    assert result.passed is False
    with pytest.raises(review.PetReplicaReviewError, match="automatic review"):
        review.approve_replica_candidate(plan, candidate, _manual_review())

    monkeypatch.undo()
    review.review_replica_candidate(plan, plan.shots[0], candidate, fake_bad_frames, fake_bad_probe)
    archives = list((plan.output_root / "rejected" / "reviews" / "R001" / "candidate_01").glob("*/attempt-*/review.json"))
    assert len(archives) >= 2
    assert any(path.parent.joinpath("evidence", "contact_4x3.jpg").is_file() for path in archives)


def test_manual_review_accepts_visible_unsegmented_chinese_note(tmp_path):
    from factory.pet_replica_review import approve_replica_candidate, review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    assert review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe).passed
    note = "起始画面人物和猫位置正确，结尾手机仍在桌上"
    assert approve_replica_candidate(plan, candidate, {**_manual_review(), "note": note}).is_file()


@pytest.mark.parametrize("escape", (False, True), ids=("renamed", "dotdot"))
def test_review_rejects_noncanonical_or_escaping_source_frame_path(tmp_path, escape):
    from factory.pet_replica_review import review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    _replace_start_evidence_path(plan, escape=escape)

    result = review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe)

    assert result.passed is False
    assert "source evidence is stale" in result.failures


@pytest.mark.parametrize("escape", (False, True), ids=("renamed", "dotdot"))
def test_approval_rejects_noncanonical_or_escaping_source_frame_path(tmp_path, escape):
    from factory.pet_replica_review import PetReplicaReviewError, approve_replica_candidate, review_replica_candidate

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    assert review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe).passed
    _replace_start_evidence_path(plan, escape=escape)

    with pytest.raises(PetReplicaReviewError, match="automatic review"):
        approve_replica_candidate(plan, candidate, _manual_review())


@pytest.mark.parametrize("escape", (False, True), ids=("renamed", "dotdot"))
def test_selection_validation_rejects_noncanonical_or_escaping_source_frame_path(tmp_path, escape):
    from factory.pet_replica_review import (
        PetReplicaReviewError,
        approve_replica_candidate,
        review_replica_candidate,
        validate_replica_selection,
    )

    plan = replica_plan(tmp_path)
    candidate = valid_candidate(tmp_path)
    assert review_replica_candidate(plan, plan.shots[0], candidate, fake_good_frames, fake_good_probe).passed
    approve_replica_candidate(plan, candidate, _manual_review())
    _replace_start_evidence_path(plan, escape=escape)

    with pytest.raises(PetReplicaReviewError, match="R001"):
        validate_replica_selection(plan, pilot_only=True)
