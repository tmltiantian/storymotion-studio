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
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
  useParams,
} from "react-router";

import { apiClient, type ApiClient } from "../api/client";
import type {
  ProjectDetail,
  ProviderCapability,
  ProviderSettings,
  ReviewState,
  StageDetail,
  StageName,
  WorkCapability,
} from "../api/types";
import { AppShell, STAGES } from "./AppShell";
import { CreateProjectDialog } from "./CreateProjectDialog";

type ProjectsApi = Pick<ApiClient, "listProjects" | "createProject"> &
  Partial<Pick<ApiClient, "getProviderSettings" | "works">>;

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

function stageLabel(stage: StageName | "complete"): string {
  return STAGES.find((item) => item.name === stage)?.label ?? "成片";
}

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

const executionLabels: Record<StageDetail["execution_state"], string> = {
  pending: "待开始",
  ready: "可运行",
  running: "运行中",
  passed: "成果已生成",
  failed: "运行失败",
  blocked: "已阻塞",
  stale: "需要重跑",
};

const reviewLabels: Record<ReviewState, string> = {
  not_ready: "审核尚未开始",
  awaiting_review: "等待确认",
  approved: "已确认",
  changes_requested: "已退回修改",
  auto_approved: "自动通过",
  skipped: "已跳过",
};

