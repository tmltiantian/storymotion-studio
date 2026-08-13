# Dual-Character Action Stress Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, generate, inspect, and deliver an isolated 15-second Seedance native-audio sample with three continuous two-character shots, large readable actions, direct wrist contact, correct speakers, visible lip motion, and evidence-backed iteration.

**Architecture:** Add one focused generation/composition module and one focused review module. Reuse the existing gateway clients and `render_gateway_video_single` for guarded, resumable provider work. Keep immutable plans, candidate selection, reference hashes, continuity-frame provenance, verified dialogue timings, diagnostic evidence, and human review as separate artifacts under one isolated run directory.

**Tech Stack:** Python 3.12, dataclasses, Pillow, FFmpeg/FFprobe, pytest, existing provider-profile resolver, `GatewayImageClient`, `GatewayVideoClient`, `render_gateway_video_single`, Doubao Seedream 4.5, Doubao Seedance 2.0.

## Global Constraints

- Implement the approved design in `docs/superpowers/specs/2026-07-23-dual-character-action-stress-test-design.md`.
- Use `doubao-seedance-2-0`, native audio, 9:16, 1080p, and exactly three five-second source clips.
- Use the reviewed Lin Che and Su Mian character references and one shared person-free night-street anchor.
- Preserve source motion and convert directly to 30 fps. Never use optical flow, `minterpolate`, or a 6/8/10 fps intermediate cadence.
- Generate candidate 1 first. A failed shot may receive exactly one targeted candidate-2 retry.
- Generate shots sequentially. Shot 2 depends on the selected shot-1 ending frame; shot 3 depends on the selected shot-2 ending frame.
- If an upstream selected candidate changes, invalidate downstream selections whose stored reference hashes no longer match. Preserve their files as evidence, but never compose them.
- Keep all new runtime assets below `runs/sample_episode/dual_action_stress_20260723/`.
- Do not modify `output/sample_episode/final_preview.mp4`, the existing 61-second `goal_ready` deliverable, or `runs/sample_episode/visual_selection.json`.
- Provider calls are disabled unless the caller explicitly passes `--enable-live`.
- Never serialize API keys, authorization values, signed URLs, image data URIs, or HTTP headers.
- Do not claim phoneme-level lip-sync certification. The current gate is frame-by-frame visual review plus source-audio inspection.

---

### Task 1: Immutable Plan, Paths, And Prompt Contracts

**Files:**
- Create: `factory/dual_action_stress.py`
- Create: `tests/test_dual_action_stress.py`

**Interfaces:**
- Produces `DualActionStressError`, `DualActionShot`, and `DualActionPlan`.
- Produces `build_dual_action_plan(config: dict[str, Any], project_id: str) -> DualActionPlan`.
- Produces `write_dual_action_plan(plan: DualActionPlan) -> Path`.
- Later tasks consume the exact paths, prompts, dialogue, speaker IDs, and reference ordering stored by the plan.

- [ ] **Step 1: Write failing plan and isolation tests**

```python
def test_plan_has_three_sequential_native_audio_shots(factory_config):
    plan = build_dual_action_plan(factory_config, "sample_episode")

    assert plan.output_dir.name == "dual_action_stress_20260723"
    assert [shot.shot_id for shot in plan.shots] == [
        "shot_01",
        "shot_02",
        "shot_03",
    ]
    assert [shot.dialogue for shot in plan.shots] == [
        "",
        "末班车不是出城，是回到十年前。",
        "十年前？你想让我看什么？",
    ]
    assert [shot.speaker_id for shot in plan.shots] == [None, "su_mian", "lin_che"]
    assert all(shot.generate_audio is True for shot in plan.shots)
    assert all(shot.duration_seconds == 5 for shot in plan.shots)


def test_plan_keeps_every_output_inside_isolated_directory(factory_config):
    plan = build_dual_action_plan(factory_config, "sample_episode")

    assert plan.final_output.name == "双人动作对白压力测试.mp4"
    assert all(
        path == plan.output_dir or plan.output_dir in path.parents
        for path in plan.all_output_paths()
    )
    assert Path(factory_config["outputDir"]) / "sample_episode" / "final_preview.mp4" \
        not in plan.all_output_paths()


def test_prompts_lock_speakers_actions_camera_and_reference_order(factory_config):
    plan = build_dual_action_plan(factory_config, "sample_episode")

    assert "No spoken dialogue" in plan.shots[0].base_prompt
    assert "末班车不是出城，是回到十年前。" in plan.shots[1].base_prompt
    assert "Only Su Mian speaks" in plan.shots[1].base_prompt
    assert "十年前？你想让我看什么？" in plan.shots[2].base_prompt
    assert "Only Lin Che speaks" in plan.shots[2].base_prompt
    assert "no camera push-in" in plan.shared_constraints
    assert "no optical-flow look" in plan.shared_constraints
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py
```

Expected: test collection fails because `factory.dual_action_stress` does not exist.

- [ ] **Step 3: Implement immutable plan types**

Use these public fields:

