from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, UnidentifiedImageError

from .gateway_image import GatewayImageClient, GatewayImageConfig, is_valid_image_file
from .pet_replica import PetReplicaPlan, validate_pet_replica_plan


ASSET_SCHEMA_VERSION = "motion-comic-factory.pet-replica-assets.v1"
ASSET_REVIEW_SCHEMA_VERSION = "motion-comic-factory.pet-replica-assets-review.v1"
REFERENCE_EVIDENCE_SCHEMA_VERSION = "motion-comic-factory.pet-replica-reference.v1"
ASSET_MODEL = "doubao-seedream-4-5"
ASSET_SIZE = "1440x2560"
APPROVED_CAT_REFERENCE_ROOT = (
    Path.home()
    / "Desktop"
    / "宠物短剧样片"
    / "斑鸠来访_20260729_v1"
    / "assets"
    / "characters"
)
_SUPPORTED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_REVIEW_GATES = (
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
)


class PetReplicaAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplicaAssetJob:
    asset_id: str
    kind: str
    output_path: Path
    prompt: str
    negative_prompt: str
    model: str
    size: str

    @property
    def full_prompt(self) -> str:
        return f"{self.prompt} {self.negative_prompt}"


@dataclass(frozen=True)
class ReplicaAssetRecord:
    asset_id: str
    kind: str
    path: Path
    sha256: str
    width: int
    height: int
    provenance: str
    source_path: Path | None
    source_sha256: str | None
    provider: str
    model: str
    prompt: str
    creation_mode: str
    reference_asset_id: str | None = None
    reference_path: Path | None = None
    reference_sha256: str | None = None


@dataclass(frozen=True)
class ReplicaAssetManifest:
    schema_version: str
    output_root: Path
    manifest_path: Path
    source_sha256: str
    assets: tuple[ReplicaAssetRecord, ...]
    jobs: tuple[ReplicaAssetJob, ...]
    live_generation_enabled: bool
    evidence_manifest_sha256: str | None = None
    evidence_frame_count: int | None = None
    evidence_contact_sheet_count: int | None = None


@dataclass(frozen=True)
class _TrustedEvidenceInventory:
    manifest_sha256: str
    frame_count: int
    contact_sheet_count: int
    image_sha256s: frozenset[str]


def prepare_replica_asset_jobs(
    plan: PetReplicaPlan,
    naitang_reference: str | Path,
    doubao_reference: str | Path,
) -> tuple[ReplicaAssetJob, ...]:
    """Install immutable approved cat anchors and plan the paid still jobs."""
    validate_pet_replica_plan(plan)
    root = _output_root(plan)
    cat_records = (
        _copy_approved_cat_reference(
            root,
            asset_id="naitang_reference",
            source=naitang_reference,
            destination=root / "assets" / "characters" / "奶糖_reference.png",
        ),
        _copy_approved_cat_reference(
            root,
            asset_id="doubao_reference",
            source=doubao_reference,
            destination=root / "assets" / "characters" / "豆包_reference.png",
        ),
    )
    jobs = _expected_jobs(plan)
    manifest = ReplicaAssetManifest(
        schema_version=ASSET_SCHEMA_VERSION,
        output_root=root,
        manifest_path=_manifest_path(plan),
        source_sha256=_sha256_required(plan.source_video, "Replica source video"),
        assets=cat_records,
        jobs=jobs,
        live_generation_enabled=False,
    )
    _write_manifest(manifest)
    return jobs


def generate_replica_assets(
    plan: PetReplicaPlan,
    jobs: tuple[ReplicaAssetJob, ...],
    client_factory: Callable[[GatewayImageConfig], GatewayImageClient],
    enable_live: bool,
    *,
    woman_master_reference: str | Path | None = None,
    scene_master_reference: str | Path | None = None,
) -> ReplicaAssetManifest:
    """Create all paid anchors atomically, or leave the project at the plan stage."""
    validate_pet_replica_plan(plan)
    expected_jobs = _expected_jobs(plan)
    if tuple(jobs) != expected_jobs:
        raise PetReplicaAssetError("Replica asset jobs do not match the locked contract.")
    manifest = _load_manifest(plan, require_complete=False)
    _validate_cat_records(plan, manifest.assets)
    if not enable_live:
        if len(manifest.assets) != 2:
            raise PetReplicaAssetError("Live generation is disabled; only a job manifest is allowed.")
        return manifest

    root = _output_root(plan)
    if woman_master_reference is None or scene_master_reference is None:
        raise PetReplicaAssetError(
            "Woman and scene master references are required for live generation."
        )
    source_sha256 = _sha256_required(plan.source_video, "Replica source video")
    evidence_inventory = _trusted_source_evidence_inventory(
        plan, root, source_sha256
    )
    woman_source = _validated_master_reference(
        root,
        woman_master_reference,
        "Woman master reference",
        trusted_evidence_hashes=evidence_inventory.image_sha256s,
    )
    scene_source = _validated_master_reference(
        root,
        scene_master_reference,
        "Scene master reference",
        trusted_evidence_hashes=evidence_inventory.image_sha256s,
    )
    config = GatewayImageConfig(
        api_key=os.environ.get("GATEWAY_API_KEY", ""),
        base_url=os.environ.get(
            "OPENAI_BASE_URL",
            os.environ.get("GATEWAY_BASE_URL", "https://ops-ai-gateway.yc345.tv/v1"),
        ),
        model=ASSET_MODEL,
    )
    staged: list[tuple[Path, ReplicaAssetRecord]] = []
    installed: list[Path] = []
    created_directories: list[Path] = []
    pre_live_manifest = manifest.manifest_path.read_bytes()
    try:
        woman_temporary, woman_master = _stage_master_reference(
            root,
            source=woman_source,
            asset_id="woman_master",
            target=root / "assets" / "masters" / "woman_master.png",
            provenance="project_original_woman_master",
            prompt="Project-original photorealistic adult woman identity master.",
            created_directories=created_directories,
        )
        staged.append((woman_temporary, woman_master))
        scene_temporary, scene_master = _stage_master_reference(
            root,
            source=scene_source,
            asset_id="scene_master",
            target=root / "assets" / "masters" / "scene_master.png",
            provenance="project_empty_scene_master",
            prompt="Project-original empty photographed apartment geometry master.",
            created_directories=created_directories,
        )
        staged.append((scene_temporary, scene_master))

        for job in expected_jobs:
            target = _safe_output_path(root, job.output_path)
            if target.exists() or target.is_symlink():
                raise PetReplicaAssetError(f"Generated asset already exists: {target}")
            _make_output_directory(root, target.parent, created_directories)
            _assert_no_symlinks(root, target.parent)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=f".tmp{target.suffix}", dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                client = client_factory(config)
                reference = woman_temporary if job.kind == "woman" else scene_temporary
                reference_record = woman_master if job.kind == "woman" else scene_master
                client.generate(
                    job.full_prompt,
                    temporary,
                    size=job.size,
                    n=1,
                    ref_image_path=reference,
                )
                width, height = _validated_image(temporary, expected_size=job.size)
                staged.append(
                    (
                        temporary,
                        ReplicaAssetRecord(
                            asset_id=job.asset_id,
                            kind=job.kind,
                            path=target,
                            sha256=_sha256_required(temporary, "Generated asset"),
                            width=width,
                            height=height,
                            provenance="gateway_generated",
                            source_path=None,
                            source_sha256=None,
                            provider="gateway",
                            model=job.model,
                            prompt=job.full_prompt,
                            creation_mode="generated_anchor",
                            reference_asset_id=reference_record.asset_id,
                            reference_path=reference_record.path,
                            reference_sha256=reference_record.sha256,
                        ),
                    )
                )
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        for temporary, record in staged:
            _assert_no_symlinks(root, record.path.parent)
            os.replace(temporary, record.path)
            installed.append(record.path)
        completed = ReplicaAssetManifest(
            schema_version=ASSET_SCHEMA_VERSION,
            output_root=root,
            manifest_path=_manifest_path(plan),
            source_sha256=source_sha256,
            assets=(*manifest.assets, *(record for _temp, record in staged)),
            jobs=expected_jobs,
            live_generation_enabled=True,
            evidence_manifest_sha256=evidence_inventory.manifest_sha256,
            evidence_frame_count=evidence_inventory.frame_count,
            evidence_contact_sheet_count=evidence_inventory.contact_sheet_count,
        )
        _write_manifest(completed)
        return completed
    except Exception as exc:
        _rollback_live_generation(
            root,
            manifest.manifest_path,
            pre_live_manifest,
            staged,
            installed,
            created_directories,
        )
        if isinstance(exc, PetReplicaAssetError):
            raise
        raise PetReplicaAssetError(f"Replica asset generation failed: {exc}") from exc
    finally:
        for temporary, _record in staged:
            temporary.unlink(missing_ok=True)


