from __future__ import annotations

import math
import re
from pathlib import Path

from .schema import Character, DialogueLine, Episode, NARRATOR_ID, Shot, assert_valid_episode


DEFAULT_CHARACTER_NAMES = ("林澈", "苏眠")
DEFAULT_ORIGINAL_NAMES = ("主角A", "主角B")
NAME_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,3})(?:说|问|喊|低声|抬头|推开|站在|看见|握住|停下)")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?])")
DIALOGUE_PATTERN = re.compile(r"(?:说|问|喊|低声说)[，,:：](.+?)(?:[。！？!?]|$)")
BASE_SHOT_DURATION_SECONDS = 7.5
MAX_SHOT_DURATION_SECONDS = 15.0
VOICE_CHARS_PER_SECOND = 4.5


def read_novel(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _slug_id(name: str, index: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "", name).strip()
    return f"char_{index}_{safe}" if safe else f"char_{index}"


def extract_main_character_names(text: str, limit: int = 2) -> list[str]:
    seen: list[str] = []
    for match in NAME_PATTERN.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            return seen

    for name in DEFAULT_CHARACTER_NAMES:
        if name in text and name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            return seen

    for name in DEFAULT_CHARACTER_NAMES:
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            return seen
    return seen


def split_story_beats(text: str, target_count: int) -> list[str]:
    raw_parts = [part.strip() for part in SENTENCE_SPLIT_PATTERN.split(text) if part.strip()]
    if not raw_parts:
        return [text.strip()]
    if len(raw_parts) <= target_count:
        return raw_parts

    beats: list[str] = []
    bucket: list[str] = []
    target_chars = max(38, len(text) // max(1, target_count))

    for part in raw_parts:
        bucket.append(part)
        if sum(len(item) for item in bucket) >= target_chars and len(beats) < target_count - 1:
            beats.append("".join(bucket))
            bucket = []
    if bucket:
        beats.append("".join(bucket))

    return beats[:target_count]


def build_characters(names: list[str]) -> list[Character]:
    voice_styles = ("清亮、克制、带一点少年感", "温柔、坚定、情绪细腻")
    roles = ("主角A", "主角B")
    anchors = (
        "anime motion comic, consistent face, dark short hair, alert eyes, simple school jacket",
        "anime motion comic, consistent face, soft shoulder-length hair, calm eyes, light cardigan",
    )

    return [
        Character(
            id=_slug_id(name, index + 1),
            name=name,
            role=roles[index] if index < len(roles) else "配角",
            description=f"{name} 是本集核心人物，承担主要情绪推进和动作反应。",
            visual_anchor=anchors[index] if index < len(anchors) else "anime motion comic, consistent character design",
            voice_style=voice_styles[index] if index < len(voice_styles) else "自然、清晰",
        )
        for index, name in enumerate(names)
    ]


def _build_original_characters(text: str) -> list[Character]:
    if "猫" in text or "喵" in text:
        return [
            Character(
                id="naitang",
                name="奶糖",
                role="活泼调查员",
                description="橘白短毛猫，圆脸琥珀眼，反应快、好奇心强。",
                visual_anchor="photorealistic orange-and-white short-haired cat, round face, amber eyes, white muzzle and chest, consistent markings",
                voice_style="可爱活泼、轻亮灵动、自然短停顿",
            ),
            Character(
                id="doubao",
                name="豆包",
                role="冷静调查员",
                description="黑白燕尾服短毛猫，绿眼睛，身形纤细，冷静克制。",
                visual_anchor="photorealistic black-and-white tuxedo short-haired cat, green eyes, slim body, white nose-to-chin marking, consistent markings",
                voice_style="高冷御姐、干脆克制、自然语速",
            ),
        ]
    explicit: list[str] = []
    for match in NAME_PATTERN.finditer(text):
        name = match.group(1)
        if name not in explicit:
            explicit.append(name)
    return build_characters((explicit + list(DEFAULT_ORIGINAL_NAMES))[:2])


def _expand_original_beats(text: str, target_count: int) -> list[str]:
    existing = split_story_beats(text, target_count)
    if len(existing) >= target_count:
        return existing[:target_count]
    premise = text.strip().rstrip("。！？!?")
    templates = (
        f"开场建立地点和角色关系：{premise}。",
        "异常再次出现，两位主角先停下观察声音来源和周围物体状态。",
        "两位主角分工调查，一位靠近线索，另一位留在原位观察并保持空间关系。",
        "调查出现误导线索，角色产生不同判断，但场景、道具数量和位置保持连续。",
        "真正原因被发现，前面的声音和动作得到符合物理规律的解释。",
        "两位主角用一个轻松反应收尾，并为下一集留下一个小悬念。",
    )
    beats = list(existing) if len(existing) > 1 else []
    for template in templates:
        if len(beats) >= target_count:
            break
        beats.append(template)
    return beats[:target_count]


def _dialogue_for_beat(beat: str, characters: list[Character], index: int) -> list[DialogueLine]:
    match = DIALOGUE_PATTERN.search(beat)
    lines: list[DialogueLine] = []
    if match:
        narration = _narration_without_spoken_dialogue(beat, match)
        if narration:
            lines.append(
                DialogueLine(
                    speaker_id=NARRATOR_ID,
                    text=_shorten_for_narration(narration, limit=20),
                    emotion="narrating",
                )
            )
        speaker = _infer_speaker(beat, characters, index)
        lines.append(
            DialogueLine(
                speaker_id=speaker.id,
                text=_shorten_for_narration(match.group(1).strip(), limit=34),
                emotion="focused",
            )
        )
        return lines

    return [
        DialogueLine(
            speaker_id=NARRATOR_ID,
            text=_shorten_for_narration(beat),
            emotion="narrating",
        )
    ]


def _narration_without_spoken_dialogue(beat: str, match: re.Match[str]) -> str:
    before = beat[: match.start()].strip().rstrip("，,:：")
    before = re.sub(r"[他她]$", "", before).strip()
    after = beat[match.end() :].strip()
    return f"{before}{after}".strip()


def _extract_dialogue_text(beat: str) -> str | None:
    match = DIALOGUE_PATTERN.search(beat)
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def _infer_speaker(beat: str, characters: list[Character], index: int) -> Character:
    for character in characters:
        if character.name in beat:
            return character
    return characters[(index - 1) % len(characters)]


def _shorten_for_narration(text: str, limit: int = 58) -> str:
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= limit:
        return compact
    clauses = [
        part
        for part in re.split(r"(?<=[，。！？!?])", compact)
        if part
    ]
    selected = ""
    for clause in clauses:
        if selected and len(selected + clause) > limit:
            break
        if len(clause) > limit:
            break
        selected += clause
    if selected:
        selected = selected.rstrip("，")
        return (
            selected
            if selected.endswith(("。", "！", "？", "!", "?"))
            else selected + "。"
        )
    return compact[: limit - 1].rstrip("，") + "。"


def _estimated_shot_duration(dialogue: list[DialogueLine]) -> float:
    speech_seconds = sum(
        max(
            1.0,
            len(re.sub(r"\s+", "", line.text)) / VOICE_CHARS_PER_SECOND + 0.4,
        )
        for line in dialogue
    )
    gap_seconds = max(0, len(dialogue) - 1) * 0.3
    rounded = math.ceil((0.4 + speech_seconds + gap_seconds + 0.4) * 2) / 2
    return min(
        MAX_SHOT_DURATION_SECONDS,
        max(BASE_SHOT_DURATION_SECONDS, rounded),
    )


def plan_episode(
    text: str,
    project_id: str,
    title: str | None = None,
    target_shots: int = 8,
    style: str = "国漫风动态漫剧，电影感光影，角色一致",
    content_mode: str = "novel",
) -> Episode:
    if content_mode not in {"novel", "original"}:
        raise ValueError("content_mode must be novel or original")
    if content_mode == "original":
        characters = _build_original_characters(text)
        beats = _expand_original_beats(text, target_shots)
        if "猫" in text or "喵" in text:
            style = "写实可爱宠物短剧，原木风自然光，猫咪身份和场景连续"
    else:
        names = extract_main_character_names(text, limit=2)
        characters = build_characters(names)
        beats = split_story_beats(text, target_count=target_shots)

    shots: list[Shot] = []
    for idx, beat in enumerate(beats, start=1):
        character_names = "、".join(character.name for character in characters)
        dialogue = _dialogue_for_beat(beat, characters, idx)
        shots.append(
            Shot(
                id=f"shot_{idx:03d}",
                index=idx,
                scene_title=f"第 {idx} 镜",
                action=beat,
                visual_prompt=(
                    f"{style}。画面包含 {character_names}。"
                    f"剧情动作：{beat}。竖版9:16，适合短视频漫剧，字幕安全区留白。"
                ),
                camera="medium shot, slow push-in" if idx % 2 else "close-up, slight handheld tension",
                duration_seconds=_estimated_shot_duration(dialogue),
                audio_mood="悬念推进，轻微环境音，对白清晰",
                dialogue=dialogue,
            )
        )

    episode = Episode(
        project_id=project_id,
        title=title or "小说漫剧样片",
        language="zh-CN",
        style=style,
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=characters,
        shots=shots,
    )
    assert_valid_episode(episode)
    return episode
