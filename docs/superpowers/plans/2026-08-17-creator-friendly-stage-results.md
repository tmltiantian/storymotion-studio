# Creator-friendly Stage Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw stage artifact code previews with creator-facing concept, script, storyboard, media, quality, and delivery views.

**Architecture:** Add a schema-aware presentation boundary in Python that converts registered stage documents into a small allow-listed public contract. Attach that contract to `StageDetail`, omit internal JSON/text artifacts from public stage artifacts, and render the contract through stage-specific React components while retaining the existing authorized image, audio, and video viewers.

**Tech Stack:** Python 3.11+, pytest, FastAPI service facade, React 19, TypeScript 5.9, Vitest, Testing Library, Playwright, Vite.

## Global Constraints

- The frontend must never render raw JSON, code blocks, MIME types, internal IDs, schema versions, hashes, storage paths, executor names, model request bodies, or internal manifest filenames.
- JSON remains the authoritative internal persistence format and existing pipeline files must not be modified.
- The workbench remains desktop-only and is verified at 1440x900.
- Existing stage execution, approval, repair, EVAL, delivery, and authorized media behavior must remain unchanged.
- Missing or malformed presentation data must produce a plain-language empty/error state and must never fall back to raw source.
- No new runtime dependency is required.

---

### Task 1: Stage presentation contract and schema-aware builders

**Files:**
- Create: `factory/stage_presentations.py`
- Create: `tests/test_stage_presentations.py`

**Interfaces:**
- Consumes: a `StageName | str` and a sequence of decoded JSON objects from artifacts registered to that stage.
- Produces: `build_stage_presentation(stage: StageName | str, documents: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None`.
- Produces: an allow-listed object with `stage`, `state`, and stage-specific creator fields; it never copies arbitrary input mappings.

- [ ] **Step 1: Write failing tests for concept, script, and storyboard projections**

```python
def test_script_presentation_keeps_creator_fields_and_drops_internal_fields():
    source = {
        "schema_version": "motion-comic-factory.script.v1",
        "project_id": "secret-internal-id",
        "episode_draft": {
            "title": "雨夜来电",
            "target_aspect_ratio": "9:16",
            "characters": [{
                "id": "char_01",
                "name": "阿眠",
                "role": "主角",
                "description": "谨慎但好奇",
                "visual_anchor": "short black hair",
                "voice_style": "清亮、克制",
            }],
            "shots": [{
                "id": "shot_001",
                "index": 1,
                "scene_title": "门外",
                "action": "她停在门边听见铃声。",
                "camera": "近景",
                "duration_seconds": 6.5,
                "dialogue": [{"speaker_id": "char_01", "emotion": "紧张", "text": "谁？"}],
                "visual_prompt": "internal prompt",
            }],
        },
    }

    result = build_stage_presentation("script", [source])

    assert result["title"] == "雨夜来电"
    assert result["characters"][0]["name"] == "阿眠"
    assert result["shots"][0]["dialogue"][0]["text"] == "谁？"
    assert "schema_version" not in repr(result)
    assert "project_id" not in repr(result)
    assert "visual_prompt" not in repr(result)
    assert "id" not in result["characters"][0]
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv/bin/pytest tests/test_stage_presentations.py -q`

Expected: FAIL because `factory.stage_presentations` does not exist.

- [ ] **Step 3: Implement strict helpers and the first three builders**

```python
def build_stage_presentation(stage, documents):
    selected = StageName(stage)
    builders = {
        StageName.CONCEPT: _concept,
        StageName.SCRIPT: _script,
        StageName.STORYBOARD: _storyboard,
    }
    builder = builders.get(selected)
    return builder(tuple(documents)) if builder else None

def _public_string(value: Any, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    return text[:maximum]
```

Build every returned dictionary field-by-field. Resolve dialogue speaker IDs to character display names inside the projection and calculate total duration from finite positive shot durations.

- [ ] **Step 4: Add tests and builders for assets, audio, video, edit, EVAL, and delivery**

