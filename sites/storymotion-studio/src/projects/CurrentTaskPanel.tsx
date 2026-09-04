import { useId, type ReactNode } from "react";
import { Link } from "react-router";

import type { ProjectDetail, StageDetail } from "../api/types";
import type { CreatorTask } from "./creatorTask";
import { stageLabel } from "./StageRail";

type CurrentTaskPanelProps = {
  project: ProjectDetail;
  stage: StageDetail;
  task: CreatorTask;
  isDirectStageRoute: boolean;
  children: ReactNode;
  primaryControl: ReactNode;
};

export function CurrentTaskPanel({
  project,
  stage,
  task,
  isDirectStageRoute,
  children,
  primaryControl,
}: CurrentTaskPanelProps) {
  const titleId = useId();
  const showReturnToCurrentTask = (
    isDirectStageRoute
    && project.next_stage !== "complete"
    && project.next_stage !== stage.stage
  );
  const context = showReturnToCurrentTask
    ? `正在查看${stageLabel(stage.stage)}；项目当前停在${stageLabel(project.next_stage)}`
    : `${project.title} · ${stageLabel(stage.stage)}`;

  return (
    <section
      className={`current-task-panel tone-${task.tone}`}
      aria-labelledby={titleId}
    >
      <header className="current-task-heading">
        <div>
          <p className="eyebrow">{showReturnToCurrentTask ? "正在查看" : "当前任务"}</p>
          <p className="current-task-context">{context}</p>
        </div>
        {showReturnToCurrentTask ? (
          <Link
            className="current-task-return"
            to={`/projects/${project.project_id}/stages/${project.next_stage}`}
          >
            回到当前任务
          </Link>
        ) : null}
        <h2 id={titleId}>{task.title}</h2>
        <p>{task.summary}</p>
      </header>
      {children}
      <small className="current-task-next-step">{task.afterAction}</small>
      {primaryControl}
    </section>
  );
}
