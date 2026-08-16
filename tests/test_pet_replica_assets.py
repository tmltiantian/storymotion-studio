from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image

import factory.pet_replica_assets as replica_assets
from factory.gateway_image import GatewayImageConfig, GatewayImageResult
from factory.pet_replica import build_pet_replica_plan
from factory.pet_replica_assets import (
    APPROVED_CAT_REFERENCE_ROOT,
    PetReplicaAssetError,
    generate_replica_assets,
    load_approved_replica_assets,
    prepare_replica_asset_jobs,
    write_replica_asset_review_template,
)


@pytest.fixture(autouse=True)
def _explicit_gateway_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("GATEWAY_BASE_URL", "https://gateway.example.invalid")


def test_replica_assets_require_an_explicit_gateway_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)

    with pytest.raises(PetReplicaAssetError, match="GATEWAY_BASE_URL"):
        replica_assets._configured_gateway_base_url()


def _write_image(
    path: Path,
    *,
    size: tuple[int, int] = (1440, 2560),
    color: str = "teal",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _plan(tmp_path: Path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    return build_pet_replica_plan(source, tmp_path / "output")


def _approved_cats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "approved-pet-output" / "assets" / "characters"
    monkeypatch.setattr("factory.pet_replica_assets.APPROVED_CAT_REFERENCE_ROOT", root)
    assert APPROVED_CAT_REFERENCE_ROOT != root
    return (
        _write_image(root / "奶糖_reference.png"),
        _write_image(root / "豆包_reference.png"),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_sha256(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sample_timestamps(
    start_s: float,
    end_s: float,
    count: int,
    fps: int,
    last_video_frame_s: float,
) -> tuple[float, ...]:
    final = min(max(start_s, end_s - 1 / fps), last_video_frame_s)
    increment = (final - start_s) / (count - 1)
    return tuple(start_s + index * increment for index in range(count))


def _shot_id_at(plan, timestamp_s: float) -> str:
    for shot in plan.shots:
        if shot.start_s <= timestamp_s < shot.end_s:
            return shot.shot_id
    return plan.shots[-1].shot_id


def _write_evidence_manifest(plan) -> tuple[Path, Path]:
    source_sha256 = _sha256(plan.source_video)
    last_video_frame_s = 77.133333
    frame_specs: list[tuple[Path, str, str, float]] = []
    for shot in plan.shots:
        timestamps = {
            "start": shot.start_s,
            "middle": (shot.start_s + shot.end_s) / 2,
            "end": min(
                max(shot.start_s, shot.end_s - 1 / plan.fps),
                last_video_frame_s,
            ),
        }
        for label, timestamp_s in timestamps.items():
            frame_specs.append(
                (
                    plan.output_root
                    / "reference"
                    / "shots"
                    / shot.shot_id
                    / f"{label}.jpg",
                    shot.shot_id,
                    label,
                    timestamp_s,
                )
            )
    for prefix, count, end_s in (
        ("pilot", 12, plan.pilot_end_s),
        ("full_01", 40, plan.duration_s),
    ):
        for index, timestamp_s in enumerate(
            _sample_timestamps(
                0.0, end_s, count, plan.fps, last_video_frame_s
            ),
            start=1,
        ):
            frame_specs.append(
                (
                    plan.output_root
                    / "reference"
                    / "contact_sheets"
                    / f"{prefix}_frames"
                    / f"{index:03d}.jpg",
                    _shot_id_at(plan, timestamp_s),
                    f"{prefix}_{index:03d}",
                    timestamp_s,
                )
            )

    frame = _write_image(frame_specs[0][0], color="red")
    tiny = _write_image(frame_specs[1][0], size=(2, 2), color="gray").read_bytes()
    for path, _shot_id, _label, _timestamp_s in frame_specs[2:]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tiny)
    frames = [
        {
            "image_path": path.relative_to(plan.output_root).as_posix(),
            "image_sha256": _sha256(path),
            "source_sha256": source_sha256,
            "shot_id": shot_id,
            "label": label,
            "timestamp_s": timestamp_s,
            "command": "fixture",
        }
        for path, shot_id, label, timestamp_s in frame_specs
    ]

    contact_sheet = _write_image(
        plan.output_root / "reference" / "contact_sheets" / "pilot_4x3.jpg",
        color="blue",
    )
    full_sheet = _write_image(
        plan.output_root / "reference" / "contact_sheets" / "full_01_5x8.jpg",
        size=(2, 2),
        color="navy",
    )
    manifest = plan.output_root / "reference" / "evidence_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "motion-comic-factory.pet-replica-reference.v1",
                "source_sha256": source_sha256,
                "last_video_frame_s": last_video_frame_s,
                "frames": frames,
                "contact_sheets": [
                    {
                        "image_path": "reference/contact_sheets/pilot_4x3.jpg",
                        "image_sha256": _sha256(contact_sheet),
                        "source_sha256": source_sha256,
                        "layout": "4x3",
                    },
                    {
                        "image_path": "reference/contact_sheets/full_01_5x8.jpg",
                        "image_sha256": _sha256(full_sheet),
                        "source_sha256": source_sha256,
                        "layout": "5x8",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return frame, contact_sheet


def _master_inputs(plan) -> tuple[Path, Path]:
    _write_evidence_manifest(plan)
    return (
        _write_image(plan.output_root / "master_inputs" / "woman.png", color="teal"),
        _write_image(plan.output_root / "master_inputs" / "scene.png", color="purple"),
    )


def _master_kwargs(plan) -> dict[str, Path]:
    woman, scene = _master_inputs(plan)
    return {
        "woman_master_reference": woman,
        "scene_master_reference": scene,
    }


class _FakeImageClient:
    def __init__(self, config: GatewayImageConfig, calls: list[dict[str, object]]):
        self.config = config
        self.calls = calls

    def generate(self, prompt, output_path, **kwargs):
        reference = kwargs.get("ref_image_path")
        self.calls.append(
            {
                "model": self.config.model,
                "prompt": prompt,
                "output_path": str(output_path),
                "reference_sha256": (
                    hashlib.sha256(Path(reference).read_bytes()).hexdigest()
                    if reference
                    else None
                ),
                **kwargs,
            }
        )
        _write_image(Path(output_path))
        return GatewayImageResult(
            output_path=str(output_path),
            model=self.config.model,
            size=kwargs["size"],
            duration_seconds=0.01,
            response_format="url",
        )


def test_asset_jobs_generate_woman_and_scenes_but_reuse_approved_cats(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)

    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)

    assert [job.asset_id for job in jobs] == [
        "woman_front",
        "woman_left_three_quarter",
        "woman_right_three_quarter",
        "woman_half_body",
        "woman_full_body",
        "scene_sofa",
        "scene_table",
        "scene_phone",
    ]
    assert all(job.model == "doubao-seedream-4-5" for job in jobs)
    assert all(job.size == "1440x2560" for job in jobs)
    assert (plan.output_root / "assets" / "characters" / "奶糖_reference.png").read_bytes() == naitang.read_bytes()
    assert (plan.output_root / "assets" / "characters" / "豆包_reference.png").read_bytes() == doubao.read_bytes()
    assert "original East Asian young adult woman" in jobs[0].prompt
    assert "must not resemble the source-video woman" in jobs[0].prompt
    assert "preserve the supplied project-original woman identity master" in jobs[0].prompt
    assert "do not use any person reference image" not in jobs[0].prompt
    assert "watermark" in jobs[0].prompt
    assert "bright modern living room" in jobs[-1].prompt


def test_woman_prompts_lock_photorealistic_live_action_style(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    woman_jobs = jobs[:5]
    required_photographic_tokens = (
        "photorealistic live-action editorial photograph",
        "real adult human",
        "natural skin pores and fine facial texture",
        "realistic individual hair strands",
        "subtle everyday makeup",
        "physically plausible lens, depth of field, and daylight",
    )
    forbidden_style_tokens = (
        "illustration",
        "anime",
        "cartoon",
        "3D render",
        "CGI",
        "doll",
        "game character",
        "plastic or waxy skin",
        "painterly style",
    )

    assert all(
        all(token in job.prompt for token in required_photographic_tokens)
        for job in woman_jobs
    )
    assert all(
        all(token in job.negative_prompt for token in forbidden_style_tokens)
        for job in woman_jobs
    )
    assert all(
        "preserve the supplied project-original woman identity master" in job.prompt
        and "do not use the source-video woman or any extracted source frame" in job.prompt
        and "do not use any person reference image" not in job.prompt
        for job in woman_jobs
    )


def test_all_jobs_lock_one_edge_to_edge_camera_view(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    exact_single_image_contract = (
        "Generate exactly one edge-to-edge, full-frame, single-camera continuous "
        "photograph. Show one camera view only."
    )
    forbidden_layout_tokens = (
        "collage",
        "montage",
        "contact sheet",
        "split screen",
        "diptych",
        "triptych",
        "multi-panel",
        "grid",
        "storyboard",
        "screenshot",
        "app/social-media interface",
        "overlay",
        "border",
    )

    assert all(exact_single_image_contract in job.prompt for job in jobs)
    assert all(
        all(token in job.negative_prompt for token in forbidden_layout_tokens)
        for job in jobs
    )


def test_woman_prompts_forbid_ui_and_lock_full_body_camera(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    woman_jobs = jobs[:5]
    exact_woman_frame_contract = (
        "The frame may contain only the real woman and a plain realistic indoor "
        "background. Absolutely no text, letters, numbers, Chinese characters, "
        "glyphs, icons, logos, menus, buttons, search bar, status bar, navigation "
        "bar, app dock, watermark, label, tag, or UI overlay."
    )
    full_body_job = jobs[4]
    exact_full_body_contract = (
        "Camera at natural waist height. Entire head and hair through both shoes and "
        "feet are visible with margin on all sides. Use a neutral eye-level "
        "perspective. No overhead, fisheye, or extreme perspective. No crop at head, "
        "hands, knees, or feet."
    )
    full_body_negative = (
        "No overhead view, fisheye lens, extreme perspective, or crop at head, hands, "
        "knees, or feet."
    )

    assert all(exact_woman_frame_contract in job.prompt for job in woman_jobs)
    assert all(exact_woman_frame_contract in job.negative_prompt for job in woman_jobs)
    assert exact_full_body_contract in full_body_job.prompt
    assert full_body_negative in full_body_job.negative_prompt


def test_scene_prompts_lock_one_real_apartment_and_empty_phone_position(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    scene_jobs = jobs[5:]
    fixed_scene_tokens = (
        "one pre-existing real photographed apartment",
        "2.6m beige modular sofa centered against the back wall",
        "1.1m x 0.6m light-oak table centered 0.8m in front of the sofa",
        "open kitchen fixed behind-left",
        "floor-to-ceiling window fixed on the right",
        "Do not show any other camera angle in this image",
    )
    forbidden_scene_drift = (
        "changed furniture count",
        "changed furniture style",
        "changed furniture position",
    )
    phone_job = scene_jobs[-1]
    phone_prompt = phone_job.prompt
    exact_negative_prohibition = (
        "Absolutely no phone, tripod, hand, arm, human, cat, screen, device, or UI "
        "in the image."
    )

    assert all(
        all(token in job.prompt for token in fixed_scene_tokens)
        for job in scene_jobs
    )
    assert all("across all three views" not in job.prompt for job in scene_jobs)
    assert "front-center camera position at 1.2m height with a 24mm lens" in scene_jobs[0].prompt
    assert "front-right camera position at 0.9m height with a 35mm lens" in scene_jobs[1].prompt
    assert "sofa-left camera position at 1.3m height with a 35mm lens" in scene_jobs[2].prompt
    assert all(
        all(token in job.negative_prompt for token in forbidden_scene_drift)
        for job in scene_jobs
    )
    assert "empty camera position where a phone tripod will later stand" in phone_prompt
    assert (
        "absolutely no phone, tripod, hand, arm, human, cat, screen, device, or UI"
        in phone_prompt
    )
    assert phone_job.negative_prompt.endswith(exact_negative_prohibition)


def test_prepare_rejects_unsafe_cat_inputs(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)

    with pytest.raises(PetReplicaAssetError, match="approved pet output"):
        prepare_replica_asset_jobs(plan, tmp_path / "outside.png", doubao)

    unsupported = naitang.with_suffix(".gif")
    unsupported.write_bytes(naitang.read_bytes())
    with pytest.raises(PetReplicaAssetError, match="unsupported"):
        prepare_replica_asset_jobs(plan, unsupported, doubao)

    symlink = naitang.with_name("linked.png")
    os.symlink(naitang, symlink)
    with pytest.raises(PetReplicaAssetError, match="symlinks"):
        prepare_replica_asset_jobs(plan, symlink, doubao)

    alpha_only = naitang.with_name("alpha_only.png")
    Image.new("RGBA", (1440, 2560), (0, 0, 0, 100)).save(alpha_only, format="PNG")
    with pytest.raises(PetReplicaAssetError, match="alpha-only"):
        prepare_replica_asset_jobs(plan, alpha_only, doubao)


def test_generate_is_network_gated_and_live_outputs_are_bound(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    called = False

    def forbidden_factory(_config):
        nonlocal called
        called = True
        raise AssertionError("network client must not be created")

    blocked = generate_replica_assets(
        plan, jobs, client_factory=forbidden_factory, enable_live=False
    )
    assert called is False
    assert len(blocked.assets) == 2
    assert not (plan.output_root / "assets" / "characters" / "woman_front.png").exists()

    calls: list[dict[str, object]] = []
    master_kwargs = _master_kwargs(plan)
    manifest = generate_replica_assets(
        plan,
        jobs,
        client_factory=lambda config: _FakeImageClient(config, calls),
        enable_live=True,
        **master_kwargs,
    )

    assert len(calls) == len(jobs)
    assert len(manifest.assets) == 12
    assert all(call["model"] == "doubao-seedream-4-5" for call in calls)
    assert all(call["size"] == "1440x2560" for call in calls)
    woman_hash = hashlib.sha256(
        master_kwargs["woman_master_reference"].read_bytes()
    ).hexdigest()
    scene_hash = hashlib.sha256(
        master_kwargs["scene_master_reference"].read_bytes()
    ).hexdigest()
    assert [call["reference_sha256"] for call in calls] == [
        *([woman_hash] * 5),
        *([scene_hash] * 3),
    ]
    assert all(call["ref_image_path"] for call in calls)
    assert [asset.asset_id for asset in manifest.assets[2:4]] == [
        "woman_master",
        "scene_master",
    ]
    assert [asset.reference_asset_id for asset in manifest.assets[4:]] == [
        *(["woman_master"] * 5),
        *(["scene_master"] * 3),
    ]
    assert all(asset.path.is_file() for asset in manifest.assets)
    serialized_manifest = manifest.manifest_path.read_text(encoding="utf-8")
    payload = json.loads(serialized_manifest)
    assert "output_root" not in payload
    assert str(plan.output_root) not in serialized_manifest
    assert payload["assets"][2]["source_id"] == "master_inputs/woman.png"
    assert payload["assets"][3]["source_id"] == "master_inputs/scene.png"
    assert payload["assets"][4]["reference_asset_id"] == "woman_master"
    assert payload["assets"][4]["reference_path"] == "assets/masters/woman_master.png"
    assert payload["assets"][-1]["reference_asset_id"] == "scene_master"
    assert payload["assets"][-1]["reference_path"] == "assets/masters/scene_master.png"
    assert payload["evidence_manifest_sha256"] == _canonical_json_sha256(
        plan.output_root / "reference" / "evidence_manifest.json"
    )
    assert payload["evidence_frame_count"] == 163
    assert payload["evidence_contact_sheet_count"] == 2
    assert all(
        not Path(item["source_id"]).is_absolute()
        for item in payload["assets"]
        if item["source_id"] is not None
    )


def test_live_generation_requires_both_master_references(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    woman, _scene = _master_inputs(plan)

    with pytest.raises(PetReplicaAssetError, match="master references are required"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
        )
    with pytest.raises(PetReplicaAssetError, match="master references are required"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            woman_master_reference=woman,
        )


def test_live_generation_rejects_unsafe_or_source_frame_masters(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    woman, scene = _master_inputs(plan)
    outside = _write_image(tmp_path / "outside.png")

    with pytest.raises(PetReplicaAssetError, match="output root"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            woman_master_reference=outside,
            scene_master_reference=scene,
        )

    linked = plan.output_root / "master_inputs" / "linked.png"
    os.symlink(woman, linked)
    with pytest.raises(PetReplicaAssetError, match="symlinks"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            woman_master_reference=linked,
            scene_master_reference=scene,
        )

    source_frame = _write_image(plan.output_root / "reference" / "frames" / "woman.png")
    with pytest.raises(PetReplicaAssetError, match="source frame"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            woman_master_reference=source_frame,
            scene_master_reference=scene,
        )

    wrong_size = _write_image(
        plan.output_root / "master_inputs" / "wrong.png", size=(720, 1280)
    )
    with pytest.raises(PetReplicaAssetError, match="dimensions"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            woman_master_reference=wrong_size,
            scene_master_reference=scene,
        )


def test_live_generation_rejects_copied_and_renamed_evidence_frame(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    evidence_frame, contact = _write_evidence_manifest(plan)
    copied = plan.output_root / "master_inputs" / "renamed_original.png"
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(evidence_frame.read_bytes())
    scene = _write_image(
        plan.output_root / "master_inputs" / "scene.png", color="purple"
    )
    calls: list[dict[str, object]] = []

    with pytest.raises(PetReplicaAssetError, match="trusted source evidence"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, calls),
            enable_live=True,
            woman_master_reference=copied,
            scene_master_reference=scene,
        )

    woman = _write_image(
        plan.output_root / "master_inputs" / "woman.png", color="teal"
    )
    copied.write_bytes(contact.read_bytes())
    with pytest.raises(PetReplicaAssetError, match="trusted source evidence"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, calls),
            enable_live=True,
            woman_master_reference=woman,
            scene_master_reference=copied,
        )

    assert calls == []
    assert not (plan.output_root / "assets" / "masters").exists()


def test_live_generation_fails_closed_on_invalid_evidence_manifest(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    evidence_manifest = plan.output_root / "reference" / "evidence_manifest.json"
    payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))

    payload["schema_version"] = "unexpected"
    evidence_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PetReplicaAssetError, match="invalid schema"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )

    _write_evidence_manifest(plan)
    payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    evidence_manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PetReplicaAssetError, match="source hash"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )

    _write_evidence_manifest(plan)
    evidence_frame = plan.output_root / "reference" / "shots" / "R001" / "start.jpg"
    _write_image(evidence_frame, color="green")
    with pytest.raises(PetReplicaAssetError, match="evidence hash"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )


def test_live_generation_rejects_valid_but_truncated_evidence_manifest(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    woman, scene = _master_inputs(plan)
    evidence_manifest = plan.output_root / "reference" / "evidence_manifest.json"
    payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    omitted = payload["frames"].pop(0)
    evidence_manifest.write_text(json.dumps(payload), encoding="utf-8")
    woman.write_bytes((plan.output_root / omitted["image_path"]).read_bytes())
    calls: list[dict[str, object]] = []

    with pytest.raises(PetReplicaAssetError, match="evidence frame contract"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, calls),
            enable_live=True,
            woman_master_reference=woman,
            scene_master_reference=scene,
        )

    assert calls == []


@pytest.mark.parametrize("mutation", ["duplicate", "unexpected"])
def test_live_generation_rejects_duplicate_or_unexpected_evidence_record(
    tmp_path, monkeypatch, mutation
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    evidence_manifest = plan.output_root / "reference" / "evidence_manifest.json"
    payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        payload["frames"].append(dict(payload["frames"][0]))
    else:
        unexpected = _write_image(
            plan.output_root
            / "reference"
            / "shots"
            / "R001"
            / "unexpected.jpg",
            size=(2, 2),
            color="black",
        )
        payload["frames"][-1] = {
            **payload["frames"][-1],
            "image_path": unexpected.relative_to(plan.output_root).as_posix(),
            "image_sha256": _sha256(unexpected),
            "shot_id": "R001",
            "label": "unexpected",
            "timestamp_s": 0.0,
        }
    evidence_manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetReplicaAssetError, match="evidence frame contract"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )


@pytest.mark.parametrize("mutation", ["replacement", "truncated"])
def test_approved_load_rejects_post_generation_evidence_replacement(
    tmp_path, monkeypatch, mutation
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    generate_replica_assets(
        plan,
        jobs,
        client_factory=lambda config: _FakeImageClient(config, []),
        enable_live=True,
        **_master_kwargs(plan),
    )
    review_template = write_replica_asset_review_template(plan)
    review = json.loads(review_template.read_text(encoding="utf-8"))
    review["manual_review_required"] = False
    review["gates"] = {key: True for key in review["gates"]}
    review_template.with_name("asset_review.json").write_text(
        json.dumps(review), encoding="utf-8"
    )

    evidence_manifest = plan.output_root / "reference" / "evidence_manifest.json"
    evidence = json.loads(evidence_manifest.read_text(encoding="utf-8"))
    if mutation == "replacement":
        evidence["frames"][0]["command"] = "replacement"
        evidence_manifest.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        expected = "evidence manifest binding"
    else:
        evidence["frames"].pop()
        evidence_manifest.write_text(json.dumps(evidence), encoding="utf-8")
        expected = "evidence frame contract"

    with pytest.raises(PetReplicaAssetError, match=expected):
        load_approved_replica_assets(plan)


def test_review_gate_rejects_unreviewed_and_tampered_assets(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    generate_replica_assets(
        plan,
        jobs,
        client_factory=lambda config: _FakeImageClient(config, []),
        enable_live=True,
        **master_kwargs,
    )

    template_path = write_replica_asset_review_template(plan)
    serialized_template = template_path.read_text(encoding="utf-8")
    template = json.loads(serialized_template)
    assert str(plan.output_root) not in serialized_template
    assert template["evidence_manifest_sha256"] == _canonical_json_sha256(
        plan.output_root / "reference" / "evidence_manifest.json"
    )
    assert template["evidence_frame_count"] == 163
    assert template["evidence_contact_sheet_count"] == 2
    assert set(template["gates"]) == {
        "original_woman_identity",
        "woman_identity_consistent",
        "woman_costume_consistent",
        "naitang_identity_match",
        "doubao_identity_match",
        "scene_geometry_match",
        "scene_light_direction_match",
        "no_source_person_identity",
        "no_platform_branding",
        "no_generated_text",
    }
    with pytest.raises(PetReplicaAssetError, match="manual review"):
        load_approved_replica_assets(plan)

    template["manual_review_required"] = False
    template["gates"] = {key: True for key in template["gates"]}
    reviewed = template_path.with_name("asset_review.json")
    reviewed.write_text(json.dumps(template), encoding="utf-8")
    approved = load_approved_replica_assets(plan)
    assert len(approved.assets) == 12

    asset = plan.output_root / "assets" / "characters" / "woman_front.png"
    asset.write_bytes(b"tampered")
    with pytest.raises(PetReplicaAssetError, match="hash"):
        load_approved_replica_assets(plan)


def test_master_tamper_and_reference_path_escape_fail_closed(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    generate_replica_assets(
        plan,
        jobs,
        client_factory=lambda config: _FakeImageClient(config, []),
        enable_live=True,
        **_master_kwargs(plan),
    )
    template_path = write_replica_asset_review_template(plan)
    template = json.loads(template_path.read_text(encoding="utf-8"))
    template["manual_review_required"] = False
    template["gates"] = {key: True for key in template["gates"]}
    review_path = template_path.with_name("asset_review.json")
    review_path.write_text(json.dumps(template), encoding="utf-8")

    master = plan.output_root / "assets" / "masters" / "woman_master.png"
    master.write_bytes(b"tampered")
    with pytest.raises(PetReplicaAssetError, match="hash"):
        load_approved_replica_assets(plan)

    # Restore a clean generation, then prove project-relative references cannot escape.
    master.write_bytes(
        (plan.output_root / "master_inputs" / "woman.png").read_bytes()
    )
    master.unlink()
    os.symlink(plan.output_root / "master_inputs" / "woman.png", master)
    with pytest.raises(PetReplicaAssetError, match="symlinks"):
        load_approved_replica_assets(plan)

    master.unlink()
    master.write_bytes(
        (plan.output_root / "master_inputs" / "woman.png").read_bytes()
    )
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][4]["reference_path"] = "../escape.png"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PetReplicaAssetError, match="manifest is invalid"):
        write_replica_asset_review_template(plan)


def test_generation_rejects_changed_approved_cat_source(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    Image.new("RGB", (1440, 2560), "red").save(naitang, format="PNG")
    with pytest.raises(PetReplicaAssetError, match="source hash"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )


def test_live_generation_failure_leaves_no_partial_generated_assets(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    calls = 0

    class FailingClient(_FakeImageClient):
        def generate(self, prompt, output_path, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("gateway rejected request")
            return super().generate(prompt, output_path, **kwargs)

    with pytest.raises(PetReplicaAssetError, match="generation failed"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: FailingClient(config, []),
            enable_live=True,
            **master_kwargs,
        )

    assert not any(job.output_path.exists() for job in jobs)


def _assert_pre_live_state(plan, jobs, manifest_before, naitang, doubao):
    assert (plan.output_root / "assets" / "asset_manifest.json").read_bytes() == manifest_before
    assert not any(job.output_path.exists() for job in jobs)
    assert (plan.output_root / "assets" / "characters" / "奶糖_reference.png").read_bytes() == naitang.read_bytes()
    assert (plan.output_root / "assets" / "characters" / "豆包_reference.png").read_bytes() == doubao.read_bytes()
    assert not (plan.output_root / "assets" / "scenes").exists()
    assert not (plan.output_root / "assets" / "masters").exists()


def test_second_promotion_failure_rolls_back_all_generated_assets(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    manifest_before = manifest_path.read_bytes()
    real_replace = os.replace
    promotions = 0
    generated_targets = {job.output_path for job in jobs}

    def fail_second_promotion(source, destination):
        nonlocal promotions
        if Path(destination) in generated_targets:
            promotions += 1
            if promotions == 2:
                raise OSError("forced second promotion failure")
        return real_replace(source, destination)

    monkeypatch.setattr(replica_assets.os, "replace", fail_second_promotion)
    with pytest.raises(PetReplicaAssetError, match="generation failed"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )

    _assert_pre_live_state(plan, jobs, manifest_before, naitang, doubao)


def test_final_manifest_failure_rolls_back_all_generated_assets(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    manifest_before = manifest_path.read_bytes()
    original_write_manifest = replica_assets._write_manifest

    def fail_completed_manifest(manifest):
        if manifest.live_generation_enabled:
            original_write_manifest(manifest)
            raise OSError("forced completed manifest failure after write")
        return original_write_manifest(manifest)

    monkeypatch.setattr(replica_assets, "_write_manifest", fail_completed_manifest)
    with pytest.raises(PetReplicaAssetError, match="generation failed"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=True,
            **master_kwargs,
        )

    _assert_pre_live_state(plan, jobs, manifest_before, naitang, doubao)


def test_manifest_and_review_redact_external_cat_paths_and_reject_source_id_tamper(
    tmp_path, monkeypatch
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    serialized = manifest_path.read_text(encoding="utf-8")
    assert "/Users/" not in serialized
    assert str(naitang.parent) not in serialized
    manifest = json.loads(serialized)
    assert manifest["assets"][0]["source_id"] == "奶糖_reference.png"
    assert "source_path" not in manifest["assets"][0]

    manifest["assets"][0]["source_id"] = "../escape.png"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PetReplicaAssetError, match="relative"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=False,
        )

    prepare_replica_asset_jobs(plan, naitang, doubao)
    master_kwargs = _master_kwargs(plan)
    generate_replica_assets(
        plan,
        jobs,
        client_factory=lambda config: _FakeImageClient(config, []),
        enable_live=True,
        **master_kwargs,
    )
    template = json.loads(write_replica_asset_review_template(plan).read_text(encoding="utf-8"))
    review_serialized = json.dumps(template, ensure_ascii=False)
    assert "/Users/" not in review_serialized
    assert str(naitang.parent) not in review_serialized
    assert template["assets"][0]["source_id"] == "奶糖_reference.png"
    template["assets"][0]["source_id"] = "../escape.png"
    template["manual_review_required"] = False
    template["gates"] = {key: True for key in template["gates"]}
    (plan.output_root / "assets" / "asset_review.json").write_text(
        json.dumps(template), encoding="utf-8"
    )
    with pytest.raises(PetReplicaAssetError, match="provenance"):
        load_approved_replica_assets(plan)


def test_manifest_rejects_legacy_or_foreign_output_root_field(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["output_root"] = "/foreign/project"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PetReplicaAssetError, match="output_root"):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=False,
        )


@pytest.mark.parametrize(
    ("source_id", "message"),
    (("/private/escape.png", "relative"), ("linked.png", "symlinks")),
)
def test_manifest_rejects_absolute_or_symlink_cat_source_ids(
    tmp_path, monkeypatch, source_id, message
):
    plan = _plan(tmp_path)
    naitang, doubao = _approved_cats(tmp_path, monkeypatch)
    jobs = prepare_replica_asset_jobs(plan, naitang, doubao)
    if source_id == "linked.png":
        os.symlink(naitang, naitang.with_name(source_id))
    manifest_path = plan.output_root / "assets" / "asset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0]["source_id"] = source_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PetReplicaAssetError, match=message):
        generate_replica_assets(
            plan,
            jobs,
            client_factory=lambda config: _FakeImageClient(config, []),
            enable_live=False,
        )
