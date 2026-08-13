from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


REFERENCE_DURATION_S = 77.229569
PILOT_END_S = 12.3
REFERENCE_WIDTH = 720
REFERENCE_HEIGHT = 1280
REFERENCE_FPS = 30
PROJECT_ID = "pet_replica_03_20260730"
_CUT_SCHEMA_VERSION = "motion-comic-factory.pet-replica-cuts.v1"
_CUTS_PATH = Path(__file__).resolve().parent.parent / "config" / "pet_replica_03.cuts.json"
_ORIGINAL_WOMAN_IDENTITY_RULE = (
    "Replace the source woman with an original woman identity that does not "
    "reproduce the source woman's recognizable face."
)
_REQUIRED_CHARACTER_TARGETS = {
    "source_orange_cat": "奶糖",
    "source_tabby_cat": "豆包",
    "source_woman": "原创女主",
}


@dataclass(frozen=True)
class ReplicaCharacter:
    source_role: str
    target_name: str
    reference_path: Path | None
    identity_rule: str


@dataclass(frozen=True)
class ReplicaShot:
    shot_id: str
    index: int
    start_s: float
    end_s: float
    characters: tuple[str, ...]
    speaker: str
    location: str
    framing: str
    action: str
    subtitle: str
    source_audio: bool

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class PetReplicaPlan:
    project_id: str
    source_video: Path
    output_root: Path
    duration_s: float
    pilot_end_s: float
    width: int
    height: int
    fps: int
    characters: Mapping[str, ReplicaCharacter]
    shots: tuple[ReplicaShot, ...]


def build_pet_replica_plan(
    source_video: Path,
    output_root: Path,
) -> PetReplicaPlan:
    boundaries = _load_cut_boundaries()
    root = output_root.expanduser().resolve()
    plan = PetReplicaPlan(
        project_id=PROJECT_ID,
        source_video=source_video.expanduser().resolve(),
        output_root=root,
        duration_s=REFERENCE_DURATION_S,
        pilot_end_s=PILOT_END_S,
        width=REFERENCE_WIDTH,
        height=REFERENCE_HEIGHT,
        fps=REFERENCE_FPS,
        characters=MappingProxyType(_build_characters()),
        shots=tuple(
            ReplicaShot(
                shot_id=f"R{index:03d}",
                index=index,
                start_s=start_s,
                end_s=end_s,
                characters=(),
                speaker="",
                location="",
                framing="",
                action="",
                subtitle="",
                source_audio=True,
            )
            for index, (start_s, end_s) in enumerate(
                zip(boundaries, boundaries[1:]),
                start=1,
            )
        ),
    )
    validate_pet_replica_plan(plan)
    return plan


def validate_pet_replica_plan(plan: PetReplicaPlan) -> None:
    if plan.duration_s != REFERENCE_DURATION_S:
        raise ValueError("Replica plan duration must match the reference duration.")
    if plan.pilot_end_s != PILOT_END_S:
        raise ValueError("Replica plan pilot end must match the reference pilot.")
    if (plan.width, plan.height, plan.fps) != (
        REFERENCE_WIDTH,
        REFERENCE_HEIGHT,
        REFERENCE_FPS,
    ):
        raise ValueError("Replica plan media contract does not match the reference.")

    if not isinstance(plan.characters, MappingProxyType):
        raise ValueError("Replica plan characters must use an immutable mapping.")
    if set(plan.characters) != set(_REQUIRED_CHARACTER_TARGETS):
        raise ValueError("Replica plan characters must match the source roles.")
    for source_role, target_name in _REQUIRED_CHARACTER_TARGETS.items():
        character = plan.characters[source_role]
        if not isinstance(character, ReplicaCharacter):
            raise ValueError("Replica plan characters must be ReplicaCharacter values.")
        if character.source_role != source_role:
            raise ValueError("Replica character source role does not match its mapping key.")
        if character.target_name != target_name:
            raise ValueError("Replica character target name does not match the source role.")
    if (
        plan.characters["source_woman"].identity_rule
        != _ORIGINAL_WOMAN_IDENTITY_RULE
    ):
        raise ValueError("Replica woman identity rule must preserve an original identity.")

    boundaries = _load_cut_boundaries()
    if len(plan.shots) != len(boundaries) - 1:
        raise ValueError("Replica plan shots must match the source cut count.")

    expected_frames = tuple(_frame_index(value, plan.fps) for value in boundaries)
    for index, shot in enumerate(plan.shots, start=1):
        if shot.index != index or shot.shot_id != f"R{index:03d}":
            raise ValueError("Replica plan shot identifiers must match the source order.")
        if index > 1 and _frame_index(plan.shots[index - 2].end_s, plan.fps) != _frame_index(
            shot.start_s, plan.fps
        ):
            raise ValueError("Replica plan shots must be contiguous.")
        if _frame_index(shot.start_s, plan.fps) != expected_frames[index - 1]:
            raise ValueError("Replica plan shot start does not match the source cut.")
        if _frame_index(shot.end_s, plan.fps) != expected_frames[index]:
            raise ValueError("Replica plan shot end does not match the source cut.")
        if _frame_index(shot.end_s, plan.fps) <= _frame_index(shot.start_s, plan.fps):
            raise ValueError("Replica plan shots must have positive frame duration.")


