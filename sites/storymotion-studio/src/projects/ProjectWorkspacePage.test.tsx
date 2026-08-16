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
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Artifact,
  ImpactPlan,
  ImpactRequest,
  ProjectDetail,
  StageDetail,
  StageName,
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
    artifacts: [evidence],
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
  };
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

describe("project review workspace", () => {
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

  it("contains focus in the mobile navigation drawer and restores it on Escape", async () => {
    renderWorkspace();
    const trigger = await screen.findByRole("button", {
      name: "打开项目与阶段导航",
    });
    fireEvent.click(trigger);

    const drawer = screen.getByRole("dialog", { name: "项目与阶段导航" });
    const shell = document.querySelector(".app-shell");
    const close = screen.getByRole("button", { name: "关闭项目与阶段导航" });
    expect(shell).toHaveAttribute("inert");
    expect(shell).toHaveAttribute("aria-hidden", "true");
    expect(close).toHaveFocus();

    close.focus();
    await userEvent.tab({ shift: true });
    expect(drawer).toContainElement(document.activeElement as HTMLElement);
    await userEvent.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "项目与阶段导航" })).not.toBeInTheDocument();
    expect(shell).not.toHaveAttribute("inert");
    expect(trigger).toHaveFocus();
  });

  it("keeps drawer containment stable across parent rerenders", async () => {
    const api = workspaceApi();
    const view = renderWorkspace(api);
    const trigger = await screen.findByRole("button", {
      name: "打开项目与阶段导航",
    });
    fireEvent.click(trigger);
    const close = screen.getByRole("button", { name: "关闭项目与阶段导航" });
    const focusSpy = vi.spyOn(trigger, "focus");
    focusSpy.mockClear();

    view.rerender(
      <WorkspaceTestTree
        api={api}
        route="/projects/episode_01/stages/storyboard"
      />,
    );

    expect(document.querySelector(".app-shell")).toHaveAttribute("inert");
    expect(close).toHaveFocus();
    expect(focusSpy).not.toHaveBeenCalled();
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
