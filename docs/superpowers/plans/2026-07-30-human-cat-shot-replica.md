# Human and Cat Shot Replica Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a source-locked, shot-by-shot remake workflow for the 77.229569-second reference video, replacing the woman with one original AI character and the two source cats with 奶糖 and 豆包 while preserving shot timing, action function, subtitles, and the local evaluation audio track.

**Architecture:** Add a new `pet_replica` workflow beside the existing `pet_longform` pipeline. The workflow ingests the reference as read-only evidence, writes an immutable timeline and audio manifest, creates approved character/scene anchors, generates each shot independently through the existing gateway image/video clients, requires automated plus manual selection, and composes pilot/final/side-by-side outputs with deterministic FFmpeg commands.

**Tech Stack:** Python 3.12, dataclasses, JSON, FFmpeg/FFprobe, OpenCV, Pillow, existing `GatewayImageClient`, existing `GatewayVideoClient`, `doubao-seedream-4-5`, `doubao-seedance-2-0`, pytest, Ruff.

## Global Constraints

- Reference source: `/Users/tml/Desktop/dy-xhs-crawler/data/xhs/videos_selected/飘莉哩_2026-07-23/03_猫猫的脑袋里到底有多少鬼点子！_6a1175370000000035033485.mp4`.
- Output root: `$HOME/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1`.
- Reference source is read-only and must never be overwritten, moved, or normalized in place.
- Master video is exactly `720x1280`, 30 fps CFR, H.264, and `77.229569` seconds within one frame.
- Pilot range is exactly `0.000-12.300` seconds within one frame.
- Source orange-white longhair role maps to 奶糖; source tabby role maps to 豆包.
- The new woman must be an original woman identity and must not reproduce the source woman's recognizable face.
- Preserve the source shot order, cut timing, framing function, actions, dialogue order, subtitle timing, music, and major sound events.
- Do not copy source video frames into the final edit; reference frames are analysis/control evidence only.
- Do not reproduce platform watermarks, account names, avatars, or source end-card branding.
- Local evaluation outputs may reuse the source audio; any public release requires separate rights review or replacement audio.
- Cut timing error is at most 2 frames; whole-film duration error is at most 1 frame.
- Speaking-mouth onset/offset target is at most 0.20 seconds from the source audio window.
- Use at most 3 paid candidates per shot.
- No `tpad`, cloned tail frames, `minterpolate`, optical-flow interpolation, or synthetic motion added during composition.
- Generated clips shorter than the editorial window fail closed and must be regenerated.
- A shot selection is invalid if its source, prompt, anchors, drive audio, model, or candidate bytes change.
- All reports must redact gateway keys, signed URLs, data URIs, and source account tokens.

---

### Task 1: Define the Replica Project and Immutable Contracts

**Files:**
- Create: `factory/pet_replica.py`
- Create: `tests/test_pet_replica.py`
- Create: `config/pet_replica_03.cuts.json`

**Interfaces:**
- Consumes: source video path and output root.
- Produces: `ReplicaCharacter`, `ReplicaShot`, `PetReplicaPlan`, `build_pet_replica_plan()`, `validate_pet_replica_plan()`, and `write_pet_replica_plan()`.

- [ ] **Step 1: Write failing contract tests**

```python
from dataclasses import replace
from pathlib import Path

import pytest

from factory.pet_replica import (
    PILOT_END_S,
    REFERENCE_DURATION_S,
    build_pet_replica_plan,
    validate_pet_replica_plan,
)


def test_reference_replica_plan_is_source_locked(tmp_path):
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"read-only-reference")
    plan = build_pet_replica_plan(source, tmp_path / "output")

    assert plan.duration_s == REFERENCE_DURATION_S == pytest.approx(77.229569)
    assert plan.pilot_end_s == PILOT_END_S == pytest.approx(12.3)
    assert plan.width == 720
    assert plan.height == 1280
    assert plan.fps == 30
    assert plan.characters["source_orange_cat"].target_name == "奶糖"
    assert plan.characters["source_tabby_cat"].target_name == "豆包"
    assert plan.characters["source_woman"].target_name == "原创女主"
    assert plan.source_video == source.resolve()
    assert plan.output_root == (tmp_path / "output").resolve()


def test_plan_rejects_non_contiguous_or_out_of_bounds_shots(tmp_path):
    plan = build_pet_replica_plan(
        tmp_path / "reference.mp4",
        tmp_path / "output",
    )
    broken = replace(
        plan,
        shots=(
            plan.shots[0],
            replace(
                plan.shots[1],
                start_s=plan.shots[1].start_s + 0.1,
            ),
            *plan.shots[2:],
        ),
    )
    with pytest.raises(ValueError, match="contiguous"):
        validate_pet_replica_plan(broken)
```

- [ ] **Step 2: Run the tests and confirm the contract is absent**

Run:

```bash
.venv/bin/python -m pytest tests/test_pet_replica.py -q
```

Expected: FAIL during import because `factory.pet_replica` does not exist.

