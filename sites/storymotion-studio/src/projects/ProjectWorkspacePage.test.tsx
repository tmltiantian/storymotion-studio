import "@testing-library/jest-dom/vitest";

import {
  act,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Artifact,
  ImpactPlan,
  ImpactRequest,
  JobDetail,
  ProjectDetail,
  StageDetail,
  StageName,
  VideoPreflight,
  VideoWorkspace,
} from "../api/types";
import {
  ProjectWorkspacePage,
  type ProjectWorkspaceApi,
} from "./ProjectWorkspacePage";
import { ImpactDialog } from "./ImpactDialog";

const evidence: Artifact = {
  artifact_id: "art_storyboard_preview",
  name: "storyboard-preview.png",
  media_type: "image/png",
  media_url: "/api/media/art_storyboard_preview",
};

const stageNames: StageName[] = [
  "concept",
  "script",
  "storyboard",
  "assets",
  "audio",
  "video",
  "edit",
  "eval",
  "deliver",
];

function stageFixture(
  overrides: Partial<StageDetail> = {},
): StageDetail {
  return {
    stage: "storyboard",
    execution_state: "passed",
    review_state: "awaiting_review",
    review_policy: "manual",
    review_blocks_progress: true,
    revision: 4,
    executor: "pipeline.storyboard",
    blocked_reasons: [],
    error: "",
    presentation: null,
    review_evidence: [{ artifact_id: evidence.artifact_id, label: "阶段成果 1" }],
    artifacts: [evidence],
    active_run_job: null,
    ...overrides,
  };
}

function projectFixture(
  selected: StageDetail = stageFixture(),
  overrides: Partial<ProjectDetail> = {},
): ProjectDetail {
  const stages = stageNames.map((stage, index) => {
    if (stage === selected.stage) return selected;
    return stageFixture({
      stage,
      execution_state: index < 2 ? "passed" : "pending",
      review_state: index < 2 ? "approved" : "not_ready",
      review_policy: index < 2 ? "automatic" : "manual",
      review_blocks_progress: false,
      revision: index < 2 ? 1 : 0,
      review_evidence: [],
      artifacts: [],
    });
  });
  return {
    project_id: "episode_01",
    title: "旧城来信 · 第 01 集",
    mode: "novel",
    target: { duration_seconds: 95 },
    next_stage: selected.stage,
    required_action: "approve_review_evidence",
    stages,
    final_outputs: [],
    eval_reports: [],
    ...overrides,
  };
}

function impactFixture(overrides: Partial<ImpactPlan> = {}): ImpactPlan {
  return {
    schema_version: "motion-comic-factory.impact-plan.v2",
    plan_id: "a".repeat(64),
    request: {
      stage: "storyboard",
      scope: "shot",
      subtitle_style: false,
      selection_counts: { dialogue: 0, character: 0, shot: 1 },
    },
    entries: [
      { stage: "storyboard", item_count: 1 },
      { stage: "video", item_count: 1 },
      { stage: "edit", item_count: 1 },
    ],
    summary: {
      schema_version: "motion-comic-factory.impact-summary.v2",
      regenerated_video_shot_count: 1,
      reused_video_shot_count: 7,
      regenerated_audio_item_count: 0,
      affected_stages: ["storyboard", "video", "edit"],
      estimate: { available: false },
    },
    preserved_artifacts: Array.from(
      { length: 14 },
      (_, index) => `art_reused_${index + 1}`,
    ),
    package_sha256: "b".repeat(64),
    episode_sha256: "c".repeat(64),
    ...overrides,
  };
}

function impactRequestFixture(): ImpactRequest {
  return {
    stage: "storyboard",
    scope: "shot",
    dialogue_ids: [],
    character_ids: [],
    shot_ids: ["shot_03"],
    subtitle_style: false,
  };
}

function workspaceApi(
  selected = stageFixture(),
  project = projectFixture(selected),
): ProjectWorkspaceApi {
  return {
    getProject: vi.fn().mockResolvedValue(project),
    getStage: vi.fn().mockResolvedValue(selected),
    getVideoWorkspace: vi.fn().mockResolvedValue(videoWorkspaceFixture()),
    runStage: vi.fn().mockResolvedValue({ job_id: "2".repeat(32), status: "queued" }),
    approveStage: vi.fn().mockResolvedValue({
      ...selected,
      review_state: "approved",
      review_blocks_progress: false,
    }),
    requestStageChanges: vi.fn().mockResolvedValue({
      ...selected,
      review_state: "changes_requested",
    }),
    previewImpact: vi.fn().mockResolvedValue(impactFixture()),
    applyImpact: vi.fn().mockResolvedValue(project),
    preflightVideo: vi.fn().mockResolvedValue(videoPreflightFixture()),
    confirmVideo: vi.fn().mockResolvedValue({
      generation_token: "memory-only-token",
      generation_request: generationRequest(videoPreflightFixture()),
    }),
    testVideo: vi.fn().mockResolvedValue({ job_id: "1".repeat(32), status: "queued" }),
    generateVideo: vi.fn().mockResolvedValue({ job_id: "1".repeat(32), status: "queued" }),
    getJob: vi.fn().mockResolvedValue(videoJob("completed")),
    resumeJob: vi.fn().mockResolvedValue(videoJob("running")),
    jobEventsUrl: vi.fn().mockReturnValue(`/api/jobs/${"1".repeat(32)}/events`),
  };
}

function videoWorkspaceFixture(
  job: JobDetail | null = null,
  recovery: VideoWorkspace["failed_job_recovery"] = null,
): VideoWorkspace {
  return {
    schema_version: "motion-comic-factory.video-workspace.v1",
    project_id: "episode_01",
    shots: [
      { shot_id: "shot_03", duration_seconds: 5 },
      { shot_id: "shot_04", duration_seconds: 4 },
    ],
    selected_shot_ids: ["shot_03", "shot_04"],
    job,
    failed_job_recovery: recovery,
  };
}

