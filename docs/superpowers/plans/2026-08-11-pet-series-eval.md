# Pet Series EVAL And First Three Episodes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evidence-bound pet-series EVAL, encode the historical bad cases as regression gates, and use it to create, repair, seal, and deliver the first three 60-75 second episodes of 《猫猫事务所：七号纸箱》.

**Architecture:** Add a generic series manifest and continuity ledger beside the existing hard-coded pet-sitcom pipeline. Pure story checks, candidate review contracts, episode checks, and repair-ticket generation remain separate modules with JSON artifacts between them; existing gateway, Seed-TTS, Seedance, FFmpeg, and Delivery Eval functions are adapted rather than duplicated. The first-season config is the source of truth for all six episodes, while only episodes 1-3 enter live generation in this plan.

**Tech Stack:** Python 3.12, dataclasses, JSON/CSV, pytest 9, Pillow 12, NumPy 2.5, OpenCV 5, FFmpeg/ffprobe, existing gateway clients, Doubao Seed-TTS 2.0, Seedream 4.5, Seedance 2.0, existing `video_delivery_eval.py`.

## Global Constraints

- Preserve unrelated dirty files in `/Users/tml/Desktop/漫剧工厂`; implementation runs on branch `codex/pet-series-eval` in an isolated worktree.
- Each episode is 60-75 seconds, 9:16, 1080x1920, 30 fps, H.264/AAC, with audio and burned Chinese captions.
- Episode structure is 9-12 shots; one shot carries one primary action or one dialogue beat and contains no model-authored scene cut.
- Any P0 or P1 finding blocks promotion; total score must be at least 85, story at least 20/25, and cross-episode continuity at least 8/10.
- Visible-speaker mouth onset/offset error must be at most 0.25 seconds; silent visible cats must not flap continuously.
- Non-designed dialogue overlap is zero; generated speech is never time-stretched with `atempo`.
- Extra limbs, disconnected limbs, duplicate characters, duplicate props, unexplained prop motion, wrong speaker, obvious lip-sync separation, accidental freeze, and unexplained scene jump are non-overridable P1 failures.
- Do not use `tpad`, copied tail frames, default optical-flow interpolation, long freezes, frequent reaction cuts, or cross-dissolves to conceal a failed source shot.
- Only approved, non-looped music and whitelisted dialogue/foley sources may enter the final mix; speech remains intelligible and the mix contains no broadband synthesis noise.
- All source assets, review evidence, reports, and sealed outputs are SHA-256-bound; changed dependencies become stale.
- Deliver only from the sealed delivery directories produced after technical and semantic EVAL pass.

---

### Task 1: Generic Series Manifest And Six-Episode Story Bible

**Files:**
- Create: `factory/pet_series.py`
- Create: `config/pet_series_season_01.json`
- Create: `tests/test_pet_series.py`

**Interfaces:**
- Consumes: existing character references `奶糖_reference.png` and `豆包_reference.png`; existing gateway model names.
- Produces: `PetSeriesPlan`, `SeriesEpisode`, `SeriesShot`, `SeriesCharacter`, `SeriesScene`, `PropContract`, `load_pet_series(path: Path, *, output_root: Path | None = None) -> PetSeriesPlan`, and `write_series_snapshot(plan: PetSeriesPlan) -> Path`.

- [ ] **Step 1: Write failing manifest tests**

```python
def test_loads_six_episode_story_bible(tmp_path):
    plan = load_pet_series(SEASON_CONFIG, output_root=tmp_path)
    assert [episode.episode_id for episode in plan.episodes] == [
        "ep01", "ep02", "ep03", "ep04", "ep05", "ep06"
    ]
    assert all(60.0 <= episode.duration_seconds <= 75.0 for episode in plan.episodes)
    assert all(9 <= len(episode.shots) <= 12 for episode in plan.episodes)
    assert {character.slug for character in plan.characters} == {"doubao", "naitang"}

def test_rejects_unknown_continuity_dependency(tmp_path):
    payload = minimal_series_payload()
    payload["episodes"][0]["shots"][1]["continuity_source_ids"] = ["missing"]
    path = write_payload(tmp_path, payload)
    with pytest.raises(PetSeriesError, match="unknown continuity source"):
        load_pet_series(path, output_root=tmp_path / "out")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series.py -q`

Expected: FAIL because `factory.pet_series` does not exist.

- [ ] **Step 3: Implement immutable dataclasses and strict loader**

