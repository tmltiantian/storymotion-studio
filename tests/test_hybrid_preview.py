import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from factory.hybrid_preview import (
    HybridShotSource,
    build_hybrid_preview_ffmpeg_command,
    render_hybrid_preview_video,
    select_hybrid_shot_sources,
)
from factory.novel_planner import plan_episode
from tests.media_fixtures import VALID_VIDEO_MP4


MINIMAL_MP4 = VALID_VIDEO_MP4


def _fixture(tmp_path: Path):
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "hybrid_sample",
        target_shots=2,
    )
    cards = []
    for shot in episode.shots:
        card = tmp_path / "cards" / f"{shot.id}.png"
        card.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 1920), "navy").save(card)
        cards.append(card)
    clip = tmp_path / "clips" / "shot_001.mp4"
    clip.parent.mkdir(parents=True)
    package = {
        "target": {"resolution": "1080x1920", "fps": 30},
        "timeline": [
            {
                "shot_id": shot.id,
                "index": shot.index,
                "expected_assets": {
                    "video_clip": str(
                        clip if shot.index == 1 else tmp_path / "clips" / f"{shot.id}.mp4"
                    )
                },
            }
            for shot in episode.shots
        ],
    }
    return episode, cards, clip, package


def test_hybrid_preview_selects_valid_video_and_falls_back_to_card(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)

    sources = select_hybrid_shot_sources(episode, package, cards)

    assert [source.kind for source in sources] == ["video", "card"]
    assert sources[0].path == clip
    assert sources[1].path == cards[1]
    assert sources[1].fallback_reason == "video_missing"


def test_hybrid_preview_rejects_invalid_video_and_falls_back_to_card(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(b"not-an-mp4")

    sources = select_hybrid_shot_sources(episode, package, cards)

    assert sources[0].kind == "card"
    assert sources[0].fallback_reason == "video_invalid"


def test_hybrid_preview_command_normalizes_and_concatenates_every_shot(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)
    sources = select_hybrid_shot_sources(episode, package, cards)

    command = build_hybrid_preview_ffmpeg_command(
        sources=sources,
        resolution="1080x1920",
        fps=30,
        motion_cadence_fps=12,
        output_path=tmp_path / "hybrid.mp4",
    )
    joined = " ".join(command)

    assert command[0] == "ffmpeg"
    assert str(clip) in command
    assert str(cards[1]) in command
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in joined
    assert "fps=12,fps=30" in joined
    assert "tpad=stop_mode=clone" in joined
    assert "concat=n=2:v=1:a=0" in joined
    assert command[-1] == str(tmp_path / "hybrid.mp4")


def test_hybrid_preview_command_applies_drop_ranges_and_source_end(tmp_path):
    clip_a = tmp_path / "shot-a.mp4"
    clip_b = tmp_path / "shot-b.mp4"
    sources = [
        HybridShotSource(
            shot_id="shot_004",
            index=4,
            duration_seconds=7.5,
            kind="video",
            path=clip_a,
            drop_ranges_seconds=((1.8, 3.0),),
        ),
        HybridShotSource(
            shot_id="shot_006",
            index=6,
            duration_seconds=7.5,
            kind="video",
            path=clip_b,
            source_end_seconds=6.5,
        ),
    ]

    command = build_hybrid_preview_ffmpeg_command(
        sources=sources,
        resolution="1080x1920",
        fps=30,
        output_path=tmp_path / "hybrid.mp4",
    )
    filters = command[command.index("-filter_complex") + 1]

    assert "trim=start=0.000:end=1.800" in filters
    assert "trim=start=3.000" in filters
    assert "concat=n=2:v=1:a=0" in filters
    assert "trim=start=0.000:end=6.500" in filters


def test_render_hybrid_preview_writes_material_usage_report(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)
    package_path = tmp_path / "openmontage_package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    output = tmp_path / "hybrid.mp4"
    report_path = tmp_path / "hybrid_report.json"
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(MINIMAL_MP4)

    report = render_hybrid_preview_video(
        episode,
        package_path=package_path,
        cards=cards,
        output_path=output,
        report_path=report_path,
        command_runner=fake_runner,
    )

    assert commands
    assert report["success"] is True
    assert report["dynamic_shot_count"] == 1
    assert report["fallback_shot_count"] == 1
    assert report["shots"][0]["source_kind"] == "video"
    assert report_path.exists()


def test_failed_hybrid_render_preserves_previous_output(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)
    package_path = tmp_path / "openmontage_package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    output = tmp_path / "hybrid.mp4"
    output.write_bytes(b"last-good")

    def failed_runner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, command, stderr="render failed")

    with pytest.raises(Exception, match="render failed"):
        render_hybrid_preview_video(
            episode,
            package_path=package_path,
            cards=cards,
            output_path=output,
            report_path=tmp_path / "report.json",
            command_runner=failed_runner,
        )

    assert output.read_bytes() == b"last-good"


def test_render_hybrid_preview_loads_adjacent_edit_decisions(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)
    package["project_id"] = episode.project_id
    package_path = tmp_path / "openmontage_package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    (tmp_path / "edit_decisions.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.edit-decisions.v1",
                "project_id": episode.project_id,
                "shots": {
                    episode.shots[0].id: {
                        "source_end_seconds": 0.5,
                        "note": "freeze before generated text",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(command, **kwargs):
        Path(command[-1]).write_bytes(MINIMAL_MP4)

    report = render_hybrid_preview_video(
        episode,
        package_path=package_path,
        cards=cards,
        output_path=tmp_path / "hybrid.mp4",
        report_path=tmp_path / "hybrid-report.json",
        command_runner=fake_runner,
    )

    assert report["edit_decisions_applied"] is True
    assert report["edit_decision_count"] == 1
    assert report["shots"][0]["source_end_seconds"] == 0.5
    assert report["shots"][0]["edit_note"] == "freeze before generated text"


def test_hybrid_render_wraps_oserror_and_preserves_previous_output(tmp_path):
    episode, cards, clip, package = _fixture(tmp_path)
    clip.write_bytes(MINIMAL_MP4)
    package_path = tmp_path / "openmontage_package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    output = tmp_path / "hybrid.mp4"
    output.write_bytes(b"last-good")

    def failed_runner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise OSError("ffmpeg missing")

    with pytest.raises(Exception, match="Unable to run hybrid preview FFmpeg"):
        render_hybrid_preview_video(
            episode,
            package_path=package_path,
            cards=cards,
            output_path=output,
            report_path=tmp_path / "report.json",
            command_runner=failed_runner,
        )

    assert output.read_bytes() == b"last-good"
