from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from factory.pet_replica import PetReplicaPlan, ReplicaShot, validate_pet_replica_plan


_REFERENCE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-reference.v1"
_ANNOTATION_SCHEMA_VERSION = "motion-comic-factory.pet-replica-annotations.v2"
_OCR_EVIDENCE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-ocr-evidence.v1"
_EXPECTED_VIDEO_CODEC = "h264"
_EXPECTED_AUDIO_CODEC = "aac"
_CONTACT_FRAME_COUNT = 40
_PILOT_CONTACT_FRAME_COUNT = 12
_SCENE_ANCHOR_IDS = frozenset({"scene_sofa", "scene_table", "scene_phone"})
_RENDERABLE_OCR_CLASSIFICATIONS = frozenset({"dialogue_subtitle"})
_EXCLUDED_OCR_CLASSIFICATIONS = frozenset(
    {
        "platform_watermark",
        "account_identity",
        "author_identity",
        "avatar",
        "creator_label",
        "decorative_caption",
        "source_end_card",
    }
)
_OCR_CLASSIFICATIONS = _RENDERABLE_OCR_CLASSIFICATIONS | _EXCLUDED_OCR_CLASSIFICATIONS
_OCR_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_OCR_DETECTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_BRANDING_TEXT = re.compile(
    (
        r"(?:@\S+|原作者|作者|账号|帐号|用户名|用户\s*ID|抖音号|小红书号|UP主|"
        r"creator|account|username|platform[_\s-]*id)"
    ),
    re.I,
)
_CAPTION_SAFE_REGION = {"x": 36, "y": 880, "width": 648, "height": 320}


class PetReplicaReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicaReferenceProbe:
    source_sha256: str
    duration_s: float
    width: int
    height: int
    fps: int
    video_codec: str
    audio_codec: str
    audio_sample_rate: int
    audio_channels: int
    last_video_frame_s: float


@dataclass(frozen=True)
class ReplicaCaptionSafeRegion:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ReplicaCaptionPlacement:
    x: int
    y: int
    width: int
    height: int
    alignment: str


@dataclass(frozen=True)
class ReplicaOCREvent:
    event_id: str
    detection_id: str
    shot_id: str
    classification: str
    reviewed_text: str
    start_frame: int
    end_frame: int
    start_s: float
    end_s: float
    placement: ReplicaCaptionPlacement
    manual_reviewed: bool
    renderable: bool


@dataclass(frozen=True)
class ReplicaOCREvidenceBinding:
    shot_id: str
    evidence_path: str
    evidence_sha256: str
    detected_item_count: int
    review_complete: bool
    reviewed_zero: bool


@dataclass(frozen=True)
class ReplicaOCRDetection:
    detection_id: str
    detected_text: str
    start_frame: int
    end_frame: int
    start_s: float
    end_s: float
    source_bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class ReplicaShotAnnotation:
    shot_id: str
    characters: tuple[str, ...]
    speaker: str
    scene_anchor_id: str
    location: str
    framing: str
    action: str
    source_audio: bool
    manual_review_required: bool
    ocr_events: tuple[ReplicaOCREvent, ...] = ()
    ocr_evidence: ReplicaOCREvidenceBinding | None = None
    subtitle: str = ""


def probe_reference_media(
    plan: PetReplicaPlan,
    runner: Callable[..., Any] = subprocess.run,
) -> ReplicaReferenceProbe:
    validate_pet_replica_plan(plan)
    _require_source(plan.source_video)
    source_sha256 = _sha256(plan.source_video)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration:stream=index,codec_type,codec_name,width,height,"
            "avg_frame_rate,r_frame_rate,sample_rate,channels"
        ),
        "-of",
        "json",
        str(plan.source_video),
    ]
    completed = _run(runner, command, "ffprobe")
    frame_command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "json",
        str(plan.source_video),
    ]
    frame_completed = _run(runner, frame_command, "ffprobe frame timestamps")
    if _sha256(plan.source_video) != source_sha256:
        raise PetReplicaReferenceError("Reference source changed during ffprobe.")
    probe = _parse_probe(
        _json_output(completed, "ffprobe"),
        plan,
        source_sha256,
        _last_video_frame_s(
            _json_output(frame_completed, "ffprobe frame timestamps")
        ),
    )
    path = _reference_path(plan, "reference_manifest.json")
    _write_json_atomically(path, asdict(probe) | {"schema_version": _REFERENCE_SCHEMA_VERSION})
    return probe


