import { AlertCircle, ArrowLeft, Download, FileCheck2, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import type { Artifact, WorkDetail, WorkVersion } from "../api/types";
import { authorizedArtifactUrl } from "../stages/viewerUtils";


export interface WorkDetailPageApi {
  getWork(workId: string, signal?: AbortSignal): Promise<WorkDetail>;
}

type DetailState =
  | { status: "loading"; workId: string }
  | { status: "ready"; workId: string; work: WorkDetail }
  | { status: "error"; workId: string };


function MediaPreview({ artifact }: { artifact: Artifact }) {
  const url = authorizedArtifactUrl(artifact);
  if (!url) return <div className="work-media-empty">媒体地址不可用</div>;
  const kind = artifact.kind ?? "file";
  if (kind === "video") {
    const ratio = artifact.viewer?.width && artifact.viewer.height
      ? `${artifact.viewer.width} / ${artifact.viewer.height}`
      : "9 / 16";
    return (
      <div className="work-media-frame" style={{ aspectRatio: ratio }}>
        <video data-testid="work-video" src={url} controls playsInline preload="metadata"><track kind="captions" /></video>
      </div>
    );
  }
  if (kind === "audio") return <audio className="work-audio" src={url} controls preload="metadata" />;
  if (kind === "image") return <img className="work-image" src={url} alt={artifact.name} />;
  return <div className="work-media-empty">这个文件仅支持下载查看</div>;
}


function RightsWarning({ artifact }: { artifact: Artifact }) {
  if (artifact.rights?.redistribution_status !== "unverified") return null;
  return (
    <div className="state-row state-warning work-rights-warning" role="alert">
      <AlertCircle aria-hidden="true" size={18} />
      <div>
        <strong>发布权利尚未核验</strong>
        <span>{artifact.rights.distribution_warning || "公开发布或再分发前需要完成人工权利审核。"}</span>
      </div>
    </div>
  );
}


function authorizedDownloadUrl(artifact: Artifact): string | null {
  const expected = `/api/download/${encodeURIComponent(artifact.artifact_id)}`;
  return artifact.download_url === expected ? expected : null;
}


function Evidence({ version }: { version: WorkVersion }) {
  return (
    <section className="work-evidence" aria-labelledby="work-evidence-title">
      <div className="section-heading"><h2 id="work-evidence-title">验收与迭代</h2><span>{version.eval_reports.length} 份 EVAL</span></div>
      <div className="evidence-grid">
        <div>
          <strong>迭代说明</strong>
          <p>{version.iteration_summary || "未记录迭代说明"}</p>
        </div>
        <div>
          <strong>EVAL 证据</strong>
          {version.eval_reports.length ? (
            <ul>
              {version.eval_reports.map((artifact) => {
                const url = authorizedArtifactUrl(artifact);
                return <li key={artifact.artifact_id}>{url ? <a href={url} target="_blank" rel="noreferrer"><FileCheck2 aria-hidden="true" size={14} />{artifact.name}</a> : artifact.name}</li>;
              })}
            </ul>
          ) : <p>未提供 EVAL 报告</p>}
        </div>
      </div>
    </section>
  );
}


export function WorkDetailPage({ api }: { api: WorkDetailPageApi }) {
  const { id = "" } = useParams();
  const [state, setState] = useState<DetailState>({ status: "loading", workId: id });
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [selectedArtifactId, setSelectedArtifactId] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void api.getWork(id, controller.signal).then(
      (work) => {
        const current = work.versions.find((version) => version.label === work.current_version) ?? work.versions[0];
        setSelectedVersionId(current?.version_id ?? "");
        setSelectedArtifactId(current?.outputs[0]?.artifact_id ?? "");
        setState({ status: "ready", workId: id, work });
      },
      () => {
        if (!controller.signal.aborted) setState({ status: "error", workId: id });
      },
    );
    return () => controller.abort();
  }, [api, id]);

  const selectedVersion = useMemo(() => {
    if (state.status !== "ready") return undefined;
    return state.work.versions.find((version) => version.version_id === selectedVersionId) ?? state.work.versions[0];
  }, [selectedVersionId, state]);
  const selectedArtifact = selectedVersion?.outputs.find((artifact) => artifact.artifact_id === selectedArtifactId) ?? selectedVersion?.outputs[0];

  if (state.status === "loading" || state.workId !== id) return (
    <div className="page-frame compact-page"><div className="state-row state-busy" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={18} /><div><strong>正在读取作品</strong><span>核对版本和媒体证据。</span></div></div></div>
  );
  if (state.status === "error") return (
    <div className="page-frame compact-page"><div className="state-row state-error" role="alert"><AlertCircle aria-hidden="true" size={18} /><div><strong>无法读取这件作品</strong><span>作品可能已归档或本机服务暂时不可用。</span></div></div></div>
  );

  const { work } = state;
  return (
    <div className="page-frame compact-page work-detail-page">
      <div className="route-context"><Link to="/works"><ArrowLeft aria-hidden="true" size={14} />返回作品中心</Link></div>
      <div className="page-heading work-detail-heading">
        <div><p className="eyebrow">{work.source === "delivered" ? "DELIVERED VERSION" : "HISTORICAL ARCHIVE"}</p><h1>{work.title}</h1><code className="heading-id">{work.project_id || work.work_id}</code></div>
        {work.versions.length ? (
          <label className="version-selector"><span>作品版本</span><select aria-label="作品版本" value={selectedVersion?.version_id ?? ""} onChange={(event) => {
            const next = work.versions.find((version) => version.version_id === event.target.value);
            setSelectedVersionId(event.target.value);
            setSelectedArtifactId(next?.outputs[0]?.artifact_id ?? "");
          }}>{work.versions.map((version) => <option value={version.version_id} key={version.version_id}>{version.label}</option>)}</select></label>
        ) : null}
      </div>

      {selectedVersion ? (
        <>
          <section className="work-preview" aria-labelledby="work-preview-title">
            <div className="section-heading"><h2 id="work-preview-title">媒体母版</h2><span>{selectedVersion.outputs.length} 个文件</span></div>
            {selectedVersion.outputs.length ? (
              <div className="work-preview-layout">
                <nav className="artifact-register" aria-label="版本文件">
                  {selectedVersion.outputs.map((artifact) => (
                    <button type="button" className={artifact.artifact_id === selectedArtifact?.artifact_id ? "is-current" : undefined} aria-pressed={artifact.artifact_id === selectedArtifact?.artifact_id} key={artifact.artifact_id} onClick={() => setSelectedArtifactId(artifact.artifact_id)}>
                      <strong>{artifact.name}</strong><span>{artifact.media_type}</span>
                    </button>
                  ))}
                </nav>
                <div className="work-media-stage">
                  {selectedArtifact ? <MediaPreview artifact={selectedArtifact} /> : null}
                  {selectedArtifact ? <RightsWarning artifact={selectedArtifact} /> : null}
                  {selectedArtifact && authorizedDownloadUrl(selectedArtifact) ? (
                    <a className="text-button work-download" href={authorizedDownloadUrl(selectedArtifact) ?? undefined} aria-label={`下载 ${selectedArtifact.name}`}><Download aria-hidden="true" size={15} />下载文件</a>
                  ) : null}
                </div>
              </div>
            ) : <div className="work-media-empty">这个版本没有可预览的媒体</div>}
          </section>
          <Evidence version={selectedVersion} />
        </>
      ) : <div className="work-media-empty">这件作品没有可用版本</div>}
    </div>
  );
}
