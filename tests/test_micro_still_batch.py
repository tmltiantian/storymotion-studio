from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from factory.gateway_image import GatewayImageConfig, GatewayImageResult
from factory.micro_still_batch import (
    MicroStillBatchError,
    build_micro_still_jobs,
    render_micro_still_batch,
)
from factory.prompt_safety import PREVIOUS_SHOT_CONTINUITY
from factory.schema import Character, Episode, Shot
from factory.visual_timeline import MicroShot, VisualTimeline


PNG = b"\x89PNG\r\n\x1a\nvalid"
JPEG = b"\xff\xd8\xffvalid"


@pytest.fixture
def sample_episode() -> Episode:
    return Episode(
        project_id="sample_episode",
        title="Sample episode",
        language="en-US",
        style="motion comic",
        target_aspect_ratio="9:16",
        target_resolution="1080x1920",
        characters=[Character("char_1", "Lin", "lead", "guarded", "coat", "low")],
        shots=[
            Shot(
                "shot_001",
                1,
                "Shop",
                "Lin reaches toward the envelope on the counter in the shop.",
                "Shop counter and envelope.",
                "static",
                6.0,
                "tense",
            ),
            Shot(
                "shot_002",
                2,
                "Second shot",
                "The envelope remains still.",
                "The envelope remains still.",
                "static",
                3.0,
                "tense",
            ),
        ],
    )


def _shot(
    shot_id: str,
    index: int,
    *,
    character_ids: tuple[str, ...] = (),
    scene_context: str = "Shop",
    purpose: str = "object",
    camera_mode: str = "object_insert",
    parent_shot_id: str = "shot_001",
) -> MicroShot:
    return MicroShot(
        id=shot_id,
        index=index,
        parent_shot_id=parent_shot_id,
        scene_context=scene_context,
        time_context="source-unspecified",
        purpose=purpose,
        character_ids=character_ids,
        emotion_start="still",
        emotion_end="still",
        emotion_intensity=1,
        gaze="at the envelope",
        pose_start="on the counter",
        pose_end="on the counter",
        action_actor_id="char_1" if character_ids else "object",
        action_code="reach" if character_ids else "hold_still",
        action_target="envelope",
        camera_mode=camera_mode,
        source_duration_seconds=3,
        timeline_duration_seconds=3.0,
        entry_cut="hard_cut",
        exit_cut="hard_cut",
        negative_constraints=("no_rain",),
        cadence_fps=8,
    )


@pytest.fixture
def visual_timeline() -> VisualTimeline:
    return VisualTimeline(
        project_id="sample_episode",
        micro_shots=(
            _shot(
                "micro_001",
                1,
                character_ids=("char_1",),
                purpose="action",
                camera_mode="locked",
            ),
            _shot("micro_002", 2),
            _shot(
                "micro_003",
                3,
                scene_context=PREVIOUS_SHOT_CONTINUITY,
                purpose="establishing",
                camera_mode="locked",
                parent_shot_id="shot_002",
            ),
        ),
    )


class FakeImageClient:
    def __init__(
        self, config: GatewayImageConfig, calls: list[dict], image: bytes = PNG
    ):
        self.config = config
        self.calls = calls
        self.image = image

    def generate(self, prompt, output_path, **kwargs):
        self.calls.append(
            {
                "model": self.config.model,
                "prompt": prompt,
                "output_path": str(output_path),
                **kwargs,
            }
        )
        descriptor = kwargs.get("output_file_descriptor")
        if descriptor is None:
            Path(output_path).write_bytes(self.image)
        else:
            os.write(descriptor, self.image)
        return GatewayImageResult(
            output_path=str(output_path),
            model=self.config.model,
            size=kwargs["size"],
            duration_seconds=0.01,
            response_format="url",
        )


def _config(model: str = "ignored") -> GatewayImageConfig:
    return GatewayImageConfig("top-secret", "https://gateway.example/v1", model)


