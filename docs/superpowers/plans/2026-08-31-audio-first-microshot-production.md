# Audio-First Microshot Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an audio-first, review-gated microshot pipeline in which every visible line of dialogue has a final audio asset, a verified speaking-video capability, continuity references, and an approved candidate before it can be edited.

**Architecture:** Keep `VisualTimeline` as the source of shot order and duration, and add a separate serializable `PerformanceSheet` that binds each microshot to one performable purpose, dialogue, motion beats, and continuity requirements. A dialogue-asset manifest, model-capability report, candidate-state manifest, and approved-only selection gate form the production evidence chain; video jobs consume that chain and reject incomplete requests locally.

**Tech Stack:** Python 3.12, dataclasses, JSON artifacts, pytest, ffmpeg, existing Gateway/Doubao clients.

**Spec:** `docs/superpowers/specs/2026-08-31-audio-first-microshot-production-design.md`

## Global Constraints

- Do not modify, regenerate, or overwrite the delivered Episode 1 V1/V2/V3 files.
- Do not submit any external video request, recharge an account, or create a paid batch without a separate user authorization.
- Each microshot has exactly one narrative purpose: establishing, see, reaction, speak, action, object, result, or resolve.
- A microshot may contain at most two characters; a contact action has one explicit contact point and one actor.
- Action microshots have a 2–4 second target timeline duration. A provider’s four-second minimum output is trimmed in editing rather than expanding the performance.
- A visible speaking shot requires final immutable dialogue audio, the matching speaker, a model that passed the three-character lip-sync test, character references, a scene keyframe, and the prior approved end-frame anchor.
- The three lip-sync test speakers are Sun Wukong, Yang Jian, and Nezha. A model that fails any required speaking test is `action_only` and cannot receive visible-speaking jobs.
- Every character video job uses approved character references. The first character shot in a scene also uses its scene keyframe; later character shots use the nearest approved end-frame anchor from the same scene.
- Motion prompts must describe weight transfer, foot or contact support, acceleration/deceleration, and a stable ending; they must prohibit floating, uniform gliding, slow motion, and unexplained camera movement.
- Candidate state transitions are only `planned -> audio_ready -> submitted -> rendered -> review_required -> approved | rejected | blocked`; already-submitted tasks must resume polling rather than resubmit.
- The editor may read only the ordered approved-candidate manifest. It must report missing story slots rather than substitute unrelated clips.

---

## File structure and responsibilities

| File | Responsibility |
| --- | --- |
| `factory/performance_card.py` | Own the serializable `PerformanceCard`/`PerformanceSheet` contracts and performable-script validation without changing legacy timeline artifacts. |
| `factory/performance_planner.py` | Ask the text planner for one performance card per microshot and parse it against the timeline. |
| `factory/prompt_compiler.py` | Compile an existing microshot plus its card into a constrained, physically specific video prompt. |
| `factory/dialogue_assets.py` | Build immutable per-dialogue audio evidence from final TTS output and bind audio files to speaking cards. |
| `factory/model_bakeoff.py` | Persist three-speaker lip-sync trial results and expose `speaking` versus `action_only` permission. |
| `factory/micro_video_batch.py` | Construct local-only video jobs from the evidence chain and pass reference images/audio/roles to the existing gateway client. |
| `factory/candidate_review.py` | Record valid candidate state transitions, review evidence, and the approved ordered visual selection. |
| `factory/micro_preview.py` | Refuse to concatenate any candidate not present in the approved ordered selection. |
| `factory/quality_production_runner.py` | Expose the new artifacts as explicit local production stages without initiating paid video generation. |

### Task 1: Add performable performance-card contracts

**Files:**
- Create: `factory/performance_card.py`
- Modify: `factory/performance_planner.py:24-151`
- Test: `tests/test_performance_card.py`
- Test: `tests/test_performance_planner.py`

**Interfaces:**
- Consumes: `factory.visual_timeline.VisualTimeline`, `factory.visual_timeline.MicroShot`, and `factory.schema.Episode`.
- Produces: `dialogue_id_for(parent_shot_id: str, dialogue_index: int) -> str`, `PerformanceCard`, `PerformanceSheet`, `performance_sheet_from_dict(data, episode, timeline) -> PerformanceSheet`, `validate_performance_sheet(sheet, episode, timeline) -> list[str]`, and `parse_performance_plan(content, episode) -> tuple[VisualTimeline, PerformanceSheet]`.

- [ ] **Step 1: Write the failing performance-card tests**