Cover these schema roots and outputs:

```python
SUPPORTED_SCHEMAS = {
    "motion-comic-factory.asset-review.v1": "assets",
    "motion-comic-factory.audio.v1": "audio",
    "motion-comic-factory.video.v1": "video",
    "motion-comic-factory.edit.v1": "edit",
    "motion-comic-factory.delivery.v1": "deliver",
}
```

For EVAL, accept a `checks` list without exposing the source object. For unknown schemas, non-mapping entries, non-finite numbers, and malformed optional arrays, assert the builder returns either valid remaining sections or `{"stage": stage, "state": "unavailable"}`.

- [ ] **Step 5: Run presentation tests**

Run: `.venv/bin/pytest tests/test_stage_presentations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add factory/stage_presentations.py tests/test_stage_presentations.py
git commit -m "feat: add creator stage presentation models"
```

---

### Task 2: Workbench API presentation boundary and artifact filtering

**Files:**
- Modify: `factory/workbench_service.py`
- Modify: `tests/test_workbench_service.py`
- Modify: `tests/test_workbench_api.py`

**Interfaces:**
- Consumes: `build_stage_presentation(...)` from Task 1.
- Produces: each public `StageDetail` includes `presentation: Mapping[str, Any] | None`.
- Produces: `artifacts` contains authorized creator media and genuine binary exports only; JSON, Markdown, TXT, and SRT stage internals are not exposed as stage-viewable artifacts.

- [ ] **Step 1: Write a failing service test for the public boundary**

```python
def test_stage_detail_returns_presentation_without_internal_text_artifacts(service):
    run_through(service, "script")
    detail = service.stage_detail("episode_01", "script")

    assert detail["presentation"]["stage"] == "script"
    assert detail["presentation"]["characters"]
    assert detail["artifacts"] == []
    serialized = json.dumps(detail, ensure_ascii=False)
    assert "schema_version" not in serialized
    assert "manifest.json" not in serialized
    assert "application/json" not in serialized
```

- [ ] **Step 2: Run the focused service test and verify failure**

Run: `.venv/bin/pytest tests/test_workbench_service.py -k presentation -q`

Expected: FAIL because the response has no `presentation` and still publishes JSON artifacts.

- [ ] **Step 3: Add registered document loading and stage projection**

Add private methods with bounded reads through `AnchoredDirectory`:

```python
def _stage_documents(self, project_id: str, record: StageRecord) -> tuple[dict[str, Any], ...]:
    # Read only registered JSON artifacts, reject symlinks and files over 1 MiB,
    # and return decoded mapping roots. Never scan arbitrary project files.

def _stage_presentation(self, project_id: str, record: StageRecord) -> dict[str, Any] | None:
    return build_stage_presentation(record.stage, self._stage_documents(project_id, record))
```

Replace the stage list comprehension with a focused `_stage_public(...)` helper so presentation construction, artifact filtering, and active job attachment are independently testable.

- [ ] **Step 4: Filter internal text while retaining creator media**

Define a private predicate that excludes `text/*`, `application/json`, and `application/x-subrip` from stage artifact responses. Keep image, audio, video, and genuine non-text deliverables. Do not change media authorization or downloadable final outputs outside stage details.

- [ ] **Step 5: Add API regression tests**

Assert `/api/projects/{id}/stages/script` exposes structured creator fields and does not contain `media_url` for `script.json`. Assert audio/video media still carries authorized `/api/media/{artifact_id}` URLs and path traversal protection remains unchanged.

- [ ] **Step 6: Run service and API tests**

Run: `.venv/bin/pytest tests/test_stage_presentations.py tests/test_workbench_service.py tests/test_workbench_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add factory/workbench_service.py tests/test_workbench_service.py tests/test_workbench_api.py
git commit -m "feat: expose creator stage presentations"
```

---

### Task 3: Typed creator-facing React presentation views

**Files:**
- Modify: `sites/storymotion-studio/src/api/types.ts`
- Create: `sites/storymotion-studio/src/stages/StagePresentation.tsx`
- Create: `sites/storymotion-studio/src/stages/StagePresentation.test.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx`

