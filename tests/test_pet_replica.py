import hashlib
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from factory.pet_replica import (
    PILOT_END_S,
    REFERENCE_DURATION_S,
    build_pet_replica_plan,
    validate_pet_replica_plan,
    write_pet_replica_plan,
)


def test_reference_replica_plan_is_source_locked(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    plan = build_pet_replica_plan(source, tmp_path / "output")

    assert plan.duration_s == REFERENCE_DURATION_S == pytest.approx(77.229569)
    assert plan.pilot_end_s == PILOT_END_S == pytest.approx(12.3)
    assert plan.width == 720
    assert plan.height == 1280
    assert plan.fps == 30
    assert plan.characters["source_orange_cat"].target_name == "奶糖"
    assert plan.characters["source_tabby_cat"].target_name == "豆包"
    assert plan.characters["source_woman"].target_name == "原创女主"
    assert plan.source_video == source.resolve()
    assert plan.output_root == (tmp_path / "output").resolve()


def test_plan_rejects_non_contiguous_or_out_of_bounds_shots(tmp_path):
    plan = build_pet_replica_plan(
        tmp_path / "reference.mp4",
        tmp_path / "output",
    )
    broken = replace(
        plan,
        shots=(
            plan.shots[0],
            replace(
                plan.shots[1],
                start_s=plan.shots[1].start_s + 0.1,
            ),
            *plan.shots[2:],
        ),
    )
    with pytest.raises(ValueError, match="contiguous"):
        validate_pet_replica_plan(broken)


@pytest.mark.parametrize(
    ("role", "changes", "message"),
    (
        (
            "source_orange_cat",
            {"target_name": "错误角色"},
            "target name",
        ),
        (
            "source_tabby_cat",
            {"source_role": "source_orange_cat"},
            "source role",
        ),
        (
            "source_woman",
            {"identity_rule": "Reuse the source woman's identity."},
            "identity rule",
        ),
    ),
)
def test_plan_rejects_altered_character_contract(tmp_path, role, changes, message):
    plan = build_pet_replica_plan(
        tmp_path / "reference.mp4",
        tmp_path / "output",
    )
    characters = dict(plan.characters)
    characters[role] = replace(characters[role], **changes)
    broken = replace(plan, characters=MappingProxyType(characters))

    with pytest.raises(ValueError, match=message):
        validate_pet_replica_plan(broken)


def test_plan_rejects_mutable_character_mapping(tmp_path):
    plan = build_pet_replica_plan(
        tmp_path / "reference.mp4",
        tmp_path / "output",
    )
    broken = replace(plan, characters=dict(plan.characters))

    with pytest.raises(ValueError, match="immutable"):
        validate_pet_replica_plan(broken)


def test_plan_accepts_timestamps_in_the_same_source_frames(tmp_path):
    plan = build_pet_replica_plan(
        tmp_path / "reference.mp4",
        tmp_path / "output",
    )
    shifted_boundary = plan.shots[0].end_s + 0.0001
    frame_equivalent = replace(
        plan,
        shots=(
            replace(plan.shots[0], end_s=shifted_boundary),
            replace(plan.shots[1], start_s=shifted_boundary),
            *plan.shots[2:],
        ),
    )

    validate_pet_replica_plan(frame_equivalent)


def test_write_plan_binds_source_hash_without_source_url_tokens(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    plan = build_pet_replica_plan(source, tmp_path / "output")

    reference_manifest, timeline, contract = write_pet_replica_plan(plan)

    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest_payload = json.loads(reference_manifest.read_text(encoding="utf-8"))
    timeline_payload = json.loads(timeline.read_text(encoding="utf-8"))
    contract_text = contract.read_text(encoding="utf-8")

    assert (reference_manifest, timeline, contract) == (
        plan.output_root / "reference" / "reference_manifest.json",
        plan.output_root / "reference" / "shot_timeline.json",
        plan.output_root / "story_contract.md",
    )
    assert manifest_payload["source_sha256"] == source_sha256
    assert timeline_payload["source_sha256"] == source_sha256
    assert manifest_payload["media_contract"] == {
        "duration_s": 77.229569,
        "width": 720,
        "height": 1280,
        "fps": 30,
    }
    assert timeline_payload["media_contract"] == manifest_payload["media_contract"]
    assert timeline_payload["shots"][0]["shot_id"] == "R001"
    assert source_sha256 in contract_text
    assert "720x1280" in contract_text
    assert "30 fps" in contract_text
    assert "source-url-token" not in (
        reference_manifest.read_text(encoding="utf-8")
        + timeline.read_text(encoding="utf-8")
        + contract_text
    )
