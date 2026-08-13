# Original AI Pet Sitcom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and deliver an original 70-second vertical photorealistic pet sitcom, with two consistent AI cats, restrained speaking-mouth motion, an off-screen owner, a clear mystery/reversal story, and evidence-backed quality review.

**Architecture:** Add focused modules for the immutable story plan, guarded provider generation, local audio/composition, and review evidence. Reuse the existing gateway image/video and Doubao TTS clients, but isolate all runtime assets under a dedicated Desktop output directory. Require an approved six-second mouth test before any of the fourteen production shots can be submitted.

**Tech Stack:** Python 3.12, dataclasses, Pillow, FFmpeg/FFprobe, pytest, existing `GatewayImageClient`, `GatewayVideoClient`, `render_gateway_video_single`, `DoubaoTTSClient`, Doubao Seedream 4.5, Doubao Seedance 2.0, Doubao Seed-TTS 2.0.

## Global Constraints

- Implement `docs/superpowers/specs/2026-07-23-original-ai-pet-sitcom-design.md`.
- The story is the original work `冻干到底是谁偷吃的？`; do not use reference-video frames, audio, dialogue, watermark, characters, or exact visual packaging.
- Deliver 1080×1920, 9:16, H.264/AAC, 30fps, with a target duration of exactly 70 seconds.
- Use fourteen five-second production clips. Preserve Seedance source motion and never use optical flow, `minterpolate`, or a 6/8/10fps intermediate cadence.
- Use `doubao-seedream-4-5` for two character sheets and two empty scene anchors.
- Use `doubao-seedance-2-0` with native audio for the mouth test and all production video clips.
- Use Doubao Seed-TTS 2.0 only for the off-screen owner lines.
- A six-second Naitang mouth test must be source-bound, manually reviewed, and approved before production-shot generation is allowed.
- Generate candidate 1 first. A failed mouth test or failed production shot may receive exactly one targeted candidate-2 retry.
- Generate production shots sequentially; every shot after shot 1 uses the selected previous shot's ending frame.
- If an upstream selected candidate changes, invalidate all downstream selections whose stored continuity hash no longer matches.
- Keep all runtime assets below `~/Desktop/宠物短剧样片/冻干案_20260723/`.
- Provider calls are disabled unless `--enable-live` is passed.
- Never serialize API keys, authorization values, signed URLs, image data URIs, or request headers.
- Mouth-sync review is subjective frame-by-frame review, not phoneme-level certification.

---

### Task 1: Immutable Story, Shot, Prompt, And Path Contracts

**Files:**
- Create: `factory/pet_sitcom.py`
- Create: `tests/test_pet_sitcom.py`

**Interfaces:**
- Produces `PetSitcomError`, `PetCharacter`, `PetScene`, `PetShot`, and `PetSitcomPlan`.
- Produces `build_pet_sitcom_plan(config: Mapping[str, Any], output_dir: str | Path | None = None) -> PetSitcomPlan`.
- Produces `write_pet_sitcom_plan(plan: PetSitcomPlan) -> Path`.
- Later tasks consume exact role prompts, scene prompts, shot prompts, dialogue, speakers, audio modes, paths, and reference order.

- [ ] **Step 1: Write failing immutable-plan tests**

```python
def test_plan_has_original_roles_scenes_and_fourteen_five_second_shots(config):
    plan = build_pet_sitcom_plan(config)

    assert [character.slug for character in plan.characters] == [
        "naitang",
        "doubao",
    ]
    assert [scene.slug for scene in plan.scenes] == [
        "living_room",
        "kitchen",
    ]
    assert len(plan.shots) == 14
    assert sum(shot.duration_seconds for shot in plan.shots) == 70
    assert all(shot.duration_seconds == 5 for shot in plan.shots)
    assert plan.title == "冻干到底是谁偷吃的？"


def test_plan_uses_exact_story_dialogue_and_speakers(config):
    plan = build_pet_sitcom_plan(config)

    assert [(shot.shot_id, shot.speaker, shot.dialogue) for shot in plan.shots] == [
        ("shot_01", "owner", "谁把新开的冻干吃完了？"),
        ("shot_02", None, ""),
        ("shot_03", "naitang", "我昨晚一直在睡觉。"),
        ("shot_04", "naitang", "豆包半夜去过厨房，我听见了。"),
        ("shot_05", "doubao", "我去喝水。"),
        ("shot_06", "doubao", "倒是你，回来时胡子上有一股鸡肉味。"),
        ("shot_07", "owner", "监控只拍到一条尾巴。"),
        ("shot_08", None, ""),
        ("shot_09", None, ""),
        ("shot_10", "naitang", "橘色尾巴那么多，"),
        ("shot_11", "naitang", "不能因为颜色就怀疑一只无辜的小猫。"),
        ("shot_12", None, ""),
        ("shot_13", "doubao", "那你嘴边这个是什么？"),
        ("shot_14", "naitang", "证据也可能是后来粘上去的。"),
    ]


def test_plan_isolated_under_desktop_output(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet_case")

    assert plan.output_dir == (tmp_path / "pet_case").resolve()
    assert plan.release_output.name == "冻干到底是谁偷吃的_发布版.mp4"
    assert plan.clean_output.name == "冻干到底是谁偷吃的_清洁版.mp4"
    assert all(
        path == plan.output_dir or plan.output_dir in path.parents
        for path in plan.all_output_paths()
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom.py
```