def extract_reference_evidence(
    plan: PetReplicaPlan,
    runner: Callable[..., Any] = subprocess.run,
    *,
    destination: Path | None = None,
) -> Path:
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    reference_root = _safe_output_path(root, destination or root / "reference")
    _ensure_safe_directory(reference_root, root)
    probe = probe_reference_media(plan, runner)
    source_sha256 = probe.source_sha256

    records: list[dict[str, Any]] = []
    for shot in plan.shots:
        for label, timestamp_s in _shot_timestamps(
            shot,
            plan.fps,
            probe.last_video_frame_s,
        ).items():
            frame_path = reference_root / "shots" / shot.shot_id / f"{label}.jpg"
            records.append(
                _extract_frame(
                    source=plan.source_video,
                    source_sha256=source_sha256,
                    shot_id=shot.shot_id,
                    label=label,
                    timestamp_s=timestamp_s,
                    output=frame_path,
                    root=root,
                    runner=runner,
                )
            )

    contact_root = reference_root / "contact_sheets"
    pilot_frames = _extract_contact_frames(
        plan,
        source_sha256,
        root,
        contact_root / "pilot_frames",
        _sample_timestamps(
            0.0,
            plan.pilot_end_s,
            _PILOT_CONTACT_FRAME_COUNT,
            plan.fps,
            probe.last_video_frame_s,
        ),
        "pilot",
        runner,
    )
    records.extend(pilot_frames[1])
    pilot_sheet = contact_root / "pilot_4x3.jpg"
    _write_contact_sheet(pilot_frames[0], pilot_sheet, columns=4, rows=3, root=root)

    full_sheets: list[Path] = []
    for page, timestamps in enumerate(
        _chunked(
            _sample_timestamps(
                0.0,
                plan.duration_s,
                _CONTACT_FRAME_COUNT,
                plan.fps,
                probe.last_video_frame_s,
            ),
            _CONTACT_FRAME_COUNT,
        ),
        start=1,
    ):
        frames, page_records = _extract_contact_frames(
            plan,
            source_sha256,
            root,
            contact_root / f"full_{page:02d}_frames",
            timestamps,
            f"full_{page:02d}",
            runner,
        )
        records.extend(page_records)
        sheet = contact_root / f"full_{page:02d}_5x8.jpg"
        _write_contact_sheet(frames, sheet, columns=5, rows=8, root=root)
        full_sheets.append(sheet)

    if _sha256(plan.source_video) != source_sha256:
        raise PetReplicaReferenceError("Reference source changed during evidence extraction.")
    manifest_path = reference_root / "evidence_manifest.json"
    _write_json_atomically(
        manifest_path,
        {
            "schema_version": _REFERENCE_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "last_video_frame_s": probe.last_video_frame_s,
            "frames": records,
            "contact_sheets": [
                _contact_sheet_record(pilot_sheet, "4x3", source_sha256, root),
                *(
                    _contact_sheet_record(path, "5x8", source_sha256, root)
                    for path in full_sheets
                ),
            ],
        },
    )
    return manifest_path


def write_shot_annotation_template(plan: PetReplicaPlan) -> Path:
    validate_pet_replica_plan(plan)
    path = _reference_path(plan, "shot_annotations.template.json")
    _write_json_atomically(
        path,
        {
            "schema_version": _ANNOTATION_SCHEMA_VERSION,
            "caption_safe_region": dict(_CAPTION_SAFE_REGION),
            "shots": [
                {
                    "shot_id": shot.shot_id,
                    "characters": [],
                    "speaker": "",
                    "scene_anchor_id": "",
                    "location": "",
                    "framing": "",
                    "action": "",
                    "ocr_review": {
                        "evidence_path": "",
                        "evidence_sha256": "",
                        "detected_item_count": 0,
                        "review_complete": False,
                    },
                    "ocr_events": [],
                    "source_audio": shot.source_audio,
                    "manual_review_required": True,
                }
                for shot in plan.shots
            ],
        },
    )
    return path


def load_reviewed_shot_annotations(
    plan: PetReplicaPlan,
    *,
    require_ocr_events: bool = False,
    expected_annotations_sha256: str | None = None,
) -> tuple[ReplicaShotAnnotation, ...]:
    validate_pet_replica_plan(plan)
    payload = _load_reviewed_annotation_payload(
        plan,
        require_ocr_events=require_ocr_events,
        expected_annotations_sha256=expected_annotations_sha256,
    )
    items = payload.get("shots")
    if not isinstance(items, list) or len(items) != len(plan.shots):
        raise PetReplicaReferenceError("Reviewed shot annotations must cover every shot.")

    safe_region = (
        _caption_safe_region(payload.get("caption_safe_region"), plan)
        if payload.get("schema_version") == _ANNOTATION_SCHEMA_VERSION
        else ReplicaCaptionSafeRegion(**_CAPTION_SAFE_REGION)
    )
    require_ocr_evidence = payload.get("schema_version") == _ANNOTATION_SCHEMA_VERSION
    source_sha256 = _sha256(plan.source_video) if require_ocr_evidence else ""
    annotations = tuple(
        _annotation_from_payload(
            item,
            plan,
            index,
            safe_region,
            require_ocr_events=require_ocr_events,
            require_ocr_evidence=require_ocr_evidence,
            source_sha256=source_sha256,
        )
        for index, item in enumerate(items)
    )
    event_ids = [
        event.event_id
        for annotation in annotations
        for event in annotation.ocr_events
    ]
    if len(event_ids) != len(set(event_ids)):
        raise PetReplicaReferenceError("Reviewed OCR event identifiers must be unique.")
    detection_ids = [
        event.detection_id
        for annotation in annotations
        for event in annotation.ocr_events
    ]
    if len(detection_ids) != len(set(detection_ids)):
        raise PetReplicaReferenceError(
            "Reviewed OCR detection identifiers must be unique across shots."
        )
    return annotations