function videoPreflightFixture(): VideoPreflight {
  return {
    schema_version: "motion-comic-factory.video-generation-request.v1",
    project_id: "episode_01",
    project_sha256: "a".repeat(64),
    package_sha256: "b".repeat(64),
    revision_hashes: { storyboard: "c".repeat(64) },
    artifact_hashes: { art_storyboard: "d".repeat(64) },
    approval_hashes: { storyboard: "e".repeat(64) },
    repair_plan_sha256: "",
    shot_ids: ["shot_03", "shot_04"],
    shots: [
      { shot_id: "shot_03", duration: 5, resolution: "768P" },
      { shot_id: "shot_04", duration: 4, resolution: "768P" },
    ],
    provider: "minimax",
    model: "MiniMax-H3",
    resolution: "768P",
    output_seconds: 9,
    estimated_cost_yuan: 4.5,
    price_yuan_per_second: 0.5,
    ready: true,
    blockers: [],
  };
}

function generationRequest(value: VideoPreflight) {
  const request = { ...value };
  delete (request as Partial<VideoPreflight>).ready;
  delete (request as Partial<VideoPreflight>).blockers;
  return request;
}

function videoJob(status: JobDetail["status"]): JobDetail {
  return {
    job_id: "1".repeat(32),
    project_id: "episode_01",
    operation: "video_generate",
    status,
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:01:00Z",
    provider_tasks: {
      shot_03: { status: "completed" },
      shot_04: { status: "completed" },
    },
    result: { total_shots: 2 },
    error: status === "failed" ? "interrupted" : "",
    resume_count: 0,
    last_event_sequence: 4,
  };
}

function videoStageFixture(): StageDetail {
  return stageFixture({
    stage: "video",
    artifacts: [
      {
        artifact_id: "art_candidate_03",
        name: "shot_03.mp4",
        media_type: "video/mp4",
        media_url: "/api/media/art_candidate_03",
        kind: "video",
        viewer: { shot_id: "shot_03", fps: 25, width: 1080, height: 1920 },
      },
    ],
  });
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="workspace-location">{location.pathname}</output>;
}

function WorkspaceTestTree({
  api,
  route,
}: {
  api: ProjectWorkspaceApi;
  route: string;
}) {
  return (
    <div className="app-shell">
      <MemoryRouter initialEntries={[route]}>
        <LocationProbe />
        <Routes>
          <Route
            path="/projects/:id"
            element={<ProjectWorkspacePage api={api} />}
          />
          <Route
            path="/projects/:id/stages/:stage"
            element={<ProjectWorkspacePage api={api} />}
          />
        </Routes>
      </MemoryRouter>
    </div>
  );
}

function renderWorkspace(
  api: ProjectWorkspaceApi = workspaceApi(),
  route = "/projects/episode_01/stages/storyboard",
  strict = false,
) {
  const content = <WorkspaceTestTree api={api} route={route} />;
  return render(strict ? <StrictMode>{content}</StrictMode> : content);
}

function renderImpactDialog(api: ProjectWorkspaceApi) {
  return render(
    <>
      <div className="app-shell" />
      <ImpactDialog
        api={api}
        projectId="episode_01"
        request={impactRequestFixture()}
        issueLabel="动作不连贯"
        description="第三镜动作接不上。"
        returnFocusRef={{ current: document.createElement("button") }}
        onClose={() => undefined}
        onApplied={vi.fn().mockResolvedValue(undefined)}
      />
    </>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});

