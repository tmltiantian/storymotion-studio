from factory.novel_planner import plan_episode
from factory.schema import DialogueLine, Shot, episode_from_dict, episode_to_dict, validate_episode


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


def test_legacy_episode_without_character_ids_migrates_to_all_characters() -> None:
    episode = plan_episode("林澈推开门。苏眠回头。", "legacy", target_shots=2)
    payload = episode_to_dict(episode)
    for shot in payload["shots"]:
        shot.pop("character_ids")

    restored = episode_from_dict(payload)

    expected = [character.id for character in restored.characters]
    assert all(shot.character_ids == expected for shot in restored.shots)


def test_explicit_character_free_shot_is_valid_without_dialogue() -> None:
    episode = plan_episode("林澈推开门。苏眠回头。", "insert", target_shots=2)
    episode.shots[0] = Shot(
        id="insert",
        index=1,
        scene_title="手机特写",
        action="手机屏幕亮起",
        visual_prompt="只有桌面和手机",
        camera="static",
        duration_seconds=4,
        audio_mood="room tone",
        character_ids=[],
        dialogue=[],
    )

    assert validate_episode(episode) == []
