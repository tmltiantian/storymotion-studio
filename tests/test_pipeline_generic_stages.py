from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.media_validation import MediaProbeResult
from factory import pipeline_generic_stages
from factory.pipeline_artifacts import load_stage_manifest, manifest_artifact_paths
from factory.pipeline_contracts import ProjectMode, ProjectSpec, StageName, StageState
from factory.pipeline_runner import run_pipeline
from factory.pipeline_store import approve_stage, create_project, load_production_package


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

    def fake_mux(video, audio, output, subtitles=None):
        Path(output).write_bytes(b"final")
        return Path(output)

    monkeypatch.setattr(pipeline_generic_stages, "render_placeholder_video", fake_placeholder)
    monkeypatch.setattr(pipeline_generic_stages, "render_voiceover_preview", fake_voiceover)
    monkeypatch.setattr(pipeline_generic_stages, "render_card_preview_video", fake_video)
    monkeypatch.setattr(pipeline_generic_stages, "_mux_audio", fake_mux)
    monkeypatch.setattr(
        pipeline_generic_stages,
        "probe_media",
        lambda path, **kwargs: MediaProbeResult(Path(path), True, 42.0, 1, 1),
    )

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
    assert (root / "stages/deliver/delivery_manifest.json").is_file()
