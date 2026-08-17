import type {
  CreatorCharacter,
  CreatorCheck,
  CreatorDialogue,
  CreatorShot,
  StagePresentation,
} from "../api/types";

type SummaryItem = {
  label: string;
  value: string | number | undefined;
};

const stageNames = new Set([
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function stringField(value: Record<string, unknown>, key: string): string | undefined {
  const field = value[key];
  if (typeof field !== "string" || !field.trim()) return undefined;
  return field;
}

function numberField(value: Record<string, unknown>, key: string): number | undefined {
  return isFiniteNumber(value[key]) ? value[key] : undefined;
}

function booleanField(value: Record<string, unknown>, key: string): boolean | undefined {
  return typeof value[key] === "boolean" ? value[key] : undefined;
}

function normalizedArray<T>(
  value: Record<string, unknown>,
  key: string,
  normalize: (item: unknown) => T | null,
): T[] | undefined {
  const source = value[key];
  if (!Array.isArray(source)) return undefined;
  return source.flatMap((item) => {
    const normalized = normalize(item);
    return normalized === null ? [] : [normalized];
  });
}

function stringArray(value: Record<string, unknown>, key: string): string[] | undefined {
  return normalizedArray(value, key, (item) => (
    typeof item === "string" && item.trim() ? item : null
  ));
}

function normalizeCreatorCharacter(value: unknown): CreatorCharacter | null {
  if (!isRecord(value)) return null;
  const result: CreatorCharacter = {
    name: stringField(value, "name"),
    role: stringField(value, "role"),
    description: stringField(value, "description"),
    appearance: stringField(value, "appearance"),
    voice: stringField(value, "voice"),
  };
  return Object.values(result).some((field) => field !== undefined) ? result : null;
}

function normalizeCreatorDialogue(value: unknown): CreatorDialogue | null {
  if (!isRecord(value)) return null;
  const speaker = stringField(value, "speaker");
  const text = stringField(value, "text");
  if (!speaker || !text) return null;
  return { speaker, text, emotion: stringField(value, "emotion") };
}

function normalizeCreatorShot(value: unknown): CreatorShot | null {
  if (!isRecord(value)) return null;
  const result: CreatorShot = {
    index: numberField(value, "index"),
    title: stringField(value, "title"),
    action: stringField(value, "action"),
    camera: stringField(value, "camera"),
    duration_seconds: numberField(value, "duration_seconds"),
    dialogue: normalizedArray(value, "dialogue", normalizeCreatorDialogue),
  };
  return Object.values(result).some((field) => field !== undefined) ? result : null;
}

function normalizeCreatorTarget(value: unknown) {
  if (!isRecord(value)) return undefined;
  const result = {
    aspect_ratio: stringField(value, "aspect_ratio"),
    resolution: stringField(value, "resolution"),
    duration_seconds: numberField(value, "duration_seconds"),
    fps: numberField(value, "fps"),
    shots: numberField(value, "shots"),
  };
  return Object.values(result).some((field) => field !== undefined) ? result : undefined;
}

function normalizeMediaCharacter(value: unknown) {
  if (!isRecord(value)) return null;
  const name = stringField(value, "name");
  const ready = booleanField(value, "ready");
  if (name === undefined && ready === undefined) return null;
  return { name, ready };
}

function normalizeSpeaker(value: unknown) {
  if (!isRecord(value)) return null;
  const name = stringField(value, "name");
  const lineCount = numberField(value, "line_count");
  return name && lineCount !== undefined ? { name, line_count: lineCount } : null;
}

function normalizeTiming(value: unknown) {
  if (!isRecord(value)) return null;
  const result = {
    speaker: stringField(value, "speaker"),
    text: stringField(value, "text"),
    start_seconds: numberField(value, "start_seconds"),
    end_seconds: numberField(value, "end_seconds"),
  };
  return Object.values(result).some((field) => field !== undefined) ? result : null;
}

function normalizeCreatorCheck(value: unknown): CreatorCheck | null {
  if (!isRecord(value)) return null;
  const name = stringField(value, "name");
  const passed = booleanField(value, "passed");
  const severity = value.severity;
  if (!name || passed === undefined || !["error", "warning", "info"].includes(severity as string)) {
    return null;
  }
  return {
    name,
    passed,
    severity: severity as CreatorCheck["severity"],
    findings: stringArray(value, "findings"),
  };
}

function normalizePresentation(value: unknown): StagePresentation | null {
  if (!isRecord(value) || typeof value.stage !== "string" || !stageNames.has(value.stage)) {
    return null;
  }
  if (value.state === "unavailable") {
    return { stage: value.stage as StagePresentation["stage"], state: "unavailable" };
  }
  if (value.state !== "ready") return null;
  if (value.stage === "concept") {
    return {
      stage: "concept",
      state: "ready",
      title: stringField(value, "title"),
      premise: stringField(value, "premise"),
      mode_label: stringField(value, "mode_label"),
      source_label: stringField(value, "source_label"),
      target: normalizeCreatorTarget(value.target),
      characters: normalizedArray(value, "characters", normalizeCreatorCharacter),
    };
  }
  if (value.stage === "script" || value.stage === "storyboard") {
    return {
      stage: value.stage,
      state: "ready",
      title: stringField(value, "title"),
      total_duration_seconds: numberField(value, "total_duration_seconds"),
      characters: normalizedArray(value, "characters", normalizeCreatorCharacter),
      shots: normalizedArray(value, "shots", normalizeCreatorShot),
    };
  }
  if (["assets", "audio", "video", "edit"].includes(value.stage)) {
    return {
      stage: value.stage as "assets" | "audio" | "video" | "edit",
      state: "ready",
      production_ready: booleanField(value, "production_ready"),
      characters: normalizedArray(value, "characters", normalizeMediaCharacter),
      review_items: stringArray(value, "review_items"),
      dialogue_count: numberField(value, "dialogue_count"),
      total_duration_seconds: numberField(value, "total_duration_seconds"),
      speakers: normalizedArray(value, "speakers", normalizeSpeaker),
      timings: normalizedArray(value, "timings", normalizeTiming),
      clip_count: numberField(value, "clip_count"),
      duration_seconds: numberField(value, "duration_seconds"),
      subtitle_ready: booleanField(value, "subtitle_ready"),
    };
  }
  if (value.stage === "eval") {
    const passed = booleanField(value, "passed");
    if (passed === undefined) return null;
    return {
      stage: "eval",
      state: "ready",
      passed,
      checks: normalizedArray(value, "checks", normalizeCreatorCheck),
      review_dimensions: stringArray(value, "review_dimensions"),
    };
  }
  if (value.stage === "deliver") {
    const qualityApproved = booleanField(value, "quality_approved");
    if (qualityApproved === undefined) return null;
    return { stage: "deliver", state: "ready", quality_approved: qualityApproved };
  }
  return null;
}

function formatDuration(seconds: number | undefined): string | undefined {
  if (seconds === undefined || !Number.isFinite(seconds)) return undefined;
  return `${seconds} 秒`;
}

function Summary({ items }: { items: SummaryItem[] }) {
  const visibleItems = items.filter((item) => item.value !== undefined && item.value !== "");
  if (!visibleItems.length) return null;
  return (
    <dl className="stage-presentation-summary">
      {visibleItems.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CharacterCards({ characters }: { characters: CreatorCharacter[] | undefined }) {
  if (!characters?.length) return null;
  return (
    <section className="stage-presentation-characters" aria-labelledby="creator-characters-title">
      <h3 id="creator-characters-title">角色</h3>
      <div className="creator-character-list">
        {characters.map((character, index) => (
          <article className="creator-character-card" key={`${character.name ?? "character"}-${index}`}>
            {character.name ? <h4>{character.name}{character.role && character.role !== character.name ? `（${character.role}）` : ""}</h4> : null}
            <Summary items={[
              { label: "人物特点", value: character.description },
              { label: "外观方向", value: character.appearance },
              { label: "声音方向", value: character.voice },
            ]} />
          </article>
        ))}
      </div>
    </section>
  );
}

function DialogueRows({ dialogue }: { dialogue: CreatorDialogue[] | undefined }) {
  if (!dialogue?.length) return null;
  return (
    <ol className="creator-dialogue-list" aria-label="台词">
      {dialogue.map((line, index) => (
        <li key={`${line.speaker}-${index}`}>
          <strong>{line.speaker}</strong>
          {line.emotion ? <span>{line.emotion}</span> : null}
          <p>{line.text}</p>
        </li>
      ))}
    </ol>
  );
}

function ShotRows({ shots }: { shots: CreatorShot[] | undefined }) {
  if (!shots?.length) return null;
  return (
    <section className="stage-presentation-shots" aria-labelledby="creator-shots-title">
      <h3 id="creator-shots-title">镜头</h3>
      <ol className="creator-shot-list">
        {shots.map((shot, index) => {
          const shotNumber = shot.index ?? index + 1;
          const generatedTitle = `第 ${shotNumber} 镜`;
          const title = shot.title?.replace(/\s/g, "") === generatedTitle.replace(/\s/g, "") ? "" : shot.title;
          return (
          <li key={`${shot.index ?? index}-${shot.title ?? "shot"}`}>
            <h4>{generatedTitle}{title ? ` · ${title}` : ""}</h4>
            <Summary items={[
              { label: "画面动作", value: shot.action },
              { label: "景别", value: shot.camera },
              { label: "时长", value: formatDuration(shot.duration_seconds) },
            ]} />
            <DialogueRows dialogue={shot.dialogue} />
          </li>
          );
        })}
      </ol>
    </section>
  );
}

function ListSection({ title, items, className }: { title: string; items: string[] | undefined; className: string }) {
  if (!items?.length) return null;
  return (
    <section className={className} aria-label={title}>
      <h3>{title}</h3>
      <ul>
        {items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
      </ul>
    </section>
  );
}

function NarrativePresentation({ presentation }: { presentation: Extract<StagePresentation, { stage: "concept" | "script" | "storyboard" }> }) {
  return (
    <>
      {presentation.title ? <h2>{presentation.title}</h2> : null}
      {presentation.stage === "concept" && presentation.premise ? <p>{presentation.premise}</p> : null}
      <Summary items={presentation.stage === "concept" ? [
        { label: "创作模式", value: presentation.mode_label },
        { label: "内容来源", value: presentation.source_label },
        { label: "预计时长", value: formatDuration(presentation.target?.duration_seconds) },
        { label: "画幅", value: presentation.target?.aspect_ratio },
        { label: "画面规格", value: presentation.target?.resolution },
        { label: "镜头数", value: presentation.target?.shots },
      ] : [
        { label: "总时长", value: formatDuration(presentation.total_duration_seconds) },
        { label: "镜头数", value: presentation.shots?.length },
      ]} />
      <CharacterCards characters={presentation.characters} />
      {presentation.stage === "concept" ? null : <ShotRows shots={presentation.shots} />}
    </>
  );
}

function MediaPresentation({ presentation }: { presentation: Extract<StagePresentation, { stage: "assets" | "audio" | "video" | "edit" }> }) {
  if (presentation.stage === "assets") {
    return (
      <>
        <Summary items={[{ label: "准备情况", value: presentation.production_ready ? "已准备" : "待完善" }]} />
        {presentation.characters?.length ? (
          <section className="stage-presentation-characters" aria-label="角色素材">
            <h3>角色素材</h3>
            <div className="creator-character-list">
              {presentation.characters.map((character, index) => (
                <article className="creator-character-card" key={`${character.name ?? "character"}-${index}`}>
                  {character.name ? <h4>{character.name}</h4> : null}
                  <p>{character.ready ? "已准备" : "待完善"}</p>
                </article>
              ))}
            </div>
          </section>
        ) : null}
        <ListSection title="检查事项" items={presentation.review_items} className="stage-presentation-review-items" />
      </>
    );
  }
  if (presentation.stage === "audio") {
    return (
      <>
        <Summary items={[
          { label: "台词数", value: presentation.dialogue_count },
          { label: "总时长", value: formatDuration(presentation.total_duration_seconds) },
        ]} />
        <ListSection
          title="配音角色"
          className="stage-presentation-speakers"
          items={presentation.speakers?.map((speaker) => `${speaker.name} · ${speaker.line_count} 句`)}
        />
        {presentation.timings?.length ? (
          <section className="stage-presentation-dialogue" aria-label="配音台词">
            <h3>配音台词</h3>
            <ol className="creator-dialogue-list">
              {presentation.timings.map((timing, index) => (
                <li key={`${timing.speaker ?? "line"}-${index}`}>
                  {timing.speaker ? <strong>{timing.speaker}</strong> : null}
                  {timing.text ? <p>{timing.text}</p> : null}
                  <span>{formatDuration(timing.start_seconds)}{timing.end_seconds === undefined ? "" : ` 至 ${formatDuration(timing.end_seconds)}`}</span>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
      </>
    );
  }
  if (presentation.stage === "video") {
    return <Summary items={[{ label: "已生成片段", value: presentation.clip_count }]} />;
  }
  return <Summary items={[
    { label: "成片时长", value: formatDuration(presentation.duration_seconds) },
    { label: "字幕", value: presentation.subtitle_ready ? "已准备" : "待完善" },
  ]} />;
}

function EvalPresentationView({ presentation }: { presentation: Extract<StagePresentation, { stage: "eval" }> }) {
  return (
    <>
      <Summary items={[{ label: "检查结果", value: presentation.passed ? "通过" : "需要关注" }]} />
      {presentation.checks?.length ? (
        <section className="stage-presentation-checks" aria-labelledby="creator-checks-title">
          <h3 id="creator-checks-title">检查项目</h3>
          <ul>
            {presentation.checks.map((check, index) => (
              <li key={`${check.name}-${index}`}>
                <strong>{check.name}</strong>
                <span>{check.passed ? "通过" : check.severity === "error" ? "需处理" : "需关注"}</span>
                {check.findings?.length ? <ul>{check.findings.map((finding, findingIndex) => <li key={`${finding}-${findingIndex}`}>{finding}</li>)}</ul> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <ListSection title="检查范围" items={presentation.review_dimensions} className="stage-presentation-dimensions" />
    </>
  );
}

export function StagePresentationView({ presentation }: { presentation: StagePresentation | null }) {
  const normalized = normalizePresentation(presentation);
  if (!normalized || normalized.state === "unavailable") {
    return <div className="stage-presentation-empty"><strong>本阶段尚未生成可查看的成果</strong></div>;
  }

  return (
    <section className="stage-presentation" aria-label="阶段成果内容">
      {normalized.stage === "concept" || normalized.stage === "script" || normalized.stage === "storyboard" ? (
        <NarrativePresentation presentation={normalized} />
      ) : null}
      {normalized.stage === "assets" || normalized.stage === "audio" || normalized.stage === "video" || normalized.stage === "edit" ? (
        <MediaPresentation presentation={normalized} />
      ) : null}
      {normalized.stage === "eval" ? <EvalPresentationView presentation={normalized} /> : null}
      {normalized.stage === "deliver" ? <Summary items={[{ label: "质量检查", value: normalized.quality_approved ? "质量检查已通过" : "质量检查待处理" }]} /> : null}
    </section>
  );
}
