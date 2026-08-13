# Novel Motion Comic Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MVP that turns a short novel excerpt into a structured, testable voiced motion-comic episode package and prepares it for OpenMontage post-production.

**Architecture:** Keep upstream projects separate. The local factory owns orchestration, schema normalization, validation, and adapter handoff. LumenX becomes the production runtime after source inspection; AIComicBuilder informs schema and prompt design; OpenMontage is called as a post-production backend.

**Tech Stack:** Python 3.12-compatible scripts, JSON artifacts, pytest, Git subdirectories, existing OpenMontage Python virtualenv, future LumenX Node/Python services.

---

### Task 1: Workspace and Source Setup

**Files:**
- Create: `README.md`
- Create: `config/factory.config.json`
- External: `external/lumenx`
- External: `external/AIComicBuilder`

- [ ] Create the project README with repository roles and first-run commands.
- [ ] Create a JSON config pointing to local OpenMontage and source repos.
- [ ] Clone `https://github.com/alibaba/lumenx.git` into `external/lumenx`.
- [ ] Clone `https://github.com/LingyiChen-AI/AIComicBuilder.git` into `external/AIComicBuilder`.
- [ ] Inspect both upstream projects for install scripts, env examples, and service ports.
- [ ] Commit the setup files.

### Task 2: Episode Schema and Sample Input

**Files:**
- Create: `factory/schema.py`
- Create: `samples/sample_novel.txt`
- Create: `tests/test_schema.py`

- [ ] Write dataclasses for `Character`, `DialogueLine`, `Shot`, and `Episode`.
- [ ] Write `episode_to_dict()` and `validate_episode()` functions.
- [ ] Add a short Chinese sample novel excerpt with two recurring characters.
- [ ] Test that an episode with two characters and six shots validates.
- [ ] Test that dialogue referencing an unknown speaker fails validation.
- [ ] Commit schema and tests.

### Task 3: Dry-Run Novel Planner

**Files:**
- Create: `factory/novel_planner.py`
- Create: `tests/test_novel_planner.py`

- [ ] Implement a deterministic dry-run planner that converts the sample excerpt into two characters and 6-8 shots.
- [ ] Include narration, dialogue, visual prompt, audio mood, and target duration per shot.
- [ ] Test that shot indexes are monotonic.
- [ ] Test that at least one narrator line and one character dialogue line are produced.
- [ ] Commit the planner.

### Task 4: OpenMontage Package Adapter

**Files:**
- Create: `factory/openmontage_adapter.py`
- Create: `tests/test_openmontage_adapter.py`

- [ ] Convert an `Episode` into `runs/<project_id>/openmontage_package.json`.
- [ ] Include output paths for audio, subtitles, preview frames, and final MP4.
- [ ] Check that configured OpenMontage path exists.
- [ ] Test package generation with the local OpenMontage path `/Users/tml/Desktop/洋葱样片/OpenMontage`.
- [ ] Commit the adapter.

### Task 5: CLI Orchestrator

**Files:**
- Create: `factory_cli.py`
- Create: `tests/test_cli.py`

- [ ] Add `python factory_cli.py plan --input samples/sample_novel.txt --project sample_episode`.
- [ ] Write `runs/sample_episode/episode.json`.
- [ ] Write `runs/sample_episode/status.json`.
- [ ] Write `runs/sample_episode/openmontage_package.json`.
- [ ] Test the CLI end to end in dry-run mode.
- [ ] Commit the CLI.

### Task 6: Upstream Runtime Evaluation

**Files:**
- Create: `docs/runtime-evaluation.md`

- [ ] Run LumenX dependency inspection.
- [ ] Run AIComicBuilder dependency inspection.
- [ ] Run OpenMontage capability summary.
- [ ] Record which services can start locally without paid keys.
- [ ] Record which features require API keys.
- [ ] Commit the evaluation.

### Task 7: First Local Deployment

**Files:**
- Create: `scripts/start_factory.sh`
- Create: `docs/deployment.md`

- [ ] Add a startup script that runs tests and then generates the sample episode package.
- [ ] If LumenX can start locally, document the service URL and required env vars.
- [ ] If LumenX cannot start without keys, keep the factory in dry-run mode and document the block.
- [ ] Verify generated artifacts exist under `runs/sample_episode/`.
- [ ] Commit deployment docs and script.

### Task 8: Iteration Loop

**Files:**
- Create: `docs/iteration-log.md`

- [ ] Record every test run with date, command, result, and next fix.
- [ ] Improve planner output based on generated package quality.
- [ ] Add real TTS or LumenX generation only after dry-run package is stable.
- [ ] Keep each iteration testable and committed.