Expected: collection fails because `factory.pet_sitcom` does not exist.

- [ ] **Step 3: Implement exact public dataclasses**

```python
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
    duration_seconds: int
    scene_slug: str
    speaker: str | None
    dialogue: str
    action: str
    base_prompt: str
    generate_audio: bool
    candidate_dir: Path
    continuity_frame: Path | None


@dataclass(frozen=True)
class PetSitcomPlan:
    project_id: str
    title: str
    output_dir: Path
    characters: tuple[PetCharacter, ...]
    scenes: tuple[PetScene, ...]
    shots: tuple[PetShot, ...]
    mouth_test_path: Path
    mouth_test_review_path: Path
    plan_path: Path
    generation_report_path: Path
    selection_path: Path
    dialogue_timing_path: Path
    shot_review_path: Path
    clean_output: Path
    release_output: Path
    review_markdown_path: Path
```

The default output is:

```python
(Path.home() / "Desktop" / "宠物短剧样片" / "冻干案_20260723").resolve()
```

- [ ] **Step 4: Implement character-sheet and scene-anchor prompts**

Naitang's single reference image is a text-free triptych of the same exact photorealistic orange-and-white short-haired cat: front portrait, three-quarter head-and-body, and full body. Require round face, amber eyes, slightly round body, recognizable white fur near the left mouth corner, unchanged markings, neutral studio light, plain warm-gray background, no collar, no accessories, no labels, no extra animal.

Doubao's single reference image is a text-free triptych of the same exact photorealistic black-and-white tuxedo cat: front portrait, three-quarter head-and-body, and full body. Require narrower face, green eyes, slimmer body, continuous white nose-to-chin marking, white chest and paws, unchanged markings, neutral studio light, plain warm-gray background, no collar, no accessories, no labels, no extra animal.

Living-room and kitchen prompts must share:

- warm natural daylight from frame left,
- honey-colored wood floor,
- light neutral furniture,
- fixed home layout,
- realistic phone-camera photography,
- no person, animal, silhouette, text, logo, watermark, food package, or mirror.

- [ ] **Step 5: Implement the shared video prompt contract**

Every production prompt must identify references in this order:

1. Naitang immutable character sheet.
2. Doubao immutable character sheet.
3. Current empty scene anchor.
4. Previous selected ending frame when the shot index is greater than one.

The shared constraints must require two distinct cats, unchanged markings/eyes/body proportions, realistic feline anatomy and weight, grounded paws, natural whiskers/ears/tails, restrained jaw movement, only the designated speaker moving their mouth as speech, stable camera, no digital zoom, no optical-flow look, no floating, no duplicated body parts, no extra animal, no human face, no text, no subtitle, and no watermark.

Each shot prompt must encode the exact action in the approved design and reserve the first and final 0.20 seconds without spoken words.

- [ ] **Step 6: Validate safe paths and write the plan artifact**

Reject symlinks in the output path chain and any path escaping `plan.output_dir`. `write_pet_sitcom_plan` writes an atomic JSON document containing:

- schema version,
- exact original dialogue,
- role, scene, and shot prompts,
- model IDs,
- duration, ratio, resolution, native-audio flags,
- local output paths,
- reference order,
- no credentials.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom.py
.venv/bin/ruff check factory/pet_sitcom.py tests/test_pet_sitcom.py
```

Expected: all plan, story, prompt, and path tests pass without network access.

- [ ] **Step 8: Commit Task 1**

```bash
git add factory/pet_sitcom.py tests/test_pet_sitcom.py
git commit -m "feat: plan original AI pet sitcom"
```

### Task 2: Resumable Character Anchors, Scene Anchors, And Mouth-Test Gate

**Files:**
- Create: `factory/pet_sitcom_generation.py`
- Create: `tests/test_pet_sitcom_generation.py`

**Interfaces:**
- Consumes `PetSitcomPlan`.
- Produces `generate_pet_sitcom_anchors(...) -> dict[str, Any]`.
- Produces `generate_pet_mouth_test(...) -> dict[str, Any]`.
- Produces `approve_pet_mouth_test(...) -> dict[str, Any]`.
- Reuses `GatewayImageClient.generate` and `render_gateway_video_single`.

- [ ] **Step 1: Write failing dry-run, normalization, and gate tests**

```python
def test_anchor_dry_run_makes_no_provider_calls(plan, fake_clients):
    report = generate_pet_sitcom_anchors(
        plan,
        image_client=fake_clients.image,
        allow_network=False,
    )

    assert report["planned_count"] == 4
    assert report["executed"] is False
    assert fake_clients.image.calls == []


