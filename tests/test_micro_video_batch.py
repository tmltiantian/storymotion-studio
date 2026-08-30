import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from factory.gateway_video import (
    GatewayVideoConfig,
    GatewayVideoError,
    GatewayVideoTask,
)
from factory.gateway_video_batch import GatewayVideoJob, _job_signature
from factory.dialogue_assets import DialogueAudioAsset, DialogueAudioManifest
from factory.micro_video_batch import (
    MicroVideoBatchError,
    MicroVideoJob,
    _reference_label,
    build_micro_video_jobs as _build_micro_video_jobs,
    candidate_output_path,
    candidate_report_path,
    render_micro_video_batch,
)
from factory.prompt_compiler import compile_video_prompt
from factory.prompt_safety import PREVIOUS_SHOT_CONTINUITY
from factory.performance_card import PerformanceCard, PerformanceSheet
from factory.schema import Character, DialogueLine, Episode, Shot
from factory.visual_timeline import MicroShot, VisualTimeline


def _episode(*, continuity: bool = False) -> Episode:
    first = Shot(
        "shot_001",
        1,
        "Shop",
        "Lin Che reaches toward the envelope at the counter.",
        "Shop counter and envelope.",
        "static",
        3.0 if continuity else 6.0,
        "tense",
    )
    shots = [first]
    if continuity:
        shots.append(
            Shot(
                "shot_002",
                2,
                "Scene 2",
                "Lin Che pauses beside the envelope.",
                "The envelope remains still.",
                "static",
                3.0,
                "tense",
            )
        )
    return Episode(
        project_id="micro-project",
        title="Micro video test",
        language="en",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[
            Character("char_a", "Lin Che", "lead", "guarded", "dark coat", "low"),
            Character("char_b", "Su Mian", "lead", "calm", "light coat", "calm"),
        ],
        shots=shots,
    )


def _micro_shot(
    *,
    micro_id: str = "micro_001",
    index: int = 1,
    parent_shot_id: str = "shot_001",
    character_ids: tuple[str, ...] = ("char_a",),
    scene_context: str = "Shop",
    source_duration_seconds: int = 3,
    timeline_duration_seconds: float = 3.0,
) -> MicroShot:
    character_free = not character_ids
    return MicroShot(
        id=micro_id,
        index=index,
        parent_shot_id=parent_shot_id,
        scene_context=scene_context,
        time_context="source-unspecified",
        purpose="object" if character_free else "action",
        character_ids=character_ids,
        emotion_start="still" if character_free else "guarded",
        emotion_end="still" if character_free else "alarmed",
        emotion_intensity=3,
        gaze="at the envelope",
        pose_start="beside the counter",
        pose_end="near the envelope",
        action_actor_id="object" if character_free else "char_a",
        action_code="hold_still" if character_free else "reach",
        action_target="envelope",
        camera_mode="object_insert" if character_free else "locked",
        source_duration_seconds=source_duration_seconds,
        timeline_duration_seconds=timeline_duration_seconds,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_rain",),
        cadence_fps=8,
    )


def _timeline(*, continuity: bool = False) -> VisualTimeline:
    episode = _episode(continuity=continuity)
    first = _micro_shot()
    second = _micro_shot(
        micro_id="micro_002",
        index=2,
        parent_shot_id="shot_002" if continuity else "shot_001",
        character_ids=("char_a",) if continuity else (),
        scene_context=PREVIOUS_SHOT_CONTINUITY if continuity else "Shop",
    )
    return VisualTimeline(project_id=episode.project_id, micro_shots=(first, second))


def _assets(tmp_path: Path, *, project_id: str = "micro-project") -> dict:
    role_a = tmp_path / "run/assets/characters/a.png"
    role_b = tmp_path / "run/assets/characters/b.png"
    role_a.parent.mkdir(parents=True, exist_ok=True)
    role_a.write_bytes(b"\x89PNG\r\n\x1a\nrole-a")
    role_b.write_bytes(b"\x89PNG\r\n\x1a\nrole-b")
    return {
        "project_id": project_id,
        "production_ready": True,
        "characters": [
            {
                "character_id": "char_a",
                "reference_image_path": str(role_a),
                "reference_image_exists": True,
                "asset_source": "user_generated_ai",
                "provenance_status": "confirmed",
                "production_ready": True,
            },
            {
                "character_id": "char_b",
                "reference_image_path": str(role_b),
                "reference_image_exists": True,
                "asset_source": "user_generated_ai",
                "provenance_status": "confirmed",
                "production_ready": True,
            },
        ],
    }


def _config(model: str = "doubao-seedance-2-0") -> GatewayVideoConfig:
    return GatewayVideoConfig(
        api_key="api-secret",
        base_url="https://gateway.example/v1",
        model=model,
    )


def _job(
    tmp_path: Path, micro_id: str, model: str = "doubao-seedance-2-0"
) -> MicroVideoJob:
    output = candidate_output_path(tmp_path, micro_id, model, 1)
    image = tmp_path / "assets/characters" / f"{micro_id}.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    return MicroVideoJob(
        micro_shot_id=micro_id,
        model=model,
        prompt="animate the confirmed character",
        images=(str(image),),
        duration=4,
        resolution="1080p",
        output_path=str(output),
        report_path=str(candidate_report_path(tmp_path, micro_id, model, 1)),
    )


