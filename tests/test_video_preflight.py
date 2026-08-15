from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory.gateway_video_batch import GatewayVideoJob, _execute_gateway_video_jobs
from factory.pipeline_contracts import (
    ProjectMode,
    ProjectSpec,
    ReviewPolicy,
    ReviewState,
    StageName,
    StageState,
)
from factory.pipeline_review import approve_stage_revision, write_stage_revision
from factory.pipeline_store import create_project, update_stage
from factory.video_preflight import (
    GenerationTokenError,
    VideoGenerationRequest,
    build_video_preflight,
    consume_generation_token,
    issue_generation_token,
)


def _ready_video_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    create_project(
        project_dir,
        ProjectSpec(
            project_id="preflight-project",
            title="Preflight Project",
            mode=ProjectMode.ORIGINAL,
            input={"kind": "idea", "text": "A compact fixture"},
            output_dir=(tmp_path / "output").resolve(),
            target={"video_resolution": "768P"},
            providers={
                "video_provider": "minimax",
                "video_model": "MiniMax-H3",
            },
        ),
    )
    stages = {
        StageName.SCRIPT: {"script": "ready"},
        StageName.STORYBOARD: {
            "project_id": "preflight-project",
            "shots": [
                {"id": "shot_01", "index": 1, "duration_seconds": 4},
                {"id": "shot_03", "index": 3, "duration_seconds": 6},
            ],
        },
        StageName.ASSETS: {"production_ready": True},
        StageName.AUDIO: {"voiceover_audio": "ready.wav"},
    }
    evidence = project_dir / "approval-evidence.json"
    evidence.write_text('{"approved":true}', encoding="utf-8")
    for stage, payload in stages.items():
        artifact = project_dir / "stages" / stage.value / (
            "episode.json" if stage is StageName.STORYBOARD else f"{stage.value}.json"
        )
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(payload), encoding="utf-8")
        revision = write_stage_revision(
            project_dir,
            stage,
            (artifact,),
            f"sig-{stage.value}",
            "fixture",
        )
        approve_stage_revision(
            project_dir,
            stage,
            revision.number,
            f"approve {stage.value}",
            (evidence,),
        )
        update_stage(
            project_dir,
            stage,
            StageState.PASSED,
            executor="fixture",
            input_signature=f"sig-{stage.value}",
            artifacts=(artifact,),
            revision=revision.number,
            review_policy=ReviewPolicy.MANUAL,
            review_state=ReviewState.APPROVED,
        )
    return project_dir


