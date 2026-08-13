from __future__ import annotations

import re
import unicodedata
from typing import Any

from .schema import Episode, NARRATOR_ID, Shot


SOURCE_UNSPECIFIED_TIME = "source-unspecified"
PREVIOUS_SHOT_CONTINUITY = "previous-shot-continuity"

ACTION_TEMPLATES = {
    "hold_still": ("{actor} holds still at {target}", "{actor}在{target}处保持静止"),
    "gaze_shift": ("{actor} shifts gaze to {target}", "{actor}将视线移向{target}"),
    "blink": ("{actor} blinks", "{actor}眨眼"),
    "brow_tighten": ("{actor} tightens brows at {target}", "{actor}对{target}蹙眉"),
    "eyes_widen": ("{actor} widens eyes at {target}", "{actor}望向{target}时睁大眼睛"),
    "head_turn": ("{actor} turns toward {target}", "{actor}转向{target}"),
    "reach": ("{actor} reaches toward {target}", "{actor}伸向{target}"),
    "stop_reaching": (
        "{actor} stops reaching toward {target}",
        "{actor}停止伸向{target}",
    ),
    "grasp": ("{actor} grasps {target}", "{actor}握住{target}"),
    "release": ("{actor} releases {target}", "{actor}松开{target}"),
    "hand_over": ("{actor} hands over {target}", "{actor}递出{target}"),
    "receive": ("{actor} receives {target}", "{actor}接过{target}"),
    "insert": ("{actor} inserts {target}", "{actor}放入{target}"),
    "open": ("{actor} opens {target}", "{actor}打开{target}"),
    "close": ("{actor} closes {target}", "{actor}合上{target}"),
    "step": ("{actor} steps toward {target}", "{actor}迈向{target}"),
    "stop": ("{actor} stops at {target}", "{actor}在{target}前停住"),
    "flinch": ("{actor} flinches at {target}", "{actor}因{target}瑟缩"),
    "recoil": ("{actor} recoils from {target}", "{actor}从{target}前退开"),
    "raise": ("{actor} raises {target}", "{actor}举起{target}"),
    "lower": ("{actor} lowers {target}", "{actor}放下{target}"),
    "light_up": ("{actor} lights up {target}", "{actor}点亮{target}"),
    "flicker": ("{actor} flickers at {target}", "{actor}在{target}处闪烁"),
    "sway": ("{actor} sways at {target}", "{actor}在{target}处摇曳"),
    "drip": ("{actor} drips at {target}", "{actor}在{target}处滴落"),
    "ripple": ("{actor} ripples at {target}", "{actor}在{target}处泛起涟漪"),
    "reveal": ("{actor} reveals {target}", "{actor}显露{target}"),
    "listen": ("{actor} listens toward {target}", "{actor}聆听{target}"),
    "breathe": ("{actor} breathes", "{actor}呼吸"),
}
OPTIONAL_NEGATIVE_CONSTRAINTS = {
    "no_text": "no additional generated text or subtitles; 禁止额外生成文字或字幕",
    "no_extra_people": "no additional people; 禁止额外人物",
    "no_duplicate_anatomy": "no duplicate anatomy; 禁止重复肢体或解剖结构",
    "no_scene_change": "no scene or location change; 禁止场景或地点变化",
    "no_camera_motion": "no additional camera motion; 禁止额外镜头运动",
    "no_rain": "no rain; 禁止降雨",
    "no_daylight": "no daylight; 禁止日光",
    "no_modern_objects": "no modern objects; 禁止现代物件",
    "no_background_crowd": "no background crowd; 禁止背景人群",
    "no_lip_closeup": "no lip closeup; 禁止嘴部特写",
    "no_facial_drift": "no facial drift; 禁止面部漂移",
}
STILL_HARD_CONSTRAINTS = (
    "image-only composition, 静态图像构图",
    "no people, characters, face, body or hand; 禁止人物、角色、脸部、身体或手",
    "no text, subtitles, watermark or logo; 禁止文字、字幕、水印或标志",
    "no duplicate face, body or limbs; 禁止重复脸部、身体或肢体",
    "no malformed anatomy; 禁止畸形解剖结构",
)

