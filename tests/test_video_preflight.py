from __future__ import annotations

import json
import hashlib
import shutil
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from factory import gateway_video_batch as gateway_batch
from factory import video_preflight
from factory.gateway_video_batch import (
    GatewayVideoJob,
    _execute_gateway_video_jobs,
    render_gateway_video_single,
)
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
        artifact = (
            project_dir
            / "stages"
            / stage.value
            / (
                "episode.json"
                if stage is StageName.STORYBOARD
                else f"{stage.value}.json"
            )
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
                (project_dir / "runs/.workbench/tokens" / f"{token_id}.json").read_text(
                    encoding="utf-8"
                )
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


def test_confirmation_required_client_cannot_submit_without_token(
    tmp_path: Path,
) -> None:
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


@pytest.mark.parametrize(
    ("client_provider", "client_model", "duration", "resolution"),
    (
        ("gateway", "MiniMax-H3", 6, "768P"),
        ("minimax", "different-model", 6, "768P"),
        ("minimax", "MiniMax-H3", 5, "768P"),
        ("minimax", "MiniMax-H3", 6, "2K"),
    ),
)
def test_batch_confirmation_rejects_actual_billable_parameter_mismatch(
    tmp_path: Path,
    client_provider: str,
    client_model: str,
    duration: int,
    resolution: str,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    submit_count = 0

    class FakeConfig:
        api_key = ""
        base_url = "https://provider.example"
        model = client_model

    class FakeClient:
        provider = client_provider
        requires_generation_confirmation = True
        config = FakeConfig()

        def prepare_submission(self, *_args, **_kwargs):
            return object()

        def submit_prepared(self, *_args, **_kwargs):
            nonlocal submit_count
            submit_count += 1
            raise AssertionError("mismatched request reached provider")

    job = GatewayVideoJob(
        shot_id="shot_03",
        index=3,
        prompt="A safe prompt",
        images=(),
        duration=duration,
        ratio="9:16",
        resolution=resolution,
        output_path=str(tmp_path / "clip.mp4"),
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

    with pytest.raises(GenerationTokenError, match="match"):
        _execute_gateway_video_jobs(
            [job],
            FakeClient(),
            tmp_path / "report.json",
            report,
            generate_audio=False,
            allow_network=True,
            overwrite=False,
            project_dir=project_dir,
            generation_token=token,
            generation_request=request,
        )

    assert submit_count == 0
    token_id = token.split(".", 1)[0]
    record = json.loads(
        (project_dir / "runs/.workbench/tokens" / f"{token_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["consumed_at"] == ""


def test_single_confirmation_rejects_actual_duration_before_submit(
    tmp_path: Path,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)

    class FakeConfig:
        api_key = ""
        base_url = "https://provider.example"
        model = "MiniMax-H3"

    class FakeClient:
        provider = "minimax"
        requires_generation_confirmation = True
        config = FakeConfig()

        def validate_generation_settings(self, **_kwargs):
            return None

        def validate_reference_images(self, _images):
            return None

        def validate_reference_audio(self, _audio):
            return None

        def prepare_submission(self, *_args, **_kwargs):
            return object()

        def submit_prepared(self, *_args, **_kwargs):
            raise AssertionError("mismatched single request reached provider")

    with pytest.raises(GenerationTokenError, match="match"):
        render_gateway_video_single(
            "prompt",
            tmp_path / "single.mp4",
            FakeClient(),
            tmp_path / "single-report.json",
            duration=5,
            resolution="768P",
            allow_network=True,
            project_dir=project_dir,
            generation_token=token,
            generation_request=request,
        )


def test_token_binds_replaced_valid_approval_provenance(tmp_path: Path) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    evidence = project_dir / "replacement-evidence.json"
    evidence.write_text('{"approved":"replacement"}', encoding="utf-8")
    review_path = project_dir / "reviews/script.review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["note"] = "replacement valid approval"
    review["evidence"] = [
        {
            "path": str(evidence.resolve()),
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "media_type": "application/json",
        }
    ]
    review_path.write_text(json.dumps(review), encoding="utf-8")

    current = build_video_preflight(project_dir, ("shot_03",))

    assert current.ready is True
    assert current.approval_hashes != preflight.approval_hashes
    with pytest.raises(GenerationTokenError, match="changed"):
        consume_generation_token(project_dir, token, request)


@pytest.mark.parametrize("rate", (0, -1, float("inf"), float("nan"), 1e-300, True))
def test_preflight_rejects_non_billable_or_non_finite_price(
    rate, tmp_path: Path
) -> None:
    project_dir = _ready_video_project(tmp_path)
    spec_path = project_dir / "project.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["target"]["video_price_yuan_per_second"] = rate
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    preflight = build_video_preflight(project_dir, ("shot_03",))

    assert preflight.ready is False
    assert preflight.estimated_cost_yuan == 0
    with pytest.raises(GenerationTokenError, match="not ready"):
        issue_generation_token(project_dir, preflight)


@pytest.mark.parametrize(
    "active",
    (
        {"schema_version": "wrong", "affected": {"video": ["shot_03"]}},
        {
            "schema_version": "motion-comic-factory.active-repair.v1",
            "plan_id": "plan",
            "request_stage": "storyboard",
            "affected": [],
            "preserved_artifacts": [],
            "source_package_sha256": "0" * 64,
            "target_package_sha256": "1" * 64,
        },
        {
            "schema_version": "motion-comic-factory.active-repair.v1",
            "plan_id": "plan",
            "request_stage": "storyboard",
            "affected": {"video": []},
            "preserved_artifacts": [],
            "source_package_sha256": "0" * 64,
            "target_package_sha256": "1" * 64,
        },
    ),
)
def test_preflight_fails_closed_on_malformed_active_repair(
    tmp_path: Path,
    active: dict[str, object],
) -> None:
    project_dir = _ready_video_project(tmp_path)
    active_path = project_dir / "impact_plans/active.json"
    active_path.parent.mkdir()
    active_path.write_text(json.dumps(active), encoding="utf-8")

    with pytest.raises(ValueError, match="repair"):
        build_video_preflight(project_dir, ("shot_03",))


def test_gateway_report_always_deep_sanitizes_nested_secrets(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    payload = {
        "headers": {"Authorization": "Bearer nested-secret"},
        "error": "failed at https://user:url-secret@provider.example/video",
        "nested": [{"accessToken": "token-secret"}],
        "callback_error": "clientSecret: callback-secret",
    }

    safe = gateway_batch._write_report(destination, payload, None)
    serialized = json.dumps(safe)

    for secret in (
        "nested-secret",
        "url-secret",
        "token-secret",
        "callback-secret",
    ):
        assert secret not in serialized
        assert secret not in destination.read_text(encoding="utf-8")


def test_gateway_state_fsyncs_parent_directory(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "clip.mp4.gateway.json"
    directory_fsyncs: list[int] = []
    original_fsync = gateway_batch.os.fsync

    def observe_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(gateway_batch.os.fstat(descriptor).st_mode):
            directory_fsyncs.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(gateway_batch.os, "fsync", observe_fsync)

    gateway_batch.write_atomic_json(state_path, {"task_id": "task-123"})

    assert directory_fsyncs


def test_token_directory_swap_fails_closed_without_outside_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    issue_generation_token(project_dir, preflight)
    token_dir = project_dir / "runs/.workbench/tokens"
    held = project_dir / "runs/.workbench/tokens-held"
    outside = tmp_path / "outside-tokens"
    outside.mkdir()
    original_replace = video_preflight.os.replace
    swapped = False

    def swap_on_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        destination_path = Path(destination)
        if not swapped and destination_path.suffix == ".json":
            swapped = True
            token_dir.rename(held)
            token_dir.symlink_to(outside, target_is_directory=True)
            relocated_source = held / Path(source).name
            return original_replace(relocated_source, destination, *args, **kwargs)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(video_preflight.os, "replace", swap_on_replace)

    with pytest.raises(ValueError, match="identity|symlink"):
        issue_generation_token(project_dir, preflight)
    assert not list(outside.iterdir())


def test_project_parent_swap_fails_closed(tmp_path: Path, monkeypatch) -> None:
    project_dir = _ready_video_project(tmp_path)
    held = tmp_path / "project-held"
    outside = tmp_path / "outside-project"
    shutil.copytree(project_dir, outside)
    original = video_preflight._safe_existing_file
    swapped = False

    def swap_after_check(path: Path, label: str, *args, **kwargs):
        nonlocal swapped
        if swapped:
            return path
        source = original(path, label, *args, **kwargs)
        if not swapped and label == "project spec":
            swapped = True
            project_dir.rename(held)
            project_dir.symlink_to(outside, target_is_directory=True)
        return source

    monkeypatch.setattr(video_preflight, "_safe_existing_file", swap_after_check)

    with pytest.raises(ValueError, match="identity|symlink"):
        build_video_preflight(project_dir, ("shot_03",))


def test_project_swap_out_read_swap_back_cannot_substitute_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    held = tmp_path / "project-held"
    attacker = tmp_path / "attacker-project"
    shutil.copytree(project_dir, attacker)
    spec_path = attacker / "project.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["providers"]["video_model"] = "attacker-model"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    original = video_preflight._read_bytes_secure

    def swap_for_project_read(path: Path, label: str, *args, **kwargs):
        if label != "project spec":
            return original(path, label, *args, **kwargs)
        project_dir.rename(held)
        attacker.rename(project_dir)
        try:
            return original(path, label, *args, **kwargs)
        finally:
            project_dir.rename(attacker)
            held.rename(project_dir)

    monkeypatch.setattr(video_preflight, "_read_bytes_secure", swap_for_project_read)

    preflight = build_video_preflight(project_dir, ("shot_03",))

    assert preflight.model == "MiniMax-H3"


def test_token_swap_out_write_swap_back_stays_on_original_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = _ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    issue_generation_token(project_dir, preflight)
    token_dir = project_dir / "runs/.workbench/tokens"
    held = project_dir / "runs/.workbench/tokens-held"
    attacker = tmp_path / "attacker-tokens"
    shutil.copytree(token_dir, attacker)
    original = video_preflight._write_token_atomic

    def swap_for_token_write(path, payload, *args, **kwargs):
        token_dir.rename(held)
        attacker.rename(token_dir)
        try:
            return original(path, payload, *args, **kwargs)
        finally:
            token_dir.rename(attacker)
            held.rename(token_dir)

    monkeypatch.setattr(video_preflight, "_write_token_atomic", swap_for_token_write)

    token = issue_generation_token(project_dir, preflight)
    token_id = token.split(".", 1)[0]

    assert (token_dir / f"{token_id}.json").is_file()
    assert not (attacker / f"{token_id}.json").exists()


def test_macos_var_alias_supports_project_preflight_and_tokens(tmp_path: Path) -> None:
    private_var = Path("/private/var")
    if not Path("/var").is_symlink() or not tmp_path.is_relative_to(private_var):
        pytest.skip("macOS /var system alias is unavailable")
    project_dir = _ready_video_project(tmp_path)
    alias = Path("/var") / project_dir.relative_to(private_var)

    preflight = build_video_preflight(alias, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(alias, preflight)
    consume_generation_token(alias, token, request)

    assert preflight.ready is True
