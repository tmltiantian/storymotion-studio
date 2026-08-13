# Pet Sitcom Audio-First Continuity Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `冻干到底是谁偷吃的？` as a 54-second, ten-shot pet sitcom whose cat dialogue drives mouth motion, whose physical actions remain continuous across cuts, and whose music and transitions follow the story.

**Architecture:** Replace the fixed fourteen-by-five-second contract with a variable-duration ten-shot plan and an explicit dependency graph. Generate immutable Doubao Seed-TTS assets before video, test the gateway's reference-audio transport with one bounded Seedance probe, and pass the exact final cat audio into every speaking video request. Compose selected clips without dialogue retiming, using one approved real music source, restrained foley, hard cuts, and J/L audio bridges; block release until technical and frame-by-frame review gates pass.

**Tech Stack:** Python 3.12, dataclasses, JSON, Pillow, FFmpeg/FFprobe, pytest, existing `GatewayVideoClient`, existing `DoubaoTTSClient`, Doubao Seedance 2.0, Doubao Seed-TTS 2.0.

## Global Constraints

- Implement `docs/superpowers/specs/2026-07-26-pet-sitcom-audio-first-continuity-redesign.md`.
- The final edit duration is exactly `54.0` seconds and must remain within `53.85` to `54.15` seconds after encoding.
- The story uses ten variable-duration shots with edit durations `[5.2, 3.4, 6.4, 4.2, 7.3, 6.1, 4.8, 7.0, 5.5, 4.1]`.
- The production video model is `doubao-seedance-2-0`; do not use the fast route for final speaking shots.
- The production audio model is Doubao `seed-tts-2.0`; Naitang, Doubao, and the owner keep immutable voice IDs.
- Generate and hash final TTS before submitting any speaking video shot.
- A successful, manually approved reference-audio probe is required before production speaking shots.
- If the gateway does not accept reference audio and no verified lip-sync provider is configured, stop without bulk-generating speaking shots.
- Never use optical flow, `minterpolate`, arbitrary digital zoom, decorative video transitions, or repeated still-frame padding to conceal failed motion.
- Keep Naitang screen-left looking right and Doubao screen-right looking left in the main kitchen setup.
- Generated source audio is never used as final dialogue; final dialogue uses the exact pre-generated TTS that drove the speaking shot.
- Do not time-stretch final cat or owner dialogue with FFmpeg `atempo`.
- Music must come from one locally approved source file that is at least 54 seconds long, must be hash-bound in provenance, and must not be looped.
- Keep every runtime artifact under the selected pet-sitcom output directory; reject symlink escapes and redact credentials and signed URLs.
- Live provider calls require `--enable-live`.
- Preserve unrelated uncommitted work already present in the repository.

---

## File Structure

- `factory/pet_sitcom.py`: immutable ten-shot story, duration, spatial, transition, and dependency contracts.
- `factory/pet_sitcom_audio_first.py`: fixed voice generation, trimmed TTS validation, absolute dialogue timeline, and padded reference-audio creation.
- `factory/gateway_video.py`: safe local/remote reference-audio transport for the gateway video API.
- `factory/pet_sitcom_audio_probe.py`: one-shot Seedance reference-audio capability probe and approval gate.
- `factory/pet_sitcom_generation.py`: dependency-aware production generation, provenance, selection, and invalidation.
- `factory/pet_sitcom_sound.py`: approved music provenance, three-act cue map, room tone, and restrained foley.
- `factory/pet_sitcom_compose.py`: variable-duration video normalization, audio bridges, no-retime dialogue mix, overlays, and final encoding.
- `factory/pet_sitcom_review.py`: variable-duration source QC, continuity comparisons, mouth timing review, final QC, and review report.
- `factory_cli.py`: dry-run, audio, audio-probe, shots, review, compose, and status orchestration.
- `tests/test_pet_sitcom*.py` and `tests/test_gateway_video.py`: offline contract and regression coverage.

---

### Task 1: Replace The Fixed Story Contract With The Ten-Shot Continuity Plan

**Files:**
- Modify: `factory/pet_sitcom.py`
- Modify: `tests/test_pet_sitcom.py`

**Interfaces:**
- Produces `PetShot.duration_seconds: float`.
- Produces `PetShot.generation_duration_seconds: int`.
- Produces `PetShot.dialogue_offset_seconds: float`.
- Produces `PetShot.continuity_source_ids: tuple[str, ...]`.
- Produces `PetShot.transition: str`, `start_state: str`, and `end_state: str`.
- Produces `PetSitcomPlan.duration_seconds: float`.
- Later tasks consume the exact durations, dependency graph, dialogue offsets, and state descriptions.

- [ ] **Step 1: Write failing ten-shot contract tests**

```python
def test_plan_uses_ten_variable_duration_shots(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")

    assert plan.duration_seconds == 54.0
    assert [shot.duration_seconds for shot in plan.shots] == [
        5.2, 3.4, 6.4, 4.2, 7.3, 6.1, 4.8, 7.0, 5.5, 4.1
    ]
    assert [shot.generation_duration_seconds for shot in plan.shots] == [
        6, 4, 7, 5, 8, 7, 5, 8, 6, 5
    ]
    assert sum(shot.duration_seconds for shot in plan.shots) == 54.0


def test_plan_combines_dialogue_into_the_approved_ten_shots(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")

    assert [(shot.shot_id, shot.speaker, shot.dialogue) for shot in plan.shots] == [
        ("shot_01", "owner", "谁把新开的冻干吃完了？"),
        ("shot_02", None, ""),
        ("shot_03", "naitang", "我昨晚一直在睡觉。豆包半夜去过厨房，我听见了。"),
        ("shot_04", "doubao", "我去喝水。"),
        ("shot_05", "doubao", "倒是你，回来时胡子上有一股鸡肉味。"),
        ("shot_06", "owner", "监控只拍到一条尾巴。"),
        ("shot_07", None, ""),
        ("shot_08", "naitang", "橘色尾巴那么多，不能因为颜色就怀疑一只无辜的小猫。"),
        ("shot_09", "doubao", "那你嘴边这个是什么？"),
        ("shot_10", "naitang", "证据也可能是后来粘上去的。"),
    ]


def test_plan_encodes_non_linear_replay_return_dependencies(config, tmp_path):
    plan = build_pet_sitcom_plan(config, output_dir=tmp_path / "pet-v2")
    dependencies = {
        shot.shot_id: shot.continuity_source_ids for shot in plan.shots
    }

    assert dependencies == {
        "shot_01": (),
        "shot_02": ("shot_01",),
        "shot_03": ("shot_02",),
        "shot_04": ("shot_03",),
        "shot_05": ("shot_04",),
        "shot_06": ("shot_05",),
        "shot_07": ("shot_05", "shot_06"),
        "shot_08": ("shot_07",),
        "shot_09": ("shot_08",),
        "shot_10": ("shot_09",),
    }
    assert plan.shots[6].transition == "match_tail_right_to_left_with_audio_l_cut"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom.py
```