```python
@dataclass(frozen=True)
class DualActionShot:
    shot_id: str
    index: int
    title: str
    dialogue: str
    speaker_id: str | None
    duration_seconds: int
    base_prompt: str
    generate_audio: bool
    candidate_dir: Path
    continuity_frame: Path | None


@dataclass(frozen=True)
class DualActionPlan:
    project_id: str
    run_dir: Path
    output_dir: Path
    lin_reference: Path
    su_reference: Path
    scene_anchor_path: Path
    scene_anchor_prompt: str
    shared_constraints: str
    shots: tuple[DualActionShot, ...]
    plan_path: Path
    generation_report_path: Path
    selection_path: Path
    dialogue_timing_path: Path
    manual_review_path: Path
    final_output: Path
    review_markdown_path: Path
```

`DualActionPlan.all_output_paths()` must enumerate the plan, scene anchor, per-candidate video and report paths, continuity frames, selection, timing, evidence, final video, and review paths without following symlinks.

- [ ] **Step 4: Encode the approved shot prompts**

The shared prompt contract must state:

- Reference 1 is Lin Che’s immutable identity.
- Reference 2 is Su Mian’s immutable identity.
- Reference 3 is the empty night-street scene anchor.
- Reference 4, when present, is only the previous shot’s ending-state continuity reference.
- Keep Lin Che and Su Mian as two distinct people with unchanged faces, hair, clothing, height relationship, and left/right screen geography.
- One warm streetlamp, fixed wet-ground reflection, fixed vanishing direction, no rain, no signs, no crowd, no vehicles.
- Stable photographic camera, natural motion blur, readable weight transfer, grounded feet, no floating, no uniform body translation, no camera push-in, no camera pull-out, no orbit, no surprise cut, no scene change, no extra person, no duplicate face, no duplicate limb, no text, no subtitle, no watermark, no optical-flow look.

The three shot-specific prompt sections must preserve the exact approved action order:

1. Shot 1: Lin Che enters quickly from frame right; Su Mian turns, steps back, raises a defensive arm; Lin reaches toward her wrist near the ending; no dialogue or human vocalization.
2. Shot 2: Lin holds Su’s wrist; Su looks down, looks up, breaks outward and fully separates; only Su says the exact line while Lin reacts silently.
3. Shot 3: Lin medium-close reverse; Su’s side face, shoulder, and pointing arm stay at the right foreground edge; Su steps back and points; Lin turns, steps back, and alone says the exact line.

Prompts must reserve at least 0.20 seconds without dialogue at the start and end of shots 2 and 3 so the audio seam never overlaps a spoken word.

- [ ] **Step 5: Validate reviewed inputs and safe destinations**

Read `runs/<project>/character_assets.json`, resolve Lin Che and Su Mian references under `runs/<project>/assets/characters/`, require production-ready entries, and validate image signatures. Reject:

- missing or unsupported character images,
- character paths outside the expected asset directory,
- symlinks in the character or output path chain,
- an existing output root that resolves outside the configured `runsDir`,
- any planned path equal to an approved episode output or selected micro-shot.

- [ ] **Step 6: Write the deterministic plan artifact**

`write_dual_action_plan` must write JSON atomically with:

- schema version,
- local paths,
- exact prompts,
- reference order,
- provider and model names,
- duration, ratio, resolution, and `generate_audio`,
- planned candidate-1 outputs,
- no credentials.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py
.venv/bin/ruff check factory/dual_action_stress.py tests/test_dual_action_stress.py
```

Expected: all plan, prompt, asset, and path-isolation tests pass without network access.

- [ ] **Step 8: Commit Task 1**

```bash
git add factory/dual_action_stress.py tests/test_dual_action_stress.py
git commit -m "feat: plan dual-character action stress test"
```

### Task 2: Guarded Scene Anchor And Sequential Video Generation

**Files:**
- Modify: `factory/dual_action_stress.py`
- Modify: `tests/test_dual_action_stress.py`

**Interfaces:**
- Produces `execute_dual_action_generation(...) -> dict[str, Any]`.
- Produces `extract_continuity_frame(...) -> Path`.
- Reuses `GatewayImageClient.generate` for the scene anchor.
- Reuses `render_gateway_video_single` for every Seedance candidate.

- [ ] **Step 1: Write failing dry-run, reference-chain, and resume tests**

```python
def test_dry_run_plans_without_provider_calls(plan, fake_clients):
    report = execute_dual_action_generation(
        plan,
        image_client=fake_clients.image,
        video_client=fake_clients.video,
        allow_network=False,
    )

    assert report["executed"] is False
    assert report["planned_count"] == 4
    assert fake_clients.total_calls == 0


