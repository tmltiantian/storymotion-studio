# Interview Cat Physics Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regenerate only S004, S012, and S018 with correct anatomy, prop counts, and physical causality while preserving the existing soundtrack and timeline.

**Architecture:** Add a prompt-level exception for source UI text, then encode shot-specific still and motion constraints in the reviewed analysis. Existing content-addressed state files will invalidate only the three changed shots. Recompose and verify into a new sealed delivery.

**Tech Stack:** Python 3.12, pytest, Seedream 4.5, Seedance 2.0, FFmpeg, Delivery Eval.

## Global Constraints

- Do not overwrite the current sealed delivery.
- Preserve 30 fps, 204.766667 seconds, existing audio, subtitles, and all unaffected shots.
- Accept no extra limb, detached paw, duplicate cat, duplicate cup, floating object, or impossible contact.

---

### Task 1: Preserve In-Device UI Text

**Files:**
- Modify: `tests/test_source_locked_cat_replica.py`
- Modify: `factory/source_locked_cat_replica.py`

**Interfaces:**
- Consumes: `annotation["preserve_source_ui_text"]: bool`
- Produces: `_anchor_prompt(...) -> str` that preserves source device UI while still removing overlays.

- [ ] Add a failing test asserting that the UI-preservation prompt keeps source in-device text and does not issue a blanket `no text` rule.
- [ ] Run the focused test and confirm the expected failure.
- [ ] Implement the conditional prompt clause.
- [ ] Run the focused test and source-locked replica test module.

### Task 2: Encode Three Shot Repairs

**Files:**
- Modify: `/Users/tml/Desktop/宠物短剧样片/咪要去面试_黑白猫复刻_20260805/reference/shot_analysis.json`

**Interfaces:**
- Consumes: existing `extra_anchor_constraints`, `anchor_label_constraints`, and `extra_video_constraints` fields.
- Produces: content-addressed invalidation for S004, S012, and S018 only.

- [ ] Add S004 phone-only, one-paw and UI-preservation constraints.
- [ ] Add S012 exactly-two-forepaws constraints.
- [ ] Add S018 exactly-one-cup start-state and motion-causality constraints.
- [ ] Load the analysis through production validation and inspect the generated prompts.

### Task 3: Regenerate and Select

**Files:**
- Regenerate: project anchor and selected video assets for S004, S012, and S018.

**Interfaces:**
- Consumes: reviewed analysis and gateway models.
- Produces: valid MP4 shot assets with original editorial frame counts after normalization.

- [ ] Generate the invalidated anchors and inspect them at full resolution.
- [ ] Generate the invalidated videos and create dense review sheets.
- [ ] Reject and retry any candidate that violates anatomy, object count, contact, or causality.

### Task 4: Recompose and Seal V2

**Files:**
- Create: `/Users/tml/Desktop/宠物短剧样片/咪要去面试_黑白猫复刻_20260805/deliveries/sealed_delivery_v2_20260810/`

**Interfaces:**
- Consumes: 43 selected shots, unchanged source audio, and existing subtitles.
- Produces: a separately sealed V2 master and verification evidence.

- [ ] Recompose the exact timeline and verify frame count, duration, audio, and subtitles.
- [ ] Review transition and dense contact sheets for the repaired regions and their neighbors.
- [ ] Run the full automated test suite.
- [ ] Complete Delivery Eval semantic review and seal the V2 package.