Expected: failures show the old fourteen-shot, five-second contract and missing fields.

- [ ] **Step 3: Add the exact v2 dataclass fields and constants**

```python
PLAN_SCHEMA_VERSION = "motion-comic-factory.pet-sitcom-plan.v2"
PROJECT_ID = "pet_sitcom_audio_first_20260726"
FINAL_DURATION_SECONDS = 54.0


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
```

- [ ] **Step 4: Encode the approved spatial and action states**

Require every main-scene prompt to state:

```text
Naitang remains screen-left and looks right. Doubao remains screen-right and
looks left. The owner remains off camera. Preserve the 180-degree axis, kitchen
doorway geometry, warm daylight from frame left, bag position, tail position,
mirror position, and each cat's pose from the declared start state.
```

Encode the ten actions and dialogue exactly as the design document. Set these dialogue offsets:

```python
{
    "shot_01": 0.55,
    "shot_03": 0.55,
    "shot_04": 0.65,
    "shot_05": 0.55,
    "shot_06": -0.20,
    "shot_08": 0.55,
    "shot_09": 2.55,
    "shot_10": 0.75,
}
```

`shot_06` is an owner J-cut and may begin 0.20 seconds before its visual clip. Cat dialogue offsets must be non-negative and leave at least 0.30 seconds after speech.

- [ ] **Step 5: Validate the dependency graph and output paths**

Reject unknown, self, forward, and cyclic continuity dependencies. Include every dependency continuity image in `all_output_paths()`, using:

```python
output_dir / "continuity" / f"{source_id}_last.png"
```

Write plan JSON with `duration_seconds`, generation duration, edit duration, transition, start/end state, dependencies, and dialogue offsets. Keep credentials absent.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom.py
.venv/bin/ruff check factory/pet_sitcom.py tests/test_pet_sitcom.py
git add factory/pet_sitcom.py tests/test_pet_sitcom.py
git commit -m "feat: define audio-first pet sitcom story contract"
```

Expected: ten-shot plan tests pass.

---

### Task 2: Generate Fixed Voices And An Absolute Audio-First Timeline

**Files:**
- Create: `factory/pet_sitcom_audio_first.py`
- Create: `tests/test_pet_sitcom_audio_first.py`
- Modify: `factory/pet_sitcom_compose.py`
- Modify: `tests/test_pet_sitcom_compose.py`

**Interfaces:**
- Produces immutable `PetSpeechAsset`.
- Produces `generate_pet_speech_assets(plan, *, tts_client, allow_network=False) -> dict[str, Any]`.
- Produces `load_pet_speech_assets(plan) -> tuple[PetSpeechAsset, ...]`.
- Produces `build_pet_drive_audio(plan, shot_id, *, command_runner=subprocess.run) -> Path`.
- Existing `generate_owner_voice_lines` and `generate_cat_voice_lines` remain compatibility wrappers around `generate_pet_speech_assets`.
- Tasks 4, 5, and 7 consume the same trimmed audio hashes and absolute start/end times.

- [ ] **Step 1: Write failing fixed-voice and timing tests**

```python
def test_voice_map_is_immutable_and_cute():
    assert PET_VOICES == {
        "owner": PetVoice("zh_female_vv_uranus_bigtts", -4),
        "naitang": PetVoice("saturn_zh_female_tiaopigongzhu_tob", -2),
        "doubao": PetVoice("saturn_zh_female_keainvsheng_tob", -3),
    }


def test_manifest_uses_real_tts_duration_without_atempo(plan, fake_tts, monkeypatch):
    monkeypatch.setattr(
        "factory.pet_sitcom_audio_first.probe_media",
        fake_audio_probe(duration_seconds=1.35),
    )

    report = generate_pet_speech_assets(
        plan, tts_client=fake_tts, allow_network=True
    )
    assets = load_pet_speech_assets(plan)

    assert report["success"] is True
    assert len(assets) == 8
    assert assets[0].absolute_start_seconds == pytest.approx(0.55)
    assert assets[0].absolute_end_seconds == pytest.approx(1.90)
    assert all(asset.output_sha256 for asset in assets)


def test_drive_audio_is_padded_not_retimed(plan, fake_assets, command_recorder):
    path = build_pet_drive_audio(
        plan, "shot_04", command_runner=command_recorder
    )
    command = command_recorder.calls[0]

    assert path.name == "shot_04_drive.wav"
    assert "atempo" not in " ".join(command)
    assert "adelay=650|650" in " ".join(command)
    assert "atrim=duration=5" in " ".join(command)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_audio_first.py
```

Expected: module import fails.

- [ ] **Step 3: Implement exact voice and speech asset types**

```python
@dataclass(frozen=True)
class PetVoice:
    voice_id: str
    speech_rate: int


@dataclass(frozen=True)
class PetSpeechAsset:
    shot_id: str
    speaker: str
    text: str
    voice_id: str
    speech_rate: int
    output_path: Path
    output_sha256: str
    duration_seconds: float
    absolute_start_seconds: float
    absolute_end_seconds: float
