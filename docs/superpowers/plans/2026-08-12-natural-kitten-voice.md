# Natural Kitten Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and validate two distinct, natural young-cat Seed-TTS 2.0 performances, then safely bind the approved direction to pet-video dialogue generation.

**Architecture:** Add a focused voice-direction module that owns immutable role profiles and conservative feline dialogue adaptation. Extend the existing Doubao client with a per-call context override, then generate an isolated A/B probe with machine-readable QC before changing production audio bindings.

**Tech Stack:** Python 3.12, dataclasses, Doubao Seed-TTS 2.0 v3 API, FFmpeg/FFprobe, pytest.

## Global Constraints

- Doubao and Naitang keep one immutable Seed-TTS 2.0 voice each throughout an episode.
- Preserve story meaning and evidence words exactly.
- Use feline reactions no more than once per three to five spoken lines.
- Never append `喵` mechanically to every sentence.
- Do not use pitch shifting or time stretching.
- Physical actions remain in video prompts and must never be read by TTS.
- Production audio remains unchanged until the isolated A/B probe passes technical QC and records an approved candidate.
- API keys remain only in ignored, mode-0600 local configuration.

---

### Task 1: Immutable Kitten Performance Profiles

**Files:**
- Create: `factory/pet_voice_direction.py`
- Create: `tests/test_pet_voice_direction.py`

**Interfaces:**
- Produces: `KittenVoiceProfile(voice_id: str, speech_rate: int, context_text: str)`.
- Produces: `KITTEN_VOICE_PROFILES: Mapping[str, KittenVoiceProfile]` for `doubao` and `naitang`.
- Produces: `adapt_kitten_line(speaker: str, text: str, *, variant: str, line_index: int) -> str` where `variant` is `acting` or `feline`.

- [ ] **Step 1: Write failing profile and dialogue-boundary tests**

```python
def test_profiles_are_distinct_and_use_verified_voices():
    assert KITTEN_VOICE_PROFILES["doubao"].voice_id != KITTEN_VOICE_PROFILES["naitang"].voice_id
    assert "柔和" in KITTEN_VOICE_PROFILES["doubao"].context_text
    assert "调皮" in KITTEN_VOICE_PROFILES["naitang"].context_text

def test_feline_variant_is_restrained_and_preserves_evidence_words():
    lines = [adapt_kitten_line("doubao", "鸡肉味碎屑是证据。", variant="feline", line_index=i) for i in range(5)]
    assert all("鸡肉味" in line and "碎屑" in line and "证据" in line for line in lines)
    assert sum("喵" in line or "咪呜" in line for line in lines) <= 1

def test_acting_variant_does_not_add_feline_words():
    assert adapt_kitten_line("naitang", "我才没有偷吃。", variant="acting", line_index=0) == "我才没有偷吃。"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `.venv/bin/pytest -q tests/test_pet_voice_direction.py`

Expected: FAIL because `factory.pet_voice_direction` does not exist.

- [ ] **Step 3: Implement immutable profiles and deterministic adaptation**

Use the verified voices `saturn_zh_female_keainvsheng_tob` for Doubao and `saturn_zh_female_tiaopigongzhu_tob` for Naitang. The adapter may add only a leading `嗯`/`诶` or one quiet `咪呜` on deterministic eligible indices; it must reject unknown speakers/variants and must not alter protected words.

- [ ] **Step 4: Run tests and lint**

Run: `.venv/bin/pytest -q tests/test_pet_voice_direction.py && .venv/bin/ruff check factory/pet_voice_direction.py tests/test_pet_voice_direction.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add factory/pet_voice_direction.py tests/test_pet_voice_direction.py
git commit -m "feat: define natural kitten voice profiles"
```

### Task 2: Per-Line Doubao Acting Context

**Files:**
- Modify: `factory/doubao_tts.py`
- Modify: `tests/test_doubao_tts.py`

**Interfaces:**
- Extends: `DoubaoTTSClient.synthesize(..., context_text: str | None = None) -> DoubaoTTSResult`.
- Extends: `DoubaoTTSClient.submit(..., context_text: str | None = None) -> DoubaoTTSTask`.
- The request uses the per-call value when supplied and otherwise preserves `DoubaoTTSConfig.context_text`.

- [ ] **Step 1: Write failing request-body tests**

```python
def test_synthesize_uses_per_call_context_override(tmp_path):
    client = DoubaoTTSClient(config, session=session, sleep=lambda _: None)
    client.synthesize("别急", tmp_path / "clip.mp3", context_text="黑白幼猫，柔和好奇。")
    additions = json.loads(session.posts[0]["json"]["req_params"]["additions"])
    assert additions["context_texts"] == ["黑白幼猫，柔和好奇。"]

def test_synthesize_falls_back_to_config_context(tmp_path):
    client.synthesize("别急", tmp_path / "clip.mp3")
    additions = json.loads(session.posts[0]["json"]["req_params"]["additions"])
    assert additions["context_texts"] == [config.context_text]