- [ ] **Step 3: Implement the immutable plan types**

Implement these exact public types:

```python
@dataclass(frozen=True)
class ReplicaCharacter:
    source_role: str
    target_name: str
    reference_path: Path | None
    identity_rule: str


@dataclass(frozen=True)
class ReplicaShot:
    shot_id: str
    index: int
    start_s: float
    end_s: float
    characters: tuple[str, ...]
    speaker: str
    location: str
    framing: str
    action: str
    subtitle: str
    source_audio: bool

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class PetReplicaPlan:
    project_id: str
    source_video: Path
    output_root: Path
    duration_s: float
    pilot_end_s: float
    width: int
    height: int
    fps: int
    characters: Mapping[str, ReplicaCharacter]
    shots: tuple[ReplicaShot, ...]
```

Load cut boundaries from `config/pet_replica_03.cuts.json`. Seed it with the measured frame-aligned boundaries:

```json
{
  "schema_version": "motion-comic-factory.pet-replica-cuts.v1",
  "duration_s": 77.229569,
  "fps": 30,
  "boundaries_s": [
    0.0, 1.733333, 3.1, 5.466667, 6.466667, 7.8, 9.833333,
    11.466667, 12.266667, 13.333333, 14.4, 15.966667, 17.3,
    18.333333, 20.866667, 26.033333, 27.966667, 30.333333,
    31.233333, 33.333333, 34.966667, 36.733333, 39.233333,
    40.9, 45.866667, 46.7, 47.8, 50.4, 52.433333, 54.1,
    57.9, 58.7, 61.266667, 63.3, 65.533333, 72.666667,
    75.566667, 77.229569
  ]
}
```

Round only for display. Validation must compare frame indices, not rounded decimal strings.

- [ ] **Step 4: Write the plan JSON and Markdown contract**

`write_pet_replica_plan()` must atomically write:

- `reference/reference_manifest.json`
- `reference/shot_timeline.json`
- `story_contract.md`

Reports must contain the source SHA-256 and media contract, but never copy the source URL token from the crawler manifest.

- [ ] **Step 5: Run contract tests and lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_pet_replica.py -q
.venv/bin/python -m ruff check factory/pet_replica.py tests/test_pet_replica.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add factory/pet_replica.py tests/test_pet_replica.py config/pet_replica_03.cuts.json
git commit -m "feat: define source locked pet replica contract"
```

### Task 2: Ingest Reference Media and Build Evidence

**Files:**
- Create: `factory/pet_replica_reference.py`
- Create: `tests/test_pet_replica_reference.py`
- Modify: `factory/pet_replica.py`
- Modify: `tests/test_pet_replica.py`

**Interfaces:**
- Consumes: `PetReplicaPlan`.
- Produces: `ReplicaReferenceProbe`, `ReplicaShotAnnotation`,
  `probe_reference_media(plan, runner=subprocess.run) -> ReplicaReferenceProbe`,
  `extract_reference_evidence(plan, runner=subprocess.run) -> Path`,
  `write_shot_annotation_template(plan) -> Path`, and
  `load_reviewed_shot_annotations(plan) -> tuple[ReplicaShotAnnotation, ...]`.

- [ ] **Step 1: Write failing media and path-safety tests**

```python
def test_probe_reference_requires_exact_media_contract(tmp_path, fake_ffprobe):
    plan = replica_plan(tmp_path)
    probe = probe_reference_media(plan, runner=fake_ffprobe)
    assert probe.duration_s == pytest.approx(77.229569)
    assert (probe.width, probe.height, probe.fps) == (720, 1280, 30)
    assert probe.audio_codec == "aac"
    assert probe.audio_sample_rate == 44100
    assert probe.audio_channels == 2


