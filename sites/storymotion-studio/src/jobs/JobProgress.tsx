import { AlertCircle, CheckCircle2, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ApiClient } from "../api/client";
import { streamSse, type SseMessage, type StreamSseOptions } from "../api/events";
import type { JobDetail, JobEvent } from "../api/types";

export type JobProgressApi = Pick<ApiClient, "getJob" | "resumeJob" | "jobEventsUrl">;
export type JobEventConnector = (
  url: string,
  options: StreamSseOptions,
) => AsyncIterable<SseMessage>;

type ViewState =
  | { status: "loading" }
  | { status: "ready"; job: JobDetail }
  | { status: "error" };

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function isTerminal(job: JobDetail): boolean {
  return TERMINAL.has(job.status);
}

function codeOf(error: unknown): string {
  return typeof error === "object" && error !== null && "code" in error
    ? String(error.code)
    : "";
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

function progress(job: JobDetail): { completed: number; total: number } {
  const tasks = Object.values(job.provider_tasks).filter(
    (value) => Boolean(value && typeof value === "object" && !Array.isArray(value)),
  ) as Array<Record<string, unknown>>;
  const completed = tasks.filter((task) => ["completed", "succeeded", "passed"].includes(String(task.status ?? ""))).length;
  const resultTotal = Number(job.result.total_shots ?? job.result.shot_count ?? 0);
  return { completed, total: Number.isFinite(resultTotal) && resultTotal > 0 ? resultTotal : tasks.length };
}

function statusLabel(job: JobDetail): string {
  if (job.status === "completed") return "生成完成";
  if (job.status === "failed") return "生成失败";
  if (job.status === "cancelled") return "生成已取消";
  if (job.status === "running") return "生成中";
  return "等待生成";
}

export function JobProgress({
  api,
  jobId,
  connect = streamSse,
  onJobChange,
}: {
  api: JobProgressApi;
  jobId: string;
  connect?: JobEventConnector;
  onJobChange?: (job: JobDetail) => void;
}) {
  const [state, setState] = useState<ViewState>({ status: "loading" });
  const [resumePending, setResumePending] = useState(false);
  const [resumeMessage, setResumeMessage] = useState("");
  const [recoveryGeneration, setRecoveryGeneration] = useState(0);
  const mountedRef = useRef(false);
  const jobIdRef = useRef(jobId);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    let active = true;
    let silenceTimer: number | null = null;
    let lastEventId = "";
    let reconnectDelay = 500;
    let serverRetryDelay = 500;

    const clearSilenceTimer = () => {
      if (silenceTimer !== null) window.clearTimeout(silenceTimer);
      silenceTimer = null;
    };

    const refresh = async (): Promise<JobDetail | null> => {
      try {
        const next = await api.getJob(jobId);
        if (!active || controller.signal.aborted || jobIdRef.current !== jobId) return null;
        setState({ status: "ready", job: next });
        onJobChange?.(next);
        const persistedSequence = next.last_event_sequence ?? 0;
        if (persistedSequence > Number(lastEventId || "0")) lastEventId = String(persistedSequence);
        if (isTerminal(next)) {
          clearSilenceTimer();
          controller.abort();
        }
        return next;
      } catch {
        if (active && !controller.signal.aborted) setState({ status: "error" });
        return null;
      }
    };

    const armSilenceFallback = () => {
      clearSilenceTimer();
      if (!active || controller.signal.aborted) return;
      silenceTimer = window.setTimeout(() => {
        silenceTimer = null;
        void refresh().then((next) => {
          if (next && !isTerminal(next)) armSilenceFallback();
        });
      }, 5000);
    };

    const run = async () => {
      setState({ status: "loading" });
      const persisted = await refresh();
      if (!persisted || isTerminal(persisted) || controller.signal.aborted) return;
      while (active && !controller.signal.aborted) {
        let receivedEvent = false;
        armSilenceFallback();
        try {
          for await (const message of connect(api.jobEventsUrl(jobId), {
            signal: controller.signal,
            lastEventId: lastEventId || undefined,
            onRetry: (milliseconds) => {
              serverRetryDelay = Math.min(5000, Math.max(250, milliseconds));
              reconnectDelay = serverRetryDelay;
            },
          })) {
            if (!active || controller.signal.aborted) return;
            receivedEvent = true;
            reconnectDelay = serverRetryDelay;
            if (message.id === "") lastEventId = "";
            const sequence = Number(message.id || "0");
            if (Number.isInteger(sequence) && sequence > Number(lastEventId || "0")) {
              lastEventId = String(sequence);
            }
            try {
              const payload = JSON.parse(message.data) as JobEvent;
              if (payload.job_id !== jobId) continue;
              if (payload.sequence > Number(lastEventId || "0")) lastEventId = String(payload.sequence);
            } catch {
              continue;
            }
            armSilenceFallback();
            const next = await refresh();
            if (!next || isTerminal(next)) return;
          }
        } catch (error) {
          if (controller.signal.aborted || !active || (error instanceof DOMException && error.name === "AbortError")) return;
        } finally {
          clearSilenceTimer();
        }
        if (!active || controller.signal.aborted) return;
        await abortableDelay(reconnectDelay, controller.signal);
        if (!receivedEvent) reconnectDelay = Math.min(Math.max(reconnectDelay, serverRetryDelay) * 2, 5000);
      }
    };

    void run();
    return () => {
      active = false;
      mountedRef.current = false;
      clearSilenceTimer();
      controller.abort();
    };
  }, [api, connect, jobId, onJobChange, recoveryGeneration]);

  const resume = async () => {
    if (resumePending) return;
    setResumePending(true);
    setResumeMessage("");
    const ownerJobId = jobId;
    try {
      const response = await api.resumeJob(jobId);
      if (!mountedRef.current || jobIdRef.current !== ownerJobId) return;
      const next = "operation" in response ? response : await api.getJob(jobId);
      if (!mountedRef.current || jobIdRef.current !== ownerJobId) return;
      setState({ status: "ready", job: next });
      onJobChange?.(next);
      if (!isTerminal(next)) setRecoveryGeneration((value) => value + 1);
    } catch (error) {
      if (!mountedRef.current || jobIdRef.current !== ownerJobId) return;
      setResumeMessage(codeOf(error) === "busy" ? "其他进程正在恢复此作业" : "恢复未能完成，请重试");
    } finally {
      if (mountedRef.current && jobIdRef.current === ownerJobId) setResumePending(false);
    }
  };

  if (state.status === "loading") return <section className="job-progress"><div className="job-progress-state" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={17} />正在恢复作业状态</div></section>;
  if (state.status === "error") return <section className="job-progress"><div className="job-progress-state job-progress-error" role="alert"><AlertCircle aria-hidden="true" size={17} />无法读取作业状态</div></section>;

  const value = progress(state.job);
  const complete = state.job.status === "completed";
  return (
    <section className={`job-progress job-${state.job.status}`} aria-labelledby={`job-${state.job.job_id}`}>
      <div className="job-progress-heading">
        <div>{complete ? <CheckCircle2 aria-hidden="true" size={17} /> : <LoaderCircle className={isTerminal(state.job) ? "" : "loading-icon"} aria-hidden="true" size={17} />}<strong id={`job-${state.job.job_id}`}>{statusLabel(state.job)}</strong></div>
        <code>{state.job.job_id.slice(0, 8)}</code>
      </div>
      {value.total ? <div className="job-progress-meter"><span>已完成 {value.completed} / {value.total} 镜</span><progress max={value.total} value={value.completed}>{value.completed} / {value.total}</progress></div> : null}
      {state.job.error ? <p className="job-error-text">{state.job.error}</p> : null}
      {resumeMessage ? <p className="job-resume-message" role="alert">{resumeMessage}</p> : null}
      {state.job.status === "failed" ? <button className="text-button" type="button" disabled={resumePending} onClick={() => void resume()}><RotateCcw aria-hidden="true" size={15} />{resumePending ? "正在恢复" : "恢复生成"}</button> : null}
    </section>
  );
}