function stageTone(stage: StageDetail, current = false): string {
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

function StageMiniRail({ project }: { project: ProjectDetail }) {
  const records = new Map(project.stages.map((stage) => [stage.stage, stage]));
  return (
    <ol className="project-stage-rail" aria-label={`${project.title} 制作进度`}>
      {STAGES.map((item) => {
        const stage = records.get(item.name);
        const tone = stage
          ? stageTone(stage, project.next_stage === item.name)
          : "pending";
        const status = stage
          ? `${executionLabels[stage.execution_state]}；${reviewLabels[stage.review_state]}`
          : "无阶段数据";
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
      <StageMiniRail project={project} />
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

function ProjectsPage({ api }: { api: ProjectsApi }) {
  const [state, setState] = useState<ProjectsState>({ status: "loading" });
  const [filterOpen, setFilterOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
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
          <button className="command-button" type="button" onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" size={17} />
            新建项目
          </button>
        </div>
      </div>

      {filterOpen && (
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
          {filter && (
            <button
              className="icon-button filter-clear"
              type="button"
              aria-label="清除项目筛选"
              title="清除项目筛选"
              onClick={() => setFilter("")}
            >
              <X aria-hidden="true" size={15} />
            </button>
          )}
          <span>{visibleProjects.length} 个匹配项目</span>
        </div>
      )}

      <section className="project-section" aria-labelledby="active-projects-title">
        <div className="section-heading">
          <h2 id="active-projects-title">进行中的项目</h2>
          {state.status === "ready" && <span>{visibleProjects.length} 个</span>}
        </div>
        {state.status === "loading" && <LoadingProjects />}
        {state.status === "busy" && (
          <div className="state-row state-busy" role="status">
            <LoaderCircle aria-hidden="true" size={18} />
            <div>
              <strong>项目正在处理</strong>
              <span>等待当前写入完成后手动重新加载。</span>
            </div>
            <button className="text-button" type="button" onClick={load}>重新加载</button>
          </div>
        )}
        {state.status === "error" && (
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
        )}
        {state.status === "ready" && projects.length === 0 && (
          <div className="empty-state">
            <div>
              <strong>还没有制作项目</strong>
              <span>创建项目后，当前操作会出现在这里。</span>
            </div>
          </div>
        )}
        {state.status === "ready" && projects.length > 0 && visibleProjects.length === 0 && (
          <div className="empty-state">
            <div>
              <strong>没有匹配项目</strong>
              <span>调整标题或项目 ID 筛选条件。</span>
            </div>
          </div>
        )}
        {state.status === "ready" && visibleProjects.length > 0 && (
          <div className="project-list">
            {visibleProjects.map((project) => (
              <ProjectCard key={project.project_id} project={project} />
            ))}
          </div>
        )}
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

      {createOpen && (
        <CreateProjectDialog
          createProject={api.createProject}
          onClose={() => setCreateOpen(false)}
        />
      )}
    </div>
  );
}

function ProjectRoutePage({ stageRoute = false }: { stageRoute?: boolean }) {
  const { id, stage } = useParams();
  const selected = STAGES.find((item) => item.name === stage);
  return (
    <div className="page-frame compact-page">
      <div className="route-context">
        <Link to="/projects">制作项目</Link>
        <span aria-hidden="true">/</span>
        <code>{id}</code>
      </div>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{stageRoute ? `STAGE ${selected?.number ?? "--"}` : "PROJECT"}</p>
          <h1>{stageRoute ? selected?.label ?? "制作阶段" : "项目工作区"}</h1>
        </div>
      </div>
      <div className="route-placeholder">
        <strong>{stageRoute ? "阶段成果将在此处打开" : "选择上方阶段进入当前成果"}</strong>
        <span>详细审核与返修操作由项目工作区承载。</span>
      </div>
    </div>
  );
}

function WorksPage({
  detail = false,
  works,
}: {
  detail?: boolean;
  works: WorkCapability;
}) {
  const { id } = useParams();
  return (
    <div className="page-frame compact-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DELIVERED WORKS</p>
          <h1>{detail ? "作品版本" : "作品中心"}</h1>
          {detail && <code className="heading-id">{id}</code>}
        </div>
      </div>
      <div className="empty-state">
        <div>
          <strong>
            {works.availability === "unavailable"
              ? "作品目录尚未接入"
              : "作品目录已连接"}
          </strong>
          <span>
            {works.availability === "unavailable"
              ? "当前版本不会请求尚未提供的作品接口。"
              : "作品浏览将在本地目录启用后显示。"}
          </span>
        </div>
      </div>
    </div>
  );
}

function CapabilityRow({ name, value }: { name: string; value: ProviderCapability }) {
  return (
    <div className="settings-row">
      <strong>{name}</strong>
      <span className={value.ready ? "provider-ready" : "provider-unavailable"}>
        {value.ready ? "可用" : "未就绪"}
      </span>
      <code title={value.provider || "未配置 Provider"}>{value.provider || "-"}</code>
      <span title={value.model || "未配置模型"}>{value.model || "未配置模型"}</span>
    </div>
  );
}

function SettingsPage({ api }: { api: ProjectsApi }) {
  const [settings, setSettings] = useState<
    { status: "loading" } | { status: "ready"; value: ProviderSettings } | { status: "error" }
  >(() =>
    api.getProviderSettings
      ? { status: "loading" }
      : { status: "ready", value: { capabilities: {} } },
  );

  useEffect(() => {
    if (!api.getProviderSettings) return;
    let active = true;
    void api.getProviderSettings().then(
      (value) => {
        if (active) setSettings({ status: "ready", value });
      },
      () => {
        if (active) setSettings({ status: "error" });
      },
    );
    return () => {
      active = false;
    };
  }, [api]);

  const capabilities = settings.status === "ready"
    ? Object.entries(settings.value.capabilities)
    : [];
  const labels: Record<string, string> = {
    text: "文本",
    image: "图像",
    video: "视频",
    audio: "音频",
  };

  return (
    <div className="page-frame compact-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">PROVIDER STATUS</p>
          <h1>设置</h1>
        </div>
      </div>
      <section className="settings-section" aria-labelledby="provider-title">
        <div className="section-heading">
          <h2 id="provider-title">Provider 状态</h2>
        </div>
        {settings.status === "loading" && (
          <div className="state-row" role="status"><LoaderCircle aria-hidden="true" size={18} />正在读取设置</div>
        )}
        {settings.status === "error" && (
          <div className="state-row state-error" role="alert"><AlertCircle aria-hidden="true" size={18} />无法读取 Provider 状态</div>
        )}
        {settings.status === "ready" && capabilities.length === 0 && (
          <div className="activity-empty">当前没有可用的 Provider 配置</div>
        )}
        {capabilities.map(([name, value]) => value && (
          <CapabilityRow key={name} name={labels[name] ?? name} value={value} />
        ))}
      </section>
    </div>
  );
}

export function App({ api = apiClient }: { api?: ProjectsApi }) {
  const works: WorkCapability = api.works ?? {
    availability: "unavailable",
    reason: "local_catalog_not_configured",
  };
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="projects" element={<ProjectsPage api={api} />} />
          <Route path="projects/:id" element={<ProjectRoutePage />} />
          <Route
            path="projects/:id/stages/:stage"
            element={<ProjectRoutePage stageRoute />}
          />
          <Route path="works" element={<WorksPage works={works} />} />
          <Route path="works/:id" element={<WorksPage detail works={works} />} />
          <Route path="settings" element={<SettingsPage api={api} />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
