from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.media_validation import MediaProbeResult
from factory import pipeline_generic_stages
from factory.pipeline_artifacts import load_stage_manifest, manifest_artifact_paths
from factory.pipeline_contracts import (
    ProjectMode,
    ProjectSpec,
    ReviewState,
    StageName,
    StageState,
)
from factory.pipeline_context import StageContext, StageExecution
from factory.pipeline_modes import get_mode_adapter
from factory.pipeline_runner import run_pipeline
from factory.pipeline_review import ApprovalPreset, resolve_review_config
from factory.pipeline_store import (
    approve_stage,
    create_project,
    load_production_package,
    load_project_spec,
)
from factory.schema import Episode, Shot


def _project(tmp_path: Path, mode: ProjectMode) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "runs" / mode.value
    if mode is ProjectMode.ORIGINAL:
        source = tmp_path / "idea.txt"
        source.write_text("两只猫调查会发出声音的纸盒", encoding="utf-8")
        input_value = {
            "kind": "idea",
            "text": "两只猫调查会发出声音的纸盒",
            "path": str(source),
        }
    else:
        source = tmp_path / "novel.txt"
        source.write_text(
            "苏眠推开旧仓库的门。林澈提醒她不要碰桌上的盒子。",
            encoding="utf-8",
        )
        input_value = {"kind": "novel", "path": str(source)}
    create_project(
        root,
        ProjectSpec(
            project_id=mode.value,
            title="Test",
            mode=mode,
            input=input_value,
            output_dir=(tmp_path / "output" / mode.value).resolve(),
            target={"shots": 4, "fps": 30},
        ),
    )
    return root


@pytest.mark.parametrize("mode", (ProjectMode.ORIGINAL, ProjectMode.NOVEL))
def test_native_pipeline_through_storyboard_writes_three_isolated_stages(
    tmp_path: Path, mode: ProjectMode
) -> None:
    root = _project(tmp_path, mode)

    result = run_pipeline(root, through=StageName.STORYBOARD)

    assert result.success is True
    assert result.next_stage is StageName.ASSETS
    assert (root / "stages/concept/concept.json").is_file()
    assert (root / "stages/script/script.json").is_file()
    assert (root / "stages/storyboard/episode.json").is_file()
    assert not (root / "stages/script/episode.json").exists()
    assert not (root / "stages/storyboard/character_assets.json").exists()
    assert not (root / "stages/audio").exists()


def test_original_and_novel_script_modes_preserve_different_source_semantics(
    tmp_path: Path,
) -> None:
    original = _project(tmp_path / "original-case", ProjectMode.ORIGINAL)
    novel = _project(tmp_path / "novel-case", ProjectMode.NOVEL)

    run_pipeline(original, through=StageName.SCRIPT)
    run_pipeline(novel, through=StageName.SCRIPT)
    original_script = json.loads(
        (original / "stages/script/script.json").read_text(encoding="utf-8")
    )
    novel_script = json.loads(
        (novel / "stages/script/script.json").read_text(encoding="utf-8")
    )

    assert original_script["adaptation_mode"] == "original"
    assert novel_script["adaptation_mode"] == "novel"
    assert original_script["source_kind"] == "idea"
    assert novel_script["source_kind"] == "novel"
    assert original_script["episode_draft"]["characters"] != novel_script[
        "episode_draft"
    ]["characters"]


def test_assets_stage_prepares_only_asset_artifacts_then_waits_for_review(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, ProjectMode.ORIGINAL)

    result = run_pipeline(root, through=StageName.ASSETS)
    record = load_production_package(root).stages[3]
    artifacts = manifest_artifact_paths(root, StageName.ASSETS)

    assert result.success is False
    assert result.stopped_state is StageState.BLOCKED
    assert {path.name for path in artifacts} == {
        "character_assets.json",
        "asset_review.json",
    }
    review = json.loads(
        (root / "stages/assets/asset_review.json").read_text(encoding="utf-8")
    )
    assert review["approved"] is False
    assert record.blocked_reasons


