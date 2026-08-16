import { AlertCircle, Archive, Clapperboard, LoaderCircle, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import type { WorkSummary } from "../api/types";


export interface WorksPageApi {
  listWorks(signal?: AbortSignal): Promise<WorkSummary[]>;
}

type LoadState =
  | { status: "loading" }
  | { status: "ready"; works: WorkSummary[] }
  | { status: "error" };


function sourceLabel(work: WorkSummary): string {
  if (work.source === "delivered") return "正式交付";
  return work.title === "历史归档" ? "未归类素材" : "历史素材";
}


function modeLabel(mode: WorkSummary["mode"]): string {
  return {
    original: "原创短剧",
    novel: "小说改编",
    replica: "参考复刻",
    historical: "历史归档",
  }[mode];
}


export function WorksPage({ api }: { api: WorksPageApi }) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [query, setQuery] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void api.listWorks(controller.signal).then(
      (works) => setState({ status: "ready", works }),
      () => {
        if (!controller.signal.aborted) setState({ status: "error" });
      },
    );
    return () => controller.abort();
  }, [api]);

  const visible = useMemo(() => {
    if (state.status !== "ready") return [];
    const selected = query.trim().toLocaleLowerCase("zh-CN");
    if (!selected) return state.works;
    return state.works.filter((work) =>
      [work.title, work.project_id, work.current_version, modeLabel(work.mode)]
        .join(" ")
        .toLocaleLowerCase("zh-CN")
        .includes(selected),
    );
  }, [query, state]);

  return (
    <div className="page-frame compact-page works-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DELIVERED WORKS</p>
          <h1>作品中心</h1>
        </div>
        {state.status === "ready" ? <span className="catalog-count">{state.works.length} 件作品</span> : null}
      </div>

      {state.status === "ready" && state.works.length ? (
        <label className="works-filter">
          <Search aria-hidden="true" size={16} />
          <span className="sr-only">筛选作品</span>
          <input
            type="search"
            aria-label="筛选作品"
            placeholder="按名称、项目或版本筛选"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query ? (
            <button className="icon-button" type="button" title="清除筛选" aria-label="清除筛选" onClick={() => setQuery("")}>
              <X aria-hidden="true" size={15} />
            </button>
          ) : <span aria-hidden="true" />}
          <code>{visible.length} 个结果</code>
        </label>
      ) : null}

      {state.status === "loading" ? (
        <div className="state-row state-busy" role="status" aria-label="正在读取作品目录">
          <LoaderCircle className="loading-icon" aria-hidden="true" size={18} />
          <div><strong>正在读取作品目录</strong><span>核对正式交付与历史归档。</span></div>
        </div>
      ) : null}
      {state.status === "error" ? (
        <div className="state-row state-error" role="alert">
          <AlertCircle aria-hidden="true" size={18} />
          <div><strong>无法读取作品目录</strong><span>请检查本机制作服务后重新打开页面。</span></div>
        </div>
      ) : null}
      {state.status === "ready" && state.works.length === 0 ? (
        <div className="empty-state">
          <Clapperboard aria-hidden="true" size={20} />
          <div><strong>还没有可查看的作品</strong><span>完成交付审核后，作品会出现在这里。</span></div>
        </div>
      ) : null}
      {state.status === "ready" && state.works.length > 0 && visible.length === 0 ? (
        <div className="empty-state">
          <Search aria-hidden="true" size={20} />
          <div><strong>没有匹配的作品</strong><span>换一个名称、项目 ID 或版本号。</span></div>
        </div>
      ) : null}

      {visible.length ? (
        <section className="works-list" aria-label="作品列表">
          {visible.map((work) => (
            <article className="work-card" key={work.work_id}>
              <div className={`work-source-mark source-${work.source}`} aria-hidden="true">
                {work.source === "delivered" ? <Clapperboard size={18} /> : <Archive size={18} />}
              </div>
              <div className="work-identity">
                <div className="work-kicker">
                  <span>{sourceLabel(work)}</span>
                  <span>{modeLabel(work.mode)}</span>
                </div>
                <h2><Link to={`/works/${encodeURIComponent(work.work_id)}`}>{work.title}</Link></h2>
                <code>{work.project_id || work.work_id}</code>
              </div>
              <div className="work-version">
                <span>当前版本</span>
                <strong>{work.current_version || "未标注"}</strong>
                <time>{work.delivered_at ? new Date(work.delivered_at).toLocaleDateString("zh-CN") : "日期未记录"}</time>
              </div>
              <Link className="action-link work-open-link" to={`/works/${encodeURIComponent(work.work_id)}`}>
                查看作品
              </Link>
            </article>
          ))}
        </section>
      ) : null}
    </div>
  );
}
