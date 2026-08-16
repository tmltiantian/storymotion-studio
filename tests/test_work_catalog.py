from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from factory.pipeline_contracts import ProjectSpec, StageName
from factory.pipeline_review import (
    approve_stage_revision,
    canonical_json_digest,
    delivery_eval_evidence,
    write_stage_revision,
)
from factory.work_catalog import WorkCatalog, WorkCatalogCache, build_work_catalog
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
    mode: str = "novel",
    deliver_policy: str = "manual",
    eval_policy: str = "manual",
    characters: list[dict[str, str]] | None = None,
) -> Path:
    project = runs / project_id
    deliver = project / "stages" / "deliver"
    evaluation = project / "stages" / "eval"
    deliver.mkdir(parents=True)
    evaluation.mkdir(parents=True)
    if mode == "replica":
        master = deliver / "release" / "final.mp4"
        master.parent.mkdir()
        master.write_bytes(b"replica-master")
    else:
        master = deliver / "master.mp4"
        master.write_bytes(b"delivered-master")
    eval_report = evaluation / "eval_result.json"
    eval_report.write_text(
        json.dumps({"automatic_passed": True, "score": 94}), encoding="utf-8"
    )
    delivery_manifest = deliver / "delivery_manifest.json"
    project_json = {
        "schema_version": "motion-comic-factory.project-spec.v1",
        "project_id": project_id,
        "title": title,
        "mode": mode,
        "input": {"kind": "reference" if mode == "replica" else "novel"},
        "output_dir": str(project / "output"),
        "target": {},
        "characters": characters or [],
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
                "executor": f"{'replica' if mode == 'replica' else 'generic'}.{stage}",
                "input_signature": (
                    "deliver-input"
                    if stage == "deliver"
                    else "eval-input"
                    if stage == "eval"
                    else ""
                ),
                "artifacts": artifacts,
                "revision": revision if stage == "deliver" else 1,
                "review_policy": (
                    deliver_policy
                    if stage == "deliver"
                    else eval_policy
                    if stage == "eval"
                    else "automatic"
                ),
                "review_state": (
                    ("auto_approved" if deliver_policy == "automatic" else "approved")
                    if stage == "deliver"
                    else (
                        "auto_approved" if eval_policy == "automatic" else "approved"
                    )
                    if stage == "eval"
                    else "auto_approved"
                ),
                "review_blocks_progress": False,
            }
        )
    package = {
        "schema_version": "motion-comic-factory.production-package.v1",
        "project_id": project_id,
        "mode": mode,
        "spec_path": str(project / "project.json"),
        "spec_sha256": ProjectSpec.from_dict(project_json).sha256,
        "stages": stage_records,
        "next_stage": "complete",
        "final_outputs": [str(master)],
        "eval_reports": [str(eval_report)],
    }
    (project / "production_package.json").write_text(
        json.dumps(package), encoding="utf-8"
    )
    write_stage_revision(
        project,
        StageName.EVAL,
        (eval_report,),
        "eval-input",
        f"{'replica' if mode == 'replica' else 'generic'}.eval",
    )
    if eval_policy != "automatic":
        approve_stage_revision(
            project,
            StageName.EVAL,
            1,
            "Approved EVAL evidence",
            (eval_report,),
        )
    eval_evidence = delivery_eval_evidence(project)
    manifest_payload = (
        {
            "schema_version": "motion-comic-factory.replica-delivery.v1",
            "workspace": str(project / "replica-workspace"),
            "operation": {"operation": "compose", "status": "completed"},
            "masters": [str(master)],
            "publication_status": "REVIEW_REQUIRED",
            "delivered_at": "2026-08-15T12:00:00Z",
            "eval_evidence": eval_evidence,
        }
        if mode == "replica"
        else {
            "schema_version": "motion-comic-factory.delivery.v1",
            "project_id": project_id,
            "master": str(master),
            "sha256": _sha256(master.read_bytes()),
            "publication_status": "REVIEW_REQUIRED",
            "delivered_at": "2026-08-15T12:00:00Z",
            "eval_evidence": eval_evidence,
        }
    )
    delivery_manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    for _ in range(revision):
        write_stage_revision(
            project,
            StageName.DELIVER,
            (delivery_manifest, master),
            "deliver-input",
            f"{'replica' if mode == 'replica' else 'generic'}.deliver",
        )
    if deliver_policy != "automatic":
        approve_stage_revision(
            project,
            StageName.DELIVER,
            revision,
            "Approved for catalog delivery",
            (master,),
        )
    return project


def _publish_updated_delivery_manifest(project: Path, note: str) -> None:
    project_payload = json.loads((project / "project.json").read_text(encoding="utf-8"))
    mode = project_payload["mode"]
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    master = (
        project / "stages/deliver/release/final.mp4"
        if mode == "replica"
        else project / "stages/deliver/master.mp4"
    )
    revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        f"{'replica' if mode == 'replica' else 'generic'}.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        revision.number,
        note,
        (master,),
    )