def load_reviewed_caption_safe_region(
    plan: PetReplicaPlan,
    *,
    expected_annotations_sha256: str | None = None,
) -> ReplicaCaptionSafeRegion:
    validate_pet_replica_plan(plan)
    payload = _load_reviewed_annotation_payload(
        plan,
        require_ocr_events=True,
        expected_annotations_sha256=expected_annotations_sha256,
    )
    return _caption_safe_region(payload.get("caption_safe_region"), plan)


def _load_reviewed_annotation_payload(
    plan: PetReplicaPlan,
    *,
    require_ocr_events: bool,
    expected_annotations_sha256: str | None = None,
) -> Mapping[str, Any]:
    path = _reference_path(plan, "shot_annotations.json")
    if not path.is_file():
        raise PetReplicaReferenceError("Shot annotations require manual review.")
    try:
        contents = path.read_bytes()
        if (
            expected_annotations_sha256 is not None
            and hashlib.sha256(contents).hexdigest() != expected_annotations_sha256
        ):
            raise PetReplicaReferenceError(
                "Reviewed shot annotations changed after the captured snapshot."
            )
        payload = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PetReplicaReferenceError("Reviewed shot annotations are not valid JSON.") from exc
    accepted_schemas = (
        {_ANNOTATION_SCHEMA_VERSION}
        if require_ocr_events
        else {
            _ANNOTATION_SCHEMA_VERSION,
            "motion-comic-factory.pet-replica-annotations.v1",
        }
    )
    if not isinstance(payload, Mapping) or payload.get("schema_version") not in accepted_schemas:
        raise PetReplicaReferenceError("Reviewed shot annotations have an invalid schema.")
    return payload


def _parse_probe(
    payload: Mapping[str, Any],
    plan: PetReplicaPlan,
    source_sha256: str,
    last_video_frame_s: float,
) -> ReplicaReferenceProbe:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise PetReplicaReferenceError("ffprobe did not return stream metadata.")
    video_streams = [item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"]
    if len(video_streams) != 1 or len(audio_streams) != 1:
        raise PetReplicaReferenceError("Reference requires exactly one video and one audio stream.")
    video = video_streams[0]
    audio = audio_streams[0]
    width = _integer(video.get("width"), "video width")
    height = _integer(video.get("height"), "video height")
    if (width, height) != (plan.width, plan.height):
        raise PetReplicaReferenceError("Reference dimensions do not match the media contract.")
    fps = _frame_rate(video.get("avg_frame_rate"), "average frame rate")
    container_fps = _frame_rate(video.get("r_frame_rate"), "container frame rate")
    if fps != plan.fps or container_fps != plan.fps:
        raise PetReplicaReferenceError("Reference frame rate does not match the media contract.")
    if video.get("codec_name") != _EXPECTED_VIDEO_CODEC:
        raise PetReplicaReferenceError("Reference video codec does not match the media contract.")
    if audio.get("codec_name") != _EXPECTED_AUDIO_CODEC:
        raise PetReplicaReferenceError("Reference audio codec does not match the media contract.")
    sample_rate = _integer(audio.get("sample_rate"), "audio sample rate")
    channels = _integer(audio.get("channels"), "audio channels")
    if sample_rate != 44100 or channels != 2:
        raise PetReplicaReferenceError("Reference audio layout does not match the media contract.")
    format_data = payload.get("format")
    duration_s = _float(format_data.get("duration") if isinstance(format_data, Mapping) else None, "duration")
    if abs(duration_s - plan.duration_s) > 1 / plan.fps:
        raise PetReplicaReferenceError("Reference duration does not match the media contract.")
    return ReplicaReferenceProbe(
        source_sha256=source_sha256,
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        video_codec=_EXPECTED_VIDEO_CODEC,
        audio_codec=_EXPECTED_AUDIO_CODEC,
        audio_sample_rate=sample_rate,
        audio_channels=channels,
        last_video_frame_s=last_video_frame_s,
    )


def _last_video_frame_s(payload: Mapping[str, Any]) -> float:
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise PetReplicaReferenceError("ffprobe did not return video frame timestamps.")
    timestamps: list[float] = []
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise PetReplicaReferenceError("ffprobe returned an invalid video frame timestamp.")
        value = frame.get("best_effort_timestamp_time")
        try:
            timestamp_s = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PetReplicaReferenceError(
                "ffprobe returned an invalid video frame timestamp."
            ) from exc
        if not math.isfinite(timestamp_s) or timestamp_s < 0:
            raise PetReplicaReferenceError("ffprobe returned an invalid video frame timestamp.")
        timestamps.append(timestamp_s)
    return max(timestamps)


def _extract_contact_frames(
    plan: PetReplicaPlan,
    source_sha256: str,
    root: Path,
    directory: Path,
    timestamps: Sequence[float],
    label: str,
    runner: Callable[..., Any],
) -> tuple[list[Path], list[dict[str, Any]]]:
    paths: list[Path] = []
    records: list[dict[str, Any]] = []
    for index, timestamp_s in enumerate(timestamps, start=1):
        shot = _shot_at(plan.shots, timestamp_s)
        path = directory / f"{index:03d}.jpg"
        records.append(
            _extract_frame(
                source=plan.source_video,
                source_sha256=source_sha256,
                shot_id=shot.shot_id,
                label=f"{label}_{index:03d}",
                timestamp_s=timestamp_s,
                output=path,
                root=root,
                runner=runner,
            )
        )
        paths.append(path)
    return paths, records


def _extract_frame(
    *,
    source: Path,
    source_sha256: str,
    shot_id: str,
    label: str,
    timestamp_s: float,
    output: Path,
    root: Path,
    runner: Callable[..., Any],
) -> dict[str, Any]:
    _ensure_safe_parent(output, root)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ss",
        f"{timestamp_s:.6f}",
        "-frames:v",
        "1",
        "-an",
        "-q:v",
        "2",
        str(output),
    ]
    _run(runner, command, "ffmpeg")
    if not output.is_file() or output.stat().st_size <= 0:
        raise PetReplicaReferenceError(f"ffmpeg did not produce evidence frame: {output}")
    return {
        "source_sha256": source_sha256,
        "shot_id": shot_id,
        "label": label,
        "timestamp_s": timestamp_s,
        "command": _redacted_command(command, source),
        "image_sha256": _sha256(output),
        "image_path": str(output.relative_to(root)),
    }