def _speaking_evidence(tmp_path: Path) -> tuple[
    Episode, VisualTimeline, PerformanceSheet, DialogueAudioManifest, dict[str, str], dict[str, str]
]:
    episode = _episode()
    episode = replace(
        episode,
        shots=[
            replace(
                episode.shots[0],
                duration_seconds=3.0,
                dialogue=[DialogueLine("char_a", "Do not open it.")],
                character_ids=["char_a"],
            )
        ],
    )
    timeline = VisualTimeline(project_id=episode.project_id, micro_shots=(_micro_shot(),))
    card = PerformanceCard(
        micro_shot_id="micro_001",
        purpose="action",
        speaker_id="char_a",
        dialogue_id="shot_001.dialogue_01",
        requires_visible_lipsync=True,
        entry_anchor_id="anchor_001",
        scene_keyframe_id="gate",
        actor_id="char_a",
        target_id="envelope",
        contact_point="",
        prop_hand="",
        start_beat="turns toward the envelope",
        main_beat="speaks one short line",
        end_beat="holds the envelope in view",
        negative_constraints=("no_rain",),
    )
    audio = tmp_path / "run/dialogue_audio/shot_001.dialogue_01.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"final-dialogue-audio")
    manifest = DialogueAudioManifest(
        assets=(
            DialogueAudioAsset(
                dialogue_id=card.dialogue_id,
                speaker_id=card.speaker_id,
                path=str(audio),
                sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
                duration_seconds=1.0,
                voice_id="char-a",
            ),
        ),
        path=str(tmp_path / "run/dialogue_audio/dialogue_audio_manifest.json"),
        voiceover_audio=str(audio),
        voiceover_sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
    )
    scene = tmp_path / "run/scene_keyframes/gate.png"
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_bytes(b"\x89PNG\r\n\x1a\nscene")
    anchor = tmp_path / "run/approved_anchors/anchor_001.png"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(b"\x89PNG\r\n\x1a\nanchor")
    return (
        episode,
        timeline,
        PerformanceSheet(project_id=episode.project_id, cards=(card,)),
        manifest,
        {"gate": str(scene)},
        {"anchor_001": str(anchor)},
    )


def _action_evidence(
    run_dir: Path, episode: Episode, timeline: VisualTimeline
) -> dict[str, object]:
    cards = []
    scene_keyframes = {}
    approved_anchors = {}
    for shot in timeline.micro_shots:
        scene_id = f"scene_{shot.index:03d}"
        anchor_id = f"anchor_{shot.index:03d}"
        scene = run_dir / "scene_keyframes" / f"{scene_id}.png"
        anchor = run_dir / "approved_anchors" / f"{anchor_id}.png"
        scene.parent.mkdir(parents=True, exist_ok=True)
        anchor.parent.mkdir(parents=True, exist_ok=True)
        scene.write_bytes(b"\x89PNG\r\n\x1a\nscene")
        anchor.write_bytes(b"\x89PNG\r\n\x1a\nanchor")
        scene_keyframes[scene_id] = str(scene)
        approved_anchors[anchor_id] = str(anchor)
        cards.append(
            PerformanceCard(
                micro_shot_id=shot.id,
                purpose=shot.purpose,
                speaker_id="",
                dialogue_id="",
                requires_visible_lipsync=False,
                entry_anchor_id=anchor_id,
                scene_keyframe_id=scene_id,
                actor_id=shot.action_actor_id,
                target_id=shot.action_target,
                contact_point="",
                prop_hand="",
                start_beat="starts still",
                main_beat="performs one action",
                end_beat="ends still",
                negative_constraints=shot.negative_constraints,
            )
        )
    return {
        "performance_sheet": PerformanceSheet(
            project_id=episode.project_id, cards=tuple(cards)
        ),
        "dialogue_manifest": DialogueAudioManifest(
            assets=(),
            path=str(run_dir / "dialogue_audio_manifest.json"),
            voiceover_audio="",
            voiceover_sha256="",
        ),
        "capability_report": {},
        "scene_keyframes": scene_keyframes,
        "approved_anchors": approved_anchors,
    }


def build_micro_video_jobs(
    episode: Episode, timeline: VisualTimeline, character_assets: dict, **kwargs
):
    """Give legacy action-only tests complete local evidence by default."""
    evidence = _action_evidence(Path(kwargs["run_dir"]), episode, timeline)
    for key, value in evidence.items():
        kwargs.setdefault(key, value)
    return _build_micro_video_jobs(episode, timeline, character_assets, **kwargs)


