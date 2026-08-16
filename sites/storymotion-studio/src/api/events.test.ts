import { describe, expect, it, vi } from "vitest";

import { streamSse } from "./events";

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("fetch-stream SSE parser", () => {
  it("sends Last-Event-ID and parses chunked multiline events while ignoring comments", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      responseFromChunks([
        ": heartbeat\r\n",
        "id: 7\r\nevent: pro",
        "gress\r\ndata: {\"line\":1}\r\n",
        "data: {\"line\":2}\r\n\r\n",
        "data: final\n\n",
      ]),
    );

    const events = [];
    for await (const event of streamSse("/api/jobs/job-1/events", {
      fetch: fetchMock,
      lastEventId: "6",
    })) events.push(event);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-1/events",
      expect.objectContaining({
        headers: expect.objectContaining({ "Last-Event-ID": "6" }),
      }),
    );
    expect(events).toEqual([
      { id: "7", event: "progress", data: '{"line":1}\n{"line":2}' },
      { id: "", event: "message", data: "final" },
    ]);
  });

  it("bounds a single event even when it arrives across many chunks", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      responseFromChunks(["data: 1234", "5678", "90\n\n"]),
    );

    const read = async () => {
      for await (const event of streamSse("/events", { fetch: fetchMock, maxEventBytes: 8 })) {
        expect(event).toBeUndefined();
      }
    };

    await expect(read()).rejects.toThrow("SSE event exceeds 8 bytes");
  });

  it("passes abort through to fetch and stream reading", async () => {
    let observedSignal: AbortSignal | undefined;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      observedSignal = init?.signal ?? undefined;
      return responseFromChunks([]);
    });
    const controller = new AbortController();
    controller.abort();

    await expect(async () => {
      for await (const event of streamSse("/events", { fetch: fetchMock, signal: controller.signal })) {
        expect(event).toBeUndefined();
      }
    }).rejects.toMatchObject({ name: "AbortError" });
    expect(observedSignal?.aborted).toBe(true);
  });
});
