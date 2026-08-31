from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from factory.model_bakeoff import SCORE_WEIGHTS
from factory.visual_qc import (
    VisualQCError,
    VisualReview,
    analyze_visual_candidate,
    record_visual_review,
    require_passed_visual_qc,
)
from factory.visual_timeline import MicroShot
from factory.performance_card import PerformanceCard


def micro_shot() -> MicroShot:
    return MicroShot(
        id="micro_001",
        index=1,
        parent_shot_id="shot_001",
        scene_context="station platform",
        time_context="night",
        purpose="action",
        character_ids=("lead",),
        emotion_start="worried",
        emotion_end="relieved",
        emotion_intensity=3,
        gaze="toward the train",
        pose_start="standing",
        pose_end="turning",
        action_actor_id="lead",
        action_code="turn",
        action_target="train",
        camera_mode="locked",
        source_duration_seconds=3,
        timeline_duration_seconds=3,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no text",),
        cadence_fps=8,
    )


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    video = tmp_path / "candidate.mp4"
    video.write_bytes(b"local-video-bytes")
    return video


class FakeRunners:
    def __init__(
        self,
        *,
        black_duration: float = 0.0,
        scene_score: float = 0.0,
        fail_ffmpeg: bool = False,
        duration: float = 3.0,
    ) -> None:
        self.black_duration = black_duration
        self.scene_score = scene_score
        self.fail_ffmpeg = fail_ffmpeg
        self.duration = duration
        self.commands: list[list[str]] = []

    def command(self, command, **kwargs):
        self.commands.append(list(command))
        if command[0] == "ffprobe":
            payload = {
                "format": {"duration": str(self.duration)},
                "streams": [{"codec_type": "video"}],
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[0] != "ffmpeg" or self.fail_ffmpeg:
            raise subprocess.CalledProcessError(1, command, stderr="runner failed")
        joined = " ".join(command)
        if "blackdetect" in joined:
            return subprocess.CompletedProcess(
                command,
                0,
                "",
                "black_start:0.200 black_end:0.300 black_duration:"
                f"{self.black_duration}",
            )
        if "scdet" in joined:
            return subprocess.CompletedProcess(
                command, 0, "", f"lavfi.scd.score: {self.scene_score}"
            )
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 120), (len(self.commands) * 20 % 255, 30, 60)).save(
            output
        )
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def fake_runners() -> FakeRunners:
    return FakeRunners()


def _review(**changes) -> VisualReview:
    values = {key: 5 for key in SCORE_WEIGHTS} | {
        "hard_failures": (),
        "selected_start_seconds": 0.0,
        "selected_end_seconds": 2.0,
        "notes": "local reviewer note",
    }
    return VisualReview(**(values | changes))


def _visible_speech_card() -> PerformanceCard:
    return PerformanceCard(
        micro_shot_id="micro_001", purpose="action", speaker_id="lead",
        dialogue_id="shot_001.dialogue_01", requires_visible_lipsync=True,
        entry_anchor_id="scene_entry", scene_keyframe_id="scene_keyframe",
        actor_id="lead", target_id="", contact_point="", prop_hand="",
        start_beat="start", main_beat="speak", end_beat="end",
        negative_constraints=("no_text",),
    )


def test_visible_speech_review_requires_speaker_and_lipsync_evidence(
    tmp_path, fake_video, fake_runners
):
    _, report_path = _analyze(tmp_path, fake_video, fake_runners)

    with pytest.raises(
        VisualQCError,
        match="visible speech requires speaker_visible and lipsync_score",
    ):
        record_visual_review(
            report_path,
            _review(),
            expected_micro_shot=micro_shot(),
            performance_card=_visible_speech_card(),
            command_runner=fake_runners.command,
            ocr_runner=lambda _: "",
        )


def test_visible_speech_review_rejects_caller_audio_without_authoritative_evidence(
    tmp_path, fake_video, fake_runners
):
    _, report_path = _analyze(tmp_path, fake_video, fake_runners)

    with pytest.raises(
        VisualQCError,
        match="visible speech requires dialogue_manifest and rendered_job_report",
    ):
        record_visual_review(
            report_path,
            _review(
                speaker_visible=True,
                lipsync_score=5.0,
                audio_sha256="a" * 64,
            ),
            expected_micro_shot=micro_shot(),
            performance_card=_visible_speech_card(),
            command_runner=fake_runners.command,
            ocr_runner=lambda _: "",
        )


