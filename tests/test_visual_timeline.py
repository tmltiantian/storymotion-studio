import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.performance_planner import parse_performance_plan
from factory.prompt_compiler import compile_still_prompt, compile_video_prompt
from factory.prompt_safety import (
    PREVIOUS_SHOT_CONTINUITY,
    SOURCE_UNSPECIFIED_TIME,
    extract_source_time_expressions,
)
from factory.schema import Character, Episode, Shot, episode_from_dict
from factory.visual_timeline import (
    VISUAL_TIMELINE_SCHEMA,
    MicroShot,
    VisualTimeline,
    VisualTimelineError,
    validate_visual_timeline,
    visual_timeline_from_dict,
    visual_timeline_to_dict,
)


@pytest.fixture
def sample_episode() -> Episode:
    return Episode(
        project_id="sample_episode",
        title="Sample episode",
        language="zh-CN",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character("char_1", "林澈", "lead", "guarded", "dark coat", "low"),
            Character("char_2", "苏眠", "lead", "calm", "light coat", "calm"),
        ],
        shots=[
            Shot(
                "shot_001",
                1,
                "和平路书店",
                "夜晚，林澈伸手靠近信封。",
                "雨停后的旧城区，和平路书店柜台上的信封。",
                "static",
                3.0,
                "tense",
            )
        ],
    )


def valid_micro_shot(sample_episode: Episode) -> MicroShot:
    return MicroShot(
        id="micro_001",
        index=1,
        parent_shot_id="shot_001",
        scene_context="和平路书店",
        time_context="夜晚",
        purpose="action",
        character_ids=("char_1",),
        emotion_start="guarded",
        emotion_end="alarmed",
        emotion_intensity=4,
        gaze="at the envelope",
        pose_start="beside the counter",
        pose_end="near the envelope",
        action_actor_id="char_1",
        action_code="reach",
        action_target="信封",
        camera_mode="locked",
        source_duration_seconds=3,
        timeline_duration_seconds=3.0,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_rain",),
        cadence_fps=8,
    )


def valid_timeline(sample_episode: Episode) -> VisualTimeline:
    return VisualTimeline(
        project_id=sample_episode.project_id,
        micro_shots=(valid_micro_shot(sample_episode),),
    )


def test_visual_timeline_v3_round_trips_and_accepts_real_place_name(sample_episode):
    timeline = valid_timeline(sample_episode)

    assert timeline.schema_version == VISUAL_TIMELINE_SCHEMA
    assert validate_visual_timeline(timeline, sample_episode) == []
    assert visual_timeline_from_dict(visual_timeline_to_dict(timeline)) == timeline


@pytest.mark.parametrize(
    "issue,match",
    [
        ("character_ids", "duplicate character ids"),
        ("character_names", "duplicate character names"),
        ("shot_ids", "duplicate shot ids"),
        ("shot_indexes", "duplicate shot indexes"),
    ],
)
def test_validate_visual_timeline_rejects_episode_identity_before_lookup(
    sample_episode, issue, match
):
    episode = sample_episode
    if issue == "character_ids":
        episode = replace(
            episode,
            characters=[
                episode.characters[0],
                replace(episode.characters[1], id="char_1", name="不同姓名"),
            ],
        )
    elif issue == "character_names":
        episode = replace(
            episode,
            characters=[
                episode.characters[0],
                replace(episode.characters[1], name="  林澈  "),
            ],
        )
    elif issue == "shot_ids":
        episode = replace(
            episode,
            shots=[
                episode.shots[0],
                replace(
                    episode.shots[0],
                    index=2,
                    action="苏眠站在书店柜台旁。",
                ),
            ],
        )
    else:
        episode = replace(
            episode,
            shots=[episode.shots[0], replace(episode.shots[0], id="shot_002")],
        )

    errors = validate_visual_timeline(valid_timeline(sample_episode), episode)

    assert any(match in error for error in errors)


