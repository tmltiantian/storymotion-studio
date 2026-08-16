import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { WorkSummary } from "../api/types";
import { WorksPage } from "./WorksPage";


const delivered: WorkSummary = {
  work_id: "work_delivered",
  project_id: "interview-cat",
  title: "咪要去面试",
  mode: "replica",
  source: "delivered",
  delivered_at: "2026-08-15T12:00:00Z",
  current_version: "V3.1",
};

const historical: WorkSummary = {
  work_id: "work_archive",
  project_id: "",
  title: "历史归档",
  mode: "historical",
  source: "historical",
  delivered_at: "",
  current_version: "4 项素材",
};


function renderPage(listWorks = vi.fn().mockResolvedValue([delivered, historical])) {
  return render(
    <MemoryRouter>
      <WorksPage api={{ listWorks }} />
    </MemoryRouter>,
  );
}


describe("WorksPage", () => {
  it("loads delivered and historical works from the API", async () => {
    const listWorks = vi.fn().mockResolvedValue([delivered, historical]);

    renderPage(listWorks);

    expect(screen.getByRole("status")).toHaveTextContent("正在读取作品目录");
    expect(await screen.findByRole("heading", { name: "咪要去面试" })).toBeInTheDocument();
    expect(screen.getByText("V3.1")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "历史归档" })).toBeInTheDocument();
    expect(screen.getByText("未归类素材")).toBeInTheDocument();
    expect(listWorks).toHaveBeenCalledTimes(1);
  });

  it("filters API results without shipping a hard-coded works array", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("咪要去面试");

    await user.type(screen.getByRole("searchbox", { name: "筛选作品" }), "面试");

    expect(screen.getByRole("heading", { name: "咪要去面试" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "历史归档" })).not.toBeInTheDocument();
  });

  it("renders honest empty and error states", async () => {
    const { unmount } = renderPage(vi.fn().mockResolvedValue([]));
    expect(await screen.findByText("还没有可查看的作品")).toBeInTheDocument();
    unmount();

    renderPage(vi.fn().mockRejectedValue(new Error("private /Users/name/file")));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取作品目录");
    expect(screen.queryByText(/Users/)).not.toBeInTheDocument();
  });
});
