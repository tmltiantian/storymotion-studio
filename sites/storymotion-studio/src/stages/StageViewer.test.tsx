import "@testing-library/jest-dom/vitest";

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Artifact } from "../api/types";
import { StageViewer } from "./StageViewer";

const audioArtifacts = [
  {
    artifact_id: "art_audio_main",
    name: "voiceover.m4a",
    media_type: "audio/mp4",
    media_url: "/api/media/art_audio_main",
    kind: "audio",
    viewer: {
      dialogues: [
        {
          dialogue_id: "shot_02:0",
          speaker: "黑白猫",
          start_seconds: 4.2,
          end_seconds: 6.1,
        },
      ],
    },
  },
  {
    artifact_id: "art_audio_alt",
    name: "alternate.wav",
    media_type: "audio/wav",
    media_url: "/api/media/art_audio_alt",
    kind: "audio",
  },
] as Artifact[];

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
] as Artifact[];

const multipleShotVideos = [
  ...videoArtifacts,
  {
    artifact_id: "art_video_shot_04",
    name: "shot_04.mp4",
    media_type: "video/mp4",
    media_url: "/api/media/art_video_shot_04",
    kind: "video",
    viewer: { fps: 25, width: 1080, height: 1920, shot_id: "shot_04" },
  },
  {
    artifact_id: "art_video_ungrouped_a",
    name: "assembly-a.mp4",
    media_type: "video/mp4",
    media_url: "/api/media/art_video_ungrouped_a",
    kind: "video",
  },
  {
    artifact_id: "art_video_ungrouped_b",
    name: "assembly-b.mp4",
    media_type: "video/mp4",
    media_url: "/api/media/art_video_ungrouped_b",
    kind: "video",
  },
] as Artifact[];

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("StageViewer registry", () => {
  it("renders registered dialogue timing and keeps only one audio playing", async () => {
    const user = userEvent.setup();
    render(<StageViewer stage="audio" artifacts={audioArtifacts} />);

    expect(screen.getByRole("button", { name: "播放黑白猫台词" })).toBeVisible();
    expect(screen.getByText("00:04.20–00:06.10")).toBeVisible();

    const audio = screen.getAllByTestId("stage-audio") as HTMLAudioElement[];
    await user.click(screen.getByRole("button", { name: "播放黑白猫台词" }));
    expect(audio[0]).toHaveProperty("currentTime", 4.2);
    expect(audio[0].play).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "播放 alternate.wav" }));
    expect(audio[0].pause).toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
  });

  it("does not invent dialogue controls when no registered timing exists", () => {
    render(<StageViewer stage="audio" artifacts={[audioArtifacts[1]]} />);

    expect(screen.queryByText(/00:/)).not.toBeInTheDocument();
    expect(screen.getByTestId("stage-audio")).toHaveAttribute("controls");
    expect(screen.getByText("未提供台词时间信息")).toBeVisible();
  });

  it("selects video candidates and exposes stable non-overlay controls", async () => {
    const user = userEvent.setup();
    const onIssueAtTime = vi.fn();
    render(
      <StageViewer
        stage="video"
        artifacts={videoArtifacts}
        onIssueAtTime={onIssueAtTime}
      />,
    );

    const video = screen.getByTestId("stage-video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { configurable: true, value: 10 });
    video.currentTime = 3.125;

    await user.click(screen.getByRole("button", { name: "后退一帧" }));
    expect(video.currentTime).toBeCloseTo(3.085);
    await user.click(screen.getByRole("button", { name: "前进一帧" }));
    expect(video.currentTime).toBeCloseTo(3.125);

    await user.click(screen.getByRole("button", { name: "0.5 倍速" }));
    expect(video.playbackRate).toBe(0.5);
    await user.click(screen.getByRole("button", { name: "静音" }));
    expect(video.muted).toBe(true);
    await user.click(screen.getByRole("button", { name: "在当前时间标记问题" }));
    expect(onIssueAtTime).toHaveBeenCalledWith(3.125, videoArtifacts[0]);

    const controls = screen.getByRole("toolbar", { name: "视频检查控制" });
    expect(controls.compareDocumentPosition(video) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    await user.selectOptions(screen.getByRole("combobox", { name: "候选视频" }), "art_video_b");
    expect(video).toHaveAttribute("src", "/api/media/art_video_b");
    expect(screen.queryByRole("checkbox", { name: "仅播放台词时段" })).not.toBeInTheDocument();
  });

  it("groups candidates only within an authoritative shot and keeps other videos independent", () => {
    render(<StageViewer stage="video" artifacts={multipleShotVideos} />);

    expect(screen.getAllByTestId("stage-video")).toHaveLength(4);
    expect(screen.getAllByRole("combobox", { name: "候选视频" })).toHaveLength(1);
    expect(screen.getByRole("option", { name: "shot_03-candidate-1.mp4" })).toBeVisible();
    expect(screen.getByRole("option", { name: "shot_03-candidate-2.mp4" })).toBeVisible();
    expect(screen.queryByRole("option", { name: "shot_04.mp4" })).not.toBeInTheDocument();
  });

  it("uses only registered media IDs and safely falls back for unsupported files", () => {
    const unsupported = {
      artifact_id: "art_archive",
      name: "source.bin",
      media_type: "application/octet-stream",
      media_url: "/api/media/art_archive",
    } as Artifact;
    const unsafe = {
      artifact_id: "art_private",
      name: "private.bin",
      media_type: "application/octet-stream",
      media_url: "/Users/person/private.bin",
    } as Artifact;

    render(<StageViewer stage="assets" artifacts={[unsupported, unsafe]} />);

    expect(screen.getByRole("link", { name: "打开 source.bin" })).toHaveAttribute(
      "href",
      "/api/media/art_archive",
    );
    expect(screen.queryByRole("link", { name: "打开 private.bin" })).not.toBeInTheDocument();
    expect(screen.getByText("private.bin 无法安全打开")).toBeVisible();
  });

  it("fetches text with an abort signal and renders markup as text", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('<img src=x onerror="alert(1)">', {
        headers: { "Content-Type": "text/plain", "Content-Length": "30" },
      }),
    );
    const artifact = {
      artifact_id: "art_script",
      name: "script.txt",
      media_type: "text/plain; charset=utf-8",
      media_url: "/api/media/art_script",
      kind: "text",
      viewer: { size_bytes: 30 },
    } as Artifact;

    const { unmount } = render(<StageViewer stage="script" artifacts={[artifact]} />);

    expect(await screen.findByText(/<img src=x/)).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
    unmount();
    expect((fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal).aborted).toBe(true);
  });

  it("rejects HTML text responses and bounds bodies when content length is missing or malformed", async () => {
    const artifact = {
      artifact_id: "art_script",
      name: "script.txt",
      media_type: "text/plain",
      media_url: "/api/media/art_script",
      kind: "text",
    } as Artifact;
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("<b>unsafe</b>", { headers: { "Content-Type": "text/html" } }))
      .mockResolvedValueOnce(new Response("plain", { headers: { "Content-Type": "text/plain", "Content-Length": "not-a-number" } }));

    const first = render(<StageViewer stage="script" artifacts={[artifact]} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取成果文件");
    first.unmount();
    render(<StageViewer stage="script" artifacts={[artifact]} />);
    expect(await screen.findByText("plain")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("clears audio state on rejection, ended, error, and unmount", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValueOnce(new Error("blocked"));
    const view = render(<StageViewer stage="audio" artifacts={audioArtifacts} />);
    await userEvent.click(screen.getByRole("button", { name: "播放 voiceover.m4a" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("音频无法播放");

    play.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "播放 voiceover.m4a" }));
    expect(await screen.findByRole("button", { name: "暂停 voiceover.m4a" })).toBeVisible();
    const audio = screen.getAllByTestId("stage-audio")[0] as HTMLAudioElement;
    fireEvent.ended(audio);
    expect(screen.getByRole("button", { name: "播放 voiceover.m4a" })).toBeVisible();
    fireEvent.error(audio);
    expect(await screen.findByRole("alert")).toHaveTextContent("音频无法播放");
    view.unmount();
    expect(audio.pause).toHaveBeenCalled();
  });

  it("renders structured evaluation checks and safely falls back for unknown JSON", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema_version: "motion-comic-factory.eval.v1",
            checks: [
              {
                name: "字幕安全区",
                severity: "warning",
                passed: false,
                findings: ["第 3 镜字幕接近底边"],
              },
            ],
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ arbitrary: "safe fallback" }), {
          headers: { "Content-Type": "application/json" },
        }),
      );
    const artifacts = [
      {
        artifact_id: "art_eval",
        name: "eval-report.json",
        media_type: "application/json",
        media_url: "/api/media/art_eval",
        kind: "eval",
      },
      {
        artifact_id: "art_unknown_eval",
        name: "quality-eval.json",
        media_type: "application/json",
        media_url: "/api/media/art_unknown_eval",
        kind: "eval",
      },
    ] as Artifact[];

    render(<StageViewer stage="eval" artifacts={artifacts} />);

    expect(await screen.findByText("字幕安全区")).toBeVisible();
    expect(screen.getByText("第 3 镜字幕接近底边")).toBeVisible();
    const fallbacks = await screen.findAllByText(/safe fallback/);
    expect(fallbacks).toHaveLength(1);
    expect(within(fallbacks[0].closest("pre") as HTMLElement).getByText(/safe fallback/)).toBeVisible();
  });
});