def test_visual_timeline_accepts_concise_source_grounded_location(sample_episode):
    timeline = valid_timeline(sample_episode)
    concise_location = replace(timeline.micro_shots[0], scene_context="柜台")

    assert (
        validate_visual_timeline(
            replace(timeline, micro_shots=(concise_location,)), sample_episode
        )
        == []
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "motion-comic-factory.visual-timeline.v2"),
        (("project_id",), 3),
        (("micro_shots",), {}),
        (("micro_shots", 0, "index"), True),
        (("micro_shots", 0, "action_code"), 1),
        (("micro_shots", 0, "negative_constraints"), "no_rain"),
    ],
)
def test_visual_timeline_from_dict_rejects_schema_or_type_coercion(
    sample_episode, path, value
):
    payload = visual_timeline_to_dict(valid_timeline(sample_episode))
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(VisualTimelineError):
        visual_timeline_from_dict(payload)


def test_visual_timeline_from_dict_rejects_removed_free_action_field(sample_episode):
    payload = visual_timeline_to_dict(valid_timeline(sample_episode))
    payload["micro_shots"][0]["single_action"] = "林澈打开信封阅读信件"

    with pytest.raises(VisualTimelineError, match="unexpected keys"):
        visual_timeline_from_dict(payload)


@pytest.mark.parametrize(
    "target",
    [
        "林澈打开信封阅读信件",
        "opens envelope reads letter",
        "镜头横移到信封",
        "fade to black",
        "day turns to night",
        "信",
    ],
)
def test_visual_timeline_rejects_action_target_that_is_not_one_object_phrase(
    sample_episode, target
):
    timeline = valid_timeline(sample_episode)
    invalid = replace(timeline.micro_shots[0], action_target=target)

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("action_target" in error for error in errors)


def test_visual_timeline_rejects_bad_actor_self_and_negative_code(sample_episode):
    timeline = valid_timeline(sample_episode)
    invalid = replace(
        timeline.micro_shots[0],
        action_actor_id="char_2",
        action_code="open",
        action_target="self",
        negative_constraints=("不允许字幕",),
    )

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("action_actor_id" in error for error in errors)
    assert any("action_target" in error for error in errors)
    assert any("negative_constraints" in error for error in errors)


def test_visual_timeline_rejects_duplicate_ids_indexes_and_characters(sample_episode):
    timeline = valid_timeline(sample_episode)
    duplicate = replace(timeline.micro_shots[0], character_ids=("char_1", "char_1"))
    same_id = replace(duplicate, index=1)
    invalid_timeline = replace(timeline, micro_shots=(duplicate, same_id))

    errors = validate_visual_timeline(invalid_timeline, sample_episode)

    assert any("duplicate character_ids" in error for error in errors)
    assert any("duplicate micro-shot ids" in error for error in errors)
    assert any("duplicate micro-shot indexes" in error for error in errors)


def test_visual_timeline_rejects_fragments_and_unspecified_time_when_source_has_night(
    sample_episode,
):
    timeline = valid_timeline(sample_episode)
    invalid = replace(
        timeline.micro_shots[0],
        scene_context="书",
        time_context=SOURCE_UNSPECIFIED_TIME,
    )

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("scene_context" in error for error in errors)
    assert any("time_context" in error for error in errors)


def test_visual_timeline_rejects_source_object_as_scene_context(sample_episode):
    timeline = valid_timeline(sample_episode)
    invalid = replace(timeline.micro_shots[0], scene_context="信封")

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("scene_context" in error for error in errors)


def test_visual_timeline_rejects_source_time_as_an_action_target(sample_episode):
    timeline = valid_timeline(sample_episode)
    invalid = replace(timeline.micro_shots[0], action_target="夜晚")

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("action_target" in error for error in errors)


def test_visual_timeline_rejects_wrong_schema_for_direct_construction(sample_episode):
    invalid = replace(valid_timeline(sample_episode), schema_version="wrong")

    assert any(
        "schema_version" in error
        for error in validate_visual_timeline(invalid, sample_episode)
    )


