from __future__ import annotations

import fcntl
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from . import pet_sitcom_sound as _sound
from .pet_sitcom import (
    FINAL_DURATION_SECONDS,
    PetSitcomError,
    PetSitcomPlan,
    _validate_plan_contract,
)
from .pet_sitcom_audio_first import (
    PET_VOICES,
    PetSitcomAudioFirstError,
    generate_pet_speech_assets,
    load_pet_speech_assets,
)


OWNER_TTS_STATE_SCHEMA = "motion-comic-factory.pet-sitcom-owner-tts.v1"
CAT_TTS_STATE_SCHEMA = "motion-comic-factory.pet-sitcom-cat-tts.v1"
DIALOGUE_TIMING_SCHEMA = "motion-comic-factory.pet-sitcom-dialogue-timings.v1"
OWNER_NATIVE_AUDIO_REVIEW_SCHEMA = (
    "motion-comic-factory.pet-sitcom-owner-native-audio-review.v1"
)
VIDEO_WIDTH, VIDEO_HEIGHT, OUTPUT_FPS = 1080, 1920, 30
MIN_AAC_BIT_RATE = 160_000
_CFR_PTS_TOLERANCE_SECONDS = 0.0005
_CFR_PHASE_TOLERANCE_SECONDS = 0.001
_FRAME_DURATION_SECONDS = 1.0 / OUTPUT_FPS
_TIMELINE_START_TOLERANCE_SECONDS = _FRAME_DURATION_SECONDS / 2.0
_TIMELINE_SYNC_TOLERANCE_SECONDS = _FRAME_DURATION_SECONDS
_TIMELINE_COMPARISON_EPSILON_SECONDS = 1e-6
PUBLISH_JOURNAL_SCHEMA = (
    "motion-comic-factory.pet-sitcom-compose-publish.v2"
)
_PUBLISH_ROLES = ("clean", "release")
_PUBLISH_TRANSACTION_ROOT = ".pet-sitcom-compose-transactions"
_PUBLISH_LOCK_NAME = ".pet-sitcom-compose-publish.lock"
_PUBLISH_LOCK_GUARD = threading.Lock()
_PUBLISH_LOCKED_ROOTS: dict[Path, object] = {}
_PAIR_RECOVERY_ATTEMPTS = 16
_DUCK_GAIN = 0.398107
_LOUDNORM_FILTER_TARGET = -16.0
_OWNER_VOICE_ID = "zh_female_vv_uranus_bigtts"
_CAT_VOICE_IDS = {
    speaker: PET_VOICES[speaker].voice_id
    for speaker in ("naitang", "doubao")
}
_OWNER_REVIEW_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "reviewed",
        "verified",
        "shots",
        "generated_at",
        "reviewer_method",
    }
)
_OWNER_REVIEW_RECORD_FIELDS = frozenset(
    {
        "selected_mp4_path",
        "selected_mp4_sha256",
        "no_native_voice",
        "room_tone_allowed",
        "reviewer_method",
        "reviewed_at",
        "notes",
    }
)
_EVIDENCE_WORDS = ("尾巴", "鸡肉味", "碎屑", "证据")
_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


class PetSitcomComposeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PetDialogueTiming:
    shot_id: str
    speaker: str
    text: str
    start_seconds: float
    end_seconds: float
    selected_mp4_sha256: str
    owner_audio_sha256: str = ""
    cat_audio_sha256: str = ""
    absolute_start_seconds: float = 0.0
    absolute_end_seconds: float = 0.0


@dataclass(frozen=True)
class _PublishLock:
    root: Path
    path: Path
    descriptor: int