def test_anchor_generation_normalizes_real_png_files(plan, jpeg_image_client):
    report = generate_pet_sitcom_anchors(
        plan,
        image_client=jpeg_image_client,
        allow_network=True,
    )

    assert report["success"] is True
    for path in plan.character_reference_paths + plan.scene_anchor_paths:
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_production_shots_block_until_mouth_test_is_approved(plan):
    with pytest.raises(PetSitcomGenerationError, match="mouth test"):
        require_approved_mouth_test(plan)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py
```

Expected: collection fails because `factory.pet_sitcom_generation` does not exist.

- [ ] **Step 3: Implement signature-bound anchor generation**

For each of the four image jobs:

- model `doubao-seedream-4-5`,
- size `1440x2560`,
- one output,
- no reference image,
- candidate 1 only.

Hash provider, model, prompt, size, and candidate number into a sidecar state. Generate into a temporary file, validate its real JPEG/PNG/WebP signature, decode with Pillow, convert to RGB, write a true PNG, fsync, and atomically replace the planned output. Reuse only when the state signature and current PNG hash match.

- [ ] **Step 4: Implement anchor review input**

Write `anchor_review_template.json` after image generation with four source hashes and required booleans:

- Naitang is one consistent cat across all three panels.
- Doubao is one consistent cat across all three panels.
- Their markings, eye colors, face shapes, and body types remain clearly distinct.
- Living room and kitchen contain no person, animal, text, or watermark.
- Both scenes share light direction, wood floor, and home design.

`approve_pet_anchors` accepts a completed review only when all booleans are true and all source hashes still match.

- [ ] **Step 5: Implement the six-second mouth test**

The mouth test uses:

- Naitang reference,
- living-room anchor,
- `doubao-seedance-2-0`,
- duration 6,
- ratio 9:16,
- resolution 1080p,
- `generate_audio=True`,
- exact line `我昨晚一直在睡觉。`,
- candidate path `tests/mouth_test/candidate_001.mp4`.

The prompt must show only Naitang in a medium close shot, use a fixed camera, keep realistic whiskers and cat mouth anatomy, request restrained jaw opening, forbid a human-like mouth or teeth, reserve 0.20 seconds of silent closed mouth at both ends, and forbid any other voice.

- [ ] **Step 6: Implement source-bound mouth-test approval**

`mouth_test_review.json` stores:

- candidate number and MP4 SHA-256,
- correct Naitang identity,
- photorealistic feline face,
- no human-mouth deformation,
- correct speaker,
- exact intelligible line,
- visible mouth and jaw movement,
- subjective start/pause/end alignment,
- no extra animal/text/watermark,
- pass/fail,
- notes,
- retry reason when failed.

Only a passing review with the current source hash unlocks production shots. Candidate 2 requires a failed candidate-1 review and one reason from `mouth_anatomy`, `wrong_speaker`, `lip_timing`, `identity`, or `extra_content`. Candidate 2 failure stops the workflow.

- [ ] **Step 7: Redact reports and test resumability**

Use the existing recursive secret-sanitization patterns. Reports may contain task IDs, local paths, hashes, models, durations, and sanitized errors. They may not contain credentials, signed URLs, data URIs, or authorization values.

Add tests proving:

- matching completed anchors are reused,
- a persisted mouth-test gateway task is resumed instead of resubmitted,
- a stale review hash does not unlock production,
- candidate 3 is rejected.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py
.venv/bin/pytest -q tests/test_gateway_image.py tests/test_gateway_video_batch.py
.venv/bin/ruff check factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
```

Expected: anchors and mouth test are dry-run-first, resumable, normalized, source-bound, and production remains locked before approval.

- [ ] **Step 9: Commit Task 2**

```bash
git add factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
git commit -m "feat: gate pet sitcom generation with mouth test"
```

### Task 3: Sequential Fourteen-Shot Generation And Candidate Provenance

**Files:**
- Modify: `factory/pet_sitcom_generation.py`
- Modify: `tests/test_pet_sitcom_generation.py`

**Interfaces:**
- Produces `generate_pet_sitcom_shots(...) -> dict[str, Any]`.
- Produces `select_pet_shot_candidate(...) -> dict[str, Any]`.
- Produces `extract_pet_continuity_frame(...) -> Path`.
- Produces and validates `selected_candidates.json`.

- [ ] **Step 1: Write failing sequence, audio, and invalidation tests**

