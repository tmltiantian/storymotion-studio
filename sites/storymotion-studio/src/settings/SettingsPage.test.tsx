import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProviderSettings } from "../api/types";
import { SettingsPage } from "./SettingsPage";


const settings: ProviderSettings = {
  capabilities: {
    text: {
      provider: "gateway",
      model: "qwen3.6-plus",
      ready: true,
      credential_present: true,
      blockers: [],
      enabled: true,
    },
    video: {
      provider: "minimax",
      model: "MiniMax-H3",
      ready: false,
      credential_present: false,
      blockers: ["视频服务凭据未配置。"],
      enabled: true,
      supports_reference_images: true,
    },
  },
  defaults: {
    voice_mapping: [
      {
        role_id: "black_cat",
        role_name: "黑白猫",
        personality: "高冷御姐",
        voice_name: "魅力女友",
        speed: "+4",
      },
      {
        role_id: "orange_cat",
        role_name: "橘猫",
        personality: "可爱活泼",
        voice_name: "调皮公主",
        speed: "+2",
      },
    ],
    output: {
      aspect_ratio: "9:16",
      resolution: "1080x1920",
      fps: 30,
      target_duration_seconds: 75,
    },
    generation: { concurrency: 1, fee_cap_yuan: null },
  },
};


describe("SettingsPage", () => {
  it("shows provider readiness, fixed voices and public production defaults", async () => {
    render(
      <SettingsPage api={{ getProviderSettings: vi.fn().mockResolvedValue(settings) }} />,
    );

    expect(await screen.findByText("qwen3.6-plus")).toBeInTheDocument();
    expect(screen.getByText("MiniMax-H3")).toBeInTheDocument();
    expect(screen.getByText("凭据已配置")).toBeInTheDocument();
    expect(screen.getByText("凭据缺失")).toBeInTheDocument();
    expect(screen.getByText("魅力女友")).toBeInTheDocument();
    expect(screen.getByText("调皮公主")).toBeInTheDocument();
    expect(screen.getByText("1080x1920")).toBeInTheDocument();
    expect(screen.getByText("未设置费用上限")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sk-");
    expect(document.body.textContent).not.toContain("https://");
  });

  it("shows loading, empty and safe error states", async () => {
    const { unmount } = render(
      <SettingsPage api={{ getProviderSettings: vi.fn().mockResolvedValue({ capabilities: {}, defaults: settings.defaults }) }} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("正在读取设置");
    expect(await screen.findByText("当前没有 Provider 配置")).toBeInTheDocument();
    unmount();

    render(
      <SettingsPage api={{ getProviderSettings: vi.fn().mockRejectedValue(new Error("sk-secret /private/.env")) }} />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取制作设置");
    expect(document.body.textContent).not.toContain("sk-secret");
    expect(document.body.textContent).not.toContain("/private/.env");
  });
});