```

Store the manifest as `motion-comic-factory.pet-sitcom-audio-first.v1`. Bind every record to the plan schema, exact dialogue, voice ID, speech rate, output path, WAV hash, WAV duration, and plan hash.

- [ ] **Step 4: Generate and trim all eight spoken lines**

Use `DoubaoTTSClient.synthesize` at 24 kHz, then FFmpeg:

```text
silenceremove=start_periods=1:start_duration=0.03:start_threshold=-42dB:
start_silence=0.08,areverse,silenceremove=start_periods=1:
start_duration=0.03:start_threshold=-42dB:start_silence=0.08,areverse
```

Convert to PCM s16le, 48 kHz, stereo. Reject empty audio, sample rates below 24 kHz, duration below 0.20 seconds, and audio that overruns the visual shot's available dialogue window. Do not call `atempo`.

- [ ] **Step 5: Build exact padded drive WAVs for cat shots**

For `shot_03`, `shot_04`, `shot_05`, `shot_08`, `shot_09`, and `shot_10`, create:

```text
output_dir/audio/drive/<shot_id>_drive.wav
```

Each drive WAV is exactly `generation_duration_seconds`, starts the immutable TTS at `dialogue_offset_seconds`, and contains silence before and after. Bind its hash to the trimmed TTS hash and timing in a `.state.json` sidecar.

- [ ] **Step 6: Keep old public wrappers but remove post-video timing ownership**

Change `generate_owner_voice_lines` and `generate_cat_voice_lines` into filtered wrappers that call the new generator and return only the requested role records. Remove the requirement that cat TTS be bound after manually choosing mouth windows. `load_verified_pet_timings` must read the audio-first manifest and later add selected video hashes without changing start/end times.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_audio_first.py tests/test_pet_sitcom_compose.py
.venv/bin/ruff check factory/pet_sitcom_audio_first.py factory/pet_sitcom_compose.py tests/test_pet_sitcom_audio_first.py tests/test_pet_sitcom_compose.py
git add factory/pet_sitcom_audio_first.py factory/pet_sitcom_compose.py tests/test_pet_sitcom_audio_first.py tests/test_pet_sitcom_compose.py
git commit -m "feat: lock pet dialogue before video generation"
```

Expected: all speech assets are deterministic and no final dialogue filter contains `atempo`.

---

### Task 3: Add Safe Reference-Audio Transport To Gateway Video

**Files:**
- Modify: `factory/gateway_video.py`
- Modify: `tests/test_gateway_video.py`
- Modify: `factory/gateway_video_batch.py`
- Modify: `tests/test_gateway_video_batch.py`

**Interfaces:**
- Adds `GatewayVideoConfig.max_reference_audio_bytes: int = 16 * 1024 * 1024`.
- Adds keyword `audio: str | Path | None = None` to `submit`, `prepare_submission`, and `generate`.
- Produces one `metadata.content` item with `type="audio_url"`, `audio_url.url`, and `role="reference_audio"`.
- Task 4 consumes this without constructing raw provider JSON.

- [ ] **Step 1: Write failing request-body tests**

```python
def test_gateway_video_submit_embeds_one_reference_audio(tmp_path):
    audio = tmp_path / "line.wav"
    write_wav(audio, sample_rate=48000, channels=2, duration=1.0)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeResponse({"id": "task-audio", "status": "queued"})

    _client(fake_urlopen).submit(
        "the cat speaks only with the supplied audio",
        audio=audio,
        duration=5,
        generate_audio=True,
        allow_network=True,
    )
    payload = json.loads(requests[0].data)
    item = payload["metadata"]["content"][0]

    assert item["type"] == "audio_url"
    assert item["role"] == "reference_audio"
    assert item["audio_url"]["url"].startswith("data:audio/wav;base64,")
    assert "audio" not in payload


def test_gateway_video_rejects_invalid_audio_before_network(tmp_path):
    audio = tmp_path / "line.wav"
    audio.write_bytes(b"not-a-wave")
    contacted = False

    def fake_urlopen(request, timeout):
        nonlocal contacted
        contacted = True
        raise AssertionError("network must not be called")

    with pytest.raises(GatewayVideoError, match="valid audio"):
        _client(fake_urlopen).submit(
            "speak", audio=audio, allow_network=True
        )

    assert contacted is False
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_gateway_video.py -k "reference_audio or invalid_audio"
```

Expected: `audio` is not an accepted keyword.

- [ ] **Step 3: Implement local and remote reference-audio normalization**

Accept:

- local `.wav`, `.mp3`, `.m4a`, or `.aac`,
- `https://` audio URLs without credentials,
- `data:audio/wav;base64,`, `data:audio/mpeg;base64,`, `data:audio/mp4;base64,`, and `data:audio/aac;base64,`.

For local files, require one audio stream, positive duration, and no video stream using `probe_media`. Read at most `max_reference_audio_bytes`, then encode a data URI. Reject symlinks, unsupported suffixes, invalid media, empty data, and oversized content.

- [ ] **Step 4: Add audio content to the existing metadata list**

Keep image content first and append audio last:

```python
content = [
    {
        "type": "image_url",
        "image_url": {"url": image},
        "role": "reference_image",
    }
    for image in image_values
]
if audio_value:
    content.append(
        {
            "type": "audio_url",
            "audio_url": {"url": audio_value},
            "role": "reference_audio",
        }
    )
metadata["content"] = content
```

Redact `data:audio/*;base64,...` from exceptions and reports. Include the reference-audio hash, not its data URI, in resumable gateway state signatures.

- [ ] **Step 5: Thread the optional audio argument through the batch helper**

Add `audio: str | Path | None = None` to `render_gateway_video_single`. Persist only:

```python
{
    "reference_audio_path": str(Path(audio).resolve()),
    "reference_audio_sha256": sha256_file(Path(audio)),
}
```