```python
def test_shots_generate_in_order_with_native_audio_and_continuity(
    plan, approved_inputs, fake_video_client
):
    report = generate_pet_sitcom_shots(
        plan,
        video_client=fake_video_client,
        allow_network=True,
    )

    assert report["completed_count"] == 14
    assert all(call.generate_audio is True for call in fake_video_client.calls)
    assert fake_video_client.calls[0].images == [
        plan.naitang_reference,
        plan.doubao_reference,
        plan.living_room_anchor,
    ]
    assert fake_video_client.calls[1].images[-1] == (
        plan.output_dir / "continuity" / "shot_01_last.png"
    )


def test_replacing_upstream_candidate_invalidates_downstream(plan, selected_chain):
    selection = select_pet_shot_candidate(plan, "shot_05", 2)

    assert selection["shots"]["shot_05"]["status"] == "selected"
    for index in range(6, 15):
        assert selection["shots"][f"shot_{index:02d}"]["status"] == "stale_upstream"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py -k "shots or upstream"
```

Expected: failures because production-shot orchestration is absent.

- [ ] **Step 3: Build exact deterministic shot jobs**

Candidate paths are:

```text
shots/shot_01/candidate_001.mp4
shots/shot_01/candidate_001.report.json
shots/shot_14/candidate_002.mp4
shots/shot_14/candidate_002.report.json
```

Every call uses:

```python
render_gateway_video_single(
    shot_prompt,
    output_path,
    video_client,
    report_path,
    images=ordered_reference_paths,
    duration=5,
    ratio="9:16",
    resolution="1080p",
    generate_audio=True,
    allow_network=allow_network,
    overwrite=False,
    report_sanitizer=sanitize_pet_sitcom_report,
)
```

Shots 1–2 use the living room, shots 3–7 use the kitchen doorway, shot 8 uses the kitchen low-angle anchor, shots 9–14 return to the living-room/kitchen-doorway geometry required by their action. The plan's exact `scene_slug` decides the third reference.

- [ ] **Step 4: Enforce exact speaker and action prompts**

- Owner shots 1 and 7: native video audio contains room tone and prop sound only; no human or animal speech. Owner TTS is added later.
- Silent shots 2, 8, 9, and 12: no speech, mumble, or voice.
- Naitang speaks only in shots 3, 4, 10, 11, and 14.
- Doubao speaks only in shots 5, 6, and 13.
- The non-speaking cat reacts through eyes, ears, tail, posture, or one non-speech feline sound, but never sustained speaking mouth movement.
- Shot 8 must show only an orange tail crossing the edge, not a full extra cat.
- Freeze-dried crumbs first become clearly visible in shot 10 and remain consistent through shot 14.
- The mirror appears only in shots 12–14.

- [ ] **Step 5: Extract and bind ending frames**

After selection, extract a PNG at 4.88 seconds and write a sidecar containing the selected video path/hash, timestamp, and frame hash. Shot N+1 is allowed only when its reference list includes the current ending-frame hash for shot N.

- [ ] **Step 6: Implement selected-candidate provenance**

Each selection stores:

- candidate number,
- status,
- video path and SHA-256,
- prompt SHA-256,
- ordered reference paths and hashes,
- previous selected video hash,
- continuity frame hash,
- selected timestamp.

Validate one video stream, one audio stream, duration 4.70–5.30 seconds, successful provider report, and matching current prompt/reference hashes before selection.

- [ ] **Step 7: Implement five targeted retry reasons**

Candidate 2 requires exactly one shot and one reason:

```python
PET_RETRY_SUFFIXES = {
    "identity": "Keep both exact reference cats unchanged in markings, eyes, face, and body proportions.",
    "paw_anatomy": "Keep all visible paws anatomically feline, grounded, separate, and free of fusion or extra toes.",
    "mouth_anatomy": "Keep the designated speaker's mouth feline and restrained, with no human lips or oversized teeth.",
    "wrong_speaker": "Only the designated cat may speak; the other cat remains vocally silent with reaction-only motion.",
    "continuity": "Match the previous ending frame's cat positions, tails, props, light direction, and room geometry at the start.",
}
```

Candidate 2 is never automatic. Candidate 3 is rejected. When an upstream selection changes, preserve old evidence but mark all downstream entries stale.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py
.venv/bin/ruff check factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
```

Expected: fourteen jobs are sequential, native-audio enabled, hash-bound, resumable, and limited to two candidates.

- [ ] **Step 9: Commit Task 3**

```bash
git add factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
git commit -m "feat: generate sequential pet sitcom shots"
```

### Task 4: Owner TTS, Original Music Bed, Verified Timings, And Composition

**Files:**
- Create: `factory/pet_sitcom_compose.py`
- Create: `tests/test_pet_sitcom_compose.py`

**Interfaces:**
- Produces `generate_owner_voice_lines(...) -> dict[str, Any]`.
- Produces `build_original_pet_music(...) -> Path`.
- Produces `load_verified_pet_timings(...) -> tuple[PetDialogueTiming, ...]`.
- Produces `compose_pet_sitcom(...) -> dict[str, Any]`.

- [ ] **Step 1: Write failing audio and composition tests**

```python
def test_owner_tts_uses_only_owner_shots(plan, fake_tts_client):
    report = generate_owner_voice_lines(
        plan,
        tts_client=fake_tts_client,
        allow_network=True,
    )

    assert [call.text for call in fake_tts_client.calls] == [
        "谁把新开的冻干吃完了？",
        "监控只拍到一条尾巴。",
    ]