```python
@dataclass(frozen=True)
class SeriesShot:
    shot_id: str
    index: int
    role: Literal["hook", "setup", "investigation", "reveal", "cliffhanger"]
    duration_seconds: float
    generation_duration_seconds: int
    scene_slug: str
    visible_characters: tuple[str, ...]
    speaker: str | None
    dialogue: str
    dialogue_offset_seconds: float
    action: str
    start_state: str
    end_state: str
    prop_contracts: tuple[PropContract, ...]
    continuity_source_ids: tuple[str, ...]

def load_pet_series(path: Path, *, output_root: Path | None = None) -> PetSeriesPlan:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _parse_and_validate(payload, source_path=path, output_root=output_root)
```

The six-episode config must copy the approved titles, reveals, and cliffhangers from the design spec. Episodes 1-3 contain 10 shots each and exact durations between 62 and 70 seconds. Episodes 4-6 may remain story-planned manifests, but each still has complete shot roles, state transitions, final authored dialogue text, and no missing fields.

- [ ] **Step 4: Bind original character and scene contracts**

The config uses:

```json
{
  "characters": [
    {"slug": "doubao", "voice_key": "doubao_series", "reference_path": "/Users/tml/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1/assets/characters/豆包_reference.png"},
    {"slug": "naitang", "voice_key": "naitang_series", "reference_path": "/Users/tml/Desktop/宠物短剧样片/猫猫鬼点子_逐镜重拍_20260730_v1/assets/characters/奶糖_reference.png"}
  ],
  "models": {"image": "doubao-seedream-4-5", "video": "doubao-seedance-2-0", "tts": "seed-tts-2.0"}
}
```

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series.py -q`

Expected: PASS.

Commit: `feat: add pet series story bible`

---

### Task 2: Script Preflight And Attraction Score

**Files:**
- Create: `factory/pet_series_story_eval.py`
- Create: `tests/test_pet_series_story_eval.py`

**Interfaces:**
- Consumes: `PetSeriesPlan`, `SeriesEpisode`, and `SeriesShot` from Task 1.
- Produces: `PreviousEpisodeContext`, `StoryFinding`, `StoryEvalResult`, `evaluate_episode_story(episode: SeriesEpisode, previous: PreviousEpisodeContext | None) -> StoryEvalResult`, and `evaluate_season_story(plan: PetSeriesPlan) -> dict[str, StoryEvalResult]`.

- [ ] **Step 1: Write failing checks for hook, causality, reveal, and cliffhanger**

```python
def test_episode_passes_required_story_shape(series_plan):
    result = evaluate_episode_story(series_plan.episode("ep01"), previous=None)
    assert result.hard_failures == ()
    assert result.story_score >= 20

def test_hook_must_finish_by_three_seconds(series_plan):
    episode = replace_first_shot_duration(series_plan.episode("ep01"), 3.5)
    result = evaluate_episode_story(episode, previous=None)
    assert "STORY_HOOK_WINDOW" in {finding.rule_id for finding in result.findings}

def test_next_episode_reclaims_previous_cliffhanger(series_plan):
    previous = ending_snapshot(series_plan.episode("ep01"))
    broken = remove_callback(series_plan.episode("ep02"), "three_knocks")
    result = evaluate_episode_story(broken, previous=previous)
    assert "SERIES_CLIFFHANGER_CALLBACK" in result.p1_rule_ids
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_story_eval.py -q`

Expected: FAIL because evaluator symbols are missing.

- [ ] **Step 3: Implement deterministic story rules**

```python
STORY_RULES = {
    "STORY_HOOK_WINDOW": 5,
    "STORY_CAUSAL_PROGRESS": 5,
    "STORY_INFORMATION_GAIN": 5,
    "STORY_REVEAL": 5,
    "STORY_CLIFFHANGER": 5,
}

@dataclass(frozen=True)
class PreviousEpisodeContext:
    episode_id: str
    ending_clues: Mapping[str, str]
    unresolved_threads: tuple[str, ...]

def evaluate_episode_story(episode, previous=None):
    findings = [
        *_check_hook_window(episode),
        *_check_role_order(episode),
        *_check_state_causality(episode),
        *_check_information_gain(episode),
        *_check_reveal_and_cliffhanger(episode, previous),
    ]
    return _story_result(findings)
