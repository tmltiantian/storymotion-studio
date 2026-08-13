from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PLAN_SCHEMA_VERSION = "motion-comic-factory.pet-sitcom-plan.v2"
PROJECT_ID = "pet_sitcom_audio_first_20260726"
FINAL_DURATION_SECONDS = 54.0
DIALOGUE_TAIL_SECONDS = 0.30
TITLE = "冻干到底是谁偷吃的？"
IMAGE_MODEL = "doubao-seedream-4-5"
VIDEO_MODEL = "doubao-seedance-2-0"
OWNER_AUDIO_MODEL = "seed-tts-2.0"
VIDEO_RATIO = "9:16"
VIDEO_RESOLUTION = "1080x1920"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "宠物短剧样片" / "冻干案_20260723"

_REFERENCE_ORDER = (
    "Naitang immutable character sheet",
    "Doubao immutable character sheet",
    "current empty scene anchor",
    "previous selected ending frame when the shot index is greater than one",
)
_SHARED_VIDEO_CONSTRAINTS = (
    "Reference 1 is Naitang immutable character sheet. "
    "Reference 2 is Doubao immutable character sheet. "
    "Reference 3 is the current empty scene anchor. "
    "Reference 4, when present, is the previous selected ending frame. "
    "Preserve Naitang and Doubao as two distinct cats with unchanged "
    "markings/eyes/body proportions whenever either is visible; neither cat needs "
    "to be fully visible in every shot. Keep realistic feline anatomy and weight, "
    "grounded paws, and natural whiskers/ears/tails. Use restrained jaw movement; "
    "only the designated speaker moving their mouth as speech. Use a stable "
    "camera, no digital zoom, no optical-flow look, no floating, no duplicated "
    "body parts, no extra animal, no human face, no text, no subtitle, and no "
    "watermark."
)
_SPATIAL_CONTINUITY_CONSTRAINTS = (
    "Naitang remains screen-left and looks right. Doubao remains screen-right and "
    "looks left. The owner remains off camera. Preserve the 180-degree axis, kitchen "
    "doorway geometry, warm daylight from frame left, bag position, tail position, "
    "mirror position, and each cat's pose from the declared start state."
)

_CHARACTER_SPECS = (
    {
        "slug": "naitang",
        "name": "奶糖",
        "description": (
            "Photorealistic orange-and-white short-haired cat with a round face, "
            "amber eyes, a slightly round body, a symmetrical flat white muzzle, "
            "and a white chest."
        ),
        "voice_description": "Slightly young, guilty, stubborn Chinese cat voice.",
        "prompt": (
            "Create a text-free triptych of the same exact photorealistic "
            "orange-and-white short-haired cat: front portrait, three-quarter "
            "head-and-body, and full body. Require a round face, amber eyes, a "
            "slightly round body, symmetrical flat white muzzle, and white chest. "
            "The white areas are ordinary short coat: no isolated white blob, "
            "tuft, object, beard, or loose fur. Require identical natural markings "
            "in all three panels and unchanged markings. The cat is bare-necked in "
            "every panel: no collar, no tag, no accessories. Use neutral studio "
            "light and a plain warm-gray background. no labels and no extra animal."
        ),
        "reference_name": "奶糖_reference.png",
    },
    {
        "slug": "doubao",
        "name": "豆包",
        "description": (
            "Photorealistic black-and-white tuxedo cat with a narrower face, "
            "green eyes, a slimmer body, a continuous white nose-to-chin marking, "
            "and white chest and paws."
        ),
        "voice_description": "Calm, lower Chinese cat voice with deliberate pauses.",
        "prompt": (
            "Create a text-free triptych of the same exact photorealistic "
            "black-and-white tuxedo cat: front portrait, three-quarter "
            "head-and-body, and full body. Require a narrower face, green eyes, a "
            "slimmer body, continuous white nose-to-chin marking, white chest and "
            "paws, and unchanged markings. Use neutral studio light and a plain "
            "warm-gray background. no collar, no accessories, no labels, and no "
            "extra animal."
        ),
        "reference_name": "豆包_reference.png",
    },
)

