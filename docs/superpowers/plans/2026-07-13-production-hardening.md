# Motion Comic Factory Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make paid generation restart-safe, state reporting accurate, media publishing atomic, and the factory runnable as a generic local job service.

**Architecture:** Persist deterministic per-asset state around provider calls, validate streams with ffprobe before atomic publication, derive control-plane reports from current gateway and TTS artifacts, and expose a manifest-backed local worker over the existing orchestration functions.

**Tech Stack:** Python 3.10+, pytest, requests, FFmpeg/ffprobe, POSIX file locks, shell launch scripts.

## Global Constraints

- Never expose or commit credentials.
- Never make a paid request from automated tests.
- Fail closed when a billable task may already have been accepted.
- Preserve the last known-good media artifact on every failed rerun.
- Keep existing CLI commands compatible unless their old status was incorrect.

---

### Task 1: Restart-safe, role-aware TTS

**Files:**
- Modify: `factory/doubao_tts.py`
- Modify: `factory/local_voiceover.py`
- Modify: `factory/env_readiness.py`
- Modify: `.env.example`
- Test: `tests/test_doubao_tts.py`
- Test: `tests/test_local_voiceover.py`

**Interfaces:**
- Produce: `DoubaoTTSTask`, `DoubaoTTSClient.submit`, and `DoubaoTTSClient.complete_task`.
- Produce: deterministic cue state files at `voiceover/clips/<cue>.tts.json`.
- Produce: actual provider status (`doubao`, `local`, or `mixed`) and role-map warnings.

- [ ] Write tests proving a completed matching cue is reused without submit.
- [ ] Run the focused test and verify it fails because state reuse is absent.
- [ ] Add signed cue state, task submission persistence, resume, and audio validation.
- [ ] Write tests proving submitted tasks resume and ambiguous submitting states fail closed.
- [ ] Run the focused tests and verify red, then implement the minimal behavior.
- [ ] Write tests for narrator/character voice-map precedence and actual mixed status.
- [ ] Run red, implement role mapping and accurate reports, then run both TTS suites green.

### Task 2: Gateway-aware control plane

**Files:**
- Modify: `factory/end_to_end_readiness.py`
- Modify: `factory/workflow_status.py`
- Modify: `factory/operator_handoff.py`
- Modify: `factory/dashboard.py`
- Modify: `factory_cli.py`
- Test: `tests/test_end_to_end_readiness.py`
- Test: `tests/test_workflow_status.py`
- Test: `tests/test_operator_handoff.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_cli_gateway.py`

**Interfaces:**
- Consume: `gateway_video_batch.json`, `hybrid_preview_report.json`, and the live provider profile.
- Produce: one refresh helper invoked after gateway generation and preview refresh.

- [ ] Write a failing readiness test where a complete gateway batch satisfies real visual generation.
- [ ] Implement gateway generation checks and current environment evaluation.
- [ ] Write failing workflow/handoff/dashboard tests for dynamic-shot and provider truth.
- [ ] Implement the derived summaries and remove obsolete gateway smoke actions.
- [ ] Write a failing CLI test proving generation refreshes all control-plane artifacts.
- [ ] Implement refresh orchestration and run the focused control-plane suites green.

### Task 3: Stream validation and atomic publication

**Files:**
- Create: `factory/media_validation.py`
- Modify: `factory/gateway_video.py`
- Modify: `factory/hybrid_preview.py`
- Modify: `factory/local_voiceover.py`
- Modify: `factory/openmontage_post.py`
- Modify: `factory/preview_refresh.py`
- Test: `tests/test_media_validation.py`
- Test: `tests/test_gateway_video.py`
- Test: `tests/test_hybrid_preview.py`
- Test: `tests/test_openmontage_post.py`
- Test: `tests/test_preview_refresh.py`

**Interfaces:**
- Produce: `probe_media(path, required_stream)` and sibling temporary-output helpers.
- Preserve: current public render and finalizer signatures.

- [ ] Write a failing test that rejects the existing box-only MP4 fixture.
- [ ] Implement bounded ffprobe validation with structural prechecks.
- [ ] Write failing tests showing failed hybrid/final renders preserve old outputs.
- [ ] Render all mutable media to sibling temporary files and replace only after validation.
- [ ] Convert subprocess failures into domain errors and run all media suites green.

### Task 4: Generic project runner and local worker

**Files:**
- Create: `factory/project_runner.py`
- Create: `factory/job_queue.py`
- Modify: `factory_cli.py`
- Modify: `scripts/start_factory.sh`
- Create: `scripts/start_worker.sh`
- Create: `requirements.txt`
- Create: `scripts/bootstrap_factory.sh`
- Test: `tests/test_project_runner.py`
- Test: `tests/test_job_queue.py`
- Test: `tests/test_cli_worker.py`
- Modify: `tests/test_live_pipeline_script.py`

**Interfaces:**
- Produce: `run_project(...)`, `enqueue_job(...)`, and `run_worker(...)`.
- Produce CLI commands: `run-project`, `enqueue`, and `worker`.

- [ ] Write failing tests for arbitrary input/project arguments and terminal run reports.
- [ ] Extract the current plan pipeline into `run_project` and keep `plan` compatible.
- [ ] Write failing tests for atomic queue claim, success, failure, and retry-safe state.
- [ ] Implement queue files and one-shot/continuous worker modes.
- [ ] Replace hard-coded sample orchestration with argument/env-driven wrappers.
- [ ] Add repository bootstrap and pinned direct dependencies, then run worker suites green.

### Task 5: Security, documentation, and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `.gitignore`
- Modify: `docs/iteration-log.md`

**Interfaces:**
- Document safe dry-run, live opt-in, role map, worker operation, and recovery commands.

- [ ] Add tests or checks that generated state and reports contain no credential values.
- [ ] Set local secret files to owner-only permissions without committing contents.
- [ ] Run `python -m pytest tests -q` and require zero failures.
- [ ] Run `python -m compileall -q factory factory_cli.py`.
- [ ] Run a no-cost arbitrary-novel worker job and verify all terminal artifacts.
- [ ] Run media preflight against the final sample and verify audio/video streams.
- [ ] Inspect `git diff --check`, tracked-secret scan, and worktree scope before completion.