Do not persist the data URI or provider authorization.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_gateway_video.py tests/test_gateway_video_batch.py
.venv/bin/ruff check factory/gateway_video.py factory/gateway_video_batch.py tests/test_gateway_video.py tests/test_gateway_video_batch.py
git add factory/gateway_video.py factory/gateway_video_batch.py tests/test_gateway_video.py tests/test_gateway_video_batch.py
git commit -m "feat: support gateway video reference audio"
```

Expected: image-only calls retain the existing payload; audio calls contain one validated reference-audio item.

---

### Task 4: Add A Bounded Seedance Audio-Drive Probe Gate

**Files:**
- Create: `factory/pet_sitcom_audio_probe.py`
- Create: `tests/test_pet_sitcom_audio_probe.py`
- Modify: `factory/pet_sitcom_generation.py`
- Modify: `tests/test_pet_sitcom_generation.py`

**Interfaces:**
- Produces `run_pet_audio_drive_probe(plan, *, video_client, allow_network=False) -> dict[str, Any]`.
- Produces `write_pet_audio_probe_review_template(plan) -> Path`.
- Produces `require_approved_pet_audio_probe(plan) -> dict[str, Any]`.
- Production speaking shots consume the approval bound to exact TTS, drive WAV, references, prompt, model, and probe MP4 hashes.

- [ ] **Step 1: Write failing gate tests**

```python
def test_probe_uses_final_doubao_audio_and_one_live_request(
    plan, fake_video_client, prepared_audio_manifest
):
    report = run_pet_audio_drive_probe(
        plan, video_client=fake_video_client, allow_network=True
    )
    call = fake_video_client.calls[0]

    assert report["success"] is True
    assert len(fake_video_client.calls) == 1
    assert call["audio"].name == "shot_04_drive.wav"
    assert call["duration"] == 5
    assert call["generate_audio"] is True
    assert report["source_shot_id"] == "shot_04"


def test_production_gate_rejects_provider_success_without_manual_review(
    plan, successful_probe
):
    with pytest.raises(PetSitcomGenerationError, match="approved audio-drive probe"):
        require_approved_pet_audio_probe(plan)


def test_probe_review_is_hash_bound(plan, successful_probe, passing_probe_review):
    approved = require_approved_pet_audio_probe(plan)
    assert approved["approved"] is True

    successful_probe.write_bytes(successful_probe.read_bytes() + b"changed")
    with pytest.raises(PetSitcomGenerationError, match="hash"):
        require_approved_pet_audio_probe(plan)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_audio_probe.py
```

Expected: module import fails.

- [ ] **Step 3: Implement one exact probe**

Use final `shot_04` Doubao assets:

```python
PROBE_SOURCE_SHOT_ID = "shot_04"
PROBE_MODEL = "doubao-seedance-2-0"
PROBE_REVIEW_GATES = (
    "reference_audio_accepted",
    "correct_doubao_identity",
    "correct_speaker_only",
    "mouth_moves_during_dialogue",
    "mouth_stays_closed_outside_dialogue",
    "natural_feline_mouth",
    "onset_offset_within_0_25_seconds",
    "no_audio_retiming_or_repetition",
)
```

Submit the Doubao character sheet, kitchen anchor, and exact padded `shot_04_drive.wav`. Store the MP4 under `tests/audio_drive_probe/shot_04.mp4`.

- [ ] **Step 4: Persist safe capability outcomes**

The probe report uses one of:

```python
{"capability": "supported", "success": True}
{"capability": "unsupported", "success": False, "http_status_code": 400}
{"capability": "inconclusive", "success": False, "task_id": "safe-task-id"}
```

Never automatically retry an ambiguous submission. Bind the report to model, prompt hash, reference hashes, drive-audio hash, gateway report hash, and MP4 hash.

- [ ] **Step 5: Create frame evidence and a strict review template**

Extract frames at:

```python
(0.20, 0.55, 0.80, 1.10, 1.40, 1.80, 2.20, 3.00, 4.50)
```

The review must record `audio_onset_seconds`, `mouth_onset_seconds`, `audio_offset_seconds`, and `mouth_offset_seconds`. Approval requires absolute onset error no greater than `0.25` seconds, absolute offset error no greater than `0.25` seconds, and every boolean gate true.

- [ ] **Step 6: Make production generation fail closed**

Call `require_approved_pet_audio_probe(plan)` before any production `GatewayVideoClient` submission. Dry runs may report the missing gate but must make zero provider calls.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_audio_probe.py tests/test_pet_sitcom_generation.py
.venv/bin/ruff check factory/pet_sitcom_audio_probe.py factory/pet_sitcom_generation.py tests/test_pet_sitcom_audio_probe.py tests/test_pet_sitcom_generation.py
git add factory/pet_sitcom_audio_probe.py factory/pet_sitcom_generation.py tests/test_pet_sitcom_audio_probe.py tests/test_pet_sitcom_generation.py
git commit -m "feat: gate pet shots on audio-driven mouth probe"
```

Expected: production calls remain blocked until the exact probe is manually approved.

---

### Task 5: Generate Speaking Shots With Final Audio And Dependency-Aware Continuity

**Files:**
- Modify: `factory/pet_sitcom_generation.py`
- Modify: `tests/test_pet_sitcom_generation.py`

**Interfaces:**
- `generate_pet_sitcom_shots` sends `audio=drive_audio` only for cat speaking shots.
- Candidate provenance stores `dependency_video_sha256: dict[str, str]`.
- Candidate selection invalidates the transitive dependency graph, not all later numeric shots.
- Task 6 consumes selected dependency edges for evidence.

- [ ] **Step 1: Write failing production request tests**

```python
def test_cat_shot_uses_exact_drive_audio(
    plan, approved_probe, prepared_audio_manifest, selected_dependencies, fake_video_client
):
    report = generate_pet_sitcom_shots(
        plan,
        video_client=fake_video_client,
        allow_network=True,
        shot_id="shot_03",
    )
    call = fake_video_client.calls[0]

    assert report["success"] is True
    assert call["audio"] == plan.output_dir / "audio/drive/shot_03_drive.wav"
    assert call["duration"] == 7
    assert call["generate_audio"] is True


def test_silent_and_owner_shots_do_not_send_reference_audio(
    plan, approved_probe, prepared_audio_manifest, selected_dependencies, fake_video_client
):
    generate_pet_sitcom_shots(
        plan,
        video_client=fake_video_client,
        allow_network=True,
        shot_id="shot_06",
    )
    assert fake_video_client.calls[0]["audio"] is None


def test_shot_07_uses_master_and_replay_continuity_frames(
    plan, approved_probe, prepared_audio_manifest, selected_shot_05_and_06, fake_video_client
):
    generate_pet_sitcom_shots(
        plan,
        video_client=fake_video_client,
        allow_network=True,
        shot_id="shot_07",
    )
    images = fake_video_client.calls[0]["images"]

    assert images[-2].name == "shot_05_last.png"
    assert images[-1].name == "shot_06_last.png"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py -k "drive_audio or continuity_frames or dependency"
```

