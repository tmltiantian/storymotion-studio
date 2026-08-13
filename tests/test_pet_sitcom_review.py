from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import wave
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from factory.pet_sitcom import build_pet_sitcom_plan


CAT_SHOT_IDS = {
    "shot_03",
    "shot_04",
    "shot_05",
    "shot_08",
    "shot_09",
    "shot_10",
}
GENERATION_DURATIONS = {
    "shot_01": 6.0,
    "shot_02": 4.0,
    "shot_03": 7.0,
    "shot_04": 5.0,
    "shot_05": 8.0,
    "shot_06": 7.0,
    "shot_07": 5.0,
    "shot_08": 8.0,
    "shot_09": 6.0,
    "shot_10": 5.0,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pcm_wav(path: Path, *, seconds: float) -> None:
    rate = 48000
    channels = 2
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x01\x00" * channels * int(seconds * rate))


def _write_current_task2_manifest(
    plan,
    monkeypatch,
    *,
    duration_seconds: float = 1.0,
    mock_probe: bool = True,
) -> tuple:
    import factory.pet_sitcom_audio_first as audio_first

    starts = audio_first._shot_start_times(plan)
    assets = []
    for shot in plan.shots:
        if not shot.dialogue:
            continue
        voice = audio_first.PET_VOICES[str(shot.speaker)]
        output = audio_first._speech_output(plan, shot)
        _write_pcm_wav(output, seconds=duration_seconds)
        absolute_start = (
            starts[shot.shot_id] + float(shot.dialogue_offset_seconds)
        )
        assets.append(
            audio_first.PetSpeechAsset(
                shot_id=shot.shot_id,
                speaker=str(shot.speaker),
                text=shot.dialogue,
                voice_id=voice.voice_id,
                speech_rate=voice.speech_rate,
                output_path=output,
                output_sha256=_sha256(output),
                duration_seconds=duration_seconds,
                absolute_start_seconds=absolute_start,
                absolute_end_seconds=absolute_start + duration_seconds,
            )
        )
    result = tuple(assets)
    audio_first._write_manifest(plan, result)
    if mock_probe:
        monkeypatch.setattr(
            audio_first,
            "probe_media",
            lambda _path, *, required_stream: SimpleNamespace(
                valid=required_stream == "audio",
                duration_seconds=duration_seconds,
                audio_stream_count=1,
                video_stream_count=0,
            ),
        )
    return result


@pytest.fixture
def plan(tmp_path: Path):
    return build_pet_sitcom_plan({}, output_dir=tmp_path / "pet-case")


@pytest.fixture
def sources(plan):
    result = {}
    for shot in plan.shots:
        source = plan.output_dir / "verified" / f"{shot.shot_id}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"verified-{shot.shot_id}".encode("ascii"))
        drive_audio_path = ""
        drive_audio_sha256 = ""
        audio_onset_seconds = None
        audio_offset_seconds = None
        if shot.shot_id in CAT_SHOT_IDS:
            drive = (
                plan.output_dir
                / "audio"
                / "drive"
                / f"{shot.shot_id}_drive.wav"
            )
            drive.parent.mkdir(parents=True, exist_ok=True)
            drive.write_bytes(f"drive-{shot.shot_id}".encode("ascii"))
            drive_audio_path = str(drive.resolve())
            drive_audio_sha256 = _sha256(drive)
            audio_onset_seconds = float(shot.dialogue_offset_seconds)
            audio_offset_seconds = 3.25
        result[shot.shot_id] = {
            "path": source,
            "sha256": _sha256(source),
            "candidate_number": 1,
            "reference_audio_path": drive_audio_path,
            "reference_audio_sha256": drive_audio_sha256,
            "audio_onset_seconds": audio_onset_seconds,
            "audio_offset_seconds": audio_offset_seconds,
            "continuity_source_video_duration_seconds": float(
                shot.generation_duration_seconds
            ),
            "edit_duration_seconds": float(shot.duration_seconds),
            "continuity_timestamp_seconds": min(
                float(shot.duration_seconds) - 0.08,
                float(shot.generation_duration_seconds) - 0.08,
            ),
        }
    return result


@pytest.fixture
def wired_sources(monkeypatch, sources):
    import factory.pet_sitcom_review as review

    monkeypatch.setattr(review, "_selected_sources", lambda _plan: sources)
    return sources


class FakeMediaRunner:
    def __init__(
        self,
        *,
        source_duration: float | None = None,
        video_duration: float | None = None,
        true_peak: float = -1.6,
        freeze_duration: float = 0.0,
        black_duration: float = 0.0,
        audio_streams_by_shot: dict[str, int] | None = None,
        video_streams: int = 1,
        omit_video_duration: bool = False,
    ) -> None:
        self.source_duration = source_duration
        self.video_duration = video_duration
        self.true_peak = true_peak
        self.freeze_duration = freeze_duration
        self.black_duration = black_duration
        self.audio_streams_by_shot = audio_streams_by_shot or {}
        self.video_streams = video_streams
        self.omit_video_duration = omit_video_duration
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        command = [str(item) for item in command]
        self.commands.append(command)
        tool = Path(command[0]).name
        if tool == "ffprobe":
            path = Path(command[-1])
            shot_id = (
                path.stem
                if path.stem.startswith("shot_")
                else path.parent.name
            )
            duration = (
                54.0
                if path.name.endswith("版.mp4")
                else (
                    self.source_duration
                    if self.source_duration is not None
                    else GENERATION_DURATIONS.get(shot_id, 5.0)
                )
            )
            audio_streams = (
                1
                if path.name.endswith("版.mp4")
                else self.audio_streams_by_shot.get(
                    shot_id, int(shot_id in CAT_SHOT_IDS)
                )
            )
            payload = _probe_payload(
                duration,
                video_duration=(
                    duration
                    if path.name.endswith("版.mp4")
                    else self.video_duration
                ),
                audio_streams=audio_streams,
                video_streams=self.video_streams,
            )
            if self.omit_video_duration:
                for stream in payload["streams"]:
                    if stream["codec_type"] == "video":
                        stream.pop("duration", None)
            return SimpleNamespace(
                stdout=json.dumps(payload),
                stderr="",
            )
        if tool != "ffmpeg":
            raise AssertionError(f"unexpected command: {command}")
        joined = " ".join(command)
        if "blackdetect=" in joined or "freezedetect=" in joined:
            if "blackdetect=" in joined and self.black_duration:
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "[blackdetect] black_start:0 "
                        f"black_end:{self.black_duration} "
                        f"black_duration:{self.black_duration}\n"
                    ),
                )
            if "freezedetect=" in joined and self.freeze_duration:
                return SimpleNamespace(
                    stdout="",
                    stderr=(
                        "[freezedetect] freeze_start: 2.875\n"
                        f"[freezedetect] freeze_end: {2.875 + self.freeze_duration}\n"
                        f"[freezedetect] freeze_duration: {self.freeze_duration}\n"
                    ),
                )
            return SimpleNamespace(stdout="", stderr="")
        if "ebur128=peak=true" in joined:
            return SimpleNamespace(
                stdout="",
                stderr=(
                    "Sample peak:\n"
                    "  Peak: 0.0 dBFS\n"
                    "Integrated loudness:\n"
                    "  I: -16.0 LUFS\n"
                    "True peak:\n"
                    f"  Peak: {self.true_peak:.1f} dBFS\n"
                ),
            )
        output = Path(command[-1])
        if "-frames:v" not in command or output.suffix != ".png":
            raise AssertionError(f"unexpected ffmpeg command: {command}")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 12), "white").save(output)
        return SimpleNamespace(stdout="", stderr="")


def _probe_payload(
    duration: float,
    *,
    video_duration: float | None = None,
    audio_streams: int = 1,
    video_streams: int = 1,
) -> dict:
    video = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "profile": "High",
        "pix_fmt": "yuv420p",
        "width": 1080,
        "height": 1920,
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "duration": str(
            duration if video_duration is None else video_duration
        ),
    }
    audio = {
        "index": video_streams,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": 2,
        "channel_layout": "stereo",
        "bit_rate": "192000",
        "duration": str(duration),
    }
    return {
        "format": {"duration": str(duration), "format_name": "mov,mp4"},
        "streams": [
            *[dict(video, index=index) for index in range(video_streams)],
            *[
                dict(audio, index=video_streams + index)
                for index in range(audio_streams)
            ],
        ],
    }


def _write_finals(plan) -> None:
    for output in (plan.clean_output, plan.release_output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"final-{output.name}".encode("utf-8"))


def _build_source(plan, runner, monkeypatch):
    import factory.pet_sitcom_review as review

    return review.build_pet_sitcom_evidence(
        plan,
        phase="source",
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )


def _build_final(plan, runner, monkeypatch):
    import factory.pet_sitcom_review as review

    monkeypatch.setattr(review, "_mp4_has_faststart", lambda _path: True)
    return review.build_pet_sitcom_evidence(
        plan,
        phase="final",
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )


def _complete_shot_reviews(plan, *, failed_shot: str | None = None) -> dict:
    import factory.pet_sitcom_review as review

    payload = json.loads(plan.shot_review_path.read_text(encoding="utf-8"))
    for shot_id, record in payload["shots"].items():
        duration = GENERATION_DURATIONS[shot_id]
        record["reviewed"] = True
        record["reviewed_at"] = "2026-07-24T00:00:00+00:00"
        record["retry_reason"] = ""
        for gate_name in review.SHOT_REVIEW_GATES:
            record["gates"][gate_name] = {
                "passed": True,
                "notes": f"{gate_name} checked frame by frame",
                "timestamps_seconds": [
                    0.1,
                    min(2.5, duration - 0.2),
                    duration - 0.1,
                ],
                "issue_codes": [],
            }
        if shot_id == failed_shot:
            record["gates"]["paws_and_feline_anatomy"] = {
                "passed": False,
                "notes": "left paw has a visible fused toe",
                "timestamps_seconds": [2.2],
                "issue_codes": ["paw_anatomy"],
            }
            record["retry_reason"] = "paw_anatomy"
        record["passed"] = all(
            gate["passed"] for gate in record["gates"].values()
        )
    for shot_id, record in payload["mouth_timing"].items():
        shot = next(item for item in plan.shots if item.shot_id == shot_id)
        audio_onset = float(shot.dialogue_offset_seconds)
        audio_offset = 3.25
        record.update(
            {
                "audio_onset_seconds": audio_onset,
                "mouth_onset_seconds": audio_onset + 0.10,
                "audio_offset_seconds": audio_offset,
                "mouth_offset_seconds": audio_offset + 0.10,
                "onset_error_seconds": 0.10,
                "offset_error_seconds": 0.10,
                "no_silent_mouth_flapping": True,
                "no_closed_mouth_during_speech": True,
                "reviewed": True,
                "passed": True,
            }
        )
    plan.shot_review_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def _rewrite_shot_reviews_as_legacy_v2(plan) -> dict:
    payload = json.loads(plan.shot_review_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "motion-comic-factory.pet-sitcom-shot-review.v3"
    payload.pop("mouth_timing", None)
    for record in payload["shots"].values():
        for gate in record["gates"].values():
            gate.pop("issue_codes")
    plan.shot_review_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def _complete_owner_review(plan) -> None:
    path = plan.output_dir / "owner_native_audio_review.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(
        {
            "reviewed": True,
            "verified": True,
            "generated_at": "2026-07-24T00:00:00+00:00",
        }
    )
    for record in payload["shots"].values():
        record.update(
            {
                "no_native_voice": True,
                "room_tone_allowed": True,
                "reviewed_at": "2026-07-24T00:00:00+00:00",
                "notes": "",
            }
        )
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_task2_reviews(plan, monkeypatch) -> None:
    import factory.pet_sitcom_generation as generation
    from factory import pet_sitcom_audio_probe

    for index, character in enumerate(plan.characters, start=1):
        character.reference_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (index * 40, 20, 20)).save(
            character.reference_path
        )
    for scene in plan.scenes:
        scene.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "white").save(scene.anchor_path)
    anchor = {
        "schema_version": generation.ANCHOR_REVIEW_SCHEMA,
        "completed": True,
        "approved": True,
        "source_hashes": generation._anchor_hashes(plan),
        "naitang_consistent_across_panels": True,
        "doubao_consistent_across_panels": True,
        "cats_remain_clearly_distinct": True,
        "scenes_are_empty_and_clean": True,
        "scenes_share_home_design": True,
    }
    (plan.output_dir / "anchor_review_template.json").write_text(
        json.dumps(anchor),
        encoding="utf-8",
    )
    candidate = generation._mouth_candidate_path(plan, 1)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"approved-mouth-test")
    gateway_report = generation._mouth_gateway_report_path(candidate)
    base_prompt = generation._mouth_test_prompt("")
    provenance = generation._mouth_candidate_provenance(
        plan,
        1,
        "",
        base_prompt,
        base_prompt,
        gateway_report,
    )
    provenance.update(
        {
            "provider_success": True,
            "video_sha256": _sha256(candidate),
        }
    )
    generation._mouth_candidate_state_path(candidate).write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    gateway_report.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.gateway-video.v2",
                "success": True,
                "provider": generation.VIDEO_PROVIDER,
                "model": generation.VIDEO_MODEL,
                "pet_sitcom_mouth_provenance": provenance,
            }
        ),
        encoding="utf-8",
    )
    review = {
        "schema_version": generation.MOUTH_REVIEW_SCHEMA,
        "candidate_number": 1,
        "mp4_sha256": _sha256(candidate),
        "completed": True,
        "correct_naitang_identity": True,
        "photorealistic_feline_face": True,
        "no_human_mouth_deformation": True,
        "correct_speaker": True,
        "exact_intelligible_line": True,
        "visible_mouth_and_jaw_movement": True,
        "subjective_start_pause_end_alignment": True,
        "no_extra_animal_text_or_watermark": True,
        "passed": True,
        "notes": "frame-by-frame mouth review passed",
        "retry_reason": "",
    }
    generation._mouth_review_path(plan, 1).write_text(
        json.dumps(review),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pet_sitcom_audio_probe,
        "require_approved_pet_audio_probe",
        lambda _plan: {"approved": True},
    )