_SCENE_SHARED_PROMPT = (
    "Use warm natural daylight from frame left, honey-colored wood floor, light "
    "neutral furniture, a fixed home layout, and natural candid interior "
    "photography as a full-bleed interior image. Show no phone frame, no device "
    "border, no camera UI, no person, no animal, no silhouette, no text, no logo, "
    "no watermark, no food package, and no mirror."
)
_SCENE_SPECS = (
    {
        "slug": "living_room",
        "name": "客厅",
        "prompt": (
            "Create an empty living room scene anchor with a plain non-reflective "
            "wall and simple low furniture. Include no wall mirror, no reflective "
            "wall decor, no snack bag, no food container, and no loose package. "
            f"{_SCENE_SHARED_PROMPT}"
        ),
    },
    {
        "slug": "kitchen",
        "name": "厨房",
        "prompt": (
            "Create a clean, empty, unoccupied kitchen scene anchor as an unobstructed "
            "full-bleed interior image. Use warm natural daylight from frame left, "
            "honey-colored wood floor, light neutral furniture, and a fixed home layout. "
            "Keep every cabinet closed, every counter completely bare, the clear open "
            "floor unobstructed, and all walls and surfaces plain and unlabeled. Use "
            "natural candid interior photography from a fixed doorway viewpoint."
        ),
    },
)