**Interfaces:**
- Consumes: `StageDetail.presentation` from Task 2.
- Produces: `StagePresentation` discriminated union and `<StagePresentationView presentation={...} />`.
- Preserves: `<StageViewer>` for media artifacts only.

- [ ] **Step 1: Add failing component tests for script review**

```tsx
render(<StagePresentationView presentation={{
  stage: "script",
  state: "ready",
  title: "雨夜来电",
  total_duration_seconds: 6.5,
  characters: [{ name: "阿眠", role: "主角", description: "谨慎但好奇", appearance: "短发", voice: "清亮、克制" }],
  shots: [{ index: 1, title: "门外", action: "她停在门边听见铃声。", camera: "近景", duration_seconds: 6.5, dialogue: [{ speaker: "阿眠", emotion: "紧张", text: "谁？" }] }],
}} />);

expect(screen.getByRole("heading", { name: "雨夜来电" })).toBeVisible();
expect(screen.getByText("阿眠")).toBeVisible();
expect(screen.getByText("谁？")).toBeVisible();
expect(document.querySelector("pre")).toBeNull();
expect(screen.queryByText(/schema_version|application\/json|manifest\.json/)).toBeNull();
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `npm test -- --run src/stages/StagePresentation.test.tsx`

Working directory: `sites/storymotion-studio`

Expected: FAIL because the component and types do not exist.

- [ ] **Step 3: Define the discriminated TypeScript contract**

Add focused display types rather than `JsonObject`, including `CreatorCharacter`, `CreatorDialogue`, `CreatorShot`, `CreatorCheck`, and a `StagePresentation` union. Add `presentation: StagePresentation | null` to `StageDetail`.

```ts
export interface CreatorDialogue {
  speaker: string;
  emotion?: string;
  text: string;
}

export type StagePresentation =
  | ScriptPresentation
  | StoryboardPresentation
  | ConceptPresentation
  | MediaStagePresentation
  | EvalPresentation
  | DeliverPresentation
  | UnavailablePresentation;
```

- [ ] **Step 4: Implement compact stage-specific sections**

Use semantic headings, description lists, ordered shot rows, character cards, dialogue rows, and severity lists. The unavailable view copy is exactly:

```tsx
<strong>本阶段尚未生成可查看的成果</strong>
```

No component may stringify arbitrary objects or render `<pre>`, `<code>`, technical filenames, or MIME types.

- [ ] **Step 5: Integrate presentation and media in the workspace**

Change `ArtifactWorkspace` to render `StagePresentationView` first and `StageViewer` only when media artifacts remain. Remove the executor `<code>` label and replace the `STAGE ARTIFACTS` eyebrow with `阶段成果` language.

- [ ] **Step 6: Update workspace fixtures and tests**

Add `presentation: null` to generic fixtures, creator presentations to concept/script fixtures, and assertions that stage approval/revision actions still operate with the new result surface.

- [ ] **Step 7: Run frontend component tests**

Run: `npm test -- --run src/stages/StagePresentation.test.tsx src/projects/ProjectWorkspacePage.test.tsx`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add sites/storymotion-studio/src/api/types.ts sites/storymotion-studio/src/stages/StagePresentation.tsx sites/storymotion-studio/src/stages/StagePresentation.test.tsx sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx
git commit -m "feat: render creator-friendly stage results"
```

---

### Task 4: Remove technical captions and finish the desktop visual system

**Files:**
- Modify: `sites/storymotion-studio/src/stages/StageViewer.tsx`
- Modify: `sites/storymotion-studio/src/stages/StageViewer.test.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`

**Interfaces:**
- Consumes: media-only artifacts from Task 2.
- Produces: creator labels derived from stage, shot number, candidate position, speaker, duration, and resolution.

- [ ] **Step 1: Write failing tests that reject technical captions**

