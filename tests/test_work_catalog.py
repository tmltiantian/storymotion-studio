from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from factory.work_catalog import build_work_catalog
from scripts.migrate_showcase_works import migrate_showcase_media


STAGES = (
    "concept",
    "script",
    "storyboard",
    "assets",
    "audio",
    "video",
    "edit",
    "eval",
    "deliver",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_delivered_project(
    runs: Path,
    *,
    project_id: str = "old-city",
    title: str = "旧城来信",
    revision: int = 3,
) -> Path:
    project = runs / project_id
    deliver = project / "stages" / "deliver"
    evaluation = project / "stages" / "eval"
    deliver.mkdir(parents=True)
    evaluation.mkdir(parents=True)
    master = deliver / "master.mp4"
    master.write_bytes(b"delivered-master")
    eval_report = evaluation / "eval_result.json"
    eval_report.write_text(
        json.dumps({"automatic_passed": True, "score": 94}), encoding="utf-8"
    )
    delivery_manifest = deliver / "delivery_manifest.json"
    delivery_manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.delivery.v1",
                "project_id": project_id,
                "master": str(master),
                "sha256": _sha256(master.read_bytes()),
                "publication_status": "APPROVED",
                "delivered_at": "2026-08-15T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    project_json = {
        "schema_version": "motion-comic-factory.project-spec.v1",
        "project_id": project_id,
        "title": title,
        "mode": "novel",
        "input": {"kind": "novel"},
        "output_dir": str(project / "output"),
        "target": {},
        "characters": [],
        "providers": {},
        "policies": {},
        "mode_options": {},
    }
    (project / "project.json").write_text(json.dumps(project_json), encoding="utf-8")
    stage_records = []
    for stage in STAGES:
        artifacts: list[str] = []
        if stage == "eval":
            artifacts = [str(eval_report)]
        if stage == "deliver":
            artifacts = [str(delivery_manifest), str(master)]
        stage_records.append(
            {
                "stage": stage,
                "state": "passed",
                "executor": f"generic.{stage}",
                "artifacts": artifacts,
                "revision": revision if stage == "deliver" else 1,
                "review_policy": "manual" if stage == "deliver" else "automatic",
                "review_state": "approved" if stage == "deliver" else "auto_approved",
                "review_blocks_progress": False,
            }
        )
    package = {
        "schema_version": "motion-comic-factory.production-package.v1",
        "project_id": project_id,
        "mode": "novel",
        "spec_path": str(project / "project.json"),
        "spec_sha256": "0" * 64,
        "stages": stage_records,
        "next_stage": "complete",
        "final_outputs": [str(master)],
        "eval_reports": [str(eval_report)],
    }
    (project / "production_package.json").write_text(
        json.dumps(package), encoding="utf-8"
    )
    return project