def test_write_visual_timeline_writes_json_payload(sample_episode, tmp_path):
    from factory.visual_timeline import write_visual_timeline

    output = tmp_path / "visual_timeline.json"

    written = write_visual_timeline(valid_timeline(sample_episode), output)

    assert written == output
    assert json.loads(output.read_text(encoding="utf-8")) == visual_timeline_to_dict(
        valid_timeline(sample_episode)
    )


def test_visual_timeline_rejects_wrong_parent_sum_and_unknown_character(sample_episode):
    timeline = valid_timeline(sample_episode)
    invalid = replace(
        timeline.micro_shots[0],
        character_ids=("char_missing",),
        action_actor_id="object",
        timeline_duration_seconds=2.0,
    )

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(invalid,)), sample_episode
    )

    assert any("char_missing" in error for error in errors)
    assert any("duration" in error for error in errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("index", True),
        ("parent_shot_id", []),
        ("scene_context", None),
        ("time_context", True),
        ("purpose", "unknown"),
        ("character_ids", ["char_1"]),
        ("emotion_start", None),
        ("emotion_end", []),
        ("emotion_intensity", "4"),
        ("gaze", None),
        ("pose_start", True),
        ("pose_end", []),
        ("action_actor_id", None),
        ("action_code", []),
        ("action_target", True),
        ("camera_mode", None),
        ("source_duration_seconds", 3.0),
        ("timeline_duration_seconds", False),
        ("entry_cut", []),
        ("exit_cut", None),
        ("negative_constraints", ["no_rain"]),
        ("cadence_fps", "8"),
    ],
)
def test_validate_visual_timeline_is_total_for_malformed_micro_shot_fields(
    sample_episode, field, value
):
    timeline = valid_timeline(sample_episode)
    malformed = replace(timeline.micro_shots[0], **{field: value})

    errors = validate_visual_timeline(
        replace(timeline, micro_shots=(malformed,)), sample_episode
    )

    assert errors
    assert any(field in error or "micro-shot" in error for error in errors)


@pytest.mark.parametrize(
    "timeline",
    [
        None,
        "timeline",
        VisualTimeline(project_id="sample_episode", micro_shots=None),
        VisualTimeline(project_id="sample_episode", micro_shots="shots"),
        VisualTimeline(project_id="sample_episode", micro_shots=[None]),
        VisualTimeline(project_id=True, micro_shots=()),
    ],
)
def test_validate_visual_timeline_is_total_for_malformed_root_containers(
    sample_episode, timeline
):
    errors = validate_visual_timeline(timeline, sample_episode)

    assert errors


@pytest.mark.parametrize("duration", [1, 1.99, 4.01, 5])
def test_validate_visual_timeline_enforces_two_to_four_seconds(
    sample_episode, duration
):
    episode = replace(
        sample_episode,
        shots=[replace(sample_episode.shots[0], duration_seconds=float(duration))],
    )
    micro = replace(
        valid_micro_shot(sample_episode), timeline_duration_seconds=duration
    )

    errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
    )

    assert any("2-4 seconds" in error for error in errors)


@pytest.mark.parametrize(
    "scene_title,action,visual_prompt,expected",
    [
        ("第 1 镜", "雨停后的旧城区很安静。", "竖版9:16", {"雨停后"}),
        ("第 2 镜", "列车开往十年前。", "竖版9:16", set()),
        ("第 3 镜", "海报和他们此刻的模样相同。", "竖版9:16", set()),
        ("第 4 镜", "倒计时持续十秒。", "竖版9:16", set()),
        ("第 5 镜", "屏幕亮起的一瞬间，他抬头。", "竖版9:16", set()),
        ("第 6 镜", "他们终于明白。", "竖版9:16", set()),
    ],
)
def test_extract_source_time_expressions_uses_current_scene_semantics(
    scene_title, action, visual_prompt, expected
):
    parent = Shot(
        "shot",
        1,
        scene_title,
        action,
        visual_prompt,
        "static",
        3.0,
        "quiet",
    )

    assert extract_source_time_expressions(parent) == expected
    assert "9:16" not in extract_source_time_expressions(parent)


