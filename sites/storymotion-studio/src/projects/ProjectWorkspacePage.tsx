import {
  AlertCircle,
  FolderOpen,
  LoaderCircle,
  Play,
  RefreshCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, Navigate, useParams } from "react-router";

import type { ApiClient } from "../api/client";
import type {
  ApproveStageRequest,
  Artifact,
  ImpactRequest,
  JobDetail,
  ProjectDetail,
  RequestChangesRequest,
  StageDetail,
  StageName,
  VideoWorkspace,
} from "../api/types";
import { STAGES } from "../app/AppShell";
import { JobProgress } from "../jobs/JobProgress";
import { VideoPreflight } from "../jobs/VideoPreflight";
import { StageViewer } from "../stages/StageViewer";
import { ImpactDialog } from "./ImpactDialog";
import { ReviewPanel, type ReviewIssueDraft } from "./ReviewPanel";
import { StageRail, stageLabel } from "./StageRail";

export type ProjectWorkspaceApi = Pick<
  ApiClient,
  | "getProject"
  | "getStage"
  | "getVideoWorkspace"
  | "runStage"
  | "approveStage"
  | "requestStageChanges"
  | "previewImpact"
  | "applyImpact"
  | "preflightVideo"
  | "confirmVideo"
  | "testVideo"
  | "generateVideo"
  | "getJob"
  | "resumeJob"
  | "jobEventsUrl"
>;

type WorkspaceState =
  | { status: "loading" }
  | { status: "ready"; project: ProjectDetail; stage: StageDetail; videoWorkspace: VideoWorkspace | null }
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

type StageRunState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "running"; jobId: string }
  | { status: "error" };

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

function ArtifactWorkspace({
  stage,
  onIssueAtTime,
}: {
  stage: StageDetail;
  onIssueAtTime: (time: number, artifact: Artifact) => void;
}) {
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
        <StageViewer
          stage={stage.stage}
          artifacts={stage.artifacts}
          onIssueAtTime={onIssueAtTime}
        />
      )}
    </section>
  );
}

function VideoGenerationWorkspace({
  api,
  projectId,
  workspace,
}: {
  api: ProjectWorkspaceApi;
  projectId: string;
  workspace: VideoWorkspace;
}) {
  const [selectedShotIds, setSelectedShotIds] = useState(
    () => workspace.selected_shot_ids,
  );
  const [job, setJob] = useState<Pick<JobDetail, "job_id" | "status"> | null>(workspace.job);
  const [failedRecovery, setFailedRecovery] = useState(workspace.failed_job_recovery);
  const [classificationState, setClassificationState] = useState<"idle" | "pending" | "error">("idle");
  const trackedStatusRef = useRef<JobDetail["status"] | null>(workspace.job?.status ?? null);
  const classificationControllerRef = useRef<AbortController | null>(null);
  const loadAuthoritativeClassification = useCallback(() => {
    classificationControllerRef.current?.abort();
    const controller = new AbortController();
    classificationControllerRef.current = controller;
    setClassificationState("pending");
    void api.getVideoWorkspace(projectId, controller.signal).then(
      (authoritative) => {
        if (controller.signal.aborted || classificationControllerRef.current !== controller) return;
        setSelectedShotIds(authoritative.selected_shot_ids);
        setFailedRecovery(authoritative.failed_job_recovery);
        setJob(authoritative.job ? {
          job_id: authoritative.job.job_id,
          status: authoritative.job.status,
        } : null);
        trackedStatusRef.current = authoritative.job?.status ?? null;
        setClassificationState("idle");
      },
      () => {
        if (!controller.signal.aborted && classificationControllerRef.current === controller) {
          setClassificationState("error");
        }
      },
    );
  }, [api, projectId]);
  useEffect(() => () => classificationControllerRef.current?.abort(), []);
  const updateJob = useCallback((next: JobDetail) => {
    const previous = trackedStatusRef.current;
    trackedStatusRef.current = next.status;
    setJob({ job_id: next.job_id, status: next.status });
    if (
      previous !== null
      && !["completed", "failed", "cancelled"].includes(previous)
      && ["completed", "failed", "cancelled"].includes(next.status)
    ) {
      loadAuthoritativeClassification();
    }
  }, [loadAuthoritativeClassification]);
  const activeJob = Boolean(job && ["queued", "running"].includes(job.status));
  const pollOnlyFailure = Boolean(
    job?.status === "failed" && failedRecovery?.mode === "poll_only",
  );
  const generationLocked = activeJob || pollOnlyFailure || classificationState !== "idle";
  const recoveryLabel = job?.status === "failed"
    ? failedRecovery?.mode === "historical"
      ? "历史作业，与当前修订不一致"
      : failedRecovery?.mode === "new_submission_required"
        ? "此作业需重新确认后提交"
        : ""
    : "";

  return (
    <section className="video-generation-workspace" aria-labelledby="video-generation-title">
      <div className="video-generation-heading">
        <div>
          <p className="eyebrow">GENERATION</p>
          <h2 id="video-generation-title">视频生成</h2>
        </div>
      </div>
      <fieldset className="video-shot-selection" disabled={generationLocked}>
        <legend>生成镜头</legend>
        {workspace.shots.map((shot) => (
          <label key={shot.shot_id}>
            <input
              type="checkbox"
              checked={selectedShotIds.includes(shot.shot_id)}
              onChange={() => setSelectedShotIds((current) => (
                current.includes(shot.shot_id)
                  ? current.filter((item) => item !== shot.shot_id)
                  : workspace.shots
                    .filter((item) => [...current, shot.shot_id].includes(item.shot_id))
                    .map((item) => item.shot_id)
              ))}
            />
            <span>{shot.shot_id}</span>
            <time>{shot.duration_seconds.toFixed(2)} 秒</time>
          </label>
        ))}
      </fieldset>
      {job ? (
        <>
          {classificationState === "pending" ? (
            <p className="job-history-label" role="status">正在确认作业恢复方式</p>
          ) : null}
          {classificationState === "error" ? (
            <button className="text-button" type="button" onClick={loadAuthoritativeClassification}>
              <RefreshCw aria-hidden="true" size={15} />重新确认恢复方式
            </button>
          ) : null}
          {recoveryLabel ? <p className="job-history-label">{recoveryLabel}</p> : null}
          <JobProgress
            api={api}
            jobId={job.job_id}
            onJobChange={updateJob}
            allowResume={
              classificationState === "idle"
              && failedRecovery?.mode === "poll_only"
            }
          />
        </>
      ) : null}
      {!generationLocked ? (
        <VideoPreflight
          api={api}
          projectId={projectId}
          shotIds={selectedShotIds}
          onJobAccepted={(accepted) => {
            trackedStatusRef.current = accepted.status;
            setFailedRecovery(null);
            setJob(accepted);
          }}
        />
      ) : null}
    </section>
  );
}

