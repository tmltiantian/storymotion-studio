import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

import factory.preview_refresh as preview_refresh
from factory.novel_planner import plan_episode
from factory.preview_refresh import (
    PreviewRefreshError,
    refresh_project_preview,
    run_voiceover_mux,
)
from factory.schema import episode_to_dict
from tests.media_fixtures import VALID_VIDEO_MP4


MINIMAL_MP4 = VALID_VIDEO_MP4


def test_voiceover_mux_wraps_ffmpeg_failure():
    def failing_runner(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="mux exploded")

    with pytest.raises(PreviewRefreshError, match="Voiceover mux failed: mux exploded"):
        run_voiceover_mux(["ffmpeg", "output.mp4"], command_runner=failing_runner)


def test_voiceover_mux_failure_preserves_previous_output(tmp_path):
    output = tmp_path / "voiced.mp4"
    output.write_bytes(b"last-good")

    def failing_runner(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, command, stderr="mux exploded")

    with pytest.raises(PreviewRefreshError, match="mux exploded"):
        run_voiceover_mux(
            ["ffmpeg", str(output)],
            command_runner=failing_runner,
        )

    assert output.read_bytes() == b"last-good"


def test_refresh_project_preview_reuses_existing_voiceover_and_updates_status(tmp_path):
    run_dir = tmp_path / "runs" / "sample"
    output_dir = tmp_path / "output"
    run_dir.mkdir(parents=True)
    episode = plan_episode(
        "林澈推开门。苏眠低声说，别急。",
        "sample",
        target_shots=2,
    )
    (run_dir / "episode.json").write_text(
        json.dumps(episode_to_dict(episode)),
        encoding="utf-8",
    )
    (run_dir / "openmontage_package.json").write_text(
        json.dumps({"target": {"resolution": "1080x1920", "fps": 30}, "timeline": []}),
        encoding="utf-8",
    )
    (run_dir / "character_assets.json").write_text(
        json.dumps({"production_ready": False, "characters": []}),
        encoding="utf-8",
    )
    (run_dir / "subtitles.srt").write_text("", encoding="utf-8")
    voiceover = run_dir / "voiceover" / "voiceover.m4a"
    voiceover.parent.mkdir()
    voiceover.write_bytes(b"existing-voiceover")
    (run_dir / "status.json").write_text(
        json.dumps({"project_id": "sample", "stage": "planned"}),
        encoding="utf-8",
    )
    calls = []

    def fake_card_renderer(episode_arg, output_dir_arg, character_assets=None):
        cards = []
        for shot in episode_arg.shots:
            card = Path(output_dir_arg) / f"{shot.id}.png"
            card.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1080, 1920), "navy").save(card)
            cards.append(card)
        return cards

    def fake_hybrid_renderer(episode_arg, **kwargs):
        calls.append(("hybrid", kwargs["package_path"]))
        Path(kwargs["output_path"]).write_bytes(MINIMAL_MP4)
        report = {
            "success": True,
            "output_path": str(kwargs["output_path"]),
            "dynamic_shot_count": 1,
            "fallback_shot_count": 1,
            "shot_count": 2,
            "shots": [],
        }
        Path(kwargs["report_path"]).write_text(json.dumps(report), encoding="utf-8")
        return report

    def fake_runner(command, **kwargs):
        calls.append(("mux", command))
        Path(command[-1]).write_bytes(MINIMAL_MP4)

    def fake_finalizer(**kwargs):
        calls.append(("post", kwargs["source_video_path"]))
        Path(kwargs["output_video_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_video_path"]).write_bytes(MINIMAL_MP4)
        Path(kwargs["report_path"]).write_text("{}", encoding="utf-8")
        return Path(kwargs["report_path"])

    report = refresh_project_preview(
        {"runsDir": str(tmp_path / "runs"), "outputDir": str(output_dir)},
        "sample",
        card_renderer=fake_card_renderer,
        hybrid_renderer=fake_hybrid_renderer,
        command_runner=fake_runner,
        post_finalizer=fake_finalizer,
    )

    assert report["success"] is True
    assert report["voiceover_reused"] is True
    assert report["dynamic_shot_count"] == 1
    assert voiceover.read_bytes() == b"existing-voiceover"
    assert [call[0] for call in calls] == ["hybrid", "mux", "post"]
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["hybrid_preview_video"] == str(run_dir / "hybrid_preview.mp4")
    assert status["dynamic_shot_count"] == 1
    assert Path(report["final_preview_video"]).is_file()


def _write_quality_refresh_inputs(tmp_path: Path) -> tuple[Path, Path, object]:
    run_dir = tmp_path / "runs/sample"
    output_dir = tmp_path / "output"
    run_dir.mkdir(parents=True)
    episode = plan_episode("林澈推开门。", "sample", target_shots=1)
    (run_dir / "episode.json").write_text(
        json.dumps(episode_to_dict(episode)), encoding="utf-8"
    )
    (run_dir / "openmontage_package.json").write_text(
        json.dumps({"target": {"resolution": "1080x1920", "fps": 30}, "timeline": []}),
        encoding="utf-8",
    )
    (run_dir / "subtitles.srt").write_text("", encoding="utf-8")
    voiceover = run_dir / "voiceover/voiceover.m4a"
    voiceover.parent.mkdir()
    voiceover.write_bytes(b"existing-voiceover")
    return run_dir, output_dir, episode


