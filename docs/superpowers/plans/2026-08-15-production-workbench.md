# StoryMotion Studio Production Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static showcase with a local-first production website that drives the fixed nine-stage pipeline through configurable review policies, scoped repair plans, safe paid-generation gates, and a data-backed works library.

**Architecture:** Keep Python as the only owner of project files, pipeline execution, review records, jobs, provider credentials, and media authorization. Add a localhost FastAPI service over those public domain services, then replace the current Vinext/Cloudflare showcase with a React/Vite operational UI that talks only to that API. Preserve the fixed nine-stage contracts while separating execution state from review state and letting policies decide where execution pauses.

**Tech Stack:** Python 3.12, dataclasses, FastAPI 0.141.1, Uvicorn 0.52.3, HTTPX 0.28.1, pytest, React 19.2.6, TypeScript 5.9.3, Vite 8.0.13, React Router 7.18.2, Lucide React, Playwright 1.62.1, FFmpeg/ffprobe.

## Global Constraints

- Bind the API to `127.0.0.1` by default and never return API key values.
- Keep `concept`, `script`, `storyboard`, `assets`, `audio`, `video`, `edit`, `eval`, and `deliver` as the only standard stages.
- Keep execution state and review state separate; do not use `blocked` for a normal review wait in newly written packages.
- Never allow approval to survive a revision artifact hash change.
- Never submit a billable video request without a one-use confirmation token bound to the displayed provider, model, seconds, resolution, and estimate.
- Preserve completed unaffected shot assets during scoped repair.
- Do not delete old showcase media until every file is either linked from a work record or listed in a historical archive manifest.
- Do not expose arbitrary filesystem paths through the media API.
- Do not run real paid generation in automated tests.
- Use TDD for every behavior change and commit each independently reviewable task.

---

## File Structure

### Python domain and API

- `factory/pipeline_contracts.py`: additive execution/review fields on stage records.
- `factory/pipeline_review.py`: review policy presets, revisions, review bundles, and approvals.
- `factory/pipeline_impact.py`: change requests, dependency rules, impact previews, and application.
- `factory/pipeline_jobs.py`: persistent background jobs, progress events, and one-project write locks.
- `factory/video_preflight.py`: paid-generation estimate and one-use confirmation tokens.
- `factory/work_catalog.py`: delivered-work and migrated-archive indexes.
- `factory/workbench_service.py`: public orchestration facade consumed by the API.
- `factory/workbench_api.py`: FastAPI routes and response models.
- `scripts/run_workbench.py`: starts API and frontend development processes.

### React website

- `sites/storymotion-studio/src/api/`: typed API client and event-stream client.
- `sites/storymotion-studio/src/app/`: router and operational page shell.
- `sites/storymotion-studio/src/projects/`: project list, production workspace, stage navigation, and review controls.
- `sites/storymotion-studio/src/stages/`: stage-specific artifact viewers.
- `sites/storymotion-studio/src/jobs/`: progress and failure recovery UI.
- `sites/storymotion-studio/src/works/`: delivered works and historical archive.
- `sites/storymotion-studio/src/settings/`: provider readiness and production defaults.
- `sites/storymotion-studio/src/styles/`: restrained responsive operational styling.

---

### Task 1: Review-Aware Pipeline Contracts

**Files:**
- Create: `factory/pipeline_review.py`
- Modify: `factory/pipeline_contracts.py`
- Modify: `factory/pipeline_store.py`
- Test: `tests/test_pipeline_review.py`
- Test: `tests/test_pipeline_contracts.py`
- Test: `tests/test_pipeline_store.py`

**Interfaces:**
- Produces: `ReviewPolicy`, `ReviewState`, `ApprovalPreset`, `ArtifactRevision`, `StageRevision`, `StageReview`, `ReviewValidation`, `ReviewConfig`.
- Produces: `resolve_review_config(preset: ApprovalPreset, overrides: Mapping[str, str]) -> ReviewConfig`.
- Produces: `write_stage_revision(project_dir, stage, artifacts, input_signature, executor) -> StageRevision`.
- Produces: `approve_stage_revision(project_dir, stage, revision, note, evidence) -> StageReview`.
- Produces: `validate_stage_review(project_dir: str | Path, stage: StageName) -> ReviewValidation`.

- [ ] **Step 1: Write failing contract and migration tests**