def test_stage_manifests_identify_native_executor_and_own_artifacts(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, ProjectMode.NOVEL)
    run_pipeline(root, through=StageName.STORYBOARD)

    script = load_stage_manifest(root, StageName.SCRIPT)
    storyboard = load_stage_manifest(root, StageName.STORYBOARD)

    assert script["executor_id"] == "novel.script"
    assert storyboard["executor_id"] == "generic.storyboard"
    assert script["artifacts"] == ["script.json"]
    assert set(storyboard["artifacts"]) == {
        "episode.json",
        "storyboard.md",
        "subtitle_draft.srt",
    }


def test_native_media_stages_keep_outputs_isolated_and_stop_at_review_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, ProjectMode.ORIGINAL)
    first = run_pipeline(root, through=StageName.ASSETS)
    assert first.stopped_at is StageName.ASSETS
    asset_review = root / "stages/assets/asset_review.json"
    approve_stage(
        root,
        StageName.ASSETS,
        note="角色、场景和道具锚点已检查",
        evidence=(asset_review,),
    )

    def fake_placeholder(episode, subtitles, output, **kwargs):
        path = Path(output)
        path.write_bytes(b"placeholder")
        return path

    def fake_voiceover(episode, source, output, work, **kwargs):
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        audio = work / "voiceover.m4a"
        script = work / "voiceover_script.txt"
        report = work / "voiceover_provider_report.json"
        audio.write_bytes(b"audio")
        script.write_text("voice", encoding="utf-8")
        report.write_text('{"provider":"fixture"}', encoding="utf-8")
        Path(output).write_bytes(b"preview")
        return {
            "voiceover_audio": audio,
            "voiceover_script": script,
            "voiceover_provider_report": report,
            "voiceover_timings": [],
        }

    def fake_video(episode, subtitles, output, cards, **kwargs):
        Path(output).write_bytes(b"visual")
        return Path(output)

    def fake_mux(video, audio, output, subtitles=None, **kwargs):
        Path(output).write_bytes(b"final")
        return Path(output)

    monkeypatch.setattr(pipeline_generic_stages, "render_placeholder_video", fake_placeholder)
    monkeypatch.setattr(pipeline_generic_stages, "render_voiceover_preview", fake_voiceover)
    monkeypatch.setattr(pipeline_generic_stages, "render_card_preview_video", fake_video)
    monkeypatch.setattr(pipeline_generic_stages, "mux_final_audio", fake_mux)
    def fake_probe(path, **kwargs):
        manifest = json.loads(
            (Path(path).parent / "edit_manifest.json").read_text(encoding="utf-8")
        )
        return MediaProbeResult(
            Path(path), True, float(manifest["duration_seconds"]), 1, 1
        )

    monkeypatch.setattr(pipeline_generic_stages, "probe_media", fake_probe)

    result = run_pipeline(root, through=StageName.EVAL)

    assert result.stopped_at is StageName.EVAL
    assert result.stopped_state is StageState.BLOCKED
    for stage in (StageName.AUDIO, StageName.VIDEO, StageName.EDIT, StageName.EVAL):
        manifest = load_stage_manifest(root, stage)
        assert manifest["stage"] == stage.value
        assert all(
            path.is_relative_to(root / "stages" / stage.value)
            for path in manifest_artifact_paths(root, stage)
        )

    eval_report = root / "stages/eval/eval_result.json"
    approve_stage(
        root,
        StageName.EVAL,
        note="技术检查和逐镜人工检查通过",
        evidence=(eval_report,),
    )
    delivery = run_pipeline(root, through=StageName.DELIVER)

    assert delivery.stopped_at is StageName.DELIVER
    assert delivery.stopped_state is StageState.BLOCKED
    assert (root / "stages/deliver/master.mp4").read_bytes() == b"final"
    delivery_manifest = json.loads(
        (root / "stages/deliver/delivery_manifest.json").read_text(encoding="utf-8")
    )
    assert delivery_manifest["eval_evidence"]["revision"] == 1
    report_evidence = next(
        item
        for item in delivery_manifest["eval_evidence"]["reports"]
        if item["path"] == "stages/eval/eval_result.json"
    )
    assert (
        report_evidence["sha256"]
        == hashlib.sha256(eval_report.read_bytes()).hexdigest()
    )


