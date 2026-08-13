import json
import subprocess
import sys
from pathlib import Path

import pytest

from factory.character_assets import (
    build_character_asset_manifest,
    build_character_asset_source_template,
    write_character_asset_manifest,
)
from factory.novel_planner import plan_episode


PNG_SIGNATURE_BYTES = b"\x89PNG\r\n\x1a\nminimal"
PYTHON = sys.executable


def _write_manifest(path: Path, image_paths: dict[str, Path]) -> None:
    path.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": name, "reference_image": str(image_path)}
                    for name, image_path in image_paths.items()
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_manifest_with_source(path: Path, image_paths: dict[str, Path], asset_source: str) -> None:
    path.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": name, "reference_image": str(image_path), "asset_source": asset_source}
                    for name, image_path in image_paths.items()
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_build_character_asset_manifest_matches_by_character_name(tmp_path):
    lin = tmp_path / "lin.png"
    su = tmp_path / "su.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES)
    su.write_bytes(PNG_SIGNATURE_BYTES)
    manifest_path = tmp_path / "characters.json"
    _write_manifest(manifest_path, {"林澈": lin, "苏眠": su})
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_sample", target_shots=2)

    assets = build_character_asset_manifest(episode, manifest_path)

    assert assets["schema_version"] == "motion-comic-factory.character-assets.v1"
    assert assets["asset_ready"] is True
    assert [item["name"] for item in assets["characters"]] == ["林澈", "苏眠"]
    assert assets["characters"][0]["reference_image_path"] == str(lin.resolve())
    assert assets["characters"][0]["reference_image_exists"] is True
    assert assets["production_ready"] is False
    assert assets["characters"][0]["asset_source"] == ""
    assert assets["characters"][0]["production_ready"] is False


def test_build_character_asset_manifest_marks_confirmed_ai_sources_production_ready(tmp_path):
    lin = tmp_path / "lin.png"
    su = tmp_path / "su.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES)
    su.write_bytes(PNG_SIGNATURE_BYTES)
    manifest_path = tmp_path / "characters.json"
    _write_manifest_with_source(manifest_path, {"林澈": lin, "苏眠": su}, asset_source="user_generated_ai")
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_source", target_shots=2)

    assets = build_character_asset_manifest(episode, manifest_path)

    assert assets["asset_ready"] is True
    assert assets["production_ready"] is True
    assert all(item["asset_source"] == "user_generated_ai" for item in assets["characters"])
    assert all(item["production_ready"] is True for item in assets["characters"])


def test_build_character_asset_manifest_rejects_missing_supplied_reference(tmp_path):
    lin = tmp_path / "lin.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES)
    missing = tmp_path / "missing.png"
    manifest_path = tmp_path / "characters.json"
    _write_manifest(manifest_path, {"林澈": lin, "苏眠": missing})
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_missing", target_shots=2)

    with pytest.raises(ValueError, match="missing character reference image"):
        build_character_asset_manifest(episode, manifest_path)


def test_build_character_asset_manifest_rejects_invalid_image_bytes(tmp_path):
    lin = tmp_path / "lin.png"
    su = tmp_path / "su.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES)
    su.write_bytes(b"not an image")
    manifest_path = tmp_path / "characters.json"
    _write_manifest(manifest_path, {"林澈": lin, "苏眠": su})
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_invalid", target_shots=2)

    with pytest.raises(ValueError, match="invalid character reference image"):
        build_character_asset_manifest(episode, manifest_path)


def test_write_character_asset_manifest_outputs_template_without_source(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_template", target_shots=2)

    output = write_character_asset_manifest(episode, None, tmp_path)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["asset_ready"] is False
    assert data["characters"][0]["reference_image_path"] == ""
    assert data["characters"][0]["reference_image_exists"] is False


def test_build_character_asset_source_template_lists_episode_characters():
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "asset_template", target_shots=2)

    template = build_character_asset_source_template(episode)

    assert template["schema_version"] == "motion-comic-factory.character-assets-source.v1"
    assert template["project_id"] == "asset_template"
    assert template["characters"] == [
        {"character_id": episode.characters[0].id, "name": "林澈", "reference_image": "", "asset_source": ""},
        {"character_id": episode.characters[1].id, "name": "苏眠", "reference_image": "", "asset_source": ""},
    ]


def test_cli_character_assets_template_writes_fillable_manifest(tmp_path):
    output = tmp_path / "character_assets.template.json"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-template",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["character_assets_template"] == str(output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [item["name"] for item in data["characters"]] == ["林澈", "苏眠"]
    assert all(item["reference_image"] == "" for item in data["characters"])
    assert all(item["asset_source"] == "" for item in data["characters"])