```python
def test_standard_preset_requires_expected_reviews():
    config = resolve_review_config(ApprovalPreset.STANDARD, {})
    assert config.policy_for(StageName.CONCEPT) is ReviewPolicy.AUTOMATIC
    assert config.policy_for(StageName.SCRIPT) is ReviewPolicy.MANUAL
    assert config.policy_for(StageName.VIDEO) is ReviewPolicy.MANUAL
    assert config.policy_for(StageName.DELIVER) is ReviewPolicy.MANUAL

def test_legacy_passed_record_migrates_as_approved():
    record = StageRecord.from_dict({"stage": "script", "state": "passed"})
    assert record.review_state is ReviewState.APPROVED

def test_revision_hash_change_invalidates_old_review(tmp_path):
    artifact = tmp_path / "script.json"
    artifact.write_text('{"version":1}', encoding="utf-8")
    revision = write_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        (artifact,),
        "script-signature",
        "original.script",
    )
    approve_stage_revision(
        tmp_path,
        StageName.SCRIPT,
        revision.number,
        "对白自然",
        (artifact,),
    )
    artifact.write_text("changed", encoding="utf-8")
    assert validate_stage_review(tmp_path, StageName.SCRIPT).valid is False
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_pipeline_review.py tests/test_pipeline_contracts.py tests/test_pipeline_store.py`

Expected: collection fails because review types and functions do not exist.

- [ ] **Step 3: Implement additive review contracts and persistence**

```python
class ReviewPolicy(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    GROUPED = "grouped"
    NOT_APPLICABLE = "not_applicable"

class ReviewState(str, Enum):
    NOT_READY = "not_ready"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    AUTO_APPROVED = "auto_approved"
    SKIPPED = "skipped"

@dataclass(frozen=True)
class StageRevision:
    stage: StageName
    number: int
    input_signature: str
    executor: str
    artifacts: tuple[ArtifactRevision, ...]
    created_at: str
```

Add `revision`, `review_policy`, `review_state`, and `review_blocks_progress` to `StageRecord`. Read old packages additively: old `passed` becomes `approved`; old manual `blocked` remains readable and is converted by the store when first reviewed.

- [ ] **Step 4: Run review/store tests and full pipeline contract tests**

Run: `.venv/bin/pytest -q tests/test_pipeline_review.py tests/test_pipeline_contracts.py tests/test_pipeline_store.py tests/test_pipeline_migration.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add factory/pipeline_review.py factory/pipeline_contracts.py factory/pipeline_store.py tests/test_pipeline_review.py tests/test_pipeline_contracts.py tests/test_pipeline_store.py
git commit -m "feat: add stage review contracts"
```

### Task 2: Configurable Review Runner and Grouped Gates

**Files:**
- Modify: `factory/pipeline_modes.py`
- Modify: `factory/pipeline_runner.py`
- Modify: `factory/pipeline_store.py`
- Modify: `factory/pipeline_cli.py`
- Test: `tests/test_pipeline_runner.py`
- Test: `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: review contracts from Task 1.
- Produces: `run_pipeline(project_dir: str | Path, *, through: StageName | None = None, enable_live: bool = False, executor: StageExecutor = execute_native_stage, review_config: ReviewConfig | None = None) -> PipelineRunResult`.
- Produces: `approve_review_bundle(project_dir, stages, note, evidence) -> ProductionPackage`.
- Produces: status fields `review_state`, `review_policy`, `current_revision`, and `required_action`.

- [ ] **Step 1: Write failing runner tests for all four policies**

```python
def test_manual_policy_runs_stage_then_waits_for_review(tmp_path):
    result = run_pipeline(root, through=StageName.SCRIPT, review_config=manual_script)
    record = load_production_package(root).stages[1]
    assert record.state is StageState.PASSED
    assert record.review_state is ReviewState.AWAITING_REVIEW
    assert result.next_stage is StageName.SCRIPT

def test_grouped_policy_runs_members_and_stops_at_group_terminal(tmp_path):
    result = run_pipeline(root, through=StageName.AUDIO, review_config=story_bundle)
    assert calls == [CONCEPT, SCRIPT, STORYBOARD, ASSETS, AUDIO]
    assert result.next_stage is StageName.AUDIO