```

Checks are structural and fail closed when required annotations are absent. They do not claim to predict audience performance; `P2_STORY_APPEAL_REVIEW` always creates a semantic case for a human/model-assisted review with concrete beat evidence.

- [ ] **Step 4: Add regression cases for empty repetition and fake mystery**

```python
def test_repeated_investigation_without_new_fact_loses_information_score(series_plan):
    episode = make_all_investigation_facts_equal(series_plan.episode("ep01"))
    result = evaluate_episode_story(episode)
    assert result.story_score <= 20
    assert "STORY_INFORMATION_GAIN" in result.p2_rule_ids

def test_planned_mystery_still_requires_physical_cause(series_plan):
    episode = remove_force_evidence(series_plan.episode("ep03"), prop="key_07")
    result = evaluate_episode_story(episode, previous=ending_snapshot(series_plan.episode("ep02")))
    assert "STORY_UNEXPLAINED_PHYSICS" in result.p1_rule_ids
```

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_story_eval.py -q`

Expected: PASS.

Commit: `feat: add pet series story preflight`

---

### Task 3: Cross-Episode Continuity Ledger

**Files:**
- Create: `factory/pet_series_continuity.py`
- Create: `tests/test_pet_series_continuity.py`

**Interfaces:**
- Consumes: `PetSeriesPlan` and episode state annotations.
- Produces: `ContinuitySnapshot`, `ContinuityLedger`, `ContinuitySnapshot.to_story_context() -> PreviousEpisodeContext`, `build_expected_snapshot(episode: SeriesEpisode) -> ContinuitySnapshot`, `validate_episode_transition(previous, current) -> tuple[ContinuityFinding, ...]`, and `write_continuity_ledger(plan, completed_episode_ids) -> Path`.

- [ ] **Step 1: Write failing continuity tests**

```python
def test_ep02_starts_with_ep01_key_and_three_knocks(series_plan):
    ledger = ContinuityLedger.from_plan(series_plan)
    assert validate_episode_transition(ledger.ending("ep01"), ledger.opening("ep02")) == ()

def test_prop_cannot_change_location_without_transition(series_plan):
    current = replace_prop_location(
        ContinuityLedger.from_plan(series_plan).opening("ep02"), "key_07", "kitchen"
    )
    findings = validate_episode_transition(
        ContinuityLedger.from_plan(series_plan).ending("ep01"), current
    )
    assert findings[0].rule_id == "SERIES_PROP_STATE_CONTRADICTION"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_continuity.py -q`

- [ ] **Step 3: Implement canonical state snapshots**

```python
@dataclass(frozen=True)
class ContinuitySnapshot:
    episode_id: str
    moment: Literal["opening", "ending"]
    character_knowledge: Mapping[str, tuple[str, ...]]
    clues: Mapping[str, str]
    props: Mapping[str, PropState]
    unresolved_threads: tuple[str, ...]
    story_time: str

def validate_episode_transition(previous, current):
    return tuple(
        (*_knowledge_findings(previous, current), *_prop_findings(previous, current),
         *_thread_findings(previous, current), *_time_findings(previous, current))
    )
```

Ledger JSON includes canonical hashes for the season config and each episode. Regenerating an earlier episode marks later snapshots stale only when their declared dependencies changed.

- [ ] **Step 4: Test stale propagation and atomic write**

Run: `.venv/bin/pytest tests/test_pet_series_continuity.py -q`

Expected: PASS, including tests that a changed `ep01` clue stales `ep02`, while changed music metadata does not stale story state.

- [ ] **Step 5: Commit**

Commit: `feat: add pet series continuity ledger`

---

### Task 4: Pet-Series Policy And Historical Bad-Case Regression Pack

**Files:**
- Create: `config/pet_series_policy.json`
- Create: `factory/pet_series_policy.py`
- Create: `tests/fixtures/pet_series_badcases/cases.json`
- Create: `tests/test_pet_series_policy.py`

**Interfaces:**
- Consumes: design-spec thresholds and existing Delivery Eval rule vocabulary.
- Produces: `PetSeriesPolicy`, `RuleSpec`, `load_pet_series_policy(path: Path) -> PetSeriesPolicy`, and `historical_case_matrix(policy) -> tuple[BadCase, ...]`.

- [ ] **Step 1: Write failing policy and fixture tests**