def test_evidence_paths_cannot_escape_output_root(tmp_path):
    plan = replica_plan(tmp_path)
    with pytest.raises(PetReplicaReferenceError, match="output root"):
        extract_reference_evidence(
            plan,
            destination=tmp_path.parent / "escape",
            runner=lambda *args, **kwargs: None,
        )
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_pet_replica_reference.py -q
```

Expected: FAIL because the module and error type do not exist.

- [ ] **Step 3: Implement exact reference probing**

Use `ffprobe -of json` and reject any mismatch in dimensions, frame rate, duration tolerance, video/audio stream count, codec type, or channel count. Persist:

```json
{
  "schema_version": "motion-comic-factory.pet-replica-reference.v1",
  "source_sha256": "b7e742ecf8f16689a5385053287ac4e12aeb95b42aacd45f680c66c3bad6a4c4",
  "duration_s": 77.229569,
  "width": 720,
  "height": 1280,
  "fps": 30,
  "video_codec": "h264",
  "audio_codec": "aac",
  "audio_sample_rate": 44100,
  "audio_channels": 2
}
```

- [ ] **Step 4: Extract per-shot composition evidence**

For every shot, write source frames at:

- `reference/shots/<shot_id>/start.jpg`
- `reference/shots/<shot_id>/middle.jpg`
- `reference/shots/<shot_id>/end.jpg`

Also write one 4x3 pilot contact sheet and full-film 5x8 contact sheets. Each evidence record binds source hash, timestamp, command, image hash, and shot ID.

- [ ] **Step 5: Write an explicit annotation template**

`write_shot_annotation_template()` creates `reference/shot_annotations.template.json` with one object per shot:

```json
{
  "shot_id": "R001",
  "characters": ["source_woman"],
  "speaker": "source_woman",
  "location": "living_room_sofa",
  "framing": "tight_face_closeup",
  "action": "woman looks up and complains to camera",
  "subtitle": "",
  "source_audio": true,
  "manual_review_required": true
}
```

`load_reviewed_shot_annotations()` must reject empty `framing`, `action`, or unreviewed records before any paid generation.

- [ ] **Step 6: Run tests and lint**

Run:

```bash
.venv/bin/python -m pytest tests/test_pet_replica.py tests/test_pet_replica_reference.py -q
.venv/bin/python -m ruff check factory/pet_replica.py factory/pet_replica_reference.py tests/test_pet_replica.py tests/test_pet_replica_reference.py
```

- [ ] **Step 7: Commit**

```bash
git add factory/pet_replica.py factory/pet_replica_reference.py tests/test_pet_replica.py tests/test_pet_replica_reference.py
git commit -m "feat: ingest replica reference evidence"
```

### Task 3: Extract Source Audio and Per-Shot Drive Windows

**Files:**
- Create: `factory/pet_replica_audio.py`
- Create: `tests/test_pet_replica_audio.py`

**Interfaces:**
- Consumes: `PetReplicaPlan` and reviewed shot annotations.
- Produces: `ReplicaAudioAsset`, `ReplicaAudioManifest`,
  `extract_replica_audio(plan, annotations, runner=subprocess.run) -> ReplicaAudioManifest`,
  `validate_replica_audio_manifest(plan, manifest_path) -> ReplicaAudioManifest`, and
  `audio_for_shot(manifest, shot_id) -> Path | None`.

- [ ] **Step 1: Write failing extraction and integrity tests**

```python
def test_audio_manifest_binds_full_aac_and_shot_drive_wavs(tmp_path):
    plan = replica_plan(tmp_path)
    manifest = extract_replica_audio(plan, runner=fake_audio_runner)

    assert manifest.full_source.codec == "aac"
    assert manifest.full_source.sample_rate == 44100
    assert manifest.full_source.channels == 2
    assert manifest.shots["R001"].codec == "pcm_s16le"
    assert manifest.shots["R001"].sample_rate == 48000
    assert manifest.shots["R001"].duration_s == pytest.approx(
        plan.shots[0].duration_s,
        abs=1 / plan.fps,
    )


def test_audio_manifest_rejects_changed_source_hash(tmp_path):
    plan = replica_plan(tmp_path)
    manifest = valid_audio_manifest(plan)
    plan.source_video.write_bytes(b"changed")
    with pytest.raises(PetReplicaAudioError, match="source hash"):
        validate_replica_audio_manifest(plan, manifest.path)
```

- [ ] **Step 2: Verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_pet_replica_audio.py -q
```

- [ ] **Step 3: Implement full-audio and drive-audio extraction**

Write:

- `audio/source_audio.aac` using stream copy from the source.
- `audio/drive/<shot_id>.wav` using `atrim`, timestamp reset, 48 kHz stereo PCM.

Do not normalize, denoise, time-stretch, or synthesize any audio. Persist source and output hashes, measured durations, sample rates, channels, codecs, and exact source windows in `audio/audio_manifest.json`.

- [ ] **Step 4: Enforce source-audio privacy scope**

Every report must include:

```json
{
  "usage_scope": "local_evaluation_only",
  "public_release_ready": false,
  "public_release_blocker": "Replace or license the source audio."
}
```

Do not copy crawler URLs or account tokens into the manifest.

- [ ] **Step 5: Run tests and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_audio.py -q
.venv/bin/python -m ruff check factory/pet_replica_audio.py tests/test_pet_replica_audio.py
```

- [ ] **Step 6: Commit**

```bash
git add factory/pet_replica_audio.py tests/test_pet_replica_audio.py
git commit -m "feat: extract source locked replica audio"
```

### Task 4: Lock Original Woman, Cat, and Scene Anchors

**Files:**
- Create: `factory/pet_replica_assets.py`
- Create: `tests/test_pet_replica_assets.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `PetReplicaPlan`, existing 奶糖/豆包 reference files, and gateway image configuration.
- Produces: `ReplicaAssetManifest`,
  `prepare_replica_asset_jobs(plan, naitang_reference, doubao_reference) -> tuple[ReplicaAssetJob, ...]`,
  `generate_replica_assets(plan, jobs, client_factory, enable_live) -> ReplicaAssetManifest`,
  `write_replica_asset_review_template(plan) -> Path`, and
  `load_approved_replica_assets(plan) -> ReplicaAssetManifest`.

