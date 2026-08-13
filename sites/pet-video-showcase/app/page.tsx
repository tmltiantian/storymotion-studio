"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import {
  Activity,
  AudioLines,
  CheckCircle2,
  Film,
  History,
  LibraryBig,
  Maximize2,
  ScanLine,
  ShieldCheck,
  Volume2,
  Wrench,
  X,
} from "lucide-react";

type TabId = "film" | "voices" | "repairs" | "timeline" | "library";
type ArchiveCategory = "all" | "comic" | "voice" | "pet";

type Evidence = {
  src: string;
  alt: string;
  caption: string;
};

const tabs = [
  { id: "film" as const, label: "成片", icon: Film },
  { id: "voices" as const, label: "角色声音", icon: AudioLines },
  { id: "repairs" as const, label: "问题修复", icon: Wrench },
  { id: "timeline" as const, label: "迭代记录", icon: History },
  { id: "library" as const, label: "历史素材", icon: LibraryBig },
];

const archiveCategories: { id: ArchiveCategory; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "comic", label: "漫剧实验" },
  { id: "voice", label: "声音实验" },
  { id: "pet", label: "宠物短剧" },
];

const archiveItems = [
  {
    title: "旧城来信",
    date: "2026.07.20",
    stage: "早期成片",
    duration: "01:01",
    category: "comic" as const,
    src: "/archive/old-city-letter.mp4",
    poster: "/archive/old-city-letter.jpg",
    description: "小说转漫剧的早期完整样片，记录从角色、分镜到声音合成的第一条生产链。",
  },
  {
    title: "六音色对比",
    date: "2026.07",
    stage: "音色测试",
    duration: "00:14",
    category: "voice" as const,
    src: "/archive/six-voices.mp4",
    poster: "/archive/six-voices.jpg",
    description: "豆包 Seed-TTS 2.0 六组中文音色横向试听，用于筛选旁白和角色声线。",
  },
  {
    title: "三角色对话小样",
    date: "2026.07",
    stage: "角色配音",
    duration: "00:15",
    category: "voice" as const,
    src: "/archive/three-role-dialogue.mp4",
    poster: "/archive/three-role-dialogue.jpg",
    description: "旁白、苏眠与林澈的多角色配音样片，验证音色区分和台词排程。",
  },
  {
    title: "冻干到底是谁偷吃的",
    date: "2026.07.23",
    stage: "初版",
    duration: "01:10",
    category: "pet" as const,
    src: "/archive/freeze-dried-v1.mp4",
    poster: "/archive/freeze-dried-v1.jpg",
    description: "第一支双猫宠物短剧，暴露了声音重叠、切镜过密和道具运动不自然等问题。",
  },
  {
    title: "冻干到底是谁偷吃的",
    date: "2026.07.26",
    stage: "精修版",
    duration: "00:45",
    category: "pet" as const,
    src: "/archive/freeze-dried-refined.mp4",
    poster: "/archive/freeze-dried-refined.jpg",
    description: "收紧叙事和切镜后的精修版，统一猫咪音色，并减少无意义场景切换。",
  },
  {
    title: "猫猫鬼点子",
    date: "2026.08.04",
    stage: "9 镜试片",
    duration: "00:13",
    category: "pet" as const,
    src: "/archive/cat-ideas-pilot.mp4",
    poster: "/archive/cat-ideas-pilot.jpg",
    description: "完整长片前的九镜压力测试，先检查角色一致性、动作连接和镜头可读性。",
  },
  {
    title: "猫猫鬼点子",
    date: "2026.08.04",
    stage: "完整长片",
    duration: "01:17",
    category: "pet" as const,
    src: "/archive/cat-ideas-final.mp4",
    poster: "/archive/cat-ideas-final.jpg",
    description: "逐镜重拍后的完整版本，把试片中通过的角色锚点和动作规则扩展到全片。",
  },
  {
    title: "咪要去面试",
    date: "2026.08.10",
    stage: "V2 物理修复",
    duration: "03:25",
    category: "pet" as const,
    src: "/archive/interview-cat-v2.mp4",
    poster: "/archive/interview-cat-v2.jpg",
    description: "当前主片进入动作优化前的 V2 节点，集中修复额外肢体、重复道具和受力错误。",
  },
];

const metrics = [
  { label: "成片时长", value: "204.77 秒" },
  { label: "镜头", value: "43" },
  { label: "画面", value: "30 fps" },
  { label: "验收", value: "PASS", accent: true },
];

