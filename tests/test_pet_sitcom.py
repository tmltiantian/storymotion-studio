from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from factory.pet_sitcom import (
    PetSitcomError,
    PetSitcomPlan,
    build_pet_sitcom_plan,
    write_pet_sitcom_plan,
)


@pytest.fixture
def config() -> dict[str, str]:
    return {
        "apiKey": "must-not-be-serialized",
        "runsDir": "/unused-for-this-fixed-plan",
        "outputDir": "/unused-for-this-fixed-plan",
    }


def test_plan_uses_ten_variable_duration_shots(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")

    assert [character.slug for character in plan.characters] == [
        "naitang",
        "doubao",
    ]
    assert [scene.slug for scene in plan.scenes] == [
        "living_room",
        "kitchen",
    ]
    assert plan.duration_seconds == 54.0
    assert [shot.duration_seconds for shot in plan.shots] == [
        5.2, 3.4, 6.4, 4.2, 7.3, 6.1, 4.8, 7.0, 5.5, 4.1
    ]
    assert [shot.generation_duration_seconds for shot in plan.shots] == [
        6, 4, 7, 5, 8, 7, 5, 8, 6, 5
    ]
    assert sum(shot.duration_seconds for shot in plan.shots) == 54.0
    assert plan.title == "冻干到底是谁偷吃的？"


def test_plan_combines_dialogue_into_the_approved_ten_shots(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")

    assert [(shot.shot_id, shot.speaker, shot.dialogue) for shot in plan.shots] == [
        ("shot_01", "owner", "谁把新开的冻干吃完了？"),
        ("shot_02", None, ""),
        ("shot_03", "naitang", "我昨晚一直在睡觉。豆包半夜去过厨房，我听见了。"),
        ("shot_04", "doubao", "我去喝水。"),
        ("shot_05", "doubao", "倒是你，回来时胡子上有一股鸡肉味。"),
        ("shot_06", "owner", "监控只拍到一条尾巴。"),
        ("shot_07", None, ""),
        ("shot_08", "naitang", "橘色尾巴那么多，不能因为颜色就怀疑一只无辜的小猫。"),
        ("shot_09", "doubao", "那你嘴边这个是什么？"),
        ("shot_10", "naitang", "证据也可能是后来粘上去的。"),
    ]
    assert all(shot.generate_audio is True for shot in plan.shots)
    assert "audio_manifest_path" in PetSitcomPlan.__annotations__
    assert "audio_probe_path" in PetSitcomPlan.__annotations__
    assert "audio_probe_review_path" in PetSitcomPlan.__annotations__


def test_plan_encodes_non_linear_replay_return_dependencies(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")
    dependencies = {
        shot.shot_id: shot.continuity_source_ids for shot in plan.shots
    }

    assert dependencies == {
        "shot_01": (),
        "shot_02": ("shot_01",),
        "shot_03": ("shot_02",),
        "shot_04": ("shot_03",),
        "shot_05": ("shot_04",),
        "shot_06": ("shot_05",),
        "shot_07": ("shot_05", "shot_06"),
        "shot_08": ("shot_07",),
        "shot_09": ("shot_08",),
        "shot_10": ("shot_09",),
    }
    assert plan.shots[6].transition == "match_tail_right_to_left_with_audio_l_cut"
    assert all(shot.start_state for shot in plan.shots)
    assert all(shot.end_state for shot in plan.shots)
    assert {
        shot.shot_id: shot.dialogue_offset_seconds
        for shot in plan.shots
        if shot.speaker is not None
    } == {
        "shot_01": 0.55,
        "shot_03": 0.55,
        "shot_04": 0.65,
        "shot_05": 0.55,
        "shot_06": -0.20,
        "shot_08": 0.55,
        "shot_09": 2.55,
        "shot_10": 0.75,
    }


def test_plan_is_immutable_and_isolated_under_desktop_output(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet_case")

    assert plan.output_dir == (tmp_path / "pet_case").resolve()
    assert plan.release_output.name == "冻干到底是谁偷吃的_发布版.mp4"
    assert plan.clean_output.name == "冻干到底是谁偷吃的_清洁版.mp4"
    assert all(
        path == plan.output_dir or plan.output_dir in path.parents
        for path in plan.all_output_paths()
    )
    with pytest.raises(FrozenInstanceError):
        plan.title = "changed"
    with pytest.raises(FrozenInstanceError):
        plan.shots[0].dialogue = "changed"


def test_role_and_scene_prompts_lock_the_approved_identity_and_home(config):
    plan = build_pet_sitcom_plan(config)
    naitang, doubao = plan.characters
    living_room, kitchen = plan.scenes

    for phrase in (
        "text-free triptych",
        "photorealistic orange-and-white short-haired cat",
        "front portrait",
        "three-quarter head-and-body",
        "full body",
        "round face",
        "amber eyes",
        "slightly round body",
        "symmetrical flat white muzzle",
        "white chest",
        "no isolated white blob",
        "identical natural markings in all three panels",
        "unchanged markings",
        "neutral studio light",
        "plain warm-gray background",
        "no collar",
        "no accessories",
        "no labels",
        "no extra animal",
    ):
        assert phrase in naitang.prompt

    for phrase in (
        "text-free triptych",
        "photorealistic black-and-white tuxedo cat",
        "narrower face",
        "green eyes",
        "slimmer body",
        "continuous white nose-to-chin marking",
        "white chest and paws",
        "unchanged markings",
        "neutral studio light",
        "plain warm-gray background",
        "no collar",
        "no accessories",
        "no labels",
        "no extra animal",
    ):
        assert phrase in doubao.prompt

    for scene in (living_room, kitchen):
        for phrase in (
            "warm natural daylight from frame left",
            "honey-colored wood floor",
            "light neutral furniture",
            "fixed home layout",
        ):
            assert phrase in scene.prompt
    for phrase in (
        "no person",
        "no animal",
        "no silhouette",
        "no text",
        "no logo",
        "no watermark",
        "no food package",
        "no mirror",
    ):
        assert phrase in living_room.prompt
    for phrase in (
        "plain non-reflective wall",
        "no wall mirror",
        "no reflective wall decor",
        "no snack bag",
        "full-bleed interior image",
        "no phone frame",
        "no device border",
    ):
        assert phrase in living_room.prompt
    assert "natural candid interior photography" in living_room.prompt
    assert "phone" not in kitchen.prompt
    assert "natural candid interior photography" in kitchen.prompt
    assert "unobstructed full-bleed interior image" in kitchen.prompt
    assert "clear open floor" in kitchen.prompt


def test_shot_prompts_lock_reference_order_and_spatial_continuity(config):
    plan = build_pet_sitcom_plan(config)

    shared_constraints = (
        "Reference 1 is Naitang immutable character sheet",
        "Reference 2 is Doubao immutable character sheet",
        "Reference 3 is the current empty scene anchor",
        "Reference 4, when present, is the previous selected ending frame",
        "two distinct cats",
        "unchanged markings/eyes/body proportions",
        "realistic feline anatomy and weight",
        "grounded paws",
        "natural whiskers/ears/tails",
        "restrained jaw movement",
        "only the designated speaker moving their mouth as speech",
        "stable camera",
        "no digital zoom",
        "no optical-flow look",
        "no floating",
        "no duplicated body parts",
        "no extra animal",
        "no human face",
        "no text",
        "no subtitle",
        "no watermark",
        "Naitang remains screen-left and looks right. Doubao remains screen-right and looks left. The owner remains off camera. Preserve the 180-degree axis, kitchen doorway geometry, warm daylight from frame left, bag position, tail position, mirror position, and each cat's pose from the declared start state.",
    )
    for shot in plan.shots:
        for phrase in shared_constraints:
            assert phrase in shot.base_prompt
        assert "first 0.20 seconds without spoken words" in shot.base_prompt

    for shot in plan.shots:
        if shot.speaker is not None:
            assert "final 0.30 seconds without spoken words" in shot.base_prompt
            assert "final 0.20 seconds without spoken words" not in shot.base_prompt

    for owner_shot in (plan.shots[0], plan.shots[5]):
        assert "Do not generate native human or animal speech" in owner_shot.base_prompt
        assert "Doubao Seed-TTS overlay added later" in owner_shot.base_prompt
        assert "room tone, prop, and natural audio" in owner_shot.base_prompt

    for silent_shot in (plan.shots[1], plan.shots[6]):
        assert "Do not generate speech or vocalization" in silent_shot.base_prompt
        assert "room tone, prop, and natural audio" in silent_shot.base_prompt

    assert len({shot.action for shot in plan.shots}) == 10
    tail_replay = plan.shots[6]
    assert "only one orange tail" in tail_replay.base_prompt
    assert "no full cat" in tail_replay.base_prompt
    assert "no extra animal" in tail_replay.base_prompt
    assert "neither cat needs to be fully visible in every shot" in tail_replay.base_prompt

    for shot in plan.shots[7:]:
        assert "crumbs continuously visible" in shot.base_prompt
    for shot in plan.shots[8:]:
        assert "mirror remains continuously visible" in shot.base_prompt


def test_write_plan_is_atomic_deterministic_and_has_complete_contract(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet_case")

    written = write_pet_sitcom_plan(plan)
    first_payload = written.read_bytes()
    assert write_pet_sitcom_plan(plan) == written
    assert written.read_bytes() == first_payload
    assert not list(plan.output_dir.glob(f".{plan.plan_path.name}.*.tmp"))

    payload = json.loads(first_payload)
    assert payload["schema_version"] == "motion-comic-factory.pet-sitcom-plan.v2"
    assert payload["duration_seconds"] == 54.0
    assert payload["models"] == {
        "image": "doubao-seedream-4-5",
        "video": "doubao-seedance-2-0",
        "owner_audio": "seed-tts-2.0",
    }
    assert payload["providers"] == {
        "image": "gateway",
        "video": "gateway",
        "owner_audio": "doubao",
    }
    assert payload["video_defaults"] == {
        "ratio": "9:16",
        "resolution": "1080x1920",
    }
    assert payload["reference_order"] == [
        "Naitang immutable character sheet",
        "Doubao immutable character sheet",
        "current empty scene anchor",
        "previous selected ending frame when the shot index is greater than one",
    ]
    assert [shot["dialogue"] for shot in payload["shots"]] == [
        shot.dialogue for shot in plan.shots
    ]
    assert all(shot["generate_audio"] is True for shot in payload["shots"])
    assert all(shot["native_audio"] is True for shot in payload["shots"])
    for shot in payload["shots"]:
        assert {
            "generation_duration_seconds",
            "duration_seconds",
            "dialogue_offset_seconds",
            "transition",
            "start_state",
            "end_state",
            "continuity_source_ids",
        } <= shot.keys()
    expected_continuity_paths = {
        plan.output_dir / "continuity" / f"{source_id}_last.png"
        for shot in plan.shots
        for source_id in shot.continuity_source_ids
    }
    assert expected_continuity_paths <= set(plan.all_output_paths())
    serialized = first_payload.decode("utf-8")
    assert config["apiKey"] not in serialized
    assert "api_key" not in serialized.lower()
    assert "authorization" not in serialized.lower()


def test_build_and_write_reject_symlinks_and_paths_outside_the_plan(config, tmp_path):
    output_dir = tmp_path / "pet_case"
    output_dir.parent.mkdir(exist_ok=True)
    output_dir.symlink_to(tmp_path / "outside", target_is_directory=True)

    with pytest.raises(PetSitcomError, match="symlink"):
        build_pet_sitcom_plan(config, output_dir=output_dir)

    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "safe_case")
    escaped = tmp_path / "escaped.json"
    with pytest.raises(PetSitcomError, match="plan_path|output_dir|inside"):
        write_pet_sitcom_plan(replace(plan, plan_path=escaped))


@pytest.mark.parametrize(
    ("source_id", "message"),
    [
        ("unknown", "unknown shot"),
        ("shot_02", "cannot reference itself"),
        ("shot_03", "cannot reference a forward shot"),
    ],
)
def test_write_plan_rejects_invalid_continuity_dependencies(
    config, tmp_path, source_id, message
):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet_case")
    invalid_shot = replace(plan.shots[1], continuity_source_ids=(source_id,))
    invalid_plan = replace(plan, shots=(plan.shots[0], invalid_shot, *plan.shots[2:]))

    with pytest.raises(PetSitcomError, match=message):
        write_pet_sitcom_plan(invalid_plan)
