import { AlertCircle, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useParams,
} from "react-router";

import { apiClient, type ApiClient } from "../api/client";
import type {
  ProviderCapability,
  ProviderSettings,
  WorkCapability,
} from "../api/types";
import {
  ProjectListPage,
  type ProjectListApi,
} from "../projects/ProjectListPage";
import {
  ProjectWorkspacePage,
  type ProjectWorkspaceApi,
} from "../projects/ProjectWorkspacePage";
import { AppShell } from "./AppShell";

type AppApi = ProjectListApi &
  Partial<ProjectWorkspaceApi> &
  Partial<Pick<ApiClient, "getProviderSettings" | "works">>;

function WorksPage({
  detail = false,
  works,
}: {
  detail?: boolean;
  works: WorkCapability;
}) {
  const { id } = useParams();
  return (
    <div className="page-frame compact-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DELIVERED WORKS</p>
          <h1>{detail ? "作品版本" : "作品中心"}</h1>
          {detail ? <code className="heading-id">{id}</code> : null}
        </div>
      </div>
      <div className="empty-state">
        <div>
          <strong>
            {works.availability === "unavailable"
              ? "作品目录尚未接入"
              : "作品目录已连接"}
          </strong>
          <span>
            {works.availability === "unavailable"
              ? "当前版本不会请求尚未提供的作品接口。"
              : "作品浏览将在本地目录启用后显示。"}
          </span>
        </div>
      </div>
    </div>
  );
}

function CapabilityRow({ name, value }: { name: string; value: ProviderCapability }) {
  return (
    <div className="settings-row">
      <strong>{name}</strong>
      <span className={value.ready ? "provider-ready" : "provider-unavailable"}>
        {value.ready ? "可用" : "未就绪"}
      </span>
      <code title={value.provider || "未配置 Provider"}>{value.provider || "-"}</code>
      <span title={value.model || "未配置模型"}>{value.model || "未配置模型"}</span>
    </div>
  );
}

function SettingsPage({ api }: { api: AppApi }) {
  const [settings, setSettings] = useState<
    { status: "loading" } | { status: "ready"; value: ProviderSettings } | { status: "error" }
  >(() =>
    api.getProviderSettings
      ? { status: "loading" }
      : { status: "ready", value: { capabilities: {} } },
  );

  useEffect(() => {
    if (!api.getProviderSettings) return;
    let active = true;
    void api.getProviderSettings().then(
      (value) => {
        if (active) setSettings({ status: "ready", value });
      },
      () => {
        if (active) setSettings({ status: "error" });
      },
    );
    return () => {
      active = false;
    };
  }, [api]);

  const capabilities = settings.status === "ready"
    ? Object.entries(settings.value.capabilities)
    : [];
  const labels: Record<string, string> = {
    text: "文本",
    image: "图像",
    video: "视频",
    audio: "音频",
  };

  return (
    <div className="page-frame compact-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">PROVIDER STATUS</p>
          <h1>设置</h1>
        </div>
      </div>
      <section className="settings-section" aria-labelledby="provider-title">
        <div className="section-heading">
          <h2 id="provider-title">Provider 状态</h2>
        </div>
        {settings.status === "loading" ? (
          <div className="state-row" role="status"><LoaderCircle aria-hidden="true" size={18} />正在读取设置</div>
        ) : null}
        {settings.status === "error" ? (
          <div className="state-row state-error" role="alert"><AlertCircle aria-hidden="true" size={18} />无法读取 Provider 状态</div>
        ) : null}
        {settings.status === "ready" && capabilities.length === 0 ? (
          <div className="activity-empty">当前没有可用的 Provider 配置</div>
        ) : null}
        {capabilities.map(([name, value]) => value ? (
          <CapabilityRow key={name} name={labels[name] ?? name} value={value} />
        ) : null)}
      </section>
    </div>
  );
}

export function App({ api = apiClient }: { api?: AppApi }) {
  const works: WorkCapability = api.works ?? {
    availability: "unavailable",
    reason: "local_catalog_not_configured",
  };
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="projects" element={<ProjectListPage api={api} />} />
          <Route path="projects/:id" element={<ProjectWorkspacePage api={api as ProjectWorkspaceApi} />} />
          <Route
            path="projects/:id/stages/:stage"
            element={<ProjectWorkspacePage api={api as ProjectWorkspaceApi} />}
          />
          <Route path="works" element={<WorksPage works={works} />} />
          <Route path="works/:id" element={<WorksPage detail works={works} />} />
          <Route path="settings" element={<SettingsPage api={api} />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