@pytest.mark.parametrize(
    "action,expected",
    [
        ("Later, the shop falls quiet.", {"later"}),
        ("Years ago, the shop was open.", {"years ago"}),
        ('The sign reads "later".', set()),
        ("The bus travels to years ago.", set()),
        ("He waits for later.", set()),
    ],
)
def test_extract_source_time_expressions_handles_english_relative_boundaries(
    action, expected
):
    parent = Shot("shot", 1, "Shop", action, "9:16", "static", 3.0, "quiet")

    assert extract_source_time_expressions(parent) == expected


def test_extract_source_time_expressions_matches_all_six_real_parent_shots():
    episode = _real_episode()
    expected = {
        "shot_001": {"雨停后"},
        "shot_002": set(),
        "shot_003": set(),
        "shot_004": set(),
        "shot_005": set(),
        "shot_006": set(),
    }

    assert {
        shot.id: extract_source_time_expressions(shot) for shot in episode.shots
    } == expected


@pytest.mark.parametrize(
    "action",
    [
        "票面写着“十年前”。",
        'The sign reads "later".',
        "时钟显示“9:16”。",
        "一瞬间的闪光照亮屏幕。",
    ],
)
def test_extract_source_time_expressions_rejects_quotation_and_event_duration(
    action,
):
    parent = Shot("shot", 1, "Shop", action, "竖版9:16", "static", 3.0, "quiet")

    assert extract_source_time_expressions(parent) == set()


def test_source_unspecified_rejects_only_true_current_scene_time(sample_episode):
    timeline = valid_timeline(sample_episode)
    parent = replace(
        sample_episode.shots[0],
        action="林澈站在列车旁，列车随后开往十年前。",
        visual_prompt="和平路书店与信封，竖版9:16。",
    )
    episode = replace(sample_episode, shots=[parent])
    micro = replace(timeline.micro_shots[0], time_context=SOURCE_UNSPECIFIED_TIME)

    assert (
        validate_visual_timeline(replace(timeline, micro_shots=(micro,)), episode) == []
    )


def test_previous_shot_continuity_is_rejected_first_and_allowed_later(sample_episode):
    first = replace(
        valid_micro_shot(sample_episode), scene_context=PREVIOUS_SHOT_CONTINUITY
    )
    second = replace(
        valid_micro_shot(sample_episode),
        id="micro_002",
        index=2,
        parent_shot_id="shot_002",
        scene_context=PREVIOUS_SHOT_CONTINUITY,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )
    continued_parent = Shot(
        "shot_002",
        2,
        "第 2 镜",
        "林澈停在信封旁。",
        "信封保持不动。",
        "static",
        3.0,
        "tense",
    )
    episode = replace(
        sample_episode,
        shots=[sample_episode.shots[0], continued_parent],
    )

    first_errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(first,)), episode
    )
    later_errors = validate_visual_timeline(
        VisualTimeline(
            project_id=episode.project_id,
            micro_shots=(valid_micro_shot(sample_episode), second),
        ),
        episode,
    )

    assert any("first micro-shot" in error for error in first_errors)
    assert later_errors == []


def test_continuity_rejects_current_parent_with_explicit_source_location(
    sample_episode,
):
    first = valid_micro_shot(sample_episode)
    second = replace(
        first,
        id="micro_002",
        index=2,
        scene_context=PREVIOUS_SHOT_CONTINUITY,
    )
    episode = replace(
        sample_episode,
        shots=[replace(sample_episode.shots[0], duration_seconds=6.0)],
    )

    errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(first, second)),
        episode,
    )

    assert any("explicit source location" in error for error in errors)


