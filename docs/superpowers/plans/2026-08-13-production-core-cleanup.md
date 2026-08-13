# Production Core Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove obsolete parallel pipelines and publish a smaller, unified motion-comic factory without losing reusable pet, replica, voice, lipsync, physics, or EVAL capabilities.

**Architecture:** The native nine-stage pipeline remains the only recommended orchestrator. A neutral video handoff replaces the LumenX-specific contract, while still-unique pet and replica modules remain capability libraries. Old orchestration, reporting, experiments, and unreachable longform code are removed with their CLI and tests.

**Tech Stack:** Python 3.12, pytest, Ruff, FFmpeg, OpenMontage handoff data, gateway Seedance, Doubao TTS, Git, GitHub CLI.

## Global Constraints

- Do not delete, rewrite, force-push, or change the original GitHub repository.
- Do not delete local generated media under `runs/` or `output/`.
- Do not remove unique pet voice, lipsync, physics, continuity, replica, or EVAL behavior.
- New cloud calls are not part of cleanup verification.
- The new GitHub repository is private by default and must contain no secrets.

---

### Task 1: Establish Cleanup Safety Tests And Shared File Utilities

**Files:**
- Create: `factory/file_io.py`
- Modify: `factory/pipeline_artifacts.py`
- Modify: `factory/pipeline_store.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `factory/pipeline_replica_stages.py`
- Test: `tests/test_file_io.py`
- Test: `tests/test_pipeline_artifacts.py`

**Interfaces:**
- Produces: `write_json_atomic(path, payload) -> Path`, `write_text_atomic(path, text) -> Path`, `sha256_file(path) -> str`.
- Consumes: regular local paths and JSON-serializable mappings.

- [ ] Write focused tests for atomic replacement, Unicode JSON, symlink rejection and stable SHA-256.
- [ ] Implement the shared helpers.
- [ ] Replace duplicate active-pipeline helpers without changing schemas.
- [ ] Run file and pipeline artifact tests.

### Task 2: Replace LumenX Contract With Neutral Video Handoff

**Files:**
- Create: `factory/video_handoff.py`
- Modify: `factory/gateway_video_batch.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `factory/project_runner.py` before its removal to expose compatibility expectations in tests.
- Delete: `factory/lumenx_adapter.py`
- Rename/Test: `tests/test_lumenx_adapter.py` to `tests/test_video_handoff.py`
- Modify: relevant gateway batch tests.

**Interfaces:**
- Produces: `build_video_handoff(episode, config, character_assets, run_dir) -> dict` and `write_video_handoff(...) -> Path`.
- Consumes: Episode, character asset manifest and stage-owned video directory.

- [ ] Write tests for the neutral schema and stage-owned clip paths.
- [ ] Implement the neutral handoff without LumenX runtime fields or API plans.
- [ ] Teach gateway batch to accept the neutral schema while retaining read-only legacy schema compatibility.
- [ ] Switch native generic video generation to the neutral handoff.
- [ ] Run adapter, gateway batch and generic pipeline tests.

### Task 3: Remove Unreachable Longform And Superseded Speaking Experiment

**Files:**
- Delete: `factory/pet_longform*.py`
- Delete: `tests/test_pet_longform*.py`
- Delete: `factory/speaking_ab.py`
- Delete: `tests/test_speaking_ab.py`
- Delete or modify: speaking A/B CLI tests and parser registrations in `factory_cli.py`.
- Modify: `docs/pipeline-code-map.md`

**Interfaces:**
- Preserves: `pet_sitcom_*`, `pet_replica_lipsync.py`, unified replica and generic pipeline APIs.

- [ ] Confirm no production imports or scripts reference longform modules.
- [ ] Confirm natural and precise lipsync behaviors are covered by replica/pet tests before deleting `speaking_ab`.
- [ ] Remove modules, tests and CLI registrations.
- [ ] Run pet sitcom, replica lipsync, replica compose and CLI tests.

### Task 4: Remove Legacy Composite Orchestration

**Files:**
- Delete: `factory/project_runner.py`
- Delete: `factory/job_queue.py`
- Delete: `tests/test_project_runner.py`
- Delete: queue/worker tests that only exercise the removed pipeline.
- Modify: `factory_cli.py`
- Modify: `scripts/start_factory.sh`
- Delete: `scripts/start_worker.sh`
- Modify: `README.md`
- Modify: `docs/deployment.md`

**Interfaces:**
- Keeps: `factory create/run/resume/status/approve/review/publish`.
- Removes: `plan`, `run-project`, `enqueue`, `worker`.

- [ ] Add/adjust CLI tests proving the unified factory entry remains complete.
- [ ] Remove old command functions, imports and parsers.
- [ ] Rewrite the starter script around `factory create` and `factory run`.
- [ ] Remove old queue and worker implementation/tests.
- [ ] Run CLI, pipeline and shell syntax tests.

### Task 5: Remove LumenX Runtime And Old Control Plane

**Files:**
- Delete: `factory/lumenx_bootstrap.py`
- Delete: `factory/lumenx_generation_guard.py`
- Delete: `factory/lumenx_live.py`
- Delete: `factory/lumenx_live_executor.py`
- Delete: `factory/lumenx_live_pipeline.py`
- Delete: `factory/lumenx_mock_backend.py`
- Delete: corresponding `tests/test_lumenx_*.py` except video handoff tests.
- Delete: `factory/control_plane.py`, `factory/dashboard.py`, `factory/end_to_end_readiness.py`, `factory/env_readiness.py`, `factory/operator_handoff.py`, `factory/real_generation_preflight.py`, `factory/real_generation_start_gate.py`, `factory/runtime_probe.py`, `factory/workflow_status.py` when no remaining producer uses them.
- Delete: corresponding tests and obsolete LumenX/control-plane shell scripts.
- Modify: `factory_cli.py`, README and deployment docs.

**Interfaces:**
- Keeps: provider profile, gateway clients, video handoff, OpenMontage and `factory status` stage details.

- [ ] Remove control-plane imports from remaining producers.
- [ ] Remove obsolete command handlers and parser registrations.
- [ ] Delete runtime/reporting modules, tests and scripts after a final reference scan.
- [ ] Run provider, gateway, pipeline, pet and replica regression suites.

### Task 6: Documentation, Full Verification And New Repository Publication

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/pipeline-code-map.md`
- Modify: `.gitignore` only if generated or credential-bearing paths are uncovered.

**Interfaces:**
- Produces: one documented native production path and a new private GitHub repository.

- [ ] Update documentation to contain only supported commands and retained capability boundaries.
- [ ] Run `python -m compileall -q factory tests`, Ruff and focused tests.
- [ ] Run the full pytest suite.
- [ ] Scan tracked candidates for API keys, tokens, private keys, credential files and files over GitHub limits.
- [ ] Review the complete diff and stage the intended cleaned project.
- [ ] Commit the cleanup on the current non-default branch.
- [ ] Create a new private GitHub repository without changing `origin`.
- [ ] Add a `next` remote and push the cleaned commit as `main`.
- [ ] Verify the new repository URL, default branch and remote isolation.