def test_original_music_is_seventy_seconds_and_deterministic(tmp_path):
    first = build_original_pet_music(tmp_path / "first.wav", duration_seconds=70)
    second = build_original_pet_music(tmp_path / "second.wav", duration_seconds=70)

    assert sha256_file(first) == sha256_file(second)
    assert probe_media(first, required_stream="audio").duration_seconds == pytest.approx(
        70.0, abs=0.05
    )


def test_composition_has_direct_fps_and_no_interpolation(plan, verified_inputs):
    commands = build_pet_sitcom_ffmpeg_commands(plan)
    rendered = " ".join(" ".join(command) for command in commands)

    assert "fps=30" in rendered
    assert "minterpolate" not in rendered
    assert "fps=6" not in rendered
    assert "fps=8" not in rendered
    assert "fps=10" not in rendered
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in rendered
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_compose.py
```

Expected: collection fails because `factory.pet_sitcom_compose` does not exist.

- [ ] **Step 3: Implement resumable owner TTS**

Generate:

```text
audio/owner/shot_01.wav
audio/owner/shot_07.wav
```

Use the configured Doubao Seed-TTS 2.0 natural female voice, speech rate `-4`, 24kHz or higher source audio, and signature-bound state containing text, voice ID, speech rate, provider, and model. Trim leading/trailing digital silence without cutting breath or consonants. Reuse a matching valid file.

- [ ] **Step 4: Create an original deterministic music bed**

Write a 48kHz stereo WAV using Python's standard `wave`, `math`, and seeded `random` modules:

- 70.0 seconds,
- four-chord loop C major, A minor, F major, G major,
- soft sine/triangle plucks every 0.625 seconds,
- exponential decay under 0.45 seconds,
- very low deterministic noise shaker every 1.25 seconds,
- peak below -12dBFS before final mixing,
- no sampled or third-party copyrighted audio.

- [ ] **Step 5: Validate source-bound dialogue timings**

`dialogue_timings.json` contains one entry for all ten spoken shots: owner shots 1 and 7 plus eight cat-dialogue shots. Each entry stores shot ID, selected MP4 hash, speaker, exact text, start offset, and end offset. Cat lines are verified by waveform/listening plus frame-by-frame mouth review. Owner lines are timed from the actual trimmed TTS duration.

Require:

- start at or after 0.20 seconds,
- end at or before 4.80 seconds,
- start less than end,
- exact text and current selected hash,
- top-level `verified=true`.

- [ ] **Step 6: Build clean and release compositions**

For every selected source:

- trim to five seconds,
- set timestamps from zero,
- scale/pad to 1080×1920,
- convert directly to 30fps and `yuv420p`,
- resample native audio to 48kHz stereo.

For owner shots, mix TTS over source room tone after confirming no native voice. For all shots, apply 40ms audio seam fades with dialogue kept outside the seam. Concatenate video to exactly 70 seconds. Mix the original music bed at a low level and duck it by 8dB during every verified speech window. Normalize to `-16 LUFS`, `-1.5 dBTP`, `LRA=11`.

The clean output includes story picture, native/TTS dialogue, effects, and music, but no generated title/subtitle/end card. The release output adds:

- opening title `冻干失窃案`,
- verified shallow-yellow dialogue captions,
- white outlined evidence emphasis,
- ending card `本案嫌疑猫拒绝认罪`.

- [ ] **Step 7: Validate final technical output**

Encode H.264 High profile, CRF 18, AAC 192kbps, 48kHz stereo, `+faststart`. Require both outputs:

- duration 69.85–70.15 seconds,
- 1080×1920,
- 30fps,
- one video stream,
- one audio stream,
- no source-frame interpolation filter.

Write to temporary MP4 files and atomically replace final paths only after validation.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_compose.py
.venv/bin/pytest -q tests/test_doubao_tts.py tests/test_local_voiceover.py
.venv/bin/ruff check factory/pet_sitcom_compose.py tests/test_pet_sitcom_compose.py
```

Expected: owner TTS is scoped correctly, music is original/deterministic, timings are source-bound, and both compositions satisfy the output contract.

- [ ] **Step 9: Commit Task 4**

