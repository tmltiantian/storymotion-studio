import type {
  CreatorCharacter,
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

function hasOptionalString(value: Record<string, unknown>, key: string): boolean {
  return !(key in value) || typeof value[key] === "string";
}

function hasOptionalNumber(value: Record<string, unknown>, key: string): boolean {
  return !(key in value) || isFiniteNumber(value[key]);
}

function hasOptionalBoolean(value: Record<string, unknown>, key: string): boolean {
  return !(key in value) || typeof value[key] === "boolean";
}

function hasOptionalArray(
  value: Record<string, unknown>,
  key: string,
  itemIsValid: (item: unknown) => boolean,
): boolean {
  return !(key in value) || (Array.isArray(value[key]) && value[key].every(itemIsValid));
}

function isCreatorCharacter(value: unknown): boolean {
  return isRecord(value) && ["name", "role", "description", "appearance", "voice"]
    .every((key) => hasOptionalString(value, key));
}

function isCreatorDialogue(value: unknown): boolean {
  return isRecord(value)
    && typeof value.speaker === "string"
    && typeof value.text === "string"
    && hasOptionalString(value, "emotion");
}

function isCreatorShot(value: unknown): boolean {
  return isRecord(value)
    && hasOptionalNumber(value, "index")
    && ["title", "action", "camera"].every((key) => hasOptionalString(value, key))
    && hasOptionalNumber(value, "duration_seconds")
    && hasOptionalArray(value, "dialogue", isCreatorDialogue);
}

function isCreatorTarget(value: unknown): boolean {
  return isRecord(value)
    && ["aspect_ratio", "resolution"].every((key) => hasOptionalString(value, key))
    && ["duration_seconds", "fps", "shots"].every((key) => hasOptionalNumber(value, key));
}

function isMediaCharacter(value: unknown): boolean {
  return isRecord(value)
    && hasOptionalString(value, "name")
    && hasOptionalBoolean(value, "ready");
}

function isSpeaker(value: unknown): boolean {
  return isRecord(value) && typeof value.name === "string" && isFiniteNumber(value.line_count);
}

function isTiming(value: unknown): boolean {
  return isRecord(value)
    && ["speaker", "text"].every((key) => hasOptionalString(value, key))
    && ["start_seconds", "end_seconds"].every((key) => hasOptionalNumber(value, key));
}

function isCreatorCheck(value: unknown): boolean {
  return isRecord(value)
    && typeof value.name === "string"
    && typeof value.passed === "boolean"
    && ["error", "warning", "info"].includes(value.severity as string)
    && hasOptionalArray(value, "findings", (item) => typeof item === "string");
}

function hasOptionalObject(
  value: Record<string, unknown>,
  key: string,
  objectIsValid: (item: unknown) => boolean,
): boolean {
  return !(key in value) || objectIsValid(value[key]);
}

function isNarrativePresentation(value: Record<string, unknown>): boolean {
  return hasOptionalString(value, "title")
    && hasOptionalArray(value, "characters", isCreatorCharacter)
    && hasOptionalArray(value, "shots", isCreatorShot)
    && hasOptionalNumber(value, "total_duration_seconds")
    && hasOptionalObject(value, "target", isCreatorTarget)
    && hasOptionalString(value, "premise");
}

function isMediaPresentation(value: Record<string, unknown>): boolean {
  return hasOptionalBoolean(value, "production_ready")
    && hasOptionalArray(value, "characters", isMediaCharacter)
    && hasOptionalArray(value, "review_items", (item) => typeof item === "string")
    && hasOptionalNumber(value, "dialogue_count")
    && hasOptionalNumber(value, "total_duration_seconds")
    && hasOptionalArray(value, "speakers", isSpeaker)
    && hasOptionalArray(value, "timings", isTiming)
    && hasOptionalNumber(value, "clip_count")
    && hasOptionalNumber(value, "duration_seconds")
    && hasOptionalBoolean(value, "subtitle_ready");
}

function normalizePresentation(value: unknown): StagePresentation | null {
  if (!isRecord(value) || typeof value.stage !== "string" || !stageNames.has(value.stage)) {
    return null;
  }
  if (value.state === "unavailable") {
    return { stage: value.stage as StagePresentation["stage"], state: "unavailable" };
  }
  if (value.state !== "ready") return null;
  if (["concept", "script", "storyboard"].includes(value.stage) && !isNarrativePresentation(value)) {
    return null;
  }
  if (["assets", "audio", "video", "edit"].includes(value.stage) && !isMediaPresentation(value)) {
    return null;
  }
  if (value.stage === "eval" && (
    typeof value.passed !== "boolean"
    || !hasOptionalArray(value, "checks", isCreatorCheck)
    || !hasOptionalArray(value, "review_dimensions", (item) => typeof item === "string")
  )) return null;
  if (value.stage === "deliver" && typeof value.quality_approved !== "boolean") return null;
  return value as unknown as StagePresentation;
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
