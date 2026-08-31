import json
import hashlib
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from factory.candidate_review import (
    CandidateReviewManifest,
    approved_selection_from_manifest,
)
from factory.micro_still_batch import PRODUCTION_STILL_MODELS
from factory.micro_video_batch import PRODUCTION_VIDEO_MODELS
from factory.model_bakeoff import (
    MODEL_BAKEOFF_REVIEW_SCHEMA,
    SCORE_WEIGHTS,
    STILL_HARD_FAILURES,
    VIDEO_HARD_FAILURES,
    ModelBakeoffError,
    build_model_bakeoff_plan,
    finalize_bakeoff,
    model_route_capability,
    require_speaking_capability,
    require_selected_production_model,
    require_selected_still_model,
    weighted_score,
)
from factory.dialogue_assets import DialogueAudioAsset, DialogueAudioManifest
from factory.performance_card import PerformanceCard, PerformanceSheet
from factory.schema import Character, DialogueLine, Episode, Shot
from factory.visual_timeline import MicroShot, VisualTimeline
from tests.media_fixtures import VALID_VIDEO_MP4


VIDEO_MODELS = ["doubao-seedance-2-0"]
STILL_MODELS = ["doubao-seedream-4-5"]


def _micro_shot(
    micro_shot_id: str,
    index: int,
    *,
    character_ids: tuple[str, ...] = ("char_a",),
    purpose: str = "action",
    camera_mode: str = "locked",
    parent_shot_id: str = "shot_001",
) -> MicroShot:
    return MicroShot(
        id=micro_shot_id,
        index=index,
        parent_shot_id=parent_shot_id,
        scene_context="Shop",
        time_context="source-unspecified",
        purpose=purpose,
        character_ids=character_ids,
        emotion_start="still" if not character_ids else "tense",
        emotion_end="still" if not character_ids else "alert",
        emotion_intensity=3,
        gaze="at the envelope",
        pose_start="beside the counter" if character_ids else "on the counter",
        pose_end="reaching" if character_ids else "on the counter",
        action_actor_id=character_ids[0] if character_ids else "object",
        action_code="reach" if character_ids else "hold_still",
        action_target="envelope",
        camera_mode=camera_mode,
        source_duration_seconds=3,
        timeline_duration_seconds=3,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_rain",),
        cadence_fps=8,
    )


def _timeline(*, with_still: bool = True) -> VisualTimeline:
    shots = [
        _micro_shot("micro_wukong", 1, character_ids=("wukong",)),
        _micro_shot("micro_yangjian", 2, character_ids=("yangjian",)),
        _micro_shot("micro_nezha", 3, character_ids=("nezha",)),
    ]
    if with_still:
        shots.append(
            _micro_shot(
                "micro_still",
                4,
                character_ids=(),
                purpose="object",
                camera_mode="object_insert",
                parent_shot_id="shot_002",
            )
        )
    return VisualTimeline(project_id="bakeoff-project", micro_shots=tuple(shots))


def _episode(*, with_still: bool = True) -> Episode:
    shots = [
        Shot(
            "shot_001",
            1,
            "Shop",
            "Wukong, Yang Jian, and Nezha deliver their short lines in the shop.",
            "Shop counter and envelope.",
            "static",
            9.0,
            "tense",
            dialogue=[
                DialogueLine("wukong", "I will go first."),
                DialogueLine("yangjian", "Then I will follow."),
                DialogueLine("nezha", "I am ready."),
            ],
        )
    ]
    if with_still:
        shots.append(
            Shot(
                "shot_002",
                2,
                "Shop",
                "The envelope remains still on the shop counter.",
                "The envelope remains still.",
                "static",
                3.0,
                "tense",
            )
        )
    return Episode(
        project_id="bakeoff-project",
        title="Bakeoff project",
        language="en-US",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character("wukong", "Wukong", "lead", "guarded", "dark coat", "low"),
            Character("yangjian", "Yang Jian", "lead", "calm", "light coat", "calm"),
            Character("nezha", "Nezha", "lead", "focused", "red armor", "bright"),
        ],
        shots=shots,
    )


