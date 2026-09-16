import { isGithub, safeUrl, type Item, type Period } from "../lib/api";

const number = (value: number | null) =>
  value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
export default function TrendCard({ item, period }: { item: Item; period: Period }) {
  const github = isGithub(item);
  const title = github ? item.full_name : item.title;
  const discussion = github
    ? ""
    : `https://news.ycombinator.com/item?id=${encodeURIComponent(item.object_id)}`;
  const url = github
    ? `https://github.com/${item.full_name.split("/").map(encodeURIComponent).join("/")}`
    : safeUrl(item.url, discussion);
  return (
    <article className="trend-card">
      <span className="rank">{item.rank}.</span>
      <div className="card-body">
        <div
          className={`card-heading ${github ? "repo-heading" : "story-heading"}`}
        >
          <h3>
            <a href={url} target="_blank" rel="noreferrer">
              {title}
            </a>
          </h3>
          {github && item.is_new && (
            <span className="new-marker" title="首次收录" aria-label="首次收录">🆕</span>
          )}
        </div>
        {github && item.description && (
          <p className="original">{item.description}</p>
        )}
        {item.summary_zh ? (
          <p className="summary">{item.summary_zh}</p>
        ) : (
          (!github || !item.description) && (
            <p className="summary untranslated">暂无中文摘要，可打开原文阅读。</p>
          )
        )}
        <div className="card-meta">
          {github ? (
            <>
              <span><span aria-hidden="true">⭐</span>{number(item.stars)}</span>
              <span className="growth">
                {{ daily: "本日", weekly: "本周", monthly: "本月" }[period]}新增{" "}
                {item.stars_period == null ? "—" : `+${number(item.stars_period)}`}
              </span>
              {item.language && <span>{item.language}</span>}
              <span title={`首次收录：${item.first_seen}；截至所选日期，跨日／周／月榜按归档日期去重累计，非连续天数。`}>
                在榜 {item.days_on_board} 天
              </span>
            </>
          ) : (
            <>
              <span>
                <span aria-hidden="true">🔥</span>
                {number(item.points)} 分
              </span>
              <a href={discussion} target="_blank" rel="noreferrer">
                <span aria-hidden="true">💬</span>
                {number(item.num_comments)} 条评论
              </a>
              <span className="domain">
                {new URL(url).hostname.replace(/^www\./, "")}
              </span>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