const approvedAssets = [
  {
    label: "最终成片",
    value: "《咪要去面试》V3.1",
    detail: "204.77 秒 · 43 镜 · 30 fps · 已封存",
  },
  {
    label: "画面链路",
    value: "Seedream 4.5 + Seedance 2.0",
    detail: "角色锚点、逐镜动态、运动补帧与局部修复",
  },
  {
    label: "角色配音",
    value: "豆包 Seed-TTS 2.0",
    detail: "双猫音色与语速已固定，供后续新片直接复用",
  },
  {
    label: "验收基线",
    value: "技术与语义双门禁",
    detail: "坏例证据、迭代记录与网页播放副本可追溯",
  },
];

const approvedVoices = [
  {
    role: "黑白猫 · 豆包",
    personality: "高冷御姐",
    voice: "魅力女友",
    rate: "+4",
    src: "/audio/black-cat-approved.m4a",
    description: "自然偏低、冷静克制、短停顿。高冷感来自干脆的句尾，不再靠拖慢语速。",
  },
  {
    role: "橘猫 · 奶糖",
    personality: "可爱活泼",
    voice: "调皮公主",
    rate: "+2",
    src: "/audio/orange-cat-approved.m4a",
    description: "声线轻亮、反应灵动、有自然笑意，与黑白猫形成清楚的角色反差。",
  },
];

const workflow = [
  { step: "01", title: "构思", detail: "创意、小说或参考视频" },
  { step: "02", title: "剧本", detail: "角色关系、对白与剧情节拍" },
  { step: "03", title: "分镜", detail: "镜头功能、动作和连续性" },
  { step: "04", title: "素材", detail: "角色、场景与道具锚点" },
  { step: "05", title: "音频", detail: "固定音色、实测时长与口型窗口" },
  { step: "06", title: "视频", detail: "逐镜动态、候选与生成记录" },
  { step: "07", title: "剪辑", detail: "切点、字幕、音效与混音" },
  { step: "08", title: "EVAL", detail: "身份、物理、动作、口型与转场" },
  { step: "09", title: "交付", detail: "局部返修通过后封存母版" },
];

const productionModes = [
  { code: "ORIGINAL", title: "原创短剧", detail: "从一句创意扩写剧情，猫咪题材复用已定版角色与音色。" },
  { code: "NOVEL", title: "小说漫剧", detail: "提取人物、场景、对白和事件，再进入统一分镜生产。" },
  { code: "REPLICA", title: "参考复刻", detail: "保留剧情节拍和镜头功能，只替换指定人物、猫咪与素材。" },
];

const timeline = [
  {
    date: "07.23",
    title: "从复刻目标开始",
    finding: "参考视频的故事和镜头节奏清楚，但直接替换角色会破坏身份与场景连续性。",
    decision: "先锁定剧情时间线，再逐镜重建黑白猫、场景与动作，不做简单贴图替换。",
    result: "建立源锁定的 43 镜生产结构。",
  },
  {
    date: "07.26",
    title: "声音与场景坏例",
    finding: "出现音色漂移、声音重叠、场景切换过多和道具自行移动。",
    decision: "统一角色音色，按台词排程音轨，并把道具接触、受力和停止写进镜头约束。",
    result: "声音排程不再重叠，物理问题开始按镜头证据验收。",
  },
  {
    date: "08.04",
    title: "从试片进入全片",
    finding: "只看首尾帧无法发现中间帧的额外肢体、口型尾帧和跨镜道具变化。",
    decision: "每镜生成密集帧、口型表和切点证据，失败候选保留但不晋升。",
    result: "逐镜审核扩展到完整长片，生成与验收被拆成独立环节。",
  },
  {
    date: "08.10 · V2",
    title: "解剖与物理局部修复",
    finding: "手机特写出现额外长前肢，抱文件镜头出现四只前爪，纸杯被复制成两个。",
    decision: "只重生成 S004、S012、S018，并明确肢体总数、连接关系和单物体运动链。",
    result: "三处坏例消失，其余 40 镜保持不变。",
  },
  {
    date: "08.10 · V3",
    title: "动作卡顿与过渡优化",
    finding: "42 个 24 fps 镜头直接补到 30 fps，造成周期性重复帧；硬切让地点变化突兀。",
    decision: "采用运动补帧，只在八个剧情边界加入单帧淡黑；淘汰会产生重影的交叉叠化。",
    result: "近似重复帧从 861 降至 229，减少约 73%。",
  },
  {
    date: "08.10 · V3.1",
    title: "验收与封存",
    finding: "成片正确之外，质量报告也需要准确区分单帧过渡黑场和持续黑场。",
    decision: "修正文档、重新运行完整交付门禁，并从密封目录交付最终版本。",
    result: "2847 项测试、62 项语义审核通过，Delivery Eval 为 PASS。",
  },
  {
    date: "08.12 · VOICE",
    title: "双猫音色定版",
    finding: "两只猫最初都偏可爱，区分不足；黑白猫降到 -12 后又出现拖字和人机式长停顿。",
    decision: "黑白猫改用魅力女友并恢复 +4 的自然语速；橘猫固定调皮公主 +2，同时禁止用合成猫叫制造角色感。",
    result: "角色只听声音也能区分，固定映射与语速已进入生产配置，并由 30 项音频测试守护。",
  },
];

