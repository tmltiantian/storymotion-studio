import { AlertCircle, Check, FlaskConical, LoaderCircle, RefreshCw, Video } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ApiClient } from "../api/client";
import type {
  ConfirmedVideoPreflight,
  JobAccepted,
  VideoGenerationRequest,
  VideoPreflight as VideoPreflightResult,
} from "../api/types";

export type VideoPreflightApi = Pick<
  ApiClient,
  "preflightVideo" | "confirmVideo" | "testVideo" | "generateVideo"
>;

type PreflightState =
  | { status: "loading"; key: string }
  | { status: "ready"; key: string; value: VideoPreflightResult; identity: string }
  | { status: "error"; key: string };

type SubmissionMode = "test" | "batch";

function requestFromPreflight(value: VideoPreflightResult): VideoGenerationRequest {
  const request = { ...value } as Partial<VideoPreflightResult>;
  delete request.ready;
  delete request.blockers;
  return request as VideoGenerationRequest;
}

function requestIdentity(value: VideoGenerationRequest): string {
  return JSON.stringify(value);
}

function errorCode(error: unknown): string {
  return typeof error === "object" && error !== null && "code" in error
    ? String(error.code)
    : "";
}

export function VideoPreflight({
  api,
  projectId,
  shotIds,
  onJobAccepted,
}: {
  api: VideoPreflightApi;
  projectId: string;
  shotIds: string[];
  onJobAccepted?: (job: JobAccepted, mode: SubmissionMode) => void;
}) {
  const shotIdsKey = JSON.stringify(shotIds);
  const selectedShotIds = useMemo(
    () => JSON.parse(shotIdsKey) as string[],
    [shotIdsKey],
  );
  const selectionKey = `${projectId}:${shotIdsKey}`;
  const currentSelectionRef = useRef(selectionKey);
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<PreflightState>({ status: "loading", key: selectionKey });
  const [confirmedIdentity, setConfirmedIdentity] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState<SubmissionMode | null>(null);
  const [message, setMessage] = useState("");
  const loadGenerationRef = useRef(0);
  const confirmGenerationRef = useRef(0);
  const confirmedEnvelopeRef = useRef<ConfirmedVideoPreflight | null>(null);
  const submissionRef = useRef(false);

  useEffect(() => {
    currentSelectionRef.current = selectionKey;
  }, [selectionKey]);

  const clearConfirmation = useCallback(() => {
    confirmedEnvelopeRef.current = null;
    setConfirmedIdentity("");
  }, []);

  useEffect(() => {
    const generation = ++loadGenerationRef.current;
    confirmGenerationRef.current += 1;
    confirmedEnvelopeRef.current = null;
    let active = true;
    queueMicrotask(() => {
      if (!active || currentSelectionRef.current !== selectionKey) return;
      setConfirmedIdentity("");
      setConfirming(false);
      setMessage("");
      setState({ status: "loading", key: selectionKey });
    });
    void api.preflightVideo(projectId, [...selectedShotIds]).then(
      (value) => {
        if (!active || generation !== loadGenerationRef.current || currentSelectionRef.current !== selectionKey) return;
        setState({
          status: "ready",
          key: selectionKey,
          value,
          identity: requestIdentity(requestFromPreflight(value)),
        });
      },
      () => {
        if (active && generation === loadGenerationRef.current && currentSelectionRef.current === selectionKey) {
          setState({ status: "error", key: selectionKey });
        }
      },
    );
    return () => {
      active = false;
      loadGenerationRef.current += 1;
      confirmGenerationRef.current += 1;
      confirmedEnvelopeRef.current = null;
    };
  }, [api, projectId, reload, selectedShotIds, selectionKey]);

  const current = state.key === selectionKey && state.status === "ready" ? state : null;
  const canConfirm = Boolean(current?.value.ready && !confirming && !submitting);
  const isConfirmed = Boolean(
    current &&
    confirmedIdentity === current.identity,
  );
  const testSelectionAllowed = selectedShotIds.length >= 1 && selectedShotIds.length <= 3;

  const confirm = async () => {
    if (!current || !canConfirm) return;
    clearConfirmation();
    const generation = ++confirmGenerationRef.current;
    const ownerSelection = selectionKey;
    const ownerIdentity = current.identity;
    setConfirming(true);
    setMessage("");
    try {
      const envelope = await api.confirmVideo(projectId, [...selectedShotIds]);
      if (
        generation !== confirmGenerationRef.current ||
        currentSelectionRef.current !== ownerSelection ||
        requestIdentity(envelope.generation_request) !== ownerIdentity
      ) return;
      confirmedEnvelopeRef.current = envelope;
      setConfirmedIdentity(ownerIdentity);
    } catch (error) {
      if (generation !== confirmGenerationRef.current || currentSelectionRef.current !== ownerSelection) return;
      clearConfirmation();
      if (errorCode(error) === "stale_confirmation") {
        setMessage("确认信息已过期，正在重新检查。 ");
        setReload((value) => value + 1);
      } else {
        setMessage(errorCode(error) === "busy" ? "项目正在处理，稍后重新检查。" : "费用确认未能完成，请重试。 ");
      }
    } finally {
      if (generation === confirmGenerationRef.current) setConfirming(false);
    }
  };

  const submit = async (mode: SubmissionMode) => {
    const envelope = confirmedEnvelopeRef.current;
    if (!current || !isConfirmed || !envelope || submissionRef.current) return;
    submissionRef.current = true;
    confirmedEnvelopeRef.current = null;
    setConfirmedIdentity("");
    setSubmitting(mode);
    setMessage("");
    try {
      const job = mode === "test"
        ? await api.testVideo(projectId, envelope)
        : await api.generateVideo(projectId, envelope);
      onJobAccepted?.(job, mode);
      setMessage(mode === "test" ? "试生成作业已提交。" : "批量生成作业已提交。");
    } catch (error) {
      if (errorCode(error) === "stale_confirmation") {
        setMessage("生成输入已变化，请重新检查并确认。 ");
        setReload((value) => value + 1);
      } else {
        setMessage(errorCode(error) === "busy" ? "项目正在处理，当前作业结束后重新检查。" : "生成作业未能提交，请重新确认后重试。 ");
      }
    } finally {
      submissionRef.current = false;
      setSubmitting(null);
    }
  };

  if (!shotIds.length) {
    return <section className="video-preflight"><div className="preflight-state" role="status">尚未选择生成镜头</div></section>;
  }
  if (!current) {
    return (
      <section className="video-preflight" aria-label="视频生成预检">
        {state.status === "error" && state.key === selectionKey ? (
          <div className="preflight-state preflight-error" role="alert"><AlertCircle aria-hidden="true" size={17} /><span>无法读取当前生成预检</span><button className="text-button" type="button" onClick={() => setReload((value) => value + 1)}><RefreshCw aria-hidden="true" size={15} />重新检查</button></div>
        ) : (
          <div className="preflight-state" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={17} />正在核对生成输入与费用</div>
        )}
      </section>
    );
  }

  const value = current.value;
  return (
    <section className="video-preflight" aria-labelledby="video-preflight-title">
      <div className="preflight-heading">
        <div><p className="eyebrow">PAID GENERATION</p><h2 id="video-preflight-title">视频生成预检</h2></div>
        <button className="icon-button" type="button" title="重新检查" aria-label="重新检查视频生成预检" disabled={Boolean(submitting || confirming)} onClick={() => setReload((item) => item + 1)}><RefreshCw aria-hidden="true" size={15} /></button>
      </div>
      <dl className="preflight-facts">
        <div><dt>所选镜头</dt><dd>{value.shot_ids.join("、")}</dd></div>
        <div><dt>Provider</dt><dd>{value.provider}</dd></div>
        <div><dt>模型</dt><dd>{value.model}</dd></div>
        <div><dt>分辨率</dt><dd>{value.resolution}</dd></div>
        <div><dt>输出时长</dt><dd>{value.output_seconds.toFixed(2)} 秒</dd></div>
        <div><dt>费用预估</dt><dd>¥{value.estimated_cost_yuan.toFixed(2)}</dd></div>
      </dl>
      <div className="preflight-revisions" aria-label="输入修订">
        {Object.entries(value.revision_hashes).map(([name, revision]) => <span key={name}><strong>{name}</strong><code>{revision.slice(0, 10)}</code></span>)}
      </div>
      {value.blockers.length ? <ul className="preflight-blockers">{value.blockers.map((blocker) => <li key={blocker}><AlertCircle aria-hidden="true" size={15} />{blocker}</li>)}</ul> : null}
      {message ? <div className="preflight-message" role="status">{message}</div> : null}
      <div className="preflight-actions">
        <button className="text-button" type="button" disabled={!canConfirm} onClick={() => void confirm()}><Check aria-hidden="true" size={15} />{confirming ? "正在确认" : "确认费用与输入"}</button>
        <button className="text-button" type="button" title={testSelectionAllowed ? "试生成所选镜头" : "试生成需选择一至三个镜头"} disabled={!isConfirmed || !testSelectionAllowed || Boolean(submitting)} onClick={() => void submit("test")}><FlaskConical aria-hidden="true" size={15} />{submitting === "test" ? "正在提交" : "试生成所选镜头"}</button>
        <button className="command-button" type="button" disabled={!isConfirmed || Boolean(submitting)} onClick={() => void submit("batch")}><Video aria-hidden="true" size={15} />{submitting === "batch" ? "正在提交" : "批量生成所选镜头"}</button>
      </div>
    </section>
  );
}
