import { Pause, Play } from "lucide-react";
import { useRef, useState } from "react";

import type { Artifact, DialogueTiming } from "../api/types";
import { authorizedArtifactUrl, formatMediaTime } from "./viewerUtils";

export function AudioViewer({
  artifact,
  onActivate,
}: {
  artifact: Artifact;
  onActivate: (artifactId: string, media: HTMLAudioElement) => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const segmentEndRef = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const url = authorizedArtifactUrl(artifact);
  const dialogues = artifact.viewer?.dialogues ?? [];

  const playFrom = async (timing?: DialogueTiming) => {
    const audio = audioRef.current;
    if (!audio) return;
    onActivate(artifact.artifact_id, audio);
    if (timing) {
      audio.currentTime = timing.start_seconds;
      segmentEndRef.current = timing.end_seconds;
    } else {
      segmentEndRef.current = null;
    }
    await audio.play();
    setPlaying(true);
  };

  if (!url) return <div className="viewer-state viewer-state-error">{artifact.name} 无法安全打开</div>;
  return (
    <div className="audio-viewer">
      <audio
        ref={audioRef}
        data-testid="stage-audio"
        src={url}
        controls
        preload="metadata"
        onPlay={() => {
          if (audioRef.current) onActivate(artifact.artifact_id, audioRef.current);
          setPlaying(true);
        }}
        onPause={() => setPlaying(false)}
        onTimeUpdate={() => {
          const audio = audioRef.current;
          if (audio && segmentEndRef.current !== null && audio.currentTime >= segmentEndRef.current) {
            audio.pause();
            segmentEndRef.current = null;
          }
        }}
      />
      <button
        className="text-button audio-master-button"
        type="button"
        aria-label={`${playing ? "暂停" : "播放"} ${artifact.name}`}
        onClick={() => {
          const audio = audioRef.current;
          if (!audio) return;
          if (playing) audio.pause();
          else void playFrom();
        }}
      >
        {playing ? <Pause aria-hidden="true" size={15} /> : <Play aria-hidden="true" size={15} />}
        {artifact.name}
      </button>
      {dialogues.length ? (
        <ol className="dialogue-timing-list" aria-label={`${artifact.name} 台词`}>
          {dialogues.map((timing) => (
            <li key={timing.dialogue_id}>
              <button
                className="icon-button"
                type="button"
                title={`播放${timing.speaker}台词`}
                aria-label={`播放${timing.speaker}台词`}
                onClick={() => void playFrom(timing)}
              ><Play aria-hidden="true" size={14} /></button>
              <strong>{timing.speaker}</strong>
              <time>{formatMediaTime(timing.start_seconds)}–{formatMediaTime(timing.end_seconds)}</time>
              {timing.text ? <span>{timing.text}</span> : null}
            </li>
          ))}
        </ol>
      ) : <p className="viewer-honesty">未提供台词时间信息</p>}
    </div>
  );
}
