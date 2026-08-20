from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def _count(result: Mapping[str, Any], key: str) -> int:
    value = result.get(key)
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else 0
    )


def _live_blockers(
    profile: Any, *, require_video: bool, require_image: bool
) -> list[str]:
    blockers: list[str] = []
    for label, capability, required in (
        ("video", profile.video, require_video),
        ("image", profile.image, require_image),
    ):
        if not required:
            continue
        if capability.provider != "gateway":
            blockers.append(f"{label}: provider is not configured as gateway")
        elif not capability.ready:
            reasons = capability.blockers or ("provider is not ready",)
            blockers.extend(f"{label}: {reason}" for reason in reasons)
    return blockers


def _write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
