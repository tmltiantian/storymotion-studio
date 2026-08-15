import { AlertCircle, LoaderCircle, X } from "lucide-react";
import {
  type RefObject,
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import type { ApiClient } from "../api/client";
import type {
  ImpactPlan,
  ImpactRequest,
  ProjectDetail,
  StageName,
} from "../api/types";
import { stageLabel } from "./StageRail";
import { useContainedSurface } from "./useContainedSurface";

type ImpactApi = Pick<ApiClient, "previewImpact" | "applyImpact">;

type ImpactState =
  | { status: "loading" }
  | { status: "ready"; plan: ImpactPlan }
  | { status: "applying"; plan: ImpactPlan }
  | { status: "preview_error"; message: string }
  | { status: "apply_error"; plan: ImpactPlan; message: string };

const STAGE_NAMES = new Set<StageName>([
  "concept",
  "script",
  "storyboard",
  "assets",
  "audio",
  "video",
  "edit",
  "eval",
  "deliver",
]);
const SHA256 = /^[0-9a-f]{64}$/;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && item.length > 0);
}

function sameRequest(left: ImpactPlan["request"], right: ImpactRequest): boolean {
  return (
    left.stage === right.stage &&
    left.scope === right.scope &&
    left.subtitle_style === Boolean(right.subtitle_style) &&
    JSON.stringify(left.dialogue_ids) === JSON.stringify(right.dialogue_ids ?? []) &&
    JSON.stringify(left.character_ids) === JSON.stringify(right.character_ids ?? []) &&
    JSON.stringify(left.shot_ids) === JSON.stringify(right.shot_ids ?? [])
  );
}

function validPlan(value: unknown, request: ImpactRequest): value is ImpactPlan {
  if (!isObject(value)) return false;
  if (value.schema_version !== "motion-comic-factory.impact-plan.v1") return false;
  if (typeof value.plan_id !== "string" || !SHA256.test(value.plan_id)) return false;
  if (typeof value.package_sha256 !== "string" || !SHA256.test(value.package_sha256)) return false;
  if (!isObject(value.request) || !sameRequest(value.request as unknown as ImpactPlan["request"], request)) return false;
  if (!Array.isArray(value.entries) || !value.entries.every((entry) => (
    isObject(entry) &&
    typeof entry.stage === "string" &&
    STAGE_NAMES.has(entry.stage as StageName) &&
    stringArray(entry.item_ids)
  ))) return false;
  return stringArray(value.preserved_artifacts) || (
    Array.isArray(value.preserved_artifacts) && value.preserved_artifacts.length === 0
  );
}

function errorCode(error: unknown): string {
  return isObject(error) && typeof error.code === "string" ? error.code : "";
}

function impactError(error: unknown, phase: "preview" | "apply"): string {
  const code = errorCode(error);
  if (code === "busy") return "项目正在处理，当前操作完成后再试。";
  if (code === "stale_confirmation" || code === "invalid_request") {
    return phase === "apply"
      ? "影响计划已过期或发生变化，请关闭后重新预览。"
      : "影响预览无法验证，请重新选择修改范围。";
  }
  return phase === "apply"
    ? "无法应用返修计划，请检查制作服务后重试。"
    : "无法生成影响预览，请检查修改范围后重试。";
}

