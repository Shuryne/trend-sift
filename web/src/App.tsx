import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Github,
  Layers2,
  Newspaper,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import TrendCard from "./components/TrendCard";
import DateSelect from "./components/DateSelect";
import {
  fetchBoard,
  isGithub,
  type Board,
  type Period,
  type Source,
} from "./lib/api";

const panels: { key: string; source: Source; period: Period; title: string }[] =
  [
    {
      key: "hn",
      source: "hacker-news",
      period: "daily",
      title: "Hacker News · 日榜",
    },
    { key: "daily", source: "github", period: "daily", title: "GitHub · 日榜" },
    {
      key: "weekly",
      source: "github",
      period: "weekly",
      title: "GitHub · 周榜",
    },
    {
      key: "monthly",
      source: "github",
      period: "monthly",
      title: "GitHub · 月榜",
    },
  ];
type Dates = { date: string; period: Period };
type PanelState = { board: Board | null; error: string; loading: boolean };
const initialStates = (): PanelState[] =>
  panels.map(() => ({ board: null, error: "", loading: true }));
function readLocation(): Dates {
  const params = new URLSearchParams(window.location.search);
  return {
    period:
      params.get("period") === "weekly"
        ? "weekly"
        : params.get("period") === "monthly"
          ? "monthly"
          : "daily",
    date:
      params.get("date") ||
      params.get("hnDate") ||
      params.get("githubDate") ||
      "",
  };
}
export default function App() {
  const [dates, setDates] = useState(readLocation);
  const [states, setStates] = useState<PanelState[]>(initialStates);
  const [retry, setRetry] = useState(0);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("rank");
  function navigate(next: Dates) {
    const params = new URLSearchParams();
    if (next.period !== "daily") params.set("period", next.period);
    if (next.date) params.set("date", next.date);
    window.history.pushState(
      null,
      "",
      params.size ? `?${params}` : window.location.pathname,
    );
    setDates(next);
  }
  useEffect(() => {
    const onPop = () => setDates(readLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStates(initialStates());
    const update = (index: number, state: PanelState) => {
      if (!controller.signal.aborted)
        setStates((current) =>
          current.map((old, i) => (i === index ? state : old)),
        );
    };
    const request = async (
      index: number,
      date: string,
    ): Promise<PanelState> => {
      try {
        const panel = panels[index];
        return {
          board: await fetchBoard(
            panel.source,
            panel.period,
            date,
            controller.signal,
          ),
          error: "",
          loading: false,
        };
      } catch (reason) {
        return {
          board: null,
          error:
            reason instanceof Error ? reason.message : "读取失败，请重试。",
          loading: false,
        };
      }
    };
    void (async () => {
      const results = await Promise.all(
        panels.map((_, index) => request(index, dates.date)),
      );
      // Align editions; the HN API maps each edition to its delayed content date.
      const commonDate =
        dates.date ||
        results
          .flatMap((result) => result.board?.dates || [])
          .sort()
          .at(-1) ||
        "";
      await Promise.all(
        results.map(async (result, index) => {
          if (controller.signal.aborted) return;
          const aligned =
            result.board && result.board.date !== commonDate && commonDate
              ? await request(index, commonDate)
              : result;
          update(index, aligned);
        }),
      );
    })();
    return () => controller.abort();
  }, [dates.date, retry]);
  const availableDates = [
    ...new Set(states.flatMap((state) => state.board?.dates || [])),
  ]
    .sort()
    .reverse();
  const currentDate =
    dates.date ||
    states.map((state) => state.board?.date || "").sort().at(-1) ||
    "";
  const previousDate = availableDates.find((date) => date < currentDate) || "";
  const nextDate = [...availableDates].reverse().find((date) => date > currentDate) || "";
  const visibleItems = useMemo(
    () =>
      states.map((state) => {
        const query = search.trim().toLocaleLowerCase();
        const result = (state.board?.items || []).filter((item) =>
          [
            item.summary_zh,
            isGithub(item) ? item.full_name : item.title,
            isGithub(item) ? item.description : "",
            isGithub(item) ? item.language : "",
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query),
        );
        if (sort === "popular")
          result.sort(
            (a, b) =>
              (isGithub(b) ? b.stars || 0 : b.points || 0) -
              (isGithub(a) ? a.stars || 0 : a.points || 0),
          );
        return result;
      }),
    [states, search, sort],
  );
  return (
    <div className="dashboard">
      <a className="skip-link" href="#main">
        跳到内容
      </a>
      <header className="topbar">
        <a className="brand" href="/" aria-label="Trend Sift 首页">
          <span className="brand-mark">
            <Layers2 size={18} />
          </span>
          trend sift<span className="brand-period">.</span>
        </a>
        <div className="date-toolbar" aria-label="归档日期">
          <DateSelect
            value={dates.date}
            dates={availableDates}
            currentDate={currentDate}
            onChange={(date) => navigate({ ...dates, date })}
          />
          <div className="archive-navigation" role="group" aria-label="切换归档">
            <button className="latest-button" disabled={!previousDate}
              onClick={() => navigate({ ...dates, date: previousDate })}>
              <ChevronLeft size={14} />上一期
            </button>
            <button className="latest-button" disabled={!nextDate}
              onClick={() => navigate({ ...dates, date: nextDate })}>
              下一期<ChevronRight size={14} />
            </button>
          </div>
          <button
            className="refresh-button"
            onClick={() => setRetry((value) => value + 1)}
            disabled={states.some((state) => state.loading)}
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
      </header>
      <main id="main">
        <div className="filterbar">
          <h1>
            趋势速览<span>社区热议与开源趋势</span>
          </h1>
          <div className="filters">
            <div className="search-box">
              <Search size={14} />
              <input
                aria-label="搜索当前两个榜单"
                placeholder="搜索标题、描述、语言或摘要…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              {search && (
                <button aria-label="清空搜索" onClick={() => setSearch("")}>
                  <X size={14} />
                </button>
              )}
            </div>
            <select
              aria-label="排序方式"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
            >
              <option value="rank">榜单顺序</option>
              <option value="popular">热度优先</option>
            </select>
          </div>
        </div>
        <div className="board-grid">
          {[
            0,
            panels.findIndex(
              (panel) =>
                panel.source === "github" && panel.period === dates.period,
            ),
          ].map((index) => {
            const panel = panels[index];
            const { board, error, loading } = states[index];
            const items = visibleItems[index];
            return (
              <section
                className={`board-panel ${index === 0 ? "hn-panel" : "github-panel"}`}
                aria-labelledby={`title-${panel.key}`}
                key={panel.key}
              >
                <header className="board-header">
                  <div className="board-title">
                    {index === 0 ? (
                      <Newspaper size={17} />
                    ) : (
                      <Github size={17} />
                    )}
                    <h2 id={`title-${panel.key}`}>
                      {index === 0 ? panel.title : "GitHub Trending"}
                    </h2>
                    <span className="count">
                      {loading ? "…" : error ? "—" : items.length}
                    </span>
                    {index !== 0 && (
                      <div
                        className="period-tabs"
                        role="group"
                        aria-label="GitHub 榜单周期"
                      >
                        {(["daily", "weekly", "monthly"] as Period[]).map(
                          (period) => (
                            <button
                              key={period}
                              aria-pressed={dates.period === period}
                              onClick={() => navigate({ ...dates, period })}
                            >
                              {
                                {
                                  daily: "日榜",
                                  weekly: "周榜",
                                  monthly: "月榜",
                                }[period]
                              }
                            </button>
                          ),
                        )}
                      </div>
                    )}
                  </div>
                  <div className="board-subtitle">
                    <span>
                      {index === 0 && board?.content_date
                        ? `${board.content_date} · 内容日期（UTC）`
                        : board?.date || dates.date || "暂无归档"}
                    </span>
                  </div>
                </header>
                <div
                  className="board-content"
                  aria-live="polite"
                  aria-busy={loading}
                >
                  {loading ? (
                    <div
                      className="skeleton-list"
                      aria-label={`${panel.title}加载中`}
                    >
                      {[1, 2, 3].map((key) => (
                        <div className="skeleton" key={key}>
                          <i />
                          <i />
                          <i />
                        </div>
                      ))}
                    </div>
                  ) : error ? (
                    <div className="empty-state" role="alert">
                      <h3>暂时无法读取</h3>
                      <p>{error}</p>
                      <button onClick={() => setRetry((value) => value + 1)}>
                        重新加载
                      </button>
                    </div>
                  ) : items.length ? (
                    items.map((item) => (
                      <TrendCard
                        key={isGithub(item) ? item.full_name : item.object_id}
                        item={item}
                        period={panel.period}
                      />
                    ))
                  ) : (
                    <div className="empty-state">
                      <h3>{search ? "没有匹配的内容" : "这一天暂无归档"}</h3>
                      <p>
                        {search
                          ? "换个关键词，或清空搜索查看全部内容。"
                          : "请选择其他日期，或等待抓取完成。"}
                      </p>
                      {search && (
                        <button onClick={() => setSearch("")}>清空搜索</button>
                      )}
                    </div>
                  )}
                </div>
                {board?.updated_at && (
                  <footer className="board-footer">
                    抓取于 {board.updated_at.replace("T", " ").slice(0, 16)}
                  </footer>
                )}
              </section>
            );
          })}
        </div>
        <nav className="date-pagination" aria-label="按日期翻页">
          <button
            className="latest-button"
            disabled={!previousDate}
            onClick={() => navigate({ ...dates, date: previousDate })}
          >
            上一期
          </button>
          <button
            className="latest-button"
            disabled={!nextDate}
            onClick={() => navigate({ ...dates, date: nextDate })}
          >
            下一期
          </button>
        </nav>
        <footer className="page-footer">
          <span>
            日期按每日期数切换；HN 展示该期按配置回溯抓取的内容（默认两天前），
            卡片标注 UTC 内容日期。切换 GitHub 周期保留所选日期。
          </span>
          <span>摘要由 AI 生成 · 历史条目显示最新可用摘要</span>
        </footer>
      </main>
    </div>
  );
}
