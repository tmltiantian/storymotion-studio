from factory.novel_planner import plan_episode
from factory.placeholder_renderer import (
    build_placeholder_ffmpeg_command,
    episode_duration_seconds,
)


def test_episode_duration_seconds_sums_shots():
    episode = plan_episode("林澈推开门。苏眠低声说，别急。", "duration_sample", target_shots=2)

    assert episode_duration_seconds(episode) == 15.0


def test_build_placeholder_ffmpeg_command_maps_video_audio_and_subtitles():
    cmd = build_placeholder_ffmpeg_command(
        subtitles_path="/tmp/subtitles.srt",
        output_path="/tmp/out.mp4",
        duration_seconds=12.5,
        resolution="1080x1920",
        fps=30,
    )

    assert cmd[0] == "ffmpeg"
    assert "color=c=0x111827:s=1080x1920:r=30:d=12.500" in cmd
    assert "-c:s" in cmd
    assert "mov_text" in cmd
    assert cmd[-1] == "/tmp/out.mp4"