def test_automatic_policy_continues_without_user_action(tmp_path):
    assert run_pipeline(root, through=StageName.CONCEPT, review_config=quick).success
    assert package.stages[0].review_state is ReviewState.AUTO_APPROVED
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_pipeline_runner.py tests/test_pipeline_cli.py`

Expected: failures show the runner still conflates manual review with `StageState.BLOCKED`.

- [ ] **Step 3: Implement policy-aware progress**

Replace `ModeStep.manual_gate` as the source of review behavior with `ReviewConfig`; retain it only as a compatibility default during migration. A stage is progress-complete when execution passed and either review does not block or its revision is approved. Group members set `review_blocks_progress=False`; the group terminal sets it to `True` and approval updates every bound member revision atomically.

Expose CLI options:

```text
factory create --mode original --project demo --title Demo --idea "two cats" --approval-preset standard
factory approve PROJECT --stage STAGE --revision N --note NOTE --evidence FILE
factory request-changes PROJECT --stage STAGE --revision N --reason REASON
```

- [ ] **Step 4: Run runner, CLI, migration, and generic-stage tests**

Run: `.venv/bin/pytest -q tests/test_pipeline_runner.py tests/test_pipeline_cli.py tests/test_pipeline_migration.py tests/test_pipeline_generic_stages.py`

Expected: all pass and newly written review waits use `StageState.PASSED` plus `ReviewState.AWAITING_REVIEW`.

- [ ] **Step 5: Commit**

```bash
git add factory/pipeline_modes.py factory/pipeline_runner.py factory/pipeline_store.py factory/pipeline_cli.py tests/test_pipeline_runner.py tests/test_pipeline_cli.py
git commit -m "feat: make pipeline reviews configurable"
```

### Task 3: Change Requests and Scoped Impact Plans

**Files:**
- Create: `factory/pipeline_impact.py`
- Modify: `factory/pipeline_context.py`
- Modify: `factory/pipeline_store.py`
- Modify: `factory/pipeline_generic_stages.py`
- Test: `tests/test_pipeline_impact.py`
- Test: `tests/test_pipeline_generic_stages.py`

**Interfaces:**
- Produces: `ChangeScope`, `ChangeRequest`, `ImpactEntry`, `ImpactPlan`.
- Produces: `preview_impact(project_dir, request) -> ImpactPlan`.
- Produces: `apply_impact_plan(project_dir, plan_id) -> ProductionPackage`.
- Produces: `StageContext.repair_scope: Mapping[str, tuple[str, ...]]`.

- [ ] **Step 1: Write failing dependency and preservation tests**

```python
def test_single_dialogue_change_targets_only_bound_audio_and_video_shot():
    plan = preview_impact(root, ChangeRequest(stage=SCRIPT, dialogue_ids=("d2",)))
    assert plan.affected[AUDIO] == ("d2",)
    assert plan.affected[VIDEO] == ("shot_03",)
    assert plan.affected[EDIT] == ("timeline",)

def test_apply_impact_keeps_unaffected_video_artifacts(tmp_path):
    plan = preview_impact(root, ChangeRequest(stage=STORYBOARD, shot_ids=("shot_03",)))
    apply_impact_plan(root, plan.plan_id)
    scope = load_active_repair_scope(root)
    assert scope["video"] == ("shot_03",)
    assert Path("shot_02.mp4") in registered_preserved_artifacts(root)
```

- [ ] **Step 2: Run impact tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_pipeline_impact.py tests/test_pipeline_generic_stages.py`

Expected: import failure for `pipeline_impact`.

- [ ] **Step 3: Implement the dependency graph and explicit apply step**

```python
DEFAULT_DEPENDENCIES = {
    "dialogue": {AUDIO: "dialogue_ids", VIDEO: "bound_shot_ids", EDIT: "timeline"},
    "character": {ASSETS: "character_ids", VIDEO: "character_shot_ids", EDIT: "timeline"},
    "shot": {STORYBOARD: "shot_ids", VIDEO: "shot_ids", EDIT: "timeline"},
    "subtitle_style": {EDIT: "subtitles", EVAL: "full", DELIVER: "full"},
}
```

Persist the preview before applying it. On apply, record affected item IDs and mark downstream stage execution stale while retaining artifacts. Pass the repair scope to executors; the gateway batch already reuses unchanged per-shot signatures, so only targeted jobs submit. Update generic edit to rebuild from preserved and regenerated clips in storyboard order.

