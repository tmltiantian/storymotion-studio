from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from factory.pet_replica import PetReplicaPlan, validate_pet_replica_plan
from factory.pet_replica_reference import ReplicaShotAnnotation


_AUDIO_SCHEMA_VERSION = "motion-comic-factory.pet-replica-audio.v1"
_LOCAL_EVALUATION_SCOPE = "local_evaluation_only"
_PUBLIC_RELEASE_BLOCKER = "Replace or license the source audio."
_FULL_AUDIO_RELATIVE_PATH = Path("audio/source_audio.aac")
_DRIVE_DIRECTORY = Path("audio/drive")
_CONTAINER_TIMESTAMP_TOLERANCE_S = 0.001


class PetReplicaAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicaAudioAsset:
    shot_id: str | None
    path: Path
    sha256: str
    duration_s: float
    sample_rate: int
    channels: int
    codec: str
    source_start_s: float
    source_end_s: float


@dataclass(frozen=True)
class ReplicaAacTimeline:
    sample_rate: int
    packet_count: int
    first_packet_pts_s: float
    first_packet_duration_s: float
    last_packet_pts_s: float
    last_packet_duration_s: float
    last_packet_end_s: float
    packet_span_s: float
    skip_samples: int | None
    discard_padding: int | None
    logical_duration_s: float | None


@dataclass(frozen=True)
class ReplicaPayloadEvidence:
    sha256: str
    byte_count: int


@dataclass(frozen=True)
class ReplicaAudioManifest:
    path: Path
    source_sha256: str
    full_source: ReplicaAudioAsset
    shots: Mapping[str, ReplicaAudioAsset]
    source_timeline: ReplicaAacTimeline
    raw_aac_timeline: ReplicaAacTimeline
    normalized_payload: ReplicaPayloadEvidence
    usage_scope: str
    public_release_ready: bool
    public_release_blocker: str


def extract_replica_audio(
    plan: PetReplicaPlan,
    annotations: Sequence[ReplicaShotAnnotation],
    runner: Callable[..., Any] = subprocess.run,
) -> ReplicaAudioManifest:
    """Extract a private, source-locked audio drive without altering its timeline."""
    validate_pet_replica_plan(plan)
    _validate_reviewed_annotations(plan, annotations)
    _require_regular_source(plan.source_video)
    root = _output_root(plan)
    manifest_path = _safe_output_path(root, root / "audio" / "audio_manifest.json")
    full_path = _safe_output_path(root, root / _FULL_AUDIO_RELATIVE_PATH)
    _ensure_safe_parent(manifest_path, root)
    _ensure_safe_parent(full_path, root)

    source_sha256 = _sha256(plan.source_video)
    source_timeline = _probe_source_timeline(plan.source_video, plan, runner)
    _extract_full_source_audio(plan.source_video, full_path, runner)
    full_source = _probed_asset(
        path=full_path,
        root=root,
        shot_id=None,
        source_start_s=0.0,
        source_end_s=plan.duration_s,
        expected_codec="aac",
        expected_sample_rate=44100,
        expected_channels=2,
        expected_duration_s=plan.duration_s,
        logical_timeline=True,
        runner=runner,
    )
    raw_aac_timeline = _probe_raw_aac_timeline(
        full_path,
        source_timeline,
        runner,
    )
    normalized_payload = _verify_normalized_payload(
        source=plan.source_video,
        raw_aac=full_path,
        root=root,
        runner=runner,
    )

    shot_assets: dict[str, ReplicaAudioAsset] = {}
    for shot in plan.shots:
        path = _safe_output_path(root, root / _DRIVE_DIRECTORY / f"{shot.shot_id}.wav")
        _ensure_safe_parent(path, root)
        _extract_shot_drive_audio(
            plan.source_video, shot.start_s, shot.end_s, path, runner
        )
        shot_assets[shot.shot_id] = _probed_asset(
            path=path,
            root=root,
            shot_id=shot.shot_id,
            source_start_s=shot.start_s,
            source_end_s=shot.end_s,
            expected_codec="pcm_s16le",
            expected_sample_rate=48000,
            expected_channels=2,
            expected_duration_s=shot.duration_s,
            logical_timeline=False,
            runner=runner,
        )

    if _sha256(plan.source_video) != source_sha256:
        raise PetReplicaAudioError(
            "Reference source hash changed during audio extraction."
        )

    manifest = ReplicaAudioManifest(
        path=manifest_path,
        source_sha256=source_sha256,
        full_source=full_source,
        shots=MappingProxyType(shot_assets),
        source_timeline=source_timeline,
        raw_aac_timeline=raw_aac_timeline,
        normalized_payload=normalized_payload,
        usage_scope=_LOCAL_EVALUATION_SCOPE,
        public_release_ready=False,
        public_release_blocker=_PUBLIC_RELEASE_BLOCKER,
    )
    _write_json_atomically(manifest_path, _manifest_payload(manifest, root))
    return validate_replica_audio_manifest(plan, manifest_path, runner=runner)