def write_pet_replica_plan(plan: PetReplicaPlan) -> tuple[Path, Path, Path]:
    validate_pet_replica_plan(plan)
    if not plan.source_video.is_file():
        raise ValueError("Replica source video must exist before writing the plan.")

    source_sha256 = _sha256(plan.source_video)
    media_contract = {
        "duration_s": plan.duration_s,
        "width": plan.width,
        "height": plan.height,
        "fps": plan.fps,
    }
    reference_manifest = plan.output_root / "reference" / "reference_manifest.json"
    timeline = plan.output_root / "reference" / "shot_timeline.json"
    contract = plan.output_root / "story_contract.md"

    _write_json_atomically(
        reference_manifest,
        {
            "schema_version": "motion-comic-factory.pet-replica-reference.v1",
            "source_sha256": source_sha256,
            "media_contract": media_contract,
        },
    )
    _write_json_atomically(
        timeline,
        {
            "schema_version": "motion-comic-factory.pet-replica-shot-timeline.v1",
            "source_sha256": source_sha256,
            "media_contract": media_contract,
            "pilot_end_s": plan.pilot_end_s,
            "shots": [_shot_payload(shot) for shot in plan.shots],
        },
    )
    _write_text_atomically(
        contract,
        "\n".join(
            (
                "# Source-Locked Pet Replica Story Contract",
                "",
                f"- Source SHA-256: `{source_sha256}`",
                (
                    "- Media contract: "
                    f"{plan.width}x{plan.height}, {plan.fps} fps, "
                    f"{plan.duration_s:.6f} s"
                ),
                f"- Pilot range: 0.000-{plan.pilot_end_s:.3f} s",
                "- Source audio usage: local evaluation only.",
                "",
                "| Shot | Start (s) | End (s) | Duration (s) |",
                "| --- | ---: | ---: | ---: |",
                *(
                    f"| {shot.shot_id} | {shot.start_s:.6f} | "
                    f"{shot.end_s:.6f} | {shot.duration_s:.6f} |"
                    for shot in plan.shots
                ),
                "",
            )
        ),
    )
    return reference_manifest, timeline, contract


def _build_characters() -> dict[str, ReplicaCharacter]:
    return {
        "source_orange_cat": ReplicaCharacter(
            source_role="source_orange_cat",
            target_name="奶糖",
            reference_path=None,
            identity_rule=(
                "Replace the source orange-white longhair cat with 奶糖 while "
                "preserving source shot timing, action function, and framing."
            ),
        ),
        "source_tabby_cat": ReplicaCharacter(
            source_role="source_tabby_cat",
            target_name="豆包",
            reference_path=None,
            identity_rule=(
                "Replace the source tabby cat with 豆包 while preserving source "
                "shot timing, action function, and framing."
            ),
        ),
        "source_woman": ReplicaCharacter(
            source_role="source_woman",
            target_name="原创女主",
            reference_path=None,
            identity_rule=_ORIGINAL_WOMAN_IDENTITY_RULE,
        ),
    }


def _load_cut_boundaries() -> tuple[float, ...]:
    payload = json.loads(_CUTS_PATH.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != _CUT_SCHEMA_VERSION
        or payload.get("duration_s") != REFERENCE_DURATION_S
        or payload.get("fps") != REFERENCE_FPS
    ):
        raise ValueError("Replica cut configuration does not match the media contract.")
    boundaries = tuple(float(value) for value in payload.get("boundaries_s", ()))
    if len(boundaries) < 2:
        raise ValueError("Replica cut configuration requires at least two boundaries.")
    return boundaries


def _frame_index(timestamp_s: float, fps: int) -> int:
    return round(timestamp_s * fps)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shot_payload(shot: ReplicaShot) -> dict[str, Any]:
    return {
        "shot_id": shot.shot_id,
        "index": shot.index,
        "start_s": shot.start_s,
        "end_s": shot.end_s,
        "duration_s": shot.duration_s,
        "characters": list(shot.characters),
        "speaker": shot.speaker,
        "location": shot.location,
        "framing": shot.framing,
        "action": shot.action,
        "subtitle": shot.subtitle,
        "source_audio": shot.source_audio,
    }


def _write_json_atomically(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    _write_bytes_atomically(path, encoded)


def _write_text_atomically(path: Path, text: str) -> None:
    _write_bytes_atomically(path, text.encode("utf-8"))


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
