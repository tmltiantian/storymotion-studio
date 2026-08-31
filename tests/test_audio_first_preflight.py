import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from factory.dialogue_assets import DialogueAudioAsset, DialogueAudioManifest
from factory.model_bakeoff import (
    MODEL_BAKEOFF_REVIEW_SCHEMA,
    SCORE_WEIGHTS,
    build_model_bakeoff_plan,
    finalize_bakeoff,
)
from factory.performance_card import PerformanceCard, PerformanceSheet
from factory.schema import (
    Character,
    DialogueLine,
    Episode,
    Shot,
    episode_to_dict,
)
from factory.visual_timeline import MicroShot, VisualTimeline, visual_timeline_to_dict
from tests.media_fixtures import VALID_VIDEO_MP4

from factory.audio_first_preflight import run_audio_first_preflight


MODEL = "doubao-seedance-2-0"


def _episode() -> Episode:
    characters = [
        Character("wukong", "Wukong", "lead", "guarded", "dark coat", "low"),
        Character("yangjian", "Yang Jian", "lead", "calm", "light coat", "calm"),
        Character("nezha", "Nezha", "lead", "focused", "red armor", "bright"),
    ]
    return Episode(
        project_id="audio-first-run",
        title="Audio-first preflight",
        language="en-US",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=characters,
        shots=[
            Shot(
                "shot_001", 1, "Shop", "Wukong, Yang Jian, and Nezha each reach toward the envelope while speaking in turn.",
                "A shop counter.", "static", 9.0, "tense",
                dialogue=[
                    DialogueLine("wukong", "I will go first."),
                    DialogueLine("yangjian", "Then I will follow."),
                    DialogueLine("nezha", "I am ready."),
                ],
                character_ids=["wukong", "yangjian", "nezha"],
            ),
            Shot(
                "shot_002", 2, "Shop", "Wukong reaches for the envelope.",
                "The counter and envelope.", "static", 3.0, "tense",
                character_ids=["wukong"],
            ),
        ],
    )


def _micro_shot(
    identifier: str,
    index: int,
    *,
    character_id: str,
    parent_shot_id: str,
    visible_speech: bool,
) -> MicroShot:
    return MicroShot(
        id=identifier,
        index=index,
        parent_shot_id=parent_shot_id,
        scene_context="Shop",
        time_context="source-unspecified",
        purpose="action",
        character_ids=(character_id,),
        emotion_start="tense",
        emotion_end="alert",
        emotion_intensity=3,
        gaze="at the envelope",
        pose_start="beside the counter",
        pose_end="near the envelope",
        action_actor_id=character_id,
        action_code="reach",
        action_target="envelope",
        camera_mode="locked",
        source_duration_seconds=3,
        timeline_duration_seconds=3,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_rain",),
        cadence_fps=8,
    )


def _timeline() -> VisualTimeline:
    return VisualTimeline(
        project_id="audio-first-run",
        micro_shots=(
            _micro_shot("micro_wukong", 1, character_id="wukong", parent_shot_id="shot_001", visible_speech=True),
            _micro_shot("micro_yangjian", 2, character_id="yangjian", parent_shot_id="shot_001", visible_speech=True),
            _micro_shot("micro_nezha", 3, character_id="nezha", parent_shot_id="shot_001", visible_speech=True),
            _micro_shot("micro_action", 4, character_id="wukong", parent_shot_id="shot_002", visible_speech=False),
        ),
    )


def _sheet(timeline: VisualTimeline) -> PerformanceSheet:
    cards = []
    for shot in timeline.micro_shots:
        is_speaking = shot.id != "micro_action"
        cards.append(
            PerformanceCard(
                micro_shot_id=shot.id,
                purpose=shot.purpose,
                speaker_id=shot.character_ids[0] if is_speaking else "",
                dialogue_id=f"shot_001.dialogue_{shot.index:02d}" if is_speaking else "",
                requires_visible_lipsync=is_speaking,
                entry_anchor_id=f"anchor_{shot.index:03d}",
                scene_keyframe_id=f"scene_{shot.index:03d}",
                actor_id=shot.character_ids[0],
                target_id="envelope",
                contact_point="",
                prop_hand="",
                start_beat="starts still",
                main_beat="speaks one line" if is_speaking else "reaches once",
                end_beat="ends still",
                negative_constraints=("no_rain",),
            )
        )
    return PerformanceSheet(project_id="audio-first-run", cards=tuple(cards))


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nlocal-reference")