def _sheet(timeline: VisualTimeline) -> PerformanceSheet:
    cards = []
    for shot in timeline.micro_shots:
        is_visible_speech = shot.id in {
            "micro_wukong",
            "micro_yangjian",
            "micro_nezha",
        }
        speaker_id = shot.character_ids[0] if is_visible_speech else ""
        dialogue_id = (
            f"shot_001.dialogue_{shot.index:02d}" if is_visible_speech else ""
        )
        cards.append(
            PerformanceCard(
                micro_shot_id=shot.id,
                purpose=shot.purpose,
                speaker_id=speaker_id,
                dialogue_id=dialogue_id,
                requires_visible_lipsync=is_visible_speech,
                entry_anchor_id="entry",
                scene_keyframe_id="scene",
                actor_id=speaker_id,
                target_id="",
                contact_point="",
                prop_hand="",
                start_beat="starts still",
                main_beat="speaks one line" if speaker_id else "holds still",
                end_beat="ends still",
                negative_constraints=("no_floating",),
            )
        )
    return PerformanceSheet(project_id="bakeoff-project", cards=tuple(cards))


def _manifest(tmp_path: Path, sheet: PerformanceSheet) -> DialogueAudioManifest:
    assets = []
    for card in sheet.cards:
        if not card.requires_visible_lipsync:
            continue
        path = tmp_path / "dialogue_audio" / f"{card.dialogue_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(card.dialogue_id.encode())
        assets.append(
            DialogueAudioAsset(
                dialogue_id=card.dialogue_id,
                speaker_id=card.speaker_id,
                path=str(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                duration_seconds=1.0,
                voice_id=f"voice-{card.speaker_id}",
            )
        )
    return DialogueAudioManifest(
        assets=tuple(assets),
        path=str(tmp_path / "dialogue_audio" / "dialogue_audio_manifest.json"),
        voiceover_audio=str(tmp_path / "voiceover.wav"),
        voiceover_sha256="0" * 64,
    )


def _build_plan(tmp_path: Path, *, with_still: bool = True) -> dict:
    timeline = _timeline(with_still=with_still)
    sheet = _sheet(timeline)
    return build_model_bakeoff_plan(
        _episode(with_still=with_still),
        timeline,
        ["micro_wukong", "micro_yangjian", "micro_nezha"],
        tmp_path,
        performance_sheet=sheet,
        dialogue_manifest=_manifest(tmp_path, sheet),
        still_micro_shot_id="micro_still" if with_still else None,
        video_models=VIDEO_MODELS,
        still_models=STILL_MODELS,
    )


def _scores(total: float) -> dict[str, float]:
    value = total / 20
    return {key: value for key in SCORE_WEIGHTS}


def _video_path(
    run_dir: Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int = 1,
) -> Path:
    path = (
        run_dir
        / "micro_clips"
        / micro_shot_id
        / model
        / f"candidate_{candidate_number:03d}.mp4"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VALID_VIDEO_MP4)
    return path


def _still_path(
    run_dir: Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int = 1,
) -> Path:
    path = (
        run_dir
        / "micro_stills"
        / micro_shot_id
        / model
        / f"candidate_{candidate_number:03d}.png"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1), "red").save(path, format="PNG")
    return path


