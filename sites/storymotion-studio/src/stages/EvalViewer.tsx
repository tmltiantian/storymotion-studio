import { AlertCircle, CheckCircle2, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import type { Artifact } from "../api/types";
import { authorizedArtifactUrl, fetchArtifactText } from "./viewerUtils";

type EvalCheck = { name: string; severity: string; passed: boolean; findings: string[] };
type EvalState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; checks: EvalCheck[] | null; fallback: string };

function structuredChecks(value: unknown): EvalCheck[] | null {
  if (!value || typeof value !== "object") return null;
  const checks = (value as { checks?: unknown }).checks;
  if (!Array.isArray(checks)) return null;
  const result: EvalCheck[] = [];
  for (const item of checks) {
    if (!item || typeof item !== "object") return null;
    const record = item as Record<string, unknown>;
    if (typeof record.name !== "string" || typeof record.passed !== "boolean") return null;
    result.push({
      name: record.name,
      severity: typeof record.severity === "string" ? record.severity : "info",
      passed: record.passed,
      findings: Array.isArray(record.findings) ? record.findings.filter((finding): finding is string => typeof finding === "string") : [],
    });
  }
  return result;
}

export function EvalViewer({ artifact }: { artifact: Artifact }) {
  const [state, setState] = useState<EvalState>({ status: "loading" });
  const url = authorizedArtifactUrl(artifact);
  useEffect(() => {
    const controller = new AbortController();
    if (!url) {
      queueMicrotask(() => {
        if (!controller.signal.aborted) setState({ status: "error" });
      });
      return () => controller.abort();
    }
    void fetchArtifactText(url, controller.signal).then(
      (raw) => {
        if (controller.signal.aborted) return;
        try {
          const value: unknown = JSON.parse(raw);
          setState({ status: "ready", checks: structuredChecks(value), fallback: JSON.stringify(value, null, 2) });
        } catch {
          setState({ status: "ready", checks: null, fallback: raw });
        }
      },
      () => {
        if (!controller.signal.aborted) setState({ status: "error" });
      },
    );
    return () => controller.abort();
  }, [url]);

  if (state.status === "loading") return <div className="viewer-state" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={17} />正在读取评估结果</div>;
  if (state.status === "error") return <div className="viewer-state viewer-state-error" role="alert"><AlertCircle aria-hidden="true" size={17} />无法读取评估结果</div>;
  if (!state.checks) return <pre className="text-viewer eval-fallback" tabIndex={0}>{state.fallback}</pre>;
  return (
    <div className="eval-viewer">
      {state.checks.map((check, index) => (
        <section className={`eval-check severity-${check.severity}`} key={`${check.name}-${index}`}>
          <div>
            {check.passed ? <CheckCircle2 aria-hidden="true" size={16} /> : <AlertCircle aria-hidden="true" size={16} />}
            <strong>{check.name}</strong>
            <span>{check.severity}</span>
          </div>
          {check.findings.length ? <ul>{check.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul> : null}
        </section>
      ))}
    </div>
  );
}