```python
from factory.performance_card import PerformanceCard, PerformanceSheet, validate_performance_sheet


def test_visible_speech_requires_a_unique_dialogue_and_speaker(episode, timeline):
    card = PerformanceCard(
        micro_shot_id="micro_001", purpose="speak", speaker_id="wukong",
        dialogue_id="", requires_visible_lipsync=True, entry_anchor_id="scene_gate",
        scene_keyframe_id="kf_gate", actor_id="wukong", target_id="yangjian",
        contact_point="", prop_hand="", start_beat="mouth closes before speaking",
        main_beat="says one short line", end_beat="holds eye contact",
        negative_constraints=("no_floating",),
    )
    sheet = PerformanceSheet(project_id=episode.project_id, cards=(card,))

    assert "micro_001 visible speech requires dialogue_id" in validate_performance_sheet(sheet, episode, timeline)


def test_contact_card_rejects_multiple_people_and_missing_contact_point(episode, timeline):
    card = make_card("micro_002", purpose="action", actor_id="wukong", target_id="yangjian", contact_point="")
    sheet = PerformanceSheet(project_id=episode.project_id, cards=(card,))

    errors = validate_performance_sheet(sheet, episode, timeline)

    assert "micro_002 contact action requires exactly one contact_point" in errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_performance_card.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'factory.performance_card'`.

- [ ] **Step 3: Implement the performance-card artifact and planner parser**

```python
# factory/performance_card.py
@dataclass(frozen=True)
class PerformanceCard:
    micro_shot_id: str
    purpose: str
    speaker_id: str
    dialogue_id: str
    requires_visible_lipsync: bool
    entry_anchor_id: str
    scene_keyframe_id: str
    actor_id: str
    target_id: str
    contact_point: str
    prop_hand: str
    start_beat: str
    main_beat: str
    end_beat: str
    negative_constraints: tuple[str, ...]


@dataclass(frozen=True)
class PerformanceSheet:
    project_id: str
    cards: tuple[PerformanceCard, ...]
    schema_version: str = "motion-comic-factory.performance-sheet.v1"


def dialogue_id_for(parent_shot_id: str, dialogue_index: int) -> str:
    return f"{parent_shot_id}.dialogue_{dialogue_index:02d}"


def validate_performance_sheet(sheet, episode, timeline) -> list[str]:
    errors = []
    shots = {shot.id: shot for shot in timeline.micro_shots}
    cards = {card.micro_shot_id: card for card in sheet.cards}
    if set(cards) != set(shots):
        errors.append("performance cards must match visual-timeline microshot ids")
    for micro_shot_id, card in cards.items():
        shot = shots.get(micro_shot_id)
        if shot is None:
            continue
        if len(shot.character_ids) > 2:
            errors.append(f"{micro_shot_id} has more than two characters")
        if card.requires_visible_lipsync and not card.dialogue_id:
            errors.append(f"{micro_shot_id} visible speech requires dialogue_id")
        if card.requires_visible_lipsync and not card.speaker_id:
            errors.append(f"{micro_shot_id} visible speech requires speaker_id")
        source_lines = next(parent for parent in episode.shots if parent.id == shot.parent_shot_id).dialogue
        source_matches = [
            line for index, line in enumerate(source_lines, start=1)
            if dialogue_id_for(shot.parent_shot_id, index) == card.dialogue_id
        ]
        if card.requires_visible_lipsync and len(source_matches) != 1:
            errors.append(f"{micro_shot_id} dialogue_id does not identify one source line")
        elif source_matches and source_matches[0].speaker_id != card.speaker_id:
            errors.append(f"{micro_shot_id} dialogue speaker does not match source line")
        if card.contact_point == "" and shot.action_code in {"press", "handoff", "hold"}:
            errors.append(f"{micro_shot_id} contact action requires exactly one contact_point")
    return errors
```

