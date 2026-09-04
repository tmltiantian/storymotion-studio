# Resume-First Creator Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make StoryMotion Studio default to resuming an unfinished production, while keeping detailed generation and review controls available on demand.

**Architecture:** Add a pure task-state adapter that translates existing project and stage records into creator-facing task copy. Build small presentational components around that adapter for the resume card, next-task list, task header, and accessible expandable controls. Keep all requests, routing, paid-video preflight, approvals, and job recovery in their existing containers so the backend contract and its safety gates remain unchanged.

**Tech Stack:** React 19, TypeScript, React Router, Vitest, Testing Library, Lucide, existing FastAPI workbench API.

**Spec:** `docs/superpowers/specs/2026-09-04-resume-first-creator-workbench-design.md`

## Global Constraints

- Do not change the FastAPI workbench API, the pipeline state machine, approval evidence, model routing, or any paid-generation policy.
- Keep `VideoPreflight`, `confirmVideo`, `testVideo`, `generateVideo`, and existing job recovery as the only video submission path.
- Use creator-facing Chinese copy for default views; technical identifiers and detailed state may appear only inside expanded detail views.
- Keep direct stage routes working and give non-current routes an explicit return-to-current-task link.
- New interactive disclosures must be keyboard accessible and use `aria-expanded` plus `aria-controls`.
- Preserve the existing abort, stale-route, mutation-owner, and focus-return behavior.

---

## File Structure

- Create: `sites/storymotion-studio/src/projects/creatorTask.ts` — pure mapping from existing project/stage records to task title, explanation, primary action, and post-action preview.
- Create: `sites/storymotion-studio/src/projects/creatorTask.test.ts` — table-driven coverage of every task status mapping.
- Create: `sites/storymotion-studio/src/projects/ResumeProjectCard.tsx` — the homepage continuation card and compact next-task rows.
- Create: `sites/storymotion-studio/src/projects/ResumeProjectCard.test.tsx` — resume routing and empty-state behavior.
- Create: `sites/storymotion-studio/src/projects/ExpandablePanel.tsx` — accessible reusable disclosure for advanced controls.
- Create: `sites/storymotion-studio/src/projects/ExpandablePanel.test.tsx` — disclosure state and accessibility assertions.
- Create: `sites/storymotion-studio/src/projects/CurrentTaskPanel.tsx` — project-workspace task header, next-step copy, and direct-route context.
- Create: `sites/storymotion-studio/src/projects/CurrentTaskPanel.test.tsx` — task context and focus content assertions.
- Modify: `sites/storymotion-studio/src/projects/ProjectListPage.tsx` — replace dashboard-first layout with resume, next-task and project-library sections.
- Create: `sites/storymotion-studio/src/projects/ProjectListPage.test.tsx` — page-level continuation and filtering coverage.
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx` — compose the task header and disclosures while retaining request logic.
- Modify: `sites/storymotion-studio/src/jobs/VideoPreflight.tsx` and `sites/storymotion-studio/src/jobs/VideoPreflight.test.tsx` — present the confirmed paid batch as one creator-readable confirmation card without weakening the existing confirmation sequence.
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx` — cover current-task priority, direct-route context, and video disclosure integration.
- Modify: `sites/storymotion-studio/src/styles/workbench.css` — add resume, next-task, task-header, disclosure, risk-card and responsive styles.

## Task 1: Derive creator task state from the existing API records

**Files:**
- Create: `sites/storymotion-studio/src/projects/creatorTask.ts`
- Create: `sites/storymotion-studio/src/projects/creatorTask.test.ts`

**Interfaces:**
- Consumes: `ProjectDetail`, `StageDetail`, `StageName` from `../api/types` and `stageLabel` from `./StageRail`.
- Produces: `CreatorTask`, `deriveCreatorTask(project, stage)`, `selectResumeProject(projects)`, and `isProjectInProgress(project)`.
- `CreatorTask` must contain `status: "start" | "running" | "review" | "changes" | "recovery" | "complete"`, `title`, `summary`, `primaryLabel`, `afterAction`, and `tone: "operation" | "review" | "changes" | "failed" | "complete"`.

- [ ] **Step 1: Write the failing mapping tests**

