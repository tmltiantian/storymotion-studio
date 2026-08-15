from pathlib import Path

from factory.novel_planner import plan_episode
from factory.shot_audio import write_shot_audio_assets


def test_write_shot_audio_assets_extracts_only_on_screen_dialogue_shots(
    tmp_path: Path,
) -> None:
    episode = plan_episode(
        "林澈说，快走。苏眠看向门外。",
        "shot_audio",
        target_shots=2,
    )
    source = tmp_path / "voiceover.m4a"
    source.write_bytes(b"audio")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"wav")

    assets = write_shot_audio_assets(
        episode,
        source,
        tmp_path / "shots",
        command_runner=fake_run,
    )

    talking = [
        shot
        for shot in episode.shots
        if any(line.speaker_id != "narrator" for line in shot.dialogue)
    ]
    assert set(assets) == {shot.id for shot in talking}
    assert len(calls) == len(talking)
    assert all("-ss" in command and "-t" in command for command in calls)
    assert all(path.is_file() for path in assets.values())