def _write_contact_sheet(
    frames: Sequence[Path],
    output: Path,
    *,
    columns: int,
    rows: int,
    root: Path,
) -> None:
    _ensure_safe_parent(output, root)
    cell_width = 144
    cell_height = 256
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "black")
    for index, frame_path in enumerate(frames):
        if index >= columns * rows:
            break
        try:
            with Image.open(frame_path) as source:
                image = source.convert("RGB")
        except OSError as exc:
            raise PetReplicaReferenceError(f"Evidence frame cannot form a contact sheet: {frame_path}") from exc
        image.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
        x = (index % columns) * cell_width + (cell_width - image.width) // 2
        y = (index // columns) * cell_height + (cell_height - image.height) // 2
        canvas.paste(image, (x, y))
    _write_image_atomically(output, canvas)


def _annotation_from_payload(
    item: object,
    plan: PetReplicaPlan,
    index: int,
    safe_region: ReplicaCaptionSafeRegion,
    *,
    require_ocr_events: bool,
    require_ocr_evidence: bool,
    source_sha256: str,
) -> ReplicaShotAnnotation:
    if not isinstance(item, Mapping):
        raise PetReplicaReferenceError("Reviewed shot annotation records must be objects.")
    shot = plan.shots[index]
    if item.get("shot_id") != shot.shot_id:
        raise PetReplicaReferenceError("Reviewed shot annotations must match source shot order.")
    if item.get("manual_review_required") is not False:
        raise PetReplicaReferenceError(f"{shot.shot_id} requires manual review.")
    characters = item.get("characters")
    if not isinstance(characters, list) or not all(isinstance(value, str) for value in characters):
        raise PetReplicaReferenceError(f"{shot.shot_id} has invalid characters.")
    if any(value not in plan.characters for value in characters):
        raise PetReplicaReferenceError(f"{shot.shot_id} references an unknown character.")
    fields = (
        "speaker",
        "scene_anchor_id",
        "location",
        "framing",
        "action",
    )
    values = {field: item.get(field) for field in fields}
    if any(not isinstance(value, str) for value in values.values()):
        raise PetReplicaReferenceError(f"{shot.shot_id} has invalid annotation text.")
    for field in ("framing", "action"):
        if not values[field].strip():
            raise PetReplicaReferenceError(f"{shot.shot_id} requires a non-empty {field}.")
    if values["scene_anchor_id"] not in _SCENE_ANCHOR_IDS:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} requires a valid scene_anchor_id."
        )
    if not isinstance(item.get("source_audio"), bool):
        raise PetReplicaReferenceError(f"{shot.shot_id} has invalid source audio flag.")
    if require_ocr_evidence:
        ocr_evidence, detections = _ocr_evidence_from_review(
            item.get("ocr_review"),
            plan,
            shot,
            source_sha256,
        )
    else:
        ocr_evidence = None
        detections = ()
    event_items = item.get("ocr_events")
    if event_items is None and not require_ocr_events and not require_ocr_evidence:
        event_items = []
    if not isinstance(event_items, list):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} requires explicit reviewed OCR events."
        )
    ocr_events = tuple(
        _ocr_event_from_payload(value, plan, shot, safe_region)
        for value in event_items
    )
    if require_ocr_evidence:
        _validate_detection_mappings(shot, detections, ocr_events)
        subtitle = _legacy_subtitle_from_events(ocr_events)
    else:
        subtitle = item.get("subtitle", "")
        if not isinstance(subtitle, str):
            raise PetReplicaReferenceError(
                f"{shot.shot_id} has invalid legacy subtitle text."
            )
    return ReplicaShotAnnotation(
        shot_id=shot.shot_id,
        characters=tuple(characters),
        speaker=values["speaker"],
        scene_anchor_id=values["scene_anchor_id"],
        location=values["location"],
        framing=values["framing"],
        action=values["action"],
        ocr_evidence=ocr_evidence,
        ocr_events=ocr_events,
        source_audio=item["source_audio"],
        manual_review_required=False,
        subtitle=subtitle,
    )