```bash
git add factory/pet_sitcom_compose.py tests/test_pet_sitcom_compose.py
git commit -m "feat: compose original AI pet sitcom"
```

### Task 5: Diagnostic Evidence And Structured Review

**Files:**
- Create: `factory/pet_sitcom_review.py`
- Create: `tests/test_pet_sitcom_review.py`

**Interfaces:**
- Produces `build_pet_sitcom_evidence(...) -> dict[str, Any]`.
- Produces `validate_pet_shot_reviews(...) -> dict[str, Any]`.
- Produces `write_pet_sitcom_review_markdown(...) -> Path`.

- [ ] **Step 1: Write failing evidence and review tests**

```python
def test_evidence_covers_all_shots_and_key_risks(plan, selected_chain):
    manifest = build_pet_sitcom_evidence_plan(plan)

    assert len(manifest["shot_contact_sheets"]) == 14
    assert set(manifest["mouth_sequences"]) == {
        "shot_03",
        "shot_04",
        "shot_05",
        "shot_06",
        "shot_10",
        "shot_11",
        "shot_13",
        "shot_14",
    }
    assert set(manifest["prop_sequences"]) == {
        "shot_01_bag",
        "shot_08_tail",
        "shot_10_crumbs",
        "shot_12_mirror",
        "shot_14_crumbs_and_mirror",
    }


def test_review_cannot_claim_phoneme_certification(plan, passing_reviews):
    markdown = render_pet_sitcom_review_markdown(plan, passing_reviews)

    assert "未进行音素级认证" in markdown
    assert "逐帧人工复核" in markdown
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_review.py
```

Expected: collection fails because `factory.pet_sitcom_review` does not exist.

- [ ] **Step 3: Build visual evidence**

Generate:

- one 3×3 whole-video contact sheet for each selected shot,
- one final 4×4 contact sheet spanning the 70-second release cut,
- 13-frame dense mouth sequences for all eight cat-speaking shots,
- paw close sequences for shots 1, 9, and 12,
- prop sequences for bag, orange tail, crumbs, and mirror,
- thirteen adjacent-shot continuity comparisons,
- start/cut/end frame checks for the final outputs.

Every image manifest entry stores the selected source path/hash and extraction timestamps.

- [ ] **Step 4: Build separate source and final technical evidence**

Before composition, `evidence/source_technical_qc.json` records FFprobe results, audio presence, duration, `blackdetect`, `freezedetect`, and hashes for all fourteen sources.

After composition, `evidence/final_technical_qc.json` records the same checks plus dimensions, 30fps, loudness, and final hashes for clean and release outputs.

Automatic checks may reject broken media, missing audio, black runs longer than 0.08 seconds, invalid duration, invalid resolution, invalid final frame rate, or excessive freeze. They must not claim identity, anatomy, acting, speaker, or mouth sync passed.

- [ ] **Step 5: Define complete manual shot reviews**

Each selected-shot review is bound to the source hash and requires:

- exact planned action visible,
- Naitang and Doubao identities stable,
- correct scene and light direction,
- grounded paws and valid feline anatomy,
- correct prop state,
- stable camera and no unexplained cut,
- no extra animal/person/text/watermark,
- correct designated speaker,
- visible restrained mouth/jaw motion for cat dialogue,
- no sustained speech mouth on the silent cat,
- subjective speech start/pause/end alignment,
- continuity with the previous selected ending frame,
- pass/fail, notes, and retry reason if failed.

- [ ] **Step 6: Generate `review.md`**

Include:

1. Originality and reference-use boundary.
2. Models, providers, candidates, and hashes.
3. Anchor and mouth-test results.
4. Per-shot action, identity, anatomy, speaker, and continuity findings.
5. Problems found and exact timestamps.
6. Candidate-2 prompt changes and retest result.
7. Subtitle/audio/technical preflight results.
8. Residual risks.
9. Explicit statement that mouth sync was visually reviewed but not phoneme-certified.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_review.py
.venv/bin/ruff check factory/pet_sitcom_review.py tests/test_pet_sitcom_review.py
```

Expected: evidence covers all shots and risks, reviews are source-bound, and certainty is stated accurately.

- [ ] **Step 8: Commit Task 5**

```bash
git add factory/pet_sitcom_review.py tests/test_pet_sitcom_review.py
git commit -m "feat: add AI pet sitcom review evidence"
```

### Task 6: Dry-Run-First CLI

**Files:**
- Modify: `factory_cli.py`
- Create: `tests/test_cli_pet_sitcom.py`

**Interfaces:**
- Produces CLI command `pet-sitcom`.
- Uses existing `load_config`, `resolve_provider_profile`, `resolve_doubao_tts_config`, and provider clients.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_pet_cli_defaults_to_non_networked_plan(run_cli):
    result = run_cli(["pet-sitcom"])
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["stage"] == "plan"
    assert payload["executed"] is False


def test_shot_stage_blocks_before_mouth_approval(run_cli):
    result = run_cli(["pet-sitcom", "--stage", "shots", "--enable-live"])

    assert result.returncode == 1
    assert "mouth test" in result.stdout.lower()


def test_cli_candidate_two_requires_one_target_and_reason(run_cli):
    result = run_cli(
        [
            "pet-sitcom",
            "--stage",
            "shots",
            "--shot",
            "shot_03",
            "--candidate",
            "2",
        ]
    )

    assert result.returncode == 1
    assert "retry reason" in result.stdout.lower()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_pet_sitcom.py
```

