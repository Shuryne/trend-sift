# 贡献指南

感谢参与 trend-sift。提交改动前请先搜索已有 Issue；涉及公开接口、数据库结构或新数据源的
较大改动，建议先创建 Issue 说明目标和取舍。

## 本地开发

```bash
make setup
make check
```

测试不得调用真实 GitHub、HN、LLM 或飞书服务，应使用固定 fixture 与模拟传输。

## 代码规范

- Python 3.11+，100 字符行宽，使用 Ruff 格式化与检查。
- 模块、函数和变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`。
- 公共接口和非显然的内部边界必须有类型标注。
- 注释与 docstring 使用英文，解释约束和设计原因，避免逐行复述实现。
- 用户可见的错误、CLI 输出和文档使用中文。
- 不得提交密钥、真实 webhook、数据库、日志、抓取归档或个人绝对路径。

## 提交与 Pull Request

提交信息建议采用 Conventional Commits，例如 `feat: add source`、`fix: handle timeout`。
Pull Request 应说明行为变化、测试方式、配置或数据库兼容影响，并确保 CI 全部通过。