In `factory/performance_planner.py`, extend `build_performance_plan_messages()` so the JSON root contains `visual_timeline` and `performance_sheet`, with every card key listed exactly. Replace `parse_performance_plan(content, episode)` with `parse_performance_plan(content, episode, timeline)`, parse the supplied timeline unchanged, and pass its sheet through `performance_sheet_from_dict`. Add the prompt rules “one card per microshot”, “a visible spoken line maps to one non-narrator source dialogue”, “maximum two characters”, and “contact action has one actor and one contact point”.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_performance_card.py tests/test_performance_planner.py -v`

Expected: PASS; malformed speaking, contact, three-character, non-contiguous, and unbound-card data produce precise validation errors.

- [ ] **Step 5: Commit**

```bash
git add factory/performance_card.py factory/performance_planner.py tests/test_performance_card.py tests/test_performance_planner.py
git commit -m "feat: add validated performance cards"
```

### Task 2: Create immutable final dialogue-audio assets

**Files:**
- Create: `factory/dialogue_assets.py`
- Modify: `factory/local_voiceover.py:35-147, 554-813`
- Modify: `factory/shot_audio.py:10-61`
- Test: `tests/test_dialogue_assets.py`
- Test: `tests/test_local_voiceover.py`

**Interfaces:**
- Consumes: `Episode`, `PerformanceSheet`, final provider records written by `render_voiceover_preview`, and ffmpeg.
- Produces: `DialogueAudioAsset(dialogue_id: str, speaker_id: str, path: str, sha256: str, duration_seconds: float, voice_id: str)`, `DialogueAudioManifest`, `write_dialogue_audio_manifest(episode: Episode, sheet: PerformanceSheet, voiceover_audio: str | Path, output_dir: str | Path, *, provider_report_path: str | Path, command_runner: Callable[..., object] = subprocess.run, ffmpeg_bin: str = "ffmpeg") -> DialogueAudioManifest`, and `require_dialogue_audio(manifest: DialogueAudioManifest, card: PerformanceCard) -> DialogueAudioAsset`.

- [ ] **Step 1: Write failing dialogue-asset tests**

```python
from factory.dialogue_assets import DialogueAudioError, require_dialogue_audio, write_dialogue_audio_manifest


def test_manifest_records_final_asset_hash_and_rejects_speaker_mismatch(tmp_path, episode, sheet, completed_voiceover):
    manifest = write_dialogue_audio_manifest(
        episode, sheet, completed_voiceover, tmp_path / "dialogue_audio",
        command_runner=fake_ffmpeg,
    )

    asset = require_dialogue_audio(manifest, card_for("micro_001"))

    assert asset.dialogue_id == "s01.dialogue_01"
    assert len(asset.sha256) == 64
    assert asset.duration_seconds > 0
    with pytest.raises(DialogueAudioError, match="speaker does not match"):
        require_dialogue_audio(manifest, replace(card_for("micro_001"), speaker_id="yangjian"))


def test_manifest_rejects_a_visible_speaking_card_without_final_audio(tmp_path, episode, sheet):
    with pytest.raises(DialogueAudioError, match="missing final dialogue audio"):
        write_dialogue_audio_manifest(episode, sheet, tmp_path / "missing.m4a", tmp_path / "out")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_dialogue_assets.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'factory.dialogue_assets'`.

- [ ] **Step 3: Implement per-dialogue evidence and keep legacy shot cuts compatible**

```python
# factory/dialogue_assets.py
@dataclass(frozen=True)
class DialogueAudioAsset:
    dialogue_id: str
    speaker_id: str
    path: str
    sha256: str
    duration_seconds: float
    voice_id: str


def write_dialogue_audio_manifest(episode, sheet, voiceover_audio, output_dir, *, provider_report_path, command_runner=subprocess.run, ffmpeg_bin="ffmpeg"):
    report = _read_completed_provider_report(provider_report_path, voiceover_audio)
    assets = []
    for card in sheet.cards:
        if not card.requires_visible_lipsync:
            continue
        cue = _matching_completed_cue(episode, card, report)
        asset_path = _cut_cue_wav(voiceover_audio, cue, output_dir, command_runner, ffmpeg_bin)
        assets.append(DialogueAudioAsset(
            dialogue_id=card.dialogue_id, speaker_id=card.speaker_id,
            path=str(asset_path), sha256=sha256_file(asset_path),
            duration_seconds=probe_duration(asset_path), voice_id=cue["voice_id"],
        ))
    return _write_manifest(output_dir, assets)


def require_dialogue_audio(manifest, card):
    if not card.requires_visible_lipsync:
        raise DialogueAudioError(f"{card.micro_shot_id} is not a visible speech card")
    asset = manifest.by_dialogue_id.get(card.dialogue_id)
    if asset is None:
        raise DialogueAudioError(f"{card.micro_shot_id} missing final dialogue audio")
    if asset.speaker_id != card.speaker_id:
        raise DialogueAudioError(f"{card.micro_shot_id} dialogue speaker does not match")
    return asset
