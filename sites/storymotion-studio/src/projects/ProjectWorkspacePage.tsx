import {
  AlertCircle,
  FileText,
  FolderOpen,
  LoaderCircle,
  Menu,
  RefreshCw,
  X,
} from "lucide-react";
import {
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Link, Navigate, useParams } from "react-router";

import type { ApiClient } from "../api/client";
import type {
  ApproveStageRequest,
  ImpactRequest,
  ProjectDetail,
  RequestChangesRequest,
  StageDetail,
  StageName,
} from "../api/types";
import { STAGES } from "../app/AppShell";
import { ImpactDialog } from "./ImpactDialog";
import { ReviewPanel } from "./ReviewPanel";
import { StageRail, stageLabel } from "./StageRail";
import { useContainedSurface } from "./useContainedSurface";

export type ProjectWorkspaceApi = Pick<
  ApiClient,
  | "getProject"
  | "getStage"
  | "approveStage"
  | "requestStageChanges"
  | "previewImpact"
  | "applyImpact"
>;

type WorkspaceState =
  | { status: "loading" }
  | { status: "ready"; project: ProjectDetail; stage: StageDetail }
  | { status: "busy" }
  | { status: "error" };

type ImpactDraft = {
  request: ImpactRequest;
  issueLabel: string;
  description: string;
  trigger: HTMLButtonElement;
};

type MessageTone = "error" | "neutral" | "operation";

type WorkspaceMessage = {
  text: string;
  tone: MessageTone;
};

type MutationOwner = {
  token: symbol;
  routeGeneration: number;
  routeIdentity: string;
  controller: AbortController;
};

const STAGE_SET = new Set<StageName>(STAGES.map((item) => item.name));

function isStageName(value: string | undefined): value is StageName {
  return Boolean(value && STAGE_SET.has(value as StageName));
}

function errorCode(error: unknown): string {
  return typeof error === "object" && error !== null && "code" in error
    ? String(error.code)
    : "";
}

function mutationMessage(error: unknown): WorkspaceMessage {
  const code = errorCode(error);
  if (code === "busy") {
    return { text: "项目正在处理，当前操作完成后再试。", tone: "operation" };
  }
  if (code === "stale_confirmation") {
    return { text: "成果修订已变化，已重新载入当前版本。", tone: "neutral" };
  }
  return { text: "审核操作未能完成，请检查制作服务后重试。", tone: "error" };
}

function ArtifactWorkspace({ stage }: { stage: StageDetail }) {
  return (
    <section className="artifact-workspace" aria-labelledby="artifact-workspace-title">
      <div className="artifact-heading">
        <div>
          <p className="eyebrow">STAGE ARTIFACTS</p>
          <h1 id="artifact-workspace-title">{stageLabel(stage.stage)}成果</h1>
        </div>
        <code>{stage.executor || "-"}</code>
      </div>

      {stage.error ? (
        <div className="artifact-error" role="alert">
          <AlertCircle aria-hidden="true" size={17} />
          <span>本阶段运行失败，请检查作业记录后重试。</span>
        </div>
      ) : null}

      {stage.artifacts.length === 0 ? (
        <div className="artifact-empty">
          <FolderOpen aria-hidden="true" size={22} />
          <strong>当前阶段没有可查看的成果</strong>
        </div>
      ) : (
        <div className="artifact-list" aria-label="阶段成果">
          {stage.artifacts.map((artifact) => {
            const isImage = artifact.media_type.startsWith("image/");
            const isVideo = artifact.media_type.startsWith("video/");
            const isAudio = artifact.media_type.startsWith("audio/");
            return (
              <figure className="artifact-item" key={artifact.artifact_id}>
                {isImage ? (
                  <img src={artifact.media_url} alt={artifact.name} />
                ) : isVideo ? (
                  <video src={artifact.media_url} controls preload="metadata">
                    <track kind="captions" />
                  </video>
                ) : isAudio ? (
                  <audio src={artifact.media_url} controls preload="metadata" />
                ) : (
                  <a className="artifact-file" href={artifact.media_url}>
                    <FileText aria-hidden="true" size={24} />
                    <span>打开成果文件</span>
                  </a>
                )}
                <figcaption>
                  <strong>{artifact.name}</strong>
                  <code>{artifact.media_type}</code>
                </figcaption>
              </figure>
            );
          })}
        </div>
      )}
    </section>
  );
}