def _analyze(
    tmp_path: Path,
    fake_video: Path,
    fake_runners: FakeRunners,
    *,
    shot: MicroShot | None = None,
    ocr_runner=None,
    **kwargs,
) -> tuple[dict, Path]:
    output_dir = tmp_path / "qc"
    report = analyze_visual_candidate(
        fake_video,
        shot or micro_shot(),
        output_dir=output_dir,
        command_runner=fake_runners.command,
        ocr_runner=(lambda _: "") if ocr_runner is None else ocr_runner,
        **kwargs,
    )
    return report, output_dir / "visual_qc.json"


def _record(
    report_path: Path,
    review: VisualReview,
    runners: FakeRunners,
    shot=None,
    labels=(),
):
    return record_visual_review(
        report_path,
        review,
        expected_micro_shot=shot or micro_shot(),
        expected_reference_image_labels=labels,
        command_runner=runners.command,
        ocr_runner=lambda _: "",
    )


def _require(report: dict, runners: FakeRunners, shot=None, labels=()):
    return require_passed_visual_qc(
        report,
        expected_micro_shot=shot or micro_shot(),
        expected_reference_image_labels=labels,
        command_runner=runners.command,
        ocr_runner=lambda _: "",
    )


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _store(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report), encoding="utf-8")


def _forge_duration(report: dict, duration: float) -> None:
    report["probe"]["duration_seconds"] = duration
    for index, sample in enumerate(report["sample_frames"]):
        sample["seconds"] = duration * (index + 0.5) / 9


def test_analyze_flags_embedded_text_and_strict_cut_thresholds(
    tmp_path, fake_video
):
    report, _ = _analyze(
        tmp_path,
        fake_video,
        FakeRunners(black_duration=0.08, scene_score=10.0),
        ocr_runner=lambda _: "最后一班车",
    )
    assert report["automatic_hard_failures"] == ["embedded_text"]
    assert report["automatic_passed"] is False
    assert report["passed"] is False

    cut_report, _ = _analyze(
        tmp_path / "cut",
        fake_video,
        FakeRunners(black_duration=0.081),
    )
    assert cut_report["automatic_hard_failures"] == ["in_model_cut"]


def test_embedded_text_requires_confirmation_across_sample_frames(
    tmp_path, fake_video
):
    single_frame, _ = _analyze(
        tmp_path / "single",
        fake_video,
        FakeRunners(),
        ocr_runner=lambda crop: (
            "Wii" if crop.name.startswith("ocr_06_") else ""
        ),
    )
    assert single_frame["automatic_hard_failures"] == []
    assert single_frame["automatic_passed"] is True

    repeated, _ = _analyze(
        tmp_path / "repeated",
        fake_video,
        FakeRunners(),
        ocr_runner=lambda crop: (
            "最后一班车"
            if crop.name.startswith(("ocr_05_", "ocr_06_"))
            else ""
        ),
    )
    assert repeated["automatic_hard_failures"] == ["embedded_text"]
    assert repeated["automatic_passed"] is False


def test_report_has_exact_schema_full_micro_shot_and_hashed_evidence(
    tmp_path, fake_video, fake_runners
):
    report, report_path = _analyze(tmp_path, fake_video, fake_runners)

    assert set(report) == {
        "schema_version",
        "output_dir",
        "evidence_dir",
        "micro_shot",
        "micro_shot_sha256",
        "reference_image_labels",
        "candidate_evidence",
        "probe",
        "sample_frames",
        "contact_sheet",
        "ocr_evidence",
        "black_evidence",
        "cut_evidence",
        "motion_evidence",
        "commands",
        "automatic_hard_failures",
        "automatic_passed",
        "manual_review",
        "passed",
    }
    assert report["micro_shot"]["action_code"] == "turn"
    assert report["micro_shot"]["character_ids"] == ["lead"]
    assert report["micro_shot"]["emotion_end"] == "relieved"
    assert report["micro_shot"]["camera_mode"] == "locked"
    assert len(report["micro_shot_sha256"]) == 64
    assert len(report["sample_frames"]) == 9
    duration = report["probe"]["duration_seconds"]
    assert report["sample_frames"][0]["seconds"] <= duration * 0.06
    assert report["sample_frames"][-1]["seconds"] >= duration * 0.94
    assert len(report["ocr_evidence"]) == 18
    for item in [*report["sample_frames"], report["contact_sheet"], *report["ocr_evidence"]]:
        evidence = item["evidence"]
        assert Path(evidence["path"]).is_file()
        assert evidence["size_bytes"] > 0
        assert len(evidence["sha256"]) == 64
    assert json.loads(report_path.read_text()) == report
    assert all(command["tool"] in {"ffprobe", "ffmpeg"} for command in report["commands"])


