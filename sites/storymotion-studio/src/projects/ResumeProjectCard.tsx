import { Link } from "react-router";

import type { ProjectDetail } from "../api/types";
import { deriveCreatorTask } from "./creatorTask";

export function ResumeProjectCard({ project }: { project: ProjectDetail }) {
  const stage = project.stages.find((item) => item.stage === project.next_stage);
  if (!stage) return null;

  const task = deriveCreatorTask(project, stage);
  return (
    <section className={`resume-project-card tone-${task.tone}`} aria-label="继续制作">
      <p className="eyebrow">继续制作</p>
      <h1>继续《{project.title}》</h1>
      <p>{task.summary}</p>
      <Link
        className="command-button"
        to={`/projects/${project.project_id}`}
      >
        {task.primaryLabel}
      </Link>
      <small>{task.afterAction}</small>
    </section>
  );
}
