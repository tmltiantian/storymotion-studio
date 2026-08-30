from __future__ import annotations

import re
from collections.abc import Sequence

from .h3_prompt_compiler import compile_h3_shot_prompt
from .performance_card import PerformanceCard
from .schema import Episode, Shot


_SECTION_LABELS = (
    ("subject_definitions:", "角色与参考图："),
    ("integrated_multimodal_description:", "画面与表演："),
    ("summary:", "镜头摘要："),
    ("retention_analysis:", "角色一致性："),
    ("detailed_description:", "画面与表演："),
    ("overall_soundscape:", "声音设计："),
    ("non_diegetic_music:", "配乐："),
)
_DIALOGUE_TAG = re.compile(r"<d>\[([^\]]+)\]\s*(.*?)</d>")


def compile_seedance_h3_style_prompt(
    episode: Episode,
    shot: Shot,
    *,
    character_ids: Sequence[str],
    reference_character_ids: Sequence[str] = (),
    card: PerformanceCard | None = None,
) -> str:
    """Render H3's shot semantics as Seedance-readable natural language."""
    prompt = compile_h3_shot_prompt(
        episode,
        shot,
        character_ids=character_ids,
        reference_character_ids=reference_character_ids,
        card=card,
    )
    characters = {character.id: character for character in episode.characters}
    for index, character_id in enumerate(reference_character_ids, start=1):
        character = characters[character_id]
        prompt = prompt.replace(f"<Subject {index}>", character.name)
        prompt = prompt.replace(
            f"<Picture {index}>", f"{character.name}的角色参考图"
        )
    for source, target in _SECTION_LABELS:
        prompt = prompt.replace(source, target)
    prompt = _DIALOGUE_TAG.sub(
        lambda match: f"台词（{match.group(1)}）：“{match.group(2)}”", prompt
    )
    return prompt.replace("[reference generation] ", "").strip()