Expected: failures because `pet-sitcom` is not registered.

- [ ] **Step 3: Register exact CLI options**

```python
pet_parser = subparsers.add_parser(
    "pet-sitcom",
    help="Plan, generate, review, or compose the original AI pet sitcom",
)
pet_parser.add_argument(
    "--stage",
    choices=["plan", "anchors", "mouth-test", "shots", "review", "compose"],
    default="plan",
)
pet_parser.add_argument("--output-dir", default="")
pet_parser.add_argument(
    "--anchor",
    choices=["naitang", "doubao", "living_room", "kitchen"],
    action="append",
    default=[],
)
pet_parser.add_argument(
    "--shot",
    choices=[f"shot_{index:02d}" for index in range(1, 15)],
    action="append",
    default=[],
)
pet_parser.add_argument("--candidate", choices=[1, 2], type=int, default=1)
pet_parser.add_argument(
    "--retry-reason",
    choices=[
        "identity",
        "paw_anatomy",
        "mouth_anatomy",
        "wrong_speaker",
        "lip_timing",
        "continuity",
        "extra_content",
    ],
    default="",
)
pet_parser.add_argument("--enable-live", action="store_true")
pet_parser.add_argument("--timeout", type=float, default=60.0)
pet_parser.add_argument("--submit-timeout", type=float, default=300.0)
pet_parser.add_argument("--download-timeout", type=float, default=120.0)
pet_parser.add_argument("--poll-interval", type=float, default=3.0)
pet_parser.add_argument("--max-wait", type=float, default=900.0)
pet_parser.set_defaults(func=pet_sitcom_command)
```

- [ ] **Step 4: Implement stage semantics**

- `plan`: always non-networked, writes the deterministic plan.
- `anchors`: requires `--enable-live`; generates selected or all missing anchor jobs.
- `mouth-test`: requires approved anchors; generates candidate 1 or one targeted candidate 2.
- `shots`: requires approved mouth test; generates selected shots or all missing shots sequentially.
- `review`: local-only; refreshes visual/technical evidence and validates review files.
- `compose`: local-only; requires current selections, source technical pass, complete passing shot reviews, and verified timings; creates both outputs and final technical evidence.

Dry-run `anchors`, `mouth-test`, and `shots` report planned calls but contact no provider unless `--enable-live` is present.

- [ ] **Step 5: Resolve providers without leaking secrets**

Use:

- gateway image profile with fixed model `doubao-seedream-4-5`,
- gateway video profile with fixed model `doubao-seedance-2-0`,
- resolved Doubao Seed-TTS 2.0 owner voice.

Fail before a call if the relevant profile is not ready. Keep credentials in memory only and recursively sanitize all errors and printed JSON.

- [ ] **Step 6: Print safe operational JSON**

Print stage, output directory, plan/report/review/timing/final paths, selected targets, planned/completed/reused/failed counts, `executed`, `composed`, blocked reasons, and sanitized errors. Exit nonzero for failed live generation, blocked review, or blocked composition.

- [ ] **Step 7: Run CLI and regression tests**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_pet_sitcom.py
.venv/bin/pytest -q tests/test_pet_sitcom.py tests/test_pet_sitcom_generation.py
.venv/bin/pytest -q tests/test_pet_sitcom_compose.py tests/test_pet_sitcom_review.py
.venv/bin/pytest -q tests/test_cli_speaking_ab.py tests/test_gateway_video_batch.py
```

Expected: CLI is safe by default, each stage enforces its gate, and existing workflows do not regress.

- [ ] **Step 8: Commit Task 6**

```bash
git add factory_cli.py tests/test_cli_pet_sitcom.py
git commit -m "feat: expose original AI pet sitcom workflow"
```

### Task 7: Live Anchors, Mouth Gate, Fourteen Shots, Iteration, And Delivery

**Files:**
- Create at runtime: `~/Desktop/宠物短剧样片/冻干案_20260723/`
- Create at runtime: `anchor_review.json`
- Create at runtime: `mouth_test_review.json`
- Create at runtime: `dialogue_timings.json`
- Create at runtime: `shot_reviews.json`
- Create at runtime: `review.md`
- Modify carefully: `docs/iteration-log.md`

**Interfaces:**
- Consumes the `pet-sitcom` CLI.
- Produces two final MP4 files and all source-bound QA evidence.

- [ ] **Step 1: Verify the zero-cost plan**

Run:

```bash
.venv/bin/python factory_cli.py pet-sitcom
```

Expected: `stage=plan`, `executed=false`, 4 anchors, 1 mouth test, 14 production shots, all runtime outputs inside the dedicated Desktop folder, and no credentials in stdout or plan JSON.

- [ ] **Step 2: Generate and review all four anchors**

Run:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage anchors --enable-live
```