_SHOT_SPECS = (
    {
        "title": "冻干袋钩子", "duration_seconds": 5.2,
        "generation_duration_seconds": 6, "scene_slug": "living_room",
        "speaker": "owner", "dialogue": "谁把新开的冻干吃完了？",
        "dialogue_offset_seconds": 0.55,
        "action": "empty freeze-dried treat bag close-up; both cats look toward the camera",
        "start_state": "Naitang and Doubao face the empty freeze-dried treat bag in the living room.",
        "end_state": "Both cats hold their suspicious look beside the empty freeze-dried treat bag.",
        "transition": "hard_cut_to_suspicious_look", "continuity_source_ids": (),
    },
    {
        "title": "沉默对视", "duration_seconds": 3.4,
        "generation_duration_seconds": 4, "scene_slug": "living_room",
        "speaker": None, "dialogue": "", "dialogue_offset_seconds": 0.0,
        "action": "Naitang and Doubao hold a silent suspicious look toward the camera",
        "start_state": "Both cats hold their suspicious look beside the empty freeze-dried treat bag.",
        "end_state": "Both cats remain suspicious before the kitchen confrontation.",
        "transition": "match_look_to_kitchen_doorway", "continuity_source_ids": ("shot_01",),
    },
    {
        "title": "奶糖自证与指认", "duration_seconds": 6.4,
        "generation_duration_seconds": 7, "scene_slug": "kitchen",
        "speaker": "naitang", "dialogue": "我昨晚一直在睡觉。豆包半夜去过厨房，我听见了。",
        "dialogue_offset_seconds": 0.55,
        "action": "Naitang sits at the kitchen doorway, looks earnestly at Doubao, then points suspicion toward Doubao while Doubao reacts silently",
        "start_state": "Naitang sits screen-left at the kitchen doorway while Doubao waits screen-right.",
        "end_state": "Naitang points toward Doubao; Doubao stays screen-right and reacts silently.",
        "transition": "match_doorway_axis", "continuity_source_ids": ("shot_02",),
    },
    {
        "title": "豆包否认", "duration_seconds": 4.2,
        "generation_duration_seconds": 5, "scene_slug": "kitchen",
        "speaker": "doubao", "dialogue": "我去喝水。", "dialogue_offset_seconds": 0.65,
        "action": "Doubao looks at Naitang, then the empty bag",
        "start_state": "Naitang points toward Doubao; Doubao stays screen-right and reacts silently.",
        "end_state": "Doubao faces Naitang after looking at the empty bag.",
        "transition": "match_accusation_to_reply", "continuity_source_ids": ("shot_03",),
    },
    {
        "title": "豆包反击", "duration_seconds": 7.3,
        "generation_duration_seconds": 8, "scene_slug": "kitchen",
        "speaker": "doubao", "dialogue": "倒是你，回来时胡子上有一股鸡肉味。",
        "dialogue_offset_seconds": 0.55,
        "action": "Doubao accuses Naitang; Naitang avoids eye contact and licks his mouth once",
        "start_state": "Doubao faces Naitang after looking at the empty bag.",
        "end_state": "Naitang avoids eye contact with crumbs beginning to show by his mouth.",
        "transition": "match_reply_to_counteraccusation", "continuity_source_ids": ("shot_04",),
    },
    {
        "title": "监控线索", "duration_seconds": 6.1,
        "generation_duration_seconds": 7, "scene_slug": "kitchen",
        "speaker": "owner", "dialogue": "监控只拍到一条尾巴。", "dialogue_offset_seconds": -0.20,
        "action": "low-angle kitchen replay shows the freeze-dried treat bag by the cabinet",
        "start_state": "Naitang avoids eye contact with crumbs beginning to show by his mouth.",
        "end_state": "The replay holds the treat bag beside the cabinet before a tail enters.",
        "transition": "audio_j_cut_to_replay", "continuity_source_ids": ("shot_05",),
    },
    {
        "title": "橘尾闪过", "duration_seconds": 4.8,
        "generation_duration_seconds": 5, "scene_slug": "kitchen",
        "speaker": None, "dialogue": "", "dialogue_offset_seconds": 0.0,
        "action": "only one orange tail crosses the frame edge in the kitchen replay; no full cat and no extra animal",
        "start_state": "The replay holds the treat bag beside the cabinet before a tail enters.",
        "end_state": "One orange tail exits frame left while the treat bag remains beside the cabinet.",
        "transition": "match_tail_right_to_left_with_audio_l_cut", "continuity_source_ids": ("shot_05", "shot_06"),
    },
    {
        "title": "奶糖狡辩", "duration_seconds": 7.0,
        "generation_duration_seconds": 8, "scene_slug": "kitchen",
        "speaker": "naitang", "dialogue": "橘色尾巴那么多，不能因为颜色就怀疑一只无辜的小猫。",
        "dialogue_offset_seconds": 0.55,
        "action": "Naitang medium-close-up with a few freeze-dried crumbs continuously visible by his mouth as he continues the same defense",
        "start_state": "Naitang returns screen-left with crumbs continuously visible by his mouth.",
        "end_state": "Naitang finishes his defense with crumbs continuously visible by his mouth.",
        "transition": "match_tail_to_naitang_closeup", "continuity_source_ids": ("shot_07",),
    },
    {
        "title": "镜子证据与质问", "duration_seconds": 5.5,
        "generation_duration_seconds": 6, "scene_slug": "kitchen",
        "speaker": "doubao", "dialogue": "那你嘴边这个是什么？", "dialogue_offset_seconds": 2.55,
        "action": "Doubao slowly pushes a small mirror in front of Naitang; freeze-dried crumbs continuously visible by Naitang's mouth and the mirror remains continuously visible as Naitang pauses before Doubao questions him",
        "start_state": "Naitang finishes his defense with crumbs continuously visible by his mouth.",
        "end_state": "The mirror remains visible as Naitang sees the crumbs by his mouth.",
        "transition": "match_crumbs_to_mirror_reveal", "continuity_source_ids": ("shot_08",),
    },
    {
        "title": "奶糖反转", "duration_seconds": 4.1,
        "generation_duration_seconds": 5, "scene_slug": "kitchen",
        "speaker": "naitang", "dialogue": "证据也可能是后来粘上去的。", "dialogue_offset_seconds": 0.75,
        "action": "Naitang looks at the camera with crumbs continuously visible by his mouth; the mirror remains continuously visible, the owner lightly laughs, and Doubao slow-blinks",
        "start_state": "The mirror remains visible as Naitang sees the crumbs by his mouth.",
        "end_state": "Naitang faces the camera with crumbs and mirror visible while Doubao slow-blinks.",
        "transition": "hold_for_comedic_button", "continuity_source_ids": ("shot_09",),
    },
)


class PetSitcomError(ValueError):
    pass


@dataclass(frozen=True)
class PetCharacter:
    slug: str
    name: str
    description: str
    voice_description: str
    prompt: str
    reference_path: Path


@dataclass(frozen=True)
class PetScene:
    slug: str
    name: str
    prompt: str
    anchor_path: Path


