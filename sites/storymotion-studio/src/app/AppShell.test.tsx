import "@testing-library/jest-dom/vitest";

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiClient, type ApiClient } from "../api/client";
import type { CreateProjectRequest, JobAccepted, ProjectDetail } from "../api/types";
import { App } from "./App";
import { AppShell } from "./AppShell";

const stages: ProjectDetail["stages"] = [
  ["concept", "passed", "approved"],
  ["script", "passed", "approved"],
  ["storyboard", "passed", "awaiting_review"],
  ["assets", "pending", "not_ready"],
  ["audio", "pending", "not_ready"],
  ["video", "pending", "not_ready"],
  ["edit", "pending", "not_ready"],
  ["eval", "pending", "not_ready"],
  ["deliver", "pending", "not_ready"],
].map(([stage, execution_state, review_state], index) => ({
  stage,
  execution_state,
  review_state,
  review_policy: index === 2 ? "manual" : "automatic",
  review_blocks_progress: index === 2,
  revision: 1,
  executor: "pipeline",
  blocked_reasons: [],
  error: "",
  artifacts: [],
})) as ProjectDetail["stages"];

const project: ProjectDetail = {
  project_id: "episode_01",
  title: "旧城来信 · 第 01 集",
  mode: "novel",
  target: { duration_seconds: 95 },
  next_stage: "storyboard",
  required_action: "approve_review_evidence",
  stages,
  final_outputs: [],
  eval_reports: [],
};

function projectsApi(result: Promise<ProjectDetail[]> = Promise.resolve([project])) {
  return {
    listProjects: vi
      .fn<(signal?: AbortSignal) => Promise<ProjectDetail[]>>()
      .mockImplementation(() => result),
    createProject: vi.fn<ApiClient["createProject"]>(),
  };
}

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