def test_visible_speech_job_requires_audio_scene_frame_anchor_and_speaking_capability(
    tmp_path, monkeypatch
):
    """Removing the approved anchor must stop a speaking request before rendering."""
    episode, timeline, sheet, manifest, scene_keyframes, _anchors = _speaking_evidence(tmp_path)
    monkeypatch.setattr(
        "factory.model_bakeoff.require_speaking_capability", lambda *args: None
    )

    with pytest.raises(MicroVideoBatchError, match="micro_001 missing approved entry anchor"):
        build_micro_video_jobs(
            episode,
            timeline,
            _assets(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
            performance_sheet=sheet,
            dialogue_manifest=manifest,
            capability_report={},
            scene_keyframes=scene_keyframes,
            approved_anchors={},
        )


def test_visible_speech_job_passes_audio_and_image_roles_to_gateway(tmp_path, monkeypatch):
    """Dropping speech evidence during handoff would make the request unsafe to resume."""
    episode, timeline, sheet, manifest, scene_keyframes, anchors = _speaking_evidence(tmp_path)
    monkeypatch.setattr(
        "factory.model_bakeoff.require_speaking_capability", lambda *args: None
    )
    speaking_job = build_micro_video_jobs(
        episode,
        timeline,
        _assets(tmp_path),
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=1,
        performance_sheet=sheet,
        dialogue_manifest=manifest,
        capability_report={},
        scene_keyframes=scene_keyframes,
        approved_anchors=anchors,
    )[0]
    received = {}

    def fake_render(*_args, **kwargs):
        received.update(kwargs)
        return {
            "success": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "blocked_reasons": ["disabled"],
            "results": [],
            "errors": [],
        }

    monkeypatch.setattr("factory.micro_video_batch.render_gateway_video_single", fake_render)
    result = render_micro_video_batch(
        [speaking_job],
        tmp_path / "run",
        _config(),
        client_factory=lambda config: type("FakeClient", (), {"config": config})(),
    )

    assert result["jobs"][0]["reference_audio_sha256"] == speaking_job.audio_sha256
    assert result["jobs"][0]["reference_image_roles"] == [
        "last_frame",
        "first_frame",
        "reference_image",
    ]
    assert received["audio"] == speaking_job.audio_path
    assert received["image_roles"] == speaking_job.image_roles


def _speaking_job(tmp_path, monkeypatch) -> MicroVideoJob:
    episode, timeline, sheet, manifest, scene_keyframes, anchors = _speaking_evidence(
        tmp_path
    )
    monkeypatch.setattr(
        "factory.model_bakeoff.require_speaking_capability", lambda *args: None
    )
    return build_micro_video_jobs(
        episode,
        timeline,
        _assets(tmp_path),
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=1,
        performance_sheet=sheet,
        dialogue_manifest=manifest,
        capability_report={},
        scene_keyframes=scene_keyframes,
        approved_anchors=anchors,
    )[0]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda job: replace(job, audio_path="", audio_sha256=""),
            "Speaking micro-video job evidence is incomplete",
        ),
        (
            lambda job: replace(job, audio_sha256="0" * 64),
            "Speaking micro-video audio is invalid",
        ),
        (
            lambda job: replace(
                job,
                image_roles=("last_frame", "reference_image", "reference_image"),
            ),
            "speaking frame evidence",
        ),
        (
            lambda job: replace(
                job,
                image_roles=("first_frame", "reference_image", "reference_image"),
            ),
            "speaking frame evidence",
        ),
        (
            lambda job: replace(job, capability_provenance=None),
            "speaking capability provenance",
        ),
    ],
)
def test_render_rejects_forged_speaking_evidence_before_client_creation(
    tmp_path, monkeypatch, mutate, message
):
    """A direct public render call must not turn forged speaking evidence into a request."""
    speaking_job = _speaking_job(tmp_path, monkeypatch)

    with pytest.raises(MicroVideoBatchError, match=message):
        render_micro_video_batch(
            [mutate(speaking_job)],
            tmp_path / "run",
            _config(),
            client_factory=lambda _config: pytest.fail("client must not be created"),
        )


def test_build_micro_jobs_routes_only_character_shots_with_exact_ordered_references(
    tmp_path,
):
    episode = _episode()
    timeline = _timeline()
    assets = _assets(tmp_path)

    jobs = build_micro_video_jobs(
        episode,
        timeline,
        assets,
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=1,
    )

    assert [job.micro_shot_id for job in jobs] == ["micro_001"]
    assert jobs[0].images[2:] == (assets["characters"][0]["reference_image_path"],)
    assert jobs[0].image_roles == (
        "last_frame",
        "first_frame",
        "reference_image",
    )
    assert jobs[0].output_path.endswith(
        "micro_clips/micro_001/doubao-seedance-2-0/candidate_001.mp4"
    )
    assert jobs[0].report_path.endswith("candidate_001.report.json")
    assert jobs[0].duration == 4
    assert jobs[0].resolution == "1080p"
    assert jobs[0].capability == "action_only"
    assert "Su Mian" not in jobs[0].prompt


def test_build_micro_jobs_preserves_supported_seedance_source_duration(tmp_path):
    episode = _episode()
    timeline = VisualTimeline(
        project_id=episode.project_id,
        micro_shots=(
            _micro_shot(source_duration_seconds=5),
            _micro_shot(micro_id="micro_002", index=2, character_ids=()),
        ),
    )

    jobs = build_micro_video_jobs(
        episode,
        timeline,
        _assets(tmp_path),
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=1,
    )

    assert jobs[0].duration == 5


def test_build_micro_jobs_preserves_multi_character_shot_reference_order(tmp_path):
    episode = _episode()
    parent = replace(
        episode.shots[0], action="Lin Che and Su Mian reach toward the envelope."
    )
    episode = replace(episode, shots=[parent])
    first = replace(_micro_shot(character_ids=("char_b", "char_a")))
    second = _micro_shot(
        micro_id="micro_002",
        index=2,
        character_ids=(),
    )
    timeline = VisualTimeline(
        project_id=episode.project_id, micro_shots=(first, second)
    )
    assets = _assets(tmp_path)

    jobs = build_micro_video_jobs(
        episode,
        timeline,
        assets,
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=1,
    )

    assert jobs[0].images[2:] == (
        assets["characters"][1]["reference_image_path"],
        assets["characters"][0]["reference_image_path"],
    )


def test_build_micro_jobs_rejects_explicit_character_free_selection(tmp_path):
    with pytest.raises(MicroVideoBatchError, match="still route.*character reference"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
            micro_shot_ids=["micro_002"],
        )


