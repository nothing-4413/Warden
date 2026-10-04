# Warden

自托管、长期运行的个人多智能体系统。

- 自研轻量 Harness（ReAct / PlanAct Agent 循环）
- 工具注册表 + 插件化扩展
- 记忆（RAG over 个人笔记）+ MCP + 调度器

当前进度：**M3 已完成** —— 在 M2（完整 Harness：状态持久化/断点续跑/重试幂等/trace_id/Prometheus+Grafana 监控）之上，加入记忆层：RAG over 个人笔记（嵌入 + SQLite 向量库 + 分块索引 + 语义检索工具）。

## 目录结构

```
Warden/
├── pyproject.toml          # 依赖（fastapi / uvicorn / httpx / pydantic / apscheduler / prometheus-client）
├── .env.example            # 环境变量示例（复制为 .env）
├── app/
│   ├── config.py           # 配置：环境变量 + 极简 .env loader
│   ├── llm.py              # OpenAI 兼容 LLM 客户端（httpx 直连）
│   ├── schemas.py          # API 请求/响应模型
│   ├── main.py             # FastAPI 应用 + 路由
│   ├── cli.py              # python -m app.cli 快速调试
│   ├── agent/
│   │   ├── base.py         # BaseAgent 抽象 + AgentStep/AgentRunResult + JSON 解析
│   │   ├── react.py        # 自研 ReAct 循环
│   │   └── planact.py      # 自研 PlanAct 循环（Plan → Act → Summarize）
│   ├── tools/
│   │   ├── base.py         # Tool 定义（pydantic 输入模型）
│   │   ├── registry.py     # 工具注册表
│   │   └── builtin/
│   │       ├── calculator.py      # 安全算术求值（ast 白名单，非 eval）
│   │       ├── datetime_tool.py   # 当前时间
│   │       └── search_notes.py    # 语义检索个人笔记（RAG 记忆工具）
│   ├── memory/                    # M3 记忆：RAG
│   │   ├── embeddings.py  # OpenAI 兼容 /embeddings 客户端
│   │   ├── vector_store.py# SQLite 向量库 + 余弦检索
│   │   ├── indexer.py     # 笔记分块 + 索引
│   │   └── retriever.py   # 查询 → 嵌入 → 检索
│   ├── notify/
│   │   ├── base.py         # Notifier 抽象
│   │   ├── console.py      # 控制台通知
│   │   └── file.py         # 写 reports/*.md
│   ├── harness/
│   │   ├── trace.py        # trace_id（contextvar + logging 注入）
│   │   ├── run_store.py    # RunStore：SQLite 持久化每次运行
│   │   ├── retry.py        # with_retry：指数退避重试
│   │   └── metrics.py      # Prometheus 指标（runs_total + duration）
│   ├── scheduler/
│   │   ├── base.py         # BaseTask 抽象 + TaskResult + Services
│   │   ├── registry.py     # 任务注册表
│   │   └── runner.py       # APScheduler 装配 + run_once + 统一送达
│   └── tasks/
│       ├── news_digest.py  # 资讯/论文简报（RSS/Atom 解析 + 去重 + LLM 摘要）
│       ├── repo_report.py  # 代码库维护（TODO 扫描 + 依赖版本 + 近期提交）
│       └── weekly_review.py# 每周复盘（笔记 + 提交 → 周报）
├── deploy/                 # M2 监控：Prometheus + Grafana（docker compose）
│   ├── docker-compose.yml
│   └── prometheus/
│       ├── prometheus.yml  # 采集 /metrics
│       └── alerts.yml      # 失败率告警规则
└── tests/                  # 单元测试（含假 LLM 冒烟测试）
```

## 快速开始

