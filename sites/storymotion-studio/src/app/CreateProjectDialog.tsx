import { AlertCircle, CheckCircle2, LoaderCircle, X } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

import type {
  ApprovalPreset,
  CreateProjectRequest,
  JobAccepted,
  ProjectMode,
} from "../api/types";

type CreateState =
  | { status: "idle" }
  | { status: "submitting" }
  | { status: "success"; job: JobAccepted }
  | { status: "busy" }
  | { status: "error" };

function isBusyError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "busy"
  );
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(",");

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => !element.hidden && element.getAttribute("aria-hidden") !== "true",
  );
}

export function CreateProjectDialog({
  createProject,
  onClose,
  returnFocusRef,
}: {
  createProject: (
    request: CreateProjectRequest,
    signal?: AbortSignal,
  ) => Promise<JobAccepted>;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const [mode, setMode] = useState<ProjectMode>("original");
  const [state, setState] = useState<CreateState>({ status: "idle" });
  const busy = state.status === "submitting";
  const dialogRef = useRef<HTMLElement>(null);
  const initialFocusRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(true);
  const submissionGeneration = useRef(0);
  const submissionController = useRef<AbortController | null>(null);
  const submittingRef = useRef(false);

  useLayoutEffect(() => {
    mountedRef.current = true;
    const shell = document.querySelector<HTMLElement>(".app-shell");
    const hadInert = shell?.hasAttribute("inert") ?? false;
    const previousAriaHidden = shell?.getAttribute("aria-hidden") ?? null;
    const returnFocusElement = returnFocusRef.current;

    shell?.setAttribute("inert", "");
    shell?.setAttribute("aria-hidden", "true");
    initialFocusRef.current?.focus();

    return () => {
      mountedRef.current = false;
      submissionGeneration.current += 1;
      submissionController.current?.abort();
      if (shell) {
        if (!hadInert) shell.removeAttribute("inert");
        if (previousAriaHidden === null) {
          shell.removeAttribute("aria-hidden");
        } else {
          shell.setAttribute("aria-hidden", previousAriaHidden);
        }
      }
      returnFocusElement?.focus();
    };
  }, [returnFocusRef]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const dialog = dialogRef.current;
      if (!dialog) return;

      if (event.key === "Escape") {
        event.preventDefault();
        if (!submittingRef.current) onClose();
        return;
      }

      if (event.key !== "Tab") return;
      const focusable = focusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    const form = new FormData(event.currentTarget);
    const request: CreateProjectRequest = {
      project_id: String(form.get("project_id") ?? "").trim(),
      title: String(form.get("title") ?? "").trim(),
      mode,
      idea: mode === "original" ? String(form.get("idea") ?? "").trim() : "",
      source_artifact_id:
        mode === "original"
          ? ""
          : String(form.get("source_artifact_id") ?? "").trim(),
      target: {},
      approval_preset: String(
        form.get("approval_preset") ?? "standard",
      ) as ApprovalPreset,
    };

    setState({ status: "submitting" });
    const controller = new AbortController();
    const generation = ++submissionGeneration.current;
    submissionController.current = controller;
    try {
      const job = await createProject(request, controller.signal);
      if (mountedRef.current && generation === submissionGeneration.current) {
        submittingRef.current = false;
        submissionController.current = null;
        setState({ status: "success", job });
      }
    } catch (error) {
      if (mountedRef.current && generation === submissionGeneration.current) {
        submittingRef.current = false;
        submissionController.current = null;
        setState({ status: isBusyError(error) ? "busy" : "error" });
      }
    }
  }

  return createPortal(
    <div className="dialog-backdrop">
      <section
        ref={dialogRef}
        className="create-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        aria-busy={busy}
        tabIndex={-1}
      >
        <div className="dialog-heading">
          <div>
            <p className="eyebrow">NEW PRODUCTION</p>
            <h2 id="create-project-title">新建项目</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label="关闭新建项目"
            title="关闭新建项目"
            onClick={onClose}
            disabled={busy}
          >
            <X aria-hidden="true" size={17} />
          </button>
        </div>

        {state.status === "success" ? (
          <div className="create-success" role="status">
            <CheckCircle2 aria-hidden="true" size={20} />
            <strong>项目已进入创建队列</strong>
            <span>作业 ID</span>
            <code>{state.job.job_id}</code>
            <button className="command-button" type="button" onClick={onClose}>
              完成
            </button>
          </div>
        ) : (
          <form className="create-form" onSubmit={submit}>
            <label>
              <span>项目 ID</span>
              <input
                ref={initialFocusRef}
                name="project_id"
                required
                maxLength={128}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
              />
            </label>
            <label>
              <span>项目标题</span>
              <input name="title" required maxLength={300} />
            </label>
            <label>
              <span>制作模式</span>
              <select
                name="mode"
                value={mode}
                onChange={(event) => setMode(event.target.value as ProjectMode)}
              >
                <option value="original">原创</option>
                <option value="novel">小说改编</option>
                <option value="replica">参考复刻</option>
              </select>
            </label>
            {mode === "original" ? (
              <label className="form-wide">
                <span>创作构想</span>
                <textarea name="idea" required maxLength={200000} rows={4} />
              </label>
            ) : (
              <label className="form-wide">
                <span>来源素材 ID</span>
                <input name="source_artifact_id" required maxLength={128} />
              </label>
            )}
            <label>
              <span>审批模板</span>
              <select name="approval_preset" defaultValue="standard">
                <option value="quick">快速</option>
                <option value="standard">标准</option>
                <option value="strict">严格</option>
              </select>
            </label>

            {state.status === "busy" && (
              <div className="form-message form-busy" role="alert">
                <AlertCircle aria-hidden="true" size={16} />
                <span>项目正在处理，请稍后再次创建。</span>
              </div>
            )}
            {state.status === "error" && (
              <div className="form-message form-error" role="alert">
                <AlertCircle aria-hidden="true" size={16} />
                <span>无法创建项目，请检查制作服务后重试。</span>
              </div>
            )}

            <div className="dialog-actions">
              <button className="text-button" type="button" onClick={onClose} disabled={busy}>
                取消
              </button>
              <button className="command-button" type="submit" disabled={busy}>
                {busy ? (
                  <LoaderCircle className="loading-icon" aria-hidden="true" size={16} />
                ) : null}
                {busy ? "正在创建" : "创建项目"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>,
    document.body,
  );
}
