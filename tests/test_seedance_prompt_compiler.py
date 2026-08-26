from factory.seedance_prompt_compiler import compile_seedance_h3_style_prompt
from factory.schema import Character, DialogueLine, Episode, Shot


def _episode() -> Episode:
    return Episode(
        project_id="seedance_prompt",
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
                "white facial blaze and four white paws",
                "cool young female voice",
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
                [DialogueLine("doubao", "今天也要加油！", "bright")],
            )
        ],
    )


def test_seedance_prompt_preserves_h3_structure_without_h3_only_tags() -> None:
    episode = _episode()

    prompt = compile_seedance_h3_style_prompt(
        episode,
        episode.shots[0],
        character_ids=("doubao", "mitao"),
        reference_character_ids=("doubao", "mitao"),
    )

    sections = ("角色与参考图：", "镜头摘要：", "角色一致性：", "画面与表演：", "声音设计：")
    positions = [prompt.index(section) for section in sections]
    assert positions == sorted(positions)
    assert "豆包" in prompt and "蜜桃" in prompt
    assert "readable causal beats" in prompt
    assert "今天也要加油！" in prompt
    assert "<Subject" not in prompt
    assert "<Picture" not in prompt
    assert "<d>" not in prompt
