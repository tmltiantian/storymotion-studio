import "@testing-library/jest-dom/vitest";

import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { ProjectDetail, StageDetail } from "../api/types";
import { ProjectListPage, type ProjectListApi } from "./ProjectListPage";

function stageFixture(overrides: Partial<StageDetail> = {}): StageDetail {
  return {
    stage: "storyboard",
    execution_state: "passed",
    review_state: "awaiting_review",
    review_policy: "manual",
    review_blocks_progress: true,
    revision: 1,
    executor: "test",
    blocked_reasons: [],
    error: "",
    presentation: null,
    review_evidence: [],
    artifacts: [],
    active_run_job: null,
    ...overrides,
  };
}

function projectFixture(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  const stage = stageFixture();
  return {
    project_id: "active-project",
    title: "雨夜来信",
    mode: "original",
    target: {},
    next_stage: stage.stage,
    required_action: "approve_review_evidence",
    stages: [stage],
    final_outputs: [],
    eval_reports: [],
    ...overrides,
  };
}

function apiWith(projects: ProjectDetail[]): ProjectListApi {
  return {
    listProjects: vi.fn().mockResolvedValue(projects),
    createProject: vi.fn(),
  };
}

describe("ProjectListPage", () => {
  it("puts the unfinished project in the continue-making region before the project library", async () => {
    const completedProject = projectFixture({
      project_id: "completed-project",
      next_stage: "complete",
      required_action: "none",
    });
    const activeProject = projectFixture();

    render(
      <MemoryRouter>
        <ProjectListPage api={apiWith([completedProject, activeProject])} />
      </MemoryRouter>,
    );

    const resume = await screen.findByRole("region", { name: "继续制作" });
    expect(within(resume).getByRole("link", { name: "查看成果并确认" }))
      .toHaveAttribute("href", "/projects/active-project");
    expect(screen.getByRole("heading", { name: "全部项目" })).toBeVisible();
  });

  it("shows a new-project invitation instead of an empty continuation card", async () => {
    render(
      <MemoryRouter>
        <ProjectListPage api={apiWith([])} />
      </MemoryRouter>,
    );

    expect(await screen.findByText("从一个新灵感开始制作")).toBeVisible();
  });
});