export function ProjectWorkspacePage({ api }: { api: ProjectWorkspaceApi }) {
  const { id, stage: routeStage } = useParams();
  const [state, setState] = useState<WorkspaceState>({ status: "loading" });
  const [message, setMessage] = useState<WorkspaceMessage | null>(null);
  const [mutationPending, setMutationPending] = useState(false);
  const [impactDraft, setImpactDraft] = useState<ImpactDraft | null>(null);
  const [reviewIssue, setReviewIssue] = useState<ReviewIssueDraft | null>(null);
  const [stageRun, setStageRun] = useState<StageRunState>({ status: "idle" });
  const impactTriggerRef = useRef<HTMLElement>(null);
  const loadGeneration = useRef(0);
  const loadController = useRef<AbortController | null>(null);
  const mutationOwnerRef = useRef<MutationOwner | null>(null);
  const routeGenerationRef = useRef(0);
  const routeIdentityRef = useRef("");
  const mountedRef = useRef(true);
  const stageRunGenerationRef = useRef(0);
  const stageRunRoutesRef = useRef(new Map<string, string>());
  const handledStageRunRef = useRef("");

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
      let videoWorkspace: VideoWorkspace | null = null;
      if (selectedRouteStage) {
        const [loadedProject, loadedStage, loadedVideoWorkspace] = await Promise.all([
          api.getProject(id, controller.signal),
          api.getStage(id, selectedRouteStage, controller.signal),
          selectedRouteStage === "video"
            ? api.getVideoWorkspace(id, controller.signal)
            : Promise.resolve(null),
        ]);
        project = loadedProject;
        stage = loadedStage;
        videoWorkspace = loadedVideoWorkspace;
        selected = selectedRouteStage;
      } else {
        project = await api.getProject(id, controller.signal);
        selected = project.next_stage === "complete" ? "deliver" : project.next_stage;
        [stage, videoWorkspace] = await Promise.all([
          api.getStage(id, selected, controller.signal),
          selected === "video"
            ? api.getVideoWorkspace(id, controller.signal)
            : Promise.resolve(null),
        ]);
      }
      if (!mountedRef.current || generation !== loadGeneration.current) return;
      if (stage.stage !== selected) throw new Error("Stage response mismatch");
      if (videoWorkspace && videoWorkspace.project_id !== project.project_id) {
        throw new Error("Video workspace response mismatch");
      }
      setState({ status: "ready", project, stage, videoWorkspace });
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
      setReviewIssue(null);
      stageRunGenerationRef.current += 1;
      handledStageRunRef.current = "";
      setStageRun({ status: "idle" });
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
  ): Promise<boolean> => {
    if (mutationOwnerRef.current) return false;
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
      if (!isCurrentOwner()) return false;
      await load(false);
      if (!isCurrentOwner()) return false;
      return true;
    } catch (error) {
      if (!isCurrentOwner() || owner.controller.signal.aborted) return false;
      if (errorCode(error) === "stale_confirmation") await load(false);
      if (isCurrentOwner()) setMessage(mutationMessage(error));
      return false;
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

  const handleStageRunJob = useCallback((job: JobDetail) => {
    if (!["completed", "failed", "cancelled"].includes(job.status)) return;
    if (handledStageRunRef.current === job.job_id) return;
    if (stageRunRoutesRef.current.get(job.job_id) !== routeIdentityRef.current) return;
    handledStageRunRef.current = job.job_id;
    stageRunGenerationRef.current += 1;
    if (job.status === "completed") {
      setMessage({ text: "阶段运行完成，已载入当前修订。", tone: "neutral" });
      setStageRun({ status: "idle" });
    } else {
      setMessage({ text: "阶段运行未完成，请检查作业记录后重试。", tone: "error" });
      setStageRun({ status: "error" });
    }
    void load(false);
  }, [load]);

  useEffect(() => {
    if (state.status !== "ready") return;
    const jobId = state.stage.active_run_job?.job_id ?? (
      stageRun.status === "running" ? stageRun.jobId : ""
    );
    if (jobId) stageRunRoutesRef.current.set(jobId, routeIdentity);
  }, [routeIdentity, stageRun, state]);

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
  const canRunStage = ["pending", "ready", "failed", "stale"].includes(
    stage.execution_state,
  ) || stage.review_state === "changes_requested";
  const trackedStageJobId = stage.active_run_job?.job_id ?? (
    stageRun.status === "running" ? stageRun.jobId : ""
  );

  async function runSelectedStage() {
    if (!canRunStage || stageRun.status === "submitting" || stageRun.status === "running") return;
    const generation = ++stageRunGenerationRef.current;
    setMessage(null);
    setStageRun({ status: "submitting" });
    try {
      const accepted = await api.runStage(project.project_id, stage.stage, { enable_live: false });
      if (!mountedRef.current || generation !== stageRunGenerationRef.current) return;
      stageRunRoutesRef.current.set(accepted.job_id, routeIdentityRef.current);
      setStageRun({ status: "running", jobId: accepted.job_id });
    } catch (error) {
      if (!mountedRef.current || generation !== stageRunGenerationRef.current) return;
      setMessage(mutationMessage(error));
      setStageRun({ status: "error" });
    }
  }

  return (
    <div className="workspace-page">
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
        <ArtifactWorkspace
          stage={stage}
          onIssueAtTime={(time, artifact) => {
            const shotId = artifact.viewer?.shot_id?.trim();
            if (!shotId) return;
            setReviewIssue({
              key: `${artifact.artifact_id}:${time}`,
              shotId,
              artifactId: artifact.artifact_id,
              timeSeconds: time,
            });
          }}
        />
        {trackedStageJobId ? (
          <section className="stage-run-control" aria-label="阶段执行">
            <div>
              <strong>阶段正在本机运行</strong>
              <span>页面会连接持久化作业，不会重复提交。</span>
            </div>
            <JobProgress
              key={`${stage.stage}:${trackedStageJobId}`}
              api={api}
              jobId={trackedStageJobId}
              allowResume={false}
              onJobChange={handleStageRunJob}
            />
          </section>
        ) : canRunStage ? (
          <section className="stage-run-control" aria-label="阶段执行">
            <div>
              <strong>准备生成本阶段成果</strong>
              <span>本机执行不会开启收费视频生成。</span>
            </div>
            <button
              className="command-button"
              type="button"
              disabled={stageRun.status === "submitting"}
              onClick={() => void runSelectedStage()}
            >
              {stageRun.status === "submitting" ? (
                <LoaderCircle className="loading-icon" aria-hidden="true" size={16} />
              ) : (
                <Play aria-hidden="true" size={16} />
              )}
              {stageRun.status === "submitting"
                ? `正在运行${stageLabel(stage.stage)}`
                : `${stage.execution_state === "failed" || stage.execution_state === "stale" || stage.review_state === "changes_requested" ? "重新" : ""}运行${stageLabel(stage.stage)}阶段`}
            </button>
          </section>
        ) : null}
        {stage.stage === "video" && state.videoWorkspace ? (
          <VideoGenerationWorkspace
            key={`${project.project_id}:${state.videoWorkspace.job?.job_id ?? "new"}`}
            api={api}
            projectId={project.project_id}
            workspace={state.videoWorkspace}
          />
        ) : null}
      </div>

      <ReviewPanel
        key={`${stage.stage}-${stage.revision}-${stage.review_evidence.map((item) => item.artifact_id).join("-")}`}
        stage={stage}
        pending={mutationPending}
        issueDraft={reviewIssue}
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
        onRequestStageChanges={async (request: RequestChangesRequest) => {
          const issue = reviewIssue;
          const success = await runMutation((signal) =>
            api.requestStageChanges(project.project_id, stage.stage, request, signal));
          if (success && issue) {
            setReviewIssue(null);
            setMessage({ text: "问题已提交到当前修订。", tone: "neutral" });
          }
          return success;
        }}
        onOpenImpact={(request, issueLabel, description, trigger) => {
          impactTriggerRef.current = trigger;
          setImpactDraft({ request, issueLabel, description, trigger });
        }}
      />

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