def _write_showcase(source: Path) -> dict[str, bytes]:
    files = {
        "audio/black-cat-approved.m4a": b"black-cat",
        "audio/orange-cat-approved.m4a": b"orange-cat",
        "audio/two-cat-approved-dialogue.m4a": b"dialogue",
        "favicon.svg": b"<svg>favicon</svg>",
        "file.svg": b"<svg>file</svg>",
        "globe.svg": b"<svg>globe</svg>",
        "window.svg": b"<svg>window</svg>",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return files


def test_catalog_reads_only_authoritative_deliveries_and_archived_media(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    _write_delivered_project(runs)
    arbitrary = runs / "looks-finished"
    arbitrary.mkdir(parents=True)
    (arbitrary / "final.mp4").write_bytes(b"must-not-leak")
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    archive_manifest = migrate_showcase_media(source, archive)

    catalog = build_work_catalog(runs, archive_manifest)

    assert [work.title for work in catalog.works] == [
        "旧城来信",
        "双猫定版音色",
        "历史归档",
    ]
    delivered = catalog.works[0]
    assert delivered.source == "delivered"
    assert delivered.versions[0].label == "V3"
    assert delivered.versions[0].outputs[0].sha256 == _sha256(b"delivered-master")
    assert b"must-not-leak" not in [
        artifact.path.read_bytes()
        for work in catalog.works
        for version in work.versions
        for artifact in version.outputs
    ]


def test_catalog_order_and_opaque_ids_are_deterministic(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_delivered_project(runs, project_id="zeta", title="晚到", revision=2)
    _write_delivered_project(runs, project_id="alpha", title="先到", revision=1)

    first = build_work_catalog(runs, None)
    second = build_work_catalog(runs, None)

    assert [item.work_id for item in first.works] == [
        item.work_id for item in second.works
    ]
    assert [item.title for item in first.works] == ["晚到", "先到"]
    assert all(item.work_id.startswith("work_") for item in first.works)
    assert all("/" not in item.work_id for item in first.works)


@pytest.mark.parametrize(
    "mutation",
    ("malformed", "missing-media", "escaped-media"),
)
def test_catalog_rejects_invalid_authoritative_records(
    tmp_path: Path, mutation: str
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs)
    package_path = project / "production_package.json"
    if mutation == "malformed":
        package_path.write_text("[]", encoding="utf-8")
    elif mutation == "missing-media":
        (project / "stages/deliver/master.mp4").unlink()
    else:
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"outside")
        manifest_path = project / "stages/deliver/delivery_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["master"] = str(outside)
        manifest["sha256"] = _sha256(outside.read_bytes())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    catalog = build_work_catalog(runs, None)

    assert catalog.works == ()
    assert catalog.warnings
    serialized = json.dumps(catalog.to_public(), ensure_ascii=False)
    assert str(tmp_path) not in serialized


def test_catalog_rejects_duplicate_versions_inside_one_archive_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    payload = manifest.to_dict()
    payload["entries"][1]["version_label"] = payload["entries"][0]["version_label"]
    (archive / "archive_manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    catalog = build_work_catalog(tmp_path / "runs", archive / "archive_manifest.json")

    assert all(work.title != "双猫定版音色" for work in catalog.works)
    assert "duplicate_archive_version" in catalog.warnings


def test_migration_dry_run_accounts_for_every_file_and_writes_nothing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    expected = _write_showcase(source)
    archive = tmp_path / "archive"

    manifest = migrate_showcase_media(source, archive, dry_run=True)

    assert len(manifest.entries) == len(expected)
    assert {entry.source_relative for entry in manifest.entries} == set(expected)
    assert sum(entry.classification == "linked" for entry in manifest.entries) == 3
    assert (
        sum(entry.classification == "unclassified" for entry in manifest.entries) == 4
    )
    assert not archive.exists()


def test_migration_cli_runs_directly_and_reports_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "public"
    expected = _write_showcase(source)
    archive = tmp_path / "archive"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/migrate_showcase_works.py",
            "--source",
            str(source),
            "--destination",
            str(archive),
            "--dry-run",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["source_file_count"] == len(expected)
    assert not archive.exists()


def test_real_migration_is_idempotent_and_preserves_sources_and_unclassified(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    expected = _write_showcase(source)
    original_stats = {
        relative: (
            (source / relative).read_bytes(),
            (source / relative).stat().st_mtime_ns,
        )
        for relative in expected
    }
    archive = tmp_path / "archive"

    first = migrate_showcase_media(source, archive)
    second = migrate_showcase_media(source, archive)

    assert first == second
    assert (archive / "archive_manifest.json").is_file()
    assert all(entry.sha256 and len(entry.sha256) == 64 for entry in first.entries)
    assert all((archive / entry.archive_relative).is_file() for entry in first.entries)
    assert any(
        entry.classification == "unclassified"
        and entry.source_relative == "favicon.svg"
        for entry in first.entries
    )
    assert {
        relative: (
            (source / relative).read_bytes(),
            (source / relative).stat().st_mtime_ns,
        )
        for relative in expected
    } == original_stats


def test_migration_rejects_symlinks_and_destination_collisions(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (source / "linked.bin").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        migrate_showcase_media(source, tmp_path / "archive")

    (source / "linked.bin").unlink()
    dry = migrate_showcase_media(source, tmp_path / "archive", dry_run=True)
    collision = tmp_path / "archive" / dry.entries[0].archive_relative
    collision.parent.mkdir(parents=True)
    collision.write_bytes(b"different")
    with pytest.raises(ValueError, match="collision"):
        migrate_showcase_media(source, tmp_path / "archive")


def test_migration_rejects_source_mutation_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    original = module._copy_file_atomic
    mutated = False

    def mutate_after_copy(*args, **kwargs):
        nonlocal mutated
        result = original(*args, **kwargs)
        if not mutated:
            mutated = True
            (source / "audio/black-cat-approved.m4a").write_bytes(b"changed")
        return result

    monkeypatch.setattr(module, "_copy_file_atomic", mutate_after_copy)

    with pytest.raises(ValueError, match="changed during migration"):
        migrate_showcase_media(source, archive)

    assert not (archive / "archive_manifest.json").exists()
    assert not list(archive.rglob("*.tmp")) if archive.exists() else True


def test_interrupted_copy_leaves_no_manifest_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    real_replace = module.os.replace
    interrupted = False

    def fail_first_media_replace(source_name, destination_name, **kwargs):
        nonlocal interrupted
        if destination_name != "archive_manifest.json" and not interrupted:
            interrupted = True
            raise OSError("simulated interruption")
        return real_replace(source_name, destination_name, **kwargs)

    monkeypatch.setattr(module.os, "replace", fail_first_media_replace)

    with pytest.raises(OSError, match="simulated interruption"):
        migrate_showcase_media(source, archive)

    assert not (archive / "archive_manifest.json").exists()
    assert not list(archive.rglob("*.tmp"))


def test_migration_rejects_unsafe_roots(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        migrate_showcase_media(source_alias, tmp_path / "archive")

    with pytest.raises(ValueError):
        migrate_showcase_media(source, source / "nested-archive")