const evidence: Record<string, Evidence> = {
  beforePaws: {
    src: "/evidence/physics-before-four-paws.png",
    alt: "修复前手机画面中黑白猫出现不合理的额外长前肢",
    caption: "修复前：主体与画外前肢被错误并置",
  },
  s004: {
    src: "/evidence/S004_dense.jpg",
    alt: "S004 修复后的密集帧，仅保留一只与画外身体连接的前爪",
    caption: "S004：单前爪按键动作连续",
  },
  s012: {
    src: "/evidence/S012_dense.jpg",
    alt: "S012 修复后的密集帧，两只前爪持续支撑同一份文件",
    caption: "S012：两只前爪持续支撑文件",
  },
  s018: {
    src: "/evidence/S018_dense.jpg",
    alt: "S018 修复后的密集帧，单个纸杯经历接触、倾倒、滑动和停止",
    caption: "S018：单纸杯遵循完整受力链",
  },
  duplicated: {
    src: "/evidence/S025_duplicated_30frames.jpg",
    alt: "旧版 24 帧镜头通过复制补到 30 帧的连续帧对照",
    caption: "旧方案：周期性复制帧",
  },
  interpolated: {
    src: "/evidence/S025_interpolated_30frames.jpg",
    alt: "新版通过运动估计补到 30 帧的连续帧对照",
    caption: "最终方案：运动补帧",
  },
  actionAudit: {
    src: "/evidence/V3_action_frame_audit.jpg",
    alt: "五个重点镜头的运动补帧逐帧审查长图",
    caption: "重点动作逐帧审查",
  },
  transitions: {
    src: "/evidence/V3_eight_transitions.jpg",
    alt: "八个剧情边界淡黑过渡的前后连续帧",
    caption: "八个剧情边界：不叠加人物和道具",
  },
};

function EvidenceButton({ item, onOpen }: { item: Evidence; onOpen: (item: Evidence) => void }) {
  return (
    <button className="evidence-button" type="button" onClick={() => onOpen(item)}>
      <img src={item.src} alt={item.alt} loading="lazy" />
      <span className="evidence-caption">
        {item.caption}
        <Maximize2 aria-hidden="true" size={16} />
      </span>
    </button>
  );
}

