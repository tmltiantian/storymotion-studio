import { FileDown, FileWarning } from "lucide-react";
import { useRef } from "react";

import type { Artifact, ArtifactKind, StageName } from "../api/types";
import { AudioViewer } from "./AudioViewer";
import { EvalViewer } from "./EvalViewer";
import { ImageViewer } from "./ImageViewer";
import { TextViewer } from "./TextViewer";
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

function ArtifactFrame({ artifact, children }: { artifact: Artifact; children: React.ReactNode }) {
  return (
    <figure className="stage-viewer-item">
      {children}
      <figcaption><strong>{artifact.name}</strong><code>{artifact.media_type}</code></figcaption>
    </figure>
  );
}

function FileViewer({ artifact }: { artifact: Artifact }) {
  const url = authorizedArtifactUrl(artifact);
  if (!url) return <div className="artifact-file artifact-file-disabled"><FileWarning aria-hidden="true" size={22} /><span>{artifact.name} 无法安全打开</span></div>;
  return <a className="artifact-file" href={url} target="_blank" rel="noreferrer" aria-label={`打开 ${artifact.name}`}><FileDown aria-hidden="true" size={22} /><span>打开或下载成果</span></a>;
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
  const videoArtifacts = artifacts.filter((artifact) => viewerKind(stage, artifact) === "video");
  const firstVideoId = videoArtifacts[0]?.artifact_id;
  return (
    <div className="stage-viewer-list">
      {artifacts.map((artifact) => {
        const kind = viewerKind(stage, artifact);
        if (kind === "video") {
          if (artifact.artifact_id !== firstVideoId) return null;
          return <ArtifactFrame artifact={artifact} key="stage-video-viewer"><VideoViewer artifacts={videoArtifacts} onIssueAtTime={onIssueAtTime} /></ArtifactFrame>;
        }
        if (kind === "image") return <ArtifactFrame artifact={artifact} key={artifact.artifact_id}><ImageViewer artifact={artifact} /></ArtifactFrame>;
        if (kind === "audio") return (
          <ArtifactFrame artifact={artifact} key={artifact.artifact_id}>
            <AudioViewer artifact={artifact} onActivate={(id, media) => {
              if (activeAudio.current && activeAudio.current.id !== id) activeAudio.current.media.pause();
              activeAudio.current = { id, media };
            }} />
          </ArtifactFrame>
        );
        if (kind === "eval") return <ArtifactFrame artifact={artifact} key={artifact.artifact_id}><EvalViewer artifact={artifact} /></ArtifactFrame>;
        if (kind === "text") return <ArtifactFrame artifact={artifact} key={artifact.artifact_id}><TextViewer artifact={artifact} /></ArtifactFrame>;
        return <ArtifactFrame artifact={artifact} key={artifact.artifact_id}><FileViewer artifact={artifact} /></ArtifactFrame>;
      })}
    </div>
  );
}
