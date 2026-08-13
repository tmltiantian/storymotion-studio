# Motion Comic Quality Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable micro-shot production pipeline that writes detailed character performances, selects a production video model through a paid bakeoff, rejects visually broken clips, renders deliberate motion-comic cadence, and iteratively delivers a corrected version of `sample_episode`.

**Architecture:** Keep `Episode.shots` as the story, voice, and subtitle timeline, then add a separate `VisualTimeline` whose micro-shots can be shorter and more numerous. Compile every micro-shot into a single-action production prompt, generate versioned candidates through the existing gateway client, require machine and visual QC before selection, and let a dedicated renderer assemble only approved ranges into the existing OpenMontage voice/subtitle finalization path.

**Tech Stack:** Python 3.12 dataclasses and JSON, existing `GatewayTextClient` and `GatewayVideoClient`, FFmpeg/ffprobe, Tesseract `chi_sim+eng`, pytest, Ruff, OpenMontage/Remotion.

## Global Constraints

- Formal character video candidates are `doubao-seedance-2-0` and `doubao-seedance-1-5-pro`; `doubao-seedance-2-0-fast` remains draft-only.
- Production video requests use 1080p, 9:16, no generated audio, production-ready role references, and one scene plus one primary action per prompt.
- Environment/object still candidates are `doubao-seedream-4-5` and `gpt-image-2`; they must not generate character close-ups because the current image route has no role-reference contract.
- The delivery stream remains 1080x1920, 30 fps, H.264/AAC. Micro-shots expose motion at 6-10 fps through held delivery frames.
- Default transitions are hard cuts. A 2-4 frame black transition is allowed only for an explicit time or location jump.
- Source text, subtitles, watermarks, extra characters, duplicate faces/limbs, in-model cuts, dissolves, zooms, and orbiting cameras are hard failures.
- This upgrade does not implement phoneme-level lip sync; dialogue uses profile, over-shoulder, hand insert, and listener-reaction coverage instead of long frontal mouth close-ups.
- A micro-shot may submit at most three paid candidates per model. After the third failure, revise the shot or prompt before another paid request.
- Batch generation fails closed until a bakeoff report selects a model with score at least 80 and no hard failures.
- Preserve the existing zero-overlap voice schedule and measured subtitle boundaries.
- Do not delete or overwrite existing source clips. New candidates live under `runs/<project>/micro_clips/`.

---

### Task 1: Visual Timeline Schema and Validation

**Files:**
- Create: `factory/visual_timeline.py`
- Create: `tests/test_visual_timeline.py`
- Modify: `factory/project_runner.py`

**Interfaces:**
- Consumes: `factory.schema.Episode`, `factory.schema.Character`, and `factory.schema.Shot`.
- Produces: `MicroShot`, `VisualTimeline`, `visual_timeline_to_dict()`, `visual_timeline_from_dict()`, `validate_visual_timeline()`, and `write_visual_timeline()`.

- [ ] **Step 1: Write failing schema and invariant tests**

```python
from factory.visual_timeline import (
    MicroShot,
    VisualTimeline,
    validate_visual_timeline,
    visual_timeline_from_dict,
    visual_timeline_to_dict,
)


def test_visual_timeline_round_trips_and_covers_parent_duration(sample_episode):
    timeline = VisualTimeline(
        project_id=sample_episode.project_id,
        micro_shots=(
            MicroShot(
                id="micro_001",
                index=1,
                parent_shot_id="shot_001",
                scene_context="Shop",
                time_context="source-unspecified",
                purpose="reaction",
                character_ids=(sample_episode.characters[0].id,),
                emotion_start="guarded",
                emotion_end="alarmed",
                emotion_intensity=4,
                gaze="from the envelope to the shopkeeper",
                pose_start="shoulders still, right hand beside body",
                pose_end="right hand stops above the envelope",
                action_actor_id=sample_episode.characters[0].id,
                action_code="reach",
                action_target="envelope",
                camera_mode="locked",
                source_duration_seconds=5,
                timeline_duration_seconds=sample_episode.shots[0].duration_seconds,
                entry_cut="hard_cut",
                exit_cut="hard_cut",
                negative_constraints=("no_text", "no_scene_change"),
                cadence_fps=8,
            ),
        ),
    )
    assert validate_visual_timeline(timeline, sample_episode) == []
    assert visual_timeline_from_dict(visual_timeline_to_dict(timeline)) == timeline


def test_visual_timeline_rejects_wrong_parent_sum_and_unknown_character(sample_episode):
    payload = visual_timeline_to_dict(valid_timeline(sample_episode))
    payload["micro_shots"][0]["timeline_duration_seconds"] = 0.5
    payload["micro_shots"][0]["character_ids"] = ["char_missing"]
    timeline = visual_timeline_from_dict(payload)
    errors = validate_visual_timeline(timeline, sample_episode)
    assert any("duration" in error for error in errors)
    assert any("char_missing" in error for error in errors)
```

- [ ] **Step 2: Run the new tests and confirm the module is missing**

Run: `.venv/bin/pytest -q tests/test_visual_timeline.py`

Expected: collection fails with `ModuleNotFoundError: No module named 'factory.visual_timeline'`.

- [ ] **Step 3: Implement immutable schema, JSON conversion, and exact validation**