def _write_selection_details(
    plan,
    sources,
    *,
    candidate_two_retry_reason: str = "continuity",
) -> None:
    import factory.pet_sitcom_generation as generation

    selections = {}
    for shot in plan.shots:
        candidate_number = 2 if shot.shot_id == "shot_10" else 1
        source = Path(sources[shot.shot_id]["path"])
        selections[shot.shot_id] = {
            "candidate_number": candidate_number,
            "status": "selected",
            "video_path": str(source.resolve()),
            "video_sha256": sources[shot.shot_id]["sha256"],
        }
        generation._pet_candidate_state_path(source).write_text(
            json.dumps(
                {
                    "candidate_number": candidate_number,
                    "video_sha256": sources[shot.shot_id]["sha256"],
                    "prompt_sha256": f"prompt-{shot.shot_id}",
                    "retry_reason": (
                        candidate_two_retry_reason
                        if candidate_number == 2
                        else ""
                    ),
                    "retry_suffix": (
                        generation.PET_RETRY_SUFFIXES[
                            candidate_two_retry_reason
                        ]
                        if candidate_number == 2
                        else ""
                    ),
                    "reference_paths": [],
                    "reference_sha256": [],
                }
            ),
            encoding="utf-8",
        )
    plan.selection_path.write_text(
        json.dumps(
            {
                "schema_version": generation.PET_SELECTION_SCHEMA,
                "shots": selections,
                "history": {},
            }
        ),
        encoding="utf-8",
    )


def _write_marker_png(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), marker).save(path)


@pytest.mark.parametrize("candidate_number", (3, 4, 5))
def test_review_source_candidate_number_supports_retry_candidates(
    candidate_number
):
    import factory.pet_sitcom_review as review

    assert (
        review._source_candidate_number(
            {"candidate_number": candidate_number}
        )
        == candidate_number
    )
    assert (
        review._source_candidate_number(
            {
                "path": (
                    f"/tmp/shot_06/candidate_{candidate_number:03d}.mp4"
                )
            }
        )
        == candidate_number
    )


def _write_continuity_state(
    source: Path,
    frame: Path,
    *,
    source_duration: float,
    edit_duration: float,
) -> None:
    import factory.pet_sitcom_generation as generation

    _write_marker_png(frame, "white")
    generation._pet_continuity_state_path(frame).write_text(
        json.dumps(
            {
                "schema_version": generation.PET_CONTINUITY_SCHEMA,
                "source_video_path": str(source.resolve()),
                "source_video_sha256": _sha256(source),
                "source_video_duration_seconds": source_duration,
                "edit_duration_seconds": edit_duration,
                "timestamp_seconds": min(
                    edit_duration - 0.08,
                    source_duration - 0.08,
                ),
                "extracted_at": "2026-07-24T00:00:00+00:00",
                "frame_sha256": _sha256(frame),
            }
        ),
        encoding="utf-8",
    )


def _write_partial_task3_chain(plan, through: int, monkeypatch) -> dict:
    import factory.pet_sitcom_generation as generation
    from factory.media_validation import MediaProbeResult

    for index, character in enumerate(plan.characters, start=1):
        _write_marker_png(character.reference_path, ("red", "blue")[index - 1])
    for scene in plan.scenes:
        _write_marker_png(scene.anchor_path, "white")
    tts_paths: dict[str, Path] = {}
    drive_paths: dict[str, Path] = {}
    for shot_id in CAT_SHOT_IDS:
        tts = plan.output_dir / "audio" / "tts" / f"{shot_id}.wav"
        drive = (
            plan.output_dir
            / "audio"
            / "drive"
            / f"{shot_id}_drive.wav"
        )
        tts.parent.mkdir(parents=True, exist_ok=True)
        drive.parent.mkdir(parents=True, exist_ok=True)
        tts.write_bytes(f"tts-{shot_id}".encode("ascii"))
        drive.write_bytes(f"drive-{shot_id}".encode("ascii"))
        tts_paths[shot_id] = tts
        drive_paths[shot_id] = drive

    monkeypatch.setattr(
        generation,
        "build_pet_drive_audio",
        lambda _plan, shot_id, **_kwargs: drive_paths[shot_id],
    )
    monkeypatch.setattr(
        generation,
        "load_pet_speech_assets",
        lambda _plan: tuple(
            SimpleNamespace(
                shot_id=shot_id,
                output_path=path,
                output_sha256=_sha256(path),
                duration_seconds=2.70,
            )
            for shot_id, path in sorted(tts_paths.items())
        ),
    )
    monkeypatch.setattr(generation, "is_valid_mp4_file", lambda _path: True)

    def probe(path, **_kwargs):
        candidate = Path(path)
        shot_id = candidate.parent.name
        return MediaProbeResult(
            candidate,
            True,
            GENERATION_DURATIONS[shot_id],
            1,
            int(shot_id in CAT_SHOT_IDS),
        )

    monkeypatch.setattr(
        generation,
        "probe_media",
        probe,
    )
    selections: dict[str, dict] = {}
    for shot in plan.shots[:through]:
        candidate = generation._pet_candidate_path(shot, 1)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(f"candidate-{shot.shot_id}".encode("ascii"))
        references = generation._pet_shot_references(
            plan, shot, selections
        )
        drive_audio = drive_paths.get(shot.shot_id)
        tts_sha256 = (
            _sha256(tts_paths[shot.shot_id])
            if shot.shot_id in CAT_SHOT_IDS
            else ""
        )
        provenance = generation._pet_candidate_provenance(
            shot,
            1,
            generation._pet_shot_prompt(shot, 1, ""),
            "",
            references,
            selections,
            drive_audio,
            tts_sha256,
        )
        provenance.update(
            {
                "provider_success": True,
                "gateway_report_path": str(
                    generation._pet_gateway_report_path(candidate).resolve()
                ),
                "video_sha256": _sha256(candidate),
            }
        )
        generation._pet_candidate_state_path(candidate).write_text(
            json.dumps(provenance),
            encoding="utf-8",
        )
        generation._pet_gateway_report_path(candidate).write_text(
            json.dumps(
                {
                    "success": True,
                    "pet_sitcom_provenance": provenance,
                }
            ),
            encoding="utf-8",
        )
        continuity_frame = generation._pet_continuity_frame_path(
            plan,
            shot.shot_id,
        )
        _write_continuity_state(
            candidate,
            continuity_frame,
            source_duration=float(shot.generation_duration_seconds),
            edit_duration=float(shot.duration_seconds),
        )
        continuity_state = json.loads(
            generation._pet_continuity_state_path(
                continuity_frame
            ).read_text()
        )
        entry = {
            "candidate_number": 1,
            "status": "selected",
            "video_path": str(candidate.resolve()),
            "video_sha256": _sha256(candidate),
            "prompt_sha256": provenance["prompt_sha256"],
            "reference_paths": provenance["reference_paths"],
            "reference_sha256": provenance["reference_sha256"],
            "dependency_video_sha256": provenance[
                "dependency_video_sha256"
            ],
            "source_tts_sha256": provenance["source_tts_sha256"],
            "reference_audio_sha256": provenance[
                "reference_audio_sha256"
            ],
            "continuity_frame_path": str(continuity_frame.resolve()),
            "continuity_sidecar_path": str(
                generation._pet_continuity_state_path(
                    continuity_frame
                ).resolve()
            ),
            "continuity_frame_sha256": _sha256(continuity_frame),
            "continuity_timestamp_seconds": continuity_state[
                "timestamp_seconds"
            ],
            "selected_at": "2026-07-24T00:00:00+00:00",
        }
        selections[shot.shot_id] = entry
    plan.selection_path.parent.mkdir(parents=True, exist_ok=True)
    plan.selection_path.write_text(
        json.dumps(
            {
                "schema_version": generation.PET_SELECTION_SCHEMA,
                "shots": selections,
                "history": {},
            }
        ),
        encoding="utf-8",
    )
    return selections


