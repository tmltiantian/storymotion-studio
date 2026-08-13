# Speaking A/B Scene Continuity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a resumable 10-second two-character A/B sample that compares Doubao-dubbed natural mouth motion against Seedance native-audio lip sync while sharing one scene anchor and preserving source motion.

**Architecture:** Add one focused `factory.speaking_ab` module that owns deterministic planning, guarded provider calls, resumable state, FFmpeg composition, and safe reporting. Expose it through one dry-run-first CLI command. Keep all experiment assets below `runs/sample_episode/speaking_ab_20260720/` and leave the approved 61-second render untouched.

**Tech Stack:** Python 3.12, existing GatewayImage/GatewayVideo/Doubao clients, FFmpeg/FFprobe, pytest, Seedream 4.5, Seedance 2.0, Seed TTS 2.0.

## Global Constraints

- Use `doubao-seedance-2-0`, 9:16, 1080p, and 5 seconds for each speaking source clip.
- Use the approved Lin Che and Su Mian reference images plus one shared person-free scene anchor.
- A uses `generate_audio=false` and the existing Doubao voices; B uses `generate_audio=true` and keeps native Seedance audio.
- Preserve the 24 fps source motion and convert directly to 30 fps; do not insert a 6/8/10 fps cadence stage.
- One initial candidate per role and route. Resume matching completed jobs instead of resubmitting.
- Store every new asset under `runs/sample_episode/speaking_ab_20260720/`.
- Do not overwrite `output/sample_episode/final_preview.mp4` or any approved micro-shot candidate.
- Never serialize API keys, access keys, signed URLs, image data URIs, or authorization headers.

---

### Task 1: Deterministic A/B Plan And Prompt Contracts

**Files:**
- Create: `factory/speaking_ab.py`
- Create: `tests/test_speaking_ab.py`

**Interfaces:**
- Produces: `SpeakingABError`, `SpeakingLine`, `SpeakingABPlan`.
- Produces: `build_speaking_ab_plan(config: dict[str, Any], project_id: str) -> SpeakingABPlan`.
- Produces: `write_speaking_ab_plan(plan: SpeakingABPlan) -> Path`.
- Later tasks consume `SpeakingABPlan.to_report()` and its exact local output paths.

- [ ] **Step 1: Write failing planning tests**

```python
def test_build_plan_uses_two_roles_one_scene_anchor_and_isolated_outputs(factory_config):
    plan = build_speaking_ab_plan(factory_config, "sample_episode")
    assert [line.character_name for line in plan.lines] == ["苏眠", "林澈"]
    assert [line.text for line in plan.lines] == [
        "末班车不是出城，是回到十年前。",
        "十年前？你想让我看什么？",
    ]
    assert plan.scene_anchor_path.name == "scene_anchor.png"
    assert plan.output_dir.name == "speaking_ab_20260720"
    assert all(plan.output_dir in path.parents for path in plan.all_output_paths())


def test_prompts_share_scene_contract_and_split_audio_modes(factory_config):
    plan = build_speaking_ab_plan(factory_config, "sample_episode")
    for line in plan.lines:
        assert "Reference 1" in line.natural_prompt
        assert "Reference 2" in line.natural_prompt
        assert line.text in line.natural_prompt
        assert line.text in line.native_prompt
        assert "locked camera" in line.natural_prompt
        assert "no camera movement" in line.native_prompt
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py`

Expected: collection fails because `factory.speaking_ab` does not exist.

- [ ] **Step 3: Implement immutable plan types and prompt builders**

```python
@dataclass(frozen=True)
class SpeakingLine:
    slug: str
    character_id: str
    character_name: str
    text: str
    voice_id: str
    character_reference: Path
    natural_prompt: str
    native_prompt: str
    natural_video: Path
    native_video: Path
    tts_audio: Path


@dataclass(frozen=True)
class SpeakingABPlan:
    project_id: str
    run_dir: Path
    output_dir: Path
    scene_anchor_path: Path
    scene_anchor_prompt: str
    lines: tuple[SpeakingLine, ...]
    natural_output: Path
    native_output: Path
    comparison_output: Path
    plan_path: Path
    report_path: Path
```