def _legacy_subtitle_from_events(
    events: Sequence[ReplicaOCREvent],
) -> str:
    dialogue = sorted(
        (
            event
            for event in events
            if event.renderable and event.classification == "dialogue_subtitle"
        ),
        key=lambda event: (event.start_frame, event.end_frame, event.event_id),
    )
    return "\n".join(event.reviewed_text for event in dialogue)


def _ocr_evidence_from_review(
    value: object,
    plan: PetReplicaPlan,
    shot: ReplicaShot,
    source_sha256: str,
) -> tuple[ReplicaOCREvidenceBinding, tuple[ReplicaOCRDetection, ...]]:
    if not isinstance(value, Mapping):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} requires a complete OCR review evidence binding."
        )
    if value.get("review_complete") is not True:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} requires a complete OCR review attestation."
        )
    evidence_sha256 = value.get("evidence_sha256")
    if (
        not isinstance(evidence_sha256, str)
        or _SHA256.fullmatch(evidence_sha256) is None
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence SHA-256 is invalid."
        )
    detected_item_count = value.get("detected_item_count")
    if (
        isinstance(detected_item_count, bool)
        or not isinstance(detected_item_count, int)
        or detected_item_count < 0
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence count is invalid."
        )
    evidence_path = value.get("evidence_path")
    relative_path = Path(evidence_path) if isinstance(evidence_path, str) else None
    if (
        not isinstance(evidence_path, str)
        or not evidence_path
        or "\\" in evidence_path
        or relative_path is None
        or relative_path.is_absolute()
        or relative_path.as_posix() != evidence_path
        or len(relative_path.parts) != 4
        or relative_path.parts[:3] != ("reference", "ocr_evidence", shot.shot_id)
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence path is not canonical."
        )
    if relative_path.name != f"{evidence_sha256}.json":
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence path does not bind its SHA-256."
        )
    expected_path = relative_path
    root = _output_root(plan)
    _reject_symlinks(root / expected_path, "OCR evidence path")
    evidence_file = _safe_output_path(root, root / expected_path)
    expected_parent = (
        root / "reference" / "ocr_evidence" / shot.shot_id
    ).resolve(strict=False)
    if evidence_file.parent.resolve(strict=False) != expected_parent:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence path is not canonical."
        )
    if not evidence_file.is_file() or evidence_file.is_symlink():
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence path must be a regular nonsymlink file."
        )
    try:
        evidence_bytes = evidence_file.read_bytes()
    except OSError as exc:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence is unreadable."
        ) from exc
    if hashlib.sha256(evidence_bytes).hexdigest() != evidence_sha256:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence SHA-256 does not match."
        )
    try:
        payload = json.loads(evidence_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence is not valid JSON."
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _OCR_EVIDENCE_SCHEMA_VERSION
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence schema is invalid."
        )
    if payload.get("source_sha256") != source_sha256:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence source SHA-256 does not match."
        )
    if payload.get("shot_id") != shot.shot_id:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence shot identity does not match."
        )
    source_window = payload.get("source_window")
    shot_start_frame = round(shot.start_s * plan.fps)
    shot_end_frame = round(shot.end_s * plan.fps)
    if not isinstance(source_window, Mapping):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence source window does not match."
        )
    try:
        source_start_frame = _strict_integer(
            source_window.get("start_frame"),
            f"{shot.shot_id} source window start frame",
        )
        source_end_frame = _strict_integer(
            source_window.get("end_frame"),
            f"{shot.shot_id} source window end frame",
        )
    except PetReplicaReferenceError as exc:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence source window does not match."
        ) from exc
    if (
        source_start_frame != shot_start_frame
        or source_end_frame != shot_end_frame
        or not _optional_derived_seconds_match(
            source_window,
            "start_s",
            source_start_frame,
            plan.fps,
        )
        or not _optional_derived_seconds_match(
            source_window,
            "end_s",
            source_end_frame,
            plan.fps,
        )
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence source window does not match."
        )
    detected_items = payload.get("detected_items")
    if not isinstance(detected_items, list):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence detected items are invalid."
        )
    detections = tuple(
        _ocr_detection_from_payload(item, plan, shot)
        for item in detected_items
    )
    detection_ids = [item.detection_id for item in detections]
    if len(detection_ids) != len(set(detection_ids)):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has duplicate OCR detection identifiers."
        )
    if detected_item_count != len(detections):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence count does not match."
        )
    reviewed_zero = payload.get("reviewed_zero")
    if (
        not isinstance(reviewed_zero, bool)
        or reviewed_zero != (len(detections) == 0)
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR evidence reviewed-zero result is invalid."
        )
    return (
        ReplicaOCREvidenceBinding(
            shot_id=shot.shot_id,
            evidence_path=expected_path.as_posix(),
            evidence_sha256=evidence_sha256,
            detected_item_count=detected_item_count,
            review_complete=True,
            reviewed_zero=reviewed_zero,
        ),
        detections,
    )