```

Extend the completed branch of `render_voiceover_preview()` to write a machine-readable cue table with `dialogue_id`, source text digest, speaker ID, selected voice ID, absolute start/end, and final output SHA-256. Update `write_shot_audio_assets()` to accept an optional `dialogue_manifest`; when supplied it returns only the legacy parent-shot aliases that point to the validated dialogue asset and refuses a parent-shot cut for a visible-speaking card with no manifest entry.

Extend `VoiceoverCue` with `dialogue_id: str`, assign it in `build_voiceover_cues()` by enumerating each parent shot’s dialogue using `dialogue_id_for(shot.id, dialogue_index)`, and include that value in timing rows, per-cue state, provider reports, and generated script lines. This preserves the existing `Episode` schema while giving every legacy dialogue line a stable production identity.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_dialogue_assets.py tests/test_shot_audio.py tests/test_local_voiceover.py -v`

Expected: PASS; final audio produces one hashed asset per dialogue, changed speaker/text/audio evidence invalidates the request, and old non-speaking shot-cut callers still pass.

- [ ] **Step 5: Commit**

```bash
git add factory/dialogue_assets.py factory/local_voiceover.py factory/shot_audio.py tests/test_dialogue_assets.py tests/test_local_voiceover.py
git commit -m "feat: bind final audio to dialogue cards"
```

### Task 3: Add a three-character speaking-capability gate

**Files:**
- Modify: `factory/model_bakeoff.py:24-250`
- Test: `tests/test_model_bakeoff.py`

**Interfaces:**
- Consumes: `PerformanceSheet` and the hashed `DialogueAudioManifest` from Task 2.
- Produces: `build_model_bakeoff_plan(episode: Episode, timeline: VisualTimeline, representative_character_micro_shot_ids: Sequence[str], run_dir: str | Path, *, performance_sheet: PerformanceSheet, dialogue_manifest: DialogueAudioManifest, still_micro_shot_id: str | None = None, video_models: Sequence[str] = _DEFAULT_VIDEO_MODELS, still_models: Sequence[str] = _DEFAULT_STILL_MODELS) -> dict[str, Any]`, `model_route_capability(report: Mapping[str, Any], model: str) -> Literal["speaking", "action_only", "blocked"]`, and `require_speaking_capability(report: Mapping[str, Any], model: str, micro_shot_id: str) -> None`.

- [ ] **Step 1: Write failing capability-gate tests**

```python
from factory.model_bakeoff import ModelBakeoffError, model_route_capability, require_speaking_capability


def test_bakeoff_requires_one_visible_trial_for_each_required_speaker(episode, timeline, sheet, manifest, tmp_path):
    with pytest.raises(ModelBakeoffError, match="exactly three visible-speaking trials"):
        build_model_bakeoff_plan(
            episode, timeline, ["micro_wukong", "micro_yangjian"], tmp_path,
            performance_sheet=sheet, dialogue_manifest=manifest,
        )


def test_failed_nezha_lipsync_makes_model_action_only():
    report = capability_report({"wukong": 4.5, "yangjian": 4.3, "nezha": 1.0})

    assert model_route_capability(report, "doubao-seedance-2-0") == "action_only"
    with pytest.raises(ModelBakeoffError, match="not speaking-capable"):
        require_speaking_capability(report, "doubao-seedance-2-0", "micro_nezha")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_model_bakeoff.py -k "visible_trial or action_only" -v`

Expected: FAIL because the existing bakeoff accepts two generic character shots and has no route-capability API.

- [ ] **Step 3: Implement speech trials and route capability**

```python
LIPSYNC_SPEAKERS = frozenset({"wukong", "yangjian", "nezha"})
LIPSYNC_MINIMUM_SCORE = 3.5


def model_route_capability(report, model):
    result = _video_result_for(report, model)
    trials = result["speaking_trials"]
    if all(trial["passed"] for trial in trials):
        return "speaking"
    return "action_only" if result["passed"] else "blocked"


def require_speaking_capability(report, model, micro_shot_id):
    if model_route_capability(report, model) != "speaking":
        raise ModelBakeoffError(f"{model} is not speaking-capable for {micro_shot_id}")
```