def _refresh_embedded_eval_digests(evidence: dict[str, object]) -> None:
    review = evidence["review"]
    assert isinstance(review, dict)
    snapshot = review["snapshot"]
    assert isinstance(snapshot, dict)
    review["sha256"] = canonical_json_digest(snapshot)
    unsigned = {key: value for key, value in evidence.items() if key != "snapshot_sha256"}
    evidence["snapshot_sha256"] = canonical_json_digest(unsigned)


def _migrate_in_process(source: str, archive: str, queue) -> None:
    try:
        manifest = migrate_showcase_media(source, archive)
        queue.put(("ok", len(manifest.entries)))
    except Exception as exc:  # pragma: no cover - child process evidence
        queue.put(("error", type(exc).__name__, str(exc)))


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
    _write_delivered_project(
        runs,
        characters=[{"character_id": "cat_01", "name": "豆包"}],
    )
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
    assert delivered.roles == ("豆包",)
    assert delivered.delivery_date == "2026-08-15"
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


def test_catalog_rejects_stale_spec_and_forged_or_absent_delivery_review(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, project_id="stale-spec")
    spec_path = project / "project.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["title"] = "被后改的标题"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    assert build_work_catalog(runs, None).works == ()

    project = _write_delivered_project(runs, project_id="absent-review")
    (project / "reviews/deliver.review.json").unlink()
    assert all(
        work.project_id != "absent-review"
        for work in build_work_catalog(runs, None).works
    )

    project = _write_delivered_project(runs, project_id="forged-review")
    review_path = project / "reviews/deliver.review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["evidence"][0]["sha256"] = "f" * 64
    review_path.write_text(json.dumps(review), encoding="utf-8")
    assert all(
        work.project_id != "forged-review"
        for work in build_work_catalog(runs, None).works
    )


@pytest.mark.parametrize("mutation", ("stale-revision", "stale-artifact", "skipped"))
def test_catalog_binds_latest_delivery_revision_and_never_accepts_skipped(
    tmp_path: Path, mutation: str
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=2)
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    deliver = package["stages"][-1]
    if mutation == "stale-revision":
        deliver["revision"] = 1
        package_path.write_text(json.dumps(package), encoding="utf-8")
    elif mutation == "stale-artifact":
        (project / "stages/deliver/master.mp4").write_bytes(b"mutated")
    else:
        deliver["review_state"] = "skipped"
        package_path.write_text(json.dumps(package), encoding="utf-8")

    assert build_work_catalog(runs, None).works == ()