def test_live_generation_is_sequential_and_uses_native_audio(plan, fake_clients):
    report = execute_dual_action_generation(
        plan,
        image_client=fake_clients.image,
        video_client=fake_clients.video,
        allow_network=True,
    )

    assert report["success"] is True
    assert [call.images for call in fake_clients.video.calls] == [
        [plan.lin_reference, plan.su_reference, plan.scene_anchor_path],
        [
            plan.lin_reference,
            plan.su_reference,
            plan.scene_anchor_path,
            plan.output_dir / "continuity" / "shot_01_last.png",
        ],
        [
            plan.lin_reference,
            plan.su_reference,
            plan.scene_anchor_path,
            plan.output_dir / "continuity" / "shot_02_last.png",
        ],
    ]
    assert all(call.generate_audio is True for call in fake_clients.video.calls)


def test_matching_completed_jobs_resume_without_new_submission(plan, fake_clients):
    first = execute_dual_action_generation(
        plan,
        image_client=fake_clients.image,
        video_client=fake_clients.video,
        allow_network=True,
    )
    call_count = fake_clients.total_calls
    second = execute_dual_action_generation(
        plan,
        image_client=fake_clients.image,
        video_client=fake_clients.video,
        allow_network=True,
    )

    assert first["success"] is True
    assert second["reused_count"] == 4
    assert fake_clients.total_calls == call_count
```

- [ ] **Step 2: Run generation tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py -k "dry_run or sequential or resume"
```

Expected: failures because generation and continuity extraction are not implemented.

- [ ] **Step 3: Implement resumable scene-anchor generation**

Use `GatewayImageConfig` with:

- model `doubao-seedream-4-5`,
- size `1440x2560`,
- output `scene_anchor.png`,
- no reference images.

Hash provider, model, prompt, size, and candidate number into `scene_anchor.state.json`. Generate to a temporary local file, validate the real image signature, decode with Pillow, convert to RGB, save a true PNG to another temporary file, fsync, and atomically replace `scene_anchor.png`. Reuse only when both the state signature and current output hash match.

The scene prompt must forbid people, human silhouettes, text, logos, vehicles, rain, and changed light direction.

- [ ] **Step 4: Implement exact shot job construction**

For candidate number `N`, use:

```text
shots/shot_01/candidate_NNN.mp4
shots/shot_01/candidate_NNN.report.json
shots/shot_02/candidate_NNN.mp4
shots/shot_02/candidate_NNN.report.json
shots/shot_03/candidate_NNN.mp4
shots/shot_03/candidate_NNN.report.json
```

Every `render_gateway_video_single` call must use:

```python
render_gateway_video_single(
    prompt,
    output_path,
    video_client,
    report_path,
    images=ordered_references,
    duration=5,
    ratio="9:16",
    resolution="1080p",
    generate_audio=True,
    allow_network=allow_network,
    overwrite=False,
    report_sanitizer=sanitize_dual_action_report,
)
```

Before a provider call, hash every reference image and store its absolute local path, order, and SHA-256 in the aggregate generation report. Require at most four references.

- [ ] **Step 5: Enforce sequential prerequisites**

- Shot 1 requires a valid scene anchor.
- Shot 2 requires a selected, valid shot 1 and a valid `shot_01_last.png` extracted from that exact selected hash.
- Shot 3 requires a selected, valid shot 2 and a valid `shot_02_last.png` extracted from that exact selected hash.
- A targeted request for a blocked downstream shot returns a clear prerequisite error and makes no provider call.
- A failed shot stops later shots but preserves every completed state and report.

- [ ] **Step 6: Extract provenance-bound continuity frames**

After a shot is successfully selected, extract at 4.88 seconds:

```bash
ffmpeg -hide_banner -loglevel error -ss 4.88 -i INPUT.mp4 \
  -frames:v 1 -vf scale=1080:1920:force_original_aspect_ratio=decrease,\
pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black OUTPUT.tmp.png
```

Validate the PNG signature and store a sidecar containing:

- source video path,
- source video SHA-256,
- extraction timestamp,
- frame SHA-256.

Reuse the frame only when all four values still match.

- [ ] **Step 7: Sanitize all reports**

The sanitizer must recursively redact:

- known API-key values,
- keys named `authorization`, `api_key`, `access_key`, `token`, or `secret`,
- bearer strings,
- URL query strings,
- signed download URLs,
- image data URIs.

Local paths, provider/model IDs, task IDs, counts, hashes, durations, and sanitized errors are allowed.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py
.venv/bin/pytest -q tests/test_gateway_video_batch.py tests/test_gateway_image.py
```

Expected: generation is dry-run-first, sequential, resumable, native-audio enabled, and secret-safe.

- [ ] **Step 9: Commit Task 2**

```bash
git add factory/dual_action_stress.py tests/test_dual_action_stress.py
git commit -m "feat: generate sequential action stress clips"
```

### Task 3: Candidate Selection, Targeted Retry, And Stale-Chain Invalidation

**Files:**
- Modify: `factory/dual_action_stress.py`
- Modify: `tests/test_dual_action_stress.py`

**Interfaces:**
- Produces `select_dual_action_candidate(...) -> dict[str, Any]`.
- Produces `build_targeted_retry_prompt(shot: DualActionShot, reason: str) -> str`.
- Produces and validates `selected_candidates.json`.

- [ ] **Step 1: Write failing selection and invalidation tests**

```python
def test_candidate_two_requires_one_failed_shot_and_retry_reason(plan):
    with pytest.raises(DualActionStressError, match="candidate 2"):
        build_generation_request(
            plan,
            only=("shot_02",),
            candidate_number=2,
            retry_reason="",
        )