export default function ProjectShowcasePage() {
  const [activeTab, setActiveTab] = useState<TabId>("film");
  const [activeEvidence, setActiveEvidence] = useState<Evidence | null>(null);
  const [archiveCategory, setArchiveCategory] = useState<ArchiveCategory>("all");

  useEffect(() => {
    if (!activeEvidence) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setActiveEvidence(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [activeEvidence]);

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const offset = event.key === "ArrowRight" ? 1 : -1;
    const next = (index + offset + tabs.length) % tabs.length;
    setActiveTab(tabs[next].id);
    document.getElementById(`tab-${tabs[next].id}`)?.focus();
  };

  const onPlayMedia = (current: HTMLMediaElement) => {
    document.querySelectorAll<HTMLMediaElement>("audio, video").forEach((media) => {
      if (media !== current && !media.paused) media.pause();
    });
  };

  const filteredArchiveItems = archiveItems.filter(
    (item) => archiveCategory === "all" || item.category === archiveCategory,
  );

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#screening" aria-label="返回成片放映区">
          <ScanLine aria-hidden="true" size={19} />
          <span>宠物短剧项目放映室</span>
        </a>
        <div className="release-state">
          <span className="status-dot" aria-hidden="true" />
          V3.1 已封存
        </div>
      </header>

      <section className="screening-room" id="screening">
        <div className="video-bay">
          <div className="frame-counter" aria-hidden="true">
            <span>MASTER / 01</span>
            <span>00:03:24:23</span>
          </div>
          <video
            className="master-video"
            controls
            playsInline
            preload="metadata"
            poster="/evidence/poster.jpg"
            aria-label="《咪要去面试》最终成片"
            onPlay={(event) => onPlayMedia(event.currentTarget)}
          >
            <source src="/media/final-master.mp4" type="video/mp4" />
            当前浏览器无法播放此视频。
          </video>
        </div>

        <div className="project-intro">
          <p className="project-kicker">AI PET DRAMA / PERSONAL ARCHIVE</p>
          <h1>咪要去面试</h1>
          <p className="project-summary">
            一支以黑白猫为主角的 204 秒宠物短剧。从参考拆解、角色重建到逐镜生成，
            再针对肢体、物理、口型、动作和转场持续修复。
          </p>

          <dl className="metric-grid">
            {metrics.map((metric) => (
              <div className={metric.accent ? "metric metric-accent" : "metric"} key={metric.label}>
                <dt>{metric.label}</dt>
                <dd>{metric.value}</dd>
              </div>
            ))}
          </dl>

          <div className="verification-line">
            <ShieldCheck aria-hidden="true" size={19} />
            <span>2847 项测试 · 62 项语义审核 · 音轨哈希一致</span>
          </div>

          <a className="jump-link" href="#archive">
            查看制作档案
            <span aria-hidden="true">↓</span>
          </a>
        </div>
      </section>

      <section className="archive" id="archive">
        <div className="tab-shell">
          <div className="tab-list" role="tablist" aria-label="项目档案视图">
            {tabs.map((tab, index) => {
              const Icon = tab.icon;
              const selected = activeTab === tab.id;
              return (
                <button
                  id={`tab-${tab.id}`}
                  className={selected ? "tab-button is-active" : "tab-button"}
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls={`panel-${tab.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActiveTab(tab.id)}
                  onKeyDown={(event) => onTabKeyDown(event, index)}
                >
                  <Icon aria-hidden="true" size={18} />
                  {tab.label}
                </button>
              );
            })}
          </div>
          <span className="archive-version">ARCHIVE / 2026.08.10</span>
        </div>

        {activeTab === "film" && (
          <div id="panel-film" role="tabpanel" aria-labelledby="tab-film" className="tab-panel film-panel">
            <div className="section-heading">
              <p>UNIFIED PRODUCTION</p>
              <h2>三种输入，共用一条可验收生产线</h2>
            </div>

            <div className="mode-strip" aria-label="支持的项目模式">
              {productionModes.map((mode) => (
                <article key={mode.code}>
                  <span>{mode.code}</span>
                  <h3>{mode.title}</h3>
                  <p>{mode.detail}</p>
                </article>
              ))}
            </div>

            <ol className="workflow-list">
              {workflow.map((item) => (
                <li key={item.step}>
                  <span className="workflow-step">{item.step}</span>
                  <div>
                    <h3>{item.title}</h3>
                    <p>{item.detail}</p>
                  </div>
                </li>
              ))}
            </ol>

            <div className="film-notes">
              <div className="note-block">
                <Film aria-hidden="true" size={21} />
                <div>
                  <h3>画面生成</h3>
                  <p>Seedream 4.5 建立角色与场景锚点，Seedance 2.0 生成逐镜动态。</p>
                </div>
              </div>
              <div className="note-block">
                <Volume2 aria-hidden="true" size={21} />
                <div>
                  <h3>声音说明</h3>
                  <p>当前主片保留既有音轨；新定版的双猫 Seed-TTS 声线用于下一轮原创短剧生产。</p>
                </div>
              </div>
              <div className="note-block">
                <CheckCircle2 aria-hidden="true" size={21} />
                <div>
                  <h3>交付门禁</h3>
                  <p>自动技术检查、逐项语义审核和密封交付全部通过，母版可追溯。</p>
                </div>
              </div>
            </div>

            <section className="asset-register" aria-labelledby="asset-register-title">
              <div className="asset-register-heading">
                <p>APPROVED ASSETS</p>
                <h3 id="asset-register-title">已确认资产</h3>
              </div>
              <dl>
                {approvedAssets.map((asset) => (
                  <div key={asset.label}>
                    <dt>{asset.label}</dt>
                    <dd>
                      <strong>{asset.value}</strong>
                      <span>{asset.detail}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>
        )}

        {activeTab === "voices" && (
          <div id="panel-voices" role="tabpanel" aria-labelledby="tab-voices" className="tab-panel voice-panel">
            <div className="section-heading voice-heading">
              <p>APPROVED VOICES</p>
              <div>
                <h2>两只猫，一听就能分清</h2>
                <p className="voice-intro">
                  这是后续宠物连续剧的固定声音基线。角色感来自声线、节奏和表达，不添加不自然的合成猫叫。
                </p>
              </div>
            </div>

            <div className="voice-grid">
              {approvedVoices.map((item) => (
                <article className="voice-profile" key={item.role}>
                  <div className="voice-profile-head">
                    <div>
                      <span className="voice-status"><CheckCircle2 aria-hidden="true" size={14} /> 已定版</span>
                      <h3>{item.role}</h3>
                    </div>
                    <span className="voice-personality">{item.personality}</span>
                  </div>
                  <dl className="voice-specs">
                    <div><dt>豆包音色</dt><dd>{item.voice}</dd></div>
                    <div><dt>语速</dt><dd>语速 {item.rate}</dd></div>
                    <div><dt>模型</dt><dd>Seed-TTS 2.0</dd></div>
                  </dl>
                  <p>{item.description}</p>
                  <audio
                    className="voice-player"
                    controls
                    preload="metadata"
                    aria-label={`试听${item.role}定版音色`}
                    onPlay={(event) => onPlayMedia(event.currentTarget)}
                  >
                    <source src={item.src} type="audio/mp4" />
                    当前浏览器无法播放此音频。
                  </audio>
                </article>
              ))}
            </div>

            <section className="dialogue-proof" aria-labelledby="dialogue-proof-title">
              <div>
                <span className="voice-status"><Activity aria-hidden="true" size={14} /> 定版片段</span>
                <h3 id="dialogue-proof-title">双猫对话试听</h3>
                <p>黑白猫先说、橘猫回应，共四句。已去除首尾静音，角色间保留自然短停顿。</p>
              </div>
              <audio
                className="voice-player dialogue-player"
                controls
                preload="metadata"
                aria-label="试听双猫定版对话片段"
                onPlay={(event) => onPlayMedia(event.currentTarget)}
              >
                <source src="/audio/two-cat-approved-dialogue.m4a" type="audio/mp4" />
                当前浏览器无法播放此音频。
              </audio>
            </section>

            <div className="voice-rules" aria-label="后续生产声音规则">
              <span>角色映射固定</span>
              <span>禁止合成猫叫</span>
              <span>台词音轨不重叠</span>
              <span>新片沿用同一基线</span>
            </div>
          </div>
        )}

        {activeTab === "repairs" && (
          <div id="panel-repairs" role="tabpanel" aria-labelledby="tab-repairs" className="tab-panel">
            <div className="section-heading">
              <p>BAD CASE REVIEW</p>
              <h2>问题不是被遮住，而是被逐镜纠正</h2>
            </div>

            <article className="repair-row">
              <div className="repair-copy">
                <span className="repair-index">01 / 解剖</span>
                <h3>额外肢体与归属错误</h3>
                <p>模型把猫头、按键爪和画外长前肢并置。修复时限制可见肢体总数，并要求每只前爪持续连接同一身体。</p>
                <ul>
                  <li>S004 仅保留一只画外连接前爪</li>
                  <li>S012 两只前爪持续支撑同一份文件</li>
                </ul>
              </div>
              <div className="evidence-grid evidence-grid-three">
                <EvidenceButton item={evidence.beforePaws} onOpen={setActiveEvidence} />
                <EvidenceButton item={evidence.s004} onOpen={setActiveEvidence} />
                <EvidenceButton item={evidence.s012} onOpen={setActiveEvidence} />
              </div>
            </article>

            <article className="repair-row">
              <div className="repair-copy">
                <span className="repair-index">02 / 物理</span>
                <h3>道具复制与自行移动</h3>
                <p>纸杯镜头曾同时出现碰倒前后两个状态。最终提示固定杯子总数为一个，并写清接触、倾倒、滑动和停止的受力顺序。</p>
              </div>
              <div className="evidence-grid">
                <EvidenceButton item={evidence.s018} onOpen={setActiveEvidence} />
              </div>
            </article>

            <article className="repair-row">
              <div className="repair-copy">
                <span className="repair-index">03 / 动作</span>
                <h3>24 帧直接补到 30 帧造成卡顿</h3>
                <p>旧流程周期性复制原帧。改为双向运动估计后，近似重复相邻帧从 861 组降至 229 组，减少约 73%。</p>
                <div className="result-number">
                  <strong>861</strong>
                  <span>→</span>
                  <strong>229</strong>
                </div>
              </div>
              <div className="evidence-grid evidence-grid-three">
                <EvidenceButton item={evidence.duplicated} onOpen={setActiveEvidence} />
                <EvidenceButton item={evidence.interpolated} onOpen={setActiveEvidence} />
                <EvidenceButton item={evidence.actionAudit} onOpen={setActiveEvidence} />
              </div>
            </article>

            <article className="repair-row">
              <div className="repair-copy">
                <span className="repair-index">04 / 转场</span>
                <h3>硬切突兀，叠化又产生重影</h3>
                <p>0.1 秒交叉叠化会让猫和手机半透明并置，因此标记为未采用。最终只在八个剧情边界加入一个完整黑帧，不重叠主体和道具。</p>
                <span className="rejected-label">未采用：交叉叠化</span>
              </div>
              <div className="evidence-grid">
                <EvidenceButton item={evidence.transitions} onOpen={setActiveEvidence} />
              </div>
            </article>
          </div>
        )}

        {activeTab === "timeline" && (
          <div id="panel-timeline" role="tabpanel" aria-labelledby="tab-timeline" className="tab-panel">
            <div className="section-heading">
              <p>ITERATION LOG</p>
              <h2>每次修改都从一个看得见的问题开始</h2>
            </div>

            <ol className="timeline-list">
              {timeline.map((item) => (
                <li key={`${item.date}-${item.title}`}>
                  <time>{item.date}</time>
                  <div className="timeline-body">
                    <h3>{item.title}</h3>
                    <dl>
                      <div>
                        <dt>发现</dt>
                        <dd>{item.finding}</dd>
                      </div>
                      <div>
                        <dt>处理</dt>
                        <dd>{item.decision}</dd>
                      </div>
                      <div>
                        <dt>效果</dt>
                        <dd>{item.result}</dd>
                      </div>
                    </dl>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}

        {activeTab === "library" && (
          <div id="panel-library" role="tabpanel" aria-labelledby="tab-library" className="tab-panel library-panel">
            <div className="section-heading library-heading">
              <p>HISTORY LIBRARY</p>
              <div>
                <h2>以前做过的，也放在同一个放映室</h2>
                <p className="library-intro">
                  从小说漫剧、角色声音到宠物短剧，保留八个代表性节点。这里展示网页播放副本，原始母版保持不变。
                </p>
              </div>
            </div>

            <div className="archive-filter" aria-label="按素材类型筛选">
              {archiveCategories.map((category) => (
                <button
                  key={category.id}
                  className={archiveCategory === category.id ? "archive-filter-button is-active" : "archive-filter-button"}
                  type="button"
                  aria-pressed={archiveCategory === category.id}
                  onClick={() => setArchiveCategory(category.id)}
                >
                  {category.label}
                </button>
              ))}
            </div>

            <div className="archive-grid">
              {filteredArchiveItems.map((item) => (
                <article className="archive-card" key={`${item.src}-${item.stage}`}>
                  <div className="archive-media">
                    <video
                      controls
                      playsInline
                      preload="metadata"
                      poster={item.poster}
                      aria-label={`播放《${item.title}》${item.stage}`}
                      onPlay={(event) => onPlayMedia(event.currentTarget)}
                    >
                      <source src={item.src} type="video/mp4" />
                      当前浏览器无法播放此视频。
                    </video>
                  </div>
                  <div className="archive-card-body">
                    <div className="archive-meta">
                      <span>{item.date}</span>
                      <span>{item.stage}</span>
                      <span>{item.duration}</span>
                    </div>
                    <h3>{item.title}</h3>
                    <p>{item.description}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}
      </section>

      <footer>
        <span>宠物短剧项目放映室</span>
        <span>FINAL MASTER / V3.1 / 2026</span>
      </footer>

      {activeEvidence && (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={activeEvidence.caption}>
          <button className="lightbox-close" type="button" onClick={() => setActiveEvidence(null)} aria-label="关闭图片预览" autoFocus>
            <X aria-hidden="true" size={22} />
          </button>
          <div className="lightbox-content">
            <img src={activeEvidence.src} alt={activeEvidence.alt} />
            <p>{activeEvidence.caption}</p>
          </div>
        </div>
      )}
    </main>
  );
}