_SELF_ACTION_CODES = frozenset({"hold_still", "blink", "breathe"})
_RESERVED_ACTORS = frozenset({"object", "environment"})
_CJK = re.compile(r"[\u3400-\u9fff]")
_GENERIC_SCENE_TITLE = re.compile(
    r"^(?:第\s*\d+\s*镜|镜头\s*\d+|(?:shot|scene)\s*\d+)$", re.IGNORECASE
)
_RELATIVE_TIME_TERMS = (
    "雨停后",
    "十年前",
    "此刻",
    "一瞬间",
    "later",
    "years ago",
)
_SETTING_TIME_TERMS = (
    "夜晚",
    "清晨",
    "黄昏",
    "凌晨",
    "中午",
    "傍晚",
    "night",
    "dawn",
    "morning",
    "noon",
    "dusk",
    "evening",
    "midnight",
)
_TIME_PATTERNS = (
    re.compile(r"\d{4}年\d{1,2}月\d{1,2}日"),
    re.compile(r"(?<!\d)\d{1,2}:\d{2}(?!\d)"),
    re.compile(r"\d{1,2}点(?:\d{1,2}分)?"),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
)
_LOCATION_TERMS = (
    "shop",
    "store",
    "room",
    "street",
    "road",
    "district",
    "city",
    "house",
    "home",
    "counter",
    "table",
    "desk",
    "window",
    "door",
    "station",
    "office",
    "school",
    "alley",
    "alley entrance",
    "park",
    "bridge",
    "rooftop",
    "shore",
    "forest",
    "convenience store",
    "streetlight",
    "under the streetlight",
    "cinema",
    "movie theater",
    "sign",
    "signboard",
    "ticket hall",
    "ticket gate",
    "screen",
    "书店",
    "便利店",
    "店",
    "街灯",
    "街灯下",
    "街",
    "路",
    "巷口",
    "巷",
    "城区",
    "城市",
    "电影院",
    "影院",
    "招牌",
    "售票厅",
    "检票口",
    "屏幕",
    "银幕",
    "房",
    "屋",
    "柜台",
    "桌",
    "窗",
    "门",
    "车站",
    "办公室",
    "学校",
    "公园",
    "桥",
    "天台",
    "岸",
    "树林",
)
_FREE_TEXT_FIELDS = (
    "scene_context",
    "time_context",
    "purpose",
    "emotion_start",
    "emotion_end",
    "gaze",
    "pose_start",
    "pose_end",
    "action_target",
)
_UNSAFE_DIRECTIVES = (
    "camera",
    "camera pan",
    "camera move",
    "zoom",
    "dolly",
    "orbit",
    "cut to",
    "fade to",
    "dissolve",
    "transition",
    "scene change",
    "change scene",
    "new scene",
    "new location",
    "change location",
    "change to night",
    "day turns to night",
    "add text",
    "show text",
    "subtitle",
    "subtitles",
    "watermark",
    "logo",
    "ignore previous",
    "ignore all previous",
    "ignore earlier directions",
    "ignore earlier instructions",
    "forget previous instructions",
    "forget all instructions",
    "disregard",
    "system prompt",
    "developer message",
    "override instructions",
    "bypass",
    "weaken constraints",
    "do not enforce",
    "镜头",
    "横移",
    "推拉",
    "变焦",
    "环绕",
    "摇镜",
    "切到",
    "转场",
    "溶解",
    "换到",
    "加入字幕",
    "添加字幕",
    "允许字幕",
    "显示文字",
    "水印",
    "标志",
    "忽略此前",
    "忽略以上",
    "忽略前面的要求",
    "忽略前面的指令",
    "无视之前指令",
    "无视之前要求",
    "覆盖以上规则",
    "覆盖以上要求",
    "系统提示",
    "放宽限制",
    "绕过限制",
    "不要遵守",
    "忘记之前的要求",
    "忘记以上要求",
)
_ACTION_TARGET_FORBIDDEN = (
    *ACTION_TEMPLATES,
    "opens",
    "reads",
    "hands over",
    "gives to",
    "stands",
    "sits",
    "walks",
    "runs",
    "looks",
    "turns",
    "reaches",
    "lifts",
    "raises",
    "lowers",
    "holds",
    "grasps",
    "nod",
    "nodding",
    "smile",
    "smiling",
    "leave",
    "leaving",
    "turn away",
    "look away",
    "fade",
    "camera",
    "day turns",
    "打开",
    "阅读",
    "交给",
    "递给",
    "抬头",
    "低头",
    "转身",
    "站在",
    "坐在",
    "走向",
    "跑向",
    "看向",
    "伸手",
    "抬起",
    "举起",
    "放下",
    "握住",
    "塞进",
    "点头",
    "微笑",
    "离开",
    "横移",
    "镜头",
    "切到",
    "转场",
    "溶解",
    "推拉",
    "变焦",
    "环绕",
    "摇镜",
)
_TARGET_CHAINING_TERMS = (
    "and",
    "then",
    "while",
    "as well as",
    "after",
    "并",
    "并且",
    "且",
    "然后",
    "随后",
    "接着",
    "同时",
    "又",
    "再",
)
_VISIBLE_TEXT_TARGETS = (
    "字幕",
    "文字",
    "商标",
    "标识",
    "水印",
    "caption",
    "subtitle",
    "text",
    "watermark",
    "trademark",
    "logo",
)
_NON_NOUN_TARGET_TERMS = ("手里", "手中")
_ENGLISH_PERSON_TERMS = (
    "stranger",
    "unknown person",
    "passerby",
    "passer-by",
    "crowd",
    "customer",
    "clerk",
    "shop clerk",
    "sales clerk",
    "boy",
    "girl",
    "teenager",
    "youth",
    "elderly person",
    "human",
    "man",
    "woman",
    "child",
    "person",
    "people",
    "figure",
    "silhouette",
    "pedestrian",
    "traveler",
    "traveller",
    "passenger",
)
_CHINESE_PERSON_TERMS = (
    "陌生人",
    "陌生男人",
    "路人",
    "人群",
    "顾客",
    "店员",
    "售货员",
    "男孩",
    "女孩",
    "少年",
    "少女",
    "青年",
    "老人",
    "老者",
    "小孩",
    "孩子",
    "人物",
    "人影",
    "身影",
    "男人",
    "女人",
    "行人",
    "旅客",
    "乘客",
)
_ENGLISH_BODY_TERMS = (
    "hand",
    "hands",
    "finger",
    "fingers",
    "palm",
    "palms",
    "face",
    "body",
    "eye",
    "eyes",
    "arm",
    "arms",
    "lip",
    "lips",
    "leg",
    "legs",
    "foot",
    "feet",
)
_CHINESE_BODY_TERMS = (
    "手臂",
    "胳膊",
    "嘴唇",
    "嘴巴",
    "腿",
    "脚",
    "手里",
    "手中",
    "双手",
    "手指",
    "手掌",
    "人脸",
    "脸部",
    "面部",
    "身体",
    "眼睛",
)
_CHINESE_PERSON_NAME = re.compile(
    r"(?:张|王|李|赵|刘|陈|杨|黄|周|吴|徐|孙|胡|朱|高|林|何|郭|马|罗|梁|宋|郑|谢|韩|唐|冯|于|董|萧|程|曹|袁|邓|许|傅|沈|曾|彭|吕|苏|卢|蒋|蔡|贾|丁|魏|薛|叶|阎|余|潘|杜|戴|夏|钟|汪|田|任|姜|范|方|石|姚|谭|廖|邹|熊|金|陆|郝|孔|白|崔|康|毛|邱|秦|江|史|顾|侯|邵|孟|龙|万|段|雷|钱|汤|尹|黎|易|常|武|乔|贺|赖|龚|文)[\u3400-\u9fff]{1,2}(?=(?:站在|坐在|走向|看向|伸手|抬头|低头|转身|靠在|跪在|微笑|注视|凝视|盯着|眨眼|皱眉|开口|说话))"
)
_ENGLISH_PERSON_NAME = re.compile(
    r"\b[A-Z][a-z]+\s+[A-Z][a-z]+(?=\s+(?:stands?|sits?|walks?|looks?|reaches?|turns?|leans?|kneels?|smiles?|smiling|watches?|gazes?|stares?|is\s+standing)\b)"
)
_CHINESE_BODY_PATTERNS = (
    re.compile(r"手(?:靠|贴|伸)"),
    re.compile(r"脸(?:贴|靠)"),
    re.compile(r"嘴(?:微张|张开)"),
    re.compile(r"头(?:靠|贴)"),
    re.compile(r"肩(?:膀)?贴"),
)
_SINGLE_CJK_ACTION_TARGETS = frozenset({"手"})
_POSTPOSITION_ACTION = re.compile(
    r"后(?:离开|转身|点头|微笑|抬头|低头|走向|跑向|看向|伸手|抬起|举起|放下)"
)