```ts
it.each([
  [stageFixture({ execution_state: "pending", review_state: "not_ready" }), "start", "开始制作"],
  [stageFixture({ execution_state: "running" }), "running", "查看制作进度"],
  [stageFixture({ execution_state: "passed", review_state: "awaiting_review" }), "review", "查看成果并确认"],
  [stageFixture({ review_state: "changes_requested" }), "changes", "查看修改反馈"],
  [stageFixture({ execution_state: "failed" }), "recovery", "恢复并重新检查"],
] as const)("maps %s to creator-facing task copy", (stage, status, label) => {
  expect(deriveCreatorTask(projectFixture(stage), stage)).toMatchObject({ status, primaryLabel: label });
});

it("chooses the first unfinished project supplied by the API as the resume target", () => {
  expect(selectResumeProject([completedProject, activeProject])).toBe(activeProject);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/creatorTask.test.ts`

Expected: FAIL because `creatorTask.ts` does not exist.

- [ ] **Step 3: Implement the smallest pure adapter**

```ts
export function isProjectInProgress(project: ProjectDetail): boolean {
  return project.next_stage !== "complete";
}

export function selectResumeProject(projects: ProjectDetail[]): ProjectDetail | null {
  return projects.find(isProjectInProgress) ?? null;
}

export function deriveCreatorTask(project: ProjectDetail, stage: StageDetail): CreatorTask {
  if (stage.execution_state === "failed" || stage.execution_state === "stale") {
    return recoveryTask(stage);
  }
  if (stage.review_state === "changes_requested") return changesTask(stage);
  if (stage.execution_state === "running" || stage.active_run_job) return runningTask(stage);
  if (stage.review_state === "awaiting_review") return reviewTask(stage);
  if (stage.execution_state === "passed" && project.next_stage === "complete") return completeTask(stage);
  return startTask(stage);
}
```

Use the exact stage label in every summary. Keep the adapter free of React, routes, and API calls.

- [ ] **Step 4: Run the focused tests**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/creatorTask.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit the task**

```bash
git add sites/storymotion-studio/src/projects/creatorTask.ts sites/storymotion-studio/src/projects/creatorTask.test.ts
git commit -m "feat: derive creator task state"
```

## Task 2: Build the resume-first homepage components and project list

**Files:**
- Create: `sites/storymotion-studio/src/projects/ResumeProjectCard.tsx`
- Create: `sites/storymotion-studio/src/projects/ResumeProjectCard.test.tsx`
- Create: `sites/storymotion-studio/src/projects/ProjectListPage.test.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectListPage.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`

**Interfaces:**
- Consumes: `ProjectDetail`, `CreatorTask`, `deriveCreatorTask`, `selectResumeProject`, existing `projectAction`, `ProjectStageMiniRail`, and `CreateProjectDialog`.
- Produces: a homepage whose first ready-state region is labelled `继续制作`, points to `/projects/:id`, and whose next-task links point to `/projects/:id/stages/:stage`.

- [ ] **Step 1: Write failing component and page tests**

```tsx
it("puts the unfinished project in the continue-making region before the project library", async () => {
  render(<ProjectListPage api={apiWith([completedProject, activeProject])} />);
  const resume = await screen.findByRole("region", { name: "继续制作" });
  expect(within(resume).getByRole("link", { name: "查看成果并确认" }))
    .toHaveAttribute("href", "/projects/active-project/stages/storyboard");
  expect(screen.getByRole("heading", { name: "全部项目" })).toBeVisible();
});

it("shows a new-project invitation instead of an empty continuation card", async () => {
  render(<ProjectListPage api={apiWith([])} />);
  expect(await screen.findByText("从一个新灵感开始制作")).toBeVisible();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ResumeProjectCard.test.tsx src/projects/ProjectListPage.test.tsx`

Expected: FAIL because the resume components and regions do not exist.

- [ ] **Step 3: Implement the resume and next-task components**

```tsx
export function ResumeProjectCard({ project }: { project: ProjectDetail }) {
  const stage = project.stages.find((item) => item.stage === project.next_stage);
  if (!stage) return null;
  const task = deriveCreatorTask(project, stage);
  return (
    <section className="resume-project-card" aria-label="继续制作">
      <p className="eyebrow">继续制作</p>
      <h1>继续《{project.title}》</h1>
      <p>{task.summary}</p>
      <Link className="command-button" to={`/projects/${project.project_id}/stages/${stage.stage}`}>
        {task.primaryLabel}
      </Link>
      <small>{task.afterAction}</small>
    </section>
  );
}
```

In `ProjectListPage`, derive `resumeProject` only when `state.status === "ready"`; render `ResumeProjectCard` before the project library; render at most three `NextTaskRow` items for review, changes, failed/stale, and running stages; rename the old section heading to `全部项目`. Preserve search, filter, refresh, create, empty, busy and error behavior.

