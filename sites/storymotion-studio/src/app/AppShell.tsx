import {
  Activity,
  Clapperboard,
  FolderKanban,
  Settings,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router";

import type { StageName } from "../api/types";

export const STAGES: ReadonlyArray<{
  name: StageName;
  number: string;
  label: string;
}> = [
  { name: "concept", number: "01", label: "概念" },
  { name: "script", number: "02", label: "剧本" },
  { name: "storyboard", number: "03", label: "分镜" },
  { name: "assets", number: "04", label: "资产" },
  { name: "audio", number: "05", label: "音频" },
  { name: "video", number: "06", label: "视频" },
  { name: "edit", number: "07", label: "剪辑" },
  { name: "eval", number: "08", label: "质检" },
  { name: "deliver", number: "09", label: "交付" },
];

const navigation = [
  { to: "/projects", label: "制作项目", icon: FolderKanban },
  { to: "/works", label: "作品中心", icon: Clapperboard },
  { to: "/settings", label: "设置", icon: Settings },
] as const;

function PrimaryNavigation() {
  return (
    <nav className="primary-navigation" aria-label="主导航">
      {navigation.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => (isActive ? "is-active" : undefined)}
          title={label}
        >
          <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const location = useLocation();
  const activeStage = STAGES.find(({ name }) =>
    location.pathname.includes(`/stages/${name}`),
  )?.name;

  return (
    <div className="app-shell">
      <header className="masthead">
        <NavLink className="brand" to="/projects" aria-label="StoryMotion 创作工作台">
          <span className="brand-mark" aria-hidden="true">SM</span>
          <span className="brand-name">StoryMotion</span>
          <span className="brand-role">创作工作台</span>
        </NavLink>
        <PrimaryNavigation />
        {location.pathname === "/projects" ? (
          <a
            className="icon-button masthead-activity"
            href="#job-activity"
            aria-label="查看作业活动"
            title="查看作业活动"
          >
            <Activity aria-hidden="true" size={17} />
          </a>
        ) : (
          <span className="masthead-spacer" aria-hidden="true" />
        )}
      </header>

      <div className="production-rail-wrap">
        <ol className="production-rail" aria-label="九阶段制作流程">
          {STAGES.map((stage) => (
            <li
              key={stage.name}
              className={activeStage === stage.name ? "is-current" : undefined}
              aria-current={activeStage === stage.name ? "step" : undefined}
            >
              <span className="stage-number">{stage.number}</span>
              <span>{stage.label}</span>
              {activeStage === stage.name && (
                <span className="stage-current-label">当前操作</span>
              )}
            </li>
          ))}
        </ol>
      </div>

      <main className="workbench-main">
        <Outlet />
      </main>
    </div>
  );
}
