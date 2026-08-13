# Reference Pet Longform Implementation Plan

> **For agentic workers:** Execute tasks in order, keep the existing dirty worktree intact, and commit only the files named by each task.

**Goal:** Produce a 160-second original AI pet short whose story beats, scene sequence, shot functions, and pacing closely follow the locally archived reference `这位猫咪，请你自重！`, while using the established original cat characters, newly generated spotted-dove assets, rewritten dialogue, fixed cute voices, and a fully auditable generation/QC pipeline.

**Architecture:** Add an isolated `pet_longform_*` workflow instead of extending the already modified short-form workflow. A pure story contract defines all 39 shots and their dependencies. Assets and immutable voice tracks are locked before generation. Each video candidate is generated from a predecessor end frame plus fixed character/scene anchors, reviewed before selection, and only approved media can enter the FFmpeg composition manifest. The clean master and release master are rendered from the same reviewed timeline.

**Tech Stack:** Python 3.11, pytest, existing gateway image/video clients, Doubao Seed-TTS 2.0, ffmpeg/ffprobe, Pillow, OpenCV, existing pet-sitcom review utilities.

## Global Constraints

- Output root: `/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1`
- Master format: 1080x1920, 30 fps, 160.0 seconds.
- Reuse the locked original cat identities:
  - 奶糖: calm orange older sister.
  - 豆包: lively black-and-white younger sister.
- Create one spotted-dove reference and four fixed scene anchors.
- Use rewritten dialogue and newly generated audio only. Do not reuse the reference creator's voice, subtitles, music, watermark, or exact wording.
- Do not speed up dialogue with `atempo`.
- Do not create ambience with pink noise, sine waves, generated room-tone loops, or unexplained foley.
- Every moving prop needs a visible actor, contact, and consequence.
- No `tpad`, `minterpolate`, optical-flow smoothing, or duplicated-frame padding in selected shots.
- Maximum two hard cuts in any rolling five-second window.
- Fail closed when a required asset, voice, candidate review, provenance record, or QC result is missing.
- Never revert, stage, or commit unrelated existing changes.

---

## Task 1: Encode the 160-Second Story Contract

**Files:**

- Create: `factory/pet_longform.py`
- Create: `tests/test_pet_longform.py`

**Step 1: Write the failing contract tests**

Test these public interfaces:

```python
from factory.pet_longform import (
    LongformDialogue,
    LongformShot,
    PetLongformPlan,
    build_dove_visit_plan,
    validate_pet_longform_plan,
    write_pet_longform_plan,
)
```

Required assertions:

```python
def test_dove_visit_plan_is_exactly_160_seconds(tmp_path):
    plan = build_dove_visit_plan(tmp_path)
    assert len(plan.shots) == 39
    assert sum(shot.duration_s for shot in plan.shots) == pytest.approx(160.0)
    assert plan.shots[0].start_s == pytest.approx(0.0)
    assert plan.shots[-1].end_s == pytest.approx(160.0)


def test_story_keeps_reference_beat_order(tmp_path):
    plan = build_dove_visit_plan(tmp_path)
    assert [act.name for act in plan.acts] == [
        "逃亡与收留",
        "相处与误会",
        "告别与回礼",
    ]
    assert plan.beat_ids == (
        "hawk_escape",
        "cats_notice_guest",
        "temporary_shelter",
        "water_and_rest",
        "repayment_question",
        "sister_relationship",
        "predator_misunderstanding",
        "feeding_wordplay",
        "farewell",
        "damaged_treat_return",
        "sunset_end",
    )


def test_dialogue_is_rewritten_and_role_bound(tmp_path):
    plan = build_dove_visit_plan(tmp_path)
    assert {line.speaker for line in plan.dialogue} <= {"旁白", "奶糖", "豆包", "斑斑"}
    assert all(line.text.strip() for line in plan.dialogue)
    assert all(line.voice_id for line in plan.dialogue)
```

Also test:

- shot IDs are `S001` through `S039`;
- shot starts and ends are contiguous;
- all dialogue lies inside its shot;
- each prop action defines actor, contact, and consequence;
- scene/axis dependencies point only backward;
- no rolling five-second window contains more than two hard cuts;
- invalid plans raise `ValueError`;
- JSON and Markdown contracts are written to the output root.

**Step 2: Run tests and confirm the expected import failure**

Run:

```bash
.venv/bin/pytest tests/test_pet_longform.py -q
```

