from dataclasses import replace

import pytest

from factory.schema import Character, DialogueLine, Episode, Shot
from factory.visual_timeline import MicroShot, VisualTimeline


@pytest.fixture
def episode() -> Episode:
    return Episode(
        project_id="performable_episode",
        title="Performable episode",
        language="en",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character("wukong", "Wukong", "lead", "alert", "gold armor", "low"),
            Character("yangjian", "Yangjian", "lead", "calm", "silver armor", "low"),
            Character("nezha", "Nezha", "lead", "focused", "red armor", "low"),
        ],
        shots=[
            Shot(
                "scene_001", 1, "Gate", "Wukong presses the gate.", "A gate.",
                "static", 4.0, "tense",
                dialogue=[DialogueLine("wukong", "Open the gate.")],
                character_ids=["wukong", "yangjian"],
            )
        ],
    )


@pytest.fixture
def timeline() -> VisualTimeline:
    return VisualTimeline(
        project_id="performable_episode",
        micro_shots=(
            MicroShot(
                "micro_001", 1, "scene_001", "Gate", "source-unspecified",
                "action", ("wukong", "yangjian"), "alert", "focused", 3,
                "at the gate", "before the gate", "at the gate", "wukong", "grasp",
                "gate", "locked", 4, 4.0, "hard_cut", "hard_cut", ("no_text",), 8,
            ),
        ),
    )


def make_card(micro_shot_id: str = "micro_001", **changes):
    from factory.performance_card import PerformanceCard

    card = PerformanceCard(
        micro_shot_id=micro_shot_id,
        purpose="speak",
        speaker_id="wukong",
        dialogue_id="scene_001.dialogue_01",
        requires_visible_lipsync=True,
        entry_anchor_id="scene_gate",
        scene_keyframe_id="kf_gate",
        actor_id="wukong",
        target_id="yangjian",
        contact_point="gate handle",
        prop_hand="right hand",
        start_beat="settles before the gate",
        main_beat="presses the gate handle",
        end_beat="holds eye contact",
        negative_constraints=("no_floating",),
    )
    return replace(card, **changes)


def test_visible_speech_requires_a_unique_dialogue_and_speaker(episode, timeline):
    from factory.performance_card import PerformanceSheet, validate_performance_sheet

    card = make_card(dialogue_id="", speaker_id="")
    sheet = PerformanceSheet(project_id=episode.project_id, cards=(card,))

    errors = validate_performance_sheet(sheet, episode, timeline)

    assert "micro_001 visible speech requires dialogue_id" in errors
    assert "micro_001 visible speech requires speaker_id" in errors
    assert "micro_001 dialogue_id does not identify one source line" in errors


def test_contact_card_rejects_multiple_people_and_missing_contact_point(episode, timeline):
    from factory.performance_card import PerformanceSheet, validate_performance_sheet

    card = make_card(purpose="action", requires_visible_lipsync=False, dialogue_id="", speaker_id="", contact_point="")
    crowded_timeline = replace(
        timeline,
        micro_shots=(replace(timeline.micro_shots[0], character_ids=("wukong", "yangjian", "nezha")),),
    )
    sheet = PerformanceSheet(project_id=episode.project_id, cards=(card,))

    errors = validate_performance_sheet(sheet, episode, crowded_timeline)

    assert "micro_001 has more than two characters" in errors
    assert "micro_001 contact action requires exactly one contact_point" in errors


def test_sheet_requires_one_card_for_each_microshot(episode, timeline):
    from factory.performance_card import PerformanceSheet, validate_performance_sheet

    sheet = PerformanceSheet(project_id=episode.project_id, cards=())

    assert validate_performance_sheet(sheet, episode, timeline) == [
        "performance cards must match visual-timeline microshot ids"
    ]


def test_contact_card_requires_an_actor(episode, timeline):
    from factory.performance_card import PerformanceSheet, validate_performance_sheet

    sheet = PerformanceSheet(
        project_id=episode.project_id,
        cards=(make_card(purpose="action", requires_visible_lipsync=False, dialogue_id="", speaker_id="", actor_id=""),),
    )

    assert "micro_001 contact action requires exactly one actor_id" in validate_performance_sheet(
        sheet, episode, timeline
    )