def write_replica_asset_review_template(plan: PetReplicaPlan) -> Path:
    manifest = _load_manifest(plan, require_complete=True)
    payload = {
        "schema_version": ASSET_REVIEW_SCHEMA_VERSION,
        "source_sha256": manifest.source_sha256,
        "asset_manifest_sha256": _sha256_required(
            manifest.manifest_path, "Replica asset manifest"
        ),
        "evidence_manifest_sha256": manifest.evidence_manifest_sha256,
        "evidence_frame_count": manifest.evidence_frame_count,
        "evidence_contact_sheet_count": manifest.evidence_contact_sheet_count,
        "manual_review_required": True,
        "gates": {gate: False for gate in _REVIEW_GATES},
        "assets": [_review_snapshot(asset, manifest.output_root) for asset in manifest.assets],
    }
    path = _review_template_path(plan)
    _write_json(path, payload)
    return path


def load_approved_replica_assets(plan: PetReplicaPlan) -> ReplicaAssetManifest:
    manifest = _load_manifest(plan, require_complete=True)
    path = _review_path(plan)
    if not path.is_file() or path.is_symlink():
        raise PetReplicaAssetError("Replica assets require manual review before use.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaAssetError("Replica asset review is not valid JSON.") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != ASSET_REVIEW_SCHEMA_VERSION:
        raise PetReplicaAssetError("Replica asset review has an invalid schema.")
    if payload.get("manual_review_required") is not False:
        raise PetReplicaAssetError("Replica assets require manual review before use.")
    gates = payload.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(_REVIEW_GATES):
        raise PetReplicaAssetError("Replica asset review gates are incomplete.")
    if any(value is not True for value in gates.values()):
        raise PetReplicaAssetError("Replica assets require manual review approval for every gate.")
    if payload.get("source_sha256") != manifest.source_sha256:
        raise PetReplicaAssetError("Replica asset review source hash does not match.")
    if (
        payload.get("evidence_manifest_sha256")
        != manifest.evidence_manifest_sha256
        or payload.get("evidence_frame_count") != manifest.evidence_frame_count
        or payload.get("evidence_contact_sheet_count")
        != manifest.evidence_contact_sheet_count
    ):
        raise PetReplicaAssetError(
            "Replica asset review evidence binding does not match."
        )
    if payload.get("asset_manifest_sha256") != _sha256_required(
        manifest.manifest_path, "Replica asset manifest"
    ):
        raise PetReplicaAssetError("Replica asset review manifest hash does not match.")
    snapshots = payload.get("assets")
    expected_snapshots = [_review_snapshot(asset, manifest.output_root) for asset in manifest.assets]
    if snapshots != expected_snapshots:
        raise PetReplicaAssetError("Replica asset review does not match current asset provenance.")
    _validate_complete_manifest(plan, manifest)
    return manifest


def _expected_jobs(plan: PetReplicaPlan) -> tuple[ReplicaAssetJob, ...]:
    root = _output_root(plan)
    character_root = root / "assets" / "characters"
    scene_root = root / "assets" / "scenes"
    single_image_contract = (
        "Generate exactly one edge-to-edge, full-frame, single-camera continuous "
        "photograph. Show one camera view only."
    )
    woman_contract = (
        "Create a photorealistic live-action editorial photograph of a real adult "
        "human: an original East Asian young adult woman, never a minor, with "
        "shoulder-length dark auburn layered hair, round glasses, a beige sleeveless "
        "lounge dress, and one simple fixed gold necklace and small gold earrings. "
        "Show natural skin pores and fine facial texture, realistic individual hair "
        "strands, subtle everyday makeup, and a physically plausible lens, depth of "
        "field, and daylight. "
        "Keep the same face, eye shape, glasses, hair, dress, and jewelry identity "
        "across every requested view and preserve the supplied project-original woman "
        "identity master. She must not resemble the source-video woman or any person "
        "in extracted source frames. This is a new fictional identity; do not use the "
        "source-video woman or any extracted source frame as an identity reference."
    )
    woman_frame_contract = (
        "The frame may contain only the real woman and a plain realistic indoor "
        "background. Absolutely no text, letters, numbers, Chinese characters, "
        "glyphs, icons, logos, menus, buttons, search bar, status bar, navigation "
        "bar, app dock, watermark, label, tag, or UI overlay."
    )
    no_text = (
        "No watermark, username, logo, subtitle, phone UI, generated text, letters, "
        "numbers, brand, collage, border, or panel layout."
    )
    scene_contract = (
        "Create a photorealistic live-action editorial photograph of one pre-existing "
        "real photographed apartment: an empty bright modern living room. This current "
        "single photograph uses fixed metric geometry: a 2.6m beige modular sofa "
        "centered against the back wall; a 1.1m x 0.6m light-oak table centered 0.8m "
        "in front of the sofa; an open kitchen fixed behind-left; and a floor-to-ceiling "
        "window fixed on the right. Preserve the same daylight direction and shadows. "
        "Do not show any other camera angle in this image. No people, cats, text, "
        "brands, logos, watermarks, subtitles, phone UI, or generated writing."
    )
    layout_negative = (
        "No collage, montage, contact sheet, split screen, diptych, triptych, "
        "multi-panel, grid, storyboard, screenshot, app/social-media interface, "
        "overlay, or border."
    )
    style_negative = (
        f"{layout_negative} Avoid source-person likeness, identity drift, changed wardrobe, changed "
        "jewelry, extra limbs, malformed hands, duplicate people, floating props, "
        "warped furniture, impossible geometry, inconsistent lighting, watermark, "
        "username, logo, subtitle, phone UI, generated text, letters, numbers, "
        "illustration, anime, cartoon, 3D render, CGI, doll, game character, plastic "
        "or waxy skin, and painterly style."
    )
    woman_negative = f"{style_negative} {woman_frame_contract}"
    full_body_contract = (
        "Camera at natural waist height. Entire head and hair through both shoes and "
        "feet are visible with margin on all sides. Use a neutral eye-level "
        "perspective. No overhead, fisheye, or extreme perspective. No crop at head, "
        "hands, knees, or feet."
    )
    full_body_negative = (
        f"{woman_negative} No overhead view, fisheye lens, extreme perspective, or "
        "crop at head, hands, knees, or feet."
    )
    scene_negative = (
        f"{style_negative} Avoid changed furniture count, changed furniture style, "
        "changed furniture position, redesigned rooms, replaced sofas, replaced "
        "tables, moved windows, and altered kitchen geometry."
    )
    phone_negative = (
        f"{scene_negative} Absolutely no phone, tripod, hand, arm, human, cat, screen, "
        "device, or UI in the image."
    )
    definitions = (
        ("woman_front", "woman", character_root / "woman_front.png", f"{woman_contract} {woman_frame_contract} Straight-on head-and-shoulders portrait, neutral relaxed expression. {no_text}", woman_negative),
        ("woman_left_three_quarter", "woman", character_root / "woman_left_three_quarter.png", f"{woman_contract} {woman_frame_contract} Left three-quarter head-and-shoulders portrait, neutral relaxed expression. {no_text}", woman_negative),
        ("woman_right_three_quarter", "woman", character_root / "woman_right_three_quarter.png", f"{woman_contract} {woman_frame_contract} Right three-quarter head-and-shoulders portrait, neutral relaxed expression. {no_text}", woman_negative),
        ("woman_half_body", "woman", character_root / "woman_half_body.png", f"{woman_contract} {woman_frame_contract} Half-body portrait with relaxed natural hands. {no_text}", woman_negative),
        ("woman_full_body", "woman", character_root / "woman_full_body.png", f"{woman_contract} {woman_frame_contract} {full_body_contract} Full-body standing portrait with natural proportions. {no_text}", full_body_negative),
        ("scene_sofa", "scene", scene_root / "scene_sofa.png", f"{scene_contract} Use the front-center camera position at 1.2m height with a 24mm lens. Keep the sofa centered and the table fully visible. {no_text}", scene_negative),
        ("scene_table", "scene", scene_root / "scene_table.png", f"{scene_contract} Use the front-right camera position at 0.9m height with a 35mm lens. Frame the table in front of the centered sofa. {no_text}", scene_negative),
        ("scene_phone", "scene", scene_root / "scene_phone.png", f"{scene_contract} Use the sofa-left camera position at 1.3m height with a 35mm lens. Show the empty camera position where a phone tripod will later stand, but the photograph contains absolutely no phone, tripod, hand, arm, human, cat, screen, device, or UI. {no_text}", phone_negative),
    )
    return tuple(
        ReplicaAssetJob(
            asset_id=asset_id,
            kind=kind,
            output_path=output_path,
            prompt=f"{single_image_contract} {prompt}",
            negative_prompt=negative_prompt,
            model=ASSET_MODEL,
            size=ASSET_SIZE,
        )
        for asset_id, kind, output_path, prompt, negative_prompt in definitions
    )


def _trusted_source_evidence_inventory(
    plan: PetReplicaPlan, root: Path, source_sha256: str
) -> _TrustedEvidenceInventory:
    manifest_path = _safe_output_path(
        root, root / "reference" / "evidence_manifest.json"
    )
    _assert_regular_file(manifest_path, "Replica reference evidence manifest")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaAssetError(
            "Replica reference evidence manifest is not valid JSON."
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != REFERENCE_EVIDENCE_SCHEMA_VERSION
    ):
        raise PetReplicaAssetError(
            "Replica reference evidence manifest has an invalid schema."
        )
    if payload.get("source_sha256") != source_sha256:
        raise PetReplicaAssetError(
            "Replica reference evidence manifest source hash does not match."
        )

    last_video_frame_s = payload.get("last_video_frame_s")
    if (
        isinstance(last_video_frame_s, bool)
        or not isinstance(last_video_frame_s, (int, float))
        or not math.isfinite(float(last_video_frame_s))
        or not plan.shots[-1].start_s
        <= float(last_video_frame_s)
        <= plan.duration_s
        or abs(float(last_video_frame_s) * plan.fps - round(float(last_video_frame_s) * plan.fps))
        > 0.001
    ):
        raise PetReplicaAssetError(
            "Replica reference evidence last_video_frame_s is invalid."
        )
    last_video_frame_s = float(last_video_frame_s)
    frames = payload.get("frames")
    expected_frames = _expected_evidence_frame_contract(plan, last_video_frame_s)
    if not isinstance(frames, list) or len(frames) != len(expected_frames):
        raise PetReplicaAssetError(
            "Replica reference evidence frame contract is incomplete."
        )
    contact_sheets = payload.get("contact_sheets")
    expected_contact_sheets = (
        ("reference/contact_sheets/pilot_4x3.jpg", "4x3"),
        ("reference/contact_sheets/full_01_5x8.jpg", "5x8"),
    )
    if (
        not isinstance(contact_sheets, list)
        or len(contact_sheets) != len(expected_contact_sheets)
    ):
        raise PetReplicaAssetError(
            "Replica reference evidence contact sheet contract is incomplete."
        )

    trusted_hashes: set[str] = set()
    for record, expected in zip(frames, expected_frames):
        if not isinstance(record, Mapping):
            raise PetReplicaAssetError(
                "Replica reference evidence frame contract is invalid."
            )
        expected_path, expected_shot_id, expected_label, expected_timestamp = expected
        timestamp = record.get("timestamp_s")
        if (
            record.get("image_path") != expected_path
            or record.get("shot_id") != expected_shot_id
            or record.get("label") != expected_label
            or isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))
            or not math.isclose(
                float(timestamp), expected_timestamp, rel_tol=0.0, abs_tol=1e-6
            )
        ):
            raise PetReplicaAssetError(
                "Replica reference evidence frame contract has missing, duplicate, "
                "or unexpected records."
            )
        trusted_hashes.add(
            _validated_evidence_record_hash(
                root,
                record,
                source_sha256,
                (("reference", "shots"), ("reference", "contact_sheets")),
            )
        )

    for record, expected in zip(contact_sheets, expected_contact_sheets):
        if (
            not isinstance(record, Mapping)
            or record.get("image_path") != expected[0]
            or record.get("layout") != expected[1]
        ):
            raise PetReplicaAssetError(
                "Replica reference evidence contact sheet contract has missing, "
                "duplicate, or unexpected records."
            )
        trusted_hashes.add(
            _validated_evidence_record_hash(
                root,
                record,
                source_sha256,
                (("reference", "contact_sheets"),),
            )
        )

    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PetReplicaAssetError(
            "Replica reference evidence manifest is not canonicalizable."
        ) from exc
    return _TrustedEvidenceInventory(
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        frame_count=len(frames),
        contact_sheet_count=len(contact_sheets),
        image_sha256s=frozenset(trusted_hashes),
    )


