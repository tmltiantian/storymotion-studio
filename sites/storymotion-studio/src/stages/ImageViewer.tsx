import { Maximize2 } from "lucide-react";

import type { Artifact } from "../api/types";
import { authorizedArtifactUrl } from "./viewerUtils";

export function ImageViewer({ artifact }: { artifact: Artifact }) {
  const url = authorizedArtifactUrl(artifact);
  if (!url) return <div className="viewer-state viewer-state-error">{artifact.name} 无法安全打开</div>;
  const ratio = artifact.viewer?.width && artifact.viewer?.height
    ? `${artifact.viewer.width} / ${artifact.viewer.height}`
    : "16 / 9";
  return (
    <div className="image-viewer">
      <div className="image-viewer-frame" style={{ aspectRatio: ratio }}>
        <img src={url} alt={artifact.name} />
      </div>
      <a className="viewer-open-link" href={url} target="_blank" rel="noreferrer">
        <Maximize2 aria-hidden="true" size={15} />查看原图
      </a>
    </div>
  );
}
