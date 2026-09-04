import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { ProjectDetail, StageDetail } from "../api/types";
import { ResumeProjectCard } from "./ResumeProjectCard";

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

function projectFixture(stage = stageFixture()): ProjectDetail {
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
  };
}

describe("ResumeProjectCard", () => {
  it("links the next creator task to its project stage", () => {
    render(
      <MemoryRouter>
        <ResumeProjectCard project={projectFixture()} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("region", { name: "继续制作" })).toBeVisible();
    expect(screen.getByRole("link", { name: "查看成果并确认" })).toHaveAttribute(
      "href",
      "/projects/active-project/stages/storyboard",
    );
  });
});
