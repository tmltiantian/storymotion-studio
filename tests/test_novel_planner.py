from pathlib import Path

from factory.novel_planner import (
    extract_main_character_names,
    plan_episode,
    read_novel,
)


def test_extracts_two_sample_characters():
    text = Path("samples/sample_novel.txt").read_text(encoding="utf-8")

    assert extract_main_character_names(text) == ["林澈", "苏眠"]


def test_planner_creates_monotonic_shots_and_dialogue():
    episode = plan_episode(read_novel("samples/sample_novel.txt"), "sample_episode", target_shots=8)

    assert [shot.index for shot in episode.shots] == sorted(shot.index for shot in episode.shots)
    assert any(line.speaker_id == "narrator" for shot in episode.shots for line in shot.dialogue)
    character_ids = {character.id for character in episode.characters}
    assert any(line.speaker_id in character_ids for shot in episode.shots for line in shot.dialogue)


def test_planner_extracts_spoken_dialogue_from_story_text():
    episode = plan_episode(
        "苏眠站在街灯下。她低声说，最后一班车不是开往城外，而是开往十年前。",
        "dialogue_sample",
        target_shots=2,
    )

    lines = [line.text for shot in episode.shots for line in shot.dialogue]
    assert "最后一班车不是开往城外，而是开往十年前" in lines


def test_planner_does_not_invent_character_dialogue_for_narration_only_beats():
    episode = plan_episode(
        "林澈推开便利店的门，柜台上放着一封黑色信封。",
        "narration_only",
        target_shots=1,
    )

    assert [line.speaker_id for line in episode.shots[0].dialogue] == ["narrator"]


def test_planner_separates_spoken_dialogue_from_narration_and_sizes_the_shot():
    spoken = "最后一班车不是开往城外，而是开往十年前"
    episode = plan_episode(
        f"苏眠站在街灯下，手里握着被雨水泡软的车票。她低声说，{spoken}。",
        "dialogue_timing",
        target_shots=1,
    )

    shot = episode.shots[0]
    narrator_lines = [
        line.text for line in shot.dialogue if line.speaker_id == "narrator"
    ]
    assert spoken not in "".join(narrator_lines)
    assert any(line.text == spoken for line in shot.dialogue)
    assert 7.5 < shot.duration_seconds <= 15.0


def test_original_pet_idea_uses_cat_roles_and_expands_to_requested_beats():
    episode = plan_episode(
        "两只猫在原木风客厅发现一个会响的纸盒，先观察，再分工调查。",
        "original_cats",
        target_shots=6,
        content_mode="original",
    )

    assert [character.name for character in episode.characters] == ["奶糖", "豆包"]
    assert len(episode.shots) == 6
    assert all("cat" in character.visual_anchor for character in episode.characters)
    assert all("林澈" not in shot.visual_prompt for shot in episode.shots)