def test_build_still_jobs_accepts_only_character_free_inserts(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="doubao-seedream-4-5",
        run_dir=tmp_path,
        candidate_number=1,
    )

    assert [job.micro_shot_id for job in jobs] == ["micro_002", "micro_003"]
    assert all(not job.character_ids for job in jobs)
    assert jobs[1].output_path.endswith(
        "micro_stills/micro_003/doubao-seedream-4-5/candidate_001.png"
    )
    assert jobs[0].report_path == str(tmp_path / "micro_still_batch.json")
    assert all(job.size == "1440x2560" for job in jobs)


def test_build_still_jobs_uses_model_specific_gpt_image_size(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
    )

    assert all(job.size == "1024x1536" for job in jobs)


def test_build_still_jobs_rejects_explicit_character_or_non_routable_shot(
    sample_episode, visual_timeline, tmp_path
):
    with pytest.raises(MicroStillBatchError, match="character reference"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
            micro_shot_ids=["micro_001"],
        )

    non_routable = replace(
        visual_timeline.micro_shots[1], camera_mode="locked", purpose="action"
    )
    timeline = replace(
        visual_timeline,
        micro_shots=(
            visual_timeline.micro_shots[0],
            non_routable,
            visual_timeline.micro_shots[2],
        ),
    )
    with pytest.raises(MicroStillBatchError, match="not eligible"):
        build_micro_still_jobs(
            sample_episode,
            timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
            micro_shot_ids=["micro_002"],
        )


@pytest.mark.parametrize("model", ["", "qwen-image-2", "doubao-seedream-4-5-preview"])
def test_build_still_jobs_rejects_unapproved_models(
    sample_episode, visual_timeline, tmp_path, model
):
    with pytest.raises(MicroStillBatchError):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model=model,
            run_dir=tmp_path,
            candidate_number=1,
        )


@pytest.mark.parametrize("candidate", [0, 4])
def test_build_still_jobs_rejects_candidate_limits(
    sample_episode, visual_timeline, tmp_path, candidate
):
    with pytest.raises(MicroStillBatchError, match="at most 3"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=candidate,
        )


@pytest.mark.parametrize("candidate", [True, False, 1.0, "1", None])
def test_build_still_jobs_requires_an_actual_integer_candidate_number(
    sample_episode, visual_timeline, tmp_path, candidate
):
    with pytest.raises(MicroStillBatchError, match="at most 3"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=candidate,
        )


def test_build_still_jobs_rejects_duplicate_explicit_micro_shot_ids(
    sample_episode, visual_timeline, tmp_path
):
    with pytest.raises(MicroStillBatchError, match="duplicate"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
            micro_shot_ids=["micro_002", "micro_002"],
        )


def test_build_still_jobs_validates_entire_timeline_before_routing(
    sample_episode, visual_timeline, tmp_path
):
    invalid = replace(visual_timeline.micro_shots[0], cadence_fps=99)
    timeline = replace(
        visual_timeline, micro_shots=(invalid, *visual_timeline.micro_shots[1:])
    )

    with pytest.raises(MicroStillBatchError, match="Visual timeline is invalid"):
        build_micro_still_jobs(
            sample_episode,
            timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
        )


def test_build_still_jobs_resolves_previous_context_before_compilation(
    sample_episode, visual_timeline, tmp_path
):
    job = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
        micro_shot_ids=["micro_003"],
    )[0]

    assert "Scene: Shop" in job.prompt
    assert PREVIOUS_SHOT_CONTINUITY not in job.prompt


def test_build_still_jobs_rejects_path_traversal_and_symlink_escape(
    sample_episode, visual_timeline, tmp_path
):
    traversal = replace(visual_timeline.micro_shots[1], id="../escape")
    with pytest.raises(MicroStillBatchError, match="safe path component"):
        build_micro_still_jobs(
            sample_episode,
            replace(
                visual_timeline,
                micro_shots=(
                    visual_timeline.micro_shots[0],
                    traversal,
                    visual_timeline.micro_shots[2],
                ),
            ),
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
        )

    outside = tmp_path.parent / "outside"
    outside.mkdir()
    (tmp_path / "micro_stills").symlink_to(outside, target_is_directory=True)
    with pytest.raises(MicroStillBatchError, match="escapes run directory"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
        )