Expected: old calls always use five seconds and have no audio input or multi-source dependencies.

- [ ] **Step 3: Build references from declared dependencies**

Start every request with:

1. Naitang immutable character sheet.
2. Doubao immutable character sheet.
3. Current kitchen scene anchor.

Append each declared dependency's selected last frame in order. For `shot_07`, label the two reference roles in the prompt:

```text
Reference 4 preserves the main-scene layout from shot_05.
Reference 5 preserves the right-to-left orange-tail motion from shot_06.
Return to the main camera axis while continuing the tail direction.
```

- [ ] **Step 4: Submit exact generation durations and audio**

Use:

```python
drive_audio = (
    build_pet_drive_audio(plan, shot.shot_id)
    if shot.speaker in {"naitang", "doubao"}
    else None
)
```

Send `duration=shot.generation_duration_seconds`, `resolution="1080p"`, and `generate_audio=drive_audio is not None`. Owner and silent shots use no reference audio and no native generated speech.

- [ ] **Step 5: Replace single previous hash with dependency hashes**

Persist:

```python
"dependency_video_sha256": {
    source_id: selections[source_id]["video_sha256"]
    for source_id in shot.continuity_source_ids
},
"reference_audio_sha256": file_hash_or_empty(drive_audio),
```

Reject reuse or selection if any dependency, reference, prompt, TTS, or drive-audio hash changes.

- [ ] **Step 6: Invalidate only transitive dependents**

Implement:

```python
def dependent_shot_ids(plan: PetSitcomPlan, changed_shot_id: str) -> tuple[str, ...]:
    pending = [changed_shot_id]
    result: list[str] = []
    while pending:
        current = pending.pop(0)
        for shot in plan.shots:
            if current in shot.continuity_source_ids and shot.shot_id not in result:
                result.append(shot.shot_id)
                pending.append(shot.shot_id)
    return tuple(result)
```

Changing `shot_06` invalidates `shot_07`, `shot_08`, `shot_09`, and `shot_10`; changing `shot_05` additionally invalidates `shot_06`.

- [ ] **Step 7: Extract continuity at each edit endpoint**

Replace the fixed `4.88` timestamp with:

```python
timestamp = min(
    shot.duration_seconds - 0.08,
    probed_video_duration_seconds - 0.08,
)
```

Persist the exact timestamp, source hash, frame hash, and edit duration.

- [ ] **Step 8: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_generation.py
.venv/bin/ruff check factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
git add factory/pet_sitcom_generation.py tests/test_pet_sitcom_generation.py
git commit -m "feat: generate dependency-bound audio-driven pet shots"
```

Expected: every cat speaking shot is source-bound to final TTS, and replay-return continuity has two explicit dependencies.

---

### Task 6: Add Mouth-Timing, Motion, And Continuity Review Gates

**Files:**
- Modify: `factory/pet_sitcom_review.py`
- Modify: `tests/test_pet_sitcom_review.py`

**Interfaces:**
- Updates `SHOT_REVIEW_SCHEMA` to v4.
- Adds `mouth_timing` records for six cat speaking shots.
- Builds continuity evidence for every declared dependency edge.
- Rejects intra-shot freeze longer than `0.35` seconds and unexplained visual jumps.

- [ ] **Step 1: Write failing review contract tests**

```python
def test_review_requires_mouth_timing_for_every_cat_speaking_shot(
    plan, selected_sources, audio_manifest
):
    review = build_review_template(plan)
    expected = {"shot_03", "shot_04", "shot_05", "shot_08", "shot_09", "shot_10"}

    assert set(review["mouth_timing"]) == expected
    assert review["mouth_timing"]["shot_04"]["max_onset_error_seconds"] == 0.25
    assert review["mouth_timing"]["shot_04"]["max_offset_error_seconds"] == 0.25


def test_review_rejects_mouth_motion_that_starts_too_late(
    plan, passing_review_document
):
    record = passing_review_document["mouth_timing"]["shot_04"]
    record["audio_onset_seconds"] = 0.65
    record["mouth_onset_seconds"] = 1.05

    with pytest.raises(PetSitcomReviewError, match="mouth onset"):
        validate_pet_shot_reviews(plan)