def _ocr_detection_from_payload(
    value: object,
    plan: PetReplicaPlan,
    shot: ReplicaShot,
) -> ReplicaOCRDetection:
    if not isinstance(value, Mapping):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} OCR detections must be objects."
        )
    detection_id = value.get("detection_id")
    if (
        not isinstance(detection_id, str)
        or _OCR_DETECTION_ID.fullmatch(detection_id) is None
    ):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has an invalid OCR detection identifier."
        )
    detected_text = value.get("detected_text")
    if not isinstance(detected_text, str) or not detected_text.strip():
        raise PetReplicaReferenceError(
            f"{detection_id} requires detected OCR text."
        )
    start_frame, end_frame, start_s, end_s = _ocr_frame_window(
        value,
        plan,
        shot,
        f"{detection_id} OCR timing",
    )
    bbox = value.get("source_bbox")
    if not isinstance(bbox, Mapping):
        raise PetReplicaReferenceError(
            f"{detection_id} requires an explicit source bbox."
        )
    x = _strict_integer(bbox.get("x"), f"{detection_id} source bbox x")
    y = _strict_integer(bbox.get("y"), f"{detection_id} source bbox y")
    width = _strict_integer(bbox.get("width"), f"{detection_id} source bbox width")
    height = _strict_integer(bbox.get("height"), f"{detection_id} source bbox height")
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > plan.width
        or y + height > plan.height
    ):
        raise PetReplicaReferenceError(
            f"{detection_id} source bbox must stay inside the source frame."
        )
    return ReplicaOCRDetection(
        detection_id=detection_id,
        detected_text=detected_text.strip(),
        start_frame=start_frame,
        end_frame=end_frame,
        start_s=start_s,
        end_s=end_s,
        source_bbox=(x, y, width, height),
    )


def _validate_detection_mappings(
    shot: ReplicaShot,
    detections: Sequence[ReplicaOCRDetection],
    events: Sequence[ReplicaOCREvent],
) -> None:
    detection_by_id = {item.detection_id: item for item in detections}
    event_detection_ids = [item.detection_id for item in events]
    if len(event_detection_ids) != len(set(event_detection_ids)):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has a duplicate OCR detection mapping."
        )
    extra = sorted(set(event_detection_ids) - set(detection_by_id))
    if extra:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has an extra OCR detection mapping: {extra}."
        )
    missing = sorted(set(detection_by_id) - set(event_detection_ids))
    if missing:
        if not events:
            raise PetReplicaReferenceError(
                f"{shot.shot_id} does not contain a complete OCR review."
            )
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has a missing OCR detection mapping: {missing}."
        )
    for event in events:
        detection = detection_by_id[event.detection_id]
        if (
            event.start_frame != detection.start_frame
            or event.end_frame != detection.end_frame
        ):
            raise PetReplicaReferenceError(
                f"{event.event_id} has a stale OCR detection timing mapping."
            )


def _ocr_frame_window(
    value: Mapping[str, Any],
    plan: PetReplicaPlan,
    shot: ReplicaShot,
    label: str,
) -> tuple[int, int, float, float]:
    start_frame = _strict_integer(value.get("start_frame"), f"{label} start frame")
    end_frame = _strict_integer(value.get("end_frame"), f"{label} end frame")
    shot_start_frame = round(shot.start_s * plan.fps)
    shot_end_frame = round(shot.end_s * plan.fps)
    if (
        start_frame < shot_start_frame
        or end_frame > shot_end_frame
        or end_frame <= start_frame
    ):
        raise PetReplicaReferenceError(
            f"{label} must stay within its source shot frame window."
        )
    if not _optional_derived_seconds_match(value, "start_s", start_frame, plan.fps):
        raise PetReplicaReferenceError(
            f"{label} start seconds must match its derived frame time."
        )
    if not _optional_derived_seconds_match(value, "end_s", end_frame, plan.fps):
        raise PetReplicaReferenceError(
            f"{label} end seconds must match its derived frame time."
        )
    return start_frame, end_frame, start_frame / plan.fps, end_frame / plan.fps