def test_continuity_chain_resolves_recursively_to_concrete_prior_scene(sample_episode):
    parent_2 = Shot(
        "shot_002",
        2,
        "第 2 镜",
        "林澈停在信封旁。",
        "信封保持不动。",
        "static",
        3.0,
        "tense",
    )
    parent_3 = replace(parent_2, id="shot_003", index=3)
    episode = replace(
        sample_episode, shots=[sample_episode.shots[0], parent_2, parent_3]
    )
    first = valid_micro_shot(sample_episode)
    second = replace(
        first,
        id="micro_002",
        index=2,
        parent_shot_id="shot_002",
        scene_context=PREVIOUS_SHOT_CONTINUITY,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )
    third = replace(
        second,
        id="micro_003",
        index=3,
        parent_shot_id="shot_003",
    )

    assert (
        validate_visual_timeline(
            VisualTimeline(
                project_id=episode.project_id, micro_shots=(first, second, third)
            ),
            episode,
        )
        == []
    )


def test_continuity_rejects_missing_previous_index_and_unresolved_chain(sample_episode):
    parent = replace(
        sample_episode.shots[0],
        scene_title="第 2 镜",
        action="林澈停在信封旁。",
        visual_prompt="信封保持不动。",
    )
    episode = replace(sample_episode, shots=[parent])
    unresolved = replace(
        valid_micro_shot(sample_episode),
        index=2,
        scene_context=PREVIOUS_SHOT_CONTINUITY,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )

    errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(unresolved,)),
        episode,
    )

    assert any("actual previous micro-shot" in error for error in errors)


@pytest.mark.parametrize("duration", [3, 3.0])
def test_visual_timeline_from_dict_preserves_numeric_duration_type(
    sample_episode, duration
):
    payload = visual_timeline_to_dict(valid_timeline(sample_episode))
    payload["micro_shots"][0]["timeline_duration_seconds"] = duration

    timeline = visual_timeline_from_dict(payload)

    assert type(timeline.micro_shots[0].timeline_duration_seconds) is type(duration)


@pytest.mark.parametrize(
    "location",
    [
        "便利店",
        "街灯下",
        "巷口",
        "电影院",
        "招牌",
        "售票厅",
        "检票口",
        "屏幕",
        "银幕",
        "convenience store",
        "streetlight",
        "alley entrance",
        "cinema",
        "sign",
        "ticket hall",
        "ticket gate",
        "screen",
    ],
)
def test_visual_timeline_accepts_required_location_vocabulary(sample_episode, location):
    parent = replace(
        sample_episode.shots[0],
        scene_title="第 4 镜",
        action=f"环境位于{location}。林澈看见信封。",
        visual_prompt=f"{location}。",
    )
    episode = replace(sample_episode, shots=[parent])
    micro = replace(
        valid_micro_shot(sample_episode),
        scene_context=location,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )

    assert (
        validate_visual_timeline(
            VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
        )
        == []
    )


@pytest.mark.parametrize(
    "target,action",
    [
        ("电影院招牌", "林澈在电影院看见亮起的招牌。"),
        ("cinema sign", "林澈 sees a sign light up outside the cinema."),
    ],
)
def test_visual_timeline_accepts_one_composed_source_grounded_noun_phrase(
    sample_episode, target, action
):
    parent = replace(
        sample_episode.shots[0],
        action=action,
        visual_prompt="便利店柜台上的信封。",
    )
    episode = replace(sample_episode, shots=[parent])
    micro = replace(
        valid_micro_shot(sample_episode),
        action_target=target,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )

    assert (
        validate_visual_timeline(
            VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
        )
        == []
    )


def test_generic_numbered_scene_title_is_not_a_location(sample_episode):
    parent = replace(sample_episode.shots[0], scene_title="第 4 镜")
    episode = replace(sample_episode, shots=[parent])
    micro = replace(valid_micro_shot(sample_episode), scene_context="第 4 镜")

    errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
    )

    assert any("scene_context" in error for error in errors)