def test_default_ocr_filters_tsv_at_80_and_detects_all_required_unicode(
    tmp_path, fake_video, fake_runners, monkeypatch
):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    unicode_text = "\U00020000\uf900あア한\u1100\u3130Ａ１"
    monkeypatch.setattr(
        "factory.visual_qc._run_tesseract",
        lambda _: header + f"5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t80\t{unicode_text}",
    )
    report = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "qc",
        command_runner=fake_runners.command,
    )

    assert "embedded_text" in report["automatic_hard_failures"]
    tesseract = next(command for command in report["commands"] if command["tool"] == "tesseract")
    assert tesseract["args"][-5:] == ["stdout", "-l", "chi_sim+eng", "--psm", "11", "tsv"][-5:]
    assert "chi_sim+eng" in tesseract["args"]
    assert tesseract["args"][-1] == "tsv"


def test_default_ocr_ignores_low_confidence_clothing_and_envelope_noise(
    tmp_path, fake_video, fake_runners, monkeypatch
):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = (
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t62\twe\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t71\tAN\n"
        "5\t1\t1\t1\t1\t3\t0\t0\t1\t1\t77\tALN\n"
    )
    monkeypatch.setattr(
        "factory.visual_qc._run_tesseract",
        lambda _: header + rows,
    )

    report = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "qc",
        command_runner=fake_runners.command,
    )

    assert report["automatic_hard_failures"] == []
    assert report["automatic_passed"] is True


def test_default_ocr_ignores_isolated_short_shape_noise(
    tmp_path, fake_video, fake_runners, monkeypatch
):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = (
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t83\ty\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t74\tCR\n"
    )
    monkeypatch.setattr(
        "factory.visual_qc._run_tesseract",
        lambda _: header + rows,
    )

    report = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "qc",
        command_runner=fake_runners.command,
    )

    assert report["automatic_hard_failures"] == []
    assert report["automatic_passed"] is True


def test_default_ocr_rejects_repeated_three_letter_texture_noise(
    tmp_path, fake_video, fake_runners, monkeypatch
):
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    row = "5\t1\t1\t1\t1\t1\t389\t877\t39\t61\t95\tiff\n"
    monkeypatch.setattr(
        "factory.visual_qc._run_tesseract",
        lambda _: header + row,
    )
    noise = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "noise-qc",
        command_runner=fake_runners.command,
    )
    assert noise["automatic_hard_failures"] == []

    text_row = "5\t1\t1\t1\t1\t1\t300\t800\t160\t60\t95\tSALE\n"
    monkeypatch.setattr(
        "factory.visual_qc._run_tesseract",
        lambda _: header + text_row,
    )
    text = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "text-qc",
        command_runner=fake_runners.command,
    )
    assert text["automatic_hard_failures"] == ["embedded_text"]


def test_analyze_rejects_invalid_media_runners_and_untrusted_input(
    tmp_path, fake_video, fake_runners
):
    with pytest.raises(VisualQCError, match="Invalid candidate video"):
        _analyze(tmp_path, fake_video, FakeRunners(duration=0))
    with pytest.raises(VisualQCError, match="FFmpeg"):
        _analyze(tmp_path, fake_video, FakeRunners(fail_ffmpeg=True))
    for value in (
        "file:/tmp/video.mp4",
        "mailto:reviewer@example.test",
        "javascript:alert(1)",
        "s3:bucket/key",
        "token=abc",
        "secret: abc",
    ):
        with pytest.raises(VisualQCError, match="URI or credential"):
            _analyze(
                tmp_path / value.replace(":", "_"),
                fake_video,
                fake_runners,
                reference_image_labels=(value,),
            )