def _bind_real_task2_audio_to_partial_task5(
    plan,
    selections: dict,
    monkeypatch,
) -> dict:
    import factory.pet_sitcom_audio_first as audio_first
    import factory.pet_sitcom_generation as generation

    assets = _write_current_task2_manifest(
        plan,
        monkeypatch,
        mock_probe=False,
    )
    shot = next(item for item in plan.shots if item.shot_id == "shot_03")
    asset = next(item for item in assets if item.shot_id == shot.shot_id)
    drive = (
        plan.output_dir / "audio" / "drive" / f"{shot.shot_id}_drive.wav"
    )
    _write_pcm_wav(
        drive,
        seconds=float(shot.generation_duration_seconds),
    )
    drive.with_suffix(".state.json").write_text(
        json.dumps(
            {
                "schema_version": audio_first.DRIVE_AUDIO_STATE_SCHEMA,
                "status": "completed",
                "signature": audio_first._drive_signature(shot, asset),
                "shot_id": shot.shot_id,
                "dialogue_offset_seconds": shot.dialogue_offset_seconds,
                "generation_duration_seconds": (
                    shot.generation_duration_seconds
                ),
                "source_path": str(asset.output_path),
                "source_sha256": asset.output_sha256,
                "source_duration_seconds": asset.duration_seconds,
                "absolute_start_seconds": asset.absolute_start_seconds,
                "absolute_end_seconds": asset.absolute_end_seconds,
                "output_path": str(drive),
                "output_sha256": _sha256(drive),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        generation,
        "build_pet_drive_audio",
        audio_first.build_pet_drive_audio,
    )
    monkeypatch.setattr(
        generation,
        "load_pet_speech_assets",
        audio_first.load_pet_speech_assets,
    )

    candidate = generation._pet_candidate_path(shot, 1)
    references = generation._pet_shot_references(plan, shot, selections)
    drive_audio, tts_sha256 = generation._pet_shot_audio_bindings(plan, shot)
    provenance = generation._pet_candidate_provenance(
        shot,
        1,
        generation._pet_shot_prompt(shot, 1, ""),
        "",
        references,
        selections,
        drive_audio,
        tts_sha256,
    )
    provenance.update(
        {
            "provider_success": True,
            "gateway_report_path": str(
                generation._pet_gateway_report_path(candidate).resolve()
            ),
            "video_sha256": _sha256(candidate),
        }
    )
    generation._pet_candidate_state_path(candidate).write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    generation._pet_gateway_report_path(candidate).write_text(
        json.dumps(
            {
                "success": True,
                "pet_sitcom_provenance": provenance,
            }
        ),
        encoding="utf-8",
    )
    prior = selections[shot.shot_id]
    selections[shot.shot_id] = {
        "candidate_number": 1,
        "status": "selected",
        "video_path": str(candidate.resolve()),
        "video_sha256": _sha256(candidate),
        "prompt_sha256": provenance["prompt_sha256"],
        "reference_paths": provenance["reference_paths"],
        "reference_sha256": provenance["reference_sha256"],
        "dependency_video_sha256": provenance["dependency_video_sha256"],
        "source_tts_sha256": provenance["source_tts_sha256"],
        "reference_audio_sha256": provenance["reference_audio_sha256"],
        "continuity_frame_path": prior["continuity_frame_path"],
        "continuity_sidecar_path": prior["continuity_sidecar_path"],
        "continuity_frame_sha256": prior["continuity_frame_sha256"],
        "continuity_timestamp_seconds": prior[
            "continuity_timestamp_seconds"
        ],
        "selected_at": prior["selected_at"],
    }
    document = json.loads(plan.selection_path.read_text())
    document["shots"] = selections
    plan.selection_path.write_text(json.dumps(document), encoding="utf-8")
    return selections


def _switch_partial_selection_to_candidate_two(
    plan,
    shot_id: str,
    retry_reason: str,
) -> dict:
    import factory.pet_sitcom_generation as generation

    document = json.loads(plan.selection_path.read_text(encoding="utf-8"))
    selections = document["shots"]
    shot = next(item for item in plan.shots if item.shot_id == shot_id)
    candidate = generation._pet_candidate_path(shot, 2)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(f"candidate-two-{shot_id}".encode("ascii"))
    references = generation._pet_shot_references(plan, shot, selections)
    drive_audio, tts_sha256 = generation._pet_shot_audio_bindings(
        plan,
        shot,
    )
    prompt = generation._pet_shot_prompt(shot, 2, retry_reason)
    provenance = generation._pet_candidate_provenance(
        shot,
        2,
        prompt,
        retry_reason,
        references,
        selections,
        drive_audio,
        tts_sha256,
    )
    provenance.update(
        {
            "provider_success": True,
            "gateway_report_path": str(
                generation._pet_gateway_report_path(candidate).resolve()
            ),
            "video_sha256": _sha256(candidate),
        }
    )
    generation._pet_candidate_state_path(candidate).write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )
    generation._pet_gateway_report_path(candidate).write_text(
        json.dumps(
            {
                "success": True,
                "pet_sitcom_provenance": provenance,
            }
        ),
        encoding="utf-8",
    )
    continuity_frame = generation._pet_continuity_frame_path(
        plan,
        shot.shot_id,
    )
    _write_continuity_state(
        candidate,
        continuity_frame,
        source_duration=float(shot.generation_duration_seconds),
        edit_duration=float(shot.duration_seconds),
    )
    continuity_state = json.loads(
        generation._pet_continuity_state_path(continuity_frame).read_text()
    )
    entry = {
        "candidate_number": 2,
        "status": "selected",
        "video_path": str(candidate.resolve()),
        "video_sha256": _sha256(candidate),
        "prompt_sha256": provenance["prompt_sha256"],
        "reference_paths": provenance["reference_paths"],
        "reference_sha256": provenance["reference_sha256"],
        "dependency_video_sha256": provenance["dependency_video_sha256"],
        "source_tts_sha256": provenance["source_tts_sha256"],
        "reference_audio_sha256": provenance["reference_audio_sha256"],
        "continuity_frame_path": str(continuity_frame.resolve()),
        "continuity_sidecar_path": str(
            generation._pet_continuity_state_path(
                continuity_frame
            ).resolve()
        ),
        "continuity_frame_sha256": _sha256(continuity_frame),
        "continuity_timestamp_seconds": continuity_state[
            "timestamp_seconds"
        ],
        "selected_at": "2026-07-24T00:00:00+00:00",
    }
    document.setdefault("history", {}).setdefault(shot_id, []).append(
        selections[shot_id]
    )
    selections[shot_id] = entry
    plan.selection_path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    return entry


def test_source_phase_is_default_and_does_not_require_final_outputs(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    manifest = review.build_pet_sitcom_evidence(
        plan,
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert manifest["phase"] == "source"
    assert len(manifest["shot_sheets"]) == 10
    assert set(manifest["mouth_sequences"]) == CAT_SHOT_IDS
    assert set(manifest["paw_sequences"]) == {"shot_01", "shot_09"}
    assert set(manifest["prop_sequences"]) == {
        "bag",
        "orange_tail",
        "crumbs",
        "mirror",
    }
    assert set(manifest["prop_sequences"]["bag"]) == {
        "shot_01",
        "shot_02",
        "shot_06",
    }
    assert set(manifest["prop_sequences"]["crumbs"]) == {
        "shot_07",
        "shot_08",
        "shot_09",
        "shot_10",
    }
    assert len(manifest["continuity_comparisons"]) == 10
    pairs = {
        (item["previous_shot_id"], item["current_shot_id"])
        for item in manifest["continuity_comparisons"]
    }
    assert ("shot_05", "shot_07") in pairs
    assert ("shot_06", "shot_07") in pairs
    assert not plan.clean_output.exists()
    assert not (plan.output_dir / "evidence" / "final_manifest.json").exists()
    owner = json.loads(
        (plan.output_dir / "owner_native_audio_review.json").read_text()
    )
    assert owner["reviewed"] is False and owner["verified"] is False
    shot_template = json.loads(plan.shot_review_path.read_text())
    assert shot_template["schema_version"].endswith(".v4")
    assert set(shot_template["mouth_timing"]) == CAT_SHOT_IDS
    assert all(record["passed"] is None for record in shot_template["shots"].values())
    assert all(
        gate
        == {
            "passed": None,
            "notes": "",
            "timestamps_seconds": [],
            "issue_codes": [],
        }
        for record in shot_template["shots"].values()
        for gate in record["gates"].values()
    )


def test_final_phase_requires_current_source_evidence(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_finals(plan)
    with pytest.raises(
        review.PetSitcomReviewError,
        match="[Ss]ource evidence",
    ):
        _build_final(plan, FakeMediaRunner(), monkeypatch)


def test_final_phase_samples_sixteen_frames_across_full_cut(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _write_finals(plan)
    final = _build_final(plan, runner, monkeypatch)

    times = final["whole_cut_sheet"]["timestamps_seconds"]
    assert len(times) == 16
    assert times[0] == 0.0
    assert times[-1] == pytest.approx(53.95)
    assert times[8] > 27
    command = next(
        item
        for item in runner.commands
        if ".whole_cut." in Path(item[-1]).name
    )
    command_times = [
        float(command[index + 1])
        for index, value in enumerate(command)
        if value == "-ss"
    ]
    assert command_times == times
    assert review.validate_final_evidence(plan)["valid"] is True


def test_source_sampling_timestamps_match_explicit_ffmpeg_seeks(
    plan, wired_sources, monkeypatch
):
    runner = FakeMediaRunner()
    source = _build_source(plan, runner, monkeypatch)
    shot = source["shot_sheets"][0]
    command = next(
        item
        for item in runner.commands
        if ".shot_01." in Path(item[-1]).name
    )
    command_times = [
        float(command[index + 1])
        for index, value in enumerate(command)
        if value == "-ss"
    ]
    assert command_times == shot["timestamps_seconds"]
    assert command_times[0] == 0.0
    assert command_times[-1] == pytest.approx(5.95)


def test_continuity_sampling_uses_validated_endpoint_and_dependency_edges(
    plan, wired_sources, monkeypatch
):
    runner = FakeMediaRunner()
    manifest = _build_source(plan, runner, monkeypatch)

    comparison = manifest["continuity_comparisons"][0]
    assert comparison["label"] == "state_match"
    assert comparison["previous_timestamps_seconds"] == [4.86, 5.04, 5.12]
    assert comparison["current_timestamps_seconds"] == [0.04, 0.12, 0.3]
    assert len(comparison["frame_paths"]) == 6
    assert len(comparison["frame_sha256"]) == 6

    dual = {
        (item["previous_shot_id"], item["current_shot_id"]): item
        for item in manifest["continuity_comparisons"]
    }
    assert dual[("shot_05", "shot_07")]["label"] == (
        "main_axis_and_pose_return"
    )
    assert dual[("shot_06", "shot_07")]["label"] == "tail_direction_match"


def test_source_evidence_validator_rejects_changed_image_hash(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    manifest = _build_source(plan, FakeMediaRunner(), monkeypatch)
    image = Path(manifest["shot_sheets"][0]["evidence_path"])
    image.write_bytes(b"changed")
    with pytest.raises(review.PetSitcomReviewError, match="stale"):
        review.validate_source_evidence(plan)


def test_source_evidence_validator_rejects_extra_records(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["shot_sheets"].append(deepcopy(manifest["shot_sheets"][0]))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(review.PetSitcomReviewError, match="exactly ten"):
        review.validate_source_evidence(plan)


def test_source_evidence_validator_rejects_stale_selected_source(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    wired_sources["shot_03"]["path"].write_bytes(b"replacement")
    with pytest.raises(review.PetSitcomReviewError, match="stale"):
        review.validate_source_evidence(plan)


@pytest.mark.parametrize(
    ("runner", "rejects_before_ffmpeg"),
    [
        (FakeMediaRunner(omit_video_duration=True), True),
        (FakeMediaRunner(video_duration=math.nan), True),
        (FakeMediaRunner(video_duration=math.inf), True),
        (FakeMediaRunner(video_duration=True), True),
        (FakeMediaRunner(video_duration=0.0), True),
        (
            FakeMediaRunner(
                source_duration=6.0,
                video_duration=0.25,
            ),
            False,
        ),
    ],
)
def test_source_qc_rejects_invalid_explicit_video_stream_duration_before_ffmpeg(
    plan,
    wired_sources,
    monkeypatch,
    runner,
    rejects_before_ffmpeg,
):
    import factory.pet_sitcom_review as review

    with pytest.raises(
        review.PetSitcomReviewError,
        match="video stream duration|technical|duration",
    ):
        _build_source(plan, runner, monkeypatch)

    ran_ffmpeg = any(
        Path(command[0]).name == "ffmpeg"
        for command in runner.commands
    )
    assert ran_ffmpeg is not rejects_before_ffmpeg


def test_source_evidence_validator_rejects_symlinked_artifact(
    plan, wired_sources, monkeypatch, tmp_path
):
    import factory.pet_sitcom_review as review

    manifest = _build_source(plan, FakeMediaRunner(), monkeypatch)
    image = Path(manifest["shot_sheets"][0]["evidence_path"])
    external = tmp_path / "external.png"
    external.write_bytes(image.read_bytes())
    image.unlink()
    image.symlink_to(external)
    with pytest.raises(review.PetSitcomReviewError, match="symlink"):
        review.validate_source_evidence(plan)


def test_continuity_validator_rejects_persisted_current_sample_past_video(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    qc_path = plan.output_dir / "evidence" / "source_technical_qc.json"
    qc = json.loads(qc_path.read_text())
    record = next(item for item in qc["records"] if item["name"] == "shot_02")
    record["video_duration_seconds"] = 0.20
    video_stream = next(
        item
        for item in record["ffprobe"]["streams"]
        if item["codec_type"] == "video"
    )
    video_stream["duration"] = "0.20"
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    manifest_path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_technical_qc_sha256"] = _sha256(qc_path)
    timestamps = review._sample_timestamps(9, 0.20)
    next(
        item
        for item in manifest["shot_sheets"]
        if item["shot_id"] == "shot_02"
    )["timestamps_seconds"] = timestamps
    manifest["prop_sequences"]["bag"]["shot_02"][
        "timestamps_seconds"
    ] = timestamps
    for comparison in manifest["continuity_comparisons"]:
        if comparison["current_shot_id"] == "shot_02":
            comparison["current_video_duration_seconds"] = 0.20
        if comparison["previous_shot_id"] == "shot_02":
            comparison["previous_video_duration_seconds"] = 0.20
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="Continuity.*timestamp|video duration",
    ):
        review.validate_source_evidence(plan)


def test_continuity_validator_rechecks_current_selection_edit_endpoint(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    manifest = _build_source(plan, FakeMediaRunner(), monkeypatch)
    item = manifest["continuity_comparisons"][0]
    wired_sources[item["previous_shot_id"]]["edit_duration_seconds"] = 5.10

    with pytest.raises(
        review.PetSitcomReviewError,
        match="edit endpoint|sidecar",
    ):
        review.validate_source_evidence(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("continuity_source_video_duration_seconds", True),
        ("continuity_source_video_duration_seconds", math.nan),
        ("continuity_source_video_duration_seconds", math.inf),
        ("continuity_source_video_duration_seconds", -1.0),
        ("continuity_timestamp_seconds", True),
        ("continuity_timestamp_seconds", math.nan),
        ("continuity_timestamp_seconds", math.inf),
        ("continuity_timestamp_seconds", -1.0),
    ],
)
def test_continuity_validator_rejects_invalid_sidecar_timing_values(
    plan, wired_sources, monkeypatch, field, value
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    wired_sources["shot_01"][field] = value

    with pytest.raises(
        review.PetSitcomReviewError,
        match="sidecar.*duration|sidecar.*timestamp",
    ):
        review.validate_source_evidence(plan)


def test_real_selection_sidecar_duration_must_match_current_source_qc(
    plan, monkeypatch
):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan)
    shot = plan.shots[2]
    frame = generation._pet_continuity_frame_path(plan, shot.shot_id)
    sidecar_path = generation._pet_continuity_state_path(frame)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["source_video_duration_seconds"] = 999.0
    sidecar["timestamp_seconds"] = min(
        sidecar["edit_duration_seconds"] - 0.08,
        sidecar["source_video_duration_seconds"] - 0.08,
    )
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    selection = json.loads(plan.selection_path.read_text())
    selection["shots"][shot.shot_id]["continuity_timestamp_seconds"] = (
        sidecar["timestamp_seconds"]
    )
    plan.selection_path.write_text(json.dumps(selection), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="sidecar.*duration|source QC",
    ):
        review.validate_pet_shot_review(plan, shot.shot_id)


@pytest.mark.parametrize(
    ("delta", "passes"),
    [(0.0009, True), (0.0011, False)],
)
def test_sidecar_source_duration_uses_one_millisecond_ffprobe_tolerance(
    plan, wired_sources, monkeypatch, delta, passes
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    source = wired_sources["shot_01"]
    source["continuity_source_video_duration_seconds"] = 6.0 + delta
    source["continuity_timestamp_seconds"] = min(
        source["edit_duration_seconds"] - 0.08,
        source["continuity_source_video_duration_seconds"] - 0.08,
    )

    if passes:
        assert review.validate_source_evidence(plan)["valid"] is True
    else:
        with pytest.raises(
            review.PetSitcomReviewError,
            match="sidecar.*duration|source QC",
        ):
            review.validate_source_evidence(plan)


def test_manifest_duration_forgery_cannot_mask_sidecar_qc_mismatch(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    source = wired_sources["shot_01"]
    source["continuity_source_video_duration_seconds"] = 999.0
    source["continuity_timestamp_seconds"] = min(
        source["edit_duration_seconds"] - 0.08,
        source["continuity_source_video_duration_seconds"] - 0.08,
    )
    manifest_path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    comparison = next(
        item
        for item in manifest["continuity_comparisons"]
        if item["previous_shot_id"] == "shot_01"
    )
    comparison["previous_duration_seconds"] = 999.0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="sidecar.*duration|source QC",
    ):
        review.validate_source_evidence(plan)


def test_real_selection_rejects_sidecar_timestamp_not_derived_from_duration(
    plan, monkeypatch
):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan)
    shot = plan.shots[2]
    frame = generation._pet_continuity_frame_path(plan, shot.shot_id)
    sidecar_path = generation._pet_continuity_state_path(frame)
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["timestamp_seconds"] -= 0.01
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    selection = json.loads(plan.selection_path.read_text())
    selection["shots"][shot.shot_id]["continuity_timestamp_seconds"] = (
        sidecar["timestamp_seconds"]
    )
    plan.selection_path.write_text(json.dumps(selection), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="continuity|sidecar|provenance",
    ):
        review.validate_pet_shot_review(plan, shot.shot_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_seconds", True),
        ("duration_seconds", math.nan),
        ("duration_seconds", math.inf),
        ("duration_seconds", -1.0),
        ("video_duration_seconds", True),
        ("video_duration_seconds", math.nan),
        ("video_duration_seconds", math.inf),
        ("video_duration_seconds", -1.0),
    ],
)
def test_source_qc_persisted_validator_rejects_invalid_durations(
    plan, wired_sources, monkeypatch, field, value
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    qc_path = plan.output_dir / "evidence" / "source_technical_qc.json"
    qc = json.loads(qc_path.read_text())
    qc["records"][0][field] = value
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    manifest_path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_technical_qc_sha256"] = _sha256(qc_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match="QC duration"):
        review.validate_source_evidence(plan)


def test_source_qc_persisted_validator_rejects_extra_record_field(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    qc_path = plan.output_dir / "evidence" / "source_technical_qc.json"
    qc = json.loads(qc_path.read_text())
    qc["records"][0]["forged"] = True
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    manifest_path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_technical_qc_sha256"] = _sha256(qc_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match="fields"):
        review.validate_source_evidence(plan)


def test_structured_manual_reviews_derive_overall_pass(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan)
    result = review.validate_pet_shot_reviews(plan)
    assert result["passed"] is True
    assert result["failed_shots"] == []


def test_structured_failed_gate_requires_notes_timestamp_and_retry_reason(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan, failed_shot="shot_09")
    assert review.validate_pet_shot_reviews(plan)["failed_shots"] == ["shot_09"]

    record = payload["shots"]["shot_09"]
    record["gates"]["paws_and_feline_anatomy"]["timestamps_seconds"] = []
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(review.PetSitcomReviewError, match="timestamp"):
        review.validate_pet_shot_reviews(plan)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["shots"]["shot_01"]["gates"].pop(
                "planned_action"
            ),
            "hard gates",
        ),
        (
            lambda payload: payload["shots"]["shot_01"]["gates"].update(
                {
                    "forged": {
                        "passed": True,
                        "notes": "x",
                        "timestamps_seconds": [],
                        "issue_codes": [],
                    }
                }
            ),
            "hard gates",
        ),
        (
            lambda payload: payload["shots"]["shot_01"].update({"passed": False}),
            "contradicts",
        ),
        (
            lambda payload: payload["shots"]["shot_01"]["gates"][
                "planned_action"
            ].update({"notes": ""}),
            "notes",
        ),
        (
            lambda payload: payload["shots"]["shot_01"]["gates"][
                "planned_action"
            ].update({"timestamps_seconds": [6.1]}),
            "timestamp",
        ),
        (
            lambda payload: payload["shots"]["shot_01"]["gates"][
                "planned_action"
            ].update({"issue_codes": ["continuity"]}),
            "issue code",
        ),
    ],
)
def test_structured_manual_review_rejects_forged_or_contradictory_state(
    plan, wired_sources, monkeypatch, mutate, message
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    mutate(payload)
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(review.PetSitcomReviewError, match=message):
        review.validate_pet_shot_reviews(plan)


def test_failed_gate_requires_allowed_issue_code_matching_retry_reason(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan, failed_shot="shot_09")
    gate = payload["shots"]["shot_09"]["gates"]["paws_and_feline_anatomy"]
    gate["issue_codes"] = []
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(review.PetSitcomReviewError, match="issue code"):
        review.validate_pet_shot_reviews(plan)

    gate["issue_codes"] = ["identity"]
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(review.PetSitcomReviewError, match="gate|retry"):
        review.validate_pet_shot_reviews(plan)


def test_review_requires_exact_mouth_timing_for_cat_speaking_shots(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    document = json.loads(plan.shot_review_path.read_text())

    assert set(document["mouth_timing"]) == CAT_SHOT_IDS
    for shot_id, record in document["mouth_timing"].items():
        assert set(record) == review._MOUTH_TIMING_FIELDS
        assert (
            record["selected_mp4_sha256"]
            == wired_sources[shot_id]["sha256"]
        )
        assert (
            record["drive_audio_sha256"]
            == wired_sources[shot_id]["reference_audio_sha256"]
        )
        assert (
            record["audio_onset_seconds"]
            == wired_sources[shot_id]["audio_onset_seconds"]
        )
        assert (
            record["audio_offset_seconds"]
            == wired_sources[shot_id]["audio_offset_seconds"]
        )
        assert record["mouth_onset_seconds"] is None
        assert record["mouth_offset_seconds"] is None
        assert record["onset_error_seconds"] is None
        assert record["offset_error_seconds"] is None
        assert record["max_onset_error_seconds"] == 0.25
        assert record["max_offset_error_seconds"] == 0.25
        assert record["reviewed"] is False
        assert record["passed"] is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["mouth_timing"].pop("shot_04"),
            "mouth timing",
        ),
        (
            lambda payload: payload["mouth_timing"].update(
                {"shot_02": deepcopy(payload["mouth_timing"]["shot_03"])}
            ),
            "mouth timing",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"mouth_onset_seconds": 1.15, "onset_error_seconds": 0.5}
            ),
            "mouth onset",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"mouth_offset_seconds": 3.65, "offset_error_seconds": 0.4}
            ),
            "mouth offset",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"audio_onset_seconds": True}
            ),
            "finite",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"audio_onset_seconds": math.nan}
            ),
            "finite",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"audio_onset_seconds": -0.01}
            ),
            "non-negative",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"selected_mp4_sha256": "0" * 64}
            ),
            "stale",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"drive_audio_sha256": "0" * 64}
            ),
            "drive audio|stale",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"max_onset_error_seconds": 0.3}
            ),
            "threshold",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"onset_error_seconds": 0.01}
            ),
            "onset error",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"no_silent_mouth_flapping": False}
            ),
            "silent mouth flapping",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"no_closed_mouth_during_speech": False}
            ),
            "closed mouth",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"reviewed": False}
            ),
            "not completed",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"passed": False}
            ),
            "must pass",
        ),
        (
            lambda payload: payload["mouth_timing"]["shot_04"].update(
                {"forged": True}
            ),
            "fields",
        ),
    ],
)
def test_mouth_timing_rejects_missing_stale_or_forged_measurements(
    plan, wired_sources, monkeypatch, mutate, message
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    mutate(payload)
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match=message):
        review.validate_pet_shot_reviews(plan)