```tsx
render(<StageViewer stage="video" artifacts={videoArtifacts} />);
expect(screen.queryByText("video/mp4")).not.toBeInTheDocument();
expect(screen.queryByText("shot_03-candidate-1.mp4")).not.toBeInTheDocument();
expect(screen.getByText("第 3 镜 · 候选 1")).toBeVisible();
```

- [ ] **Step 2: Run the viewer test and verify failure**

Run: `npm test -- --run src/stages/StageViewer.test.tsx`

Working directory: `sites/storymotion-studio`

Expected: FAIL because `ArtifactFrame` renders filename and media type.

- [ ] **Step 3: Replace `ArtifactFrame` captions with creator labels**

Add an explicit `label` prop produced from stage and registered viewer metadata. Audio without speaker timing uses “完整配音”; image uses “角色或场景参考”; video with a shot ID uses a localized shot/candidate label. Unsupported internal files are not rendered.

- [ ] **Step 4: Add presentation styles**

Use unframed full-width sections separated by rules, character cards only for repeated characters, and stable two-column desktop grids. Keep card radius at 4px, body copy at readable desktop sizes, `letter-spacing: 0`, no viewport-scaled type, no nested cards, and no gradients.

- [ ] **Step 5: Run all frontend checks**

Run: `npm test -- --run && npm run typecheck && npm run lint && npm run build`

Working directory: `sites/storymotion-studio`

Expected: all commands PASS.

- [ ] **Step 6: Commit**

```bash
git add sites/storymotion-studio/src/stages/StageViewer.tsx sites/storymotion-studio/src/stages/StageViewer.test.tsx sites/storymotion-studio/src/styles/workbench.css
git commit -m "refactor: remove technical artifact presentation"
```

---

### Task 5: Full regression and desktop browser verification

**Files:**
- Modify: `sites/storymotion-studio/tests/workbench-flow.spec.ts`
- Modify: `sites/storymotion-studio/tests/workbench-media.spec.ts`
- Create: `docs/verification/creator-friendly-stage-results.md`

**Interfaces:**
- Consumes: completed backend and frontend behavior from Tasks 1-4.
- Produces: automated proof and a short verification record linked to the accepted design criteria.

- [ ] **Step 1: Add a browser regression for creator results**

Add a 1440x900 Playwright flow that opens a completed script stage and asserts:

```ts
await expect(page.getByRole("heading", { name: /剧本成果/ })).toBeVisible();
await expect(page.getByText("主角A")).toBeVisible();
await expect(page.locator("pre, code")).toHaveCount(0);
await expect(page.getByText(/schema_version|application\/json|manifest\.json/)).toHaveCount(0);
await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
```

- [ ] **Step 2: Run full backend regression**

Run: `.venv/bin/pytest -q`

Expected: PASS with no existing workbench, pipeline, provider, media authorization, approval, repair, EVAL, or delivery regression.

- [ ] **Step 3: Run full frontend regression**

Run: `npm test -- --run && npm run typecheck && npm run lint && npm run build`

Working directory: `sites/storymotion-studio`

Expected: PASS.

- [ ] **Step 4: Run Playwright at desktop size and inspect screenshots**

Run: `npm run dev -- --host 127.0.0.1 --port 5174` and `npx playwright test tests/workbench-flow.spec.ts tests/workbench-media.spec.ts --project=chromium` with the local API on port 8799.

Capture concept, script, storyboard, media, EVAL, and delivery states. Verify no clipping, overlapping text, horizontal overflow, technical data, or inaccessible controls.

- [ ] **Step 5: Record verification evidence**

Document commands, pass counts, inspected routes, screenshots, and any residual limitations in `docs/verification/creator-friendly-stage-results.md`. Do not include credentials, local secrets, internal absolute artifact paths, or raw manifests.

- [ ] **Step 6: Commit**

```bash
git add sites/storymotion-studio/tests/workbench-flow.spec.ts sites/storymotion-studio/tests/workbench-media.spec.ts docs/verification/creator-friendly-stage-results.md
git commit -m "test: verify creator stage result experience"
```