def video_hard_constraints(camera_mode: str) -> tuple[str, ...]:
    common = (
        "single continuous shot, 单一连续镜头",
        "fixed scene, location and time, 固定场景、地点与时间",
        "no text, subtitles, watermark or logo; 禁止文字、字幕、水印或标志",
        "no extra people or characters; 禁止额外人物或角色",
        "no duplicate face, body or limbs; 禁止重复脸部、身体或肢体",
        "no malformed anatomy; 禁止畸形解剖结构",
        "no cuts, dissolves, scene, location or time changes; 禁止切镜、溶解、场景、地点或时间变化",
        "no zoom, dolly, orbit or camera shake; 禁止变焦、推拉、环绕或镜头抖动",
    )
    if camera_mode in {"locked", "object_insert"}:
        return (*common, "no pan or camera movement; 禁止平移或任何镜头运动")
    if camera_mode == "micro_pan":
        return (
            *common,
            "only one restrained lateral pan under two percent of frame width; 仅允许一次小于画面宽度百分之二的克制横移",
            "no additional camera movement; 禁止额外镜头运动",
        )
    return common


def optional_negative_phrases(codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(OPTIONAL_NEGATIVE_CONSTRAINTS[code] for code in codes)


def render_action(episode: Episode, shot: Any) -> str:
    language_index = 1 if normalize_text(episode.language).startswith("zh") else 0
    target = shot.action_target
    if shot.action_actor_id in _RESERVED_ACTORS and shot.action_code == "hold_still":
        return f"{target}保持静止" if language_index else f"{target} remains still"
    actor = _actor_label(episode, shot.action_actor_id, language_index)
    if target == "self" and shot.action_code == "hold_still":
        return f"{actor}保持静止" if language_index else f"{actor} holds still"
    template = ACTION_TEMPLATES[shot.action_code][language_index]
    return template.format(actor=actor, target=target)


def micro_shot_safety_errors(
    episode: Episode,
    shot: Any,
    parent: Shot,
    *,
    character_free: bool,
) -> list[str]:
    errors = parent_character_provenance_errors(episode, shot, parent)
    errors.extend(scene_time_context_errors(shot, parent))
    errors.extend(action_errors(episode, shot, parent, character_free=character_free))
    errors.extend(negative_constraint_errors(shot))
    errors.extend(_free_text_identity_errors(episode, shot))
    errors.extend(_invented_person_errors(episode, shot))
    errors.extend(_free_text_safety_errors(shot))
    if character_free:
        errors.extend(_body_content_errors(shot))
    return errors


def episode_integrity_errors(episode: Episode) -> list[str]:
    errors: list[str] = []
    character_ids = [character.id for character in episode.characters]
    if len(set(character_ids)) != len(character_ids):
        errors.append("episode has duplicate character ids")
    names: dict[str, list[str]] = {}
    for character in episode.characters:
        names.setdefault(normalize_text(character.name), []).append(character.id)
    duplicates = [ids for ids in names.values() if len(ids) > 1]
    if duplicates:
        errors.append(
            "episode has duplicate character names: "
            + ", ".join("/".join(ids) for ids in duplicates)
        )
    shot_ids = [shot.id for shot in episode.shots]
    if len(set(shot_ids)) != len(shot_ids):
        errors.append("episode has duplicate shot ids")
    shot_indexes = [shot.index for shot in episode.shots]
    if len(set(shot_indexes)) != len(shot_indexes):
        errors.append("episode has duplicate shot indexes")
    return errors


def parent_character_provenance_errors(
    episode: Episode, shot: Any, parent: Shot
) -> list[str]:
    characters = {character.id: character for character in episode.characters}
    parent_text = " ".join([parent.action, *(line.text for line in parent.dialogue)])
    speaking_character_ids = {
        line.speaker_id for line in parent.dialogue if line.speaker_id != NARRATOR_ID
    }
    collective_allowance = _has_exact_two_character_collective_provenance(
        episode, shot, parent_text
    )
    errors: list[str] = []
    for character_id in shot.character_ids:
        character = characters.get(character_id)
        if character is None or contains_literal(parent_text, character.name):
            continue
        other_present_speaker = any(
            speaker_id != character_id and speaker_id in shot.character_ids
            for speaker_id in speaking_character_ids
        )
        if shot.purpose == "reaction" and other_present_speaker:
            continue
        if collective_allowance:
            continue
        errors.append(
            f"{shot.id} character {character_id} is absent from its parent action and dialogue."
        )
    return errors


def _has_exact_two_character_collective_provenance(
    episode: Episode, shot: Any, parent_text: str
) -> bool:
    episode_character_ids = {character.id for character in episode.characters}
    if (
        len(episode_character_ids) != 2
        or set(shot.character_ids) != episode_character_ids
    ):
        return False
    return any(
        contains_literal(parent_text, pronoun)
        for pronoun in ("他们", "二人", "两人", "they", "both of them", "they both")
    )


def scene_time_context_errors(shot: Any, parent: Shot) -> list[str]:
    errors: list[str] = []
    if shot.scene_context == PREVIOUS_SHOT_CONTINUITY:
        if shot.index == 1:
            errors.append(
                f"{shot.id} cannot use previous-shot-continuity on the first micro-shot."
            )
        if extract_source_locations(parent):
            errors.append(
                f"{shot.id} cannot use previous-shot-continuity when its parent has an explicit source location."
            )
    elif not _is_meaningful_span(shot.scene_context) or _has_context_separator(
        shot.scene_context
    ):
        errors.append(f"{shot.id} scene_context must be one meaningful location span.")
    elif not _is_source_grounded(shot.scene_context, parent, allow_scene_title=True):
        errors.append(f"{shot.id} scene_context is not grounded in its parent source.")
    elif not _has_location_semantics(shot.scene_context, parent):
        errors.append(f"{shot.id} scene_context must name a source location.")
    if _has_context_separator(shot.time_context):
        errors.append(f"{shot.id} time_context must be one complete time expression.")
    else:
        source_times = extract_source_time_expressions(parent)
        if shot.time_context == SOURCE_UNSPECIFIED_TIME:
            if source_times:
                errors.append(
                    f"{shot.id} time_context cannot be source-unspecified when parent source specifies current scene time."
                )
        elif normalize_text(shot.time_context) not in source_times:
            errors.append(
                f"{shot.id} time_context is not a complete current parent source time expression."
            )
    return errors


def action_errors(
    episode: Episode, shot: Any, parent: Shot, *, character_free: bool
) -> list[str]:
    errors: list[str] = []
    character_ids = {character.id for character in episode.characters}
    if shot.action_actor_id not in character_ids | _RESERVED_ACTORS:
        errors.append(f"{shot.id} action_actor_id is unknown.")
    elif character_free and shot.action_actor_id not in _RESERVED_ACTORS:
        errors.append(
            f"{shot.id} character-free shot action_actor_id must be object or environment."
        )
    elif (
        shot.action_actor_id in character_ids
        and shot.action_actor_id not in shot.character_ids
    ):
        errors.append(f"{shot.id} action_actor_id must be present in character_ids.")
    if shot.action_code not in ACTION_TEMPLATES:
        errors.append(f"{shot.id} action_code is not a production enum value.")
        return errors
    if shot.action_target == "self":
        if (
            shot.action_code not in _SELF_ACTION_CODES
            or shot.action_actor_id in _RESERVED_ACTORS
        ):
            errors.append(
                f"{shot.id} action_target self is not allowed for this action."
            )
        return errors
    single_cjk_target = (
        shot.action_target in _SINGLE_CJK_ACTION_TARGETS
        and not character_free
        and shot.action_actor_id in shot.character_ids
    )
    if (
        not _is_meaningful_span(shot.action_target) and not single_cjk_target
    ) or _has_context_separator(shot.action_target):
        errors.append(f"{shot.id} action_target must be one meaningful object phrase.")
        return errors
    if _target_has_chaining(shot.action_target):
        errors.append(
            f"{shot.id} action_target must be one noun phrase without chaining."
        )
    if any(
        term in normalize_text(shot.action_target) for term in _NON_NOUN_TARGET_TERMS
    ):
        errors.append(f"{shot.id} action_target must be one source noun phrase.")
    if any(
        contains_literal(shot.action_target, term) for term in _VISIBLE_TEXT_TARGETS
    ):
        errors.append(f"{shot.id} action_target cannot request visible text content.")
    if any(
        contains_literal(shot.action_target, phrase)
        for phrase in _ACTION_TARGET_FORBIDDEN
    ):
        errors.append(
            f"{shot.id} action_target cannot contain action or camera language."
        )
    if _contains_person_content(
        _without_declared_identities(episode, shot.action_target)
    ):
        errors.append(
            f"{shot.id} action_target cannot contain invented person content."
        )
    if normalize_text(shot.action_target) in extract_source_time_expressions(parent):
        errors.append(f"{shot.id} action_target cannot be a source time expression.")
    target_is_grounded = (
        _single_cjk_action_target_is_grounded(episode, shot, parent)
        if single_cjk_target
        else _is_source_grounded_target(shot.action_target, parent)
    )
    if not target_is_grounded:
        errors.append(
            f"{shot.id} action_target is not grounded as one noun phrase in parent source."
        )
    for character in episode.characters:
        if (
            contains_literal(shot.action_target, character.id)
            or contains_literal(shot.action_target, character.name)
        ) and character.id not in shot.character_ids:
            errors.append(
                f"{shot.id} action_target names undeclared character {character.id}."
            )
    return errors


def negative_constraint_errors(shot: Any) -> list[str]:
    unknown = [
        code
        for code in shot.negative_constraints
        if code not in OPTIONAL_NEGATIVE_CONSTRAINTS
    ]
    if unknown:
        return [
            f"{shot.id} negative_constraints contain unknown code(s): {', '.join(unknown)}."
        ]
    return []


def extract_source_time_expressions(parent: Shot) -> set[str]:
    source = f"{normalize_text(parent.scene_title)}\n{normalize_text(parent.action)}"
    matches: set[str] = set()
    for term in _RELATIVE_TIME_TERMS:
        for occurrence in re.finditer(re.escape(term), source):
            if _inside_quotation(source, occurrence.start()):
                continue
            if term == "一瞬间" and source[occurrence.end() :].startswith("的"):
                continue
            if _at_sentence_or_shot_boundary(source, occurrence.start()):
                matches.add(term)
                break
    for term in _SETTING_TIME_TERMS:
        if _setting_time_present(source, term):
            matches.add(term)
    for pattern in _TIME_PATTERNS:
        for occurrence in pattern.finditer(source):
            if not _inside_quotation(
                source, occurrence.start()
            ) and _clock_or_date_is_setting(source, occurrence.start()):
                matches.add(occurrence.group(0))
    return matches


def extract_source_locations(parent: Shot) -> tuple[str, ...]:
    candidates: list[tuple[int, int, str]] = []
    title = parent.scene_title.strip()
    if (
        title
        and not _GENERIC_SCENE_TITLE.fullmatch(normalize_text(title))
        and any(contains_literal(title, term) for term in _LOCATION_TERMS)
    ):
        candidates.append((-1, -len(normalize_text(title)), title))
    source = f"{parent.action}\n{parent.visual_prompt}"
    normalized_source = normalize_text(source)
    for term in _LOCATION_TERMS:
        normalized_term = normalize_text(term)
        if not contains_literal(normalized_source, normalized_term):
            continue
        candidates.append(
            (normalized_source.find(normalized_term), -len(normalized_term), term)
        )
    selected: list[str] = []
    for _, _, candidate in sorted(candidates):
        normalized = normalize_text(candidate)
        if any(
            normalized == normalize_text(existing)
            or normalized in normalize_text(existing)
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return tuple(selected)


def is_valid_concrete_scene_context(value: str, parent: Shot) -> bool:
    return (
        value != PREVIOUS_SHOT_CONTINUITY
        and _is_meaningful_span(value)
        and not _has_context_separator(value)
        and _is_source_grounded(value, parent, allow_scene_title=True)
        and _has_location_semantics(value, parent)
    )


def continuity_context_errors(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return ["previous_scene_context must be a non-empty string"]
    if "\n" in value or "\r" in value or ";" in value or "；" in value:
        return ["previous_scene_context contains unsafe free text"]
    if value == PREVIOUS_SHOT_CONTINUITY:
        return ["previous_scene_context must be concrete, not a continuity sentinel"]
    if not _is_meaningful_span(value) or _has_context_separator(value):
        return ["previous_scene_context must be one concrete location span"]
    normalized = normalize_text(value)
    if any(contains_literal(normalized, term) for term in _VISIBLE_TEXT_TARGETS):
        return ["previous_scene_context contains visible text semantics"]
    if any(contains_literal(normalized, term) for term in _UNSAFE_DIRECTIVES):
        return ["previous_scene_context contains unsafe free text"]
    if _contains_person_content(value):
        return ["previous_scene_context contains generic person content"]
    return []


def contains_literal(text: str, phrase: str) -> bool:
    text_normalized = normalize_text(text)
    phrase_normalized = normalize_text(phrase)
    if not phrase_normalized:
        return False
    if re.search(r"[a-z0-9]", phrase_normalized):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(phrase_normalized)}(?![a-z0-9])",
                text_normalized,
            )
        )
    return phrase_normalized in text_normalized


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _actor_label(episode: Episode, actor_id: str, language_index: int) -> str:
    if actor_id == "object":
        return "道具" if language_index else "object"
    if actor_id == "environment":
        return "环境" if language_index else "environment"
    return next(item.name for item in episode.characters if item.id == actor_id)