def _reviews(
    plan: dict,
    *,
    video_scores: dict[str, float] | None = None,
    video_failures: dict[str, list[str]] | None = None,
    still_scores: dict[str, float] | None = None,
    still_failures: dict[str, list[str]] | None = None,
) -> dict:
    run_dir = Path(plan["run_dir"])
    video_scores = video_scores or {model: 85 for model in VIDEO_MODELS}
    video_failures = video_failures or {}
    still_scores = still_scores or {model: 85 for model in STILL_MODELS}
    still_failures = still_failures or {}
    video_reviews = {}
    for model in plan["video_models"]:
        video_reviews[model] = [
            {
                "micro_shot_id": shot_id,
                "speaker_id": next(
                    trial["speaker_id"]
                    for trial in plan["speaking_trials"]
                    if trial["micro_shot_id"] == shot_id
                ),
                "dialogue_id": next(
                    trial["dialogue_id"]
                    for trial in plan["speaking_trials"]
                    if trial["micro_shot_id"] == shot_id
                ),
                "audio_sha256": next(
                    trial["audio_sha256"]
                    for trial in plan["speaking_trials"]
                    if trial["micro_shot_id"] == shot_id
                ),
                "candidate_path": str(_video_path(run_dir, shot_id, model)),
                "scores": _scores(video_scores[model]),
                "hard_failures": list(video_failures.get(model, [])),
                "notes": "local representative review",
                "passed": _scores(video_scores[model])["lipsync"] >= 3.5,
            }
            for shot_id in plan["representative_character_micro_shot_ids"]
        ]
    still_reviews = {}
    if plan["requires_still"]:
        for model in plan["still_models"]:
            still_reviews[model] = [
                {
                    "micro_shot_id": plan["still_micro_shot_id"],
                    "candidate_path": str(
                        _still_path(
                            run_dir,
                            plan["still_micro_shot_id"],
                            model,
                        )
                    ),
                    "score": still_scores[model],
                    "hard_failures": list(still_failures.get(model, [])),
                    "notes": "local still review",
                }
            ]
    return {
        "schema_version": MODEL_BAKEOFF_REVIEW_SCHEMA,
        "project_id": plan["project_id"],
        "video_reviews": video_reviews,
        "still_reviews": still_reviews,
    }


def test_bakeoff_requires_one_visible_trial_for_each_required_speaker(tmp_path):
    timeline = _timeline()
    sheet = _sheet(timeline)
    with pytest.raises(ModelBakeoffError, match="exactly three visible-speaking trials"):
        build_model_bakeoff_plan(
            _episode(),
            timeline,
            ["micro_wukong", "micro_yangjian"],
            tmp_path,
            performance_sheet=sheet,
            dialogue_manifest=_manifest(tmp_path, sheet),
            still_micro_shot_id="micro_still",
            video_models=VIDEO_MODELS,
            still_models=STILL_MODELS,
        )


def test_model_whitelists_are_reused_and_failure_sets_are_complete():
    from factory import model_bakeoff

    assert model_bakeoff.PRODUCTION_VIDEO_MODELS is PRODUCTION_VIDEO_MODELS
    assert model_bakeoff.PRODUCTION_STILL_MODELS is PRODUCTION_STILL_MODELS
    assert SCORE_WEIGHTS == {
        "identity": 20,
        "expression": 15,
        "anatomy": 15,
        "continuity": 15,
        "semantics": 10,
        "motion": 10,
        "clean_frame": 5,
        "lipsync": 10,
    }
    assert VIDEO_HARD_FAILURES >= {
        "identity_swap",
        "extra_character",
        "duplicate_face",
        "severe_anatomy",
        "embedded_text",
        "in_model_cut",
    }
    assert STILL_HARD_FAILURES == {
        "embedded_text",
        "broken_geometry",
        "composition_mismatch",
        "style_mismatch",
    }


def test_weighted_score_returns_a_two_decimal_0_to_100_score():
    scores = {
        "identity": 5,
        "expression": 4,
        "anatomy": 3,
        "continuity": 2,
        "semantics": 1,
        "motion": 0,
        "clean_frame": 5,
        "lipsync": 4,
    }

    assert weighted_score(scores) == 62.0
    assert weighted_score({key: 4.123 for key in SCORE_WEIGHTS}) == 82.46


@pytest.mark.parametrize(
    "scores",
    [
        {key: 5 for key in SCORE_WEIGHTS if key != "motion"},
        {**{key: 5 for key in SCORE_WEIGHTS}, "extra": 5},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": float("nan")},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": float("inf")},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": True},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": "5"},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": -0.01},
        {**{key: 5 for key in SCORE_WEIGHTS}, "identity": 5.01},
    ],
)
def test_weighted_score_rejects_noncanonical_scores(scores):
    with pytest.raises(ModelBakeoffError, match="score"):
        weighted_score(scores)


