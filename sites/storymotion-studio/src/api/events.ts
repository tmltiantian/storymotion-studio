export interface SseMessage {
  id: string;
  event: string;
  data: string;
}

export interface StreamSseOptions {
  signal?: AbortSignal;
  lastEventId?: string;
  maxEventBytes?: number;
  maxLineBytes?: number;
  minRetryMs?: number;
  maxRetryMs?: number;
  onRetry?: (milliseconds: number) => void;
  fetch?: typeof fetch;
}

const DEFAULT_MAX_EVENT_BYTES = 64 * 1024;
const DEFAULT_MAX_LINE_BYTES = 16 * 1024;
const DEFAULT_MIN_RETRY_MS = 250;
const DEFAULT_MAX_RETRY_MS = 5000;

function abortError(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}

function contentType(response: Response): string {
  return (response.headers.get("Content-Type") ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
}

export async function* streamSse(
  url: string,
  options: StreamSseOptions = {},
): AsyncGenerator<SseMessage> {
  const fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  const maxEventBytes = options.maxEventBytes ?? DEFAULT_MAX_EVENT_BYTES;
  const maxLineBytes = options.maxLineBytes ?? DEFAULT_MAX_LINE_BYTES;
  const minRetryMs = options.minRetryMs ?? DEFAULT_MIN_RETRY_MS;
  const maxRetryMs = options.maxRetryMs ?? DEFAULT_MAX_RETRY_MS;
  if (maxEventBytes < 1 || maxLineBytes < 1 || minRetryMs < 0 || maxRetryMs < minRetryMs) {
    throw new Error("Invalid SSE stream limits");
  }

  const response = await fetchImpl(url, {
    signal: options.signal,
    headers: {
      Accept: "text/event-stream",
      ...(options.lastEventId !== undefined
        ? { "Last-Event-ID": options.lastEventId }
        : {}),
    },
  });
  if (options.signal?.aborted) throw abortError();
  if (!response.ok || !response.body) {
    throw new Error(`SSE request failed with status ${response.status}`);
  }
  if (contentType(response) !== "text/event-stream") {
    throw new Error("SSE response has an invalid content type");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  let lastEventId = options.lastEventId ?? "";
  let eventName = "";
  let dataLines: string[] = [];
  let eventBytes = 0;

  let rejectReadAbort: ((reason: DOMException) => void) | null = null;
  const readAborted = new Promise<never>((_resolve, reject) => {
    rejectReadAbort = reject;
  });
  const onAbort = () => {
    rejectReadAbort?.(abortError());
    void reader.cancel().catch(() => undefined);
  };
  options.signal?.addEventListener("abort", onAbort, { once: true });

  const resetEvent = () => {
    eventName = "";
    dataLines = [];
    eventBytes = 0;
  };

  const dispatch = (): SseMessage | null => {
    if (!dataLines.length) {
      resetEvent();
      return null;
    }
    const message = {
      id: lastEventId,
      event: eventName || "message",
      data: dataLines.join("\n"),
    };
    resetEvent();
    return message;
  };

  const consumeLine = (line: string): SseMessage | null => {
    const lineBytes = encoder.encode(line).byteLength;
    if (lineBytes > maxLineBytes) {
      throw new Error(`SSE line exceeds ${maxLineBytes} bytes`);
    }
    if (line === "") return dispatch();
    eventBytes += lineBytes + 1;
    if (eventBytes > maxEventBytes) {
      throw new Error(`SSE event exceeds ${maxEventBytes} bytes`);
    }
    if (line.startsWith(":")) return null;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") dataLines.push(value);
    else if (field === "event") eventName = value;
    else if (field === "id" && !value.includes("\0")) lastEventId = value;
    else if (field === "retry" && /^[0-9]+$/.test(value)) {
      const parsed = Number(value);
      if (Number.isSafeInteger(parsed)) {
        options.onRetry?.(Math.min(maxRetryMs, Math.max(minRetryMs, parsed)));
      }
    }
    return null;
  };

  const consumeCompleteLines = function* (eof = false): Generator<SseMessage> {
    let index = 0;
    while (index < buffer.length) {
      const character = buffer[index];
      if (character !== "\r" && character !== "\n") {
        index += 1;
        continue;
      }
      if (character === "\r" && index === buffer.length - 1 && !eof) break;
      const separatorLength = character === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + separatorLength);
      index = 0;
      const message = consumeLine(line);
      if (message) yield message;
    }
    if (encoder.encode(buffer).byteLength > maxLineBytes) {
      throw new Error(`SSE line exceeds ${maxLineBytes} bytes`);
    }
  };

  try {
    while (true) {
      if (options.signal?.aborted) throw abortError();
      const chunk = await Promise.race([reader.read(), readAborted]);
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      yield* consumeCompleteLines();
    }
    buffer += decoder.decode();
    yield* consumeCompleteLines(true);
    if (buffer.length) {
      const message = consumeLine(buffer);
      buffer = "";
      if (message) yield message;
    }
    const pending = dispatch();
    if (pending) yield pending;
  } finally {
    options.signal?.removeEventListener("abort", onAbort);
    rejectReadAbort = null;
    try {
      await reader.cancel();
    } catch {
      // The network stream may already be closed.
    }
  }
}