Inspect both triptychs and both scene anchors at original resolution. Complete `anchor_review.json` only when all design checks pass. If one anchor fails, fix only that prompt and regenerate only that anchor; do not regenerate passed anchors.

- [ ] **Step 3: Generate and review the mouth test**

Run:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage mouth-test --enable-live
```

Run preflight and inspect a dense mouth sequence. Confirm Naitang identity, feline mouth anatomy, correct single speaker, intelligible exact line, subjective start/pause/end alignment, and no extra content.

If one hard gate fails, generate candidate 2 with one exact reason:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage mouth-test \
  --enable-live --candidate 2 --retry-reason mouth_anatomy
```

Do not continue to production when candidate 2 also fails.

- [ ] **Step 4: Generate production shots sequentially**

After mouth-test approval, generate one shot at a time:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage shots \
  --enable-live --shot shot_01
```

Repeat through `shot_14`. After every shot:

- refresh its nine-frame sheet,
- inspect identity, paws, mouth, speaker, props, scene, and camera,
- compare its start with the previous ending frame,
- record pass/fail before submitting the next shot.

For any individual failed shot, run at most one targeted candidate 2 for that
same shot:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage shots \
  --enable-live --shot shot_06 --candidate 2 --retry-reason wrong_speaker
```

Never submit candidate 3 and never regenerate a passed independent shot.

- [ ] **Step 5: Verify dialogue timing and complete reviews**

Listen to original source audio and inspect waveform plus dense mouth frames. Write actual start/end offsets for the ten spoken shots, bound to current selected hashes. Complete all fourteen source-bound shot reviews with exact timestamps and observations.

- [ ] **Step 6: Refresh source evidence and compose**

Run:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage review
.venv/bin/python factory_cli.py pet-sitcom --stage compose
```

Expected final paths:

```text
~/Desktop/宠物短剧样片/冻干案_20260723/final/冻干到底是谁偷吃的_清洁版.mp4
~/Desktop/宠物短剧样片/冻干案_20260723/final/冻干到底是谁偷吃的_发布版.mp4
```

- [ ] **Step 7: Run final technical and visual self-check**

Run:

```bash
python3 /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  "$HOME/Desktop/宠物短剧样片/冻干案_20260723/final/冻干到底是谁偷吃的_清洁版.mp4"
python3 /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  "$HOME/Desktop/宠物短剧样片/冻干案_20260723/final/冻干到底是谁偷吃的_发布版.mp4"
```

Inspect both contact sheets, all eight mouth sequences, bag/tail/crumb/mirror sequences, all thirteen continuity comparisons, subtitle safety, voice separation, music ducking, loudness, black frames, freezes, and final ending card.

Fix local composition errors without new provider calls. Use a shot candidate 2 only when a selected source fails a hard gate and has not yet used its retry.

- [ ] **Step 8: Write final review and iteration record**

Generate `review.md` from validated reviews. Append an iteration-log entry covering the reference-analysis boundary, original story, anchors, mouth gate, per-shot retries, technical results, final paths, and residual risks.

- [ ] **Step 9: Run full verification**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check factory factory_cli.py tests
.venv/bin/python -m compileall -q factory factory_cli.py
git diff --check
rg -n 'sk-[A-Za-z0-9]|Authorization:|Bearer |data:image|X-Api-Key' \
  factory/pet_sitcom.py factory/pet_sitcom_generation.py \
  factory/pet_sitcom_compose.py factory/pet_sitcom_review.py \
  "$HOME/Desktop/宠物短剧样片/冻干案_20260723" docs/iteration-log.md
```

Expected: tests, Ruff, compile, diff, technical preflight, visual review, and secret scan pass; reference videos remain untouched; generated media is not committed.

- [ ] **Step 10: Commit source and iteration records only**

```bash
git add factory/pet_sitcom.py factory/pet_sitcom_generation.py \
  factory/pet_sitcom_compose.py factory/pet_sitcom_review.py factory_cli.py \
  tests/test_pet_sitcom.py tests/test_pet_sitcom_generation.py \
  tests/test_pet_sitcom_compose.py tests/test_pet_sitcom_review.py \
  tests/test_cli_pet_sitcom.py
git add -p docs/iteration-log.md
git commit -m "feat: deliver original AI pet sitcom sample"
```