@pytest.mark.parametrize("model", ["doubao-seedance-2-0-fast", "wan-2.1", "unknown"])
def test_build_micro_jobs_rejects_non_production_models_before_compile(
    tmp_path, monkeypatch, model
):
    monkeypatch.setattr(
        "factory.micro_video_batch.compile_video_prompt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not compile")
        ),
    )

    with pytest.raises(
        MicroVideoBatchError, match="Unsupported production video model"
    ):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path),
            model=model,
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_rejects_a_fourth_candidate(tmp_path):
    with pytest.raises(MicroVideoBatchError, match="at most 3"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=4,
        )


def test_candidate_paths_reject_traversal_and_symlink_escape(tmp_path):
    with pytest.raises(MicroVideoBatchError, match="inside run_dir/micro_clips"):
        candidate_output_path(tmp_path / "run", "../escape", "doubao-seedance-2-0", 1)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (run_dir / "micro_clips").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        MicroVideoBatchError, match="inside run_dir/micro_clips|symlink"
    ):
        candidate_report_path(run_dir, "micro_001", "doubao-seedance-2-0", 1)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda data: data.update(production_ready=False), "not production-ready"),
        (
            lambda data: data["characters"][0].update(production_ready=False),
            "production_ready",
        ),
        (
            lambda data: data["characters"][0].update(asset_source="placeholder"),
            "source",
        ),
        (
            lambda data: data["characters"][0].update(provenance_status="placeholder"),
            "provenance",
        ),
        (
            lambda data: data["characters"].append(dict(data["characters"][0])),
            "duplicate character_id",
        ),
        (
            lambda data: data["characters"][1].update(
                reference_image_path=data["characters"][0]["reference_image_path"]
            ),
            "duplicate reference image",
        ),
    ],
)
def test_build_micro_jobs_rejects_unconfirmed_or_duplicate_assets(
    tmp_path, mutate, match
):
    assets = _assets(tmp_path)
    mutate(assets)

    with pytest.raises(MicroVideoBatchError, match=match):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            assets,
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_rejects_mismatched_project_and_missing_reference(tmp_path):
    with pytest.raises(MicroVideoBatchError, match="project_id"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path, project_id="other-project"),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )

    assets = _assets(tmp_path)
    assets["characters"] = [assets["characters"][1]]
    with pytest.raises(MicroVideoBatchError, match="character IDs must exactly match"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            assets,
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_rejects_extra_manifest_character_id(tmp_path):
    assets = _assets(tmp_path)
    extra_image = tmp_path / "run/assets/characters/extra.png"
    extra_image.write_bytes(b"\x89PNG\r\n\x1a\nextra")
    assets["characters"].append(
        {
            "character_id": "char_extra",
            "reference_image_path": str(extra_image),
            "asset_source": "user_generated_ai",
            "provenance_status": "confirmed",
            "production_ready": True,
        }
    )

    with pytest.raises(MicroVideoBatchError, match="character IDs must exactly match"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            assets,
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_rejects_existing_reference_with_invalid_image_signature(
    tmp_path,
):
    assets = _assets(tmp_path)
    Path(assets["characters"][0]["reference_image_path"]).write_bytes(b"not-a-png")

    with pytest.raises(
        MicroVideoBatchError, match="supported existing reference image"
    ):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            assets,
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_fails_closed_for_invalid_timeline_duration_and_prompt(
    tmp_path, monkeypatch
):
    invalid = VisualTimeline(
        project_id="micro-project",
        micro_shots=(replace(_micro_shot(source_duration_seconds=16)),),
    )
    with pytest.raises(MicroVideoBatchError, match="Visual timeline is invalid"):
        build_micro_video_jobs(
            _episode(),
            invalid,
            _assets(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )

    monkeypatch.setattr(
        "factory.micro_video_batch.compile_video_prompt", lambda *args, **kwargs: " "
    )
    with pytest.raises(MicroVideoBatchError, match="prompt is empty"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_build_micro_jobs_resolves_continuity_before_compiling(tmp_path, monkeypatch):
    episode = _episode(continuity=True)
    timeline = _timeline(continuity=True)
    contexts = []

    def compile_spy(episode_arg, shot, *, card=None, previous_scene_context=None):
        contexts.append((shot.id, previous_scene_context))
        return compile_video_prompt(
            episode_arg,
            shot,
            card=card,
            previous_scene_context=previous_scene_context,
        )

    monkeypatch.setattr("factory.micro_video_batch.compile_video_prompt", compile_spy)
    jobs = build_micro_video_jobs(
        episode,
        timeline,
        _assets(tmp_path),
        model="doubao-seedance-2-0",
        run_dir=tmp_path / "run",
        candidate_number=2,
    )

    assert contexts == [("micro_001", None), ("micro_002", "Shop")]
    assert PREVIOUS_SHOT_CONTINUITY not in jobs[1].prompt
    assert "Scene: Shop" in jobs[1].prompt


def test_build_micro_jobs_rejects_reference_incompatible_seedance_1_5(
    tmp_path,
):
    with pytest.raises(MicroVideoBatchError, match="Unsupported production"):
        build_micro_video_jobs(
            _episode(),
            _timeline(),
            _assets(tmp_path),
            model="doubao-seedance-1-5-pro",
            run_dir=tmp_path / "run",
            candidate_number=1,
        )


def test_render_micro_video_batch_dry_run_writes_a_no_charge_plan(
    tmp_path, monkeypatch
):
    calls = []

    def fake_render(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["allow_network"] is False
        return {
            "success": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "blocked_reasons": ["Live gateway video generation is disabled."],
            "results": [],
            "errors": [],
        }

    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single", fake_render
    )
    report = render_micro_video_batch(
        [_job(tmp_path / "run", "micro_001")],
        tmp_path / "run",
        _config(),
    )

    assert len(calls) == 1
    assert report["blocked_count"] == 1
    assert report["executed"] is False
    assert report["success"] is False
    assert json.loads((tmp_path / "run/micro_video_batch.json").read_text()) == report


def test_render_micro_video_batch_uses_each_job_model_and_single_job_contract(
    tmp_path, monkeypatch
):
    built_configs = []
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

    def fake_render(prompt, output_path, client, report_path, **kwargs):
        calls.append((prompt, output_path, client.config.model, report_path, kwargs))
        return {
            "success": True,
            "completed_count": 1,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "blocked_reasons": [],
            "results": [{"status": "completed", "output_path": output_path}],
            "errors": [],
        }

    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single", fake_render
    )
    jobs = [
        _job(tmp_path / "run", "micro_001", "doubao-seedance-2-0"),
        _job(tmp_path / "run", "micro_002", "doubao-seedance-2-0"),
    ]
    report = render_micro_video_batch(
        jobs,
        tmp_path / "run",
        _config("untrusted-template-model"),
        client_factory=lambda config: (
            built_configs.append(config) or FakeClient(config)
        ),
        allow_network=True,
        overwrite=True,
    )

    assert [config.model for config in built_configs] == [job.model for job in jobs]
    assert [call[2] for call in calls] == [job.model for job in jobs]
    for index, call in enumerate(calls, start=1):
        assert call[4] == {
            "images": (
                str(tmp_path / "run/assets/characters" / f"micro_{index:03d}.png"),
            ),
            "image_roles": ("reference_image",),
            "audio": None,
            "reference_audio_sha256": "",
            "entry_anchor_id": "",
            "capability": "action_only",
            "capability_provenance_sha256": "",
            "duration": 4,
            "ratio": "9:16",
            "resolution": "1080p",
            "generate_audio": False,
            "allow_network": True,
            "overwrite": True,
            "report_sanitizer": call[4]["report_sanitizer"],
        }
        assert callable(call[4]["report_sanitizer"])
    assert report["completed_count"] == 2


@pytest.mark.parametrize("field", ["output_path", "report_path", "images"])
@pytest.mark.parametrize("attack", ["parent", "dotdot"])
@pytest.mark.parametrize("as_path", [False, True])
def test_manual_job_paths_reject_raw_symlinks_and_dotdots_before_client(
    tmp_path, field, attack, as_path
):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")
    candidate_report = Path(job.report_path)
    candidate_output = Path(job.output_path)

    if attack == "dotdot":
        original = (
            candidate_output
            if field == "output_path"
            else candidate_report
            if field == "report_path"
            else Path(job.images[0])
        )
        malicious = str(original.parent / ".." / original.parent.name / original.name)
    elif field == "images":
        target = run_dir / "assets/characters/actual.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\nactual")
        link = run_dir / "assets/characters/linked.png"
        link.symlink_to(target)
        malicious = str(link)
    else:
        target = run_dir / "safe-target"
        target.mkdir(parents=True)
        symlink_parent = candidate_output.parent.parent
        symlink_parent.parent.mkdir(parents=True, exist_ok=True)
        symlink_parent.symlink_to(target, target_is_directory=True)
        malicious = str(
            candidate_output if field == "output_path" else candidate_report
        )

    supplied_value = Path(malicious) if as_path else malicious
    mutated = replace(
        job,
        **(
            {field: supplied_value}
            if field != "images"
            else {"images": (supplied_value,)}
        ),
    )
    called = False

    def client_factory(config):
        nonlocal called
        called = True
        raise AssertionError("client factory must not run")

    with pytest.raises(MicroVideoBatchError, match="(?:symlink|canonical|path)"):
        render_micro_video_batch(
            [mutated], run_dir, _config(), client_factory=client_factory
        )

    assert called is False
    assert not candidate_output.exists()
    assert not candidate_report.exists()


def test_render_uses_verified_canonical_paths_and_keeps_model_prompt_raw(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run"
    raw_prompt = "Scene: shop Authorization: Bearer prompt-secret"
    job = replace(_job(run_dir, "micro_001"), prompt=raw_prompt)
    calls = []

    def fake_render(prompt, output_path, client, report_path, **kwargs):
        calls.append((prompt, output_path, report_path, kwargs["images"]))
        return {
            "success": False,
            "executed": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 0,
            "blocked_reasons": ["disabled"],
            "results": [],
            "errors": [],
        }

    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single", fake_render
    )
    render_micro_video_batch(
        [job],
        run_dir,
        _config(),
        client_factory=lambda config: type("Client", (), {"config": config})(),
    )

    assert calls == [
        (
            raw_prompt,
            str(candidate_output_path(run_dir, job.micro_shot_id, job.model, 1)),
            str(candidate_report_path(run_dir, job.micro_shot_id, job.model, 1)),
            (str(run_dir / "assets/characters/micro_001.png"),),
        )
    ]


@pytest.mark.parametrize("attack", ["dotdot", "symlink"])
def test_render_rejects_raw_run_dir_before_client_factory(tmp_path, attack):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")
    if attack == "dotdot":
        supplied_run_dir = f"{run_dir}/../{run_dir.name}"
    else:
        supplied_run_dir = tmp_path / "run-link"
        supplied_run_dir.symlink_to(run_dir, target_is_directory=True)

    with pytest.raises(MicroVideoBatchError, match="(?:symlink|path components)"):
        render_micro_video_batch(
            [job],
            supplied_run_dir,
            _config(),
            client_factory=lambda config: (_ for _ in ()).throw(
                AssertionError("client factory must not run")
            ),
        )


def test_manual_job_output_and_report_require_exact_absolute_paths(tmp_path):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")

    with pytest.raises(MicroVideoBatchError, match="exact absolute canonical"):
        render_micro_video_batch(
            [replace(job, output_path="candidate_001.mp4")], run_dir, _config()
        )


@pytest.mark.parametrize(
    "mutate,match",
    [
        (
            lambda job, run_dir: replace(
                job,
                output_path=str(
                    candidate_output_path(
                        run_dir,
                        "micro_other",
                        job.model,
                        1,
                    )
                ),
            ),
            "deterministic output path",
        ),
        (
            lambda job, run_dir: replace(
                job,
                output_path=str(
                    run_dir
                    / "micro_clips"
                    / job.micro_shot_id
                    / "doubao-seedance-1-5-pro"
                    / "candidate_001.mp4"
                ),
            ),
            "deterministic output path",
        ),
        (
            lambda job, run_dir: replace(
                job,
                report_path=str(
                    candidate_report_path(run_dir, job.micro_shot_id, job.model, 2)
                ),
            ),
            "deterministic report path",
        ),
        (
            lambda job, run_dir: replace(job, report_path=job.output_path),
            "must differ",
        ),
    ],
)
def test_render_micro_video_batch_rejects_noncanonical_job_paths(
    tmp_path, mutate, match
):
    run_dir = tmp_path / "run"
    job = mutate(_job(run_dir, "micro_001"), run_dir)

    with pytest.raises(MicroVideoBatchError, match=match):
        render_micro_video_batch([job], run_dir, _config())


def test_render_micro_video_batch_rejects_duplicate_jobs_and_candidate_paths(tmp_path):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")

    with pytest.raises(MicroVideoBatchError, match="(?i)duplicate micro-video job"):
        render_micro_video_batch([job, job], run_dir, _config())


def test_render_micro_video_batch_keeps_existing_skip_and_resume_unexecuted(
    tmp_path, monkeypatch
):
    responses = iter(
        [
            {
                "success": True,
                "executed": False,
                "completed_count": 0,
                "skipped_count": 1,
                "resumed_count": 0,
                "failed_count": 0,
                "blocked_reasons": [],
                "results": [{"status": "skipped_existing"}],
                "errors": [],
            },
            {
                "success": True,
                "executed": False,
                "completed_count": 1,
                "skipped_count": 0,
                "resumed_count": 1,
                "failed_count": 0,
                "blocked_reasons": [],
                "results": [{"status": "completed"}],
                "errors": [],
            },
        ]
    )
    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single",
        lambda *args, **kwargs: next(responses),
    )

    report = render_micro_video_batch(
        [_job(tmp_path / "run", "micro_001"), _job(tmp_path / "run", "micro_002")],
        tmp_path / "run",
        _config(),
        client_factory=lambda config: type("Client", (), {"config": config})(),
        allow_network=True,
    )

    assert report["success"] is True
    assert report["executed"] is False


def test_render_micro_video_batch_aggregates_resume_skip_failures_and_redacts(
    tmp_path, monkeypatch
):
    responses = iter(
        [
            {
                "success": True,
                "completed_count": 1,
                "skipped_count": 0,
                "resumed_count": 0,
                "failed_count": 0,
                "blocked_reasons": [],
                "results": [{"status": "completed"}],
                "errors": [],
            },
            {
                "success": True,
                "completed_count": 1,
                "skipped_count": 1,
                "resumed_count": 1,
                "failed_count": 0,
                "blocked_reasons": [],
                "results": [{"status": "skipped_existing"}],
                "errors": [],
            },
            {
                "success": False,
                "completed_count": 0,
                "skipped_count": 0,
                "resumed_count": 0,
                "failed_count": 1,
                "blocked_reasons": [],
                "results": [],
                "errors": [
                    {
                        "error": "Authorization: Bearer api-secret https://cdn.example/private.mp4?signature=token data:image/png;base64,QUJD"
                    }
                ],
            },
        ]
    )
    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single",
        lambda *args, **kwargs: next(responses),
    )

    report = render_micro_video_batch(
        [_job(tmp_path / "run", f"micro_{index:03d}") for index in range(1, 4)],
        tmp_path / "run",
        _config(),
        client_factory=lambda config: type("Client", (), {"config": config})(),
        allow_network=True,
    )

    report_text = json.dumps(report)
    assert report["planned_count"] == 3
    assert report["completed_count"] == 2
    assert report["resumed_count"] == 1
    assert report["skipped_count"] == 1
    assert report["failed_count"] == 1
    assert report["success"] is False
    assert "api-secret" not in report_text
    assert "Authorization: Bearer" not in report_text
    assert "https://cdn.example/private.mp4" not in report_text
    assert "data:image/png;base64" not in report_text
    assert list((tmp_path / "run").glob(".micro_video_batch.json.*.tmp")) == []