Expected: failure because `factory.pet_longform` does not exist.

**Step 3: Implement immutable data contracts**

Create frozen dataclasses:

```python
@dataclass(frozen=True)
class LongformDialogue:
    line_id: str
    shot_id: str
    speaker: str
    text: str
    start_offset_s: float
    max_duration_s: float
    voice_id: str
    emotion: str


@dataclass(frozen=True)
class PropActionContract:
    prop_id: str
    actor: str
    contact: str
    consequence: str


@dataclass(frozen=True)
class LongformShot:
    shot_id: str
    act_id: str
    beat_id: str
    start_s: float
    duration_s: float
    scene_id: str
    framing: str
    subjects: tuple[str, ...]
    action: str
    expression: str
    dialogue_ids: tuple[str, ...]
    predecessor_shot_id: str | None
    continuity_anchor: str
    transition: str = "hard_cut"
    prop_actions: tuple[PropActionContract, ...] = ()
```

`PetLongformPlan` must expose `shots`, `dialogue`, `acts`, `beat_ids`, `output_root`, and derived `timeline_path`, `story_contract_path`, and `audio_manifest_path`.

**Step 4: Encode the exact 39-shot duration map**

Use this duration sequence:

```python
(
    4, 4, 4, 4, 4,
    4, 4, 4, 4,
    5, 5, 5, 5,
    4, 4, 4,
    4, 4, 4, 4, 4, 5,
    3.5, 3.5, 4,
    4, 4, 4, 5,
    4, 5,
    4, 4, 4, 4, 4, 3,
    3.5, 3.5,
)
```

Map the shots to these time-locked sections:

| Time | Shots | Story function |
|---|---:|---|
| 0-16 | S001-S004 | 斑斑躲避追赶、飞入猫咪家、两猫注意到访客 |
| 16-20 | S005 | 原创片名短停顿 |
| 20-36 | S006-S009 | 正式相遇、确认受伤与安全 |
| 36-56 | S010-S013 | 气味/捕食者误会、斑斑恭维、暂时收留 |
| 56-68 | S014-S016 | 喝水、休息、两猫照顾 |
| 68-93 | S017-S022 | 询问报答、问奶糖喜好、豆包吃醋、姐妹关系笑点 |
| 93-104 | S023-S025 | 豆包捕食本能冒头、斑斑惊慌、奶糖制止并道歉 |
| 104-121 | S026-S029 | 喂食与谷物误解，逐个排除错误食物 |
| 121-130 | S030-S031 | 斑斑告别、约定再见 |
| 130-153 | S032-S037 | 斑斑返回，带回一块破损冻干，自称从追赶者那里夺回 |
| 153-160 | S038-S039 | 三角色释然、夕阳收尾 |

Keep shots between 3 and 5 seconds except the two intentional 3.5-second sections. Set `S038` and `S039` to 3.5 seconds so the ending does not create a dense three-cut cluster.

**Step 5: Write the rewritten dialogue**

Use concise original lines that fit the shot budgets. Preserve only the situation and comedic function. Example voice contract:

```python
VOICE_IDS = {
    "旁白": "zh_female_tianmeixiaoyuan_moon_bigtts",
    "奶糖": "zh_female_vv_uranus_bigtts",
    "豆包": "zh_female_xiaohe_uranus_bigtts",
    "斑斑": "zh_male_shaonianzixin_moon_bigtts",
}
```

The actual dialogue must:

- give both cats spoken lines;
- keep each speaker's voice immutable;
- give 奶糖 shorter, calmer sentences;
- give 豆包 brighter and faster but still intelligible reactions;
- give 斑斑 cautious pauses without synthetic stutter repetition;
- leave at least 0.18 seconds between adjacent lines;
- avoid more than 16 Chinese characters in a single close-up line unless the shot is at least five seconds.

**Step 6: Implement validation and serialization**

`validate_pet_longform_plan()` must validate duration, contiguity, dependencies, dialogue bounds, role IDs, prop causality, cuts, and required beat order.

`write_pet_longform_plan()` writes:

- `story_contract.json`
- `timeline.json`
- `story_contract.md`

All files use UTF-8 and stable sorted JSON keys.

**Step 7: Run tests**

```bash
.venv/bin/pytest tests/test_pet_longform.py -q
```

Expected: all pass.

**Step 8: Commit**

```bash
git add factory/pet_longform.py tests/test_pet_longform.py
git commit -m "feat: define 160 second pet longform story"
```

---

