import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { ProjectDetail, StageDetail } from "../api/types";
import type { CreatorTask } from "./creatorTask";
import { CurrentTaskPanel } from "./CurrentTaskPanel";

const oldStage: StageDetail = {
  stage: "script",
  execution_state: "passed",
  review_state: "approved",
  review_policy: "manual",
  review_blocks_progress: false,
  revision: 1,
  executor: "test",
  blocked_reasons: [],
  error: "",
  presentation: null,
  review_evidence: [],
  artifacts: [],
  active_run_job: null,
};

const project: ProjectDetail = {
  project_id: "episode_01",
  title: "旧城来信 · 第 01 集",
  mode: "novel",
  target: {},
  next_stage: "storyboard",
  required_action: "approve_review_evidence",
  stages: [oldStage],
  final_outputs: [],
  eval_reports: [],
};

const task: CreatorTask = {
  status: "review",
  title: "等你确认",
  summary: "故事板成果已生成，请先查看成果再决定是否继续。",
  primaryLabel: "查看成果并确认",
  afterAction: "通过后进入下一阶段。",
  tone: "review",
};

describe("CurrentTaskPanel", () => {
  it("offers a return to the current task when viewing a non-current stage", () => {
    render(
      <MemoryRouter>
        <CurrentTaskPanel
          project={project}
          stage={oldStage}
          task={task}
          isDirectStageRoute
          primaryControl={null}
        >
          preview
        </CurrentTaskPanel>
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "回到当前任务" }))
      .toHaveAttribute("href", "/projects/episode_01/stages/storyboard");
  });
});