def test_replacing_shot_one_invalidates_shots_two_and_three(plan, ready_candidates):
    select_dual_action_candidate(plan, "shot_01", 1)
    select_dual_action_candidate(plan, "shot_02", 1)
    select_dual_action_candidate(plan, "shot_03", 1)

    selection = select_dual_action_candidate(plan, "shot_01", 2)

    assert selection["shots"]["shot_01"]["candidate_number"] == 2
    assert selection["shots"]["shot_02"]["status"] == "stale_upstream"
    assert selection["shots"]["shot_03"]["status"] == "stale_upstream"


def test_composition_rejects_reference_hash_mismatch(plan, stale_selection):
    with pytest.raises(DualActionStressError, match="continuity provenance"):
        require_current_selected_chain(plan, stale_selection)
```

- [ ] **Step 2: Run selection tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py -k "candidate or replacing or provenance"
```

Expected: failures because candidate selection and stale-chain validation are absent.

- [ ] **Step 3: Implement the selection schema**

Each selected shot must store:

```json
{
  "candidate_number": 1,
  "status": "selected",
  "output_path": "/absolute/local/path.mp4",
  "output_sha256": "hex",
  "prompt_sha256": "hex",
  "reference_paths": ["/absolute/local/reference.png"],
  "reference_sha256": ["hex"],
  "continuity_source_sha256": "hex-or-empty",
  "selected_at": "UTC ISO-8601"
}
```

Selection is atomic and is allowed only when:

- video and audio streams both exist,
- duration is between 4.70 and 5.30 seconds,
- the per-candidate report is successful,
- current prompt and reference hashes match the report.

- [ ] **Step 4: Implement five targeted retry reasons**

Candidate 2 requires exactly one shot target and exactly one reason:

```python
RETRY_PROMPT_SUFFIXES = {
    "motion_incomplete": (
        "Retry correction: complete every listed action in order with larger, "
        "clearly separated poses and visible grounded weight transfer."
    ),
    "hand_contact": (
        "Retry correction: keep both hands visible; use one anatomically correct "
        "hand-to-wrist contact, then show complete clean separation without fusion."
    ),
    "lip_visibility": (
        "Retry correction: keep the designated speaker's mouth and jaw unobstructed "
        "through the entire exact line; the silent character only reacts."
    ),
    "continuity": (
        "Retry correction: match the previous ending frame's positions, arm direction, "
        "clothes, faces, streetlamp direction, and wet-ground reflection at the start."
    ),
    "wrong_speaker": (
        "Retry correction: only the explicitly designated character may speak or move "
        "their lips as speech; the other character remains vocally silent."
    ),
}
```

Store both the base prompt hash and retry suffix in the candidate report.

- [ ] **Step 5: Invalidate downstream provenance without deleting evidence**

When the selected hash changes:

- replacing shot 1 marks shot 2 and shot 3 `stale_upstream`,
- replacing shot 2 marks shot 3 `stale_upstream`,
- replacing shot 3 changes no upstream selection,
- corresponding continuity frames are regenerated,
- old MP4, reports, frames, and review evidence remain untouched.

Composition and final review must reject every stale entry.

- [ ] **Step 6: Enforce the two-candidate ceiling**

Reject candidate numbers outside 1 and 2. Candidate 2 is never an automatic retry. If candidate 2 fails, return a blocked report and stop all further provider work for that shot.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py
```

Expected: candidate selection is hash-bound, candidate 2 is targeted, and upstream replacement invalidates dependent shots.

- [ ] **Step 8: Commit Task 3**

```bash
git add factory/dual_action_stress.py tests/test_dual_action_stress.py
git commit -m "feat: gate action stress candidate retries"
```

### Task 4: Verified Dialogue Timings And Final Composition

**Files:**
- Modify: `factory/dual_action_stress.py`
- Modify: `tests/test_dual_action_stress.py`

**Interfaces:**
- Produces `DialogueTiming` and `load_verified_dialogue_timings(...)`.
- Produces `build_dual_action_ffmpeg_command(...) -> list[str]`.
- Produces `compose_dual_action_stress(...) -> dict[str, Any]`.

- [ ] **Step 1: Write failing timing and composition tests**

```python
def test_composition_requires_verified_actual_dialogue_windows(plan, ready_selection):
    with pytest.raises(DualActionStressError, match="verified dialogue timings"):
        compose_dual_action_stress(plan)


def test_ffmpeg_preserves_direct_motion_and_native_audio(plan, verified_timings):
    command = build_dual_action_ffmpeg_command(plan, verified_timings)
    rendered = " ".join(command)

    assert "fps=30" in rendered
    assert "minterpolate" not in rendered
    assert "fps=6" not in rendered
    assert "fps=8" not in rendered
    assert "fps=10" not in rendered
    assert "acrossfade=d=0.04" in rendered
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in rendered