```bash
# 1. 安装依赖（Python 3.11+）
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

# 2. 配置 LLM（默认指向本地 Ollama）
copy .env.example .env          # 按需改 WARDEN_LLM_*

# 3. 跑 CLI 冒烟
python -m app.cli chat "12 * 7 + 3 等于多少？"
python -m app.cli chat "现在几点了？" --agent planact

# 3b. 定时任务
python -m app.cli tasks              # 列出任务
python -m app.cli run news_digest    # 手动触发一次（需 LLM 在线）

# 3c. 记忆（RAG）：索引个人笔记 + 语义检索
# 先在 .env 设 WARDEN_NOTES_DIR=你的笔记目录，并 `ollama pull nomic-embed-text`
python -m app.cli index-notes        # 建向量索引
python -m app.cli search "上个月学了什么"   # 语义检索

# 4. 跑 API
uvicorn app.main:app --reload
# 健康检查：GET /health
# 对话：POST /api/v1/chat  body 见 app/schemas.py 的 ChatRequest
# 任务列表：GET /api/v1/tasks
# 手动触发：POST /api/v1/tasks/{name}/run
# 监控指标：GET /metrics（Prometheus 文本格式，Grafana 数据源）

# 5. 跑测试
pytest -q
```

### 对话请求示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"agent":"react","messages":[{"role":"user","content":"帮我算 12*7+3"}]}'
```

## 切换 LLM（OpenAI 兼容接口）

改 `.env` 里三个变量即可，代码零改动：

| 后端 | `WARDEN_LLM_BASE_URL` | `WARDEN_LLM_MODEL` |
|---|---|---|
| Ollama（本地） | `http://localhost:11434/v1` | `qwen2.5:7b` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| vLLM / LM Studio | 各自 `/v1` 地址 | 对应模型名 |

## 监控大盘（M2）

```bash
# 1. 启动应用（metrics 默认开启）
uvicorn app.main:app

# 2. 启动 Prometheus + Grafana（需 Docker）
cd deploy
docker compose up -d
# Prometheus: http://localhost:9090（采集 /metrics + 评估失败率告警规则）
# Grafana:    http://localhost:3000（admin/admin，添加 Prometheus 数据源即可建面板）
```

失败率告警规则见 `deploy/prometheus/alerts.yml`：5 分钟窗口内 `error` 占比 > 20% 触发。指标：`warden_runs_total{kind,name,status}` 计数 + `warden_run_duration_seconds` 耗时。

## 核心设计（M0）

- **ReAct 循环**（`app/agent/react.py`）：Thought → Action → Observation 直到 Final Answer。输出契约为**单个 JSON 对象**二选一 —— `{"thought","action","action_input"}` 或 `{"thought","final_answer"}`。选 JSON 而非自由文本 `Action:` 解析，是因为本地小模型对 JSON 遵从度更高、解析更鲁棒（`extract_json` 容忍代码围栏与噪声）。
- **PlanAct 循环**（`app/agent/planact.py`）：三步 —— ① Plan 让模型产出有序步骤列表（每步可选绑定工具+参数）；② Act 按序确定性执行；③ Summarize 汇总结果出最终答案。M0 用"规划期即固定工具调用"，可解释、无额外 LLM 调用。
- **工具注册表**（`app/tools/registry.py`）：新增工具 = 写一个 `Tool` 并 `register`，核心循环零改动。工具输入用 pydantic 模型，同一模型既做运行时校验、又生成 JSON Schema 注入 prompt。
- **安全 calculator**（`app/tools/builtin/calculator.py`）：不用 `eval`，改用 `ast` 解析 + 白名单节点，只允许四则/幂/取模/整除/括号。
- **LLM 客户端**（`app/llm.py`）：不引入 `openai` SDK，`httpx` 直连 `/chat/completions`，`base_url` 指向哪就是哪。

## 核心设计（M1）

