# 更新日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/spec/v2.0.0.html)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- 评测脚本：`scripts/eval_resume.py`（`os._exit` 跨进程硬崩 + `resume` 续跑，统计恢复成功率、重复步骤、崩溃点已落库步数）、`scripts/eval_retrieval.py`（自建中文标注语料，统计 Hit@1/@3/@k 与 MRR，`--compare` 对照「纯向量」与「查询改写 + LLM 重排」）。

### 修复

- `extract_json()`：模型在一轮里连吐多个 JSON（例如先 `action` 再 `final_answer`）时，改为取**第一个完整对象**。旧实现把整段拼起来解析，解析失败后原始 JSON 文本被当成最终答案，**工具因此从不被执行**——本机 1.5B 模型实测正是这种输出，修复前 `tools` 完全走不到。

### 文档

- README 路线图补 M6（InferGate 网关协同）与两个评测脚本的入口。

## [0.2.0] - 2026-10-06

把「一堆能跑的脚本」整理成可交付的个人项目：许可证、CI、Docker、类型化配置、锁定依赖。
功能与 0.1.0 一致，本次没有破坏性的接口变更。

### 新增

- `LICENSE`（MIT）。
- GitHub Actions（`.github/workflows/ci.yml`）：ruff 格式与 lint、pytest + 覆盖率（Python 3.11 / 3.14）、`docker build` 验证镜像可构建。
- `Dockerfile` + `.dockerignore`：`docker compose up -d --build` 一把起应用、Prometheus、Grafana。
- `uv.lock`：39 个依赖的完整锁定解析，本地与 CI 装同一批版本。
- `tests/test_config.py`：7 个配置用例（默认值、前缀映射、JSON 解析、类型校验报错、单例）。
- `live` 测试 marker：`pytest -m "not live"` 跳过需要真实网关的回归（未设 `WARDEN_GATEWAY_E2E_URL` 时默认跳过）。
- 覆盖率配置（`[tool.coverage.*]`）与 CI 里的 `--cov=app` 报告，当前基线 78%。

### 变更

- `app/config.py` 重写为 `pydantic-settings`：类型校验、`.env` 内置加载（删掉手写 dotenv 解析）。
  - `llm_input_price_per_mtok` / `llm_output_price_per_mtok` 改名为 `llm_input_price_per_1m` / `llm_output_price_per_1m`。
  - `mcp_servers_json`（字符串 + property）改为 `mcp_servers: list[dict]`。
  - 环境变量名不变（`WARDEN_` 前缀 + 字段名大写），`.env` 留空的项回落默认值。
- 全仓接入 ruff（格式化 + lint），`pyproject.toml` 增加 `[tool.ruff]` 配置。
- `deploy/docker-compose.yml` 增加 `app` 服务与命名卷；Prometheus 抓取目标改为同一 compose 网络的 `app:8000`。
- README 重写：动机、架构图、Docker 用法、开发与测试说明、CI 与许可证徽章。

### 修复

- 停止跟踪 pytest 临时目录（`pytest-of-*/` 下的 48 个 `*.db` / `news_seen.json`），`.gitignore` 补 `.pytest_tmp/`、`pytest-of-*/` 与覆盖率产物（`.coverage*`、`htmlcov/`）。

## [0.1.0] - 2026-10-04

首个版本，M0–M5 的功能全在里面：

- FastAPI 服务 + CLI + 零构建静态控制台。
- Agent 循环：ReAct、PlanAct、原生 Function Calling，以及路由编排、接力、共享黑板。
- 工具注册表与内置工具（计算器、时间、笔记读写、黑板）。
- 记忆（RAG）：embedding、向量存储、索引、检索。
- MCP 客户端（stdio JSON-RPC）与工具适配。
- 定时任务：新闻摘要、仓库报告、周报；通知器（控制台 / 文件）。
- Harness：trace、运行记录持久化与断点续跑、重试与幂等、指标、成本统计。
- Prometheus 指标 + Grafana 看板（`deploy/`）。
