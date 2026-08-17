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
  if (job.operation === "run_stage") {
    if (job.status === "completed") return "阶段运行完成";
    if (job.status === "failed") return "阶段运行失败";
    if (job.status === "cancelled") return "阶段运行已取消";
    if (job.status === "running") return "阶段运行中";
    return "等待阶段运行";
  }
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
  allowResume = true,
}: {
  api: JobProgressApi;
  jobId: string;
  connect?: JobEventConnector;
  onJobChange?: (job: JobDetail) => void;
  allowResume?: boolean;
}) {
  const [state, setState] = useState<ViewState>({ status: "loading" });
  const [resumePending, setResumePending] = useState(false);
  const [resumeRecoveryJobId, setResumeRecoveryJobId] = useState("");
  const [resumeMessage, setResumeMessage] = useState("");
  const [recoveryGeneration, setRecoveryGeneration] = useState(0);
  const mountedRef = useRef(false);
  const jobIdRef = useRef(jobId);
  const lastJobRef = useRef<JobDetail | null>(null);
  const resumeRecoveryRef = useRef(false);
  const resumeRecovering = resumeRecoveryJobId === jobId;

  useEffect(() => {
    jobIdRef.current = jobId;
    lastJobRef.current = null;
    resumeRecoveryRef.current = false;
  }, [jobId]);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    let active = true;
    let silenceTimer: number | null = null;
    let lastEventId = String(lastJobRef.current?.last_event_sequence ?? "");
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
        lastJobRef.current = next;
        resumeRecoveryRef.current = false;
        setResumeRecoveryJobId("");
        setResumeMessage("");
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
        return null;
      }
    };

    const initialRefresh = async (): Promise<JobDetail | null> => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const next = await refresh();
        if (next || !active || controller.signal.aborted) return next;
        if (attempt < 2) {
          await abortableDelay(250 * (2 ** attempt), controller.signal);
        }
      }
      if (active && !controller.signal.aborted && lastJobRef.current === null) {
        setState({ status: "error" });
      }
      return null;
    };

    const armSilenceFallback = () => {
      clearSilenceTimer();
      if (!active || controller.signal.aborted) return;
      silenceTimer = window.setTimeout(() => {
        silenceTimer = null;
        void refresh().then((next) => {
          if (!next || !isTerminal(next)) armSilenceFallback();
        });
      }, 5000);
    };

    const run = async () => {
      if (lastJobRef.current === null) setState({ status: "loading" });
      const persisted = await initialRefresh();
      if (controller.signal.aborted) return;
      if (!persisted && !resumeRecoveryRef.current) return;
      if (persisted && isTerminal(persisted)) return;
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
            if (next && isTerminal(next)) return;
            armSilenceFallback();
          }
          if (!active || controller.signal.aborted) return;
          const closedState = await refresh();
          if (closedState && isTerminal(closedState)) return;
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
    if (resumePending || resumeRecovering) return;
    setResumePending(true);
    setResumeMessage("");
    const ownerJobId = jobId;
    try {
      const response = await api.resumeJob(jobId);
      if (!mountedRef.current || jobIdRef.current !== ownerJobId) return;
      if ("operation" in response) {
        lastJobRef.current = response;
        setState({ status: "ready", job: response });
        onJobChange?.(response);
        if (!isTerminal(response)) setRecoveryGeneration((value) => value + 1);
      } else {
        resumeRecoveryRef.current = true;
        setResumeRecoveryJobId(ownerJobId);
        setRecoveryGeneration((value) => value + 1);
      }
    } catch (error) {
      if (!mountedRef.current || jobIdRef.current !== ownerJobId) return;
      setResumeMessage(codeOf(error) === "busy" ? "其他进程正在恢复此作业" : "恢复未能完成，请重试");
    } finally {
      if (mountedRef.current && jobIdRef.current === ownerJobId) setResumePending(false);
    }
  };

  if (state.status === "loading") return <section className="job-progress"><div className="job-progress-state" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={17} />正在恢复作业状态</div></section>;
  if (state.status === "error") return <section className="job-progress"><div className="job-progress-state job-progress-error" role="alert"><AlertCircle aria-hidden="true" size={17} />无法读取作业状态<button className="text-button" type="button" onClick={() => setRecoveryGeneration((value) => value + 1)}><RotateCcw aria-hidden="true" size={15} />重新读取作业</button></div></section>;

  const value = progress(state.job);
  const complete = state.job.status === "completed";
  return (
    <section className={`job-progress job-${state.job.status}`} aria-label="作业进度">
      <div className="job-progress-heading">
        <div>{complete ? <CheckCircle2 aria-hidden="true" size={17} /> : <LoaderCircle className={isTerminal(state.job) ? "" : "loading-icon"} aria-hidden="true" size={17} />}<strong>{statusLabel(state.job)}</strong></div>
      </div>
      {value.total ? <div className="job-progress-meter"><span>已完成 {value.completed} / {value.total} 镜</span><progress max={value.total} value={value.completed}>{value.completed} / {value.total}</progress></div> : null}
      {state.job.error ? <p className="job-error-text">{state.job.operation === "run_stage" ? "阶段运行遇到问题，请恢复后重试。" : "生成过程中遇到问题，请恢复后重试。"}</p> : null}
      {resumeRecovering ? <p className="job-resume-message" role="status">正在恢复作业状态</p> : null}
      {resumeMessage ? <p className="job-resume-message" role="alert">{resumeMessage}</p> : null}
      {state.job.status === "failed" && allowResume ? <button className="text-button" type="button" disabled={resumePending || resumeRecovering} onClick={() => void resume()}><RotateCcw aria-hidden="true" size={15} />{resumePending || resumeRecovering ? "正在恢复" : "恢复生成"}</button> : null}
    </section>
  );
}
