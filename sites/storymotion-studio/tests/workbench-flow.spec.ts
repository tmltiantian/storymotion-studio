import { expect, test, type Page, type Route } from "playwright/test";

const stages = [
  "concept",
  "script",
  "storyboard",
  "assets",
  "audio",
  "video",
  "edit",
  "eval",
  "deliver",
] as const;

type StageName = typeof stages[number];

function artifact(
  id: string,
  name: string,
  mediaType: string,
  kind: string,
  viewer: Record<string, unknown> = {},
) {
  return {
    artifact_id: id,
    name,
    media_type: mediaType,
    media_url: `/api/media/${id}`,
    download_url: `/api/download/${id}`,
    kind,
    viewer,
  };
}

function stage(name: StageName, overrides: Record<string, unknown> = {}) {
  return {
    stage: name,
    execution_state: "pending",
    review_state: "not_ready",
    review_policy: "manual",
    review_blocks_progress: false,
    revision: 0,
    executor: "",
    blocked_reasons: [],
    error: "",
    artifacts: [],
    ...overrides,
  };
}

function terminalJob(
  id: string,
  operation: string,
  status: "completed" | "failed" = "completed",
) {
  return {
    job_id: id,
    project_id: "rain-box",
    operation,
    status,
    created_at: "2026-08-16T08:00:00Z",
    updated_at: "2026-08-16T08:00:01Z",
    provider_tasks: {},
    result: operation === "run_stage" ? { completed_stages: ["concept"] } : { total_shots: 2 },
    error: status === "failed" ? "本地恢复夹具：作业已中断" : "",
    resume_count: 0,
    last_event_sequence: 4,
  };
}

const conceptArtifact = artifact(
  "art_concept_fixture",
  "concept.json",
  "application/json",
  "text",
);
const storyboardArtifact = artifact(
  "art_storyboard_fixture",
  "storyboard.svg",
  "image/svg+xml",
  "image",
  { width: 640, height: 360 },
);
const editArtifact = artifact(
  "art_edit_fixture",
  "edit.json",
  "application/json",
  "text",
);
const archiveArtifact = {
  ...artifact(
    "art_archive_fixture",
    "window.svg",
    "image/svg+xml",
    "image",
    { width: 128, height: 128 },
  ),
  rights: {
    origin: "旧展示站迁移",
    creator: "未核验",
    license: "未核验",
    commercial_use: "未核验",
    redistribution_status: "unverified",
    distribution_warning: "公开发布或再分发前需要完成人工权利审核。",
  },
};

const svg = `<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="#d44934"/><path d="M24 64h80" stroke="#fff" stroke-width="12"/></svg>`;

async function fulfill(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, json });
}

