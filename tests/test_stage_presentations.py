from __future__ import annotations

import json
import math

import pytest

from factory.pipeline_contracts import StageName
from factory.stage_presentations import build_stage_presentation


def test_script_presentation_keeps_creator_fields_and_drops_internal_fields():
    source = {
        "schema_version": "motion-comic-factory.script.v1",
        "project_id": "secret-internal-id",
        "episode_draft": {
            "title": "雨夜来电",
            "target_aspect_ratio": "9:16",
            "characters": [
                {
                    "id": "char_01",
                    "name": "阿眠",
                    "role": "主角",
                    "description": "谨慎但好奇",
                    "visual_anchor": "short black hair",
                    "voice_style": "清亮、克制",
                }
            ],
            "shots": [
                {
                    "id": "shot_001",
                    "index": 1,
                    "scene_title": "门外",
                    "action": "她停在门边听见铃声。",
                    "camera": "近景",
                    "duration_seconds": 6.5,
                    "dialogue": [
                        {"speaker_id": "char_01", "emotion": "紧张", "text": "谁？"}
                    ],
                    "visual_prompt": "internal prompt",
                }
            ],
        },
    }

    result = build_stage_presentation("script", [source])

    assert result["stage"] == "script"
    assert result["state"] == "ready"
    assert result["title"] == "雨夜来电"
    assert result["total_duration_seconds"] == 6.5
    assert result["characters"] == [
        {
            "name": "阿眠",
            "role": "主角",
            "description": "谨慎但好奇",
            "voice": "清亮、克制",
        }
    ]
    assert result["shots"][0]["dialogue"] == [
        {"speaker": "阿眠", "emotion": "紧张", "text": "谁？"}
    ]
    assert "schema_version" not in repr(result)
    assert "project_id" not in repr(result)
    assert "visual_prompt" not in repr(result)
    assert "char_01" not in repr(result)
    assert "shot_001" not in repr(result)
    assert "id" not in result["characters"][0]


def test_narrative_presentation_suppresses_prompt_fragments_and_translates_known_cameras():
    source = {
        "schema_version": "motion-comic-factory.script.v1",
        "episode_draft": {
            "characters": [
                {"name": "阿眠", "visual_anchor": "anime motion comic, short black hair"},
                {"name": "小舟", "visual_anchor": "短黑发，神情警觉"},
            ],
            "shots": [
                {"index": 1, "camera": "medium shot, slow push-in"},
                {"index": 2, "camera": "close-up, slight handheld tension"},
                {"index": 3, "camera": "wide dynamic orbit"},
            ],
        },
    }

    result = build_stage_presentation("script", [source])

    assert "appearance" not in result["characters"][0]
    assert result["characters"][1]["appearance"] == "短黑发，神情警觉"
    assert [shot.get("camera") for shot in result["shots"]] == [
        "中景，缓慢推进",
        "近景，轻微手持",
        None,
    ]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "anime motion comic" not in serialized
    assert "medium shot, slow push-in" not in serialized
    assert "close-up, slight handheld tension" not in serialized
    assert "wide dynamic orbit" not in serialized


def test_concept_presentation_normalizes_only_creator_target_fields():
    source = {
        "schema_version": "motion-comic-factory.concept.v1",
        "project_id": "secret",
        "title": "雨夜来电",
        "premise": "一个深夜电话改变了她的选择。",
        "target": {
            "target_duration_seconds": 42.0,
            "target_aspect_ratio": "9:16",
            "target_resolution": "1080x1920",
            "fps": 30,
            "shots": 8,
            "provider": "internal",
            "prompt": "do not expose",
        },
        "characters": [
            {
                "id": "c1",
                "name": "阿眠",
                "role": "主角",
                "description": "谨慎但好奇",
                "visual_anchor": "短黑发",
                "voice_style": "清亮",
            }
        ],
        "source_kind": "idea",
    }

    result = build_stage_presentation(StageName.CONCEPT, [source])

    assert result["target"] == {
        "duration_seconds": 42.0,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "fps": 30,
        "shots": 8,
    }
    assert result["characters"][0]["appearance"] == "短黑发"
    assert result["characters"][0]["voice"] == "清亮"
    assert "source_kind" not in repr(result)
    assert "provider" not in repr(result)
    assert "prompt" not in repr(result)


