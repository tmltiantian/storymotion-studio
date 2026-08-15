from __future__ import annotations

import pytest

from factory.pipeline_contracts import StageName
from factory.pipeline_review import (
    ApprovalPreset,
    ReviewPolicy,
    approve_stage_revision,
    resolve_review_config,
    validate_stage_review,
    write_stage_revision,
)


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
