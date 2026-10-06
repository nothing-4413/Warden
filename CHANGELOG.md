# 更新日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/spec/v2.0.0.html)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- 评测脚本：`scripts/eval_resume.py`（`os._exit` 跨进程硬崩 + `resume` 续跑，统计恢复成功率、重复步骤、崩溃点已落库步数）、`scripts/eval_retrieval.py`（自建中文标注语料，统计 Hit@1/@3/@k 与 MRR，`--compare` 对照「纯向量」与「查询改写 + LLM 重排」）。
- `eval_retrieval.py --sweep 1,3,4,8`：检索一次、从同一份排序里数出 Hit@K 曲线（各点之间可比，也省掉 K 倍嵌入开销）。本机实测 Hit@1 42.5% / Hit@3 55.0% / Hit@4 65.0% / Hit@8 85.0%，MRR 0.538（k=8）。
- `eval_resume.py` 增加第三种任务形态（时间工具 + 计算器两步链条），崩溃点从「仅第一步之后」扩到「链条中间」，用来覆盖更深一层的半成品状态。本机实测 30/30 = 100%（0 次剔除），`answer` 全部正确、无重复步骤。
- `scripts/eval_router.py`：把 chat / embeddings 两个本地实例拼成一个 OpenAI 兼容 base_url（Warden 的 `LLMClient` 与 `EmbeddingClient` 共用 `llm_base_url`）。纯标准库，用 Ollama 时不需要。

### 修复

- `extract_json()`：模型在一轮里连吐多个 JSON（例如先 `action` 再 `final_answer`）时，改为取**第一个完整对象**。旧实现把整段拼起来解析，解析失败后原始 JSON 文本被当成最终答案，**工具因此从不被执行**——本机 1.5B 模型实测正是这种输出，修复前 `tools` 完全走不到。
- 本机目标不再被系统代理劫持：新增 `app/net.py`，LLM / 嵌入 / 能力探测三条出站请求在目标是 `localhost`/回环 IP 时关掉环境代理（`trust_env=False`），指外网时行为不变。修复前 Windows 上开着系统代理（实测注册表 `127.0.0.1:7897`）连本机 Ollama 都会返回 502 空 body，且 Ollama 侧看不到请求。
- `git log` 输出在 Windows 上被按本地代码页（GBK）解码：含中文的提交信息会在读取线程里抛 `UnicodeDecodeError`，`repo_report` / `weekly_review` 的「近期提交」段落被静默丢掉。两处 `subprocess.run` 显式 `encoding="utf-8", errors="replace"`。
- 未关闭的 sqlite 句柄：CLI 的「未知任务」早退路径与 `chat` / `index-notes` / `search` 的异常路径漏掉 `store.close()`（改用 `finally`）；`MCPClient.close()` 只关 stdin，stdout 的 `TextIOWrapper` 留到解释器退出；`scripts/eval_router.py` 透传上游 4xx/5xx 时没有关闭 `HTTPError` 自带的 body 流。三处都不影响功能，但会让长驻进程/测试会话出现 `ResourceWarning`。

### 测试

- `tests/test_net.py`（17 个用例）：回环判定真值表、三条出站请求的代理开关、嵌入按 `index` 纠正乱序、网络/形状错误包装。
- `tests/test_cli.py`（18 个）：11 个 CLI 子命令的接线、退出码、打印内容（`app/cli.py` 覆盖率 0% → 95%）。
- `tests/test_scheduler.py`、`tests/test_notify.py`、`tests/test_mcp_registry.py`、`tests/test_weekly_review.py`：触发器构造、重试与落库、通知器选择与文件名、MCP 工具前缀注册、近 7 天笔记/提交收集与提示词组装。
- `tests/test_llm_client.py`：`chat_with_tools` 解析（含缺 id、参数不是 JSON 的容错）与坏形状/网络错误包装。
- `tests/test_api.py`：健康检查、`/metrics`（含禁用时 404）、控制台运行记录、`/api/v1/chat`（步骤映射 + 幂等回放不打模型）、任务列表/触发/404、lifespan 启停调度器。
- `tests/test_resume.py` 改用 fixture 持有 `RunStore`，跑完关闭连接。
- `tests/test_eval_retrieval.py`：Hit@K 曲线的口径（同一份排序、未召回不计入任何截断点、截断点越大命中率单调不降）。
- 全量：**209 passed / 3 skipped（live）**，覆盖率基线 79% → **95%**。

### 文档

- README 新增「评测」小节：前置条件（Ollama 或 llama.cpp + 路由器）、三个脚本的运行命令、本机实测结果表；目录结构补 `scripts/`。
- README 新增「网关实网回归」小节：在本机 InferGate 最小栈（`agent-local.yaml`）上跑通 3 个 `live` 用例的步骤与线路事实（同一会话、重放不打上游、capabilities 带回上下文窗口）。
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