def test_subtitles_omit_shot_one_and_match_verified_windows(plan, verified_timings):
    srt = build_dual_action_srt(plan, verified_timings)

    assert "末班车不是出城，是回到十年前。" in srt
    assert "十年前？你想让我看什么？" in srt
    assert "00:00:00" not in srt
```

- [ ] **Step 2: Run composition tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py -k "composition or ffmpeg or subtitles"
```

Expected: failures because timing validation and composition do not exist.

- [ ] **Step 3: Define the verified timing artifact**

Use `dialogue_timings.json`:

```json
{
  "schema_version": "motion-comic-factory.dual-action-dialogue-timings.v1",
  "verified": true,
  "method": "waveform inspection plus frame-by-frame human review",
  "shots": {
    "shot_02": {
      "start_seconds": 0.0,
      "end_seconds": 0.0,
      "text": "末班车不是出城，是回到十年前。"
    },
    "shot_03": {
      "start_seconds": 0.0,
      "end_seconds": 0.0,
      "text": "十年前？你想让我看什么？"
    }
  }
}
```

The zeros above are schema examples only and must fail validation. Real values must:

- be greater than or equal to 0.20,
- end no later than 4.80,
- have start less than end,
- match the exact shot text,
- be reviewed against the selected candidate hash stored alongside each timing.

- [ ] **Step 4: Build exact subtitles from verified offsets**

Map shot-2 offsets to the final timeline by adding 5.0 seconds and shot-3 offsets by adding 10.0 seconds. Write UTF-8 SRT atomically. Shot 1 must have no dialogue subtitle.

- [ ] **Step 5: Build one direct-motion FFmpeg graph**

For each input:

- trim video and audio to five seconds,
- set timestamps from zero,
- scale/pad to 1080×1920,
- apply `fps=30` directly,
- convert to `yuv420p`,
- resample native audio to 48 kHz stereo.

Concatenate video at exact 5.0-second boundaries. Use 40ms triangular audio crossfades, then `apad` and `atrim=0:15` so the final audio remains exactly 15 seconds. The reserved 0.20-second non-speech edges keep spoken words outside the crossfade. Normalize the combined result with `loudnorm=I=-16:TP=-1.5:LRA=11`.

Encode:

- H.264 High profile,
- `-crf 18`,
- AAC at 192 kbps,
- 48 kHz stereo,
- `-movflags +faststart`,
- exact 15-second output.

Burn the verified subtitles in the bottom-safe area using PingFang SC, white text, dark outline, and no top labels.

- [ ] **Step 6: Validate selected-chain and final media**

Before FFmpeg, require a current three-shot selection and valid native audio in every source. Write to a temporary MP4, probe it, require:

- duration between 14.85 and 15.15 seconds,
- one video stream,
- one audio stream,
- 1080×1920,
- final frame rate 30 fps.

Only then atomically replace `final/双人动作对白压力测试.mp4`.

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_stress.py
```

Expected: composition refuses unverified timing, preserves native source motion/audio, and creates exact shot-2/shot-3 subtitles.

- [ ] **Step 8: Commit Task 4**

```bash
git add factory/dual_action_stress.py tests/test_dual_action_stress.py
git commit -m "feat: compose verified dual-action sample"
```

### Task 5: Diagnostic Evidence And Structured Human Review

**Files:**
- Create: `factory/dual_action_review.py`
- Create: `tests/test_dual_action_review.py`

**Interfaces:**
- Produces `build_dual_action_evidence(plan: DualActionPlan) -> dict[str, Any]`.
- Produces `validate_manual_dual_action_review(...) -> dict[str, Any]`.
- Produces `write_dual_action_review_markdown(...) -> Path`.

- [ ] **Step 1: Write failing evidence-manifest tests**

```python
def test_evidence_plan_covers_every_required_diagnostic(plan, ready_selection):
    manifest = build_dual_action_evidence_plan(plan)

    assert set(manifest["contact_sheets"]) == {"shot_01", "shot_02", "shot_03"}
    assert set(manifest["dense_sequences"]) == {
        "shot_02_hands",
        "shot_02_mouths",
        "shot_03_mouths",
    }
    assert set(manifest["continuity_pairs"]) == {
        "shot_01_to_02",
        "shot_02_to_03",
    }


def test_review_rejects_missing_action_and_speaker_checks(plan, incomplete_review):
    with pytest.raises(DualActionReviewError, match="shot_02"):
        validate_manual_dual_action_review(plan, incomplete_review)


def test_review_markdown_states_lip_sync_is_not_phoneme_certified(
    plan, passing_review
):
    markdown = render_dual_action_review_markdown(plan, passing_review)

    assert "未进行音素级认证" in markdown
    assert "逐帧人工复核" in markdown
```

- [ ] **Step 2: Run review tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_review.py
```

Expected: collection fails because `factory.dual_action_review` does not exist.

