# Task 7 report — local audio-first microshot preflight

## Implementation

- Added `factory/audio_first_preflight.py` with `run_audio_first_preflight(run_dir, *, model)`.
- The preflight reads local episode, timeline, performance-card, dialogue-audio,
  bakeoff, character-asset, scene-keyframe, and candidate-review artifacts,
  deriving anchors from approved last-frame evidence, then writes only `preflight_report.json` in the
  supplied run directory.
- Each character-bearing microshot is planned independently.  Missing speaking
  capability, final dialogue audio, or continuity evidence becomes a local
  blocked error for that card; independent action-only cards remain planned.
- The implementation imports neither a gateway client nor any renderer and
  never calls a TTS, video, or paid-request route.
- Updated the README with the local command, complete artifact inventory,
  paid-handoff boundary, and the prohibition on using old Episode 1 exports as
  output targets.

## TDD evidence

### RED

Before the module existed:

```text
.venv/bin/python -m pytest tests/test_audio_first_preflight.py -v
ModuleNotFoundError: No module named 'factory.audio_first_preflight'
```

### GREEN

After the minimal artifact-only implementation:

```text
.venv/bin/python -m pytest tests/test_audio_first_preflight.py -v
3 passed in 3.69s
```

The tests use local fixtures only. They verify that an action-only bakeoff
blocks each visible-speaking card while leaving the independent action card
planned, that no gateway client is constructed, that an external delivered
Episode 1 v3 file is unchanged, and that a verified speaking-capable fixture
plans all four jobs without a gateway client.

## Verification

```text
.venv/bin/python -m ruff check factory/audio_first_preflight.py tests/test_audio_first_preflight.py
All checks passed!

git diff --check
exit 0
```

Two attempts to run `.venv/bin/python -m pytest -q` were cut off by the
environment's 30-second command window after reporting only early progress
(about 2%) and before a pytest summary or exit code.  This is an inconclusive
full-suite attempt, not a passing claim. The focused Task 7 suite completed
with all tests passing.

No Task 7 command or test accessed the existing delivered Episode 1 targets:

- `runs/heavenly-blind-box-pilot/longform/edits/episode_01/episode_01_picture_cut_v1.mp4`
- `runs/heavenly-blind-box-pilot/longform/edits/episode_01/episode_01_role_dialogue_v2.mp4`
- `runs/heavenly-blind-box-pilot/longform/edits/episode_01/episode_01_role_dialogue_v3.mp4`

## Final whole-branch review fixes

### Root causes and corrections

- Performance-card validation checked a visible line only when a card opted into
  lip sync. It did not require that speaker to be on screen and did not account
  for all source dialogue. Validation now requires the visible speaker in the
  paired microshot and counts every non-narrator source line exactly once.
- Video planning trusted an `approved_anchors.json` path map. It now rejects
  caller-supplied anchor paths and resolves the nearest prior approved candidate
  in the same resolved scene from the candidate manifest's immutable QC
  `last_frame` evidence. A scene's first shot uses its scene keyframe; a later
  shot with no prior approval blocks locally.
- Candidate approval verified automatic QC but not the authoritative overall
  `passed` conclusion, and accepted speech scores below the production gate.
  Approval now requires `passed=true`; visible speech additionally requires
  authoritative `speaker_visible=true`, lip sync at least 3.5, and exact
  agreement between review evidence and the QC manual review.
- Approved selection assumed every timeline slot was an MP4 video. Character-
  free slots now come from the immutable selected still result in the validated
  bakeoff report, while character-bearing slots remain approved video records.
- Preflight now consumes candidate-review provenance directly, skips already
  approved shots, and blocks missing final dialogue audio or missing prior
  same-scene anchor evidence before any renderer can be constructed.

### Regression evidence

The new tests were first observed failing for the missing anchor API, absent
speaker/dialogue accounting, absent QC `passed`/3.5 gates, and the hard-coded
video selection. After the fixes:

```text
.venv/bin/python -m pytest tests/test_performance_card.py tests/test_performance_planner.py tests/test_candidate_review.py tests/test_micro_video_batch.py tests/test_audio_first_preflight.py tests/test_model_bakeoff.py tests/test_micro_preview.py tests/test_quality_production_runner.py -q
637 passed in 19.83s
```

The expanded affected boundary run also included dialogue assets, visual QC,
and gateway batch persistence:

```text
757 passed, 1 skipped in 25.07s
```

Preflight regression coverage now freezes all three named Episode 1 deliveries
(`episode_01_picture_cut_v1.mp4`, `episode_01_role_dialogue_v2.mp4`, and
`episode_01_role_dialogue_v3.mp4`) and separately exercises missing final audio
and missing approved-anchor evidence. No test submits a video request.

### Static verification

```text
.venv/bin/python -m ruff check <all changed Python files>
All checks passed!

git diff --check
exit 0
```
