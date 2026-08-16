import "@testing-library/jest-dom/vitest";

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { JobDetail, JobEvent, JobStatus, ResumeJobResponse } from "../api/types";
import type { SseMessage, StreamSseOptions } from "../api/events";
import { JobProgress, type JobProgressApi } from "./JobProgress";

function job(status: JobStatus = "running", overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    job_id: "1".repeat(32),
    project_id: "episode_01",
    operation: "video_generate",
    status,
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:01:00Z",
    provider_tasks: Object.fromEntries(
      Array.from({ length: 8 }, (_, index) => [
        `shot_${index + 1}`,
        { status: index < 2 ? "completed" : "queued" },
      ]),
    ),
    result: {},
    error: status === "failed" ? "Provider operation failed" : "",
    resume_count: 0,
    last_event_sequence: 3,
    ...overrides,
  };
}

function api(initial = job()): JobProgressApi {
  return {
    getJob: vi.fn().mockResolvedValue(initial),
    resumeJob: vi.fn().mockResolvedValue({ job_id: initial.job_id, status: "queued" }),
    jobEventsUrl: vi.fn().mockReturnValue(`/api/jobs/${initial.job_id}/events`),
  };
}

async function* idleStream(_url: string, options: StreamSseOptions): AsyncGenerator<SseMessage> {
  await new Promise<void>((resolve) => {
    if (options.signal?.aborted) resolve();
    else options.signal?.addEventListener("abort", () => resolve(), { once: true });
  });
  yield* [];
}