- [ ] **Step 4: Run impact, batch, edit, and runner tests**

Run: `.venv/bin/pytest -q tests/test_pipeline_impact.py tests/test_gateway_video_batch.py tests/test_pipeline_generic_stages.py tests/test_pipeline_runner.py`

Expected: all pass; a one-shot change does not submit unchanged shot jobs.

- [ ] **Step 5: Commit**

```bash
git add factory/pipeline_impact.py factory/pipeline_context.py factory/pipeline_store.py factory/pipeline_generic_stages.py tests/test_pipeline_impact.py tests/test_pipeline_generic_stages.py
git commit -m "feat: add scoped pipeline repair plans"
```

### Task 4: Persistent Jobs and Paid Video Preflight

**Files:**
- Create: `factory/pipeline_jobs.py`
- Create: `factory/video_preflight.py`
- Modify: `factory/video_provider.py`
- Modify: `factory/gateway_video_batch.py`
- Test: `tests/test_pipeline_jobs.py`
- Test: `tests/test_video_preflight.py`

**Interfaces:**
- Produces: `JobRecord`, `JobEvent`, `ProjectBusyError`, `JobManager.submit()`, `JobManager.resume()`.
- Produces: `VideoPreflight`, `VideoGenerationRequest`, `GenerationTokenError`, `build_video_preflight(project_dir, shot_ids) -> VideoPreflight`.
- Produces: `issue_generation_token(project_dir: str | Path, preflight: VideoPreflight) -> str` and `consume_generation_token(project_dir: str | Path, token: str, request: VideoGenerationRequest) -> None`.

- [ ] **Step 1: Write failing persistence, locking, and token tests**

```python
def test_job_survives_manager_restart(tmp_path):
    manager = JobManager(tmp_path)
    job_id = manager.submit(project_id="p1", operation="video_test", payload={})
    restored = JobManager(tmp_path).get(job_id)
    assert restored.status == "queued"

def test_same_project_rejects_second_mutating_job(tmp_path):
    manager = JobManager(tmp_path)
    manager.submit(project_id="p1", operation="video_test", payload={})
    with pytest.raises(ProjectBusyError):
        manager.submit(project_id="p1", operation="approve_stage", payload={})

def test_generation_token_is_bound_and_single_use(tmp_path):
    project_dir = create_ready_video_project(tmp_path)
    preflight = build_video_preflight(project_dir, ("shot_03",))
    request = VideoGenerationRequest.from_preflight(preflight)
    token = issue_generation_token(project_dir, preflight)
    consume_generation_token(project_dir, token, request)
    with pytest.raises(GenerationTokenError):
        consume_generation_token(project_dir, token, request)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_pipeline_jobs.py tests/test_video_preflight.py`

Expected: missing job and preflight modules.

- [ ] **Step 3: Implement atomic job journals and confirmation tokens**

Store jobs under `runs/.workbench/jobs/<job_id>.json` and append events to `<job_id>.jsonl`. Persist provider task IDs before polling. The token digest binds project, revision hashes, shot IDs, provider, model, resolution, output seconds, estimate, and a random nonce; consumption atomically writes `consumed_at` before any billable call.

- [ ] **Step 4: Run job, H3, gateway, and security tests**

Run: `.venv/bin/pytest -q tests/test_pipeline_jobs.py tests/test_video_preflight.py tests/test_minimax_h3_video.py tests/test_gateway_video.py tests/test_gateway_video_batch.py tests/test_provider_profile.py`

Expected: all pass and no report contains credentials.

- [ ] **Step 5: Commit**

```bash
git add factory/pipeline_jobs.py factory/video_preflight.py factory/video_provider.py factory/gateway_video_batch.py tests/test_pipeline_jobs.py tests/test_video_preflight.py
git commit -m "feat: add persistent jobs and video preflight"
```

### Task 5: Public Workbench Service and Local API

**Files:**
- Create: `factory/workbench_service.py`
- Create: `factory/workbench_api.py`
- Modify: `requirements.txt`
- Test: `tests/test_workbench_service.py`
- Test: `tests/test_workbench_api.py`

**Interfaces:**
- Consumes: review, impact, job, preflight, and pipeline services.
- Produces: `WorkbenchService` methods for project, stage, job, media, and provider routes.
- Produces: `create_workbench_app(service: WorkbenchService) -> FastAPI`.

