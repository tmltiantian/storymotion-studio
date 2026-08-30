from __future__ import annotations

import json
import hashlib
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from .gateway_video import is_valid_mp4_file
from .dialogue_assets import (
    DialogueAudioError,
    DialogueAudioManifest,
    require_dialogue_audio,
)
from .micro_still_batch import PRODUCTION_STILL_MODELS, _still_eligibility
from .micro_video_batch import PRODUCTION_VIDEO_MODELS
from .performance_card import PerformanceSheet, validate_performance_sheet
from .schema import Episode
from .visual_timeline import VisualTimeline, validate_visual_timeline


MODEL_BAKEOFF_PLAN_SCHEMA = "motion-comic-factory.model-bakeoff-plan.v1"
MODEL_BAKEOFF_REVIEW_SCHEMA = "motion-comic-factory.model-bakeoff-review.v1"
MODEL_BAKEOFF_REPORT_SCHEMA = "motion-comic-factory.model-bakeoff-report.v1"

SCORE_WEIGHTS = {
    "identity": 20,
    "expression": 15,
    "anatomy": 15,
    "continuity": 15,
    "semantics": 10,
    "motion": 10,
    "clean_frame": 5,
    "lipsync": 10,
}
LIPSYNC_SPEAKERS = frozenset({"wukong", "yangjian", "nezha"})
LIPSYNC_MINIMUM_SCORE = 3.5
VIDEO_HARD_FAILURES = frozenset(
    {
        "identity_swap",
        "extra_character",
        "duplicate_face",
        "severe_anatomy",
        "embedded_text",
        "in_model_cut",
    }
)
STILL_HARD_FAILURES = frozenset(
    {
        "embedded_text",
        "broken_geometry",
        "composition_mismatch",
        "style_mismatch",
    }
)