async function installOfflineContract(page: Page) {
  let created = false;
  let concept = stage("concept");
  let storyboard = stage("storyboard", {
    execution_state: "passed",
    review_state: "awaiting_review",
    review_blocks_progress: true,
    revision: 2,
    executor: "generic.storyboard",
    artifacts: [storyboardArtifact],
  });
  let edit = stage("edit", {
    execution_state: "passed",
    review_state: "awaiting_review",
    review_blocks_progress: true,
    revision: 3,
    executor: "generic.edit",
    artifacts: [editArtifact],
  });
  const video = stage("video", {
    execution_state: "passed",
    review_state: "awaiting_review",
    review_blocks_progress: true,
    revision: 4,
    executor: "generic.video",
    artifacts: [],
  });
  let videoJob: ReturnType<typeof terminalJob> | null = null;
  let recovery: { mode: string; reason: string } | null = null;
  let externalRequests = 0;
  let generationSubmissions = 0;
  let settingsFail = false;

  const project = () => ({
    project_id: "rain-box",
    title: "雨天纸箱",
    mode: "original",
    target: { fps: 25, resolution: "1080x1920" },
    next_stage: concept.review_state === "approved" ? "storyboard" : "concept",
    required_action: concept.review_state === "approved"
      ? "approve_review_evidence"
      : concept.execution_state === "pending"
        ? "run_or_resume"
        : "approve_review_evidence",
    stages: stages.map((name) => {
      if (name === "concept") return concept;
      if (name === "storyboard") return storyboard;
      if (name === "edit") return edit;
      if (name === "video") return video;
      return stage(name);
    }),
    final_outputs: [],
    eval_reports: [],
  });

  const videoRequest = {
    schema_version: "motion-comic-factory.video-generation-request.v1",
    project_id: "rain-box",
    project_sha256: "a".repeat(64),
    package_sha256: "b".repeat(64),
    revision_hashes: { storyboard: "c".repeat(64) },
    artifact_hashes: { art_storyboard_fixture: "d".repeat(64) },
    approval_hashes: { storyboard: "e".repeat(64) },
    repair_plan_sha256: "",
    shot_ids: ["shot_01", "shot_02"],
    shots: [
      { shot_id: "shot_01", duration: 4, resolution: "768P" },
      { shot_id: "shot_02", duration: 5, resolution: "768P" },
    ],
    provider: "offline-fixture",
    model: "deterministic-local",
    resolution: "768P",
    output_seconds: 9,
    estimated_cost_yuan: 4.5,
    price_yuan_per_second: 0.5,
  };

  const works = [{
    work_id: "historical-showcase",
    project_id: "historical-showcase",
    title: "历史归档",
    mode: "historical",
    source: "historical",
    delivered_at: "2026-08-16T00:00:00Z",
    delivery_date: "2026-08-16",
    roles: ["未知角色"],
    current_version: "旧展示站迁移",
  }];
  const work = {
    ...works[0],
    versions: [{
      version_id: "archive-v1",
      label: "旧展示站迁移",
      created_at: "2026-08-16T00:00:00Z",
      outputs: [archiveArtifact],
      eval_reports: [],
      iteration_summary: "七个旧公开文件已按哈希迁移。",
    }],
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4175") {
      externalRequests += 1;
      await route.abort("blockedbyclient");
      return;
    }
    if (!url.pathname.startsWith("/api/")) {
      await route.continue();
      return;
    }

    const path = url.pathname;
    const method = request.method();
    if (path === "/api/projects" && method === "GET") {
      await fulfill(route, created ? [project()] : []);
    } else if (path === "/api/projects" && method === "POST") {
      const body = request.postDataJSON();
      expect(body).toMatchObject({
        project_id: "rain-box",
        title: "雨天纸箱",
        mode: "original",
        approval_preset: "strict",
      });
      created = true;
      await fulfill(route, { job_id: "1".repeat(32), status: "queued" }, 202);
    } else if (path === "/api/projects/missing") {
      await fulfill(route, { error: { code: "not_found", message: "not found" } }, 404);
    } else if (path === "/api/projects/rain-box" && method === "GET") {
      await fulfill(route, project());
    } else if (path.startsWith("/api/projects/rain-box/stages/") && method === "GET") {
      const name = path.split("/").at(-1) as StageName;
      const selected = name === "concept" ? concept : name === "storyboard" ? storyboard : name === "edit" ? edit : name === "video" ? video : stage(name);
      await fulfill(route, selected);
    } else if (path === "/api/projects/rain-box/stages/concept/run") {
      concept = stage("concept", {
        execution_state: "passed",
        review_state: "awaiting_review",
        review_blocks_progress: true,
        revision: 1,
        executor: "generic.concept",
        artifacts: [conceptArtifact],
      });
      await fulfill(route, { job_id: "2".repeat(32), status: "queued" }, 202);
    } else if (path === "/api/projects/rain-box/stages/concept/approve") {
      concept = { ...concept, review_state: "approved", review_blocks_progress: false };
      await fulfill(route, concept);
    } else if (path === "/api/projects/rain-box/stages/storyboard/request-changes") {
      storyboard = { ...storyboard, review_state: "changes_requested" };
      await fulfill(route, storyboard);
    } else if (path === "/api/projects/rain-box/impact-plan" && method === "POST") {
      await fulfill(route, {
        schema_version: "motion-comic-factory.impact-plan.v2",
        plan_id: "f".repeat(64),
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
          reused_video_shot_count: 2,
          regenerated_audio_item_count: 0,
          affected_stages: ["edit", "eval", "deliver"],
          estimate: { available: false },
        },
        preserved_artifacts: ["art_video_a", "art_video_b"],
        package_sha256: "1".repeat(64),
        episode_sha256: "2".repeat(64),
      });
    } else if (path === `/api/projects/rain-box/impact-plan/${"f".repeat(64)}/apply`) {
      edit = { ...edit, execution_state: "stale", review_state: "not_ready", review_blocks_progress: false };
      await fulfill(route, project());
    } else if (path === "/api/projects/rain-box/video/workspace") {
      await fulfill(route, {
        schema_version: "motion-comic-factory.video-workspace.v1",
        project_id: "rain-box",
        shots: [
          { shot_id: "shot_01", duration_seconds: 4 },
          { shot_id: "shot_02", duration_seconds: 5 },
        ],
        selected_shot_ids: ["shot_01", "shot_02"],
        job: videoJob,
        failed_job_recovery: recovery,
      });
    } else if (path === "/api/projects/rain-box/video/preflight") {
      await fulfill(route, { ...videoRequest, ready: true, blockers: [] });
    } else if (path === "/api/projects/rain-box/video/confirm") {
      await fulfill(route, {
        generation_token: "offline-memory-token",
        generation_request: videoRequest,
      });
    } else if (path === "/api/projects/rain-box/video/test") {
      generationSubmissions += 1;
      videoJob = terminalJob("3".repeat(32), "video_test");
      recovery = null;
      await fulfill(route, { job_id: videoJob.job_id, status: "queued" }, 202);
    } else if (/^\/api\/jobs\/[0-9a-f]{32}$/.test(path) && method === "GET") {
      const id = path.split("/").at(-1) ?? "";
      await fulfill(route, id === "2".repeat(32) ? terminalJob(id, "run_stage") : videoJob ?? terminalJob(id, "video_test"));
    } else if (/^\/api\/jobs\/[0-9a-f]{32}\/resume$/.test(path)) {
      const id = path.split("/").at(-2) ?? "";
      videoJob = terminalJob(id, "video_test");
      recovery = null;
      await fulfill(route, videoJob, 202);
    } else if (path === "/api/works") {
      await fulfill(route, works);
    } else if (path === "/api/works/historical-showcase") {
      await fulfill(route, work);
    } else if (path === "/api/settings/providers") {
      if (settingsFail) {
        await fulfill(route, { error: { code: "internal_error", message: "failed" } }, 500);
      } else {
        await fulfill(route, {
          capabilities: {
            text: { provider: "local", model: "deterministic", ready: true, credential_present: false, blockers: [] },
            video: { provider: "offline", model: "none", ready: false, credential_present: false, blockers: ["未配置"] },
          },
          defaults: {
            voice_mapping: [],
            output: { aspect_ratio: "9:16", resolution: "1080x1920", fps: 25, target_duration_seconds: 60 },
            generation: { concurrency: 1, fee_cap_yuan: null },
          },
        });
      }
    } else if (path === "/api/media/art_archive_fixture") {
      await route.fulfill({ status: 200, contentType: "image/svg+xml", body: svg });
    } else if (path === "/api/download/art_archive_fixture") {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        headers: { "Content-Disposition": "attachment; filename=window.svg" },
        body: svg,
      });
    } else if (path.startsWith("/api/media/")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: '{"fixture":true}' });
    } else {
      await fulfill(route, { error: { code: "not_found", message: "not found" } }, 404);
    }
  });

  return {
    externalRequests: () => externalRequests,
    generationSubmissions: () => generationSubmissions,
    failSettings: () => { settingsFail = true; },
    installFailedRecovery: () => {
      videoJob = terminalJob("4".repeat(32), "video_test", "failed");
      recovery = { mode: "poll_only", reason: "provider_task_id_available" };
    },
  };
}