def validate_replica_audio_manifest(
    plan: PetReplicaPlan,
    manifest_path: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> ReplicaAudioManifest:
    validate_pet_replica_plan(plan)
    _require_regular_source(plan.source_video)
    root = _output_root(plan)
    path = _safe_output_path(root, manifest_path)
    if path != root / "audio" / "audio_manifest.json":
        raise PetReplicaAudioError(
            "Audio manifest must use the contracted output path."
        )
    if not path.is_file() or path.is_symlink():
        raise PetReplicaAudioError(
            "Audio manifest must be a regular file inside the output root."
        )
    payload = _read_manifest_payload(path)
    if payload.get("schema_version") != _AUDIO_SCHEMA_VERSION:
        raise PetReplicaAudioError("Audio manifest has an invalid schema.")
    if payload.get("usage_scope") != _LOCAL_EVALUATION_SCOPE:
        raise PetReplicaAudioError("Audio manifest must remain local evaluation only.")
    if payload.get("public_release_ready") is not False:
        raise PetReplicaAudioError("Audio manifest may not approve public release.")
    if payload.get("public_release_blocker") != _PUBLIC_RELEASE_BLOCKER:
        raise PetReplicaAudioError(
            "Audio manifest has an invalid public release blocker."
        )

    source_sha256 = _string(payload.get("source_sha256"), "source hash")
    if _sha256(plan.source_video) != source_sha256:
        raise PetReplicaAudioError(
            "Reference source hash does not match the audio manifest."
        )
    _reject_sensitive_manifest_values(payload, plan.source_video)
    source_timeline = _probe_source_timeline(plan.source_video, plan, runner)
    _require_timeline_match(payload.get("source_timeline"), source_timeline, "source")

    full_source = _asset_from_payload(
        payload.get("full_source"),
        root=root,
        plan=plan,
        expected_shot_id=None,
        expected_relative_path=_FULL_AUDIO_RELATIVE_PATH,
        expected_codec="aac",
        expected_sample_rate=44100,
        expected_channels=2,
        expected_start_s=0.0,
        expected_end_s=plan.duration_s,
        logical_timeline=True,
        runner=runner,
    )
    raw_aac_timeline = _probe_raw_aac_timeline(
        full_source.path,
        source_timeline,
        runner,
    )
    _require_timeline_match(
        payload.get("raw_aac_timeline"), raw_aac_timeline, "raw AAC"
    )
    normalized_payload = _verify_normalized_payload(
        source=plan.source_video,
        raw_aac=full_source.path,
        root=root,
        runner=runner,
    )
    _require_payload_match(payload.get("normalized_payload"), normalized_payload)
    shots_payload = payload.get("shots")
    if not isinstance(shots_payload, Mapping) or set(shots_payload) != {
        shot.shot_id for shot in plan.shots
    }:
        raise PetReplicaAudioError(
            "Audio manifest must include one drive asset for every shot."
        )
    shot_assets = {
        shot.shot_id: _asset_from_payload(
            shots_payload.get(shot.shot_id),
            root=root,
            plan=plan,
            expected_shot_id=shot.shot_id,
            expected_relative_path=_DRIVE_DIRECTORY / f"{shot.shot_id}.wav",
            expected_codec="pcm_s16le",
            expected_sample_rate=48000,
            expected_channels=2,
            expected_start_s=shot.start_s,
            expected_end_s=shot.end_s,
            logical_timeline=False,
            runner=runner,
        )
        for shot in plan.shots
    }
    if _sha256(plan.source_video) != source_sha256:
        raise PetReplicaAudioError(
            "Reference source hash changed during audio validation."
        )
    return ReplicaAudioManifest(
        path=path,
        source_sha256=source_sha256,
        full_source=full_source,
        shots=MappingProxyType(shot_assets),
        source_timeline=source_timeline,
        raw_aac_timeline=raw_aac_timeline,
        normalized_payload=normalized_payload,
        usage_scope=_LOCAL_EVALUATION_SCOPE,
        public_release_ready=False,
        public_release_blocker=_PUBLIC_RELEASE_BLOCKER,
    )


def audio_for_shot(manifest: ReplicaAudioManifest, shot_id: str) -> Path | None:
    asset = manifest.shots.get(shot_id)
    return None if asset is None else asset.path


def _validate_reviewed_annotations(
    plan: PetReplicaPlan,
    annotations: Sequence[ReplicaShotAnnotation],
) -> None:
    if len(annotations) != len(plan.shots):
        raise PetReplicaAudioError(
            "Audio extraction requires reviewed annotations for every shot."
        )
    for shot, annotation in zip(plan.shots, annotations):
        if not isinstance(annotation, ReplicaShotAnnotation):
            raise PetReplicaAudioError(
                "Audio extraction requires reviewed annotation records."
            )
        if annotation.shot_id != shot.shot_id:
            raise PetReplicaAudioError(
                "Reviewed annotations must match source shot order."
            )
        if annotation.manual_review_required:
            raise PetReplicaAudioError(
                f"{shot.shot_id} requires manual review before audio extraction."
            )
        if not annotation.framing.strip() or not annotation.action.strip():
            raise PetReplicaAudioError(
                f"{shot.shot_id} requires reviewed framing and action."
            )
        if annotation.source_audio is not shot.source_audio:
            raise PetReplicaAudioError(
                f"{shot.shot_id} source-audio contract does not match the plan."
            )


def _extract_full_source_audio(
    source: Path,
    destination: Path,
    runner: Callable[..., Any],
) -> None:
    _run(
        runner,
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "copy",
            str(destination),
        ],
        "ffmpeg source audio stream copy",
    )
    _require_regular_output(destination)


