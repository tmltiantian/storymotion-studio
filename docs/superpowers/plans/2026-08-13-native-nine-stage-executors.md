# Native Nine-Stage Executors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unified-pipeline composite CLI execution with one native Python executor per standard stage for original, novel, and replica projects.

**Architecture:** `pipeline_runner.py` remains the state machine. A new context and artifact layer gives every stage an isolated directory and manifest, while a registry resolves `(mode, stage)` to a native executor. Mode adapters declare policies and executor IDs instead of shell commands; legacy commands remain compatibility entrypoints.

**Tech Stack:** Python 3.12, dataclasses, JSON, pytest, Ruff, existing FFmpeg/OpenMontage and gateway provider modules.

## Global Constraints

- Keep legacy commands and historical output directories working.
- Do not invoke cloud providers unless `enable_live=True`.
- One executor may pass only its own stage.
- Every passed stage must own a manifest and its real local artifacts.
- Preserve manual approval evidence and `legacy:` migration semantics.
- Do not modify unrelated dirty pet-replica or site work.

---

### Task 1: Stage Context And Artifact Store

**Files:**
- Create: `factory/pipeline_context.py`
- Create: `factory/pipeline_artifacts.py`
- Test: `tests/test_pipeline_artifacts.py`

**Interfaces:**
- Produces: `StageContext`, `stage_dir()`, `write_stage_manifest()`, `load_stage_manifest()`.

- [x] Write failing tests for isolated stage directories, atomic manifests and upstream artifact lookup.
- [x] Run `pytest tests/test_pipeline_artifacts.py -q` and confirm failure.
- [x] Implement the context and artifact APIs with symlink rejection and local-path validation.
- [x] Run the focused tests and confirm they pass.

### Task 2: Native Executor Registry

**Files:**
- Create: `factory/pipeline_executors.py`
- Modify: `factory/pipeline_runner.py`
- Modify: `factory/pipeline_modes.py`
- Test: `tests/test_pipeline_executors.py`
- Test: `tests/test_pipeline_runner.py`
- Test: `tests/test_pipeline_modes.py`

**Interfaces:**
- Consumes: `StageContext` and stage manifests.
- Produces: `register_executor()`, `resolve_executor()`, `execute_native_stage()`.

- [x] Write failing tests proving all mode/stage pairs resolve and execution does not spawn a CLI subprocess.
- [x] Run the focused tests and confirm failure.
- [x] Implement the registry and change mode steps from legacy commands to executor IDs.
- [x] Remove composite stage coverage from the state machine.
- [x] Run the focused tests and confirm they pass.

### Task 3: Original And Novel Planning Stages

**Files:**
- Create: `factory/pipeline_generic_stages.py`
- Test: `tests/test_pipeline_generic_stages.py`

**Interfaces:**
- Produces: native `concept`, `script`, `storyboard`, and `assets` executors.
- Uses: `plan_episode()`, `episode_to_dict()`, `write_character_asset_manifest()`.

- [x] Write failing tests that each executor writes only its own directory and consumes the prior manifest.
- [x] Implement concept normalization and mode-specific script planning.
- [x] Implement storyboard materialization and asset-manifest generation.
- [x] Verify original and novel outputs differ where their source semantics differ.

### Task 4: Generic Media, EVAL And Delivery Stages

**Files:**
- Modify: `factory/pipeline_generic_stages.py`
- Test: `tests/test_pipeline_generic_stages.py`

**Interfaces:**
- Produces: native `audio`, `video`, `edit`, `eval`, and `deliver` executors.
- Uses: existing voiceover, card preview, gateway batch, OpenMontage post and readiness capabilities.

- [x] Write failing tests for live blocking, local preview, stage-owned outputs and EVAL publication gates.
- [x] Implement audio generation and timed subtitle output.
- [x] Implement local/live video generation without doing edit work.
- [x] Implement edit, EVAL evidence preparation, and delivery manifests.
- [x] Run generic-stage tests and project-runner compatibility tests.

### Task 5: Replica Native Stage Bridge

**Files:**
- Create: `factory/pipeline_replica_stages.py`
- Modify: `factory/pet_replica_cli.py`
- Test: `tests/test_pipeline_replica_stages.py`
- Test: `tests/test_pet_replica_cli.py`

**Interfaces:**
- Produces: nine replica executors using callable replica operations, not subprocess CLI calls.
- Keeps: `pet_replica_command()` as a thin compatibility adapter.

- [x] Write failing tests proving replica pipeline execution never invokes a CLI subprocess.
- [x] Extract public callable stage operations from CLI handlers without changing behavior.
- [x] Map replica concept/script/storyboard through plan evidence and keep reference/EVAL manual gates.
- [x] Run replica stage and legacy CLI tests.

### Task 6: Cutover, Documentation And Verification

**Files:**
- Modify: `factory/pipeline_cli.py`
- Modify: `README.md`
- Modify: `docs/pipeline-code-map.md`
- Test: all pipeline and CLI tests.

**Interfaces:**
- Produces: unified native execution as the default for `factory run/resume/review/publish`.

- [x] Add end-to-end tests for all three modes and artifact-specific resume behavior.
- [x] Remove obsolete composite and shell-command compatibility code from the unified path.
- [x] Update operator documentation and code map.
- [x] Run Ruff, compileall, focused regression, then the full test suite.