describe("production workbench shell", () => {
  it("opens the current project action without searching the stage list", async () => {
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={projectsApi()} />);

    await user.click(await screen.findByRole("link", { name: "确认分镜" }));

    await waitFor(() => {
      expect(window.location.pathname).toBe(
        "/projects/episode_01/stages/storyboard",
      );
    });
  });

  it("keeps projects, works, and settings as primary navigation", () => {
    render(
      <MemoryRouter>
        <AppShell />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "制作项目" })).toBeVisible();
    expect(screen.getByRole("link", { name: "作品中心" })).toBeVisible();
    expect(screen.getByRole("link", { name: "设置" })).toBeVisible();
  });

  it("shows loading and opens project creation from the empty state", async () => {
    let resolveProjects: (projects: ProjectDetail[]) => void = () => undefined;
    const pending = new Promise<ProjectDetail[]>((resolve) => {
      resolveProjects = resolve;
    });
    window.history.replaceState({}, "", "/projects");
    render(<App api={projectsApi(pending)} />);

    expect(screen.getByRole("status", { name: "正在加载项目" })).toBeVisible();

    await act(async () => resolveProjects([]));

    expect(await screen.findByText("还没有制作项目")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "新建项目" }));
    expect(screen.getByRole("dialog", { name: "新建项目" })).toBeVisible();
  });

  it("separates a busy response from a load failure", async () => {
    window.history.replaceState({}, "", "/projects");
    render(
      <App
        api={projectsApi(
          Promise.reject({ code: "busy", message: "Project is busy" }),
        )}
      />,
    );

    expect(await screen.findByText("项目正在处理")).toBeVisible();
    expect(screen.queryByText("无法读取制作项目")).not.toBeInTheDocument();
  });

  it("offers a retry after a project load failure", async () => {
    window.history.replaceState({}, "", "/projects");
    const api = {
      listProjects: vi
        .fn<() => Promise<ProjectDetail[]>>()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce([project]),
      createProject: vi.fn<
        (request: CreateProjectRequest) => Promise<JobAccepted>
      >(),
    };
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByText("无法读取制作项目")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重新加载" }));

    expect(await screen.findByText(project.title)).toBeVisible();
  });

  it("filters projects by project ID", async () => {
    const secondProject: ProjectDetail = {
      ...project,
      project_id: "episode_02",
      title: "雾港追踪 · 第 02 集",
    };
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={projectsApi(Promise.resolve([project, secondProject]))} />);
    await screen.findByText(project.title);

    await user.click(screen.getByRole("button", { name: "筛选项目" }));
    await user.type(screen.getByRole("searchbox", { name: "筛选制作项目" }), "episode_02");

    expect(screen.queryByText(project.title)).not.toBeInTheDocument();
    expect(screen.getByText(secondProject.title)).toBeVisible();
    expect(screen.getByText("1 个匹配项目")).toBeVisible();
  });

  it("submits a new original project and shows its queued job", async () => {
    const api = projectsApi();
    api.createProject.mockResolvedValue({ job_id: "c".repeat(32), status: "queued" });
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText(project.title);

    const trigger = screen.getByRole("button", { name: "新建项目" });
    await user.click(trigger);
    await user.type(screen.getByRole("textbox", { name: "项目 ID" }), "episode_03");
    await user.type(screen.getByRole("textbox", { name: "项目标题" }), "潮汐来信 · 第 03 集");
    await user.type(screen.getByRole("textbox", { name: "创作构想" }), "一封被潮水送回的信。" );
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(api.createProject).toHaveBeenCalledWith(
      {
        project_id: "episode_03",
        title: "潮汐来信 · 第 03 集",
        mode: "original",
        idea: "一封被潮水送回的信。",
        source_artifact_id: "",
        target: {},
        approval_preset: "standard",
      },
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("项目已进入创建队列")).toBeVisible();
    expect(screen.getByText("c".repeat(32))).toBeVisible();
    await user.click(screen.getByRole("button", { name: "完成" }));
    expect(trigger).toHaveFocus();
  });

  it("completes project creation under React StrictMode", async () => {
    let resolveCreate: (job: JobAccepted) => void = () => undefined;
    const pending = new Promise<JobAccepted>((resolve) => {
      resolveCreate = resolve;
    });
    const api = projectsApi();
    api.createProject.mockImplementation(() => pending);
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(
      <StrictMode>
        <App api={api} />
      </StrictMode>,
    );
    await screen.findByText(project.title);
    const trigger = screen.getByRole("button", { name: "新建项目" });

    await user.click(trigger);
    expect(screen.getByRole("textbox", { name: "项目 ID" })).toHaveFocus();
    await user.type(screen.getByRole("textbox", { name: "项目 ID" }), "episode_05");
    await user.type(screen.getByRole("textbox", { name: "项目标题" }), "雨夜归档");
    await user.type(screen.getByRole("textbox", { name: "创作构想" }), "一卷在雨夜归档的旧胶片。" );
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(api.createProject).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "正在创建" })).toBeDisabled();
    await act(async () =>
      resolveCreate({ job_id: "e".repeat(32), status: "queued" }),
    );

    expect(await screen.findByText("项目已进入创建队列")).toBeVisible();
    expect(screen.queryByRole("button", { name: "正在创建" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "完成" }));
    expect(trigger).toHaveFocus();
  });

  it("recovers from a create error under React StrictMode", async () => {
    let rejectCreate: (error: unknown) => void = () => undefined;
    const pending = new Promise<JobAccepted>((_resolve, reject) => {
      rejectCreate = reject;
    });
    const api = projectsApi();
    api.createProject.mockImplementation(() => pending);
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(
      <StrictMode>
        <App api={api} />
      </StrictMode>,
    );
    await screen.findByText(project.title);
    const trigger = screen.getByRole("button", { name: "新建项目" });

    await user.click(trigger);
    expect(screen.getByRole("textbox", { name: "项目 ID" })).toHaveFocus();
    await user.type(screen.getByRole("textbox", { name: "项目 ID" }), "episode_06");
    await user.type(screen.getByRole("textbox", { name: "项目标题" }), "失焦来电");
    await user.type(screen.getByRole("textbox", { name: "创作构想" }), "一次无人接听的深夜来电。" );
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(api.createProject).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "正在创建" })).toBeDisabled();
    await act(async () => rejectCreate(new Error("offline")));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法创建项目");
    expect(screen.getByRole("button", { name: "创建项目" })).toBeEnabled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "新建项目" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("traps focus, makes the shell inert, and restores focus after Escape", async () => {
    const api = projectsApi();
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText(project.title);
    const trigger = screen.getByRole("button", { name: "新建项目" });

    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "新建项目" });
    const shell = document.querySelector(".app-shell");
    const firstField = screen.getByRole("textbox", { name: "项目 ID" });
    const close = screen.getByRole("button", { name: "关闭新建项目" });
    const submit = screen.getByRole("button", { name: "创建项目" });
    expect(firstField).toHaveFocus();
    expect(shell).toHaveAttribute("inert");
    expect(shell).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("link", { name: "制作项目" })).not.toBeInTheDocument();

    close.focus();
    await user.tab({ shift: true });
    expect(submit).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "新建项目" })).not.toBeInTheDocument();
    expect(shell).not.toHaveAttribute("inert");
    expect(shell).not.toHaveAttribute("aria-hidden");
    expect(trigger).toHaveFocus();
  });

  it("keeps a pending create safe from Escape, duplicate submit, and unmount", async () => {
    let resolveCreate: (job: JobAccepted) => void = () => undefined;
    let submissionSignal: AbortSignal | undefined;
    const pending = new Promise<JobAccepted>((resolve) => {
      resolveCreate = resolve;
    });
    const api = projectsApi();
    api.createProject.mockImplementation((_request, signal) => {
      submissionSignal = signal;
      return pending;
    });
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    const view = render(
      <StrictMode>
        <App api={api} />
      </StrictMode>,
    );
    await screen.findByText(project.title);
    await user.click(screen.getByRole("button", { name: "新建项目" }));
    await user.type(screen.getByRole("textbox", { name: "项目 ID" }), "episode_04");
    await user.type(screen.getByRole("textbox", { name: "项目标题" }), "岸边回声");
    await user.type(screen.getByRole("textbox", { name: "创作构想" }), "一段未完成的回声。" );
    await user.click(screen.getByRole("button", { name: "创建项目" }));
    const pendingButton = screen.getByRole("button", { name: "正在创建" });

    fireEvent.submit(pendingButton.closest("form") as HTMLFormElement);
    await user.keyboard("{Escape}");

    expect(api.createProject).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "新建项目" })).toBeVisible();
    expect(pendingButton).toBeDisabled();
    expect(submissionSignal?.aborted).toBe(false);

    view.unmount();
    expect(submissionSignal?.aborted).toBe(true);
    await act(async () =>
      resolveCreate({ job_id: "d".repeat(32), status: "queued" }),
    );
  });

  it.each([
    [{ code: "busy" }, "项目正在处理"],
    [new Error("offline"), "无法创建项目"],
  ])("shows a controlled create failure for %p", async (failure, message) => {
    const api = projectsApi();
    api.createProject.mockRejectedValue(failure);
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText(project.title);

    await user.click(screen.getByRole("button", { name: "新建项目" }));
    await user.type(screen.getByRole("textbox", { name: "项目 ID" }), "episode_03");
    await user.type(screen.getByRole("textbox", { name: "项目标题" }), "潮汐来信");
    await user.type(screen.getByRole("textbox", { name: "创作构想" }), "潮汐送回一封信。" );
    await user.click(screen.getByRole("button", { name: "创建项目" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("aborts a manual refresh when the projects page unmounts", async () => {
    let refreshSignal: AbortSignal | undefined;
    const api = projectsApi();
    api.listProjects
      .mockResolvedValueOnce([project])
      .mockImplementationOnce((signal?: AbortSignal) => {
        refreshSignal = signal;
        return new Promise<ProjectDetail[]>(() => undefined);
      });
    window.history.replaceState({}, "", "/projects");
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText(project.title);

    await user.click(screen.getByRole("button", { name: "刷新项目" }));
    await user.click(screen.getByRole("link", { name: "作品中心" }));

    expect(refreshSignal?.aborted).toBe(true);
  });

  it("exposes every stage execution and review state as text", async () => {
    const executionStates: ProjectDetail["stages"][number]["execution_state"][] = [
      "pending",
      "ready",
      "running",
      "passed",
      "failed",
      "blocked",
      "stale",
      "passed",
      "passed",
    ];
    const reviewStates: ProjectDetail["stages"][number]["review_state"][] = [
      "not_ready",
      "changes_requested",
      "not_ready",
      "awaiting_review",
      "approved",
      "auto_approved",
      "skipped",
      "approved",
      "approved",
    ];
    const stateProject: ProjectDetail = {
      ...project,
      next_stage: "script",
      required_action: "address_review_changes",
      stages: stages.map((stage, index) => ({
        ...stage,
        execution_state: executionStates[index],
        review_state: reviewStates[index],
      })),
    };
    window.history.replaceState({}, "", "/projects");
    render(<App api={projectsApi(Promise.resolve([stateProject]))} />);

    const rail = await screen.findByRole("list", { name: `${project.title} 制作进度` });
    expect(rail).toHaveTextContent("待开始");
    expect(rail).toHaveTextContent("可运行");
    expect(rail).toHaveTextContent("运行中");
    expect(rail).toHaveTextContent("成果已生成");
    expect(rail).toHaveTextContent("运行失败");
    expect(rail).toHaveTextContent("审核尚未开始");
    expect(rail).toHaveTextContent("等待确认");
    expect(rail).toHaveTextContent("已退回修改");
    expect(rail).toHaveTextContent("已确认");
    expect(rail).toHaveTextContent("已阻塞");
    expect(rail).toHaveTextContent("需要重跑");
    expect(rail).toHaveTextContent("自动通过");
    expect(rail).toHaveTextContent("已跳过");
    expect(
      within(rail).getByRole("listitem", {
        name: "02 剧本：可运行；已退回修改",
      }),
    ).toHaveClass("stage-changes");
    expect(screen.getByText("需要修改")).toBeVisible();
    expect(screen.getByRole("link", { name: "处理剧本修改" })).toHaveClass(
      "action-changes",
    );
  });

  it("loads an empty works catalog through the real page contract", async () => {
    window.history.replaceState({}, "", "/works");
    const api = {
      ...projectsApi(),
      listWorks: vi.fn().mockResolvedValue([]),
    };
    render(<App api={api} />);

    expect(await screen.findByText("还没有可查看的作品")).toBeVisible();
    expect(api.listWorks).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("searchbox", { name: "筛选作品" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "查看作业活动" })).not.toBeInTheDocument();
  });
});

describe("API errors", () => {
  it("does not expose server paths in centralized errors", async () => {
    const fetchFake = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "blocked",
            message: "Invalid file /private/workspace/project.json",
          },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = createApiClient({ fetch: fetchFake });

    await expect(client.getProject("episode_01")).rejects.toMatchObject({
      code: "blocked",
      message: "操作未能完成，请重试。",
    });
  });
});