def test_render_micro_video_batch_redacts_return_and_disk_report_everywhere(
    tmp_path, monkeypatch
):
    secret_prompt = (
        "api-secret Authorization: Bearer bearer-secret X-Api-Key: x-api-secret "
        "Api-Key=generic-api-secret https://cdn.example/private.mp4?signature=token "
        "data:image/gif;base64,R0lGODlh data:image/svg+xml;base64,PHN2Zz4="
    )
    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single",
        lambda *args, **kwargs: {
            "success": False,
            "executed": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 1,
            "blocked_reasons": [],
            "results": [
                {
                    "Authorization": "bearer-secret",
                    "X-Api-Key": "x-api-secret",
                    "remote": "https://cdn.example/private.mp4?signature=token",
                }
            ],
            "errors": [{"error": secret_prompt}],
        },
    )
    job = replace(_job(tmp_path / "run", "micro_001"), prompt=secret_prompt)

    returned = render_micro_video_batch(
        [job],
        tmp_path / "run",
        _config(),
        client_factory=lambda config: type("Client", (), {"config": config})(),
        allow_network=True,
    )
    saved = json.loads((tmp_path / "run/micro_video_batch.json").read_text())

    for report in (returned, saved):
        text = json.dumps(report)
        for forbidden in (
            "api-secret",
            "bearer-secret",
            "x-api-secret",
            "generic-api-secret",
            "https://cdn.example/private.mp4",
            "data:image/gif",
            "data:image/svg+xml",
        ):
            assert forbidden not in text
    assert returned == saved