```python
def test_policy_contains_all_non_overridable_historical_rules():
    policy = load_pet_series_policy(POLICY_PATH)
    assert {
        "VISUAL_EXTRA_LIMB", "VISUAL_DISCONNECTED_LIMB", "VISUAL_DUPLICATE_CHARACTER",
        "PHYSICS_DUPLICATE_PROP", "PHYSICS_UNEXPLAINED_MOTION", "AUDIO_VOICE_DRIFT",
        "AUDIO_DIALOGUE_OVERLAP", "LIPSYNC_VISIBLE_SPEAKER", "LIPSYNC_SILENT_FLAP",
        "MOTION_ACCIDENTAL_FREEZE", "MOTION_DUPLICATE_CADENCE", "EDIT_GHOST_DISSOLVE",
        "SERIES_STATE_CONTRADICTION",
    } <= set(policy.non_overridable_rules)

def test_each_historical_rule_has_fail_and_neighbor_pass_fixture():
    cases = historical_case_matrix(load_pet_series_policy(POLICY_PATH))
    by_rule = group_cases(cases)
    for rule_id in required_historical_rules():
        assert {case.expected for case in by_rule[rule_id]} == {"PASS", "FAIL"}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_policy.py -q`

- [ ] **Step 3: Implement strict policy loader and exact thresholds**

The JSON policy includes platform dimensions, duration, loudness, silence, duplicate-frame, lip-sync, score, and evidence requirements. Rule kinds are `automatic`, `semantic`, or `automatic_or_semantic`; unknown kinds and duplicate IDs fail loading.

```json
{
  "policy_id": "original-pet-series-v1",
  "platform": {"width": 1080, "height": 1920, "fps": 30, "min_duration_seconds": 60, "max_duration_seconds": 75},
  "score_thresholds": {"total": 85, "story": 20, "continuity": 8},
  "mouth_timing_tolerance_seconds": 0.25,
  "non_overridable_levels": ["P0", "P1"]
}
```

- [ ] **Step 4: Encode historical cases with source citations**

Each fixture record has `rule_id`, `expected`, `source_project`, `source_evidence`, `observed`, and machine-readable metrics or semantic flags. Cite the existing iteration-log sections for freeze-dried noise/overcut, R036 four paws, S004/S012/S018 physics, V3 duplicate cadence, and cross-dissolve ghosting.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_policy.py -q`

Commit: `test: encode pet series historical bad cases`

---

### Task 5: Candidate Evidence Contract And Review Validator

**Files:**
- Create: `factory/pet_series_candidate_eval.py`
- Create: `tests/test_pet_series_candidate_eval.py`

**Interfaces:**
- Consumes: `SeriesShot`, selected candidate MP4, drive WAV, continuity frame, `PetSeriesPolicy`.
- Produces: `CandidateEvalBundle`, `build_candidate_evidence(plan: PetSeriesPlan, episode_id: str, shot_id: str, candidate_path: Path, *, ffmpeg_bin: str = "ffmpeg") -> CandidateEvalBundle`, `write_candidate_review_template(bundle: CandidateEvalBundle) -> Path`, and `validate_candidate_review(bundle: CandidateEvalBundle, review_path: Path) -> CandidateEvalResult`.

- [ ] **Step 1: Write failing evidence-contract tests**

```python
def test_speaking_shot_requires_action_mouth_and_boundary_evidence(candidate_bundle):
    assert set(candidate_bundle.evidence) == {
        "action_12frames", "mouth_8fps", "triptych", "incoming_boundary", "outgoing_boundary"
    }

def test_extra_paw_cannot_be_approved(candidate_bundle, review_payload):
    review_payload["checks"]["visible_limb_count"] = {
        "result": "FAIL", "actual": "four forepaws visible", "evidence": ["action_12frames.jpg"]
    }
    with pytest.raises(CandidateEvalError, match="VISUAL_EXTRA_LIMB"):
        validate_candidate_review(candidate_bundle, write_review(review_payload))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_candidate_eval.py -q`

- [ ] **Step 3: Implement evidence generation by adapting existing helpers**

Reuse frame reading and sheet composition patterns from `pet_replica_review.py` and `pet_sitcom_review.py`; do not import private helpers across modules. Shared generic media helpers may move to `factory/media_evidence.py` only when both old and new callers are updated in this task.

```python
def build_candidate_evidence(plan, episode_id, shot_id, candidate_path, *, ffmpeg="ffmpeg"):
    shot = plan.episode(episode_id).shot(shot_id)
    bindings = _bind_inputs(plan, shot, candidate_path)
    evidence = _render_required_sheets(shot, candidate_path, bindings, ffmpeg=ffmpeg)
    return CandidateEvalBundle(shot=shot, bindings=bindings, evidence=evidence)
