from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


EDIT_DECISIONS_SCHEMA = "motion-comic-factory.edit-decisions.v1"


class EditDecisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShotEditDecision:
    source_end_seconds: float | None = None
    drop_ranges_seconds: tuple[tuple[float, float], ...] = ()
    note: str = ""

    @property
    def applied(self) -> bool:
        return self.source_end_seconds is not None or bool(self.drop_ranges_seconds)


@dataclass(frozen=True)
class EditDecisions:
    project_id: str
    source_path: Path | None = None
    suppressed_subtitle_cues: tuple[int, ...] = ()
    shots: dict[str, ShotEditDecision] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return bool(self.suppressed_subtitle_cues) or any(
            decision.applied for decision in self.shots.values()
        )


@dataclass(frozen=True)
class RenderSubtitlePlan:
    path: Path
    source_path: Path
    suppressed_cues: tuple[int, ...] = ()

    def to_report(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "render_path": str(self.path),
            "suppressed_cues": list(self.suppressed_cues),
            "edited": bool(self.suppressed_cues),
        }


def empty_edit_decisions(project_id: str) -> EditDecisions:
    return EditDecisions(project_id=project_id)


def load_adjacent_edit_decisions(
    package_path: str | Path,
    *,
    expected_project_id: str,
    valid_shot_ids: Iterable[str],
) -> EditDecisions:
    path = Path(package_path).with_name("edit_decisions.json")
    if not path.is_file():
        return empty_edit_decisions(expected_project_id)
    return load_edit_decisions(
        path,
        expected_project_id=expected_project_id,
        valid_shot_ids=valid_shot_ids,
    )


