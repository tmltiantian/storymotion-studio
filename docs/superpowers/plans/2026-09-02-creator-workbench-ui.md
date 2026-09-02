# Creator Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade StoryMotion Studio into a creator-facing AI production workbench while preserving its existing APIs and production controls.

**Architecture:** Keep route and API contracts intact. Evolve the shared shell, project list and project workspace into a consistent creator-cockpit layout; use semantic HTML and focused CSS modules already present in the application.

**Tech Stack:** React 19, TypeScript, React Router 7, Lucide React, Vitest, Vite CSS.

**Spec:** `docs/superpowers/specs/2026-09-02-creator-workbench-ui-design.md`

## Global Constraints

- Do not change API request methods, Python services, video preflight behavior, or approval rules.
- Do not add UI dependencies.
- All new visual elements must have Chinese labels and accessible names.
- Keep existing narrow-screen behavior usable.

---

### Task 1: Creator shell and project dashboard

**Files:**
- Modify: `sites/storymotion-studio/src/app/AppShell.tsx`
- Modify: `sites/storymotion-studio/src/projects/ProjectListPage.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`
- Test: `sites/storymotion-studio/src/app/AppShell.test.tsx`
- Test: `sites/storymotion-studio/src/projects/ProjectListPage.test.tsx`

**Interfaces:**
- Consumes: existing routes and `ProjectListApi`.
- Produces: creator-oriented shell and dashboard without changing route paths or list-project calls.

- [ ] **Step 1: Write failing dashboard tests**

Add assertions for the visible “创作工作台” shell label, the dashboard’s “继续创作” action and its project-count summary.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm test --run src/app/AppShell.test.tsx src/projects/ProjectListPage.test.tsx`

Expected: FAIL because the new visible labels do not yet exist.

- [ ] **Step 3: Implement the shell and dashboard hierarchy**

Add a creator-context label to the shared shell, introduce a dashboard introduction/status summary, and give project cards a cover-like identity and a clear continue action while preserving their existing navigation targets.

- [ ] **Step 4: Apply the responsive visual system**

Extend `workbench.css` with creator-cockpit colors, hero/dashboard cards, progress chips, and desktop/mobile layout rules. Do not remove existing state colors.

- [ ] **Step 5: Run focused tests**

Run: `pnpm test --run src/app/AppShell.test.tsx src/projects/ProjectListPage.test.tsx`

Expected: PASS.

### Task 2: Three-column project workspace

**Files:**
- Modify: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.tsx`
- Modify: `sites/storymotion-studio/src/projects/StageRail.tsx`
- Modify: `sites/storymotion-studio/src/styles/workbench.css`
- Test: `sites/storymotion-studio/src/projects/ProjectWorkspacePage.test.tsx`

**Interfaces:**
- Consumes: existing `ProjectWorkspaceApi`, `ProjectDetail`, `StageDetail`, `VideoWorkspace`, `JobProgress`, `VideoPreflight`, and `ReviewPanel`.
- Produces: semantic workspace canvas and creator side panel using the existing actions/components.

- [ ] **Step 1: Write failing workspace test**

Add an assertion that a ready workspace renders a labelled “创作控制台” side panel alongside the current stage result.

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm test --run src/projects/ProjectWorkspacePage.test.tsx`

Expected: FAIL because the side panel does not yet exist.

- [ ] **Step 3: Implement workspace grouping**

Wrap the current stage workspace in a canvas region. Place the existing run, review, and video generation components inside a labelled right-side creator control panel. Preserve button handlers and all existing success/error copy.

- [ ] **Step 4: Implement responsive layout**

Use CSS grid for desktop three-column layout and collapse it to a single column below the current compact breakpoint. Preserve sticky stage navigation only where it fits.

- [ ] **Step 5: Run focused test**

Run: `pnpm test --run src/projects/ProjectWorkspacePage.test.tsx`

Expected: PASS.

### Task 3: Visual and full regression verification

**Files:**
- Modify: `sites/storymotion-studio/src/styles/base.css`
- Test: existing front-end suite and production build.

**Interfaces:**
- Consumes: the new dashboard and workspace markup.
- Produces: coherent global color/focus behavior and a buildable creator workbench.

- [ ] **Step 1: Write a failing accessibility-visible test if needed**

If the new component copy is not already exercised, add one user-visible assertion; do not test CSS class names.

- [ ] **Step 2: Apply minimal global token changes**

Adjust base color tokens only where needed for the creator visual system, retaining keyboard focus contrast and reduced-motion behavior.

- [ ] **Step 3: Run all tests**

Run: `pnpm test --run`

Expected: all tests pass.

- [ ] **Step 4: Run production build**

Run: `pnpm build`

Expected: TypeScript checks and Vite build pass.