def test_analyze_uses_new_private_evidence_directory_and_never_overwrites_symlinks(
    tmp_path, fake_video, fake_runners
):
    output = tmp_path / "qc"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    for path in (
        output / "sample_frames" / "sample_01.png",
        output / "contact_sheet_3x3.png",
        output / "sample_frames" / "sample_01_middle_55.png",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(outside)

    report, _ = _analyze(tmp_path, fake_video, fake_runners)

    assert outside.read_bytes() == b"outside"
    assert Path(report["evidence_dir"]).parent == output.resolve()
    assert Path(report["evidence_dir"]).stat().st_mode & 0o777 == 0o700
    assert all(
        Path(item["evidence"]["path"]).is_relative_to(Path(report["evidence_dir"]))
        for item in report["sample_frames"]
    )


def test_record_and_require_revalidate_all_evidence_and_current_automatic_result(
    tmp_path, fake_video, fake_runners
):
    report, report_path = _analyze(tmp_path, fake_video, fake_runners)
    recorded = _record(report_path, _review(), fake_runners)
    assert recorded["passed"] is True
    assert _require(recorded, fake_runners) == recorded

    fake_runners.scene_score = 10.01
    with pytest.raises(VisualQCError, match="automatic"):
        _require(recorded, fake_runners)


def test_record_and_require_reject_forged_probe_duration_and_stream_counts(
    tmp_path, fake_video, fake_runners
):
    _, record_path = _analyze(tmp_path / "record", fake_video, fake_runners)
    forged = _load(record_path)
    _forge_duration(forged, 100.0)
    _store(record_path, forged)
    with pytest.raises(VisualQCError, match="probe"):
        _record(
            record_path,
            _review(selected_start_seconds=50.0, selected_end_seconds=99.0),
            fake_runners,
        )

    report, require_path = _analyze(tmp_path / "require", fake_video, fake_runners)
    recorded = _record(require_path, _review(), fake_runners)
    _forge_duration(recorded, 100.0)
    recorded["manual_review"]["selected_start_seconds"] = 50.0
    recorded["manual_review"]["selected_end_seconds"] = 99.0
    recorded["passed"] = True
    assert report["probe"]["duration_seconds"] == 3.0
    with pytest.raises(VisualQCError, match="probe"):
        _require(recorded, fake_runners)

    _, stream_path = _analyze(tmp_path / "streams", fake_video, fake_runners)
    stream_forged = _load(stream_path)
    stream_forged["probe"]["audio_stream_count"] = 3
    _store(stream_path, stream_forged)
    with pytest.raises(VisualQCError, match="probe"):
        _record(stream_path, _review(), fake_runners)


@pytest.mark.parametrize(
    ("prefix", "forged"),
    [
        ("blackdetect=", "blackdetect=s3:bucket/key"),
        ("tile=", "tile=mailto:x"),
    ],
)
def test_persisted_commands_reject_uri_lookalike_filter_prefixes(
    tmp_path, fake_video, fake_runners, prefix, forged
):
    _, report_path = _analyze(tmp_path, fake_video, fake_runners)
    report = _load(report_path)
    for command in report["commands"]:
        for index, argument in enumerate(command["args"]):
            if argument.startswith(prefix):
                command["args"][index] = forged
    _store(report_path, report)

    with pytest.raises(VisualQCError, match="URI"):
        _record(report_path, _review(), fake_runners)


@pytest.mark.parametrize("kind", ["sample", "contact", "crop"])
@pytest.mark.parametrize("mutation", ["deleted", "rewritten", "replaced"])
def test_record_rejects_deleted_rewritten_or_replaced_evidence(
    tmp_path, fake_video, fake_runners, kind, mutation
):
    _, report_path = _analyze(tmp_path, fake_video, fake_runners)
    report = _load(report_path)
    item = {
        "sample": report["sample_frames"][0],
        "contact": report["contact_sheet"],
        "crop": report["ocr_evidence"][0],
    }[kind]
    path = Path(item["evidence"]["path"])
    if mutation == "deleted":
        path.unlink()
    elif mutation == "rewritten":
        path.write_bytes(b"tampered")
    else:
        replacement = path.with_name("replacement.png")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)

    with pytest.raises(VisualQCError, match="evidence"):
        _record(report_path, _review(), fake_runners)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report.__setitem__("extra", True),
        lambda report: report.pop("contact_sheet"),
        lambda report: report.__setitem__("automatic_passed", True),
        lambda report: report.__setitem__("automatic_hard_failures", []),
        lambda report: report.__setitem__("passed", True),
    ],
)
def test_record_fails_closed_on_forged_schema_or_derived_gates(
    tmp_path, fake_video, fake_runners, mutate
):
    _, report_path = _analyze(
        tmp_path, fake_video, fake_runners, ocr_runner=lambda _: "最后一班车"
    )
    forged = _load(report_path)
    mutate(forged)
    _store(report_path, forged)

    with pytest.raises(VisualQCError):
        _record(report_path, _review(), fake_runners)