- [ ] **Step 1: Write failing anchor-routing tests**

```python
def test_asset_jobs_generate_woman_and_scenes_but_reuse_approved_cats(tmp_path):
    plan = replica_plan(tmp_path)
    jobs = prepare_replica_asset_jobs(
        plan,
        naitang_reference=approved_cat(tmp_path, "奶糖"),
        doubao_reference=approved_cat(tmp_path, "豆包"),
    )

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
    assert jobs[0].model == "doubao-seedream-4-5"
    assert all(job.size == "1440x2560" for job in jobs)
```

- [ ] **Step 2: Verify failure**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_assets.py -q
```

- [ ] **Step 3: Implement original-woman prompt contracts**

All woman prompts must include:

- original East Asian young adult identity;
- shoulder-length dark auburn layered hair;
- round glasses;
- beige sleeveless lounge dress;
- no resemblance to any reference person;
- no platform watermark, username, logo, subtitle, phone UI, or generated text;
- consistent face, glasses, hair, dress, and jewelry across all views.

Do not pass source frames to woman identity generation. Source frames may only be used later as composition evidence.

- [ ] **Step 4: Install approved cat references without mutation**

Copy the current approved 奶糖 and 豆包 files into `assets/characters/` with their source hashes and provenance. Reject unsupported formats, alpha-only images, symlinks, external paths, or a source/destination hash mismatch.

- [ ] **Step 5: Add manual asset review**

Create `assets/asset_review.template.json` with gates:

- `original_woman_identity`
- `woman_identity_consistent`
- `woman_costume_consistent`
- `naitang_identity_match`
- `doubao_identity_match`
- `scene_geometry_match`
- `scene_light_direction_match`
- `no_source_person_identity`
- `no_platform_branding`
- `no_generated_text`

Use the exact machine key `naitang_identity_match`; the Chinese label can remain “奶糖身份一致”.

- [ ] **Step 6: Run tests and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_assets.py -q
.venv/bin/python -m ruff check factory/pet_replica_assets.py tests/test_pet_replica_assets.py
```

- [ ] **Step 7: Commit**

```bash
git add factory/pet_replica_assets.py tests/test_pet_replica_assets.py .env.example
git commit -m "feat: lock original replica character assets"
```

### Task 5: Build Source-Controlled Shot Jobs and Resumable Generation

**Files:**
- Create: `factory/pet_replica_generation.py`
- Create: `tests/test_pet_replica_generation.py`

**Interfaces:**
- Consumes: plan, reviewed annotations, approved assets, per-shot drive WAVs, and reference evidence frames.
- Produces: `ReplicaShotJob`, `ReplicaCandidate`,
  `build_replica_shot_jobs(plan, annotations, assets, audio, pilot_only, shot_ids, candidate_number) -> tuple[ReplicaShotJob, ...]`,
  `generate_replica_candidates(plan, jobs, config, enable_live, replace_stale) -> tuple[ReplicaCandidate, ...]`, and
  `select_replica_candidate(plan, shot_id, candidate_number, manual_review_note) -> Path`.

- [ ] **Step 1: Write failing shot-job tests**

```python
def test_pilot_jobs_use_reference_composition_and_new_identity_anchors(tmp_path):
    plan = replica_plan(tmp_path)
    jobs = build_replica_shot_jobs(
        plan,
        annotations=reviewed_annotations(plan),
        assets=approved_assets(plan),
        audio=valid_audio_manifest(plan),
        pilot_only=True,
    )

    assert jobs
    assert jobs[-1].start_s < 12.3
    assert all(job.model == "doubao-seedance-2-0" for job in jobs)
    assert all(job.resolution == "720p" for job in jobs)
    assert all(job.ratio == "9:16" for job in jobs)
    assert all(job.generation_duration_s >= 4 for job in jobs)
    assert all(job.candidate_number == 1 for job in jobs)
    assert all("platform watermark" in job.negative_contract for job in jobs)
```

- [ ] **Step 2: Write failing audio-drive routing tests**

```python
def test_speaking_jobs_use_source_drive_audio_without_native_audio(tmp_path):
    job = speaking_job(tmp_path)
    assert job.audio_path.name == "R001.wav"
    assert job.generate_audio is False
    assert "mouth begins" in job.prompt
    assert "mouth closes" in job.prompt


def test_silent_jobs_have_no_drive_audio_and_require_closed_mouth(tmp_path):
    job = silent_reaction_job(tmp_path)
    assert job.audio_path is None
    assert "silent closed mouth" in job.prompt
```

