from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .schema import Episode, NARRATOR_ID


CARD_BACKGROUND = (17, 24, 39)
CARD_PANEL = (31, 41, 55)
CARD_ACCENT = (56, 189, 248)
CARD_TEXT = (248, 250, 252)
CARD_MUTED = (203, 213, 225)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_gap: int,
    max_lines: int,
) -> int:
    x, y = xy
    lines = _wrap_text(draw, text, font, max_width)[:max_lines]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        _, top, _, bottom = draw.textbbox((x, y), line, font=font)
        y += bottom - top + line_gap
    return y


def _speaker_name(episode: Episode, speaker_id: str) -> str:
    if speaker_id == NARRATOR_ID:
        return "旁白"
    for character in episode.characters:
        if character.id == speaker_id:
            return character.name
    return speaker_id


def _load_character_portraits(
    episode: Episode,
    character_assets: dict[str, Any] | None,
) -> list[tuple[str, Image.Image]]:
    if not character_assets or character_assets.get("production_ready") is not True:
        return []

    entries = [item for item in character_assets.get("characters", []) if isinstance(item, dict)]
    portraits: list[tuple[str, Image.Image]] = []
    for character in episode.characters:
        entry = next(
            (
                item
                for item in entries
                if str(item.get("character_id", "")).strip() == character.id
                or str(item.get("name", "")).strip() == character.name
            ),
            None,
        )
        if not entry or entry.get("production_ready") is not True:
            continue
        path = Path(str(entry.get("reference_image_path", ""))).expanduser()
        if not path.is_file():
            continue
        try:
            with Image.open(path) as source:
                portraits.append((character.name, source.convert("RGB")))
        except (OSError, ValueError):
            continue
    return portraits[:2]


def _draw_character_portraits(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    portraits: list[tuple[str, Image.Image]],
    name_font: ImageFont.ImageFont,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    gap = 12
    count = len(portraits)
    tile_width = (right - left - gap * (count - 1)) // count
    tile_height = bottom - top
    for index, (name, portrait) in enumerate(portraits):
        x = left + index * (tile_width + gap)
        fitted = ImageOps.fit(
            portrait,
            (tile_width, tile_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.34),
        )
        image.paste(fitted, (x, top))
        draw.rectangle((x, top, x + tile_width, bottom), outline=CARD_ACCENT, width=2)
        draw.rectangle((x + 2, bottom - 66, x + tile_width - 2, bottom - 2), fill=(17, 24, 39))
        draw.text((x + 18, bottom - 58), name, font=name_font, fill=CARD_TEXT)


def render_shot_cards(
    episode: Episode,
    output_dir: str | Path,
    character_assets: dict[str, Any] | None = None,
) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    width, height = [int(part) for part in episode.target_resolution.split("x", 1)]
    title_font = _load_font(56)
    label_font = _load_font(34)
    body_font = _load_font(38)
    portrait_body_font = _load_font(32)
    small_font = _load_font(28)
    portraits = _load_character_portraits(episode, character_assets)

    cards: list[Path] = []
    for shot in episode.shots:
        image = Image.new("RGB", (width, height), CARD_BACKGROUND)
        draw = ImageDraw.Draw(image)

        margin = 72
        draw.rounded_rectangle(
            (margin, margin, width - margin, height - margin),
            radius=38,
            fill=CARD_PANEL,
            outline=CARD_ACCENT,
            width=4,
        )
        draw.text((margin + 42, margin + 42), episode.title, font=small_font, fill=CARD_MUTED)
        draw.text(
            (margin + 42, margin + 96),
            f"{shot.index:02d} / {len(episode.shots)}  {shot.scene_title}",
            font=title_font,
            fill=CARD_TEXT,
        )
        draw.line((margin + 42, margin + 178, width - margin - 42, margin + 178), fill=CARD_ACCENT, width=3)

        if portraits:
            _draw_character_portraits(
                image,
                draw,
                portraits,
                small_font,
                left=margin + 42,
                top=margin + 210,
                right=width - margin - 42,
                bottom=900,
            )
            y = 940
            content_font = portrait_body_font
            action_max_lines = 4
            camera_max_lines = 2
            dialogue_max_lines = 2
            section_gap = 30
            dialogue_gap = 14
            dialogue_limit = 2
        else:
            y = margin + 230
            content_font = body_font
            action_max_lines = 6
            camera_max_lines = 2
            dialogue_max_lines = 3
            section_gap = 56
            dialogue_gap = 22
            dialogue_limit = 3

        draw.text((margin + 42, y), "动作", font=label_font, fill=CARD_ACCENT)
        y += 52
        y = _draw_wrapped(
            draw,
            shot.action,
            (margin + 42, y),
            content_font,
            CARD_TEXT,
            width - (margin + 42) * 2,
            line_gap=12,
            max_lines=action_max_lines,
        )

        y += section_gap
        draw.text((margin + 42, y), "镜头", font=label_font, fill=CARD_ACCENT)
        y += 52
        y = _draw_wrapped(
            draw,
            f"{shot.camera} / {shot.duration_seconds:.1f}s",
            (margin + 42, y),
            content_font,
            CARD_TEXT,
            width - (margin + 42) * 2,
            line_gap=12,
            max_lines=camera_max_lines,
        )

        y += section_gap
        draw.text((margin + 42, y), "对白", font=label_font, fill=CARD_ACCENT)
        y += 52
        for line in shot.dialogue[:dialogue_limit]:
            y = _draw_wrapped(
                draw,
                f"{_speaker_name(episode, line.speaker_id)}：{line.text}",
                (margin + 42, y),
                content_font,
                CARD_TEXT if line.speaker_id != NARRATOR_ID else CARD_MUTED,
                width - (margin + 42) * 2,
                line_gap=12,
                max_lines=dialogue_max_lines,
            )
            y += dialogue_gap

        footer = "Motion Comic Factory / dry-run storyboard card"
        draw.text((margin + 42, height - margin - 78), footer, font=small_font, fill=CARD_MUTED)

        card_path = output / f"{shot.id}.png"
        image.save(card_path)
        cards.append(card_path)

    return cards


def write_concat_manifest(episode: Episode, cards: list[Path], output_path: str | Path) -> Path:
    if len(cards) != len(episode.shots):
        raise ValueError("card count must match shot count")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for shot, card in zip(episode.shots, cards):
        lines.append(f"file {shlex.quote(str(card))}")
        lines.append(f"duration {shot.duration_seconds:.3f}")
    if cards:
        lines.append(f"file {shlex.quote(str(cards[-1]))}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def build_card_video_ffmpeg_command(
    *,
    manifest_path: str | Path,
    subtitles_path: str | Path,
    output_path: str | Path,
    fps: int,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-i",
        str(subtitles_path),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-vf",
        f"fps={fps},format=yuv420p",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-c:s",
        "mov_text",
        "-shortest",
        str(output_path),
    ]


def render_card_preview_video(
    episode: Episode,
    subtitles_path: str | Path,
    output_path: str | Path,
    cards_dir: str | Path,
    character_assets: dict[str, Any] | None = None,
    fps: int = 30,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    cards = render_shot_cards(episode, cards_dir, character_assets=character_assets)
    manifest = write_concat_manifest(episode, cards, Path(cards_dir) / "cards.ffconcat")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_card_video_ffmpeg_command(
        manifest_path=manifest,
        subtitles_path=subtitles_path,
        output_path=output,
        fps=fps,
        ffmpeg_bin=ffmpeg_bin,
    )
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return output