## Task 2: Lock Character and Scene Assets

**Files:**

- Create: `factory/pet_longform_assets.py`
- Create: `tests/test_pet_longform_assets.py`

**Step 1: Write failing tests**

Test:

```python
from factory.pet_longform_assets import (
    LongformAssetManifest,
    build_longform_anchor_jobs,
    install_existing_cat_anchors,
    validate_longform_assets,
)
```

Assert that the manifest includes:

- `character_naitang`
- `character_doubao`
- `character_banban`
- `scene_living_room`
- `scene_balcony`
- `scene_feeding_table`
- `scene_entry`

Test that copied cat anchors match source SHA-256 values, all generated asset jobs contain a negative prompt for extra limbs, malformed paws/wings, text, watermark, floating props, and identity drift, and validation rejects missing or hash-changed assets.

**Step 2: Run tests and confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_assets.py -q
```

**Step 3: Implement asset manifest and copy-only cat installation**

Copy from:

```text
/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/characters/奶糖_reference.png
/Users/tml/Desktop/宠物短剧样片/冻干案_20260726_v2/characters/豆包_reference.png
```

Do not regenerate the cat identities. Record source path, destination path, SHA-256, dimensions, and creation mode in `assets/asset_manifest.json`.

**Step 4: Define generation jobs for the dove and scene anchors**

The dove identity must be a small warm-gray spotted dove with a pale throat, dark neck speckles, round black eyes, intact wings, and no accessories.

Generate portrait anchors at 1440x2560, the minimum portrait size accepted by
the production Seedream endpoint; selected video remains 1080x1920:

- living room: warm wooden floor, cream wall, low sofa, balcony visible at frame right;
- balcony: same wall/window materials and light direction;
- feeding table: same living room, low wooden table, stable bowl positions;
- entry: same home, balcony/entry edge, sunset light for return sequence.

Every prompt includes a fixed camera axis and excludes text, logos, extra animals, and moving props.

**Step 5: Implement visual and hash validation**

Validation checks:

- image exists and decodes;
- portrait dimensions;
- minimum luminance and variance;
- exact hash binding after approval;
- no duplicate file reused for different scene IDs;
- all required manifest entries are approved.

**Step 6: Run tests and commit**

```bash
.venv/bin/pytest tests/test_pet_longform_assets.py -q
git add factory/pet_longform_assets.py tests/test_pet_longform_assets.py
git commit -m "feat: lock longform character and scene assets"
```

---

## Task 3: Generate Immutable Multi-Role Dialogue

**Files:**

- Create: `factory/pet_longform_audio.py`
- Create: `tests/test_pet_longform_audio.py`

**Step 1: Write failing tests**

Test these interfaces:

```python
from factory.pet_longform_audio import (
    LongformAudioManifest,
    LongformVoiceLine,
    build_longform_voice_lines,
    generate_longform_audio,
    validate_longform_audio,
)
```

Use a fake TTS client to assert:

- each role always uses one voice ID;
- each line is generated once to immutable WAV;
- leading/trailing silence is measured and trimmed without rate change;
- actual speech duration fits the shot budget;
- adjacent lines do not overlap and have at least 0.18 seconds separation;
- output commands never contain `atempo`;
- missing provider provenance fails validation;
- WAV files are mono or stereo PCM at a supported sample rate.

**Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_audio.py -q
```

**Step 3: Implement audio-first scheduling**

For every line:

1. Generate Seed-TTS 2.0 WAV using the bound voice.
2. Detect actual non-silent interval.
3. Trim only leading/trailing silence.
4. Measure duration with ffprobe.
5. Place the clip inside the owning shot.
6. If it does not fit, shorten and regenerate the text; never speed it up.
7. Write `audio/audio_manifest.json` with hashes, provider, model, voice, line text, start/end, loudness, and retry history.

Normalize individual speech clips conservatively, then mix dialogue to a target near -16 LUFS without clipping.

**Step 4: Add audio whitelist validation**

Allowed audio:

- generated dialogue WAVs listed in the manifest;
- one explicitly approved continuous background-music file;
- optional foley files whose visible causal event and owning shot are declared.

Reject:

- unlisted files;
- generated noise beds;
- looping room tone;
- synthetic sweep/whoosh effects;
- duplicate dialogue placement;
- unexplained discontinuities.

**Step 5: Run tests and commit**

```bash
.venv/bin/pytest tests/test_pet_longform_audio.py -q
git add factory/pet_longform_audio.py tests/test_pet_longform_audio.py
git commit -m "feat: add immutable longform voice pipeline"
```