def test_mouth_timing_rejects_replaced_drive_audio(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan)
    Path(wired_sources["shot_04"]["reference_audio_path"]).write_bytes(
        b"replaced-drive"
    )

    with pytest.raises(
        review.PetSitcomReviewError,
        match="drive audio|stale",
    ):
        review.validate_pet_shot_reviews(plan)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {
                "audio_onset_seconds": 0.0,
                "mouth_onset_seconds": 0.0,
                "audio_offset_seconds": 0.0,
                "mouth_offset_seconds": 0.0,
                "onset_error_seconds": 0.0,
                "offset_error_seconds": 0.0,
            },
            "interval|canonical",
        ),
        (
            {
                "audio_onset_seconds": 0.60,
                "mouth_onset_seconds": 0.60,
                "audio_offset_seconds": 0.60,
                "mouth_offset_seconds": 0.70,
                "onset_error_seconds": 0.0,
                "offset_error_seconds": 0.10,
            },
            "interval|canonical",
        ),
        (
            {
                "audio_onset_seconds": 0.55,
                "mouth_onset_seconds": 0.65,
                "audio_offset_seconds": 0.75,
                "mouth_offset_seconds": 0.65,
                "onset_error_seconds": 0.10,
                "offset_error_seconds": 0.10,
            },
            "interval",
        ),
        (
            {
                "audio_onset_seconds": 0.55,
                "mouth_onset_seconds": 0.55,
                "audio_offset_seconds": 0.551,
                "mouth_offset_seconds": 0.551,
                "onset_error_seconds": 0.0,
                "offset_error_seconds": 0.0,
            },
            "interval|canonical",
        ),
        (
            {
                "audio_onset_seconds": 0.75,
                "mouth_onset_seconds": 0.75,
                "audio_offset_seconds": 0.55,
                "mouth_offset_seconds": 0.55,
                "onset_error_seconds": 0.0,
                "offset_error_seconds": 0.0,
            },
            "order|interval",
        ),
    ],
)
def test_mouth_timing_rejects_empty_tiny_or_reversed_intervals(
    plan, wired_sources, monkeypatch, values, message
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    payload["mouth_timing"]["shot_03"].update(values)
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match=message):
        review.validate_pet_shot_reviews(plan)


def test_mouth_timing_audio_endpoints_must_match_canonical_asset_timing(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    record = payload["mouth_timing"]["shot_03"]
    record.update(
        {
            "audio_onset_seconds": 0.65,
            "mouth_onset_seconds": 0.65,
            "audio_offset_seconds": 3.35,
            "mouth_offset_seconds": 3.35,
            "onset_error_seconds": 0.0,
            "offset_error_seconds": 0.0,
        }
    )
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match="canonical"):
        review.validate_pet_shot_reviews(plan)