def _extract_shot_drive_audio(
    source: Path,
    start_s: float,
    end_s: float,
    destination: Path,
    runner: Callable[..., Any],
) -> None:
    filter_graph = f"atrim=start={start_s:.6f}:end={end_s:.6f},asetpts=PTS-STARTPTS"
    _run(
        runner,
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            filter_graph,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        "ffmpeg shot drive audio",
    )
    _require_regular_output(destination)


def _probed_asset(
    *,
    path: Path,
    root: Path,
    shot_id: str | None,
    source_start_s: float,
    source_end_s: float,
    expected_codec: str,
    expected_sample_rate: int,
    expected_channels: int,
    expected_duration_s: float,
    logical_timeline: bool,
    runner: Callable[..., Any],
) -> ReplicaAudioAsset:
    probe = _probe_audio(path, runner)
    if logical_timeline:
        _validate_audio_layout(
            probe,
            codec=expected_codec,
            sample_rate=expected_sample_rate,
            channels=expected_channels,
        )
        measured_duration_s = expected_duration_s
    else:
        _validate_audio_probe(
            probe,
            codec=expected_codec,
            sample_rate=expected_sample_rate,
            channels=expected_channels,
            duration_s=expected_duration_s,
        )
        measured_duration_s = probe["duration_s"]
    return ReplicaAudioAsset(
        shot_id=shot_id,
        path=path,
        sha256=_sha256(path),
        duration_s=measured_duration_s,
        sample_rate=probe["sample_rate"],
        channels=probe["channels"],
        codec=probe["codec"],
        source_start_s=source_start_s,
        source_end_s=source_end_s,
    )


