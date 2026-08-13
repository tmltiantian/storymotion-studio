import json
from pathlib import Path

from factory.novel_planner import plan_episode
from factory.openmontage_adapter import (
    build_openmontage_package,
    load_config,
    validate_openmontage_package,
    write_openmontage_package,
)


def test_openmontage_package_uses_portable_optional_path():
    config = load_config("config/factory.config.json")
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "adapter_sample", target_shots=2)

    package = build_openmontage_package(episode, config)

    assert package["openmontage_path"] == "external/OpenMontage"
    assert package["render_runtime"] == "ffmpeg"
    assert package["openmontage_available"] is False
    assert package["target"]["resolution"] == "1080x1920"
    assert package["target"]["motion_cadence_fps"] == 12
    assert len(package["timeline"]) == 2
    assert Path(package["factory_workspace"]).exists()
    assert validate_openmontage_package(package) == []


def test_openmontage_package_includes_character_assets(tmp_path):
    config = load_config("config/factory.config.json")
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "adapter_assets", target_shots=2)
    ref = tmp_path / "lin.png"
    ref.write_bytes(b"png")
    character_assets = {
        "asset_ready": True,
        "characters": [
            {
                "character_id": episode.characters[0].id,
                "name": episode.characters[0].name,
                "reference_image_path": str(ref),
                "reference_image_exists": True,
            }
        ],
    }

    package = build_openmontage_package(episode, config, character_assets=character_assets)

    assert package["character_assets"]["asset_ready"] is True
    assert package["characters"][0]["reference_image_path"] == str(ref)


def test_openmontage_package_validator_rejects_missing_timeline():
    package = {
        "schema_version": "motion-comic-factory.openmontage.v1",
        "project_id": "bad",
        "openmontage_available": True,
        "render_runtime": "remotion",
        "target": {
            "final_video": "/tmp/final.mp4",
            "subtitle_srt": "/tmp/subtitles.srt",
            "audio_mix": "/tmp/audio.wav",
        },
        "timeline": [],
    }

    errors = validate_openmontage_package(package)
    assert "timeline must contain at least one shot" in errors


def test_openmontage_package_can_be_owned_by_a_pipeline_stage(tmp_path):
    config = load_config("config/factory.config.json")
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "stage_owned",
        target_shots=2,
    )
    stage_dir = tmp_path / "stages" / "video"

    package_path = write_openmontage_package(
        episode,
        config,
        run_dir=stage_dir,
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert package_path == stage_dir / "openmontage_package.json"
    assert package["target"]["final_video"] == str(stage_dir / "final.mp4")
    assert package["timeline"][0]["expected_assets"]["video_clip"].startswith(
        str(stage_dir / "clips")
    )
