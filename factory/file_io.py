from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _prepare_destination(path: str | Path) -> Path:
    output = Path(path).expanduser()
    if output.is_symlink():
        raise ValueError(f"Refusing to replace symlinked file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink():
        raise ValueError(f"Refusing to write through symlinked directory: {output.parent}")
    return output


def write_text_atomic(path: str | Path, text: str) -> Path:
    output = _prepare_destination(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def write_json_atomic(path: str | Path, payload: Any) -> Path:
    return write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {source}")
    return payload


def sha256_file(path: str | Path) -> str:
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError(f"Refusing to hash symlinked file: {source}")
    if not source.is_file():
        raise FileNotFoundError(source)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