def generate_owner_voice_lines(
    plan: PetSitcomPlan,
    *,
    tts_client: Any,
    allow_network: bool = False,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
    voice_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for owner records in the shared audio manifest."""
    _validate_plan(plan)
    configured_voice = str(
        getattr(getattr(tts_client, "config", None), "voice_type", "")
    ).strip()
    if voice_id not in {None, _OWNER_VOICE_ID} or configured_voice != _OWNER_VOICE_ID:
        raise PetSitcomComposeError(
            "Owner TTS must use the fixed owner voice configured for this project."
        )
    try:
        shared = generate_pet_speech_assets(
            plan,
            tts_client=tts_client,
            allow_network=allow_network,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
        )
    except PetSitcomAudioFirstError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    return _filtered_speech_report(
        shared,
        speakers={"owner"},
        planned_count=sum(shot.speaker == "owner" for shot in plan.shots),
        schema_version=OWNER_TTS_STATE_SCHEMA,
        records_key="owner_audio",
    )


def generate_cat_voice_lines(
    plan: PetSitcomPlan,
    *,
    tts_client: Any,
    allow_network: bool = False,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    """Compatibility wrapper for cat records in the shared audio manifest."""
    _validate_plan(plan)
    try:
        shared = generate_pet_speech_assets(
            plan,
            tts_client=tts_client,
            allow_network=allow_network,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
        )
    except PetSitcomAudioFirstError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    return _filtered_speech_report(
        shared,
        speakers=set(_CAT_VOICE_IDS),
        planned_count=sum(
            shot.speaker in _CAT_VOICE_IDS for shot in plan.shots
        ),
        schema_version=CAT_TTS_STATE_SCHEMA,
        records_key="cat_audio",
    )


def _filtered_speech_report(
    shared: Mapping[str, Any],
    *,
    speakers: set[str],
    planned_count: int,
    schema_version: str,
    records_key: str,
) -> dict[str, Any]:
    records = [
        record
        for record in shared.get("assets", [])
        if isinstance(record, Mapping) and record.get("speaker") in speakers
    ]
    result: dict[str, Any] = {
        "schema_version": schema_version,
        "success": bool(shared.get("success")),
        "executed": bool(
            shared.get("executed")
            and any(record.get("status") == "generated" for record in records)
        ),
        "planned_count": planned_count,
        "completed_count": len(records),
        "reused_count": sum(
            record.get("status") == "reused" for record in records
        ),
        records_key: [
            {
                "shot_id": record["shot_id"],
                "status": record["status"],
                "path": record["output_path"],
                "sha256": record["output_sha256"],
            }
            for record in records
        ],
        "errors": list(shared.get("errors", [])),
    }
    if "blocked_reasons" in shared:
        result["blocked_reasons"] = list(shared["blocked_reasons"])
    return result


def bind_cat_voice_lines_to_verified_timings(plan: PetSitcomPlan) -> Path:
    """Publish a compatibility timing view of the immutable audio manifest."""
    _validate_plan(plan)
    timings = load_verified_pet_timings(plan)
    _write_json(
        plan.dialogue_timing_path,
        {
            "schema_version": DIALOGUE_TIMING_SCHEMA,
            "verified": True,
            "source_manifest_path": str(plan.audio_manifest_path),
            "timings": [
                {
                    "shot_id": timing.shot_id,
                    "selected_mp4_sha256": timing.selected_mp4_sha256,
                    "speaker": timing.speaker,
                    "text": timing.text,
                    "start_seconds": timing.start_seconds,
                    "end_seconds": timing.end_seconds,
                    "absolute_start_seconds": timing.absolute_start_seconds,
                    "absolute_end_seconds": timing.absolute_end_seconds,
                    "owner_audio_sha256": timing.owner_audio_sha256,
                    "cat_audio_sha256": timing.cat_audio_sha256,
                }
                for timing in timings
            ],
        },
        plan.output_dir,
    )
    return plan.dialogue_timing_path


def _ensure_release_text_assets(
    plan: PetSitcomPlan, timings: Sequence[PetDialogueTiming]
) -> dict[str, Path]:
    _validate_plan(plan)
    paths = _release_text_asset_paths(plan)
    payloads = {
        "opening_title": _render_text_asset("冻干失窃案", "title"),
        "ending_card": _render_text_asset("本案嫌疑猫拒绝认罪", "ending"),
    }
    for timing in timings:
        payloads[_dialogue_asset_key(timing.shot_id)] = _render_text_asset(
            timing.text, "dialogue"
        )
        for index, keyword in enumerate(
            _evidence_keywords(timing.text), start=1
        ):
            payloads[_evidence_asset_key(timing.shot_id, index)] = (
                _render_text_asset(keyword, "evidence")
            )
    if set(payloads) != set(paths):
        raise PetSitcomComposeError(
            "Release text assets must cover title, ending, and every dialogue."
        )
    for key, path in paths.items():
        _write_atomic_asset(plan, path, payloads[key])
    return paths


def _render_text_asset(text: str, style: str) -> bytes:
    if style == "title":
        width, height, font_size = 1000, 140, 64
        fill, stroke_fill, stroke_width = "white", "black", 4
        background = None
    elif style == "ending":
        width, height, font_size = 1000, 160, 54
        fill, stroke_fill, stroke_width = "white", "black", 4
        background = None
    elif style == "evidence":
        width, height, font_size = 960, 128, 48
        fill, stroke_fill, stroke_width = "white", (45, 39, 27, 255), 4
        background = None
    elif style == "dialogue":
        width, height, font_size = 960, 128, 46
        fill, stroke_fill, stroke_width = (45, 39, 27, 255), None, 0
        background = (255, 230, 170, 220)
    else:
        raise PetSitcomComposeError(f"Unknown release text style: {style}")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if background is not None:
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=32,
            fill=background,
        )
    font = _fitting_font(draw, text, font_size, width, height, stroke_width)
    left, top, right, bottom = draw.textbbox(
        (0, 0), text, font=font, stroke_width=stroke_width
    )
    x = (width - (right - left)) / 2 - left
    y = (height - (bottom - top)) / 2 - top
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _fitting_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    initial_size: int,
    width: int,
    height: int,
    stroke_width: int,
) -> ImageFont.FreeTypeFont:
    for size in range(initial_size, 27, -2):
        font = ImageFont.truetype(str(_font()), size=size)
        left, top, right, bottom = draw.textbbox(
            (0, 0), text, font=font, stroke_width=stroke_width
        )
        if right - left <= width - 64 and bottom - top <= height - 24:
            return font
    raise PetSitcomComposeError(f"Release text does not fit its asset: {text}")


def _write_atomic_asset(plan: PetSitcomPlan, path: Path, payload: bytes) -> None:
    _reject_symlinks(path, "Release text asset")
    _within(path, plan.output_dir, "Release text asset")
    digest = hashlib.sha256(payload).hexdigest()
    if path.is_file() and _sha(path) == digest:
        return
    temporary = _temporary(path, ".png")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _release_text_asset_paths(plan: PetSitcomPlan) -> dict[str, Path]:
    root = plan.output_dir / "assets" / "release_text"
    paths = {
        "opening_title": root / "opening_title.png",
        "ending_card": root / "ending_card.png",
    }
    for shot in plan.shots:
        if not shot.dialogue:
            continue
        paths[_dialogue_asset_key(shot.shot_id)] = (
            root / f"{shot.shot_id}_dialogue.png"
        )
        for index, _keyword in enumerate(
            _evidence_keywords(shot.dialogue), start=1
        ):
            suffix = "" if index == 1 else f"_{index:02d}"
            paths[_evidence_asset_key(shot.shot_id, index)] = (
                root / f"{shot.shot_id}_evidence{suffix}.png"
            )
    return paths


def _dialogue_asset_key(shot_id: str) -> str:
    return f"{shot_id}:dialogue"


def _evidence_asset_key(shot_id: str, index: int) -> str:
    return f"{shot_id}:evidence:{index}"


def _evidence_keywords(text: str) -> tuple[str, ...]:
    return tuple(word for word in _EVIDENCE_WORDS if word in text)


def _validate_render_geometry(width: int, height: int, fps: int) -> None:
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (width, height, fps)
        )
        or width <= 0
        or height <= 0
        or width % 2
        or height % 2
        or fps <= 0
    ):
        raise PetSitcomComposeError(
            "Render width and height must be positive even integers, "
            "and FPS must be a positive integer."
        )


def load_verified_pet_timings(plan: PetSitcomPlan) -> tuple[PetDialogueTiming, ...]:
    _validate_plan(plan)
    return _verified_pet_timings(plan, _selected_sources(plan))


def _verified_pet_timings(
    plan: PetSitcomPlan,
    selected: Mapping[str, Mapping[str, Any]],
) -> tuple[PetDialogueTiming, ...]:
    try:
        assets = load_pet_speech_assets(plan)
    except PetSitcomAudioFirstError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    shot_starts: dict[str, float] = {}
    current = 0.0
    for shot in plan.shots:
        shot_starts[shot.shot_id] = current
        current += shot.duration_seconds
    result: list[PetDialogueTiming] = []
    for asset in assets:
        start = asset.absolute_start_seconds - shot_starts[asset.shot_id]
        end = asset.absolute_end_seconds - shot_starts[asset.shot_id]
        result.append(
            PetDialogueTiming(
                shot_id=asset.shot_id,
                speaker=asset.speaker,
                text=asset.text,
                start_seconds=start,
                end_seconds=end,
                selected_mp4_sha256=selected[asset.shot_id]["sha256"],
                owner_audio_sha256=(
                    asset.output_sha256 if asset.speaker == "owner" else ""
                ),
                cat_audio_sha256=(
                    asset.output_sha256
                    if asset.speaker in _CAT_VOICE_IDS
                    else ""
                ),
                absolute_start_seconds=asset.absolute_start_seconds,
                absolute_end_seconds=asset.absolute_end_seconds,
            )
        )
    timings = tuple(result)
    _validate_dialogue_timeline(timings, plan.duration_seconds)
    return timings


def _validate_dialogue_timeline(
    timings: Sequence[PetDialogueTiming],
    duration_seconds: float,
) -> None:
    ordered = sorted(
        timings,
        key=lambda item: (item.absolute_start_seconds, item.shot_id),
    )
    for timing in ordered:
        start, end = _absolute_timing(timing)
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0.0
            or end <= start
            or end > duration_seconds + 1e-9
        ):
            raise PetSitcomComposeError(
                f"{timing.shot_id} has invalid absolute dialogue timing."
            )
    for previous, current in zip(ordered, ordered[1:]):
        if current.absolute_start_seconds < previous.absolute_end_seconds - 1e-9:
            raise PetSitcomComposeError(
                "Task 2 dialogue overlap is not allowed: "
                f"{previous.shot_id} overlaps {current.shot_id}."
            )


def build_pet_sitcom_ffmpeg_commands(
    plan: PetSitcomPlan,
    *,
    ffmpeg_bin: str = "ffmpeg",
    output_paths: tuple[Path, Path] | None = None,
    video_width: int = VIDEO_WIDTH,
    video_height: int = VIDEO_HEIGHT,
    output_fps: int = OUTPUT_FPS,
) -> list[list[str]]:
    _validate_plan(plan)
    _validate_render_geometry(video_width, video_height, output_fps)
    sources = _reviewed_sources(plan)
    timings = _verified_pet_timings(plan, sources)
    try:
        sound_design = _sound.load_pet_sound_design(plan)
    except _sound.PetSoundError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    assets = _ensure_release_text_assets(plan, timings)
    return _build_verified_commands(
        plan,
        sources=sources,
        timings=timings,
        sound_design=sound_design,
        assets=assets,
        ffmpeg_bin=ffmpeg_bin,
        output_paths=output_paths,
        video_width=video_width,
        video_height=video_height,
        output_fps=output_fps,
    )


def _build_verified_commands(
    plan: PetSitcomPlan,
    *,
    sources: Mapping[str, Mapping[str, Any]],
    timings: Sequence[PetDialogueTiming],
    sound_design: Mapping[str, Any],
    assets: Mapping[str, Path],
    ffmpeg_bin: str,
    output_paths: tuple[Path, Path] | None,
    video_width: int,
    video_height: int,
    output_fps: int,
    speech_paths: Mapping[str, Path] | None = None,
) -> list[list[str]]:
    destinations = output_paths or (plan.clean_output, plan.release_output)
    if len(destinations) != 2:
        raise PetSitcomComposeError(
            "Pet sitcom composition requires clean and release outputs."
        )
    stems = sound_design["stems"]
    foley = sound_design["foley"]
    base_inputs = [
        *(sources[shot.shot_id]["path"] for shot in plan.shots),
        Path(stems["music"]["path"]),
        Path(stems["room_tone"]["path"]),
        *(Path(stems[event["stem"]]["path"]) for event in foley),
        Path(stems[sound_design["ending_button"]["stem"]]["path"]),
        *(
            (
                speech_paths[shot.shot_id]
                if speech_paths is not None
                else _speech_audio(plan, shot.shot_id, str(shot.speaker))
            )
            for shot in plan.shots
            if shot.dialogue
        ),
    ]
    asset_layers: list[tuple[Path, float, float, str, str]] = [
        (assets["opening_title"], 0.0, 3.0, "(W-w)/2", "110")
    ]
    for timing in timings:
        start, end = _absolute_timing(timing)
        asset_layers.append(
            (
                assets[_dialogue_asset_key(timing.shot_id)],
                start,
                end,
                "(W-w)/2",
                "H-410",
            )
        )
        for index, _keyword in enumerate(
            _evidence_keywords(timing.text), start=1
        ):
            asset_layers.append(
                (
                    assets[_evidence_asset_key(timing.shot_id, index)],
                    start,
                    end,
                    "(W-w)/2",
                    f"H-{560 + (index - 1) * 140}",
                )
            )
    asset_layers.append(
        (
            assets["ending_card"],
            _end_card_start(timings),
            plan.duration_seconds,
            "(W-w)/2",
            "(H-h)/2",
        )
    )
    first_asset_input = len(base_inputs)
    release_inputs = [
        *base_inputs,
        *(path for path, _start, _end, _x, _y in asset_layers),
    ]
    release_overlays = [
        (first_asset_input + index, start, end, x, y)
        for index, (_path, start, end, x, y) in enumerate(asset_layers)
    ]
    return [
        _command(
            ffmpeg_bin,
            base_inputs,
            _filters(
                plan,
                timings,
                sound_design,
                sources=sources,
                release=False,
                video_width=video_width,
                video_height=video_height,
                output_fps=output_fps,
            ),
            destinations[0],
            output_fps=output_fps,
            duration_seconds=plan.duration_seconds,
        ),
        _command(
            ffmpeg_bin,
            release_inputs,
            _filters(
                plan,
                timings,
                sound_design,
                sources=sources,
                release=True,
                release_overlays=release_overlays,
                video_width=video_width,
                video_height=video_height,
                output_fps=output_fps,
            ),
            destinations[1],
            output_fps=output_fps,
            duration_seconds=plan.duration_seconds,
        ),
    ]


def _reviewed_sources(
    plan: PetSitcomPlan,
) -> dict[str, dict[str, Any]]:
    from . import pet_sitcom_review as review

    try:
        evidence = review.validate_source_evidence(plan)
        reviews = review.validate_pet_shot_reviews(plan)
        if reviews.get("passed") is not True:
            failed = reviews.get("failed_shots")
            detail = ", ".join(failed) if isinstance(failed, list) else "unknown"
            raise PetSitcomComposeError(
                "Task 6 shot review v4 must pass every shot before "
                f"composition; failed: {detail}."
            )
        review.validate_owner_native_audio_review(plan)
    except PetSitcomComposeError:
        raise
    except review.PetSitcomReviewError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    sources = evidence.get("sources")
    qc = evidence.get("qc")
    records = qc.get("records") if isinstance(qc, Mapping) else None
    expected_ids = {shot.shot_id for shot in plan.shots}
    if (
        not isinstance(sources, Mapping)
        or set(sources) != expected_ids
        or not isinstance(records, list)
    ):
        raise PetSitcomComposeError(
            "Task 6 source evidence is incomplete or stale."
        )
    by_name = {
        record.get("name"): record
        for record in records
        if isinstance(record, Mapping)
    }
    if set(by_name) != expected_ids:
        raise PetSitcomComposeError(
            "Task 6 source QC must cover exactly ten selected shots."
        )
    result: dict[str, dict[str, Any]] = {}
    for shot in plan.shots:
        source = sources[shot.shot_id]
        record = by_name[shot.shot_id]
        if not isinstance(source, Mapping):
            raise PetSitcomComposeError(
                f"Task 6 source for {shot.shot_id} is invalid."
            )
        available = _number(record.get("video_duration_seconds"))
        if available < shot.duration_seconds:
            if not review._local_recut_micro_retime_allowed(
                plan,
                shot.shot_id,
                source,
                available,
            ):
                raise PetSitcomComposeError(
                    f"{shot.shot_id} source video has only {available:.3f}s "
                    f"available for a {shot.duration_seconds:.3f}s edit and must "
                    "be regenerated; short sources are never padded."
                )
        result[shot.shot_id] = dict(source)
        result[shot.shot_id]["path"] = Path(str(source["path"]))
        result[shot.shot_id]["video_duration_seconds"] = available
    return result


def compose_pet_sitcom(
    plan: PetSitcomPlan,
    *,
    command_runner: Callable[..., Any] = subprocess.run,
    ffmpeg_bin: str = "ffmpeg",
) -> dict[str, Any]:
    _validate_plan(plan)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    with _compose_publish_lock(plan.output_dir) as publish_lock:
        return _compose_pet_sitcom_locked(
            plan,
            command_runner=command_runner,
            ffmpeg_bin=ffmpeg_bin,
            publish_lock=publish_lock,
        )


def _compose_pet_sitcom_locked(
    plan: PetSitcomPlan,
    *,
    command_runner: Callable[..., Any],
    ffmpeg_bin: str,
    publish_lock: _PublishLock,
) -> dict[str, Any]:
    _recover_publish_transaction(
        plan.output_dir,
        (plan.clean_output, plan.release_output),
        _lock=publish_lock,
    )
    sources = _reviewed_sources(plan)
    timings = _verified_pet_timings(plan, sources)
    try:
        sound_design = _sound.load_pet_sound_design(plan)
    except _sound.PetSoundError as exc:
        raise PetSitcomComposeError(str(exc)) from exc
    assets = _ensure_release_text_assets(plan, timings)
    report_commands = _build_verified_commands(
        plan,
        sources=sources,
        timings=timings,
        sound_design=sound_design,
        assets=assets,
        ffmpeg_bin=ffmpeg_bin,
        output_paths=None,
        video_width=VIDEO_WIDTH,
        video_height=VIDEO_HEIGHT,
        output_fps=OUTPUT_FPS,
    )
    with tempfile.TemporaryDirectory(
        prefix=".compose-run-",
        dir=plan.output_dir,
    ) as directory:
        snapshot_root = Path(directory)
        (
            snapshot_sources,
            snapshot_sound,
            snapshot_speech,
            snapshot_assets,
        ) = _snapshot_compose_inputs(
            plan,
            snapshot_root,
            sources=sources,
            timings=timings,
            sound_design=sound_design,
            assets=assets,
        )
        render_root = snapshot_root / "render"
        render_root.mkdir()
        private = (render_root / "clean.mp4", render_root / "release.mp4")
        commands = _build_verified_commands(
            plan,
            sources=snapshot_sources,
            timings=timings,
            sound_design=snapshot_sound,
            assets=snapshot_assets,
            speech_paths=snapshot_speech,
            ffmpeg_bin=ffmpeg_bin,
            output_paths=private,
            video_width=VIDEO_WIDTH,
            video_height=VIDEO_HEIGHT,
            output_fps=OUTPUT_FPS,
        )
        for command, output in zip(commands, private, strict=True):
            _run(command_runner, command)
            _validate_output(
                output,
                command_runner,
                _ffprobe_for(ffmpeg_bin),
                ffmpeg=ffmpeg_bin,
                timings=timings,
            )
        staged_list: list[Path] = []
        try:
            for source, destination in zip(
                private,
                (plan.clean_output, plan.release_output),
                strict=True,
            ):
                staged_list.append(
                    _stage(source, destination, plan.output_dir)
                )
        except BaseException:
            for path in staged_list:
                path.unlink(missing_ok=True)
            raise
        staged = tuple(staged_list)
        _publish_outputs_atomically(
            staged,
            (plan.clean_output, plan.release_output),
            plan.output_dir,
            _lock=publish_lock,
        )
    return {
        "success": True,
        "project_id": plan.project_id,
        "outputs": {
            "clean": str(plan.clean_output),
            "release": str(plan.release_output),
        },
        "duration_seconds": plan.duration_seconds,
        "timings": len(timings),
        "commands": report_commands,
    }


def _snapshot_compose_inputs(
    plan: PetSitcomPlan,
    snapshot_root: Path,
    *,
    sources: Mapping[str, Mapping[str, Any]],
    timings: Sequence[PetDialogueTiming],
    sound_design: Mapping[str, Any],
    assets: Mapping[str, Path],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Path],
    dict[str, Path],
]:
    _within(snapshot_root, plan.output_dir, "Compose snapshot")
    _reject_symlinks(snapshot_root, "Compose snapshot")
    snapshot_sources: dict[str, dict[str, Any]] = {}
    for shot in plan.shots:
        record = sources[shot.shot_id]
        source = Path(record["path"])
        _within(source, plan.output_dir, f"{shot.shot_id} selected source")
        destination = (
            snapshot_root / "video" / f"{shot.shot_id}.mp4"
        )
        copied = _copy_verified_input(
            source,
            destination,
            str(record["sha256"]),
            f"{shot.shot_id} selected source",
        )
        snapshot_sources[shot.shot_id] = {
            **dict(record),
            "path": copied,
        }

    stems = sound_design.get("stems")
    if not isinstance(stems, Mapping):
        raise PetSitcomComposeError(
            "Sound design stems are missing during snapshot."
        )
    snapshot_stems: dict[str, dict[str, Any]] = {}
    for name in sorted(stems):
        record = stems[name]
        if not isinstance(record, Mapping):
            raise PetSitcomComposeError(
                f"Sound stem {name} is invalid during snapshot."
            )
        source = Path(str(record.get("path") or ""))
        _within(source, plan.output_dir, f"Sound stem {name}")
        copied = _copy_verified_input(
            source,
            snapshot_root / "sound" / f"{name}.wav",
            str(record.get("sha256") or ""),
            f"Sound stem {name}",
        )
        snapshot_stems[name] = {
            **dict(record),
            "path": str(copied),
        }
    snapshot_sound = {
        **dict(sound_design),
        "stems": snapshot_stems,
    }

    snapshot_speech: dict[str, Path] = {}
    for timing in timings:
        source = _speech_audio(plan, timing.shot_id, timing.speaker)
        expected_sha256 = (
            timing.owner_audio_sha256
            if timing.speaker == "owner"
            else timing.cat_audio_sha256
        )
        _within(source, plan.output_dir, f"{timing.shot_id} Task 2 audio")
        snapshot_speech[timing.shot_id] = _copy_verified_input(
            source,
            snapshot_root / "dialogue" / f"{timing.shot_id}.wav",
            expected_sha256,
            f"{timing.shot_id} Task 2 audio",
        )

    snapshot_assets: dict[str, Path] = {}
    for index, (key, source) in enumerate(sorted(assets.items())):
        _within(source, plan.output_dir, f"Release overlay {key}")
        snapshot_assets[key] = _copy_verified_input(
            source,
            snapshot_root / "overlay" / f"{index:02d}.png",
            _sha(source),
            f"Release overlay {key}",
        )
    return (
        snapshot_sources,
        snapshot_sound,
        snapshot_speech,
        snapshot_assets,
    )


def _copy_verified_input(
    source: Path,
    destination: Path,
    expected_sha256: str,
    label: str,
) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise PetSitcomComposeError(
            f"{label} expected hash is invalid."
        )
    _reject_symlinks(source, label)
    try:
        canonical = source.resolve(strict=True)
        mode = canonical.stat().st_mode
    except OSError as exc:
        raise PetSitcomComposeError(f"{label} is missing.") from exc
    if source != canonical or not stat.S_ISREG(mode):
        raise PetSitcomComposeError(
            f"{label} must use a canonical regular file."
        )
    _reject_symlinks(destination, f"{label} snapshot")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlinks(destination.parent, f"{label} snapshot")
    digest = hashlib.sha256()
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        source_descriptor = os.open(source, source_flags)
        try:
            if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
                raise PetSitcomComposeError(
                    f"{label} changed during snapshot."
                )
            destination_descriptor = os.open(
                destination,
                destination_flags,
                0o600,
            )
            try:
                while True:
                    block = os.read(source_descriptor, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    view = memoryview(block)
                    while view:
                        written = os.write(destination_descriptor, view)
                        view = view[written:]
                os.fsync(destination_descriptor)
            finally:
                os.close(destination_descriptor)
        finally:
            os.close(source_descriptor)
    except PetSitcomComposeError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise PetSitcomComposeError(
            f"{label} changed during snapshot."
        ) from exc
    _reject_symlinks(source, label)
    if (
        digest.hexdigest() != expected_sha256
        or _sha(destination) != expected_sha256
        or _sha(source) != expected_sha256
    ):
        destination.unlink(missing_ok=True)
        raise PetSitcomComposeError(
            f"{label} changed during snapshot."
        )
    _fsync_dir(destination.parent)
    return destination


def _shot_video_filter(
    input_index: int,
    *,
    duration_seconds: float = 5.0,
    available_seconds: float | None = None,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
    fps: int = OUTPUT_FPS,
) -> str:
    duration = _filter_number(duration_seconds)
    available = (
        duration_seconds
        if available_seconds is None
        else _number(available_seconds)
    )
    if available < duration_seconds:
        source_duration = _filter_number(available)
        scale = _filter_number(duration_seconds / available)
        timing = (
            f"trim=duration={source_duration},"
            f"setpts={scale}*(PTS-STARTPTS)"
        )
    else:
        timing = f"trim=duration={duration},setpts=PTS-STARTPTS"
    return (
        f"[{input_index}:v]{timing},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},format=yuv420p"
    )


def _filter_number(value: float) -> str:
    number = _number(value)
    if number <= 0:
        raise PetSitcomComposeError("Filter duration must be positive.")
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _absolute_timing(timing: PetDialogueTiming) -> tuple[float, float]:
    return timing.absolute_start_seconds, timing.absolute_end_seconds


def _end_card_start(timings: Sequence[PetDialogueTiming]) -> float:
    if not timings:
        raise PetSitcomComposeError(
            "Verified dialogue timing is required for the end card."
        )
    return max(timing.absolute_end_seconds for timing in timings) + 0.10


def _filters(
    plan: PetSitcomPlan,
    timings: Sequence[PetDialogueTiming],
    sound_design: Mapping[str, Any],
    *,
    sources: Mapping[str, Mapping[str, Any]],
    release: bool,
    release_overlays: Sequence[
        tuple[int, float, float, str, str]
    ] | None = None,
    video_width: int = VIDEO_WIDTH,
    video_height: int = VIDEO_HEIGHT,
    output_fps: int = OUTPUT_FPS,
) -> list[str]:
    filters: list[str] = []
    video_parts: list[str] = []
    foley_events = sound_design["foley"]
    stems = sound_design["stems"]
    music_input = len(plan.shots)
    room_input = music_input + 1
    first_foley_input = room_input + 1
    button_input = first_foley_input + len(foley_events)
    first_tts_input = button_input + 1
    for index, shot in enumerate(plan.shots):
        video_filter = _shot_video_filter(
            index,
            duration_seconds=shot.duration_seconds,
            available_seconds=float(
                sources[shot.shot_id]["video_duration_seconds"]
            ),
            width=video_width,
            height=video_height,
            fps=output_fps,
        )
        filters.append(f"{video_filter}[v{index}]")
        video_parts.append(f"[v{index}]")
    filters.append(
        f"{''.join(video_parts)}concat=n={len(video_parts)}:v=1:a=0[vstory]"
    )
    dialogue_parts: list[str] = []
    for index, timing in enumerate(timings):
        delay = int(round(timing.absolute_start_seconds * 1000))
        duration = _filter_number(
            timing.absolute_end_seconds - timing.absolute_start_seconds
        )
        input_index = first_tts_input + index
        label = f"dialogue{index}"
        filters.append(
            f"[{input_index}:a]atrim=duration={duration},"
            "asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay},apad=pad_dur={plan.duration_seconds:g},"
            f"atrim=duration={plan.duration_seconds:g}[{label}]"
        )
        dialogue_parts.append(f"[{label}]")
    filters.append(
        f"anullsrc=r=48000:cl=stereo:d={plan.duration_seconds:g}[dialoguesilence]"
    )
    dialogue_parts.append("[dialoguesilence]")
    filters.append(
        f"{''.join(dialogue_parts)}amix=inputs={len(dialogue_parts)}:"
        "duration=longest:dropout_transition=0,"
        f"atrim=duration={plan.duration_seconds:g}[dialogue]"
    )

    foley_parts: list[str] = []
    for index, event in enumerate(foley_events):
        input_index = first_foley_input + index
        delay = int(round(float(event["start"]) * 1000))
        stem_duration = _filter_number(
            float(stems[event["stem"]]["duration_seconds"])
        )
        label = f"foley{index}"
        filters.append(
            f"[{input_index}:a]atrim=duration={stem_duration},"
            "asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay},apad=pad_dur={plan.duration_seconds:g},"
            f"atrim=duration={plan.duration_seconds:g}[{label}]"
        )
        foley_parts.append(f"[{label}]")
    filters.append(
        f"{''.join(foley_parts)}amix=inputs={len(foley_parts)}:"
        "duration=longest:dropout_transition=0,"
        f"atrim=duration={plan.duration_seconds:g}[foley]"
    )
    filters.append(
        f"[{room_input}:a]atrim=duration={plan.duration_seconds:g},"
        "asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=channel_layouts=stereo,"
        f"apad=pad_dur={plan.duration_seconds:g},"
        f"atrim=duration={plan.duration_seconds:g}[room]"
    )
    filters.append(
        f"[{music_input}:a]atrim=duration={plan.duration_seconds:g},"
        "asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=channel_layouts=stereo,"
        f"apad=pad_dur={plan.duration_seconds:g},"
        f"atrim=duration={plan.duration_seconds:g}[musicbase]"
    )
    button = sound_design["ending_button"]
    button_duration = _filter_number(
        float(button["end"]) - float(button["start"])
    )
    button_delay = int(round(float(button["start"]) * 1000))
    filters.append(
        f"[{button_input}:a]atrim=duration={button_duration},"
        "asetpts=PTS-STARTPTS,aresample=48000,"
        "aformat=channel_layouts=stereo,"
        f"adelay={button_delay}|{button_delay},"
        f"apad=pad_dur={plan.duration_seconds:g},"
        f"atrim=duration={plan.duration_seconds:g}[endingbutton]"
    )
    filters.append(
        "[musicbase][endingbutton]"
        "amix=inputs=2:duration=longest:dropout_transition=0,"
        f"atrim=duration={plan.duration_seconds:g}[musicwithbutton]"
    )
    music = "musicwithbutton"
    for number, timing in enumerate(timings, 1):
        start, end = _absolute_timing(timing)
        duck_start = max(0.0, start - 0.10)
        duck_end = min(plan.duration_seconds, end + 0.20)
        next_music = f"musicduck{number}"
        filters.append(
            f"[{music}]volume=volume={_DUCK_GAIN:.6f}:"
            f"enable='between(t,{duck_start:.3f},{duck_end:.3f})'"
            f"[{next_music}]"
        )
        music = next_music
    if release:
        expected_overlays = (
            len(timings)
            + sum(len(_evidence_keywords(timing.text)) for timing in timings)
            + 2
        )
        if release_overlays is None or len(release_overlays) != expected_overlays:
            raise PetSitcomComposeError(
                "Release composition requires title, dialogue, evidence, "
                "and ending overlays."
            )
        video = _append_release_overlay_filters(
            filters, "vstory", release_overlays
        )
    else:
        video = "vstory"
    filters += [
        f"[{video}]format=yuv420p[vout]",
        f"[dialogue][foley][room][{music}]"
        "amix=inputs=4:duration=longest:dropout_transition=0,"
        f"atrim=duration={plan.duration_seconds:g},"
        f"loudnorm=I={_LOUDNORM_FILTER_TARGET:g}:TP=-1.5:LRA=11,"
        "alimiter=limit=0.841395:level=false[aout]",
    ]
    return filters


def _append_release_overlay_filters(
    filters: list[str],
    current: str,
    overlays: Sequence[tuple[int, float, float, str, str]],
) -> str:
    for index, (input_index, start, end, x, y) in enumerate(overlays):
        if start < 0 or end <= start:
            raise PetSitcomComposeError("Release overlay timing is invalid.")
        next_label = f"releaseoverlay{index}"
        filters.append(
            f"[{current}][{input_index}:v]overlay=x={x}:y={y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{next_label}]"
        )
        current = next_label
    return current


def _command(
    binary: str,
    inputs: Sequence[Path],
    filters: Sequence[str],
    output: Path,
    *,
    output_fps: int = OUTPUT_FPS,
    duration_seconds: float = FINAL_DURATION_SECONDS,
) -> list[str]:
    command = [binary, "-y", "-hide_banner", "-loglevel", "error"]
    for item in inputs:
        if item.suffix.lower() == ".png":
            command += ["-framerate", "1", "-loop", "1"]
        command += ["-i", str(item)]
    return command + [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-tune",
        "grain",
        "-profile:v",
        "high",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(output_fps),
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        _filter_number(duration_seconds),
        "-shortest",
        str(output),
    ]


def _selected_sources(plan: PetSitcomPlan) -> dict[str, dict[str, Any]]:
    from . import pet_sitcom_review as review

    try:
        return review._selected_sources(plan)
    except review.PetSitcomReviewError as exc:
        raise PetSitcomComposeError(str(exc)) from exc


def _validate_output(
    path: Path,
    runner: Callable[..., Any],
    ffprobe: str,
    *,
    ffmpeg: str | None = None,
    timings: Sequence[PetDialogueTiming] = (),
) -> None:
    _validate_dialogue_timeline(timings, FINAL_DURATION_SECONDS)
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,start_time:"
            "stream=codec_type,codec_name,profile,pix_fmt,width,height,"
            "avg_frame_rate,r_frame_rate,duration,start_time,sample_rate,"
            "channels,channel_layout,bit_rate"
        ),
        "-of",
        "json",
        str(path),
    ]
    try:
        payload = json.loads(
            runner(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30.0,
            ).stdout
            or "{}"
        )
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PetSitcomComposeError(f"Composed media validation failed: {exc}") from exc
    streams = payload.get("streams") if isinstance(payload, Mapping) else []
    videos = [
        item
        for item in streams
        if isinstance(item, Mapping) and item.get("codec_type") == "video"
    ]
    audios = [
        item
        for item in streams
        if isinstance(item, Mapping) and item.get("codec_type") == "audio"
    ]
    if (
        not isinstance(streams, list)
        or len(streams) != 2
        or len(videos) != 1
        or len(audios) != 1
    ):
        raise PetSitcomComposeError(
            "Composed media must have one video and one audio stream."
        )
    video, audio = videos[0], audios[0]
    if (
        video.get("codec_name") != "h264"
        or (video.get("width"), video.get("height")) != (1080, 1920)
    ):
        raise PetSitcomComposeError(
            f"Composed media must be 1080x1920 H.264 at {OUTPUT_FPS} fps."
        )
    if not (
        _is_exact_output_fps(video.get("avg_frame_rate"))
        and _is_exact_output_fps(video.get("r_frame_rate"))
    ):
        raise PetSitcomComposeError(
            "Composed media must use constant 30 fps with both "
            "avg_frame_rate and r_frame_rate equal to 30/1."
        )
    if video.get("profile") != "High" or video.get("pix_fmt") != "yuv420p":
        raise PetSitcomComposeError(
            "Composed media must use H.264 High profile with yuv420p pixels."
        )
    if (
        audio.get("codec_name") != "aac"
        or str(audio.get("sample_rate")) != "48000"
        or audio.get("channels") != 2
        or audio.get("channel_layout") != "stereo"
    ):
        raise PetSitcomComposeError("Composed media must use 48 kHz stereo AAC.")
    try:
        audio_bit_rate = int(str(audio.get("bit_rate")))
    except (TypeError, ValueError) as exc:
        raise PetSitcomComposeError(
            "Composed media AAC bitrate is unavailable."
        ) from exc
    if audio_bit_rate < MIN_AAC_BIT_RATE:
        raise PetSitcomComposeError(
            "Composed media must retain at least 160 kbps measured AAC bitrate."
        )
    format_record = payload.get("format")
    if not isinstance(format_record, Mapping):
        raise PetSitcomComposeError(
            "Composed media container duration is invalid."
        )
    measured_durations: dict[str, float] = {}
    measured_starts: dict[str, float] = {}
    for label, record in (
        ("container", format_record),
        ("video stream", video),
        ("audio stream", audio),
    ):
        duration = _finite_number(
            record.get("duration"),
            f"Composed media {label} duration",
        )
        if not (
            FINAL_DURATION_SECONDS - 0.15
            <= duration
            <= FINAL_DURATION_SECONDS + 0.15
        ):
            raise PetSitcomComposeError(
                f"Composed media {label} duration must match the "
                "54 second plan within tolerance."
            )
        measured_durations[label] = duration
        start = _finite_number(
            record.get("start_time"),
            f"Composed media {label} start time",
        )
        if (
            abs(start)
            > _TIMELINE_START_TOLERANCE_SECONDS
            + _TIMELINE_COMPARISON_EPSILON_SECONDS
        ):
            raise PetSitcomComposeError(
                f"Composed media {label} presentation timeline must start "
                "within half a video frame of zero."
            )
        measured_starts[label] = start
    if (
        max(measured_starts.values()) - min(measured_starts.values())
        > _TIMELINE_SYNC_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
    ):
        raise PetSitcomComposeError(
            "Composed media container, video, and audio timeline starts "
            "must remain synchronized within one video frame."
        )
    measured_ends = {
        label: measured_starts[label] + duration
        for label, duration in measured_durations.items()
    }
    for label, end in measured_ends.items():
        if (
            abs(end - FINAL_DURATION_SECONDS)
            > _TIMELINE_SYNC_TOLERANCE_SECONDS
            + _TIMELINE_COMPARISON_EPSILON_SECONDS
        ):
            raise PetSitcomComposeError(
                f"Composed media {label} presentation timeline end must "
                "match the 54 second plan within one video frame."
            )
    if (
        max(measured_ends.values()) - min(measured_ends.values())
        > _TIMELINE_SYNC_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
    ):
        raise PetSitcomComposeError(
            "Composed media container, video, and audio timeline ends "
            "must remain synchronized within one video frame."
        )
    _validate_cfr_frame_timestamps(
        path,
        runner,
        ffprobe,
        duration_seconds=measured_durations["video stream"],
        stream_start_seconds=measured_starts["video stream"],
    )
    if not _mp4_has_faststart(path):
        raise PetSitcomComposeError(
            "Composed media must place the moov atom before mdat for faststart."
        )
    ffmpeg_bin = ffmpeg or _ffmpeg_for_probe(ffprobe)
    integrated, true_peak = _measure_output_loudness(
        path,
        runner,
        ffmpeg_bin,
    )
    if abs(integrated - (-16.0)) > 0.7 + 1e-9:
        raise PetSitcomComposeError(
            "Composed media integrated loudness must be -16.0 +/-0.7 LUFS."
        )
    if true_peak > -1.5 + 1e-9:
        raise PetSitcomComposeError(
            "Composed media true peak must not exceed -1.5 dBTP."
        )
    max_volume = _measure_output_max_volume(path, runner, ffmpeg_bin)
    if max_volume >= 0.0:
        raise PetSitcomComposeError(
            "Composed media contains audio clipping."
        )


def _validate_cfr_frame_timestamps(
    path: Path,
    runner: Callable[..., Any],
    ffprobe: str,
    *,
    duration_seconds: float,
    stream_start_seconds: float = 0.0,
) -> None:
    duration = _finite_number(
        duration_seconds,
        "Composed media video stream duration",
    )
    stream_start = _finite_number(
        stream_start_seconds,
        "Composed media video stream start time",
    )
    if duration <= 0.0:
        raise PetSitcomComposeError(
            "Composed media video stream duration is invalid."
        )
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "json",
        str(path),
    ]
    try:
        payload = json.loads(
            runner(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60.0,
            ).stdout
            or "{}"
        )
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PetSitcomComposeError(
            f"Composed media frame timestamps are invalid: {exc}"
        ) from exc
    frames = payload.get("frames") if isinstance(payload, Mapping) else None
    expected_frames = int(round(duration * OUTPUT_FPS))
    if (
        not isinstance(frames, list)
        or expected_frames <= 0
        or abs(len(frames) - expected_frames) > 1
    ):
        raise PetSitcomComposeError(
            "Composed media frame timestamps count does not match its full "
            "30 fps video duration."
        )
    timestamps: list[float] = []
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise PetSitcomComposeError(
                "Composed media frame timestamps are invalid."
            )
        timestamps.append(
            _finite_number(
                frame.get("best_effort_timestamp_time"),
                "Composed media frame timestamps sample",
            )
        )
    expected_delta = 1.0 / OUTPUT_FPS
    if any(
        abs((current - previous) - expected_delta)
        > _CFR_PTS_TOLERANCE_SECONDS
        for previous, current in zip(
            timestamps,
            timestamps[1:],
        )
    ):
        raise PetSitcomComposeError(
            "Composed media frame timestamps do not form stable 30 fps CFR."
        )
    first_pts = timestamps[0]
    if (
        abs(first_pts) > _TIMELINE_START_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
        or abs(first_pts - stream_start)
        > _TIMELINE_START_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
    ):
        raise PetSitcomComposeError(
            "Composed media video presentation timeline must begin within "
            "half a video frame of zero and its stream start."
        )
    presentation_end = timestamps[-1] + _FRAME_DURATION_SECONDS
    stream_end = stream_start + duration
    if (
        abs(presentation_end - stream_end)
        > _TIMELINE_SYNC_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
        or abs(presentation_end - FINAL_DURATION_SECONDS)
        > _TIMELINE_SYNC_TOLERANCE_SECONDS
        + _TIMELINE_COMPARISON_EPSILON_SECONDS
    ):
        raise PetSitcomComposeError(
            "Composed media video presentation timeline end must cover "
            "its stream duration and the 54 second plan within one frame."
        )
    if any(
        abs(timestamp - (first_pts + index * expected_delta))
        > _CFR_PHASE_TOLERANCE_SECONDS
        for index, timestamp in enumerate(timestamps)
    ):
        raise PetSitcomComposeError(
            "Composed media frame timestamps exceed the 30 fps CFR phase "
            "tolerance."
        )


def _measure_output_loudness(
    path: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> tuple[float, float]:
    result = _validation_run(
        runner,
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
    )
    text = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
    integrated_matches = re.findall(
        r"\bI:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*LUFS",
        text,
    )
    true_peak_matches = re.findall(
        (
            r"True\s+peak:\s*(?:.|\n)*?Peak:\s*"
            r"(-?[0-9]+(?:\.[0-9]+)?)\s*dB(?:FS|TP)"
        ),
        text,
        flags=re.IGNORECASE,
    )
    if not integrated_matches or not true_peak_matches:
        raise PetSitcomComposeError(
            "Composed media loudness and true peak measurements are unavailable."
        )
    return float(integrated_matches[-1]), float(true_peak_matches[-1])


def _measure_output_max_volume(
    path: Path,
    runner: Callable[..., Any],
    ffmpeg: str,
) -> float:
    result = _validation_run(
        runner,
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
    )
    text = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
    matches = re.findall(
        r"max_volume:\s*(-?inf|-?[0-9]+(?:\.[0-9]+)?)\s*dB",
        text,
        flags=re.IGNORECASE,
    )
    if not matches:
        raise PetSitcomComposeError(
            "Composed media clipping measurement is unavailable."
        )
    return float(matches[-1])


def _validation_run(
    runner: Callable[..., Any],
    command: list[str],
) -> Any:
    try:
        return runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120.0,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
    ) as exc:
        raise PetSitcomComposeError(
            f"Composed media audio validation failed: {exc}"
        ) from exc


def _mp4_has_faststart(path: Path) -> bool:
    try:
        file_size = path.stat().st_size
        offset = 0
        atom_order: list[bytes] = []
        with path.open("rb") as handle:
            while offset < file_size:
                if file_size - offset < 8:
                    return False
                handle.seek(offset)
                header = handle.read(8)
                atom_size = int.from_bytes(header[:4], "big")
                atom_type = header[4:8]
                header_size = 8
                if atom_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return False
                    atom_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif atom_size == 0:
                    atom_size = file_size - offset
                if atom_size < header_size or offset + atom_size > file_size:
                    return False
                atom_order.append(atom_type)
                offset += atom_size
    except OSError:
        return False
    return (
        offset == file_size
        and b"moov" in atom_order
        and b"mdat" in atom_order
        and atom_order.index(b"moov") < atom_order.index(b"mdat")
    )


def _owner_audio(plan: PetSitcomPlan, shot_id: str) -> Path:
    return plan.output_dir / "audio" / "owner" / f"{shot_id}.wav"


def _cat_audio(plan: PetSitcomPlan, shot_id: str) -> Path:
    return plan.output_dir / "audio" / "cats" / f"{shot_id}.wav"


def _speech_audio(plan: PetSitcomPlan, shot_id: str, speaker: str) -> Path:
    if speaker == "owner":
        return _owner_audio(plan, shot_id)
    if speaker in _CAT_VOICE_IDS:
        return _cat_audio(plan, shot_id)
    raise PetSitcomComposeError(f"{shot_id} has no supported speech asset.")


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise PetSitcomComposeError("Dialogue timing offsets must be numbers.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PetSitcomComposeError("Dialogue timing offsets must be numbers.") from exc
    if not math.isfinite(number):
        raise PetSitcomComposeError("Dialogue timing offsets must be finite.")
    return number


def _validate_plan(plan: PetSitcomPlan) -> None:
    if not isinstance(plan, PetSitcomPlan):
        raise PetSitcomComposeError("plan must be a PetSitcomPlan.")
    try:
        _validate_plan_contract(plan)
    except PetSitcomError as exc:
        raise PetSitcomComposeError(
            f"Plan must match the approved pet sitcom contract: {exc}"
        ) from exc
    root = plan.output_dir.expanduser().absolute().resolve()
    if plan.output_dir != root:
        raise PetSitcomComposeError(
            "plan output directory must be a resolved safe path."
        )
    _reject_symlinks(root, "Plan output directory")
    for path in (
        plan.selection_path,
        plan.dialogue_timing_path,
        plan.audio_manifest_path,
        plan.clean_output,
        plan.release_output,
        plan.output_dir / "owner_native_audio_review.json",
        plan.output_dir / "sound_design.json",
        plan.output_dir / "audio" / "sound_design",
        _publish_journal_path(plan.output_dir),
        _publish_lock_path(plan.output_dir),
        *_release_text_asset_paths(plan).values(),
        *(
            path
            for shot in plan.shots
            if shot.dialogue
            for path in (
                _speech_audio(plan, shot.shot_id, str(shot.speaker)),
            )
        ),
    ):
        _reject_symlinks(path, "Plan path")
        _within(path, root, "Plan path")
def _within(path: Path, root: Path, label: str) -> None:
    try:
        path.expanduser().absolute().resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PetSitcomComposeError(
            f"{label} must stay inside the project output directory."
        ) from exc


def _reject_symlinks(path: Path, label: str) -> None:
    current = Path(path.expanduser().absolute().anchor)
    for part in path.expanduser().absolute().parts[1:]:
        current /= part
        if current.is_symlink():
            raise PetSitcomComposeError(f"{label} may not use a symlink: {current}")


def _temporary(output: Path, suffix: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=suffix
    )
    os.close(fd)
    return Path(name)


def _write_json(path: Path, payload: Mapping[str, Any], root: Path) -> None:
    _within(path, root, "Output")
    _reject_symlinks(path, "Output")
    temporary = _temporary(path, ".json")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise PetSitcomComposeError(f"Unable to hash local artifact: {path}") from exc
    return digest.hexdigest()


def _run(runner: Callable[..., Any], command: list[str]) -> None:
    try:
        runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300.0,
        )
    except subprocess.CalledProcessError as exc:
        raise PetSitcomComposeError(
            f"FFmpeg composition failed: {str(exc.stderr or exc.stdout or exc).strip()[-1200:]}"
        ) from exc
    except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
        raise PetSitcomComposeError(f"FFmpeg composition failed: {exc}") from exc


def _stage(source: Path, destination: Path, root: Path) -> Path:
    return _stage_with_suffix(source, destination, root, ".stage")


def _stage_with_suffix(
    source: Path,
    destination: Path,
    root: Path,
    suffix: str,
) -> Path:
    _within(destination, root, "Output")
    _reject_symlinks(destination, "Output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = _temporary(destination, suffix)
    try:
        with source.open("rb") as origin, staged.open("wb") as target:
            shutil.copyfileobj(origin, target)
            target.flush()
            os.fsync(target.fileno())
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _publish_outputs_atomically(
    staged: Sequence[Path],
    destinations: Sequence[Path],
    root: Path,
    *,
    _lock: _PublishLock | None = None,
) -> None:
    if _lock is None:
        with _compose_publish_lock(root) as publish_lock:
            _publish_outputs_atomically(
                staged,
                destinations,
                root,
                _lock=publish_lock,
            )
        return
    _require_publish_lock(_lock, root)
    if (
        len(staged) != len(destinations)
        or len(staged) != len(_PUBLISH_ROLES)
    ):
        raise PetSitcomComposeError(
            "Atomic publish requires the clean and release output pair."
        )
    _reject_symlinks(root, "Output root")
    _recover_publish_transaction(root, destinations, _lock=_lock)
    transaction_id = os.urandom(8).hex()
    transaction = _publish_transaction_dir(root, transaction_id)
    transaction_root = transaction.parent
    entries: list[dict[str, Any]] = []
    journal = _publish_journal_path(root)
    try:
        _create_publish_transaction_dir(root, transaction_root, transaction)
        for index, (role, source, destination) in enumerate(
            zip(_PUBLISH_ROLES, staged, destinations, strict=True)
        ):
            _within(source, root, "Staged output")
            _within(destination, root, "Output")
            _reject_symlinks(source, "Staged output")
            _reject_symlinks(destination, "Output")
            if (
                not source.is_file()
                or source.is_symlink()
                or source in destinations
                or source in staged[:index]
                or destination in destinations[:index]
            ):
                raise PetSitcomComposeError(
                    "Atomic publish paths are invalid."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            _reject_symlinks(destination.parent, "Output")
            if destination.exists() and not destination.is_file():
                raise PetSitcomComposeError(
                    "Atomic publish destination must be a regular file."
                )
            new_copy, old_backup = _publish_entry_paths(
                root,
                transaction_id,
                index,
                role,
            )
            new_sha256 = _sha(source)
            _copy_verified_input(
                source,
                new_copy,
                new_sha256,
                f"{role} transaction copy",
            )
            had_original = destination.is_file()
            old_sha256 = _sha(destination) if had_original else ""
            if had_original:
                _copy_verified_input(
                    destination,
                    old_backup,
                    old_sha256,
                    f"{role} transaction backup",
                )
            entries.append(
                {
                    "role": role,
                    "index": index,
                    "destination": str(destination),
                    "new_copy": str(new_copy),
                    "old_backup": str(old_backup),
                    "had_original": had_original,
                    "old_sha256": old_sha256,
                    "new_sha256": new_sha256,
                }
            )
        _fsync_dir(transaction)
        payload = {
            "schema_version": PUBLISH_JOURNAL_SCHEMA,
            "transaction_id": transaction_id,
            "validated": True,
            "phase": "publishing",
            "entries": entries,
        }
        _write_publish_journal(journal, payload, root)
    except BaseException as exc:
        _cleanup_publish_transaction(
            root,
            journal,
            transaction_id,
            remove_journal=True,
        )
        if isinstance(exc, Exception):
            raise PetSitcomComposeError(
                f"Atomic publish journal failed: {exc}"
            ) from exc
        raise

    committed = False
    try:
        _validate_new_materials(entries)
        for entry in entries:
            _install_transaction_copy(
                entry,
                transaction_id,
                use_old=False,
            )
        if not _pair_matches(entries, use_old=False):
            raise PetSitcomComposeError(
                "Published output hash verification failed."
            )
        payload["phase"] = "published"
        try:
            _write_publish_journal(journal, payload, root)
        except BaseException:
            if not _journal_matches(journal, payload):
                raise
        committed = True
    except BaseException as exc:
        restore_interrupt = _restore_pair_until_consistent(
            entries,
            transaction_id,
            use_old=True,
        )
        _cleanup_publish_transaction(
            root,
            journal,
            transaction_id,
            remove_journal=True,
        )
        if restore_interrupt is not None:
            raise restore_interrupt
        if isinstance(exc, Exception):
            raise PetSitcomComposeError(
                f"Atomic publish failed: {exc}"
            ) from exc
        raise
    finally:
        for source in staged:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
    if committed:
        _cleanup_publish_transaction(
            root,
            journal,
            transaction_id,
            remove_journal=True,
        )


def _create_publish_transaction_dir(
    root: Path,
    transaction_root: Path,
    transaction: Path,
) -> None:
    _within(transaction_root, root, "Publish transaction root")
    _within(transaction, root, "Publish transaction directory")
    _reject_symlinks(transaction_root, "Publish transaction root")
    transaction_root.mkdir(parents=True, exist_ok=True)
    _reject_symlinks(transaction_root, "Publish transaction root")
    try:
        transaction.mkdir(mode=0o700)
    except OSError as exc:
        raise PetSitcomComposeError(
            "Unable to create private publish transaction directory."
        ) from exc
    _reject_symlinks(transaction, "Publish transaction directory")
    _fsync_dir(transaction)
    _fsync_dir(transaction_root)
    _fsync_dir(root)


def _publish_transaction_dir(root: Path, transaction_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", transaction_id):
        raise PetSitcomComposeError(
            "Publish transaction id is invalid."
        )
    return root / _PUBLISH_TRANSACTION_ROOT / transaction_id


def _publish_entry_paths(
    root: Path,
    transaction_id: str,
    index: int,
    role: str,
) -> tuple[Path, Path]:
    if (
        index not in range(len(_PUBLISH_ROLES))
        or role != _PUBLISH_ROLES[index]
    ):
        raise PetSitcomComposeError(
            "Publish transaction role is invalid."
        )
    transaction = _publish_transaction_dir(root, transaction_id)
    prefix = f"{index:02d}-{role}"
    return transaction / f"{prefix}.new", transaction / f"{prefix}.old"


def _install_temp_path(
    destination: Path,
    transaction_id: str,
    index: int,
) -> Path:
    return destination.with_name(
        f".{destination.name}.{transaction_id}.{index:02d}.install"
    )


def _install_transaction_copy(
    entry: Mapping[str, Any],
    transaction_id: str,
    *,
    use_old: bool,
) -> None:
    destination = Path(str(entry["destination"]))
    expected = str(
        entry["old_sha256"] if use_old else entry["new_sha256"]
    )
    source = Path(
        str(entry["old_backup"] if use_old else entry["new_copy"])
    )
    if _file_matches(destination, expected):
        return
    if not _file_matches(source, expected):
        raise PetSitcomComposeError(
            f"Transaction material is unavailable for {destination.name}."
        )
    temporary = _install_temp_path(
        destination,
        transaction_id,
        int(entry["index"]),
    )
    _reject_symlinks(temporary, "Publish install temporary")
    if temporary.exists():
        if not temporary.is_file() or temporary.is_symlink():
            raise PetSitcomComposeError(
                "Publish install temporary is invalid."
            )
        temporary.unlink()
    try:
        _copy_verified_input(
            source,
            temporary,
            expected,
            f"{entry['role']} publish install",
        )
        os.replace(temporary, destination)
        _fsync_dir(destination.parent)
        if not _file_matches(destination, expected):
            raise PetSitcomComposeError(
                f"Published {entry['role']} output hash is invalid."
            )
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_new_materials(
    entries: Sequence[Mapping[str, Any]],
) -> None:
    if not all(
        _file_matches(
            Path(str(entry["new_copy"])),
            str(entry["new_sha256"]),
        )
        or _file_matches(
            Path(str(entry["destination"])),
            str(entry["new_sha256"]),
        )
        for entry in entries
    ):
        raise PetSitcomComposeError(
            "Publish transaction cannot obtain both verified new outputs."
        )


def _validate_old_materials(
    entries: Sequence[Mapping[str, Any]],
) -> None:
    if not all(
        not entry["had_original"]
        or _file_matches(
            Path(str(entry["old_backup"])),
            str(entry["old_sha256"]),
        )
        for entry in entries
    ):
        raise PetSitcomComposeError(
            "Publish transaction cannot obtain every required old backup."
        )


def _restore_pair_until_consistent(
    entries: Sequence[Mapping[str, Any]],
    transaction_id: str,
    *,
    use_old: bool,
) -> BaseException | None:
    first_interrupt: BaseException | None = None
    for _ in range(_PAIR_RECOVERY_ATTEMPTS):
        try:
            if use_old:
                _validate_old_materials(entries)
            else:
                _validate_new_materials(entries)
            for entry in entries:
                destination = Path(str(entry["destination"]))
                if use_old and not entry["had_original"]:
                    if destination.exists() or destination.is_symlink():
                        _reject_symlinks(destination, "Publish destination")
                        destination.unlink()
                        _fsync_dir(destination.parent)
                    continue
                _install_transaction_copy(
                    entry,
                    transaction_id,
                    use_old=use_old,
                )
            if not _pair_matches(entries, use_old=use_old):
                raise PetSitcomComposeError(
                    "Publish transaction pair verification failed."
                )
            for parent in {
                Path(str(entry["destination"])).parent
                for entry in entries
            }:
                _fsync_dir(parent)
            if not _pair_matches(entries, use_old=use_old):
                raise PetSitcomComposeError(
                    "Publish transaction durability verification failed."
                )
            return first_interrupt
        except BaseException as exc:
            if first_interrupt is None:
                first_interrupt = exc
    raise PetSitcomComposeError(
        "Publish transaction could not durably restore a consistent output "
        "pair; the journal and recovery materials were retained."
    ) from first_interrupt


def _pair_matches(
    entries: Sequence[Mapping[str, Any]],
    *,
    use_old: bool,
) -> bool:
    for entry in entries:
        destination = Path(str(entry["destination"]))
        if use_old and not entry["had_original"]:
            if destination.exists() or destination.is_symlink():
                return False
            continue
        expected = str(
            entry["old_sha256"] if use_old else entry["new_sha256"]
        )
        if not _file_matches(destination, expected):
            return False
    return True


def _file_matches(path: Path, expected_sha256: str) -> bool:
    return (
        bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha256))
        and path.is_file()
        and not path.is_symlink()
        and _sha(path) == expected_sha256
    )


def _journal_matches(
    path: Path,
    expected: Mapping[str, Any],
) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")) == expected
    except (OSError, json.JSONDecodeError):
        return False


def _publish_journal_path(root: Path) -> Path:
    return root / ".pet-sitcom-compose-transaction.json"


def _publish_lock_path(root: Path) -> Path:
    return root / _PUBLISH_LOCK_NAME


@contextmanager
def _compose_publish_lock(root: Path) -> Iterator[_PublishLock]:
    canonical_root = root.expanduser().absolute().resolve(strict=False)
    if root != canonical_root or not root.is_dir():
        raise PetSitcomComposeError(
            "Compose publish lock requires a canonical project directory."
        )
    _reject_symlinks(root, "Compose publish lock root")
    lock_path = _publish_lock_path(root)
    if lock_path != lock_path.resolve(strict=False):
        raise PetSitcomComposeError(
            "Compose publish lock path must be canonical."
        )
    _within(lock_path, root, "Compose publish lock")
    _reject_symlinks(lock_path, "Compose publish lock")
    ownership_token = object()
    descriptor: int | None = None
    acquired = False
    primary_error: BaseException | None = None
    cleanup_errors: list[tuple[str, BaseException]] = []
    try:
        with _PUBLISH_LOCK_GUARD:
            if root in _PUBLISH_LOCKED_ROOTS:
                raise PetSitcomComposeError(
                    "Compose publish is locked by another active invocation."
                )
            _PUBLISH_LOCKED_ROOTS[root] = ownership_token
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise PetSitcomComposeError(
                "Unable to open the canonical compose publish lock."
            ) from exc
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PetSitcomComposeError(
                "Compose publish lock must be a regular non-symlink file."
            )
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except (BlockingIOError, OSError) as exc:
            raise PetSitcomComposeError(
                "Compose publish is locked by another active invocation."
            ) from exc
        acquired = True
        yield _PublishLock(
            root=root,
            path=lock_path,
            descriptor=descriptor,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            try:
                if descriptor is not None and acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except BaseException as exc:
                cleanup_errors.append(("unlock", exc))
        finally:
            try:
                try:
                    if descriptor is not None:
                        os.close(descriptor)
                except BaseException as exc:
                    cleanup_errors.append(("close", exc))
            finally:
                try:
                    with _PUBLISH_LOCK_GUARD:
                        if (
                            _PUBLISH_LOCKED_ROOTS.get(root)
                            is ownership_token
                        ):
                            del _PUBLISH_LOCKED_ROOTS[root]
                except BaseException as exc:
                    cleanup_errors.append(("registry revoke", exc))
        if cleanup_errors:
            details = ", ".join(
                f"{operation} {type(error).__name__}"
                for operation, error in cleanup_errors
            )
            if primary_error is not None:
                try:
                    primary_error.add_note(
                        f"Compose publish lock cleanup also failed: {details}"
                    )
                except BaseException:
                    pass
            else:
                raise PetSitcomComposeError(
                    f"Compose publish lock cleanup failed: {details}"
                ) from cleanup_errors[0][1]


def _require_publish_lock(lock: _PublishLock, root: Path) -> None:
    if (
        lock.root != root
        or lock.path != _publish_lock_path(root)
        or lock.descriptor < 0
    ):
        raise PetSitcomComposeError(
            "Compose publish lock does not belong to this project."
        )


def _write_publish_journal(
    path: Path,
    payload: Mapping[str, Any],
    root: Path,
) -> None:
    _write_json(path, payload, root)


def _recover_publish_transaction(
    root: Path,
    destinations: Sequence[Path],
    *,
    _lock: _PublishLock | None = None,
) -> None:
    if _lock is None:
        with _compose_publish_lock(root) as publish_lock:
            _recover_publish_transaction(
                root,
                destinations,
                _lock=publish_lock,
            )
        return
    _require_publish_lock(_lock, root)
    journal = _publish_journal_path(root)
    _within(journal, root, "Publish transaction journal")
    _reject_symlinks(journal, "Publish transaction journal")
    if not journal.exists():
        return
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetSitcomComposeError(
            "Publish transaction journal is invalid."
        ) from exc
    entries = _validate_publish_journal(
        payload,
        root,
        destinations,
    )
    transaction_id = str(payload["transaction_id"])
    use_old = payload["phase"] == "publishing"
    interrupt = _restore_pair_until_consistent(
        entries,
        transaction_id,
        use_old=use_old,
    )
    if interrupt is not None:
        raise interrupt
    _cleanup_publish_transaction(
        root,
        journal,
        transaction_id,
        remove_journal=True,
    )


def _validate_publish_journal(
    payload: Any,
    root: Path,
    destinations: Sequence[Path],
) -> list[dict[str, Any]]:
    fields = {
        "schema_version",
        "transaction_id",
        "validated",
        "phase",
        "entries",
    }
    entry_fields = {
        "role",
        "index",
        "destination",
        "new_copy",
        "old_backup",
        "had_original",
        "old_sha256",
        "new_sha256",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema_version") != PUBLISH_JOURNAL_SCHEMA
        or not re.fullmatch(
            r"[0-9a-f]{16}",
            str(payload.get("transaction_id") or ""),
        )
        or payload.get("validated") is not True
        or payload.get("phase") not in {"publishing", "published"}
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) != len(_PUBLISH_ROLES)
        or len(destinations) != len(_PUBLISH_ROLES)
    ):
        raise PetSitcomComposeError(
            "Publish transaction journal is invalid."
        )
    transaction_id = str(payload["transaction_id"])
    transaction = _publish_transaction_dir(root, transaction_id)
    _within(transaction, root, "Publish transaction directory")
    _reject_symlinks(transaction, "Publish transaction directory")
    if not transaction.is_dir():
        raise PetSitcomComposeError(
            "Publish transaction directory is missing."
        )
    result: list[dict[str, Any]] = []
    all_paths = {
        _publish_journal_path(root),
        *destinations,
    }
    allowed_transaction_paths: set[Path] = set()
    for index, (role, raw, expected_destination) in enumerate(
        zip(
            _PUBLISH_ROLES,
            payload["entries"],
            destinations,
            strict=True,
        )
    ):
        if not isinstance(raw, dict) or set(raw) != entry_fields:
            raise PetSitcomComposeError(
                "Publish transaction journal entry is invalid."
            )
        entry = dict(raw)
        destination = _canonical_journal_path(
            entry["destination"],
            root,
            f"Publish destination {index}",
        )
        expected_new, expected_old = _publish_entry_paths(
            root,
            transaction_id,
            index,
            role,
        )
        if (
            entry.get("role") != role
            or isinstance(entry.get("index"), bool)
            or entry.get("index") != index
            or destination != expected_destination
            or entry.get("new_copy") != str(expected_new)
            or entry.get("old_backup") != str(expected_old)
            or not isinstance(entry.get("had_original"), bool)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(entry.get("new_sha256") or ""),
            )
            or (
                entry["had_original"]
                and not re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(entry.get("old_sha256") or ""),
                )
            )
            or (
                not entry["had_original"]
                and entry.get("old_sha256") != ""
            )
        ):
            raise PetSitcomComposeError(
                "Publish transaction journal entry is stale."
            )
        for path, label in (
            (expected_new, f"Publish new copy {index}"),
            (expected_old, f"Publish old backup {index}"),
        ):
            _within(path, root, label)
            _reject_symlinks(path, label)
            if path in all_paths:
                raise PetSitcomComposeError(
                    "Publish transaction paths must be distinct."
                )
            all_paths.add(path)
            allowed_transaction_paths.add(path)
        entry["destination"] = str(destination)
        entry["new_copy"] = str(expected_new)
        entry["old_backup"] = str(expected_old)
        result.append(entry)
    for child in transaction.iterdir():
        if (
            child not in allowed_transaction_paths
            or child.is_symlink()
            or not child.is_file()
        ):
            raise PetSitcomComposeError(
                "Publish transaction directory contains an invalid path."
            )
    return result


def _canonical_journal_path(
    value: Any,
    root: Path,
    label: str,
) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PetSitcomComposeError(f"{label} path is invalid.")
    path = Path(value)
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise PetSitcomComposeError(
            f"{label} must use a canonical absolute path."
        )
    _within(path, root, label)
    _reject_symlinks(path, label)
    return path


def _cleanup_publish_transaction(
    root: Path,
    journal: Path,
    transaction_id: str,
    *,
    remove_journal: bool,
) -> None:
    try:
        transaction = _publish_transaction_dir(root, transaction_id)
        _within(transaction, root, "Publish transaction directory")
        _reject_symlinks(transaction, "Publish transaction directory")
        if remove_journal:
            expected_journal = _publish_journal_path(root)
            if journal != expected_journal:
                raise PetSitcomComposeError(
                    "Publish journal cleanup path is invalid."
                )
            _reject_symlinks(journal, "Publish transaction journal")
            if journal.exists():
                try:
                    current = json.loads(
                        journal.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise PetSitcomComposeError(
                        "Publish journal cleanup identity is invalid."
                    ) from exc
                if (
                    not isinstance(current, Mapping)
                    or current.get("transaction_id") != transaction_id
                ):
                    raise PetSitcomComposeError(
                        "Publish journal cleanup transaction does not match."
                    )
            journal.unlink(missing_ok=True)
            _fsync_dir(root)
        for index, role in enumerate(_PUBLISH_ROLES):
            new_copy, old_backup = _publish_entry_paths(
                root,
                transaction_id,
                index,
                role,
            )
            for path in (new_copy, old_backup):
                _reject_symlinks(path, "Publish transaction artifact")
                path.unlink(missing_ok=True)
        if transaction.exists():
            transaction.rmdir()
        transaction_root = transaction.parent
        if transaction_root.exists():
            transaction_root.rmdir()
        _fsync_dir(root)
    except BaseException:
        # The pair is already verified. A remaining journal is recoverable,
        # while a removed journal can only leave unreferenced private copies.
        pass


def _is_exact_output_fps(value: Any) -> bool:
    if not isinstance(value, str) or value != "30/1":
        return False
    numerator, denominator = value.split("/", 1)
    try:
        result = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return math.isfinite(result) and result == float(OUTPUT_FPS)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise PetSitcomComposeError(f"{label} is invalid.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetSitcomComposeError(f"{label} is invalid.") from exc
    if not math.isfinite(result):
        raise PetSitcomComposeError(f"{label} is invalid.")
    return result


def _ffprobe_for(binary: str) -> str:
    path = Path(binary)
    return (
        str(path.with_name("ffprobe"))
        if path.name == "ffmpeg" and path.parent != Path(".")
        else "ffprobe"
    )


def _ffmpeg_for_probe(binary: str) -> str:
    path = Path(binary)
    return (
        str(path.with_name("ffmpeg"))
        if path.name == "ffprobe" and path.parent != Path(".")
        else "ffmpeg"
    )


def _font() -> Path:
    for candidate in _FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise PetSitcomComposeError(
        "A Chinese-capable system font is required for release captions."
    )


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
