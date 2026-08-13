# Doubao TTS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the configured OpenMontage Doubao Speech 2.0 settings to generate per-cue voiceover audio and timing metadata, with the existing macOS voice path as an automatic fallback.

**Architecture:** A focused `factory.doubao_tts` module owns dotenv resolution and the Volcengine async API contract. `factory.local_voiceover` remains responsible for cue planning, FFmpeg mixing, and preview muxing, but delegates clip rendering to Doubao when available. The CLI records the selected provider and a sanitized provider report without ever copying a secret into tracked files.

**Tech Stack:** Python 3.10+, `requests`, dataclasses, pytest, FFmpeg, Volcengine Doubao Speech 2.0 async API.

## Global Constraints

- Resolve settings from process variables, then the factory `.env`, then `<openMontage>/.env`.
- Use `DOUBAO_SPEECH_API_KEY`, `DOUBAO_SPEECH_VOICE_TYPE`, and resource `seed-tts-2.0`.
- Never log, serialize, or commit secret values.
- Preserve the current local `say` and FFmpeg path as a fallback.
- Make no paid live request during unit-test execution.

---

### Task 1: Doubao configuration and async client

**Files:**
- Create: `factory/doubao_tts.py`
- Create: `tests/test_doubao_tts.py`

**Interfaces:**
- Produces: `resolve_doubao_tts_config(config: dict, process_env: Mapping[str, str] | None = None) -> DoubaoTTSConfig | None`
- Produces: `DoubaoTTSClient.synthesize(text: str, output_path: Path, *, voice_id: str | None = None, metadata_path: Path | None = None, speech_rate: int = 0, sample_rate: int = 24000) -> DoubaoTTSResult`

- [ ] **Step 1: Write failing configuration tests**

```python
def test_resolve_doubao_config_prefers_process_env(tmp_path):
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    (openmontage / ".env").write_text(
        "DOUBAO_SPEECH_API_KEY=file-key\nDOUBAO_SPEECH_VOICE_TYPE=file-voice\n",
        encoding="utf-8",
    )
    config = {"workspace": str(tmp_path), "sources": {"openMontage": str(openmontage)}}
    resolved = resolve_doubao_tts_config(
        config,
        process_env={
            "DOUBAO_SPEECH_API_KEY": "process-key",
            "DOUBAO_SPEECH_VOICE_TYPE": "process-voice",
        },
    )
    assert resolved is not None
    assert resolved.api_key == "process-key"
    assert resolved.voice_type == "process-voice"
    assert resolved.source == "process"
```

- [ ] **Step 2: Run the configuration tests and confirm the import fails**

Run: `pytest -q tests/test_doubao_tts.py`

Expected: FAIL because `factory.doubao_tts` does not exist.

- [ ] **Step 3: Implement dotenv resolution and immutable config/result types**

```python
@dataclass(frozen=True)
class DoubaoTTSConfig:
    api_key: str
    voice_type: str
    source: str
    resource_id: str = "seed-tts-2.0"
    submit_url: str = "https://openspeech.bytedance.com/api/v3/tts/submit"
    query_url: str = "https://openspeech.bytedance.com/api/v3/tts/query"

@dataclass(frozen=True)
class DoubaoTTSResult:
    output_path: Path
    metadata_path: Path
    task_id: str
    sentences: list[dict[str, Any]]
```

Resolve each setting independently with process values taking precedence over
`<workspace>/.env`, followed by `<openMontage>/.env`. Return `None` unless both
the key and voice are present. Record only the source label, not the secret.

- [ ] **Step 4: Write failing async request tests with a fake HTTP session**

```python
def test_synthesize_submits_polls_downloads_and_writes_metadata(tmp_path):
    session = FakeSession(
        post_payloads=[
            {"code": 20000000, "data": {"task_id": "task-1"}},
            {
                "code": 20000000,
                "data": {
                    "task_status": 2,
                    "audio_url": "https://audio.test/clip.mp3",
                    "sentences": [{"text": "别急", "words": []}],
                },
            },
        ],
        audio=b"ID3fake",
    )
    client = DoubaoTTSClient(config, session=session, sleep=lambda _: None)
    result = client.synthesize("别急", tmp_path / "clip.mp3")
    assert result.output_path.read_bytes() == b"ID3fake"
    assert result.task_id == "task-1"
    assert result.sentences[0]["text"] == "别急"
    assert session.posts[0]["headers"]["X-Api-Key"] == "secret"
```