The prompt builder must repeat the same street-lamp scene description verbatim, identify the first reference as the current character and the second as the empty scene anchor, request a single front-facing speaker, quote the exact line, and forbid extra people, text, watermarks, camera motion, scene changes, duplicate anatomy, and facial drift.

- [ ] **Step 4: Validate exact project assets and safe paths**

Require `runs/<project>/character_assets.json` to be production-ready, resolve both character references under `runs/<project>/assets/characters/`, reject symlinks and missing/unsupported images, and reject every output that escapes the dedicated experiment directory.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py`

Expected: planning tests pass with no network calls.

- [ ] **Step 6: Commit Task 1**

```bash
git add factory/speaking_ab.py tests/test_speaking_ab.py
git commit -m "feat: plan speaking A/B samples"
```

### Task 2: Resumable Scene, TTS, And Video Generation

**Files:**
- Modify: `factory/speaking_ab.py`
- Modify: `tests/test_speaking_ab.py`

**Interfaces:**
- Consumes: `SpeakingABPlan`.
- Produces: `execute_speaking_ab_generation(plan, config, *, allow_network, image_client_factory, video_client_factory, tts_client_factory) -> dict[str, Any]`.
- Produces a safe `generation_report.json` plus per-asset signature state files.

- [ ] **Step 1: Write failing dry-run, audio-mode, and resume tests**

```python
def test_generation_dry_run_makes_no_provider_calls(plan, fake_factories):
    report = execute_speaking_ab_generation(
        plan, factory_config, allow_network=False, **fake_factories
    )
    assert report["planned_count"] == 7
    assert report["executed"] is False
    assert fake_factories.total_calls == 0


def test_generation_uses_shared_anchor_and_expected_audio_modes(plan, fake_factories):
    report = execute_speaking_ab_generation(
        plan, factory_config, allow_network=True, **fake_factories
    )
    assert [call.generate_audio for call in fake_factories.video.calls] == [
        False, False, True, True
    ]
    assert all(call.images[1] == str(plan.scene_anchor_path)
               for call in fake_factories.video.calls)
    assert report["completed_count"] == 7


def test_generation_resumes_matching_completed_assets_without_new_calls(
    plan, fake_factories
):
    first = execute_speaking_ab_generation(
        plan, factory_config, allow_network=True, **fake_factories
    )
    calls = fake_factories.total_calls
    second = execute_speaking_ab_generation(
        plan, factory_config, allow_network=True, **fake_factories
    )
    assert first["success"] is True
    assert second["reused_count"] == 7
    assert fake_factories.total_calls == calls
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py -k generation`

Expected: fails because `execute_speaking_ab_generation` is not defined.

- [ ] **Step 3: Implement signature-bound state**

Use SHA-256 over provider, model, prompt, exact reference-image hashes, duration, resolution, `generate_audio`, text, voice ID, speech rate, and candidate number. Write state atomically only after the output passes `is_valid_image_file`, `is_valid_mp4_file`, or `probe_media(..., required_stream="audio")`. Matching state plus valid output is reused; a submitted gateway video report resumes through `render_gateway_video_single` with the same report path. Video candidates live at `candidates/<route>/<role>/candidate_001.mp4` or `candidate_002.mp4`; candidate 1 is the default.

- [ ] **Step 4: Implement the seven guarded jobs**

Execute in this order:

1. Seedream `doubao-seedream-4-5`, `1440x2560`, no people, no text, no rain, no watermark.
2. Doubao TTS for Su Mian using `zh_female_vv_uranus_bigtts`, speech rate `-4`.
3. Doubao TTS for Lin Che using `zh_male_ruyayichen_saturn_bigtts`, speech rate `-4`.
4. A Su Mian Seedance clip, `generate_audio=False`.
5. A Lin Che Seedance clip, `generate_audio=False`.
6. B Su Mian Seedance clip, `generate_audio=True`.
7. B Lin Che Seedance clip, `generate_audio=True`.

Every video call uses references `[character_reference, scene_anchor_path]`, `duration=5`, `ratio="9:16"`, and `resolution="1080p"`.

- [ ] **Step 5: Redact reports and fail closed**

Reports may include model IDs, local paths, hashes, task IDs, counts, durations, and sanitized provider errors. Recursively remove or redact credentials, authorization values, HTTP query strings, signed URLs, and data URIs. A failed job stops composition but preserves completed state for the next run.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py`