def _validated_evidence_record_hash(
    root: Path,
    record: Mapping[str, Any],
    source_sha256: str,
    allowed_prefixes: tuple[tuple[str, ...], ...],
) -> str:
    if record.get("source_sha256") != source_sha256:
        raise PetReplicaAssetError(
            "Replica reference evidence source hash does not match."
        )
    image_path = _trusted_evidence_path(
        root, record.get("image_path"), allowed_prefixes
    )
    expected_hash = record.get("image_sha256")
    if (
        not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        raise PetReplicaAssetError("Replica reference evidence hash is invalid.")
    actual_hash = _sha256_required(image_path, "Replica reference evidence")
    if actual_hash != expected_hash:
        raise PetReplicaAssetError(
            "Replica reference evidence hash does not match."
        )
    return actual_hash


def _expected_evidence_frame_contract(
    plan: PetReplicaPlan, last_video_frame_s: float
) -> tuple[tuple[str, str, str, float], ...]:
    records: list[tuple[str, str, str, float]] = []
    for shot in plan.shots:
        timestamps = (
            ("start", shot.start_s),
            ("middle", (shot.start_s + shot.end_s) / 2),
            (
                "end",
                min(
                    max(shot.start_s, shot.end_s - 1 / plan.fps),
                    last_video_frame_s,
                ),
            ),
        )
        records.extend(
            (
                f"reference/shots/{shot.shot_id}/{label}.jpg",
                shot.shot_id,
                label,
                timestamp_s,
            )
            for label, timestamp_s in timestamps
        )
    for prefix, count, end_s in (
        ("pilot", 12, plan.pilot_end_s),
        ("full_01", 40, plan.duration_s),
    ):
        for index, timestamp_s in enumerate(
            _evidence_sample_timestamps(
                0.0, end_s, count, plan.fps, last_video_frame_s
            ),
            start=1,
        ):
            records.append(
                (
                    f"reference/contact_sheets/{prefix}_frames/{index:03d}.jpg",
                    _evidence_shot_id_at(plan, timestamp_s),
                    f"{prefix}_{index:03d}",
                    timestamp_s,
                )
            )
    if len(records) != 163:
        raise PetReplicaAssetError(
            "Replica reference evidence frame contract is not locked to 163 records."
        )
    return tuple(records)


def _evidence_sample_timestamps(
    start_s: float,
    end_s: float,
    count: int,
    fps: int,
    last_video_frame_s: float,
) -> tuple[float, ...]:
    final = min(max(start_s, end_s - 1 / fps), last_video_frame_s)
    increment = (final - start_s) / (count - 1)
    return tuple(start_s + index * increment for index in range(count))


def _evidence_shot_id_at(plan: PetReplicaPlan, timestamp_s: float) -> str:
    for shot in plan.shots:
        if shot.start_s <= timestamp_s < shot.end_s:
            return shot.shot_id
    return plan.shots[-1].shot_id


def _trusted_evidence_path(
    root: Path, value: Any, allowed_prefixes: tuple[tuple[str, ...], ...]
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PetReplicaAssetError("Replica reference evidence path is missing.")
    identifier = Path(value)
    if (
        identifier.is_absolute()
        or any(part in {".", ".."} for part in identifier.parts)
        or not any(
            identifier.parts[: len(prefix)] == prefix for prefix in allowed_prefixes
        )
    ):
        raise PetReplicaAssetError(
            "Replica reference evidence path must be a trusted relative path."
        )
    path = _safe_output_path(root, root / identifier)
    _assert_regular_file(path, "Replica reference evidence")
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES or not is_valid_image_file(path):
        raise PetReplicaAssetError(
            "Replica reference evidence has an invalid image signature."
        )
    return path


def _validated_master_reference(
    root: Path,
    value: str | Path,
    label: str,
    *,
    trusted_evidence_hashes: frozenset[str] | None = None,
) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    if raw.is_symlink():
        raise PetReplicaAssetError(f"{label} must not use symlinks.")
    try:
        lexical_relative = raw.absolute().relative_to(root)
    except ValueError as exc:
        raise PetReplicaAssetError(f"{label} must remain inside the output root.") from exc
    if any(part in {".", ".."} for part in lexical_relative.parts):
        raise PetReplicaAssetError(f"{label} must remain inside the output root.")
    _assert_no_symlinks(root, raw.parent)
    resolved = raw.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise PetReplicaAssetError(f"{label} must remain inside the output root.") from exc
    if any(
        part.lower() in {"reference", "frames", "source_frames", "source-frames"}
        for part in relative.parts[:-1]
    ):
        raise PetReplicaAssetError(f"{label} must not be a source frame.")
    _assert_regular_file(resolved, label)
    _validated_image(resolved, expected_size=ASSET_SIZE)
    if (
        trusted_evidence_hashes is not None
        and _sha256_required(resolved, label) in trusted_evidence_hashes
    ):
        raise PetReplicaAssetError(f"{label} matches trusted source evidence.")
    masters_root = root / "assets" / "masters"
    try:
        resolved.relative_to(masters_root)
    except ValueError:
        return resolved
    raise PetReplicaAssetError(f"{label} must be a project input, not an installed master.")


def _stage_master_reference(
    root: Path,
    *,
    source: Path,
    asset_id: str,
    target: Path,
    provenance: str,
    prompt: str,
    created_directories: list[Path],
) -> tuple[Path, ReplicaAssetRecord]:
    destination = _safe_output_path(root, target)
    if destination.exists() or destination.is_symlink():
        raise PetReplicaAssetError(f"Master asset already exists: {destination}")
    _make_output_directory(root, destination.parent, created_directories)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=f".tmp{destination.suffix}",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    source_sha256 = _sha256_required(source, "Project master reference")
    try:
        shutil.copyfile(source, temporary)
        width, height = _validated_image(temporary, expected_size=ASSET_SIZE)
        copied_sha256 = _sha256_required(temporary, "Staged master reference")
        if copied_sha256 != source_sha256:
            raise PetReplicaAssetError("Staged master reference hash mismatch.")
        return (
            temporary,
            ReplicaAssetRecord(
                asset_id=asset_id,
                kind="master_reference",
                path=destination,
                sha256=copied_sha256,
                width=width,
                height=height,
                provenance=provenance,
                source_path=source,
                source_sha256=source_sha256,
                provider="local",
                model="project_master_reference",
                prompt=prompt,
                creation_mode="copied_project_master",
            ),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _copy_approved_cat_reference(
    root: Path, *, asset_id: str, source: str | Path, destination: Path
) -> ReplicaAssetRecord:
    source_path = _approved_cat_path(source)
    width, height = _validated_image(source_path, expected_size=ASSET_SIZE)
    source_sha256 = _sha256_required(source_path, "Approved cat reference")
    target = _safe_output_path(root, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(root, target.parent)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise PetReplicaAssetError("Replica asset paths must not use symlinks.")
        if _sha256_required(target, "Installed cat reference") != source_sha256:
            raise PetReplicaAssetError("Installed cat reference hash mismatch.")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.", suffix=f".tmp{target.suffix}", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source_path, temporary)
            _validated_image(temporary, expected_size=ASSET_SIZE)
            if _sha256_required(temporary, "Copied cat reference") != source_sha256:
                raise PetReplicaAssetError("Copied cat reference hash mismatch.")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return ReplicaAssetRecord(
        asset_id=asset_id,
        kind="cat_reference",
        path=target,
        sha256=source_sha256,
        width=width,
        height=height,
        provenance="approved_pet_output",
        source_path=source_path,
        source_sha256=source_sha256,
        provider="local",
        model="approved_pet_reference",
        prompt="Immutable approved cat identity reference copied without modification.",
        creation_mode="copied_approved_cat_reference",
    )


def _approved_cat_path(value: str | Path) -> Path:
    root = APPROVED_CAT_REFERENCE_ROOT.expanduser().resolve()
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raw = raw.absolute()
    if raw.is_symlink():
        raise PetReplicaAssetError("Approved cat references must not use symlinks.")
    try:
        raw.relative_to(root)
    except ValueError as exc:
        raise PetReplicaAssetError(
            "Approved cat reference must be inside the approved pet output root."
        ) from exc
    _assert_no_symlinks(root, raw.parent)
    _assert_regular_file(raw, "Approved cat reference")
    if raw.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise PetReplicaAssetError("Approved cat reference uses an unsupported image format.")
    if not is_valid_image_file(raw):
        raise PetReplicaAssetError("Approved cat reference has an invalid image signature.")
    _validated_image(raw, expected_size=ASSET_SIZE)
    return raw.resolve()


def _approved_cat_source_id(path: Path) -> str:
    root = APPROVED_CAT_REFERENCE_ROOT.expanduser().resolve()
    source = _approved_cat_path(path)
    try:
        return source.relative_to(root).as_posix()
    except ValueError as exc:
        raise PetReplicaAssetError(
            "Approved cat reference must be inside the approved pet output root."
        ) from exc


def _approved_cat_path_from_id(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PetReplicaAssetError("Approved cat source identifier is missing.")
    identifier = Path(value)
    if identifier.is_absolute() or any(part in {".", ".."} for part in identifier.parts):
        raise PetReplicaAssetError("Approved cat source identifier must be relative.")
    root = APPROVED_CAT_REFERENCE_ROOT.expanduser().resolve()
    candidate = root / identifier
    if candidate.is_symlink():
        raise PetReplicaAssetError("Approved cat references must not use symlinks.")
    _assert_no_symlinks(root, candidate.parent)
    _assert_regular_file(candidate, "Approved cat reference")
    if candidate.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise PetReplicaAssetError("Approved cat reference uses an unsupported image format.")
    if not is_valid_image_file(candidate):
        raise PetReplicaAssetError("Approved cat reference has an invalid image signature.")
    _validated_image(candidate, expected_size=ASSET_SIZE)
    return candidate.resolve()


def _project_source_id(root: Path, path: Path) -> str:
    source = _validated_master_reference(root, path, "Project master reference")
    return source.relative_to(root).as_posix()


def _project_path_from_id(
    root: Path, value: Any, trusted_evidence_hashes: frozenset[str]
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PetReplicaAssetError("Project master source identifier is missing.")
    identifier = Path(value)
    if identifier.is_absolute() or any(part in {".", ".."} for part in identifier.parts):
        raise PetReplicaAssetError("Project master source identifier must be relative.")
    return _validated_master_reference(
        root,
        root / identifier,
        "Project master reference",
        trusted_evidence_hashes=trusted_evidence_hashes,
    )


def _load_manifest(plan: PetReplicaPlan, *, require_complete: bool) -> ReplicaAssetManifest:
    path = _manifest_path(plan)
    _assert_regular_file(path, "Replica asset manifest")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PetReplicaAssetError("Replica asset manifest is not valid JSON.") from exc
    root = _output_root(plan)
    if not isinstance(payload, Mapping):
        raise PetReplicaAssetError("Replica asset manifest is invalid.")
    if payload.get("schema_version") != ASSET_SCHEMA_VERSION:
        raise PetReplicaAssetError("Replica asset manifest has an invalid schema.")
    if "output_root" in payload:
        raise PetReplicaAssetError(
            "Replica asset manifest must not persist output_root."
        )
    source_sha256 = _sha256_required(plan.source_video, "Replica source video")
    if payload.get("source_sha256") != source_sha256:
        raise PetReplicaAssetError("Replica asset manifest source hash does not match.")
    try:
        raw_assets = payload["assets"]
        if not isinstance(raw_assets, list):
            raise TypeError("assets must be a list")
        has_master_records = any(
            isinstance(item, Mapping)
            and item.get("provenance")
            in {"project_original_woman_master", "project_empty_scene_master"}
            for item in raw_assets
        )
        evidence_inventory = (
            _trusted_source_evidence_inventory(plan, root, source_sha256)
            if has_master_records
            else None
        )
        trusted_evidence_hashes = (
            evidence_inventory.image_sha256s
            if evidence_inventory is not None
            else frozenset()
        )
        evidence_fields = (
            payload.get("evidence_manifest_sha256"),
            payload.get("evidence_frame_count"),
            payload.get("evidence_contact_sheet_count"),
        )
        if evidence_inventory is None:
            if any(value is not None for value in evidence_fields):
                raise PetReplicaAssetError(
                    "Planned replica asset manifest must not bind live evidence."
                )
        elif evidence_fields != (
            evidence_inventory.manifest_sha256,
            evidence_inventory.frame_count,
            evidence_inventory.contact_sheet_count,
        ):
            raise PetReplicaAssetError(
                "Replica asset evidence manifest binding does not match."
            )
        assets = tuple(
            _record_from_payload(
                item,
                root,
                trusted_evidence_hashes=trusted_evidence_hashes,
            )
            for item in raw_assets
        )
        jobs = tuple(_job_from_payload(item, root) for item in payload["jobs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PetReplicaAssetError("Replica asset manifest is invalid.") from exc
    manifest = ReplicaAssetManifest(
        schema_version=ASSET_SCHEMA_VERSION,
        output_root=root,
        manifest_path=path,
        source_sha256=source_sha256,
        assets=assets,
        jobs=jobs,
        live_generation_enabled=payload.get("live_generation_enabled") is True,
        evidence_manifest_sha256=(
            evidence_inventory.manifest_sha256
            if evidence_inventory is not None
            else None
        ),
        evidence_frame_count=(
            evidence_inventory.frame_count
            if evidence_inventory is not None
            else None
        ),
        evidence_contact_sheet_count=(
            evidence_inventory.contact_sheet_count
            if evidence_inventory is not None
            else None
        ),
    )
    if tuple(jobs) != _expected_jobs(plan):
        raise PetReplicaAssetError("Replica asset manifest jobs do not match the locked contract.")
    if require_complete:
        _validate_complete_manifest(plan, manifest)
    return manifest


def _validate_complete_manifest(plan: PetReplicaPlan, manifest: ReplicaAssetManifest) -> None:
    expected_jobs = _expected_jobs(plan)
    expected_ids = (
        "naitang_reference",
        "doubao_reference",
        "woman_master",
        "scene_master",
        *(job.asset_id for job in expected_jobs),
    )
    if tuple(asset.asset_id for asset in manifest.assets) != expected_ids:
        raise PetReplicaAssetError("Replica asset manifest does not contain every locked asset.")
    if manifest.live_generation_enabled is not True:
        raise PetReplicaAssetError("Replica asset manifest is not a completed live generation.")
    _validate_cat_records(plan, manifest.assets[:2])
    root = _output_root(plan)
    evidence_inventory = _trusted_source_evidence_inventory(
        plan, root, manifest.source_sha256
    )
    if (
        manifest.evidence_manifest_sha256 != evidence_inventory.manifest_sha256
        or manifest.evidence_frame_count != evidence_inventory.frame_count
        or manifest.evidence_contact_sheet_count
        != evidence_inventory.contact_sheet_count
    ):
        raise PetReplicaAssetError(
            "Replica asset evidence manifest binding does not match."
        )
    trusted_evidence_hashes = evidence_inventory.image_sha256s
    woman_master, scene_master = manifest.assets[2:4]
    _validate_master_record(
        root,
        woman_master,
        expected_id="woman_master",
        expected_path=root / "assets" / "masters" / "woman_master.png",
        expected_provenance="project_original_woman_master",
        expected_prompt="Project-original photorealistic adult woman identity master.",
        trusted_evidence_hashes=trusted_evidence_hashes,
    )
    _validate_master_record(
        root,
        scene_master,
        expected_id="scene_master",
        expected_path=root / "assets" / "masters" / "scene_master.png",
        expected_provenance="project_empty_scene_master",
        expected_prompt="Project-original empty photographed apartment geometry master.",
        trusted_evidence_hashes=trusted_evidence_hashes,
    )
    for asset, job in zip(manifest.assets[4:], expected_jobs):
        reference = woman_master if job.kind == "woman" else scene_master
        if (
            asset.kind != job.kind
            or asset.path != job.output_path
            or asset.provenance != "gateway_generated"
            or asset.source_path is not None
            or asset.source_sha256 is not None
            or asset.provider != "gateway"
            or asset.model != job.model
            or asset.prompt != job.full_prompt
            or asset.creation_mode != "generated_anchor"
            or asset.reference_asset_id != reference.asset_id
            or asset.reference_path != reference.path
            or asset.reference_sha256 != reference.sha256
        ):
            raise PetReplicaAssetError("Generated asset provenance does not match its locked job.")
        _validate_record_file(asset, expected_size=job.size)


def _validate_master_record(
    root: Path,
    record: ReplicaAssetRecord,
    *,
    expected_id: str,
    expected_path: Path,
    expected_provenance: str,
    expected_prompt: str,
    trusted_evidence_hashes: frozenset[str],
) -> None:
    if (
        record.asset_id != expected_id
        or record.kind != "master_reference"
        or record.path != expected_path
        or record.provenance != expected_provenance
        or record.source_path is None
        or record.source_sha256 is None
        or record.provider != "local"
        or record.model != "project_master_reference"
        or record.prompt != expected_prompt
        or record.creation_mode != "copied_project_master"
        or record.reference_asset_id is not None
        or record.reference_path is not None
        or record.reference_sha256 is not None
    ):
        raise PetReplicaAssetError("Master asset provenance does not match the locked contract.")
    source = _validated_master_reference(
        root,
        record.source_path,
        "Project master reference",
        trusted_evidence_hashes=trusted_evidence_hashes,
    )
    if _sha256_required(source, "Project master reference") != record.source_sha256:
        raise PetReplicaAssetError("Project master reference source hash does not match.")
    _validate_record_file(record, expected_size=ASSET_SIZE)
    if record.sha256 != record.source_sha256:
        raise PetReplicaAssetError("Installed master reference hash mismatch.")


def _validate_cat_records(plan: PetReplicaPlan, records: tuple[ReplicaAssetRecord, ...]) -> None:
    if len(records) != 2 or tuple(item.asset_id for item in records) != (
        "naitang_reference",
        "doubao_reference",
    ):
        raise PetReplicaAssetError("Replica cat references are incomplete.")
    root = _output_root(plan)
    destinations = (
        root / "assets" / "characters" / "奶糖_reference.png",
        root / "assets" / "characters" / "豆包_reference.png",
    )
    for record, destination in zip(records, destinations):
        if (
            record.kind != "cat_reference"
            or record.path != destination
            or record.provenance != "approved_pet_output"
            or record.source_path is None
            or record.source_sha256 is None
            or record.provider != "local"
            or record.model != "approved_pet_reference"
            or record.creation_mode != "copied_approved_cat_reference"
            or record.reference_asset_id is not None
            or record.reference_path is not None
            or record.reference_sha256 is not None
        ):
            raise PetReplicaAssetError("Cat reference provenance does not match the approved contract.")
        source = _approved_cat_path(record.source_path)
        if record.source_sha256 != _sha256_required(source, "Approved cat reference"):
            raise PetReplicaAssetError("Approved cat reference source hash does not match.")
        _validate_record_file(record, expected_size=ASSET_SIZE)
        if record.sha256 != record.source_sha256:
            raise PetReplicaAssetError("Installed cat reference hash mismatch.")


def _validate_record_file(record: ReplicaAssetRecord, *, expected_size: str) -> None:
    _assert_regular_file(record.path, "Replica asset")
    if _sha256_required(record.path, "Replica asset") != record.sha256:
        raise PetReplicaAssetError("Replica asset hash does not match its manifest.")
    width, height = _validated_image(record.path, expected_size=expected_size)
    if (width, height) != (record.width, record.height):
        raise PetReplicaAssetError("Replica asset dimensions do not match its manifest.")


def _record_from_payload(
    payload: Any,
    root: Path,
    *,
    trusted_evidence_hashes: frozenset[str],
) -> ReplicaAssetRecord:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid asset")
    if "source_path" in payload:
        raise ValueError("external source paths are not allowed in asset manifests")
    provenance = str(payload["provenance"])
    source_identifier = payload.get("source_id")
    if source_identifier is None:
        source_path = None
    elif provenance == "approved_pet_output":
        source_path = _approved_cat_path_from_id(source_identifier)
    elif provenance in {
        "project_original_woman_master",
        "project_empty_scene_master",
    }:
        source_path = _project_path_from_id(
            root, source_identifier, trusted_evidence_hashes
        )
    else:
        raise PetReplicaAssetError("Replica asset source provenance is invalid.")
    reference_value = payload.get("reference_path")
    return ReplicaAssetRecord(
        asset_id=str(payload["asset_id"]),
        kind=str(payload["kind"]),
        path=_safe_output_path(root, _read_relative_path(root, payload["path"])),
        sha256=str(payload["sha256"]),
        width=int(payload["width"]),
        height=int(payload["height"]),
        provenance=provenance,
        source_path=source_path,
        source_sha256=str(payload["source_sha256"]) if payload.get("source_sha256") else None,
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        prompt=str(payload["prompt"]),
        creation_mode=str(payload["creation_mode"]),
        reference_asset_id=(
            str(payload["reference_asset_id"])
            if payload.get("reference_asset_id") is not None
            else None
        ),
        reference_path=(
            _safe_output_path(root, _read_relative_path(root, reference_value))
            if reference_value is not None
            else None
        ),
        reference_sha256=(
            str(payload["reference_sha256"])
            if payload.get("reference_sha256") is not None
            else None
        ),
    )


def _job_from_payload(payload: Any, root: Path) -> ReplicaAssetJob:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid job")
    return ReplicaAssetJob(
        asset_id=str(payload["asset_id"]),
        kind=str(payload["kind"]),
        output_path=_safe_output_path(root, _read_relative_path(root, payload["output_path"])),
        prompt=str(payload["prompt"]),
        negative_prompt=str(payload["negative_prompt"]),
        model=str(payload["model"]),
        size=str(payload["size"]),
    )


def _write_manifest(manifest: ReplicaAssetManifest) -> Path:
    payload: dict[str, Any] = {
        "schema_version": manifest.schema_version,
        "source_sha256": manifest.source_sha256,
        "live_generation_enabled": manifest.live_generation_enabled,
        "assets": [
            _record_payload(asset, manifest.output_root) for asset in manifest.assets
        ],
        "jobs": [_job_payload(job, manifest.output_root) for job in manifest.jobs],
    }
    if manifest.live_generation_enabled:
        if (
            manifest.evidence_manifest_sha256 is None
            or manifest.evidence_frame_count is None
            or manifest.evidence_contact_sheet_count is None
        ):
            raise PetReplicaAssetError(
                "Completed replica asset manifest requires evidence binding."
            )
        payload.update(
            {
                "evidence_manifest_sha256": manifest.evidence_manifest_sha256,
                "evidence_frame_count": manifest.evidence_frame_count,
                "evidence_contact_sheet_count": (
                    manifest.evidence_contact_sheet_count
                ),
            }
        )
    _write_json(
        manifest.manifest_path,
        payload,
    )
    return manifest.manifest_path


def _record_payload(record: ReplicaAssetRecord, root: Path) -> dict[str, Any]:
    payload = asdict(record)
    payload["path"] = str(record.path.relative_to(root))
    payload.pop("source_path")
    if record.source_path is None:
        payload["source_id"] = None
    elif record.provenance == "approved_pet_output":
        payload["source_id"] = _approved_cat_source_id(record.source_path)
    elif record.provenance in {
        "project_original_woman_master",
        "project_empty_scene_master",
    }:
        payload["source_id"] = _project_source_id(root, record.source_path)
    else:
        raise PetReplicaAssetError("Replica asset source provenance is invalid.")
    payload["reference_path"] = (
        str(record.reference_path.relative_to(root)) if record.reference_path else None
    )
    return payload


def _job_payload(job: ReplicaAssetJob, root: Path) -> dict[str, Any]:
    payload = asdict(job)
    payload["output_path"] = str(job.output_path.relative_to(root))
    return payload


def _review_snapshot(record: ReplicaAssetRecord, root: Path) -> dict[str, Any]:
    if record.source_path is None:
        source_id = None
    elif record.provenance == "approved_pet_output":
        source_id = _approved_cat_source_id(record.source_path)
    elif record.provenance in {
        "project_original_woman_master",
        "project_empty_scene_master",
    }:
        source_id = _project_source_id(root, record.source_path)
    else:
        raise PetReplicaAssetError("Replica asset source provenance is invalid.")
    return {
        "asset_id": record.asset_id,
        "path": str(record.path.relative_to(root)),
        "sha256": record.sha256,
        "width": record.width,
        "height": record.height,
        "provenance": record.provenance,
        "source_id": source_id,
        "source_sha256": record.source_sha256,
        "provider": record.provider,
        "model": record.model,
        "prompt": record.prompt,
        "creation_mode": record.creation_mode,
        "reference_asset_id": record.reference_asset_id,
        "reference_path": (
            str(record.reference_path.relative_to(root)) if record.reference_path else None
        ),
        "reference_sha256": record.reference_sha256,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    root = path.parents[1] if path.parent.name == "assets" else path.parents[2]
    _safe_output_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlinks(root, path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _make_output_directory(root: Path, directory: Path, created: list[Path]) -> None:
    try:
        parts = directory.relative_to(root).parts
    except ValueError as exc:
        raise PetReplicaAssetError("Replica asset path must remain inside the output root.") from exc
    current = root
    for part in parts:
        current = current / part
        if not current.exists():
            current.mkdir()
            created.append(current)
    _assert_no_symlinks(root, directory)


def _rollback_live_generation(
    root: Path,
    manifest_path: Path,
    pre_live_manifest: bytes,
    staged: list[tuple[Path, ReplicaAssetRecord]],
    installed: list[Path],
    created_directories: list[Path],
) -> None:
    rollback_errors: list[OSError] = []
    for temporary, _record in staged:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            rollback_errors.append(exc)
    for target in reversed(installed):
        try:
            if target.exists() and not target.is_symlink():
                target.unlink()
        except OSError as exc:
            rollback_errors.append(exc)
    try:
        if manifest_path.read_bytes() != pre_live_manifest:
            _restore_file_bytes(root, manifest_path, pre_live_manifest)
    except OSError as exc:
        rollback_errors.append(exc)
    for directory in sorted(created_directories, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            if any(directory.iterdir()):
                rollback_errors.append(exc)
    if rollback_errors:
        raise PetReplicaAssetError("Replica asset rollback could not restore the pre-live state.")


def _restore_file_bytes(root: Path, path: Path, content: bytes) -> None:
    _safe_output_path(root, path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.restore.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_path(plan: PetReplicaPlan) -> Path:
    return _output_root(plan) / "assets" / "asset_manifest.json"


def _review_template_path(plan: PetReplicaPlan) -> Path:
    return _output_root(plan) / "assets" / "asset_review.template.json"


def _review_path(plan: PetReplicaPlan) -> Path:
    return _output_root(plan) / "assets" / "asset_review.json"


def _output_root(plan: PetReplicaPlan) -> Path:
    root = plan.output_root.expanduser().resolve()
    if root != plan.output_root:
        raise PetReplicaAssetError("Replica output root must be resolved before asset work.")
    return root


def _safe_output_path(root: Path, candidate: Path) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PetReplicaAssetError("Replica asset path must remain inside the output root.") from exc
    _assert_no_symlinks(root, path.parent)
    if path.is_symlink():
        raise PetReplicaAssetError("Replica asset paths must not use symlinks.")
    return resolved


def _read_relative_path(root: Path, value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError("manifest paths must be relative to the output root")
    return root / path


def _assert_no_symlinks(root: Path, directory: Path) -> None:
    root = root.resolve()
    target = directory.absolute()
    try:
        parts = target.relative_to(root).parts
    except ValueError as exc:
        raise PetReplicaAssetError("Replica asset path must remain inside the output root.") from exc
    cursor = root
    if cursor.is_symlink():
        raise PetReplicaAssetError("Replica asset paths must not use symlinks.")
    for part in parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise PetReplicaAssetError("Replica asset paths must not use symlinks.")


def _assert_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise PetReplicaAssetError(f"{label} must not use symlinks.")
    if not path.is_file():
        raise PetReplicaAssetError(f"{label} is missing: {path}")


def _validated_image(path: Path, *, expected_size: str) -> tuple[int, int]:
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise PetReplicaAssetError("Replica asset uses an unsupported image format.")
    if not is_valid_image_file(path):
        raise PetReplicaAssetError("Replica asset has an invalid image signature.")
    try:
        with Image.open(path) as image:
            image.load()
            if image.mode == "A" or not set(image.getbands()) & {"R", "G", "B", "L", "P", "C", "M", "Y", "K"}:
                raise PetReplicaAssetError("Replica asset must not be alpha-only.")
            if "A" in image.getbands():
                color_extrema = image.convert("RGB").getextrema()
                alpha_extrema = image.getchannel("A").getextrema()
                if all(extrema == (0, 0) for extrema in color_extrema) and alpha_extrema != (255, 255):
                    raise PetReplicaAssetError("Replica asset must not be alpha-only.")
            width, height = image.size
    except PetReplicaAssetError:
        raise
    except (OSError, UnidentifiedImageError) as exc:
        raise PetReplicaAssetError("Replica asset cannot be decoded as an image.") from exc
    try:
        expected_width, expected_height = (int(value) for value in expected_size.split("x", 1))
    except (TypeError, ValueError) as exc:
        raise PetReplicaAssetError("Replica asset expected size is invalid.") from exc
    if (width, height) != (expected_width, expected_height):
        raise PetReplicaAssetError("Replica asset dimensions do not match the locked size.")
    return width, height


def _sha256_required(path: Path, label: str) -> str:
    _assert_regular_file(path, label)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PetReplicaAssetError(f"{label} cannot be read: {path}") from exc
    return digest.hexdigest()