def test_incremental_mouth_timing_rejects_manual_audio_endpoint_mismatch(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    payload = _complete_shot_reviews(plan)
    record = payload["mouth_timing"]["shot_03"]
    record.update(
        {
            "audio_onset_seconds": 0.65,
            "mouth_onset_seconds": 0.65,
            "audio_offset_seconds": 3.35,
            "mouth_offset_seconds": 3.35,
            "onset_error_seconds": 0.0,
            "offset_error_seconds": 0.0,
        }
    )
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match="canonical"):
        review.validate_pet_shot_review(plan, "shot_03")


def test_task2_manifest_timing_forgery_is_rejected_against_current_wav(
    plan, monkeypatch
):
    import factory.pet_sitcom_audio_first as audio_first

    _write_current_task2_manifest(plan, monkeypatch)
    assert audio_first.load_pet_speech_assets(plan)
    manifest = json.loads(plan.audio_manifest_path.read_text())
    record = next(
        item for item in manifest["assets"] if item["shot_id"] == "shot_03"
    )
    record["duration_seconds"] = 1.25
    record["absolute_end_seconds"] = (
        record["absolute_start_seconds"] + 1.25
    )
    plan.audio_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        audio_first.PetSitcomAudioFirstError,
        match="duration no longer matches",
    ):
        audio_first.load_pet_speech_assets(plan)


@pytest.mark.skipif(
    shutil.which("ffprobe") is None,
    reason="system ffprobe is required for the real audio integration test",
)
def test_real_ffprobe_task2_forgery_fails_through_task5_review_path(
    plan, monkeypatch
):
    import factory.media_validation as media_validation
    import factory.pet_sitcom_audio_first as audio_first
    import factory.pet_sitcom_review as review

    assert audio_first.probe_media is media_validation.probe_media
    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    selections = _bind_real_task2_audio_to_partial_task5(
        plan,
        selections,
        monkeypatch,
    )
    shot = plan.shots[2]
    source = review._validate_selection_source(plan, shot, selections)
    assert source["source_tts_sha256"] == selections["shot_03"][
        "source_tts_sha256"
    ]
    assert source["reference_audio_sha256"] == selections["shot_03"][
        "reference_audio_sha256"
    ]

    old_review = b'{"sentinel":"preserve-old-review"}'
    plan.shot_review_path.write_bytes(old_review)
    selection_before = plan.selection_path.read_bytes()
    manifest = json.loads(plan.audio_manifest_path.read_text())
    record = next(
        item for item in manifest["assets"] if item["shot_id"] == "shot_03"
    )
    record["duration_seconds"] = 1.25
    record["absolute_end_seconds"] = (
        record["absolute_start_seconds"] + 1.25
    )
    plan.audio_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="Task 5 provenance chain",
    ) as caught:
        review.build_pet_shot_evidence(
            plan,
            "shot_03",
            command_runner=FakeMediaRunner(),
            ffmpeg_bin="ffmpeg",
            ffprobe_bin="ffprobe",
        )

    assert isinstance(
        caught.value.__cause__,
        audio_first.PetSitcomAudioFirstError,
    )
    assert "speech duration no longer matches its WAV" in str(
        caught.value.__cause__
    )
    assert plan.selection_path.read_bytes() == selection_before
    assert plan.shot_review_path.read_bytes() == old_review
    assert not (
        plan.output_dir / "evidence" / "incremental" / "shot_03.json"
    ).exists()