function NavigationDrawer({
  project,
  selectedStage,
  returnFocusRef,
  onClose,
}: {
  project: ProjectDetail;
  selectedStage: StageName;
  returnFocusRef: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);
  const close = useCallback(() => onClose(), [onClose]);
  useContainedSurface({
    surfaceRef: drawerRef,
    initialFocusRef: closeRef,
    returnFocusRef,
    busyRef,
    onClose: close,
  });

  return createPortal(
    <div className="drawer-backdrop">
      <section
        ref={drawerRef}
        className="workspace-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="workspace-drawer-title"
        tabIndex={-1}
      >
        <div className="drawer-heading">
          <div>
            <span id="workspace-drawer-title">项目与阶段导航</span>
            <strong>{project.title}</strong>
          </div>
          <button
            ref={closeRef}
            className="icon-button"
            type="button"
            aria-label="关闭项目与阶段导航"
            title="关闭项目与阶段导航"
            onClick={onClose}
          >
            <X aria-hidden="true" size={17} />
          </button>
        </div>
        <Link className="drawer-project-link" to="/projects" onClick={onClose}>返回制作项目</Link>
        <StageRail
          project={project}
          selectedStage={selectedStage}
          inDrawer
          onNavigate={onClose}
        />
      </section>
    </div>,
    document.body,
  );
}

