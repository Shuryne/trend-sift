export type Source = "github" | "hacker-news";
export type Period = "daily" | "weekly" | "monthly";
export interface GithubItem {
  rank: number;
  full_name: string;
  is_new: boolean;
  days_on_board: number;
  first_seen: string;
  description: string | null;
  language: string | null;
  stars: number | null;
  stars_period: number | null;
  summary_zh: string | null;
}
export interface HackerNewsItem {
  rank: number;
  object_id: string;
  title: string;
  url: string | null;
  points: number | null;
  num_comments: number | null;
  summary_zh: string | null;
}
export type Item = GithubItem | HackerNewsItem;
export interface Board {
  content_date?: string | null;
  date: string | null;
  dates: string[];
  updated_at: string | null;
  items: Item[];
}
export function isGithub(item: Item): item is GithubItem {
  return "full_name" in item;
}
export function safeUrl(value: string | null, fallback: string): string {
  try {
    const url = new URL(value || fallback);
    return ["https:", "http:"].includes(url.protocol) ? url.href : fallback;
  } catch {
    return fallback;
  }
}
export async function fetchBoard(
  source: Source,
  period: Period,
  date: string,
  signal: AbortSignal,
): Promise<Board> {
  const params = new URLSearchParams({ period });
  if (source === "hacker-news") params.set("date_basis", "edition");
  if (date) params.set("date", date);
  const response = await fetch(`/api/${source}?${params}`, { signal });
  if (!response.ok)
    throw new Error(
      response.status === 422
        ? "日期或榜单参数无效，请返回最新榜单。"
        : "暂时无法读取榜单，请稍后重试。",
    );
  return response.json();
}