Expected: all planning and generation tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add factory/speaking_ab.py tests/test_speaking_ab.py
git commit -m "feat: generate resumable speaking A/B assets"
```

### Task 3: Direct 24-to-30 FPS Composition And CLI

**Files:**
- Modify: `factory/speaking_ab.py`
- Modify: `factory_cli.py`
- Modify: `tests/test_speaking_ab.py`
- Create: `tests/test_cli_speaking_ab.py`

**Interfaces:**
- Produces: `compose_speaking_ab(plan, *, ffmpeg_bin="ffmpeg", command_runner=subprocess.run) -> dict[str, Any]`.
- Produces CLI command: `factory_cli.py speaking-ab --project sample_episode [--enable-live] [--only JOB] [--candidate 1|2]`.

- [ ] **Step 1: Write failing composition and CLI tests**

```python
def test_composition_preserves_source_motion_without_low_cadence_stage(plan):
    commands = build_speaking_ab_ffmpeg_commands(plan)
    rendered = " ".join(" ".join(command) for command in commands)
    assert "fps=30" in rendered
    assert "fps=6" not in rendered
    assert "fps=8" not in rendered
    assert "fps=10" not in rendered


def test_cli_is_dry_run_by_default(monkeypatch, tmp_path):
    result = run_cli(["speaking-ab", "--project", "sample_episode"])
    assert result.returncode == 0
    assert '"executed": false' in result.stdout.lower()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py tests/test_cli_speaking_ab.py`

Expected: fails because the compositor and CLI command do not exist.

- [ ] **Step 3: Implement A composition**

For each A source, trim video to five seconds, scale/pad to 1080x1920, convert directly to 30 fps, and mux its matching Doubao audio. Pad shorter audio with silence but do not stretch speech. Concatenate Su Mian then Lin Che and normalize the combined audio to approximately `-16 LUFS` with an AAC 48 kHz stereo output.

- [ ] **Step 4: Implement B and comparison composition**

Require one audio stream in each B source. Normalize and concatenate the two native-audio clips without replacing their sound. Build the comparison as A, a 0.5-second neutral separator, then B. Add only a short top-safe `A 豆包配音` or `B 原生口型` label; do not cover eyes, mouth, or bottom subtitle area.

- [ ] **Step 5: Add dry-run-first CLI**

Register:

```python
speaking_ab_parser = subparsers.add_parser(
    "speaking-ab",
    help="Plan or execute the isolated two-character speaking A/B sample",
)
speaking_ab_parser.add_argument("--project", default="sample_episode")
speaking_ab_parser.add_argument("--enable-live", action="store_true")
speaking_ab_parser.add_argument(
    "--only",
    choices=["scene", "tts_su", "tts_lin", "natural_su", "natural_lin", "native_su", "native_lin"],
    action="append",
    default=[],
)
speaking_ab_parser.add_argument("--candidate", choices=[1, 2], type=int, default=1)
speaking_ab_parser.set_defaults(func=speaking_ab_command)
```

The command writes the plan on every run, executes providers only with `--enable-live`, and defaults to all seven candidate-1 jobs. `--candidate 2` is valid only when every `--only` value names a video job. A successful targeted candidate becomes that role/route's selected source without deleting candidate 1. Composition runs only when all seven selected generation assets are valid. The command prints safe JSON paths and counts and exits nonzero when live generation or composition fails.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_speaking_ab.py tests/test_cli_speaking_ab.py tests/test_gateway_video.py tests/test_doubao_tts.py`

