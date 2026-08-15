import "@testing-library/jest-dom/vitest";

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createApiClient } from "../api/client";
import type { ProjectDetail } from "../api/types";
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
  return { listProjects: vi.fn(() => result) };
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

  it("shows loading and then an actionable empty state", async () => {
    let resolveProjects: (projects: ProjectDetail[]) => void = () => undefined;
    const pending = new Promise<ProjectDetail[]>((resolve) => {
      resolveProjects = resolve;
    });
    window.history.replaceState({}, "", "/projects");
    render(<App api={projectsApi(pending)} />);

    expect(screen.getByRole("status", { name: "正在加载项目" })).toBeVisible();

    await act(async () => resolveProjects([]));

    expect(await screen.findByText("还没有制作项目")).toBeVisible();
    expect(screen.getByRole("button", { name: "新建项目" })).toBeEnabled();
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
    };
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByText("无法读取制作项目")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重新加载" }));

    expect(await screen.findByText(project.title)).toBeVisible();
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