@pytest.mark.parametrize(
    "names",
    [
        ["visual_timeline.json"],
        ["visual_selection.json", "model_bakeoff_report.json"],
    ],
)
def test_refresh_project_preview_rejects_partial_quality_artifacts(tmp_path, names):
    run_dir, output_dir, _ = _write_quality_refresh_inputs(tmp_path)
    for name in names:
        (run_dir / name).write_text("{}", encoding="utf-8")

    with pytest.raises(PreviewRefreshError, match="incomplete quality path"):
        refresh_project_preview(
            {"runsDir": str(tmp_path / "runs"), "outputDir": str(output_dir)},
            "sample",
        )


def test_refresh_project_preview_uses_complete_quality_path_without_rendering_cards(
    tmp_path, monkeypatch
):
    run_dir, output_dir, _ = _write_quality_refresh_inputs(tmp_path)
    for name in ("visual_timeline.json", "visual_selection.json"):
        (run_dir / name).write_text("{}", encoding="utf-8")
    bakeoff = {"selected_model": "approved-model"}
    (run_dir / "model_bakeoff_report.json").write_text(
        json.dumps(bakeoff), encoding="utf-8"
    )
    (run_dir / "character_assets.json").write_text("not-json", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        preview_refresh,
        "require_selected_production_model",
        lambda report: calls.append(("gate", report)) or "approved-model",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy renderer must not run")

    def fake_micro_renderer(episode_arg, **kwargs):
        calls.append(("micro", kwargs))
        Path(kwargs["output_path"]).write_bytes(MINIMAL_MP4)
        report = {
            "success": True,
            "micro_shot_count": 3,
            "duration_seconds": 9.0,
            "sources": [{"kind": "video"}, {"kind": "still"}, {"kind": "video"}],
        }
        Path(kwargs["report_path"]).write_text(json.dumps(report), encoding="utf-8")
        return report

    def fake_runner(command, **kwargs):
        calls.append(("mux", command))
        Path(command[-1]).write_bytes(MINIMAL_MP4)

    def fake_finalizer(**kwargs):
        calls.append(("post", kwargs))
        Path(kwargs["output_video_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_video_path"]).write_bytes(MINIMAL_MP4)
        Path(kwargs["report_path"]).write_text("{}", encoding="utf-8")
        return Path(kwargs["report_path"])

    report = refresh_project_preview(
        {"runsDir": str(tmp_path / "runs"), "outputDir": str(output_dir)},
        "sample",
        card_renderer=forbidden,
        hybrid_renderer=forbidden,
        micro_renderer=fake_micro_renderer,
        command_runner=fake_runner,
        post_finalizer=fake_finalizer,
    )

    assert [call[0] for call in calls] == ["gate", "micro", "mux", "post"]
    micro_kwargs = calls[1][1]
    assert micro_kwargs["timeline_path"] == run_dir / "visual_timeline.json"
    assert micro_kwargs["selection_path"] == run_dir / "visual_selection.json"
    assert micro_kwargs["bakeoff_report_path"] == run_dir / "model_bakeoff_report.json"
    assert report["render_path"] == "quality_micro"
    assert report["micro_preview_video"] == str(run_dir / "micro_preview.mp4")
    assert report["voiced_preview_video"] == str(run_dir / "micro_preview_voiced.mp4")
    assert report["micro_shot_count"] == 3
    assert report["video_source_count"] == 2
    assert report["still_source_count"] == 1
    assert report["fallback_shot_count"] == 0
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["micro_preview_video"] == str(run_dir / "micro_preview.mp4")
    assert "hybrid_preview_video" not in status


def test_quality_refresh_failure_restores_previous_micro_and_final_outputs(
    tmp_path, monkeypatch
):
    run_dir, output_dir, _ = _write_quality_refresh_inputs(tmp_path)
    for name in ("visual_timeline.json", "visual_selection.json"):
        (run_dir / name).write_text("{}", encoding="utf-8")
    (run_dir / "model_bakeoff_report.json").write_text(
        json.dumps({"selected_model": "approved-model"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        preview_refresh,
        "require_selected_production_model",
        lambda report: "approved-model",
    )
    final_output = output_dir / "sample/final_preview.mp4"
    final_output.parent.mkdir(parents=True)
    protected = {
        run_dir / "micro_preview.mp4": b"old-silent",
        run_dir / "micro_preview_voiced.mp4": b"old-voiced",
        run_dir / "micro_preview_report.json": b"old-report",
        final_output: b"old-final",
    }
    for path, data in protected.items():
        path.write_bytes(data)

    def fake_micro_renderer(episode_arg, **kwargs):
        Path(kwargs["output_path"]).write_bytes(MINIMAL_MP4)
        Path(kwargs["report_path"]).write_text('{"new": true}', encoding="utf-8")
        return {"success": True, "micro_shot_count": 1, "sources": []}

    def fake_runner(command, **kwargs):
        Path(command[-1]).write_bytes(MINIMAL_MP4)

    def failing_finalizer(**kwargs):
        Path(kwargs["output_video_path"]).write_bytes(b"partial-final")
        raise RuntimeError("post failed")

    with pytest.raises(RuntimeError, match="post failed"):
        refresh_project_preview(
            {"runsDir": str(tmp_path / "runs"), "outputDir": str(output_dir)},
            "sample",
            micro_renderer=fake_micro_renderer,
            command_runner=fake_runner,
            post_finalizer=failing_finalizer,
        )

    for path, data in protected.items():
        assert path.read_bytes() == data
