import type {
  ApproveStageRequest,
  ConfirmedVideoPreflight,
  CreateProjectRequest,
  ImpactPlan,
  ImpactRequest,
  JobAccepted,
  JobDetail,
  ProjectDetail,
  ProviderSettings,
  RequestChangesRequest,
  ResumeJobResponse,
  RunStageRequest,
  StageDetail,
  StageName,
  VideoGenerationSubmission,
  VideoPreflight,
  WorkCapability,
  WorkCatalogAdapter,
} from "./types";

type FetchImplementation = typeof fetch;

export interface ApiClientOptions {
  baseUrl?: string;
  fetch?: FetchImplementation;
  workCatalog?: WorkCatalogAdapter;
}

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
  }
}

function publicErrorMessage(code: string, status: number): string {
  if (code === "busy") return "项目正在处理，请稍后重试。";
  if (code === "not_found" || status === 404) return "请求的内容不存在。";
  if (code === "invalid_request" || status === 400) return "请求内容无效，请检查后重试。";
  if (code === "stale_confirmation") return "确认信息已过期，请重新检查。";
  if (code === "network_error") return "无法连接制作服务，请检查服务状态。";
  return "操作未能完成，请重试。";
}

function identifier(value: string): string {
  return encodeURIComponent(value);
}

export interface ApiClient {
  listProjects(signal?: AbortSignal): Promise<ProjectDetail[]>;
  createProject(
    request: CreateProjectRequest,
    signal?: AbortSignal,
  ): Promise<JobAccepted>;
  getProject(projectId: string): Promise<ProjectDetail>;
  getStage(projectId: string, stage: StageName): Promise<StageDetail>;
  runStage(
    projectId: string,
    stage: StageName,
    request?: RunStageRequest,
  ): Promise<JobAccepted>;
  approveStage(
    projectId: string,
    stage: StageName,
    request: ApproveStageRequest,
  ): Promise<StageDetail>;
  requestStageChanges(
    projectId: string,
    stage: StageName,
    request: RequestChangesRequest,
  ): Promise<StageDetail>;
  previewImpact(projectId: string, request: ImpactRequest): Promise<ImpactPlan>;
  applyImpact(projectId: string, planId: string): Promise<ProjectDetail>;
  preflightVideo(projectId: string, shotIds: string[]): Promise<VideoPreflight>;
  confirmVideo(
    projectId: string,
    shotIds: string[],
  ): Promise<ConfirmedVideoPreflight>;
  testVideo(
    projectId: string,
    request: VideoGenerationSubmission,
  ): Promise<JobAccepted>;
  generateVideo(
    projectId: string,
    request: VideoGenerationSubmission,
  ): Promise<JobAccepted>;
  getJob(jobId: string): Promise<JobDetail>;
  resumeJob(jobId: string): Promise<ResumeJobResponse>;
  jobEventsUrl(jobId: string): string;
  mediaUrl(artifactId: string): string;
  getMedia(artifactId: string, range?: string): Promise<Blob>;
  works: WorkCapability;
  getProviderSettings(): Promise<ProviderSettings>;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
  const fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);

  const path = (value: string) => `${baseUrl}${value}`;

  async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetchImpl(path(endpoint), {
        ...init,
        headers: {
          Accept: "application/json",
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
    } catch {
      throw new ApiClientError(
        "network_error",
        publicErrorMessage("network_error", 0),
        0,
      );
    }

    if (!response.ok) {
      let code = "request_failed";
      try {
        const payload = (await response.json()) as {
          error?: { code?: unknown };
        };
        if (typeof payload.error?.code === "string") code = payload.error.code;
      } catch {
        // Error bodies are intentionally not surfaced to the UI.
      }
      throw new ApiClientError(
        code,
        publicErrorMessage(code, response.status),
        response.status,
      );
    }

    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  const post = <T>(endpoint: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(endpoint, {
      method: "POST",
      signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });

  const works: WorkCapability = options.workCatalog
    ? { availability: "available", catalog: options.workCatalog }
    : {
        availability: "unavailable",
        reason: "local_catalog_not_configured",
      };

  return {
    listProjects: (signal) =>
      request<ProjectDetail[]>("/api/projects", { signal }),
    createProject: (body, signal) =>
      post<JobAccepted>("/api/projects", body, signal),
    getProject: (projectId) =>
      request<ProjectDetail>(`/api/projects/${identifier(projectId)}`),
    getStage: (projectId, stage) =>
      request<StageDetail>(
        `/api/projects/${identifier(projectId)}/stages/${identifier(stage)}`,
      ),
    runStage: (projectId, stage, body = {}) =>
      post<JobAccepted>(
        `/api/projects/${identifier(projectId)}/stages/${identifier(stage)}/run`,
        { enable_live: body.enable_live ?? false },
      ),
    approveStage: (projectId, stage, body) =>
      post<StageDetail>(
        `/api/projects/${identifier(projectId)}/stages/${identifier(stage)}/approve`,
        body,
      ),
    requestStageChanges: (projectId, stage, body) =>
      post<StageDetail>(
        `/api/projects/${identifier(projectId)}/stages/${identifier(stage)}/request-changes`,
        body,
      ),
    previewImpact: (projectId, body) =>
      post<ImpactPlan>(
        `/api/projects/${identifier(projectId)}/impact-plan`,
        {
          dialogue_ids: [],
          character_ids: [],
          shot_ids: [],
          subtitle_style: false,
          ...body,
        },
      ),
    applyImpact: (projectId, planId) =>
      post<ProjectDetail>(
        `/api/projects/${identifier(projectId)}/impact-plan/${identifier(planId)}/apply`,
      ),
    preflightVideo: (projectId, shotIds) =>
      post<VideoPreflight>(`/api/projects/${identifier(projectId)}/video/preflight`, {
        shot_ids: shotIds,
      }),
    confirmVideo: (projectId, shotIds) =>
      post<ConfirmedVideoPreflight>(
        `/api/projects/${identifier(projectId)}/video/confirm`,
        { shot_ids: shotIds },
      ),
    testVideo: (projectId, body) =>
      post<JobAccepted>(`/api/projects/${identifier(projectId)}/video/test`, body),
    generateVideo: (projectId, body) =>
      post<JobAccepted>(`/api/projects/${identifier(projectId)}/video/generate`, body),
    getJob: (jobId) => request<JobDetail>(`/api/jobs/${identifier(jobId)}`),
    resumeJob: (jobId) =>
      post<ResumeJobResponse>(`/api/jobs/${identifier(jobId)}/resume`),
    jobEventsUrl: (jobId) => path(`/api/jobs/${identifier(jobId)}/events`),
    mediaUrl: (artifactId) => path(`/api/media/${identifier(artifactId)}`),
    getMedia: async (artifactId, range) => {
      let response: Response;
      try {
        response = await fetchImpl(path(`/api/media/${identifier(artifactId)}`), {
          headers: range ? { Range: range } : undefined,
        });
      } catch {
        throw new ApiClientError(
          "network_error",
          publicErrorMessage("network_error", 0),
          0,
        );
      }
      if (!response.ok) {
        throw new ApiClientError(
          response.status === 404 ? "not_found" : "request_failed",
          publicErrorMessage("request_failed", response.status),
          response.status,
        );
      }
      return response.blob();
    },
    works,
    getProviderSettings: () =>
      request<ProviderSettings>("/api/settings/providers"),
  };
}

export const apiClient = createApiClient();
