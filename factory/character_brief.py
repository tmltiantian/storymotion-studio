from __future__ import annotations

import filecmp
import json
import shutil
from pathlib import Path
from typing import Any

from .character_assets import IMAGE_SUFFIXES, is_production_asset_source, is_supported_image_file, provenance_status
from .schema import Character, Episode


NEGATIVE_PROMPT = (
    "low quality, blurry, jpeg artifacts, deformed face, bad anatomy, extra fingers, "
    "extra limbs, inconsistent outfit, inconsistent face, text, watermark, logo"
)
PYTHON_BIN = ".venv/bin/python"

ASSET_REQUIREMENT_SPECS = (
    (
        "reference_image",
        "front half-body reference",
        "正面半身角色参考图，脸部清晰，服装完整，背景干净，用于后续角色一致性绑定。",
        "_reference.png",
    ),
    (
        "full_body",
        "front full-body design",
        "正面全身设定图，包含鞋子和完整轮廓，方便分镜和姿态生成。",
        "_full_body.png",
    ),
    (
        "turnaround_sheet",
        "three-view character sheet",
        "三视图角色设定表，正面、侧面、背面保持同一发型、服装和脸型。",
        "_turnaround.png",
    ),
)


def _asset_path(character: Character, suffix: str) -> str:
    return f"assets/characters/{character.id}{suffix}"


def _base_positive_prompt(episode: Episode, character: Character) -> str:
    return (
        f"{episode.style}, {character.name}, {character.role}, {character.description}, "
        f"{character.visual_anchor}, consistent character design, clean anime line art, "
        "cinematic lighting, expressive eyes, production-ready motion comic character, "
        "plain background, no text, no watermark"
    )


def _asset_requirements(episode: Episode, character: Character) -> list[dict[str, str]]:
    base_prompt = _base_positive_prompt(episode, character)
    return [
        {
            "asset_role": asset_role,
            "shot_type": shot_type,
            "description": description,
            "suggested_path": _asset_path(character, suffix),
            "positive_prompt": f"{base_prompt}, {shot_type}, {description}",
            "negative_prompt": NEGATIVE_PROMPT,
        }
        for asset_role, shot_type, description, suffix in ASSET_REQUIREMENT_SPECS
    ]


def _character_brief(episode: Episode, character: Character) -> dict[str, Any]:
    asset_requirements = _asset_requirements(episode, character)
    return {
        "character_id": character.id,
        "name": character.name,
        "role": character.role,
        "description": character.description,
        "visual_anchor": character.visual_anchor,
        "voice_style": character.voice_style,
        "positive_prompt": asset_requirements[0]["positive_prompt"],
        "negative_prompt": NEGATIVE_PROMPT,
        "recommended_reference_image": asset_requirements[0]["suggested_path"],
        "asset_requirements": asset_requirements,
    }


def build_character_generation_brief(episode: Episode) -> dict[str, Any]:
    characters = [_character_brief(episode, character) for character in episode.characters]
    return {
        "schema_version": "motion-comic-factory.character-generation-brief.v1",
        "project_id": episode.project_id,
        "title": episode.title,
        "style": episode.style,
        "target_aspect_ratio": episode.target_aspect_ratio,
        "target_resolution": episode.target_resolution,
        "characters": characters,
        "character_assets_template": {
            "schema_version": "motion-comic-factory.character-assets-source.v1",
            "project_id": episode.project_id,
            "title": episode.title,
            "characters": [
                {
                    "character_id": character["character_id"],
                    "name": character["name"],
                    "reference_image": character["recommended_reference_image"],
                    "asset_source": "",
                }
                for character in characters
            ],
        },
        "next_command": (
            f"{PYTHON_BIN} factory_cli.py factory create --mode novel "
            f"--input <novel.txt> --project {episode.project_id} --title {episode.title} "
            f"--character-assets <filled_character_assets.json>"
        ),
    }


