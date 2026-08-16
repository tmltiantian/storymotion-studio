from __future__ import annotations

import hashlib
import json

import pytest

from factory.pipeline_contracts import PIPELINE_STAGES, StageName
from factory.pipeline_review import (
    ApprovalPreset,
    ReviewPolicy,
    approve_stage_revision,
    delivery_eval_evidence,
    resolve_review_config,
    validate_stage_review,
    write_stage_revision,
)


def _write_eval_package(
    root,
    *,
    policy: str,
    state: str,
):
    report = root / "stages/eval/eval_result.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"automatic_passed":true}', encoding="utf-8")
    revision = write_stage_revision(
        root,
        StageName.EVAL,
        (report,),
        "eval-input",
        "generic.eval",
    )
    stages = []
    for stage in PIPELINE_STAGES:
        is_eval = stage is StageName.EVAL
        stages.append(
            {
                "stage": stage.value,
                "state": "passed" if is_eval else "pending",
                "executor": "generic.eval" if is_eval else "",
                "input_signature": "eval-input" if is_eval else "",
                "artifacts": [str(report)] if is_eval else [],
                "revision": revision.number if is_eval else None,
                "review_policy": policy if is_eval else "automatic",
                "review_state": state if is_eval else "not_ready",
                "review_blocks_progress": False,
            }
        )
    (root / "production_package.json").write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.production-package.v1",
                "project_id": "episode",
                "mode": "original",
                "spec_path": str(root / "project.json"),
                "spec_sha256": "0" * 64,
                "stages": stages,
                "final_outputs": [],
                "eval_reports": [str(report)],
            }
        ),
        encoding="utf-8",
    )
    return report, revision


def _canonical_digest(value) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_standard_preset_requires_expected_reviews() -> None:
    config = resolve_review_config(ApprovalPreset.STANDARD, {})

    assert config.policy_for(StageName.CONCEPT) is ReviewPolicy.AUTOMATIC
    assert config.policy_for(StageName.SCRIPT) is ReviewPolicy.MANUAL
    assert config.policy_for(StageName.VIDEO) is ReviewPolicy.MANUAL
    assert config.policy_for(StageName.DELIVER) is ReviewPolicy.MANUAL


def test_review_override_applies_to_an_unprotected_stage() -> None:
    config = resolve_review_config(
        ApprovalPreset.STANDARD,
        {StageName.AUDIO.value: ReviewPolicy.AUTOMATIC.value},
    )

    assert config.policy_for(StageName.AUDIO) is ReviewPolicy.AUTOMATIC


def test_quick_automatic_eval_builds_delivery_evidence_without_review_file(
    tmp_path,
) -> None:
    report, revision = _write_eval_package(
        tmp_path,
        policy="automatic",
        state="auto_approved",
    )

    evidence = delivery_eval_evidence(tmp_path)

    assert evidence["schema_version"] == (
        "motion-comic-factory.delivery-eval-evidence.v2"
    )
    assert evidence["policy"] == "automatic"
    assert evidence["state"] == "auto_approved"
    assert evidence["revision"] == revision.number
    assert evidence["stage_revision"]["artifacts"] == [
        {
            "path": "stages/eval/eval_result.json",
            "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            "media_type": "application/json",
        }
    ]
    assert evidence["stage_revision_sha256"] == _canonical_digest(
        evidence["stage_revision"]
    )
    assert "review" not in evidence
    snapshot = {key: value for key, value in evidence.items() if key != "snapshot_sha256"}
    assert evidence["snapshot_sha256"] == _canonical_digest(snapshot)


@pytest.mark.parametrize("policy", ("manual", "grouped"))
def test_human_eval_delivery_evidence_embeds_canonical_approved_review(
    tmp_path,
    policy: str,
) -> None:
    report, revision = _write_eval_package(
        tmp_path,
        policy=policy,
        state="approved",
    )
    approve_stage_revision(
        tmp_path,
        StageName.EVAL,
        revision.number,
        "Approved EVAL",
        (report,),
    )

    evidence = delivery_eval_evidence(tmp_path)

    assert evidence["policy"] == policy
    assert evidence["state"] == "approved"
    assert evidence["review"]["snapshot"]["schema_version"] == (
        "motion-comic-factory.stage-review.v1"
    )
    assert evidence["review"]["snapshot"]["evidence"][0]["path"] == (
        "stages/eval/eval_result.json"
    )
    assert evidence["review"]["sha256"] == _canonical_digest(
        evidence["review"]["snapshot"]
    )


def test_revision_hash_change_invalidates_old_review(tmp_path) -> None:
    artifact = tmp_path / "script.json"
    artifact.write_text('{"version":1}', encoding="utf-8")
    revision = write_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        (artifact,),
        "script-signature",
        "original.script",
    )
    approve_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        revision.number,
        "dialogue is natural",
        (artifact,),
    )

    artifact.write_text("changed", encoding="utf-8")

    validation = validate_stage_review(tmp_path, StageName.SCRIPT)
    assert validation.valid is False
    assert "changed" in validation.reason


def test_stale_revision_approval_is_rejected_before_persistence(tmp_path) -> None:
    artifact = tmp_path / "script.json"
    artifact.write_text('{"version":1}', encoding="utf-8")
    first = write_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        (artifact,),
        "script-signature-1",
        "original.script",
    )
    artifact.write_text('{"version":2}', encoding="utf-8")
    write_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        (artifact,),
        "script-signature-2",
        "original.script",
    )

    with pytest.raises(ValueError, match="latest revision"):
        approve_stage_revision(
            tmp_path,
            StageName.SCRIPT,
            first.number,
            "dialogue is natural",
            (artifact,),
        )

    assert not (tmp_path / "reviews" / "script.review.json").exists()


def test_malformed_review_record_is_invalid(tmp_path) -> None:
    artifact = tmp_path / "script.json"
    artifact.write_text('{"version":1}', encoding="utf-8")
    revision = write_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        (artifact,),
        "script-signature",
        "original.script",
    )
    approve_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        revision.number,
        "dialogue is natural",
        (artifact,),
    )
    (tmp_path / "reviews" / "script.review.json").write_text(
        '{"schema_version":"motion-comic-factory.stage-review.v1"}',
        encoding="utf-8",
    )

    validation = validate_stage_review(tmp_path, StageName.SCRIPT)

    assert validation.valid is False
    assert "unreadable" in validation.reason
