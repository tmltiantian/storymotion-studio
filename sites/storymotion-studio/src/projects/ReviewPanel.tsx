import { Check, ChevronDown, LoaderCircle, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type {
  ApproveStageRequest,
  ImpactRequest,
  RequestChangesRequest,
  StageDetail,
} from "../api/types";
import { executionLabels, reviewLabels } from "./StageRail";

type IssueCategory =
  | "dialogue"
  | "character"
  | "action"
  | "subtitle"
  | "overall";

type ScopedCategory = Exclude<IssueCategory, "overall">;

export interface ReviewIssueDraft {
  key: string;
  shotId: string;
  artifactId: string;
  timeSeconds: number;
}

const issueCategories: ReadonlyArray<{
  id: IssueCategory;
  label: string;
}> = [
  { id: "dialogue", label: "对白内容有误" },
  { id: "character", label: "角色资产有误" },
  { id: "action", label: "动作不连贯" },
  { id: "subtitle", label: "字幕样式有误" },
  { id: "overall", label: "整体成果需调整" },
];

const scopedCategories: Record<ScopedCategory, {
  stage: ImpactRequest["stage"];
  scope: ImpactRequest["scope"];
  itemLabel?: string;
}> = {
  dialogue: { stage: "script", scope: "dialogue", itemLabel: "对白 ID" },
  character: { stage: "assets", scope: "character", itemLabel: "角色 ID" },
  action: { stage: "storyboard", scope: "shot", itemLabel: "镜头 ID" },
  subtitle: { stage: "edit", scope: "subtitle_style" },
};

function impactRequest(category: ScopedCategory): ImpactRequest {
  const config = scopedCategories[category];
  return {
    stage: config.stage,
    scope: config.scope,
    dialogue_ids: [],
    character_ids: [],
    shot_ids: [],
    subtitle_style: category === "subtitle",
  };
}

export function ReviewPanel({
  stage,
  pending,
  onApprove,
  onRequestStageChanges,
  onOpenImpact,
  issueDraft,
}: {
  stage: StageDetail;
  pending: boolean;
  onApprove: (request: ApproveStageRequest) => void;
  onRequestStageChanges: (
    request: RequestChangesRequest,
  ) => boolean | void | Promise<boolean | void>;
  onOpenImpact: (
    request: ImpactRequest,
    issueLabel: string,
    description: string,
    trigger: HTMLButtonElement,
  ) => void;
  issueDraft?: ReviewIssueDraft | null;
}) {
  const [approvalNote, setApprovalNote] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>(
    () => stage.artifacts.map((artifact) => artifact.artifact_id),
  );
  const [changesOpen, setChangesOpen] = useState(false);
  const [category, setCategory] = useState<IssueCategory | "">("");
  const [description, setDescription] = useState("");
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!issueDraft) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setChangesOpen(true);
      setCategory("overall");
      setDescription(
        `镜头 ${issueDraft.shotId}\n候选成果 ${issueDraft.artifactId}\n时间码 ${String(issueDraft.timeSeconds)} 秒\n`,
      );
      queueMicrotask(() => descriptionRef.current?.focus());
    });
    return () => {
      active = false;
    };
  }, [issueDraft]);

  const reviewable =
    stage.execution_state === "passed" &&
    stage.review_state === "awaiting_review" &&
    stage.revision > 0;
  const canApprove =
    reviewable &&
    approvalNote.trim().length > 0 &&
    selectedEvidence.length > 0 &&
    !pending;
  const scoped = category && category !== "overall" ? scopedCategories[category] : null;
  const requiredFields =
    reviewable &&
    Boolean(category) &&
    description.trim().length > 0 &&
    !scoped?.itemLabel &&
    (!scoped || scoped.stage === stage.stage) &&
    !pending;
  const selectedLabel = issueCategories.find((item) => item.id === category)?.label ?? "";

  function toggleEvidence(artifactId: string) {
    setSelectedEvidence((current) =>
      current.includes(artifactId)
        ? current.filter((item) => item !== artifactId)
        : [...current, artifactId],
    );
  }

  return (
    <aside className="review-panel" aria-labelledby="review-panel-title">
      <div className="review-heading">
        <div>
          <p className="eyebrow">REVIEW</p>
          <h2 id="review-panel-title">审核检查</h2>
        </div>
        <code>修订 {stage.revision || "-"}</code>
      </div>

      <dl className="stage-state-pair">
        <div className={`execution-state state-${stage.execution_state}`}>
          <dt>执行状态</dt>
          <dd>{executionLabels[stage.execution_state]}</dd>
        </div>
        <div className={`review-state state-${stage.review_state}`}>
          <dt>审核状态</dt>
          <dd>{reviewLabels[stage.review_state]}</dd>
        </div>
      </dl>

      {stage.blocked_reasons.length > 0 ? (
        <div className="review-reasons">
          <strong>当前阻塞</strong>
          <ul>{stage.blocked_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      ) : null}

      <div className="approval-form">
        <label>
          <span>确认说明</span>
          <textarea
            value={approvalNote}
            onChange={(event) => setApprovalNote(event.target.value)}
            rows={3}
            maxLength={10_000}
            disabled={pending || !reviewable}
          />
        </label>
        <fieldset disabled={pending || !reviewable}>
          <legend>确认依据</legend>
          {stage.artifacts.length > 0 ? stage.artifacts.map((artifact) => (
            <label key={artifact.artifact_id} className="evidence-option">
              <input
                type="checkbox"
                checked={selectedEvidence.includes(artifact.artifact_id)}
                onChange={() => toggleEvidence(artifact.artifact_id)}
              />
              <span>{artifact.name}</span>
            </label>
          )) : <span className="review-unavailable">当前修订没有可绑定的确认依据</span>}
        </fieldset>
        <button
          className="command-button approval-button"
          type="button"
          disabled={!canApprove}
          onClick={() => onApprove({
            revision: stage.revision,
            note: approvalNote.trim(),
            evidence_artifact_ids: selectedEvidence,
          })}
        >
          {pending ? <LoaderCircle className="loading-icon" aria-hidden="true" size={16} /> : <Check aria-hidden="true" size={16} />}
          确认通过
        </button>
      </div>

      <div className="changes-section">
        <button
          className="changes-toggle"
          type="button"
          aria-expanded={changesOpen}
          aria-controls="change-request-form"
          disabled={pending || !reviewable}
          onClick={() => setChangesOpen((open) => !open)}
        >
          <RotateCcw aria-hidden="true" size={16} />
          退回修改
          <ChevronDown aria-hidden="true" size={15} />
        </button>

        {changesOpen ? (
          <div id="change-request-form" className="change-request-form">
            <fieldset disabled={pending}>
              <legend>问题类别</legend>
              <div className="issue-options">
                {issueCategories.map((item) => (
                  <label key={item.id} className={
                    item.id !== "overall" && (
                      scopedCategories[item.id].itemLabel ||
                      scopedCategories[item.id].stage !== stage.stage
                    )
                      ? "issue-option-disabled"
                      : undefined
                  }>
                    <input
                      type="radio"
                      name="issue-category"
                      value={item.id}
                      checked={category === item.id}
                      onChange={() => {
                        setCategory(item.id);
                      }}
                      disabled={
                        pending ||
                        (item.id !== "overall" && (
                          Boolean(scopedCategories[item.id].itemLabel) ||
                          scopedCategories[item.id].stage !== stage.stage
                        ))
                      }
                    />
                    <span>
                      {item.label}
                      {item.id !== "overall" && scopedCategories[item.id].itemLabel
                        ? "（缺少可选项目 ID）"
                        : ""}
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label>
              <span>问题说明</span>
              <textarea
                ref={descriptionRef}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={4}
                maxLength={10_000}
                disabled={pending}
              />
            </label>

            {category === "overall" ? (
              <button
                className="command-button change-command"
                type="button"
                disabled={!requiredFields}
                onClick={() => void onRequestStageChanges({
                  revision: stage.revision,
                  reason: `[${selectedLabel}] ${description.trim()}`,
                })}
              >
                退回整阶段
              </button>
            ) : (
              <button
                className="command-button change-command"
                type="button"
                disabled={!requiredFields || !category}
                onClick={(event) => {
                  if (!category) return;
                  onOpenImpact(
                    impactRequest(category),
                    selectedLabel,
                    description.trim(),
                    event.currentTarget,
                  );
                }}
              >
                查看影响
              </button>
            )}
          </div>
        ) : null}
      </div>
    </aside>
  );
}