- [ ] **Step 4: Add the layout styles**

```css
.resume-project-card { display: grid; gap: 12px; padding: clamp(24px, 4vw, 42px); border-radius: 18px; }
.next-task-list { display: grid; gap: 8px; margin: 20px 0 32px; }
.next-task-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
```

Use existing color variables for review, failed, operation and complete tones. Do not reintroduce a large decorative dashboard hero.

- [ ] **Step 5: Run the focused tests**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ResumeProjectCard.test.tsx src/projects/ProjectListPage.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit the task**

```bash
git add sites/storymotion-studio/src/projects/ResumeProjectCard.tsx sites/storymotion-studio/src/projects/ResumeProjectCard.test.tsx sites/storymotion-studio/src/projects/ProjectListPage.tsx sites/storymotion-studio/src/projects/ProjectListPage.test.tsx sites/storymotion-studio/src/styles/workbench.css
git commit -m "feat: prioritize resuming unfinished projects"
```

## Task 3: Add accessible expandable controls and the current-task header

**Files:**
- Create: `sites/storymotion-studio/src/projects/ExpandablePanel.tsx`
- Create: `sites/storymotion-studio/src/projects/ExpandablePanel.test.tsx`
- Create: `sites/storymotion-studio/src/projects/CurrentTaskPanel.tsx`
- Create: `sites/storymotion-studio/src/projects/CurrentTaskPanel.test.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`

**Interfaces:**
- `ExpandablePanel` consumes `title`, `summary`, `children`, `defaultOpen?: boolean`, and `tone?: CreatorTask["tone"]`; it produces a labelled button and region with stable IDs.
- `CurrentTaskPanel` consumes `project`, `stage`, `task`, `isDirectStageRoute`, `children`, and `primaryControl`; it produces the project context, current action title, next-step copy, optional return link, and a place for the existing primary control.

- [ ] **Step 1: Write failing accessibility and route-context tests**

```tsx
it("keeps detailed controls closed until the creator asks to view them", async () => {
  const user = userEvent.setup();
  render(<ExpandablePanel title="生成设置" summary="2 个镜头">内容</ExpandablePanel>);
  const trigger = screen.getByRole("button", { name: /生成设置/ });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("内容")).not.toBeInTheDocument();
  await user.click(trigger);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("内容")).toBeVisible();
});

it("offers a return to the current task when viewing a non-current stage", () => {
  render(<CurrentTaskPanel project={project} stage={oldStage} task={task} isDirectStageRoute primaryControl={null}>preview</CurrentTaskPanel>);
  expect(screen.getByRole("link", { name: "回到当前任务" }))
    .toHaveAttribute("href", "/projects/episode_01/stages/storyboard");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ExpandablePanel.test.tsx src/projects/CurrentTaskPanel.test.tsx`

Expected: FAIL because neither component exists.

- [ ] **Step 3: Implement the two presentational components**

```tsx
export function ExpandablePanel({ title, summary, children, defaultOpen = false }: ExpandablePanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  return (
    <section className="expandable-panel">
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
        <span><strong>{title}</strong><small>{summary}</small></span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open ? <div id={panelId}>{children}</div> : null}
    </section>
  );
}
```

`CurrentTaskPanel` must place its children after the heading and before `primaryControl`, render task copy from `CreatorTask`, and use a normal `Link` to the project’s `next_stage` for the return action. It must not issue requests or mutate state.

- [ ] **Step 4: Add disclosure and task-header styles**

```css
.current-task-panel { display: grid; gap: 14px; padding: 20px; border: 1px solid var(--rule); background: var(--surface); }
.expandable-panel > button { display: flex; width: 100%; justify-content: space-between; text-align: left; }
.expandable-panel > [id] { padding: 14px 0 2px; border-top: 1px solid var(--rule); }
```

Apply visible `:focus-visible` styling using the existing palette and make controls full-width below 820px.

- [ ] **Step 5: Run focused tests**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ExpandablePanel.test.tsx src/projects/CurrentTaskPanel.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit the task**

```bash
git add sites/storymotion-studio/src/projects/ExpandablePanel.tsx sites/storymotion-studio/src/projects/ExpandablePanel.test.tsx sites/storymotion-studio/src/projects/CurrentTaskPanel.tsx sites/storymotion-studio/src/projects/CurrentTaskPanel.test.tsx sites/storymotion-studio/src/styles/workbench.css
git commit -m "feat: add focused task controls"
```