describe("project review workspace", () => {
  it("runs a pending stage through the production job contract and reloads its revision", async () => {
    const user = userEvent.setup();
    const pending = stageFixture({
      stage: "concept",
      execution_state: "pending",
      review_state: "not_ready",
      review_blocks_progress: false,
      revision: 0,
      executor: "",
      artifacts: [],
    });
    const passed = stageFixture({
      stage: "concept",
      revision: 1,
      presentation: {
        stage: "concept",
        state: "ready",
        title: "雨夜来电",
        premise: "一个深夜电话改变了她的选择。",
        characters: [{ name: "阿眠", role: "主角", description: "谨慎但好奇" }],
      },
      artifacts: [],
    });
    const initialProject = projectFixture(pending, {
      next_stage: "concept",
      required_action: "run_or_resume",
    });
    const completedProject = projectFixture(passed, {
      next_stage: "concept",
      required_action: "approve_review_evidence",
    });
    const client = workspaceApi(pending, initialProject) as ProjectWorkspaceApi & {
      runStage: ReturnType<typeof vi.fn>;
    };
    client.runStage = vi.fn().mockResolvedValue({
      job_id: "2".repeat(32),
      status: "queued",
    });
    vi.mocked(client.getJob).mockResolvedValue({
      ...videoJob("completed"),
      job_id: "2".repeat(32),
      operation: "run_stage",
      result: { completed_stages: ["concept"] },
      provider_tasks: {},
    });
    vi.mocked(client.getProject)
      .mockResolvedValueOnce(initialProject)
      .mockResolvedValue(completedProject);
    vi.mocked(client.getStage)
      .mockResolvedValueOnce(pending)
      .mockResolvedValue(passed);

    render(<WorkspaceTestTree api={client} route="/projects/episode_01/stages/concept" />);
    await user.click(await screen.findByRole("button", { name: "运行概念阶段" }));

    expect(client.runStage).toHaveBeenCalledWith("episode_01", "concept", { enable_live: false });
    expect(await screen.findByRole("heading", { name: "雨夜来电" })).toBeVisible();
    expect(screen.queryByText("concept.json")).not.toBeInTheDocument();
    expect(screen.getAllByText("等待确认")[0]).toBeVisible();
  });

  it("reconnects to an authoritative local-stage job under StrictMode without resubmitting", async () => {
    const runningJob: JobDetail = {
      ...videoJob("running"),
      job_id: "2".repeat(32),
      operation: "run_stage",
      provider_tasks: {},
      result: {},
    };
    const active = {
      ...stageFixture({
        stage: "concept",
        execution_state: "running",
        review_state: "not_ready",
        revision: 0,
        artifacts: [],
      }),
      active_run_job: runningJob,
    } as StageDetail;
    const passed = {
      ...stageFixture({ stage: "concept", revision: 1 }),
      active_run_job: null,
    } as StageDetail;
    let finished = false;
    const client = workspaceApi(active, projectFixture(active));
    vi.mocked(client.getProject).mockImplementation(async () => (
      projectFixture(finished ? passed : active)
    ));
    vi.mocked(client.getStage).mockImplementation(async () => (
      finished ? passed : active
    ));
    vi.mocked(client.getJob).mockImplementation(async () => {
      finished = true;
      return { ...runningJob, status: "completed" };
    });

    renderWorkspace(client, "/projects/episode_01/stages/concept", true);

    await waitFor(() => expect(client.getJob).toHaveBeenCalledWith(runningJob.job_id));
    await waitFor(() => {
      expect(client.getStage).toHaveBeenLastCalledWith(
        "episode_01",
        "concept",
        expect.any(AbortSignal),
      );
    });
    expect(client.runStage).not.toHaveBeenCalled();
  });

  it("does not reload an old local-stage route after navigation", async () => {
    const runningJob: JobDetail = {
      ...videoJob("running"),
      job_id: "2".repeat(32),
      operation: "run_stage",
      provider_tasks: {},
      result: {},
    };
    const concept = {
      ...stageFixture({ stage: "concept", execution_state: "running" }),
      active_run_job: runningJob,
    } as StageDetail;
    const script = {
      ...stageFixture({ stage: "script", revision: 2 }),
      active_run_job: null,
    } as StageDetail;
    let finishJob: (job: JobDetail) => void = () => undefined;
    const client = workspaceApi(concept, projectFixture(concept));
    vi.mocked(client.getStage).mockImplementation(async (_id, stage) => (
      stage === "script" ? script : concept
    ));
    vi.mocked(client.getJob).mockImplementation(() => new Promise((resolve) => {
      finishJob = resolve;
    }));
    const user = userEvent.setup();

    renderWorkspace(client, "/projects/episode_01/stages/concept");
    await waitFor(() => expect(client.getJob).toHaveBeenCalledWith(runningJob.job_id));
    await user.click(screen.getByRole("link", {
      name: "02 剧本：成果已生成；已确认",
    }));
    await screen.findByText("修订 2");
    const callsBeforeOldCompletion = vi.mocked(client.getStage).mock.calls.length;

    await act(async () => finishJob({ ...runningJob, status: "completed" }));

    await waitFor(() => {
      expect(screen.getByTestId("workspace-location")).toHaveTextContent(
        "/projects/episode_01/stages/script",
      );
    });
    expect(vi.mocked(client.getStage).mock.calls).toHaveLength(callsBeforeOldCompletion);
  });

  it("loads a deep-linked project and stage in parallel and shows both states", async () => {
    let resolveProject: (value: ProjectDetail) => void = () => undefined;
    let resolveStage: (value: StageDetail) => void = () => undefined;
    const selected = stageFixture();
    const project = projectFixture(selected);
    const api = workspaceApi(selected, project);
    vi.mocked(api.getProject).mockImplementation(
      () => new Promise((resolve) => { resolveProject = resolve; }),
    );
    vi.mocked(api.getStage).mockImplementation(
      () => new Promise((resolve) => { resolveStage = resolve; }),
    );

    renderWorkspace(api);

    await waitFor(() => {
      expect(api.getProject).toHaveBeenCalledTimes(1);
      expect(api.getStage).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("status", { name: "正在加载项目工作区" })).toBeVisible();

    await act(async () => {
      resolveProject(project);
      resolveStage(selected);
    });

    expect(await screen.findByText("成果已生成")).toBeVisible();
    expect(screen.getByText("等待确认")).toBeVisible();
    expect(screen.getByText("修订 4")).toBeVisible();
  });

  it("renders stage artifacts through the inspectable viewer before review", async () => {
    renderWorkspace();

    const image = await screen.findByRole("img", { name: "storyboard-preview.png" });
    const open = screen.getByRole("link", { name: "查看原图" });
    const review = screen.getByRole("heading", { name: "审核检查" });

    expect(open).toHaveAttribute("href", "/api/media/art_storyboard_preview");
    expect(image.compareDocumentPosition(review) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders creator-facing stage content before remaining media artifacts", async () => {
    const selected = stageFixture({
      presentation: {
        stage: "storyboard",
        state: "ready",
        title: "门外",
        shots: [{ index: 1, title: "门外", action: "她停下。" }],
      },
    });
    renderWorkspace(workspaceApi(selected, projectFixture(selected)));

    const title = await screen.findByRole("heading", { name: "门外" });
    const image = screen.getByRole("img", { name: "storyboard-preview.png" });

    expect(title.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("runs the paid video flow once and recovers the persisted job on remount", async () => {
    const selected = videoStageFixture();
    const project = projectFixture(selected);
    const persisted = videoJob("completed");
    const api = workspaceApi(selected, project);
    let discoveredJob: JobDetail | null = null;
    vi.mocked(api.getVideoWorkspace).mockImplementation(async () => videoWorkspaceFixture(discoveredJob));
    vi.mocked(api.generateVideo).mockImplementation(async () => {
      discoveredJob = persisted;
      return { job_id: persisted.job_id, status: "queued" };
    });
    const user = userEvent.setup();
    const first = renderWorkspace(api, "/projects/episode_01/stages/video");

    expect(await screen.findByText("视频生成预检")).toBeVisible();
    const shots = screen.getByRole("group", { name: "生成镜头" });
    expect(within(shots).getByRole("checkbox", { name: /shot_03/ })).toBeChecked();
    expect(within(shots).getByRole("checkbox", { name: /shot_04/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    await user.click(await screen.findByRole("button", { name: "批量生成所选镜头" }));

    expect(api.confirmVideo).toHaveBeenCalledWith("episode_01", ["shot_03", "shot_04"]);
    expect(api.generateVideo).toHaveBeenCalledTimes(1);
    expect(api.generateVideo).toHaveBeenCalledWith("episode_01", {
      generation_token: "memory-only-token",
      generation_request: generationRequest(videoPreflightFixture()),
    });
    expect(await screen.findByText("生成完成")).toBeVisible();
    first.unmount();

    renderWorkspace(api, "/projects/episode_01/stages/video");
    expect(await screen.findByText("生成完成")).toBeVisible();
    expect(api.getVideoWorkspace).toHaveBeenLastCalledWith("episode_01", expect.any(AbortSignal));
    expect(api.generateVideo).toHaveBeenCalledTimes(1);
  });

  it("recovers a failed persisted video job and resumes without generating again", async () => {
    const selected = videoStageFixture();
    const api = workspaceApi(selected, projectFixture(selected));
    const failed = videoJob("failed");
    const running = { ...videoJob("running"), resume_count: 1 };
    vi.mocked(api.getVideoWorkspace).mockResolvedValue(videoWorkspaceFixture(failed, {
      mode: "poll_only",
      shot_ids: ["shot_03", "shot_04"],
    }));
    vi.mocked(api.getJob).mockResolvedValueOnce(failed).mockResolvedValue(running);
    vi.mocked(api.resumeJob).mockResolvedValue({ job_id: failed.job_id, status: "queued" });
    const user = userEvent.setup();
    renderWorkspace(api, "/projects/episode_01/stages/video");

    await user.click(await screen.findByRole("button", { name: "恢复生成" }));

    expect(api.resumeJob).toHaveBeenCalledWith(failed.job_id);
    expect(await screen.findByText("生成中")).toBeVisible();
    expect(api.generateVideo).not.toHaveBeenCalled();
    expect(api.testVideo).not.toHaveBeenCalled();
  });

  it.each([
    ["poll_only", "恢复生成"],
    ["new_submission_required", "视频生成预检"],
  ] as const)(
    "authoritatively classifies a running job that becomes failed as %s",
    async (mode, expectedControl) => {
      const selected = videoStageFixture();
      const api = workspaceApi(selected, projectFixture(selected));
      const running = videoJob("running");
      const failed = videoJob("failed");
      let resolveClassification!: (value: VideoWorkspace) => void;
      const classification = new Promise<VideoWorkspace>((resolve) => {
        resolveClassification = resolve;
      });
      vi.mocked(api.getVideoWorkspace)
        .mockResolvedValueOnce(videoWorkspaceFixture(running, {
          mode: "poll_only",
          shot_ids: ["shot_03", "shot_04"],
        }))
        .mockImplementationOnce(async () => classification);
      vi.mocked(api.getJob).mockResolvedValue(failed);

      renderWorkspace(api, "/projects/episode_01/stages/video");

      expect(await screen.findByText("正在确认作业恢复方式")).toBeVisible();
      expect(screen.queryByText("视频生成预检")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "恢复生成" })).not.toBeInTheDocument();
      expect(screen.getByRole("group", { name: "生成镜头" })).toBeDisabled();

      await act(async () => {
        resolveClassification(videoWorkspaceFixture(failed, {
          mode,
          shot_ids: ["shot_03", "shot_04"],
        }));
      });

      if (expectedControl === "恢复生成") {
        expect(await screen.findByRole("button", { name: expectedControl })).toBeEnabled();
        expect(screen.queryByText("视频生成预检")).not.toBeInTheDocument();
      } else {
        expect(await screen.findByText(expectedControl)).toBeVisible();
        expect(screen.queryByRole("button", { name: "恢复生成" })).not.toBeInTheDocument();
      }
      expect(api.getVideoWorkspace).toHaveBeenCalledTimes(2);
      expect(api.generateVideo).not.toHaveBeenCalled();
      expect(api.testVideo).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["new_submission_required", "此作业需重新确认后提交"],
    ["historical", "历史作业，与当前修订不一致"],
  ] as const)("exposes fresh preflight for a %s failed job", async (mode, historyLabel) => {
    const selected = videoStageFixture();
    const api = workspaceApi(selected, projectFixture(selected));
    const failed = videoJob("failed");
    vi.mocked(api.getVideoWorkspace).mockResolvedValue(videoWorkspaceFixture(failed, {
      mode,
      shot_ids: mode === "historical" ? [] : ["shot_03"],
    }));
    vi.mocked(api.getJob).mockResolvedValue(failed);
    const user = userEvent.setup();
    renderWorkspace(api, "/projects/episode_01/stages/video");

    expect(await screen.findByText(historyLabel)).toBeVisible();
    expect(await screen.findByText("视频生成预检")).toBeVisible();
    expect(screen.queryByRole("button", { name: "恢复生成" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    await user.click(await screen.findByRole("button", { name: "批量生成所选镜头" }));

    expect(api.generateVideo).toHaveBeenCalledTimes(1);
    expect(api.resumeJob).not.toHaveBeenCalled();
  });

  it("prefills a timecoded video issue and claims success only after persistence", async () => {
    const selected = videoStageFixture();
    const changed = { ...selected, review_state: "changes_requested" as const };
    const api = workspaceApi(selected, projectFixture(selected));
    vi.mocked(api.getProject)
      .mockResolvedValueOnce(projectFixture(selected))
      .mockResolvedValueOnce(projectFixture(changed));
    vi.mocked(api.getStage).mockResolvedValueOnce(selected).mockResolvedValueOnce(changed);
    const user = userEvent.setup();
    renderWorkspace(api, "/projects/episode_01/stages/video");
    const video = await screen.findByTestId("stage-video") as HTMLVideoElement;
    video.currentTime = 2.375;

    await user.click(screen.getByRole("button", { name: "退回修改" }));
    const description = screen.getByRole("textbox", { name: "问题说明" });
    await user.type(description, "角色动作断裂。");

    await user.click(screen.getByRole("button", { name: "在当前时间标记问题" }));
    expect(description).toHaveValue(
      "角色动作断裂。\n\n--- 视频时间标记 ---\n镜头 shot_03\n候选成果 art_candidate_03\n时间码 2.375 秒\n--- 标记结束 ---",
    );
    await user.click(screen.getByRole("button", { name: "在当前时间标记问题" }));
    expect((description as HTMLTextAreaElement).value.match(/--- 视频时间标记 ---/g)).toHaveLength(1);
    expect(api.requestStageChanges).not.toHaveBeenCalled();
    expect(screen.queryByText("问题已提交到当前修订。")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "退回整阶段" }));

    expect(api.requestStageChanges).toHaveBeenCalledWith(
      "episode_01",
      "video",
      {
        revision: 4,
        reason: "[整体成果需调整] 角色动作断裂。\n\n--- 视频时间标记 ---\n镜头 shot_03\n候选成果 art_candidate_03\n时间码 2.375 秒\n--- 标记结束 ---",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("问题已提交到当前修订。")).toBeVisible();
  });

  it("exposes all nine stages as stable links with textual current semantics", async () => {
    renderWorkspace();

    const rail = await screen.findByRole("navigation", { name: "项目阶段" });
    expect(within(rail).getAllByRole("link")).toHaveLength(9);
    expect(
      within(rail).getByRole("link", {
        name: "03 分镜：成果已生成；等待确认",
      }),
    ).toHaveAttribute("aria-current", "step");
    expect(rail).toHaveTextContent("01概念");
    expect(rail).toHaveTextContent("09交付");
  });

  it("binds approval to the displayed revision and evidence, then reloads canonical data", async () => {
    const selected = stageFixture();
    const approved = { ...selected, review_state: "approved" as const };
    const project = projectFixture(selected);
    const canonicalProject = projectFixture(approved, {
      required_action: "run_or_resume",
    });
    const api = workspaceApi(selected, project);
    vi.mocked(api.getProject)
      .mockResolvedValueOnce(project)
      .mockResolvedValueOnce(canonicalProject);
    vi.mocked(api.getStage)
      .mockResolvedValueOnce(selected)
      .mockResolvedValueOnce(approved);
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.type(await screen.findByRole("textbox", { name: "确认说明" }), "分镜节奏和镜头关系已核对。" );
    await user.click(screen.getByRole("button", { name: "确认通过" }));

    expect(api.approveStage).toHaveBeenCalledWith(
      "episode_01",
      "storyboard",
      {
        revision: 4,
        note: "分镜节奏和镜头关系已核对。",
        evidence_artifact_ids: ["art_storyboard_preview"],
      },
      expect.any(AbortSignal),
    );
    expect(api.getProject).toHaveBeenCalledTimes(2);
    expect(api.getStage).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("已确认")).toBeVisible();
  });

  it("approves a JSON-only stage from opaque review evidence", async () => {
    const selected = {
      ...stageFixture({
        stage: "script",
        artifacts: [],
        presentation: {
          stage: "script",
          state: "ready",
          title: "雨夜来电",
          characters: [{ name: "阿眠", role: "主角" }],
          shots: [{
            index: 1,
            title: "门外",
            action: "她停在门边听见铃声。",
            dialogue: [{ speaker: "阿眠", text: "谁？" }],
          }],
        },
      }),
      review_evidence: [
        { artifact_id: "art_script_internal", label: "阶段成果 1" },
      ],
    } as StageDetail;
    const api = workspaceApi(selected, projectFixture(selected));
    const user = userEvent.setup();
    renderWorkspace(api, "/projects/episode_01/stages/script");

    expect(await screen.findByRole("heading", { name: "雨夜来电" })).toBeVisible();
    await user.type(await screen.findByRole("textbox", { name: "确认说明" }), "剧本已核对。");
    expect(screen.getByRole("checkbox", { name: "阶段成果 1" })).toBeChecked();
    expect(screen.queryByText("art_script_internal")).not.toBeInTheDocument();
    expect(screen.queryByText("script.json")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认通过" }));

    expect(api.approveStage).toHaveBeenCalledWith(
      "episode_01",
      "script",
      {
        revision: 4,
        note: "剧本已核对。",
        evidence_artifact_ids: ["art_script_internal"],
      },
      expect.any(AbortSignal),
    );
  });

  it("rejects a stale approval result and reloads the changed revision", async () => {
    const selected = stageFixture();
    const changed = stageFixture({ revision: 5 });
    const api = workspaceApi(selected, projectFixture(selected));
    vi.mocked(api.getProject)
      .mockResolvedValueOnce(projectFixture(selected))
      .mockResolvedValueOnce(projectFixture(changed));
    vi.mocked(api.getStage)
      .mockResolvedValueOnce(selected)
      .mockResolvedValueOnce(changed);
    vi.mocked(api.approveStage).mockResolvedValue({
      ...changed,
      review_state: "approved",
    });
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.type(await screen.findByRole("textbox", { name: "确认说明" }), "确认当前成果。" );
    await user.click(screen.getByRole("button", { name: "确认通过" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("成果修订已变化");
    expect(screen.getByRole("alert")).toHaveClass("message-neutral");
    expect(screen.getByText("修订 5")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认通过" })).toBeDisabled();
  });

  it("keeps item-scoped categories disabled when canonical item IDs are unavailable", async () => {
    const api = workspaceApi();
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(await screen.findByRole("button", { name: "退回修改" }));
    expect(screen.getByLabelText("动作不连贯（缺少可选项目 ID）")).toBeDisabled();
    expect(screen.getByLabelText("对白内容有误（缺少可选项目 ID）")).toBeDisabled();
    expect(api.applyImpact).not.toHaveBeenCalled();
    expect(api.previewImpact).not.toHaveBeenCalled();
    expect(api.requestStageChanges).not.toHaveBeenCalled();
  });

  it("applies only the exact previewed plan after a second explicit action", async () => {
    const api = workspaceApi();
    const onApplied = vi.fn().mockResolvedValue(undefined);
    const returnFocusRef = { current: document.createElement("button") };
    const user = userEvent.setup();
    render(
      <>
        <div className="app-shell" />
        <ImpactDialog
          api={api}
          projectId="episode_01"
          request={impactRequestFixture()}
          issueLabel="动作不连贯"
          description="第三镜动作接不上。"
          returnFocusRef={returnFocusRef}
          onClose={() => undefined}
          onApplied={onApplied}
        />
      </>,
    );

    expect(await screen.findByText("将重做 1 个视频镜头")).toBeVisible();
    expect(screen.getByText("其他 7 个镜头继续复用")).toBeVisible();
    expect(screen.getByText("保留 14 个现有文件")).toBeVisible();
    expect(screen.getByText("费用预估：后端未提供")).toBeVisible();
    expect(screen.getByText("视频 · 1 个项目")).toBeVisible();
    expect(api.applyImpact).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "应用返修计划" }));

    expect(api.applyImpact).toHaveBeenCalledWith(
      "episode_01",
      "a".repeat(64),
      expect.any(AbortSignal),
    );
    expect(api.applyImpact).toHaveBeenCalledTimes(1);
    expect(api.requestStageChanges).not.toHaveBeenCalled();
    expect(onApplied).toHaveBeenCalledTimes(1);
  });

  it("shows every video shot reused for a subtitle-only impact", async () => {
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockResolvedValue(impactFixture({
      request: {
        stage: "edit",
        scope: "subtitle_style",
        subtitle_style: true,
        selection_counts: { dialogue: 0, character: 0, shot: 0 },
      },
      entries: [
        { stage: "edit", item_count: 1 },
        { stage: "eval", item_count: 1 },
        { stage: "deliver", item_count: 1 },
      ],
      summary: {
        schema_version: "motion-comic-factory.impact-summary.v2",
        regenerated_video_shot_count: 0,
        reused_video_shot_count: 8,
        regenerated_audio_item_count: 0,
        affected_stages: ["edit", "eval", "deliver"],
        estimate: { available: false },
      },
      preserved_artifacts: [],
    }));
    render(
      <>
        <div className="app-shell" />
        <ImpactDialog
          api={api}
          projectId="episode_01"
          request={{
            stage: "edit",
            scope: "subtitle_style",
            dialogue_ids: [],
            character_ids: [],
            shot_ids: [],
            subtitle_style: true,
          }}
          issueLabel="字幕样式有误"
          description="字幕需要调整。"
          returnFocusRef={{ current: document.createElement("button") }}
          onClose={() => undefined}
          onApplied={vi.fn().mockResolvedValue(undefined)}
        />
      </>,
    );

    expect(await screen.findByText("将重做 0 个视频镜头")).toBeVisible();
    expect(screen.getByText("其他 8 个镜头继续复用")).toBeVisible();
    expect(screen.getByText("保留 0 个现有文件")).toBeVisible();
  });

  it("discards a stale apply plan and requires a fresh preview", async () => {
    const api = workspaceApi();
    vi.mocked(api.applyImpact)
      .mockRejectedValueOnce({ code: "stale_confirmation" })
      .mockResolvedValueOnce(projectFixture());
    const user = userEvent.setup();
    render(
      <>
        <div className="app-shell" />
        <ImpactDialog
          api={api}
          projectId="episode_01"
          request={impactRequestFixture()}
          issueLabel="动作不连贯"
          description="第三镜动作接不上。"
          returnFocusRef={{ current: document.createElement("button") }}
          onClose={() => undefined}
          onApplied={vi.fn().mockResolvedValue(undefined)}
        />
      </>,
    );

    await user.click(await screen.findByRole("button", { name: "应用返修计划" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("影响计划已过期");
    expect(screen.queryByRole("button", { name: "应用返修计划" })).not.toBeInTheDocument();
    expect(api.applyImpact).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新预览" }));
    await user.click(await screen.findByRole("button", { name: "应用返修计划" }));
    expect(api.previewImpact).toHaveBeenCalledTimes(2);
    expect(api.applyImpact).toHaveBeenCalledTimes(2);
  });

  it("refuses a mismatched or malformed impact preview without enabling apply", async () => {
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockResolvedValue(
      impactFixture({
        plan_id: "not-a-plan-id",
        request: {
          ...impactFixture().request,
          selection_counts: { dialogue: 0, character: 0, shot: 2 },
        },
      }),
    );
    render(
      <>
        <div className="app-shell" />
        <ImpactDialog
          api={api}
          projectId="episode_01"
          request={impactRequestFixture()}
          issueLabel="动作不连贯"
          description="第三镜动作接不上。"
          returnFocusRef={{ current: document.createElement("button") }}
          onClose={() => undefined}
          onApplied={vi.fn().mockResolvedValue(undefined)}
        />
      </>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("影响预览无法验证");
    expect(screen.queryByRole("button", { name: "应用返修计划" })).not.toBeInTheDocument();
    expect(api.applyImpact).not.toHaveBeenCalled();
  });

  it("rejects a duplicate stage entry that understates regenerated work", async () => {
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockResolvedValue(impactFixture({
      entries: [
        { stage: "storyboard", item_count: 1 },
        { stage: "video", item_count: 1 },
        { stage: "video", item_count: 1 },
        { stage: "edit", item_count: 1 },
      ],
    }));

    renderImpactDialog(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("影响预览无法验证");
    expect(screen.queryByRole("button", { name: "应用返修计划" })).not.toBeInTheDocument();
    expect(api.applyImpact).not.toHaveBeenCalled();
  });

  it("rejects a summary count understated against its complete stage entry", async () => {
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockResolvedValue(impactFixture({
      entries: [
        { stage: "storyboard", item_count: 1 },
        { stage: "video", item_count: 2 },
        { stage: "edit", item_count: 1 },
      ],
    }));

    renderImpactDialog(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("影响预览无法验证");
    expect(screen.queryByRole("button", { name: "应用返修计划" })).not.toBeInTheDocument();
    expect(api.applyImpact).not.toHaveBeenCalled();
  });

  it("rejects impact entries outside canonical stage order", async () => {
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockResolvedValue(impactFixture({
      entries: [
        { stage: "video", item_count: 1 },
        { stage: "storyboard", item_count: 1 },
        { stage: "edit", item_count: 1 },
      ],
      summary: {
        ...impactFixture().summary,
        affected_stages: ["video", "storyboard", "edit"],
      },
    }));

    renderImpactDialog(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("影响预览无法验证");
    expect(screen.queryByRole("button", { name: "应用返修计划" })).not.toBeInTheDocument();
  });

  it("uses the legal stage-level request when no scoped item action is selected", async () => {
    const selected = stageFixture();
    const changed = { ...selected, review_state: "changes_requested" as const };
    const api = workspaceApi(selected, projectFixture(selected));
    vi.mocked(api.getProject)
      .mockResolvedValueOnce(projectFixture(selected))
      .mockResolvedValueOnce(projectFixture(changed, {
        required_action: "address_review_changes",
      }));
    vi.mocked(api.getStage)
      .mockResolvedValueOnce(selected)
      .mockResolvedValueOnce(changed);
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(await screen.findByRole("button", { name: "退回修改" }));
    await user.click(screen.getByLabelText("整体成果需调整"));
    const submit = screen.getByRole("button", { name: "退回整阶段" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "问题说明" }), "镜头语言需要整体调整。" );
    await user.click(submit);

    expect(api.requestStageChanges).toHaveBeenCalledWith(
      "episode_01",
      "storyboard",
      {
        revision: 4,
        reason: "[整体成果需调整] 镜头语言需要整体调整。",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("已退回修改")).toBeVisible();
  });

  it("offers a rerun for a persisted whole-stage change request", async () => {
    const changed = {
      ...stageFixture(),
      review_state: "changes_requested" as const,
      review_blocks_progress: true,
    };
    const api = workspaceApi(changed, projectFixture(changed, {
      required_action: "address_review_changes",
    }));

    renderWorkspace(api);

    expect(await screen.findByRole("button", { name: "重新运行分镜阶段" })).toBeEnabled();
  });

  it("recovers controls after a failed stage-level mutation", async () => {
    const api = workspaceApi();
    vi.mocked(api.requestStageChanges)
      .mockRejectedValueOnce({ code: "busy" })
      .mockResolvedValueOnce({
        ...stageFixture(),
        review_state: "changes_requested",
      });
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.click(await screen.findByRole("button", { name: "退回修改" }));
    await user.click(screen.getByLabelText("整体成果需调整"));
    await user.type(screen.getByRole("textbox", { name: "问题说明" }), "整体重新梳理。" );
    const submit = screen.getByRole("button", { name: "退回整阶段" });
    await user.click(submit);

    expect(await screen.findByRole("alert")).toHaveTextContent("项目正在处理");
    expect(screen.getByRole("alert")).toHaveClass("message-operation");
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(api.requestStageChanges).toHaveBeenCalledTimes(2);
  });
  it("keeps impact containment while apply is busy and the parent rerenders", async () => {
    let resolveApply: (project: ProjectDetail) => void = () => undefined;
    const api = workspaceApi();
    vi.mocked(api.applyImpact).mockImplementation(
      () => new Promise((resolve) => { resolveApply = resolve; }),
    );
    const returnFocus = document.createElement("button");
    const focusSpy = vi.spyOn(returnFocus, "focus");
    const props = {
      api,
      projectId: "episode_01",
      request: impactRequestFixture(),
      issueLabel: "动作不连贯",
      description: "第三镜动作接不上。",
      returnFocusRef: { current: returnFocus },
      onApplied: vi.fn().mockResolvedValue(undefined),
    };
    const view = render(
      <>
        <div className="app-shell" />
        <ImpactDialog {...props} onClose={() => undefined} />
      </>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "应用返修计划" }));
    focusSpy.mockClear();

    view.rerender(
      <>
        <div className="app-shell" />
        <ImpactDialog {...props} onClose={() => window.clearTimeout(0)} />
      </>,
    );

    const dialog = screen.getByRole("dialog", { name: "修改影响预览" });
    expect(document.querySelector(".app-shell")).toHaveAttribute("inert");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(focusSpy).not.toHaveBeenCalled();
    await act(async () => resolveApply(projectFixture()));
  });

  it("aborts impact previews and restores containment on StrictMode unmount", async () => {
    const previewSignals: AbortSignal[] = [];
    const api = workspaceApi();
    vi.mocked(api.previewImpact).mockImplementation((_id, _request, signal) => {
      if (signal) previewSignals.push(signal);
      return new Promise(() => undefined);
    });
    const returnFocus = document.createElement("button");
    const view = render(
      <StrictMode>
        <div className="app-shell" />
        <ImpactDialog
          api={api}
          projectId="episode_01"
          request={impactRequestFixture()}
          issueLabel="动作不连贯"
          description="第三镜动作接不上。"
          returnFocusRef={{ current: returnFocus }}
          onApplied={vi.fn().mockResolvedValue(undefined)}
          onClose={() => undefined}
        />
      </StrictMode>,
    );

    await waitFor(() => expect(previewSignals.length).toBeGreaterThanOrEqual(2));
    const mountedShell = document.querySelector<HTMLElement>(".app-shell");
    expect(mountedShell).toHaveAttribute("inert");

    view.unmount();

    expect(previewSignals.every((signal) => signal.aborted)).toBe(true);
    expect(mountedShell).not.toHaveAttribute("inert");
    expect(mountedShell).not.toHaveAttribute("aria-hidden");
  });

  it("redirects an invalid stage route before requesting that stage", async () => {
    const api = workspaceApi();
    renderWorkspace(api, "/projects/episode_01/stages/not-a-stage");

    await waitFor(() => {
      expect(screen.getByTestId("workspace-location")).toHaveTextContent(
        "/projects/episode_01",
      );
    });
    expect(api.getStage).not.toHaveBeenCalledWith(
      "episode_01",
      "not-a-stage",
      expect.any(AbortSignal),
    );
  });

  it("does not let an old mutation completion disturb a newer route mutation", async () => {
    let resolveOld: (stage: StageDetail) => void = () => undefined;
    let resolveNew: (stage: StageDetail) => void = () => undefined;
    let newSignal: AbortSignal | undefined;
    const storyboard = stageFixture();
    const assets = stageFixture({ stage: "assets", revision: 6 });
    const api = workspaceApi(storyboard, projectFixture(storyboard));
    vi.mocked(api.getProject).mockImplementation((_id, signal) => {
      return Promise.resolve(
        signal?.aborted ? projectFixture(storyboard) : projectFixture(assets),
      );
    });
    vi.mocked(api.getProject).mockResolvedValueOnce(projectFixture(storyboard));
    vi.mocked(api.getStage).mockImplementation((_id, stage) => {
      return Promise.resolve(stage === "assets" ? assets : storyboard);
    });
    vi.mocked(api.approveStage)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockImplementationOnce((_id, _stage, _request, signal) => {
        newSignal = signal;
        return new Promise((resolve) => { resolveNew = resolve; });
      });
    const user = userEvent.setup();
    renderWorkspace(api);

    await user.type(await screen.findByRole("textbox", { name: "确认说明" }), "确认分镜。" );
    await user.click(screen.getByRole("button", { name: "确认通过" }));
    await user.click(screen.getByRole("link", {
      name: "04 资产：待开始；审核尚未开始",
    }));
    await screen.findByText("修订 6");
    await user.type(screen.getByRole("textbox", { name: "确认说明" }), "确认资产。" );
    const newApproval = screen.getByRole("button", { name: "确认通过" });
    expect(newApproval).toBeEnabled();
    await user.click(newApproval);
    expect(api.approveStage).toHaveBeenCalledTimes(2);

    await act(async () => resolveOld(storyboard));
    expect(newSignal?.aborted).toBe(false);
    expect(newApproval).toBeDisabled();
    expect(api.getStage).toHaveBeenCalledWith(
      "episode_01",
      "assets",
      expect.any(AbortSignal),
    );
    await act(async () => resolveNew(assets));
  });

  it("aborts current loads and a pending mutation on real unmount under StrictMode", async () => {
    const loadSignals: AbortSignal[] = [];
    let approvalSignal: AbortSignal | undefined;
    const api = workspaceApi();
    vi.mocked(api.getProject).mockImplementation((_id, signal) => {
      if (signal) loadSignals.push(signal);
      return Promise.resolve(projectFixture());
    });
    vi.mocked(api.getStage).mockImplementation((_id, _stage, signal) => {
      if (signal) loadSignals.push(signal);
      return Promise.resolve(stageFixture());
    });
    vi.mocked(api.approveStage).mockImplementation((_id, _stage, _body, signal) => {
      approvalSignal = signal;
      return new Promise(() => undefined);
    });
    const user = userEvent.setup();
    const view = renderWorkspace(api, undefined, true);

    await user.type(await screen.findByRole("textbox", { name: "确认说明" }), "准备确认。" );
    await user.click(screen.getByRole("button", { name: "确认通过" }));
    expect(approvalSignal?.aborted).toBe(false);

    view.unmount();

    expect(loadSignals.length).toBeGreaterThanOrEqual(2);
    expect(loadSignals.every((signal) => signal.aborted)).toBe(true);
    expect(approvalSignal?.aborted).toBe(true);
  });
});
