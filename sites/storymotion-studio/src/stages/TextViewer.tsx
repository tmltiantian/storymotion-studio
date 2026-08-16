import { AlertCircle, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import type { Artifact } from "../api/types";
import { authorizedArtifactUrl, fetchArtifactText } from "./viewerUtils";

type TextState =
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "error"; reason: "large" | "request" };

export function TextViewer({ artifact }: { artifact: Artifact }) {
  const [state, setState] = useState<TextState>({ status: "loading" });
  const url = authorizedArtifactUrl(artifact);

  useEffect(() => {
    const controller = new AbortController();
    if (!url) {
      queueMicrotask(() => {
        if (!controller.signal.aborted) setState({ status: "error", reason: "request" });
      });
      return () => controller.abort();
    }
    if ((artifact.viewer?.size_bytes ?? 0) > 1024 * 1024) {
      queueMicrotask(() => {
        if (!controller.signal.aborted) setState({ status: "error", reason: "large" });
      });
      return () => controller.abort();
    }
    queueMicrotask(() => {
      if (!controller.signal.aborted) setState({ status: "loading" });
    });
    void fetchArtifactText(url, controller.signal).then(
      (raw) => {
        if (controller.signal.aborted) return;
        if (artifact.media_type.toLowerCase().includes("json")) {
          try {
            setState({ status: "ready", text: JSON.stringify(JSON.parse(raw), null, 2) });
            return;
          } catch {
            // Invalid JSON remains inspectable as plain text.
          }
        }
        setState({ status: "ready", text: raw });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          reason: error instanceof Error && error.message === "media_too_large" ? "large" : "request",
        });
      },
    );
    return () => controller.abort();
  }, [artifact.media_type, artifact.viewer?.size_bytes, url]);

  if (state.status === "loading") {
    return <div className="viewer-state" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={17} />正在读取 {artifact.name}</div>;
  }
  if (state.status === "error") {
    return <div className="viewer-state viewer-state-error" role="alert"><AlertCircle aria-hidden="true" size={17} />{state.reason === "large" ? "文件过大，无法在此预览" : "无法读取成果文件"}</div>;
  }
  return <pre className="text-viewer" tabIndex={0}>{state.text}</pre>;
}