@pytest.mark.parametrize("allow_network", [False, True])
def test_candidate_report_is_sanitized_without_changing_model_prompt(
    tmp_path, allow_network
):
    run_dir = tmp_path / "run"
    prompt = (
        "Scene: shop Authorization: 'Bearer quoted-token' "
        "X-Api-Key=\"x-api-secret\" gateway_api_key: 'gateway-secret' "
        "data:,bare-payload mailto:private@example.test "
        "s3://access:secret@bucket/private ftp://user:password@example.test/file"
    )
    job = replace(_job(run_dir, "micro_001"), prompt=prompt)
    submitted_prompts = []

    class FakeClient:
        config = _config()

        def validate_reference_images(self, images):
            return None

        def prepare_submission(self, model_prompt, **kwargs):
            submitted_prompts.append(model_prompt)
            raise GatewayVideoError(
                'Authorization: "Bearer submit-secret" '
                "https://example.test/private?token=result-secret"
            )

    report = render_micro_video_batch(
        [job],
        run_dir,
        _config(),
        client_factory=lambda config: FakeClient(),
        allow_network=allow_network,
    )
    candidate = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
    candidate_text = json.dumps(candidate)

    assert candidate == report["results"][0]["result"]
    assert "Scene: shop" in candidate_text
    for forbidden in (
        "quoted-token",
        "x-api-secret",
        "gateway-secret",
        "bare-payload",
        "private@example.test",
        "access:secret",
        "user:password",
        "submit-secret",
        "result-secret",
    ):
        assert forbidden not in candidate_text
    if allow_network:
        assert submitted_prompts == [prompt]
    else:
        assert submitted_prompts == []


