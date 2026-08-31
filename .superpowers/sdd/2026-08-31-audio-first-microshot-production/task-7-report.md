# Task 7 report — local audio-first microshot preflight

## Implementation

- Added `factory/audio_first_preflight.py` with `run_audio_first_preflight(run_dir, *, model)`.
- The preflight reads local episode, timeline, performance-card, dialogue-audio,
  bakeoff, character-asset, scene-keyframe, approved-anchor, and
  candidate-review artifacts, then writes only `preflight_report.json` in the
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