- [ ] **Step 3: Build nine-frame whole-shot contact sheets**

For each selected five-second candidate, extract frames at:

```text
0.25, 0.80, 1.35, 1.90, 2.45, 3.00, 3.55, 4.10, 4.65 seconds
```

Tile them 3×3 with shot ID and timestamps below each frame. Store under:

```text
evidence/contact_sheets/shot_01.png
evidence/contact_sheets/shot_02.png
evidence/contact_sheets/shot_03.png
```

Every evidence manifest entry stores the selected candidate number and source SHA-256.

- [ ] **Step 4: Build dense hand, mouth, and continuity sequences**

- Shot-2 hands: 13 frames from 0.20 to 4.70 seconds, with a central 88%-width and 52%-height action crop plus full-frame thumbnails.
- Shot-2 mouths: 13 frames across the verified dialogue window, keeping both faces visible.
- Shot-3 mouths: 13 frames across the verified dialogue window, keeping Lin Che’s face and Su Mian’s right-edge reaction visible.
- Continuity 1→2: shot-1 frame at 4.88 seconds beside shot-2 frame at 0.12 seconds.
- Continuity 2→3: shot-2 frame at 4.88 seconds beside shot-3 frame at 0.12 seconds.

Use Pillow for labels and tiling after FFmpeg extracts immutable PNG frames.

- [ ] **Step 5: Add technical evidence**

Write `evidence/source_technical_qc.json` before composition with:

- FFprobe stream, codec, resolution, frame-rate, and duration results,
- FFmpeg `blackdetect`,
- FFmpeg `freezedetect`,
- FFmpeg `ebur128`,
- selected source hashes,
- whether every source has native audio,
- whether every selected source passes the specified tolerances.

After composition, write `evidence/final_technical_qc.json` with the final hash,
stream/codec/resolution/frame-rate/duration results, `blackdetect`,
`freezedetect`, and `ebur128`. Automatic checks may reject broken media,
black runs longer than 0.08 seconds, missing audio, invalid dimensions, wrong
duration, or wrong final frame rate. They must not claim that hands, identity,
acting, speaker identity, or lip sync passed.

- [ ] **Step 6: Define complete manual review fields**

`manual_review.json` must bind each review to the selected source hash and require:

- all planned action beats observed in order,
- feet grounded and no uniform sliding,
- no unexplained camera movement or scene cut,
- identity, hair, clothing, and height relationship stable,
- no extra people, duplicate faces, duplicate limbs, text, or watermark,
- shot-2 pre-contact/contact/post-separation hand anatomy,
- shot-3 pointing hand anatomy,
- correct designated speaker,
- visible mouth/jaw motion through the line,
- silent character has reaction but no sustained speaking mouth motion,
- continuity with the preceding selected frame,
- pass/fail decision,
- concrete notes,
- retry reason when failed.

The pre-composition decision passes only when all three selected shots pass,
all source hashes match, source technical QC passes, and dialogue timings are
verified. The delivered final decision additionally requires passing final
technical QC.

- [ ] **Step 7: Generate an iteration-oriented `review.md`**

Write these sections:

1. Models, providers, source candidates, and hashes.
2. Per-shot planned actions and observed results.
3. Hand/contact findings.
4. Speaker and lip-motion findings.
5. Identity and scene-continuity findings.
6. Motion and camera findings.
7. Problems found.
8. Prompt or candidate changes made.
9. Retest result.
10. Residual risks and explicit statement that lip sync was not phoneme-certified.

- [ ] **Step 8: Run tests and verify GREEN**

Run:

```bash
.venv/bin/pytest -q tests/test_dual_action_review.py
.venv/bin/ruff check factory/dual_action_review.py tests/test_dual_action_review.py
```

Expected: all required evidence is planned, reviews are source-bound, and the markdown cannot overstate lip-sync certainty.

- [ ] **Step 9: Commit Task 5**

```bash
git add factory/dual_action_review.py tests/test_dual_action_review.py
git commit -m "feat: add dual-action diagnostic review"
```

### Task 6: Dry-Run-First CLI And Local Composition Mode

**Files:**
- Modify: `factory_cli.py`
- Create: `tests/test_cli_dual_action_stress.py`

**Interfaces:**
- Produces CLI command `dual-action-stress`.
- Uses existing `load_config` and `resolve_provider_profile`.
- Prints safe machine-readable JSON with paths, counts, selected candidates, blocked reasons, and no credentials.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_is_non_networked_by_default(run_cli):
    result = run_cli(["dual-action-stress", "--project", "sample_episode"])

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["executed"] is False
    assert payload["planned_count"] == 4


def test_cli_candidate_two_requires_one_shot_and_reason(run_cli):
    result = run_cli(
        [
            "dual-action-stress",
            "--project",
            "sample_episode",
            "--candidate",
            "2",
            "--only",
            "shot_02",
        ]
    )

    assert result.returncode == 1
    assert "retry reason" in result.stdout.lower()