Change plan validation from “exactly two representative character microshots” to “exactly three visible-speaking cards, one each for Wukong/Yang Jian/Nezha”. Each trial must include a final matching asset from `DialogueAudioManifest`. Extend review JSON with `lipsync` in `SCORE_WEIGHTS`, trial `speaker_id`, `dialogue_id`, `audio_sha256`, and a `passed` conclusion. Preserve existing still-model handling and permit an action-only video model to remain selected for non-speaking jobs.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_model_bakeoff.py -v`

Expected: PASS; all three speaker tests must pass before `speaking` is returned, and any failed/missing trial returns `action_only` or `blocked` with evidence.

- [ ] **Step 5: Commit**

```bash
git add factory/model_bakeoff.py tests/test_model_bakeoff.py
git commit -m "feat: gate speaking shots on lip sync trials"
```

### Task 4: Compile physically grounded prompts from cards

**Files:**
- Modify: `factory/prompt_compiler.py:18-70`
- Test: `tests/test_prompt_compiler.py`
- Test: `tests/test_seedance_prompt_compiler.py`
- Test: `tests/test_h3_prompt_compiler.py`

**Interfaces:**
- Consumes: `Episode`, `MicroShot`, and `PerformanceCard` from Task 1.
- Produces: `compile_video_prompt(episode, shot, *, card: PerformanceCard, previous_scene_context: str | None = None) -> str` and `PromptCompilerError` for mismatched card/timeline pairs.

- [ ] **Step 1: Write failing prompt tests**

```python
def test_run_card_prompt_requires_weight_transfer_and_no_gliding(episode, micro_shot, run_card):
    prompt = compile_video_prompt(episode, micro_shot, card=run_card)

    assert "foot plants and bears weight" in prompt
    assert "center of mass leans forward" in prompt
    assert "decelerates into a stable stop" in prompt
    assert "no uniform gliding" in prompt
    assert "slow motion" in prompt


