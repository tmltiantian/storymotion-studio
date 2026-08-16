import {
  AlertCircle,
  CheckCircle2,
  Filter,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import type { ApiClient } from "../api/client";
import type { ProjectDetail } from "../api/types";
import { CreateProjectDialog } from "../app/CreateProjectDialog";
import {
  ProjectStageMiniRail,
  stageLabel,
  stageTone,
} from "./StageRail";

export type ProjectListApi = Pick<ApiClient, "listProjects" | "createProject">;

type ProjectsState =
  | { status: "loading" }
  | { status: "ready"; projects: ProjectDetail[] }
  | { status: "busy" }
  | { status: "error" };

const modeLabels: Record<ProjectDetail["mode"], string> = {
  original: "原创",
  novel: "小说改编",
  replica: "参考复刻",
};

function isBusyError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "busy"
  );
}

function projectAction(project: ProjectDetail): {
  label: string;
  to: string;
  tone: "review" | "changes" | "current" | "failed" | "complete";
} {
  if (project.next_stage === "complete") {
    return {
      label: "查看成片",
      to: `/works/${project.project_id}`,
      tone: "complete",
    };
  }
  const to = `/projects/${project.project_id}/stages/${project.next_stage}`;
  const label = stageLabel(project.next_stage);
  if (project.required_action === "approve_review_evidence") {
    return { label: `确认${label}`, to, tone: "review" };
  }
  if (project.required_action === "address_review_changes") {
    return { label: `处理${label}修改`, to, tone: "changes" };
  }
  if (project.required_action === "fix_stage_error_and_resume") {
    return { label: `排查${label}失败`, to, tone: "failed" };
  }
  return { label: `继续${label}`, to, tone: "current" };
}

function ProjectCard({ project }: { project: ProjectDetail }) {
  const action = projectAction(project);
  const actionStateLabels = {
    review: "等待人工确认",
    changes: "需要修改",
    current: "当前操作",
    failed: "运行失败",
    complete: "已完成",
  } as const;
  const passed = project.stages.filter(
    (stage) => stage.execution_state === "passed",
  ).length;
  return (
    <article className="project-card">
      <div className="project-identity">
        <div className="project-kicker">
          <span>{modeLabels[project.mode]}</span>
          <code>{project.project_id}</code>
        </div>
        <h2>
          <Link to={`/projects/${project.project_id}`}>{project.title}</Link>
        </h2>
        <div className="project-progress-copy">
          <span>{passed}/9 阶段通过</span>
          <span>当前：{stageLabel(project.next_stage)}</span>
        </div>
      </div>
      <ProjectStageMiniRail project={project} />
      <div className="project-action">
        <span className={`action-state state-${action.tone}`}>
          {actionStateLabels[action.tone]}
        </span>
        <Link className={`action-link action-${action.tone}`} to={action.to}>
          {action.label}
        </Link>
      </div>
    </article>
  );
}

function LoadingProjects() {
  return (
    <div className="state-row" role="status" aria-label="正在加载项目">
      <LoaderCircle className="loading-icon" aria-hidden="true" size={18} />
      <span>正在读取制作项目</span>
    </div>
  );
}