def test_cli_never_prints_gateway_key(run_cli, configured_gateway):
    result = run_cli(["dual-action-stress", "--project", "sample_episode"])

    assert configured_gateway.api_key not in result.stdout
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_dual_action_stress.py
```

Expected: failures because the command is not registered.

- [ ] **Step 3: Register exact CLI options**

```python
dual_action_parser = subparsers.add_parser(
    "dual-action-stress",
    help="Plan, generate, review, or compose the isolated dual-character action sample",
)
dual_action_parser.add_argument("--project", default="sample_episode")
dual_action_parser.add_argument(
    "--only",
    choices=["scene", "shot_01", "shot_02", "shot_03"],
    action="append",
    default=[],
)
dual_action_parser.add_argument("--candidate", choices=[1, 2], type=int, default=1)
dual_action_parser.add_argument(
    "--retry-reason",
    choices=[
        "motion_incomplete",
        "hand_contact",
        "lip_visibility",
        "continuity",
        "wrong_speaker",
    ],
    default="",
)
dual_action_parser.add_argument("--compose", action="store_true")
dual_action_parser.add_argument("--enable-live", action="store_true")
dual_action_parser.add_argument("--timeout", type=float, default=60.0)
dual_action_parser.add_argument("--submit-timeout", type=float, default=300.0)
dual_action_parser.add_argument("--download-timeout", type=float, default=120.0)
dual_action_parser.add_argument("--poll-interval", type=float, default=3.0)
dual_action_parser.add_argument("--max-wait", type=float, default=900.0)
dual_action_parser.set_defaults(func=dual_action_stress_command)
```

- [ ] **Step 4: Resolve fixed production clients**

For live work:

- require a ready gateway image provider and instantiate `GatewayImageClient` with model `doubao-seedream-4-5`,
- require a ready gateway video provider and instantiate `GatewayVideoClient` with model `doubao-seedance-2-0`,
- use the configured base URL and API key in memory only,
- pass timeout arguments to the client config,
- fail before any call when the relevant provider is not ready.

For dry runs, build and write the plan without requiring credentials.

- [ ] **Step 5: Implement mode semantics**

- No `--enable-live`: write plan and return a successful non-networked report.
- `--enable-live` with no `--only`: generate scene, shot 1, shot 2, and shot 3 sequentially, stopping at the first failure.
- `--enable-live --only scene`: generate or reuse only the scene anchor.
- `--enable-live --only shot_NN`: generate or resume only the requested shot after validating prerequisites.
- `--candidate 2`: require exactly one shot target and one retry reason.
- `--compose`: make no provider calls; require current selection, passing source technical evidence, passing manual review, and verified dialogue timings before final composition, then refresh final technical evidence.
- After each successful shot, regenerate its continuity frame and refresh evidence for currently selected shots.

- [ ] **Step 6: Keep CLI failures safe and useful**

Printed JSON must include:

- plan path,
- output directory,
- generation report path,
- selected-candidates path,
- evidence directory,
- timing path,
- manual-review path,
- review markdown path,
- final output path,
- planned/completed/reused/failed counts,
- executed and composed booleans,
- blocked reasons and sanitized errors.

Catch all provider and validation errors, sanitize them with known in-memory secrets, and exit nonzero for failed live generation or blocked composition.

- [ ] **Step 7: Run CLI and regression tests**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_dual_action_stress.py
.venv/bin/pytest -q tests/test_dual_action_stress.py tests/test_dual_action_review.py
.venv/bin/pytest -q tests/test_cli_speaking_ab.py tests/test_gateway_video_batch.py
```

Expected: the new command is safe by default and does not regress existing gateway or speaking-A/B workflows.

- [ ] **Step 8: Commit Task 6**

```bash
git add factory_cli.py tests/test_cli_dual_action_stress.py
git commit -m "feat: expose dual-action stress workflow"
```

### Task 7: Live Generation, Per-Shot Review, Targeted Iteration, And Delivery

**Files:**
- Create at runtime: `runs/sample_episode/dual_action_stress_20260723/`
- Create at runtime: `runs/sample_episode/dual_action_stress_20260723/dialogue_timings.json`
- Create at runtime: `runs/sample_episode/dual_action_stress_20260723/manual_review.json`
- Create at runtime: `runs/sample_episode/dual_action_stress_20260723/review.md`
- Modify carefully: `docs/iteration-log.md`

**Interfaces:**
- Consumes the `dual-action-stress` CLI.
- Produces the final 15-second MP4 plus source-bound diagnostic evidence and iteration notes.

- [ ] **Step 1: Run the zero-cost plan**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress --project sample_episode
```

Expected:

- exit 0,
- `executed=false`,
- four planned provider jobs,
- every output below `runs/sample_episode/dual_action_stress_20260723/`,
- no API key in stdout or JSON.

- [ ] **Step 2: Generate and inspect the scene anchor**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only scene
```

Inspect `scene_anchor.png` at original resolution. Require:

- no person or human silhouette,
- one stable warm streetlamp,
- wet-ground reflection,
- useful depth and vanishing direction,
- no text, sign, logo, vehicle, rain, or watermark.