@dataclass(frozen=True)
class PetShot:
    shot_id: str
    index: int
    title: str
    duration_seconds: float
    generation_duration_seconds: int
    scene_slug: str
    speaker: str | None
    dialogue: str
    dialogue_offset_seconds: float
    action: str
    start_state: str
    end_state: str
    transition: str
    continuity_source_ids: tuple[str, ...]
    base_prompt: str
    generate_audio: bool
    candidate_dir: Path


@dataclass(frozen=True)
class PetSitcomPlan:
    project_id: str
    title: str
    duration_seconds: float
    output_dir: Path
    characters: tuple[PetCharacter, ...]
    scenes: tuple[PetScene, ...]
    shots: tuple[PetShot, ...]
    audio_manifest_path: Path
    audio_probe_path: Path
    audio_probe_review_path: Path
    plan_path: Path
    generation_report_path: Path
    selection_path: Path
    dialogue_timing_path: Path
    shot_review_path: Path
    clean_output: Path
    release_output: Path
    review_markdown_path: Path

    def all_output_paths(self) -> tuple[Path, ...]:
        candidate_paths = tuple(
            path
            for shot in self.shots
            for path in (
                shot.candidate_dir / "candidate_001.mp4",
                shot.candidate_dir / "candidate_001.report.json",
            )
        )
        continuity_paths = tuple(
            self.output_dir / "continuity" / f"{source_id}_last.png"
            for shot in self.shots
            for source_id in shot.continuity_source_ids
        )
        return (
            *(character.reference_path for character in self.characters),
            *(scene.anchor_path for scene in self.scenes),
            self.audio_manifest_path,
            self.audio_probe_path,
            self.audio_probe_review_path,
            self.plan_path,
            *candidate_paths,
            *continuity_paths,
            self.generation_report_path,
            self.selection_path,
            self.dialogue_timing_path,
            self.shot_review_path,
            self.output_dir / "evidence",
            self.clean_output,
            self.release_output,
            self.review_markdown_path,
        )

    def to_report(self) -> dict[str, Any]:
        anchors = {scene.slug: scene.anchor_path for scene in self.scenes}
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "project_id": self.project_id,
            "title": self.title,
            "duration_seconds": self.duration_seconds,
            "output_dir": str(self.output_dir),
            "providers": {"image": "gateway", "video": "gateway", "owner_audio": "doubao"},
            "models": {
                "image": IMAGE_MODEL,
                "video": VIDEO_MODEL,
                "owner_audio": OWNER_AUDIO_MODEL,
            },
            "video_defaults": {
                "ratio": VIDEO_RATIO,
                "resolution": VIDEO_RESOLUTION,
            },
            "reference_order": list(_REFERENCE_ORDER),
            "characters": [
                {
                    "slug": character.slug,
                    "name": character.name,
                    "description": character.description,
                    "voice_description": character.voice_description,
                    "prompt": character.prompt,
                    "reference_path": str(character.reference_path),
                    "provider": "gateway",
                    "model": IMAGE_MODEL,
                }
                for character in self.characters
            ],
            "scenes": [
                {
                    "slug": scene.slug,
                    "name": scene.name,
                    "prompt": scene.prompt,
                    "anchor_path": str(scene.anchor_path),
                    "provider": "gateway",
                    "model": IMAGE_MODEL,
                }
                for scene in self.scenes
            ],
            "shots": [
                _shot_report(shot, self.characters, anchors) for shot in self.shots
            ],
            "artifacts": {
                "audio_manifest_path": str(self.audio_manifest_path),
                "audio_probe_path": str(self.audio_probe_path),
                "audio_probe_review_path": str(self.audio_probe_review_path),
                "plan_path": str(self.plan_path),
                "generation_report_path": str(self.generation_report_path),
                "selection_path": str(self.selection_path),
                "dialogue_timing_path": str(self.dialogue_timing_path),
                "shot_review_path": str(self.shot_review_path),
                "clean_output": str(self.clean_output),
                "release_output": str(self.release_output),
                "review_markdown_path": str(self.review_markdown_path),
            },
        }