def test_continuity_evidence_covers_declared_edges(plan, selected_sources):
    evidence = build_source_evidence(plan)
    pairs = {
        (item["previous_shot_id"], item["current_shot_id"])
        for item in evidence["continuity_comparisons"]
    }

    assert ("shot_05", "shot_07") in pairs
    assert ("shot_06", "shot_07") in pairs
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_review.py -k "mouth_timing or continuity_evidence"
```

Expected: old review expects fourteen fixed shots and no timing measurements.

- [ ] **Step 3: Update source technical QC**

For each source:

- expected duration is `shot.generation_duration_seconds`,
- accepted duration is expected ±0.35 seconds,
- exactly one video stream is required,
- audio is required only for cat speaking sources,
- black frames may not exceed 0.08 seconds,
- freeze detection uses `freezedetect=n=-50dB:d=0.35`,
- cuts or perceptual jumps inside a single generated shot are reported as manual-review failures.

- [ ] **Step 4: Build dependency-edge continuity sheets**

For every dependency edge, place:

- previous source at `previous_edit_end - 0.30`, `-0.12`, and `-0.04` seconds,
- current source at `0.04`, `0.12`, and `0.30` seconds.

For `shot_06 -> shot_07`, label the check `tail_direction_match`; for `shot_05 -> shot_07`, label it `main_axis_and_pose_return`.

- [ ] **Step 5: Add strict mouth timing records**

Use:

```python
{
    "audio_onset_seconds": float,
    "mouth_onset_seconds": float,
    "audio_offset_seconds": float,
    "mouth_offset_seconds": float,
    "onset_error_seconds": float,
    "offset_error_seconds": float,
    "max_onset_error_seconds": 0.25,
    "max_offset_error_seconds": 0.25,
    "no_silent_mouth_flapping": bool,
    "no_closed_mouth_during_speech": bool,
    "reviewed": bool,
    "passed": bool,
}
```

Bind each record to selected MP4 hash and drive-audio hash. Reject missing, stale, non-finite, negative, or over-threshold measurements.

- [ ] **Step 6: Replace old shot-specific gate lists**

Speaking shots are `shot_03`, `shot_04`, `shot_05`, `shot_08`, `shot_09`, and `shot_10`. Prop gates are:

```python
{
    "bag": ("shot_01", "shot_02", "shot_06"),
    "orange_tail": ("shot_06", "shot_07"),
    "crumbs": ("shot_07", "shot_08", "shot_09", "shot_10"),
    "mirror": ("shot_09", "shot_10"),
}
```

Add manual gates for `action_preparation_execution_settle`, `screen_position_and_eyeline`, `music_transition_motivation`, and `physical_transition_logic`.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_review.py
.venv/bin/ruff check factory/pet_sitcom_review.py tests/test_pet_sitcom_review.py
git add factory/pet_sitcom_review.py tests/test_pet_sitcom_review.py
git commit -m "feat: gate pet release on sync and continuity review"
```

Expected: stale or late mouth movement, frozen shots, and missing dependency checks block composition.

---

### Task 7: Build Three-Act Sound Design And Variable-Duration Composition

**Files:**
- Create: `factory/pet_sitcom_sound.py`
- Create: `tests/test_pet_sitcom_sound.py`
- Modify: `factory/pet_sitcom_compose.py`
- Modify: `tests/test_pet_sitcom_compose.py`

**Interfaces:**
- Produces `prepare_pet_sound_design(plan, *, music_source, command_runner=subprocess.run) -> Path`.
- Produces a hash-bound `sound_design.json`.
- `build_pet_sitcom_ffmpeg_commands` consumes variable edit durations, final dialogue assets, one approved music bed, room tone, and foley stems.

- [ ] **Step 1: Write failing sound and composition tests**

```python
def test_sound_design_requires_non_looped_music_at_least_54_seconds(
    plan, short_music
):
    with pytest.raises(PetSoundError, match="at least 54"):
        prepare_pet_sound_design(plan, music_source=short_music)


def test_sound_manifest_has_three_story_cues_and_four_foley_events(
    plan, approved_music
):
    manifest = json.loads(
        prepare_pet_sound_design(plan, music_source=approved_music).read_text()
    )

    assert [(cue["start"], cue["end"], cue["name"]) for cue in manifest["music_cues"]] == [
        (0.0, 26.5, "light_interrogation"),
        (26.5, 37.4, "surveillance_investigation"),
        (37.4, 54.0, "comic_reveal"),
    ]
    assert [event["name"] for event in manifest["foley"]] == [
        "bag_rustle", "tail_floor_rustle", "light_paw_steps", "mirror_slide"
    ]


def test_composition_uses_variable_trim_durations_and_no_atempo(
    plan, selected_sources, passing_reviews, sound_manifest
):
    commands = build_pet_sitcom_ffmpeg_commands(plan)
    serialized = "\n".join(" ".join(command) for command in commands)

    assert "trim=duration=5.2" in serialized
    assert "trim=duration=3.4" in serialized
    assert "concat=n=10" in serialized
    assert "atempo" not in serialized
    assert "minterpolate" not in serialized
    assert "-t 54" in serialized
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_sound.py tests/test_pet_sitcom_compose.py -k "sound or variable or atempo"
```

Expected: sound module is missing and composition is fixed to 70 seconds.

- [ ] **Step 3: Validate and bind the approved music source**

Require one local, non-symlink audio file with:

- exactly one audio stream,
- duration at least 54.0 seconds,
- sample rate at least 44.1 kHz,
- no path escape,
- a human/agent approval record containing `reviewed=true`, `approved=true`, `not_harsh=true`, `not_repetitive=true`, and `dialogue_compatible=true`.

Persist source path, SHA-256, duration, and approval hash. Never loop the source.

- [ ] **Step 4: Prepare the three cue regions**

Create one 54-second PCM stereo music bed:

- `0.0-26.5`: original source, gentle high shelf reduction, target about `-31 LUFS`.
- `26.5-37.4`: low-pass at 4.5 kHz and another 3 dB reduction.
- `37.4-54.0`: restore the open timbre at about `-30 LUFS`.
- fade music down from `48.80-49.90` for the mirror question.
- fade music down from `50.00-53.60` for the final answer.
- add one short ending button from `53.60-54.00`.

Use short audio fades only; do not alter video transitions.

- [ ] **Step 5: Create restrained foley and room tone**

Create deterministic 48 kHz stereo stems from filtered noise and short tonal transients:

```python
FOLEY_EVENTS = (
    FoleyEvent("bag_rustle", 5.25, 0.30, -30.0),
    FoleyEvent("tail_floor_rustle", 29.70, 0.45, -32.0),
    FoleyEvent("light_paw_steps", 30.30, 1.20, -34.0),
    FoleyEvent("mirror_slide", 44.80, 1.60, -29.0),
)
```

Keep continuous kitchen room tone at approximately `-42 LUFS`. Do not add whooshes, impact booms, or transition stingers.

- [ ] **Step 6: Rewrite variable-duration video and dialogue filters**

For each shot:

```python
trim=duration=<shot.duration_seconds>,setpts=PTS-STARTPTS,
scale=1080:1920:force_original_aspect_ratio=decrease,
pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,
fps=30,format=yuv420p
```

Do not `tpad` a short provider clip. Reject the source and regenerate it if its available video is shorter than the edit duration.

Place trimmed TTS at its absolute start without changing tempo. Discard all generated source audio from the final mix.

- [ ] **Step 7: Implement J/L bridges and dialogue ducking**