If the scene anchor fails, stop. Fix the scene prompt in source and rerun its tests before making a second live scene request.

- [ ] **Step 3: Generate and review shot 1**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_01
```

Open the nine-frame sheet and the source video. Confirm fast approach, turn, step back, defensive arm, late contact approach, no voice, grounded feet, stable identities, stable camera, and no added person/text.

If it fails one hard gate, choose the single matching retry reason and run candidate 2. Example:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_01 \
  --candidate 2 --retry-reason motion_incomplete
```

Do not request candidate 3.

- [ ] **Step 4: Generate and review shot 2**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_02
```

Inspect the source, nine-frame sheet, and hand sequence. Confirm correct wrist contact, visible force, look-down/look-up, full separation, natural Lin arm recovery, only Su speaking, readable Su mouth/jaw motion, and silent Lin reaction.

If one retry is required, choose exactly one reason:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_02 \
  --candidate 2 --retry-reason hand_contact
```

Do not regenerate shot 1 when shot 1 already passes.

- [ ] **Step 5: Generate and review shot 3**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_03
```

Inspect the source, nine-frame sheet, mouth sequence, and 2→3 continuity pair. Confirm Su remains at the right foreground edge, steps back and points, Lin turns and steps back, only Lin speaks, Lin’s mouth stays visible, and Su reacts without speaking.

If one retry is required:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --enable-live --only shot_03 \
  --candidate 2 --retry-reason lip_visibility
```

Do not request candidate 3.

- [ ] **Step 6: Inspect audio and write verified dialogue timings**

For shots 2 and 3:

- listen to the original native audio,
- inspect an FFmpeg waveform or spectrogram,
- inspect the matching mouth dense sequence,
- record the actual speech start/end offsets,
- bind each timing to the selected MP4 SHA-256,
- set `verified=true` only after start, pauses, and ending look subjectively aligned.

Write `dialogue_timings.json` atomically with `apply_patch`. Do not infer phoneme-level accuracy.

- [ ] **Step 7: Complete source-bound manual review**

Fill every required field in `manual_review.json` for all three selected shots. Use only observations visible in the source video, full-frame sheets, dense sequences, and continuity pairs. Failed observations must include:

- exact timestamp or action phase,
- visible symptom,
- hard-failure or soft-failure classification,
- selected retry reason,
- candidate-2 result when used.

Generate `review.md` from the validated structured review.

- [ ] **Step 8: Compose the final sample**

Run:

```bash
.venv/bin/python factory_cli.py dual-action-stress \
  --project sample_episode --compose
```

Expected:

- no provider call,
- current three-shot chain accepted,
- verified timing accepted,
- review accepted,
- final output at `runs/sample_episode/dual_action_stress_20260723/final/双人动作对白压力测试.mp4`.

- [ ] **Step 9: Run technical preflight and final visual review**

Run:

```bash
.venv/bin/python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  runs/sample_episode/dual_action_stress_20260723/final/双人动作对白压力测试.mp4
```

Then inspect:

- final start, both cut points, both dialogue midpoints, and final frame,
- hand contact before/during/after separation,
- both continuity pair images,
- both mouth dense sequences,
- final audio loudness and absence of overlapping dialogue,
- subtitle start/end against actual native speech.

If composition alone fails, fix local composition and rerun without generating new clips. If a selected source fails a hard visual gate and has no candidate 2 yet, use one targeted retry. If candidate 2 also fails, stop provider spending and record the blocked result.

- [ ] **Step 10: Record the iteration**

Append a concise entry to `docs/iteration-log.md` covering:

- initial problems,
- reasoning and selected three-shot design,
- models and native-audio route,
- candidate results and targeted prompt changes,
- hand, motion, identity, scene, speaker, and lip-motion findings,
- technical preflight result,
- delivered path,
- residual limitations.

Patch only the intended log section and preserve unrelated dirty content.

- [ ] **Step 11: Run full verification**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check factory factory_cli.py tests
.venv/bin/python -m compileall -q factory factory_cli.py
git diff --check
rg -n 'sk-[A-Za-z0-9]|Authorization:|Bearer |data:image|X-Api-Key' \
  factory/dual_action_stress.py factory/dual_action_review.py \
  runs/sample_episode/dual_action_stress_20260723 docs/iteration-log.md
```

Expected:

- tests, Ruff, compile, and diff checks pass,
- preflight passes,
- credential scan shows no secret-bearing source or runtime report,
- generated media remains ignored,
- the approved 61-second output and visual selection are unchanged.

- [ ] **Step 12: Commit implementation records only**

Do not commit generated MP4, images, provider task state, or credentials. Stage only source, tests, and the intended iteration-log hunk:

```bash
git add factory/dual_action_stress.py factory/dual_action_review.py factory_cli.py \
  tests/test_dual_action_stress.py tests/test_dual_action_review.py \
  tests/test_cli_dual_action_stress.py
git add -p docs/iteration-log.md
git commit -m "feat: deliver dual-character action stress sample"
```
