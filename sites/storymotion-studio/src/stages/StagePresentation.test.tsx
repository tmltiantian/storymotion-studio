import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StagePresentation } from "../api/types";
import { StagePresentationView } from "./StagePresentation";

describe("StagePresentationView", () => {
  it("renders a concept summary with character directions", () => {
    render(<StagePresentationView presentation={{
      stage: "concept",
      state: "ready",
      title: "雨夜来电",
      premise: "一个深夜电话改变了她的选择。",
      target: { duration_seconds: 42, aspect_ratio: "9:16", shots: 8 },
      characters: [{ name: "阿眠", role: "主角", appearance: "短发", voice: "清亮" }],
    }} />);

    expect(screen.getByRole("heading", { name: "雨夜来电" })).toBeVisible();
    expect(screen.getByText("一个深夜电话改变了她的选择。")).toBeVisible();
    expect(screen.getByText("短发")).toBeVisible();
  });

  it("renders a script for creator review without raw source content", () => {
    render(<StagePresentationView presentation={{
      stage: "script",
      state: "ready",
      title: "雨夜来电",
      total_duration_seconds: 6.5,
      characters: [{
        name: "阿眠",
        role: "主角",
        description: "谨慎但好奇",
        appearance: "短发",
        voice: "清亮、克制",
      }],
      shots: [{
        index: 1,
        title: "门外",
        action: "她停在门边听见铃声。",
        camera: "近景",
        duration_seconds: 6.5,
        dialogue: [{ speaker: "阿眠", emotion: "紧张", text: "谁？" }],
      }],
    }} />);

    expect(screen.getByRole("heading", { name: "雨夜来电" })).toBeVisible();
    expect(screen.getByText("阿眠")).toBeVisible();
    expect(screen.getByText("谁？")).toBeVisible();
    expect(document.querySelector("pre")).toBeNull();
    expect(screen.queryByText(/schema_version|application\/json|manifest\.json/)).toBeNull();
  });

  it("renders storyboard shots in their declared order", () => {
    render(<StagePresentationView presentation={{
      stage: "storyboard",
      state: "ready",
      title: "门外",
      shots: [
        { index: 2, title: "门外", action: "她停下。", camera: "近景", duration_seconds: 2 },
        { index: 3, title: "走廊", action: "她回头。", camera: "中景", duration_seconds: 3.5 },
      ],
    }} />);

    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("第 2 镜");
    expect(rows[1]).toHaveTextContent("第 3 镜");
  });

  it("renders media, quality, and delivery results as review information", () => {
    const { rerender } = render(<StagePresentationView presentation={{
      stage: "audio",
      state: "ready",
      dialogue_count: 2,
      total_duration_seconds: 4.25,
      speakers: [{ name: "阿眠", line_count: 1 }],
      timings: [{ speaker: "阿眠", text: "谁？", start_seconds: 0.5, end_seconds: 2 }],
    }} />);

    expect(screen.getByText("谁？")).toBeVisible();
    rerender(<StagePresentationView presentation={{
      stage: "eval",
      state: "ready",
      passed: false,
      checks: [{ name: "对白同步", severity: "warning", passed: false, findings: ["节奏需要调整"] }],
    }} />);
    expect(screen.getByText("对白同步")).toBeVisible();
    expect(screen.getByText("节奏需要调整")).toBeVisible();
    rerender(<StagePresentationView presentation={{
      stage: "deliver",
      state: "ready",
      quality_approved: true,
    }} />);
    expect(screen.getByText("质量检查已通过")).toBeVisible();
  });

  it("uses the creator-facing unavailable state", () => {
    render(<StagePresentationView presentation={{ stage: "assets", state: "unavailable" }} />);

    expect(screen.getByText("本阶段尚未生成可查看的成果")).toBeVisible();
  });

  it("replaces malformed ready sections with the unavailable state", () => {
    render(<StagePresentationView presentation={{
      stage: "script",
      state: "ready",
      title: "雨夜来电",
      characters: "bad",
    } as unknown as StagePresentation} />);

    expect(screen.getByText("本阶段尚未生成可查看的成果")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "雨夜来电" })).not.toBeInTheDocument();
  });

  it("replaces malformed nested arrays with the unavailable state", () => {
    render(<StagePresentationView presentation={{
      stage: "storyboard",
      state: "ready",
      shots: [{ index: 1, dialogue: ["bad"] }],
    } as unknown as StagePresentation} />);

    expect(screen.getByText("本阶段尚未生成可查看的成果")).toBeVisible();
  });

  it("replaces an unknown presentation variant with the unavailable state", () => {
    render(<StagePresentationView presentation={{
      stage: "unknown",
      state: "ready",
    } as unknown as StagePresentation} />);

    expect(screen.getByText("本阶段尚未生成可查看的成果")).toBeVisible();
  });

  it("replaces an unknown presentation state with the unavailable state", () => {
    render(<StagePresentationView presentation={{
      stage: "script",
      state: "invalid",
    } as unknown as StagePresentation} />);

    expect(screen.getByText("本阶段尚未生成可查看的成果")).toBeVisible();
  });
});