- [ ] **Step 5: Implement submit, polling, download, metadata, validation, and redaction**

The client must send:

```python
headers = {
    "X-Api-Key": config.api_key,
    "X-Api-Resource-Id": config.resource_id,
    "X-Api-Request-Id": request_id,
    "X-Control-Require-Usage-Tokens-Return": "true",
    "Content-Type": "application/json",
}
```

The request body must use `req_params.text`, `req_params.speaker`, MP3 at
24 kHz, timestamps enabled, and `disable_markdown_filter=false`. Poll every two
seconds until `task_status == 2`, fail on `task_status == 3`, and enforce a
300-second deadline. Replace the configured key with `[redacted]` in every
raised error string.

- [ ] **Step 6: Run focused tests**

Run: `pytest -q tests/test_doubao_tts.py`

Expected: PASS.

- [ ] **Step 7: Commit the client**

```bash
git add factory/doubao_tts.py tests/test_doubao_tts.py
git commit -m "feat: add Doubao Speech TTS client"
```

### Task 2: Provider-aware voiceover rendering

**Files:**
- Modify: `factory/local_voiceover.py`
- Modify: `tests/test_local_voiceover.py`

**Interfaces:**
- Consumes: `DoubaoTTSClient`, `DoubaoTTSConfig`, and `resolve_doubao_tts_config`
- Produces: `render_voiceover_preview(..., config: dict, process_env: Mapping[str, str] | None = None, doubao_client: DoubaoTTSClient | None = None) -> dict[str, Path | int | str]`
- Preserves: `render_local_voiceover_preview(...)` for deterministic local fallback and existing callers.

- [ ] **Step 1: Write failing provider-selection and fallback tests**

```python
def test_render_voiceover_uses_doubao_when_configured(tmp_path, monkeypatch):
    fake = FakeDoubaoClient(tmp_path)
    result = render_voiceover_preview(
        episode,
        source_video_path=source_video,
        output_path=tmp_path / "voiced.mp4",
        work_dir=tmp_path / "voiceover",
        config=factory_config,
        process_env={
            "TTS_PROVIDER": "auto",
            "DOUBAO_SPEECH_API_KEY": "secret",
            "DOUBAO_SPEECH_VOICE_TYPE": "voice",
        },
        doubao_client=fake,
        command_runner=fake_command_runner,
    )
    assert result["voiceover_provider"] == "doubao"
    assert fake.texts == [cue.text for cue in build_voiceover_cues(episode)]
```

Also test that `TTS_PROVIDER=auto` falls back to local when configuration is
absent, and that a per-cue Doubao failure is recorded before the local renderer
creates that cue.

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `pytest -q tests/test_local_voiceover.py`

Expected: FAIL because `render_voiceover_preview` does not exist.

- [ ] **Step 3: Extract command execution and add provider selection**

Keep cue planning and FFmpeg command builders unchanged. Add injectable command
execution so tests do not invoke `say` or FFmpeg. In auto mode:

1. Resolve Doubao configuration.
2. Generate one `.mp3` plus one `.mp3.json` per cue using only `cue.text`.
3. On a cue failure, generate that cue as `.aiff` with the existing local voice.
4. Mix all cue files with their current start offsets.
5. Write `voiceover_provider_report.json` containing provider, configuration
   source, cloud/local clip counts, sanitized errors, and metadata paths.

- [ ] **Step 4: Run local voiceover tests**

Run: `pytest -q tests/test_local_voiceover.py tests/test_doubao_tts.py`

Expected: PASS.

- [ ] **Step 5: Commit provider-aware rendering**

```bash
git add factory/local_voiceover.py tests/test_local_voiceover.py
git commit -m "feat: render voiceover with Doubao fallback"
```

### Task 3: CLI, readiness, and configuration documentation