def test_catalog_rejects_automatic_delivery_without_human_approval(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    _write_delivered_project(runs, deliver_policy="automatic", revision=1)

    catalog = build_work_catalog(runs, None)

    assert catalog.works == ()


def test_catalog_accepts_delivery_bound_automatic_eval_without_review_file(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, eval_policy="automatic", revision=1)

    catalog = build_work_catalog(runs, None)

    assert not (project / "reviews/eval.review.json").exists()
    assert [item.name for item in catalog.works[0].versions[0].eval_reports] == [
        "eval_result.json"
    ]


@pytest.mark.parametrize(
    ("mode", "eval_policy"),
    (("novel", "manual"), ("replica", "grouped")),
)
def test_catalog_accepts_exact_durable_human_eval_snapshot(
    tmp_path: Path,
    mode: str,
    eval_policy: str,
) -> None:
    runs = tmp_path / "runs"
    _write_delivered_project(
        runs,
        mode=mode,
        eval_policy=eval_policy,
        revision=1,
    )

    catalog = build_work_catalog(runs, None)

    assert [item.name for item in catalog.works[0].versions[0].eval_reports] == [
        "eval_result.json"
    ]


@pytest.mark.parametrize(
    ("mode", "eval_policy"),
    (("novel", "manual"), ("replica", "grouped")),
)
@pytest.mark.parametrize(
    "mutation",
    ("note", "evidence", "transaction_id", "created_at"),
)
def test_catalog_rejects_self_consistent_forged_human_eval_snapshot(
    tmp_path: Path,
    mode: str,
    eval_policy: str,
    mutation: str,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(
        runs,
        mode=mode,
        eval_policy=eval_policy,
        revision=1,
    )
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = manifest["eval_evidence"]
    snapshot = evidence["review"]["snapshot"]
    if mutation == "note":
        snapshot["note"] = "FORGED BUT SELF-CONSISTENT"
    elif mutation == "evidence":
        snapshot["evidence"][0]["media_type"] = "text/plain"
    elif mutation == "transaction_id":
        snapshot["transaction_id"] = "forged-transaction"
    else:
        snapshot["created_at"] = "2026-08-16T00:00:00+00:00"
    _refresh_embedded_eval_digests(evidence)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _publish_updated_delivery_manifest(project, "Approved forged EVAL fixture")

    catalog = build_work_catalog(runs, None)

    assert len(catalog.works) == 1
    assert catalog.works[0].versions[0].eval_reports == ()


@pytest.mark.parametrize("mutation", ("changed", "missing"))
def test_catalog_cache_invalidates_changed_or_missing_durable_eval_review(
    tmp_path: Path,
    mutation: str,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    cache = WorkCatalogCache()
    assert len(cache.build(runs, None).works[0].versions[0].eval_reports) == 1
    review_path = project / "reviews/eval.review.json"
    if mutation == "changed":
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["note"] = "Durable review changed after delivery"
        review_path.write_text(json.dumps(review), encoding="utf-8")
    else:
        review_path.unlink()

    updated = cache.build(runs, None)

    assert len(updated.works) == 1
    assert updated.works[0].versions[0].eval_reports == ()


def test_catalog_cache_tracks_external_durable_eval_review_evidence(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    report = project / "stages/eval/eval_result.json"
    external = tmp_path / "eval-approval-proof.txt"
    external.write_text("approved EVAL proof", encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.EVAL,
        1,
        "Approved EVAL with external evidence",
        (report, external),
    )
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["eval_evidence"] = delivery_eval_evidence(project)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _publish_updated_delivery_manifest(project, "Approved external EVAL evidence")
    cache = WorkCatalogCache()
    assert len(cache.build(runs, None).works[0].versions[0].eval_reports) == 1

    external.write_text("changed EVAL proof", encoding="utf-8")

    updated = cache.build(runs, None)
    assert len(updated.works) == 1
    assert updated.works[0].versions[0].eval_reports == ()


@pytest.mark.parametrize(
    "mutation",
    ("snapshot_digest", "revision_digest", "review_digest", "review_snapshot"),
)
def test_catalog_omits_forged_delivery_eval_snapshot(
    tmp_path: Path,
    mutation: str,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = manifest["eval_evidence"]
    if mutation == "snapshot_digest":
        evidence["snapshot_sha256"] = "f" * 64
    elif mutation == "revision_digest":
        evidence["stage_revision_sha256"] = "f" * 64
    elif mutation == "review_digest":
        evidence["review"]["sha256"] = "f" * 64
    else:
        evidence["review"]["snapshot"]["note"] = "forged approval"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    master = project / "stages/deliver/master.mp4"
    deliver_revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "generic.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = deliver_revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        deliver_revision.number,
        "Approved delivery containing mutated evidence",
        (master,),
    )

    catalog = build_work_catalog(runs, None)

    assert len(catalog.works) == 1
    assert catalog.works[0].versions[0].eval_reports == ()


@pytest.mark.parametrize("legacy_kind", ("missing", "minimal", "forged_v1"))
def test_catalog_omits_missing_or_legacy_eval_evidence(
    tmp_path: Path,
    legacy_kind: str,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if legacy_kind == "missing":
        manifest.pop("eval_evidence")
    elif legacy_kind == "minimal":
        manifest["eval_evidence"] = {"stage": "eval", "revision": 1}
    else:
        report = project / "stages/eval/eval_result.json"
        manifest["eval_evidence"] = {
            "stage": "eval",
            "revision": 1,
            "input_signature": "eval-input",
            "executor": "generic.eval",
            "reports": [
                {
                    "path": "stages/eval/eval_result.json",
                    "sha256": _sha256(report.read_bytes()),
                }
            ],
            "review": {"revision": 1, "sha256": "f" * 64},
        }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    master = project / "stages/deliver/master.mp4"
    deliver_revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "generic.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = deliver_revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        deliver_revision.number,
        "Approved legacy delivery",
        (master,),
    )

    catalog = build_work_catalog(runs, None)

    assert len(catalog.works) == 1
    assert catalog.works[0].versions[0].eval_reports == ()


def test_catalog_uses_delivery_bound_eval_after_edit_or_package_injection(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    report = project / "stages/eval/eval_result.json"

    assert len(build_work_catalog(runs, None).works[0].versions[0].eval_reports) == 1

    report.write_text(json.dumps({"automatic_passed": False}), encoding="utf-8")
    assert build_work_catalog(runs, None).works[0].versions[0].eval_reports == ()

    project = _write_delivered_project(runs, project_id="new-eval", revision=1)
    injected = project / "stages/eval/injected.json"
    injected.write_text(json.dumps({"score": 100}), encoding="utf-8")
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["eval_reports"] = [str(injected)]
    package_path.write_text(json.dumps(package), encoding="utf-8")
    selected = next(
        work
        for work in build_work_catalog(runs, None).works
        if work.project_id == "new-eval"
    )
    assert [item.name for item in selected.versions[0].eval_reports] == [
        "eval_result.json"
    ]

    project = _write_delivered_project(runs, project_id="stale-eval", revision=1)
    report = project / "stages/eval/eval_result.json"
    revision = write_stage_revision(
        project,
        StageName.EVAL,
        (report,),
        "eval-input",
        "generic.eval",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-2]["revision"] = revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    selected = next(
        work
        for work in build_work_catalog(runs, None).works
        if work.project_id == "stale-eval"
    )
    assert selected.versions[0].eval_reports == ()


def test_catalog_omits_stale_human_eval_until_matching_redelivery(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    newer_report = project / "stages/eval/eval_result-v2.json"
    newer_report.write_text(json.dumps({"automatic_passed": True, "score": 99}))
    eval_revision = write_stage_revision(
        project,
        StageName.EVAL,
        (newer_report,),
        "eval-input-v2",
        "generic.eval",
    )
    approve_stage_revision(
        project,
        StageName.EVAL,
        eval_revision.number,
        "Approved newer EVAL after delivery",
        (newer_report,),
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-2].update(
        {
            "artifacts": [str(newer_report)],
            "revision": eval_revision.number,
            "input_signature": "eval-input-v2",
        }
    )
    package["eval_reports"] = [str(newer_report)]
    package_path.write_text(json.dumps(package), encoding="utf-8")

    before_redelivery = build_work_catalog(runs, None).works[0].versions[0]

    assert before_redelivery.eval_reports == ()

    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["eval_evidence"] = delivery_eval_evidence(project)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    master = project / "stages/deliver/master.mp4"
    deliver_revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "generic.deliver",
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = deliver_revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        deliver_revision.number,
        "Approved redelivery",
        (master,),
    )

    after_redelivery = build_work_catalog(runs, None).works[0].versions[0]

    assert [report.name for report in after_redelivery.eval_reports] == [
        newer_report.name
    ]


@pytest.mark.parametrize("mutation", ("missing-operation", "unregistered-master"))
def test_catalog_accepts_real_replica_delivery_and_rejects_partial_masters(
    tmp_path: Path,
    mutation: str,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, mode="replica", revision=1)

    catalog = build_work_catalog(runs, None)

    assert catalog.works[0].mode == "replica"
    assert catalog.works[0].versions[0].outputs[0].sha256 == _sha256(b"replica-master")

    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing-operation":
        manifest.pop("operation")
    else:
        manifest["masters"].append(str(project / "stages/deliver/release/missing.mp4"))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    master = project / "stages/deliver/release/final.mp4"
    revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "replica.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        revision.number,
        "Approved malformed fixture to test parser",
        (master,),
    )
    assert build_work_catalog(runs, None).works == ()


def test_catalog_rejects_replica_master_aliases_after_normalization(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, mode="replica", revision=1)
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    master = project / "stages/deliver/release/final.mp4"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["masters"] = [str(master), "stages/deliver/release/final.mp4"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "replica.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        revision.number,
        "Approved after alias mutation",
        (master,),
    )

    assert build_work_catalog(runs, None).works == ()


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
    assert all(
        (archive / entry.archive_relative).stat().st_nlink == 1
        for entry in first.entries
    )
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


def test_migration_rejects_source_and_destination_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    external = tmp_path / "external.m4a"
    os.link(source / "audio/black-cat-approved.m4a", external)
    with pytest.raises(ValueError, match="hard link"):
        migrate_showcase_media(source, tmp_path / "archive", dry_run=True)

    external.unlink()
    dry = migrate_showcase_media(source, tmp_path / "archive", dry_run=True)
    destination = tmp_path / "archive" / dry.entries[0].archive_relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes((source / dry.entries[0].source_relative).read_bytes())
    os.link(destination, tmp_path / "destination-alias")
    with pytest.raises(ValueError, match="hard link"):
        migrate_showcase_media(source, tmp_path / "archive")


def test_migration_does_not_replace_concurrent_writer_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    real_link = module.os.link
    injected = False

    def race_link(source_name, destination_name, **kwargs):
        nonlocal injected
        if not injected and not str(destination_name).endswith("archive_manifest.json"):
            injected = True
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            os.write(descriptor, b"concurrent owner data")
            os.close(descriptor)
        return real_link(source_name, destination_name, **kwargs)

    monkeypatch.setattr(module.os, "link", race_link)
    with pytest.raises(ValueError, match="collision"):
        migrate_showcase_media(source, archive)
    collided = next(archive.rglob("black-cat-approved.m4a"))
    assert collided.read_bytes() == b"concurrent owner data"
    assert not (archive / "archive_manifest.json").exists()


def test_concurrent_migrations_are_serialized_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_migrate_in_process, args=(str(source), str(archive), queue)
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert sorted(queue.get(timeout=2) for _ in processes) == [("ok", 7), ("ok", 7)]
    assert (
        len(json.loads((archive / "archive_manifest.json").read_text())["entries"]) == 7
    )


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
    real_link = module.os.link
    interrupted = False

    def fail_first_media_publish(source_name, destination_name, **kwargs):
        nonlocal interrupted
        if destination_name != "archive_manifest.json" and not interrupted:
            interrupted = True
            raise OSError("simulated interruption")
        return real_link(source_name, destination_name, **kwargs)

    monkeypatch.setattr(module.os, "link", fail_first_media_publish)

    with pytest.raises(OSError, match="simulated interruption"):
        migrate_showcase_media(source, archive)

    assert not (archive / "archive_manifest.json").exists()
    assert not list(archive.rglob("*.tmp"))


def test_migration_rejects_destination_replacement_after_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    original = module._existing_digest
    calls: dict[str, int] = {}
    replaced = False

    def replace_after_digest(parent: int, name: str):
        nonlocal replaced
        digest = original(parent, name)
        calls[name] = calls.get(name, 0) + 1
        if digest is not None and calls[name] >= 2 and not replaced:
            replaced = True
            descriptor = os.open(name, os.O_RDONLY, dir_fd=parent)
            try:
                content = b""
                while chunk := os.read(descriptor, 1024):
                    content += chunk
            finally:
                os.close(descriptor)
            replacement = f".{name}.replacement"
            candidate = os.open(
                replacement,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            os.write(candidate, content)
            os.close(candidate)
            os.rename(replacement, name, src_dir_fd=parent, dst_dir_fd=parent)
        return digest

    monkeypatch.setattr(module, "_existing_digest", replace_after_digest)

    with pytest.raises(ValueError, match="ownership verification"):
        migrate_showcase_media(source, archive)

    assert replaced
    assert not (archive / "archive_manifest.json").exists()


def test_migration_recovers_its_own_publish_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import migrate_showcase_works as module

    class SimulatedCrash(BaseException):
        pass

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    real_unlink = module.os.unlink

    def crash_before_temp_unlink(name, **kwargs):
        if str(name).endswith(".tmp"):
            raise SimulatedCrash
        return real_unlink(name, **kwargs)

    monkeypatch.setattr(module.os, "unlink", crash_before_temp_unlink)
    with pytest.raises(SimulatedCrash):
        migrate_showcase_media(source, archive)
    monkeypatch.setattr(module.os, "unlink", real_unlink)

    manifest = migrate_showcase_media(source, archive)

    assert len(manifest.entries) == 7
    assert not list(archive.rglob("*.tmp"))
    assert not list(archive.rglob("*.publish.json"))
    assert all(
        (archive / entry.archive_relative).stat().st_nlink == 1
        for entry in manifest.entries
    )


def test_migration_publishes_transaction_journal_before_any_payload_temp(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    repository = Path(__file__).resolve().parents[1]
    script = f"""
import os
from scripts import migrate_showcase_works as module

module._write_journal = lambda *args, **kwargs: os._exit(71)
module.migrate_showcase_media({str(source)!r}, {str(archive)!r})
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        check=False,
    )

    assert crashed.returncode == 71
    namespace = archive / ".storymotion-migration-transactions"
    transactions = list(namespace.iterdir())
    assert len(transactions) == 1
    owner_path = transactions[0] / "owner.json"
    assert owner_path.is_file()
    assert not (transactions[0] / "journal.json").exists()
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    crashed_entry = next(
        entry
        for entry in migrate_showcase_media(source, archive, dry_run=True).entries
        if entry.archive_relative == owner["destination"]
    )
    (source / crashed_entry.source_relative).unlink()

    manifest = migrate_showcase_media(source, archive)

    assert len(manifest.entries) == 6
    assert list(namespace.iterdir()) == []
    assert not list(archive.rglob("*.tmp"))
    assert not list(archive.rglob("*.publish.json"))


def test_migration_cleans_transaction_after_first_journal_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    original = module._write_journal
    calls = 0

    def fail_first_journal(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("initial journal failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_write_journal", fail_first_journal)
    with pytest.raises(OSError, match="initial journal failure"):
        migrate_showcase_media(source, archive)

    namespace = archive / ".storymotion-migration-transactions"
    assert list(namespace.iterdir()) == []
    monkeypatch.setattr(module, "_write_journal", original)
    manifest = migrate_showcase_media(source, archive)
    assert len(manifest.entries) == 7
    assert list(namespace.iterdir()) == []


def test_migration_cleans_new_transaction_when_directory_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    original = module.os.open
    failed = False

    def fail_transaction_open(path, flags, *args, **kwargs):
        nonlocal failed
        if not failed and str(path).startswith("tx-"):
            failed = True
            raise OSError("transaction directory open failure")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", fail_transaction_open)
    with pytest.raises(OSError, match="transaction directory open failure"):
        migrate_showcase_media(source, archive)

    namespace = archive / ".storymotion-migration-transactions"
    assert list(namespace.iterdir()) == []
    monkeypatch.setattr(module.os, "open", original)
    manifest = migrate_showcase_media(source, archive)
    assert len(manifest.entries) == 7
    assert list(namespace.iterdir()) == []


def test_migration_never_adopts_or_deletes_foreign_matching_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    planned = migrate_showcase_media(source, archive, dry_run=True).entries[0]
    destination = archive / planned.archive_relative
    destination.parent.mkdir(parents=True)
    foreign_temp = destination.parent / f".{destination.name}.foreign.tmp"
    foreign_temp.write_bytes((source / planned.source_relative).read_bytes())
    foreign_journal = destination.parent / f".{destination.name}.publish.json"
    foreign_payload = {
        "schema_version": "storymotion.archive-publish.v1",
        "destination": destination.name,
        "temporary": foreign_temp.name,
        "sha256": planned.sha256,
    }
    foreign_journal.write_text(json.dumps(foreign_payload), encoding="utf-8")
    before = (
        foreign_temp.stat(),
        foreign_temp.read_bytes(),
        foreign_journal.read_bytes(),
    )

    manifest = migrate_showcase_media(source, archive)

    assert len(manifest.entries) == 7
    assert foreign_temp.read_bytes() == before[1]
    assert foreign_journal.read_bytes() == before[2]
    assert (foreign_temp.stat().st_dev, foreign_temp.stat().st_ino) == (
        before[0].st_dev,
        before[0].st_ino,
    )
    assert destination.is_file()


def test_migration_preserves_malformed_private_transaction_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    namespace = archive / ".storymotion-migration-transactions"
    foreign = namespace / ("tx-" + "a" * 32)
    foreign.mkdir(parents=True, mode=0o700)
    namespace.chmod(0o700)
    owner = foreign / "owner.json"
    owner.write_text('{"schema_version":"foreign"}', encoding="utf-8")
    owner.chmod(0o600)
    before = owner.read_bytes()

    manifest = migrate_showcase_media(source, archive)

    assert len(manifest.entries) == 7
    assert owner.read_bytes() == before
    assert foreign.is_dir()


@pytest.mark.parametrize(
    ("target", "exit_code"),
    (("_create_payload", 72), ("_payload_ready", 73), ("_publish_no_replace", 74)),
)
def test_migration_recovers_private_transaction_crash_stages(
    tmp_path: Path,
    target: str,
    exit_code: int,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    repository = Path(__file__).resolve().parents[1]
    script = f"""
import os
from scripts import migrate_showcase_works as module

setattr(module, {target!r}, lambda *args, **kwargs: os._exit({exit_code}))
module.migrate_showcase_media({str(source)!r}, {str(archive)!r})
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        check=False,
    )

    assert crashed.returncode == exit_code
    manifest = migrate_showcase_media(source, archive)
    namespace = archive / ".storymotion-migration-transactions"
    assert namespace.stat().st_mode & 0o777 == 0o700
    assert list(namespace.iterdir()) == []
    assert all(
        (archive / entry.archive_relative).is_file() for entry in manifest.entries
    )


def test_migration_cleans_owned_transaction_when_journal_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import migrate_showcase_works as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    original = module._write_journal
    calls = 0

    def fail_payload_identity_update(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated journal update failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_write_journal", fail_payload_identity_update)
    with pytest.raises(OSError, match="journal update failure"):
        migrate_showcase_media(source, archive)
    monkeypatch.setattr(module, "_write_journal", original)

    manifest = migrate_showcase_media(source, archive)
    namespace = archive / ".storymotion-migration-transactions"
    assert list(namespace.iterdir()) == []
    assert len(manifest.entries) == 7


def test_migration_recovers_crash_between_payload_create_and_inode_journal(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    repository = Path(__file__).resolve().parents[1]
    script = f"""
import os
from scripts import migrate_showcase_works as module

original = module._write_journal
calls = 0
def crash_second_journal(*args, **kwargs):
    global calls
    calls += 1
    if calls == 2:
        os._exit(75)
    return original(*args, **kwargs)
module._write_journal = crash_second_journal
module.migrate_showcase_media({str(source)!r}, {str(archive)!r})
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        check=False,
    )

    assert crashed.returncode == 75
    manifest = migrate_showcase_media(source, archive)
    namespace = archive / ".storymotion-migration-transactions"
    assert list(namespace.iterdir()) == []
    assert len(manifest.entries) == 7


def test_archive_manifest_marks_redistribution_rights_unverified(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)

    manifest = migrate_showcase_media(source, tmp_path / "archive", dry_run=True)

    assert len(manifest.entries) == 7
    for entry in manifest.entries:
        assert entry.rights["origin"] == "legacy_storymotion_showcase"
        assert entry.rights["creator"] == "unverified"
        assert entry.rights["license"] == "unverified"
        assert entry.rights["commercial_use"] == "unverified"
        assert entry.rights["redistribution_status"] == "unverified"
        assert "do not redistribute" in entry.rights["distribution_warning"].lower()


@pytest.mark.parametrize("rights", ({}, {"origin": "legacy"}))
def test_archive_manifest_missing_or_incomplete_rights_defaults_unverified(
    tmp_path: Path,
    rights: dict[str, str],
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    payload = manifest.to_dict()
    payload["entries"][0]["rights"] = rights

    loaded = type(manifest).from_dict(payload, archive_root=archive)
    selected = loaded.entries[0].rights

    assert selected["origin"] == "unverified"
    assert selected["creator"] == "unverified"
    assert selected["license"] == "unverified"
    assert selected["commercial_use"] == "unverified"
    assert selected["redistribution_status"] == "unverified"
    assert "do not redistribute" in selected["distribution_warning"].lower()


@pytest.mark.parametrize(
    "media_type",
    (
        "audio/mp4; access_token=FAKE",
        "video/mp4\r\nX-Injected: yes",
        "视频/mp4",
        "audio/",
        f"audio/{'x' * 128}",
    ),
)
def test_archive_manifest_rejects_unsafe_media_types(
    tmp_path: Path,
    media_type: str,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    manifest = migrate_showcase_media(source, tmp_path / "archive", dry_run=True)
    payload = manifest.entries[0].to_dict()
    payload["media_type"] = media_type

    with pytest.raises(ValueError, match="media type"):
        type(manifest.entries[0]).from_dict(payload)


@pytest.mark.parametrize(
    "media_type",
    (
        "audio/mp4",
        "audio/mp4a-latm",
        "audio/x-wav",
        "video/quicktime",
        "image/svg+xml",
        "text/plain",
        "application/json",
    ),
)
def test_archive_manifest_accepts_safe_media_types(
    tmp_path: Path,
    media_type: str,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    manifest = migrate_showcase_media(source, tmp_path / "archive", dry_run=True)
    payload = manifest.entries[0].to_dict()
    payload["media_type"] = media_type

    loaded = type(manifest.entries[0]).from_dict(payload)

    assert loaded.media_type == media_type


def test_tracked_archive_builds_catalog_in_clean_copy_and_matches_all_sources(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    tracked = repository / "assets/workbench_archive"
    copied = tmp_path / "clean-copy/assets/workbench_archive"
    import shutil

    shutil.copytree(tracked, copied)
    manifest = json.loads(
        (copied / "archive_manifest.json").read_text(encoding="utf-8")
    )
    catalog = build_work_catalog(
        tmp_path / "clean-copy/runs", copied / "archive_manifest.json"
    )

    assert manifest["source_file_count"] == 7
    assert sum(len(work.versions) for work in catalog.works) == 7
    for entry in manifest["entries"]:
        payload = (copied / entry["archive_relative"]).read_bytes()
        assert _sha256(payload) == entry["sha256"]
    from factory.pipeline_jobs import JobManager
    from factory.workbench_service import WorkbenchService

    service = WorkbenchService(
        tmp_path / "clean-copy",
        job_manager=JobManager(tmp_path / "clean-copy"),
        provider_profile_loader=lambda: None,
    )
    assert len(service.list_works()) == 2


def test_catalog_cache_reuses_unchanged_projects_and_invalidates_one_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import work_catalog as module

    runs = tmp_path / "runs"
    first_project = _write_delivered_project(runs, project_id="first", revision=1)
    _write_delivered_project(runs, project_id="second", revision=1)
    calls: list[str] = []
    original = module._delivered_work

    def counted(project):
        calls.append(project.canonical_path.name)
        return original(project)

    monkeypatch.setattr(module, "_delivered_work", counted)
    cache = WorkCatalogCache(max_entries=8)
    assert len(cache.build(runs, None).works) == 2
    assert cache.entry_count <= 8
    assert len(cache.build(runs, None).works) == 2
    assert calls == ["first", "second"]

    os.utime(first_project / "production_package.json", None)
    assert len(cache.build(runs, None).works) == 2
    assert calls == ["first", "second", "first"]


def test_catalog_cache_tracks_archive_manifest_object_payloads(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    cache = WorkCatalogCache()
    first = cache.build(tmp_path / "runs", manifest)
    entry = manifest.entries[0]
    (archive / entry.archive_relative).write_bytes(b"modified archive payload")

    cached_after_mutation = cache.build(tmp_path / "runs", manifest)
    fresh_after_mutation = WorkCatalogCache().build(tmp_path / "runs", manifest)

    assert cached_after_mutation == fresh_after_mutation
    assert cached_after_mutation != first
    assert any(
        "archive_entry_unavailable" in item for item in cached_after_mutation.warnings
    )


def test_catalog_cache_keys_archive_manifest_objects_by_content(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    cache = WorkCatalogCache()
    first = cache.build(tmp_path / "runs", manifest)
    payload = manifest.to_dict()
    payload["entries"][0]["title"] = "Updated archive title"
    updated = type(manifest).from_dict(payload, archive_root=archive)

    cached_after_metadata_change = cache.build(tmp_path / "runs", updated)
    fresh_after_metadata_change = WorkCatalogCache().build(
        tmp_path / "runs", updated
    )

    assert cached_after_metadata_change == fresh_after_metadata_change
    assert cached_after_metadata_change != first


def test_catalog_cache_ignores_unrelated_archive_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory import work_catalog as module

    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    calls = 0
    original = module._archive_works

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(module, "_archive_works", counted)
    cache = WorkCatalogCache()
    first = cache.build(tmp_path / "runs", manifest)
    unrelated = archive / "operator-notes.txt"
    unrelated.write_text("not catalog evidence", encoding="utf-8")

    assert cache.build(tmp_path / "runs", manifest) == first
    assert calls == 1


def test_catalog_cache_hit_ignores_unrelated_assets_without_recursive_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory import work_catalog as module

    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    cache = WorkCatalogCache()
    first = cache.build(runs, None)
    unrelated = project / "stages/assets/unrelated.bin"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"unrelated")

    def reject_recursive_walk(*_args, **_kwargs):
        raise AssertionError("catalog cache hits must not walk the full runs tree")

    monkeypatch.setattr(module.os, "walk", reject_recursive_walk)

    assert cache.build(runs, None) == first


def test_catalog_cache_archive_invalidation_is_authoritative_under_concurrency(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    archive = tmp_path / "archive"
    manifest = migrate_showcase_media(source, archive)
    cache = WorkCatalogCache()
    cache.build(tmp_path / "runs", manifest)
    entry = manifest.entries[0]
    (archive / entry.archive_relative).write_bytes(b"concurrent mutation")
    expected = WorkCatalogCache().build(tmp_path / "runs", manifest)
    barrier = threading.Barrier(4)
    results: list[WorkCatalog] = []

    def build_after_barrier() -> None:
        barrier.wait()
        results.append(cache.build(tmp_path / "runs", manifest))

    workers = [threading.Thread(target=build_after_barrier) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert results == [expected] * 4


def test_catalog_cache_discards_parse_when_authoritative_dependency_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import work_catalog as module

    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    original = module._delivered_work
    changed = False

    def mutate_after_parse(anchor):
        nonlocal changed
        work = original(anchor)
        if not changed:
            changed = True
            package_path = project / "production_package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package["stages"][-1]["review_state"] = "skipped"
            package_path.write_text(json.dumps(package), encoding="utf-8")
        return work

    monkeypatch.setattr(module, "_delivered_work", mutate_after_parse)

    assert WorkCatalogCache().build(runs, None).works == ()


def test_catalog_cache_tracks_non_public_revision_artifacts(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    manifest = project / "stages/deliver/delivery_manifest.json"
    master = project / "stages/deliver/master.mp4"
    notes = project / "stages/deliver/release-notes.txt"
    notes.write_text("approved notes", encoding="utf-8")
    revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest, master, notes),
        "deliver-input",
        "generic.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = revision.number
    package["stages"][-1]["artifacts"] = [str(manifest), str(master), str(notes)]
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        revision.number,
        "Approved notes",
        (master,),
    )
    cache = WorkCatalogCache()
    assert len(cache.build(runs, None).works) == 1

    notes.write_text("edited after approval", encoding="utf-8")

    assert cache.build(runs, None).works == ()


def test_catalog_cache_tracks_external_review_evidence(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    master = project / "stages/deliver/master.mp4"
    external = tmp_path / "approval-proof.txt"
    external.write_text("approved proof", encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        1,
        "Approved with external proof",
        (master, external),
    )
    cache = WorkCatalogCache()
    assert len(cache.build(runs, None).works) == 1

    external.write_text("changed proof", encoding="utf-8")

    assert cache.build(runs, None).works == ()


def test_catalog_snapshot_avoids_lru_thrash_above_project_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory import work_catalog as module

    runs = tmp_path / "runs"
    for project_id in ("first", "second", "third"):
        _write_delivered_project(runs, project_id=project_id, revision=1)
    original = module._delivered_work
    calls = 0

    def counted(project):
        nonlocal calls
        calls += 1
        return original(project)

    monkeypatch.setattr(module, "_delivered_work", counted)
    cache = WorkCatalogCache(max_entries=2)

    assert len(cache.build(runs, None).works) == 3
    assert len(cache.build(runs, None).works) == 3
    assert calls == 3


def test_catalog_streams_large_master_without_read_bytes_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory.secure_posix import AnchoredDirectory

    runs = tmp_path / "runs"
    project = _write_delivered_project(runs, revision=1)
    master = project / "stages/deliver/master.mp4"
    with master.open("wb") as stream:
        stream.seek(16 * 1024 * 1024 - 1)
        stream.write(b"x")
    with master.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest_path = project / "stages/deliver/delivery_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    revision = write_stage_revision(
        project,
        StageName.DELIVER,
        (manifest_path, master),
        "deliver-input",
        "generic.deliver",
    )
    package_path = project / "production_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["stages"][-1]["revision"] = revision.number
    package_path.write_text(json.dumps(package), encoding="utf-8")
    approve_stage_revision(
        project,
        StageName.DELIVER,
        revision.number,
        "Approved sparse master",
        (master,),
    )
    original = AnchoredDirectory.read_bytes

    def reject_media_materialization(self, path):
        if Path(path).suffix == ".mp4":
            raise AssertionError("catalog must stream media evidence")
        return original(self, path)

    monkeypatch.setattr(AnchoredDirectory, "read_bytes", reject_media_materialization)

    catalog = build_work_catalog(runs, None)

    assert catalog.works[0].versions[0].outputs[0].size_bytes == 16 * 1024 * 1024


def test_migration_rejects_unsafe_roots(tmp_path: Path) -> None:
    source = tmp_path / "public"
    _write_showcase(source)
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(source, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        migrate_showcase_media(source_alias, tmp_path / "archive")

    with pytest.raises(ValueError):
        migrate_showcase_media(source, source / "nested-archive")