def test_no_network_plan_writes_atomic_report_without_client_calls(
    sample_episode, visual_timeline, tmp_path
):
    calls: list[dict] = []
    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        client_factory=lambda config: FakeImageClient(config, calls),
    )

    assert calls == []
    assert report["planned_count"] == 2
    assert report["blocked_count"] == 2
    assert report["completed_count"] == 0
    assert json.loads((tmp_path / "micro_still_batch.json").read_text()) == report


@pytest.mark.parametrize("config", [None, _config()])
@pytest.mark.parametrize("allow_network", [False, True])
def test_empty_explicit_selection_is_a_successful_live_noop(
    sample_episode, visual_timeline, tmp_path, config, allow_network
):
    calls: list[dict] = []
    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        micro_shot_ids=[],
        config=config,
        allow_network=allow_network,
        client_factory=lambda client_config: FakeImageClient(client_config, calls),
    )

    assert report["success"] is True
    assert report["executed"] is False
    assert all(
        report[f"{name}_count"] == 0
        for name in ("planned", "completed", "failed", "blocked")
    )
    assert calls == []
    assert json.loads((tmp_path / "micro_still_batch.json").read_text()) == report


def test_live_generation_uses_no_references_and_job_model_config(
    sample_episode, visual_timeline, tmp_path
):
    calls: list[dict] = []
    configs: list[GatewayImageConfig] = []
    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="doubao-seedream-4-5",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        client_factory=lambda config: (
            configs.append(config) or FakeImageClient(config, calls)
        ),
    )

    assert report["completed_count"] == 2
    assert [config.model for config in configs] == ["doubao-seedream-4-5"] * 2
    assert all(call["n"] == 1 and call["size"] == "1440x2560" for call in calls)
    assert all(
        call["ref_image_path"] is None and call["ref_image_paths"] is None
        for call in calls
    )
    assert all(Path(call["output_path"]).name != "candidate_001.png" for call in calls)


def test_live_generation_creates_a_missing_run_directory(
    sample_episode, visual_timeline, tmp_path
):
    run_dir = tmp_path / "new" / "run"
    calls: list[dict] = []

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=run_dir,
        config=_config(),
        allow_network=True,
        client_factory=lambda config: FakeImageClient(config, calls),
    )

    assert report["completed_count"] == 2
    assert run_dir.is_dir()
    assert len(calls) == 2


def test_live_generation_rejects_directory_swap_before_atomic_install(
    sample_episode, visual_timeline, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    calls: list[dict] = []

    class SwappingClient(FakeImageClient):
        def generate(self, prompt, output_path, **kwargs):
            result = super().generate(prompt, output_path, **kwargs)
            model_directory = run_dir / "micro_stills" / "micro_002" / "gpt-image-2"
            temporary = model_directory / Path(output_path).name
            temporary.unlink()
            model_directory.rmdir()
            model_directory.symlink_to(outside, target_is_directory=True)
            (outside / temporary.name).write_bytes(PNG)
            return result

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=run_dir,
        micro_shot_ids=["micro_002"],
        config=_config(),
        allow_network=True,
        client_factory=lambda config: SwappingClient(config, calls),
    )

    assert report["failed_count"] == 1
    assert not (outside / "candidate_001.png").exists()


def test_report_redacts_common_api_key_spellings_in_memory_and_on_disk(
    sample_episode, visual_timeline, tmp_path
):
    calls: list[dict] = []

    class SecretFailingClient(FakeImageClient):
        def generate(self, prompt, output_path, **kwargs):
            raise RuntimeError(
                "{'Authorization': 'Bearer auth-secret'} "
                '{"X-Api-Key": "header-secret"} '
                '{"api_key":"json-secret"} '
                '{"b64_json": "aW1hZ2UtYnl0ZXM="} '
                "X-Api-Key: upstream-secret api_key=second-secret api key: third-secret"
            )

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        micro_shot_ids=["micro_002"],
        config=_config(),
        allow_network=True,
        client_factory=lambda config: SecretFailingClient(config, calls),
    )

    persisted = json.loads((tmp_path / "micro_still_batch.json").read_text())
    for payload in (report, persisted):
        serialized = json.dumps(payload)
        assert "top-secret" not in serialized
        assert "upstream-secret" not in serialized
        assert "second-secret" not in serialized
        assert "third-secret" not in serialized
        assert "auth-secret" not in serialized
        assert "header-secret" not in serialized
        assert "json-secret" not in serialized
        assert "aW1hZ2UtYnl0ZXM=" not in serialized


