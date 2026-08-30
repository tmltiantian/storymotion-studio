from factory.h3_prompt_compiler import compile_h3_shot_prompt
from factory.performance_card import PerformanceCard
from factory.schema import Character, DialogueLine, Episode, Shot


def _episode(*, narrator: bool = False) -> Episode:
    dialogue = [
        DialogueLine(
            "narrator" if narrator else "doubao",
            "今天也要加油！",
            "bright",
        )
    ]
    return Episode(
        project_id="cat_interview",
        title="猫咪面试",
        language="zh-CN",
        style="warm live-action pet sitcom",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character(
                "doubao",
                "豆包",
                "lead",
                "a slender black-and-white tuxedo cat with green eyes",
                "white facial blaze, white chest bib, and four white paws",
                "cool young female voice, brisk natural pace",
            ),
            Character(
                "mitao",
                "蜜桃",
                "support",
                "a round orange tabby kitten with amber eyes",
                "round face and clear orange stripes",
                "cute lively young female voice",
            ),
        ],
        shots=[
            Shot(
                "shot_001",
                1,
                "warm wooden living room",
                "豆包 lifts one front paw, taps the closed notebook once, then looks at 蜜桃.",
                "Medium close shot of 豆包 beside a closed notebook on a low wooden table.",
                "slow small-amplitude push in",
                6.0,
                "quiet room tone with one soft paw tap",
                dialogue,
            )
        ],
    )


def test_reference_prompt_uses_official_six_sections_in_order():
    episode = _episode()

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao", "mitao"),
        reference_character_ids=("doubao", "mitao"),
    )

    fields = (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    )
    positions = [prompt.index(field) for field in fields]
    assert positions == sorted(positions)
    assert "<Subject 1>" in prompt
    assert "<Picture 1>" in prompt
    assert "<Subject 2>" in prompt
    assert "<Picture 2>" in prompt
    assert "[reference generation]" in prompt


def test_character_dialogue_preserves_text_and_defines_lip_window():
    episode = _episode()

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao",),
        reference_character_ids=("doubao",),
    )

    assert "<Subject 1> (S1)" in prompt
    assert "<d>[Chinese] 今天也要加油！</d>" in prompt
    assert "mouth movements naturally synchronized" in prompt
    assert "lips close completely when the line ends" in prompt


def test_narration_keeps_every_visible_character_mouth_closed():
    episode = _episode(narrator=True)

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao", "mitao"),
        reference_character_ids=("doubao", "mitao"),
    )

    assert "says in an off-screen voiceover" in prompt
    assert "<d>[Chinese] 今天也要加油！</d>" in prompt
    assert "豆包 and 蜜桃 keep their lips completely closed" in prompt


def test_text_only_prompt_uses_official_three_sections_without_reference_labels():
    episode = _episode()

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao",),
    )

    assert prompt.startswith("integrated_multimodal_description: [Shot 1]")
    assert "overall_soundscape:" in prompt
    assert "non_diegetic_music:" in prompt
    assert "subject_definitions:" not in prompt
    assert "<Subject" not in prompt


def test_speaker_ids_remain_stable_across_episode_shots():
    episode = _episode(narrator=True)
    second = Shot(
        "shot_002",
        2,
        "warm wooden living room",
        "豆包 turns toward the camera.",
        "Close shot of 豆包 beside the same low wooden table.",
        "static",
        4.0,
        "quiet room tone",
        [DialogueLine("doubao", "轮到我啦。", "cool")],
    )
    episode = Episode(
        project_id=episode.project_id,
        title=episode.title,
        language=episode.language,
        style=episode.style,
        target_aspect_ratio=episode.target_aspect_ratio,
        target_resolution=episode.target_resolution,
        characters=episode.characters,
        shots=[episode.shots[0], second],
    )

    prompt = compile_h3_shot_prompt(
        episode,
        second,
        character_ids=("doubao",),
        reference_character_ids=("doubao",),
    )

    assert "<Subject 1> (S2)" in prompt


def test_motion_prompt_requires_causal_beats_without_uniform_smoothing():
    episode = _episode()

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao", "mitao"),
        reference_character_ids=("doubao", "mitao"),
    )

    assert "readable causal beats" in prompt
    assert "brief reaction hold" in prompt
    assert "support or contact point" in prompt
    assert "weight transfer" in prompt
    assert "result settles before the next major action begins" in prompt
    assert "not uniformly smooth" in prompt
    assert "constant-speed tweening" in prompt
    assert "floating, gliding, teleporting" in prompt


def test_h3_prompt_uses_shared_performance_card_clauses():
    episode = _episode()
    card = PerformanceCard(
        micro_shot_id="micro_001",
        purpose="action",
        speaker_id="",
        dialogue_id="",
        requires_visible_lipsync=False,
        entry_anchor_id="living_room_entry",
        scene_keyframe_id="living_room_keyframe",
        actor_id="doubao",
        target_id="notebook",
        contact_point="notebook cover",
        prop_hand="front paw",
        start_beat="paw settles beside the notebook",
        main_beat="presses the notebook cover once",
        end_beat="returns to a stable stance",
        negative_constraints=("no_floating",),
    )

    prompt = compile_h3_shot_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao", "mitao"),
        reference_character_ids=("doubao", "mitao"),
        card=card,
    )

    assert "Performance beats: start paw settles beside the notebook" in prompt
    assert "one visible contact at notebook cover; no second contact" in prompt
    assert "no uniform gliding" in prompt