def test_build_preflight_is_read_only_and_binds_current_revisions(
    tmp_path: Path,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    before = {
        path.relative_to(project_dir): path.read_bytes()
        for path in project_dir.rglob("*")
        if path.is_file()
    }

    preflight = build_video_preflight(project_dir, ("shot_03",))

    after = {
        path.relative_to(project_dir): path.read_bytes()
        for path in project_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert preflight.ready is True
    assert preflight.project_id == "preflight-project"
    assert preflight.shot_ids == ("shot_03",)
    assert preflight.provider == "minimax"
    assert preflight.model == "MiniMax-H3"
    assert preflight.resolution == "768P"
    assert preflight.output_seconds == 6
    assert preflight.estimated_cost_yuan == 3.0
    assert set(preflight.revision_hashes) == {"script", "storyboard", "assets", "audio"}
    assert preflight.artifact_hashes


def test_generation_token_is_bound_and_single_use(tmp_path: Path) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)

    consume_generation_token(project_dir, token, request)

    with pytest.raises(GenerationTokenError, match="consumed"):
        consume_generation_token(project_dir, token, request)


@pytest.mark.parametrize(
    "changed_request",
    (
        lambda request: replace(request, shot_ids=("shot_01",)),
        lambda request: replace(request, model="MiniMax-H3-changed"),
        lambda request: replace(request, output_seconds=request.output_seconds + 1),
        lambda request: replace(
            request,
            estimated_cost_yuan=request.estimated_cost_yuan + 0.5,
        ),
    ),
)
def test_generation_token_rejects_changed_canonical_request(
    tmp_path: Path,
    changed_request,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)

    with pytest.raises(GenerationTokenError, match="match"):
        consume_generation_token(project_dir, token, changed_request(request))


def test_generation_token_rejects_artifact_changed_after_issue(tmp_path: Path) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    storyboard = project_dir / "stages/storyboard/episode.json"
    storyboard.write_text('{"shots":[]}', encoding="utf-8")

    with pytest.raises(GenerationTokenError, match="changed"):
        consume_generation_token(project_dir, token, request)


def test_generation_token_rejects_review_evidence_changed_after_issue(
    tmp_path: Path,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    (project_dir / "approval-evidence.json").write_text(
        '{"approved":false}',
        encoding="utf-8",
    )

    with pytest.raises(GenerationTokenError, match="changed"):
        consume_generation_token(project_dir, token, request)


def test_preflight_rejects_unknown_duplicate_and_traversal_shot_ids(
    tmp_path: Path,
) -> None:
    project_dir = _ready_video_project(tmp_path)

    for shot_ids in (("missing",), ("shot_03", "shot_03"), ("../shot_03",)):
        with pytest.raises(ValueError, match="shot"):
            build_video_preflight(project_dir, shot_ids)


def test_token_storage_rejects_symlink_and_writes_no_secret_material(
    tmp_path: Path,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    token = issue_generation_token(project_dir, preflight)
    token_dir = project_dir / "runs/.workbench/tokens"
    token_id, _secret = token.split(".", 1)
    token_record = json.loads(
        (token_dir / f"{token_id}.json").read_text(encoding="utf-8")
    )
    serialized = json.dumps(token_record)
    assert token not in serialized
    assert _secret not in serialized
    assert "api_key" not in serialized.lower()
    assert "authorization" not in serialized.lower()

    outside = tmp_path / "outside-tokens"
    outside.mkdir()
    for child in token_dir.iterdir():
        child.unlink()
    token_dir.rmdir()
    token_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        issue_generation_token(project_dir, preflight)
    assert not list(outside.iterdir())


def test_confirmed_request_is_consumed_before_billable_submit(tmp_path: Path) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    output = tmp_path / "clip.mp4"
    report_path = tmp_path / "report.json"
    observed_consumed: list[bool] = []

    class FakeConfig:
        api_key = ""
        base_url = "https://provider.example"
        model = "MiniMax-H3"

    class FakeClient:
        provider = "minimax"
        requires_generation_confirmation = True
        config = FakeConfig()

        def prepare_submission(self, *_args, **_kwargs):
            return object()

        def submit_prepared(self, _submission, *, allow_network=False):
            del allow_network
            token_id = token.split(".", 1)[0]
            record = json.loads(
                (
                    project_dir
                    / "runs/.workbench/tokens"
                    / f"{token_id}.json"
                ).read_text(encoding="utf-8")
            )
            observed_consumed.append(bool(record["consumed_at"]))
            raise RuntimeError("stop after observing submit boundary")

    job = GatewayVideoJob(
        shot_id="shot_03",
        index=3,
        prompt="A safe prompt",
        images=(),
        duration=6,
        ratio="9:16",
        resolution="768P",
        output_path=str(output),
    )
    report = {
        "executed": False,
        "blocked_reasons": [],
        "results": [],
        "errors": [],
        "completed_count": 0,
        "skipped_count": 0,
        "resumed_count": 0,
        "failed_count": 0,
        "planned_count": 1,
    }

    with pytest.raises(RuntimeError, match="submit boundary"):
        _execute_gateway_video_jobs(
            [job],
            FakeClient(),
            report_path,
            report,
            generate_audio=False,
            allow_network=True,
            overwrite=False,
            project_dir=project_dir,
            generation_token=token,
            generation_request=request,
        )

    assert observed_consumed == [True]


def test_confirmation_required_client_cannot_submit_without_token(tmp_path: Path) -> None:
    output = tmp_path / "clip.mp4"

    class FakeConfig:
        api_key = ""
        base_url = "https://provider.example"
        model = "MiniMax-H3"

    class FakeClient:
        provider = "minimax"
        requires_generation_confirmation = True
        config = FakeConfig()

        def submit_prepared(self, *_args, **_kwargs):
            raise AssertionError("provider must not be contacted")

    job = GatewayVideoJob(
        shot_id="shot_03",
        index=3,
        prompt="A safe prompt",
        images=(),
        duration=6,
        ratio="9:16",
        resolution="768P",
        output_path=str(output),
    )
    report = {
        "executed": False,
        "blocked_reasons": [],
        "results": [],
        "errors": [],
        "completed_count": 0,
        "skipped_count": 0,
        "resumed_count": 0,
        "failed_count": 0,
        "planned_count": 1,
    }

    with pytest.raises(GenerationTokenError, match="confirmation"):
        _execute_gateway_video_jobs(
            [job],
            FakeClient(),
            tmp_path / "report.json",
            report,
            generate_audio=False,
            allow_network=True,
            overwrite=False,
        )
