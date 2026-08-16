import "@testing-library/jest-dom/vitest";

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ConfirmedVideoPreflight,
  JobAccepted,
  VideoGenerationRequest,
  VideoPreflight as VideoPreflightResult,
} from "../api/types";
import { VideoPreflight, type VideoPreflightApi } from "./VideoPreflight";

function request(overrides: Partial<VideoGenerationRequest> = {}): VideoGenerationRequest {
  return {
    schema_version: "motion-comic-factory.video-generation-request.v1",
    project_id: "episode_01",
    project_sha256: "a".repeat(64),
    package_sha256: "b".repeat(64),
    revision_hashes: { video: "c".repeat(64) },
    artifact_hashes: { art_package: "d".repeat(64) },
    approval_hashes: { storyboard: "e".repeat(64) },
    repair_plan_sha256: "f".repeat(64),
    shot_ids: ["shot_02", "shot_03"],
    shots: [
      { shot_id: "shot_02", duration: 4, resolution: "1080x1920" },
      { shot_id: "shot_03", duration: 6, resolution: "1080x1920" },
    ],
    provider: "gateway",
    model: "seedance-1-5-pro",
    resolution: "1080x1920",
    output_seconds: 10,
    estimated_cost_yuan: 18.6,
    price_yuan_per_second: 1.86,
    ...overrides,
  };
}

function estimate(overrides: Partial<VideoPreflightResult> = {}): VideoPreflightResult {
  return { ...request(), ready: true, blockers: [], ...overrides };
}

function confirmed(
  generationRequest = request(),
  token = "secret-generation-token",
): ConfirmedVideoPreflight {
  return { generation_token: token, generation_request: generationRequest };
}

function api(preflight = estimate()): VideoPreflightApi {
  const accepted: JobAccepted = { job_id: "1".repeat(32), status: "queued" };
  return {
    preflightVideo: vi.fn().mockResolvedValue(preflight),
    confirmVideo: vi.fn().mockResolvedValue(confirmed()),
    testVideo: vi.fn().mockResolvedValue(accepted),
    generateVideo: vi.fn().mockResolvedValue(accepted),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("VideoPreflight paid gate", () => {
  it("requires a fresh confirmation before batch generation", async () => {
    const user = userEvent.setup();
    const client = api();
    render(<VideoPreflight api={client} projectId="episode_01" shotIds={["shot_02", "shot_03"]} />);

    expect(await screen.findByText("shot_02、shot_03")).toBeVisible();
    expect(screen.getByText("¥18.60")).toBeVisible();
    const batch = screen.getByRole("button", { name: "批量生成全片" });
    expect(batch).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    expect(await screen.findByRole("button", { name: "批量生成全片" })).toBeEnabled();
    expect(document.body).not.toHaveTextContent("secret-generation-token");
  });

  it("submits the exact confirmed canonical request and consumes the token once", async () => {
    const user = userEvent.setup();
    const canonical = request({ output_seconds: 9.75, estimated_cost_yuan: 18.14 });
    const client = api({ ...canonical, ready: true, blockers: [] });
    vi.mocked(client.confirmVideo).mockResolvedValue(confirmed(canonical, "one-shot-token"));
    render(<VideoPreflight api={client} projectId="episode_01" shotIds={canonical.shot_ids} />);

    await screen.findByText("¥18.14");
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    const batch = await screen.findByRole("button", { name: "批量生成全片" });
    await user.dblClick(batch);

    expect(client.generateVideo).toHaveBeenCalledTimes(1);
    expect(client.generateVideo).toHaveBeenCalledWith("episode_01", {
      generation_token: "one-shot-token",
      generation_request: canonical,
    });
    expect(batch).toBeDisabled();
  });

  it("uses the same canonical envelope for test generation", async () => {
    const user = userEvent.setup();
    const client = api();
    render(<VideoPreflight api={client} projectId="episode_01" shotIds={["shot_02", "shot_03"]} />);

    await screen.findByText("¥18.60");
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    await user.click(screen.getByRole("button", { name: "试生成所选镜头" }));

    expect(client.testVideo).toHaveBeenCalledWith("episode_01", {
      generation_token: "secret-generation-token",
      generation_request: request(),
    });
    expect(screen.getByRole("button", { name: "批量生成全片" })).toBeDisabled();
  });

  it("invalidates confirmation when selection or preflight identity changes", async () => {
    const user = userEvent.setup();
    const client = api();
    const { rerender } = render(
      <VideoPreflight api={client} projectId="episode_01" shotIds={["shot_02", "shot_03"]} />,
    );
    await screen.findByText("¥18.60");
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    expect(screen.getByRole("button", { name: "批量生成全片" })).toBeEnabled();

    vi.mocked(client.preflightVideo).mockResolvedValueOnce(
      estimate({
        shot_ids: ["shot_03"],
        shots: [{ shot_id: "shot_03", duration: 6, resolution: "720x1280" }],
        model: "seedance-2-0",
        resolution: "720x1280",
        output_seconds: 6,
        estimated_cost_yuan: 9,
      }),
    );
    rerender(<VideoPreflight api={client} projectId="episode_01" shotIds={["shot_03"]} />);

    expect(await screen.findByText("seedance-2-0")).toBeVisible();
    expect(screen.getByRole("button", { name: "批量生成全片" })).toBeDisabled();
    expect(client.preflightVideo).toHaveBeenLastCalledWith("episode_01", ["shot_03"]);
  });

  it("ignores a confirmation that resolves after the selection changed", async () => {
    const user = userEvent.setup();
    let resolveConfirm: (value: ConfirmedVideoPreflight) => void = () => undefined;
    const client = api();
    vi.mocked(client.confirmVideo).mockImplementation(
      () => new Promise((resolve) => { resolveConfirm = resolve; }),
    );
    const { rerender } = render(
      <VideoPreflight api={client} projectId="episode_01" shotIds={["shot_02", "shot_03"]} />,
    );
    await screen.findByText("¥18.60");
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));
    vi.mocked(client.preflightVideo).mockResolvedValueOnce(
      estimate({ shot_ids: ["shot_03"], shots: [request().shots[1]], output_seconds: 6 }),
    );
    rerender(<VideoPreflight api={client} projectId="episode_01" shotIds={["shot_03"]} />);
    await act(async () => resolveConfirm(confirmed()));

    await screen.findByText("shot_03");
    expect(screen.getByRole("button", { name: "批量生成全片" })).toBeDisabled();
  });

  it("requires a new preflight after stale confirmation and keeps blockers disabled", async () => {
    const user = userEvent.setup();
    const client = api();
    vi.mocked(client.confirmVideo).mockRejectedValueOnce(
      Object.assign(new Error("stale"), { code: "stale_confirmation" }),
    );
    vi.mocked(client.preflightVideo)
      .mockResolvedValueOnce(estimate())
      .mockResolvedValueOnce(estimate({ ready: false, blockers: ["分镜修订已变化"] }));
    render(<VideoPreflight api={client} projectId="episode_01" shotIds={["shot_02", "shot_03"]} />);

    await screen.findByText("¥18.60");
    await user.click(screen.getByRole("button", { name: "确认费用与输入" }));

    expect(await screen.findByText("分镜修订已变化")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认费用与输入" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "批量生成全片" })).toBeDisabled();
    await waitFor(() => expect(client.preflightVideo).toHaveBeenCalledTimes(2));
  });
});