```

- [ ] **Step 4: Implement fail-closed semantic review schema**

Required checks include identity, character count, limb count, limb connection, facial stability, expression, preparation/execution/settle, prop count, support/contact/force/stop, internal cut, scene state, source residue, visible-speaker mouth timing, and silent-character mouth state. Missing notes, missing evidence, `UNCERTAIN`, stale hashes, or false checks fail validation.

- [ ] **Step 5: Add audio-window tests**

```python
def test_visible_speaker_mouth_offset_tolerance_is_quarter_second():
    result = validate_mouth_window(audio=(0.60, 3.10), mouth=(0.82, 3.31), tolerance=0.25)
    assert result.passed
    assert not validate_mouth_window(audio=(0.60, 3.10), mouth=(0.90, 3.31), tolerance=0.25).passed
```

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_candidate_eval.py -q`

Commit: `feat: add evidence-bound pet shot eval`

---

### Task 6: Repair Tickets And Minimal Regeneration Closure

**Files:**
- Create: `factory/pet_series_repair.py`
- Create: `tests/test_pet_series_repair.py`

**Interfaces:**
- Consumes: candidate or episode findings and shot dependency graph.
- Produces: `RepairTicket`, `RepairPlan`, `build_repair_plan(plan, episode_id, findings) -> RepairPlan`, and `write_repair_plan(repair_plan, destination: Path) -> Path`.

- [ ] **Step 1: Write failing minimal-scope tests**

```python
def test_physics_failure_only_rebuilds_failed_shot_and_true_dependents(series_plan):
    finding = finding_for("ep01", "s004", "PHYSICS_UNEXPLAINED_MOTION")
    repair = build_repair_plan(series_plan, "ep01", [finding])
    assert repair.regenerate_shots == ("s004", "s005")
    assert "s001" in repair.preserve_shots

def test_audio_mix_failure_does_not_regenerate_video(series_plan):
    finding = finding_for("ep01", "global", "AUDIO_MUSIC_DUCKING")
    repair = build_repair_plan(series_plan, "ep01", [finding])
    assert repair.regenerate_shots == ()
    assert repair.recompose_audio is True
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_repair.py -q`

- [ ] **Step 3: Implement rule-to-remediation mapping**

```python
REMEDIATIONS = {
    "VISUAL_EXTRA_LIMB": ("reframe_and_regenerate", "candidate"),
    "PHYSICS_UNEXPLAINED_MOTION": ("rewrite_force_chain_and_regenerate", "candidate"),
    "LIPSYNC_VISIBLE_SPEAKER": ("regenerate_or_local_mouth_repair", "candidate"),
    "AUDIO_DIALOGUE_OVERLAP": ("rebuild_absolute_audio_timeline", "episode_audio"),
    "EDIT_GHOST_DISSOLVE": ("replace_with_motivated_cut", "episode_compose"),
}
```

Tickets include exact rule, evidence, observed interval, probable root cause, immutable dependencies, smallest allowed rerun, and checks that must rerun.

- [ ] **Step 4: Verify deterministic JSON and stale binding**

Run: `.venv/bin/pytest tests/test_pet_series_repair.py -q`

Expected: PASS and byte-stable JSON for identical inputs.

- [ ] **Step 5: Commit**

Commit: `feat: generate pet series repair tickets`

---

### Task 7: Generic Series Audio Adapter

**Files:**
- Create: `factory/pet_series_audio.py`
- Create: `tests/test_pet_series_audio.py`

**Interfaces:**
- Consumes: `PetSeriesPlan`, existing Seed-TTS client, and fixed voice contracts.
- Produces: `SeriesSpeechAsset`, `generate_episode_speech(plan: PetSeriesPlan, episode_id: str, *, tts_client: Any) -> tuple[SeriesSpeechAsset, ...]`, `build_episode_drive_audio(plan: PetSeriesPlan, episode_id: str, assets: Sequence[SeriesSpeechAsset]) -> tuple[Path, ...]`, `load_episode_speech_assets(plan: PetSeriesPlan, episode_id: str) -> tuple[SeriesSpeechAsset, ...]`, and `audio_manifest.json`.

- [ ] **Step 1: Write failing audio contract tests**

```python
def test_audio_timeline_uses_measured_wav_duration_without_atempo(series_plan, fake_tts):
    assets = generate_episode_speech(series_plan, "ep01", tts_client=fake_tts)
    assert all(asset.measured_duration_seconds > 0 for asset in assets)
    assert non_designed_overlaps(assets) == []
    assert "atempo" not in json.dumps([asset.command for asset in assets])

def test_character_voice_is_stable_across_three_episodes(series_plan):
    contracts = voice_contracts(series_plan, episode_ids=("ep01", "ep02", "ep03"))
    assert len({contracts[e]["doubao"].voice_id for e in contracts}) == 1
    assert len({contracts[e]["naitang"].voice_id for e in contracts}) == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_audio.py -q`