def test_build_plan_writes_exact_atomic_artifact(tmp_path):
    plan = _build_plan(tmp_path)

    assert plan["project_id"] == "bakeoff-project"
    assert plan["representative_character_micro_shot_ids"] == [
        "micro_wukong",
        "micro_yangjian",
        "micro_nezha",
    ]
    assert plan["requires_still"] is True
    assert plan["still_micro_shot_id"] == "micro_still"
    assert plan["video_models"] == VIDEO_MODELS
    assert plan["still_models"] == STILL_MODELS
    assert plan["minimum_score"] == 80
    assert plan["max_candidates_per_model"] == 3
    assert json.loads((tmp_path / "model_bakeoff_plan.json").read_text()) == plan
    assert not list(tmp_path.glob(".model_bakeoff_plan.json.*.tmp"))


@pytest.mark.parametrize(
    ("character_ids", "still_id", "video_models", "still_models", "message"),
    [
        (["micro_wukong"], "micro_still", VIDEO_MODELS, STILL_MODELS, "exactly three"),
        (
            ["micro_wukong", "micro_wukong", "micro_wukong"],
            "micro_still",
            VIDEO_MODELS,
            STILL_MODELS,
            "visible-speaking",
        ),
        (
            ["micro_wukong", "micro_yangjian", "unknown"],
            "micro_still",
            VIDEO_MODELS,
            STILL_MODELS,
            "visible-speaking",
        ),
        (
            ["micro_wukong", "micro_yangjian", "micro_still"],
            "micro_still",
            VIDEO_MODELS,
            STILL_MODELS,
            "visible-speaking",
        ),
        (
            ["micro_wukong", "micro_yangjian", "micro_nezha"],
            "micro_wukong",
            VIDEO_MODELS,
            STILL_MODELS,
            "still route",
        ),
        (
            ["micro_wukong", "micro_yangjian", "micro_nezha"],
            "micro_still",
            [VIDEO_MODELS[0], VIDEO_MODELS[0]],
            STILL_MODELS,
            "whitelist",
        ),
        (
            ["micro_wukong", "micro_yangjian", "micro_nezha"],
            "micro_still",
            VIDEO_MODELS + ["unknown"],
            STILL_MODELS,
            "whitelist",
        ),
    ],
)
def test_build_plan_rejects_bad_selection_or_models(
    tmp_path, character_ids, still_id, video_models, still_models, message
):
    timeline = _timeline()
    sheet = _sheet(timeline)
    with pytest.raises(ModelBakeoffError, match=message):
        build_model_bakeoff_plan(
            _episode(),
            timeline,
            character_ids,
            tmp_path,
            performance_sheet=sheet,
            dialogue_manifest=_manifest(tmp_path, sheet),
            still_micro_shot_id=still_id,
            video_models=video_models,
            still_models=still_models,
        )


def test_build_plan_requires_one_still_id_only_when_still_route_exists(tmp_path):
    timeline = _timeline()
    sheet = _sheet(timeline)
    with pytest.raises(ModelBakeoffError, match="still-routed"):
        build_model_bakeoff_plan(
            _episode(),
            timeline,
            ["micro_wukong", "micro_yangjian", "micro_nezha"],
            tmp_path,
            performance_sheet=sheet,
            dialogue_manifest=_manifest(tmp_path, sheet),
            video_models=VIDEO_MODELS,
            still_models=STILL_MODELS,
        )

    plan = _build_plan(tmp_path / "no-still", with_still=False)
    assert plan["requires_still"] is False
    assert plan["still_micro_shot_id"] == ""
    assert plan["still_models"] == STILL_MODELS


@pytest.mark.parametrize(
    "timeline",
    [
        replace(_timeline(), schema_version="invalid-schema"),
        replace(
            _timeline(),
            micro_shots=(
                replace(_timeline().micro_shots[0], index=4),
                *_timeline().micro_shots[1:],
            ),
        ),
        replace(_timeline(), project_id="wrong-project"),
        replace(
            _timeline(),
            micro_shots=(
                replace(_timeline().micro_shots[0], timeline_duration_seconds=2),
                *_timeline().micro_shots[1:],
            ),
        ),
        replace(
            _timeline(),
            micro_shots=(
                replace(_timeline().micro_shots[0], character_ids=("unknown",)),
                *_timeline().micro_shots[1:],
            ),
        ),
    ],
)
def test_build_plan_rejects_invalid_visual_timeline(tmp_path, timeline):
    with pytest.raises(ModelBakeoffError, match="Visual timeline is invalid"):
        build_model_bakeoff_plan(
            _episode(),
            timeline,
            ["micro_wukong", "micro_yangjian", "micro_nezha"],
            tmp_path,
            performance_sheet=_sheet(_timeline()),
            dialogue_manifest=_manifest(tmp_path, _sheet(_timeline())),
            still_micro_shot_id="micro_still",
            video_models=VIDEO_MODELS,
            still_models=STILL_MODELS,
        )