@pytest.mark.parametrize(
    ("header", "token"),
    [
        ("Authorization: Bearer auth-secret", "auth-secret"),
        ("authorization=Bearer auth-secret", "auth-secret"),
        ("{'Authorization':'Bearer auth-secret'}", "auth-secret"),
        ('{"Authorization":"Bearer auth-secret"}', "auth-secret"),
        ("X-Api-Key: Bearer x-api-secret", "x-api-secret"),
        ("x-api-key = Bearer x-api-secret", "x-api-secret"),
    ],
)
def test_report_redacts_complete_bearer_credentials_in_memory_and_on_disk(
    sample_episode, visual_timeline, tmp_path, header, token
):
    class SecretFailingClient(FakeImageClient):
        def generate(self, prompt, output_path, **kwargs):
            raise RuntimeError(header)

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        micro_shot_ids=["micro_002"],
        config=_config(),
        allow_network=True,
        client_factory=lambda config: SecretFailingClient(config, []),
    )

    persisted = json.loads((tmp_path / "micro_still_batch.json").read_text())
    for payload in (report, persisted):
        serialized = json.dumps(payload)
        assert "Bearer" not in serialized
        assert token not in serialized


def test_live_generation_validates_signature_and_replaces_atomically(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
    )
    output = Path(jobs[0].output_path)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"old")
    calls: list[dict] = []

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        overwrite=True,
        client_factory=lambda config: FakeImageClient(config, calls, JPEG),
    )

    assert report["completed_count"] == 2
    assert output.read_bytes() == JPEG
    assert not list(output.parent.glob(".*.tmp*"))


def test_live_generation_rejects_invalid_signature_without_replacing_final(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
    )
    output = Path(jobs[0].output_path)
    output.parent.mkdir(parents=True)
    output.write_bytes(PNG)
    calls: list[dict] = []

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        overwrite=True,
        client_factory=lambda config: FakeImageClient(config, calls, b"not-an-image"),
    )

    assert report["failed_count"] == 1
    assert output.read_bytes() == PNG
    assert not list(output.parent.glob(".*.tmp*"))


def test_resume_rejects_invalid_existing_unless_overwrite_and_cleans_failed_temp(
    sample_episode, visual_timeline, tmp_path
):
    cwd_temporaries = {
        path.name for path in Path.cwd().glob(".candidate_001.png.*.tmp")
    }
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
    )
    output = Path(jobs[0].output_path)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"not-an-image")
    calls: list[dict] = []
    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        client_factory=lambda config: FakeImageClient(config, calls),
    )
    assert report["failed_count"] == 1
    assert calls == []

    class FailingClient(FakeImageClient):
        def generate(self, prompt, output_path, **kwargs):
            os.write(kwargs["output_file_descriptor"], b"bad")
            raise RuntimeError(
                "https://cdn.example/file?signature=private top-secret "
                "data:image/png;base64,cHJpdmF0ZQ=="
            )

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        overwrite=True,
        client_factory=lambda config: FailingClient(config, calls),
    )
    assert report["failed_count"] == 1
    assert output.read_bytes() == b"not-an-image"
    assert not list(output.parent.glob(".*.tmp*"))
    serialized = json.dumps(report)
    assert "top-secret" not in serialized
    assert "https://" not in serialized
    assert "signature=private" not in serialized
    assert "data:image" not in serialized
    assert "cHJpdmF0ZQ==" not in serialized
    assert {
        path.name for path in Path.cwd().glob(".candidate_001.png.*.tmp")
    } == cwd_temporaries


def test_resume_skips_valid_existing_candidate(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        candidate_number=1,
    )
    for job in jobs:
        output = Path(job.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PNG)
    calls: list[dict] = []

    report = render_micro_still_batch(
        sample_episode,
        visual_timeline,
        model="gpt-image-2",
        run_dir=tmp_path,
        config=_config(),
        allow_network=True,
        client_factory=lambda config: FakeImageClient(config, calls),
    )

    assert report["skipped_count"] == 2
    assert report["completed_count"] == 0
    assert calls == []