def _optional_derived_seconds_match(
    value: Mapping[str, Any],
    key: str,
    frame_index: int,
    fps: int,
) -> bool:
    if key not in value:
        return True
    try:
        seconds = float(value[key])
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(seconds) and seconds == frame_index / fps


def _caption_safe_region(
    value: object,
    plan: PetReplicaPlan,
) -> ReplicaCaptionSafeRegion:
    if not isinstance(value, Mapping):
        raise PetReplicaReferenceError("Reviewed captions require a declared safe region.")
    x = _strict_integer(value.get("x"), "caption safe region x")
    y = _strict_integer(value.get("y"), "caption safe region y")
    width = _strict_integer(value.get("width"), "caption safe region width")
    height = _strict_integer(value.get("height"), "caption safe region height")
    if (
        x < 0
        or y < plan.height // 2
        or width <= 0
        or height <= 0
        or x + width > plan.width
        or y + height > plan.height
    ):
        raise PetReplicaReferenceError(
            "Caption safe region must be a bounded lower-screen rectangle."
        )
    return ReplicaCaptionSafeRegion(x=x, y=y, width=width, height=height)


def _ocr_event_from_payload(
    value: object,
    plan: PetReplicaPlan,
    shot: ReplicaShot,
    safe_region: ReplicaCaptionSafeRegion,
) -> ReplicaOCREvent:
    if not isinstance(value, Mapping):
        raise PetReplicaReferenceError(
            f"{shot.shot_id} reviewed OCR events must be objects."
        )
    event_id = value.get("event_id")
    if not isinstance(event_id, str) or _OCR_EVENT_ID.fullmatch(event_id) is None:
        raise PetReplicaReferenceError(
            f"{shot.shot_id} has an invalid OCR event identifier."
        )
    detection_id = value.get("detection_id")
    if (
        not isinstance(detection_id, str)
        or _OCR_DETECTION_ID.fullmatch(detection_id) is None
    ):
        raise PetReplicaReferenceError(
            f"{event_id} has an invalid OCR detection mapping."
        )
    classification = value.get("classification")
    if not isinstance(classification, str) or classification not in _OCR_CLASSIFICATIONS:
        raise PetReplicaReferenceError(
            f"{event_id} has an unknown OCR classification."
        )
    reviewed_text = value.get("reviewed_text")
    if not isinstance(reviewed_text, str) or not reviewed_text.strip():
        raise PetReplicaReferenceError(f"{event_id} requires reviewed text.")
    reviewed_text = reviewed_text.strip()
    if (
        classification in _RENDERABLE_OCR_CLASSIFICATIONS
        and _BRANDING_TEXT.search(reviewed_text)
    ):
        raise PetReplicaReferenceError(
            f"{event_id} contains branding disguised as dialogue."
        )
    start_frame, end_frame, start_s, end_s = _ocr_frame_window(
        value,
        plan,
        shot,
        f"{event_id} OCR timing",
    )
    placement = _caption_placement(value.get("placement"), event_id, safe_region)
    if value.get("manual_reviewed") is not True:
        raise PetReplicaReferenceError(f"{event_id} requires explicit manual review.")
    return ReplicaOCREvent(
        event_id=event_id,
        detection_id=detection_id,
        shot_id=shot.shot_id,
        classification=classification,
        reviewed_text=reviewed_text,
        start_frame=start_frame,
        end_frame=end_frame,
        start_s=start_s,
        end_s=end_s,
        placement=placement,
        manual_reviewed=True,
        renderable=classification in _RENDERABLE_OCR_CLASSIFICATIONS,
    )


def _caption_placement(
    value: object,
    event_id: str,
    safe_region: ReplicaCaptionSafeRegion,
) -> ReplicaCaptionPlacement:
    if not isinstance(value, Mapping):
        raise PetReplicaReferenceError(
            f"{event_id} requires explicit lower-screen caption placement."
        )
    x = _strict_integer(value.get("x"), f"{event_id} placement x")
    y = _strict_integer(value.get("y"), f"{event_id} placement y")
    width = _strict_integer(value.get("width"), f"{event_id} placement width")
    height = _strict_integer(value.get("height"), f"{event_id} placement height")
    alignment = value.get("alignment")
    if alignment != "bottom_center" or width <= 0 or height <= 0:
        raise PetReplicaReferenceError(
            f"{event_id} requires explicit lower-screen caption placement."
        )
    if (
        x < safe_region.x
        or y < safe_region.y
        or x + width > safe_region.x + safe_region.width
        or y + height > safe_region.y + safe_region.height
    ):
        raise PetReplicaReferenceError(
            f"{event_id} placement must stay inside the declared caption safe region."
        )
    return ReplicaCaptionPlacement(
        x=x,
        y=y,
        width=width,
        height=height,
        alignment=alignment,
    )


