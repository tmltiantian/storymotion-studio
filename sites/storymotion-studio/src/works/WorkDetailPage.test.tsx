import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { WorkDetail } from "../api/types";
import { WorkDetailPage } from "./WorkDetailPage";


const work = {
  work_id: "work_delivered",
  project_id: "interview-cat",
  title: "咪要去面试",
  mode: "replica",
  source: "delivered",
  delivered_at: "2026-08-15T12:00:00Z",
  delivery_date: "2026-08-15",
  roles: ["豆包"],
  current_version: "V3.1",
  versions: [
    {
      version_id: "version_31",
      label: "V3.1",
      created_at: "2026-08-15T12:00:00Z",
      outputs: [
        {
          artifact_id: "art_master",
          name: "master.mp4",
          media_type: "video/mp4",
          media_url: "/api/media/art_master",
          download_url: "/api/download/art_master",
          kind: "video",
          viewer: { size_bytes: 2048, width: 1080, height: 1920 },
          sha256: "a".repeat(64),
        },
      ],
      eval_reports: [
        {
          artifact_id: "art_eval",
          name: "eval_result.json",
          media_type: "application/json",
          media_url: "/api/media/art_eval",
          download_url: "/api/download/art_eval",
          kind: "eval",
          viewer: { size_bytes: 512 },
          sha256: "b".repeat(64),
        },
      ],
      iteration_summary: "逐镜返修通过",
    },
    {
      version_id: "version_30",
      label: "V3.0",
      created_at: "2026-08-14T12:00:00Z",
      outputs: [],
      eval_reports: [],
    },
  ],
} as WorkDetail;


function renderDetail(getWork = vi.fn().mockResolvedValue(work)) {
  return render(
    <MemoryRouter initialEntries={["/works/work_delivered"]}>
      <Routes>
        <Route path="/works/:id" element={<WorkDetailPage api={{ getWork }} />} />
      </Routes>
    </MemoryRouter>,
  );
}


describe("WorkDetailPage", () => {
  it("shows selectable versions, stable media and an opaque download", async () => {
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByRole("heading", { name: "咪要去面试" })).toBeInTheDocument();
    expect(screen.getByLabelText("作品版本")).toHaveValue("version_31");
    const video = screen.getByTestId("work-video");
    expect(video).toHaveAttribute("src", "/api/media/art_master");
    expect(screen.getByRole("link", { name: "下载 master.mp4" })).toHaveAttribute(
      "href",
      "/api/download/art_master",
    );
    expect(screen.getByText("逐镜返修通过")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("作品版本"), "version_30");
    expect(screen.getByText("这个版本没有可预览的媒体")).toBeInTheDocument();
    expect(screen.queryByText("逐镜返修通过")).not.toBeInTheDocument();
  });

  it("does not invent unavailable EVAL or iteration evidence", async () => {
    renderDetail(
      vi.fn().mockResolvedValue({
        ...work,
        versions: [{ ...work.versions[1], outputs: work.versions[0].outputs }],
        current_version: "V3.0",
      }),
    );

    await screen.findByRole("heading", { name: "咪要去面试" });
    expect(screen.getByText("未提供 EVAL 报告")).toBeInTheDocument();
    expect(screen.getByText("未记录迭代说明")).toBeInTheDocument();
  });

  it("shows safe loading and failure states", async () => {
    renderDetail(vi.fn().mockRejectedValue(new Error("/private/archive/master.mp4")));

    expect(screen.getByRole("status")).toHaveTextContent("正在读取作品");
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取这件作品");
    expect(screen.queryByText(/private\/archive/)).not.toBeInTheDocument();
  });
});