test("creates, runs, reviews, repairs, and recovers an offline project", async ({ page }) => {
  const fixture = await installOfflineContract(page);
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    if (!request.failure()?.errorText.includes("ERR_ABORTED")) {
      failedRequests.push(`${request.url()} ${request.failure()?.errorText ?? "unknown"}`);
    }
  });

  await page.goto("/projects");
  await expect(page.getByText("还没有制作项目")).toBeVisible();
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByLabel("项目 ID").fill("rain-box");
  await page.getByLabel("项目标题").fill("雨天纸箱");
  await page.getByLabel("创作构想").fill("雨夜里，一只猫把纸箱改造成移动小剧场。");
  await page.getByLabel("审批模板").selectOption("strict");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page.getByText("项目已进入创建队列")).toBeVisible();
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: "刷新项目" }).click();
  await page.getByRole("link", { name: "继续概念" }).click();

  await page.getByRole("button", { name: "运行概念阶段" }).click();
  await expect(page.getByText("阶段运行完成，已载入当前修订。")).toBeVisible();
  await page.getByLabel("确认说明").fill("概念方向清楚，按严格模板确认。 ");
  await page.getByRole("button", { name: "确认通过" }).click();
  await expect(page.getByRole("complementary", { name: "审核检查" }).getByText("已确认", { exact: true })).toBeVisible();

  await page.goto("/projects/rain-box/stages/storyboard");
  await page.getByRole("button", { name: "退回修改" }).click();
  await page.getByRole("radio", { name: "整体成果需调整" }).check();
  await page.getByLabel("问题说明").fill("第二镜头动作衔接需要更清楚。 ");
  await page.getByRole("button", { name: "退回整阶段" }).click();
  await expect(page.getByRole("complementary", { name: "审核检查" }).getByText("已退回修改", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("complementary", { name: "审核检查" }).getByText("已退回修改", { exact: true })).toBeVisible();

  await page.goto("/projects/rain-box/stages/edit");
  await page.getByRole("button", { name: "退回修改" }).click();
  await page.getByRole("radio", { name: "字幕样式有误" }).check();
  await page.getByLabel("问题说明").fill("字幕安全区需要上移。 ");
  await page.getByRole("button", { name: "查看影响" }).click();
  await expect(page.getByRole("dialog", { name: "修改影响预览" })).toContainText("其他 2 个镜头继续复用");
  await page.getByRole("button", { name: "应用返修计划" }).click();
  await expect(page.getByRole("button", { name: "重新运行剪辑阶段" })).toBeVisible();

  await page.goto("/projects/rain-box/stages/video");
  await expect(page.getByText("offline-fixture")).toBeVisible();
  await expect(page.getByRole("button", { name: "试生成所选镜头" })).toBeDisabled();
  await page.getByRole("button", { name: "确认费用与输入" }).click();
  await page.getByRole("button", { name: "试生成所选镜头" }).click();
  await expect(page.getByText("生成完成")).toBeVisible();
  expect(fixture.generationSubmissions()).toBe(1);
  await page.reload();
  await expect(page.getByText("生成完成")).toBeVisible();
  expect(fixture.generationSubmissions()).toBe(1);

  fixture.installFailedRecovery();
  await page.reload();
  await page.getByRole("button", { name: "恢复生成" }).click();
  await expect(page.getByText("生成完成")).toBeVisible();

  expect(fixture.externalRequests()).toBe(0);
  expect(consoleErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});

