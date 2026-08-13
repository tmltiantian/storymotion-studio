from pathlib import Path

from PIL import Image

from factory.novel_planner import plan_episode
from factory.shot_card_renderer import (
    build_card_video_ffmpeg_command,
    render_shot_cards,
    write_concat_manifest,
)


def test_render_shot_cards_creates_one_png_per_shot(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "cards_sample", target_shots=2)

    cards = render_shot_cards(episode, tmp_path)

    assert len(cards) == len(episode.shots)
    for card in cards:
        assert card.exists()
        assert card.suffix == ".png"
        assert card.stat().st_size > 1000


def test_render_shot_cards_uses_production_character_references(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "cards_with_roles", target_shots=2)
    reference_dir = tmp_path / "references"
    reference_dir.mkdir()
    colors = [(220, 40, 40), (40, 200, 80)]
    characters = []
    for character, color in zip(episode.characters, colors):
        path = reference_dir / f"{character.id}.png"
        Image.new("RGB", (480, 960), color).save(path)
        characters.append(
            {
                "character_id": character.id,
                "name": character.name,
                "reference_image_path": str(path),
                "reference_image_exists": True,
                "production_ready": True,
            }
        )

    cards = render_shot_cards(
        episode,
        tmp_path / "cards",
        character_assets={"production_ready": True, "characters": characters},
    )

    with Image.open(cards[0]) as card:
        left = card.getpixel((320, 520))
        right = card.getpixel((760, 520))
    assert left[0] > left[1] * 3
    assert right[1] > right[0] * 3


def test_write_concat_manifest_preserves_durations(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "manifest_sample", target_shots=2)
    cards = [tmp_path / "shot_001.png", tmp_path / "shot_002.png"]
    for card in cards:
        card.write_bytes(b"fake")

    manifest = write_concat_manifest(episode, cards, tmp_path / "cards.ffconcat")

    text = manifest.read_text(encoding="utf-8")
    assert "ffconcat version 1.0" in text
    assert "duration 7.500" in text
    assert "shot_001.png" in text


def test_build_card_video_ffmpeg_command_uses_concat_manifest():
    cmd = build_card_video_ffmpeg_command(
        manifest_path="/tmp/cards.ffconcat",
        subtitles_path="/tmp/subtitles.srt",
        output_path="/tmp/card_preview.mp4",
        fps=30,
    )

    assert cmd[0] == "ffmpeg"
    assert "/tmp/cards.ffconcat" in cmd
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert cmd[-1] == "/tmp/card_preview.mp4"
