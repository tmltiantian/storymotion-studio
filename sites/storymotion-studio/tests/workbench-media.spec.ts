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
    route.fulfill({ json: selectedStage }),
  );
  await page.route(/\/api\/projects\/episode_01$/, (route) =>
    route.fulfill({ json: project }),
  );
  await page.route(/\/api\/media\/art_video_[ab]$/, (route) =>
    route.fulfill({ status: 200, contentType: "video/mp4", body: "" }),
  );
}

test("video inspection controls remain usable and unobstructed", async ({ page }, testInfo) => {
  await interceptWorkbench(page);
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
  await page.getByRole("button", { name: "在当前时间标记问题" }).click();
  await expect(page.getByRole("alert")).toContainText("已在 3.125 秒标记 shot_03-candidate-1.mp4");

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

  if (testInfo.project.name === "mobile") {
    const artifact = page.getByRole("region", { name: "视频成果" });
    const review = page.getByRole("complementary", { name: "审核检查" });
    const artifactBox = await artifact.boundingBox();
    const reviewBox = await review.boundingBox();
    expect((reviewBox?.y ?? 0)).toBeGreaterThanOrEqual((artifactBox?.y ?? 0) + (artifactBox?.height ?? 0));
  }
});