---

## Task 4: Build Continuity-Aware Video Generation

**Files:**

- Create: `factory/pet_longform_generation.py`
- Create: `tests/test_pet_longform_generation.py`

**Step 1: Write failing tests**

Test:

```python
from factory.pet_longform_generation import (
    LongformCandidate,
    LongformShotJob,
    build_longform_shot_jobs,
    generate_longform_candidates,
    select_longform_candidate,
)
```

Assert:

- each shot job includes character references, scene anchor, duration, exact action, expression, framing, dialogue timing, and negative constraints;
- every non-opening job references a predecessor end frame or a declared act-reset anchor;
- dialogue shots explicitly require visible mouth opening and closing during speech;
- silent reaction shots explicitly require closed-mouth idle motion;
- prop actions include visible contact and consequence;
- candidate provenance records model, prompt, input hashes, request ID, output hash, and retry index;
- no candidate can be selected without a passing review.

**Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_generation.py -q
```

**Step 3: Implement shot-job construction**

Build jobs in three generation batches:

- Act 1: S001-S016, 0-68 seconds.
- Act 2: S017-S029, 68-121 seconds.
- Act 3: S030-S039, 121-160 seconds.

Each job prompt must contain:

- same locked character appearance;
- same scene materials and light direction;
- current shot framing and camera height;
- one primary physical action only;
- start pose inherited from predecessor;
- end pose suitable for the next shot;
- speech intervals with natural jaw motion;
- no camera glide unless explicitly required;
- no self-moving props;
- no morphing, duplicate limbs, facial freezing, text, logos, or extra animals.

Use normal-speed physical action. Do not request "ultra smooth", slow floating camera movement, or interpolation.

**Step 4: Implement candidate generation and provenance**

Create two candidates for dialogue/action shots and one candidate for simple reaction/establishing shots. Store each under:

```text
shots/S001/candidate_01.mp4
shots/S001/candidate_01.json
```

Extract and hash first/end frames. Retry only the failed shot and its dependent successors.

**Step 5: Implement review-gated selection**

`select_longform_candidate()` accepts a candidate only when its review JSON passes identity, scene, motion, lip activity, causal prop, black-frame, and freeze-frame gates.

Selected files are linked or copied to:

```text
selected/S001.mp4
selected/S001.json
```

**Step 6: Run tests and commit**

```bash
.venv/bin/pytest tests/test_pet_longform_generation.py -q
git add factory/pet_longform_generation.py tests/test_pet_longform_generation.py
git commit -m "feat: add continuity aware longform generation"
```

---

## Task 5: Add Per-Shot Visual and Lip-Sync Review

**Files:**

- Create: `factory/pet_longform_review.py`
- Create: `tests/test_pet_longform_review.py`

**Step 1: Write failing tests**

Test:

```python
from factory.pet_longform_review import (
    LongformReviewResult,
    review_longform_candidate,
    render_longform_contact_sheet,
    validate_longform_selection,
)
```

Required gates:

- decodable video and audio streams;
- duration tolerance no more than 0.12 seconds;
- no black interval over 0.10 seconds;
- no frozen interval over 0.35 seconds during an action;
- no unexplained one-frame cut;
- speech onset/offset mouth-activity tolerance within 0.25 seconds;
- sufficient but not continuous mouth movement on dialogue shots;
- closed mouth on silent listening shots;
- no more than one primary action per shot;
- prop actor/contact/consequence visible in ordered review frames;
- character identity and scene anchor similarity above configured thresholds;
- predecessor-end/current-start continuity;
- manual review notes required before final selection.

**Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_review.py -q
```

**Step 3: Implement deterministic media checks**

Use ffprobe/OpenCV to compute:

- frame count and cadence;
- black-frame spans;
- perceptual frame differences;
- freeze spans;
- scene-cut count;
- mouth-region motion during line intervals;
- mouth-region motion outside line intervals;
- start/end-frame continuity.

Do not claim phoneme-level lip-sync. The automated gate verifies natural visible speaking activity aligned to line windows.

**Step 4: Render review artifacts**

For every shot, create:

- six-frame contact sheet;
- waveform and dialogue interval overlay;
- predecessor-end/current-start pair;
- `review.json`;
- `review.md` with problem, likely cause, action, and outcome.

Create act-level and full-timeline contact sheets after selection.

**Step 5: Run tests and commit**