- [ ] **Step 1: Add exact API dependencies and failing route tests**

Add:

```text
fastapi==0.141.1
uvicorn==0.52.3
httpx==0.28.1
```

Write tests:

```python
def test_project_detail_contains_execution_and_review_states(client):
    response = client.get("/api/projects/episode_01")
    assert response.status_code == 200
    assert response.json()["stages"][0]["review_state"] == "awaiting_review"

def test_media_route_rejects_raw_paths(client):
    assert client.get("/api/media/../../.env").status_code in {400, 404}

def test_provider_status_never_returns_secrets(client):
    payload = client.get("/api/settings/providers").json()
    assert "sk-" not in json.dumps(payload)
```

- [ ] **Step 2: Install dependencies and verify RED**

Run: `.venv/bin/pip install -r requirements.txt && .venv/bin/pytest -q tests/test_workbench_service.py tests/test_workbench_api.py`

Expected: route tests fail because the app factory does not exist.

- [ ] **Step 3: Implement service facade, routes, SSE, and authorized media**

Use dependency-injected `WorkbenchService` in tests. Return artifact IDs instead of paths. Implement byte-range media responses with `Accept-Ranges: bytes`, `206 Partial Content`, exact `Content-Range`, and MIME detection. Restrict CORS to configured localhost frontend origins.

- [ ] **Step 4: Run API, CLI, security, and media tests**

Run: `.venv/bin/pytest -q tests/test_workbench_service.py tests/test_workbench_api.py tests/test_pipeline_cli.py tests/test_gateway_video.py tests/test_provider_profile.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt factory/workbench_service.py factory/workbench_api.py tests/test_workbench_service.py tests/test_workbench_api.py
git commit -m "feat: expose local workbench API"
```

### Task 6: Replace the Static Showcase with the Production App Shell

**Files:**
- Create directory: `sites/storymotion-studio/`
- Create: `sites/storymotion-studio/package.json`
- Create: `sites/storymotion-studio/vite.config.ts`
- Create: `sites/storymotion-studio/tsconfig.json`
- Create: `sites/storymotion-studio/eslint.config.mjs`
- Create: `sites/storymotion-studio/index.html`
- Create: `sites/storymotion-studio/src/main.tsx`
- Create: `sites/storymotion-studio/src/app/App.tsx`
- Create: `sites/storymotion-studio/src/app/AppShell.tsx`
- Create: `sites/storymotion-studio/src/api/client.ts`
- Create: `sites/storymotion-studio/src/api/types.ts`
- Create: `sites/storymotion-studio/src/styles/base.css`
- Create: `sites/storymotion-studio/src/styles/workbench.css`
- Test: `sites/storymotion-studio/src/app/AppShell.test.tsx`

**Interfaces:**
- Consumes: Task 5 JSON API.
- Produces: routes `/projects`, `/projects/:id`, `/works`, `/works/:id`, `/settings`.
- Produces: `apiClient` with typed project, stage, review, job, work, and settings methods.

- [ ] **Step 1: Write failing navigation and shell tests**

```tsx
it("opens the current project action without searching the stage list", async () => {
  render(<App />, { route: "/projects" });
  await user.click(await screen.findByText("确认分镜"));
  expect(location.pathname).toBe("/projects/episode_01/stages/storyboard");
});

it("keeps projects, works, and settings as primary navigation", () => {
  render(<AppShell />);
  expect(screen.getByRole("link", { name: "制作项目" })).toBeVisible();
  expect(screen.getByRole("link", { name: "作品中心" })).toBeVisible();
  expect(screen.getByRole("link", { name: "设置" })).toBeVisible();
});
```

- [ ] **Step 2: Replace Cloudflare/Vinext dependencies and verify RED**

Use React Router `7.18.2`, Vitest `4.1.10`, Testing Library React `16.3.2`, Testing Library User Event `14.6.4`, JSDOM `30.0.1`, Playwright `1.62.1`, and plain Vite. Define `dev`, `build`, `test`, `typecheck`, and `lint` scripts in `package.json`. The new site contains no Drizzle, Cloudflare Worker, Vinext, D1, R2, hard-coded work arrays, or template examples. Keep the complete old `sites/pet-video-showcase` directory untouched until Task 9 migrates its media and Task 10 verifies removal safety.