def test_quick_preset_auto_eval_reaches_generic_delivery_without_review_file(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, ProjectMode.ORIGINAL)
    review_config = resolve_review_config(ApprovalPreset.QUICK, {})

    def execute(context: StageContext) -> StageExecution:
        if context.stage is StageName.EDIT:
            preview = context.stage_dir / "final_preview.mp4"
            preview.write_bytes(b"quick-final")
            manifest = context.stage_dir / "edit_manifest.json"
            manifest.write_text(
                json.dumps({"final_preview": str(preview)}), encoding="utf-8"
            )
            return StageExecution.passed(
                executor=context.step.executor_id,
                artifacts=(manifest, preview),
            )
        if context.stage is StageName.EVAL:
            report = context.stage_dir / "eval_result.json"
            report.write_text('{"automatic_passed":true}', encoding="utf-8")
            return StageExecution.passed(
                executor=context.step.executor_id,
                artifacts=(report,),
            )
        if context.stage is StageName.DELIVER:
            return pipeline_generic_stages.execute_deliver(context)
        artifact = context.stage_dir / f"{context.stage.value}.json"
        artifact.write_text("{}", encoding="utf-8")
        return StageExecution.passed(
            executor=context.step.executor_id,
            artifacts=(artifact,),
        )

    storyboard_wait = run_pipeline(
        root,
        through=StageName.STORYBOARD,
        executor=execute,
        review_config=review_config,
    )
    assert storyboard_wait.stopped_at is StageName.STORYBOARD
    approve_stage(
        root,
        StageName.STORYBOARD,
        note="Quick storyboard approved",
        evidence=(root / "stages/storyboard/storyboard.json",),
    )
    video_wait = run_pipeline(
        root,
        through=StageName.VIDEO,
        executor=execute,
        review_config=review_config,
    )
    assert video_wait.stopped_at is StageName.VIDEO
    approve_stage(
        root,
        StageName.VIDEO,
        note="Quick video approved",
        evidence=(root / "stages/video/video.json",),
    )

    delivery = run_pipeline(
        root,
        through=StageName.DELIVER,
        executor=execute,
        review_config=review_config,
    )
    package = load_production_package(root)
    eval_record = next(item for item in package.stages if item.stage is StageName.EVAL)
    manifest = json.loads(
        (root / "stages/deliver/delivery_manifest.json").read_text(encoding="utf-8")
    )

    assert delivery.stopped_at is StageName.DELIVER
    assert eval_record.review_state is ReviewState.AUTO_APPROVED
    assert not (root / "reviews/eval.review.json").exists()
    assert manifest["eval_evidence"]["policy"] == "automatic"
    assert manifest["eval_evidence"]["state"] == "auto_approved"


def test_edit_assembles_preserved_and_repaired_clips_in_storyboard_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, ProjectMode.ORIGINAL)
    clips = {
        shot_id: root / "stages" / "video" / f"{shot_id}.mp4"
        for shot_id in ("shot_01", "shot_02", "shot_03")
    }
    for path in clips.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode("ascii"))
    audio = root / "stages" / "audio" / "voice.m4a"
    subtitles = root / "stages" / "audio" / "subtitles.srt"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    subtitles.write_text("", encoding="utf-8")
    episode = Episode(
        project_id="original",
        title="Test",
        language="zh-CN",
        style="comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[],
        shots=[
            Shot(
                id=shot_id,
                index=index,
                scene_title="scene",
                action="action",
                visual_prompt="prompt",
                camera="wide",
                duration_seconds=float(index),
                audio_mood="quiet",
            )
            for index, shot_id in enumerate(clips, start=1)
        ],
    )
    assembled: list[Path] = []

    def fake_assemble(ordered_clips, durations, output, **kwargs):
        assembled.extend(ordered_clips)
        Path(output).write_bytes(b"assembled")
        return Path(output)

    def fake_mux(video, voiceover, output, **kwargs):
        Path(output).write_bytes(b"final")
        return Path(output)

    monkeypatch.setattr(pipeline_generic_stages, "_episode", lambda context: episode)
    monkeypatch.setattr(
        pipeline_generic_stages,
        "_video_manifest",
        lambda context: {
            "primary_video": "",
            "clips": [str(path) for path in reversed(tuple(clips.values()))],
            "clip_by_shot": {
                shot_id: str(path) for shot_id, path in clips.items()
            },
        },
    )
    monkeypatch.setattr(
        pipeline_generic_stages,
        "_audio_manifest",
        lambda context: {
            "voiceover_audio": str(audio),
            "subtitles": str(subtitles),
        },
    )
    monkeypatch.setattr(pipeline_generic_stages, "assemble_visual_track", fake_assemble)
    monkeypatch.setattr(pipeline_generic_stages, "mux_final_audio", fake_mux)
    adapter = get_mode_adapter(ProjectMode.ORIGINAL)
    context = StageContext(
        root,
        load_project_spec(root),
        StageName.EDIT,
        adapter.stage_steps[StageName.EDIT],
        False,
        repair_scope={StageName.VIDEO.value: ("shot_03",)},
    )

    pipeline_generic_stages.execute_edit(context)

    assert assembled == [clips["shot_01"], clips["shot_02"], clips["shot_03"]]