- [ ] **Step 3: Adapt Seed-TTS and absolute scheduling**

Reuse provider calls and WAV validation from `pet_sitcom_audio_first.py`. Define two fixed voices in the series manifest, trim provider edge silence, measure each WAV, keep at least 0.30 seconds nonnegative tail clearance unless a declared J/L cut exists, and write `audio_manifest.json` with provider, voice ID, rate, hash, raw/trimmed durations, and absolute timing.

- [ ] **Step 4: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_audio.py -q`

Commit: `feat: add pet series audio adapter`

---

### Task 8: Generic Series Generation Adapter

**Files:**
- Create: `factory/pet_series_generation.py`
- Create: `tests/test_pet_series_generation.py`

**Interfaces:**
- Consumes: `PetSeriesPlan`, approved anchors, drive WAV assets from Task 7, existing gateway clients, and candidate review status from Task 5.
- Produces: `generate_episode_anchors(plan: PetSeriesPlan, episode_id: str, *, image_client: Any, enable_live: bool = False) -> Mapping[str, Any]`, `generate_episode_candidates(plan: PetSeriesPlan, episode_id: str, *, video_client: Any, shot_ids: Sequence[str] = (), enable_live: bool = False) -> Mapping[str, Any]`, `select_episode_candidate(plan: PetSeriesPlan, episode_id: str, shot_id: str, candidate_number: int) -> Path`, `build_series_shot_prompt(plan: PetSeriesPlan, episode_id: str, shot_id: str, candidate_number: int) -> str`, resumable task state, and selection manifests.

- [ ] **Step 1: Write failing generation-contract tests**

```python
def test_generation_prompt_contains_exact_limb_and_prop_contract(series_plan):
    prompt = build_series_shot_prompt(series_plan, "ep01", "s004", candidate_number=1)
    assert "visible forepaw count" in prompt
    assert "exactly one key marked 07" in prompt
    assert "preparation, action, result, settle" in prompt

def test_full_generation_requires_two_current_risk_probes(series_plan):
    with pytest.raises(PetSeriesGenerationError, match="risk probes"):
        generate_episode_candidates(series_plan, "ep01", enable_live=True)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_generation.py -q`

- [ ] **Step 3: Adapt gateway generation with resumable state**

Reuse atomic submission/poll/download and secret redaction from `pet_sitcom_generation.py`. References are ordered: current character sheets, scene anchor, declared continuity ending frame. Risk-probe mode accepts exactly two shot IDs and must pass current manual reviews before full generation unlocks. Candidate 2 requires a concrete retry reason; candidate 3 additionally requires a current P1 repair ticket.

- [ ] **Step 4: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_generation.py -q`

Commit: `feat: add pet series generation adapter`

---

### Task 9: Generic Series Composition Adapter

**Files:**
- Create: `factory/pet_series_compose.py`
- Create: `tests/test_pet_series_compose.py`

**Interfaces:**
- Consumes: selected candidate manifests from Task 8, verified speech timing from Task 7, approved non-looped music, foley whitelist, and `PetSeriesPolicy`.
- Produces: `compose_series_episode(plan: PetSeriesPlan, episode_id: str, *, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe") -> SeriesCompositionResult`, clean/captioned masters, composition manifest, measured audio report, and atomic publication records.

- [ ] **Step 1: Write failing composition tests**

```python
def test_compose_rejects_short_source_instead_of_padding(series_plan, short_selected_clip):
    with pytest.raises(PetSeriesComposeError, match="shorter than edit window"):
        compose_series_episode(series_plan, "ep01")

def test_compose_command_has_no_forbidden_rescue_filters(series_plan, reviewed_sources):
    command = build_series_compose_command(series_plan, "ep01", reviewed_sources)
    rendered = " ".join(command)
    assert "tpad" not in rendered
    assert "minterpolate" not in rendered
    assert "xfade" not in rendered
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_compose.py -q`

- [ ] **Step 3: Implement deterministic composition**

Use selected source trims only. Default transitions are hard cuts; a declared time/space jump may use a four-frame fade-through-black with exactly one full black frame. Use one approved non-looped music source, dialogue ducking, restrained foley, and no synthesized broadband effects. Validate 1080x1920@30, AAC stereo 48 kHz, loudness, peak, faststart, frame count, and zero dialogue overlap.

