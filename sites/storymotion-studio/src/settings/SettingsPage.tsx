import { AlertCircle, CheckCircle2, KeyRound, LoaderCircle, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";

import type { ProviderCapability, ProviderSettings } from "../api/types";


export interface SettingsPageApi {
  getProviderSettings(): Promise<ProviderSettings>;
}

type SettingsState =
  | { status: "loading" }
  | { status: "ready"; settings: ProviderSettings }
  | { status: "error" };


const CAPABILITY_LABELS = { text: "文本", image: "图像", video: "视频", audio: "音频" } as const;


function CapabilityRow({ name, value }: { name: string; value: ProviderCapability }) {
  const local = value.provider === "local";
  return (
    <div className="provider-row">
      <strong>{name}</strong>
      <span className={value.ready ? "status-passed" : "status-failed"}>
        {value.ready ? <CheckCircle2 aria-hidden="true" size={14} /> : <ShieldAlert aria-hidden="true" size={14} />}
        {value.ready ? "可用" : "未就绪"}
      </span>
      <div><code>{value.provider || "未配置"}</code><span>{value.model || "未选择模型"}</span></div>
      <span className={value.credential_present || local ? "credential-neutral" : "status-failed"}>
        <KeyRound aria-hidden="true" size={13} />
        {local ? "本地运行" : value.credential_present ? "凭据已配置" : "凭据缺失"}
      </span>
    </div>
  );
}


export function SettingsPage({ api }: { api: SettingsPageApi }) {
  const [state, setState] = useState<SettingsState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    void api.getProviderSettings().then(
      (settings) => {
        if (active) setState({ status: "ready", settings });
      },
      () => {
        if (active) setState({ status: "error" });
      },
    );
    return () => { active = false; };
  }, [api]);

  return (
    <div className="page-frame compact-page settings-page">
      <div className="page-heading"><div><p className="eyebrow">PRODUCTION SETTINGS</p><h1>设置</h1></div></div>
      {state.status === "loading" ? <div className="state-row state-busy" role="status"><LoaderCircle className="loading-icon" aria-hidden="true" size={18} /><div><strong>正在读取设置</strong><span>检查模型、音色和交付默认值。</span></div></div> : null}
      {state.status === "error" ? <div className="state-row state-error" role="alert"><AlertCircle aria-hidden="true" size={18} /><div><strong>无法读取制作设置</strong><span>请检查本机制作服务后重试。</span></div></div> : null}
      {state.status === "ready" ? (
        <>
          <section className="settings-section" aria-labelledby="provider-title">
            <div className="section-heading"><h2 id="provider-title">模型与服务</h2><span>{Object.keys(state.settings.capabilities).length} 项能力</span></div>
            {Object.keys(state.settings.capabilities).length ? Object.entries(state.settings.capabilities).map(([key, value]) => value ? <CapabilityRow key={key} name={CAPABILITY_LABELS[key as keyof typeof CAPABILITY_LABELS] ?? key} value={value} /> : null) : <div className="activity-empty">当前没有 Provider 配置</div>}
          </section>

          <section className="settings-section" aria-labelledby="voice-title">
            <div className="section-heading"><h2 id="voice-title">固定角色音色</h2><span>{state.settings.defaults.voice_mapping.length} 个角色</span></div>
            <div className="voice-mapping-table">
              {state.settings.defaults.voice_mapping.map((voice) => <div className="voice-row" key={voice.role_id}><strong>{voice.role_name}</strong><span>{voice.personality}</span><span>{voice.voice_name}</span><code>{voice.speed}</code></div>)}
            </div>
          </section>

          <section className="settings-section" aria-labelledby="output-title">
            <div className="section-heading"><h2 id="output-title">输出与费用</h2></div>
            <dl className="production-defaults">
              <div><dt>画幅</dt><dd>{state.settings.defaults.output.aspect_ratio}</dd></div>
              <div><dt>分辨率</dt><dd>{state.settings.defaults.output.resolution}</dd></div>
              <div><dt>帧率</dt><dd>{state.settings.defaults.output.fps} fps</dd></div>
              <div><dt>目标时长</dt><dd>{state.settings.defaults.output.target_duration_seconds} 秒</dd></div>
              <div><dt>并发</dt><dd>{state.settings.defaults.generation.concurrency}</dd></div>
              <div><dt>费用上限</dt><dd>{state.settings.defaults.generation.fee_cap_yuan === null ? "未设置费用上限" : `¥${state.settings.defaults.generation.fee_cap_yuan.toFixed(2)}`}</dd></div>
            </dl>
          </section>
        </>
      ) : null}
    </div>
  );
}
