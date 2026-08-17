import type { Artifact } from "../api/types";

export const MAX_TEXT_BYTES = 1024 * 1024;

const TEXT_MEDIA_TYPES = new Set([
  "text/plain",
  "text/markdown",
  "text/csv",
  "text/tab-separated-values",
  "text/vtt",
  "application/json",
  "application/x-subrip",
]);

function isAllowedTextMediaType(value: string): boolean {
  const mediaType = value.split(";", 1)[0].trim().toLowerCase();
  return TEXT_MEDIA_TYPES.has(mediaType) || /^application\/[a-z0-9.+-]+\+json$/.test(mediaType);
}

export function authorizedArtifactUrl(artifact: Artifact): string | null {
  try {
    const base = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const parsed = new URL(artifact.media_url, base);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const mediaIndex = parts.lastIndexOf("media");
    if (
      mediaIndex < 1 ||
      parts[mediaIndex - 1] !== "api" ||
      mediaIndex !== parts.length - 2 ||
      decodeURIComponent(parts[mediaIndex + 1] ?? "") !== artifact.artifact_id
    ) {
      return null;
    }
    if (parsed.origin !== base && !artifact.media_url.startsWith("/")) return null;
    return artifact.media_url;
  } catch {
    return null;
  }
}

export function authorizedArtifactDownloadUrl(artifact: Artifact): string | null {
  const downloadUrl = artifact.download_url;
  if (!downloadUrl) return null;
  try {
    const base = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const parsed = new URL(downloadUrl, base);
    const parts = parsed.pathname.split("/").filter(Boolean);
    const downloadIndex = parts.lastIndexOf("download");
    if (
      downloadIndex < 1 ||
      parts[downloadIndex - 1] !== "api" ||
      downloadIndex !== parts.length - 2 ||
      decodeURIComponent(parts[downloadIndex + 1] ?? "") !== artifact.artifact_id
    ) {
      return null;
    }
    if (parsed.origin !== base && !downloadUrl.startsWith("/")) return null;
    return downloadUrl;
  } catch {
    return null;
  }
}

export async function fetchArtifactText(
  url: string,
  signal: AbortSignal,
  maxBytes = MAX_TEXT_BYTES,
): Promise<string> {
  const response = await fetch(url, {
    signal,
    headers: { Accept: "text/plain, application/json" },
  });
  if (!response.ok) throw new Error("media_request_failed");
  if (!isAllowedTextMediaType(response.headers.get("Content-Type") ?? "")) {
    throw new Error("media_type_not_allowed");
  }
  const rawLength = response.headers.get("Content-Length")?.trim() ?? "";
  const announced = /^\d+$/.test(rawLength) ? Number(rawLength) : null;
  if (announced !== null && Number.isSafeInteger(announced) && announced > maxBytes) {
    throw new Error("media_too_large");
  }
  if (!response.body) {
    const value = await response.text();
    if (new TextEncoder().encode(value).byteLength > maxBytes) {
      throw new Error("media_too_large");
    }
    return value;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let received = 0;
  let text = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    received += chunk.value.byteLength;
    if (received > maxBytes) {
      await reader.cancel();
      throw new Error("media_too_large");
    }
    text += decoder.decode(chunk.value, { stream: true });
  }
  return text + decoder.decode();
}

export function formatMediaTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const minutes = Math.floor(safe / 60);
  const remaining = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remaining.toFixed(2).padStart(5, "0")}`;
}