```python
VISUAL_TIMELINE_SCHEMA = "motion-comic-factory.visual-timeline.v3"
CAMERA_MODES = {"locked", "micro_pan", "object_insert"}
CUT_MODES = {"hard_cut", "match_cut", "time_jump_black"}


@dataclass(frozen=True)
class MicroShot:
    id: str
    index: int
    parent_shot_id: str
    scene_context: str
    time_context: str
    purpose: str
    character_ids: tuple[str, ...]
    emotion_start: str
    emotion_end: str
    emotion_intensity: int
    gaze: str
    pose_start: str
    pose_end: str
    action_actor_id: str
    action_code: str
    action_target: str
    camera_mode: str
    source_duration_seconds: int
    timeline_duration_seconds: float
    entry_cut: str
    exit_cut: str
    negative_constraints: tuple[str, ...]
    cadence_fps: int


@dataclass(frozen=True)
class VisualTimeline:
    project_id: str
    micro_shots: tuple[MicroShot, ...]
    schema_version: str = VISUAL_TIMELINE_SCHEMA


def validate_visual_timeline(timeline: VisualTimeline, episode: Episode) -> list[str]:
    errors: list[str] = []
    character_ids = {item.id for item in episode.characters}
    parent_by_id = {item.id: item for item in episode.shots}
    duration_by_parent = {item.id: 0.0 for item in episode.shots}
    if timeline.project_id != episode.project_id:
        errors.append("visual timeline project_id does not match episode")
    if [item.index for item in timeline.micro_shots] != list(
        range(1, len(timeline.micro_shots) + 1)
    ):
        errors.append("micro-shot indexes must be contiguous from 1")
    for item in timeline.micro_shots:
        if item.parent_shot_id not in parent_by_id:
            errors.append(f"{item.id} has unknown parent {item.parent_shot_id}")
            continue
        duration_by_parent[item.parent_shot_id] += item.timeline_duration_seconds
        for character_id in item.character_ids:
            if character_id not in character_ids:
                errors.append(f"{item.id} has unknown character {character_id}")
        if item.camera_mode not in CAMERA_MODES:
            errors.append(f"{item.id} has invalid camera_mode")
        if item.entry_cut not in CUT_MODES or item.exit_cut not in CUT_MODES:
            errors.append(f"{item.id} has invalid cut mode")
        if not 1 <= item.emotion_intensity <= 5:
            errors.append(f"{item.id} emotion_intensity must be 1-5")
        if not 1 <= item.source_duration_seconds <= 15:
            errors.append(f"{item.id} source duration must be 1-15 seconds")
        if not 1 <= item.cadence_fps <= 10:
            errors.append(f"{item.id} cadence_fps must be 1-10")
        if item.timeline_duration_seconds <= 0:
            errors.append(f"{item.id} timeline duration must be positive")
    for parent_id, duration in duration_by_parent.items():
        expected = parent_by_id[parent_id].duration_seconds
        if abs(duration - expected) > 0.001:
            errors.append(
                f"{parent_id} visual duration {duration:.3f} does not match {expected:.3f}"
            )
    return errors
```

Add `visual_timeline.json` to `_ROLLBACK_RUN_ARTIFACTS` so a failed project rerun preserves the last accepted visual plan.

- [ ] **Step 4: Run focused and schema tests**

Run: `.venv/bin/pytest -q tests/test_visual_timeline.py tests/test_schema.py tests/test_project_runner.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the schema unit**

```bash
git add factory/visual_timeline.py factory/project_runner.py tests/test_visual_timeline.py
git commit -m "feat: add validated micro-shot timeline"
```

### Task 2: Performance Planner and Production Prompt Compiler

**Files:**
- Create: `factory/performance_planner.py`
- Create: `factory/prompt_compiler.py`
- Create: `tests/test_performance_planner.py`
- Create: `tests/test_prompt_compiler.py`

**Interfaces:**
- Consumes: `VisualTimeline`, `MicroShot`, `Episode`, and `GatewayTextClient.chat()`.
- Produces: `build_performance_plan_messages(episode)`, `parse_performance_plan(content, episode)`, `generate_performance_plan(...)`, `compile_video_prompt(...)`, and `compile_still_prompt(...)`.

- [ ] **Step 1: Write failing parser and prompt tests**

```python
def test_parse_performance_plan_rejects_unmentioned_character(sample_episode):
    payload = performance_payload(sample_episode)
    payload["micro_shots"][0]["character_ids"] = [sample_episode.characters[1].id]
    payload["micro_shots"][0]["action_target"] = "street"
    with pytest.raises(PerformancePlanError, match="character"):
        parse_performance_plan(json.dumps(payload, ensure_ascii=False), sample_episode)


def test_compile_video_prompt_names_only_present_characters(sample_episode):
    shot = micro_shot(character_ids=(sample_episode.characters[0].id,))
    prompt = compile_video_prompt(sample_episode, shot)
    assert sample_episode.characters[0].name in prompt
    assert sample_episode.characters[1].name not in prompt
    assert "single continuous shot" in prompt
    assert "no text, subtitles, watermark or logo" in prompt
    assert "locked camera" in prompt
