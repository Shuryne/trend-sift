# 部署指南

## Docker Compose（推荐）

在项目目录准备 `.env`：

```bash
cp .env.example .env  # 仅首次；已有配置不要覆盖
```

填写 LLM、GitHub、飞书配置。每日任务配置为：

```dotenv
SCHEDULE_ENABLED=true
SCHEDULE_TIME=09:00
SCHEDULE_TIMEZONE=Asia/Shanghai
```

然后启动：

```bash
docker compose up -d --build
```

首次构建或更新代码时加 `--build`；之后只需 `docker compose up -d`。
修改 `.env` 后重新执行 `docker compose up -d`，让容器加载新配置。

Compose 只有一个 `web` 服务：单副本、单 Uvicorn worker，同时提供网页、API 和每日调度。
不需要宿主 crontab、独立 scheduler 容器或任务队列。若以前安装过 cron 任务，请禁用它。

## 每日任务

后端启动时等待下一个北京时间 **09:00**，到点后在线程中调用现有抓取、摘要和飞书推送流水线，
不会阻塞网页请求。执行完成后等待下一次计划时间，任务不会在进程内重叠。

- 启动不立即执行，不补跑停机期间错过的任务。
- 失败记录日志，下一次按计划运行；不增加整批重试，现有外部请求重试逻辑保留。
- `SCHEDULE_TIME` 格式为 `HH:MM`，`SCHEDULE_TIMEZONE` 使用 IANA 时区；修改后需重新创建容器。
- `SCHEDULE_ENABLED=false` 可关闭调度，只提供网页和 API。
- 服务关闭时取消定时等待；已经开始的同步抓取线程不能被 asyncio 强制取消，容器停止可能中断
  尚未完成的流水线。更新服务尽量避开抓取期间。

首次需要数据时，可在每日任务未运行时手动执行一次：

```bash
# 抓取并生成摘要，不推送飞书
docker compose exec web trend-sift run --dry-run
# 或完整运行（会推送飞书）
docker compose exec web trend-sift run
```

需要在启动网页前检查配置，可使用 `docker compose run --rm web trend-sift doctor`。
手动命令与定时任务没有跨进程锁，应避免同时执行。通知沿用现有逻辑，手动重复运行 `digest`
模式仍可能重复推送。

## 数据存储

项目 `./data` 挂载到容器 `/var/lib/trend-sift`，`./logs` 挂载到 `/var/log/trend-sift`：

```text
项目目录/
├── data/
│   ├── trending.db          # Web 与定时抓取使用同一数据库
│   ├── trending.db-wal      # SQLite 运行时文件（可能存在）
│   └── trending.db-shm
└── logs/
    └── trend-sift.log       # 应用日志，自动轮转
```

路径相对于 `compose.yaml` 所在目录。容器重启、删除或重建不会删除宿主文件。
不再使用 `trend-sift-data` 命名卷。Compose 固定容器内的数据库及日志路径，避免原生 `.env` 路径
配置影响 Docker 挂载。

启动入口先为这两个目录及其中的文件设置 UID/GID `10001:10001`，再切换到非 root 用户运行后端，
无需额外初始化容器。Linux 宿主普通用户维护这些文件时可能需要 sudo。SQLite 应使用本机文件系统。

已有原生 `data/trending.db` 会直接使用。若历史数据在旧的 `trend-sift_trend-sift-data` 命名卷内，
需先停止旧写入任务和服务，用 SQLite backup 生成一致性备份，再恢复到项目 `data/trending.db`。
仅修改挂载不会迁移旧卷数据；确认迁移成功前保留旧卷，活跃写入时不要仅复制主数据库文件。

## 网页服务

```bash
docker compose ps
curl -f http://127.0.0.1:8111/api/health
docker compose logs --tail=100 web
```

可通过 `/api/health` 手动检查 Web 数据库可读性；定时任务的下一次执行时间和结果可在日志中查看。
新数据库首次抓取前网页显示空归档。

默认仅绑定 `127.0.0.1:8111`。宿主反向代理可配置：

```caddyfile
trends.example.com {
    reverse_proxy 127.0.0.1:8111
}
```

替换为自己的域名并配置 DNS、HTTPS。代理若在容器内，通过共享 Docker 网络访问 `web:8000`。
临时预览可运行 `ssh -L 8111:127.0.0.1:8111 用户@服务器`，然后打开 http://127.0.0.1:8111。
如需直接公开 HTTP，在 `.env` 中设置 `TREND_SIFT_BIND=0.0.0.0`；`TREND_SIFT_PORT` 默认 8111。
网页和 API 无登录限制，个人专用可在反向代理加访问控制。

## 原生 uv（可选）

```bash
uv sync --locked --no-dev
# 在 .env 中设置 SCHEDULE_ENABLED=true 及每日执行时间
npm --prefix web ci
npm --prefix web run build
uv run --frozen --no-sync uvicorn trend_sift.api.app:app --host 127.0.0.1 --port 8111 --workers 1
```

需要自己的进程管理器保持服务运行。默认数据在项目 `data/trending.db`，日志在 `logs/`。
未配置 `SCHEDULE_ENABLED` 时，原生运行默认关闭调度；Docker Compose 默认开启。
本地开发使用 `--reload` 时建议关闭调度。

原生 cron 仍可使用 `deploy/run.sh` 和 `deploy/crontab.example`，但应关闭内置调度。
代理配置使用标准 `HTTP_PROXY`、`HTTPS_PROXY`。
