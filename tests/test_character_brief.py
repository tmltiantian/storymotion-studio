import json
import subprocess
import sys
from pathlib import Path

import pytest

import factory.character_brief as character_brief
from factory.character_brief import (
    build_confirmed_character_assets_manifest,
    build_character_assets_manifest_from_brief,
    build_character_assets_status_from_brief,
    build_character_generation_brief,
    build_reviewed_role_images_manifest_from_directory,
    build_reviewed_role_images_template_from_brief,
    install_character_references_from_manifest,
    write_reviewed_role_images_manifest_from_directory,
)
from factory.novel_planner import plan_episode


PNG_SIGNATURE_BYTES = b"\x89PNG\r\n\x1a\nminimal"
PYTHON = sys.executable


def test_build_character_generation_brief_contains_prompts_and_asset_targets():
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_sample", title="旧城来信", target_shots=2)

    brief = build_character_generation_brief(episode)

    assert brief["schema_version"] == "motion-comic-factory.character-generation-brief.v1"
    assert brief["project_id"] == "brief_sample"
    assert [item["name"] for item in brief["characters"]] == ["林澈", "苏眠"]
    first = brief["characters"][0]
    assert first["character_id"] == episode.characters[0].id
    assert "林澈" in first["positive_prompt"]
    assert episode.characters[0].visual_anchor in first["positive_prompt"]
    assert "low quality" in first["negative_prompt"]
    assert [item["asset_role"] for item in first["asset_requirements"]] == [
        "reference_image",
        "full_body",
        "turnaround_sheet",
    ]
    assert first["recommended_reference_image"].endswith("_reference.png")


def test_cli_character_brief_writes_generation_brief(tmp_path):
    output = tmp_path / "character_generation_brief.json"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
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
    assert payload["character_generation_brief"] == str(output)
    assert payload["character_count"] == 2
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [item["name"] for item in data["characters"]] == ["林澈", "苏眠"]
    assert data["character_assets_template"]["characters"][0]["reference_image"].endswith("_reference.png")


def test_build_reviewed_role_images_template_from_brief_keeps_source_paths_blank():
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "reviewed_template", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)

    template = build_reviewed_role_images_template_from_brief(
        brief,
        brief_path="/tmp/character_generation_brief.json",
        output_path="/tmp/reviewed_role_images.template.json",
    )

    assert template["schema_version"] == "motion-comic-factory.reviewed-role-images-template.v1"
    assert template["project_id"] == "reviewed_template"
    assert template["title"] == "旧城来信"
    assert template["filled_manifest_path"] == "/tmp/reviewed_role_images.json"
    assert "character-assets-install-references" in template["next_command"]
    assert "--source-manifest /tmp/reviewed_role_images.json" in template["next_command"]
    assert [item["name"] for item in template["characters"]] == ["林澈", "苏眠"]
    first = template["characters"][0]
    assert first["reference_image"] == ""
    assert first["target_reference_image"].endswith("_reference.png")
    assert "林澈" in first["positive_prompt"]
    assert "watermark" in first["negative_prompt"]