Run: `cd sites/storymotion-studio && npm test -- --run`

Expected: tests fail before the app shell exists.

- [ ] **Step 3: Implement the operational shell**

Use a quiet neutral palette with green only for passed states, amber for review waits, red for failures, and blue for the current operation. Keep cards to repeated project/work items; use full-width bands and unframed panels for page structure. Use Lucide icons with tooltips for refresh, filters, download, history, and settings.

- [ ] **Step 4: Run unit tests, typecheck, lint, and production build**

Run: `cd sites/storymotion-studio && npm test -- --run && npm run typecheck && npm run lint && npm run build`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sites/storymotion-studio
git commit -m "feat: replace showcase with workbench shell"
```

### Task 7: Project Workspace, Stage Navigation, and Review Actions

**Files:**
- Create: `sites/storymotion-studio/src/projects/ProjectListPage.tsx`
- Create: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx`
- Create: `sites/storymotion-studio/src/projects/StageRail.tsx`
- Create: `sites/storymotion-studio/src/projects/ReviewPanel.tsx`
- Create: `sites/storymotion-studio/src/projects/ImpactDialog.tsx`
- Test: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx`

**Interfaces:**
- Produces: stage state visualization, approve, request changes, and impact preview actions.
- Consumes: `GET project`, `GET stage`, `approve`, `request-changes`, and `impact-plan` API methods.

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("shows execution and review state independently", async () => {
  renderWorkspace({ execution_state: "passed", review_state: "awaiting_review" });
  expect(await screen.findByText("成果已生成"));
  expect(screen.getByText("等待确认"));
});

it("previews invalidated shots and cost before applying changes", async () => {
  await user.click(screen.getByRole("button", { name: "退回修改" }));
  await user.click(screen.getByLabelText("动作不连贯"));
  await user.click(screen.getByRole("button", { name: "查看影响" }));
  expect(await screen.findByText("将重做 1 个视频镜头"));
  expect(screen.getByText("其他 7 个镜头继续复用"));
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd sites/storymotion-studio && npm test -- --run src/projects/ProjectWorkspacePage.test.tsx`

Expected: missing workspace components.

- [ ] **Step 3: Implement project list, fixed stage rail, and review flow**

Desktop layout uses project navigation, artifact workspace, and review inspector. On narrow screens, collapse project navigation into a drawer and place review actions after the artifact viewer; never overlay actions on media. Require an issue category and description for changes. Approvals bind the displayed revision and disable while submitting.

- [ ] **Step 4: Run project interaction tests and accessibility checks**

Run: `cd sites/storymotion-studio && npm test -- --run src/projects && npm run lint`

Expected: all pass with no missing accessible names.

- [ ] **Step 5: Commit**

```bash
git add sites/storymotion-studio/src/projects sites/storymotion-studio/src/styles
git commit -m "feat: add project review workspace"
```

### Task 8: Stage Viewers, Video Preflight, and Job Recovery

**Files:**
- Create: `sites/storymotion-studio/src/stages/StageViewer.tsx`
- Create: `sites/storymotion-studio/src/stages/TextViewer.tsx`
- Create: `sites/storymotion-studio/src/stages/ImageViewer.tsx`
- Create: `sites/storymotion-studio/src/stages/AudioViewer.tsx`
- Create: `sites/storymotion-studio/src/stages/VideoViewer.tsx`
- Create: `sites/storymotion-studio/src/stages/EvalViewer.tsx`
- Create: `sites/storymotion-studio/src/jobs/VideoPreflight.tsx`
- Create: `sites/storymotion-studio/src/jobs/JobProgress.tsx`
- Create: `sites/storymotion-studio/src/api/events.ts`
- Test: `sites/storymotion-studio/src/stages/StageViewer.test.tsx`
- Test: `sites/storymotion-studio/src/jobs/VideoPreflight.test.tsx`

**Interfaces:**
- Consumes: artifact descriptors, authorized media URLs, preflight, token, job, and event APIs.
- Produces: stage-specific preview registry and safe test/batch generation controls.

- [ ] **Step 1: Write failing viewer and paid-gate tests**