export function ProjectListPage({ api }: { api: ProjectListApi }) {
  const [state, setState] = useState<ProjectsState>({ status: "loading" });
  const [filterOpen, setFilterOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const createTriggerRef = useRef<HTMLButtonElement>(null);
  const requestGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    requestController.current?.abort();
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    requestController.current = controller;
    setState({ status: "loading" });
    void api.listProjects(controller.signal).then(
      (projects) => {
        if (generation === requestGeneration.current) {
          setState({ status: "ready", projects });
        }
      },
      (error: unknown) => {
        if (generation === requestGeneration.current) {
          setState({ status: isBusyError(error) ? "busy" : "error" });
        }
      },
    );
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    requestController.current = controller;
    void api.listProjects(controller.signal).then(
      (projects) => {
        if (generation === requestGeneration.current) {
          setState({ status: "ready", projects });
        }
      },
      (error: unknown) => {
        if (generation === requestGeneration.current) {
          setState({ status: isBusyError(error) ? "busy" : "error" });
        }
      },
    );
    return () => {
      requestGeneration.current += 1;
      requestController.current?.abort();
    };
  }, [api]);

  const projects = state.status === "ready" ? state.projects : [];
  const normalizedFilter = filter.trim().toLocaleLowerCase("zh-CN");
  const visibleProjects = normalizedFilter
    ? projects.filter((project) =>
        `${project.title}\n${project.project_id}`
          .toLocaleLowerCase("zh-CN")
          .includes(normalizedFilter),
      )
    : projects;
  const activeJobs = visibleProjects.flatMap((project) =>
    project.stages
      .filter((stage) =>
        ["running", "failed", "stale"].includes(stage.execution_state),
      )
      .map((stage) => ({ project, stage })),
  );

  return (
    <div className="page-frame">
      <div className="page-heading">
        <div>
          <p className="eyebrow">PRODUCTION QUEUE</p>
          <h1>制作项目</h1>
        </div>
        <div className="heading-actions">
          <button
            className="icon-button"
            type="button"
            aria-label="筛选项目"
            title="筛选项目"
            aria-expanded={filterOpen}
            aria-controls="project-filter"
            onClick={() => setFilterOpen((open) => !open)}
          >
            <Filter aria-hidden="true" size={17} />
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label="刷新项目"
            title="刷新项目"
            onClick={load}
            disabled={state.status === "loading"}
          >
            <RefreshCw aria-hidden="true" size={17} />
          </button>
          <button
            ref={createTriggerRef}
            className="command-button"
            type="button"
            aria-label="新建项目"
            title="新建项目"
            onClick={() => setCreateOpen(true)}
          >
            <Plus aria-hidden="true" size={17} />
            新建项目
          </button>
        </div>
      </div>

      {filterOpen ? (
        <div className="filter-row" id="project-filter">
          <Search aria-hidden="true" size={16} />
          <label className="sr-only" htmlFor="project-filter-input">筛选制作项目</label>
          <input
            id="project-filter-input"
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="项目标题或 ID"
            aria-label="筛选制作项目"
          />
          {filter ? (
            <button
              className="icon-button filter-clear"
              type="button"
              aria-label="清除项目筛选"
              title="清除项目筛选"
              onClick={() => setFilter("")}
            >
              <X aria-hidden="true" size={15} />
            </button>
          ) : null}
          <span>{visibleProjects.length} 个匹配项目</span>
        </div>
      ) : null}

      <section className="project-section" aria-labelledby="active-projects-title">
        <div className="section-heading">
          <h2 id="active-projects-title">进行中的项目</h2>
          {state.status === "ready" ? <span>{visibleProjects.length} 个</span> : null}
        </div>
        {state.status === "loading" ? <LoadingProjects /> : null}
        {state.status === "busy" ? (
          <div className="state-row state-busy" role="status">
            <LoaderCircle aria-hidden="true" size={18} />
            <div>
              <strong>项目正在处理</strong>
              <span>等待当前写入完成后手动重新加载。</span>
            </div>
            <button className="text-button" type="button" onClick={load}>重新加载</button>
          </div>
        ) : null}
        {state.status === "error" ? (
          <div className="state-row state-error" role="alert">
            <AlertCircle aria-hidden="true" size={18} />
            <div>
              <strong>无法读取制作项目</strong>
              <span>检查本地制作服务后重新加载。</span>
            </div>
            <button className="text-button" type="button" onClick={load}>
              <RotateCcw aria-hidden="true" size={15} />
              重新加载
            </button>
          </div>
        ) : null}
        {state.status === "ready" && projects.length === 0 ? (
          <div className="empty-state">
            <div>
              <strong>还没有制作项目</strong>
              <span>创建项目后，当前操作会出现在这里。</span>
            </div>
          </div>
        ) : null}
        {state.status === "ready" && projects.length > 0 && visibleProjects.length === 0 ? (
          <div className="empty-state">
            <div>
              <strong>没有匹配项目</strong>
              <span>调整标题或项目 ID 筛选条件。</span>
            </div>
          </div>
        ) : null}
        {state.status === "ready" && visibleProjects.length > 0 ? (
          <div className="project-list">
            {visibleProjects.map((project) => (
              <ProjectCard key={project.project_id} project={project} />
            ))}
          </div>
        ) : null}
      </section>

      <section id="job-activity" className="activity-section" aria-labelledby="activity-title">
        <div className="section-heading">
          <h2 id="activity-title">作业活动</h2>
          <span>{activeJobs.length} 项活动</span>
        </div>
        {activeJobs.length === 0 ? (
          <div className="activity-empty">
            <CheckCircle2 aria-hidden="true" size={17} />
            <span>当前没有运行中或需要恢复的作业</span>
          </div>
        ) : (
          <div className="activity-list">
            {activeJobs.map(({ project, stage }) => (
              <Link
                key={`${project.project_id}-${stage.stage}`}
                to={`/projects/${project.project_id}/stages/${stage.stage}`}
              >
                <span className={`activity-status stage-${stageTone(stage)}`} />
                <strong>{project.title}</strong>
                <span>{stageLabel(stage.stage)}</span>
                <code>{stage.execution_state}</code>
              </Link>
            ))}
          </div>
        )}
      </section>

      {createOpen ? (
        <CreateProjectDialog
          createProject={api.createProject}
          onClose={() => setCreateOpen(false)}
          onCreated={load}
          returnFocusRef={createTriggerRef}
        />
      ) : null}
    </div>
  );
}
