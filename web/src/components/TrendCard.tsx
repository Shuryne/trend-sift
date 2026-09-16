import { ArrowUpRight, MessageCircle, Star, TrendingUp } from "lucide-react";
import { isGithub, safeUrl, type Item } from "../lib/api";

const number = (value: number | null) =>
  value == null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
export default function TrendCard({ item }: { item: Item }) {
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
      <span className="rank">{String(item.rank).padStart(2, "0")}</span>
      <div className="card-body">
        <div
          className={`card-heading ${github ? "repo-heading" : "story-heading"}`}
        >
          <h3>
            <a href={url} target="_blank" rel="noreferrer">
              {title}
              <ArrowUpRight size={16} />
            </a>
          </h3>
          {github && (
            <span
              className={`board-status ${item.is_new ? "is-new" : "returning"}`}
              title={`首次收录：${item.first_seen}；截至所选日期，跨日／周／月榜按归档日期去重累计，非连续天数。`}
            >
              {item.is_new
                ? "new"
                : `在榜${item.days_on_board}天`}
            </span>
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
              <span>
                <i className="language-dot" />
                {item.language || "未标注语言"}
              </span>
              <span>
                <Star size={14} />
                {number(item.stars)}
              </span>
              <span className="growth">
                <TrendingUp size={14} />
                {item.stars_period == null
                  ? "—"
                  : `+${number(item.stars_period)}`}{" "}
                <span className="meta-label">本期</span>
              </span>
            </>
          ) : (
            <>
              <span>
                <TrendingUp size={14} />
                {number(item.points)} 分
              </span>
              <a href={discussion} target="_blank" rel="noreferrer">
                <MessageCircle size={14} />
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
