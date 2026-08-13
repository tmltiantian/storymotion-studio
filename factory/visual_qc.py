from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

from .media_validation import probe_media
from .model_bakeoff import SCORE_WEIGHTS, VIDEO_HARD_FAILURES, weighted_score
from .visual_timeline import MicroShot


VISUAL_QC_SCHEMA = "motion-comic-factory.visual-qc.v2"
OCR_CONFIDENCE_MINIMUM = 80.0
BLACK_DURATION_MAXIMUM = 0.08
SCENE_SCORE_MAXIMUM = 10.0

_URI_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*:")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:password|passwd|access[ _-]?key|api[ _-]?key|authorization|"
    r"token|secret|credential)\b\s*[:=]|\bbearer\s+\S+"
)
_BLACK_DURATION_PATTERN = re.compile(r"black_duration:([0-9]+(?:\.[0-9]+)?)")
_SCENE_SCORE_PATTERN = re.compile(r"(?:scd\.score|scene_score)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)")
_TEXT_PATTERN = re.compile(
    r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002fa1f\u3040-\u309f\u30a0-\u30ff\u1100-\u11ff"
    r"\u3130-\u318f\uac00-\ud7af"
    r"\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]"
)
_REVIEW_KEYS = frozenset(
    {*SCORE_WEIGHTS, "hard_failures", "selected_start_seconds", "selected_end_seconds", "notes"}
)
_REPORT_KEYS = frozenset(
    {
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
)
_EVIDENCE_KEYS = frozenset({"path", "size_bytes", "sha256", "device", "inode"})
_MICRO_SHOT_KEYS = frozenset(field.name for field in fields(MicroShot))


class VisualQCError(ValueError):
    pass


@dataclass(frozen=True)
class VisualReview:
    identity: int
    expression: int
    anatomy: int
    continuity: int
    semantics: int
    motion: int
    clean_frame: int
    hard_failures: tuple[str, ...]
    selected_start_seconds: float
    selected_end_seconds: float
    notes: str


def analyze_visual_candidate(
    candidate_path: str | Path,
    micro_shot: MicroShot,
    *,
    output_dir: str | Path,
    reference_image_labels: Sequence[str] = (),
    command_runner: Callable[..., Any] = subprocess.run,
    ocr_runner: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Create an immutable automatic-QC artifact bound to local evidence files."""
    candidate = _canonical_local_file(candidate_path, "candidate video")
    output = _prepare_output_dir(output_dir)
    labels = _reference_labels(reference_image_labels)
    payload, payload_hash = _micro_shot_payload(micro_shot)
    evidence_dir = _new_evidence_dir(output)
    report = _automatic_report(
        candidate,
        output,
        evidence_dir,
        payload,
        payload_hash,
        labels,
        command_runner,
        ocr_runner,
    )
    _validate_automatic_report(report, expected_micro_shot=micro_shot)
    _write_atomic_json(output / "visual_qc.json", report)
    return report


def record_visual_review(
    report_path: str | Path,
    review: VisualReview | Mapping[str, Any],
    *,
    expected_micro_shot: MicroShot,
    expected_reference_image_labels: Sequence[str] = (),
    command_runner: Callable[..., Any] = subprocess.run,
    ocr_runner: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Record review only after validating stored evidence and a fresh QC rerun."""
    destination = _existing_report_path(report_path)
    report = _read_report(destination)
    candidate, output = _validate_automatic_report(
        report,
        expected_micro_shot=expected_micro_shot,
        expected_reference_image_labels=expected_reference_image_labels,
        report_path=destination,
    )
    fresh_duration = _require_current_automatic_result(
        report, candidate, output, expected_micro_shot, command_runner, ocr_runner
    )
    manual_review = _normalise_review(review)
    if not _range_is_valid(manual_review, fresh_duration):
        raise VisualQCError("Manual selected range is invalid.")
    report["manual_review"] = manual_review
    report["passed"] = report["automatic_passed"] and _manual_passes(
        manual_review, fresh_duration
    )
    _validate_automatic_report(
        report,
        expected_micro_shot=expected_micro_shot,
        expected_reference_image_labels=expected_reference_image_labels,
        report_path=destination,
    )
    _write_atomic_json(destination, report)
    return report


def require_passed_visual_qc(
    report: Mapping[str, Any],
    *,
    expected_micro_shot: MicroShot,
    expected_reference_image_labels: Sequence[str] = (),
    command_runner: Callable[..., Any] = subprocess.run,
    ocr_runner: Callable[[Path], str] | None = None,
) -> Mapping[str, Any]:
    """Require intact automatic evidence, a matching MicroShot, and passing gates."""
    candidate, output = _validate_automatic_report(
        report,
        expected_micro_shot=expected_micro_shot,
        expected_reference_image_labels=expected_reference_image_labels,
    )
    fresh_duration = _require_current_automatic_result(
        report, candidate, output, expected_micro_shot, command_runner, ocr_runner
    )
    if report["automatic_passed"] is not True:
        raise VisualQCError("Candidate failed automatic visual QC.")
    review = report["manual_review"]
    if not isinstance(review, Mapping):
        raise VisualQCError("Candidate is missing manual review.")
    if review["hard_failures"]:
        raise VisualQCError("Candidate has manual hard failures.")
    if not _manual_passes(review, fresh_duration):
        raise VisualQCError("Candidate manual review score or range does not pass.")
    if report["passed"] is not True:
        raise VisualQCError("Candidate derived visual QC pass state is invalid.")
    return report


def _automatic_report(
    candidate: Path,
    output_dir: Path,
    evidence_dir: Path,
    micro_payload: dict[str, Any],
    micro_hash: str,
    labels: list[str],
    command_runner: Callable[..., Any],
    ocr_runner: Callable[[Path], str] | None,
) -> dict[str, Any]:
    initial_candidate = _file_evidence(candidate, "candidate video")
    probe = probe_media(candidate, required_stream="video", command_runner=command_runner)
    if not probe.valid:
        raise VisualQCError(f"Invalid candidate video: {probe.error}")
    commands = [_command("ffprobe", _probe_args(candidate))]
    samples: list[dict[str, Any]] = []
    for index, seconds in enumerate(_sample_seconds(probe.duration_seconds), start=1):
        frame = evidence_dir / f"sample_{index:02d}.png"
        command = ["ffmpeg", "-y", "-ss", _seconds(seconds), "-i", str(candidate), "-frames:v", "1", str(frame)]
        _run_ffmpeg(command_runner, command, "sample-frame generation")
        samples.append({"seconds": seconds, "evidence": _file_evidence(frame, "sample evidence")})
        commands.append(_command("ffmpeg", command[1:]))
    contact = evidence_dir / "contact_sheet_3x3.png"
    contact_command = [
        "ffmpeg",
        "-y",
        "-framerate",
        "1",
        "-i",
        str(evidence_dir / "sample_%02d.png"),
        "-vf",
        "tile=3x3:padding=2",
        "-frames:v",
        "1",
        str(contact),
    ]
    _run_ffmpeg(command_runner, contact_command, "contact-sheet generation")
    contact_evidence = {"evidence": _file_evidence(contact, "contact-sheet evidence")}
    commands.append(_command("ffmpeg", contact_command[1:]))
    black_command = ["ffmpeg", "-hide_banner", "-i", str(candidate), "-vf", "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-"]
    black_output = _run_ffmpeg(command_runner, black_command, "blackdetect")
    commands.append(_command("ffmpeg", black_command[1:]))
    cut_command = ["ffmpeg", "-hide_banner", "-i", str(candidate), "-vf", "scdet=threshold=10", "-an", "-f", "null", "-"]
    cut_output = _run_ffmpeg(command_runner, cut_command, "scdet")
    commands.append(_command("ffmpeg", cut_command[1:]))
    ocr_evidence, ocr_commands = _run_ocr(samples, evidence_dir, ocr_runner)
    commands.extend(ocr_commands)
    failures = _automatic_failures(
        _parse_numbers(_BLACK_DURATION_PATTERN, black_output),
        _parse_numbers(_SCENE_SCORE_PATTERN, cut_output),
        ocr_evidence,
    )
    if _file_evidence(candidate, "candidate video") != initial_candidate:
        raise VisualQCError("Candidate video changed during visual QC.")
    return {
        "schema_version": VISUAL_QC_SCHEMA,
        "output_dir": str(output_dir),
        "evidence_dir": str(evidence_dir),
        "micro_shot": micro_payload,
        "micro_shot_sha256": micro_hash,
        "reference_image_labels": labels,
        "candidate_evidence": initial_candidate,
        "probe": {"duration_seconds": probe.duration_seconds, "video_stream_count": probe.video_stream_count, "audio_stream_count": probe.audio_stream_count},
        "sample_frames": samples,
        "contact_sheet": contact_evidence,
        "ocr_evidence": ocr_evidence,
        "black_evidence": {"durations_seconds": _parse_numbers(_BLACK_DURATION_PATTERN, black_output)},
        "cut_evidence": {"scene_scores": _parse_numbers(_SCENE_SCORE_PATTERN, cut_output)},
        "motion_evidence": _motion_evidence(samples),
        "commands": commands,
        "automatic_hard_failures": failures,
        "automatic_passed": not failures,
        "manual_review": None,
        "passed": False,
    }


def _require_current_automatic_result(
    report: Mapping[str, Any],
    candidate: Path,
    output: Path,
    expected_micro_shot: MicroShot,
    command_runner: Callable[..., Any],
    ocr_runner: Callable[[Path], str] | None,
) -> float:
    payload, payload_hash = _micro_shot_payload(expected_micro_shot)
    with tempfile.TemporaryDirectory(dir=output, prefix=".visual_qc_recheck.") as name:
        evidence_dir = Path(name)
        os.chmod(evidence_dir, 0o700)
        fresh = _automatic_report(
            candidate,
            output,
            evidence_dir,
            payload,
            payload_hash,
            list(report["reference_image_labels"]),
            command_runner,
            ocr_runner,
        )
    if fresh["automatic_hard_failures"] != report["automatic_hard_failures"]:
        raise VisualQCError("Candidate automatic visual QC result changed since analysis.")
    if _canonical_probe(fresh["probe"]) != _canonical_probe(report["probe"]):
        raise VisualQCError("Candidate fresh probe does not match persisted probe evidence.")
    return _canonical_probe(fresh["probe"])["duration_seconds"]


def _validate_automatic_report(
    report: Mapping[str, Any],
    *,
    expected_micro_shot: MicroShot,
    expected_reference_image_labels: Sequence[str] | None = None,
    report_path: Path | None = None,
) -> tuple[Path, Path]:
    if not isinstance(report, Mapping) or set(report) != _REPORT_KEYS:
        raise VisualQCError("Visual QC report has an invalid exact-key schema.")
    if report["schema_version"] != VISUAL_QC_SCHEMA:
        raise VisualQCError("Visual QC report has an unsupported schema.")
    output = _canonical_local_directory(report["output_dir"], "output directory")
    evidence_dir = _canonical_local_directory(report["evidence_dir"], "evidence directory")
    if evidence_dir.parent != output or not evidence_dir.name.startswith(".visual_qc."):
        raise VisualQCError("Visual QC evidence directory is invalid.")
    if report_path is not None and report_path.parent != output:
        raise VisualQCError("Visual QC report path does not match its output directory.")
    _validate_untrusted_strings(report["reference_image_labels"], "reference image label")
    if (
        expected_reference_image_labels is not None
        and report["reference_image_labels"]
        != _reference_labels(expected_reference_image_labels)
    ):
        raise VisualQCError(
            "Visual QC report reference image labels do not match the expected labels."
        )
    payload, expected_hash = _micro_shot_payload(expected_micro_shot)
    if report["micro_shot"] != payload or report["micro_shot_sha256"] != expected_hash:
        raise VisualQCError("Visual QC report micro-shot does not match the expected micro-shot.")
    candidate = _verify_file_evidence(report["candidate_evidence"], None, "candidate evidence")
    probe = _canonical_probe(report["probe"])
    duration = probe["duration_seconds"]
    _validate_samples(report["sample_frames"], evidence_dir, duration)
    _verify_contact(report["contact_sheet"], evidence_dir)
    _validate_ocr_evidence(report["ocr_evidence"], evidence_dir)
    _validate_cut_evidence(report["black_evidence"], "durations_seconds")
    _validate_cut_evidence(report["cut_evidence"], "scene_scores")
    _validate_motion_evidence(report["motion_evidence"])
    _validate_commands(report["commands"])
    failures = _automatic_failures(
        report["black_evidence"]["durations_seconds"],
        report["cut_evidence"]["scene_scores"],
        report["ocr_evidence"],
    )
    if report["automatic_hard_failures"] != failures or report["automatic_passed"] is not (not failures):
        raise VisualQCError("Visual QC automatic derived gates are inconsistent.")
    manual = report["manual_review"]
    if manual is None:
        manual_passed = False
    elif isinstance(manual, Mapping):
        manual = _normalise_review(manual, allow_weighted_score=True)
        if dict(report["manual_review"]) != manual:
            raise VisualQCError("Visual QC manual review is non-canonical.")
        manual_passed = _manual_passes(manual, duration)
    else:
        raise VisualQCError("Visual QC manual review is invalid.")
    if report["passed"] is not (bool(not failures and manual_passed)):
        raise VisualQCError("Visual QC derived pass state is inconsistent.")
    return candidate, output


def _validate_samples(samples: Any, root: Path, duration: float) -> None:
    if not isinstance(samples, list) or len(samples) != 9:
        raise VisualQCError("Visual QC report must contain exactly nine samples.")
    expected_seconds = _sample_seconds(duration)
    for item, seconds in zip(samples, expected_seconds):
        if not isinstance(item, Mapping) or set(item) != {"seconds", "evidence"}:
            raise VisualQCError("Visual QC sample evidence is invalid.")
        if not math.isclose(_finite_seconds(item["seconds"], "sample seconds"), seconds, abs_tol=1e-6):
            raise VisualQCError("Visual QC sample timing is invalid.")
        _verify_file_evidence(item["evidence"], root, "sample evidence")


def _verify_contact(contact: Any, root: Path) -> None:
    if not isinstance(contact, Mapping) or set(contact) != {"evidence"}:
        raise VisualQCError("Visual QC contact-sheet evidence is invalid.")
    _verify_file_evidence(contact["evidence"], root, "contact-sheet evidence")


def _validate_ocr_evidence(items: Any, root: Path) -> None:
    if not isinstance(items, list) or len(items) != 18:
        raise VisualQCError("Visual QC OCR evidence is invalid.")
    expected = [(index, region) for index in range(1, 10) for region in ("middle_55", "lower_55")]
    for item, (index, region) in zip(items, expected):
        if not isinstance(item, Mapping) or set(item) != {"sample_index", "region", "evidence", "detected_text"}:
            raise VisualQCError("Visual QC OCR evidence is invalid.")
        if item["sample_index"] != index or item["region"] != region or not isinstance(item["detected_text"], bool):
            raise VisualQCError("Visual QC OCR evidence is invalid.")
        _verify_file_evidence(item["evidence"], root, "OCR crop evidence")


def _validate_cut_evidence(value: Any, key: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {key} or not isinstance(value[key], list):
        raise VisualQCError("Visual QC cut evidence is invalid.")
    if any(_finite_seconds(item, "cut evidence") < 0 for item in value[key]):
        raise VisualQCError("Visual QC cut evidence is invalid.")


def _validate_motion_evidence(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"frame_differences", "mean_absolute_rgb_difference"}:
        raise VisualQCError("Visual QC motion evidence is invalid.")
    differences = value["frame_differences"]
    if not isinstance(differences, list) or len(differences) != 8:
        raise VisualQCError("Visual QC motion evidence is invalid.")
    values = []
    for position, item in enumerate(differences, start=1):
        if not isinstance(item, Mapping) or set(item) != {"from_index", "to_index", "mean_absolute_rgb_difference"}:
            raise VisualQCError("Visual QC motion evidence is invalid.")
        if item["from_index"] != position or item["to_index"] != position + 1:
            raise VisualQCError("Visual QC motion evidence is invalid.")
        values.append(_finite_seconds(item["mean_absolute_rgb_difference"], "motion evidence"))
    expected = round(sum(values) / len(values), 6)
    if _finite_seconds(value["mean_absolute_rgb_difference"], "motion evidence") != expected:
        raise VisualQCError("Visual QC motion evidence is inconsistent.")


def _validate_commands(commands: Any) -> None:
    if not isinstance(commands, list) or not commands:
        raise VisualQCError("Visual QC commands are invalid.")
    for command in commands:
        if not isinstance(command, Mapping) or set(command) != {"tool", "args"}:
            raise VisualQCError("Visual QC commands are invalid.")
        tool, args = command["tool"], command["args"]
        if tool not in {"ffprobe", "ffmpeg", "tesseract"} or not isinstance(args, list):
            raise VisualQCError("Visual QC commands are invalid.")
        for arg in args:
            if not isinstance(arg, str) or not arg:
                raise VisualQCError("Visual QC commands are invalid.")
            if _CREDENTIAL_PATTERN.search(arg):
                raise VisualQCError("Visual QC commands contain a credential.")
            if _URI_PATTERN.search(arg) and not _trusted_command_syntax(tool, arg):
                raise VisualQCError("Visual QC commands contain a URI.")


def _trusted_command_syntax(tool: str, argument: str) -> bool:
    return (tool, argument) in {
        (
            "ffprobe",
            "format=duration:stream=index,codec_type,codec_name,duration",
        ),
        ("ffmpeg", "-frames:v"),
        ("ffmpeg", "blackdetect=d=0.08:pix_th=0.10"),
        ("ffmpeg", "scdet=threshold=10"),
        ("ffmpeg", "tile=3x3:padding=2"),
    }


def _run_ocr(
    samples: Sequence[Mapping[str, Any]], evidence_dir: Path, ocr_runner: Callable[[Path], str] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    for index, sample in enumerate(samples, start=1):
        source = Path(sample["evidence"]["path"])
        for region, crop in _ocr_crops(source, evidence_dir, index):
            if ocr_runner is None:
                args = [str(crop), "stdout", "-l", "chi_sim+eng", "--psm", "11", "tsv"]
                detected = bool(_filtered_tsv_text(_run_tesseract(["tesseract", *args])))
                commands.append(_command("tesseract", args))
            else:
                try:
                    detected = bool(_TEXT_PATTERN.search(str(ocr_runner(crop))))
                except (OSError, subprocess.SubprocessError) as exc:
                    raise VisualQCError(f"OCR runner failed: {exc}") from exc
            evidence.append({"sample_index": index, "region": region, "evidence": _file_evidence(crop, "OCR crop evidence"), "detected_text": detected})
    return evidence, commands


def _ocr_crops(source: Path, evidence_dir: Path, index: int) -> list[tuple[str, Path]]:
    try:
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            if width <= 0 or height <= 0:
                raise VisualQCError("Sample frame has invalid dimensions.")
            crop_height = max(1, round(height * 0.55))
            result = []
            for name, top in (("middle_55", round(height * 0.225)), ("lower_55", round(height * 0.45))):
                crop = evidence_dir / f"ocr_{index:02d}_{name}.png"
                if crop.exists() or crop.is_symlink():
                    raise VisualQCError("OCR crop destination already exists.")
                top = min(max(0, top), height - crop_height)
                rgb.crop((0, top, width, top + crop_height)).save(crop)
                result.append((name, crop))
            return result
    except (OSError, UnidentifiedImageError) as exc:
        raise VisualQCError(f"Unable to crop sample frame: {exc}") from exc


def _motion_evidence(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    differences = []
    for index, (previous, current) in enumerate(zip(samples, samples[1:]), start=1):
        try:
            with Image.open(previous["evidence"]["path"]) as first, Image.open(current["evidence"]["path"]) as second:
                first_rgb, second_rgb = first.convert("RGB"), second.convert("RGB")
                if first_rgb.size != second_rgb.size:
                    raise VisualQCError("Sample frame dimensions differ.")
                mean = ImageStat.Stat(ImageChops.difference(first_rgb, second_rgb)).mean
        except (OSError, UnidentifiedImageError) as exc:
            raise VisualQCError(f"Unable to compare sample frames: {exc}") from exc
        differences.append({"from_index": index, "to_index": index + 1, "mean_absolute_rgb_difference": round(sum(mean) / len(mean), 6)})
    values = [item["mean_absolute_rgb_difference"] for item in differences]
    return {"frame_differences": differences, "mean_absolute_rgb_difference": round(sum(values) / len(values), 6)}


def _automatic_failures(black: Sequence[float], cuts: Sequence[float], ocr: Sequence[Mapping[str, Any]]) -> list[str]:
    failures = set()
    if any(value > BLACK_DURATION_MAXIMUM for value in black) or any(value > SCENE_SCORE_MAXIMUM for value in cuts):
        failures.add("in_model_cut")
    detected_sample_indices = {
        item.get("sample_index")
        for item in ocr
        if item.get("detected_text") is True
    }
    if len(detected_sample_indices) >= 2:
        failures.add("embedded_text")
    return sorted(failures)


def _run_ffmpeg(command_runner: Callable[..., Any], command: list[str], label: str) -> str:
    try:
        completed = command_runner(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VisualQCError(f"FFmpeg {label} failed: {exc}") from exc
    if getattr(completed, "returncode", 0) != 0:
        raise VisualQCError(f"FFmpeg {label} failed.")
    return f"{getattr(completed, 'stdout', '')}\n{getattr(completed, 'stderr', '')}"


def _run_tesseract(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VisualQCError(f"Tesseract OCR failed: {exc}") from exc
    return completed.stdout or ""


def _filtered_tsv_text(value: str) -> str:
    lines = str(value).splitlines()
    if not lines:
        return ""
    header = lines[0].split("\t")
    try:
        confidence, text = header.index("conf"), header.index("text")
    except ValueError:
        return ""
    matches: list[str] = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) <= max(confidence, text):
            continue
        try:
            accepted = float(fields[confidence]) >= OCR_CONFIDENCE_MINIMUM
        except ValueError:
            accepted = False
        token = fields[text].strip()
        normalized = "".join(_TEXT_PATTERN.findall(token))
        if accepted and normalized:
            matches.append(normalized)
    character_count = sum(len(token) for token in matches)
    if character_count < 4:
        return ""
    return " ".join(matches)


def _parse_numbers(pattern: re.Pattern[str], output: str) -> list[float]:
    return [float(match.group(1)) for match in pattern.finditer(output) if math.isfinite(float(match.group(1)))]


def _sample_seconds(duration: float) -> list[float]:
    return [duration * (index + 0.5) / 9 for index in range(9)]


def _seconds(value: float) -> str:
    return f"{value:.6f}"


def _file_evidence(path: Path, label: str) -> dict[str, Any]:
    source = _canonical_local_file(path, label)
    try:
        metadata = source.stat()
    except OSError as exc:
        raise VisualQCError(f"Unable to inspect {label}: {exc}") from exc
    if metadata.st_size <= 0:
        raise VisualQCError(f"{label.capitalize()} is empty.")
    return {"path": str(source), "size_bytes": metadata.st_size, "sha256": _sha256(source, label), "device": metadata.st_dev, "inode": metadata.st_ino}


def _verify_file_evidence(value: Any, root: Path | None, label: str) -> Path:
    if not isinstance(value, Mapping) or set(value) != _EVIDENCE_KEYS or not isinstance(value.get("path"), str):
        raise VisualQCError(f"Visual QC {label} is invalid.")
    path = _canonical_local_file(value["path"], label)
    if root is not None and not path.is_relative_to(root):
        raise VisualQCError(f"Visual QC {label} escapes the evidence directory.")
    if _file_evidence(path, label) != dict(value):
        raise VisualQCError(f"Visual QC {label} changed since analysis.")
    return path


def _sha256(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise VisualQCError(f"Unable to hash {label}: {exc}") from exc
    return digest.hexdigest()


def _micro_shot_payload(shot: MicroShot) -> tuple[dict[str, Any], str]:
    if not isinstance(shot, MicroShot):
        raise VisualQCError("Visual QC expected micro-shot must be a MicroShot.")
    payload = json.loads(json.dumps(asdict(shot), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    _validate_untrusted_strings(payload, "micro-shot")
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return payload, hashlib.sha256(canonical).hexdigest()


def _normalise_review(review: VisualReview | Mapping[str, Any], *, allow_weighted_score: bool = False) -> dict[str, Any]:
    raw = {field: getattr(review, field) for field in _REVIEW_KEYS} if isinstance(review, VisualReview) else dict(review) if isinstance(review, Mapping) else None
    if raw is None or set(raw) != _REVIEW_KEYS | ({"weighted_score"} if allow_weighted_score else set()):
        raise VisualQCError("Manual review must contain exactly the seven scores and review fields.")
    scores = {}
    for key in SCORE_WEIGHTS:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
            raise VisualQCError(f"Manual score {key} must be an integer from 0 to 5.")
        scores[key] = value
    failures = raw["hard_failures"]
    if not isinstance(failures, (tuple, list)) or len(set(failures)) != len(failures) or any(not isinstance(item, str) or item not in VIDEO_HARD_FAILURES for item in failures):
        raise VisualQCError("Manual hard failures must use the video hard-failure set.")
    result = {**scores, "hard_failures": sorted(failures), "selected_start_seconds": _finite_seconds(raw["selected_start_seconds"], "selected start"), "selected_end_seconds": _finite_seconds(raw["selected_end_seconds"], "selected end"), "notes": _safe_untrusted_text(raw["notes"], "manual review notes"), "weighted_score": weighted_score(scores)}
    if allow_weighted_score and raw["weighted_score"] != result["weighted_score"]:
        raise VisualQCError("Manual review weighted score is invalid.")
    return result


def _manual_passes(review: Mapping[str, Any], duration: float) -> bool:
    return not review["hard_failures"] and review["weighted_score"] >= 80 and _range_is_valid(review, duration)


def _range_is_valid(review: Mapping[str, Any], duration: float) -> bool:
    return 0 <= review["selected_start_seconds"] < review["selected_end_seconds"] <= duration


def _report_duration(report: Mapping[str, Any]) -> float:
    return _finite_positive(report["probe"]["duration_seconds"], "probe duration")


def _canonical_probe(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping) or set(value) != {
        "duration_seconds",
        "video_stream_count",
        "audio_stream_count",
    }:
        raise VisualQCError("Visual QC probe evidence is invalid.")
    duration = _finite_positive(value["duration_seconds"], "probe duration")
    video_count = value["video_stream_count"]
    audio_count = value["audio_stream_count"]
    if (
        not _nonnegative_int(video_count)
        or not _nonnegative_int(audio_count)
        or video_count < 1
    ):
        raise VisualQCError("Visual QC probe stream counts are invalid.")
    return {
        "duration_seconds": duration,
        "video_stream_count": video_count,
        "audio_stream_count": audio_count,
    }


def _finite_positive(value: Any, label: str) -> float:
    result = _finite_seconds(value, label)
    if result <= 0:
        raise VisualQCError(f"{label.capitalize()} must be positive.")
    return result


def _finite_seconds(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise VisualQCError(f"{label.capitalize()} must be finite.")
    return float(value)


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _prepare_output_dir(value: str | Path) -> Path:
    directory = _absolute_path(value, "output directory")
    _reject_symlink_components(directory, "output directory")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VisualQCError(f"Unable to create visual QC output directory: {exc}") from exc
    return _canonical_local_directory(directory, "output directory")


def _new_evidence_dir(output: Path) -> Path:
    _reject_symlink_components(output, "output directory")
    try:
        directory = Path(tempfile.mkdtemp(dir=output, prefix=".visual_qc."))
        os.chmod(directory, 0o700)
    except OSError as exc:
        raise VisualQCError(f"Unable to create visual QC evidence directory: {exc}") from exc
    return _canonical_local_directory(directory, "evidence directory")


def _existing_report_path(value: str | Path) -> Path:
    path = _canonical_local_file(value, "report path")
    if path.name != "visual_qc.json":
        raise VisualQCError("Visual QC report path must be visual_qc.json.")
    return path


def _canonical_local_directory(value: str | Path, label: str) -> Path:
    path = _absolute_path(value, label)
    _reject_symlink_components(path, label)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise VisualQCError(f"{label.capitalize()} is missing or inaccessible: {exc}") from exc
    if canonical != path or not canonical.is_dir():
        raise VisualQCError(f"{label.capitalize()} must be a canonical local directory.")
    return canonical


def _canonical_local_file(value: str | Path, label: str) -> Path:
    path = _absolute_path(value, label)
    _reject_symlink_components(path, label)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise VisualQCError(f"{label.capitalize()} is missing or inaccessible: {exc}") from exc
    if canonical != path or not canonical.is_file():
        raise VisualQCError(f"{label.capitalize()} must be a canonical local file.")
    return canonical


def _absolute_path(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise VisualQCError(f"{label.capitalize()} must be a local path.")
    raw = str(value)
    if _URI_PATTERN.search(raw) or _CREDENTIAL_PATTERN.search(raw):
        raise VisualQCError(f"{label.capitalize()} must not contain a URI or credential.")
    path = Path(raw)
    return path if path.is_absolute() else (Path.cwd() / path).absolute()


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in path.parts:
        if part == path.anchor:
            continue
        current /= part
        try:
            if current.is_symlink():
                raise VisualQCError(f"{label.capitalize()} must not use a symlink: {current}")
        except OSError as exc:
            raise VisualQCError(f"Unable to inspect {label}: {exc}") from exc


def _read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualQCError(f"Unable to read visual QC report: {exc}") from exc
    if not isinstance(payload, dict):
        raise VisualQCError("Visual QC report must be an object.")
    return payload


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _reject_symlink_components(path.parent, "report directory")
    if path.exists() and path.is_symlink():
        raise VisualQCError("Visual QC report path must not be a symlink.")
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise VisualQCError(f"Unable to write visual QC report atomically: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _probe_args(candidate: Path) -> list[str]:
    return ["-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name,duration", "-of", "json", str(candidate)]


def _command(tool: str, args: Sequence[str]) -> dict[str, Any]:
    command = {"tool": tool, "args": list(args)}
    _validate_commands([command])
    return command


def _reference_labels(value: Sequence[str]) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise VisualQCError("Reference image labels must be a sequence of local text.")
    return [_safe_untrusted_text(item, "reference image label") for item in value]


def _safe_untrusted_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or _URI_PATTERN.search(value) or _CREDENTIAL_PATTERN.search(value):
        raise VisualQCError(f"{label.capitalize()} must not contain a URI or credential.")
    return value


def _validate_untrusted_strings(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_untrusted_strings(item, label)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_untrusted_strings(item, label)
    elif isinstance(value, str):
        _safe_untrusted_text(value, label)
