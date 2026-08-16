import { expect, test, type Page } from "playwright/test";

const stageNames = [
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

const videoArtifacts = [
  {
    artifact_id: "art_video_a",
    name: "shot_03-candidate-1.mp4",
    media_type: "video/mp4",
    media_url: "/api/media/art_video_a",
    kind: "video",
    viewer: {
      fps: 25,
      width: 1080,
      height: 1920,
      shot_id: "shot_03",
      dialogues: [
        {
          dialogue_id: "shot_03:0",
          speaker: "旁白",
          start_seconds: 0.5,
          end_seconds: 1.2,
        },
      ],
    },
  },
  {
    artifact_id: "art_video_b",
    name: "shot_03-candidate-2.mp4",
    media_type: "video/mp4",
    media_url: "/api/media/art_video_b",
    kind: "video",
    viewer: { fps: 30, width: 1920, height: 1080, shot_id: "shot_03" },
  },
];

const selectedStage = {
  stage: "video",
  execution_state: "passed",
  review_state: "awaiting_review",
  review_policy: "manual",
  review_blocks_progress: true,
  revision: 2,
  executor: "generic.video",
  blocked_reasons: [],
  error: "",
  artifacts: videoArtifacts,
};

const project = {
  project_id: "episode_01",
  title: "旧城来信 · 第 01 集",
  mode: "original",
  target: { fps: 25, resolution: "1080x1920" },
  next_stage: "video",
  required_action: "approve_review_evidence",
  stages: stageNames.map((stage) => stage === "video" ? selectedStage : {
    ...selectedStage,
    stage,
    execution_state: "pending",
    review_state: "not_ready",
    review_blocks_progress: false,
    revision: 0,
    artifacts: [],
  }),
  final_outputs: [],
  eval_reports: [],
};

async function interceptWorkbench(page: Page) {
  let generationCalls = 0;
  let persistedJob = false;
  let stageResponse = selectedStage;
  const generationRequest = {
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
  };
  const job = {
    job_id: "1".repeat(32),
    project_id: "episode_01",
    operation: "video_generate",
    status: "completed",
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:01:00Z",
    provider_tasks: { shot_03: { status: "completed" }, shot_04: { status: "completed" } },
    result: { total_shots: 2 },
    error: "",
    resume_count: 0,
    last_event_sequence: 4,
  };
  await page.addInitScript(() => {
    const currentTimes = new WeakMap<HTMLMediaElement, number>();
    Object.defineProperty(HTMLMediaElement.prototype, "currentTime", {
      configurable: true,
      get() { return currentTimes.get(this) ?? 3.125; },
      set(value: number) {
        currentTimes.set(this, value);
        this.dispatchEvent(new Event("timeupdate"));
      },
    });
    Object.defineProperty(HTMLMediaElement.prototype, "duration", {
      configurable: true,
      get() { return 10; },
    });
    HTMLMediaElement.prototype.play = function play() {
      this.dispatchEvent(new Event("play"));
      return Promise.resolve();
    };
    HTMLMediaElement.prototype.pause = function pause() {
      this.dispatchEvent(new Event("pause"));
    };
  });
  await page.route(/\/api\/projects\/episode_01\/stages\/video$/, (route) =>
    route.fulfill({ json: stageResponse }),
  );
  await page.route(/\/api\/projects\/episode_01\/video\/workspace$/, (route) =>
    route.fulfill({ json: {
      schema_version: "motion-comic-factory.video-workspace.v1",
      project_id: "episode_01",
      shots: [
        { shot_id: "shot_03", duration_seconds: 5 },
        { shot_id: "shot_04", duration_seconds: 4 },
      ],
      selected_shot_ids: ["shot_03", "shot_04"],
      job: persistedJob ? job : null,
      failed_job_recovery: null,
    } }),
  );
  await page.route(/\/api\/projects\/episode_01\/video\/preflight$/, (route) =>
    route.fulfill({ json: { ...generationRequest, ready: true, blockers: [] } }),
  );
  await page.route(/\/api\/projects\/episode_01\/video\/confirm$/, (route) =>
    route.fulfill({ json: { generation_token: "browser-memory-token", generation_request: generationRequest } }),
  );
  await page.route(/\/api\/projects\/episode_01\/video\/generate$/, async (route) => {
    generationCalls += 1;
    persistedJob = true;
    await route.fulfill({ status: 202, json: { job_id: job.job_id, status: "queued" } });
  });
  await page.route(new RegExp(`/api/jobs/${job.job_id}$`), (route) => route.fulfill({ json: job }));
  await page.route(/\/api\/projects\/episode_01\/stages\/video\/request-changes$/, async (route) => {
    stageResponse = { ...selectedStage, review_state: "changes_requested" } as typeof selectedStage;
    await route.fulfill({ json: stageResponse });
  });
  await page.route(/\/api\/projects\/episode_01$/, (route) =>
    route.fulfill({ json: project }),
  );
  await page.route(/\/api\/media\/art_video_[ab]$/, (route) =>
    route.fulfill({ status: 200, contentType: "video/mp4", body: "" }),
  );
  return { generationCalls: () => generationCalls };
}

test("video inspection controls remain usable and unobstructed", async ({ page }) => {
  const intercepted = await interceptWorkbench(page);
  await page.goto("/projects/episode_01/stages/video");

  const video = page.getByTestId("stage-video");
  const toolbar = page.getByRole("toolbar", { name: "视频检查控制" });
  await expect(video).toBeVisible();
  await expect(toolbar).toBeVisible();
  await expect(page.getByRole("combobox", { name: "候选视频" })).toHaveValue("art_video_a");

  const videoBox = await video.boundingBox();
  const toolbarBox = await toolbar.boundingBox();
  expect(videoBox?.width).toBeGreaterThan(0);
  expect(videoBox?.height).toBeGreaterThan(0);
  expect((toolbarBox?.y ?? 0)).toBeGreaterThanOrEqual((videoBox?.y ?? 0) + (videoBox?.height ?? 0));

  await page.getByRole("button", { name: "后退一帧" }).click();
  await expect.poll(() => video.evaluate((node) => (node as HTMLVideoElement).currentTime)).toBeCloseTo(3.085, 3);
  await page.getByRole("button", { name: "前进一帧" }).click();
  await expect.poll(() => video.evaluate((node) => (node as HTMLVideoElement).currentTime)).toBeCloseTo(3.125, 3);
  await page.getByRole("button", { name: "0.5 倍速" }).click();
  await expect.poll(() => video.evaluate((node) => (node as HTMLVideoElement).playbackRate)).toBe(0.5);
  await page.getByRole("button", { name: "静音" }).click();
  await expect.poll(() => video.evaluate((node) => (node as HTMLVideoElement).muted)).toBe(true);

  await page.getByRole("combobox", { name: "候选视频" }).selectOption("art_video_b");
  await expect(video).toHaveAttribute("src", "/api/media/art_video_b");
  await expect(page.getByRole("checkbox", { name: "仅播放台词时段" })).toHaveCount(0);

  const overlaps = await toolbar.locator("button:visible, label:visible").evaluateAll((nodes) => {
    const boxes = nodes.map((node) => node.getBoundingClientRect());
    return boxes.some((left, index) => boxes.slice(index + 1).some((right) =>
      Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1 &&
      Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1,
    ));
  });
  expect(overlaps).toBe(false);

  await expect(page.getByText("视频生成预检")).toBeVisible();
  await page.getByRole("button", { name: "确认费用与输入" }).click();
  await page.getByRole("button", { name: "批量生成所选镜头" }).click();
  await expect(page.getByText("生成完成")).toBeVisible();
  expect(intercepted.generationCalls()).toBe(1);
  await expect(page.locator("body")).not.toContainText("browser-memory-token");

  await page.reload();
  await expect(page.getByText("生成完成")).toBeVisible();
  expect(intercepted.generationCalls()).toBe(1);

  const reloadedVideo = page.getByTestId("stage-video");
  await page.getByRole("button", { name: "退回修改" }).click();
  await page.getByRole("textbox", { name: "问题说明" }).fill("动作衔接错误。");
  await page.getByRole("button", { name: "在当前时间标记问题" }).click();
  await expect(page.getByRole("textbox", { name: "问题说明" })).toHaveValue(
    "动作衔接错误。\n\n--- 视频时间标记 ---\n镜头 shot_03\n候选成果 art_video_a\n时间码 3.125 秒\n--- 标记结束 ---",
  );
  await page.getByRole("button", { name: "退回整阶段" }).click();
  await expect(page.getByText("问题已提交到当前修订。")).toBeVisible();
  expect(await reloadedVideo.boundingBox()).not.toBeNull();

});
