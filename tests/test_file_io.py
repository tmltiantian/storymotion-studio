from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from factory.file_io import read_json_object, sha256_file, write_json_atomic, write_text_atomic


def test_write_json_atomic_preserves_unicode_and_replaces_existing_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "report.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"old":true}', encoding="utf-8")

    result = write_json_atomic(output, {"标题": "猫咪", "count": 2})

    assert result == output
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "标题": "猫咪",
        "count": 2,
    }
    assert not list(output.parent.glob(".*.tmp"))


def test_write_text_atomic_creates_parent_and_returns_output(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "story.md"

    result = write_text_atomic(output, "猫咪开口说话\n")

    assert result == output
    assert output.read_text(encoding="utf-8") == "猫咪开口说话\n"


@pytest.mark.parametrize("writer,payload", ((write_json_atomic, {}), (write_text_atomic, "x")))
def test_atomic_writers_reject_symlink_destination(
    tmp_path: Path, writer, payload
) -> None:
    target = tmp_path / "target"
    target.write_text("original", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        writer(link, payload)

    assert target.read_text(encoding="utf-8") == "original"


def test_sha256_file_matches_standard_digest(tmp_path: Path) -> None:
    source = tmp_path / "asset.bin"
    source.write_bytes(b"motion-comic")

    assert sha256_file(source) == hashlib.sha256(b"motion-comic").hexdigest()


def test_sha256_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "asset.bin"
    target.write_bytes(b"asset")
    link = tmp_path / "linked.bin"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        sha256_file(link)


def test_read_json_object_requires_an_object(tmp_path: Path) -> None:
    path = tmp_path / "value.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        read_json_object(path)
