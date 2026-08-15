# Production Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the nine-stage pipeline reliably invalidate stale work, preserve MiniMax H3 image roles, share one audio/video timeline, assemble normalized media, run meaningful EVAL checks, and expose specialist capabilities through stable public services.

**Architecture:** Keep the existing project contracts and three modes. Extend the shared shot and video-job contracts instead of adding another workflow, add a deterministic media assembly service, and adapt existing quality modules behind one pipeline evaluator. Preserve v1 JSON readers while writing additive v2 fields.

**Tech Stack:** Python 3.12, dataclasses, FFmpeg/ffprobe, pytest, Ruff, MiniMax H3 API, Doubao TTS.

## Global Constraints

- Preserve existing project outputs and manual approvals; invalidate only the changed stage and downstream stages.
- Do not delete `pet_sitcom_*`, `pet_replica_*`, or historical GitHub material until their unique capabilities have public adapters and no production callers remain.
- Never persist API keys, signed URLs, inline image bytes, or private source media in reports.
- Use test-first red-green cycles for every behavior change.

---

### Task 1: Explicit stage implementation revisions

**Files:**
- Modify: `factory/pipeline_modes.py`
- Modify: `tests/test_pipeline_modes.py`
- Modify: `tests/test_pipeline_runner.py`

**Interfaces:**
- Produces: required `ModeStep.version: int` values for every stage.

- [x] Add a failing test proving every stage declares a positive explicit revision and that a revision change invalidates the stage and downstream work.
- [x] Remove the implicit `version=1` default and assign revisions to every mode step.
- [x] Run the pipeline mode and runner tests.

### Task 2: Provider-neutral image roles and effective model binding

**Files:**
- Modify: `factory/gateway_video_batch.py`
- Modify: `factory/minimax_h3_video.py`
- Modify: `factory/video_handoff.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `tests/test_gateway_video_batch.py`
- Modify: `tests/test_minimax_h3_video.py`
- Modify: `tests/test_video_handoff.py`

**Interfaces:**
- Produces: `VideoImageReference(source: str, role: str)` and `GatewayVideoJob.images: tuple[VideoImageReference, ...]`.
- Produces: `build_video_handoff(..., video_provider=..., video_model=...)` bound to the same effective provider/model used by the client.

- [x] Add failing tests that first-frame, last-frame, and character-reference roles survive handoff, job planning, signatures, reports, and MiniMax request preparation.
- [x] Add failing tests for project-level `video_model` overrides.
- [x] Implement additive reference-role parsing with v1 plain-string compatibility.
- [x] Bind handoff prompt compilation to the effective provider/model selected by the video stage.
- [x] Run H3, handoff, batch, and provider tests.

### Task 3: Explicit on-screen character membership

**Files:**
- Modify: `factory/schema.py`
- Modify: `factory/novel_planner.py`
- Modify: `factory/video_handoff.py`
- Modify: `factory/h3_prompt_compiler.py`
- Modify: affected schema, planner, handoff, and prompt tests.

**Interfaces:**
- Produces: `Shot.character_ids: list[str]`, serialized additively and defaulted safely for legacy episodes.

- [x] Add failing tests for a silent visible character, a character-free insert, and an on-screen speaker.
- [x] Add and validate the explicit field; infer it once in the planner, never in the provider handoff.
- [x] Remove name-substring fallback from the handoff.
- [x] Run schema, planner, prompt, and handoff tests.

### Task 4: One audio/video timing contract

**Files:**
- Create: `factory/shot_audio.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `factory/gateway_video_batch.py`
- Modify: `factory/minimax_h3_video.py`
- Modify: related audio, batch, and pipeline tests.

**Interfaces:**
- Produces: per-shot audio slices and timing metadata derived from `audio_manifest.json`.
- Produces: `lip_sync_policy` and bound audio SHA-256 in video manifests and job signatures.

- [x] Add failing tests proving each talking shot receives its exact audio slice and that its hash changes the cloud-video signature.
- [x] Build lossless per-shot audio slices from measured TTS timings.
- [x] Pass reference audio to providers that support it; mark unsupported providers for mandatory post-process lip sync instead of claiming native synchronization.
- [x] Ensure H3 reports its native-audio behavior truthfully and final editing deliberately replaces or preserves audio according to policy.
- [x] Run audio, provider, and pipeline tests.

### Task 5: Deterministic media assembly

**Files:**
- Create: `factory/media_assembly.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `tests/test_pipeline_generic_stages.py`
- Create: `tests/test_media_assembly.py`

**Interfaces:**
- Produces: normalized CFR/H.264/AAC-free visual clips and a final mux with an explicit target duration.

- [x] Add failing command-construction and real-media tests for mixed codecs, fractional durations, padding, trimming, subtitles, and no truncated ending.
- [x] Normalize every cloud clip to the project dimensions/FPS/pixel format and remove provider audio before concatenation.
- [x] Replace concat stream-copy and `-shortest` with measured trim/pad and deterministic mapping.
- [x] Run assembly and pipeline tests with generated fixtures.

### Task 6: Unified automatic EVAL

**Files:**
- Create: `factory/pipeline_eval.py`
- Modify: `factory/pipeline_generic_stages.py`
- Modify: `factory/pipeline_replica_stages.py`
- Modify: `tests/test_pipeline_generic_stages.py`
- Modify: `tests/test_pipeline_replica_stages.py`
- Create: `tests/test_pipeline_eval.py`

**Interfaces:**
- Produces: one `motion-comic-factory.eval.v2` report with technical, timing, continuity, audio, and manual-review sections.

- [x] Add failing tests for invalid media, duration drift, missing audio, overlapping dialogue, unbound shot outputs, and failed specialist review operations.
- [x] Reuse media probes and persisted specialist evidence without invoking paid generation.
- [x] Fail the stage on objective hard failures; reserve the manual gate for subjective anatomy, expression, and story review.
- [x] Run generic, replica, and evaluator tests.

### Task 7: Public specialist services and shared infrastructure

**Files:**
- Create: `factory/pet_sitcom_service.py`
- Modify: `factory_cli.py`
- Modify: `factory/file_io.py`
- Modify: touched `pet_sitcom_*` modules only where public wrappers require it.
- Modify: CLI and file-I/O tests.

**Interfaces:**
- Produces: public inspection/service functions used by the CLI instead of private module symbols.
- Produces: shared strict JSON read, atomic JSON write, SHA-256, and guarded command helpers.

- [x] Add tests that forbid new CLI references to private specialist names and cover public service behavior.
- [x] Move the existing inspection orchestration behind public service functions in small slices.
- [x] Replace duplicate helpers only in files touched by Tasks 1-6; do not mechanically rewrite all specialist modules.
- [x] Keep `source_locked_cat_replica.py` until its remaining configuration behavior is represented by `pet_replica_*`; document the removal gate.
- [x] Run CLI, service, and full regression tests.

### Task 8: Documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `docs/pipeline-code-map.md`
- Modify: `docs/iteration-log.md`

- [x] Document the new shot, video-reference, audio-sync, assembly, cache, and EVAL contracts.
- [x] Run Ruff, compileall, `git diff --check`, secret scanning, focused tests, and the full test suite.
- [x] Inspect the final diff for accidental output, credentials, vendored repositories, and unrelated changes.