```bash
.venv/bin/pytest tests/test_pet_longform_review.py -q
git add factory/pet_longform_review.py tests/test_pet_longform_review.py
git commit -m "feat: add longform visual review gates"
```

---

## Task 6: Compose Clean and Release Masters

**Files:**

- Create: `factory/pet_longform_compose.py`
- Create: `tests/test_pet_longform_compose.py`

**Step 1: Write failing tests**

Test:

```python
from factory.pet_longform_compose import (
    LongformCompositionManifest,
    build_longform_composition,
    compose_longform,
    validate_longform_master,
)
```

Assert:

- exactly 39 reviewed selected shots enter the manifest;
- selected durations sum to 160.0 seconds;
- dialogue placement is sourced only from `audio_manifest.json`;
- subtitles are derived from the rewritten line text;
- clean and release masters use the same picture timeline;
- no filters include `atempo`, `tpad`, `minterpolate`, `loop`, or generated noise;
- music is one approved continuous source with fades and ducking;
- release-only title/subtitle overlays are declared;
- no rolling five-second window has more than two hard cuts.

**Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_compose.py -q
```

**Step 3: Implement stable video assembly**

Normalize selected shots only for container compatibility:

- 1080x1920;
- 30 fps;
- H.264 yuv420p;
- preserve source motion without optical-flow synthesis.

Concatenate the exact reviewed timeline. Do not hide missing duration with duplicate frames.

**Step 4: Implement dialogue, music, and subtitles**

- Place every WAV at its scheduled start.
- Duck approved music under dialogue with smooth envelopes.
- Use one restrained continuous music track, with fade-in/out only.
- Use warm-cream subtitle boxes with black Chinese text, centered low, outside faces and mouths.
- Keep speaker changes readable without per-line camera cuts.

Create:

```text
final/斑鸠来访_无字净版.mp4
final/斑鸠来访_发布版.mp4
final/composition_manifest.json
```

**Step 5: Implement master QC**

Validate:

- exact duration within 0.05 seconds;
- 1080x1920, 30 fps;
- playable H.264/AAC streams;
- integrated loudness -16 ± 1 LUFS;
- true peak no higher than -1.5 dBTP;
- no dialogue overlap;
- no black/freeze spans;
- no unexplained audio cuts;
- subtitle bounds;
- audio whitelist;
- cut-density rule.

Write `final/final_qc.json` and fail the command when a gate fails.

**Step 6: Run tests and commit**

```bash
.venv/bin/pytest tests/test_pet_longform_compose.py -q
git add factory/pet_longform_compose.py tests/test_pet_longform_compose.py
git commit -m "feat: compose and validate pet longform masters"
```

---

## Task 7: Add a Dedicated Resumable CLI

**Files:**

- Create: `factory/pet_longform_cli.py`
- Create: `tests/test_pet_longform_cli.py`

**Step 1: Write failing CLI tests**

Commands:

```bash
python -m factory.pet_longform_cli plan
python -m factory.pet_longform_cli assets
python -m factory.pet_longform_cli audio
python -m factory.pet_longform_cli generate --act 1
python -m factory.pet_longform_cli review --act 1
python -m factory.pet_longform_cli compose
python -m factory.pet_longform_cli status
python -m factory.pet_longform_cli run
```

Test that:

- every command accepts `--output-root`;
- `run` resumes from existing valid manifests;
- invalid or hash-changed dependencies are regenerated from the earliest affected stage;
- status reports passed, failed, pending, and blocked shot IDs;
- nonzero exit is returned on failed gates;
- secrets are never printed.

**Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_pet_longform_cli.py -q
```

**Step 3: Implement orchestration**

Use atomic JSON writes and stage marker files. Do not mark a stage complete until validation passes. Keep gateway/TTS credentials in environment variables and redact them from logs.

**Step 4: Run focused and regression tests**

```bash
.venv/bin/pytest \
  tests/test_pet_longform.py \
  tests/test_pet_longform_assets.py \
  tests/test_pet_longform_audio.py \
  tests/test_pet_longform_generation.py \
  tests/test_pet_longform_review.py \
  tests/test_pet_longform_compose.py \
  tests/test_pet_longform_cli.py -q
```

Then run the existing relevant short-form tests:

```bash
.venv/bin/pytest \
  tests/test_pet_sitcom.py \
  tests/test_pet_sitcom_generation.py \
  tests/test_pet_sitcom_review.py \
  tests/test_pet_sitcom_compose.py -q
```