- [ ] **Step 3: Verify tests fail**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_generation.py -q
```

- [ ] **Step 4: Implement prompt compilation**

Each prompt must state:

- source shot ID and editorial duration;
- composition, camera height, framing, subject positions, and eyelines;
- original-woman or 奶糖/豆包 anchor identity;
- action checkpoints in seconds;
- speaking/silent mouth contract;
- prop start and end states;
- retain-room-geometry instruction;
- final 0.25-second settle only;
- no source face, source cat markings, text, watermark, logo, extra person, extra animal, anatomy mutation, camera drift, or object teleportation.

The source frame is a composition reference, never an identity reference. Character anchor images must precede the composition image in the ordered image list.

- [ ] **Step 5: Implement generation duration and editorial trim rules**

Use:

```python
generation_duration_s = min(15, max(4, math.ceil(editorial_duration_s)))
```

Reject a source shot longer than 15 seconds. Persist both durations; never rewrite the editorial duration to the provider duration.

- [ ] **Step 6: Reuse `render_gateway_video_single()`**

Call it with:

```python
render_gateway_video_single(
    job.prompt,
    job.output_path,
    client,
    job.gateway_report_path,
    images=job.reference_images,
    audio=job.audio_path,
    duration=job.generation_duration_s,
    ratio="9:16",
    resolution="720p",
    generate_audio=False,
    allow_network=enable_live,
)
```

Bind provider/model, endpoint fingerprint, prompt hash, source hash, anchor hashes, composition-frame hashes, drive-audio hash, candidate number, and output hash in provenance.

- [ ] **Step 7: Enforce three-candidate and stale-state rules**

Reject candidate numbers outside `1..3`. A changed source, prompt, anchor, audio, or model invalidates reuse and requires explicit `--replace-stale`; never silently resume mismatched state.

- [ ] **Step 8: Run tests and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_generation.py tests/test_gateway_video_batch.py -q
.venv/bin/python -m ruff check factory/pet_replica_generation.py tests/test_pet_replica_generation.py
```

- [ ] **Step 9: Commit**

```bash
git add factory/pet_replica_generation.py tests/test_pet_replica_generation.py
git commit -m "feat: generate source controlled replica shots"
```

### Task 6: Add Automated and Manual Replica Candidate Review

**Files:**
- Create: `factory/pet_replica_review.py`
- Create: `tests/test_pet_replica_review.py`

**Interfaces:**
- Consumes: plan, source evidence, candidate provenance, and candidate MP4.
- Produces: `ReplicaReviewResult`,
  `review_replica_candidate(plan, shot, candidate, frame_reader, probe_runner) -> ReplicaReviewResult`,
  `render_replica_contact_sheet(plan, shot, candidate) -> Path`,
  `approve_replica_candidate(plan, candidate, manual_review) -> Path`, and
  `validate_replica_selection(plan, pilot_only=False) -> None`.

- [ ] **Step 1: Write failing review-gate tests**

```python
def test_review_rejects_wrong_dimensions_short_source_and_long_freeze(tmp_path):
    result = review_replica_candidate(
        plan=replica_plan(tmp_path),
        shot=pilot_shot(tmp_path),
        candidate=bad_candidate(tmp_path),
        frame_reader=fake_bad_frames,
        probe_runner=fake_bad_probe,
    )
    assert result.passed is False
    assert "resolution" in result.failures
    assert "shorter than editorial window" in result.failures
    assert "freeze" in result.failures


def test_selection_requires_all_manual_identity_and_action_gates(tmp_path):
    with pytest.raises(PetReplicaReviewError, match="manual gates"):
        approve_replica_candidate(
            candidate=valid_candidate(tmp_path),
            manual_review={
                "new_identity_match": True,
                "source_identity_absent": False,
            },
        )
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_review.py -q
```

- [ ] **Step 3: Implement automated checks**

Check only the editorial window:

- 720x1280 video;
- usable duration at least editorial duration;
- no black frames;
- no unexpected internal cut;
- longest exact freeze at most 0.35 seconds for active shots;
- sampled-frame hashes differ from the source frame hashes;
- candidate provenance and bytes are current;
- drive-audio speaking activity occurs within the declared window;
- final settle does not exceed the shot contract.

- [ ] **Step 4: Implement manual gates**

Every selected shot requires:

- `new_identity_match`
- `source_identity_absent`
- `character_count_correct`
- `anatomy_correct`
- `framing_matches_reference`
- `screen_position_matches_reference`
- `action_function_matches_reference`
- `mouth_timing_natural`
- `silent_characters_closed_mouth`
- `prop_state_physical`
- `scene_axis_consistent`
- `no_platform_branding`
- `no_generated_text`

The review note must name visible evidence and cannot be empty or use only “pass/ok”.

- [ ] **Step 5: Generate review evidence**

Write:

- 12-frame per-shot contact sheet;
- dense mouth sheet at 8 fps for speaking shots;
- source/candidate side-by-side start-middle-end sheet;
- JSON review bound to candidate SHA-256;
- immutable failed-review archive before selecting a later candidate.

