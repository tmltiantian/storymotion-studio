from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from factory.novel_planner import plan_episode
from factory.openmontage_adapter import load_config
from factory.video_handoff import build_video_handoff, write_video_handoff


def test_video_handoff_is_provider_neutral_and_contains_generation_jobs() -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_sample",
        target_shots=2,
    )

    handoff = build_video_handoff(episode, config)

    assert handoff["schema_version"] == "motion-comic-factory.video-handoff.v1"
    assert "lumenx" not in handoff
    assert "api_plan" not in handoff
    assert len(handoff["characters"]) == 2
    assert len(handoff["shots"]) == 2
    assert handoff["shots"][0]["index"] == 1
    assert handoff["shots"][0]["video_prompt"]
    assert handoff["shots"][0]["duration_seconds"] > 0


def test_video_handoff_binds_character_reference_images(tmp_path: Path) -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_refs",
        target_shots=2,
    )
    references = []
    characters = []
    for index, character in enumerate(episode.characters):
        image = tmp_path / f"role-{index}.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nrole")
        references.append(image)
        characters.append(
            {
                "character_id": character.id,
                "name": character.name,
                "reference_image_path": str(image),
                "reference_image_exists": True,
                "production_ready": True,
            }
        )

    handoff = build_video_handoff(
        episode,
        config,
        character_assets={"production_ready": True, "characters": characters},
    )

    assert handoff["characters"][0]["reference_image_path"] == str(references[0])
    assert handoff["characters"][0]["production_ready"] is True
    assert handoff["shots"][0]["character_ids"] == [episode.characters[0].id]


def test_write_video_handoff_uses_stage_owned_filename(tmp_path: Path) -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_file",
        target_shots=2,
    )

    output = write_video_handoff(episode, config, tmp_path)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert output == tmp_path / "video_handoff.json"
    assert payload["project_id"] == "handoff_file"


def test_minimax_handoff_compiles_official_h3_reference_prompt(tmp_path: Path) -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_h3",
        target_shots=2,
    )
    characters = []
    for index, character in enumerate(episode.characters):
        image = tmp_path / f"role-{index}.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nrole")
        characters.append(
            {
                "character_id": character.id,
                "name": character.name,
                "reference_image_path": str(image),
                "reference_image_exists": True,
                "production_ready": True,
            }
        )

    handoff = build_video_handoff(
        episode,
        config,
        character_assets={"production_ready": True, "characters": characters},
        process_env={
            "VIDEO_PROVIDER": "minimax",
            "MINIMAX_API_KEY": "test-secret",
            "ENABLE_MINIMAX_VIDEO": "1",
            "MINIMAX_VIDEO_MODEL": "MiniMax-H3",
        },
    )

    prompt = handoff["shots"][0]["video_prompt"]
    assert prompt.startswith("subject_definitions:")
    assert "detailed_description:" in prompt
    assert "overall_soundscape:" in prompt
    assert "non_diegetic_music:" in prompt


def test_gateway_handoff_keeps_existing_flat_prompt_contract() -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_gateway",
        target_shots=2,
    )

    handoff = build_video_handoff(
        episode,
        config,
        process_env={
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "test-secret",
            "GATEWAY_VIDEO_MODEL": "doubao-seedance-2-0",
        },
    )

    first = episode.shots[0]
    assert handoff["shots"][0]["video_prompt"] == (
        f"{first.visual_prompt} 运镜：{first.camera}"
    )
    assert "integrated_multimodal_description:" not in handoff["shots"][0][
        "video_prompt"
    ]


def test_handoff_uses_effective_provider_model_override() -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "handoff_effective_model",
        target_shots=2,
    )

    handoff = build_video_handoff(
        episode,
        config,
        process_env={
            "VIDEO_PROVIDER": "gateway",
            "GATEWAY_API_KEY": "test-secret",
            "GATEWAY_VIDEO_MODEL": "doubao-seedance-2-0",
        },
        video_provider="minimax",
        video_model="MiniMax-H3",
    )

    assert handoff["video_provider"] == "minimax"
    assert handoff["video_model"] == "MiniMax-H3"
    assert handoff["shots"][0]["video_prompt"].startswith(
        "integrated_multimodal_description:"
    )


def test_handoff_uses_explicit_character_membership_without_name_guessing() -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode("林澈推开门。苏眠回头。", "membership", target_shots=2)
    selected = episode.characters[1]
    episode.shots[0] = replace(
        episode.shots[0],
        action=f"{episode.characters[0].name}留在画外",
        visual_prompt=f"只有{selected.name}站在门边",
        character_ids=[selected.id],
        dialogue=[],
    )

    handoff = build_video_handoff(episode, config)

    assert handoff["shots"][0]["character_ids"] == [selected.id]


def test_handoff_preserves_character_free_insert() -> None:
    config = load_config("config/factory.config.json")
    episode = plan_episode("林澈推开门。苏眠回头。", "insert", target_shots=2)
    episode.shots[0] = replace(
        episode.shots[0],
        action="手机屏幕亮起",
        visual_prompt="只有桌面和手机",
        character_ids=[],
        dialogue=[],
    )

    handoff = build_video_handoff(episode, config)

    assert handoff["shots"][0]["character_ids"] == []
