import { describe, expect, it } from "vitest";

import type { ProjectDetail, StageDetail } from "../api/types";
import { deriveCreatorTask, selectResumeProject } from "./creatorTask";

function stageFixture(overrides: Partial<StageDetail> = {}): StageDetail {
  return {
    stage: "script",
    execution_state: "pending",
    review_state: "not_ready",
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

function projectFixture(stage: StageDetail): ProjectDetail {
  return {
    project_id: "project-1",
    title: "测试作品",
    mode: "original",
    target: {},
    next_stage: stage.stage,
    required_action: "run_or_resume",
    stages: [stage],
    final_outputs: [],
    eval_reports: [],
  };
}

const completedProject = projectFixture(stageFixture());
completedProject.next_stage = "complete";
const activeProject = projectFixture(stageFixture({ execution_state: "running" }));
activeProject.project_id = "project-2";

describe("creator task adapter", () => {
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

  it("treats an approved stage behind the project next stage as completed history", () => {
    const historical = stageFixture({
      execution_state: "passed",
      review_state: "approved",
    });
    const project = projectFixture(historical);
    project.next_stage = "storyboard";

    expect(deriveCreatorTask(project, historical)).toMatchObject({
      status: "complete",
      title: "本阶段已完成",
      primaryLabel: "回到当前任务",
    });
  });
});