- **任务 = 一个类**（`app/scheduler/base.py` 的 `BaseTask`）：`run(ctx) -> TaskResult`，`schedule` 声明触发规则（APScheduler 的 cron/interval）。新增任务不改调度核心，与工具注册表同构。
- **统一送达**（`app/scheduler/runner.py` 的 `_notify`）：任务只产出 `TaskResult`，由 runner 统一决定 ok→送报告 / error→送失败原因 / skipped→静默。任务本身不关心"发给谁"，换 `WARDEN_NOTIFY_KIND` 即切换 console/file。
- **三个内置任务**：
  - `news_digest`：stdlib `xml.etree` 解析 RSS 2.0/Atom（不引 feedparser）→ 链接去重（`data/news_seen.json` 跨次去重，只留最近 500 条）→ LLM 摘要。
  - `repo_report`：`rglob` 扫 TODO/FIXME/HACK → `tomllib` 读依赖 + `importlib.metadata` 查版本 → `git log` 取近期提交 → LLM 报告。
  - `weekly_review`：近 7 天笔记（`*.md` 修改时间）+ 近期提交 → LLM 周报。
- **调度器装配**（`app/scheduler/runner.py`）：`build_scheduler` 按注册表挂后台线程，本地时区（stdlib 取 tzinfo，不引 pytz/tzlocal），`coalesce=True, max_instances=1` 防止任务堆积/重入。

## 核心设计（M2）

- **RunStore（SQLite 持久化）**（`app/harness/run_store.py`）：每次 agent/task 运行一条 `RunRecord`，`id` 即 trace_id。零外部服务即可跑通（`PRAGMA journal_mode=WAL`），接口只暴露 start/update/get/list，换 PostgreSQL 只改这一处。
- **断点续跑**（`app/agent/base.py`）：`BaseAgent.run()` 包 `_begin → _run → _finish_ok`，异常走 `_finish_error`；`resume(run_id)` 从已落库的 `steps`/`meta` 重建上下文，React 从中断步继续、PlanAct 跳过已执行步直接继续 Act/Summarize。
- **重试/幂等**（`app/harness/retry.py` 的 `with_retry`）：指数退避（1s→2s→4s…），`retriable` 过滤不可重试异常；chat 端点的 `request_id` 已成功过则直接回放缓存答案（不重复调 LLM）。
- **trace_id 全链路**（`app/harness/trace.py`）：`contextvars` 传 trace_id，子线程/子协程自动继承；`TraceFilter` 把它注入每条日志的 `[trace_id]` 段。
- **监控指标**（`app/harness/metrics.py`）：`warden_runs_total`（按 kind/name/status）+ `warden_run_duration_seconds`，失败率 = error/(ok+error)。`metrics_enabled=false` 可关。

## 核心设计（M3）

- **嵌入**（`app/memory/embeddings.py`）：OpenAI 兼容 `/embeddings`（`httpx` 直连），对话模型与嵌入模型分离（如对话 `qwen2.5:7b` + 嵌入 `nomic-embed-text`）。
- **向量库**（`app/memory/vector_store.py`）：SQLite 存向量（JSON float 数组）+ 暴力余弦相似度检索。个人笔记量级（数千 chunk）零外部服务即可，接口 `add/search` 留作换 pgvector/Milvus 只改这一处（与 RunStore 同思路）。
- **分块**（`app/memory/indexer.py` 的 `chunk_text`）：先按空行切段落，贪心合并到 `chunk_size`，单段超长硬切带 `overlap`；chunk id 用 `sha1(文件路径:序号)`，重跑索引幂等（INSERT OR REPLACE）。
- **检索工具**（`app/tools/builtin/search_notes.py`）：`make_search_notes_tool(retriever, top_k)` 生成 `search_notes` 工具，Agent 当用户问"我的笔记/过去想法"时自动调用；`build_default_registry(retriever=None)` 传 retriever 才注册（不传保持 M0 两个工具，零破坏）。

## 路线图

- **M0 骨架** ✅ FastAPI + ReAct/PlanAct + 工具注册表 + 2 示例工具
- **M1 动起来** ✅ APScheduler 调度器 + 3 个定时任务 + 通知器
- **M2 Harness** ✅ 状态持久化 + 断点续跑 + 重试/幂等 + trace_id + Prometheus/Grafana 监控
- **M3 记忆** ✅ RAG over 个人笔记（嵌入 + SQLite 向量库 + 语义检索工具）
- **M4 多 Agent + MCP**：检索/摘要/生成多 Agent，MCP 接外部工具
- **M5 打磨**：失败率告警、成本统计、可选前端