def _free_text_identity_errors(episode: Episode, shot: Any) -> list[str]:
    text = "\n".join(getattr(shot, field) for field in _FREE_TEXT_FIELDS)
    errors: list[str] = []
    for character in episode.characters:
        if character.id not in shot.character_ids and (
            contains_literal(text, character.id)
            or contains_literal(text, character.name)
        ):
            errors.append(f"{shot.id} mentions undeclared character {character.id}.")
    return errors


def _free_text_safety_errors(shot: Any) -> list[str]:
    errors: list[str] = []
    for field in _FREE_TEXT_FIELDS:
        value = getattr(shot, field)
        normalized = normalize_text(value)
        if any(contains_literal(normalized, term) for term in _VISIBLE_TEXT_TARGETS):
            errors.append(
                f"{shot.id} {field} contains unsafe free text (visible text semantics)."
            )
            continue
        if "\n" in value or "\r" in value or ";" in value or "；" in value:
            errors.append(f"{shot.id} {field} contains unsafe free text.")
            continue
        if re.search(r"[.!?。！？]\s*\S", value):
            errors.append(f"{shot.id} {field} contains unsafe free text.")
            continue
        if any(contains_literal(normalized, phrase) for phrase in _UNSAFE_DIRECTIVES):
            errors.append(f"{shot.id} {field} contains unsafe free text.")
    return errors


