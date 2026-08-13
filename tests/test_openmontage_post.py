import json
import subprocess
from pathlib import Path

import pytest

from factory.openmontage_post import (
    OpenMontagePostError,
    _split_caption_text,
    build_openmontage_finalize_command,
    build_caption_segments_from_srt,
    build_openmontage_post_report,
    finalize_openmontage_preview,
    write_openmontage_post_report,
)
from tests.media_fixtures import VALID_VIDEO_MP4


def _package(tmp_path: Path) -> Path:
    openmontage = tmp_path / "OpenMontage"
    (openmontage / "tools/video").mkdir(parents=True)
    (openmontage / "tools/video/video_compose.py").write_text("# compose", encoding="utf-8")
    (openmontage / "tools/video/remotion_caption_burn.py").write_text("# burn", encoding="utf-8")
    path = tmp_path / "openmontage_package.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.openmontage.v1",
                "project_id": "sample_episode",
                "title": "旧城来信",
                "openmontage_path": str(openmontage),
                "openmontage_available": True,
                "render_runtime": "remotion",
                "target": {"resolution": "1080x1920", "fps": 30},
                "timeline": [{"shot_id": "shot_001"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_build_openmontage_finalize_command_copies_streams():
    cmd = build_openmontage_finalize_command(
        source_video_path="/tmp/card_preview_voiced.mp4",
        output_video_path="/tmp/final_preview.mp4",
    )

    assert cmd[:4] == ["ffmpeg", "-y", "-i", "/tmp/card_preview_voiced.mp4"]
    assert "-map" in cmd
    assert "0" in cmd
    assert "-c" in cmd
    assert "copy" in cmd
    assert cmd[-1] == "/tmp/final_preview.mp4"


def test_build_caption_segments_from_srt_chunks_chinese_lines(tmp_path):
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text(
        "\n".join(
            [
                "1",
                "00:00:00,000 --> 00:00:03,000",
                "旁白：雨停后的旧城区像被擦亮的玻璃。林澈推开便利店的门，看见柜台上放着一枚没有寄件人的黑色信封。",
                "",
                "2",
                "00:00:03,000 --> 00:00:05,000",
                "苏眠：别急，先看清楚眼前的线索。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    segments = build_caption_segments_from_srt(subtitles, max_chars=14)
    words = segments[0]["words"]

    assert len(words) > 3
    assert all(len(word["word"]) <= 14 for word in words)
    assert words[0]["start"] == 0
    assert words[-1]["end"] == 5
    assert "雨停后的旧城区像被擦亮的玻璃" not in [word["word"] for word in words]


def test_caption_split_rebalances_a_tiny_final_page():
    chunks = _split_caption_text(
        "看见柜台上放着一枚没有寄件人的黑色信封。",
        max_chars=18,
    )

    assert "".join(chunks) == "看见柜台上放着一枚没有寄件人的黑色信封。"
    assert len(chunks) == 2
    assert min(map(len, chunks)) >= 4


def test_build_openmontage_post_report_records_delivery_and_tool_candidates(tmp_path):
    source = tmp_path / "card_preview_voiced.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "output/final_preview.mp4"
    output.parent.mkdir()
    output.write_bytes(b"final")

    report = build_openmontage_post_report(
        package_path=_package(tmp_path),
        source_video_path=source,
        output_video_path=output,
        command=["ffmpeg", "-i", str(source), str(output)],
    )

    assert report["schema_version"] == "motion-comic-factory.openmontage-post.v1"
    assert report["success"] is True
    assert report["project_id"] == "sample_episode"
    assert report["source_video_path"] == str(source)
    assert report["final_preview_path"] == str(output)
    assert report["openmontage_available"] is True
    assert "tools/video/video_compose.py" in report["openmontage_tool_candidates"]
    assert "tools/video/remotion_caption_burn.py" in report["openmontage_tool_candidates"]


def test_write_openmontage_post_report(tmp_path):
    source = tmp_path / "card_preview_voiced.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "output/final_preview.mp4"
    output.parent.mkdir()
    output.write_bytes(b"final")
    report_path = tmp_path / "openmontage_post_report.json"

    written = write_openmontage_post_report(
        package_path=_package(tmp_path),
        source_video_path=source,
        output_video_path=output,
        command=["ffmpeg"],
        output_path=report_path,
    )

    assert written == report_path
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["success"] is True


def test_finalize_openmontage_preview_prefers_caption_burn_runner(tmp_path):
    source = tmp_path / "card_preview_voiced.mp4"
    source.write_bytes(b"video")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n旁白：测试\n", encoding="utf-8")
    output = tmp_path / "output/final_preview.mp4"
    report_path = tmp_path / "openmontage_post_report.json"
    fallback_called = False

    def caption_burn_runner(**kwargs):
        assert kwargs["subtitles_path"] == subtitles
        Path(kwargs["output_video_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_video_path"]).write_bytes(VALID_VIDEO_MP4)
        return {
            "tool": "remotion_caption_burn",
            "success": True,
            "method": "remotion",
            "data": {"method": "remotion", "caption_count": 1},
            "artifacts": [str(kwargs["output_video_path"])],
        }

    def copy_runner(command):
        nonlocal fallback_called
        fallback_called = True

    written = finalize_openmontage_preview(
        package_path=_package(tmp_path),
        source_video_path=source,
        subtitles_path=subtitles,
        output_video_path=output,
        report_path=report_path,
        caption_burn_runner=caption_burn_runner,
        copy_runner=copy_runner,
    )

    assert written == report_path
    assert fallback_called is False
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["success"] is True
    assert data["mode"] == "openmontage_remotion_caption_burn"
    assert data["openmontage_execution"]["tool"] == "remotion_caption_burn"
    assert data["openmontage_execution"]["data"]["caption_count"] == 1


def test_finalize_openmontage_preview_falls_back_when_caption_burn_fails(tmp_path):
    source = tmp_path / "card_preview_voiced.mp4"
    source.write_bytes(b"video")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n旁白：测试\n", encoding="utf-8")
    output = tmp_path / "output/final_preview.mp4"
    report_path = tmp_path / "openmontage_post_report.json"
    fallback_command = None

    def caption_burn_runner(**kwargs):
        return {
            "tool": "remotion_caption_burn",
            "success": False,
            "error": "Remotion unavailable",
        }

    def copy_runner(command):
        nonlocal fallback_command
        fallback_command = command
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(VALID_VIDEO_MP4)

    finalize_openmontage_preview(
        package_path=_package(tmp_path),
        source_video_path=source,
        subtitles_path=subtitles,
        output_video_path=output,
        report_path=report_path,
        caption_burn_runner=caption_burn_runner,
        copy_runner=copy_runner,
    )

    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert fallback_command is not None
    assert data["success"] is True
    assert data["mode"] == "factory_ffmpeg_finalization_with_openmontage_handoff"
    assert data["openmontage_execution"]["success"] is False
    assert data["fallback_reason"] == "Remotion unavailable"


def test_finalize_failure_preserves_previous_good_output(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n测试\n",
        encoding="utf-8",
    )
    output = tmp_path / "final.mp4"
    output.write_bytes(b"last-good")

    def failed_caption(**kwargs):
        Path(kwargs["output_video_path"]).write_bytes(b"partial-caption")
        return {"success": False, "error": "caption failed"}

    def failed_copy(command):
        Path(command[-1]).write_bytes(b"partial-copy")
        raise subprocess.CalledProcessError(1, command, stderr="copy failed")

    with pytest.raises(OpenMontagePostError, match="copy failed"):
        finalize_openmontage_preview(
            package_path=_package(tmp_path),
            source_video_path=source,
            subtitles_path=subtitles,
            output_video_path=output,
            report_path=tmp_path / "report.json",
            caption_burn_runner=failed_caption,
            copy_runner=failed_copy,
        )

    assert output.read_bytes() == b"last-good"


def test_finalize_uses_render_subtitles_without_suppressed_source_duplicate(tmp_path):
    package = _package(tmp_path)
    package_data = json.loads(package.read_text(encoding="utf-8"))
    package_data["project_id"] = "sample_episode"
    package.write_text(json.dumps(package_data), encoding="utf-8")
    (tmp_path / "edit_decisions.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.edit-decisions.v1",
                "project_id": "sample_episode",
                "subtitle": {"suppress_cues": [2]},
                "shots": {},
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n重复字幕\n",
        encoding="utf-8",
    )
    output = tmp_path / "final.mp4"
    received_subtitles = None

    def caption_runner(**kwargs):
        nonlocal received_subtitles
        received_subtitles = Path(kwargs["subtitles_path"])
        Path(kwargs["output_video_path"]).write_bytes(VALID_VIDEO_MP4)
        return {"success": True, "tool": "test", "method": "test"}

    finalize_openmontage_preview(
        package_path=package,
        source_video_path=source,
        subtitles_path=subtitles,
        output_video_path=output,
        report_path=tmp_path / "report.json",
        caption_burn_runner=caption_runner,
    )

    assert received_subtitles == tmp_path / "subtitles.render.srt"
    assert "第一句" in received_subtitles.read_text(encoding="utf-8")
    assert "重复字幕" not in received_subtitles.read_text(encoding="utf-8")
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["subtitle_edit"]["suppressed_cues"] == [2]


def test_finalize_falls_back_when_injected_caption_runner_raises(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    subtitles = tmp_path / "subtitles.srt"
    subtitles.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n测试\n",
        encoding="utf-8",
    )
    output = tmp_path / "final.mp4"

    def caption_runner(**kwargs):
        Path(kwargs["output_video_path"]).write_bytes(b"partial")
        raise RuntimeError("caption crashed")

    def copy_runner(command):
        Path(command[-1]).write_bytes(VALID_VIDEO_MP4)

    finalize_openmontage_preview(
        package_path=_package(tmp_path),
        source_video_path=source,
        subtitles_path=subtitles,
        output_video_path=output,
        report_path=tmp_path / "report.json",
        caption_burn_runner=caption_runner,
        copy_runner=copy_runner,
    )

    assert output.read_bytes() == VALID_VIDEO_MP4
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert "caption crashed" in report["fallback_reason"]
