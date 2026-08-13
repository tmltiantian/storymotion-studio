from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import Episode


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
RIFF_SIGNATURE = b"RIFF"
WEBP_SIGNATURE = b"WEBP"
PRODUCTION_ASSET_SOURCES = {"user_generated_ai"}
PLACEHOLDER_ASSET_SOURCES = {"placeholder", "smoke_placeholder", "storyboard_card_placeholder"}


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _image_path_from_entry(entry: dict[str, Any]) -> str:
    for key in ("reference_image", "reference_image_path", "image", "image_path"):
        value = str(entry.get(key, "")).strip()
        if value:
            return value
    return ""


def _asset_source_from_entry(entry: dict[str, Any]) -> str:
    return str(entry.get("asset_source", "")).strip()


def _resolve_image_path(raw_path: str, manifest_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _has_supported_image_signature(path: Path) -> bool:
    try:
        header = path.read_bytes()[:12]
    except OSError:
        return False

    suffix = path.suffix.lower()
    if suffix == ".png":
        return header.startswith(PNG_SIGNATURE)
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(JPEG_SIGNATURE)
    if suffix == ".webp":
        return header.startswith(RIFF_SIGNATURE) and header[8:12] == WEBP_SIGNATURE
    return False


def is_supported_image_file(path: str | Path) -> bool:
    image_path = Path(path)
    return image_path.is_file() and image_path.suffix.lower() in IMAGE_SUFFIXES and _has_supported_image_signature(image_path)


def is_production_asset_source(asset_source: str) -> bool:
    return asset_source.strip() in PRODUCTION_ASSET_SOURCES


def provenance_status(asset_source: str) -> str:
    source = asset_source.strip()
    if not source:
        return "missing"
    if source in PRODUCTION_ASSET_SOURCES:
        return "confirmed"
    if source in PLACEHOLDER_ASSET_SOURCES:
        return "placeholder"
    return "unknown"


def _entry_matches_character(entry: dict[str, Any], character_id: str, name: str) -> tuple[bool, str]:
    if str(entry.get("character_id", "")).strip() == character_id:
        return True, "character_id"
    if str(entry.get("id", "")).strip() == character_id:
        return True, "id"
    if str(entry.get("name", "")).strip() == name:
        return True, "name"
    return False, ""


def _find_entry(entries: list[dict[str, Any]], character_id: str, name: str) -> tuple[dict[str, Any] | None, str]:
    for entry in entries:
        matched, matched_by = _entry_matches_character(entry, character_id, name)
        if matched:
            return entry, matched_by
    return None, ""


def _empty_character_asset(character_id: str, name: str) -> dict[str, Any]:
    return {
        "character_id": character_id,
        "name": name,
        "reference_image_path": "",
        "reference_image_exists": False,
        "asset_role": "reference_image",
        "asset_source": "",
        "production_ready": False,
        "matched_by": "",
    }


def build_character_asset_manifest(
    episode: Episode,
    source_manifest_path: str | Path | None,
) -> dict[str, Any]:
    source = Path(source_manifest_path).expanduser() if source_manifest_path else None
    if not source:
        characters = [_empty_character_asset(character.id, character.name) for character in episode.characters]
        return {
            "schema_version": "motion-comic-factory.character-assets.v1",
            "project_id": episode.project_id,
            "source_manifest_path": "",
            "asset_ready": False,
            "production_ready": False,
            "characters": characters,
            "errors": [],
        }

    source = source.resolve()
    manifest = _read_manifest(source)
    entries = manifest.get("characters", [])
    if not isinstance(entries, list):
        raise ValueError("character asset manifest must contain a characters list")

    characters: list[dict[str, Any]] = []
    errors: list[str] = []
    for character in episode.characters:
        entry, matched_by = _find_entry(entries, character.id, character.name)
        if not entry:
            errors.append(f"missing character asset entry for {character.name}")
            characters.append(_empty_character_asset(character.id, character.name))
            continue

        raw_image_path = _image_path_from_entry(entry)
        asset_source = _asset_source_from_entry(entry)
        image_path = _resolve_image_path(raw_image_path, source) if raw_image_path else Path("")
        exists = bool(raw_image_path) and image_path.is_file()
        valid_image = exists and is_supported_image_file(image_path)
        if raw_image_path and image_path.suffix.lower() not in IMAGE_SUFFIXES:
            errors.append(f"unsupported character reference image type for {character.name}: {image_path}")
        if not exists:
            errors.append(f"missing character reference image for {character.name}: {image_path}")
        elif not valid_image:
            errors.append(f"invalid character reference image for {character.name}: {image_path}")
        production_ready = valid_image and is_production_asset_source(asset_source)

        characters.append(
            {
                "character_id": character.id,
                "name": character.name,
                "reference_image_path": str(image_path) if raw_image_path else "",
                "reference_image_exists": exists,
                "asset_role": str(entry.get("asset_role", "reference_image")),
                "asset_source": asset_source,
                "provenance_status": provenance_status(asset_source),
                "production_ready": production_ready,
                "matched_by": matched_by,
            }
        )

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "schema_version": "motion-comic-factory.character-assets.v1",
        "project_id": episode.project_id,
        "source_manifest_path": str(source),
        "asset_ready": all(item["reference_image_exists"] for item in characters),
        "production_ready": all(item["production_ready"] for item in characters),
        "characters": characters,
        "errors": [],
    }


def build_character_asset_source_template(episode: Episode) -> dict[str, Any]:
    return {
        "schema_version": "motion-comic-factory.character-assets-source.v1",
        "project_id": episode.project_id,
        "title": episode.title,
        "characters": [
            {
                "character_id": character.id,
                "name": character.name,
                "reference_image": "",
                "asset_source": "",
            }
            for character in episode.characters
        ],
    }


def write_character_asset_source_template(episode: Episode, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = build_character_asset_source_template(episode)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def write_character_asset_manifest(
    episode: Episode,
    source_manifest_path: str | Path | None,
    run_dir: str | Path,
) -> Path:
    output = Path(run_dir) / "character_assets.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    data = build_character_asset_manifest(episode, source_manifest_path)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def character_asset_by_id(character_assets: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not character_assets:
        return {}
    return {
        str(item.get("character_id")): item
        for item in character_assets.get("characters", [])
        if item.get("character_id")
    }