- [ ] **Step 4: Run focused tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_compose.py -q`

Commit: `feat: add pet series composition adapter`

---

### Task 10: Episode EVAL, Delivery Eval Adapter, And Operator CLI

**Files:**
- Create: `factory/pet_series_episode_eval.py`
- Create: `factory/pet_series_cli.py`
- Create: `tests/test_pet_series_episode_eval.py`
- Create: `tests/test_pet_series_cli.py`

**Interfaces:**
- Consumes: story result, continuity ledger, candidate reviews, final MP4, audio manifest, composition manifest, policy, and existing `video_delivery_eval.py`.
- Produces: `evaluate_series_episode(inputs: EpisodeEvalInputs) -> EpisodeEvalResult`, four required Delivery Eval input artifacts, `semantic_review.json`, `repair_plan.json`, and CLI commands `preflight`, `evidence`, `repair-plan`, `delivery-prepare`, `delivery-finalize`, and `delivery-deliver`.

- [ ] **Step 1: Write failing episode aggregation tests**

```python
def test_p1_blocks_high_numeric_score(complete_eval_inputs):
    result = evaluate_series_episode(**complete_eval_inputs, injected_findings=[
        finding("VISUAL_EXTRA_LIMB", level="P1")
    ])
    assert result.score >= 85
    assert result.status == "FAIL"

def test_pass_requires_story_and_continuity_subscores(complete_eval_inputs):
    result = evaluate_series_episode(**complete_eval_inputs, story_score=19, continuity_score=10)
    assert result.status == "FAIL"
    assert "STORY_SCORE_THRESHOLD" in result.rule_ids
```

- [ ] **Step 2: Write failing CLI tests**

```python
def test_preflight_cli_writes_json_report(tmp_path):
    result = run_cli(["preflight", "--series", str(SEASON_CONFIG), "--episode", "ep01", "--output-root", str(tmp_path)])
    assert result.exit_code == 0
    assert json.loads((tmp_path / "ep01" / "eval" / "story_preflight.json").read_text())["status"] == "PASS"
```

- [ ] **Step 3: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_pet_series_episode_eval.py tests/test_pet_series_cli.py -q`

- [ ] **Step 4: Implement aggregation and required artifacts**

```python
def evaluate_series_episode(inputs: EpisodeEvalInputs) -> EpisodeEvalResult:
    findings = collect_all_findings(inputs)
    scores = score_dimensions(inputs, findings)
    blocked = any(item.level in {"P0", "P1"} for item in findings)
    thresholds_met = scores.total >= 85 and scores.story >= 20 and scores.continuity >= 8
    return EpisodeEvalResult(
        status="PASS" if not blocked and thresholds_met else "FAIL",
        scores=scores,
        findings=tuple(findings),
    )
```

Generate absolute `cut_plan.csv`, `source_routing.csv`, `quality_report.md`, and `pet_series_policy.json` for the external evaluator. Prepare must return the expected “semantic review required” state; finalize requires every generated case to be filled with evidence; deliver only runs after PASS.

- [ ] **Step 5: Implement CLI without editing the giant root dispatcher**