```

- [ ] **Step 2: Run the tests and confirm missing modules**

Run: `.venv/bin/pytest -q tests/test_performance_planner.py tests/test_prompt_compiler.py`

Expected: collection fails for the two new modules.

- [ ] **Step 3: Implement strict JSON planning with no prose fallback**

```python
def build_performance_plan_messages(episode: Episode) -> list[dict[str, str]]:
    source = episode_to_dict(episode)
    return [
        {
            "role": "system",
            "content": (
                "You are a motion-comic performance director. Return one JSON object only. "
                "Split every parent shot into 2-4 second micro-shots. Each micro-shot has one "
                "location, one composition, one primary action, exact on-screen character IDs, "
                "observable facial acting, gaze, start/end pose, locked camera by default, and "
                "timeline durations whose sum exactly equals each parent duration. Never invent "
                "dialogue, characters, locations, subtitles, transitions, or camera movement. "
                "The root keys must be schema_version, project_id, and micro_shots. Every item "
                "in micro_shots must contain id, index, parent_shot_id, scene_context, time_context, purpose, character_ids, "
                "emotion_start, emotion_end, emotion_intensity, gaze, pose_start, pose_end, "
                "action_actor_id, action_code, action_target, camera_mode, source_duration_seconds, timeline_duration_seconds, "
                "entry_cut, exit_cut, negative_constraints, and cadence_fps."
            ),
        },
        {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
    ]


def generate_performance_plan(
    episode: Episode,
    client: GatewayTextClient,
    *,
    allow_network: bool,
) -> tuple[VisualTimeline, dict[str, Any]]:
    result = client.chat(
        build_performance_plan_messages(episode),
        response_format={"type": "json_object"},
        allow_network=allow_network,
    )
    timeline = parse_performance_plan(result.content, episode)
    return timeline, result.to_report()
```

`parse_performance_plan()` must strip no Markdown fences, accept no partial object, convert through `visual_timeline_from_dict()`, and enforce `validate_visual_timeline()`. The shared micro-shot validator, used by parsing and both direct compilers, rejects duplicate Episode character names and any character absent from the parent action/dialogue, except a `reaction` containing another explicitly speaking on-screen character or an exact-two-main-character collective pronoun naming both characters.

The v3 planner instruction must state the exact root and micro-shot keys, real JSON scalar/container types, every action/negative/camera/cut/purpose enum, both `source-unspecified` and `previous-shot-continuity`, all numeric ranges, contiguous and unique identity rules, and exact parent-duration sums. Its compact JSON example must be a validator-safe full-Episode timeline; when that cannot be generated without invention, omit populated JSON and provide only a non-JSON type shape. Generic numbered scene titles are not locations. Continuity is legal only for a parent without an explicit location and must resolve through an actual prior micro-shot to a concrete source scene. `time_context` derives only from current-scene expressions in `scene_title` and `action`, never `visual_prompt`. All free fields reject multiline/multi-sentence prose, production directives, invented people, visible-text semantics, and instruction-override language.

- [ ] **Step 4: Implement exact prompt ordering and hard constraints**

```python
VIDEO_HARD_CONSTRAINTS = (
    "single continuous shot",
    "fixed location and composition",
    "no cuts, dissolves, scene changes, zoom, dolly, orbit or camera shake",
    "no extra people, duplicate face, duplicate body or malformed hands",
    "no text, subtitles, watermark or logo",
)


def compile_video_prompt(episode: Episode, shot: MicroShot) -> str:
    characters = {item.id: item for item in episode.characters}
    present = [characters[item] for item in shot.character_ids]
    camera = {
        "locked": "locked camera",
        "micro_pan": "one restrained lateral move under two percent of frame width",
        "object_insert": "locked object insert",
    }[shot.camera_mode]
    parts = [
        episode.style,
        "vertical 9:16 cinematic motion comic",
        "On-screen characters: " + (", ".join(item.name for item in present) or "none"),
        f"Opening expression and pose: {shot.emotion_start}; {shot.pose_start}",
        f"Gaze: {shot.gaze}",
        f"Only action: {render_action(episode, shot)}",
        f"Ending expression and pose: {shot.emotion_end}; {shot.pose_end}",
        camera,
        *VIDEO_HARD_CONSTRAINTS,
        *optional_negative_phrases(shot.negative_constraints),
    ]
    return ". ".join(item.strip(" .") for item in parts if item.strip()) + "."


def compile_still_prompt(episode: Episode, shot: MicroShot) -> str:
    if shot.character_ids:
        raise PromptCompilerError(
            f"{shot.id} contains characters and cannot use the reference-free still route."
        )
    parts = [
        episode.style,
        "vertical 9:16 cinematic motion-comic keyframe",
        f"Purpose: {shot.purpose}",
        f"Composition and only visible event: {render_action(episode, shot)}",
        "leave the lower subtitle-safe area visually quiet",
        "no people, face, body, hand, text, subtitle, watermark or logo",
    ]
    return ". ".join(item.strip(" .") for item in parts if item.strip()) + "."
```

Both compilers validate Episode identity integrity, direct `MicroShot` runtime types, and the 2-4 second timeline range before lookup or rendering. A continuity micro-shot requires keyword-only `previous_scene_context`; it must be concrete, injection-free, and consistent with prior source locations where those are resolvable. The compilers emit one language-selected action clause and order output as style/aspect, scene, time, exact on-screen characters, opening composition/expression/pose, only action, ending expression/gaze/pose, camera, optional canonical negatives, then the immutable hard tail. `object_insert` is character-free only; still prompts remain image-only.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_performance_planner.py tests/test_prompt_compiler.py tests/test_gateway_text.py`

Expected: all selected tests pass and no network call occurs in tests.

- [ ] **Step 6: Commit the planning unit**

```bash
git add factory/performance_planner.py factory/prompt_compiler.py tests/test_performance_planner.py tests/test_prompt_compiler.py
git commit -m "feat: compile detailed performance prompts"
```

### Task 3: Versioned Micro-Shot Video Jobs

**Files:**
- Create: `factory/micro_video_batch.py`
- Create: `tests/test_micro_video_batch.py`
- Modify: `factory/gateway_video_batch.py`

**Interfaces:**
- Consumes: `VisualTimeline`, `compile_video_prompt()`, production character manifest, `GatewayVideoClient`, and existing resumable `render_gateway_video_single()`.
- Produces: `MicroVideoJob`, `build_micro_video_jobs()`, `render_micro_video_batch()`, candidate reports, and deterministic candidate paths.

- [ ] **Step 1: Write failing job and paid-attempt-cap tests**

```python
def test_build_micro_jobs_uses_exact_references_and_versioned_paths(
    sample_episode, visual_timeline, confirmed_assets, tmp_path
):
    jobs = build_micro_video_jobs(
        sample_episode,
        visual_timeline,
        confirmed_assets,
        model="doubao-seedance-2-0",
        run_dir=tmp_path,
        candidate_number=1,
    )
    assert jobs[0].images == (confirmed_assets["characters"][0]["reference_image_path"],)
    assert jobs[0].output_path.endswith(
        "micro_clips/micro_001/doubao-seedance-2-0/candidate_001.mp4"
    )
    assert jobs[0].resolution == "1080p"
    assert jobs[0].generate_audio is False


def test_build_micro_jobs_rejects_fourth_paid_candidate(sample_episode, visual_timeline, tmp_path):
    with pytest.raises(MicroVideoBatchError, match="at most 3"):
        build_micro_video_jobs(
            sample_episode,
            visual_timeline,
            confirmed_assets_payload(tmp_path),
            model="doubao-seedance-2-0",
            run_dir=tmp_path,
            candidate_number=4,
        )
```

- [ ] **Step 2: Run tests and confirm missing module**

Run: `.venv/bin/pytest -q tests/test_micro_video_batch.py`

Expected: collection fails for `factory.micro_video_batch`.

- [ ] **Step 3: Implement job construction and fail-closed model rules**

```python
PRODUCTION_VIDEO_MODELS = {
    "doubao-seedance-2-0",
    "doubao-seedance-1-5-pro",
}


@dataclass(frozen=True)
class MicroVideoJob:
    micro_shot_id: str
    model: str
    prompt: str
    images: tuple[str, ...]
    duration: int
    resolution: str
    output_path: str
    report_path: str
    generate_audio: bool = False


def candidate_output_path(
    run_dir: Path,
    micro_shot_id: str,
    model: str,
    candidate_number: int,
) -> Path:
    if not 1 <= candidate_number <= 3:
        raise MicroVideoBatchError("A micro-shot may submit at most 3 paid candidates per model.")
    return (
        run_dir
        / "micro_clips"
        / micro_shot_id
        / model
        / f"candidate_{candidate_number:03d}.mp4"
    )
```

Reject non-production models, missing production-ready assets, absent character references, duplicate references, empty prompts, any output outside `run_dir/micro_clips`, and any duration above 15 seconds before creating a network request.

- [ ] **Step 4: Reuse resumable single-job execution without exposing secrets**

For each job, construct a client with that job's model and call:

```python
render_gateway_video_single(
    job.prompt,
    job.output_path,
    client,
    job.report_path,
    images=job.images,
    duration=job.duration,
    ratio="9:16",
    resolution=job.resolution,
    generate_audio=False,
    allow_network=allow_network,
    overwrite=overwrite,
)
```

Write one atomic `micro_video_batch.json` that records planned, completed, resumed, skipped, and failed counts. Reports may include prompt text and reference filenames but never API keys, signed download URLs, or inline image data.

- [ ] **Step 5: Run focused and gateway regression tests**

Run: `.venv/bin/pytest -q tests/test_micro_video_batch.py tests/test_gateway_video.py tests/test_gateway_video_batch.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the generation unit**

```bash
git add factory/micro_video_batch.py factory/gateway_video_batch.py tests/test_micro_video_batch.py
git commit -m "feat: add resumable micro-shot video jobs"
```

### Task 4: Environment and Object Still Jobs

**Files:**
- Create: `factory/micro_still_batch.py`
- Create: `tests/test_micro_still_batch.py`
- Modify: `factory/gateway_image.py`

**Interfaces:**
- Consumes: micro-shots with no character IDs, `compile_still_prompt()`, `GatewayImageClient`, and a selected still model.
- Produces: versioned PNG candidates under `runs/<project>/micro_stills/` and `micro_still_batch.json`.

- [ ] **Step 1: Write failing routing and reference-safety tests**

```python
def test_build_still_jobs_accepts_only_character_free_inserts(
    sample_episode, visual_timeline, tmp_path
):
    jobs = build_micro_still_jobs(
        sample_episode,
        visual_timeline,
        model="doubao-seedream-4-5",
        run_dir=tmp_path,
        candidate_number=1,
    )
    assert all(not job.character_ids for job in jobs)
    assert jobs[0].output_path.endswith(
        "micro_stills/micro_003/doubao-seedream-4-5/candidate_001.png"
    )


def test_build_still_jobs_rejects_character_shot(
    sample_episode, visual_timeline_with_character, tmp_path
):
    with pytest.raises(MicroStillBatchError, match="character reference"):
        build_micro_still_jobs(
            sample_episode,
            visual_timeline_with_character,
            model="gpt-image-2",
            run_dir=tmp_path,
            candidate_number=1,
            micro_shot_ids=["micro_001"],
        )
```

- [ ] **Step 2: Run tests and confirm the module is missing**

Run: `.venv/bin/pytest -q tests/test_micro_still_batch.py`

Expected: collection fails for `factory.micro_still_batch`.

- [ ] **Step 3: Implement exact still routing and candidate limits**

```python
PRODUCTION_STILL_MODELS = {
    "doubao-seedream-4-5",
    "gpt-image-2",
}


@dataclass(frozen=True)
class MicroStillJob:
    micro_shot_id: str
    model: str
    prompt: str
    character_ids: tuple[str, ...]
    size: str
    output_path: str


def build_micro_still_jobs(
    episode: Episode,
    timeline: VisualTimeline,
    *,
    model: str,
    run_dir: Path,
    candidate_number: int,
    micro_shot_ids: Sequence[str] | None = None,
) -> list[MicroStillJob]:
    if model not in PRODUCTION_STILL_MODELS:
        raise MicroStillBatchError(f"Unsupported production still model: {model}")
    if not 1 <= candidate_number <= 3:
        raise MicroStillBatchError("A micro-shot may submit at most 3 paid still candidates.")
    selected_ids = set(micro_shot_ids or ())
    jobs: list[MicroStillJob] = []
    for shot in timeline.micro_shots:
        if selected_ids and shot.id not in selected_ids:
            continue
        if shot.character_ids:
            if shot.id in selected_ids:
                raise MicroStillBatchError(
                    f"{shot.id} requires a character reference and cannot use the still route."
                )
            continue
        if shot.camera_mode != "object_insert" and shot.purpose not in {"establishing", "object"}:
            continue
        output = run_dir / "micro_stills" / shot.id / model / f"candidate_{candidate_number:03d}.png"
        jobs.append(
            MicroStillJob(
                micro_shot_id=shot.id,
                model=model,
                prompt=compile_still_prompt(episode, shot),
                character_ids=(),
                size="1024x1536",
                output_path=str(output),
            )
        )
    return jobs
```

- [ ] **Step 4: Generate without pretending references are supported**

For each job, construct `GatewayImageClient` with the job model and call `generate()` with `ref_image_path=None`, `ref_image_paths=None`, `n=1`, and `size="1024x1536"`. Validate PNG/JPEG/WebP signatures after download, write output atomically through a sibling temporary path, and persist completed/failed counts. A request is made only when the command carries `--enable-live`.

- [ ] **Step 5: Run focused image tests**

Run: `.venv/bin/pytest -q tests/test_micro_still_batch.py tests/test_gateway_image.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the still-generation unit**

```bash
git add factory/micro_still_batch.py factory/gateway_image.py tests/test_micro_still_batch.py
git commit -m "feat: generate character-free motion-comic stills"
```

### Task 5: Paid Model Bakeoff and Production Gate

**Files:**
- Create: `factory/model_bakeoff.py`
- Create: `tests/test_model_bakeoff.py`
- Modify: `config/factory.config.json`
- Modify: `.env.example`

**Interfaces:**
- Consumes: two representative character `MicroShot` IDs, one character-free environment/prop micro-shot, video/still candidate reports, and a human/agent review JSON.
- Produces: `model_bakeoff_plan.json`, `model_bakeoff_review.json`, `model_bakeoff_report.json`, `require_selected_production_model()`, and `require_selected_still_model()`.

- [ ] **Step 1: Write failing weighted-score and hard-failure tests**

```python
def test_finalize_bakeoff_selects_highest_passing_model(bakeoff_plan):
    reviews = {
        "doubao-seedance-2-0": passing_reviews(total=88),
        "doubao-seedance-1-5-pro": passing_reviews(total=82),
    }
    report = finalize_bakeoff(bakeoff_plan, reviews)
    assert report["selected_model"] == "doubao-seedance-2-0"
    assert report["production_ready"] is True


def test_finalize_bakeoff_rejects_hard_failure_even_above_80(bakeoff_plan):
    reviews = {
        "doubao-seedance-2-0": passing_reviews(total=95, hard_failures=["duplicate_face"]),
        "doubao-seedance-1-5-pro": passing_reviews(total=79),
    }
    report = finalize_bakeoff(bakeoff_plan, reviews)
    assert report["selected_model"] == ""
    assert report["production_ready"] is False


def test_finalize_bakeoff_selects_character_free_still_model(bakeoff_plan):
    reviews = complete_bakeoff_reviews(
        video_winner="doubao-seedance-2-0",
        still_scores={"doubao-seedream-4-5": 91, "gpt-image-2": 84},
    )
    report = finalize_bakeoff(bakeoff_plan, reviews)
    assert report["selected_still_model"] == "doubao-seedream-4-5"
    assert require_selected_still_model(report) == "doubao-seedream-4-5"
```

- [ ] **Step 2: Run tests and confirm missing module**

Run: `.venv/bin/pytest -q tests/test_model_bakeoff.py`

Expected: collection fails for `factory.model_bakeoff`.

- [ ] **Step 3: Implement exact scoring and gate**

```python
SCORE_WEIGHTS = {
    "identity": 25,
    "expression": 20,
    "anatomy": 15,
    "continuity": 15,
    "semantics": 10,
    "motion": 10,
    "clean_frame": 5,
}
HARD_FAILURES = {
    "identity_swap",
    "extra_character",
    "duplicate_face",
    "severe_anatomy",
    "embedded_text",
    "in_model_cut",
}


def weighted_score(scores: Mapping[str, float]) -> float:
    missing = set(SCORE_WEIGHTS) - set(scores)
    if missing:
        raise ModelBakeoffError(f"Missing score fields: {', '.join(sorted(missing))}")
    return round(
        sum(float(scores[key]) / 5.0 * weight for key, weight in SCORE_WEIGHTS.items()),
        2,
    )


def require_selected_production_model(report: Mapping[str, Any]) -> str:
    model = str(report.get("selected_model") or "").strip()
    if not report.get("production_ready") or model not in PRODUCTION_VIDEO_MODELS:
        raise ModelBakeoffError("No production video model has passed the bakeoff gate.")
    return model


def require_selected_still_model(report: Mapping[str, Any]) -> str:
    model = str(report.get("selected_still_model") or "").strip()
    if model not in PRODUCTION_STILL_MODELS:
        raise ModelBakeoffError("No production still model has passed the bakeoff gate.")
    return model
```

Each score field is 0-5. A video model passes only when both representative character shots exist, both were reviewed, the aggregate weighted score is at least 80, and no hard failure appears. A still model passes when its character-free environment/prop candidate scores at least 80 with no embedded text, broken geometry, composition mismatch, or style mismatch. Projects with no still-routed micro-shots do not require a selected still model.

- [ ] **Step 4: Add explicit defaults**

Add to `config/factory.config.json`:

```json
"qualityUpgrade": {
  "draftVideoModel": "doubao-seedance-2-0-fast",
  "productionVideoModels": [
    "doubao-seedance-2-0",
    "doubao-seedance-1-5-pro"
  ],
  "productionStillModels": [
    "doubao-seedream-4-5",
    "gpt-image-2"
  ],
  "productionResolution": "1080p",
  "productionStillSize": "1024x1536",
  "maxPaidCandidatesPerModel": 3,
  "minimumBakeoffScore": 80
}
```

Add the same model names as commented examples in `.env.example`; do not add credentials.

- [ ] **Step 5: Run tests and config regressions**

Run: `.venv/bin/pytest -q tests/test_model_bakeoff.py tests/test_provider_profile.py tests/test_openmontage_adapter.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the bakeoff unit**

```bash
git add factory/model_bakeoff.py tests/test_model_bakeoff.py config/factory.config.json .env.example
git commit -m "feat: gate production video model selection"
```

### Task 6: Automated Clip QC and Review Records

**Files:**
- Create: `factory/visual_qc.py`
- Create: `tests/test_visual_qc.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: candidate MP4, expected `MicroShot`, reference-image labels, FFmpeg, ffprobe, and Tesseract.
- Produces: contact sheet, OCR evidence, motion/cut evidence, `visual_qc.json`, `record_visual_review()`, and `require_passed_visual_qc()`.

- [ ] **Step 1: Write failing technical-QC and review-gate tests**

```python
def test_analyze_visual_candidate_flags_embedded_text(tmp_path, fake_video, fake_runners):
    report = analyze_visual_candidate(
        fake_video,
        micro_shot(),
        output_dir=tmp_path / "qc",
        command_runner=fake_runners.command,
        ocr_runner=lambda _: "最后一班车",
    )
    assert "embedded_text" in report["automatic_hard_failures"]
    assert report["passed"] is False


def test_require_passed_visual_qc_rejects_missing_manual_review():
    with pytest.raises(VisualQCError, match="manual review"):
        require_passed_visual_qc({"automatic_passed": True, "manual_review": None})
```

- [ ] **Step 2: Run tests and confirm missing module**

Run: `.venv/bin/pytest -q tests/test_visual_qc.py`

Expected: collection fails for `factory.visual_qc`.

- [ ] **Step 3: Implement deterministic technical analysis**

```python
@dataclass(frozen=True)
class VisualReview:
    identity: int
    expression: int
    anatomy: int
    continuity: int
    semantics: int
    motion: int
    clean_frame: int
    hard_failures: tuple[str, ...]
    selected_start_seconds: float
    selected_end_seconds: float
    notes: str


def require_passed_visual_qc(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if not report.get("automatic_passed"):
        raise VisualQCError("Candidate failed automatic visual QC.")
    review = report.get("manual_review")
    if not isinstance(review, dict):
        raise VisualQCError("Candidate is missing manual review.")
    if review.get("hard_failures"):
        raise VisualQCError("Candidate has manual hard failures.")
    if float(review.get("selected_end_seconds", 0)) <= float(
        review.get("selected_start_seconds", 0)
    ):
        raise VisualQCError("Candidate selected range is invalid.")
    return report
```

`analyze_visual_candidate()` must:

1. Call `probe_media(required_stream="video")`.
2. Generate a 3x3 full-duration contact sheet with FFmpeg.
3. Run `blackdetect` and `scdet`; any black segment over 0.08 seconds or unexpected scene score over the chosen threshold is a hard failure.
4. Crop the middle and lower 55% of sampled frames, run `tesseract <image> stdout -l chi_sim+eng --psm 11`, and flag non-empty CJK or alphanumeric text after confidence filtering.
5. Record frame-difference statistics without changing the source file.
6. Write JSON atomically and include exact evidence paths and commands with secrets absent.

- [ ] **Step 4: Implement review recording without overwriting automatic evidence**

`record_visual_review(report_path, review)` must load the existing report, validate all seven scores are integers from 0-5, validate hard-failure values against `HARD_FAILURES`, ensure the selected range is inside the probed duration, add the review under `manual_review`, compute the weighted score, and set `passed` only when both automatic and manual gates pass.

- [ ] **Step 5: Run focused tests and local dependency probes**

Run: `.venv/bin/pytest -q tests/test_visual_qc.py tests/test_media_validation.py`

Expected: all selected tests pass.

Run: `tesseract --list-langs`

Expected: output contains `chi_sim` and `eng`.

- [ ] **Step 6: Commit the QC unit**

```bash
git add factory/visual_qc.py tests/test_visual_qc.py requirements.txt
git commit -m "feat: add visual candidate quality gates"
```

### Task 7: Approved Micro-Shot Selection and Variable-Cadence Renderer

**Files:**
- Create: `factory/micro_preview.py`
- Create: `tests/test_micro_preview.py`
- Modify: `factory/preview_refresh.py`
- Modify: `factory/project_runner.py`

**Interfaces:**
- Consumes: `visual_timeline.json`, passed candidate QC reports, existing voiceover M4A, measured SRT, and OpenMontage package.
- Produces: `visual_selection.json`, `micro_preview.mp4`, `micro_preview_voiced.mp4`, `micro_preview_report.json`, and final `output/<project>/final_preview.mp4`.

- [ ] **Step 1: Write failing source-selection and filter tests**

```python
def test_build_micro_preview_requires_every_candidate_to_pass_qc(
    sample_episode, visual_timeline, selection_with_failed_qc
):
    with pytest.raises(MicroPreviewError, match="failed visual QC"):
        select_micro_sources(sample_episode, visual_timeline, selection_with_failed_qc)


def test_micro_preview_command_uses_per_shot_cadence_and_selected_range(tmp_path):
    command = build_micro_preview_ffmpeg_command(
        sources=[micro_source(start=1.2, end=3.4, cadence_fps=8, duration=4.0)],
        resolution="1080x1920",
        output_fps=30,
        output_path=tmp_path / "preview.mp4",
    )
    filters = command[command.index("-filter_complex") + 1]
    assert "trim=start=1.200:end=3.400" in filters
    assert "fps=8,fps=30" in filters
    assert "tpad=stop_mode=clone" in filters
```

- [ ] **Step 2: Run tests and confirm missing module**

Run: `.venv/bin/pytest -q tests/test_micro_preview.py`

Expected: collection fails for `factory.micro_preview`.

- [ ] **Step 3: Implement approved selection schema**

```python
@dataclass(frozen=True)
class MicroSource:
    micro_shot_id: str
    index: int
    kind: str
    path: Path
    selected_start_seconds: float
    selected_end_seconds: float
    timeline_duration_seconds: float
    cadence_fps: int
    entry_cut: str
    exit_cut: str


def select_micro_sources(
    episode: Episode,
    timeline: VisualTimeline,
    selection: Mapping[str, Any],
) -> list[MicroSource]:
    selected = selection.get("selected_candidates")
    if not isinstance(selected, dict):
        raise MicroPreviewError("Visual selection is missing selected_candidates.")
    sources: list[MicroSource] = []
    for shot in timeline.micro_shots:
        item = selected.get(shot.id)
        if not isinstance(item, dict):
            raise MicroPreviewError(f"No selected candidate for {shot.id}.")
        qc = load_visual_qc(item["qc_report_path"])
        require_passed_visual_qc(qc)
        sources.append(micro_source_from_review(shot, item, qc))
    return sources
```

- [ ] **Step 4: Implement exact rendering behavior**

For every video source, apply selected-range trim, scale/pad, `fps=<cadence>,fps=30`, clone-pad to the timeline duration, and final trim. For every approved still source, use `-loop 1`, scale/crop inside the subtitle-safe area, and hold it for the exact timeline duration; allow at most a 2% linear zoom only when `camera_mode == "micro_pan"`. Concatenate sources with no crossfade. Insert a 2-4 frame black color source only when `exit_cut == "time_jump_black"`; subtract that black duration from the preceding micro-shot so parent duration totals remain exact.

After silent rendering, reuse `build_mux_voiced_preview_command()` and `finalize_openmontage_preview()` exactly as `refresh_project_preview()` does. Add the four new visual artifacts to `_ROLLBACK_RUN_ARTIFACTS`.

- [ ] **Step 5: Make refresh select the quality path only when complete**

`refresh_project_preview()` must use the micro renderer when all three files exist:

```text
runs/<project>/visual_timeline.json
runs/<project>/visual_selection.json
runs/<project>/model_bakeoff_report.json
```

It must call `require_selected_production_model()` and reject an incomplete quality path. When none of the three files exists, preserve the legacy hybrid renderer. A partial set is an error, not a silent fallback.

- [ ] **Step 6: Run render and rollback tests**

Run: `.venv/bin/pytest -q tests/test_micro_preview.py tests/test_preview_refresh.py tests/test_project_runner.py tests/test_hybrid_preview.py`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the render unit**

```bash
git add factory/micro_preview.py factory/preview_refresh.py factory/project_runner.py tests/test_micro_preview.py tests/test_preview_refresh.py tests/test_project_runner.py
git commit -m "feat: render approved motion-comic micro-shots"
```

### Task 8: CLI, Control Plane, and Operator Reports

**Files:**
- Modify: `factory_cli.py`
- Modify: `factory/control_plane.py`
- Modify: `factory/workflow_status.py`
- Modify: `factory/operator_handoff.py`
- Modify: `factory/dashboard.py`
- Create: `tests/test_cli_quality_upgrade.py`
- Modify: `tests/test_control_plane.py`
- Modify: `tests/test_workflow_status.py`
- Modify: `tests/test_operator_handoff.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: all new quality artifacts and existing provider profile.
- Produces: operator commands for planning, bakeoff, QC, generation, selection, rendering, and iteration.

- [ ] **Step 1: Write failing CLI parser and gate-status tests**

```python
def test_quality_commands_are_registered():
    parser = build_parser()
    assert parser.parse_args(["visual-plan", "--project", "sample_episode"]).command == "visual-plan"
    assert parser.parse_args(["video-bakeoff", "--project", "sample_episode"]).command == "video-bakeoff"
    assert parser.parse_args(["visual-qc", "--project", "sample_episode"]).command == "visual-qc"
    assert parser.parse_args(["micro-video-batch", "--project", "sample_episode"]).command == "micro-video-batch"
    assert parser.parse_args(["micro-still-batch", "--project", "sample_episode"]).command == "micro-still-batch"


def test_control_plane_blocks_batch_before_bakeoff(project_fixture):
    report = refresh_project_control_plane(project_fixture.config, project_fixture.id)
    assert "model_bakeoff_not_ready" in report["blocker_codes"]
    assert not any(action["command"].startswith("micro-video-batch") for action in report["ready_actions"])
```

- [ ] **Step 2: Run tests and confirm command failures**

Run: `.venv/bin/pytest -q tests/test_cli_quality_upgrade.py tests/test_control_plane.py`

Expected: parser assertions fail because the commands are absent.

- [ ] **Step 3: Add exact commands and safe defaults**

Register:

```text
visual-plan --project PROJECT [--enable-live]
video-bakeoff --project PROJECT [--candidate N] [--enable-live]
visual-qc --project PROJECT --micro-shot ID --model MODEL --candidate N
visual-review --project PROJECT --review-json PATH
micro-video-batch --project PROJECT [--candidate N] [--limit N] [--enable-live]
micro-still-batch --project PROJECT [--candidate N] [--limit N] [--enable-live]
visual-select --project PROJECT --selection-json PATH
refresh-preview --project PROJECT
```

`visual-plan` may make one text-model request only with `--enable-live`. Video and still generation commands make zero network calls without `--enable-live`. `visual-review` and `visual-select` reject paths outside the workspace and never edit source media.

- [ ] **Step 4: Surface the next real blocker instead of stale readiness**

Control-plane priority must be:

1. Missing or invalid visual timeline.
2. Bakeoff candidates missing.
3. Bakeoff reviews missing or no model passed.
4. Production micro-shots missing.
5. Automatic or manual QC missing/failed.
6. Selection incomplete.
7. Preview stale.
8. Ready.

Workflow, handoff, and dashboard must report selected production model, passed/total micro-shots, paid-attempt counts, failed micro-shot IDs, latest review notes, and final preview path. They must never display API keys or signed URLs.

- [ ] **Step 5: Run CLI and reporting tests**

Run: `.venv/bin/pytest -q tests/test_cli_quality_upgrade.py tests/test_control_plane.py tests/test_workflow_status.py tests/test_operator_handoff.py tests/test_dashboard.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the operator unit**

```bash
git add factory_cli.py factory/control_plane.py factory/workflow_status.py factory/operator_handoff.py factory/dashboard.py tests/test_cli_quality_upgrade.py tests/test_control_plane.py tests/test_workflow_status.py tests/test_operator_handoff.py tests/test_dashboard.py
git commit -m "feat: expose visual quality workflow controls"
```

### Task 9: Repository Verification Before Paid Generation

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Consumes: completed code from Tasks 1-8.
- Produces: a verified, documented quality workflow ready for small paid model comparison.

- [ ] **Step 1: Run the complete Python suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass; the count is at least the previous baseline of 331 plus the new quality tests.

- [ ] **Step 2: Run static and syntax checks**

Run: `.venv/bin/ruff check factory tests factory_cli.py`

Expected: `All checks passed!`

Run: `.venv/bin/python -m compileall -q factory factory_cli.py`

Expected: exit code 0 and no output.

Run: `bash -n scripts/*.sh`

Expected: exit code 0 and no output.

Run: `git diff --check`

Expected: exit code 0 and no output.

- [ ] **Step 3: Run no-charge workflow dry runs**

Run: `.venv/bin/python factory_cli.py visual-plan --project sample_episode`

Expected: a fail-closed report explaining that live text planning needs `--enable-live`, with zero gateway requests.

Run: `.venv/bin/python factory_cli.py video-bakeoff --project sample_episode`

Expected: a plan containing two models and two representative shots, with `executed=false` and zero video requests.

Run: `.venv/bin/python factory_cli.py micro-video-batch --project sample_episode`

Expected: blocked by the missing selected bakeoff model and zero video requests.

Run: `.venv/bin/python factory_cli.py micro-still-batch --project sample_episode`

Expected: blocked by the missing selected still model when still-routed shots exist, with zero image requests.

- [ ] **Step 4: Document commands, gates, artifacts, and candidate cap**

Add a concise quality-upgrade section to README and deployment docs. Append one iteration-log entry containing exact no-charge verification results and the fact that paid generation has not started yet.

- [ ] **Step 5: Commit verified implementation**

```bash
git add README.md docs/deployment.md docs/iteration-log.md
git commit -m "docs: document visual quality production flow"
```

### Task 10: Paid Bakeoff, Agent Review, and Winner Selection

**Files:**
- Create runtime artifacts under: `runs/sample_episode/model_bakeoff/`
- Create runtime artifact: `runs/sample_episode/model_bakeoff_review.json`
- Create runtime artifact: `runs/sample_episode/model_bakeoff_report.json`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Consumes: ignored `.env` gateway credential, approved visual timeline, production role references, and verified commands.
- Produces: four 5-second 1080p character-video candidates, two character-free still candidates when required, contact sheets, reviews, one selected video model, and one selected still model when required.

- [ ] **Step 1: Generate the detailed visual plan with the configured text model**

Run: `.venv/bin/python factory_cli.py visual-plan --project sample_episode --enable-live`

Expected: `visual_timeline.json` is valid, contains 14-18 micro-shots for the current 61-second episode, and every parent duration sum matches exactly.

- [ ] **Step 2: Inspect and revise the plan before video charges**

Read every micro-shot and reject any multi-action prompt, absent expression, unnecessary character, non-locked camera without narrative reason, or dialogue invention. Record revisions in `runs/sample_episode/visual_plan_review.json`, update the visual plan through the validated writer, and rerun validation.

- [ ] **Step 3: Generate four paid bakeoff clips**

Run: `.venv/bin/python factory_cli.py video-bakeoff --project sample_episode --candidate 1 --enable-live`

Expected: two representative micro-shots are generated once by each production model at 1080p; every task ID and resume state is persisted.

When the visual timeline contains a character-free environment or prop route, also run the bakeoff still jobs once for `doubao-seedream-4-5` and `gpt-image-2` at 1024x1536. Do not include character reference images in either request.

- [ ] **Step 4: Run automatic QC and inspect every contact sheet**

Run `visual-qc` for all video candidates and still candidates. View all generated contact sheets/images at original detail, compare character clips with role references and every candidate with its micro-shot specification, and write all seven 0-5 scores, hard failures, selected source ranges for videos, and concrete revision notes.

- [ ] **Step 5: Finalize the bakeoff**

Run: `.venv/bin/python factory_cli.py visual-review --project sample_episode --review-json runs/sample_episode/model_bakeoff_review.json`

Expected: exactly one model reaches at least 80 with no hard failures. If neither passes, revise the two micro-shots and generate candidate 2; do not start the production batch.

- [ ] **Step 6: Commit only durable code/docs, not generated media or secrets**

Append model scores and selection reasoning to the iteration log. Generated clips, task states, reports with runtime IDs, and `.env` remain ignored.

### Task 11: Full Production Batch and Iterative Final-Cut Review

**Files:**
- Create runtime artifacts under: `runs/sample_episode/micro_clips/`
- Create runtime artifacts under: `runs/sample_episode/visual_qc/`
- Create runtime artifact: `runs/sample_episode/visual_selection.json`
- Replace delivery artifact: `output/sample_episode/final_preview.mp4`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Consumes: selected production model, approved visual timeline, role references, QC and render pipeline.
- Produces: fully reviewed micro-shot selection and final motion-comic cut.

- [ ] **Step 1: Generate only candidate 1 for all remaining micro-shots**

Run: `.venv/bin/python factory_cli.py micro-video-batch --project sample_episode --candidate 1 --enable-live`

Expected: already-generated bakeoff clips are reused where signatures match; each remaining micro-shot has one candidate or one explicit failure report.

Run `micro-still-batch --candidate 1 --enable-live` for character-free establishing/prop shots after the still model gate passes. Character shots must never be silently routed to this command.

- [ ] **Step 2: Run automatic QC and contact-sheet review per micro-shot**

For each candidate, run `visual-qc`, inspect the full contact sheet, and write a review. Select only stable 1-3 second ranges. Do not approve a clip with a hard failure even when a shorter crop could conceal it.

- [ ] **Step 3: Revise and regenerate only failed micro-shots**

Write concrete revision notes naming the failed field: expression, gaze, action complexity, extra character, anatomy, text, cut, or motion. Update only that micro-shot, increment its candidate number, and generate it again. Stop after candidate 3 and redesign the micro-shot before any further paid call.

- [ ] **Step 4: Write complete selection and render the first quality cut**

Run: `.venv/bin/python factory_cli.py visual-select --project sample_episode --selection-json runs/sample_episode/visual_selection.reviewed.json`

Run: `.venv/bin/python factory_cli.py refresh-preview --project sample_episode`

Expected: the quality path renders all selected ranges, reuses the existing zero-overlap voiceover, burns measured subtitles, and produces a valid final preview.

- [ ] **Step 5: Run technical and visual final-cut self-check**

Run: `.venv/bin/python /Users/tml/.codex/skills/video-edit-self-check/scripts/video_preflight.py output/sample_episode/final_preview.mp4`

Expected: 1080x1920, 30 fps, H.264/AAC, one audio stream, positive duration, and no technical warnings.

Inspect the full contact sheet plus dense sheets around every cut. Compare each visible segment against the visual timeline and spoken line. Check expressions, role identity, hands, source text, caption collisions, camera drift, repeated frame cadence, and transition logic.

- [ ] **Step 6: Iterate the edit and only the failed sources**

Write `runs/sample_episode/final_cut_review.json` with timestamped findings and dispositions. Fix edit-range or cadence problems without regeneration. Regenerate only when source identity, expression, anatomy, text, or internal-cut quality fails. Repeat render and preflight until the review has zero open hard failures.

- [ ] **Step 7: Run final repository and secret checks**

Run: `.venv/bin/pytest -q`

Run: `.venv/bin/ruff check factory tests factory_cli.py`

Run: `git diff --check`

Scan tracked files for gateway and Speech token patterns. Expected: all tests/static checks pass and no secret appears in tracked content.

- [ ] **Step 8: Record final evidence and complete the goal only after acceptance**

Append final model, micro-shot counts, regenerated IDs, QC totals, cadence distribution, media metadata, test counts, and residual risks to the iteration log. Mark the goal complete only when every acceptance condition in the design spec is satisfied and the final video path exists.