function event(sequence: number): SseMessage {
  const payload: JobEvent = {
    job_id: "1".repeat(32),
    sequence,
    kind: "provider_task",
    data: { shot_id: "shot_03", status: "completed" },
    created_at: "2026-08-16T00:01:30Z",
  };
  return { id: String(sequence), event: payload.kind, data: JSON.stringify(payload) };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("JobProgress recovery", () => {
  it("resumes from persisted job state after remount before opening a stream", async () => {
    const client = api();
    const calls: string[] = [];
    vi.mocked(client.getJob).mockImplementation(async () => {
      calls.push("get");
      return job();
    });
    const connect = vi.fn(async function* (_url: string, options: StreamSseOptions) {
      calls.push("stream");
      yield* idleStream("", options);
    });

    const first = render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);
    expect(await screen.findByText("已完成 2 / 8 镜")).toBeVisible();
    await waitFor(() => expect(calls.slice(0, 2)).toEqual(["get", "stream"]));
    first.unmount();

    render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);
    expect(await screen.findByText("已完成 2 / 8 镜")).toBeVisible();
    expect(client.getJob).toHaveBeenCalledTimes(2);
  });

  it("reconnects after the last known event ID and advances it from events", async () => {
    vi.useFakeTimers();
    const client = api();
    const lastIds: Array<string | undefined> = [];
    let attempt = 0;
    const connect = vi.fn(async function* (_url: string, options: StreamSseOptions) {
      lastIds.push(options.lastEventId);
      attempt += 1;
      if (attempt === 1) yield event(4);
      else yield* idleStream("", options);
    });

    render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);
    await act(async () => vi.advanceTimersByTimeAsync(600));

    expect(lastIds[0]).toBe("3");
    expect(lastIds[1]).toBe("4");
  });

  it("honors a bounded server retry hint before reconnecting", async () => {
    vi.useFakeTimers();
    const client = api();
    let attempts = 0;
    const connect = vi.fn(async function* (_url: string, options: StreamSseOptions) {
      attempts += 1;
      if (attempts === 1) options.onRetry?.(1200);
      else yield* idleStream("", options);
    });

    render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);
    await act(async () => vi.advanceTimersByTimeAsync(1199));
    expect(connect).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(connect).toHaveBeenCalledTimes(2);
  });

  it("falls back to persisted GET after five seconds without events", async () => {
    vi.useFakeTimers();
    const client = api();
    render(<JobProgress api={client} jobId={job().job_id} connect={idleStream} />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(client.getJob).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(client.getJob).toHaveBeenCalledTimes(2);
  });

  it("preserves the last job when an event refresh fails and recovers on fallback", async () => {
    vi.useFakeTimers();
    const client = api();
    const recovered = job("running", {
      provider_tasks: {
        ...job().provider_tasks,
        shot_3: { status: "completed" },
      },
      last_event_sequence: 4,
    });
    vi.mocked(client.getJob)
      .mockResolvedValueOnce(job())
      .mockRejectedValueOnce(new Error("temporary read failure"))
      .mockResolvedValue(recovered);
    const connect = vi.fn(async function* (_url: string, options: StreamSseOptions) {
      yield event(4);
      yield* idleStream("", options);
    });

    render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByText("已完成 2 / 8 镜")).toBeVisible();
    expect(screen.queryByText("无法读取作业状态")).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(client.getJob).toHaveBeenCalledTimes(3);
    expect(screen.getByText("已完成 3 / 8 镜")).toBeVisible();
  });

  it("re-arms silence fallback after a transient fallback GET failure", async () => {
    vi.useFakeTimers();
    const client = api();
    const recovered = job("running", { result: { total_shots: 9 } });
    vi.mocked(client.getJob)
      .mockResolvedValueOnce(job())
      .mockRejectedValueOnce(new Error("temporary read failure"))
      .mockResolvedValue(recovered);

    render(<JobProgress api={client} jobId={job().job_id} connect={idleStream} />);
    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(client.getJob).toHaveBeenCalledTimes(2);
    expect(screen.getByText("已完成 2 / 8 镜")).toBeVisible();
    expect(screen.queryByText("无法读取作业状态")).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(client.getJob).toHaveBeenCalledTimes(3);
    expect(screen.getByText("已完成 2 / 9 镜")).toBeVisible();
  });

  it("offers retry when the initial persisted job read fails", async () => {
    const user = userEvent.setup();
    const client = api();
    vi.mocked(client.getJob)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(job());

    render(<JobProgress api={client} jobId={job().job_id} connect={idleStream} />);

    await user.click(await screen.findByRole("button", { name: "重新读取作业" }));

    expect(await screen.findByText("已完成 2 / 8 镜")).toBeVisible();
    expect(client.getJob).toHaveBeenCalledTimes(2);
  });

  it("does not leave duplicate streams or timers under StrictMode and aborts on unmount", async () => {
    let active = 0;
    let maximum = 0;
    let aborts = 0;
    const client = api();
    const connect = vi.fn(async function* (_url: string, options: StreamSseOptions) {
      active += 1;
      maximum = Math.max(maximum, active);
      try {
        yield* idleStream("", options);
      } finally {
        active -= 1;
        if (options.signal?.aborted) aborts += 1;
      }
    });
    const view = render(
      <StrictMode><JobProgress api={client} jobId={job().job_id} connect={connect} /></StrictMode>,
    );

    expect(await screen.findByText("已完成 2 / 8 镜")).toBeVisible();
    await waitFor(() => expect(connect).toHaveBeenCalled());
    view.unmount();
    await waitFor(() => expect(active).toBe(0));
    expect(maximum).toBe(1);
    expect(aborts).toBeGreaterThan(0);
  });

  it("does not subscribe for terminal jobs", async () => {
    const client = api(job("completed"));
    const connect = vi.fn(idleStream);
    render(<JobProgress api={client} jobId={job().job_id} connect={connect} />);

    expect(await screen.findByText("生成完成")).toBeVisible();
    expect(connect).not.toHaveBeenCalled();
  });

  it("re-reads persisted state after resume instead of assuming queued", async () => {
    const user = userEvent.setup();
    const failed = job("failed");
    const running = job("running", { resume_count: 1 });
    const client = api(failed);
    vi.mocked(client.getJob)
      .mockResolvedValueOnce(failed)
      .mockResolvedValue(running);
    vi.mocked(client.resumeJob).mockResolvedValue({ job_id: failed.job_id, status: "queued" });
    render(<JobProgress api={client} jobId={failed.job_id} connect={idleStream} />);

    await user.click(await screen.findByRole("button", { name: "恢复生成" }));

    expect(await screen.findByText("生成中")).toBeVisible();
    expect(client.getJob).toHaveBeenCalledTimes(2);
  });

  it("recovers after the first persisted read fails following accepted resume", async () => {
    vi.useFakeTimers();
    const failed = job("failed");
    const running = job("running", { resume_count: 1 });
    const client = api(failed);
    vi.mocked(client.getJob)
      .mockResolvedValueOnce(failed)
      .mockRejectedValueOnce(new Error("temporary post-resume read failure"))
      .mockResolvedValue(running);
    vi.mocked(client.resumeJob).mockResolvedValue({
      job_id: failed.job_id,
      status: "queued",
    });

    render(<JobProgress api={client} jobId={failed.job_id} connect={idleStream} />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    fireEvent.click(screen.getByRole("button", { name: "恢复生成" }));
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByText("生成失败")).toBeVisible();
    expect(screen.getByText("正在恢复作业状态")).toBeVisible();
    expect(client.resumeJob).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(screen.getByText("生成中")).toBeVisible();
    expect(client.getJob).toHaveBeenCalledTimes(3);
    expect(client.resumeJob).toHaveBeenCalledTimes(1);
  });

  it("uses a full resume response and reports cross-process busy", async () => {
    const user = userEvent.setup();
    const failed = job("failed");
    const completed = job("completed", { resume_count: 1 });
    const client = api(failed);
    vi.mocked(client.resumeJob)
      .mockResolvedValueOnce(completed as ResumeJobResponse)
      .mockRejectedValueOnce(Object.assign(new Error("busy"), { code: "busy" }));
    const { rerender } = render(<JobProgress api={client} jobId={failed.job_id} connect={idleStream} />);
    await user.click(await screen.findByRole("button", { name: "恢复生成" }));
    expect(await screen.findByText("生成完成")).toBeVisible();

    vi.mocked(client.getJob).mockResolvedValue(failed);
    rerender(<JobProgress api={client} jobId={`${failed.job_id.slice(0, -1)}2`} connect={idleStream} />);
    await user.click(await screen.findByRole("button", { name: "恢复生成" }));
    expect(await screen.findByText("其他进程正在恢复此作业")).toBeVisible();
  });

  it("hides resume when the workspace requires a newly confirmed submission", async () => {
    const failed = job("failed");
    const client = api(failed);

    render(
      <JobProgress
        api={client}
        jobId={failed.job_id}
        connect={idleStream}
        allowResume={false}
      />,
    );

    expect(await screen.findByText("生成失败")).toBeVisible();
    expect(screen.queryByRole("button", { name: "恢复生成" })).not.toBeInTheDocument();
  });
});
