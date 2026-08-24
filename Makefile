.DEFAULT_GOAL := help
CLI := uv run --frozen trend-sift

.PHONY: help setup doctor run dry-run test lint format typecheck check clean-db \
	docker-build docker-dry-run

help:  ## 显示可用命令
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## 安装锁定依赖并创建本地配置
	uv sync --locked
	@mkdir -p data logs var
	@test -f .env || cp .env.example .env
	@echo "环境已就绪；请检查 .env 后运行 make doctor"

doctor:  ## 检查配置、LLM 与数据库
	@$(CLI) doctor

run:  ## 运行全部数据源并推送
	@$(CLI) run

dry-run:  ## 运行全部数据源但只预览
	@$(CLI) run --dry-run

test:  ## 运行测试与覆盖率检查
	@uv run --frozen pytest

lint:  ## 运行 Ruff 静态检查
	@uv run --frozen ruff check .

format:  ## 格式化 Python 代码
	@uv run --frozen ruff format .

typecheck:  ## 运行 Pyright 类型检查
	@uv run --frozen pyright src tests

check:  ## 运行格式、静态检查、类型检查和测试
	@uv run --frozen ruff format --check .
	@uv run --frozen ruff check .
	@uv run --frozen pyright src tests
	@uv run --frozen pytest

clean-db:  ## 删除本地数据库（交互确认）
	@printf "这会删除 data/trending.db 及全部历史数据。确认? [y/N] " && read answer && [ "$$answer" = "y" ]
	rm -f data/trending.db data/trending.db-wal data/trending.db-shm

docker-build:  ## 构建本地容器镜像
	docker compose build

docker-dry-run:  ## 使用容器运行完整预览
	docker compose run --rm trend-sift run --dry-run