test("filters and downloads the rights-warned historical archive", async ({ page }) => {
  const fixture = await installOfflineContract(page);
  await page.goto("/works");
  await expect(page.getByRole("heading", { name: "历史归档" })).toBeVisible();
  await page.getByLabel("角色").selectOption("未知角色");
  await expect(page.getByText("1 个结果")).toBeVisible();
  await page.getByRole("link", { name: "查看作品" }).click();
  await expect(page.getByRole("alert")).toContainText("发布权利尚未核验");
  const image = page.getByRole("img", { name: "window.svg" });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node) => ({
    width: (node as HTMLImageElement).naturalWidth,
    height: (node as HTMLImageElement).naturalHeight,
  }))).toEqual({ width: 128, height: 128 });
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 window.svg" }).click();
  expect((await download).suggestedFilename()).toBe("window.svg");
  expect(fixture.externalRequests()).toBe(0);
});

test("shows production settings and safe not-found and service-error states", async ({ page }) => {
  const fixture = await installOfflineContract(page);
  await page.goto("/settings");
  await expect(page.getByText("本地运行")).toBeVisible();
  await expect(page.getByText("凭据缺失")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("api_key");

  await page.goto("/projects/missing");
  await expect(page.getByRole("alert")).toContainText("无法读取项目工作区");

  fixture.failSettings();
  await page.goto("/settings");
  await expect(page.getByRole("alert")).toContainText("无法读取制作设置");
  expect(fixture.externalRequests()).toBe(0);
});
