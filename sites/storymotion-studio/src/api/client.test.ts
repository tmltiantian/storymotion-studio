import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createApiClient } from "./client";
import type { JobDetail, JobStatus, ResumeJobResponse } from "./types";

function job(status: JobStatus): JobDetail {
  return {
    job_id: "a".repeat(32),
    project_id: "episode_01",
    operation: "video_generate",
    status,
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:01:00Z",
    provider_tasks: {},
    result: {},
    error: status === "failed" ? "Provider job failed" : "",
    resume_count: 1,
  };
}

describe("Task 5 API contracts", () => {
  it("models the future works catalog without issuing nonexistent HTTP routes", () => {
    const fetchFake = vi.fn<typeof fetch>();
    const client = createApiClient({ fetch: fetchFake });

    expect(client.works).toEqual({
      availability: "unavailable",
      reason: "local_catalog_not_configured",
    });
    expect(fetchFake).not.toHaveBeenCalled();
  });

  it("preserves every full job record branch returned by resume", async () => {
    const statuses: JobStatus[] = [
      "queued",
      "running",
      "completed",
      "cancelled",
      "failed",
    ];
    const fetchFake = vi.fn<typeof fetch>();
    for (const status of statuses) {
      fetchFake.mockResolvedValueOnce(
        new Response(JSON.stringify(job(status)), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    const client = createApiClient({ fetch: fetchFake });

    expectTypeOf(client.resumeJob).returns.resolves.toEqualTypeOf<ResumeJobResponse>();
    const responses: ResumeJobResponse[] = [];
    for (const status of statuses) {
      responses.push(await client.resumeJob(`${status}_job`));
    }

    expect(responses.map((response) => response.status)).toEqual(statuses);
    expect(responses.every((response) => "operation" in response)).toBe(true);
  });

  it("also accepts the queued acknowledgment returned when recovery starts", async () => {
    const fetchFake = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ job_id: "b".repeat(32), status: "queued" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = createApiClient({ fetch: fetchFake });

    const response: ResumeJobResponse = await client.resumeJob("b".repeat(32));

    expect(response).toEqual({ job_id: "b".repeat(32), status: "queued" });
  });
});