- Start `shot_06` owner audio at absolute `26.30`, 0.20 seconds before its visual starts at `26.50`.
- Continue replay footstep and tail rustle to `32.90`, 0.30 seconds into `shot_07`.
- Duck music by 8 dB from 0.10 seconds before through 0.20 seconds after each spoken line.
- Keep dialogue, foley, room tone, and music as separate FFmpeg inputs before final `amix`.

- [ ] **Step 8: Update output validation**

Require:

- 1080×1920,
- H.264 High profile,
- 30 fps,
- AAC stereo at 48 kHz and at least 160 kbps,
- duration 53.85 to 54.15 seconds,
- integrated loudness `-16.0 ± 0.7 LUFS`,
- true peak no higher than `-1.5 dBTP`,
- no audio clipping and no dialogue overlap.

- [ ] **Step 9: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_pet_sitcom_sound.py tests/test_pet_sitcom_compose.py
.venv/bin/ruff check factory/pet_sitcom_sound.py factory/pet_sitcom_compose.py tests/test_pet_sitcom_sound.py tests/test_pet_sitcom_compose.py
git add factory/pet_sitcom_sound.py factory/pet_sitcom_compose.py tests/test_pet_sitcom_sound.py tests/test_pet_sitcom_compose.py
git commit -m "feat: compose pet sitcom with story-led sound"
```

Expected: final commands contain ten variable clips, no dialogue retiming, and a three-act non-looped sound bed.

---

### Task 8: Expose The Audio-First Workflow Through The CLI

**Files:**
- Modify: `factory_cli.py`
- Modify: `tests/test_cli_pet_sitcom.py`

**Interfaces:**
- Adds stages `audio`, `audio-probe`, and `status`.
- Keeps `plan`, `anchors`, `shots`, `review`, and `compose`.
- Adds `--music-source`.
- Every stage reports the exact current gate and next safe command.

- [ ] **Step 1: Write failing CLI routing tests**

```python
def test_pet_sitcom_audio_stage_generates_tts_before_video(cli, fake_tts):
    result = cli(
        "pet-sitcom", "--stage", "audio", "--enable-live"
    )
    assert result.exit_code == 0
    assert result.json["stage"] == "audio"
    assert result.json["completed_count"] == 8
    assert result.json["artifacts"]["audio_manifest"].endswith("audio_manifest.json")


def test_audio_probe_dry_run_makes_no_provider_call(cli, fake_providers):
    result = cli("pet-sitcom", "--stage", "audio-probe")
    assert result.exit_code == 0
    assert result.json["executed"] is False
    assert fake_providers.video_calls == []


def test_shots_report_probe_gate_before_profile_submission(cli, fake_providers):
    result = cli(
        "pet-sitcom", "--stage", "shots", "--enable-live", "--shot", "shot_03"
    )
    assert result.exit_code == 1
    assert "approved audio-drive probe" in result.json["blocked_reasons"][0]
    assert fake_providers.video_calls == []
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_pet_sitcom.py -k "audio_stage or audio_probe or probe_gate"
```

Expected: parser rejects the new stages.

- [ ] **Step 3: Add exact stage order and stage targets**

Use:

```python
PET_STAGE_ORDER = (
    "plan",
    "anchors",
    "audio",
    "audio-probe",
    "shots",
    "review",
    "compose",
    "status",
)
```

The parser's shot choices become `shot_01` through `shot_10`. `audio` requires ready Doubao TTS only. `audio-probe` requires approved anchors and a current audio manifest. `shots` requires the approved probe.

- [ ] **Step 4: Add status reporting**

Report:

```python
{
    "plan_ready": bool,
    "anchors_approved": bool,
    "audio_ready": bool,
    "audio_probe_approved": bool,
    "selected_shot_count": int,
    "shot_review_passed_count": int,
    "sound_design_approved": bool,
    "composition_ready": bool,
    "next_stage": str,
}
```

Do not contact providers from `status`.

- [ ] **Step 5: Require an approved music source at compose time**

Add:

```text
--music-source /absolute/path/to/approved/music.m4a
```

If omitted and no current hash-bound sound manifest exists, fail before FFmpeg starts. If a current sound manifest exists, reuse its exact source.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_cli_pet_sitcom.py
.venv/bin/ruff check factory_cli.py tests/test_cli_pet_sitcom.py
git add factory_cli.py tests/test_cli_pet_sitcom.py
git commit -m "feat: orchestrate audio-first pet sitcom workflow"
```

Expected: CLI enforces plan → anchors → audio → probe → shots → review → compose.

---

### Task 9: Update Documentation And Run The Full Offline Regression Suite

**Files:**
- Modify: `README.md`
- Modify: `docs/iteration-log.md`
- Modify: `docs/quality-iteration-handbook.md`
- Test: all `tests/test_pet_sitcom*.py`, gateway tests, and CLI tests.

**Interfaces:**
- Documents the new output directory, commands, gates, model choices, and known capability fallback.
- Records why the old edit failed and how the new pipeline prevents recurrence.

- [ ] **Step 1: Update the operator commands**

Document:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage plan --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
.venv/bin/python factory_cli.py pet-sitcom --stage anchors --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage audio --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage audio-probe --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage shots --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --enable-live
.venv/bin/python factory_cli.py pet-sitcom --stage review --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
.venv/bin/python factory_cli.py pet-sitcom --stage compose --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2" --music-source "/absolute/path/to/approved/music.m4a"
```

- [ ] **Step 2: Record the root causes and protections**

Add these exact before/after points:

- fourteen independent fixed five-second clips → ten variable shots with state and dependency contracts,
- video-first TTS overlay → immutable TTS-driven speaking shots,
- global `atempo` fitting → no dialogue retiming,
- fixed pluck/shaker loop → approved non-looped source with three narrative cue regions,
- numeric previous-shot continuity → explicit main-axis and replay dependency graph,
- subjective “looks okay” only → hash-bound mouth onset/offset and action-continuity review.

- [ ] **Step 3: Run focused tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_gateway_video.py \
  tests/test_gateway_video_batch.py \
  tests/test_pet_sitcom.py \
  tests/test_pet_sitcom_audio_first.py \
  tests/test_pet_sitcom_audio_probe.py \
  tests/test_pet_sitcom_generation.py \
  tests/test_pet_sitcom_sound.py \
  tests/test_pet_sitcom_compose.py \
  tests/test_pet_sitcom_review.py \
  tests/test_cli_pet_sitcom.py
```