def _asset_from_payload(
    value: object,
    *,
    root: Path,
    plan: PetReplicaPlan,
    expected_shot_id: str | None,
    expected_relative_path: Path,
    expected_codec: str,
    expected_sample_rate: int,
    expected_channels: int,
    expected_start_s: float,
    expected_end_s: float,
    logical_timeline: bool,
    runner: Callable[..., Any],
) -> ReplicaAudioAsset:
    if not isinstance(value, Mapping):
        raise PetReplicaAudioError("Audio manifest contains an invalid asset record.")
    shot_id = value.get("shot_id")
    if shot_id != expected_shot_id:
        raise PetReplicaAudioError(
            "Audio manifest asset shot identifier does not match the plan."
        )
    relative_path = _relative_path(value.get("path"), "asset path")
    if relative_path != expected_relative_path:
        raise PetReplicaAudioError(
            "Audio manifest asset path does not match the audio contract."
        )
    path = _safe_output_path(root, root / relative_path)
    _require_regular_output(path)
    start_s = _number(value.get("source_start_s"), "source start")
    end_s = _number(value.get("source_end_s"), "source end")
    if not _same_source_timestamp(
        start_s, expected_start_s
    ) or not _same_source_timestamp(end_s, expected_end_s):
        raise PetReplicaAudioError(
            "Audio manifest source window does not match the source timeline."
        )
    if end_s <= start_s:
        raise PetReplicaAudioError(
            "Audio manifest source window must have positive duration."
        )
    asset = _probed_asset(
        path=path,
        root=root,
        shot_id=expected_shot_id,
        source_start_s=start_s,
        source_end_s=end_s,
        expected_codec=expected_codec,
        expected_sample_rate=expected_sample_rate,
        expected_channels=expected_channels,
        expected_duration_s=expected_end_s - expected_start_s,
        logical_timeline=logical_timeline,
        runner=runner,
    )
    if asset.sha256 != _string(value.get("sha256"), "asset hash"):
        raise PetReplicaAudioError("Audio manifest asset hash does not match its file.")
    if not _within_frame(
        _number(value.get("duration_s"), "asset duration"), asset.duration_s, plan.fps
    ):
        raise PetReplicaAudioError("Audio manifest duration does not match its file.")
    if value.get("codec") != asset.codec:
        raise PetReplicaAudioError("Audio manifest codec does not match its file.")
    if (
        value.get("sample_rate") != asset.sample_rate
        or value.get("channels") != asset.channels
    ):
        raise PetReplicaAudioError("Audio manifest layout does not match its file.")
    return asset