- [ ] **Step 6: Run tests and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_review.py -q
.venv/bin/python -m ruff check factory/pet_replica_review.py tests/test_pet_replica_review.py
```

- [ ] **Step 7: Commit**

```bash
git add factory/pet_replica_review.py tests/test_pet_replica_review.py
git commit -m "feat: review source controlled replica candidates"
```

### Task 7: Compose Pilot, Final, and Side-by-Side Masters

**Files:**
- Create: `factory/pet_replica_compose.py`
- Create: `tests/test_pet_replica_compose.py`

**Interfaces:**
- Consumes: complete selected-shot set, source audio manifest, and reviewed subtitle annotations.
- Produces: `ReplicaCompositionManifest`,
  `build_replica_composition(plan, selection, mode) -> ReplicaCompositionManifest`,
  `compose_replica_pilot(plan) -> ReplicaCompositionManifest`,
  `compose_replica_final(plan) -> ReplicaCompositionManifest`, and
  `validate_replica_master(plan, manifest) -> dict[str, Any]`.

- [ ] **Step 1: Write failing composition-manifest tests**

```python
def test_pilot_composition_trims_real_motion_to_12_3_seconds(tmp_path):
    manifest = build_replica_composition(
        plan=replica_plan(tmp_path),
        selection=complete_pilot_selection(tmp_path),
        mode="pilot",
    )
    assert manifest.start_s == 0.0
    assert manifest.end_s == 12.3
    assert manifest.duration_s == 12.3
    assert "tpad" not in " ".join(manifest.ffmpeg_command)
    assert "minterpolate" not in " ".join(manifest.ffmpeg_command)


def test_final_composition_maps_source_audio_without_timeline_rewrite(tmp_path):
    manifest = build_replica_composition(
        plan=replica_plan(tmp_path),
        selection=complete_full_selection(tmp_path),
        mode="final",
    )
    command = " ".join(manifest.ffmpeg_command)
    assert "-map" in command
    assert "source_audio.aac" in command
    assert manifest.duration_s == pytest.approx(77.229569)
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_compose.py -q
```

- [ ] **Step 3: Implement deterministic video assembly**

For each selected clip:

1. trim only `0..editorial_duration`;
2. reset PTS;
3. scale/crop to 720x1280 without changing aspect ratio;
4. convert to 30 fps by deterministic frame duplication/drop only;
5. concatenate in exact shot order.

Do not add transitions. The reference cut itself is the edit.

- [ ] **Step 4: Rebuild subtitles and exclude source branding**

Render reviewed subtitle text into ASS with the source timing and lower-screen placement. Do not render any OCR item classified as platform watermark, username, avatar, creator label, or source end card.

- [ ] **Step 5: Preserve local-evaluation audio**

Map `audio/source_audio.aac` against the complete video timeline. For the full final, prefer AAC stream copy; if container timestamps prevent one-frame duration compliance, remux through PCM and encode AAC once at 192 kbps without normalization or timeline edits, and record the fallback in `final_qc.json`.

- [ ] **Step 6: Build side-by-side comparison**

Create a 1440x1280 comparison with reference on the left and remake on the right, each letterboxed to 720x1280, with small static labels outside the content area. Use one source audio track only.

- [ ] **Step 7: Implement final validators**

Require:

- exact dimensions and 30 fps;
- one H.264 video stream and one AAC stereo stream;
- pilot/full duration within one frame;
- cut count and each cut timestamp within 2 frames;
- zero black segments;
- no unapproved freeze;
- no direct source-frame hash matches;
- no platform branding in reviewed subtitle manifest;
- complete current selection and manual reviews.

- [ ] **Step 8: Run tests and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_compose.py tests/test_pet_replica_review.py -q
.venv/bin/python -m ruff check factory/pet_replica_compose.py tests/test_pet_replica_compose.py
```

- [ ] **Step 9: Commit**

```bash
git add factory/pet_replica_compose.py tests/test_pet_replica_compose.py
git commit -m "feat: compose source aligned replica masters"
```

### Task 8: Add the `pet-replica` CLI and Pure-Read Status

**Files:**
- Create: `factory/pet_replica_cli.py`
- Create: `tests/test_pet_replica_cli.py`
- Modify: `factory_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: public functions from Tasks 1-7.
- Produces: `pet_replica_status(plan) -> dict[str, Any]` and `pet-replica`
  stages `plan`, `reference`, `audio`, `assets`, `generate`, `review`,
  `compose`, `status`, and `run`.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_pet_replica_plan_writes_source_locked_contract(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "factory_cli.py",
            "pet-replica",
            "--stage",
            "plan",
            "--source",
            str(reference_video(tmp_path)),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["duration_s"] == pytest.approx(77.229569)
    assert payload["next_stage"] == "reference"


def test_status_is_pure_read(tmp_path, monkeypatch):
    forbidden = lambda *args, **kwargs: pytest.fail("status mutated state")
    monkeypatch.setattr(subprocess, "run", forbidden)
    payload = pet_replica_status(replica_plan(tmp_path))
    assert payload["project_id"] == "cat-ideas-shot-replica"
```

- [ ] **Step 2: Verify tests fail**

```bash
.venv/bin/python -m pytest tests/test_pet_replica_cli.py -q
```

- [ ] **Step 3: Implement CLI arguments**

Register:

