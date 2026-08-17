import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "playwright/test";

const E2E_ROOT = "/tmp/storymotion-studio-playwright-e2e";
const stageLabels = {
  concept: "概念", script: "剧本", storyboard: "分镜", assets: "资产",
  audio: "音频", video: "视频", edit: "剪辑", eval: "质检", deliver: "交付",
} as const;
type StageName = keyof typeof stageLabels;

test("uses the desktop release QA viewport", async ({ page }) => {
  expect(page.viewportSize()).toEqual({ width: 1440, height: 900 });
});

async function installNetworkGuard(page: Page) {
  const externalRequests: string[] = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "127.0.0.1" || url.hostname === "localhost") {
      await route.continue();
      return;
    }
    externalRequests.push(`${route.request().method()} ${url.origin}${url.pathname}`);
    await route.abort("blockedbyclient");
  });
  return externalRequests;
}

function monitorBrowser(page: Page) {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "unknown";
    if (!failure.includes("ERR_ABORTED")) failedRequests.push(`${request.url()} ${failure}`);
  });
  return { consoleErrors, failedRequests };
}

async function expectNoHorizontalOverflow(page: Page) {
  const layout = await page.evaluate(() => ({
    body: document.body.scrollWidth - document.body.clientWidth,
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    clipped: [...document.querySelectorAll<HTMLElement>("button, a, input, select, textarea")]
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && (rect.left < -1 || rect.right > window.innerWidth + 1);
      })
      .map((element) => element.getAttribute("aria-label") || element.textContent?.trim() || element.tagName),
  }));
  expect(layout.body).toBeLessThanOrEqual(1);
  expect(layout.document).toBeLessThanOrEqual(1);
  expect(layout.clipped).toEqual([]);
}