def _probe_audio(path: Path, runner: Callable[..., Any]) -> dict[str, Any]:
    completed = _run(
        runner,
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        "ffprobe audio",
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise PetReplicaAudioError("ffprobe audio did not return valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaAudioError("ffprobe audio did not return a JSON object.")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise PetReplicaAudioError("ffprobe audio did not return stream metadata.")
    audio_streams = [
        item
        for item in streams
        if isinstance(item, Mapping) and item.get("codec_type") == "audio"
    ]
    if len(audio_streams) != 1:
        raise PetReplicaAudioError("Audio artifact requires exactly one audio stream.")
    stream = audio_streams[0]
    format_data = payload.get("format")
    if not isinstance(format_data, Mapping):
        raise PetReplicaAudioError("ffprobe audio did not return format metadata.")
    return {
        "codec": _string(stream.get("codec_name"), "codec"),
        "sample_rate": _positive_integer(stream.get("sample_rate"), "sample rate"),
        "channels": _positive_integer(stream.get("channels"), "channels"),
        "duration_s": _positive_number(format_data.get("duration"), "duration"),
    }


def _probe_source_timeline(
    source: Path,
    plan: PetReplicaPlan,
    runner: Callable[..., Any],
) -> ReplicaAacTimeline:
    metadata = _probe_audio(source, runner)
    _validate_audio_layout(metadata, codec="aac", sample_rate=44100, channels=2)
    timeline = _probe_aac_packets(
        source,
        runner,
        require_zero_start=False,
        source_sample_rate=metadata["sample_rate"],
    )
    _validate_source_timeline(timeline, plan)
    return replace(timeline, logical_duration_s=plan.duration_s)


def _probe_raw_aac_timeline(
    raw_aac: Path,
    source_timeline: ReplicaAacTimeline,
    runner: Callable[..., Any],
) -> ReplicaAacTimeline:
    metadata = _probe_audio(raw_aac, runner)
    _validate_audio_layout(
        metadata,
        codec="aac",
        sample_rate=source_timeline.sample_rate,
        channels=2,
    )
    timeline = _probe_aac_packets(
        raw_aac,
        runner,
        require_zero_start=True,
        source_sample_rate=source_timeline.sample_rate,
    )
    if timeline.packet_count != source_timeline.packet_count:
        raise PetReplicaAudioError(
            "Raw AAC packet count does not match the source audio."
        )
    return timeline


def _probe_aac_packets(
    path: Path,
    runner: Callable[..., Any],
    *,
    require_zero_start: bool,
    source_sample_rate: int,
) -> ReplicaAacTimeline:
    completed = _run(
        runner,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_packets",
            "-show_entries",
            "packet=pts_time,duration_time,side_data_list",
            "-of",
            "json",
            str(path),
        ],
        "ffprobe AAC packets",
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise PetReplicaAudioError(
            "ffprobe AAC packets did not return valid JSON."
        ) from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaAudioError("ffprobe AAC packets did not return a JSON object.")
    packets = payload.get("packets")
    if not isinstance(packets, list) or not packets:
        raise PetReplicaAudioError("ffprobe AAC packets must be non-empty.")
    records: list[tuple[float, float]] = []
    skip_samples: int | None = None
    discard_padding: int | None = None
    for index, packet in enumerate(packets):
        if not isinstance(packet, Mapping):
            raise PetReplicaAudioError(
                "ffprobe AAC packets returned an invalid record."
            )
        start_s = _number(packet.get("pts_time"), "AAC packet timestamp")
        packet_duration_s = _positive_number(
            packet.get("duration_time"), "AAC packet duration"
        )
        if records and start_s < records[-1][0]:
            raise PetReplicaAudioError("AAC packet timestamps must be monotonic.")
        if records and not _within_sample(
            start_s,
            records[-1][0] + records[-1][1],
            source_sample_rate,
        ):
            raise PetReplicaAudioError("AAC packet timeline contains a gap or overlap.")
        if index == 0:
            skip_samples, discard_padding = _packet_skip_samples(packet)
        records.append((start_s, packet_duration_s))
    first_pts_s, first_duration_s = records[0]
    last_pts_s, last_duration_s = records[-1]
    if require_zero_start and not _within_sample(first_pts_s, 0.0, source_sample_rate):
        raise PetReplicaAudioError("Raw AAC first packet PTS must be zero.")
    last_end_s = last_pts_s + last_duration_s
    return ReplicaAacTimeline(
        sample_rate=source_sample_rate,
        packet_count=len(records),
        first_packet_pts_s=first_pts_s,
        first_packet_duration_s=first_duration_s,
        last_packet_pts_s=last_pts_s,
        last_packet_duration_s=last_duration_s,
        last_packet_end_s=last_end_s,
        packet_span_s=last_end_s - first_pts_s,
        skip_samples=skip_samples,
        discard_padding=discard_padding,
        logical_duration_s=None,
    )


def _packet_skip_samples(packet: Mapping[str, Any]) -> tuple[int | None, int | None]:
    side_data = packet.get("side_data_list")
    if side_data is None:
        return None, None
    if not isinstance(side_data, list):
        raise PetReplicaAudioError("AAC packet skip-sample evidence is invalid.")
    entries = [
        item
        for item in side_data
        if isinstance(item, Mapping) and item.get("side_data_type") == "Skip Samples"
    ]
    if not entries:
        return None, None
    if len(entries) != 1:
        raise PetReplicaAudioError("AAC packet has ambiguous skip-sample evidence.")
    entry = entries[0]
    return (
        _nonnegative_integer(entry.get("skip_samples"), "skip samples"),
        _nonnegative_integer(entry.get("discard_padding"), "discard padding"),
    )


def _validate_source_timeline(
    timeline: ReplicaAacTimeline, plan: PetReplicaPlan
) -> None:
    if timeline.first_packet_pts_s > 0:
        raise PetReplicaAudioError("Source AAC first packet PTS may not be positive.")
    if timeline.first_packet_pts_s < 0:
        if timeline.skip_samples is None or timeline.skip_samples <= 0:
            raise PetReplicaAudioError(
                "Negative source AAC PTS requires Skip Samples evidence."
            )
        if not _within_sample(
            -timeline.first_packet_pts_s,
            timeline.skip_samples / timeline.sample_rate,
            timeline.sample_rate,
        ):
            raise PetReplicaAudioError(
                "Negative source AAC PTS is not explained by Skip Samples."
            )
    elif timeline.skip_samples not in (None, 0):
        raise PetReplicaAudioError(
            "Zero source AAC PTS may not retain unexplained Skip Samples."
        )
    if not _within_container_timestamp(
        timeline.last_packet_end_s, plan.duration_s, timeline.sample_rate
    ):
        raise PetReplicaAudioError(
            "Source AAC last packet end does not match the replica timeline."
        )


def _verify_normalized_payload(
    *,
    source: Path,
    raw_aac: Path,
    root: Path,
    runner: Callable[..., Any],
) -> ReplicaPayloadEvidence:
    temp_root = _safe_output_path(root, root / "audio" / ".payload-verification")
    _ensure_safe_parent(temp_root / ".keep", root)
    temporary = Path(tempfile.mkdtemp(prefix="payload-", dir=temp_root))
    try:
        source_payload = temporary / "source.payload"
        raw_payload = temporary / "raw.payload"
        _run(
            runner,
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-f",
                "data",
                str(source_payload),
            ],
            "ffmpeg source payload copy",
        )
        _run(
            runner,
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(raw_aac),
                "-map",
                "0:a:0",
                "-bsf:a",
                "aac_adtstoasc",
                "-c:a",
                "copy",
                "-f",
                "data",
                str(raw_payload),
            ],
            "ffmpeg raw AAC payload copy",
        )
        _require_regular_output(source_payload)
        _require_regular_output(raw_payload)
        source_evidence = ReplicaPayloadEvidence(
            sha256=_sha256(source_payload),
            byte_count=source_payload.stat().st_size,
        )
        raw_evidence = ReplicaPayloadEvidence(
            sha256=_sha256(raw_payload),
            byte_count=raw_payload.stat().st_size,
        )
        if source_evidence != raw_evidence:
            raise PetReplicaAudioError(
                "Raw AAC normalized payload does not match the source audio."
            )
        return source_evidence
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _require_timeline_match(
    value: object,
    expected: ReplicaAacTimeline,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        raise PetReplicaAudioError(f"Audio manifest {label} timeline is invalid.")
    try:
        recorded = ReplicaAacTimeline(**value)
    except TypeError as exc:
        raise PetReplicaAudioError(
            f"Audio manifest {label} timeline is invalid."
        ) from exc
    if recorded != expected:
        raise PetReplicaAudioError(
            f"Audio manifest {label} timeline does not match current evidence."
        )


def _require_payload_match(value: object, expected: ReplicaPayloadEvidence) -> None:
    if not isinstance(value, Mapping):
        raise PetReplicaAudioError("Audio manifest payload evidence is invalid.")
    try:
        recorded = ReplicaPayloadEvidence(**value)
    except TypeError as exc:
        raise PetReplicaAudioError(
            "Audio manifest payload evidence is invalid."
        ) from exc
    if recorded != expected:
        raise PetReplicaAudioError(
            "Audio manifest payload evidence does not match current audio."
        )


def _validate_audio_probe(
    probe: Mapping[str, Any],
    *,
    codec: str,
    sample_rate: int,
    channels: int,
    duration_s: float,
) -> None:
    _validate_audio_layout(
        probe,
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
    )
    if abs(probe["duration_s"] - duration_s) > 1 / 30:
        raise PetReplicaAudioError(
            "Audio artifact duration does not match the source timeline."
        )


def _validate_audio_layout(
    probe: Mapping[str, Any],
    *,
    codec: str,
    sample_rate: int,
    channels: int,
) -> None:
    if probe["codec"] != codec:
        raise PetReplicaAudioError(
            "Audio artifact codec does not match the audio contract."
        )
    if probe["sample_rate"] != sample_rate or probe["channels"] != channels:
        raise PetReplicaAudioError(
            "Audio artifact layout does not match the audio contract."
        )


def _manifest_payload(manifest: ReplicaAudioManifest, root: Path) -> dict[str, Any]:
    return {
        "schema_version": _AUDIO_SCHEMA_VERSION,
        "source_sha256": manifest.source_sha256,
        "full_source": _asset_payload(manifest.full_source, root),
        "shots": {
            shot_id: _asset_payload(asset, root)
            for shot_id, asset in manifest.shots.items()
        },
        "source_timeline": asdict(manifest.source_timeline),
        "raw_aac_timeline": asdict(manifest.raw_aac_timeline),
        "normalized_payload": asdict(manifest.normalized_payload),
        "usage_scope": manifest.usage_scope,
        "public_release_ready": manifest.public_release_ready,
        "public_release_blocker": manifest.public_release_blocker,
    }


def _asset_payload(asset: ReplicaAudioAsset, root: Path) -> dict[str, Any]:
    payload = asdict(asset)
    payload["path"] = str(asset.path.relative_to(root))
    return payload


def _read_manifest_payload(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaAudioError("Audio manifest is not valid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise PetReplicaAudioError("Audio manifest must be a JSON object.")
    return payload


def _reject_sensitive_manifest_values(payload: Mapping[str, Any], source: Path) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if str(source) in serialized:
        raise PetReplicaAudioError(
            "Audio manifest may not store the unredacted source path."
        )
    if "crawler" in serialized.lower() or "token" in serialized.lower():
        raise PetReplicaAudioError(
            "Audio manifest may not store crawler data or tokens."
        )


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
        raise PetReplicaAudioError(f"{tool} failed: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
        raise PetReplicaAudioError(f"{tool} failed: {exc}") from exc


def _output_root(plan: PetReplicaPlan) -> Path:
    root = plan.output_root.expanduser().absolute()
    _reject_symlinks(root, "output root")
    return root.resolve(strict=False)


def _safe_output_path(root: Path, path: Path) -> Path:
    candidate = path.expanduser().absolute()
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise PetReplicaAudioError(
            "Audio output must stay inside the output root."
        ) from exc
    _reject_symlinks(candidate, "audio output")
    return candidate


def _ensure_safe_parent(path: Path, root: Path) -> None:
    _safe_output_path(root, path)
    try:
        relative = path.parent.relative_to(root)
    except ValueError as exc:
        raise PetReplicaAudioError(
            "Audio output must stay inside the output root."
        ) from exc
    if not root.exists():
        root.mkdir(parents=True)
    if not root.is_dir() or root.is_symlink():
        raise PetReplicaAudioError("Output root must be a regular directory.")
    current = root
    for component in relative.parts:
        current /= component
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise PetReplicaAudioError(
                    f"Audio output may not use symlinks: {current}"
                )
        else:
            current.mkdir()


def _reject_symlinks(path: Path, label: str) -> None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise PetReplicaAudioError(f"{label} may not use symlinks: {current}")


def _relative_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PetReplicaAudioError(f"Audio manifest {label} is invalid.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PetReplicaAudioError(
            "Audio manifest asset path must stay inside the output root."
        )
    return path


def _require_regular_source(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise PetReplicaAudioError("Reference source video must be a regular file.")


def _require_regular_output(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise PetReplicaAudioError("Audio output must be a non-empty regular file.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PetReplicaAudioError(f"Unable to hash audio artifact: {path}") from exc
    return digest.hexdigest()


def _write_json_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PetReplicaAudioError(f"Audio {label} is invalid.")
    return value


def _positive_integer(value: object, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetReplicaAudioError(f"Audio {label} is invalid.") from exc
    if parsed <= 0:
        raise PetReplicaAudioError(f"Audio {label} is invalid.")
    return parsed


def _nonnegative_integer(value: object, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetReplicaAudioError(f"Audio {label} is invalid.") from exc
    if parsed < 0:
        raise PetReplicaAudioError(f"Audio {label} is invalid.")
    return parsed


def _number(value: object, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PetReplicaAudioError(f"Audio {label} is invalid.") from exc
    if not math.isfinite(parsed):
        raise PetReplicaAudioError(f"Audio {label} is invalid.")
    return parsed


def _positive_number(value: object, label: str) -> float:
    parsed = _number(value, label)
    if parsed <= 0:
        raise PetReplicaAudioError(f"Audio {label} is invalid.")
    return parsed


def _within_frame(left: float, right: float, fps: int) -> bool:
    return abs(left - right) <= 1 / fps


def _same_source_timestamp(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=0.0000005)


def _within_sample(left: float, right: float, sample_rate: int) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1 / sample_rate)


def _within_container_timestamp(left: float, right: float, sample_rate: int) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=max(1 / sample_rate, _CONTAINER_TIMESTAMP_TOLERANCE_S),
    )
