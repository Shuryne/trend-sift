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
docker compose build
docker compose run --rm trend-sift doctor
docker compose run --rm trend-sift run --dry-run
docker compose run --rm trend-sift run
```

Compose 每次创建一个执行完即退出的任务容器。SQLite 与应用日志保存在命名卷中；项目没有
端口，也不需要常驻容器。定时调度由宿主机 cron 完成，不在容器里运行 cron。

## 配置

| 变量 | 必填 | 默认值 | 说明 |
|---|:---:|---|---|
| `LLM_BASE_URL` | 是 | — | OpenAI 兼容 API 地址 |
| `LLM_API_KEY` | 是 | — | LLM API 密钥 |
| `LLM_MODEL` | 是 | `deepseek-chat` | 模型名称 |
| `LLM_RESPONSE_FORMAT` | 否 | `auto` | `auto`、`json_schema`、`json_object` 或 `text`；已知服务商能力时可固定 |
| `LLM_SUMMARY_INITIAL_TOKENS` | 否 | `1500` | 摘要首次请求的输出额度 |
| `LLM_SUMMARY_MAX_TOKENS` | 否 | `6000` | 推理耗尽时自适应增长的硬上限 |
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

原生部署先执行一次 `uv sync --locked --no-dev`。cron 调用 `deploy/run.sh`，脚本使用已经
同步的环境，不会在每天运行时下载依赖或执行测试。Docker 部署使用宿主 cron 调用
`docker compose run --rm trend-sift run`。完整示例见
[`deploy/crontab.example`](deploy/crontab.example) 和 [部署文档](docs/deployment.md)。

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