def load_edit_decisions(
    path: str | Path,
    *,
    expected_project_id: str,
    valid_shot_ids: Iterable[str],
) -> EditDecisions:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise EditDecisionError(f"Edit decisions not found: {source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise EditDecisionError(f"Unable to read edit decisions: {source}") from exc
    if not isinstance(payload, dict):
        raise EditDecisionError("Edit decisions must contain a JSON object.")
    if payload.get("schema_version") != EDIT_DECISIONS_SCHEMA:
        raise EditDecisionError(
            f"Unsupported edit decisions schema: {payload.get('schema_version')}"
        )

    project_id = str(payload.get("project_id") or "").strip()
    if project_id != expected_project_id:
        raise EditDecisionError(
            "Edit decisions project ID does not match the active project: "
            f"{project_id or '[missing]'} != {expected_project_id}"
        )

    subtitle = payload.get("subtitle") or {}
    if not isinstance(subtitle, dict):
        raise EditDecisionError("Edit decisions subtitle section must be an object.")
    suppressed = _positive_ints(
        subtitle.get("suppress_cues") or (),
        label="subtitle suppress_cues",
    )

    raw_shots = payload.get("shots") or {}
    if not isinstance(raw_shots, dict):
        raise EditDecisionError("Edit decisions shots section must be an object.")
    allowed_shots = set(valid_shot_ids)
    unknown_shots = sorted(set(raw_shots) - allowed_shots)
    if unknown_shots:
        raise EditDecisionError(
            "Edit decisions contain unknown shot IDs: " + ", ".join(unknown_shots)
        )

    shots: dict[str, ShotEditDecision] = {}
    for shot_id, value in raw_shots.items():
        if not isinstance(value, dict):
            raise EditDecisionError(
                f"Edit decision for {shot_id} must contain an object."
            )
        source_end = _optional_positive_float(
            value.get("source_end_seconds"),
            label=f"{shot_id} source_end_seconds",
        )
        ranges = _drop_ranges(
            value.get("drop_ranges_seconds") or (),
            shot_id=shot_id,
            source_end_seconds=source_end,
        )
        shots[shot_id] = ShotEditDecision(
            source_end_seconds=source_end,
            drop_ranges_seconds=ranges,
            note=str(value.get("note") or "").strip(),
        )

    return EditDecisions(
        project_id=project_id,
        source_path=source,
        suppressed_subtitle_cues=suppressed,
        shots=shots,
    )


def prepare_render_subtitles(
    subtitles_path: str | Path,
    decisions: EditDecisions,
    *,
    output_path: str | Path | None = None,
) -> RenderSubtitlePlan:
    source = Path(subtitles_path)
    if not decisions.suppressed_subtitle_cues:
        return RenderSubtitlePlan(path=source, source_path=source)
    try:
        content = source.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise EditDecisionError(f"Subtitles not found: {source}") from exc
    except OSError as exc:
        raise EditDecisionError(f"Unable to read subtitles: {source}") from exc

    suppressed = set(decisions.suppressed_subtitle_cues)
    kept_blocks: list[list[str]] = []
    found_cues: set[int] = set()
    for raw_block in re.split(r"\n\s*\n", content.strip()):
        lines = [line.rstrip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        try:
            cue_id = int(lines[0].strip())
        except ValueError as exc:
            raise EditDecisionError(
                f"Subtitle cue ID is not an integer: {lines[0]}"
            ) from exc
        if cue_id in suppressed:
            found_cues.add(cue_id)
            continue
        kept_blocks.append(lines)
    missing = sorted(suppressed - found_cues)
    if missing:
        raise EditDecisionError(
            "Suppressed subtitle cue IDs were not found: "
            + ", ".join(str(value) for value in missing)
        )

    rendered_blocks: list[str] = []
    for cue_index, lines in enumerate(kept_blocks, start=1):
        rendered_blocks.append("\n".join([str(cue_index), *lines[1:]]))
    rendered = "\n\n".join(rendered_blocks)
    if rendered:
        rendered += "\n"
    destination = (
        Path(output_path)
        if output_path is not None
        else source.with_name(f"{source.stem}.render{source.suffix}")
    )
    _write_text_atomic(destination, rendered)
    return RenderSubtitlePlan(
        path=destination,
        source_path=source,
        suppressed_cues=decisions.suppressed_subtitle_cues,
    )


def _positive_ints(value: Any, *, label: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise EditDecisionError(f"Edit decisions {label} must be a list.")
    result: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise EditDecisionError(
                f"Edit decisions {label} values must be positive integers."
            )
        result.add(item)
    return tuple(sorted(result))


def _optional_positive_float(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise EditDecisionError(f"Edit decisions {label} must be a positive number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise EditDecisionError(
            f"Edit decisions {label} must be a positive number."
        ) from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise EditDecisionError(f"Edit decisions {label} must be a positive number.")
    return normalized


def _drop_ranges(
    value: Any,
    *,
    shot_id: str,
    source_end_seconds: float | None,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        raise EditDecisionError(
            f"Edit decisions {shot_id} drop_ranges_seconds must be a list."
        )
    ranges: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise EditDecisionError(
                f"Edit decisions {shot_id} drop range must contain start and end."
            )
        start = _nonnegative_float(item[0], label=f"{shot_id} drop range start")
        end = _nonnegative_float(item[1], label=f"{shot_id} drop range end")
        if end <= start:
            raise EditDecisionError(
                f"Edit decisions {shot_id} drop range end must be after start."
            )
        if source_end_seconds is not None and end > source_end_seconds:
            raise EditDecisionError(
                f"Edit decisions {shot_id} drop range exceeds source_end_seconds."
            )
        ranges.append((start, end))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            raise EditDecisionError(
                f"Edit decisions {shot_id} drop ranges overlap."
            )
    return tuple(ranges)


def _nonnegative_float(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise EditDecisionError(
            f"Edit decisions {label} must be a non-negative number."
        )
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise EditDecisionError(
            f"Edit decisions {label} must be a non-negative number."
        ) from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise EditDecisionError(
            f"Edit decisions {label} must be a non-negative number."
        )
    return normalized


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