export function ProjectWorkspacePage({ api }: { api: ProjectWorkspaceApi }) {
  const { id, stage: routeStage } = useParams();
  const [state, setState] = useState<WorkspaceState>({ status: "loading" });
  const [message, setMessage] = useState<WorkspaceMessage | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const [impactDraft, setImpactDraft] = useState<ImpactDraft | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerTriggerRef = useRef<HTMLButtonElement>(null);
  const impactTriggerRef = useRef<HTMLElement>(null);
  const loadGeneration = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const mutationOwnerRef = useRef<MutationOwner | null>(null);
  const routeGenerationRef = useRef(0);
  const routeIdentityRef = useRef("");
  const mountedRef = useRef(true);

  const invalidStageRoute = routeStage !== undefined && !isStageName(routeStage);
  const selectedRouteStage = isStageName(routeStage) ? routeStage : undefined;
  const routeIdentity = `${id ?? ""}:${routeStage ?? ""}`;

  const load = useCallback(async (showLoading = true) => {
    if (!id || invalidStageRoute) {
      setState({ status: "error" });
      return;
    }
    loadController.current?.abort();
    const controller = new AbortController();
    const generation = ++loadGeneration.current;
    loadController.current = controller;
    if (showLoading) setState({ status: "loading" });

    try {
      let project: ProjectDetail;
      let selected: StageName;
      let stage: StageDetail;
      if (selectedRouteStage) {
        [project, stage] = await Promise.all([
          api.getProject(id, controller.signal),
          api.getStage(id, selectedRouteStage, controller.signal),
        ]);
        selected = selectedRouteStage;
      } else {
        project = await api.getProject(id, controller.signal);
        selected = project.next_stage === "complete" ? "deliver" : project.next_stage;
        stage = await api.getStage(id, selected, controller.signal);
      }
      if (!mountedRef.current || generation !== loadGeneration.current) return;
      if (stage.stage !== selected) throw new Error("Stage response mismatch");
      setState({ status: "ready", project, stage });
    } catch (error) {
      if (!mountedRef.current || generation !== loadGeneration.current) return;
      setState({ status: errorCode(error) === "busy" ? "busy" : "error" });
    }
  }, [api, id, invalidStageRoute, selectedRouteStage]);

  useEffect(() => {
    mountedRef.current = true;
    const routeGeneration = ++routeGenerationRef.current;
    routeIdentityRef.current = routeIdentity;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setMutationPending(false);
      setMessage(null);
      setImpactDraft(null);
      setDrawerOpen(false);
      if (!invalidStageRoute) void load(false);
    });
    return () => {
      active = false;
      mountedRef.current = false;
      loadGeneration.current += 1;
      loadController.current?.abort();
      const owner = mutationOwnerRef.current;
      if (owner?.routeGeneration === routeGeneration) {
        owner.controller.abort();
        mutationOwnerRef.current = null;
      }
    };
  }, [invalidStageRoute, load, routeIdentity]);

  const runMutation = useCallback(async (
    operation: (signal: AbortSignal) => Promise<unknown>,
  ) => {
    if (mutationOwnerRef.current) return;
    const owner: MutationOwner = {
      token: Symbol("workspace-mutation"),
      routeGeneration: routeGenerationRef.current,
      routeIdentity: routeIdentityRef.current,
      controller: new AbortController(),
    };
    mutationOwnerRef.current = owner;
    setMutationPending(true);
    setMessage(null);
    const isCurrentOwner = () => (
      mountedRef.current &&
      mutationOwnerRef.current?.token === owner.token &&
      routeGenerationRef.current === owner.routeGeneration &&
      routeIdentityRef.current === owner.routeIdentity
    );
    try {
      await operation(owner.controller.signal);
      if (!isCurrentOwner()) return;
      await load(false);
      if (!isCurrentOwner()) return;
    } catch (error) {
      if (!isCurrentOwner() || owner.controller.signal.aborted) return;
      if (errorCode(error) === "stale_confirmation") await load(false);
      if (isCurrentOwner()) setMessage(mutationMessage(error));
    } finally {
      if (mutationOwnerRef.current?.token === owner.token) {
        mutationOwnerRef.current = null;
        if (
          mountedRef.current &&
          routeGenerationRef.current === owner.routeGeneration &&
          routeIdentityRef.current === owner.routeIdentity
        ) {
          setMutationPending(false);
        }
      }
    }
  }, [load]);

  if (invalidStageRoute) {
    return <Navigate to={id ? `/projects/${encodeURIComponent(id)}` : "/projects"} replace />;
  }

  if (
    state.status === "loading" ||
    (state.status === "ready" &&
      selectedRouteStage !== undefined &&
      state.stage.stage !== selectedRouteStage)
  ) {
    return (
      <div className="workspace-state" role="status" aria-label="正在加载项目工作区">
        <LoaderCircle className="loading-icon" aria-hidden="true" size={19} />
        <span>正在读取项目与阶段成果</span>
      </div>
    );
  }

  if (state.status === "busy" || state.status === "error") {
    return (
      <div className={`workspace-state ${state.status === "error" ? "state-error" : "state-busy"}`} role="alert">
        <AlertCircle aria-hidden="true" size={19} />
        <div>
          <strong>{state.status === "busy" ? "项目正在处理" : "无法读取项目工作区"}</strong>
          <span>{state.status === "busy" ? "等待当前写入完成后重新加载。" : "检查本地制作服务和项目地址后重试。"}</span>
        </div>
        <button className="text-button" type="button" onClick={() => void load()}>
          <RefreshCw aria-hidden="true" size={15} />
          重新加载
        </button>
      </div>
    );
  }

  const { project, stage } = state;
  const selectedStage = stage.stage;

  return (
    <div className="workspace-page">
      <header className="workspace-mobile-heading">
        <button
          ref={drawerTriggerRef}
          className="icon-button workspace-nav-button"
          type="button"
          aria-label="打开项目与阶段导航"
          title="打开项目与阶段导航"
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen(true)}
        >
          <Menu aria-hidden="true" size={18} />
        </button>
        <div>
          <strong>{project.title}</strong>
          <span>{stageLabel(stage.stage)} · 修订 {stage.revision || "-"}</span>
        </div>
      </header>

      <aside className="workspace-navigation-column">
        <Link className="workspace-project-back" to="/projects">制作项目</Link>
        <div className="workspace-project-identity">
          <strong>{project.title}</strong>
          <code>{project.project_id}</code>
        </div>
        <StageRail project={project} selectedStage={selectedStage} />
      </aside>

      <div className="workspace-artifact-column">
        {message ? (
          <div className={`workspace-message message-${message.tone}`} role="alert">
            <AlertCircle aria-hidden="true" size={16} />
            <span>{message.text}</span>
          </div>
        ) : null}
        <ArtifactWorkspace stage={stage} />
      </div>

      <ReviewPanel
        key={`${stage.stage}-${stage.revision}-${stage.artifacts.map((item) => item.artifact_id).join("-")}`}
        stage={stage}
        pending={mutationPending}
        onApprove={(request: ApproveStageRequest) => void runMutation(async (signal) => {
          const result = await api.approveStage(
            project.project_id,
            stage.stage,
            request,
            signal,
          );
          if (result.revision !== request.revision) {
            throw Object.assign(new Error("Approval revision changed"), {
              code: "stale_confirmation",
            });
          }
          return result;
        })}
        onRequestStageChanges={(request: RequestChangesRequest) => void runMutation((signal) =>
          api.requestStageChanges(project.project_id, stage.stage, request, signal),
        )}
        onOpenImpact={(request, issueLabel, description, trigger) => {
          impactTriggerRef.current = trigger;
          setImpactDraft({ request, issueLabel, description, trigger });
        }}
      />

      {drawerOpen ? (
        <NavigationDrawer
          project={project}
          selectedStage={selectedStage}
          returnFocusRef={drawerTriggerRef}
          onClose={() => setDrawerOpen(false)}
        />
      ) : null}

      {impactDraft ? (
        <ImpactDialog
          api={api}
          projectId={project.project_id}
          request={impactDraft.request}
          issueLabel={impactDraft.issueLabel}
          description={impactDraft.description}
          returnFocusRef={impactTriggerRef}
          onClose={() => setImpactDraft(null)}
          onApplied={async () => {
            await load(false);
          }}
        />
      ) : null}
    </div>
  );
}