export function ImpactDialog({
  api,
  projectId,
  request,
  issueLabel,
  description,
  returnFocusRef,
  onClose,
  onApplied,
}: {
  api: ImpactApi;
  projectId: string;
  request: ImpactRequest;
  issueLabel: string;
  description: string;
  returnFocusRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onApplied: (project: ProjectDetail) => Promise<void>;
}) {
  const [state, setState] = useState<ImpactState>({ status: "loading" });
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    if (!busyRef.current) onClose();
  }, [onClose]);

  useContainedSurface({
    surfaceRef: dialogRef,
    initialFocusRef: closeRef,
    returnFocusRef,
    busyRef,
    onClose: close,
  });

  useLayoutEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    const generation = ++generationRef.current;
    controllerRef.current = controller;
    void api.previewImpact(projectId, request, controller.signal).then(
      (plan) => {
        if (!mountedRef.current || generation !== generationRef.current) return;
        controllerRef.current = null;
        if (!validPlan(plan, request)) {
          setState({ status: "preview_error", message: "影响预览无法验证，请重新选择修改范围。" });
          return;
        }
        setState({ status: "ready", plan });
      },
      (error: unknown) => {
        if (!mountedRef.current || generation !== generationRef.current) return;
        controllerRef.current = null;
        setState({ status: "preview_error", message: impactError(error, "preview") });
      },
    );
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      controller.abort();
      controllerRef.current?.abort();
    };
  }, [api, projectId, request]);

  async function applyPlan(plan: ImpactPlan) {
    if (busyRef.current) return;
    busyRef.current = true;
    const controller = new AbortController();
    const generation = ++generationRef.current;
    controllerRef.current = controller;
    setState({ status: "applying", plan });
    try {
      const project = await api.applyImpact(projectId, plan.plan_id, controller.signal);
      if (!mountedRef.current || generation !== generationRef.current) return;
      await onApplied(project);
      if (!mountedRef.current || generation !== generationRef.current) return;
      busyRef.current = false;
      controllerRef.current = null;
      onClose();
    } catch (error) {
      if (!mountedRef.current || generation !== generationRef.current) return;
      busyRef.current = false;
      controllerRef.current = null;
      setState({ status: "apply_error", plan, message: impactError(error, "apply") });
    }
  }

  const plan = state.status === "ready" || state.status === "applying" || state.status === "apply_error"
    ? state.plan
    : null;
  const videoCount = plan?.entries.find((entry) => entry.stage === "video")?.item_ids.length ?? 0;
  const audioCount = plan?.entries.find((entry) => entry.stage === "audio")?.item_ids.length ?? 0;
  const preservedCount = plan?.preserved_artifacts.length ?? 0;
  const busy = state.status === "applying";

  return createPortal(
    <div className="dialog-backdrop impact-backdrop">
      <section
        ref={dialogRef}
        className="impact-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="impact-dialog-title"
        aria-busy={state.status === "loading" || busy}
        tabIndex={-1}
      >
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">SCOPED IMPACT</p>
            <h2 id="impact-dialog-title">修改影响预览</h2>
          </div>
          <button
            ref={closeRef}
            className="icon-button"
            type="button"
            aria-label="关闭修改影响预览"
            title="关闭修改影响预览"
            onClick={close}
            disabled={busy}
          >
            <X aria-hidden="true" size={17} />
          </button>
        </div>

        <div className="impact-body">
          <div className="impact-issue">
            <strong>{issueLabel}</strong>
            <span>{description}</span>
          </div>

          {state.status === "loading" ? (
            <div className="impact-loading" role="status">
              <LoaderCircle className="loading-icon" aria-hidden="true" size={18} />
              <span>正在计算受影响范围</span>
            </div>
          ) : null}

          {state.status === "preview_error" || state.status === "apply_error" ? (
            <div className="form-message form-error" role="alert">
              <AlertCircle aria-hidden="true" size={16} />
              <span>{state.message}</span>
            </div>
          ) : null}

          {plan ? (
            <>
              <div className="impact-counts" aria-label="返修数量">
                <div><strong>将重做 {videoCount} 个视频镜头</strong><span>视频生成</span></div>
                <div><strong>将重做 {audioCount} 条音频</strong><span>音频生成</span></div>
                <div><strong>其他 {preservedCount} 个镜头继续复用</strong><span>保留现有素材</span></div>
              </div>
              <div className="impact-estimate">费用预估：后端未提供</div>
              <section className="impact-entries" aria-labelledby="impact-entries-title">
                <h3 id="impact-entries-title">受影响阶段与项目</h3>
                <ul>
                  {plan.entries.map((entry) => (
                    <li key={`${entry.stage}-${entry.item_ids.join("-")}`}>
                      <strong>{stageLabel(entry.stage)}</strong>
                      <span>{stageLabel(entry.stage)} · {entry.item_ids.join("、")}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : null}
        </div>

        <div className="dialog-actions impact-actions">
          <button className="text-button" type="button" onClick={close} disabled={busy}>取消</button>
          {plan ? (
            <button
              className="command-button"
              type="button"
              onClick={() => void applyPlan(plan)}
              disabled={busy}
            >
              {busy ? <LoaderCircle className="loading-icon" aria-hidden="true" size={16} /> : null}
              {busy ? "正在应用" : "应用返修计划"}
            </button>
          ) : null}
        </div>
      </section>
    </div>,
    document.body,
  );
}