```

- [ ] **Step 2: Run tests and verify the new keyword is rejected**

Run: `.venv/bin/pytest -q tests/test_doubao_tts.py -k context`

Expected: FAIL with an unexpected `context_text` keyword or missing override.

- [ ] **Step 3: Thread the optional context through both authentication paths**

Pass the resolved context through `synthesize`, `submit`, `_synthesize_streaming`, and `_submit_body`. Do not mutate the frozen client configuration and do not include secrets in metadata.

- [ ] **Step 4: Run the full Doubao client suite**

Run: `.venv/bin/pytest -q tests/test_doubao_tts.py tests/test_provider_profile.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add factory/doubao_tts.py tests/test_doubao_tts.py
git commit -m "feat: support per-line Doubao voice direction"
```

### Task 3: Isolated Kitten Voice A/B Probe

**Files:**
- Create: `factory/kitten_voice_probe.py`
- Create: `tests/test_kitten_voice_probe.py`
- Modify: `factory_cli.py`

**Interfaces:**
- Produces: `generate_kitten_voice_probe(output_dir: Path, tts_client: DoubaoTTSClient, *, allow_network: bool) -> Path`.
- Produces: `kitten_voice_probe.json`, four role/variant MP3 files, and `kitten_voice_ab.m4a` at 48 kHz stereo.
- Adds CLI command: `kitten-voice-probe --output-dir PATH [--enable-live]`.

- [ ] **Step 1: Write failing dry-run and manifest tests**

```python
def test_probe_dry_run_makes_no_provider_calls(tmp_path):
    report = generate_kitten_voice_probe(tmp_path, FakeTTS(), allow_network=False)
    payload = json.loads(report.read_text())
    assert payload["executed"] is False
    assert FakeTTS.calls == []

def test_probe_binds_two_roles_and_two_variants(tmp_path):
    report = generate_kitten_voice_probe(tmp_path, FakeTTS(), allow_network=True)
    payload = json.loads(report.read_text())
    assert {(x["speaker"], x["variant"]) for x in payload["assets"]} == {
        ("doubao", "acting"), ("doubao", "feline"),
        ("naitang", "acting"), ("naitang", "feline"),
    }
```

- [ ] **Step 2: Run tests and verify the module/CLI is absent**

Run: `.venv/bin/pytest -q tests/test_kitten_voice_probe.py`

Expected: FAIL because the probe module does not exist.

- [ ] **Step 3: Implement resumable generation and composition**

Hash speaker, variant, exact text, voice ID, rate, context, and API resource ID. Reuse an asset only when its state hash matches and FFprobe confirms valid audio. Compose A then B with 450 ms inter-line gaps, normalize to `-16 LUFS`, and force AAC stereo at 48 kHz.

- [ ] **Step 4: Add deterministic technical QC fields**

Record duration, sample rate, channels, integrated loudness, peak, leading/trailing silence, role voice distinctness, feline-reaction count, source SHA-256, and whether dialogue overlaps. Fail the report for invalid media, reused role voice, reaction overuse, overlap, or non-48-kHz combined output.

- [ ] **Step 5: Run focused tests and lint**

Run: `.venv/bin/pytest -q tests/test_kitten_voice_probe.py tests/test_doubao_tts.py && .venv/bin/ruff check factory/kitten_voice_probe.py factory_cli.py tests/test_kitten_voice_probe.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add factory/kitten_voice_probe.py factory_cli.py tests/test_kitten_voice_probe.py
git commit -m "feat: add natural kitten voice A/B probe"
```

### Task 4: Live Probe, Approval, and Production Binding

**Files:**
- Modify: `factory/pet_sitcom_audio_first.py`
- Modify: `tests/test_pet_sitcom_audio_first.py`
- Create: `output/kitten_voice_probe_20260812/kitten_voice_review.json`

**Interfaces:**
- Consumes: approved `kitten_voice_probe.json` and immutable `KITTEN_VOICE_PROFILES`.
- Production calls `tts_client.synthesize(..., voice_id=profile.voice_id, speech_rate=profile.speech_rate, context_text=profile.context_text)`.
- Production uses `adapt_kitten_line(..., variant="feline")` only when the review binds the current probe hashes and marks candidate B approved.

- [ ] **Step 1: Generate the live A/B probe**

Run: `.venv/bin/python factory_cli.py kitten-voice-probe --output-dir output/kitten_voice_probe_20260812 --enable-live`

Expected: four valid MP3 files, one 48-kHz stereo comparison M4A, and a technically passing JSON report with no secret values.

- [ ] **Step 2: Review the audio against the design rubric**

Record `approved_variant`, `natural_kitten_quality`, `role_distinctness`, `forced_meow`, `announcer_tone`, `electronic_pitch_effect`, and notes. Candidate B may be approved only when the first three are positive and the last three are false.

- [ ] **Step 3: Write failing production-binding tests**

```python
def test_pet_audio_uses_profile_voice_context_and_feline_line(tmp_path):
    generate_pet_speech_assets(plan, tts_client=fake, allow_network=True)
    doubao_call = next(call for call in fake.calls if call["speaker"] == "doubao")
    assert doubao_call["voice_id"] == KITTEN_VOICE_PROFILES["doubao"].voice_id
    assert doubao_call["context_text"] == KITTEN_VOICE_PROFILES["doubao"].context_text
    assert doubao_call["text"] == adapt_kitten_line("doubao", original, variant="feline", line_index=expected_index)
```

- [ ] **Step 4: Bind approved profiles without changing timing rules**

Replace the duplicate `PET_VOICES` constants with the shared profiles for cat speakers. Preserve owner voice behavior, silence trimming, dialogue-tail checks, immutable asset hashes, and the prohibition on time stretching.

- [ ] **Step 5: Run regression and real-media verification**

Run: `.venv/bin/pytest -q tests/test_pet_voice_direction.py tests/test_doubao_tts.py tests/test_kitten_voice_probe.py tests/test_pet_sitcom_audio_first.py tests/test_pet_sitcom_compose.py`

Expected: PASS. Then run FFprobe and loudness/silence checks on every probe asset and confirm `.env` remains ignored with mode `0600`.

- [ ] **Step 6: Commit**

```bash
git add factory/pet_sitcom_audio_first.py tests/test_pet_sitcom_audio_first.py output/kitten_voice_probe_20260812/kitten_voice_review.json
git commit -m "feat: bind approved kitten voices to pet dialogue"
```
