import { AlertCircle, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { useState, type FormEvent } from "react";

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

export function CreateProjectDialog({
  createProject,
  onClose,
}: {
  createProject: (request: CreateProjectRequest) => Promise<JobAccepted>;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<ProjectMode>("original");
  const [state, setState] = useState<CreateState>({ status: "idle" });
  const busy = state.status === "submitting";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
    try {
      const job = await createProject(request);
      setState({ status: "success", job });
    } catch (error) {
      setState({ status: isBusyError(error) ? "busy" : "error" });
    }
  }

  return (
    <div className="dialog-backdrop">
      <section
        className="create-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        aria-busy={busy}
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
                name="project_id"
                required
                maxLength={128}
                pattern="[A-Za-z0-9][A-Za-z0-9._:-]*"
                autoFocus
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
    </div>
  );
}
