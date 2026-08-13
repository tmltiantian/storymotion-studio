from __future__ import annotations

import json
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