def test_prompt_rejects_card_for_different_microshot(episode, micro_shot, run_card):
    with pytest.raises(PromptCompilerError, match="does not belong to"):
        compile_video_prompt(episode, micro_shot, card=replace(run_card, micro_shot_id="micro_999"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_prompt_compiler.py tests/test_seedance_prompt_compiler.py tests/test_h3_prompt_compiler.py -k "weight_transfer or different_microshot" -v`

Expected: FAIL because `compile_video_prompt` has no `card` parameter and only describes a generic action.

- [ ] **Step 3: Implement card-aware prompt clauses**

```python
MOTION_REALISM = (
    "real-time natural acceleration and deceleration",
    "visible weight transfer and stable ground contact",
    "no floating, no uniform gliding, no slow motion",
    "no unexplained camera movement",
)


def compile_video_prompt(episode, shot, *, card, previous_scene_context=None):
    _require_matching_card(shot, card)
    parts = [
        episode.style,
        "vertical 9:16 cinematic motion comic",
        f"Scene: {_resolve_scene_context(episode, shot, previous_scene_context)}",
        f"Only action: {render_action(episode, shot)}",
        f"Performance beats: start {card.start_beat}; main {card.main_beat}; end {card.end_beat}",
        f"Actor and target: {card.actor_id} -> {card.target_id or 'self'}",
        *MOTION_REALISM,
    ]
    if card.requires_visible_lipsync:
        parts.append("the named speaker visibly speaks this one short line; no off-screen narration")
    return _join_prompt_parts(parts)
```

Add an explicit contact clause only when `contact_point` is non-empty: “one visible contact at `<contact_point>`; no second contact”. Keep the current provider-specific Seedance 2.0 and H3 test expectations by routing their prompt builders through the shared compiled performance clauses, rather than duplicating a separate format.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_prompt_compiler.py tests/test_seedance_prompt_compiler.py tests/test_h3_prompt_compiler.py -v`

Expected: PASS; run, stop, handoff, press, and speaking cards emit required physical beats, negatives, and visible-speaker wording while mismatched cards fail locally.

- [ ] **Step 5: Commit**

```bash
git add factory/prompt_compiler.py tests/test_prompt_compiler.py tests/test_seedance_prompt_compiler.py tests/test_h3_prompt_compiler.py
git commit -m "feat: compile physical performance prompts"
```

### Task 5: Build evidence-complete video jobs without network submission

**Files:**
- Modify: `factory/micro_video_batch.py:32-226`
- Modify: `factory/gateway_video_batch.py:48-93, 430-480, 1392-1515`
- Test: `tests/test_micro_video_batch.py`
- Test: `tests/test_gateway_video_batch.py`

**Interfaces:**
- Consumes: `PerformanceSheet`, `DialogueAudioManifest`, a capability report, approved character assets, scene-keyframe paths, and approved candidate anchors.
- Produces: `MicroVideoJob(micro_shot_id: str, model: str, prompt: str, images: tuple[str, ...], image_roles: tuple[str, ...], duration: int, resolution: str, output_path: str, report_path: str, audio_path: str = "", audio_sha256: str = "", entry_anchor_id: str = "", capability: str = "action_only")`, `build_micro_video_jobs(episode: Episode, timeline: VisualTimeline, character_assets: dict[str, Any], *, model: str, run_dir: str | Path, candidate_number: int, performance_sheet: PerformanceSheet, dialogue_manifest: DialogueAudioManifest, capability_report: Mapping[str, Any], scene_keyframes: Mapping[str, str], approved_anchors: Mapping[str, str], micro_shot_ids: Sequence[str] | None = None) -> list[MicroVideoJob]`, and a gateway clip-state record containing `reference_audio_sha256`, `entry_anchor_id`, and `capability`.

- [ ] **Step 1: Write failing evidence-chain tests**

```python
def test_visible_speech_job_requires_audio_scene_frame_anchor_and_speaking_capability(
    episode, timeline, sheet, assets, manifest, capability_report, tmp_path
):
    with pytest.raises(MicroVideoBatchError, match="micro_001 missing approved entry anchor"):
        build_micro_video_jobs(
            episode, timeline, assets, model="doubao-seedance-2-0", run_dir=tmp_path,
            candidate_number=1, performance_sheet=sheet, dialogue_manifest=manifest,
            capability_report=capability_report, scene_keyframes={"gate": "scene.png"},
            approved_anchors={},
        )


def test_visible_speech_job_passes_audio_and_image_roles_to_gateway(tmp_path, speaking_job):
    result = render_micro_video_batch([speaking_job], tmp_path, config, client_factory=FakeClient)

    assert result["jobs"][0]["reference_audio_sha256"] == speaking_job.audio_sha256
    assert result["jobs"][0]["reference_image_roles"] == ["last_frame", "first_frame", "reference_image"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_micro_video_batch.py tests/test_gateway_video_batch.py -k "speech_job or reference_audio_sha256" -v`

Expected: FAIL because current jobs only carry character images and render with `audio=None`.

- [ ] **Step 3: Implement local submission gates and request evidence**

```python
@dataclass(frozen=True)
class MicroVideoJob:
    micro_shot_id: str
    model: str
    prompt: str
    images: tuple[str, ...]
    image_roles: tuple[str, ...]
    duration: int
    resolution: str
    output_path: str
    report_path: str
    audio_path: str = ""
    audio_sha256: str = ""
    entry_anchor_id: str = ""
    capability: str = "action_only"


def _speech_requirements(card, model, manifest, capability_report, scene_keyframes, approved_anchors):
    audio = require_dialogue_audio(manifest, card)
    require_speaking_capability(capability_report, model, card.micro_shot_id)
    keyframe = _require_local_image(scene_keyframes[card.scene_keyframe_id], "scene keyframe")
    anchor = _require_local_image(approved_anchors[card.entry_anchor_id], "approved entry anchor")
    return audio, keyframe, anchor
```

For every character job, create the image ordering `(entry anchor, scene keyframe, character references)` with one `"last_frame"` role, one `"first_frame"` role, and one `"reference_image"` role for every character reference. Require both anchor and keyframe for visible speech, and require the keyframe for the first character card in a scene. Call `compile_video_prompt(episode, shot, card=card, previous_scene_context=previous_scene_context)` and make it include the motion beats. In `render_micro_video_batch`, pass `audio=job.audio_path` to `render_gateway_video_single`. Add audio SHA-256, entry-anchor ID, capability conclusion, and image roles to report sanitization and persistent gateway clip state; resume logic must compare those values before treating a prior task as reusable.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_micro_video_batch.py tests/test_gateway_video_batch.py -v`

Expected: PASS; incomplete visible-speaking requests fail before `FakeClient` is instantiated, action-only jobs remain valid without audio, and an interrupted matching request resumes rather than resubmits.

- [ ] **Step 5: Commit**

```bash
git add factory/micro_video_batch.py factory/gateway_video_batch.py tests/test_micro_video_batch.py tests/test_gateway_video_batch.py
git commit -m "feat: enforce audio and continuity video gates"
```

### Task 6: Gate selection and editing on reviewed candidates only

**Files:**
- Create: `factory/candidate_review.py`
- Modify: `factory/visual_qc.py:71-161`
- Modify: `factory/micro_preview.py:132-348`
- Modify: `factory/quality_production_runner.py:260-300`
- Test: `tests/test_candidate_review.py`
- Test: `tests/test_visual_qc.py`
- Test: `tests/test_micro_preview.py`
- Test: `tests/test_quality_production_runner.py`

**Interfaces:**
- Consumes: rendered job report evidence from Task 5, `PerformanceSheet`, and existing `VisualTimeline` ordering.
- Produces: `CandidateState`, `CandidateRecord`, `CandidateReviewManifest`, `transition_candidate(record: CandidateRecord, target: CandidateState, *, reason: str = "", evidence: Mapping[str, str] | None = None) -> CandidateRecord`, `write_candidate_review_manifest(manifest: CandidateReviewManifest, output_path: str | Path) -> Path`, and `approved_selection_from_manifest(manifest: CandidateReviewManifest, timeline: VisualTimeline) -> dict[str, Any]`.

- [ ] **Step 1: Write failing state-machine and edit-gate tests**

```python
from factory.candidate_review import CandidateReviewError, CandidateState, transition_candidate


def test_candidate_state_machine_cannot_skip_review():
    planned = make_record("micro_001", CandidateState.PLANNED)

    with pytest.raises(CandidateReviewError, match="planned cannot transition to approved"):
        transition_candidate(planned, CandidateState.APPROVED)


def test_preview_refuses_an_unapproved_mp4(tmp_path, episode, timeline, bakeoff_report):
    selection = {"micro_001": {"kind": "video", "path": "candidates/micro_001.mp4"}}
    write_json(tmp_path / "visual_selection.json", selection)
    write_candidate_manifest(tmp_path / "candidate_review.json", state="review_required")

    with pytest.raises(MicroPreviewError, match="not approved"):
        render_micro_preview_video(
            episode, timeline_path=tmp_path / "visual_timeline.json",
            selection_path=tmp_path / "visual_selection.json",
            bakeoff_report_path=tmp_path / "model_bakeoff_report.json",
            run_dir=tmp_path, output_path=tmp_path / "preview.mp4",
            report_path=tmp_path / "preview_report.json",
        )


def test_visible_speech_review_requires_speaker_and_lipsync_evidence(qc_report, visible_speech_card):
    review = make_visual_review(lipsync_score=None, speaker_visible=None)

    with pytest.raises(VisualQCError, match="visible speech requires speaker_visible and lipsync_score"):
        record_visual_review(qc_report, review, expected_micro_shot=micro_shot, performance_card=visible_speech_card)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_candidate_review.py tests/test_visual_qc.py tests/test_micro_preview.py -k "skip_review or unapproved or visible_speech_review" -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'factory.candidate_review'`, then fail because the current preview accepts a directory-derived selection.

- [ ] **Step 3: Implement transition evidence and approved selection**

```python
class CandidateState(StrEnum):
    PLANNED = "planned"
    AUDIO_READY = "audio_ready"
    SUBMITTED = "submitted"
    RENDERED = "rendered"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


_ALLOWED = {
    CandidateState.PLANNED: {CandidateState.AUDIO_READY, CandidateState.BLOCKED},
    CandidateState.AUDIO_READY: {CandidateState.SUBMITTED, CandidateState.BLOCKED},
    CandidateState.SUBMITTED: {CandidateState.RENDERED, CandidateState.BLOCKED},
    CandidateState.RENDERED: {CandidateState.REVIEW_REQUIRED, CandidateState.BLOCKED},
    CandidateState.REVIEW_REQUIRED: {CandidateState.APPROVED, CandidateState.REJECTED, CandidateState.BLOCKED},
    CandidateState.REJECTED: {CandidateState.PLANNED, CandidateState.BLOCKED},
}
```

Extend `VisualReview` with `speaker_visible: bool | None`, `lipsync_score: float | None`, and `audio_sha256: str`; extend `record_visual_review(..., performance_card: PerformanceCard | None = None)` so a visible-speaking card rejects a review unless the speaker is visible, `lipsync_score` is 0–5 and at least `LIPSYNC_MINIMUM_SCORE`, and the review audio hash equals its dialogue manifest asset. Reuse `analyze_visual_candidate()`'s sampled first/middle/last frames as immutable evidence instead of extracting a second set of images.

`CandidateRecord` contains `micro_shot_id`, `candidate_path`, `candidate_sha256`, `state`, `audio_sha256`, `entry_anchor_id`, `visual_qc_report_path`, `reason`, and `evidence`. Require review evidence keys `first_frame`, `middle_frame`, `last_frame`, and `review_note`; for visible-speaking cards also require `audio_sha256`, `speaker_visible`, and `lipsync_score`. `approved_selection_from_manifest()` must verify that every selected microshot is `approved`, has an existing unchanged MP4, matches the manifest audio/anchor hashes, and appears once in `VisualTimeline` order. Update `select_micro_sources()` and `render_micro_preview_video()` to require `candidate_review.json`; update `write_quality_visual_selection()` to accept the same approved selection rather than copying arbitrary selection JSON.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest tests/test_candidate_review.py tests/test_visual_qc.py tests/test_micro_preview.py tests/test_quality_production_runner.py -v`

Expected: PASS; only legal transitions work, rejected/blocked/unreviewed candidates cannot edit, and a missing approved story slot produces a clear missing-slot report.

- [ ] **Step 5: Commit**

```bash
git add factory/candidate_review.py factory/visual_qc.py factory/micro_preview.py factory/quality_production_runner.py tests/test_candidate_review.py tests/test_visual_qc.py tests/test_micro_preview.py tests/test_quality_production_runner.py
git commit -m "feat: edit only approved reviewed candidates"
```

### Task 7: Add a local end-to-end preflight and protect current outputs

**Files:**
- Create: `factory/audio_first_preflight.py`
- Test: `tests/test_audio_first_preflight.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `episode.json`, `visual_timeline.json`, `performance_sheet.json`, `dialogue_audio_manifest.json`, `model_bakeoff_report.json`, scene-keyframe map, approved-anchor map, and candidate-review manifest within a run directory.
- Produces: `run_audio_first_preflight(run_dir: str | Path, *, model: str) -> dict[str, Any]` with `success: bool`, `planned_count: int`, `blocked_count: int`, `errors: list[str]`, and `preflight_report.json`; it never calls `GatewayVideoClient`.

- [ ] **Step 1: Write the failing preflight tests**

```python
from factory.audio_first_preflight import run_audio_first_preflight


def test_preflight_is_local_and_blocks_visible_speech_without_a_speaking_model(run_fixture, monkeypatch):
    monkeypatch.setattr("factory.micro_video_batch.GatewayVideoClient", lambda *_: pytest.fail("network client created"))

    report = run_audio_first_preflight(run_fixture, model="doubao-seedance-2-0")

    assert report["success"] is False
    assert report["blocked_count"] == 1
    assert "not speaking-capable" in report["errors"][0]


def test_preflight_never_overwrites_delivered_episode_v3(delivered_episode_v3, run_fixture):
    original = delivered_episode_v3.read_bytes()

    run_audio_first_preflight(run_fixture, model="doubao-seedance-2-0")

    assert delivered_episode_v3.read_bytes() == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_audio_first_preflight.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'factory.audio_first_preflight'`.

- [ ] **Step 3: Implement an artifact-only preflight and document the handoff**

```python
def run_audio_first_preflight(run_dir, *, model):
    root = Path(run_dir).resolve()
    episode, timeline, sheet = _load_core_artifacts(root)
    manifest = read_dialogue_audio_manifest(root / "dialogue_audio_manifest.json")
    capability_report = _read_json(root / "model_bakeoff_report.json")
    jobs = build_micro_video_jobs(
        episode, timeline, _read_json(root / "character_assets.json"), model=model,
        run_dir=root, candidate_number=1, performance_sheet=sheet,
        dialogue_manifest=manifest, capability_report=capability_report,
        scene_keyframes=_read_json(root / "scene_keyframes.json"),
        approved_anchors=_read_json(root / "approved_anchors.json"),
    )
    return _write_preflight_report(root, jobs=jobs, errors=[])
```

Catch only validation exceptions and serialize each as a blocked local planning error; do not instantiate a gateway client or call `render_micro_video_batch`. In `README.md`, add an “Audio-first microshot preflight” section with the exact artifact list, the preflight command, the rule that a paid request is a separate later step, and the statement that old Episode 1 exports are never output targets.

- [ ] **Step 4: Run focused tests and the full local suite**

Run: `pytest tests/test_audio_first_preflight.py -v && pytest -q`

Expected: PASS; the preflight emits only local reports, catches missing audio/capability/anchor evidence before a paid call, and the full suite remains green.

- [ ] **Step 5: Commit**

```bash
git add factory/audio_first_preflight.py tests/test_audio_first_preflight.py README.md
git commit -m "feat: add local audio-first production preflight"
```

## Final verification checklist

- [ ] Run `git diff --check`.
- [ ] Run `pytest -q`.
- [ ] Run one fixture-backed `run_audio_first_preflight()` with a speaking-capable report and verify it produces planned jobs without constructing a gateway client.
- [ ] Run one fixture-backed preflight with an action-only report and verify visible-speaking cards are `blocked` while non-speaking action cards remain planned.
- [ ] Confirm no command or test touches `/Users/Admin1/Documents/ChatGPT/story_factory/runs/heavenly-blind-box-pilot/longform/edits/episode_01/episode_01_picture_cut_v1.mp4`, `episode_01_role_dialogue_v2.mp4`, or `episode_01_role_dialogue_v3.mp4`.