def test_manual_gates_include_transition_and_jump_failures(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    assert {
        "action_preparation_execution_settle",
        "screen_position_and_eyeline",
        "music_transition_motivation",
        "physical_transition_logic",
        "camera_stability_and_unexplained_cuts",
    } <= set(review.SHOT_REVIEW_GATES)
    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    record = payload["shots"]["shot_05"]
    record["gates"]["camera_stability_and_unexplained_cuts"] = {
        "passed": False,
        "notes": "unexplained perceptual jump inside the generated shot",
        "timestamps_seconds": [2.1],
        "issue_codes": ["continuity"],
    }
    record["passed"] = False
    record["retry_reason"] = "continuity"
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    assert review.validate_pet_shot_reviews(plan)["failed_shots"] == [
        "shot_05"
    ]


@pytest.mark.parametrize(
    ("shot_id", "duration", "audio_streams", "video_streams", "passes"),
    [
        ("shot_03", 7.35, 1, 1, True),
        ("shot_03", 7.351, 1, 1, False),
        ("shot_03", 7.0, 0, 1, False),
        ("shot_03", 7.0, 2, 1, False),
        ("shot_01", 6.0, 0, 1, True),
        ("shot_01", 6.0, 1, 1, False),
        ("shot_02", 4.0, 0, 1, True),
        ("shot_02", 4.0, 0, 2, False),
    ],
)
def test_source_qc_uses_shot_duration_and_exact_stream_contract(
    plan,
    shot_id,
    duration,
    audio_streams,
    video_streams,
    passes,
):
    import factory.pet_sitcom_review as review

    source = plan.output_dir / "verified" / f"{shot_id}.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    record = review._technical_record(
        plan,
        shot_id,
        {"path": source, "sha256": _sha256(source)},
        final=False,
        command_runner=FakeMediaRunner(
            source_duration=duration,
            audio_streams_by_shot={shot_id: audio_streams},
            video_streams=video_streams,
        ),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert record["passed"] is passes
    assert (
        record["freezedetect"]["filter"]
        == "freezedetect=n=-50dB:d=0.35"
    )


def test_source_qc_uses_local_recut_output_duration(plan):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    shot = next(item for item in plan.shots if item.shot_id == "shot_04")
    source = generation._pet_candidate_path(shot, 3)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"local-recut")
    generation._pet_candidate_state_path(source).write_text(
        json.dumps(
            {
                "schema_version": generation.PET_LOCAL_RECUT_SCHEMA,
                "recipe": {"output_duration_seconds": 4.2},
            }
        ),
        encoding="utf-8",
    )

    record = review._technical_record(
        plan,
        shot.shot_id,
        {
            "path": source,
            "sha256": _sha256(source),
            "candidate_number": 3,
        },
        final=False,
        command_runner=FakeMediaRunner(
            source_duration=4.2,
            audio_streams_by_shot={shot.shot_id: 1},
        ),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert record["passed"] is True
    assert record["duration_seconds"] == 4.2


def test_source_qc_limits_black_and_freeze_analysis_to_edit_window(plan):
    import factory.pet_sitcom_review as review

    source = plan.output_dir / "verified" / "shot_01.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    runner = FakeMediaRunner(source_duration=6.0)

    record = review._technical_record(
        plan,
        "shot_01",
        {"path": source, "sha256": _sha256(source)},
        final=False,
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    detection_commands = [
        command
        for command in runner.commands
        if any(
            token.startswith(("blackdetect=", "freezedetect="))
            for token in command
        )
    ]
    assert record["passed"] is True
    assert len(detection_commands) == 2
    for command in detection_commands:
        assert command[command.index("-t") + 1] == "5.200"


@pytest.mark.parametrize(
    ("black", "freeze", "passes"),
    [
        (0.08, 0.35, True),
        (0.0801, 0.0, False),
        (0.0, 0.3501, False),
    ],
)
def test_source_qc_black_and_freeze_limits_are_strict(
    plan, black, freeze, passes
):
    import factory.pet_sitcom_review as review

    source = plan.output_dir / "verified" / "shot_03.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    record = review._technical_record(
        plan,
        "shot_03",
        {"path": source, "sha256": _sha256(source)},
        final=False,
        command_runner=FakeMediaRunner(
            source_duration=7.0,
            black_duration=black,
            freeze_duration=freeze,
        ),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert record["passed"] is passes


def test_freeze_parser_handles_spaces_events_and_open_ended_eof(plan):
    import factory.pet_sitcom_review as review

    media = plan.output_dir / "freeze.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"media")

    def completed_runner(_command, **_kwargs):
        return SimpleNamespace(
            stdout="",
            stderr=(
                "[freezedetect] freeze_start: 0\n"
                "[freezedetect] freeze_end: 1 | freeze_duration: 1\n"
            ),
        )

    complete = review._detect(
        completed_runner,
        "ffmpeg",
        media,
        "freezedetect=n=-50dB:d=0.35",
        "freeze_duration",
        source_duration=2.0,
    )
    assert complete["starts_seconds"] == [0.0]
    assert complete["ends_seconds"] == [1.0]
    assert complete["durations_seconds"] == [1.0]

    def duration_only_runner(_command, **_kwargs):
        return SimpleNamespace(
            stdout="",
            stderr=(
                "[freezedetect] freeze_start: 0.25\n"
                "[freezedetect] freeze_duration: 1.25\n"
            ),
        )

    duration_only = review._detect(
        duration_only_runner,
        "ffmpeg",
        media,
        "freezedetect=n=-50dB:d=0.35",
        "freeze_duration",
        source_duration=2.0,
    )
    assert duration_only["starts_seconds"] == [0.25]
    assert duration_only["ends_seconds"] == [1.5]
    assert duration_only["durations_seconds"] == [1.25]

    def eof_runner(_command, **_kwargs):
        return SimpleNamespace(
            stdout="",
            stderr="[freezedetect] freeze_start: 0\n",
        )

    open_ended = review._detect(
        eof_runner,
        "ffmpeg",
        media,
        "freezedetect=n=-50dB:d=0.35",
        "freeze_duration",
        source_duration=2.0,
    )
    assert open_ended["starts_seconds"] == [0.0]
    assert open_ended["ends_seconds"] == [2.0]
    assert open_ended["durations_seconds"] == [2.0]


def test_loudness_requires_true_peak_not_sample_peak(plan):
    import factory.pet_sitcom_review as review

    media = plan.output_dir / "final.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"media")
    loudness = review._loudness(FakeMediaRunner(true_peak=0.0), "ffmpeg", media)
    assert loudness == {
        "filter": "ebur128=peak=true",
        "integrated_lufs": -16.0,
        "true_peak_dbtp": 0.0,
        "measurement_available": True,
    }
    errors = review._technical_errors(
        _probe_payload(70.0),
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": 0.0},
        loudness,
        True,
        media,
    )
    assert "true peak exceeds -1.5 dBTP tolerance" in errors


@pytest.mark.parametrize(
    ("true_peak", "fails"),
    [(-1.50, False), (-1.49, True), (-1.40, True)],
)
def test_true_peak_limit_is_strict(plan, true_peak, fails):
    import factory.pet_sitcom_review as review

    media = plan.output_dir / "strict-peak.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"media")
    errors = review._technical_errors(
        _probe_payload(70.0),
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": 0.0},
        {
            "measurement_available": True,
            "integrated_lufs": -16.0,
            "true_peak_dbtp": true_peak,
        },
        True,
        media,
    )
    assert ("true peak exceeds -1.5 dBTP tolerance" in errors) is fails


def test_freeze_limit_is_exactly_point_three_five_seconds(plan):
    import factory.pet_sitcom_review as review

    media = plan.output_dir / "source.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"media")
    allowed = review._technical_errors(
        _probe_payload(5.0),
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": 0.35},
        None,
        False,
        media,
    )
    rejected = review._technical_errors(
        _probe_payload(5.0),
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": 0.350001},
        None,
        False,
        media,
    )

    assert "freeze exceeds 0.35 seconds" not in allowed
    assert "freeze exceeds 0.35 seconds" in rejected


def test_source_video_stream_must_cover_the_full_edit_window(plan):
    import factory.pet_sitcom_review as review

    media = plan.output_dir / "one-frame-short.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"media")
    errors = review._technical_errors(
        _probe_payload(4.2, video_duration=4.166667),
        {"max_duration_seconds": 0.0},
        {"max_duration_seconds": 0.0},
        None,
        False,
        media,
        expected_duration=4.2,
        expected_audio_streams=1,
        minimum_video_duration=4.2,
    )

    assert "video stream is shorter than the edit duration" in errors


def test_owner_template_stays_pending_and_keeps_task4_exact_schema(
    plan, wired_sources
):
    import factory.pet_sitcom_review as review

    path = review.write_owner_native_audio_review_template(plan)
    template = json.loads(path.read_text())
    assert set(template) == review._OWNER_TOP_FIELDS
    assert template["reviewed"] is False
    assert template["verified"] is False
    assert set(template["shots"]) == {"shot_01", "shot_06"}
    assert all(
        set(record) == review._OWNER_RECORD_FIELDS
        for record in template["shots"].values()
    )


def test_unchanged_mouth_timing_refreshes_canonical_fields_only(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    payload = _complete_shot_reviews(plan)
    record = payload["mouth_timing"]["shot_03"]
    record.update(
        {
            "audio_onset_seconds": 0.65,
            "audio_offset_seconds": 3.35,
            "mouth_onset_seconds": 0.65,
            "mouth_offset_seconds": 3.35,
            "onset_error_seconds": 0.0,
            "offset_error_seconds": 0.0,
        }
    )
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    refreshed = json.loads(plan.shot_review_path.read_text())
    current = refreshed["mouth_timing"]["shot_03"]

    assert current["audio_onset_seconds"] == 0.55
    assert current["audio_offset_seconds"] == 3.25
    assert current["mouth_onset_seconds"] == 0.65
    assert current["mouth_offset_seconds"] == 3.35
    assert current["onset_error_seconds"] == pytest.approx(0.10)
    assert current["offset_error_seconds"] == pytest.approx(0.10)
    assert current["reviewed"] is True
    assert current["passed"] is True


@pytest.mark.parametrize("changed_binding", ("candidate", "audio"))
def test_candidate_or_audio_change_resets_only_human_mouth_timing(
    plan, wired_sources, monkeypatch, changed_binding
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    completed = _complete_shot_reviews(plan)
    unchanged = deepcopy(completed["mouth_timing"]["shot_03"])
    source = wired_sources["shot_08"]
    if changed_binding == "candidate":
        replacement = (
            plan.output_dir / "candidates" / "shot_08" / "candidate_002.mp4"
        )
        replacement.parent.mkdir(parents=True, exist_ok=True)
        replacement.write_bytes(b"shot-08-candidate-two")
        source.update(
            {
                "path": replacement,
                "sha256": _sha256(replacement),
                "candidate_number": 2,
            }
        )
    else:
        drive = Path(source["reference_audio_path"])
        drive.write_bytes(b"shot-08-new-drive-audio")
        source.update(
            {
                "reference_audio_sha256": _sha256(drive),
                "audio_onset_seconds": 0.60,
                "audio_offset_seconds": 3.30,
            }
        )

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    current = json.loads(plan.shot_review_path.read_text())
    timing = current["mouth_timing"]["shot_08"]

    assert current["mouth_timing"]["shot_03"] == unchanged
    assert timing["selected_mp4_sha256"] == source["sha256"]
    assert timing["drive_audio_sha256"] == source["reference_audio_sha256"]
    assert timing["audio_onset_seconds"] == source["audio_onset_seconds"]
    assert timing["audio_offset_seconds"] == source["audio_offset_seconds"]
    assert timing["mouth_onset_seconds"] is None
    assert timing["mouth_offset_seconds"] is None
    assert timing["onset_error_seconds"] is None
    assert timing["offset_error_seconds"] is None
    assert timing["no_silent_mouth_flapping"] is False
    assert timing["no_closed_mouth_during_speech"] is False
    assert timing["reviewed"] is False
    assert timing["passed"] is None


def test_changed_candidates_archive_reviews_and_reset_only_changed_records(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    reviews = _complete_shot_reviews(plan, failed_shot="shot_09")
    _complete_owner_review(plan)
    unchanged = deepcopy(reviews["shots"]["shot_08"])
    old_shot = deepcopy(reviews["shots"]["shot_09"])
    old_owner = json.loads(
        (plan.output_dir / "owner_native_audio_review.json").read_text()
    )["shots"]["shot_06"]

    replacements = {}
    for shot_id in ("shot_09", "shot_06"):
        replacement = (
            plan.output_dir / "candidates" / shot_id / "candidate_002.mp4"
        )
        replacement.parent.mkdir(parents=True, exist_ok=True)
        replacement.write_bytes(f"replacement-{shot_id}".encode("ascii"))
        replacements[shot_id] = {
            **wired_sources[shot_id],
            "path": replacement,
            "sha256": _sha256(replacement),
            "candidate_number": 2,
        }
        wired_sources[shot_id] = replacements[shot_id]

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    review.write_owner_native_audio_review_template(
        plan, sources=wired_sources
    )
    migrated = json.loads(plan.shot_review_path.read_text())
    assert migrated["shots"]["shot_08"] == unchanged
    assert migrated["shots"]["shot_09"]["reviewed"] is False
    assert migrated["shots"]["shot_09"]["passed"] is None
    assert (
        migrated["shots"]["shot_09"]["selected_mp4_sha256"]
        == replacements["shot_09"]["sha256"]
    )
    owner = json.loads(
        (plan.output_dir / "owner_native_audio_review.json").read_text()
    )
    assert set(owner) == review._OWNER_TOP_FIELDS
    assert set(owner["shots"]["shot_06"]) == review._OWNER_RECORD_FIELDS
    assert owner["shots"]["shot_06"]["no_native_voice"] is False

    history_dir = plan.output_dir / "evidence" / "review_history"
    history = [
        json.loads(path.read_text())
        for path in sorted(history_dir.rglob("*.json"))
    ]
    shot_archive = next(
        item
        for item in history
        if item["review_type"] == "shot" and item["shot_id"] == "shot_09"
    )
    assert shot_archive["old_candidate_number"] == 1
    assert shot_archive["old_selected_mp4_sha256"] == old_shot[
        "selected_mp4_sha256"
    ]
    assert shot_archive["review_record"]["retry_reason"] == "paw_anatomy"
    assert shot_archive["review_record"]["gates"][
        "paws_and_feline_anatomy"
    ]["issue_codes"] == ["paw_anatomy"]
    owner_archive = next(
        item
        for item in history
        if item["review_type"] == "owner" and item["shot_id"] == "shot_06"
    )
    assert owner_archive["review_record"] == old_owner

    before = sorted(
        (path.relative_to(history_dir), path.read_bytes())
        for path in history_dir.rglob("*.json")
    )
    review.write_pet_shot_review_template(plan, sources=wired_sources)
    review.write_owner_native_audio_review_template(
        plan, sources=wired_sources
    )
    after = sorted(
        (path.relative_to(history_dir), path.read_bytes())
        for path in history_dir.rglob("*.json")
    )
    assert after == before


def test_review_history_survives_same_path_local_recut(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    reviews = _complete_shot_reviews(plan, failed_shot="shot_09")
    source = Path(wired_sources["shot_09"]["path"])
    old_hash = reviews["shots"]["shot_09"]["selected_mp4_sha256"]
    source.write_bytes(b"same-slot-local-recut")
    new_hash = _sha256(source)
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "sha256": new_hash,
    }

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    history = review._validated_shot_review_history(
        plan,
        wired_sources,
        {
            shot.shot_id: {"candidate_number": 1}
            for shot in plan.shots
        },
    )

    assert len(history) == 1
    assert history[0]["old_selected_mp4_path"] == str(source.resolve())
    assert history[0]["current_selected_mp4_path"] == str(source.resolve())
    assert history[0]["old_selected_mp4_sha256"] == old_hash
    assert history[0]["current_selected_mp4_sha256"] == new_hash


def test_review_history_filename_must_bind_archived_hashes(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    replacement = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(b"candidate-two")
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": replacement,
        "sha256": _sha256(replacement),
        "candidate_number": 2,
    }
    review.write_pet_shot_review_template(plan, sources=wired_sources)
    archived = next(
        (
            plan.output_dir / "evidence" / "review_history" / "shot"
        ).rglob("*.json")
    )
    tampered = archived.with_name(
        f"{'0' * 64}_to_{wired_sources['shot_09']['sha256']}.json"
    )
    archived.rename(tampered)

    with pytest.raises(
        review.PetSitcomReviewError,
        match="filename binding is invalid",
    ):
        review._validated_shot_review_history(
            plan,
            wired_sources,
            {
                shot.shot_id: {
                    "candidate_number": (
                        2 if shot.shot_id == "shot_09" else 1
                    )
                }
                for shot in plan.shots
            },
        )


def test_local_recut_uses_failed_source_candidate_as_review_history(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    source_two = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    source_two.parent.mkdir(parents=True, exist_ok=True)
    source_two.write_bytes(b"reviewed-source-candidate")
    source_two_hash = _sha256(source_two)
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": source_two,
        "sha256": source_two_hash,
        "candidate_number": 2,
    }
    review.write_pet_shot_review_template(plan, sources=wired_sources)

    recut = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_003.mp4"
    )
    recut.write_bytes(b"local-recut")
    recut_hash = _sha256(recut)
    generation._pet_candidate_state_path(recut).write_text(
        json.dumps(
            {
                "schema_version": generation.PET_LOCAL_RECUT_SCHEMA,
                "provider": "local_ffmpeg_recut",
                "candidate_number": 3,
                "video_sha256": recut_hash,
                "source_candidates": [
                    {
                        "candidate_number": 2,
                        "video_path": str(source_two.resolve()),
                        "video_sha256": source_two_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": recut,
        "sha256": recut_hash,
        "candidate_number": 3,
    }

    history = review._validated_shot_review_history(
        plan,
        wired_sources,
        {
            shot.shot_id: {
                "candidate_number": (
                    3 if shot.shot_id == "shot_09" else 1
                )
            }
            for shot in plan.shots
        },
    )

    assert len(history) == 1
    assert history[0]["current_selected_mp4_sha256"] == source_two_hash
    matching = review._review_history_for_selection(
        plan,
        "shot_09",
        3,
        wired_sources["shot_09"],
        history,
    )
    assert matching == history


def test_local_recut_rejects_unreviewed_source_candidate(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    source_two = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    source_two.parent.mkdir(parents=True, exist_ok=True)
    source_two.write_bytes(b"reviewed-source-candidate")
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": source_two,
        "sha256": _sha256(source_two),
        "candidate_number": 2,
    }
    review.write_pet_shot_review_template(plan, sources=wired_sources)

    recut = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_003.mp4"
    )
    recut.write_bytes(b"local-recut")
    recut_hash = _sha256(recut)
    generation._pet_candidate_state_path(recut).write_text(
        json.dumps(
            {
                "schema_version": generation.PET_LOCAL_RECUT_SCHEMA,
                "provider": "local_ffmpeg_recut",
                "candidate_number": 3,
                "video_sha256": recut_hash,
                "source_candidates": [
                    {
                        "source_shot_id": "shot_09",
                        "candidate_number": 2,
                        "video_path": str(source_two.resolve()),
                        "video_sha256": "f" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": recut,
        "sha256": recut_hash,
        "candidate_number": 3,
    }

    with pytest.raises(
        review.PetSitcomReviewError,
        match="Candidate 3 review history is missing",
    ):
        review._validated_shot_review_history(
            plan,
            wired_sources,
            {
                shot.shot_id: {
                    "candidate_number": (
                        3 if shot.shot_id == "shot_09" else 1
                    )
                }
                for shot in plan.shots
            },
        )


def test_unchanged_legacy_review_is_upgraded_without_losing_human_result(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    _rewrite_shot_reviews_as_legacy_v2(plan)

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    upgraded = json.loads(plan.shot_review_path.read_text())
    assert upgraded["schema_version"] == review.SHOT_REVIEW_SCHEMA
    assert upgraded["shots"]["shot_08"]["reviewed"] is True
    assert upgraded["shots"]["shot_08"]["passed"] is True
    assert upgraded["shots"]["shot_09"]["reviewed"] is True
    assert upgraded["shots"]["shot_09"]["passed"] is False
    assert upgraded["shots"]["shot_09"]["gates"][
        "paws_and_feline_anatomy"
    ]["issue_codes"] == ["paw_anatomy"]
    assert not list(
        (plan.output_dir / "evidence" / "review_history").rglob("*.json")
    )


def test_changed_legacy_failure_is_upgraded_before_archive(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    legacy = _complete_shot_reviews(plan, failed_shot="shot_09")
    old_hash = legacy["shots"]["shot_09"]["selected_mp4_sha256"]
    _rewrite_shot_reviews_as_legacy_v2(plan)
    replacement = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(b"legacy-candidate-two")
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": replacement,
        "sha256": _sha256(replacement),
        "candidate_number": 2,
    }

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    current = json.loads(plan.shot_review_path.read_text())
    assert current["shots"]["shot_09"]["reviewed"] is False
    assert current["shots"]["shot_09"]["selected_mp4_sha256"] == _sha256(
        replacement
    )
    archives = [
        json.loads(path.read_text())
        for path in (
            plan.output_dir / "evidence" / "review_history" / "shot"
        ).rglob("*.json")
    ]
    assert len(archives) == 1
    archived = archives[0]
    assert archived["old_candidate_number"] == 1
    assert archived["old_selected_mp4_sha256"] == old_hash
    assert (
        set(
            archived["review_record"]["gates"][
                "paws_and_feline_anatomy"
            ]
        )
        == review._GATE_FIELDS
    )
    assert archived["review_record"]["gates"][
        "paws_and_feline_anatomy"
    ]["issue_codes"] == ["paw_anatomy"]


def test_changed_invalid_legacy_review_is_not_archived(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    legacy = _rewrite_shot_reviews_as_legacy_v2(plan)
    legacy["shots"]["shot_09"]["retry_reason"] = "identity"
    plan.shot_review_path.write_text(json.dumps(legacy), encoding="utf-8")
    replacement = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(b"invalid-legacy-candidate-two")
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": replacement,
        "sha256": _sha256(replacement),
        "candidate_number": 2,
    }

    review.write_pet_shot_review_template(plan, sources=wired_sources)
    current = json.loads(plan.shot_review_path.read_text())
    assert current["shots"]["shot_09"]["reviewed"] is False
    assert not list(
        (plan.output_dir / "evidence" / "review_history" / "shot").rglob(
            "*.json"
        )
    )


def test_review_migration_write_failure_preserves_primary_and_retries(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_09")
    original = plan.shot_review_path.read_bytes()
    replacement = (
        plan.output_dir / "candidates" / "shot_09" / "candidate_002.mp4"
    )
    replacement.parent.mkdir(parents=True, exist_ok=True)
    replacement.write_bytes(b"atomic-replacement")
    wired_sources["shot_09"] = {
        **wired_sources["shot_09"],
        "path": replacement,
        "sha256": _sha256(replacement),
        "candidate_number": 2,
    }
    real_write_json = review._write_json

    def fail_primary(plan_arg, path, payload):
        if path == plan.shot_review_path:
            raise review.PetSitcomReviewError("injected primary write failure")
        return real_write_json(plan_arg, path, payload)

    monkeypatch.setattr(review, "_write_json", fail_primary)
    with pytest.raises(
        review.PetSitcomReviewError,
        match="injected primary write failure",
    ):
        review.write_pet_shot_review_template(plan, sources=wired_sources)
    assert plan.shot_review_path.read_bytes() == original
    history = list(
        (plan.output_dir / "evidence" / "review_history").rglob("*.json")
    )
    assert len(history) == 1

    monkeypatch.setattr(review, "_write_json", real_write_json)
    review.write_pet_shot_review_template(plan, sources=wired_sources)
    migrated = json.loads(plan.shot_review_path.read_text())
    assert migrated["shots"]["shot_09"]["reviewed"] is False
    assert len(
        list(
            (plan.output_dir / "evidence" / "review_history").rglob(
                "*.json"
            )
        )
    ) == 1


def test_build_single_shot_evidence_uses_partial_task3_chain(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    monkeypatch.setattr(
        review._compose,
        "_selected_sources",
        lambda _plan: (_ for _ in ()).throw(
            AssertionError("full compose selection loader was called")
        ),
    )
    evidence = review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert evidence["shot_id"] == "shot_03"
    assert evidence["source_technical_qc"]["name"] == "shot_03"
    assert evidence["shot_sheet"]["shot_id"] == "shot_03"
    assert evidence["mouth_sequence"]["shot_id"] == "shot_03"
    assert evidence["paw_sequence"] is None
    assert evidence["prop_sequences"] == {}
    assert evidence["continuity_comparison"][0]["previous_shot_id"] == "shot_02"
    reviews = json.loads(plan.shot_review_path.read_text())
    assert set(reviews["shots"]) == {"shot_03"}
    assert reviews["shots"]["shot_03"]["reviewed"] is False


def test_single_shot_evidence_samples_with_video_stream_duration(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 1, monkeypatch)
    evidence = review.build_pet_shot_evidence(
        plan,
        "shot_01",
        command_runner=FakeMediaRunner(
            source_duration=6.085,
            video_duration=6.042,
        ),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )

    assert evidence["source_technical_qc"]["duration_seconds"] == 6.085
    assert evidence["shot_sheet"]["source_duration_seconds"] == 6.085
    assert evidence["shot_sheet"]["timestamps_seconds"][-1] == 5.992


def test_incremental_technical_failure_remains_reviewable_for_retry(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    evidence = review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(freeze_duration=0.625),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    assert evidence["source_technical_qc"]["passed"] is False
    assert evidence["source_technical_qc"]["errors"] == [
        "freeze exceeds 0.35 seconds"
    ]

    payload = _complete_shot_reviews(plan)
    record = payload["shots"]["shot_03"]
    record["gates"]["camera_stability_and_unexplained_cuts"] = {
        "passed": False,
        "notes": "repeated frames exceed the technical freeze limit",
        "timestamps_seconds": [2.875, 3.5],
        "issue_codes": ["continuity"],
    }
    record["passed"] = False
    record["retry_reason"] = "continuity"
    plan.shot_review_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert review.validate_pet_shot_review(plan, "shot_03") == {
        "passed": False,
        "failed": True,
        "retry_reason": "continuity",
        "candidate": 1,
        "hash": selections["shot_03"]["video_sha256"],
    }


def test_failed_speaking_shot_accepts_bound_pending_mouth_timing_for_retry(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(freeze_duration=0.625),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    pending_mouth_timing = deepcopy(
        json.loads(plan.shot_review_path.read_text())["mouth_timing"][
            "shot_03"
        ]
    )
    payload = _complete_shot_reviews(plan)
    record = payload["shots"]["shot_03"]
    record["gates"]["camera_stability_and_unexplained_cuts"] = {
        "passed": False,
        "notes": "repeated frames exceed the technical freeze limit",
        "timestamps_seconds": [2.875, 3.5],
        "issue_codes": ["continuity"],
    }
    record["gates"]["subjective_speech_start_pause_end_alignment"] = {
        "passed": False,
        "notes": "visible mouth motion starts more than one second late",
        "timestamps_seconds": [0.55, 1.94, 5.144],
        "issue_codes": ["mouth_anatomy"],
    }
    record["passed"] = False
    record["retry_reason"] = "continuity"
    payload["mouth_timing"]["shot_03"] = pending_mouth_timing
    plan.shot_review_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert review.validate_pet_shot_review(plan, "shot_03") == {
        "passed": False,
        "failed": True,
        "retry_reason": "continuity",
        "candidate": 1,
        "hash": selections["shot_03"]["video_sha256"],
    }


def test_incremental_candidate_two_archives_upgraded_legacy_failure(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    runner = FakeMediaRunner()
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan, failed_shot="shot_03")
    _rewrite_shot_reviews_as_legacy_v2(plan)
    candidate_two = _switch_partial_selection_to_candidate_two(
        plan,
        "shot_03",
        "paw_anatomy",
    )

    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=runner,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    current = json.loads(plan.shot_review_path.read_text())
    assert current["shots"]["shot_03"]["reviewed"] is False
    assert (
        current["shots"]["shot_03"]["selected_mp4_sha256"]
        == candidate_two["video_sha256"]
    )
    history_path = next(
        (
            plan.output_dir / "evidence" / "review_history" / "shot"
        ).rglob("*.json")
    )
    history = json.loads(history_path.read_text())
    failed_gate = history["review_record"]["gates"][
        "paws_and_feline_anatomy"
    ]
    assert failed_gate["issue_codes"] == ["paw_anatomy"]
    assert failed_gate["timestamps_seconds"] == [2.2]


def test_validate_single_failed_shot_with_only_partial_selection(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan, failed_shot="shot_03")

    result = review.validate_pet_shot_review(plan, "shot_03")

    assert result == {
        "passed": False,
        "failed": True,
        "retry_reason": "paw_anatomy",
        "candidate": 1,
        "hash": selections["shot_03"]["video_sha256"],
    }
    assert set(selections) == {"shot_01", "shot_02", "shot_03"}
    assert not (
        plan.output_dir / "evidence" / "source_manifest.json"
    ).exists()


def test_validate_single_shot_rejects_unknown_or_pending_review(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    with pytest.raises(review.PetSitcomReviewError, match="known shot"):
        review.validate_pet_shot_review(plan, "shot_99")

    _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    with pytest.raises(review.PetSitcomReviewError, match="human|completed"):
        review.validate_pet_shot_review(plan, "shot_03")


def test_validate_single_shot_rejects_tampered_predecessor(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan, failed_shot="shot_03")
    Path(selections["shot_02"]["video_path"]).write_bytes(
        b"tampered-predecessor"
    )

    with pytest.raises(
        review.PetSitcomReviewError,
        match="provenance|chain|stale",
    ):
        review.validate_pet_shot_review(plan, "shot_03")


@pytest.mark.parametrize("tamper", ("schema", "timestamps", "image"))
def test_validate_single_shot_rejects_stale_incremental_evidence(
    plan, monkeypatch, tamper
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    evidence = review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    _complete_shot_reviews(plan, failed_shot="shot_03")
    evidence_path = (
        plan.output_dir / "evidence" / "incremental" / "shot_03.json"
    )
    if tamper == "image":
        Path(evidence["shot_sheet"]["evidence_path"]).write_bytes(
            b"stale-image"
        )
    else:
        document = json.loads(evidence_path.read_text())
        if tamper == "schema":
            document["schema_version"] = "forged-schema"
        else:
            document["shot_sheet"]["timestamps_seconds"][1] += 0.25
        evidence_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="evidence|schema|stale",
    ):
        review.validate_pet_shot_review(plan, "shot_03")


@pytest.mark.parametrize("tamper", ("overall", "issue_code", "timestamp"))
def test_validate_single_shot_rejects_forged_manual_review(
    plan, monkeypatch, tamper
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    review.build_pet_shot_evidence(
        plan,
        "shot_03",
        command_runner=FakeMediaRunner(),
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
    )
    payload = _complete_shot_reviews(plan, failed_shot="shot_03")
    record = payload["shots"]["shot_03"]
    gate = record["gates"]["paws_and_feline_anatomy"]
    if tamper == "overall":
        record["passed"] = True
    elif tamper == "issue_code":
        gate["issue_codes"] = ["identity"]
    else:
        gate["timestamps_seconds"] = [7.1]
    plan.shot_review_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="contradicts|issue code|timestamp",
    ):
        review.validate_pet_shot_review(plan, "shot_03")


def test_build_single_shot_evidence_rejects_missing_predecessor(
    plan, monkeypatch
):
    import factory.pet_sitcom_review as review

    _write_partial_task3_chain(plan, 3, monkeypatch)
    selection = json.loads(plan.selection_path.read_text())
    selection["shots"].pop("shot_02")
    plan.selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(
        review.PetSitcomReviewError,
        match="shot_02|predecessor|chain",
    ):
        review.build_pet_shot_evidence(
            plan,
            "shot_03",
            command_runner=FakeMediaRunner(),
            ffmpeg_bin="ffmpeg",
            ffprobe_bin="ffprobe",
        )


@pytest.mark.parametrize(
    "tamper",
    (
        "dependency",
        "reference_audio",
        "generation_duration",
        "source_tts",
        "continuity_edit_endpoint",
        "selection_extra",
    ),
)
def test_incremental_review_rejects_forged_task5_provenance(
    plan, monkeypatch, tamper
):
    import factory.pet_sitcom_generation as generation
    import factory.pet_sitcom_review as review

    selections = _write_partial_task3_chain(plan, 3, monkeypatch)
    shot = plan.shots[2]
    candidate = generation._pet_candidate_path(shot, 1)
    state_path = generation._pet_candidate_state_path(candidate)
    report_path = generation._pet_gateway_report_path(candidate)
    state = json.loads(state_path.read_text())
    entry = selections["shot_03"]

    if tamper == "dependency":
        forged = {"shot_02": "0" * 64}
        state["dependency_video_sha256"] = forged
        entry["dependency_video_sha256"] = forged
    elif tamper == "reference_audio":
        state["reference_audio_sha256"] = "0" * 64
        entry["reference_audio_sha256"] = "0" * 64
    elif tamper == "generation_duration":
        state["generation_duration_seconds"] = 6
    elif tamper == "source_tts":
        state["source_tts_sha256"] = "0" * 64
        entry["source_tts_sha256"] = "0" * 64
    elif tamper == "continuity_edit_endpoint":
        frame = generation._pet_continuity_frame_path(plan, "shot_03")
        sidecar_path = generation._pet_continuity_state_path(frame)
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["edit_duration_seconds"] = 6.0
        sidecar["timestamp_seconds"] = 5.92
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        entry["continuity_timestamp_seconds"] = 5.92
    else:
        entry["forged"] = True

    if tamper not in {"continuity_edit_endpoint", "selection_extra"}:
        state_path.write_text(json.dumps(state), encoding="utf-8")
        report = json.loads(report_path.read_text())
        report["pet_sitcom_provenance"] = state
        report_path.write_text(json.dumps(report), encoding="utf-8")
    selection = json.loads(plan.selection_path.read_text())
    selection["shots"] = selections
    plan.selection_path.write_text(json.dumps(selection), encoding="utf-8")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="Task 5|provenance|continuity",
    ):
        review.build_pet_shot_evidence(
            plan,
            "shot_03",
            command_runner=FakeMediaRunner(),
            ffmpeg_bin="ffmpeg",
            ffprobe_bin="ffprobe",
        )


@pytest.mark.parametrize(
    "tamper",
    (
        "label",
        "previous_timestamp",
        "current_timestamp",
        "frame_hash",
        "previous_video_hash",
        "current_video_hash",
    ),
)
def test_source_evidence_rejects_forged_six_frame_continuity(
    plan, wired_sources, monkeypatch, tamper
):
    import factory.pet_sitcom_review as review

    _build_source(plan, FakeMediaRunner(), monkeypatch)
    manifest_path = plan.output_dir / "evidence" / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item = manifest["continuity_comparisons"][0]
    if tamper == "label":
        item["label"] = "forged"
    elif tamper == "previous_timestamp":
        item["previous_timestamps_seconds"][0] += 0.01
    elif tamper == "current_timestamp":
        item["current_timestamps_seconds"][0] += 0.01
    elif tamper == "frame_hash":
        item["frame_sha256"][0] = "0" * 64
    elif tamper == "previous_video_hash":
        item["previous_selected_mp4_sha256"] = "0" * 64
    else:
        item["current_selected_mp4_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError, match="stale"):
        review.validate_source_evidence(plan)


def test_continuity_sampling_rejects_unsafe_edit_endpoint_without_clamping(
    plan, sources
):
    import factory.pet_sitcom_review as review

    with pytest.raises(
        review.PetSitcomReviewError,
        match="edit endpoint safely",
    ):
        review._continuity_evidence(
            plan,
            "shot_06",
            "shot_07",
            sources["shot_06"],
            sources["shot_07"],
            6.0,
            5.0,
            FakeMediaRunner(),
            "ffmpeg",
            "2026-07-24T00:00:00+00:00",
            previous_video_duration=6.0,
            current_video_duration=5.0,
        )


def test_review_markdown_rejects_pending_manual_evidence(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _write_finals(plan)
    _build_final(plan, runner, monkeypatch)
    with pytest.raises(review.PetSitcomReviewError, match="human"):
        review.write_pet_sitcom_review_markdown(plan)
    assert not plan.review_markdown_path.exists()


def test_review_markdown_requires_task4_composition_preflight(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _complete_shot_reviews(plan)
    _complete_owner_review(plan)
    _write_task2_reviews(plan, monkeypatch)
    _write_selection_details(plan, wired_sources)
    _write_finals(plan)
    _build_final(plan, runner, monkeypatch)

    with pytest.raises(
        review.PetSitcomReviewError,
        match="composition preflight",
    ):
        review.write_pet_sitcom_review_markdown(plan)
    assert not plan.review_markdown_path.exists()


def test_real_task2_loader_preserves_shot06_j_cut(
    plan,
    monkeypatch,
):
    import factory.pet_sitcom_compose as compose

    assets = _write_current_task2_manifest(
        plan,
        monkeypatch,
        mock_probe=False,
    )
    selected = {
        shot.shot_id: {"sha256": "0" * 64}
        for shot in plan.shots
    }
    timings = compose._verified_pet_timings(plan, selected)

    shot06_asset = next(
        asset for asset in assets if asset.shot_id == "shot_06"
    )
    shot06 = next(
        timing for timing in timings if timing.shot_id == "shot_06"
    )
    assert shot06.start_seconds == pytest.approx(-0.20)
    assert shot06.absolute_start_seconds == pytest.approx(26.30)
    assert shot06.absolute_end_seconds == pytest.approx(
        shot06_asset.absolute_end_seconds
    )


def test_review_markdown_uses_validated_real_records(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _complete_shot_reviews(plan, failed_shot="shot_10")
    _rewrite_shot_reviews_as_legacy_v2(plan)
    candidate_two = (
        plan.output_dir / "candidates" / "shot_10" / "candidate_002.mp4"
    )
    candidate_two.parent.mkdir(parents=True, exist_ok=True)
    candidate_two.write_bytes(b"shot-10-candidate-two")
    wired_sources["shot_10"] = {
        **wired_sources["shot_10"],
        "path": candidate_two,
        "sha256": _sha256(candidate_two),
        "candidate_number": 2,
    }
    _build_source(plan, runner, monkeypatch)
    archived = next(
        (
            plan.output_dir / "evidence" / "review_history" / "shot"
        ).rglob("*.json")
    )
    archived_review = json.loads(archived.read_text())["review_record"]
    assert archived_review["gates"]["paws_and_feline_anatomy"][
        "issue_codes"
    ] == ["paw_anatomy"]
    _complete_shot_reviews(plan)
    _complete_owner_review(plan)
    _write_task2_reviews(plan, monkeypatch)
    _write_current_task2_manifest(
        plan,
        monkeypatch,
        mock_probe=False,
    )
    _write_selection_details(
        plan,
        wired_sources,
        candidate_two_retry_reason="paw_anatomy",
    )
    _write_finals(plan)
    _build_final(plan, runner, monkeypatch)
    path = review.write_pet_sitcom_review_markdown(plan)
    text = path.read_text(encoding="utf-8")
    assert all(f"## {index}." in text for index in range(1, 10))
    assert "shot_10: candidate 2" in text
    assert "issue_code=paw_anatomy" in text
    assert "timestamps=[2.2]" in text
    assert "left paw has a visible fused toe" in text
    assert "retry_reason=paw_anatomy" in text
    assert (
        "prompt_change=Keep all visible paws anatomically feline"
        in text
    )
    assert "retest_result=selected_after_human_review" in text
    assert "true_peak=-1.60 dBTP" in text
    assert "shot_06: speaker=owner, start=-0.200s, end=0.800s" in text
    assert (
        "Naitang fixed voice: `saturn_zh_female_tiaopigongzhu_tob`" in text
    )
    assert "Doubao fixed voice: `saturn_zh_female_keainvsheng_tob`" in text
    assert "逐帧人工复核" in text
    assert "未进行音素级认证" in text


def test_final_evidence_validator_rejects_replaced_release(
    plan, wired_sources, monkeypatch
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _write_finals(plan)
    _build_final(plan, runner, monkeypatch)
    plan.release_output.write_bytes(b"replaced release")
    with pytest.raises(review.PetSitcomReviewError, match="stale"):
        review.validate_final_evidence(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("integrated_lufs", True),
        ("integrated_lufs", float("nan")),
        ("integrated_lufs", float("inf")),
        ("true_peak_dbtp", False),
        ("true_peak_dbtp", float("nan")),
        ("true_peak_dbtp", float("-inf")),
    ],
)
def test_final_evidence_rejects_nonfinite_or_boolean_persisted_loudness(
    plan,
    wired_sources,
    monkeypatch,
    field,
    value,
):
    import factory.pet_sitcom_review as review

    runner = FakeMediaRunner()
    _build_source(plan, runner, monkeypatch)
    _write_finals(plan)
    _build_final(plan, runner, monkeypatch)
    qc_path = plan.output_dir / "evidence" / "final_technical_qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    release = next(
        record for record in qc["records"] if record["name"] == "release"
    )
    release["loudness"][field] = value
    qc_path.write_text(
        json.dumps(qc, allow_nan=True),
        encoding="utf-8",
    )
    manifest_path = review._final_manifest_path(plan)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["final_technical_qc_sha256"] = _sha256(qc_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(review.PetSitcomReviewError):
        review.validate_final_evidence(plan)


def test_review_json_writer_rejects_nonfinite_values(plan):
    import factory.pet_sitcom_review as review

    output = plan.output_dir / "evidence" / "nonfinite.json"
    with pytest.raises(review.PetSitcomReviewError, match="finite"):
        review._write_json(plan, output, {"value": float("nan")})
    assert not output.exists()


def test_atomic_image_failure_preserves_previous_evidence(plan):
    import factory.pet_sitcom_review as review

    source = plan.output_dir / "source.mp4"
    output = plan.output_dir / "evidence" / "atomic.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"previous")

    def failing_runner(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="secret=do-not-leak")

    with pytest.raises(
        review.PetSitcomReviewError,
        match="Local FFmpeg/FFprobe evidence command failed",
    ):
        review._extract_at_times(
            plan,
            source,
            output,
            [0.0, 1.0],
            "2x1",
            failing_runner,
            "ffmpeg",
        )
    assert output.read_bytes() == b"previous"
    assert not list(output.parent.glob(".atomic.*.png"))


def test_evidence_layout_allows_unused_cells_for_thirteen_mouth_frames(plan):
    import factory.pet_sitcom_review as review

    source = plan.output_dir / "source.mp4"
    output = plan.output_dir / "evidence" / "mouth.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")

    review._extract_at_times(
        plan,
        source,
        output,
        [float(index) for index in range(13)],
        "4x4",
        FakeMediaRunner(),
        "ffmpeg",
    )

    assert output.is_file()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are unavailable",
)
def test_real_ffmpeg_two_second_static_video_fails_freeze_qc(plan):
    import factory.pet_sitcom_review as review

    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    source = plan.output_dir / "static-two-seconds.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:size=36x64:rate=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )

    record = review._technical_record(
        plan,
        "shot_03",
        {"path": source, "sha256": _sha256(source)},
        final=False,
        command_runner=subprocess.run,
        ffmpeg_bin=ffmpeg,
        ffprobe_bin=ffprobe,
    )
    assert record["freezedetect"]["starts_seconds"] == [0.0]
    assert record["freezedetect"]["max_duration_seconds"] >= 1.9
    assert "freeze exceeds 0.35 seconds" in record["errors"]


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are unavailable",
)
def test_real_ffmpeg_evidence_samples_video_stream_when_audio_is_longer(plan):
    import factory.pet_sitcom_review as review

    ffmpeg = str(shutil.which("ffmpeg"))
    ffprobe = str(shutil.which("ffprobe"))
    source = plan.output_dir / "audio-longer-than-video.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-t",
            "5.0",
            "-i",
            "testsrc2=size=36x64:rate=24",
            "-f",
            "lavfi",
            "-t",
            "5.085",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    probe = review._ffprobe(subprocess.run, ffprobe, source)
    media_duration = review._duration(probe)
    video_duration = review._video_duration(probe)
    output = plan.output_dir / "evidence" / "audio-longer.png"

    review._extract_at_times(
        plan,
        source,
        output,
        review._sample_timestamps(9, video_duration),
        "3x3",
        subprocess.run,
        ffmpeg,
    )

    assert media_duration > video_duration
    with Image.open(output) as image:
        assert image.size == (1080, 1920)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are unavailable",
)
def test_real_ffmpeg_smoke_covers_seventy_seconds_and_continuity_boundary(plan):
    import factory.pet_sitcom_review as review

    ffmpeg = str(shutil.which("ffmpeg"))
    long_source = plan.output_dir / "long.mp4"
    short_source = plan.output_dir / "short.mp4"
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    for path, duration in ((long_source, 70.0), (short_source, 4.70)):
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=36x64:rate=30",
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
    whole = plan.output_dir / "evidence" / "whole.png"
    times = review._sample_timestamps(16, 70.0)
    review._extract_at_times(
        plan,
        long_source,
        whole,
        times,
        "4x4",
        subprocess.run,
        ffmpeg,
    )
    continuity = plan.output_dir / "evidence" / "continuity.png"
    review._extract_pair(
        plan,
        short_source,
        4.65,
        short_source,
        0.02,
        continuity,
        subprocess.run,
        ffmpeg,
    )

    assert times[0] == 0.0 and times[-1] == pytest.approx(69.95)
    with Image.open(whole) as image:
        assert image.width > 0 and image.height > 0
    with Image.open(continuity) as image:
        assert image.width > 0 and image.height > 0