```tsx
it("renders audio dialogue timing without overlapping controls", () => {
  render(<StageViewer stage="audio" artifacts={audioArtifacts} />);
  expect(screen.getByRole("button", { name: "播放黑白猫台词" })).toBeVisible();
  expect(screen.getByText("00:04.20–00:06.10")).toBeVisible();
});

it("requires a fresh token before batch generation", async () => {
  render(<VideoPreflight preflight={estimate} />);
  expect(screen.getByRole("button", { name: "批量生成全片" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
  expect(await screen.findByRole("button", { name: "批量生成全片" })).toBeEnabled();
});

it("resumes progress from persisted job state after remount", async () => {
  const { unmount } = render(<JobProgress jobId="job-1" />);
  unmount();
  render(<JobProgress jobId="job-1" />);
  expect(await screen.findByText("已完成 2 / 8 镜"));
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd sites/storymotion-studio && npm test -- --run src/stages src/jobs`

Expected: missing viewers and job components.

- [ ] **Step 3: Implement viewers and progress recovery**

Video controls include frame step, 0.5x/1x playback, mute, dialogue-only, candidate selection, and issue-at-current-time. Use stable aspect-ratio containers. SSE reconnects with the last event ID and falls back to `GET /api/jobs/{id}` after 5 seconds without events.

- [ ] **Step 4: Run unit tests and Playwright media interaction tests**

Run: `cd sites/storymotion-studio && npm test -- --run src/stages src/jobs && npx playwright test tests/workbench-media.spec.ts`

Expected: all pass in the local desktop web viewport.

- [ ] **Step 5: Commit**

```bash
git add sites/storymotion-studio/src/stages sites/storymotion-studio/src/jobs sites/storymotion-studio/src/api/events.ts sites/storymotion-studio/tests/workbench-media.spec.ts
git commit -m "feat: add stage viewers and generation progress"
```

### Task 9: Works Catalog, Historical Migration, and Settings

**Files:**
- Create: `factory/work_catalog.py`
- Create: `scripts/migrate_showcase_works.py`
- Create: `sites/storymotion-studio/src/works/WorksPage.tsx`
- Create: `sites/storymotion-studio/src/works/WorkDetailPage.tsx`
- Create: `sites/storymotion-studio/src/settings/SettingsPage.tsx`
- Modify: `factory/workbench_service.py`
- Modify: `factory/workbench_api.py`
- Modify: `tests/test_workbench_api.py`
- Test: `tests/test_work_catalog.py`
- Test: `sites/storymotion-studio/src/works/WorksPage.test.tsx`
- Test: `sites/storymotion-studio/src/settings/SettingsPage.test.tsx`

**Interfaces:**
- Produces: `build_work_catalog(runs_dir, archive_manifest) -> WorkCatalog`.
- Produces: `migrate_showcase_media(source_public, archive_root) -> ArchiveManifest`.
- Consumes: delivery manifests and current provider profile.
- Produces: `GET /api/works` and `GET /api/works/{id}` backed by `WorkCatalog`.

- [ ] **Step 1: Write failing catalog and page tests**

```python
def test_catalog_reads_delivered_projects_and_archived_media(tmp_path):
    catalog = build_work_catalog(runs, archive_manifest)
    assert [item.title for item in catalog.works] == ["咪要去面试", "旧城来信"]
    assert catalog.works[0].versions[0].sha256

def test_migration_never_drops_unclassified_media(tmp_path):
    manifest = migrate_showcase_media(public, archive)
    assert manifest.unclassified == ("evidence/orphan.jpg",)
```

```tsx
it("loads works from the API instead of hard-coded archive arrays", async () => {
  render(<WorksPage />);
  expect(await screen.findByText("咪要去面试"));
  expect(screen.getByText("V3.1"));
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_work_catalog.py && cd sites/storymotion-studio && npm test -- --run src/works src/settings`

Expected: missing catalog and pages.

- [ ] **Step 3: Implement migration, works pages, and settings**

Move only media identified by the generated manifest. Keep unclassified files in the archive and show them under “历史归档”. Settings shows readiness, model name, default voice mapping, output defaults, concurrency, and fee cap; credentials are boolean status only.

- [ ] **Step 4: Execute a dry migration and verify every old media file is classified**

Run: `.venv/bin/python scripts/migrate_showcase_works.py --source sites/pet-video-showcase/public --destination output/workbench_archive --dry-run`

Expected: exit 0; report lists every media file as linked or unclassified and copies nothing.