def test_micro_shot_binding_requires_exact_type_and_full_semantics(
    tmp_path, fake_video, fake_runners
):
    report, report_path = _analyze(tmp_path, fake_video, fake_runners)
    with pytest.raises(TypeError):
        record_visual_review(report_path, _review(), command_runner=fake_runners.command)
    with pytest.raises(VisualQCError, match="MicroShot"):
        record_visual_review(
            report_path,
            _review(),
            expected_micro_shot=object(),
            command_runner=fake_runners.command,
            ocr_runner=lambda _: "",
        )
    changed = replace(micro_shot(), action_target="different train")
    with pytest.raises(VisualQCError, match="micro-shot"):
        _record(report_path, _review(), fake_runners, shot=changed)
    assert report["micro_shot"]["action_target"] == "train"


def test_reference_image_labels_are_bound_for_record_and_require(
    tmp_path, fake_video, fake_runners
):
    labels = ("lead-reference.png", "partner-reference.png")
    report, report_path = _analyze(
        tmp_path,
        fake_video,
        fake_runners,
        reference_image_labels=labels,
    )
    recorded = _record(report_path, _review(), fake_runners, labels=labels)
    assert _require(recorded, fake_runners, labels=labels) == recorded
    with pytest.raises(VisualQCError, match="reference image labels"):
        _require(recorded, fake_runners, labels=("replacement.png",))


def test_review_validation_score_boundary_and_safe_notes(
    tmp_path, fake_video, fake_runners
):
    _, path = _analyze(tmp_path, fake_video, fake_runners)
    score_80 = _review(identity=1)
    recorded = _record(path, score_80, fake_runners)
    assert recorded["manual_review"]["weighted_score"] == 84.0
    assert recorded["passed"] is True
    for index, notes in enumerate(
        ("password=abc", "passwd: abc", "access key=abc", "Bearer abc")
    ):
        _, invalid_path = _analyze(tmp_path / f"invalid-note-{index}", fake_video, fake_runners)
        with pytest.raises(VisualQCError, match="URI or credential"):
            _record(invalid_path, _review(notes=notes), fake_runners)


def test_candidate_same_byte_replacement_and_output_symlink_are_rejected(
    tmp_path, fake_video, fake_runners
):
    _, path = _analyze(tmp_path, fake_video, fake_runners)
    recorded = _record(path, _review(), fake_runners)
    replacement = tmp_path / "same.mp4"
    replacement.write_bytes(fake_video.read_bytes())
    replacement.replace(fake_video)
    with pytest.raises(VisualQCError, match="changed"):
        _require(recorded, fake_runners)

    link = tmp_path / "qc-link"
    link.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(VisualQCError, match="symlink"):
        analyze_visual_candidate(
            fake_video,
            micro_shot(),
            output_dir=link,
            command_runner=fake_runners.command,
            ocr_runner=lambda _: "",
        )


def _local_tools_available() -> bool:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe") or not shutil.which("tesseract"):
        return False
    languages = subprocess.run(
        ["tesseract", "--list-langs"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    return {"chi_sim", "eng"}.issubset(languages)


@pytest.mark.skipif(not _local_tools_available(), reason="local FFmpeg/Tesseract tools unavailable")
def test_real_local_ffmpeg_and_tesseract_integration(tmp_path):
    candidate = tmp_path / "plain.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x96:r=30",
            "-t",
            "1.0",
            "-pix_fmt",
            "yuv420p",
            str(candidate),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report = analyze_visual_candidate(candidate, micro_shot(), output_dir=tmp_path / "qc")

    assert len(report["sample_frames"]) == 9
    duration = report["probe"]["duration_seconds"]
    assert report["sample_frames"][0]["seconds"] <= duration * 0.06
    assert report["sample_frames"][-1]["seconds"] >= duration * 0.94
    assert Path(report["contact_sheet"]["evidence"]["path"]).is_file()
    assert any(
        command["tool"] == "tesseract"
        and command["args"][-4:] == ["-l", "chi_sim+eng", "--psm", "11", "tsv"][-4:]
        for command in report["commands"]
    )
