import { Link } from "react-router";

import type {
  ProjectDetail,
  ReviewState,
  StageDetail,
  StageName,
} from "../api/types";
import { STAGES } from "../app/AppShell";

export const executionLabels: Record<StageDetail["execution_state"], string> = {
  pending: "待开始",
  ready: "可运行",
  running: "运行中",
  passed: "成果已生成",
  failed: "运行失败",
  blocked: "已阻塞",
  stale: "需要重跑",
};

export const reviewLabels: Record<ReviewState, string> = {
  not_ready: "审核尚未开始",
  awaiting_review: "等待确认",
  approved: "已确认",
  changes_requested: "已退回修改",
  auto_approved: "自动通过",
  skipped: "已跳过",
};

export function stageLabel(stage: StageName | "complete"): string {
  return STAGES.find((item) => item.name === stage)?.label ?? "成片";
}

export function stageTone(stage: StageDetail, current = false): string {
  if (stage.execution_state === "failed") return "failed";
  if (stage.review_state === "awaiting_review") return "review";
  if (stage.review_state === "changes_requested") return "changes";
  if (stage.execution_state === "running") return "current";
  if (stage.execution_state === "passed") return "passed";
  if (stage.execution_state === "blocked") return "blocked";
  if (stage.execution_state === "stale") return "stale";
  if (stage.execution_state === "ready") return current ? "current" : "ready";
  return "pending";
}

function stageStatus(stage: StageDetail | undefined): string {
  return stage
    ? `${executionLabels[stage.execution_state]}；${reviewLabels[stage.review_state]}`
    : "无阶段数据";
}

export function ProjectStageMiniRail({ project }: { project: ProjectDetail }) {
  const records = new Map(project.stages.map((stage) => [stage.stage, stage]));
  return (
    <ol className="project-stage-rail" aria-label={`${project.title} 制作进度`}>
      {STAGES.map((item) => {
        const stage = records.get(item.name);
        const tone = stage
          ? stageTone(stage, project.next_stage === item.name)
          : "pending";
        const status = stageStatus(stage);
        const accessibleLabel = `${item.number} ${item.label}：${status}`;
        return (
          <li
            key={item.name}
            className={`stage-${tone}`}
            title={accessibleLabel}
            aria-label={accessibleLabel}
          >
            <span className="stage-dot" aria-hidden="true" />
            <span>{item.number}</span>
            <span className="sr-only">{item.label}：{status}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function StageRail({
  project,
  selectedStage,
  inDrawer = false,
  onNavigate,
}: {
  project: ProjectDetail;
  selectedStage: StageName;
  inDrawer?: boolean;
  onNavigate?: () => void;
}) {
  const records = new Map(project.stages.map((stage) => [stage.stage, stage]));
  return (
    <nav
      className={inDrawer ? "workspace-stage-nav drawer-stage-nav" : "workspace-stage-nav"}
      aria-label="项目阶段"
    >
      <ol>
        {STAGES.map((item) => {
          const stage = records.get(item.name);
          const status = stageStatus(stage);
          const current = selectedStage === item.name;
          const accessibleLabel = `${item.number} ${item.label}：${status}`;
          return (
            <li key={item.name} className={stage ? `stage-${stageTone(stage, current)}` : "stage-pending"}>
              <Link
                to={`/projects/${project.project_id}/stages/${item.name}`}
                aria-label={accessibleLabel}
                aria-current={current ? "step" : undefined}
                title={accessibleLabel}
                onClick={onNavigate}
              >
                <span className="workspace-stage-index">{item.number}</span>
                <span className="workspace-stage-copy">
                  <strong>{item.label}</strong>
                  <span>{status}</span>
                </span>
                <span className="stage-dot" aria-hidden="true" />
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