def build_pet_sitcom_plan(
    config: Mapping[str, Any],
    output_dir: str | Path | None = None,
) -> PetSitcomPlan:
    if not isinstance(config, Mapping):
        raise PetSitcomError("config must be a mapping.")
    root = _safe_output_dir(output_dir)
    characters = _build_characters(root)
    scenes = _build_scenes(root)
    plan = PetSitcomPlan(
        project_id=PROJECT_ID,
        title=TITLE,
        duration_seconds=FINAL_DURATION_SECONDS,
        output_dir=root,
        characters=characters,
        scenes=scenes,
        shots=_build_shots(root),
        audio_manifest_path=root / "audio_manifest.json",
        audio_probe_path=root / "audio_probe.json",
        audio_probe_review_path=root / "audio_probe_review.json",
        plan_path=root / "pet_sitcom_plan.json",
        generation_report_path=root / "generation_report.json",
        selection_path=root / "selected_candidates.json",
        dialogue_timing_path=root / "dialogue_timings.json",
        shot_review_path=root / "shot_review.json",
        clean_output=root / "final" / "冻干到底是谁偷吃的_清洁版.mp4",
        release_output=root / "final" / "冻干到底是谁偷吃的_发布版.mp4",
        review_markdown_path=root / "review.md",
    )
    _validate_plan_contract(plan)
    return plan