Expected: all focused tests pass.

- [ ] **Step 4: Run repository regression and static checks**

Run:

```bash
.venv/bin/python -m py_compile factory/*.py factory_cli.py
.venv/bin/ruff check factory tests factory_cli.py
.venv/bin/pytest -q
```

Expected: zero compile failures, zero Ruff failures, and the full suite passes. If an unrelated pre-existing dirty-worktree test fails, record its exact test ID and error without reverting user changes.

- [ ] **Step 5: Commit documentation**

Run:

```bash
git add README.md docs/iteration-log.md docs/quality-iteration-handbook.md
git commit -m "docs: record audio-first pet sitcom workflow"
```

Expected: documentation matches the implemented CLI.

---

### Task 10: Run The Live Probe, Generate The New Cut, And Iterate By Evidence

**Files:**
- Runtime output: `~/Desktop/宠物短剧样片/冻干案_20260726_v2/`
- Runtime review: `~/Desktop/宠物短剧样片/冻干案_20260726_v2/review.md`
- Runtime final: `~/Desktop/宠物短剧样片/冻干案_20260726_v2/final/冻干到底是谁偷吃的_发布版.mp4`

**Interfaces:**
- Consumes the implemented CLI and current gateway/TTS configuration.
- Produces the user-viewable final MP4, clean MP4, source evidence, final evidence, and iteration notes.

- [ ] **Step 1: Build the plan, reuse or approve anchors, and generate all TTS**

Run the first three documented commands. Verify:

```bash
.venv/bin/python factory_cli.py pet-sitcom --stage status --output-dir "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2"
```

Expected: `plan_ready`, `anchors_approved`, and `audio_ready` are true; `audio_probe_approved` is false.

- [ ] **Step 2: Submit exactly one live audio-drive probe**

Run the documented `audio-probe --enable-live` command once. If the provider rejects the audio schema, preserve the sanitized HTTP detail and stop. If it succeeds, inspect the MP4 and extracted frames at normal speed and frame-by-frame.

- [ ] **Step 3: Complete the probe review from observed evidence**

Set the eight review booleans from actual observation and record measured onset/offset values. Approve only when every gate passes and both timing errors are no greater than 0.25 seconds. Re-run the stage without `--enable-live` to bind approval; do not create a second paid probe for the same hashes.

- [ ] **Step 4: Generate candidates sequentially**

Generate `shot_01` through `shot_10`. After each selected candidate:

- inspect the nine-frame shot sheet,
- inspect speaking shots frame-by-frame with audio,
- inspect every dependency comparison,
- record all gate results,
- generate one targeted retry only when a named gate fails.

Do not proceed to a dependent shot until its required source selections pass.

- [ ] **Step 5: Select and approve a real music source**

Audition the available local OpenMontage music candidates and select one that is at least 54 seconds, soft, non-repetitive, and does not mask dialogue. Persist its approval and hash before composition. If no local candidate passes, install or obtain a properly licensed music source before continuing; do not reinstate the old generated pluck/shaker loop.

- [ ] **Step 6: Compose and run final technical evidence**

Run the documented review and compose commands. Verify:

```bash
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate,channels -show_entries format=duration -of json "$HOME/Desktop/宠物短剧样片/冻干案_20260726_v2/final/冻干到底是谁偷吃的_发布版.mp4"
```

Expected: H.264/AAC, 1080×1920, 30 fps, 48 kHz stereo, and duration 53.85 to 54.15 seconds.

- [ ] **Step 7: Perform the final video-edit self-check**

Follow `video-edit-self-check`:

- view the entire output with audio,
- inspect every cut at half speed,
- inspect all six speaking shots frame-by-frame,
- verify the J-cut into surveillance and L-cut back to reality,
- verify no teleport, role swap, frozen hold, abrupt music restart, missing mouth motion, or speech outside mouth motion,
- run black, freeze, loudness, and codec checks,
- write observed defects and exact timestamps to `review.md`.

- [ ] **Step 8: Iterate only the failed source and its dependents**

When a defect is found, map it to one of:

```text
identity
paw_anatomy
mouth_anatomy
wrong_speaker
lip_timing
continuity
prop_state
motion_cadence
music_balance
```

Regenerate the smallest failing shot set, invalidate transitive dependents automatically, rebuild the mix, and repeat Step 7. Stop only when all hard gates pass.

- [ ] **Step 9: Record final results**

Append to `docs/iteration-log.md`:

- probe capability outcome,
- selected model and voice IDs,
- selected music source hash,
- number of candidates per shot,
- exact failed gates and corrections,
- final codec, duration, fps, loudness, true peak, freeze, and black-frame results,
- final clean and release paths.

Commit code/test documentation changes only; do not add generated MP4, WAV, provider reports containing remote URLs, or secret-bearing runtime files to Git.

---

## Plan Self-Review

- Spec coverage: ten-shot story, 54-second timing, spatial axis, replay dependency, audio-first TTS, gateway probe, speaking-shot audio drive, three-act music, physical foley, hard cuts, J/L bridges, automated QC, manual mouth timing, versioned outputs, and final acceptance all map to Tasks 1 through 10.
- Placeholder scan: the plan contains no `TBD`, `TODO`, or deferred implementation placeholders. Live review values are intentionally derived from observed output and are guarded against blind approval.
- Type consistency: `PetShot.duration_seconds` is the edit duration; `generation_duration_seconds` is the integer provider duration. `PetSpeechAsset` owns final TTS timing and hash. `build_pet_drive_audio` produces the exact path accepted by `GatewayVideoClient(audio=...)`. Generation provenance and review both bind the same drive-audio hash.
- Cost control: only one probe is submitted per immutable input signature; ambiguous or unsupported provider outcomes stop production; dependent shots cannot run before selected sources pass.