def _invented_person_errors(episode: Episode, shot: Any) -> list[str]:
    for field in _FREE_TEXT_FIELDS:
        value = getattr(shot, field)
        cleaned = _without_declared_identities(episode, value)
        normalized_for_names = unicodedata.normalize("NFKC", cleaned)
        if (
            _contains_person_content(cleaned)
            or _CHINESE_PERSON_NAME.search(normalized_for_names)
            or _ENGLISH_PERSON_NAME.search(normalized_for_names)
        ):
            return [
                f"{shot.id} {field} mentions generic human content: invented person or person name."
            ]
    return []


def _without_declared_identities(episode: Episode, value: str) -> str:
    cleaned = value
    for character in episode.characters:
        for identity in (character.id, character.name):
            if identity:
                cleaned = re.sub(re.escape(identity), "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _body_content_errors(shot: Any) -> list[str]:
    for field in _FREE_TEXT_FIELDS:
        if _contains_body_content(getattr(shot, field)):
            return [
                f"{shot.id} character-free route {field} mentions generic human content (body content)."
            ]
    return []


def _contains_person_content(value: str) -> bool:
    return any(contains_literal(value, term) for term in _ENGLISH_PERSON_TERMS) or any(
        term in normalize_text(value) for term in _CHINESE_PERSON_TERMS
    )


def _contains_body_content(value: str) -> bool:
    normalized = normalize_text(value)
    return (
        any(contains_literal(value, term) for term in _ENGLISH_BODY_TERMS)
        or any(term in normalized for term in _CHINESE_BODY_TERMS)
        or any(pattern.search(normalized) for pattern in _CHINESE_BODY_PATTERNS)
    )


def _target_has_chaining(value: str) -> bool:
    normalized = normalize_text(value)
    return any(
        contains_literal(normalized, term) for term in _TARGET_CHAINING_TERMS
    ) or bool(
        re.search(r"一边.+一边", normalized) or _POSTPOSITION_ACTION.search(normalized)
    )


def _single_cjk_action_target_is_grounded(
    episode: Episode, shot: Any, parent: Shot
) -> bool:
    if shot.action_target != "手":
        return False
    character = next(
        (
            item
            for item in episode.characters
            if item.id == shot.action_actor_id and item.id in shot.character_ids
        ),
        None,
    )
    if character is None or not contains_literal(parent.action, character.name):
        return False
    return bool(
        re.search(
            r"(?:抬起|举起|伸出|收回|放下|握紧|松开|张开|抬|举|伸|握|放).{0,3}手(?!机|表)",
            normalize_text(parent.action),
        )
    )


def _is_source_grounded(value: str, parent: Shot, *, allow_scene_title: bool) -> bool:
    if allow_scene_title and normalize_text(value) == normalize_text(
        parent.scene_title
    ):
        return not _GENERIC_SCENE_TITLE.fullmatch(normalize_text(value))
    return contains_literal(parent.action, value) or contains_literal(
        parent.visual_prompt, value
    )


def _is_source_grounded_target(value: str, parent: Shot) -> bool:
    if contains_literal(parent.action, value) or contains_literal(
        parent.visual_prompt, value
    ):
        return True
    normalized = normalize_text(value)
    if _CJK.search(normalized):
        compact = re.sub(r"(?:的|[- ]+)", "", normalized)
        return _cjk_components_are_source_grounded(compact, parent)
    else:
        components = re.findall(r"[a-z0-9]+", normalized)
        components = [
            part for part in components if part not in {"a", "an", "the", "of"}
        ]
    return bool(components) and all(
        contains_literal(parent.action, part)
        or contains_literal(parent.visual_prompt, part)
        for part in components
    )


def _cjk_components_are_source_grounded(value: str, parent: Shot) -> bool:
    source = normalize_text(f"{parent.action} {parent.visual_prompt}")
    failed_positions: set[int] = set()

    def grounded_from(position: int) -> bool:
        if position == len(value):
            return True
        if position in failed_positions:
            return False
        for end in range(len(value), position + 1, -1):
            component = value[position:end]
            if len(component) < 2 and component not in {"门", "票", "灯"}:
                continue
            if component in source and grounded_from(end):
                return True
        failed_positions.add(position)
        return False

    return bool(value) and grounded_from(0)


def _has_location_semantics(value: str, parent: Shot) -> bool:
    normalized = normalize_text(value)
    if normalized == normalize_text(parent.scene_title):
        return not _GENERIC_SCENE_TITLE.fullmatch(normalized)
    return any(contains_literal(value, term) for term in _LOCATION_TERMS)


def _is_meaningful_span(value: str) -> bool:
    normalized = normalize_text(value)
    if len(_CJK.findall(normalized)) >= 2:
        return True
    return bool(re.fullmatch(r"[a-z0-9]+(?:[ '-][a-z0-9]+)*", normalized))


def _has_context_separator(value: str) -> bool:
    normalized = normalize_text(value)
    if re.search(r"[,;/，；】【。、]", normalized):
        return True
    return bool(re.search(r"(?<![a-z0-9])(?:and|or)(?![a-z0-9])", normalized))


def _at_sentence_or_shot_boundary(source: str, position: int) -> bool:
    prefix = source[:position]
    boundary = max(
        prefix.rfind(mark) for mark in ("\n", ".", "!", "?", "。", "！", "？")
    )
    between = prefix[boundary + 1 :].strip(" \t,，:：'\"")
    return between in {"", "在", "at"}


def _setting_time_present(source: str, term: str) -> bool:
    if re.search(r"[a-z]", term):
        return bool(
            re.search(
                rf"(?:^|[.!?\n]\s*|\bat\s+|\bduring\s+){re.escape(term)}\b",
                source,
            )
        )
    return bool(re.search(rf"(?:^|[。！？\n]\s*|在){re.escape(term)}", source))


def _clock_or_date_is_setting(source: str, position: int) -> bool:
    if _at_sentence_or_shot_boundary(source, position):
        return True
    prefix = source[max(0, position - 12) : position]
    return bool(re.search(r"(?:在|时间是|at|on)\s*$", prefix))


def _inside_quotation(source: str, position: int) -> bool:
    prefix = source[:position]
    if prefix.count('"') % 2 or prefix.count("'") % 2:
        return True
    return prefix.rfind("“") > prefix.rfind("”") or prefix.rfind("‘") > prefix.rfind(
        "’"
    )