def _strict_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PetReplicaReferenceError(f"{label} must be an integer.")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PetReplicaReferenceError(f"{label} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise PetReplicaReferenceError(f"{label} must be finite.")
    return number


def _shot_timestamps(
    shot: ReplicaShot,
    fps: int,
    last_video_frame_s: float,
) -> dict[str, float]:
    return {
        "start": shot.start_s,
        "middle": (shot.start_s + shot.end_s) / 2,
        "end": min(
            max(shot.start_s, shot.end_s - 1 / fps),
            last_video_frame_s,
        ),
    }


def _sample_timestamps(
    start_s: float,
    end_s: float,
    count: int,
    fps: int,
    last_video_frame_s: float,
) -> tuple[float, ...]:
    final = min(max(start_s, end_s - 1 / fps), last_video_frame_s)
    if count == 1:
        return (start_s,)
    increment = (final - start_s) / (count - 1)
    return tuple(start_s + index * increment for index in range(count))


def _shot_at(shots: Sequence[ReplicaShot], timestamp_s: float) -> ReplicaShot:
    for shot in shots:
        if shot.start_s <= timestamp_s < shot.end_s:
            return shot
    return shots[-1]


def _chunked(values: Sequence[float], size: int) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(values[index : index + size]) for index in range(0, len(values), size))


def _contact_sheet_record(path: Path, layout: str, source_sha256: str, root: Path) -> dict[str, Any]:
    return {
        "source_sha256": source_sha256,
        "layout": layout,
        "image_sha256": _sha256(path),
        "image_path": str(path.relative_to(root)),
    }


def _json_output(completed: Any, tool: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(completed.stdout or "{}")
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise PetReplicaReferenceError(f"{tool} did not return valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaReferenceError(f"{tool} did not return a JSON object.")
    return payload


def _run(runner: Callable[..., Any], command: list[str], tool: str) -> Any:
    try:
        return runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300.0,
        )
    except subprocess.CalledProcessError as exc:
        detail = str(exc.stderr or exc.stdout or exc).strip()[-1200:]
        raise PetReplicaReferenceError(f"{tool} failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
        raise PetReplicaReferenceError(f"{tool} failed: {exc}") from exc


def _reference_path(plan: PetReplicaPlan, filename: str) -> Path:
    root = _output_root(plan)
    path = _safe_output_path(root, root / "reference" / filename)
    _ensure_safe_parent(path, root)
    return path


def _output_root(plan: PetReplicaPlan) -> Path:
    root = plan.output_root.expanduser().absolute()
    _reject_symlinks(root, "output root")
    return root.resolve(strict=False)


def _safe_output_path(root: Path, path: Path) -> Path:
    candidate = path.expanduser().absolute()
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetReplicaReferenceError("Evidence output must stay inside the output root.") from exc
    _reject_symlinks(candidate, "evidence output")
    return candidate


def _ensure_safe_directory(path: Path, root: Path) -> None:
    _safe_output_path(root, path)
    _ensure_safe_parent(path / ".directory", root)
    if not path.exists():
        path.mkdir()
    if not path.is_dir() or path.is_symlink():
        raise PetReplicaReferenceError(f"Evidence output must be a directory: {path}")


def _ensure_safe_parent(path: Path, root: Path) -> None:
    _safe_output_path(root, path)
    try:
        relative = path.parent.relative_to(root)
    except ValueError as exc:
        raise PetReplicaReferenceError("Evidence output must stay inside the output root.") from exc
    if not root.exists():
        root.mkdir(parents=True)
    if not root.is_dir() or root.is_symlink():
        raise PetReplicaReferenceError("Output root must be a regular directory.")
    current = root
    for component in relative.parts:
        current /= component
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise PetReplicaReferenceError(f"Evidence output may not use symlinks: {current}")
        else:
            current.mkdir()


def _reject_symlinks(path: Path, label: str) -> None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise PetReplicaReferenceError(f"{label} may not use symlinks: {current}")


def _write_json_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    _write_bytes_atomically(
        path,
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        ).encode("utf-8"),
    )


def _write_image_atomically(path: Path, image: Image.Image) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            image.save(handle, format="JPEG", quality=90)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_source(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise PetReplicaReferenceError("Reference source video must be a regular file.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PetReplicaReferenceError(f"Unable to hash reference artifact: {path}") from exc
    return digest.hexdigest()


def _redacted_command(command: Sequence[str], source: Path) -> str:
    return shlex.join("<source-video>" if value == str(source) else value for value in command)


def _integer(value: object, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.") from exc
    if parsed <= 0:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.")
    return parsed


def _float(value: object, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.") from exc
    if parsed <= 0:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.")
    return parsed


def _frame_rate(value: object, label: str) -> int:
    if not isinstance(value, str) or "/" not in value:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.")
    numerator, denominator = value.split("/", maxsplit=1)
    try:
        top = int(numerator)
        bottom = int(denominator)
    except ValueError as exc:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.") from exc
    if top <= 0 or bottom <= 0 or top % bottom:
        raise PetReplicaReferenceError(f"Reference {label} is invalid.")
    return top // bottom