def write_pet_sitcom_plan(plan: PetSitcomPlan) -> Path:
    _validate_plan_contract(plan)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(plan.output_dir, "output_dir")
    _reject_symlink_components(plan.plan_path, "plan_path")
    if plan.plan_path.is_symlink():
        raise PetSitcomError("plan_path must not be a symlink.")

    payload = json.dumps(
        plan.to_report(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=plan.output_dir,
            prefix=f".{plan.plan_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, plan.plan_path)
        temporary_path = None
        _fsync_directory(plan.output_dir)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return plan.plan_path


def _build_characters(output_dir: Path) -> tuple[PetCharacter, ...]:
    return tuple(
        PetCharacter(
            slug=str(spec["slug"]),
            name=str(spec["name"]),
            description=str(spec["description"]),
            voice_description=str(spec["voice_description"]),
            prompt=str(spec["prompt"]),
            reference_path=output_dir / "characters" / str(spec["reference_name"]),
        )
        for spec in _CHARACTER_SPECS
    )


def _build_scenes(output_dir: Path) -> tuple[PetScene, ...]:
    return tuple(
        PetScene(
            slug=str(spec["slug"]),
            name=str(spec["name"]),
            prompt=str(spec["prompt"]),
            anchor_path=output_dir / "scenes" / f"{spec['slug']}.png",
        )
        for spec in _SCENE_SPECS
    )


def _build_shots(output_dir: Path) -> tuple[PetShot, ...]:
    shots = []
    for index, spec in enumerate(_SHOT_SPECS, start=1):
        speaker = spec["speaker"]
        dialogue = str(spec["dialogue"])
        shots.append(
            PetShot(
                shot_id=f"shot_{index:02d}",
                index=index,
                title=str(spec["title"]),
                duration_seconds=float(spec["duration_seconds"]),
                generation_duration_seconds=int(spec["generation_duration_seconds"]),
                scene_slug=str(spec["scene_slug"]),
                speaker=speaker if speaker is None else str(speaker),
                dialogue=dialogue,
                dialogue_offset_seconds=float(spec["dialogue_offset_seconds"]),
                action=str(spec["action"]),
                start_state=str(spec["start_state"]),
                end_state=str(spec["end_state"]),
                transition=str(spec["transition"]),
                continuity_source_ids=tuple(spec["continuity_source_ids"]),
                base_prompt=_shot_prompt(str(spec["action"]), speaker, dialogue),
                generate_audio=True,
                candidate_dir=output_dir / "shots" / f"shot_{index:02d}",
            )
        )
    return tuple(shots)


def _shot_prompt(action: str, speaker: object, dialogue: str) -> str:
    if speaker is None:
        audio_instruction = (
            "Do not generate speech or vocalization. Preserve Seedance room tone, "
            "prop, and natural audio."
        )
    elif speaker == "owner":
        audio_instruction = (
            "Do not generate native human or animal speech or vocalization. The "
            f'owner dialogue "{dialogue}" is a Doubao Seed-TTS overlay added later. '
            "Both cats react silently and never move their mouths as speech. "
            "Preserve Seedance room tone, prop, and natural audio."
        )
    else:
        name = "Naitang" if speaker == "naitang" else "Doubao"
        other_name = "Doubao" if speaker == "naitang" else "Naitang"
        audio_instruction = (
            f'Only {name} speaks and says exactly "{dialogue}". {other_name} '
            "reacts silently and never moves their mouth as speech."
        )
    return (
        f"{_SHARED_VIDEO_CONSTRAINTS} {_SPATIAL_CONTINUITY_CONSTRAINTS} "
        f"Approved action: {action}. "
        f"{audio_instruction} Reserve the first 0.20 seconds without spoken words "
        f"and the final {DIALOGUE_TAIL_SECONDS:.2f} seconds without spoken words."
    )


def _shot_report(
    shot: PetShot,
    characters: tuple[PetCharacter, ...],
    anchors: dict[str, Path],
) -> dict[str, Any]:
    references = [
        characters[0].reference_path,
        characters[1].reference_path,
        anchors[shot.scene_slug],
    ]
    references.extend(
        shot.candidate_dir.parent.parent / "continuity" / f"{source_id}_last.png"
        for source_id in shot.continuity_source_ids
    )
    return {
        "shot_id": shot.shot_id,
        "index": shot.index,
        "title": shot.title,
        "duration_seconds": shot.duration_seconds,
        "edit_duration_seconds": shot.duration_seconds,
        "generation_duration_seconds": shot.generation_duration_seconds,
        "scene_slug": shot.scene_slug,
        "speaker": shot.speaker,
        "dialogue": shot.dialogue,
        "dialogue_offset_seconds": shot.dialogue_offset_seconds,
        "action": shot.action,
        "start_state": shot.start_state,
        "end_state": shot.end_state,
        "transition": shot.transition,
        "continuity_source_ids": list(shot.continuity_source_ids),
        "base_prompt": shot.base_prompt,
        "generate_audio": shot.generate_audio,
        "audio_mode": _audio_mode(shot),
        "native_audio": shot.generate_audio,
        "candidate_dir": str(shot.candidate_dir),
        "provider": "gateway",
        "model": VIDEO_MODEL,
        "ratio": VIDEO_RATIO,
        "resolution": VIDEO_RESOLUTION,
        "references": [str(path) for path in references],
        "candidate_1": {
            "candidate_number": 1,
            "output_path": str(shot.candidate_dir / "candidate_001.mp4"),
            "report_path": str(shot.candidate_dir / "candidate_001.report.json"),
        },
    }


def _audio_mode(shot: PetShot) -> str:
    if shot.speaker in {"naitang", "doubao"}:
        return "native_seedance_dialogue_and_ambience"
    if shot.speaker == "owner":
        return "native_ambience_with_doubao_seed_tts_overlay"
    return "native_ambience_and_prop_audio"


def _safe_output_dir(output_dir: str | Path | None) -> Path:
    candidate = DEFAULT_OUTPUT_DIR if output_dir is None else Path(output_dir)
    candidate = candidate.expanduser().absolute()
    _reject_symlink_components(candidate, "output_dir")
    return candidate.resolve()


def _validate_plan_contract(plan: PetSitcomPlan) -> None:
    if not isinstance(plan, PetSitcomPlan):
        raise PetSitcomError("plan must be a PetSitcomPlan.")
    _reject_symlink_components(plan.output_dir, "output_dir")
    expected_output_dir = plan.output_dir.resolve()
    if plan.output_dir != expected_output_dir:
        raise PetSitcomError("plan.output_dir must be a resolved safe path.")
    expected_characters = _build_characters(expected_output_dir)
    expected_scenes = _build_scenes(expected_output_dir)
    expected_shots = _build_shots(expected_output_dir)
    _validate_continuity_dependencies(plan.shots)
    if plan.project_id != PROJECT_ID:
        raise PetSitcomError("plan.project_id must match the fixed pet sitcom project.")
    if plan.title != TITLE:
        raise PetSitcomError("plan.title must match the approved story title.")
    if plan.duration_seconds != FINAL_DURATION_SECONDS:
        raise PetSitcomError("plan.duration_seconds must match the approved duration.")
    if sum(shot.duration_seconds for shot in plan.shots) != FINAL_DURATION_SECONDS:
        raise PetSitcomError("shot durations must match the approved duration.")
    if plan.characters != expected_characters:
        raise PetSitcomError("plan.characters do not match the approved contract.")
    if plan.scenes != expected_scenes:
        raise PetSitcomError("plan.scenes do not match the approved contract.")
    if plan.shots != expected_shots:
        raise PetSitcomError("plan.shots do not match the approved contract.")
    expected_paths = {
        "audio_manifest_path": expected_output_dir / "audio_manifest.json",
        "audio_probe_path": expected_output_dir / "audio_probe.json",
        "audio_probe_review_path": expected_output_dir / "audio_probe_review.json",
        "plan_path": expected_output_dir / "pet_sitcom_plan.json",
        "generation_report_path": expected_output_dir / "generation_report.json",
        "selection_path": expected_output_dir / "selected_candidates.json",
        "dialogue_timing_path": expected_output_dir / "dialogue_timings.json",
        "shot_review_path": expected_output_dir / "shot_review.json",
        "clean_output": expected_output_dir / "final" / "冻干到底是谁偷吃的_清洁版.mp4",
        "release_output": expected_output_dir / "final" / "冻干到底是谁偷吃的_发布版.mp4",
        "review_markdown_path": expected_output_dir / "review.md",
    }
    for field_name, expected_path in expected_paths.items():
        if getattr(plan, field_name) != expected_path:
            raise PetSitcomError(
                f"plan.{field_name} must stay inside the fixed output_dir."
            )
    for path in plan.all_output_paths():
        _require_within(path, expected_output_dir, "plan output path")
        _reject_symlink_components(path, "plan output path")


def _validate_continuity_dependencies(shots: tuple[PetShot, ...]) -> None:
    shot_ids = {shot.shot_id for shot in shots}
    shot_indexes = {shot.shot_id: shot.index for shot in shots}
    dependencies = {
        shot.shot_id: shot.continuity_source_ids
        for shot in shots
    }
    for shot in shots:
        if shot.generation_duration_seconds < shot.duration_seconds:
            raise PetSitcomError("generation duration must cover edit duration.")
        if not shot.start_state or not shot.end_state or not shot.transition:
            raise PetSitcomError("each shot requires declared continuity states.")
        if shot.speaker in {"naitang", "doubao"}:
            if shot.dialogue_offset_seconds < 0:
                raise PetSitcomError("cat dialogue offsets must be non-negative.")
            if shot.dialogue_offset_seconds > (
                shot.duration_seconds - DIALOGUE_TAIL_SECONDS
            ):
                raise PetSitcomError(
                    "cat dialogue offset cannot enter the dialogue tail reserve."
                )
        elif shot.shot_id == "shot_06" and shot.speaker == "owner":
            if shot.dialogue_offset_seconds != -0.20:
                raise PetSitcomError("shot_06 owner dialogue must use the approved J-cut.")
        elif shot.dialogue_offset_seconds < 0:
            raise PetSitcomError("only shot_06 may have a negative dialogue offset.")
        for source_id in shot.continuity_source_ids:
            if source_id not in shot_ids:
                raise PetSitcomError("continuity dependency references an unknown shot.")
            if source_id == shot.shot_id:
                raise PetSitcomError("continuity dependency cannot reference itself.")
            if shot_indexes[source_id] >= shot.index:
                raise PetSitcomError("continuity dependency cannot reference a forward shot.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(shot_id: str) -> None:
        if shot_id in visiting:
            raise PetSitcomError("continuity dependencies cannot contain a cycle.")
        if shot_id in visited:
            return
        visiting.add(shot_id)
        for source_id in dependencies[shot_id]:
            visit(source_id)
        visiting.remove(shot_id)
        visited.add(shot_id)

    for shot_id in dependencies:
        visit(shot_id)


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PetSitcomError(f"{label} must be inside output_dir.") from exc


def _reject_symlink_components(path: Path, label: str) -> None:
    absolute_path = path.expanduser().absolute()
    current = Path(absolute_path.anchor)
    for part in absolute_path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PetSitcomError(f"{label} must not use a symlink: {current}")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