def test_storyboard_presentation_uses_episode_shape_and_safe_shots():
    source = {
        "project_id": "internal",
        "title": "门外",
        "target_aspect_ratio": "16:9",
        "target_resolution": "1920x1080",
        "characters": [
            {
                "id": "c1",
                "name": "阿眠",
                "role": "主角",
                "description": "谨慎",
                "visual_anchor": "短发",
                "voice_style": "克制",
            }
        ],
        "shots": [
            {
                "id": "s1",
                "index": 2,
                "scene_title": "门外",
                "action": "她停下。",
                "camera": "近景",
                "duration_seconds": 2.0,
                "dialogue": [],
                "visual_prompt": "private",
            },
            {
                "id": "s2",
                "index": 3,
                "scene_title": "走廊",
                "action": "她回头。",
                "camera": "中景",
                "duration_seconds": 3.5,
                "dialogue": [],
                "visual_prompt": "private",
            },
        ],
    }

    result = build_stage_presentation("storyboard", [source])

    assert result["stage"] == "storyboard"
    assert result["title"] == "门外"
    assert result["total_duration_seconds"] == 5.5
    assert [shot["index"] for shot in result["shots"]] == [2, 3]
    assert result["shots"][0]["title"] == "门外"
    assert "s1" not in json.dumps(result, ensure_ascii=False)
    assert "visual_prompt" not in json.dumps(result, ensure_ascii=False)


def test_assets_merge_character_manifest_and_review_without_provenance_details():
    character_assets = {
        "schema_version": "motion-comic-factory.character-assets.v1",
        "project_id": "secret",
        "source_manifest_path": "/private/assets.json",
        "production_ready": True,
        "characters": [
            {
                "character_id": "c1",
                "name": "阿眠",
                "production_ready": True,
                "provenance_status": "confirmed",
                "asset_source": "user_generated_ai",
                "reference_image_path": "/private/a.png",
                "matched_by": "character_id",
            }
        ],
    }
    review = {
        "schema_version": "motion-comic-factory.asset-review.v1",
        "project_id": "secret",
        "production_ready": True,
        "review_items": ["角色身份稳定", {"bad": "mapping"}],
    }

    result = build_stage_presentation("assets", [character_assets, review])

    assert result["production_ready"] is True
    assert result["characters"] == [
        {"name": "阿眠", "ready": True, "source_status": "confirmed"}
    ]
    assert result["review_items"] == ["角色身份稳定"]
    serialized = json.dumps(result, ensure_ascii=False)
    for internal in ("asset_source", "reference_image_path", "matched_by", "character_id", "project_id"):
        assert internal not in serialized


