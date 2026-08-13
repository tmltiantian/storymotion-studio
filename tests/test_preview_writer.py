from pathlib import Path

import factory.preview_writer as preview_writer
from factory.novel_planner import plan_episode
from factory.preview_writer import format_srt_time, write_preview_artifacts


def test_format_srt_time():
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(7.5) == "00:00:07,500"
    assert format_srt_time(3661.25) == "01:01:01,250"


def test_write_preview_artifacts(tmp_path: Path):
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "preview_sample", target_shots=2)

    paths = write_preview_artifacts(episode, tmp_path)

    storyboard = Path(paths["storyboard_preview"])
    subtitles = Path(paths["subtitles_srt"])
    snapshot = Path(paths["episode_snapshot"])
    assert storyboard.exists()
    assert subtitles.exists()
    assert snapshot.exists()
    assert "林澈" in storyboard.read_text(encoding="utf-8")
    assert "-->" in subtitles.read_text(encoding="utf-8")


def test_write_timed_subtitles_uses_measured_voice_boundaries(tmp_path: Path):
    episode = plan_episode(
        "苏眠站在街灯下。她低声说，别急。",
        "timed_subtitles",
        target_shots=1,
    )
    timings = [
        {
            "shot_id": episode.shots[0].id,
            "speaker_id": line.speaker_id,
            "start_seconds": start,
            "end_seconds": end,
        }
        for line, start, end in zip(
            episode.shots[0].dialogue,
            [0.4, 2.7],
            [2.4, 6.9],
        )
    ]

    assert hasattr(preview_writer, "write_timed_subtitles")
    output = preview_writer.write_timed_subtitles(
        episode,
        timings,
        tmp_path / "subtitles.srt",
    )

    text = output.read_text(encoding="utf-8")
    assert "00:00:00,400 --> 00:00:02,400" in text
    assert "00:00:02,700 --> 00:00:06,900" in text