```text
factory_cli.py pet-replica
  --stage {plan,reference,audio,assets,generate,review,compose,status,run}
  --source ABSOLUTE_MP4
  --output-dir ABSOLUTE_DIR
  --shot R001
  --candidate {1,2,3}
  --pilot-only
  --enable-live
  --replace-stale
```

`--source` is required for `plan` and then read from the current reference manifest for later stages. `generate` defaults to dry-run unless `--enable-live` is explicit.

- [ ] **Step 4: Implement stage gates**

Stage order:

```text
plan -> reference -> audio -> assets -> generate -> review -> compose
```

`compose --pilot-only` requires every shot intersecting `0..12.3` selected and approved. Full compose requires every shot selected and approved.

- [ ] **Step 5: Implement pure-read status**

Status must not call providers, FFmpeg, FFprobe, image decoders, audio decoders, or write files. It validates schema, expected paths, persisted hashes, counts, and the first missing gate using existing evidence only.

- [ ] **Step 6: Document exact commands**

Add to `README.md`:

```bash
.venv/bin/python factory_cli.py pet-replica --stage plan --source "$SOURCE" --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage reference --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage audio --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage assets --output-dir "$OUT" --enable-live
.venv/bin/python factory_cli.py pet-replica --stage generate --output-dir "$OUT" --pilot-only --enable-live
.venv/bin/python factory_cli.py pet-replica --stage review --output-dir "$OUT" --pilot-only
.venv/bin/python factory_cli.py pet-replica --stage compose --output-dir "$OUT" --pilot-only
.venv/bin/python factory_cli.py pet-replica --stage status --output-dir "$OUT"
```

- [ ] **Step 7: Run CLI tests, full related tests, and lint**

```bash
.venv/bin/python -m pytest tests/test_pet_replica*.py tests/test_gateway_video*.py tests/test_gateway_image.py -q
.venv/bin/python -m ruff check factory/pet_replica*.py tests/test_pet_replica*.py factory_cli.py
```

- [ ] **Step 8: Commit**

```bash
git add factory/pet_replica_cli.py tests/test_pet_replica_cli.py factory_cli.py README.md
git commit -m "feat: expose pet replica production workflow"
```

### Task 9: Produce and Review the 0-12.3 Second Pilot

**Files:**
- Generate outside git: `$HOME/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1/`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Consumes: completed CLI from Task 8 and current gateway credentials from environment variables.
- Produces: approved pilot masters, comparison, review evidence, and one iteration-log entry.

- [ ] **Step 1: Initialize reference, timeline, and audio**

Run:

```bash
SOURCE='/Users/tml/Desktop/dy-xhs-crawler/data/xhs/videos_selected/飘莉哩_2026-07-23/03_猫猫的脑袋里到底有多少鬼点子！_6a1175370000000035033485.mp4'
OUT="$HOME/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1"
.venv/bin/python factory_cli.py pet-replica --stage plan --source "$SOURCE" --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage reference --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage audio --output-dir "$OUT"
```

Expected: source contract passes, shot evidence and audio manifest exist, source hash remains unchanged.

- [ ] **Step 2: Complete pilot annotations**

Review every shot intersecting `0..12.3` at normal speed and in the source contact sheet. Fill exact character, speaker, framing, action, prop, subtitle, and speaking-window fields. Mark each record manually reviewed and run the reference stage again to validate.

- [ ] **Step 3: Generate and approve character/scene anchors**

```bash
.venv/bin/python factory_cli.py pet-replica --stage assets --output-dir "$OUT" --enable-live
```

Inspect the woman multi-view sheet, 奶糖/豆包 references, and sofa/phone scenes. Fill `asset_review.json`; do not proceed if the woman resembles the source person or a cat identity drifts.

- [ ] **Step 4: Generate pilot candidate 1**

```bash
.venv/bin/python factory_cli.py pet-replica --stage generate --output-dir "$OUT" --pilot-only --candidate 1 --enable-live
```

Review each shot against source start/middle/end evidence. Archive failures with timestamped reasons.

- [ ] **Step 5: Retry only failed shots**

For each failed shot, edit only its action/framing contract and run candidate 2. Candidate 3 is allowed only after candidate 2 review identifies a different, specific failure. Never rerun already approved shots.

- [ ] **Step 6: Approve and compose the pilot**

```bash
.venv/bin/python factory_cli.py pet-replica --stage review --output-dir "$OUT" --pilot-only
.venv/bin/python factory_cli.py pet-replica --stage compose --output-dir "$OUT" --pilot-only
```

Expected:

- `pilot/猫猫鬼点子_0-12.3秒_无字版.mp4`
- `pilot/猫猫鬼点子_0-12.3秒_字幕版.mp4`
- `pilot/猫猫鬼点子_0-12.3秒_对照版.mp4`
- pilot QC and contact sheets.

- [ ] **Step 7: Run pilot visual and audio checks**

Use:

```bash
python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py "$OUT/pilot/猫猫鬼点子_0-12.3秒_字幕版.mp4"
```

