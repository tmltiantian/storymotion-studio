# MiniMax H3 Official Prompt Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile StoryMotion shots into MiniMax H3's official audiovisual prompt format and preserve explicit roles for first-frame, last-frame, and reference assets.

**Architecture:** Keep the official MiniMax-H3 repository as an external reference checkout. Add a provider-specific compiler beside the provider adapter, then select it only for `MiniMax-H3`; existing Seedance prompts remain unchanged. Extend H3 image inputs with typed roles while retaining plain-path backward compatibility.

**Tech Stack:** Python 3.13, dataclasses, pytest, MiniMax H3 `/v2/video_generation` API.

## Global Constraints

- Keep `/Users/tml/Desktop/MiniMax-H3` independent from StoryMotion Studio.
- Follow the official H3 section names and order exactly.
- Preserve dialogue text and punctuation exactly inside `<d>` blocks.
- Never expose API keys in prompts, reports, tests, or documentation.
- Keep existing gateway and Seedance behavior backward compatible.

---

### Task 1: H3 Prompt Compiler

**Files:**
- Create: `factory/h3_prompt_compiler.py`
- Create: `tests/test_h3_prompt_compiler.py`

**Interfaces:**
- Consumes: `Episode`, `Shot`, and optional reference-presence information.
- Produces: `compile_h3_shot_prompt(episode, shot, *, character_ids) -> str`.

- [x] Write failing tests for the official section order, stable subject/speaker labels, exact Chinese dialogue, closed-mouth narration, and concrete camera/sound descriptions.
- [x] Run the focused tests and confirm they fail because the compiler is missing.
- [x] Implement the smallest compiler that satisfies the official H3 base/reference format.
- [x] Run focused tests and refactor with all tests green.

### Task 2: H3 Asset Roles

**Files:**
- Modify: `factory/minimax_h3_video.py`
- Modify: `tests/test_minimax_h3_video.py`

**Interfaces:**
- Consumes: plain paths or `MiniMaxH3ImageInput(source, role)` values.
- Produces: official `first_frame`, `last_frame`, or `reference_image` request content.

- [x] Write failing payload and validation tests for explicit image roles.
- [x] Run the focused tests and confirm the old generic role behavior fails them.
- [x] Add the typed input and role validation while preserving plain paths as reference images.
- [x] Run focused tests and refactor with all tests green.

### Task 3: Provider-Aware Handoff

**Files:**
- Modify: `factory/video_handoff.py`
- Modify: `tests/test_video_handoff.py`

**Interfaces:**
- Consumes: resolved video provider and existing episode/asset data.
- Produces: official H3 prompts for MiniMax and unchanged prompts for other providers.

- [x] Write failing tests proving MiniMax receives H3 fields and gateway/Seedance output is unchanged.
- [x] Run focused tests and confirm the provider-aware behavior is absent.
- [x] Select the H3 compiler only when the resolved provider/model is MiniMax H3.
- [x] Run focused tests and refactor with all tests green.

### Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/iteration-log.md`
- Modify: `docs/pipeline-code-map.md`

**Interfaces:**
- Consumes: completed implementation and test evidence.
- Produces: documented H3 prompt behavior, external reference-repository location, and known audio policy.

- [x] Document the external official checkout and the provider-specific prompt path.
- [x] Run focused tests, full pytest, Ruff, compile checks, and secret scans.
- [x] Inspect a generated prompt sample for section order, dialogue fidelity, and unresolved labels.
- [x] Record verification evidence and remaining production risks.
