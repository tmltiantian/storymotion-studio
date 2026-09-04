import type { ProjectDetail, StageDetail } from "../api/types";
import { stageLabel } from "./StageRail";

export type CreatorTaskStatus = "start" | "running" | "review" | "changes" | "recovery" | "complete";
export type CreatorTaskTone = "operation" | "review" | "changes" | "failed" | "complete";

export interface CreatorTask {
  status: CreatorTaskStatus;
  title: string;
  summary: string;
  primaryLabel: string;
  afterAction: string;
  tone: CreatorTaskTone;
}

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
  if (
    stage.execution_state === "passed"
    && stage.review_state === "approved"
    && project.next_stage !== stage.stage
  ) {
    return historicalTask(stage);
  }
  return startTask(stage);
}

function startTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "start",
    title: "准备开始",
    summary: `${label}还没有开始，准备好后即可生成成果。`,
    primaryLabel: "开始制作",
    afterAction: "将生成本阶段成果，随后可预览和确认。",
    tone: "operation",
  };
}

function runningTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "running",
    title: "正在制作",
    summary: `${label}正在制作中，完成后会在这里显示成果。`,
    primaryLabel: "查看制作进度",
    afterAction: "完成后会自动回到此处供你检查。",
    tone: "operation",
  };
}

function reviewTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "review",
    title: "等你确认",
    summary: `${label}成果已生成，请先查看成果再决定是否继续。`,
    primaryLabel: "查看成果并确认",
    afterAction: "通过后进入下一阶段。",
    tone: "review",
  };
}

function changesTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "changes",
    title: "需要修改",
    summary: `${label}收到了修改反馈，请查看反馈后调整成果。`,
    primaryLabel: "查看修改反馈",
    afterAction: "修改后只重做受影响的内容。",
    tone: "changes",
  };
}

function recoveryTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "recovery",
    title: "制作遇到问题",
    summary: `${label}制作没有顺利完成，可以恢复并重新检查。`,
    primaryLabel: "恢复并重新检查",
    afterAction: "不会自动重复提交或重复计费。",
    tone: "failed",
  };
}

function completeTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "complete",
    title: "作品已完成",
    summary: `${label}已完成，所有阶段都已准备就绪。`,
    primaryLabel: "查看成果",
    afterAction: "下一阶段已准备好。",
    tone: "complete",
  };
}

function historicalTask(stage: StageDetail): CreatorTask {
  const label = stageLabel(stage.stage);
  return {
    status: "complete",
    title: "本阶段已完成",
    summary: `${label}已确认，项目已继续到后续阶段。`,
    primaryLabel: "回到当前任务",
    afterAction: "返回当前任务可继续制作。",
    tone: "complete",
  };
}