Then inspect the full pilot comparison, all speaking-mouth dense sheets, cut alignment, identities, props, subtitle timing, black frames, freezes, and audio continuity. Reject the pilot if any identity, cut, or lip gate fails.

- [ ] **Step 8: Record the pilot iteration**

Append to `docs/iteration-log.md`:

- source and pilot media facts;
- every failed candidate and visible reason;
- prompt/anchor/timing change;
- before/after effect;
- residual limits;
- pilot output paths and QC.

- [ ] **Step 9: Run regression and commit the log**

```bash
.venv/bin/python -m pytest tests/test_pet_replica*.py -q
.venv/bin/python -m ruff check factory/pet_replica*.py tests/test_pet_replica*.py
git diff --check
git add docs/iteration-log.md
git commit -m "docs: record pet replica pilot iteration"
```

### Task 10: Produce the Full 77.229569-Second Replica and Final QC

**Files:**
- Generate outside git: `$HOME/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1/final/`
- Modify: `docs/iteration-log.md`
- Modify: `docs/quality-iteration-handbook.md`

**Interfaces:**
- Consumes: approved pilot baseline and all remaining reviewed annotations.
- Produces: complete full-length masters, final evidence, and updated long-form iteration documentation.

- [ ] **Step 1: Freeze the approved pilot**

Record selected candidate hashes for all pilot shots. Full generation must treat those clips as immutable and skip them unless an explicit downstream continuity failure names a pilot shot.

- [ ] **Step 2: Complete annotations for remaining shots**

Review the source from `12.3..77.229569` and fill every remaining action, framing, prop, subtitle, and speaker field. Validate the complete timeline before network calls.

- [ ] **Step 3: Generate remaining shots in bounded batches**

Run batches of at most 8 unapproved shots:

```bash
.venv/bin/python factory_cli.py pet-replica --stage generate --output-dir "$OUT" --candidate 1 --enable-live
```

Use explicit `--shot` filters for each batch. Review and select before starting the next batch.

- [ ] **Step 4: Retry failures with the same three-candidate policy**

Reject source-face leakage, cat-marking leakage, wrong character count, action mismatch, speaking-mouth mismatch, prop teleportation, freeze, camera drift, or platform branding. Retry only the failed shot and its real continuity dependents.

- [ ] **Step 5: Compose all final masters**

```bash
.venv/bin/python factory_cli.py pet-replica --stage review --output-dir "$OUT"
.venv/bin/python factory_cli.py pet-replica --stage compose --output-dir "$OUT"
```

Expected:

- `final/猫猫鬼点子_逐镜重拍_无字版.mp4`
- `final/猫猫鬼点子_逐镜重拍_字幕版.mp4`
- `final/猫猫鬼点子_逐镜重拍_对照版.mp4`
- `final/final_qc.json`

- [ ] **Step 6: Perform full-film visual review**

Watch the remake twice at normal speed. Inspect:

- all 40-second 1 fps sheets;
- dense sheets around every cut mismatch;
- every woman/cat speaking window;
- all human-cat contact and prop interactions;
- first and last frames of every selected shot;
- the source/remake side-by-side master.

- [ ] **Step 7: Perform technical and audio review**

Require:

- 720x1280, 30 fps CFR, H.264;
- one AAC stereo stream;
- total duration within 1 frame;
- all cuts within 2 frames;
- zero black segments;
- no unapproved freeze;
- source audio sequence unchanged;
- no platform watermark, account label, or source end card;
- no direct source-frame hash match.

- [ ] **Step 8: Run full regression**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check factory tests factory_cli.py
git diff --check
```

- [ ] **Step 9: Document all bad-case corrections**

Append the final round to both documentation files. Record what failed, the evidence, the reasoning, the targeted correction, the measurable effect, and the honest residual risk. Explicitly state that the local evaluation master reuses source audio and is not automatically cleared for public distribution.

- [ ] **Step 10: Commit documentation only**

```bash
git add docs/iteration-log.md docs/quality-iteration-handbook.md
git commit -m "docs: record full pet replica quality loop"
```

## Final Acceptance Checklist

- [ ] Reference source hash is unchanged.
- [ ] Woman identity is original and consistent across all shots.
- [ ] 奶糖 and 豆包 remain identifiable and never swap roles.
- [ ] Every cut is within 2 frames of the reference.
- [ ] Final duration is within 1 frame of `77.229569` seconds.
- [ ] All visuals are newly generated and no source frame is directly copied.
- [ ] Source audio timeline is unchanged in local evaluation outputs.
- [ ] Subtitles preserve content/timing while excluding platform and account branding.
- [ ] Speaking windows have natural mouth motion within 0.20 seconds.
- [ ] No black frame, unapproved freeze, object teleportation, extra character, or anatomy mutation remains.
- [ ] Pilot, full remake, and side-by-side comparison all pass technical preflight.
- [ ] Full pytest and Ruff pass.
- [ ] Iteration documents include failures, reasoning, corrections, effects, and residual limits.
