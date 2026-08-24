# 架构与数据分层

## 模块边界

```text
cli/                 参数解析与终端展示
core/                配置、SQLite 连接、schema、LLM 客户端
notifications/       飞书协议与发送逻辑
sources/github/      GitHub Trending 独立流水线
sources/hacker_news/ Hacker News 独立流水线
```

两个数据源共享配置、数据库连接、LLM 客户端和通知传输，但各自保留模型、抓取、解析、富化、
摘要提示词和查询逻辑。新增数据源时不要求继承公共基类；只有出现稳定且相同的行为后才抽象。

## 流水线

每个数据源按 `fetch → enrich → summarize → notify` 执行。顶层 `run` 会依次运行两个数据源，
一侧失败不会阻止另一侧执行。`--dry-run` 完成前面阶段并生成真实卡片预览，但不发送请求。

## SQLite 分层

| 层 | GitHub | Hacker News | 含义 |
|---|---|---|---|
| 原始归档 | `gh_raw_pages` | `hn_raw_feeds` | gzip 压缩的原始 HTML/JSON |
| 快照 | `gh_snapshots` | `hn_snapshots` | 按日期保存的不可变榜单记录 |
| 富化 | `gh_repos` | `hn_stories` | API 元信息、README 或正文 |
| 摘要 | `gh_summaries` | `hn_summaries` | 按 prompt 版本保存的派生结果 |
| 通知 | `gh_notifications` | `hn_notifications` | 首次推送记录与幂等依据 |

`runs` 记录每次流水线状态。原始数据和派生结果分开后，可以从历史响应重放解析，也可以升级
提示词后重算摘要而不污染抓取证据。

## 时间语义

GitHub 默认使用 `Asia/Shanghai` 当天。HN 的得分需要时间积累，默认抓取 UTC 的两天前，
也可以通过 `--hn-date` 或数据源命令的 `--date` 显式指定。两者不能共用一个含糊的日期参数。