Expected: all tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add factory/speaking_ab.py factory_cli.py tests/test_speaking_ab.py tests/test_cli_speaking_ab.py
git commit -m "feat: compose and run speaking A/B samples"
```

### Task 4: Live Generation, Review, And Delivery

**Files:**
- Create: `runs/sample_episode/speaking_ab_20260720/review.md`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Consumes the `speaking-ab` CLI.
- Produces the three MP4 deliverables and `comparison_report.json`.

- [ ] **Step 1: Verify the zero-cost plan**

Run: `.venv/bin/python factory_cli.py speaking-ab --project sample_episode`

Expected: `executed=false`, seven planned jobs, all output paths inside the experiment directory, and no provider call.

- [ ] **Step 2: Execute the four video, two TTS, and one scene job**

Run: `.venv/bin/python factory_cli.py speaking-ab --project sample_episode --enable-live`

Expected: seven completed or safely reused jobs, three composed MP4 files, no secrets in reports.

- [ ] **Step 3: Run technical preflight**

Run:

```bash
python3 /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  runs/sample_episode/speaking_ab_20260720/A_自然开口_豆包配音.mp4
python3 /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  runs/sample_episode/speaking_ab_20260720/B_原生音频_精确口型.mp4
python3 /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py \
  runs/sample_episode/speaking_ab_20260720/AB_顺序对比.mp4
```

Expected: nonzero duration, 1080x1920, one audio stream, and no warnings for all three.

- [ ] **Step 4: Inspect visual and audio evidence**

Inspect all three contact sheets and extract start, middle, pause, and ending frames for each speaker. Confirm visible mouth motion, identity, no extra people/text/watermark, and shared scene lighting. Run FFmpeg `blackdetect`, `freezedetect`, and `ebur128`; read provider reports to confirm A uses Doubao audio and B retains native audio.

- [ ] **Step 5: Apply at most one targeted retry per failed role/route**

Only when a hard gate fails, delete neither old evidence nor other passed assets. Run one exact targeted retry, for example:

```bash
.venv/bin/python factory_cli.py speaking-ab --project sample_episode \
  --enable-live --only natural_lin --candidate 2
```

The successful candidate-2 job becomes the selected source for that role/route. Repeat Step 3 and Step 4. Do not regenerate a passed scene anchor, voice, role, or route.

- [ ] **Step 6: Record the decision**

Write `review.md` with:

- exact model and audio provider for A and B,
- whether mouth start/end and pauses align,
- scene-consistency observations,
- identity/anatomy/text findings,
- motion smoothness result,
- chosen route for the full episode,
- residual risks.

Append a concise v1.63 entry to `docs/iteration-log.md`.

- [ ] **Step 7: Run final regression and safety checks**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check factory factory_cli.py tests
.venv/bin/python -m compileall -q factory factory_cli.py
git diff --check
rg -n 'sk-[A-Za-z0-9]|Authorization:|Bearer |data:image|X-Api-Key' \
  runs/sample_episode/speaking_ab_20260720 docs/iteration-log.md
```

Expected: tests/lint/compile/diff pass; credential scan produces no secret-bearing report content.

- [ ] **Step 8: Commit implementation records only**

Do not commit generated media or credential-bearing state. Commit source, tests, and the iteration log:

```bash
git add factory/speaking_ab.py factory_cli.py tests/test_speaking_ab.py \
  tests/test_cli_speaking_ab.py docs/iteration-log.md
git commit -m "feat: deliver speaking A/B quality probe"
```