## Task 4: Recompose the project workspace around the current task

**Files:**
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`

**Interfaces:**
- Consumes: `deriveCreatorTask`, `CurrentTaskPanel`, `ExpandablePanel`, existing `ArtifactWorkspace`, `JobProgress`, `ReviewPanel`, `ImpactDialog`, `StageRail`, and all existing mutation callbacks.
- Produces: one visible task header and one visible primary action for a workspace route; review, run state and task controls remain based on the existing API calls.

- [ ] **Step 1: Write failing workspace behavior tests**

```tsx
it("puts the current task before the artifact preview and exposes one primary review action", async () => {
  renderWorkspace();
  expect(await screen.findByRole("region", { name: "当前任务" })).toBeVisible();
  expect(screen.getByText("先检查分镜成果，再决定是否确认通过。")).toBeVisible();
  expect(screen.getAllByRole("button", { name: /确认通过/ })).toHaveLength(1);
});

it("moves the stage map and review history into expandable controls", async () => {
  renderWorkspace();
  expect(await screen.findByRole("button", { name: /制作地图/ })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: /审核与修改/ })).toBeVisible();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ProjectWorkspacePage.test.tsx`

Expected: FAIL because the current layout is a three-column console with no current-task region or disclosures.

- [ ] **Step 3: Recompose without changing request logic**

```tsx
const task = deriveCreatorTask(project, stage);
const isDirectStageRoute = selectedRouteStage !== undefined && selectedStage !== project.next_stage;

<CurrentTaskPanel
  project={project}
  stage={stage}
  task={task}
  isDirectStageRoute={isDirectStageRoute}
  primaryControl={primaryControl}
>
  <ArtifactWorkspace stage={stage} onIssueAtTime={...} />
</CurrentTaskPanel>

<ExpandablePanel title="审核与修改" summary="查看反馈与返修范围">
  <ReviewPanel ... />