- [ ] **Step 5: Run catalog, API, works, and settings tests**

Run: `.venv/bin/pytest -q tests/test_work_catalog.py tests/test_workbench_api.py && cd sites/storymotion-studio && npm test -- --run src/works src/settings`

Expected: all pass.

- [ ] **Step 6: Execute the classified migration**

Run: `.venv/bin/python scripts/migrate_showcase_works.py --source sites/pet-video-showcase/public --destination output/workbench_archive`

Expected: exit 0; `output/workbench_archive/archive_manifest.json` exists, every copied file has a SHA-256, and source files remain untouched.

- [ ] **Step 7: Commit**

```bash
git add factory/work_catalog.py factory/workbench_service.py factory/workbench_api.py scripts/migrate_showcase_works.py tests/test_work_catalog.py tests/test_workbench_api.py sites/storymotion-studio/src/works sites/storymotion-studio/src/settings
git commit -m "feat: add works catalog and settings"
```

### Task 10: Local Launcher, End-to-End Verification, and Old Site Removal

**Files:**
- Create: `scripts/run_workbench.py`
- Create: `tests/test_workbench_launcher.py`
- Create: `sites/storymotion-studio/tests/workbench-flow.spec.ts`
- Modify: `README.md`
- Modify: `docs/pipeline-code-map.md`
- Modify: `docs/iteration-log.md`
- Delete after migration verification: remaining `sites/pet-video-showcase/`

**Interfaces:**
- Produces: `build_launch_config(api_host: str = "127.0.0.1", api_port: int = 8787, web_port: int = 5173) -> LaunchConfig` and one local command that starts API and frontend, prints URLs, forwards termination, and selects free ports.

- [ ] **Step 1: Write failing launcher and end-to-end tests**

```python
def test_launcher_selects_distinct_free_ports(monkeypatch):
    config = build_launch_config(api_port=8787, web_port=8787)
    assert config.api_port != config.web_port
    assert config.api_host == "127.0.0.1"
```

Playwright flow:

```ts
test("creates, reviews, revises, and delivers an offline project", async ({ page }) => {
  await page.goto("/projects");
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByLabel("项目名称").fill("雨天纸箱");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page.getByText("等待确认")).toBeVisible();
  await page.getByRole("button", { name: "退回修改" }).click();
  await expect(page.getByText("影响范围")).toBeVisible();
});
```

- [ ] **Step 2: Run launcher/E2E tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_workbench_launcher.py && cd sites/storymotion-studio && npx playwright test tests/workbench-flow.spec.ts`

Expected: launcher import or flow fails before implementation.

- [ ] **Step 3: Implement launcher and update documentation**

Start Uvicorn and Vite as child processes, print both local URLs, stop both on SIGINT/SIGTERM, and never print environment values. Document project creation, approval presets, local repair, test-shot gate, and works migration.

- [ ] **Step 4: Perform real browser QA for the local desktop workbench**

Use Playwright screenshots at `1440x900`. Verify no overlap, no clipped text, stable media aspect ratios, keyboard navigation, visible focus, error states, empty states, and the full create/review/repair flow. Inspect browser console and network failures. Phone and tablet layouts are outside the local-workbench delivery scope.

- [ ] **Step 5: Remove old site only after catalog and browser verification**

Verify the migration report accounts for every old `public/` media file, then remove remaining old page, Cloudflare worker, D1 schema, Vinext config, and old rendered-HTML tests. Do not delete migrated or unclassified archive media.

- [ ] **Step 6: Run complete release verification**

Run:

```bash
.venv/bin/ruff check factory factory_cli.py scripts tests
.venv/bin/python -m compileall -q factory factory_cli.py scripts
.venv/bin/pytest -q
cd sites/storymotion-studio && npm test -- --run && npm run typecheck && npm run lint && npm run build && npx playwright test
git diff --check
```

Expected: every command passes; no paid provider request occurs.

- [ ] **Step 7: Audit release contents**

Confirm no `.env`, API key, provider response URL, generated cloud clip, `node_modules`, build output, MiniMax upstream checkout, or unclassified private source media is staged.

- [ ] **Step 8: Commit**

```bash
git add README.md docs factory scripts tests sites/storymotion-studio
git add -u sites/pet-video-showcase
git commit -m "feat: deliver StoryMotion production workbench"
```
