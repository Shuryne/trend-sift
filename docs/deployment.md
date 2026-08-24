# 部署指南

## 原生 uv

```bash
git clone <仓库地址> /opt/trend-sift
cd /opt/trend-sift
cp .env.example .env
uv sync --locked --no-dev
mkdir -p data logs var
uv run --frozen --no-sync trend-sift doctor
uv run --frozen --no-sync trend-sift run --dry-run
```

确认后把 `deploy/crontab.example` 中的原生任务合并进 `crontab -e`。`deploy/run.sh` 会切换到
项目目录并用锁目录防止重入。升级时拉取代码，再执行一次 `uv sync --locked --no-dev`；每日
cron 不同步依赖、不运行测试。

应用日志位于 `logs/`，cron 重定向日志位于 `logs/cron.log`。崩溃可能留下 `var/run.lock`；
确认没有进程运行后可手动删除该空目录。

## Docker Compose

```bash
cp .env.example .env
docker compose build
docker compose run --rm trend-sift doctor
docker compose run --rm trend-sift run --dry-run
```

Compose 不启动常驻服务。宿主 cron 使用 Linux `flock` 后运行：

```cron
5 9 * * * cd /opt/trend-sift && flock -n /tmp/trend-sift.lock docker compose run --rm trend-sift run >> logs/docker-cron.log 2>&1
```

安装 cron 前先在宿主项目目录执行 `mkdir -p logs`，因为 shell 会在启动容器前打开重定向文件。

SQLite 与应用日志位于 Compose 命名卷 `trend-sift-data`。查看卷：

```bash
docker volume inspect trend-sift_trend-sift-data
```

备份时启动只读辅助容器，把卷内容写入当前目录：

```bash
docker run --rm -v trend-sift_trend-sift-data:/data:ro -v "$PWD:/backup" alpine \
  tar czf /backup/trend-sift-data.tar.gz -C /data .
```

删除容器或重建镜像不会删除命名卷。`docker compose down -v` 会永久删除卷及全部历史数据，
执行前必须先备份并明确确认目标项目。

## 时区与代理

cron 示例使用 `CRON_TZ=Asia/Shanghai`；不支持 `CRON_TZ` 的实现需要换算为 UTC。应用不设置
固定代理，如有需要，在宿主环境或 `.env` 中使用标准 `HTTP_PROXY`、`HTTPS_PROXY`。