</ExpandablePanel>
```

Extract the existing `trackedStageJobId` / `canRunStage` control into a local `primaryControl` variable. For awaiting review, the primary control must be the existing `ReviewPanel` confirmation action rendered once; if `ReviewPanel` cannot expose only that action cleanly, add an optional `mode="primary" | "details"` prop to `ReviewPanel` and test it. For running stages, retain the existing `JobProgress`; for pending and failed states retain `runSelectedStage`; for video, the primary control is the collapsed video preflight entry point, not submission itself.

- [ ] **Step 4: Update grid and responsive styles**

```css
.workspace-page { display: block; background: var(--canvas); }
.workspace-content { width: min(100%, 1080px); margin: 0 auto; padding: 24px 28px 56px; }
.production-map-panel { margin: 0 auto; width: min(100%, 1080px); }
```

Retain the stage rail inside the `制作地图` disclosure. On desktop, auxiliary disclosures may form a two-column grid; below 820px they must stack after the current task. The artifact preview must remain ahead of expanded technical controls in reading order.

- [ ] **Step 5: Run focused workspace tests**

Run: `pnpm --dir sites/storymotion-studio test --run src/projects/ProjectWorkspacePage.test.tsx`

Expected: PASS, including pre-existing mutation, routing, job and review tests.

- [ ] **Step 6: Commit the task**

```bash
git add sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx sites/storymotion-studio/src/styles/workbench.css
git commit -m "feat: focus workspace on the current task"
```

## Task 5: Make video confirmation creator-readable without weakening payment controls

**Files:**
- Modify: `sites/storymotion-studio/src/jobs/VideoPreflight.tsx`
- Modify: `sites/storymotion-studio/src/jobs/VideoPreflight.test.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`

**Interfaces:**
- Consumes: existing `VideoPreflight`, `VideoGenerationSubmission`, and `VideoPreflight` component callbacks.
- Produces: a collapsed `生成设置` panel and a visible post-preflight `本次生成确认` card that shows shot count, output seconds, provider, model, estimated yuan, existing task recovery copy, and the existing explicit confirm/submit action.

- [ ] **Step 1: Write failing confirmation tests**

```tsx
it("summarizes model, duration, shots and estimated cost before accepting a paid submission", async () => {
  const user = userEvent.setup();
  render(<VideoPreflight api={api} projectId="episode_01" shotIds={["shot_03", "shot_04"]} onJobAccepted={vi.fn()} />);
  await user.click(await screen.findByRole("button", { name: "检查本次生成" }));
  const card = await screen.findByRole("region", { name: "本次生成确认" });
  expect(within(card).getByText("2 个镜头 · 9 秒")).toBeVisible();
  expect(within(card).getByText("MiniMax-H3")).toBeVisible();
  expect(within(card).getByText("预计 ¥4.50")).toBeVisible();
  expect(within(card).getByRole("button", { name: "确认并提交生成" })).toBeEnabled();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm --dir sites/storymotion-studio test --run src/jobs/VideoPreflight.test.tsx`

Expected: FAIL because no region named `本次生成确认` exists.

- [ ] **Step 3: Implement the confirmation card using existing submission flow**

```tsx
{preflight?.ready ? (
  <section className="video-confirmation-card" aria-label="本次生成确认">
    <p>本次生成确认</p>
    <strong>{preflight.shot_ids.length} 个镜头 · {preflight.output_seconds} 秒</strong>
    <dl><div><dt>模型</dt><dd>{preflight.model}</dd></div><div><dt>预计费用</dt><dd>预计 ¥{preflight.estimated_cost_yuan.toFixed(2)}</dd></div></dl>
    <button type="button" onClick={confirmAndSubmit}>确认并提交生成</button>
  </section>
) : null}
```

`confirmAndSubmit` must continue to call the existing confirmation API first and use its returned generation token for the existing submit API. If blockers exist, show them in the same card and do not render the submission button. Do not create a client-side alternative submission request.

- [ ] **Step 4: Place video controls behind an explicit disclosure in the workspace**

Wrap `VideoGenerationWorkspace` in `ExpandablePanel` with title `生成设置`, a shot/duration summary, and `defaultOpen={task.status === "recovery"}`. Keep `JobProgress` visible in the current task only while a job is queued or running; retain recovery instructions in the disclosure for failed jobs.

- [ ] **Step 5: Run video and workspace tests**

Run: `pnpm --dir sites/storymotion-studio test --run src/jobs/VideoPreflight.test.tsx src/projects/ProjectWorkspacePage.test.tsx`

Expected: PASS; existing tests still prove that confirmation happens before generation and recovery does not re-submit a task.

- [ ] **Step 6: Commit the task**

```bash
git add sites/storymotion-studio/src/jobs/VideoPreflight.tsx sites/storymotion-studio/src/jobs/VideoPreflight.test.tsx sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx sites/storymotion-studio/src/styles/workbench.css
git commit -m "feat: clarify video generation confirmation"
```

## Task 6: Run full verification and inspect the local creator flow

**Files:**
- Modify only if verification exposes a real defect: the smallest affected source or test file from Tasks 1–5.

**Interfaces:**
- Consumes: the completed frontend and existing local API launch command.
- Produces: a verified responsive creator flow with no API contract regression.

- [ ] **Step 1: Run all frontend tests**

Run: `pnpm --dir sites/storymotion-studio test --run`

Expected: PASS with all existing and newly added tests.

- [ ] **Step 2: Run static checks and production build**

Run: `pnpm --dir sites/storymotion-studio lint && pnpm --dir sites/storymotion-studio build`

Expected: both commands exit 0.

- [ ] **Step 3: Inspect the real local flow**

Run: `.venv/bin/python scripts/run_workbench.py`

Open: `http://127.0.0.1:<printed-port>/projects`

Check: the unfinished project appears in `继续制作`; open it; confirm current task precedes detail panels; open and close `制作地图`, `审核与修改`, and `生成设置` where applicable; verify direct stage link exposes `回到当前任务`.

- [ ] **Step 4: Fix only verified defects and rerun the exact failed check**

```bash
pnpm --dir sites/storymotion-studio test --run <affected-test-file>
pnpm --dir sites/storymotion-studio lint
pnpm --dir sites/storymotion-studio build
```

Do not change API behavior to make a frontend assertion pass.

- [ ] **Step 5: Commit the verified implementation**

```bash
git add sites/storymotion-studio
git commit -m "test: verify resume-first creator flow"
```

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 cover continuation and next-task ordering; Tasks 3–4 cover current-task priority, direct-stage context, disclosures, accessible controls, error preservation and responsive layout; Task 5 covers video/fee presentation while preserving existing safety controls; Task 6 covers full verification. No backend or payment change is planned.
- **Placeholder scan:** No open-ended implementation markers are present; each task names exact files, interfaces, tests, commands and commits.
- **Type consistency:** `CreatorTask` is created in Task 1 and consumed only by Tasks 2–5. `ExpandablePanel` and `CurrentTaskPanel` are created in Task 3 before Task 4 consumes them. Existing video types remain unchanged.