def test_finalize_selects_passing_video_and_best_still_and_writes_artifacts(
    tmp_path,
):
    plan = _build_plan(tmp_path)
    reviews = _reviews(
        plan,
        video_scores={VIDEO_MODELS[0]: 88},
        still_scores={STILL_MODELS[0]: 91},
    )

    report = finalize_bakeoff(plan, reviews)

    assert report["selected_model"] == VIDEO_MODELS[0]
    assert report["selected_still_model"] == STILL_MODELS[0]
    assert report["production_ready"] is True
    assert report["representative_character_micro_shot_ids"] == [
        "micro_wukong",
        "micro_yangjian",
        "micro_nezha",
    ]
    assert report["still_micro_shot_id"] == "micro_still"
    assert report["video_results"][0]["aggregate_score"] == 88.0
    assert [
        item["weighted_score"] for item in report["video_results"][0]["shots"]
    ] == [88.0, 88.0, 88.0]
    assert [
        trial["speaker_id"] for trial in report["video_results"][0]["speaking_trials"]
    ] == ["wukong", "yangjian", "nezha"]
    assert model_route_capability(report, VIDEO_MODELS[0]) == "speaking"
    assert require_selected_production_model(report) == VIDEO_MODELS[0]
    assert require_selected_still_model(report) == STILL_MODELS[0]
    persisted_reviews = json.loads(
        (tmp_path / "model_bakeoff_review.json").read_text()
    )
    video_review = persisted_reviews["video_reviews"][VIDEO_MODELS[0]][0]
    video_result = report["video_results"][0]["shots"][0]
    assert video_review["sha256"] == video_result["sha256"]
    assert video_review["size_bytes"] == video_result["size_bytes"]
    assert len(video_review["sha256"]) == 64
    assert video_review["size_bytes"] > 0
    still_review = persisted_reviews["still_reviews"][STILL_MODELS[0]][0]
    still_result = report["still_results"][0]
    assert still_review["sha256"] == still_result["sha256"]
    assert still_review["size_bytes"] == still_result["size_bytes"]
    assert json.loads((tmp_path / "model_bakeoff_report.json").read_text()) == report
    assert not list(tmp_path.glob(".model_bakeoff_*.json.*.tmp"))