@pytest.mark.parametrize(
    "credential",
    [
        "bare",
        '"quoted"',
        "'quoted'",
        "<angle>",
        "[square]",
        "(round)",
        "{curly}",
        "`backtick`",
        "trailing,",
    ],
)
def test_no_network_reports_redact_standalone_bearer_token_end_to_end(
    tmp_path, credential
):
    run_dir = tmp_path / "run"
    prompt = f"Scene: shop. Bearer {credential} The bearer."
    job = replace(_job(run_dir, "micro_001"), prompt=prompt)

    returned = render_micro_video_batch([job], run_dir, _config())
    candidate = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
    aggregate = json.loads((run_dir / "micro_video_batch.json").read_text())

    for report in (returned, candidate, aggregate):
        text = json.dumps(report)
        assert credential not in text
        assert "Scene: shop." in text
        assert "The bearer." in text


@pytest.mark.parametrize(
    "credential",
    [
        "bare",
        '"quoted"',
        "'quoted'",
        "<angle>",
        "[square]",
        "(round)",
        "{curly}",
        "`backtick`",
        "trailing,",
    ],
)
def test_live_error_reports_redact_standalone_bearer_but_keep_raw_prompt_and_signature(
    tmp_path, credential
):
    run_dir = tmp_path / "run"
    prompt = f"Scene: shop. Bearer {credential} The bearer."
    job = replace(_job(run_dir, "micro_001"), prompt=prompt)
    submitted_prompts = []

    class FakeClient:
        config = _config()

        def validate_reference_images(self, images):
            return None

        def prepare_submission(self, model_prompt, **kwargs):
            submitted_prompts.append(model_prompt)
            return object()

        def submit_prepared(self, submission, **kwargs):
            return GatewayVideoTask("task-1", "queued", "")

        def complete_task(self, task, output_path, **kwargs):
            raise GatewayVideoError("Live completion failed.")

    returned = render_micro_video_batch(
        [job],
        run_dir,
        _config(),
        client_factory=lambda config: FakeClient(),
        allow_network=True,
    )
    candidate = json.loads(Path(job.report_path).read_text(encoding="utf-8"))
    aggregate = json.loads((run_dir / "micro_video_batch.json").read_text())
    state = json.loads(Path(f"{job.output_path}.gateway.json").read_text())
    expected_signature = _job_signature(
        GatewayVideoJob(
            shot_id="single",
            index=1,
            prompt=prompt,
            images=job.images,
            duration=job.duration,
            ratio="9:16",
            resolution=job.resolution,
            output_path=job.output_path,
        ),
        model=job.model,
        generate_audio=False,
    )

    assert submitted_prompts == [prompt]
    assert state["signature"] == expected_signature
    for report in (returned, candidate, aggregate):
        text = json.dumps(report)
        assert credential not in text
        assert "Scene: shop." in text
        assert "The bearer." in text


