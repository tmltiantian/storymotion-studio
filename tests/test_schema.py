from factory.novel_planner import plan_episode
from factory.schema import DialogueLine, Shot, validate_episode


def test_episode_from_sample_validates():
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。电影院亮起灯。",
        project_id="unit_sample",
        target_shots=3,
    )

    assert validate_episode(episode) == []
    assert len(episode.characters) == 2
    assert len(episode.shots) == 3


def test_dialogue_unknown_speaker_fails_validation():
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。电影院亮起灯。",
        project_id="bad_speaker",
        target_shots=2,
    )
    bad_shot = Shot(
        id="bad",
        index=99,
        scene_title="bad",
        action="bad",
        visual_prompt="bad",
        camera="static",
        duration_seconds=1,
        audio_mood="none",
        dialogue=[DialogueLine(speaker_id="ghost", text="hello")],
    )
    broken = episode.__class__(
        project_id=episode.project_id,
        title=episode.title,
        language=episode.language,
        style=episode.style,
        target_aspect_ratio=episode.target_aspect_ratio,
        target_resolution=episode.target_resolution,
        characters=episode.characters,
        shots=[*episode.shots, bad_shot],
    )

    errors = validate_episode(broken)
    assert any("ghost" in error for error in errors)