async function captureStageScreenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`) });
}

async function waitForJob(page: Page, jobId: string, status = "completed") {
  await expect.poll(async () => {
    const response = await page.request.get(`/api/jobs/${jobId}`);
    if (!response.ok()) return `http-${response.status()}`;
    return (await response.json() as { status: string }).status;
  }, { timeout: 30_000 }).toBe(status);
}

async function runStage(page: Page, projectId: string, stage: StageName, reload = false) {
  await page.goto(`/projects/${projectId}/stages/${stage}`);
  const label = stageLabels[stage];
  const button = page.getByRole("button", { name: new RegExp(`运行${label}阶段$`) });
  await expect(button).toBeEnabled();
  await button.click();
  if (reload) {
    await page.reload();
  } else {
    await expect(page.getByText("阶段运行完成，已载入当前修订。")).toBeVisible({ timeout: 30_000 });
  }
  await expect(page.getByRole("complementary", { name: "审核检查" }).getByText("等待确认", { exact: true })).toBeVisible();
}

async function approveStage(page: Page, stage: StageName) {
  const review = page.getByRole("complementary", { name: "审核检查" });
  await review.getByLabel("确认说明").fill(`${stageLabels[stage]}阶段本地验收通过。`);
  await review.getByRole("button", { name: "确认通过" }).click();
  await expect(review.getByText("已确认", { exact: true })).toBeVisible();
}

test("runs the real offline production, review, repair, and recovery flow", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const externalRequests = await installNetworkGuard(page);
  const browser = monitorBrowser(page);
  const projectId = `e2e-${testInfo.project.name}`;
  const projectDir = path.join(E2E_ROOT, "runs", projectId);

  await page.goto("/projects");
  await page.getByRole("button", { name: "新建项目" }).click();
  await page.getByLabel("项目 ID").fill(projectId);
  await page.getByLabel("项目标题").fill(`离线制作 ${testInfo.project.name}`);
  await page.getByLabel("创作构想").fill("雨夜里，两位纸偶演员把纸箱改造成移动小剧场。");
  await page.getByLabel("审批模板").selectOption("strict");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page.getByText("项目已进入创建队列")).toBeVisible();
  const creationJobId = (await page.getByRole("dialog").locator("code").textContent())?.trim() ?? "";
  expect(creationJobId).toMatch(/^[0-9a-f]{32}$/);
  await waitForJob(page, creationJobId);
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: "刷新项目" }).click();
  await expect(page.getByText(projectId, { exact: true })).toBeVisible();

  await runStage(page, projectId, "concept", true);
  const conceptReview = page.getByRole("complementary", { name: "审核检查" });
  await conceptReview.getByRole("button", { name: "退回修改" }).click();
  await conceptReview.getByRole("radio", { name: "整体成果需调整" }).check();
  await conceptReview.getByLabel("问题说明").fill("需要加强纸箱剧场的视觉动机。");
  await conceptReview.getByRole("button", { name: "退回整阶段" }).click();
  await expect(conceptReview.getByText("已退回修改", { exact: true })).toBeVisible();
  await page.reload();
  await expect(conceptReview.getByText("已退回修改", { exact: true })).toBeVisible();
  await runStage(page, projectId, "concept");
  await captureStageScreenshot(page, testInfo, "concept-stage-result");
  await approveStage(page, "concept");

  await runStage(page, projectId, "script");
  await expect(page.getByRole("heading", { name: /剧本成果/ })).toBeVisible();
  await expect(page.getByText("主角A").first()).toBeVisible();
  await expect(page.getByText("e2e-desktop", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Review is required after generic.script.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("REVIEW", { exact: true })).toHaveCount(0);
  await expect(page.getByText("阶段审核", { exact: true })).toBeVisible();
  await expect(page.getByText("请确认当前阶段成果后继续制作。", { exact: true })).toBeVisible();
  await expect(page.getByText(/anime motion comic|medium shot, slow push-in/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "主角A（主角A）" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "第 1 镜 · 第 1 镜" })).toHaveCount(0);
  await expect(page.locator("pre, code")).toHaveCount(0);
  await expect(page.getByText(/schema_version|application\/json|manifest\.json/)).toHaveCount(0);
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
  await captureStageScreenshot(page, testInfo, "script-stage-result");
  await approveStage(page, "script");

  for (const stage of ["storyboard", "assets", "audio"] as const) {
    await runStage(page, projectId, stage);
    if (stage === "storyboard") {
      await captureStageScreenshot(page, testInfo, "storyboard-stage-result");
    }
    await approveStage(page, stage);
  }

  await page.goto(`/projects/${projectId}/stages/video`);
  await expect(page.getByText("MiniMax-H3")).toBeVisible();
  const shots = page.getByRole("group", { name: "生成镜头" }).getByRole("checkbox");
  await expect(shots).toHaveCount(6);
  for (let index = 3; index < 6; index += 1) await shots.nth(index).uncheck();
  await expect(page.getByRole("button", { name: "试生成所选镜头" })).toBeDisabled();
  await page.getByRole("button", { name: "确认费用与输入" }).click();
  await page.getByRole("button", { name: "试生成所选镜头" }).click();
  await expect(page.getByText("生成失败")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "恢复生成" })).toBeVisible();

  const failedResponse = await page.request.get(`/api/projects/${projectId}/video/workspace`);
  const failed = await failedResponse.json() as {
    job: { job_id: string; status: string; provider_tasks: Record<string, unknown> };
    failed_job_recovery: { mode: string };
  };
  expect(failedResponse.ok()).toBe(true);
  expect(failed.job.status).toBe("failed");
  expect(Object.keys(failed.job.provider_tasks)).toHaveLength(3);
  expect(failed.failed_job_recovery.mode).toBe("poll_only");
  await page.getByRole("button", { name: "恢复生成" }).click();
  await expect(page.getByText("生成完成")).toBeVisible({ timeout: 30_000 });
  const recovered = await (await page.request.get(`/api/jobs/${failed.job.job_id}`)).json() as {
    status: string; resume_count: number;
  };
  expect(recovered).toMatchObject({ status: "completed", resume_count: 1 });
  const offlineRender = JSON.parse(
    await readFile(path.join(projectDir, "stages/video/offline-render.json"), "utf-8"),
  ) as { project_id: string; shot_ids: string[] };
  expect(offlineRender.project_id).toBe(projectId);
  expect(offlineRender.shot_ids).toHaveLength(3);

  await runStage(page, projectId, "video");
  await approveStage(page, "video");
  await runStage(page, projectId, "edit");
  const editReview = page.getByRole("complementary", { name: "审核检查" });
  await editReview.getByRole("button", { name: "退回修改" }).click();
  await editReview.getByRole("radio", { name: "字幕样式有误" }).check();
  await editReview.getByLabel("问题说明").fill("字幕安全区需要上移。");
  await editReview.getByRole("button", { name: "查看影响" }).click();
  const impact = page.getByRole("dialog", { name: "修改影响预览" });
  await expect(impact).toContainText("其他 6 个镜头继续复用");
  await impact.getByRole("button", { name: "应用返修计划" }).click();
  await expect(page.getByRole("button", { name: "重新运行剪辑阶段" })).toBeVisible();
  expect((await readdir(path.join(projectDir, "impact_plans"))).some((name) => name.endsWith(".json"))).toBe(true);
  await runStage(page, projectId, "edit");
  await approveStage(page, "edit");
  await runStage(page, projectId, "eval");
  await captureStageScreenshot(page, testInfo, "eval-stage-result");
  await approveStage(page, "eval");
  await runStage(page, projectId, "deliver");
  await captureStageScreenshot(page, testInfo, "delivery-stage-result");
  await approveStage(page, "deliver");

  await stat(path.join(projectDir, "production_package.json"));
  expect(await readdir(path.join(projectDir, "reviews"))).toEqual(expect.arrayContaining([
    "concept.review.json", "script.review.json", "storyboard.review.json",
    "assets.review.json", "audio.review.json", "video.review.json",
    "edit.review.json", "eval.review.json", "deliver.review.json",
  ]));
  const jobsDir = path.join(E2E_ROOT, "runs/.workbench/jobs");
  const jobFiles = (await readdir(jobsDir)).filter((name) => name.endsWith(".json"));
  const projectJobs = (await Promise.all(jobFiles.map(async (name) => JSON.parse(
    await readFile(path.join(jobsDir, name), "utf-8"),
  ) as { project_id: string; operation: string }))).filter((job) => job.project_id === projectId);
  expect(projectJobs.filter((job) => job.operation === "run_stage").length).toBeGreaterThanOrEqual(10);
  expect(projectJobs.some((job) => job.operation === "video_test")).toBe(true);
  await expect(page.getByText(/正在读取/)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  expect(externalRequests).toEqual([]);
  expect(browser.consoleErrors).toEqual([]);
  expect(browser.failedRequests).toEqual([]);
});

test("reads real archive media with rights warnings, ranges, and downloads", async ({ page }) => {
  const externalRequests = await installNetworkGuard(page);
  const browser = monitorBrowser(page);
  await page.goto("/works");
  await page.getByLabel("角色").selectOption("未知角色");
  await expect(page.getByText(/\d+ 个结果/)).toBeVisible();
  const historical = page.getByRole("article").filter({ hasText: "历史归档" });
  await historical.getByRole("link", { name: "查看作品" }).click();
  await expect(page.getByRole("alert")).toContainText("发布权利尚未核验");
  await page.getByLabel("作品版本").selectOption({ label: "window.svg" });
  const image = page.getByRole("img", { name: "window.svg" });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node) => ({
    width: (node as HTMLImageElement).naturalWidth,
    height: (node as HTMLImageElement).naturalHeight,
  }))).toEqual({ width: 150, height: 150 });

  const works = await (await page.request.get("/api/works")).json() as Array<{ work_id: string; title: string }>;
  const archive = works.find((work) => work.title === "历史归档");
  expect(archive).toBeDefined();
  const detail = await (await page.request.get(`/api/works/${archive?.work_id}`)).json() as {
    versions: Array<{ outputs: Array<{ name: string; media_url: string; download_url: string }> }>;
  };
  const artifact = detail.versions.flatMap((version) => version.outputs)
    .find((output) => output.name === "window.svg");
  expect(artifact).toBeDefined();
  const range = await page.request.get(artifact?.media_url ?? "", { headers: { Range: "bytes=0-15" } });
  expect(range.status()).toBe(206);
  expect((await range.body()).length).toBe(16);
  expect(range.headers()["content-range"]).toMatch(/^bytes 0-15\//);
  const downloadResponse = await page.request.get(artifact?.download_url ?? "");
  expect(downloadResponse.ok()).toBe(true);
  expect(downloadResponse.headers()["content-disposition"]).toContain("window.svg");
  const browserDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载 window.svg" }).click();
  expect((await browserDownload).suggestedFilename()).toBe("window.svg");
  await expectNoHorizontalOverflow(page);
  expect(externalRequests).toEqual([]);
  expect(browser.consoleErrors).toEqual([]);
  expect(browser.failedRequests).toEqual([]);
});

test("shows real provider settings plus safe empty and not-found states", async ({ page }) => {
  const externalRequests = await installNetworkGuard(page);
  const browser = monitorBrowser(page);
  await page.goto("/settings");
  await expect(page.getByText("本地运行").first()).toBeVisible();
  await expect(page.getByText("凭据已配置")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/api[_-]?key|FICTIONAL_E2E_SECRET/i);
  await page.goto("/works");
  await page.getByLabel("筛选作品").fill("不存在的离线作品");
  await expect(page.getByText("没有匹配的作品")).toBeVisible();
  await page.goto("/projects/definitely-missing");
  await expect(page.getByRole("alert")).toContainText("无法读取项目工作区");
  expect(browser.consoleErrors).toEqual([
    expect.stringContaining("404 (Not Found)"),
  ]);
  browser.consoleErrors.length = 0;
  const invalid = await page.request.get("/api/jobs/not-a-job");
  expect(invalid.status()).toBe(400);
  expect(await invalid.json()).toEqual({ error: { code: "invalid_request", message: "Identifier is invalid" } });
  await expectNoHorizontalOverflow(page);
  expect(externalRequests).toEqual([]);
  expect(browser.consoleErrors).toEqual([]);
  expect(browser.failedRequests).toEqual([]);
});