Invoke with `.venv/bin/python -m factory.pet_series_cli <command> --series config/pet_series_season_01.json --episode ep01 --output-root <absolute-output-root>`. Commands print artifact paths and status only; no gateway secrets. Exit codes: 0 PASS/success, 2 semantic review required, 3 quality failure, 4 stale dependency, 5 external provider failure.

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/pytest tests/test_pet_series_episode_eval.py tests/test_pet_series_cli.py -q`

Commit: `feat: add pet series eval cli`

---

### Task 11: Full Regression, Documentation, And Production Readiness

**Files:**
- Create: `docs/pet-series-eval.md`
- Modify: `README.md`
- Modify: `docs/quality-iteration-handbook.md`

**Interfaces:**
- Consumes: all new modules and historical case matrix.
- Produces: operator documentation and verified production readiness report.

- [ ] **Step 1: Run focused pet-series suite**

Run: `.venv/bin/pytest tests/test_pet_series*.py -q`

Expected: all pet-series tests pass.

- [ ] **Step 2: Run affected existing suites**

Run: `.venv/bin/pytest tests/test_pet_sitcom_review.py tests/test_pet_longform_review.py tests/test_pet_replica_review.py tests/test_pet_replica_compose.py -q`

Expected: all pass; new generic helpers do not regress existing projects.

- [ ] **Step 3: Run whole repository quality gates**

Run: `.venv/bin/pytest -q`

Run: `.venv/bin/ruff check factory tests`

Run: `.venv/bin/python -m compileall -q factory tests`

Run: `git diff --check`

Expected: zero failures and zero lint/compile/whitespace errors.

- [ ] **Step 4: Document exact workflow**

The guide contains the six commands from manifest preflight through sealed delivery, artifact meanings, status/exit-code table, bad-case memory update procedure, and a rule-by-rule explanation of why a numeric score never overrides P0/P1.

- [ ] **Step 5: Commit**

Commit: `docs: document pet series eval workflow`

---

### Task 12: Generate, Review, Repair, And Seal Episodes 1-3

**Files:**
- Runtime create: `/Users/tml/Desktop/宠物短剧样片/猫猫事务所_七号纸箱_20260811/`
- Runtime update: `docs/iteration-log.md`

**Interfaces:**
- Consumes: production-ready code from Tasks 1-11, gateway credentials from the existing local `.env`, approved character references, approved music, and the external Delivery Eval tool.
- Produces: three sealed episodes and their complete evidence, repair history, continuity snapshots, and delivery manifests.

- [ ] **Step 1: Preflight all six stories and freeze the season snapshot**

Run:

```bash
.venv/bin/python -m factory.pet_series_cli preflight \
  --series config/pet_series_season_01.json --all-episodes \
  --output-root /Users/tml/Desktop/宠物短剧样片/猫猫事务所_七号纸箱_20260811
```

Expected: six story reports pass structural hard gates and the season snapshot hash is written.

- [ ] **Step 2: Generate and review two risk probes per delivery episode**

For each of `ep01`, `ep02`, and `ep03`, generate one dual-cat speaking shot and one prop-physics shot. Build 12-frame action sheets, 8 fps mouth sheets for speaking shots, and continuity boundaries. Fill the review contracts honestly. A failed probe creates a repair ticket and is regenerated before full episode production.

- [ ] **Step 3: Generate fixed multi-character audio for episodes 1-3**

Run the audio command for each episode, then verify provider/voice/rate stability, trimmed silence, measured timing, zero unintended overlap, and dialogue clearance. Listen to the dialogue comparison before candidate generation; keep the same two selected voices for all three episodes.

- [ ] **Step 4: Generate episode candidates in dependency order**

Generate shots in batches of at most four. Review each batch before continuing. Candidate 1 is the baseline; candidate 2 addresses a concrete ticket; candidate 3 is allowed only for a P1 failure after the prompt or composition changed. Stop after candidate 3 and redesign the shot rather than continuing random retries.

- [ ] **Step 5: Compose rough episode and run EVAL prepare**

For each episode, create clean and captioned masters plus `cut_plan.csv`, `source_routing.csv`, and `quality_report.md`. Run external Delivery Eval `prepare`; inspect full contact sheet, beat sheet, every generated semantic case, dense action evidence, mouth evidence, and all boundaries.

- [ ] **Step 6: Repair every FAIL or UNCERTAIN case**

Use `repair_plan.json` to regenerate only failed shots or recompose only affected audio/edit layers. Regenerate all four delivery artifacts and rerun prepare after every repair. Never rewrite semantic review results to pass an unchanged artifact.

- [ ] **Step 7: Finalize and seal each episode**

Fill every semantic case with `PASS`, `FAIL`, or `UNCERTAIN`, concrete `actual`, and generated evidence paths. Finalize only with all PASS; then deliver to a new sealed destination. Confirm sealed video hash equals the evaluated final-master hash.

- [ ] **Step 8: Run cross-episode playback and continuity review**

Play the three sealed videos in order. Verify ep01’s three knocks are recalled within ep02’s first 15 seconds, ep02’s moving robot leads into ep03’s vigil, key/clue locations remain consistent, character knowledge never regresses, and voices remain recognizable across all episodes.

- [ ] **Step 9: Record measured outcomes**

Append one iteration-log section per episode with problems, evidence, root cause, selected repair, measured effect, remaining P2 notes, final score, sealed path, duration, media specs, test counts, and SHA-256.

- [ ] **Step 10: Commit runtime documentation only**

Do not commit generated videos or secret-bearing gateway reports. Commit sanitized iteration records and any new reusable bad-case fixtures.

Commit: `docs: record first pet series deliveries`
