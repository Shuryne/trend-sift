# trend-sift

`trend-sift` 定时聚合 GitHub Trending 与 Hacker News，使用兼容 OpenAI API 的 LLM
生成简洁中文摘要，并通过飞书自定义机器人推送。原始响应、结构化快照、富化数据、摘要与
通知记录分层保存在 SQLite 中，可重放、可追溯、可幂等重跑。

```text
GitHub Trending ─┐
                 ├─ 抓取 → 归档 → 解析 → 富化 → LLM 摘要 → 飞书卡片
Hacker News ─────┘
```

## 功能

- GitHub 日榜、周榜、月榜抓取，以及 topics、README 和项目元信息富化。
- Hacker News 指定 UTC 日期的高分帖子抓取与原文正文提取。
- 独立、版本化的中文摘要提示词，单条失败不会阻断整批任务。
- SQLite 分层归档、历史查询、解析重放和通知幂等。
- 原生 uv 与 Docker Compose 两种部署方式。

## 原生 uv 快速开始

要求 Python 3.11–3.13 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone <你的仓库地址>
cd trend-sift
uv sync --locked
cp .env.example .env
# 编辑 .env
uv run trend-sift doctor
uv run trend-sift run --dry-run
uv run trend-sift run
```

`uv sync` 只需在首次安装或依赖变更后执行。正常启动不需要先运行测试；测试和静态检查是
开发/CI 环节，通过 `make check` 执行。

## Docker 快速开始

Docker 用户不需要安装 Python 或 uv：

```bash
cp .env.example .env
# 编辑 .env
docker compose up -d --build
```

一个 Web 服务内置每日定时任务，默认北京时间 **09:00** 抓取、生成摘要并推送，无需 crontab。
时间通过 `.env` 的 `SCHEDULE_TIME` / `SCHEDULE_TIMEZONE` 配置；数据库在项目 **`data/trending.db`**，
日志在 `logs/`。启动后等待下一次计划时间，不立即抓取或补跑。
首次部署需填写 `.env` 并构建镜像，之后直接 `docker compose up -d`。

## 网页阅读

白色浅色主题，左侧 HN 日榜、右侧 GitHub 榜单，GitHub 可切换日／周／月；支持统一期数日期（HN 默认展示两天前的内容）、
当前两榜搜索和排序，原始描述与中文摘要直接展开。

```bash
# Docker：构建前端并启动网页及内置定时任务
docker compose up -d --build
```

默认仅绑定服务器本机 `127.0.0.1:8111`，本地打开 http://127.0.0.1:8111。
公网域名与反向代理配置见 [部署指南](docs/deployment.md#网页服务)。
网页读取共享归档，后端定时任务负责生成数据；首次抓取完成前页面可能为空。

前端开发（需要 Node.js 22 和 pnpm 10.33.2）：

```bash
uv sync --locked
pnpm --dir web install --frozen-lockfile
make dev  # 同时启动前后端，打开 http://127.0.0.1:8111；Ctrl+C 一起停止
```

本地生产预览运行 `make web-build` 后，启动 `make api-dev`，再在另一个终端执行
`pnpm --dir web run preview`，打开 http://127.0.0.1:8111。开发和预览均将 API 请求转发至 8000 端口。
前端提交前运行 `make web-build`；Python 检查仍使用 `make check`。
统一样式见 [网页设计规范](docs/design-system.md)。

## 配置

| 变量 | 必填 | 默认值 | 说明 |
|---|:---:|---|---|
| `LLM_BASE_URL` | 是 | — | OpenAI 兼容 API 地址 |
| `LLM_API_KEY` | 是 | — | LLM API 密钥 |
| `LLM_MODEL` | 是 | `deepseek-chat` | 模型名称 |
| `LLM_RESPONSE_FORMAT` | 否 | `auto` | `auto`、`json_schema`、`json_object` 或 `text`；已知服务商能力时可固定 |
| `LLM_SUMMARY_INITIAL_TOKENS` | 否 | `1500` | 摘要首次请求的输出额度 |
| `LLM_SUMMARY_MAX_TOKENS` | 否 | `6000` | 推理耗尽时自适应增长的硬上限 |
| `LLM_SUMMARY_CONCURRENCY` | 否 | `5` | 同时生成的摘要数，范围 1–20；设为 1 可恢复串行 |
| `GITHUB_TOKEN` | 建议 | — | 公开仓库只读 token；避免匿名限流 |
| `GITHUB_PERIODS` | 否 | `daily,weekly,monthly` | GitHub 榜单周期 |
| `GITHUB_REQUEST_DELAY_SECONDS` | 否 | `2.0` | GitHub 请求间隔 |
| `FEISHU_WEBHOOK_URL` | 推送时 | — | 飞书自定义机器人 Webhook |
| `FEISHU_SECRET` | 否 | — | 机器人签名密钥 |
| `HN_LAG_DAYS` | 否 | `2` | 默认抓取几天前的 UTC 榜单 |
| `HN_MIN_POINTS` | 否 | `100` | HN 最低分数 |
| `HN_TOP_N` | 否 | `25` | HN 最多条数，范围 1–35 |
| `NOTIFY_MODE` | 否 | `digest` | `digest` 或 `new_only` |
| `EXPANDED_PER_SECTION` | 否 | `5` | 卡片默认展开条数 |
| `TREND_SIFT_DB_PATH` | 否 | `data/trending.db` | SQLite 文件位置 |
| `TREND_SIFT_LOG_DIR` | 否 | `logs` | 日志目录 |
| `SCHEDULE_ENABLED` | 否 | Docker 为 `true`，原生为 `false` | 开启 Web 内置调度 |
| `SCHEDULE_TIME` | 否 | `09:00` | 每日时间，格式 `HH:MM` |
| `SCHEDULE_TIMEZONE` | 否 | `Asia/Shanghai` | 调度时区 |

密钥只应保存在 `.env` 或部署平台的秘密管理系统中，不要提交到 Git。

## CLI

```bash
trend-sift run [--dry-run] [--github-date YYYY-MM-DD] [--hn-date YYYY-MM-DD]
trend-sift doctor

trend-sift github run|fetch|enrich|summarize|notify|show|history|reparse
trend-sift hacker-news run|fetch|enrich|summarize|notify|show|reparse
```

使用 `trend-sift <命令> --help` 查看完整参数。GitHub 默认使用上海时区当天；HN 默认使用
UTC 的 `HN_LAG_DAYS` 天前。顶层命令使用两个独立日期参数，避免混淆。

## 定时运行

推荐使用 `docker compose up -d --build` 启动单个 Web 服务，后端单 worker 内按 `.env` 配置定时执行。
默认每天北京时间 09:00，启动不立即运行，不补跑或整批自动重试。
首次需数据可执行 `docker compose exec web trend-sift run --dry-run`。
原有 cron 方式保留为可选方案，不应与内置调度同时启用。详见 [部署文档](docs/deployment.md)。

## 开发

```bash
make setup
make check
```

代码遵循 Ruff 格式和检查规则，公共接口提供类型标注。代码标识符、注释和 docstring 使用
英文，面向用户的文档和 CLI 输出使用中文。详见 [贡献指南](CONTRIBUTING.md)。

## 文档

- [架构与数据分层](docs/architecture.md)
- [设计决策](docs/design-decisions.md)
- [原生与 Docker 部署](docs/deployment.md)
- [安全策略](SECURITY.md)

## 许可证

[MIT](LICENSE) © 2026 trend-sift contributors
