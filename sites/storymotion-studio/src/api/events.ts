export interface SseMessage {
  id: string;
  event: string;
  data: string;
}

export interface StreamSseOptions {
  signal?: AbortSignal;
  lastEventId?: string;
  maxEventBytes?: number;
  fetch?: typeof fetch;
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}

export async function* streamSse(
  url: string,
  options: StreamSseOptions = {},
): AsyncGenerator<SseMessage> {
  const fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  const maxEventBytes = options.maxEventBytes ?? 64 * 1024;
  const response = await fetchImpl(url, {
    signal: options.signal,
    headers: {
      Accept: "text/event-stream",
      ...(options.lastEventId ? { "Last-Event-ID": options.lastEventId } : {}),
    },
  });
  if (options.signal?.aborted) throw abortError();
  if (!response.ok || !response.body) {
    throw new Error(`SSE request failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  let eventId = "";
  let eventName = "message";
  let dataLines: string[] = [];
  let eventBytes = 0;

  const consumeLine = (line: string): SseMessage | null => {
    if (line === "") {
      const message = dataLines.length
        ? { id: eventId, event: eventName || "message", data: dataLines.join("\n") }
        : null;
      eventId = "";
      eventName = "message";
      dataLines = [];
      eventBytes = 0;
      return message;
    }
    eventBytes += encoder.encode(`${line}\n`).byteLength;
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
    else if (field === "id" && !value.includes("\0")) eventId = value;
    return null;
  };

  try {
    while (true) {
      if (options.signal?.aborted) throw abortError();
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        let line = buffer.slice(0, newline);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        buffer = buffer.slice(newline + 1);
        const message = consumeLine(line);
        if (message) yield message;
        newline = buffer.indexOf("\n");
      }
      if (eventBytes + encoder.encode(buffer).byteLength > maxEventBytes) {
        throw new Error(`SSE event exceeds ${maxEventBytes} bytes`);
      }
    }
    buffer += decoder.decode();
    if (buffer) consumeLine(buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer);
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The network stream may already be closed.
    }
  }
}