def _write_run(run_dir: Path, *, speaking_capable: bool) -> Path:
    run_dir.mkdir()
    episode = _episode()
    timeline = _timeline()
    sheet = _sheet(timeline)
    assets: list[DialogueAudioAsset] = []
    for card in sheet.cards:
        if not card.requires_visible_lipsync:
            continue
        audio = run_dir / "dialogue_audio" / f"{card.dialogue_id}.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(card.dialogue_id.encode("utf-8"))
        assets.append(
            DialogueAudioAsset(
                dialogue_id=card.dialogue_id,
                speaker_id=card.speaker_id,
                path=str(audio),
                sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
                duration_seconds=1.0,
                voice_id=f"voice-{card.speaker_id}",
            )
        )
    manifest = DialogueAudioManifest(
        assets=tuple(assets),
        path=str(run_dir / "dialogue_audio_manifest.json"),
        voiceover_audio="",
        voiceover_sha256="",
    )
    character_entries = []
    for character in episode.characters:
        reference = run_dir / "assets" / "characters" / f"{character.id}.png"
        _write_image(reference)
        character_entries.append(
            {
                "character_id": character.id,
                "reference_image_path": str(reference),
                "reference_image_exists": True,
                "asset_source": "user_generated_ai",
                "provenance_status": "confirmed",
                "production_ready": True,
            }
        )
    scene_keyframes = {}
    approved_anchors = {}
    for card in sheet.cards:
        keyframe = run_dir / "scene_keyframes" / f"{card.scene_keyframe_id}.png"
        anchor = run_dir / "approved_anchors" / f"{card.entry_anchor_id}.png"
        _write_image(keyframe)
        _write_image(anchor)
        scene_keyframes[card.scene_keyframe_id] = str(keyframe)
        approved_anchors[card.entry_anchor_id] = str(anchor)

    (run_dir / "episode.json").write_text(json.dumps(episode_to_dict(episode)), encoding="utf-8")
    (run_dir / "visual_timeline.json").write_text(json.dumps(visual_timeline_to_dict(timeline)), encoding="utf-8")
    (run_dir / "performance_sheet.json").write_text(json.dumps(asdict(sheet)), encoding="utf-8")
    (run_dir / "dialogue_audio_manifest.json").write_text(
        json.dumps({"schema_version": manifest.schema_version, "voiceover_audio": "", "voiceover_sha256": "", "assets": [asdict(asset) for asset in manifest.assets]}),
        encoding="utf-8",
    )
    (run_dir / "character_assets.json").write_text(
        json.dumps({"project_id": episode.project_id, "production_ready": True, "characters": character_entries}),
        encoding="utf-8",
    )
    (run_dir / "scene_keyframes.json").write_text(json.dumps(scene_keyframes), encoding="utf-8")
    (run_dir / "approved_anchors.json").write_text(json.dumps(approved_anchors), encoding="utf-8")
    (run_dir / "candidate_review.json").write_text(
        json.dumps({"schema_version": "motion-comic-factory.candidate-review.v1", "project_id": episode.project_id, "candidates": []}),
        encoding="utf-8",
    )

    plan = build_model_bakeoff_plan(
        episode,
        timeline,
        ["micro_wukong", "micro_yangjian", "micro_nezha"],
        run_dir,
        performance_sheet=sheet,
        dialogue_manifest=manifest,
        still_micro_shot_id=None,
        video_models=[MODEL],
        still_models=["doubao-seedream-4-5"],
    )
    reviews = {"schema_version": MODEL_BAKEOFF_REVIEW_SCHEMA, "project_id": episode.project_id, "video_reviews": {MODEL: []}, "still_reviews": {}}
    for trial in plan["speaking_trials"]:
        candidate = run_dir / "micro_clips" / trial["micro_shot_id"] / MODEL / "candidate_001.mp4"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(VALID_VIDEO_MP4)
        reviews["video_reviews"][MODEL].append(
            {
                **trial,
                "candidate_path": str(candidate),
                "scores": {name: 5.0 for name in SCORE_WEIGHTS},
                "hard_failures": [],
                "notes": "local review",
                "passed": True,
            }
        )
    report = finalize_bakeoff(plan, reviews)
    if not speaking_capable:
        report["video_results"][0]["speaking_trials"] = []
        (run_dir / "model_bakeoff_report.json").write_text(json.dumps(report), encoding="utf-8")
    return run_dir


@pytest.fixture
def run_fixture(tmp_path: Path) -> Path:
    return _write_run(tmp_path / "run", speaking_capable=False)


@pytest.fixture
def speaking_fixture(tmp_path: Path) -> Path:
    return _write_run(tmp_path / "run", speaking_capable=True)


@pytest.fixture
def delivered_episode_v3(tmp_path: Path) -> Path:
    export = tmp_path / "episode_01_role_dialogue_v3.mp4"
    export.write_bytes(b"delivered-episode-v3")
    return export


def test_preflight_is_local_and_blocks_visible_speech_without_a_speaking_model(run_fixture, monkeypatch):
    monkeypatch.setattr(
        "factory.micro_video_batch.GatewayVideoClient",
        lambda *_: pytest.fail("network client created"),
    )

    report = run_audio_first_preflight(run_fixture, model=MODEL)

    assert report["success"] is False
    assert report["planned_count"] == 1
    assert report["blocked_count"] == 3
    assert all("not speaking-capable" in error for error in report["errors"])
    assert json.loads((run_fixture / "preflight_report.json").read_text()) == report


def test_preflight_never_overwrites_delivered_episode_v3(delivered_episode_v3, run_fixture):
    original = delivered_episode_v3.read_bytes()

    run_audio_first_preflight(run_fixture, model=MODEL)

    assert delivered_episode_v3.read_bytes() == original


def test_speaking_capable_fixture_plans_jobs_without_constructing_gateway_client(speaking_fixture, monkeypatch):
    monkeypatch.setattr(
        "factory.micro_video_batch.GatewayVideoClient",
        lambda *_: pytest.fail("network client created"),
    )

    report = run_audio_first_preflight(speaking_fixture, model=MODEL)

    assert report["success"] is True
    assert report["planned_count"] == 4
    assert report["blocked_count"] == 0
    assert report["errors"] == []
