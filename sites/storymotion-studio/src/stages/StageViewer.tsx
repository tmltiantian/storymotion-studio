import { FileDown, FileWarning } from "lucide-react";
import { useCallback, useRef } from "react";

import type { Artifact, ArtifactKind, StageName } from "../api/types";
import { AudioViewer } from "./AudioViewer";
import { ImageViewer } from "./ImageViewer";
import { VideoViewer } from "./VideoViewer";
import { authorizedArtifactUrl } from "./viewerUtils";

const MIME_REGISTRY: ReadonlyArray<[RegExp, ArtifactKind]> = [
  [/^image\//, "image"],
  [/^audio\//, "audio"],
  [/^video\//, "video"],
  [/^(text\/|application\/(json|x-subrip))/, "text"],
];

const NAME_REGISTRY: ReadonlyArray<[RegExp, ArtifactKind]> = [
  [/\.(md|txt|srt|json)$/i, "text"],
  [/\.(png|jpe?g|gif|webp)$/i, "image"],
  [/\.(m4a|mp3|wav|aac|flac|ogg)$/i, "audio"],
  [/\.(mp4|mov|webm)$/i, "video"],
];

function viewerKind(stage: StageName, artifact: Artifact): ArtifactKind {
  if (artifact.kind && ["text", "image", "audio", "video", "eval", "file"].includes(artifact.kind)) {
    return artifact.kind;
  }
  if (stage === "eval" && /json/i.test(artifact.media_type)) return "eval";
  const mime = artifact.media_type.split(";", 1)[0].trim().toLowerCase();
  for (const [pattern, kind] of MIME_REGISTRY) if (pattern.test(mime)) return kind;
  for (const [pattern, kind] of NAME_REGISTRY) if (pattern.test(artifact.name)) return kind;
  return "file";
}

const STAGE_LABELS: Record<StageName, string> = {
  concept: "创意方案",
  script: "剧本内容",
  storyboard: "分镜内容",
  assets: "制作素材",
  audio: "配音成果",
  video: "视频片段",
  edit: "剪辑成片",
  eval: "检查结果",
  deliver: "交付成片",
};

function formatDuration(seconds: number): string {
  return `${Number(seconds.toFixed(1))} 秒`;
}

function creatorLabel(stage: StageName, artifact: Artifact, candidatePosition = 1): string {
  const kind = viewerKind(stage, artifact);
  if (kind === "video") {
    const shotNumber = artifact.viewer?.shot_id?.match(/\d+/)?.[0];
    return shotNumber ? `第 ${Number(shotNumber)} 镜 · 候选 ${candidatePosition}` : "视频片段";
  }
  if (kind === "audio") {
    const dialogues = artifact.viewer?.dialogues ?? [];
    const speaker = dialogues[0]?.speaker;
    if (!speaker) return "完整配音";
    const start = Math.min(...dialogues.map((dialogue) => dialogue.start_seconds));
    const end = Math.max(...dialogues.map((dialogue) => dialogue.end_seconds));
    return `${speaker}配音${end > start ? ` · ${formatDuration(end - start)}` : ""}`;
  }
  if (kind === "image") {
    const { width, height } = artifact.viewer ?? {};
    return width && height ? `角色或场景参考 · ${width} × ${height}` : "角色或场景参考";
  }
  return STAGE_LABELS[stage];
}

function ArtifactFrame({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <figure className="stage-viewer-item">
      {children}
      {label ? <figcaption><strong>{label}</strong></figcaption> : null}
    </figure>
  );
}

function SummaryOnlyViewer() {
  return <div className="viewer-state">本成果已整理到阶段摘要</div>;
}

function FileViewer({ artifact }: { artifact: Artifact }) {
  const url = authorizedArtifactUrl(artifact);
  if (!url) return <div className="artifact-file artifact-file-disabled"><FileWarning aria-hidden="true" size={22} /><span>本成果暂无法打开</span></div>;
  return <a className="artifact-file" href={url} target="_blank" rel="noreferrer" aria-label="打开或下载成果"><FileDown aria-hidden="true" size={22} /><span>打开或下载成果</span></a>;
}

export function StageViewer({
  stage,
  artifacts,
  onIssueAtTime,
}: {
  stage: StageName;
  artifacts: Artifact[];
  onIssueAtTime?: (time: number, artifact: Artifact) => void;
}) {
  const activeAudio = useRef<{ id: string; media: HTMLAudioElement } | null>(null);
  const activateAudio = useCallback((id: string, media: HTMLAudioElement) => {
    if (activeAudio.current && activeAudio.current.media !== media) {
      activeAudio.current.media.pause();
    }
    activeAudio.current = { id, media };
  }, []);
  const releaseAudio = useCallback((id: string, media: HTMLAudioElement) => {
    if (activeAudio.current?.id === id && activeAudio.current.media === media) {
      activeAudio.current = null;
    }
  }, []);
  const renderedVideoShots = new Set<string>();
  return (
    <div className="stage-viewer-list">
      {artifacts.map((artifact) => {
        const kind = viewerKind(stage, artifact);
        if (kind === "video") {
          const shotId = artifact.viewer?.shot_id?.trim();
          if (shotId) {
            if (renderedVideoShots.has(shotId)) return null;
            renderedVideoShots.add(shotId);
          }
          const candidates = shotId
            ? artifacts.filter((item) => viewerKind(stage, item) === "video" && item.viewer?.shot_id?.trim() === shotId)
            : [artifact];
          const displayCandidates = candidates.map((candidate, index) => ({
            ...candidate,
            name: `候选 ${index + 1}`,
          }));
          const candidateLabels = new Map(candidates.map((candidate, index) => [
            candidate.artifact_id,
            creatorLabel(stage, candidate, index + 1),
          ]));
          return (
            <ArtifactFrame key={`stage-video-${shotId || artifact.artifact_id}`}>
              <VideoViewer
                artifacts={displayCandidates}
                creatorLabel={(selected) => candidateLabels.get(selected.artifact_id) ?? "视频片段"}
                onIssueAtTime={onIssueAtTime ? (time, selected) => {
                  const original = candidates.find((candidate) => candidate.artifact_id === selected.artifact_id) ?? selected;
                  onIssueAtTime(time, original);
                } : undefined}
              />
            </ArtifactFrame>
          );
        }
        const label = creatorLabel(stage, artifact);
        const displayArtifact = { ...artifact, name: label };
        if (kind === "image") return <ArtifactFrame label={label} key={artifact.artifact_id}><ImageViewer artifact={displayArtifact} /></ArtifactFrame>;
        if (kind === "audio") return (
          <ArtifactFrame label={label} key={artifact.artifact_id}>
            <AudioViewer artifact={displayArtifact} onActivate={activateAudio} onRelease={releaseAudio} />
          </ArtifactFrame>
        );
        if (kind === "eval" || kind === "text") return <ArtifactFrame label={label} key={artifact.artifact_id}><SummaryOnlyViewer /></ArtifactFrame>;
        return <ArtifactFrame label={label} key={artifact.artifact_id}><FileViewer artifact={artifact} /></ArtifactFrame>;
      })}
    </div>
  );
}