@pytest.mark.parametrize("target", ["黑色信封", "车票", "招牌", "检票口", "屏幕"])
def test_visual_timeline_accepts_exact_source_noun_targets(sample_episode, target):
    parent = replace(
        sample_episode.shots[0],
        action=f"林澈在便利店看见{target}。",
        visual_prompt=f"便利店里的{target}。",
    )
    episode = replace(sample_episode, shots=[parent])
    micro = replace(
        valid_micro_shot(sample_episode),
        action_target=target,
        time_context=SOURCE_UNSPECIFIED_TIME,
    )

    assert (
        validate_visual_timeline(
            VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
        )
        == []
    )


@pytest.mark.parametrize(
    "target",
    [
        "黑色信封交给陌生人",
        "人影",
        "手里",
        "envelope and stranger",
        "camera-facing envelope",
    ],
)
def test_visual_timeline_rejects_smuggled_action_targets(sample_episode, target):
    parent = replace(
        sample_episode.shots[0],
        action=f"林澈在便利店看见{target}。",
        visual_prompt=f"便利店里的{target}。",
    )
    episode = replace(sample_episode, shots=[parent])
    micro = replace(valid_micro_shot(sample_episode), action_target=target)

    errors = validate_visual_timeline(
        VisualTimeline(project_id=episode.project_id, micro_shots=(micro,)), episode
    )

    assert any("action_target" in error for error in errors)


def _real_episode() -> Episode:
    path = Path(__file__).parents[1] / "samples" / "sample_episode.json"
    return episode_from_dict(json.loads(path.read_text(encoding="utf-8")))


def _real_micro(
    micro_id,
    index,
    parent,
    scene,
    duration,
    *,
    purpose="object",
    characters=(),
    actor="object",
    code="hold_still",
    target="黑色信封",
    time=SOURCE_UNSPECIFIED_TIME,
    camera="object_insert",
):
    if target == "self":
        gaze = "视线稳定"
        pose_start = "身体保持静止"
        pose_end = "姿态保持稳定"
    else:
        gaze = f"朝向{target}"
        pose_start = f"{target}位于构图中央"
        pose_end = f"{target}保持在构图中央"
    return {
        "id": micro_id,
        "index": index,
        "parent_shot_id": parent,
        "scene_context": scene,
        "time_context": time,
        "purpose": purpose,
        "character_ids": list(characters),
        "emotion_start": "克制",
        "emotion_end": "警觉",
        "emotion_intensity": 3,
        "gaze": gaze,
        "pose_start": pose_start,
        "pose_end": pose_end,
        "action_actor_id": actor,
        "action_code": code,
        "action_target": target,
        "camera_mode": camera,
        "source_duration_seconds": 4,
        "timeline_duration_seconds": duration,
        "entry_cut": "hard_cut",
        "exit_cut": "hard_cut",
        "negative_constraints": ["no_text", "no_scene_change"],
        "cadence_fps": 8,
    }