**Files:**
- Modify: `factory_cli.py`
- Modify: `factory/env_readiness.py`
- Modify: `tests/test_env_readiness.py`
- Modify: `.gitignore`
- Create: `.env.example`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-13-gateway-provider-design.md`

**Interfaces:**
- Consumes: `render_voiceover_preview`
- Produces: `status.json` fields `voiceover_provider` and `voiceover_provider_report`
- Produces: env readiness entries for `DOUBAO_SPEECH_API_KEY` and `DOUBAO_SPEECH_VOICE_TYPE`

- [ ] **Step 1: Write failing readiness tests**

```python
def test_env_readiness_detects_openmontage_doubao_config(tmp_path):
    openmontage = tmp_path / "OpenMontage"
    openmontage.mkdir()
    (openmontage / ".env").write_text(
        "DOUBAO_SPEECH_API_KEY=secret\nDOUBAO_SPEECH_VOICE_TYPE=voice\n",
        encoding="utf-8",
    )
    report = check_env_readiness(config, process_env={})
    assert report["optional"]["DOUBAO_SPEECH_API_KEY"]["present"] is True
    assert report["optional"]["DOUBAO_SPEECH_API_KEY"]["source"] == "openmontage.env"
```

- [ ] **Step 2: Run the readiness tests and confirm they fail**

Run: `pytest -q tests/test_env_readiness.py`

Expected: FAIL because OpenMontage dotenv values are not inspected.

- [ ] **Step 3: Add OpenMontage dotenv detection without exposing values**

Extend `_presence` to accept named dotenv sources and report only a source
label. Add both Doubao settings to optional readiness. Keep the legacy required
DashScope result unchanged in this TTS-only slice; provider-aware stage gates
remain part of the gateway implementation plan.

- [ ] **Step 4: Route the plan command through provider-aware rendering**

Replace the direct `render_local_voiceover_preview` call with
`render_voiceover_preview(..., config=config)`. Add the provider and report path
to CLI JSON and `status.json` while retaining all existing artifact fields.

- [ ] **Step 5: Add safe configuration examples and docs**

Add `.env` to `.gitignore`. Create `.env.example` containing only:

```dotenv
TTS_PROVIDER=auto
DOUBAO_SPEECH_API_KEY=
DOUBAO_SPEECH_VOICE_TYPE=
```

Document that the factory automatically reuses the configured OpenMontage
dotenv and that tracked files never contain the secret.

- [ ] **Step 6: Run focused and full tests**

Run: `pytest -q tests/test_doubao_tts.py tests/test_local_voiceover.py tests/test_env_readiness.py tests/test_cli_readiness.py`

Expected: PASS.

Run: `pytest -q`

Expected: full suite PASS.

- [ ] **Step 7: Run a no-cost sample plan and inspect provider reporting**

Run with Doubao disabled to avoid paid calls:

```bash
TTS_PROVIDER=local python factory_cli.py plan --project sample_episode --input samples/novel.txt
```

Expected: final preview is regenerated, `voiceover_provider=local`, and existing
readiness checks remain green.

- [ ] **Step 8: Commit integration and docs**

```bash
git add factory_cli.py factory/env_readiness.py tests/test_env_readiness.py .gitignore .env.example README.md docs/superpowers/specs/2026-07-13-gateway-provider-design.md
git commit -m "feat: reuse OpenMontage Doubao TTS settings"
```

### Task 4: Verification before a paid smoke

**Files:**
- No code changes expected.

**Interfaces:**
- Consumes: the OpenMontage dotenv through the configured `openMontage` source path.
- Produces: verification evidence only; it must not print credentials.

- [ ] **Step 1: Confirm configuration presence without printing values**

Run a presence-only check that reports `DOUBAO_SPEECH_API_KEY_PRESENT=yes` and
`DOUBAO_SPEECH_VOICE_TYPE_PRESENT=yes`.

- [ ] **Step 2: Confirm the repository is clean and tests pass**

Run: `git status --short --branch`

Expected: clean worktree on `main`.

- [ ] **Step 3: Stop before the paid smoke**

Report the exact 10-15 second sample command, expected output paths, and that no
cloud request has been made. Execute that command only after the user confirms
the small paid TTS call.