def write_character_generation_brief(episode: Episode, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = build_character_generation_brief(episode)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _filled_reviewed_role_images_path(output_path: str | Path | None) -> str:
    if not output_path:
        return "reviewed_role_images.json"
    output = Path(output_path)
    if output.name.endswith(".template.json"):
        return str(output.with_name(output.name.replace(".template.json", ".json")))
    return str(output.with_name("reviewed_role_images.json"))


def build_reviewed_role_images_template_from_brief(
    brief: dict[str, Any],
    brief_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    template = brief.get("character_assets_template", {})
    target_entries = _brief_template_entries(brief)
    brief_characters = brief.get("characters", [])
    if not isinstance(brief_characters, list):
        raise ValueError("character generation brief must contain characters")

    characters: list[dict[str, str]] = []
    for target_entry in target_entries:
        if not isinstance(target_entry, dict):
            raise ValueError("character generation brief character asset entries must be objects")
        character_id = str(target_entry.get("character_id", "")).strip()
        name = str(target_entry.get("name", "")).strip()
        brief_character = _find_source_reference_entry(brief_characters, character_id, name) or {}
        target_reference_image = str(target_entry.get("reference_image", "")).strip()
        characters.append(
            {
                "character_id": character_id,
                "name": name,
                "reference_image": "",
                "target_reference_image": target_reference_image,
                "positive_prompt": str(brief_character.get("positive_prompt", "")),
                "negative_prompt": str(brief_character.get("negative_prompt", NEGATIVE_PROMPT)),
            }
        )

    source_manifest_path = _filled_reviewed_role_images_path(output_path)
    brief_argument = str(brief_path) if brief_path else "<character_generation_brief.json>"
    confirmed_manifest_path = (
        str(Path(brief_path).with_name("character_assets.confirmed.json"))
        if brief_path
        else "<character_assets.confirmed.json>"
    )
    return {
        "schema_version": "motion-comic-factory.reviewed-role-images-template.v1",
        "project_id": str(template.get("project_id") or brief.get("project_id", "")),
        "title": str(template.get("title") or brief.get("title", "")),
        "instructions": (
            "Fill reference_image with the absolute path to each reviewed AI-generated role image, "
            "save as reviewed_role_images.json, then run next_command."
        ),
        "filled_manifest_path": source_manifest_path,
        "characters": characters,
        "next_command": (
            f"{PYTHON_BIN} factory_cli.py "
            f"character-assets-install-references --brief {brief_argument} "
            f"--source-manifest {source_manifest_path} --output {confirmed_manifest_path} --overwrite"
        ),
    }


def write_reviewed_role_images_template_from_brief(
    brief_path: str | Path,
    output_path: str | Path,
) -> Path:
    brief_file = Path(brief_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    data = build_reviewed_role_images_template_from_brief(
        brief,
        brief_path=brief_file,
        output_path=output,
    )
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _reviewed_role_image_candidates(image_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def _reviewed_role_image_score(path: Path, character_id: str, name: str) -> int | None:
    stem = path.stem.strip()
    if stem == character_id or stem == name:
        return 0
    if character_id and stem.startswith(f"{character_id}_"):
        return 1
    if name and stem.startswith(f"{name}_"):
        return 1
    if character_id and character_id in stem:
        return 2
    if name and name in stem:
        return 2
    return None


def _match_reviewed_role_image(candidates: list[Path], character_id: str, name: str) -> Path | None:
    scored: list[tuple[int, Path]] = []
    for candidate in candidates:
        score = _reviewed_role_image_score(candidate, character_id, name)
        if score is not None:
            scored.append((score, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1].name))
    best_score = scored[0][0]
    best = [path for score, path in scored if score == best_score]
    if len(best) > 1:
        names = ", ".join(path.name for path in best)
        label = name or character_id or "unknown character"
        raise ValueError(f"ambiguous reviewed role images for {label}: {names}")
    return best[0]


def _scored_reviewed_role_images(candidates: list[Path], character_id: str, name: str) -> list[tuple[int, Path]]:
    scored: list[tuple[int, Path]] = []
    for candidate in candidates:
        score = _reviewed_role_image_score(candidate, character_id, name)
        if score is not None:
            scored.append((score, candidate))
    return sorted(scored, key=lambda item: (item[0], item[1].name))


def _expected_reviewed_role_image_filenames(character_id: str, name: str) -> list[str]:
    filenames: list[str] = []
    if character_id:
        filenames.extend([f"{character_id}_reference.png", f"{character_id}.png"])
    if name:
        filenames.append(f"{name}.png")
    return filenames


def _reviewed_role_images_intake_action(
    action_id: str,
    label: str,
    target_path: Path,
    command: str,
) -> dict[str, str]:
    return {
        "id": action_id,
        "label": label,
        "target_path": str(target_path),
        "command": command,
    }


def _reviewed_role_images_intake_next_actions(
    *,
    ready: bool,
    installed: bool,
    summary: dict[str, int],
    brief_path: str,
    source_dir: Path,
    output_path: str,
    manifest_output_path: str,
    confirmed_output_path: str,
) -> list[dict[str, str]]:
    intake_command = (
        f"{PYTHON_BIN} factory_cli.py character-assets-reviewed-intake "
        f"--brief {brief_path} --image-dir {source_dir} --output {output_path}"
    )
    from_dir_command = (
        f"{PYTHON_BIN} factory_cli.py character-assets-reviewed-from-dir "
        f"--brief {brief_path} --image-dir {source_dir} --output {manifest_output_path}"
    )
    install_command = (
        f"{PYTHON_BIN} factory_cli.py character-assets-install-references "
        f"--brief {brief_path} --source-manifest {manifest_output_path} "
        f"--output {confirmed_output_path} --overwrite"
    )

    if installed:
        return []

    if ready:
        return [
            _reviewed_role_images_intake_action(
                "install_reviewed_role_images",
                "All reviewed role images are matched and valid; install them as production-ready references.",
                source_dir,
                f"{from_dir_command} && {install_command}",
            )
        ]

    actions: list[dict[str, str]] = []
    if summary["missing"]:
        actions.append(
            _reviewed_role_images_intake_action(
                "place_missing_role_images",
                "Place the missing reviewed role images in the drop folder.",
                source_dir,
                intake_command,
            )
        )
    if summary["invalid"]:
        actions.append(
            _reviewed_role_images_intake_action(
                "replace_invalid_role_images",
                "Replace invalid files with PNG, JPG, JPEG, or WEBP images that have valid image bytes.",
                source_dir,
                from_dir_command,
            )
        )
    if summary["ambiguous"]:
        actions.append(
            _reviewed_role_images_intake_action(
                "resolve_ambiguous_role_images",
                "Rename duplicate matching files so each character has exactly one reviewed role image.",
                source_dir,
                intake_command,
            )
        )
    return actions


def _manifest_image_path(value: Any, manifest_path: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _confirmed_reviewed_role_image_installations(confirmed_output: Path) -> dict[str, dict[str, Any]]:
    if not confirmed_output.is_file():
        return {}
    try:
        data = json.loads(confirmed_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    installations: dict[str, dict[str, Any]] = {}
    for item in data.get("characters", []):
        if not isinstance(item, dict):
            continue
        if item.get("production_ready") is not True or item.get("provenance_status") != "confirmed":
            continue
        source = _manifest_image_path(item.get("installed_from"), confirmed_output)
        target = _manifest_image_path(item.get("reference_image"), confirmed_output)
        if source is None or target is None or not is_supported_image_file(target):
            continue
        installation = {"source": source, "target": target}
        for key in (str(item.get("character_id", "")).strip(), str(item.get("name", "")).strip()):
            if key:
                installations[key] = installation
    return installations


def build_reviewed_role_images_intake_from_directory(
    brief: dict[str, Any],
    image_dir: str | Path,
    manifest_output_path: str | Path | None = None,
    confirmed_output_path: str | Path | None = None,
    output_path: str | Path | None = None,
    brief_path: str | Path | None = None,
) -> dict[str, Any]:
    template = brief.get("character_assets_template", {})
    entries = _brief_template_entries(brief)
    source_dir = Path(image_dir).expanduser().resolve()
    candidates = _reviewed_role_image_candidates(source_dir) if source_dir.is_dir() else []
    manifest_output = Path(manifest_output_path or source_dir.with_name("reviewed_role_images.json")).expanduser()
    confirmed_output = Path(confirmed_output_path or source_dir.with_name("character_assets.confirmed.json")).expanduser()
    intake_output = Path(output_path or source_dir.with_name("reviewed_role_images_intake.json")).expanduser()
    brief_argument = str(brief_path) if brief_path else "<character_generation_brief.json>"
    installations = _confirmed_reviewed_role_image_installations(confirmed_output)

    summary = {"matched": 0, "missing": 0, "invalid": 0, "ambiguous": 0, "total": len(entries)}
    characters: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            summary["missing"] += 1
            characters.append(
                {
                    "character_id": "",
                    "name": "",
                    "status": "missing",
                    "matched_filename": "",
                    "reference_image": "",
                    "candidate_filenames": [],
                    "expected_filenames": [],
                    "installed": False,
                    "error": "character asset entry must be an object",
                }
            )
            continue

        character_id = str(entry.get("character_id", "")).strip()
        name = str(entry.get("name", "")).strip()
        label = name or character_id or "unknown character"
        scored = _scored_reviewed_role_images(candidates, character_id, name)
        expected_filenames = _expected_reviewed_role_image_filenames(character_id, name)
        base_character = {
            "character_id": character_id,
            "name": name,
            "expected_filenames": expected_filenames,
            "candidate_filenames": [path.name for _, path in scored],
        }
        if not scored:
            summary["missing"] += 1
            characters.append(
                {
                    **base_character,
                    "status": "missing",
                    "matched_filename": "",
                    "reference_image": "",
                    "installed": False,
                    "error": f"missing reviewed role image for {label}",
                }
            )
            continue

        best_score = scored[0][0]
        best = [path for score, path in scored if score == best_score]
        if len(best) > 1:
            names = ", ".join(path.name for path in best)
            summary["ambiguous"] += 1
            characters.append(
                {
                    **base_character,
                    "status": "ambiguous",
                    "matched_filename": "",
                    "reference_image": "",
                    "installed": False,
                    "error": f"ambiguous reviewed role images for {label}: {names}",
                }
            )
            continue

        match = best[0]
        if not is_supported_image_file(match):
            summary["invalid"] += 1
            characters.append(
                {
                    **base_character,
                    "status": "invalid",
                    "matched_filename": match.name,
                    "reference_image": str(match.resolve()),
                    "installed": False,
                    "error": f"invalid reviewed role image for {label}: {match.name}",
                }
            )
            continue

        installation = installations.get(character_id) or installations.get(name)
        is_installed = bool(
            installation
            and installation["source"] == match.resolve()
            and filecmp.cmp(match, installation["target"], shallow=False)
        )
        summary["matched"] += 1
        characters.append(
            {
                **base_character,
                "status": "matched",
                "matched_filename": match.name,
                "reference_image": str(match.resolve()),
                "installed": is_installed,
                "error": "",
            }
        )

    ready = summary["matched"] == summary["total"] and summary["total"] > 0
    installed = ready and all(bool(item.get("installed")) for item in characters)
    return {
        "schema_version": "motion-comic-factory.reviewed-role-images-intake.v1",
        "project_id": str(template.get("project_id") or brief.get("project_id", "")),
        "title": str(template.get("title") or brief.get("title", "")),
        "source_directory": str(source_dir),
        "directory_exists": source_dir.is_dir(),
        "ready": ready,
        "installed": installed,
        "summary": summary,
        "characters": characters,
        "manifest_output_path": str(manifest_output),
        "confirmed_output_path": str(confirmed_output),
        "next_actions": _reviewed_role_images_intake_next_actions(
            ready=ready,
            installed=installed,
            summary=summary,
            brief_path=brief_argument,
            source_dir=source_dir,
            output_path=str(intake_output),
            manifest_output_path=str(manifest_output),
            confirmed_output_path=str(confirmed_output),
        ),
    }


def write_reviewed_role_images_intake_from_directory(
    brief_path: str | Path,
    image_dir: str | Path,
    output_path: str | Path,
    manifest_output_path: str | Path | None = None,
    confirmed_output_path: str | Path | None = None,
) -> Path:
    brief_file = Path(brief_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    data = build_reviewed_role_images_intake_from_directory(
        brief,
        image_dir,
        manifest_output_path=manifest_output_path,
        confirmed_output_path=confirmed_output_path,
        output_path=output,
        brief_path=brief_file,
    )
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def build_reviewed_role_images_manifest_from_directory(
    brief: dict[str, Any],
    image_dir: str | Path,
) -> dict[str, Any]:
    template = brief.get("character_assets_template", {})
    entries = _brief_template_entries(brief)
    source_dir = Path(image_dir).expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"reviewed role image directory does not exist: {image_dir}")

    candidates = _reviewed_role_image_candidates(source_dir)
    characters: list[dict[str, str]] = []
    errors: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("character asset entry must be an object")
            continue
        character_id = str(entry.get("character_id", "")).strip()
        name = str(entry.get("name", "")).strip()
        label = name or character_id or "unknown character"
        try:
            match = _match_reviewed_role_image(candidates, character_id, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not match:
            errors.append(f"missing reviewed role image for {label}")
            continue
        if not is_supported_image_file(match):
            errors.append(f"invalid reviewed role image for {label}: {match.name}")
            continue
        characters.append(
            {
                "character_id": character_id,
                "name": name,
                "reference_image": str(match.resolve()),
                "matched_filename": match.name,
            }
        )

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "schema_version": "motion-comic-factory.reviewed-role-images.v1",
        "project_id": str(template.get("project_id") or brief.get("project_id", "")),
        "title": str(template.get("title") or brief.get("title", "")),
        "source_directory": str(source_dir),
        "characters": characters,
    }


def write_reviewed_role_images_manifest_from_directory(
    brief_path: str | Path,
    image_dir: str | Path,
    output_path: str | Path,
) -> Path:
    brief_file = Path(brief_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    data = build_reviewed_role_images_manifest_from_directory(brief, image_dir)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _resolve_manifest_image_path(raw_path: str, manifest_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = manifest_dir / path
    return path.resolve()


def _reference_image_from_entry(entry: dict[str, Any]) -> str:
    for key in ("reference_image", "reference_image_path", "image", "image_path"):
        value = str(entry.get(key, "")).strip()
        if value:
            return value
    return ""


def _brief_template_entries(brief: dict[str, Any]) -> list[dict[str, Any]]:
    template = brief.get("character_assets_template", {})
    entries = template.get("characters", [])
    if not isinstance(entries, list):
        raise ValueError("character generation brief must contain character_assets_template.characters")
    return entries


def _entry_match_key(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("character_id") or entry.get("id") or "").strip(), str(entry.get("name", "")).strip()


def _find_source_reference_entry(entries: list[dict[str, Any]], character_id: str, name: str) -> dict[str, Any] | None:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id, entry_name = _entry_match_key(entry)
        if character_id and entry_id == character_id:
            return entry
        if name and entry_name == name:
            return entry
    return None


def build_character_assets_manifest_from_brief(
    brief: dict[str, Any],
    manifest_dir: str | Path | None = None,
    require_files: bool = False,
) -> dict[str, Any]:
    template = brief.get("character_assets_template", {})
    entries = _brief_template_entries(brief)

    manifest_dir_path = Path(manifest_dir or ".").expanduser().resolve()
    characters: list[dict[str, str]] = []
    errors: list[str] = []
    for entry in entries:
        reference_image = str(entry.get("reference_image", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not reference_image:
            errors.append(f"missing brief reference image path for {name or 'unknown character'}")
        elif require_files:
            resolved_image = _resolve_manifest_image_path(reference_image, manifest_dir_path)
            if not resolved_image.is_file():
                errors.append(f"missing brief reference image for {name}: {reference_image}")
            elif not is_supported_image_file(resolved_image):
                errors.append(f"invalid brief reference image for {name}: {reference_image}")

        characters.append(
            {
                "character_id": str(entry.get("character_id", "")).strip(),
                "name": name,
                "reference_image": reference_image,
                "asset_source": str(entry.get("asset_source", "")).strip(),
            }
        )

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "schema_version": "motion-comic-factory.character-assets-source.v1",
        "project_id": str(template.get("project_id") or brief.get("project_id", "")),
        "title": str(template.get("title") or brief.get("title", "")),
        "characters": characters,
    }


def write_character_assets_manifest_from_brief(
    brief_path: str | Path,
    output_path: str | Path,
    require_files: bool = False,
) -> Path:
    brief_file = Path(brief_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    data = build_character_assets_manifest_from_brief(
        brief,
        manifest_dir=output.parent,
        require_files=require_files,
    )
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def install_character_references_from_manifest(
    brief_path: str | Path,
    source_manifest_path: str | Path,
    output_path: str | Path,
    asset_source: str = "user_generated_ai",
    overwrite: bool = False,
) -> Path:
    source = asset_source.strip()
    if not is_production_asset_source(source):
        raise ValueError(f"asset_source must be production-ready for visual generation: {asset_source}")

    brief_file = Path(brief_path)
    source_manifest_file = Path(source_manifest_path)
    output = Path(output_path)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    source_manifest = json.loads(source_manifest_file.read_text(encoding="utf-8"))
    source_entries = source_manifest.get("characters", [])
    if not isinstance(source_entries, list):
        raise ValueError("source manifest must contain a characters list")

    template = brief.get("character_assets_template", {})
    install_plan: list[tuple[Path, Path, dict[str, Any]]] = []
    errors: list[str] = []
    for target_entry in _brief_template_entries(brief):
        character_id = str(target_entry.get("character_id", "")).strip()
        name = str(target_entry.get("name", "")).strip()
        label = name or character_id or "unknown character"
        reference_image = str(target_entry.get("reference_image", "")).strip()
        if not reference_image:
            errors.append(f"missing target reference image path for {label}")
            continue

        source_entry = _find_source_reference_entry(source_entries, character_id, name)
        if not source_entry:
            errors.append(f"missing source reference image entry for {label}")
            continue

        source_image_raw = _reference_image_from_entry(source_entry)
        if not source_image_raw:
            errors.append(f"missing source reference image path for {label}")
            continue
        source_image = _resolve_manifest_image_path(source_image_raw, source_manifest_file.parent)
        target_image = _resolve_manifest_image_path(reference_image, brief_file.parent)

        if not is_supported_image_file(source_image):
            errors.append(f"invalid source reference image for {label}: {source_image_raw}")
            continue
        if target_image.exists() and source_image.resolve() != target_image.resolve() and not overwrite:
            errors.append(f"target reference image already exists for {label}: {target_image}")
            continue

        install_plan.append(
            (
                source_image,
                target_image,
                {
                    "character_id": character_id,
                    "name": name,
                    "reference_image": reference_image,
                    "asset_source": source,
                    "provenance_status": provenance_status(source),
                    "production_ready": True,
                    "installed_from": str(source_image.resolve()),
                },
            )
        )

    if errors:
        raise ValueError("; ".join(errors))

    installed_characters: list[dict[str, Any]] = []
    for source_image, target_image, character_record in install_plan:
        target_image.parent.mkdir(parents=True, exist_ok=True)
        if source_image.resolve() != target_image.resolve():
            shutil.copy2(source_image, target_image)
        installed_characters.append(character_record)

    data = {
        "schema_version": "motion-comic-factory.character-assets-source.v1",
        "project_id": str(template.get("project_id") or brief.get("project_id", "")),
        "title": str(template.get("title") or brief.get("title", "")),
        "characters": installed_characters,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def build_confirmed_character_assets_manifest(
    manifest: dict[str, Any],
    manifest_dir: str | Path | None = None,
    asset_source: str = "user_generated_ai",
    require_files: bool = True,
) -> dict[str, Any]:
    source = asset_source.strip()
    if not is_production_asset_source(source):
        raise ValueError(f"asset_source must be production-ready for visual generation: {asset_source}")

    entries = manifest.get("characters", [])
    if not isinstance(entries, list):
        raise ValueError("character asset manifest must contain a characters list")

    manifest_dir_path = Path(manifest_dir or ".").expanduser().resolve()
    characters: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("character asset entry must be an object")
            continue

        reference_image = _reference_image_from_entry(entry)
        name = str(entry.get("name", "")).strip()
        label = name or str(entry.get("character_id", "")).strip() or "unknown character"
        valid_image = False
        if not reference_image:
            errors.append(f"missing confirmed reference image for {label}")
        elif require_files:
            resolved_image = _resolve_manifest_image_path(reference_image, manifest_dir_path)
            if not resolved_image.is_file():
                errors.append(f"missing confirmed reference image for {label}: {reference_image}")
            elif not is_supported_image_file(resolved_image):
                errors.append(f"invalid confirmed reference image for {label}: {reference_image}")
            else:
                valid_image = True

        confirmed_entry = dict(entry)
        confirmed_entry["reference_image"] = reference_image
        confirmed_entry["asset_source"] = source
        confirmed_entry["provenance_status"] = provenance_status(source)
        confirmed_entry["production_ready"] = valid_image if require_files else False
        characters.append(confirmed_entry)

    if errors:
        raise ValueError("; ".join(errors))

    data = dict(manifest)
    data["schema_version"] = str(manifest.get("schema_version") or "motion-comic-factory.character-assets-source.v1")
    data["characters"] = characters
    return data


def write_confirmed_character_assets_manifest(
    manifest_path: str | Path,
    output_path: str | Path,
    asset_source: str = "user_generated_ai",
    require_files: bool = True,
) -> Path:
    manifest_file = Path(manifest_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    data = build_confirmed_character_assets_manifest(
        manifest,
        manifest_dir=manifest_file.parent,
        asset_source=asset_source,
        require_files=require_files,
    )
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def _status_action(
    action_id: str,
    label: str,
    target_path: Path,
    command: str,
) -> dict[str, str]:
    return {
        "id": action_id,
        "label": label,
        "target_path": str(target_path),
        "command": command,
    }


def _character_assets_status_next_actions(
    project_id: str,
    root: Path,
    asset_ready: bool,
    production_ready: bool,
) -> list[dict[str, str]]:
    if production_ready:
        return []

    brief_path = root / "character_generation_brief.json"
    reviewed_dir = root / "reviewed_role_images"
    reviewed_manifest = root / "reviewed_role_images.json"
    from_brief_manifest = root / "character_assets.from_brief.json"
    confirmed_manifest = root / "character_assets.confirmed.json"

    if not asset_ready:
        return [
            _status_action(
                "place_reviewed_role_images",
                "Place reviewed AI role images in the drop folder, named with each character name, then rerun the factory.",
                reviewed_dir,
                f"PROJECT={project_id} scripts/start_factory.sh",
            )
        ]

    return [
        _status_action(
            "confirm_existing_reference_images",
            "If the current target reference images are the final reviewed AI role images, stamp them as production-ready.",
            root / "assets" / "characters",
            (
                f"{PYTHON_BIN} factory_cli.py character-assets-from-brief "
                f"--brief {brief_path} --output {from_brief_manifest} --require-files && "
                f"{PYTHON_BIN} factory_cli.py character-assets-confirm-source "
                f"--manifest {from_brief_manifest} --output {confirmed_manifest}"
            ),
        ),
        _status_action(
            "install_reviewed_role_images",
            "Otherwise place reviewed AI role images in the drop folder and install them as production-ready references.",
            reviewed_dir,
            (
                f"{PYTHON_BIN} factory_cli.py character-assets-reviewed-from-dir "
                f"--brief {brief_path} --image-dir {reviewed_dir} --output {reviewed_manifest} && "
                f"{PYTHON_BIN} factory_cli.py character-assets-install-references "
                f"--brief {brief_path} --source-manifest {reviewed_manifest} "
                f"--output {confirmed_manifest} --overwrite"
            ),
        ),
    ]


def build_character_assets_status_from_brief(
    brief: dict[str, Any],
    asset_root: str | Path | None = None,
) -> dict[str, Any]:
    template = brief.get("character_assets_template", {})
    entries = _brief_template_entries(brief)
    root = Path(asset_root or ".").expanduser().resolve()

    characters: list[dict[str, Any]] = []
    summary = {"ready": 0, "missing": 0, "invalid": 0, "total": len(entries)}
    for entry in entries:
        reference_image = str(entry.get("reference_image", "")).strip()
        resolved_image = _resolve_manifest_image_path(reference_image, root) if reference_image else Path("")
        exists = bool(reference_image) and resolved_image.is_file()
        valid_image = exists and is_supported_image_file(resolved_image)
        asset_source = str(entry.get("asset_source", "")).strip()
        source_provenance_status = provenance_status(asset_source)
        production_ready = valid_image and is_production_asset_source(asset_source)
        if valid_image:
            status = "ready"
        elif exists:
            status = "invalid"
        else:
            status = "missing"
        summary[status] += 1
        characters.append(
            {
                "character_id": str(entry.get("character_id", "")).strip(),
                "name": str(entry.get("name", "")).strip(),
                "reference_image": reference_image,
                "resolved_reference_image_path": str(resolved_image) if reference_image else "",
                "asset_source": asset_source,
                "provenance_status": source_provenance_status,
                "exists": exists,
                "valid_image": valid_image,
                "production_ready": production_ready,
                "status": status,
            }
        )

    asset_ready = summary["ready"] == summary["total"] and summary["total"] > 0
    production_ready = all(item["production_ready"] for item in characters) and bool(characters)
    project_id = str(template.get("project_id") or brief.get("project_id", ""))

    return {
        "schema_version": "motion-comic-factory.character-assets-status.v1",
        "project_id": project_id,
        "title": str(template.get("title") or brief.get("title", "")),
        "asset_root": str(root),
        "asset_ready": asset_ready,
        "production_ready": production_ready,
        "summary": summary,
        "characters": characters,
        "next_actions": _character_assets_status_next_actions(
            project_id,
            root,
            asset_ready,
            production_ready,
        ),
    }


def write_character_assets_status_from_brief(
    brief_path: str | Path,
    output_path: str | Path,
    asset_root: str | Path | None = None,
) -> Path:
    brief_file = Path(brief_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    data = build_character_assets_status_from_brief(
        brief,
        asset_root=asset_root or brief_file.parent,
    )
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