def test_audio_presentation_aggregates_timings_and_omits_dialogue_ids():
    source = {
        "schema_version": "motion-comic-factory.audio.v1",
        "project_id": "secret",
        "voiceover_audio": "/private/audio.wav",
        "timings": [
            {
                "shot_id": "s1",
                "dialogue_id": "d1",
                "speaker_name": "阿眠",
                "speaker": "fallback",
                "text": "谁？",
                "start_seconds": 0.5,
                "end_seconds": 2.0,
            },
            {
                "speaker": "旁白",
                "text": "门外很安静。",
                "start_seconds": 2.0,
                "end_seconds": 4.25,
            },
        ],
    }

    result = build_stage_presentation("audio", [source])

    assert result["dialogue_count"] == 2
    assert result["total_duration_seconds"] == 4.25
    assert result["speakers"] == [
        {"name": "阿眠", "line_count": 1},
        {"name": "旁白", "line_count": 1},
    ]
    assert result["timings"][0] == {
        "speaker": "阿眠",
        "text": "谁？",
        "start_seconds": 0.5,
        "end_seconds": 2.0,
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "shot_id" not in serialized
    assert "dialogue_id" not in serialized
    assert "/private" not in serialized


def test_video_edit_and_delivery_presentations_are_allow_listed():
    video = {
        "schema_version": "motion-comic-factory.video.v1",
        "generation_mode": "local_storyboard_preview",
        "primary_video": "/private/video.mp4",
        "clips": ["/private/one.mp4", "/private/two.mp4"],
        "clip_by_shot": {"s1": "/private/one.mp4"},
        "cloud_generation_requested": False,
        "lip_sync_policy": "local_preview",
    }
    edit = {
        "schema_version": "motion-comic-factory.edit.v1",
        "duration_seconds": 12.5,
        "final_preview": "/private/final.mp4",
        "subtitles": "/private/subtitles.srt",
        "transition_policy": "cut_on_action_or_audio_motivation",
        "assembly_policy": "normalized_cfr_trim_pad",
    }
    delivery = {
        "schema_version": "motion-comic-factory.delivery.v1",
        "master": "/private/master.mp4",
        "sha256": "a" * 64,
        "publication_status": "REVIEW_REQUIRED",
        "eval_evidence": {"status": "approved", "automatic_passed": True},
    }

    video_result = build_stage_presentation("video", [video])
    edit_result = build_stage_presentation("edit", [edit])
    delivery_result = build_stage_presentation("deliver", [delivery])

    assert video_result == {
        "stage": "video",
        "state": "ready",
        "generation_mode": "local_storyboard_preview",
        "clip_count": 2,
        "cloud_generated": False,
        "lip_sync": "local_preview",
    }
    assert edit_result == {
        "stage": "edit",
        "state": "ready",
        "duration_seconds": 12.5,
        "transition": "cut_on_action_or_audio_motivation",
        "assembly": "normalized_cfr_trim_pad",
        "subtitle_ready": True,
    }
    assert delivery_result == {
        "stage": "deliver",
        "state": "ready",
        "publication_status": "REVIEW_REQUIRED",
        "quality_approved": True,
    }
    serialized = json.dumps((video_result, edit_result, delivery_result))
    for internal in ("primary_video", "clip_by_shot", "final_preview", "subtitles", "master", "sha256"):
        assert internal not in serialized


def test_eval_presentation_supports_generic_checks_and_allow_lists_fields():
    source = {
        "schema_version": "motion-comic-factory.eval.v1",
        "status": "REVIEW_REQUIRED",
        "automatic_passed": True,
        "checks": [
            {
                "name": "对白同步",
                "severity": "warning",
                "passed": True,
                "findings": ["节奏自然", {"private": "drop"}],
                "source_object": {"project_id": "secret"},
            }
        ],
        "review_dimensions": ["声音", "画面", {"private": "drop"}],
    }

    result = build_stage_presentation("eval", [source])

    assert result == {
        "stage": "eval",
        "state": "ready",
        "passed": True,
        "status": "REVIEW_REQUIRED",
        "checks": [
            {
                "name": "对白同步",
                "severity": "warning",
                "passed": True,
                "findings": ["节奏自然"],
            }
        ],
        "review_dimensions": ["声音", "画面"],
    }
    assert "source_object" not in repr(result)
    assert "project_id" not in repr(result)


def test_eval_v2_maps_failures_and_known_summaries_without_specialist_operation():
    source = {
        "schema_version": "motion-comic-factory.eval.v2",
        "status": "AUTOMATIC_FAILURE",
        "automatic_passed": False,
        "hard_failures": [
            {
                "message": "对白重叠",
                "evidence": {
                    "overlap_count": 2,
                    "actual_seconds": 8.0,
                    "tolerance_seconds": math.inf,
                    "operation_code": "OVERLAP_CHECK",
                    "unknown_key": "drop",
                    "path": "/private/nope",
                },
            }
        ],
        "timing": {"cue_count": 3, "overlap_count": 2, "private": "drop"},
        "shots": {"expected_count": 4, "rendered_count": 3, "ids": ["s1"]},
        "specialist_review": {"operation": {"secret": True}},
        "review_dimensions": ["连续性"],
    }

    result = build_stage_presentation("eval", [source])

    assert result["passed"] is False
    assert result["checks"][0] == {
        "name": "对白重叠",
        "severity": "error",
        "passed": False,
        "findings": ["重叠对白：2 条", "实际时长：8 秒", "检查状态码：OVERLAP_CHECK"],
    }
    assert {item["name"] for item in result["checks"][1:]} == {"时间安排", "镜头完成情况"}
    serialized = repr(result)
    for technical_key in (
        "operation_code",
        "overlap_count",
        "actual_seconds",
        "cue_count",
        "expected_count",
        "rendered_count",
        "unknown_key",
    ):
        assert technical_key not in serialized
    assert "/private" not in repr(result)
    assert "s1" not in repr(result)


def test_malformed_optional_sections_and_nonfinite_numbers_keep_valid_sections():
    source = {
        "schema_version": "motion-comic-factory.script.v1",
        "episode_draft": {
            "title": "仍然可见",
            "characters": "not-a-list",
            "shots": [
                {
                    "index": 1,
                    "scene_title": "有效镜头",
                    "action": "继续。",
                    "camera": "近景",
                    "duration_seconds": 2.0,
                    "dialogue": "not-a-list",
                },
                {
                    "index": 2,
                    "scene_title": "坏时长",
                    "duration_seconds": math.inf,
                },
            ],
        },
    }

    result = build_stage_presentation("script", [source, {"schema_version": "unknown"}, None])

    assert result["title"] == "仍然可见"
    assert result["total_duration_seconds"] == 2.0
    assert len(result["shots"]) == 2
    assert "duration_seconds" not in result["shots"][1]
    assert "inf" not in repr(result).lower()


def test_overflowing_numeric_values_are_omitted():
    result = build_stage_presentation(
        "edit",
        [
            {
                "schema_version": "motion-comic-factory.edit.v1",
                "duration_seconds": 10**10000,
                "subtitles": "",
            }
        ],
    )

    assert result == {
        "stage": "edit",
        "state": "ready",
        "subtitle_ready": False,
    }


def test_aggregate_shot_duration_overflow_is_omitted():
    result = build_stage_presentation(
        "storyboard",
        [
            {
                "project_id": "internal",
                "title": "大时长",
                "characters": [],
                "shots": [
                    {"index": 1, "duration_seconds": 1.7e308},
                    {"index": 2, "duration_seconds": 1.7e308},
                ],
            }
        ],
    )

    assert result["stage"] == "storyboard"
    assert result["state"] == "ready"
    assert "total_duration_seconds" not in result
    assert all(math.isfinite(shot["duration_seconds"]) for shot in result["shots"])


@pytest.mark.parametrize(
    "episode_draft",
    [None, "malformed"],
)
def test_recognized_script_with_missing_or_malformed_episode_is_unavailable(episode_draft):
    source = {"schema_version": "motion-comic-factory.script.v1"}
    if episode_draft is not None:
        source["episode_draft"] = episode_draft

    assert build_stage_presentation("script", [source]) == {
        "stage": "script",
        "state": "unavailable",
    }


@pytest.mark.parametrize(
    ("stage", "documents"),
    [
        ("concept", [{"schema_version": "unknown"}]),
        ("script", [None, {"schema_version": "unknown"}]),
        ("assets", [{"characters": []}]),
        ("audio", [{"schema_version": "motion-comic-factory.audio.v0"}]),
        ("eval", [{"schema_version": "motion-comic-factory.eval.v0"}]),
    ],
)
def test_no_supported_document_returns_exact_unavailable(stage, documents):
    assert build_stage_presentation(stage, documents) == {
        "stage": stage,
        "state": "unavailable",
    }


def test_unknown_stage_returns_none():
    assert build_stage_presentation("not-a-stage", []) is None