_DEFAULT_VIDEO_MODELS = tuple(sorted(PRODUCTION_VIDEO_MODELS, reverse=True))
_DEFAULT_STILL_MODELS = tuple(sorted(PRODUCTION_STILL_MODELS))
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "project_id",
        "run_dir",
        "representative_character_micro_shot_ids",
        "speaking_trials",
        "requires_still",
        "still_micro_shot_id",
        "video_models",
        "still_models",
        "minimum_score",
        "max_candidates_per_model",
    }
)
_REVIEW_KEYS = frozenset(
    {"schema_version", "project_id", "video_reviews", "still_reviews"}
)
_VIDEO_REVIEW_KEYS = frozenset(
    {
        "micro_shot_id",
        "speaker_id",
        "dialogue_id",
        "audio_sha256",
        "candidate_path",
        "scores",
        "hard_failures",
        "notes",
        "passed",
    }
)
_STILL_REVIEW_KEYS = frozenset(
    {"micro_shot_id", "candidate_path", "score", "hard_failures", "notes"}
)
_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "project_id",
        "run_dir",
        "representative_character_micro_shot_ids",
        "speaking_trials",
        "requires_still",
        "still_micro_shot_id",
        "minimum_score",
        "max_candidates_per_model",
        "video_models",
        "still_models",
        "selected_model",
        "selected_still_model",
        "production_ready",
        "video_results",
        "still_results",
    }
)
_VIDEO_RESULT_KEYS = frozenset(
    {
        "model",
        "shots",
        "speaking_trials",
        "aggregate_score",
        "hard_failures",
        "passed",
    }
)
_VIDEO_SHOT_RESULT_KEYS = frozenset(
    {
        "micro_shot_id",
        "speaker_id",
        "dialogue_id",
        "audio_sha256",
        "candidate_path",
        "size_bytes",
        "sha256",
        "scores",
        "weighted_score",
        "hard_failures",
        "notes",
        "passed",
    }
)
_SPEAKING_TRIAL_KEYS = frozenset(
    {"micro_shot_id", "speaker_id", "dialogue_id", "audio_sha256"}
)
_RESULT_SPEAKING_TRIAL_KEYS = _SPEAKING_TRIAL_KEYS | frozenset({"lipsync", "passed"})
_STILL_RESULT_KEYS = frozenset(
    {
        "model",
        "micro_shot_id",
        "candidate_path",
        "size_bytes",
        "sha256",
        "score",
        "hard_failures",
        "notes",
        "passed",
    }
)
_VIDEO_CANDIDATE = re.compile(r"candidate_(\d{3})\.mp4")
_STILL_CANDIDATE = re.compile(
    r"candidate_(\d{3})\.(?:png|jpe?g|webp)", re.IGNORECASE
)
_URI_SCHEME = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*:(?=\S)"
)
_SECRET_VALUE = re.compile(
    r"""
    ["']?
    (?:
        authorization
        | api[-_ ]?key
        | password
        | passwd
        | access[-_ ]?key
        | token
        | secret
        | credential
    )
    ["']?
    \s*[:=]\s*
    ["']?(?:bearer\s+)?\S
    |
    \bbearer\s+\S
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ModelBakeoffError(ValueError):
    pass


def weighted_score(scores: Mapping[str, float]) -> float:
    """Return the exact seven-field weighted score on a 0-100 scale."""
    if not isinstance(scores, Mapping):
        raise ModelBakeoffError("Video scores must be an object.")
    _require_exact_keys(scores, frozenset(SCORE_WEIGHTS), "Video scores")
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        value = _finite_number(scores[key], f"Video score {key}", minimum=0, maximum=5)
        total += value / 5 * weight
    return round(total, 2)


def build_model_bakeoff_plan(
    episode: Episode,
    timeline: VisualTimeline,
    representative_character_micro_shot_ids: Sequence[str],
    run_dir: str | Path,
    *,
    performance_sheet: PerformanceSheet,
    dialogue_manifest: DialogueAudioManifest,
    still_micro_shot_id: str | None = None,
    video_models: Sequence[str] = _DEFAULT_VIDEO_MODELS,
    still_models: Sequence[str] = _DEFAULT_STILL_MODELS,
) -> dict[str, Any]:
    """Validate representative routes and atomically write the paid bakeoff plan."""
    if not isinstance(episode, Episode):
        raise ModelBakeoffError("Bakeoff planning requires an Episode.")
    timeline_errors = validate_visual_timeline(timeline, episode)
    if timeline_errors:
        raise ModelBakeoffError(
            "Visual timeline is invalid: " + "; ".join(timeline_errors)
        )
    project_id = _exact_text(episode.project_id, "Plan project_id")
    shot_by_id: dict[str, Any] = {}
    for shot in timeline.micro_shots:
        shot_id = _exact_text(getattr(shot, "id", None), "Micro-shot ID")
        if shot_id in shot_by_id:
            raise ModelBakeoffError(f"Duplicate micro-shot ID: {shot_id}.")
        shot_by_id[shot_id] = shot

    representative_ids = _exact_string_list(
        representative_character_micro_shot_ids,
        "Representative character micro-shot IDs",
    )
    speaking_trials = _speaking_trials_for_plan(
        episode,
        timeline,
        performance_sheet,
        dialogue_manifest,
        representative_ids,
    )

    still_routed = {
        shot_id for shot_id, shot in shot_by_id.items() if _still_eligibility(shot)[0]
    }
    requires_still = bool(still_routed)
    normalized_still_id = "" if still_micro_shot_id is None else _exact_text(
        still_micro_shot_id, "Still micro-shot ID"
    )
    if requires_still:
        if not normalized_still_id:
            raise ModelBakeoffError(
                "A still-routed timeline requires exactly one representative still micro-shot ID."
            )
        if normalized_still_id not in shot_by_id:
            raise ModelBakeoffError(
                f"Unknown representative still micro-shot ID: {normalized_still_id}."
            )
        if normalized_still_id not in still_routed:
            raise ModelBakeoffError(
                f"Micro-shot {normalized_still_id} is not eligible for the still route."
            )
    elif normalized_still_id:
        raise ModelBakeoffError(
            "A timeline without a still-routed micro-shot must not select a still ID."
        )

    normalized_video_models = _model_list(
        video_models, PRODUCTION_VIDEO_MODELS, "Video model whitelist"
    )
    normalized_still_models = _model_list(
        still_models, PRODUCTION_STILL_MODELS, "Still model whitelist"
    )
    root = _run_root(run_dir, create=True)
    plan = {
        "schema_version": MODEL_BAKEOFF_PLAN_SCHEMA,
        "project_id": project_id,
        "run_dir": str(root),
        "representative_character_micro_shot_ids": representative_ids,
        "speaking_trials": speaking_trials,
        "requires_still": requires_still,
        "still_micro_shot_id": normalized_still_id,
        "video_models": normalized_video_models,
        "still_models": normalized_still_models,
        "minimum_score": 80,
        "max_candidates_per_model": 3,
    }
    _write_atomic_json(root / "model_bakeoff_plan.json", plan)
    return plan


def finalize_bakeoff(
    plan: Mapping[str, Any], reviews: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate local candidate reviews, select winners, and write audit artifacts."""
    normalized_plan = _validate_plan(plan)
    persisted_plan = _validate_plan(
        _read_json_artifact(
            Path(normalized_plan["run_dir"]) / "model_bakeoff_plan.json",
            "plan artifact",
        )
    )
    if persisted_plan != normalized_plan:
        raise ModelBakeoffError(
            "Bakeoff plan artifact does not match the supplied plan."
        )
    normalized_reviews = _validate_reviews(normalized_plan, reviews)
    minimum = normalized_plan["minimum_score"]

    video_results: list[dict[str, Any]] = []
    for model in normalized_plan["video_models"]:
        shots: list[dict[str, Any]] = []
        model_failures: set[str] = set()
        for review in normalized_reviews["video_reviews"][model]:
            score = weighted_score(review["scores"])
            model_failures.update(review["hard_failures"])
            shots.append(
                {
                    "micro_shot_id": review["micro_shot_id"],
                    "speaker_id": review["speaker_id"],
                    "dialogue_id": review["dialogue_id"],
                    "audio_sha256": review["audio_sha256"],
                    "candidate_path": review["candidate_path"],
                    "size_bytes": review["size_bytes"],
                    "sha256": review["sha256"],
                    "scores": dict(review["scores"]),
                    "weighted_score": score,
                    "hard_failures": list(review["hard_failures"]),
                    "notes": review["notes"],
                    "passed": review["passed"],
                }
            )
        aggregate = round(
            sum(item["weighted_score"] for item in shots) / len(shots), 2
        )
        failures = sorted(model_failures)
        video_results.append(
            {
                "model": model,
                "shots": shots,
                "speaking_trials": [
                    {
                        "micro_shot_id": shot["micro_shot_id"],
                        "speaker_id": shot["speaker_id"],
                        "dialogue_id": shot["dialogue_id"],
                        "audio_sha256": shot["audio_sha256"],
                        "lipsync": shot["scores"]["lipsync"],
                        "passed": shot["passed"],
                    }
                    for shot in shots
                ],
                "aggregate_score": aggregate,
                "hard_failures": failures,
                "passed": aggregate >= minimum and not failures,
            }
        )

    still_results: list[dict[str, Any]] = []
    if normalized_plan["requires_still"]:
        for model in normalized_plan["still_models"]:
            review = normalized_reviews["still_reviews"][model][0]
            failures = list(review["hard_failures"])
            still_results.append(
                {
                    "model": model,
                    "micro_shot_id": review["micro_shot_id"],
                    "candidate_path": review["candidate_path"],
                    "size_bytes": review["size_bytes"],
                    "sha256": review["sha256"],
                    "score": review["score"],
                    "hard_failures": failures,
                    "notes": review["notes"],
                    "passed": review["score"] >= minimum and not failures,
                }
            )

    selected_model = _highest_passing(video_results, "aggregate_score")
    selected_still_model = (
        _highest_passing(still_results, "score")
        if normalized_plan["requires_still"]
        else ""
    )
    production_ready = bool(selected_model) and (
        not normalized_plan["requires_still"] or bool(selected_still_model)
    )
    report = {
        "schema_version": MODEL_BAKEOFF_REPORT_SCHEMA,
        "project_id": normalized_plan["project_id"],
        "run_dir": normalized_plan["run_dir"],
        "representative_character_micro_shot_ids": list(
            normalized_plan["representative_character_micro_shot_ids"]
        ),
        "speaking_trials": list(normalized_plan["speaking_trials"]),
        "requires_still": normalized_plan["requires_still"],
        "still_micro_shot_id": normalized_plan["still_micro_shot_id"],
        "minimum_score": minimum,
        "max_candidates_per_model": normalized_plan["max_candidates_per_model"],
        "video_models": list(normalized_plan["video_models"]),
        "still_models": list(normalized_plan["still_models"]),
        "selected_model": selected_model,
        "selected_still_model": selected_still_model,
        "production_ready": production_ready,
        "video_results": video_results,
        "still_results": still_results,
    }
    _validate_report(report, require_artifacts=False)
    root = Path(normalized_plan["run_dir"])
    _write_atomic_json(root / "model_bakeoff_review.json", normalized_reviews)
    _write_atomic_json(root / "model_bakeoff_report.json", report)
    return report


def require_selected_production_model(report: Mapping[str, Any]) -> str:
    normalized = _validate_report(report)
    model = normalized["selected_model"]
    if normalized["production_ready"] is not True or model not in PRODUCTION_VIDEO_MODELS:
        raise ModelBakeoffError(
            "No production video model has passed the bakeoff gate."
        )
    return model


def model_route_capability(
    report: Mapping[str, Any], model: str
) -> Literal["speaking", "action_only", "blocked"]:
    """Return the verified route permission for one reviewed video model."""
    result = _video_result_for(_validate_report(report), model)
    if result["passed"] is not True:
        return "blocked"
    trials = result.get("speaking_trials")
    if not isinstance(trials, list):
        return "action_only" if result.get("passed") is True else "blocked"
    speakers = {
        trial.get("speaker_id")
        for trial in trials
        if isinstance(trial, Mapping)
    }
    if (
        len(trials) == len(LIPSYNC_SPEAKERS)
        and speakers == LIPSYNC_SPEAKERS
        and all(isinstance(trial, Mapping) and trial.get("passed") is True for trial in trials)
    ):
        return "speaking"
    return "action_only" if result.get("passed") is True else "blocked"


def require_speaking_capability(
    report: Mapping[str, Any], model: str, micro_shot_id: str
) -> None:
    if model_route_capability(report, model) != "speaking":
        raise ModelBakeoffError(f"{model} is not speaking-capable for {micro_shot_id}")


def _video_result_for(report: Mapping[str, Any], model: str) -> Mapping[str, Any]:
    if not isinstance(report, Mapping) or not isinstance(report.get("video_results"), list):
        raise ModelBakeoffError("Bakeoff report must contain video results.")
    matches = [
        result
        for result in report["video_results"]
        if isinstance(result, Mapping) and result.get("model") == model
    ]
    if len(matches) != 1:
        raise ModelBakeoffError(f"Bakeoff report has no video result for {model}.")
    return matches[0]


def _speaking_trials_for_plan(
    episode: Episode,
    timeline: VisualTimeline,
    performance_sheet: PerformanceSheet,
    dialogue_manifest: DialogueAudioManifest,
    representative_ids: list[str],
) -> list[dict[str, str]]:
    if not isinstance(performance_sheet, PerformanceSheet):
        raise ModelBakeoffError("Bakeoff planning requires a PerformanceSheet.")
    sheet_errors = validate_performance_sheet(performance_sheet, episode, timeline)
    if sheet_errors:
        raise ModelBakeoffError("Performance sheet is invalid: " + "; ".join(sheet_errors))
    if not isinstance(dialogue_manifest, DialogueAudioManifest):
        raise ModelBakeoffError("Bakeoff planning requires a DialogueAudioManifest.")
    visible_cards = [card for card in performance_sheet.cards if card.requires_visible_lipsync]
    if (
        len(representative_ids) != len(LIPSYNC_SPEAKERS)
        or len(set(representative_ids)) != len(representative_ids)
        or len(visible_cards) != len(LIPSYNC_SPEAKERS)
        or set(representative_ids) != {card.micro_shot_id for card in visible_cards}
        or {card.speaker_id for card in visible_cards} != LIPSYNC_SPEAKERS
    ):
        raise ModelBakeoffError(
            "Bakeoff plan requires exactly three visible-speaking trials, one each for Wukong, Yang Jian, and Nezha."
        )
    cards_by_shot = {card.micro_shot_id: card for card in visible_cards}
    trials: list[dict[str, str]] = []
    for shot_id in representative_ids:
        card = cards_by_shot[shot_id]
        try:
            asset = require_dialogue_audio(dialogue_manifest, card)
        except DialogueAudioError as exc:
            raise ModelBakeoffError(str(exc)) from exc
        trials.append(
            {
                "micro_shot_id": shot_id,
                "speaker_id": card.speaker_id,
                "dialogue_id": card.dialogue_id,
                "audio_sha256": asset.sha256,
            }
        )
    return trials


def _validate_speaking_trials(
    value: Any, representative_ids: list[str], label: str
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(LIPSYNC_SPEAKERS):
        raise ModelBakeoffError(f"{label} must bind exactly three visible-speaking trials.")
    if len(representative_ids) != len(LIPSYNC_SPEAKERS) or len(set(representative_ids)) != len(representative_ids):
        raise ModelBakeoffError(f"{label} must bind exactly three different representative character shots.")
    normalized: list[dict[str, str]] = []
    for expected_shot_id, trial in zip(representative_ids, value, strict=True):
        if not isinstance(trial, Mapping):
            raise ModelBakeoffError(f"{label} speaking trial must be an object.")
        _require_exact_keys(trial, _SPEAKING_TRIAL_KEYS, f"{label} speaking trial")
        shot_id = _exact_text(trial["micro_shot_id"], f"{label} trial micro-shot ID")
        if shot_id != expected_shot_id:
            raise ModelBakeoffError(f"{label} speaking trials must match representative shot order.")
        normalized.append(
            {
                "micro_shot_id": shot_id,
                "speaker_id": _exact_text(trial["speaker_id"], f"{label} trial speaker ID"),
                "dialogue_id": _exact_text(trial["dialogue_id"], f"{label} trial dialogue ID"),
                "audio_sha256": _audio_sha256(trial["audio_sha256"], f"{label} trial audio_sha256"),
            }
        )
    if {trial["speaker_id"] for trial in normalized} != LIPSYNC_SPEAKERS:
        raise ModelBakeoffError(f"{label} must include one lipsync trial for each required speaker.")
    return normalized


def _trial_for_shot(
    trials: Sequence[Mapping[str, Any]], micro_shot_id: str
) -> Mapping[str, Any]:
    matches = [trial for trial in trials if trial.get("micro_shot_id") == micro_shot_id]
    if len(matches) != 1:
        raise ModelBakeoffError("Speaking trial does not match a representative micro-shot.")
    return matches[0]


def _validate_result_speaking_trials(
    value: Any, shots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ModelBakeoffError("Report speaking trials must exactly match video shots.")
    if value == []:
        return []
    if len(value) != len(shots):
        raise ModelBakeoffError("Report speaking trials must exactly match video shots.")
    normalized: list[dict[str, Any]] = []
    for trial, shot in zip(value, shots, strict=True):
        if not isinstance(trial, Mapping):
            raise ModelBakeoffError("Report speaking trial must be an object.")
        _require_exact_keys(trial, _RESULT_SPEAKING_TRIAL_KEYS, "Report speaking trial")
        expected = {
            "micro_shot_id": shot["micro_shot_id"],
            "speaker_id": shot["speaker_id"],
            "dialogue_id": shot["dialogue_id"],
            "audio_sha256": shot["audio_sha256"],
            "lipsync": shot["scores"]["lipsync"],
            "passed": shot["passed"],
        }
        if dict(trial) != expected:
            raise ModelBakeoffError("Report speaking trial does not match video shot evidence.")
        normalized.append(expected)
    return normalized


def require_selected_still_model(report: Mapping[str, Any]) -> str:
    normalized = _validate_report(report)
    if not normalized["requires_still"]:
        return ""
    model = normalized["selected_still_model"]
    if normalized["production_ready"] is not True or model not in PRODUCTION_STILL_MODELS:
        raise ModelBakeoffError(
            "No production still model has passed the bakeoff gate."
        )
    return model


def _validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ModelBakeoffError("Bakeoff plan must be an object.")
    _require_exact_keys(plan, _PLAN_KEYS, "Bakeoff plan")
    if plan["schema_version"] != MODEL_BAKEOFF_PLAN_SCHEMA:
        raise ModelBakeoffError("Bakeoff plan has an unsupported schema_version.")
    project_id = _exact_text(plan["project_id"], "Plan project_id")
    root = _run_root(plan["run_dir"])
    representative_ids = _exact_string_list(
        plan["representative_character_micro_shot_ids"],
        "Representative character micro-shot IDs",
    )
    speaking_trials = _validate_speaking_trials(
        plan["speaking_trials"], representative_ids, "Bakeoff plan"
    )
    if not isinstance(plan["requires_still"], bool):
        raise ModelBakeoffError("Bakeoff plan requires_still must be a boolean.")
    still_id = plan["still_micro_shot_id"]
    if plan["requires_still"]:
        still_id = _exact_text(still_id, "Still micro-shot ID")
    elif still_id != "":
        raise ModelBakeoffError(
            "Bakeoff plan without a still route must use an empty still micro-shot ID."
        )
    if still_id in representative_ids:
        raise ModelBakeoffError("Video and still representative IDs must be different.")
    video_models = _model_list(
        plan["video_models"], PRODUCTION_VIDEO_MODELS, "Video model whitelist"
    )
    still_models = _model_list(
        plan["still_models"], PRODUCTION_STILL_MODELS, "Still model whitelist"
    )
    if (
        isinstance(plan["minimum_score"], bool)
        or plan["minimum_score"] != 80
    ):
        raise ModelBakeoffError("Bakeoff plan minimum_score must be exactly 80.")
    if (
        isinstance(plan["max_candidates_per_model"], bool)
        or plan["max_candidates_per_model"] != 3
    ):
        raise ModelBakeoffError(
            "Bakeoff plan max_candidates_per_model must be exactly 3."
        )
    return {
        "schema_version": MODEL_BAKEOFF_PLAN_SCHEMA,
        "project_id": project_id,
        "run_dir": str(root),
        "representative_character_micro_shot_ids": representative_ids,
        "speaking_trials": speaking_trials,
        "requires_still": plan["requires_still"],
        "still_micro_shot_id": still_id,
        "video_models": video_models,
        "still_models": still_models,
        "minimum_score": 80,
        "max_candidates_per_model": 3,
    }


def _validate_reviews(
    plan: Mapping[str, Any], reviews: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(reviews, Mapping):
        raise ModelBakeoffError("Bakeoff reviews must be an object.")
    _require_exact_keys(reviews, _REVIEW_KEYS, "Bakeoff reviews")
    if reviews["schema_version"] != MODEL_BAKEOFF_REVIEW_SCHEMA:
        raise ModelBakeoffError("Bakeoff reviews have an unsupported schema_version.")
    if reviews["project_id"] != plan["project_id"]:
        raise ModelBakeoffError("Bakeoff review project_id does not match the plan.")
    if not isinstance(reviews["video_reviews"], Mapping):
        raise ModelBakeoffError("Bakeoff video reviews must be an object.")
    if set(reviews["video_reviews"]) != set(plan["video_models"]):
        raise ModelBakeoffError(
            "Bakeoff review video models must exactly match the plan video models."
        )

    normalized_video: dict[str, list[dict[str, Any]]] = {}
    expected_shots = plan["representative_character_micro_shot_ids"]
    for model in plan["video_models"]:
        entries = reviews["video_reviews"][model]
        if not isinstance(entries, list) or len(entries) != 3:
            raise ModelBakeoffError(
                f"Video model {model} must have exactly one review for each representative shot."
            )
        normalized_entries = [
            _validate_video_review(plan, model, entry) for entry in entries
        ]
        by_shot = {entry["micro_shot_id"]: entry for entry in normalized_entries}
        if len(by_shot) != 3 or set(by_shot) != set(expected_shots):
            raise ModelBakeoffError(
                f"Video model {model} must have exactly one review for each representative shot."
            )
        normalized_video[model] = [by_shot[shot_id] for shot_id in expected_shots]

    if not isinstance(reviews["still_reviews"], Mapping):
        raise ModelBakeoffError("Bakeoff still reviews must be an object.")
    normalized_still: dict[str, list[dict[str, Any]]] = {}
    if not plan["requires_still"]:
        if reviews["still_reviews"]:
            raise ModelBakeoffError(
                "Bakeoff reviews must omit still model reviews when no still route is required."
            )
    else:
        if set(reviews["still_reviews"]) != set(plan["still_models"]):
            raise ModelBakeoffError(
                "Bakeoff review still models must exactly match the plan still models."
            )
        for model in plan["still_models"]:
            entries = reviews["still_reviews"][model]
            if not isinstance(entries, list) or len(entries) != 1:
                raise ModelBakeoffError(
                    f"Still model {model} must have exactly one review."
                )
            normalized_still[model] = [
                _validate_still_review(plan, model, entries[0])
            ]

    return {
        "schema_version": MODEL_BAKEOFF_REVIEW_SCHEMA,
        "project_id": plan["project_id"],
        "video_reviews": normalized_video,
        "still_reviews": normalized_still,
    }


def _validate_video_review(
    plan: Mapping[str, Any], model: str, review: Any
) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        raise ModelBakeoffError(f"Video review for {model} must be an object.")
    _require_exact_keys(review, _VIDEO_REVIEW_KEYS, f"Video review for {model}")
    shot_id = _exact_text(review["micro_shot_id"], "Video review micro-shot ID")
    if shot_id not in plan["representative_character_micro_shot_ids"]:
        raise ModelBakeoffError(f"Unknown video review micro-shot ID: {shot_id}.")
    expected_trial = _trial_for_shot(plan["speaking_trials"], shot_id)
    speaker_id = _exact_text(review["speaker_id"], "Video review speaker ID")
    dialogue_id = _exact_text(review["dialogue_id"], "Video review dialogue ID")
    audio_sha256 = _audio_sha256(review["audio_sha256"], "Video review audio_sha256")
    if (
        speaker_id != expected_trial["speaker_id"]
        or dialogue_id != expected_trial["dialogue_id"]
        or audio_sha256 != expected_trial["audio_sha256"]
    ):
        raise ModelBakeoffError("Video review speaking trial does not match the plan.")
    path = _candidate_path(
        plan,
        review["candidate_path"],
        shot_id=shot_id,
        model=model,
        kind="video",
    )
    evidence = _file_evidence(path)
    scores = dict(review["scores"]) if isinstance(review["scores"], Mapping) else review["scores"]
    weighted_score(scores)
    passed = scores["lipsync"] >= LIPSYNC_MINIMUM_SCORE
    if not isinstance(review["passed"], bool) or review["passed"] != passed:
        raise ModelBakeoffError("Video review lipsync passed state is inconsistent.")
    failures = _hard_failures(
        review["hard_failures"], VIDEO_HARD_FAILURES, "Video hard failure"
    )
    notes = _local_notes(review["notes"])
    return {
        "micro_shot_id": shot_id,
        "speaker_id": speaker_id,
        "dialogue_id": dialogue_id,
        "audio_sha256": audio_sha256,
        "candidate_path": str(path),
        "size_bytes": evidence["size_bytes"],
        "sha256": evidence["sha256"],
        "scores": scores,
        "hard_failures": failures,
        "notes": notes,
        "passed": passed,
    }


def _validate_still_review(
    plan: Mapping[str, Any], model: str, review: Any
) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        raise ModelBakeoffError(f"Still review for {model} must be an object.")
    _require_exact_keys(review, _STILL_REVIEW_KEYS, f"Still review for {model}")
    shot_id = _exact_text(review["micro_shot_id"], "Still review micro-shot ID")
    if shot_id != plan["still_micro_shot_id"]:
        raise ModelBakeoffError(f"Unknown still review micro-shot ID: {shot_id}.")
    path = _candidate_path(
        plan,
        review["candidate_path"],
        shot_id=shot_id,
        model=model,
        kind="still",
    )
    evidence = _file_evidence(path)
    score = _finite_number(
        review["score"], "Still review score", minimum=0, maximum=100
    )
    failures = _hard_failures(
        review["hard_failures"], STILL_HARD_FAILURES, "Still hard failure"
    )
    notes = _local_notes(review["notes"])
    return {
        "micro_shot_id": shot_id,
        "candidate_path": str(path),
        "size_bytes": evidence["size_bytes"],
        "sha256": evidence["sha256"],
        "score": score,
        "hard_failures": failures,
        "notes": notes,
    }


def _validate_report(
    report: Mapping[str, Any], *, require_artifacts: bool = True
) -> dict[str, Any]:
    try:
        normalized = _validate_report_inner(report)
        if require_artifacts:
            _validate_bound_artifacts(normalized)
        return normalized
    except ModelBakeoffError as exc:
        if str(exc).startswith("Bakeoff report"):
            raise
        raise ModelBakeoffError(f"Bakeoff report is invalid: {exc}") from exc


def _validate_report_inner(report: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ModelBakeoffError("Bakeoff report must be an object.")
    _require_exact_keys(report, _REPORT_KEYS, "Bakeoff report")
    if report["schema_version"] != MODEL_BAKEOFF_REPORT_SCHEMA:
        raise ModelBakeoffError("Bakeoff report has an unsupported schema_version.")
    project_id = _exact_text(report["project_id"], "Report project_id")
    root = _run_root(report["run_dir"])
    representative_ids = _exact_string_list(
        report["representative_character_micro_shot_ids"],
        "Report representative character micro-shot IDs",
    )
    speaking_trials = _validate_speaking_trials(
        report["speaking_trials"], representative_ids, "Report"
    )
    if not isinstance(report["requires_still"], bool):
        raise ModelBakeoffError("Report requires_still must be a boolean.")
    still_id = report["still_micro_shot_id"]
    if report["requires_still"]:
        still_id = _exact_text(still_id, "Report still micro-shot ID")
    elif still_id != "":
        raise ModelBakeoffError(
            "Report without a still route must use an empty still micro-shot ID."
        )
    if still_id in representative_ids:
        raise ModelBakeoffError("Report video and still representative IDs must differ.")
    if isinstance(report["minimum_score"], bool) or report["minimum_score"] != 80:
        raise ModelBakeoffError("Report minimum_score must be exactly 80.")
    if (
        isinstance(report["max_candidates_per_model"], bool)
        or report["max_candidates_per_model"] != 3
    ):
        raise ModelBakeoffError(
            "Report max_candidates_per_model must be exactly 3."
        )
    video_models = _model_list(
        report["video_models"], PRODUCTION_VIDEO_MODELS, "Report video model whitelist"
    )
    still_models = _model_list(
        report["still_models"], PRODUCTION_STILL_MODELS, "Report still model whitelist"
    )
    if not isinstance(report["video_results"], list) or len(
        report["video_results"]
    ) != len(video_models):
        raise ModelBakeoffError("Report video results must exactly match video models.")

    candidate_plan = {
        "run_dir": str(root),
        "max_candidates_per_model": 3,
    }
    normalized_video_results: list[dict[str, Any]] = []
    for model, result in zip(video_models, report["video_results"], strict=True):
        if not isinstance(result, Mapping):
            raise ModelBakeoffError("Report video result must be an object.")
        _require_exact_keys(result, _VIDEO_RESULT_KEYS, "Report video result")
        if result["model"] != model:
            raise ModelBakeoffError("Report video result model order is invalid.")
        shots = result["shots"]
        if not isinstance(shots, list) or len(shots) != 3:
            raise ModelBakeoffError("Report video result must contain exactly three shots.")
        normalized_shots: list[dict[str, Any]] = []
        failures: set[str] = set()
        shot_ids: list[str] = []
        for shot in shots:
            if not isinstance(shot, Mapping):
                raise ModelBakeoffError("Report video shot must be an object.")
            _require_exact_keys(
                shot, _VIDEO_SHOT_RESULT_KEYS, "Report video shot result"
            )
            shot_id = _exact_text(shot["micro_shot_id"], "Report micro-shot ID")
            if shot_id in shot_ids:
                raise ModelBakeoffError("Report video shot IDs must be unique.")
            path = _candidate_path(
                candidate_plan,
                shot["candidate_path"],
                shot_id=shot_id,
                model=model,
                kind="video",
            )
            evidence = _stored_evidence(shot, path, "Report video candidate")
            expected_trial = _trial_for_shot(speaking_trials, shot_id)
            speaker_id = _exact_text(shot["speaker_id"], "Report speaker ID")
            dialogue_id = _exact_text(shot["dialogue_id"], "Report dialogue ID")
            audio_sha256 = _audio_sha256(shot["audio_sha256"], "Report audio_sha256")
            if (
                speaker_id != expected_trial["speaker_id"]
                or dialogue_id != expected_trial["dialogue_id"]
                or audio_sha256 != expected_trial["audio_sha256"]
            ):
                raise ModelBakeoffError("Report video speaking trial does not match the plan.")
            scores = dict(shot["scores"]) if isinstance(shot["scores"], Mapping) else shot["scores"]
            calculated = weighted_score(scores)
            stored = _finite_number(
                shot["weighted_score"],
                "Report weighted score",
                minimum=0,
                maximum=100,
            )
            if stored != calculated:
                raise ModelBakeoffError("Report weighted score does not match raw scores.")
            trial_passed = scores["lipsync"] >= LIPSYNC_MINIMUM_SCORE
            if not isinstance(shot["passed"], bool) or shot["passed"] != trial_passed:
                raise ModelBakeoffError("Report video lipsync passed state is inconsistent.")
            shot_failures = _hard_failures(
                shot["hard_failures"], VIDEO_HARD_FAILURES, "Report video hard failure"
            )
            failures.update(shot_failures)
            shot_ids.append(shot_id)
            normalized_shots.append(
                {
                    "micro_shot_id": shot_id,
                    "speaker_id": speaker_id,
                    "dialogue_id": dialogue_id,
                    "audio_sha256": audio_sha256,
                    "candidate_path": str(path),
                    "size_bytes": evidence["size_bytes"],
                    "sha256": evidence["sha256"],
                    "scores": scores,
                    "weighted_score": stored,
                    "hard_failures": shot_failures,
                    "notes": _local_notes(shot["notes"]),
                    "passed": trial_passed,
                }
            )
        if shot_ids != representative_ids:
            raise ModelBakeoffError(
                "Report video models must review the bound representative shots in order."
            )
        aggregate = round(
            sum(item["weighted_score"] for item in normalized_shots) / 3, 2
        )
        stored_aggregate = _finite_number(
            result["aggregate_score"],
            "Report aggregate score",
            minimum=0,
            maximum=100,
        )
        if stored_aggregate != aggregate:
            raise ModelBakeoffError("Report aggregate score is inconsistent.")
        stored_failures = _hard_failures(
            result["hard_failures"], VIDEO_HARD_FAILURES, "Report video hard failure"
        )
        if stored_failures != sorted(failures):
            raise ModelBakeoffError("Report video hard failures are inconsistent.")
        expected_passed = aggregate >= 80 and not failures
        if not isinstance(result["passed"], bool) or result["passed"] != expected_passed:
            raise ModelBakeoffError("Report video passed state is inconsistent.")
        speaking_result_trials = _validate_result_speaking_trials(
            result["speaking_trials"], normalized_shots
        )
        normalized_video_results.append(
            {
                "model": model,
                "shots": normalized_shots,
                "speaking_trials": speaking_result_trials,
                "aggregate_score": aggregate,
                "hard_failures": stored_failures,
                "passed": expected_passed,
            }
        )

    normalized_still_results: list[dict[str, Any]] = []
    if report["requires_still"]:
        if not isinstance(report["still_results"], list) or len(
            report["still_results"]
        ) != len(still_models):
            raise ModelBakeoffError(
                "Report still results must exactly match still models."
            )
        for model, result in zip(still_models, report["still_results"], strict=True):
            if not isinstance(result, Mapping):
                raise ModelBakeoffError("Report still result must be an object.")
            _require_exact_keys(result, _STILL_RESULT_KEYS, "Report still result")
            if result["model"] != model:
                raise ModelBakeoffError("Report still result model order is invalid.")
            shot_id = _exact_text(result["micro_shot_id"], "Report still micro-shot ID")
            if shot_id != still_id:
                raise ModelBakeoffError(
                    "Report still models must review the bound representative shot."
                )
            path = _candidate_path(
                candidate_plan,
                result["candidate_path"],
                shot_id=shot_id,
                model=model,
                kind="still",
            )
            evidence = _stored_evidence(result, path, "Report still candidate")
            score = _finite_number(
                result["score"], "Report still score", minimum=0, maximum=100
            )
            failures = _hard_failures(
                result["hard_failures"],
                STILL_HARD_FAILURES,
                "Report still hard failure",
            )
            expected_passed = score >= 80 and not failures
            if not isinstance(result["passed"], bool) or result["passed"] != expected_passed:
                raise ModelBakeoffError("Report still passed state is inconsistent.")
            normalized_still_results.append(
                {
                    "model": model,
                    "micro_shot_id": shot_id,
                    "candidate_path": str(path),
                    "size_bytes": evidence["size_bytes"],
                    "sha256": evidence["sha256"],
                    "score": score,
                    "hard_failures": failures,
                    "notes": _local_notes(result["notes"]),
                    "passed": expected_passed,
                }
            )
    elif report["still_results"] != []:
        raise ModelBakeoffError("Report without a still route must have no still results.")

    selected_model = _highest_passing(normalized_video_results, "aggregate_score")
    selected_still_model = (
        _highest_passing(normalized_still_results, "score")
        if report["requires_still"]
        else ""
    )
    if report["selected_model"] != selected_model:
        raise ModelBakeoffError("Report selected video model is inconsistent.")
    if report["selected_still_model"] != selected_still_model:
        raise ModelBakeoffError("Report selected still model is inconsistent.")
    production_ready = bool(selected_model) and (
        not report["requires_still"] or bool(selected_still_model)
    )
    if (
        not isinstance(report["production_ready"], bool)
        or report["production_ready"] != production_ready
    ):
        raise ModelBakeoffError("Report production_ready state is inconsistent.")
    return {
        "schema_version": MODEL_BAKEOFF_REPORT_SCHEMA,
        "project_id": project_id,
        "run_dir": str(root),
        "representative_character_micro_shot_ids": representative_ids,
        "speaking_trials": speaking_trials,
        "requires_still": report["requires_still"],
        "still_micro_shot_id": still_id,
        "minimum_score": 80,
        "max_candidates_per_model": 3,
        "video_models": video_models,
        "still_models": still_models,
        "selected_model": selected_model,
        "selected_still_model": selected_still_model,
        "production_ready": production_ready,
        "video_results": normalized_video_results,
        "still_results": normalized_still_results,
    }


def _validate_bound_artifacts(report: Mapping[str, Any]) -> None:
    root = Path(report["run_dir"])
    plan_artifact = _validate_plan(
        _read_json_artifact(root / "model_bakeoff_plan.json", "plan artifact")
    )
    expected_plan = {
        "schema_version": MODEL_BAKEOFF_PLAN_SCHEMA,
        "project_id": report["project_id"],
        "run_dir": report["run_dir"],
        "representative_character_micro_shot_ids": report[
            "representative_character_micro_shot_ids"
        ],
        "speaking_trials": report["speaking_trials"],
        "requires_still": report["requires_still"],
        "still_micro_shot_id": report["still_micro_shot_id"],
        "video_models": report["video_models"],
        "still_models": report["still_models"],
        "minimum_score": report["minimum_score"],
        "max_candidates_per_model": report["max_candidates_per_model"],
    }
    if plan_artifact != expected_plan:
        raise ModelBakeoffError(
            "Bakeoff plan artifact does not match the reviewed report."
        )

    review_artifact = _read_json_artifact(
        root / "model_bakeoff_review.json", "review artifact"
    )
    expected_review = _review_artifact_from_report(report)
    if review_artifact != expected_review:
        raise ModelBakeoffError(
            "Bakeoff review artifact does not match the report evidence."
        )

    report_artifact = _read_json_artifact(
        root / "model_bakeoff_report.json", "report artifact"
    )
    if report_artifact != report:
        raise ModelBakeoffError(
            "Bakeoff report artifact does not match the supplied report."
        )


def _review_artifact_from_report(report: Mapping[str, Any]) -> dict[str, Any]:
    video_reviews: dict[str, list[dict[str, Any]]] = {}
    for result in report["video_results"]:
        video_reviews[result["model"]] = [
            {
                "micro_shot_id": shot["micro_shot_id"],
                "speaker_id": shot["speaker_id"],
                "dialogue_id": shot["dialogue_id"],
                "audio_sha256": shot["audio_sha256"],
                "candidate_path": shot["candidate_path"],
                "size_bytes": shot["size_bytes"],
                "sha256": shot["sha256"],
                "scores": shot["scores"],
                "hard_failures": shot["hard_failures"],
                "notes": shot["notes"],
                "passed": shot["passed"],
            }
            for shot in result["shots"]
        ]
    still_reviews: dict[str, list[dict[str, Any]]] = {}
    for result in report["still_results"]:
        still_reviews[result["model"]] = [
            {
                "micro_shot_id": result["micro_shot_id"],
                "candidate_path": result["candidate_path"],
                "size_bytes": result["size_bytes"],
                "sha256": result["sha256"],
                "score": result["score"],
                "hard_failures": result["hard_failures"],
                "notes": result["notes"],
            }
        ]
    return {
        "schema_version": MODEL_BAKEOFF_REVIEW_SCHEMA,
        "project_id": report["project_id"],
        "video_reviews": video_reviews,
        "still_reviews": still_reviews,
    }


def _read_json_artifact(path: Path, label: str) -> Mapping[str, Any]:
    _reject_symlink_components(path, f"Bakeoff {label}")
    try:
        metadata = path.stat()
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelBakeoffError(f"Bakeoff {label} is missing or unreadable.") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ModelBakeoffError(f"Bakeoff {label} must be a regular file.")
    if _unsafe_artifact_text(raw):
        raise ModelBakeoffError(
            f"Bakeoff {label} contains a URI or credential value."
        )
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ModelBakeoffError(f"Bakeoff {label} is not valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise ModelBakeoffError(f"Bakeoff {label} must contain an object.")
    return payload


def _candidate_path(
    plan: Mapping[str, Any],
    value: Any,
    *,
    shot_id: str,
    model: str,
    kind: str,
) -> Path:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ModelBakeoffError("Candidate path must be an exact local path.")
    parsed = urlsplit(value)
    if parsed.scheme or value.lower().startswith("data:"):
        raise ModelBakeoffError("Candidate path must be a local file path.")
    if any(part in {".", ".."} for part in value.split(os.sep)):
        raise ModelBakeoffError("Candidate path must not contain traversal components.")
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        raise ModelBakeoffError("Candidate path must be an exact absolute path.")
    root = Path(plan["run_dir"])
    category = "micro_clips" if kind == "video" else "micro_stills"
    pattern = _VIDEO_CANDIDATE if kind == "video" else _STILL_CANDIDATE
    match = pattern.fullmatch(path.name)
    if match is None:
        raise ModelBakeoffError("Candidate path has an invalid candidate filename.")
    number = int(match.group(1))
    if not 1 <= number <= plan["max_candidates_per_model"]:
        raise ModelBakeoffError("Candidate path exceeds the maximum candidate number.")
    expected_parent = root / category / shot_id / model
    if path.parent != expected_parent:
        raise ModelBakeoffError(
            "Candidate path does not match the reviewed micro-shot and model."
        )
    _reject_symlink_components(path, "Candidate path")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ModelBakeoffError("Candidate path must be an existing regular file.") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ModelBakeoffError("Candidate path must be an existing regular file.")
    if kind == "video":
        if not is_valid_mp4_file(path):
            raise ModelBakeoffError("Candidate path must contain a valid MP4 video.")
    else:
        try:
            with Image.open(path) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
            with Image.open(path) as decoded:
                decoded.load()
                if decoded.format != image_format or decoded.size != (width, height):
                    raise OSError("Still candidate changed during image decoding.")
        except (OSError, SyntaxError, ValueError, UnidentifiedImageError) as exc:
            raise ModelBakeoffError(
                "Candidate path must contain a valid PNG, JPEG, or WebP image."
            ) from exc
        if image_format not in {"PNG", "JPEG", "WEBP"} or width <= 0 or height <= 0:
            raise ModelBakeoffError(
                "Candidate path must contain a valid PNG, JPEG, or WebP image."
            )
    return path


def _file_evidence(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ModelBakeoffError(
            "Candidate content changed or could not be read for hashing."
        ) from exc
    return {"size_bytes": size, "sha256": digest.hexdigest()}


def _stored_evidence(
    record: Mapping[str, Any], path: Path, label: str
) -> dict[str, int | str]:
    size = record["size_bytes"]
    digest = record["sha256"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ModelBakeoffError(f"{label} size_bytes must be a positive integer.")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ModelBakeoffError(f"{label} sha256 must be lowercase hexadecimal.")
    current = _file_evidence(path)
    if current["size_bytes"] != size or current["sha256"] != digest:
        raise ModelBakeoffError(
            f"{label} content changed: size_bytes or sha256 no longer matches."
        )
    return {"size_bytes": size, "sha256": digest}


def _run_root(value: Any, *, create: bool = False) -> Path:
    if not isinstance(value, (str, Path)):
        raise ModelBakeoffError("Bakeoff run_dir must be a string or Path.")
    raw = str(value)
    if not raw or raw != raw.strip() or _unsafe_artifact_text(raw):
        raise ModelBakeoffError("Bakeoff run_dir must be an exact local path.")
    if any(part in {".", ".."} for part in raw.split(os.sep)):
        raise ModelBakeoffError("Bakeoff run_dir must not contain traversal components.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if str(path) != str(Path(str(path))):
        raise ModelBakeoffError("Bakeoff run_dir must be canonical.")
    _reject_symlink_components(path, "Bakeoff run_dir")
    if path.exists() and not path.is_dir():
        raise ModelBakeoffError("Bakeoff run_dir must be a directory.")
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ModelBakeoffError("Unable to create bakeoff run_dir.") from exc
        _reject_symlink_components(path, "Bakeoff run_dir")
    return path


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in path.parts:
        if part == path.anchor:
            continue
        current /= part
        try:
            if current.is_symlink():
                raise ModelBakeoffError(f"{label} must not use a symlink: {current}")
        except OSError as exc:
            raise ModelBakeoffError(f"Unable to inspect {label.lower()}: {current}") from exc


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _reject_symlink_components(path.parent, "Bakeoff artifact directory")
    if path.is_symlink():
        raise ModelBakeoffError("Bakeoff artifact path must not be a symlink.")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    if _unsafe_artifact_text(serialized):
        raise ModelBakeoffError(
            "Bakeoff artifacts must contain only local data and no secrets."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _highest_passing(results: Sequence[Mapping[str, Any]], score_key: str) -> str:
    passing = [item for item in results if item["passed"]]
    if not passing:
        return ""
    return max(passing, key=lambda item: item[score_key])["model"]


def _model_list(
    value: Any, whitelist: frozenset[str], label: str
) -> list[str]:
    models = _exact_string_list(value, label)
    if len(models) != len(whitelist) or len(set(models)) != len(models) or set(models) != whitelist:
        raise ModelBakeoffError(f"{label} must exactly match the production whitelist.")
    return models


def _hard_failures(value: Any, allowed: frozenset[str], label: str) -> list[str]:
    failures = _exact_string_list(value, f"{label}s")
    if len(set(failures)) != len(failures):
        raise ModelBakeoffError(f"{label}s must not contain duplicates.")
    unknown = sorted(set(failures) - allowed)
    if unknown:
        raise ModelBakeoffError(f"Unknown {label.lower()}: {', '.join(unknown)}.")
    return failures


def _local_notes(value: Any) -> str:
    if not isinstance(value, str):
        raise ModelBakeoffError("Review notes must be a string.")
    if _unsafe_artifact_text(value):
        raise ModelBakeoffError("Review notes must contain only local, non-secret text.")
    return value


def _unsafe_artifact_text(value: str) -> bool:
    return bool(_URI_SCHEME.search(value) or _SECRET_VALUE.search(value))


def _finite_number(
    value: Any, label: str, *, minimum: float, maximum: float
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelBakeoffError(f"{label} must be a finite numeric score.")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ModelBakeoffError(
            f"{label} must be a finite score from {minimum:g} to {maximum:g}."
        )
    return value


def _audio_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ModelBakeoffError(f"{label} must be lowercase hexadecimal.")
    return value


def _exact_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ModelBakeoffError(f"{label} must be a non-empty exact string.")
    return value


def _exact_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ModelBakeoffError(f"{label} must be a list.")
    return [_exact_text(item, label) for item in value]


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if extra:
        details.append("extra: " + ", ".join(extra))
    raise ModelBakeoffError(
        f"{label} must contain exact fields ({'; '.join(details)})."
    )