def test_cli_character_assets_reviewed_template_writes_fillable_source_manifest(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    template_path = tmp_path / "reviewed_role_images.template.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-reviewed-template",
            "--brief",
            str(brief_path),
            "--output",
            str(template_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["reviewed_role_images_template"] == str(template_path)
    assert payload["filled_manifest_path"] == str(tmp_path / "reviewed_role_images.json")
    assert payload["character_count"] == 2
    data = json.loads(template_path.read_text(encoding="utf-8"))
    assert [item["reference_image"] for item in data["characters"]] == ["", ""]
    assert data["characters"][0]["target_reference_image"].endswith("_reference.png")


def test_build_reviewed_role_images_manifest_from_directory_matches_character_names(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "reviewed_from_dir", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    lin = image_dir / "林澈.png"
    su = image_dir / "苏眠_reference.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES + b"lin")
    su.write_bytes(PNG_SIGNATURE_BYTES + b"su")

    manifest = build_reviewed_role_images_manifest_from_directory(brief, image_dir)

    assert manifest["schema_version"] == "motion-comic-factory.reviewed-role-images.v1"
    assert manifest["project_id"] == "reviewed_from_dir"
    assert manifest["source_directory"] == str(image_dir.resolve())
    assert [item["name"] for item in manifest["characters"]] == ["林澈", "苏眠"]
    assert manifest["characters"][0]["reference_image"] == str(lin.resolve())
    assert manifest["characters"][1]["reference_image"] == str(su.resolve())
    assert manifest["characters"][1]["matched_filename"] == "苏眠_reference.png"


def test_build_reviewed_role_images_intake_reports_partial_drop_folder_state(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "reviewed_intake", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    lin = image_dir / "林澈.png"
    invalid_su = image_dir / "苏眠.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES + b"lin")
    invalid_su.write_bytes(b"not an image")

    assert hasattr(character_brief, "build_reviewed_role_images_intake_from_directory")
    intake = character_brief.build_reviewed_role_images_intake_from_directory(
        brief,
        image_dir,
        manifest_output_path=tmp_path / "reviewed_role_images.json",
        confirmed_output_path=tmp_path / "character_assets.confirmed.json",
    )

    assert intake["schema_version"] == "motion-comic-factory.reviewed-role-images-intake.v1"
    assert intake["project_id"] == "reviewed_intake"
    assert intake["ready"] is False
    assert intake["summary"] == {"matched": 1, "missing": 0, "invalid": 1, "ambiguous": 0, "total": 2}
    assert [item["status"] for item in intake["characters"]] == ["matched", "invalid"]
    assert intake["characters"][0]["reference_image"] == str(lin.resolve())
    assert intake["characters"][0]["matched_filename"] == "林澈.png"
    assert "invalid reviewed role image" in intake["characters"][1]["error"]
    assert [item["id"] for item in intake["next_actions"]] == ["replace_invalid_role_images"]
    assert "character-assets-reviewed-from-dir" in intake["next_actions"][0]["command"]
    assert "--output" in intake["next_actions"][0]["command"]
    assert "character-assets-install-references" not in intake["next_actions"][0]["command"]


def test_build_reviewed_role_images_intake_detects_already_installed_sources(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "reviewed_installed", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    sources = []
    confirmed_characters = []
    for index, entry in enumerate(brief["character_assets_template"]["characters"]):
        source = image_dir / f"{entry['name']}.png"
        source.write_bytes(PNG_SIGNATURE_BYTES + str(index).encode("ascii"))
        sources.append(source)
        target = tmp_path / entry["reference_image"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        confirmed_characters.append(
            {
                **entry,
                "installed_from": str(source.resolve()),
                "asset_source": "user_generated_ai",
                "provenance_status": "confirmed",
                "production_ready": True,
            }
        )
    confirmed_output = tmp_path / "character_assets.confirmed.json"
    confirmed_output.write_text(
        json.dumps({"characters": confirmed_characters}, ensure_ascii=False),
        encoding="utf-8",
    )

    intake = character_brief.build_reviewed_role_images_intake_from_directory(
        brief,
        image_dir,
        confirmed_output_path=confirmed_output,
    )

    assert intake["ready"] is True
    assert intake["installed"] is True
    assert [item["installed"] for item in intake["characters"]] == [True, True]
    assert intake["next_actions"] == []

    sources[0].write_bytes(PNG_SIGNATURE_BYTES + b"updated")
    stale_intake = character_brief.build_reviewed_role_images_intake_from_directory(
        brief,
        image_dir,
        confirmed_output_path=confirmed_output,
    )

    assert stale_intake["ready"] is True
    assert stale_intake["installed"] is False
    assert [item["installed"] for item in stale_intake["characters"]] == [False, True]
    assert [item["id"] for item in stale_intake["next_actions"]] == ["install_reviewed_role_images"]


def test_write_reviewed_role_images_manifest_from_directory_rejects_missing_or_invalid_images(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "reviewed_invalid", title="旧城来信", target_shots=2)
    brief_path = tmp_path / "character_generation_brief.json"
    brief_path.write_text(json.dumps(build_character_generation_brief(episode), ensure_ascii=False), encoding="utf-8")
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    (image_dir / "林澈.png").write_bytes(PNG_SIGNATURE_BYTES)
    (image_dir / "苏眠.png").write_bytes(b"not an image")

    with pytest.raises(ValueError, match="invalid reviewed role image"):
        write_reviewed_role_images_manifest_from_directory(
            brief_path,
            image_dir,
            tmp_path / "reviewed_role_images.json",
        )

    (image_dir / "苏眠.png").unlink()
    with pytest.raises(ValueError, match="missing reviewed role image"):
        write_reviewed_role_images_manifest_from_directory(
            brief_path,
            image_dir,
            tmp_path / "reviewed_role_images.json",
        )


def test_cli_character_assets_reviewed_from_dir_writes_source_manifest(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    (image_dir / "林澈.png").write_bytes(PNG_SIGNATURE_BYTES + b"lin")
    (image_dir / "苏眠.png").write_bytes(PNG_SIGNATURE_BYTES + b"su")
    output = tmp_path / "reviewed_role_images.json"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-reviewed-from-dir",
            "--brief",
            str(brief_path),
            "--image-dir",
            str(image_dir),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["reviewed_role_images"] == str(output)
    assert payload["success"] is True
    assert payload["character_count"] == 2
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [Path(item["reference_image"]).name for item in data["characters"]] == ["林澈.png", "苏眠.png"]


def test_cli_character_assets_reviewed_intake_writes_machine_readable_report(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    intake_path = tmp_path / "reviewed_role_images_intake.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    image_dir = tmp_path / "reviewed_role_images"
    image_dir.mkdir()
    (image_dir / "林澈.png").write_bytes(PNG_SIGNATURE_BYTES + b"lin")

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-reviewed-intake",
            "--brief",
            str(brief_path),
            "--image-dir",
            str(image_dir),
            "--output",
            str(intake_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["reviewed_role_images_intake"] == str(intake_path)
    assert payload["ready"] is False
    assert payload["summary"] == {"matched": 1, "missing": 1, "invalid": 0, "ambiguous": 0, "total": 2}
    assert payload["next_actions"] == ["place_missing_role_images"]
    data = json.loads(intake_path.read_text(encoding="utf-8"))
    assert [item["status"] for item in data["characters"]] == ["matched", "missing"]
    assert data["characters"][1]["expected_filenames"][-1] == "苏眠.png"
    assert data["characters"][1]["expected_filenames"][0].endswith("_reference.png")


def test_build_character_assets_manifest_from_brief_checks_reference_files(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_assets", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    for item in brief["character_assets_template"]["characters"]:
        item["asset_source"] = "user_generated_ai"
    for item in brief["character_assets_template"]["characters"]:
        image_path = tmp_path / item["reference_image"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_SIGNATURE_BYTES)

    manifest = build_character_assets_manifest_from_brief(brief, manifest_dir=tmp_path, require_files=True)

    assert manifest["schema_version"] == "motion-comic-factory.character-assets-source.v1"
    assert [item["name"] for item in manifest["characters"]] == ["林澈", "苏眠"]
    assert manifest["characters"][0]["reference_image"].endswith("_reference.png")
    assert all(item["asset_source"] == "user_generated_ai" for item in manifest["characters"])


def test_build_character_assets_manifest_from_brief_rejects_missing_reference_files(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_missing", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)

    with pytest.raises(ValueError, match="missing brief reference image"):
        build_character_assets_manifest_from_brief(brief, manifest_dir=tmp_path, require_files=True)


def test_build_character_assets_manifest_from_brief_rejects_invalid_reference_files(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_invalid", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    for item in brief["character_assets_template"]["characters"]:
        image_path = tmp_path / item["reference_image"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"not an image")

    with pytest.raises(ValueError, match="invalid brief reference image"):
        build_character_assets_manifest_from_brief(brief, manifest_dir=tmp_path, require_files=True)


def test_build_character_assets_status_from_brief_reports_each_reference_state(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_status", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    brief["character_assets_template"]["characters"][0]["asset_source"] = "user_generated_ai"
    first_path = tmp_path / brief["character_assets_template"]["characters"][0]["reference_image"]
    first_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_bytes(PNG_SIGNATURE_BYTES)

    status = build_character_assets_status_from_brief(brief, asset_root=tmp_path)

    assert status["schema_version"] == "motion-comic-factory.character-assets-status.v1"
    assert status["project_id"] == "brief_status"
    assert status["asset_ready"] is False
    assert status["summary"] == {"ready": 1, "missing": 1, "invalid": 0, "total": 2}
    assert [item["status"] for item in status["characters"]] == ["ready", "missing"]
    assert status["characters"][0]["valid_image"] is True
    assert status["characters"][0]["asset_source"] == "user_generated_ai"
    assert status["characters"][0]["production_ready"] is True
    assert status["characters"][1]["exists"] is False
    assert status["characters"][1]["provenance_status"] == "missing"


def test_build_character_assets_status_marks_ready_images_without_source_not_production_ready(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_unconfirmed", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    for item in brief["character_assets_template"]["characters"]:
        image_path = tmp_path / item["reference_image"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_SIGNATURE_BYTES)

    status = build_character_assets_status_from_brief(brief, asset_root=tmp_path)

    assert status["asset_ready"] is True
    assert status["production_ready"] is False
    assert all(item["status"] == "ready" for item in status["characters"])
    assert all(item["production_ready"] is False for item in status["characters"])
    assert all(item["provenance_status"] == "missing" for item in status["characters"])
    assert [item["id"] for item in status["next_actions"]] == [
        "confirm_existing_reference_images",
        "install_reviewed_role_images",
    ]
    assert "character-assets-confirm-source" in status["next_actions"][0]["command"]
    assert "character-assets-reviewed-from-dir" in status["next_actions"][1]["command"]


def test_build_character_assets_status_suggests_drop_folder_for_missing_images(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "brief_missing_actions", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)

    status = build_character_assets_status_from_brief(brief, asset_root=tmp_path)

    assert status["asset_ready"] is False
    assert [item["id"] for item in status["next_actions"]] == ["place_reviewed_role_images"]
    assert status["next_actions"][0]["target_path"] == str(tmp_path / "reviewed_role_images")


def test_build_confirmed_character_assets_manifest_stamps_production_source(tmp_path):
    manifest = {
        "schema_version": "motion-comic-factory.character-assets-source.v1",
        "project_id": "confirmed_assets",
        "title": "旧城来信",
        "characters": [
            {
                "character_id": "lin",
                "name": "林澈",
                "reference_image": "assets/characters/lin_reference.png",
                "asset_source": "",
            },
            {
                "character_id": "su",
                "name": "苏眠",
                "reference_image": "assets/characters/su_reference.png",
                "asset_source": "",
            },
        ],
    }
    for item in manifest["characters"]:
        image_path = tmp_path / item["reference_image"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_SIGNATURE_BYTES)

    confirmed = build_confirmed_character_assets_manifest(manifest, manifest_dir=tmp_path)

    assert confirmed["schema_version"] == "motion-comic-factory.character-assets-source.v1"
    assert confirmed["project_id"] == "confirmed_assets"
    assert [item["reference_image"] for item in confirmed["characters"]] == [
        "assets/characters/lin_reference.png",
        "assets/characters/su_reference.png",
    ]
    assert all(item["asset_source"] == "user_generated_ai" for item in confirmed["characters"])
    assert all(item["provenance_status"] == "confirmed" for item in confirmed["characters"])
    assert all(item["production_ready"] is True for item in confirmed["characters"])


def test_build_confirmed_character_assets_manifest_rejects_missing_or_invalid_files(tmp_path):
    manifest = {
        "characters": [
            {
                "character_id": "lin",
                "name": "林澈",
                "reference_image": "assets/characters/lin_reference.png",
            },
            {
                "character_id": "su",
                "name": "苏眠",
                "reference_image": "assets/characters/su_reference.png",
            },
        ],
    }
    invalid_path = tmp_path / "assets/characters/su_reference.png"
    invalid_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path.write_bytes(b"not an image")

    with pytest.raises(ValueError, match="missing confirmed reference image"):
        build_confirmed_character_assets_manifest(manifest, manifest_dir=tmp_path)

    (tmp_path / "assets/characters/lin_reference.png").write_bytes(PNG_SIGNATURE_BYTES)
    with pytest.raises(ValueError, match="invalid confirmed reference image"):
        build_confirmed_character_assets_manifest(manifest, manifest_dir=tmp_path)


def test_build_confirmed_character_assets_manifest_rejects_untrusted_source(tmp_path):
    manifest = {"characters": []}

    with pytest.raises(ValueError, match="asset_source must be production-ready"):
        build_confirmed_character_assets_manifest(
            manifest,
            manifest_dir=tmp_path,
            asset_source="smoke_placeholder",
        )


def test_install_character_references_from_manifest_copies_images_and_writes_confirmed_manifest(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "install_refs", title="旧城来信", target_shots=2)
    brief_path = tmp_path / "character_generation_brief.json"
    brief_path.write_text(json.dumps(build_character_generation_brief(episode), ensure_ascii=False), encoding="utf-8")
    source_dir = tmp_path / "generated"
    source_dir.mkdir()
    lin = source_dir / "lin.png"
    su = source_dir / "su.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES + b"lin")
    su.write_bytes(PNG_SIGNATURE_BYTES + b"su")
    source_manifest = tmp_path / "reviewed_role_images.json"
    source_manifest.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": "林澈", "reference_image": str(lin)},
                    {"name": "苏眠", "reference_image": str(su)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "character_assets.confirmed.json"

    written = install_character_references_from_manifest(
        brief_path,
        source_manifest,
        output,
        overwrite=False,
    )

    assert written == output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == "motion-comic-factory.character-assets-source.v1"
    assert data["project_id"] == "install_refs"
    assert all(item["asset_source"] == "user_generated_ai" for item in data["characters"])
    assert all(item["production_ready"] is True for item in data["characters"])
    first_target = tmp_path / data["characters"][0]["reference_image"]
    second_target = tmp_path / data["characters"][1]["reference_image"]
    assert first_target.read_bytes().endswith(b"lin")
    assert second_target.read_bytes().endswith(b"su")
    assert data["characters"][0]["installed_from"] == str(lin.resolve())


def test_install_character_references_from_manifest_requires_overwrite_for_existing_targets(tmp_path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "install_overwrite", title="旧城来信", target_shots=2)
    brief = build_character_generation_brief(episode)
    brief_path = tmp_path / "character_generation_brief.json"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    existing_target = tmp_path / brief["character_assets_template"]["characters"][0]["reference_image"]
    existing_target.parent.mkdir(parents=True, exist_ok=True)
    existing_target.write_bytes(PNG_SIGNATURE_BYTES + b"old")
    source = tmp_path / "lin_new.png"
    source.write_bytes(PNG_SIGNATURE_BYTES + b"new")
    source_manifest = tmp_path / "reviewed_role_images.json"
    source_manifest.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": "林澈", "reference_image": str(source)},
                    {"name": "苏眠", "reference_image": str(source)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="target reference image already exists"):
        install_character_references_from_manifest(
            brief_path,
            source_manifest,
            tmp_path / "character_assets.confirmed.json",
            overwrite=False,
        )
    second_target = tmp_path / brief["character_assets_template"]["characters"][1]["reference_image"]
    assert not second_target.exists()


def test_cli_character_assets_install_references_writes_confirmed_manifest(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lin = tmp_path / "lin.png"
    su = tmp_path / "su.png"
    lin.write_bytes(PNG_SIGNATURE_BYTES + b"lin")
    su.write_bytes(PNG_SIGNATURE_BYTES + b"su")
    source_manifest = tmp_path / "reviewed_role_images.json"
    source_manifest.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": "林澈", "reference_image": str(lin)},
                    {"name": "苏眠", "reference_image": str(su)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "character_assets.confirmed.json"

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-install-references",
            "--brief",
            str(brief_path),
            "--source-manifest",
            str(source_manifest),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["character_assets"] == str(output)
    assert payload["success"] is True
    assert payload["installed_count"] == 2
    data = json.loads(output.read_text(encoding="utf-8"))
    assert all(item["production_ready"] is True for item in data["characters"])


def test_cli_character_assets_from_brief_writes_manifest(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    manifest_path = tmp_path / "character_assets.from_brief.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-from-brief",
            "--brief",
            str(brief_path),
            "--output",
            str(manifest_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["character_assets"] == str(manifest_path)
    assert payload["character_count"] == 2
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [item["name"] for item in data["characters"]] == ["林澈", "苏眠"]
    assert data["characters"][0]["reference_image"].endswith("_reference.png")


def test_cli_character_assets_confirm_source_writes_confirmed_manifest(tmp_path):
    manifest_path = tmp_path / "character_assets.from_brief.json"
    output_path = tmp_path / "character_assets.confirmed.json"
    manifest = {
        "schema_version": "motion-comic-factory.character-assets-source.v1",
        "project_id": "sample_episode",
        "title": "旧城来信",
        "characters": [
            {
                "character_id": "lin",
                "name": "林澈",
                "reference_image": "assets/characters/lin_reference.png",
                "asset_source": "",
            },
            {
                "character_id": "su",
                "name": "苏眠",
                "reference_image": "assets/characters/su_reference.png",
                "asset_source": "",
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for item in manifest["characters"]:
        image_path = tmp_path / item["reference_image"]
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(PNG_SIGNATURE_BYTES)

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-confirm-source",
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["character_assets"] == str(output_path)
    assert payload["success"] is True
    assert payload["asset_source"] == "user_generated_ai"
    assert payload["character_count"] == 2
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert all(item["asset_source"] == "user_generated_ai" for item in data["characters"])
    assert all(item["production_ready"] is True for item in data["characters"])


def test_cli_character_assets_from_brief_reports_missing_required_files(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-from-brief",
            "--brief",
            str(brief_path),
            "--output",
            str(tmp_path / "character_assets.json"),
            "--require-files",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["success"] is False
    assert "missing brief reference image" in payload["errors"][0]


def test_cli_character_assets_status_writes_report(tmp_path):
    brief_path = tmp_path / "character_generation_brief.json"
    status_path = tmp_path / "character_assets_status.json"
    subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-brief",
            "--input",
            "samples/sample_novel.txt",
            "--project",
            "sample_episode",
            "--title",
            "旧城来信",
            "--output",
            str(brief_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            PYTHON,
            "factory_cli.py",
            "character-assets-status",
            "--brief",
            str(brief_path),
            "--output",
            str(status_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["character_assets_status"] == str(status_path)
    assert payload["asset_ready"] is False
    assert payload["summary"] == {"ready": 0, "missing": 2, "invalid": 0, "total": 2}
    assert payload["next_actions"] == ["place_reviewed_role_images"]
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert [item["status"] for item in data["characters"]] == ["missing", "missing"]