def test_live_video_repair_enables_signature_reuse_and_registers_every_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, ProjectMode.ORIGINAL)
    adapter = get_mode_adapter(ProjectMode.ORIGINAL)
    context = StageContext(
        root,
        load_project_spec(root),
        StageName.VIDEO,
        adapter.stage_steps[StageName.VIDEO],
        True,
        repair_scope={StageName.VIDEO.value: ("shot_02",)},
    )
    episode = SimpleNamespace(
        shots=[
            SimpleNamespace(id="shot_01"),
            SimpleNamespace(id="shot_02"),
        ]
    )
    audio = context.project_dir / "audio.m4a"
    audio.write_bytes(b"audio")
    handoff = context.stage_dir / "handoff.json"
    package = context.stage_dir / "package.json"
    clips = [context.stage_dir / "shot_01.mp4", context.stage_dir / "shot_02.mp4"]
    stale_local_preview = context.stage_dir / "visual_preview.mp4"
    stale_local_preview.write_bytes(b"stale-local-preview")
    observed: dict[str, object] = {}

    monkeypatch.setattr(pipeline_generic_stages, "_episode", lambda current: episode)
    monkeypatch.setattr(
        pipeline_generic_stages,
        "_audio_manifest",
        lambda current: {"voiceover_audio": str(audio)},
    )
    monkeypatch.setattr(pipeline_generic_stages, "_assets", lambda current: {})
    monkeypatch.setattr(pipeline_generic_stages, "_factory_config", lambda current: {})
    monkeypatch.setattr(
        pipeline_generic_stages,
        "resolve_provider_profile",
        lambda config: SimpleNamespace(
            video=SimpleNamespace(
                provider="gateway", model="video-model", ready=True, blockers=()
            )
        ),
    )
    monkeypatch.setattr(
        pipeline_generic_stages,
        "write_video_handoff",
        lambda *args, **kwargs: handoff,
    )
    monkeypatch.setattr(
        pipeline_generic_stages,
        "write_shot_audio_assets",
        lambda *args, **kwargs: {},
    )

    def fake_package(*args, **kwargs):
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text(
            json.dumps(
                {
                    "timeline": [
                        {
                            "shot_id": shot_id,
                            "expected_assets": {"video_clip": str(clip)},
                        }
                        for shot_id, clip in zip(
                            ("shot_01", "shot_02"), clips, strict=True
                        )
                    ]
                }
            ),
            encoding="utf-8",
        )
        return package

    def fake_batch(*args, **kwargs):
        observed.update(kwargs)
        for clip in clips:
            clip.write_bytes(clip.name.encode("ascii"))
            Path(f"{clip}.gateway.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )
        return {"success": True}

    monkeypatch.setattr(
        pipeline_generic_stages, "write_openmontage_package", fake_package
    )
    monkeypatch.setattr(
        pipeline_generic_stages, "build_video_client", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        pipeline_generic_stages, "render_gateway_video_batch", fake_batch
    )

    execution = pipeline_generic_stages.execute_video(context)
    manifest = json.loads(
        (context.stage_dir / "video_manifest.json").read_text(encoding="utf-8")
    )

    assert observed["replace_stale"] is True
    assert observed["repair_shot_ids"] == ("shot_02",)
    assert manifest["primary_video"] == ""
    assert manifest["clip_by_shot"] == {
        "shot_01": str(clips[0].resolve()),
        "shot_02": str(clips[1].resolve()),
    }
    assert all(str(clip) in execution.artifacts for clip in clips)
    assert all(
        str(Path(f"{clip}.gateway.json")) in execution.artifacts for clip in clips
    )