**Step 5: Commit**

```bash
git add factory/pet_longform_cli.py tests/test_pet_longform_cli.py
git commit -m "feat: add resumable pet longform workflow"
```

---

## Task 8: Run Live Production in Three Acts

**Files:**

- Create through CLI: `/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/**`
- Update: `docs/iteration-log.md`

**Step 1: Generate and lock the plan**

```bash
.venv/bin/python -m factory.pet_longform_cli plan \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

Inspect `story_contract.md` and `timeline.json`. Confirm 39 shots and 160 seconds.

**Step 2: Install/generate and manually approve assets**

```bash
.venv/bin/python -m factory.pet_longform_cli assets \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

Visually inspect the dove and four scene anchors. Reject any identity drift, wrong room axis, extra animal, malformed wing/paw, text, or floating prop before continuing.

**Step 3: Generate all role audio first**

```bash
.venv/bin/python -m factory.pet_longform_cli audio \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

Listen to a dialogue reel containing at least three lines per role. Confirm both cats sound cute but distinct, the dove sounds cautious, no role voice changes, no overlap, no clipped tails, and no speed artifacts.

**Step 4: Generate and review Act 1**

```bash
.venv/bin/python -m factory.pet_longform_cli generate --act 1 \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
.venv/bin/python -m factory.pet_longform_cli review --act 1 \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

Review the 0-68 second preview. Fix only failed shots and their downstream continuity dependents. Do not start Act 2 until character, scene, motion, and mouth gates pass.

**Step 5: Generate and review Acts 2 and 3**

Repeat for acts 2 and 3. Pay special attention to:

- S023-S025: mouth state and predator misunderstanding;
- S026-S029: bowls/food remain still unless visibly touched;
- S032-S037: damaged freeze-dried treat appears only after the dove visibly brings and releases it;
- S038-S039: no rapid montage at the ending.

**Step 6: Compose the first full master**

```bash
.venv/bin/python -m factory.pet_longform_cli compose \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

**Step 7: Run the video self-check skill**

Perform:

- technical probe;
- sampled frame/contact-sheet review;
- motion/freeze check;
- audio noise and overlap review;
- subtitle bounds review;
- causal prop audit;
- narrative continuity watch-through.

Classify every issue as:

- `must_fix`;
- `should_fix`;
- `acceptable_generation_variance`.

**Step 8: Iterate until no must-fix findings remain**

For every iteration, append to `docs/iteration-log.md`:

```markdown
### Iteration N

- Problem:
- Evidence:
- Cause hypothesis:
- Change:
- Result:
- Remaining risk:
```

Regenerate the smallest affected shot set. Re-run all dependent reviews and final QC after every replacement.

**Step 9: Commit documentation only**

Do not commit generated video binaries. Commit the iteration record:

```bash
git add docs/iteration-log.md
git commit -m "docs: record pet longform production iterations"
```

---

## Task 9: Final Verification and Handoff

**Files:**

- Verify: `/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/**`
- Update: `docs/iteration-log.md`

**Step 1: Run all new tests**

```bash
.venv/bin/pytest tests/test_pet_longform*.py -q
```

**Step 2: Run the workflow status check**

```bash
.venv/bin/python -m factory.pet_longform_cli status \
  --output-root "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1"
```

Expected:

- 39 selected shots;
- 39 passed reviews;
- zero blocked shots;
- clean and release masters present;
- final QC passed.

**Step 3: Independently probe the release master**

```bash
ffprobe -v error -show_entries \
  format=duration:stream=index,codec_name,width,height,r_frame_rate,sample_rate,channels \
  -of json \
  "/Users/tml/Desktop/宠物短剧样片/斑鸠来访_20260729_v1/final/斑鸠来访_发布版.mp4"
```

**Step 4: Verify expected deliverables**

Required:

```text
final/斑鸠来访_无字净版.mp4
final/斑鸠来访_发布版.mp4
final/composition_manifest.json
final/final_qc.json
review/full_contact_sheet.jpg
review/final_review.md
story_contract.json
story_contract.md
timeline.json
audio/audio_manifest.json
assets/asset_manifest.json
```

**Step 5: Record final outcome**

Append the final file paths, duration, loudness, shot count, failed/replaced candidate count, remaining accepted limitations, and test result to `docs/iteration-log.md`.

Commit only the log update:

```bash
git add docs/iteration-log.md
git commit -m "docs: finalize pet longform quality report"
```