def test_failed_nezha_lipsync_makes_model_action_only(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    nezha_review = reviews["video_reviews"][VIDEO_MODELS[0]][2]
    nezha_review["scores"]["lipsync"] = 1.0
    nezha_review["passed"] = False

    report = finalize_bakeoff(plan, reviews)

    assert report["selected_model"] == VIDEO_MODELS[0]
    assert model_route_capability(report, VIDEO_MODELS[0]) == "action_only"
    with pytest.raises(ModelBakeoffError, match="not speaking-capable"):
        require_speaking_capability(report, VIDEO_MODELS[0], "micro_nezha")


def test_failed_model_blocks_speaking_even_when_all_lipsync_trials_pass(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(
        plan,
        video_failures={VIDEO_MODELS[0]: ["duplicate_face"]},
    )

    report = finalize_bakeoff(plan, reviews)

    assert all(
        trial["passed"] for trial in report["video_results"][0]["speaking_trials"]
    )
    assert report["video_results"][0]["passed"] is False
    assert model_route_capability(report, VIDEO_MODELS[0]) == "blocked"
    with pytest.raises(ModelBakeoffError, match="not speaking-capable"):
        require_speaking_capability(report, VIDEO_MODELS[0], "micro_wukong")


def test_empty_speaking_trials_explicitly_routes_a_passing_model_action_only(tmp_path):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    report["video_results"][0]["speaking_trials"] = []
    (tmp_path / "model_bakeoff_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    assert model_route_capability(report, VIDEO_MODELS[0]) == "action_only"
    with pytest.raises(ModelBakeoffError, match="not speaking-capable"):
        require_speaking_capability(report, VIDEO_MODELS[0], "micro_wukong")
    with pytest.raises(ModelBakeoffError, match="report"):
        model_route_capability({"video_results": []}, VIDEO_MODELS[0])


def test_finalize_rejects_plan_that_differs_from_plan_artifact(tmp_path):
    plan = _build_plan(tmp_path)
    forged = deepcopy(plan)
    forged["representative_character_micro_shot_ids"].reverse()

    with pytest.raises(ModelBakeoffError, match="speaking trials|plan artifact"):
        finalize_bakeoff(forged, _reviews(forged))


def test_finalize_rejects_high_score_video_with_hard_failure(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(
        plan,
        video_scores={VIDEO_MODELS[0]: 95},
        video_failures={VIDEO_MODELS[0]: ["duplicate_face"]},
    )

    report = finalize_bakeoff(plan, reviews)

    assert report["selected_model"] == ""
    assert report["production_ready"] is False
    assert report["video_results"][0]["aggregate_score"] == 95.0
    assert report["video_results"][0]["passed"] is False


def test_score_exactly_80_passes_and_ties_follow_plan_order(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(
        plan,
        video_scores={model: 80 for model in VIDEO_MODELS},
        still_scores={model: 80 for model in STILL_MODELS},
    )

    report = finalize_bakeoff(plan, reviews)

    assert report["production_ready"] is True
    assert report["selected_model"] == VIDEO_MODELS[0]
    assert report["selected_still_model"] == STILL_MODELS[0]
    assert all(result["passed"] for result in report["video_results"])
    assert all(result["passed"] for result in report["still_results"])


def test_finalize_rejects_missing_or_duplicate_video_review(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    reviews["video_reviews"][VIDEO_MODELS[0]].pop()
    with pytest.raises(ModelBakeoffError, match="exactly one review"):
        finalize_bakeoff(plan, reviews)

    reviews = _reviews(plan)
    reviews["video_reviews"][VIDEO_MODELS[0]][1] = deepcopy(
        reviews["video_reviews"][VIDEO_MODELS[0]][0]
    )
    with pytest.raises(ModelBakeoffError, match="exactly one review"):
        finalize_bakeoff(plan, reviews)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda plan, reviews: reviews["video_reviews"].update(
                {"unknown": reviews["video_reviews"][VIDEO_MODELS[0]]}
            ),
            "video models",
        ),
        (
            lambda plan, reviews: reviews["video_reviews"][VIDEO_MODELS[0]][0].update(
                {"micro_shot_id": "unknown"}
            ),
            "micro-shot",
        ),
        (
            lambda plan, reviews: reviews["video_reviews"][VIDEO_MODELS[0]][0][
                "hard_failures"
            ].append("unknown"),
            "hard failure",
        ),
        (
            lambda plan, reviews: reviews["still_reviews"][STILL_MODELS[0]][0].update(
                {"score": True}
            ),
            "score",
        ),
        (
            lambda plan, reviews: reviews["video_reviews"][VIDEO_MODELS[0]][0].update(
                {"notes": "https://remote.example/review"}
            ),
            "local",
        ),
        (
            lambda plan, reviews: reviews["video_reviews"][VIDEO_MODELS[0]][0].update(
                {"notes": "s3://private-bucket/review"}
            ),
            "local",
        ),
        (
            lambda plan, reviews: reviews["video_reviews"][VIDEO_MODELS[0]][0].update(
                {"notes": "password='review-secret'"}
            ),
            "local",
        ),
    ],
)
def test_finalize_rejects_noncanonical_reviews(tmp_path, mutation, message):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    mutation(plan, reviews)

    with pytest.raises(ModelBakeoffError, match=message):
        finalize_bakeoff(plan, reviews)


@pytest.mark.parametrize("kind", ["wrong_model", "candidate_four", "traversal"])
def test_finalize_rejects_candidate_path_model_number_or_traversal(tmp_path, kind):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    item = reviews["video_reviews"][VIDEO_MODELS[0]][0]
    if kind == "wrong_model":
        item["candidate_path"] = str(
            _video_path(
                tmp_path,
                "micro_001",
                "doubao-seedance-1-5-pro",
            )
        )
    elif kind == "candidate_four":
        item["candidate_path"] = str(
            _video_path(tmp_path, "micro_001", VIDEO_MODELS[0], 4)
        )
    else:
        item["candidate_path"] = str(
            tmp_path
            / "micro_clips"
            / "micro_001"
            / ".."
            / "micro_001"
            / VIDEO_MODELS[0]
            / "candidate_001.mp4"
        )

    with pytest.raises(ModelBakeoffError, match="(?i)candidate path"):
        finalize_bakeoff(plan, reviews)


def test_finalize_rejects_symlink_candidate(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    item = reviews["video_reviews"][VIDEO_MODELS[0]][0]
    target = Path(item["candidate_path"])
    real = target.with_name("real.mp4")
    target.rename(real)
    target.symlink_to(real)

    with pytest.raises(ModelBakeoffError, match="symlink"):
        finalize_bakeoff(plan, reviews)


def test_finalize_rejects_missing_candidate(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    Path(reviews["video_reviews"][VIDEO_MODELS[0]][0]["candidate_path"]).unlink()

    with pytest.raises(ModelBakeoffError, match="existing regular file"):
        finalize_bakeoff(plan, reviews)


def test_finalize_rejects_truncated_still_with_valid_png_signature(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    candidate = Path(
        reviews["still_reviews"][STILL_MODELS[0]][0]["candidate_path"]
    )
    candidate.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")

    with pytest.raises(ModelBakeoffError, match="valid PNG, JPEG, or WebP"):
        finalize_bakeoff(plan, reviews)


def test_finalize_rejects_truncated_jpeg_that_verify_does_not_catch(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    item = reviews["still_reviews"][STILL_MODELS[0]][0]
    candidate = Path(item["candidate_path"]).with_suffix(".jpg")
    encoded = BytesIO()
    Image.new("RGB", (32, 32), "red").save(encoded, format="JPEG")
    truncated = encoded.getvalue()[:-1]
    with Image.open(BytesIO(truncated)) as image:
        image.verify()
    with pytest.raises(OSError, match="truncated"):
        with Image.open(BytesIO(truncated)) as image:
            image.load()
    candidate.write_bytes(truncated)
    item["candidate_path"] = str(candidate)

    with pytest.raises(ModelBakeoffError, match="valid PNG, JPEG, or WebP"):
        finalize_bakeoff(plan, reviews)


def test_finalize_rejects_truncated_webp(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    item = reviews["still_reviews"][STILL_MODELS[0]][0]
    candidate = Path(item["candidate_path"]).with_suffix(".webp")
    encoded = BytesIO()
    Image.new("RGB", (32, 32), "red").save(encoded, format="WEBP")
    candidate.write_bytes(encoded.getvalue()[:-1])
    item["candidate_path"] = str(candidate)

    with pytest.raises(ModelBakeoffError, match="valid PNG, JPEG, or WebP"):
        finalize_bakeoff(plan, reviews)


def test_review_notes_allow_a_normal_label_with_space_after_colon(tmp_path):
    plan = _build_plan(tmp_path)
    reviews = _reviews(plan)
    reviews["video_reviews"][VIDEO_MODELS[0]][0]["notes"] = "Scene: value"

    report = finalize_bakeoff(plan, reviews)

    assert report["production_ready"] is True


def test_still_gate_rejects_hard_failure_and_no_still_route_returns_empty(tmp_path):
    plan = _build_plan(tmp_path / "still")
    reviews = _reviews(
        plan,
        still_scores={STILL_MODELS[0]: 99},
        still_failures={STILL_MODELS[0]: ["embedded_text"]},
    )
    report = finalize_bakeoff(plan, reviews)
    assert report["selected_still_model"] == ""
    assert report["production_ready"] is False
    with pytest.raises(ModelBakeoffError, match="still model"):
        require_selected_still_model(report)

    no_still_plan = _build_plan(tmp_path / "no-still", with_still=False)
    no_still_report = finalize_bakeoff(no_still_plan, _reviews(no_still_plan))
    assert no_still_report["production_ready"] is True
    assert no_still_report["selected_still_model"] == ""
    assert require_selected_still_model(no_still_report) == ""


def test_approved_selection_uses_authoritative_still_for_character_free_slot(tmp_path):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    timeline = VisualTimeline(
        project_id="bakeoff-project",
        micro_shots=(
            _micro_shot(
                "micro_still",
                1,
                character_ids=(),
                purpose="object",
                camera_mode="object_insert",
                parent_shot_id="shot_002",
            ),
        ),
    )

    selection = approved_selection_from_manifest(
        CandidateReviewManifest(project_id="bakeoff-project", candidates=()),
        timeline,
        bakeoff_report=report,
    )

    assert selection["selected_candidates"]["micro_still"]["kind"] == "still"
    assert selection["selected_candidates"]["micro_still"]["candidate_path"] == (
        report["still_results"][0]["candidate_path"]
    )


def test_gate_recomputes_results_and_rejects_forged_report(tmp_path):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    forged = deepcopy(report)
    forged["video_results"][0]["shots"][0]["scores"] = _scores(10)
    forged["video_results"][0]["shots"][0]["weighted_score"] = 99
    forged["video_results"][0]["aggregate_score"] = 99
    forged["video_results"][0]["passed"] = True
    forged["selected_model"] = VIDEO_MODELS[0]
    forged["production_ready"] = True

    with pytest.raises(ModelBakeoffError, match="report"):
        require_selected_production_model(forged)


@pytest.mark.parametrize("kind", ["video", "still"])
def test_gate_rejects_valid_candidate_replaced_after_finalize(tmp_path, kind):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    if kind == "video":
        candidate = Path(report["video_results"][0]["shots"][0]["candidate_path"])
        candidate.write_bytes(VALID_VIDEO_MP4 + b"\x00\x00\x00\x08free")
        gate = require_selected_production_model
    else:
        candidate = Path(report["still_results"][0]["candidate_path"])
        Image.new("RGB", (1, 1), "blue").save(candidate, format="PNG")
        gate = require_selected_still_model

    with pytest.raises(ModelBakeoffError, match="sha256|size|changed"):
        gate(report)


def test_gate_rejects_review_artifact_digest_that_disagrees_with_report(tmp_path):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    review_path = tmp_path / "model_bakeoff_review.json"
    review_artifact = json.loads(review_path.read_text())
    review_artifact["video_reviews"][VIDEO_MODELS[0]][0]["sha256"] = "0" * 64
    review_path.write_text(json.dumps(review_artifact), encoding="utf-8")

    with pytest.raises(ModelBakeoffError, match="review artifact"):
        require_selected_production_model(report)


def test_gate_rejects_report_rebound_to_different_representative_shots(tmp_path):
    plan = _build_plan(tmp_path)
    report = finalize_bakeoff(plan, _reviews(plan))
    forged = deepcopy(report)
    forged["representative_character_micro_shot_ids"] = [
        "micro_002",
        "micro_001",
    ]

    with pytest.raises(ModelBakeoffError, match="plan artifact|representative"):
        require_selected_production_model(forged)


def test_config_and_env_keep_draft_separate_from_production_models():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config/factory.config.json").read_text())
    quality = config["qualityUpgrade"]

    assert quality == {
        "draftVideoModel": "doubao-seedance-2-0-fast",
        "productionVideoModels": VIDEO_MODELS,
        "productionStillModels": STILL_MODELS,
        "experimentalVideoModels": ["doubao-seedance-1-5-pro"],
        "experimentalStillModels": ["gpt-image-2"],
        "productionResolution": "1080p",
        "stillModelSizes": {
            "doubao-seedream-4-5": "1440x2560",
            "gpt-image-2": "1024x1536",
        },
        "maxPaidCandidatesPerModel": 3,
        "minimumBakeoffScore": 80,
    }
    env_example = (root / ".env.example").read_text()
    assert "GATEWAY_VIDEO_MODEL=doubao-seedance-2-0-fast" in env_example
    for model in VIDEO_MODELS + STILL_MODELS:
        assert model in env_example
