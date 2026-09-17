# 架构与数据分层

## 模块边界

```text
cli/                 参数解析与终端展示
scheduler.py         Web 进程内的每日定时任务
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

## 网页与查询 API

`web/` 是独立的 React + TypeScript + Vite 工程，依赖与锁文件保存在该目录。
`src/trend_sift/api/` 是 FastAPI 查询入口；启动时可按配置创建每日后台任务。
查询路由只读取归档，定时任务通过线程调用抓取、摘要和通知流水线。
第一版只有两个业务路由，暂时集中于 `app.py`；业务扩展时再拆分 `routes/` 与数据查询模块。
HTTP 响应使用 `schemas.py` 的显式模型，不直接暴露完整 SQLite 行。

- `GET /api/github?period=daily&date=YYYY-MM-DD`
- `GET /api/hacker-news?date=YYYY-MM-DD`
- `GET /api/health`

省略日期时返回对应来源、周期的最新快照日期。响应包含 `date`、倒序 `dates`、
`updated_at` 与 `items`。指定没有快照的合法日期返回空列表；非法日期或周期返回 422。
HN API 默认仍按内容日期查询；Web 传 `date_basis=edition` 时，查询日期减去配置的 `HN_LAG_DAYS`，
返回的 `date` / `dates` 为期数日期，`content_date` 始终为实际 UTC 内容日期。
该映射按当前配置计算，不使用补抓或重跑的执行时间；缺失的期数不回退到其他内容日期。
每个条目选择最新非空摘要，避免多个提示词版本导致重复条目；摘要未冻结到历史日期。
搜索与排序在前端对当前完整榜单执行，目前无需分页；跨日期全文检索应另增后端接口。

启动时幂等初始化已有 schema，每次查询使用独立的 SQLite 只读连接。Web 服务与批处理
共享项目 data/ 挂载目录，延续 WAL 模式；不适合让多个服务器跨网络文件系统共享 SQLite。
生产环境由 Python 提供前端构建产物，页面和 API 同源；开发时 Vite 代理 `/api`。
视觉规范见 [网页设计规范](design-system.md)。

GitHub 响应额外提供 `is_new`、`days_on_board`、`first_seen`。统计仅使用不晚于所选快照日期的本地归档，
跨周期按日期去重；累计在榜天数不包含缺失日期，不等同于连续上榜天数。

## 定时执行

Compose 运行单个 Web 服务、单 worker，启动时创建 `scheduler.daily_loop`。
默认每天 `Asia/Shanghai` 09:00，可通过 `SCHEDULE_TIME` 和 `SCHEDULE_TIMEZONE` 修改。
启动后等待下一次计划时间，不补跑。同步流水线在线程中执行，结束后再等待下一次，不会在
进程内重叠；关闭时取消定时等待，已经开始的同步线程不能被 asyncio 强制取消。
不增加调度状态表、整批自动重试或跨进程锁；手动执行应避开定时抓取。