def real_six_shot_payload() -> dict[str, object]:
    episode = _real_episode()
    lin, su = (character.id for character in episode.characters)
    specs = [
        (
            "shot_001",
            "旧城区",
            3.5,
            "establishing",
            (),
            "environment",
            "drip",
            "便利店",
            "雨停后",
            "locked",
        ),
        (
            "shot_001",
            "便利店",
            4.0,
            "action",
            (lin,),
            lin,
            "open",
            "便利店的门",
            "雨停后",
            "locked",
        ),
        (
            "shot_001",
            "柜台",
            4.0,
            "object",
            (),
            "object",
            "hold_still",
            "黑色信封",
            "雨停后",
            "object_insert",
        ),
        (
            "shot_002",
            "街灯下",
            4.0,
            "action",
            (su,),
            su,
            "grasp",
            "车票",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_002",
            "街灯下",
            4.0,
            "object",
            (),
            "object",
            "hold_still",
            "车票",
            SOURCE_UNSPECIFIED_TIME,
            "object_insert",
        ),
        (
            "shot_003",
            "巷口",
            3.0,
            "action",
            (lin,),
            lin,
            "head_turn",
            "巷口",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_003",
            "电影院",
            3.0,
            "establishing",
            (),
            "environment",
            "light_up",
            "招牌",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_003",
            "招牌",
            3.0,
            "object",
            (),
            "object",
            "hold_still",
            "招牌",
            SOURCE_UNSPECIFIED_TIME,
            "object_insert",
        ),
        (
            "shot_003",
            "电影院",
            4.0,
            "reaction",
            (lin,),
            lin,
            "eyes_widen",
            "招牌",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_004",
            "售票厅",
            3.0,
            "establishing",
            (),
            "environment",
            "sway",
            "售票厅",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_004",
            "售票厅",
            4.0,
            "action",
            (su,),
            su,
            "stop",
            "检票口",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_004",
            "检票口",
            4.0,
            "object",
            (),
            "object",
            "hold_still",
            "检票口",
            SOURCE_UNSPECIFIED_TIME,
            "object_insert",
        ),
        (
            "shot_005",
            "屏幕",
            3.0,
            "establishing",
            (),
            "environment",
            "light_up",
            "屏幕",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_005",
            "屏幕",
            3.0,
            "reaction",
            (lin,),
            lin,
            "eyes_widen",
            "黑色信封",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_005",
            "屏幕",
            4.0,
            "object",
            (),
            "object",
            "reveal",
            "黑色信封",
            SOURCE_UNSPECIFIED_TIME,
            "object_insert",
        ),
        (
            "shot_006",
            PREVIOUS_SHOT_CONTINUITY,
            3.5,
            "reaction",
            (lin, su),
            lin,
            "blink",
            "self",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
        (
            "shot_006",
            PREVIOUS_SHOT_CONTINUITY,
            4.0,
            "resolve",
            (lin, su),
            su,
            "hold_still",
            "self",
            SOURCE_UNSPECIFIED_TIME,
            "locked",
        ),
    ]
    micro_shots = []
    for index, spec in enumerate(specs, start=1):
        parent, scene, duration, purpose, chars, actor, code, target, time, camera = (
            spec
        )
        micro_shots.append(
            _real_micro(
                f"micro_{index:03d}",
                index,
                parent,
                scene,
                duration,
                purpose=purpose,
                characters=chars,
                actor=actor,
                code=code,
                target=target,
                time=time,
                camera=camera,
            )
        )
    return {
        "schema_version": VISUAL_TIMELINE_SCHEMA,
        "project_id": episode.project_id,
        "micro_shots": micro_shots,
    }


def test_real_six_shot_timeline_parses_validates_and_compiles_both_routes():
    episode = _real_episode()

    timeline = parse_performance_plan(
        json.dumps(real_six_shot_payload(), ensure_ascii=False), episode
    )

    assert validate_visual_timeline(timeline, episode) == []
    assert {shot.parent_shot_id for shot in timeline.micro_shots} == {
        parent.id for parent in episode.shots
    }
    assert all(
        2 <= shot.timeline_duration_seconds <= 4 for shot in timeline.micro_shots
    )
    video = compile_video_prompt(episode, timeline.micro_shots[1])
    still = compile_still_prompt(episode, timeline.micro_shots[2])
    continuity = compile_video_prompt(
        episode, timeline.micro_shots[-1], previous_scene_context="屏幕"
    )
    assert "林澈打开便利店的门" in video
    assert "Only visible action" in still
    assert "Scene: 屏幕" in continuity
    assert all(
        "self" not in field
        for shot in timeline.micro_shots[-2:]
        for field in (shot.gaze, shot.pose_start, shot.pose_end)
    )
