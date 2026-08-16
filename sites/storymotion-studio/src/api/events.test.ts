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
      { id: "7", event: "message", data: "final" },
    ]);
  });

  it("persists and resets event IDs, ignores NUL IDs, and dispatches at EOF", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(responseFromChunks([
      "id: 4\rdata: first\r\r",
      "id: bad\0id\ndata: second\n\n",
      "id:\r\ndata: reset at eof",
    ]));

    const events = [];
    for await (const event of streamSse("/events", { fetch: fetchMock, lastEventId: "3" })) events.push(event);

    expect(events).toEqual([
      { id: "4", event: "message", data: "first" },
      { id: "4", event: "message", data: "second" },
      { id: "", event: "message", data: "reset at eof" },
    ]);
  });

  it("decodes UTF-8 split across chunks and accepts bounded retry hints", async () => {
    const bytes = new TextEncoder().encode("retry: 0\ndata: 猫\n\nretry: 999999\ndata: 完成\n\n");
    const response = new Response(new ReadableStream({
      start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
    const retries: number[] = [];
    const events = [];
    for await (const event of streamSse("/events", {
      fetch: vi.fn<typeof fetch>().mockResolvedValue(response),
      onRetry: (value) => retries.push(value),
    })) events.push(event);

    expect(retries).toEqual([250, 5000]);
    expect(events.map((item) => item.data)).toEqual(["猫", "完成"]);
  });

  it("rejects wrong content types and overlong lines", async () => {
    const wrongType = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("data: no\n\n", { headers: { "Content-Type": "application/json" } }),
    );
    await expect(async () => {
      for await (const event of streamSse("/events", { fetch: wrongType })) void event;
    }).rejects.toThrow("invalid content type");

    const longLine = vi.fn<typeof fetch>().mockResolvedValue(responseFromChunks(["data: 123456789\n\n"]));
    await expect(async () => {
      for await (const event of streamSse("/events", { fetch: longLine, maxLineBytes: 8 })) void event;
    }).rejects.toThrow("SSE line exceeds 8 bytes");
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

  it("aborts while a stream read is pending", async () => {
    let cancelled = false;
    const response = new Response(new ReadableStream({
      pull() {
        return new Promise(() => undefined);
      },
      cancel() {
        cancelled = true;
      },
    }), { headers: { "Content-Type": "text/event-stream" } });
    const controller = new AbortController();
    const read = (async () => {
      for await (const event of streamSse("/events", {
        fetch: vi.fn<typeof fetch>().mockResolvedValue(response),
        signal: controller.signal,
      })) void event;
    })();
    await Promise.resolve();
    controller.abort();

    await expect(read).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toBe(true);
  });
});
