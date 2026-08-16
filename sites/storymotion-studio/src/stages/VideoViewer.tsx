import { Flag, Pause, Play, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import type { Artifact } from "../api/types";
import { authorizedArtifactUrl } from "./viewerUtils";

export function VideoViewer({
  artifacts,
  onIssueAtTime,
}: {
  artifacts: Artifact[];
  onIssueAtTime?: (time: number, artifact: Artifact) => void;
}) {
  const [selectedId, setSelectedId] = useState(artifacts[0]?.artifact_id ?? "");
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [rate, setRate] = useState(1);
  const [dialogueOnly, setDialogueOnly] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const selected = useMemo(
    () => artifacts.find((item) => item.artifact_id === selectedId) ?? artifacts[0],
    [artifacts, selectedId],
  );
  if (!selected) return null;
  const url = authorizedArtifactUrl(selected);
  if (!url) return <div className="viewer-state viewer-state-error">{selected.name} 无法安全打开</div>;
  const fps = selected.viewer?.fps && selected.viewer.fps > 0 ? selected.viewer.fps : 30;
  const dialogues = selected.viewer?.dialogues ?? [];
  const ratio = selected.viewer?.width && selected.viewer?.height
    ? `${selected.viewer.width} / ${selected.viewer.height}`
    : "9 / 16";

  const frameStep = (direction: -1 | 1) => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    setPlaying(false);
    const next = Math.max(0, video.currentTime + direction / fps);
    video.currentTime = Number.isFinite(video.duration) ? Math.min(video.duration, next) : next;
  };

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video) return;
    if (playing) {
      video.pause();
      return;
    }
    if (dialogueOnly && dialogues.length && !dialogues.some((item) => video.currentTime >= item.start_seconds && video.currentTime < item.end_seconds)) {
      video.currentTime = dialogues.find((item) => item.start_seconds > video.currentTime)?.start_seconds ?? dialogues[0].start_seconds;
    }
    await video.play();
  };

  return (
    <div className="video-viewer">
      {artifacts.length > 1 ? (
        <label className="candidate-selector">
          <span>候选视频</span>
          <select
            aria-label="候选视频"
            value={selected.artifact_id}
            onChange={(event) => {
              videoRef.current?.pause();
              setPlaying(false);
              setDialogueOnly(false);
              setSelectedId(event.target.value);
            }}
          >
            {artifacts.map((artifact) => <option key={artifact.artifact_id} value={artifact.artifact_id}>{artifact.name}</option>)}
          </select>
        </label>
      ) : null}
      <div className="video-viewer-frame" style={{ aspectRatio: ratio }}>
        <video
          ref={videoRef}
          data-testid="stage-video"
          src={url}
          controls
          playsInline
          preload="metadata"
          muted={muted}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={() => {
            const video = videoRef.current;
            if (!video || !dialogueOnly || !dialogues.length) return;
            const current = dialogues.find((item) => video.currentTime >= item.start_seconds && video.currentTime < item.end_seconds);
            if (current) return;
            const next = dialogues.find((item) => item.start_seconds > video.currentTime);
            if (next) video.currentTime = next.start_seconds;
            else video.pause();
          }}
        ><track kind="captions" /></video>
      </div>
      <div className="video-control-toolbar" role="toolbar" aria-label="视频检查控制">
        <button className="icon-button" type="button" title={playing ? "暂停" : "播放"} aria-label={playing ? "暂停视频" : "播放视频"} onClick={() => void togglePlayback()}>
          {playing ? <Pause aria-hidden="true" size={15} /> : <Play aria-hidden="true" size={15} />}
        </button>
        <button className="icon-button" type="button" title="后退一帧" aria-label="后退一帧" onClick={() => frameStep(-1)}><SkipBack aria-hidden="true" size={15} /></button>
        <button className="icon-button" type="button" title="前进一帧" aria-label="前进一帧" onClick={() => frameStep(1)}><SkipForward aria-hidden="true" size={15} /></button>
        <div className="rate-control" aria-label="播放速度">
          {[0.5, 1].map((value) => (
            <button
              key={value}
              type="button"
              aria-label={`${value} 倍速`}
              aria-pressed={rate === value}
              onClick={() => {
                setRate(value);
                if (videoRef.current) videoRef.current.playbackRate = value;
              }}
            >{value}x</button>
          ))}
        </div>
        <button
          className="icon-button"
          type="button"
          title={muted ? "取消静音" : "静音"}
          aria-label={muted ? "取消静音" : "静音"}
          onClick={() => {
            const next = !muted;
            setMuted(next);
            if (videoRef.current) videoRef.current.muted = next;
          }}
        >{muted ? <VolumeX aria-hidden="true" size={15} /> : <Volume2 aria-hidden="true" size={15} />}</button>
        {dialogues.length ? (
          <label className="dialogue-only-control">
            <input type="checkbox" checked={dialogueOnly} onChange={(event) => setDialogueOnly(event.target.checked)} />
            <span>仅播放台词时段</span>
          </label>
        ) : null}
        {onIssueAtTime && selected.viewer?.shot_id ? (
          <button
            className="text-button issue-time-button"
            type="button"
            onClick={() => onIssueAtTime(videoRef.current?.currentTime ?? 0, selected)}
          ><Flag aria-hidden="true" size={15} />在当前时间标记问题</button>
        ) : null}
      </div>
    </div>
  );
}