def test_candidate_report_never_copies_resumable_state_download_url(tmp_path):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")
    remote_url = "s3://access:resume-secret@bucket/private.mp4"

    class FakeClient:
        config = _config()

        def validate_reference_images(self, images):
            return None

        def prepare_submission(self, prompt, **kwargs):
            return object()

        def submit_prepared(self, submission, **kwargs):
            return GatewayVideoTask("task-1", "queued", remote_url)

        def complete_task(self, task, output_path, **kwargs):
            raise GatewayVideoError("Simulated completion failure.")

    render_micro_video_batch(
        [job],
        run_dir,
        _config(),
        client_factory=lambda config: FakeClient(),
        allow_network=True,
    )

    state_text = Path(f"{job.output_path}.gateway.json").read_text(encoding="utf-8")
    candidate_text = Path(job.report_path).read_text(encoding="utf-8")
    assert "resume-secret" not in state_text
    assert remote_url not in state_text
    assert "resume-secret" not in candidate_text
    assert remote_url not in candidate_text


@pytest.mark.parametrize(
    "value",
    [
        "data:,bare-payload",
        "DATA:image/gif;base64,R0lGODlh",
        "ftp://user:password@example.test/private.mp4",
        "s3://access:secret@bucket/private.mp4",
        "https://example.test/private.mp4?signature=token",
    ],
)
def test_reference_labels_never_expose_remote_or_data_payloads(value):
    assert _reference_label(value) == "[remote-url]"


def test_report_redaction_covers_sensitive_key_labels_and_uri_schemes(
    tmp_path, monkeypatch
):
    secret_text = (
        "gateway_api_key=gw-secret provider-api-key: provider-secret "
        "authorization_token=auth-secret token=token-secret credential: cred-secret "
        "data:,bare-payload DATA:image/gif;base64,R0lGODlh "
        "ftp://user:password@example.test/private s3://key:secret@bucket/private"
    )
    monkeypatch.setattr(
        "factory.micro_video_batch.render_gateway_video_single",
        lambda *args, **kwargs: {
            "success": False,
            "executed": False,
            "completed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "failed_count": 1,
            "blocked_reasons": [],
            "results": [{"gateway_api_key": "gw-secret"}],
            "errors": [{"error": secret_text}],
        },
    )
    job = replace(_job(tmp_path / "run", "micro_001"), prompt=secret_text)

    report = render_micro_video_batch(
        [job],
        tmp_path / "run",
        _config(),
        client_factory=lambda config: type("Client", (), {"config": config})(),
        allow_network=True,
    )

    report_text = json.dumps(report)
    for forbidden in (
        "gw-secret",
        "provider-secret",
        "auth-secret",
        "token-secret",
        "cred-secret",
        "bare-payload",
        "R0lGODlh",
        "user:password",
        "key:secret",
    ):
        assert forbidden not in report_text


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", 1),
        ("duration", True),
        ("duration", "3"),
        ("duration", 3),
        ("output_path", 1),
        ("report_path", None),
        ("images", "not-a-list"),
    ],
)
def test_manual_jobs_reject_runtime_types_before_client_factory(tmp_path, field, value):
    called = False

    def client_factory(config):
        nonlocal called
        called = True
        raise AssertionError("client factory must not run")

    with pytest.raises(MicroVideoBatchError):
        render_micro_video_batch(
            [replace(_job(tmp_path / "run", "micro_001"), **{field: value})],
            tmp_path / "run",
            _config(),
            client_factory=client_factory,
        )
    assert called is False


def test_manual_jobs_reject_untrusted_image_inputs_before_client_factory(
    tmp_path,
):
    run_dir = tmp_path / "run"
    valid = _job(run_dir, "micro_001")
    bad_signature = run_dir / "assets/characters/bad.png"
    bad_signature.write_bytes(b"not-a-png")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\noutside")
    invalid_images = [
        (),
        (valid.images[0], valid.images[0]),
        ("https://example.test/role.png",),
        ("ftp://example.test/role.png",),
        ("s3://bucket/role.png",),
        ("data:,bare",),
        (str(run_dir / "assets/characters/missing.png"),),
        (str(bad_signature),),
        (str(outside),),
        (123,),
    ]

    for images in invalid_images:
        with pytest.raises(MicroVideoBatchError):
            render_micro_video_batch(
                [replace(valid, images=images)],
                run_dir,
                _config(),
                client_factory=lambda config: (_ for _ in ()).throw(
                    AssertionError("client factory must not run")
                ),
            )


def test_manual_jobs_reject_existing_destination_symlinks(tmp_path):
    run_dir = tmp_path / "run"
    job = _job(run_dir, "micro_001")
    report = Path(job.report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    Path(job.output_path).write_bytes(b"target")
    report.symlink_to(Path(job.output_path))

    with pytest.raises(MicroVideoBatchError, match="symlink"):
        render_micro_video_batch([job], run_dir, _config())
