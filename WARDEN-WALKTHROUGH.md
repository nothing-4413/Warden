
# Warden 全项目逐行走读讲义

> 覆盖范围：`app/**/*.py` 共 51 个文件（3455 行）、`scripts/*.py` 5 个脚本（913 行）、`tests/*.py` 34 个文件（2746 行），
> 外加 `app/static/index.html`、`Dockerfile`、`deploy/**`、`.github/workflows/ci.yml`、`pyproject.toml`、`README.md`、`CHANGELOG.md`。
> 全仓合计 7114 行源码。
>
> **行号约定**：本讲义所有行号均以 `read` 工具报告的行号为准（即文件真实总行数）。
> 注意 `Get-Content | Measure-Object -Line` 会跳过空行而系统性少算（例如 `app/agent/base.py` 会被算成 276 行，真实为 300 行），
> 两者不可混用。下文凡标 `(验证)` 的行号是本会话逐字读过的原文；标 `(会话记录)` 的是本会话内已归档的读取结果。

---

## 目录

| 章 | 主题 | 覆盖文件 |
|---|---|---|
| 0 | 项目定位与八条设计主线 | README、CHANGELOG |
| 1 | 数据流转：一次请求的完整生命周期 | 跨模块 |
| 2 | 配置与出站网络 | `config.py` `net.py` |
| 3 | API 契约 | `schemas.py` |
| 4 | 应用装配与 HTTP 入口 | `main.py` |
| 5 | LLM 客户端：`_post` 重试状态机 | `llm.py` |
| 6 | Agent 骨架：模板方法 | `agent/base.py` |
| 7 | 三条循环 | `react.py` `planact.py` `function_call.py` |
| 8 | 三种多 Agent 协作 | `orchestrator.py` `pipeline.py` `blackboard.py` |
| 9 | Harness：状态、重试、指标、trace、成本 | `harness/*` |
| 10 | 工具系统与记忆 RAG | `tools/*` `memory/*` |
| 11 | 网关协同：会话身份、幂等、能力发现 | `gateway/*` |
| 12 | MCP 客户端 | `mcp/*` |
| 13 | 定时任务与通知 | `scheduler/*` `notify/*` `tasks/*` |
| 14 | 控制台与 CLI | `static/index.html` `cli.py` |
| 15 | 脚本：冒烟与三个评测 | `scripts/*` |
| 16 | 测试套件：34 文件逐项 | `tests/*` |
| 17 | 已知缺口汇总 | 全仓 |
| 18 | 架构评估：在什么规模下失效 | 含实测数据 |
| 附录 A | 本会话实测结果原始数据 | 实验 |
| 附录 B | 全仓文件清单与行数 | 全仓 |

---

# 第 0 章 项目定位与八条设计主线

Warden 是**自托管、长期运行的个人多智能体系统**。README 把它和"玩具 demo"对立起来，三条动机（README L8–L14）：

1. **数据不出网** —— 默认指向本机 Ollama；除自配的 LLM/嵌入地址外不主动访问外部。
2. **要"长期运行"** —— 每次运行落 SQLite `RunRecord`、可 resume 续跑、重试幂等、指标可查。
3. **个人项目的取舍** —— 够用优先。控制台是零构建静态 HTML，向量检索是 SQLite 暴力余弦，不引入 LangChain / 向量数据库 / 消息队列。

这三条动机解释了后面几乎所有看起来"过于朴素"的技术选择。把它们展开成八条贯穿全仓的设计主线：

**主线 1：零外部服务依赖。** 状态用 stdlib `sqlite3` + WAL（`app/harness/run_store.py:5–6`），向量库用同一套 SQLite（`app/memory/vector_store.py`），MCP 用 `subprocess` + JSON-RPC 手写（`app/mcp/client.py`），RSS 解析用 stdlib `xml.etree`（`app/tasks/news_digest.py`），`pyproject.toml` 读依赖用 stdlib `tomllib`（`app/tasks/repo_report.py`），时间触发器用 stdlib `tzinfo`（`app/scheduler/runner.py:1–4` 明说"不引入 tzlocal/pytz"），评测路由器用 stdlib `http.server`（`scripts/eval_router.py`）。**依赖清单只有 7 个运行时包**（apscheduler、fastapi、uvicorn、httpx、pydantic、pydantic-settings、prometheus-client）。

**主线 2：接口留换点。** 每处选型都写明"换实现只改这一处"：`run_store.py:4` 说换 PostgreSQL 只改这个文件；`vector_store.py:1–5` 说换 Milvus/pgvector 只改 `add`/`search`；`app/gateway/__init__.py:1–8` 说"不新增第二条 HTTP 通路"。

**主线 3：错误一律作为数据回传给模型，不中断循环。** `app/agent/base.py` 的 `_execute` 把工具异常转成 `"error: ..."` 字符串；`app/tools/base.py:75–76` 把以 `"error: "` 开头的串视为工具已上报错误；PlanAct 据此触发重规划（`planact.py:133–137`）。**循环的健壮性优先于错误的传播。**

**主线 4：一切可降级。** 能力探测失败返回 `None`（`gateway/capabilities.py`）、摘要失败退化为纯窗口（`agent/base.py`）、反思解析失败默认放行（`react.py:122–123`）、自评任何异常返回 `None`（`agent/base.py`）、单条 RSS 源失败不拖垮整体（`news_digest.py`）、`git log` 失败静默返回 `[]`、MCP 启动失败只 warn（`cli.py`）。**没有任何一条可选路径能让主流程挂掉。**

**主线 5：确定性派生代替随机与存储。** `gateway/identity.py` 的会话 id 与幂等键全部是内容哈希，不看时钟、不用随机数、不落库。

**主线 6：所有 ID 都是 trace_id。** `RunRecord.id` 即 `trace_id`（`run_store.py:1`），HTTP 请求的 `request_id` 也即 `trace_id`（`main.py`），`trace_span` 用 `ContextVar` 自动向子线程/子协程传播。

**主线 7：配置集中、启动即校验。** `config.py:3–4` 明说"类型写错时在启动那一刻就按字段名报错"，`tests/test_config.py:78` 正是断言 `"agent_max_steps" in str(exc)`。

**主线 8：插件化靠注册表。** `tools/registry.py:1–4` 写明"新增工具 = 写一个 Tool 并 register，核心循环零改动"；`scheduler/registry.py` 与工具注册表同构；`tasks/__init__.py` 用工厂函数装配默认任务。

---

# 第 1 章 数据流转：一次请求的完整生命周期

以 `POST /api/v1/chat` 走 `react` agent 为例，把数据在模块之间的流转完整走一遍。这是理解全项目的骨架。

```
HTTP POST /api/v1/chat
  │  {"messages":[{"role":"user","content":"用计算器算 3*4"}], "agent":"react", "request_id":null}
  ▼
main.py  POST /api/v1/chat
  │  ① with trace_span(req.request_id) as trace_id:   ← trace_id = run id
  │  ② 幂等回放：store.get(request_id) 命中 ok+output → 直接返回，不调 LLM
  │  ③ history = [m.model_dump() for m in req.messages]
  ▼
_agents["react"].run(history, trace_id)
  │  ④ with gateway_session_scope(derive_session_id(history)):   ← 会话 id 由裁剪前历史派生
  ▼
BaseAgent._turn(history, trace_id)
  │  ⑤ started = time.time()
  │  ⑥ history = self._history_with_summary(history)   ← 可能多花一次 LLM 调用做摘要
  │  ⑦ record = self._begin("chat", history, trace_id) → RunStore.start → INSERT runs
  ▼
ReactAgent._run(history, record) → _loop(messages, [], record, 0)
  │  ⑧ messages = [system_prompt, *history]
  │  ⑨ with_retry(lambda: llm.chat(messages))
  ▼
LLMClient.chat(messages)
  │  ⑩ payload = {model, messages, temperature}
  ▼
LLMClient._post(payload)
  │  ⑪ headers = {"Authorization": "Bearer ..."} + gateway_headers(payload)
  │     gateway_headers → resolve_session_id → derive_idempotency_key
  │  ⑫ max_attempts = gateway_retry_attempts if 有幂等键 else 1
  │  ⑬ httpx.post(url, json=payload, headers=..., **httpx_env_kwargs(url))
  │  ⑭ 409 in_flight → sleep(Retry-After) → 同键重发（上限）
  │  ⑮ 409 conflict  → logger.error + 立刻 LLMError（不重试）
  │  ⑯ Replay: true  → 算成功，记 record_idempotent_replay + last_gateway
  │  ⑰ _record_usage → record_llm_usage(prompt, completion, cost)
  ▼
返回 content 文本
  │  ⑱ extract_json(raw)  ← 去围栏 → json.loads → 退化为"取第一个完整对象"
  │  ⑲ 若拿到 {"action":"calculator","action_input":{...}}
  ▼
BaseAgent._execute("calculator", {"expression":"3 * 4"})
  │  ⑳ tools.get("calculator") → ToolNotFoundError → "error: unknown tool ..."
  │     tool.run(raw) → CalculatorInput.model_validate → _invoke → _coerce_output
  ▼
observation = 12 (int)
  │  ○21 AgentStep(index=0, thought=..., action="calculator", observation=12)
  │  ○22 _persist_step(record, s, raw) → record.steps.append → RunStore.update → UPDATE runs
  │  ○23 messages += [assistant raw, user "Observation: 12"]
  ▼
下一轮 → 模型回 {"thought":..., "final_answer":"12"}
  │  ○24 _persist_step(record, s, raw)   ← 传了未 strip 的 raw
  │  ○25 _maybe_self_eval(result)        ← 可选，多一次 LLM 调用
  ▼
BaseAgent._finish_ok(record, result)
  │  ○26 record.status = "ok"; record.output = "12"; record.finished_at = time.time()
  │     RunStore.update → UPDATE runs
  │  ○27 _record_metrics("chat", "ok", 耗时) → warden_runs_total / warden_run_duration_seconds
  ▼
main.py 组装 ChatResponse(answer, agent, model, steps=[StepView...], self_eval)
  ▼
HTTP 200
```

**关键观察**：

- **状态只在三处落库**：`RunStore.start`（一次 INSERT）、每次 `_persist_step`（UPDATE）、`_finish_ok`/`_finish_error`（UPDATE）。中间崩溃时库里留的是 `status="running"` 且 `steps` 是当时已完成的步骤 —— 这正是 `resume` 能工作的全部依据。
- **一次用户请求可能产生 1～4 次 LLM 调用**：主循环每次迭代一次，加上可能的摘要（`_history_with_summary`）、JSON 自纠（`_chat_json`）、反思（`_reflect`）、自评（`_self_eval`）。这些"隐形调用"都归属同一网关会话（因为 `gateway_session_scope` 包住了整个 `_turn`）。
- **`trace_id` 与 `session_id` 是两个正交的身份**：前者标识"这一次 run"（随机 uuid4），后者标识"这一场对话"（内容派生，跨轮稳定）。这是 M6 的核心区分。

---

# 第 2 章 配置与出站网络

## 2.1 `app/config.py`（112 行）(验证)

模块 docstring（L1–5）三句话定了三条性质：

```python
"""Warden 配置：pydantic-settings 从环境变量 / .env 读取，字段带类型与默认值。

设计取舍：所有开关集中在此文件，新增配置只改这一处；类型写错时在启动那一刻就按字段名报错，
不必等到第一次用到才抛 ValueError。真实环境变量优先于 .env（.env 不覆盖已存在的变量）。
"""
```

### `SettingsConfigDict` 六个参数（L15–24）

```python
model_config = SettingsConfigDict(
    env_prefix="WARDEN_",
    env_file=".env",
    env_file_encoding="utf-8",
    env_ignore_empty=True,   # .env 里 `KEY=`（留空）按未设置处理，回落默认值
    extra="ignore",          # 无关的 WARDEN_* 变量不影响启动
    frozen=True,
)
```

四个参数各有明确的失败模式要防：

| 参数 | 不设时的后果 |
|---|---|
| `env_prefix="WARDEN_"` | 与系统的 `LLM_MODEL` 之类通用变量撞名 |
| `env_ignore_empty=True` | `.env` 里写 `WARDEN_NOTES_DIR=`（清空配置的标准写法）会被解析成空串而非回落默认值。**这条对 `notes_dir`/`gateway_session` 尤其重要**，`tests/test_config.py:66` 专门测它 |
| `extra="ignore"` | 历史遗留或别家工具的 `WARDEN_*` 变量会让启动直接报错 |
| `frozen=True` | 配置可变 → 多线程下被误改。冻结后 `Settings()` 实例是只读的，`tests/test_config.py:85` 断言赋值抛 `ValidationError` |

`env_file_encoding="utf-8"` 是 Windows 上的必修项（默认编码是本地代码页，中文注释/值会解码失败）。

### 字段与默认值（L27–97）

**LLM（L27–31）**：`llm_base_url="http://localhost:11434/v1"`、`llm_api_key="ollama"`、`llm_model="qwen2.5:7b"`、`llm_temperature=0.0`、`llm_timeout_s=120.0`。
`temperature=0.0` 是"可复现优先"的信号：Agent 循环要的是稳定解析，不是多样性。

**Agent 循环（L34–44）**：

```python
agent_max_steps: int = 10
max_observation_chars: int = 2000   # 0 = 不截断
context_max_messages: int = 30      # 0 = 不压缩，保留全部历史
reflect_enabled: bool = False
max_replans: int = 2                # 0 = 关闭重规划
self_eval_enabled: bool = False
```

**两个 `0` 是哨兵而非"零值"**：`max_observation_chars=0` 意思是"别截断"，`context_max_messages=0` 意思是"别压缩"。`agent/base.py` 的 `_truncate_observation` 用 `limit <= 0` 判断、`_history_with_summary` 用 `limit <= 0` 判断，两处都把 `0` 当"关闭"。这是紧凑但容易误读的设计 —— 注释（L35、L37）就是为它准备的。

`reflect_enabled` / `self_eval_enabled` 默认 `False` 的理由是成本：各多一次 LLM 调用。

**API（L47–48）**：`api_host="127.0.0.1"`、`api_port=8000`。默认只听本机。

**调度与任务（L51–56）**：`notify_kind="console"`（注释 `# console | file`）、`report_dir="reports"`、`data_dir="data"`、`rss_sources` 默认两条（Hacker News + arXiv cs.AI）、`notes_dir=""`、`repo_path="."`。

**Harness（L59–62）**：`db_path="data/warden.db"`、`retry_attempts=3`、`retry_backoff_s=1.0`、`metrics_enabled=True`。

**记忆 RAG（L65–74）**：`embedding_model="nomic-embed-text"`、`memory_db_path="data/memory.db"`、`chunk_size=600`、`chunk_overlap=100`、`retrieval_top_k=4`、`retrieval_min_score=0.0`、`rag_rewrite_enabled=False`、`rag_rerank_enabled=False`。

**后两项默认关闭有实测依据**（见附录 A）：本机 qwen2.5-1.5b + nomic-embed-text-v1.5 下，改写+重排让 Hit@1 从 42.5% 掉到 10.0%、延迟从 88ms 涨到 4383ms。**默认值是评测结论的产物，不是保守。**

**MCP（L77）**：`mcp_servers: list[dict] = []`，环境变量里写 JSON 数组。从字符串+property 改成原生 list 是 0.2.0 的变更（CHANGELOG）。

**成本（L80–81）**：`llm_input_price_per_1m=0.0`、`llm_output_price_per_1m=0.0`，注释"默认 0 = 本地模型免费"。

**网关 M6（L86–97）**：

```python
gateway_enabled: bool = True      # 未知请求头会被普通 OpenAI 端点忽略，所以默认开启是安全的
gateway_tenant: str = "warden"
gateway_session: str = ""         # 留空 = 由完整历史首条用户消息确定性派生
gateway_retry_attempts: int = 3
gateway_retry_after_s: float = 1.0
gateway_capabilities_enabled: bool = True
gateway_capabilities_ttl_s: float = 60.0
```

L84–85 那段注释是整个 M6"渐进增强"策略的声明：**默认开启之所以安全，是因为未知请求头在标准 OpenAI 端点上被静默忽略。** 这让 M6 不需要开关就能对老部署生效。

### `rss_source_list` 与单例（L99–112）

```python
@property
def rss_source_list(self) -> list[str]:
    return [s.strip() for s in self.rss_sources.split(",") if s.strip()]
```

用一个逗号分隔的字符串存列表 + property 解析，是为了绕过"环境变量只能是字符串"。`if s.strip()` 顺手吃掉尾随逗号产生的空项。

```python
_settings: Settings | None = None

def get_settings() -> Settings:
    """进程内单例（配置只读，读一次即可）。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

**模块级单例是双刃剑**：生产上省了重复解析，测试上必须显式打破它 —— `tests/test_cli.py:82` 用 `monkeypatch.setattr("app.config._settings", None)` 就是这个用途。这是第 18 章"模块级单例对测试的影响"的第一个证据点。

## 2.2 `app/net.py`（35 行）(验证)

这 35 行解决一个**真实踩过的坑**，docstring（L1–9）把现象记得非常具体：

```
Windows 上 `httpx` 默认 `trust_env=True`，会去读注册表 Internet Settings 里的代理；
实测本机 Clash 类代理（127.0.0.1:7897）对 **127.0.0.1 目标也返回 502 且 body 为空**，
被代理的 llama-server / Ollama 日志里根本看不到这条请求，排查方向极易跑偏。

所以：目标是本机 → 显式 `trust_env=False`；目标是外网 → 保持 httpx 默认行为，
企业代理、`SSL_CERT_FILE` 这类环境配置照常生效。
```

**为什么这个 bug 特别贵**：被代理的 llama-server 日志里看不到请求，所以第一反应是"服务没起来"，会在错误的方向上排查很久。`body 为空` + `502` 的组合是代理返回的，不是模型服务返回的。

```python
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})   # L17

def is_loopback_url(url: str) -> bool:                                                  # L20
    """URL 的主机是不是本机（localhost 或回环 IP）；解析不出主机则按非本机处理。"""
    host = urlparse(url).hostname                                                       # L22
    if not host:
        return False
    if host.lower() in _LOOPBACK_NAMES:                                                 # L25
        return True
    try:
        return ipaddress.ip_address(host).is_loopback                                   # L28
    except ValueError:
        return False                                                                    # L30

def httpx_env_kwargs(url: str) -> dict[str, bool]:                                      # L33
    """展开到 httpx 调用里的关键字：本机目标不读环境代理。"""
    return {"trust_env": False} if is_loopback_url(url) else {}
```

三个细节：

1. **`host.lower()`** —— `LOCALHOST` 大小写不敏感。`tests/test_net.py` 明确用 `LOCALHOST` 做参数化用例。
2. **`ipaddress.ip_address(host).is_loopback`** 覆盖了 `127.0.0.0/8` 整段（不只是 `127.0.0.1`）和 `::1`。`tests/test_net.py` 参数化了 `127.0.0.2` 与 `[::1]`。
3. **`except ValueError: return False`** —— `urlparse("not a url").hostname` 可能给出一个非 IP 非主机名的串，`ip_address` 会抛；此时按"非本机"处理（保守：保持 httpx 默认行为），而不是崩。

**返回 `{}` 而不是 `{"trust_env": True}`** 是刻意的：不传这个 kwarg 才能让环境配置（企业代理、证书）照常生效。`tests/test_net.py:100` 断言远程目标时 `"trust_env" not in rec.calls[0]` —— 断言的是"这个键不存在"，而不是"值为 True"。

`httpx_env_kwargs` 被三条出站路径共用：`llm.py` 的 `_post`（L256）、`llm.py` 的能力探测经由 `capabilities.py`、`memory/embeddings.py` 的 `embed`。`tests/test_net.py:139` 专门验证能力探测对本机网关也关代理。

---

# 第 3 章 API 契约：`app/schemas.py`（48 行）(验证)

```python
class ChatMessage(BaseModel):                                    # L10
    role: Literal["system", "user", "assistant"]                 # L11
    content: str                                                 # L12

class ChatRequest(BaseModel):                                    # L15
    messages: list[ChatMessage] = Field(min_length=1)            # L16
    agent: Literal["react", "planact", "function_call"] = "react" # L17
    request_id: str | None = None  # 幂等键：同 id 重复请求直接回放已存结果  # L18

class StepView(BaseModel):                                       # L21
    index: int
    thought: str | None = None
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: Any = None                                      # L26  ← Any，不是 str

class ChatResponse(BaseModel):                                   # L29
    answer: str
    agent: str
    model: str
    steps: list[StepView]
    self_eval: dict[str, Any] | None = None                      # L34

class TaskView(BaseModel):                                       # L37
    name: str
    description: str
    schedule: dict[str, Any]

class TaskRunResponse(BaseModel):                                # L43
    task: str
    status: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
```

四个设计点：

1. **`StepView.observation: Any`（L26）** —— 这是整个契约里最关键的一处类型选择。工具可以返回 int（计算器）、dict（结构化输出）、str。如果这里写 `str`，`tests/test_react.py:31` 断言的 `steps[0].observation == 4`（int 而非 `"4"`）就会在 HTTP 边界上被 pydantic 强制转成字符串，**破坏"观测保持原类型"这条契约**。`base.py` 的 `_truncate_observation` 只截字符串、不动数字，与这里是同一条思路的两端。

2. **`ChatRequest.messages` 用 `Field(min_length=1)`（L16）** —— pydantic v2 的 `min_length` 对 list 生效，空列表在请求解析阶段就被拒（422），不必进 Agent 才发现。

3. **`agent` 是 `Literal` 三选一（L17）** —— 默认 `"react"`。非法 agent 名在 pydantic 层被拒，而不是在 `_agents[name]` 上抛 `KeyError`。**注意**：这意味着新增一种 Agent 循环必须同时改这里 —— 是插件化的一个未打通处（对比 `ToolRegistry` 的"零改动"）。

4. **`request_id` 可空（L18）** —— 幂等是**可选**能力。不传就是普通请求。传了则同 id 且已成功时直接回放（`main.py`）。

`TaskView.schedule` 是 `dict` 而非强类型，因为 trigger 形状由 APScheduler 决定（`{"trigger":"cron","hour":8}` 或 `{"trigger":"interval","hours":24}`），强类型收益低。

---

# 第 4 章 应用装配与 HTTP 入口：`app/main.py`（166 行）(会话记录)

## 4.1 模块级单例装配（L27–52）—— 本文件最重要的结构

```python
configure_logging()
settings = get_settings()
llm = LLMClient(settings)
embedder = EmbeddingClient(settings)
memory_store = VectorStore(settings.memory_db_path)
retriever = Retriever(embedder, memory_store, llm=llm)
indexer = NotesIndexer(settings, embedder, memory_store)
registry = build_default_registry(
    retriever=retriever, top_k=settings.retrieval_top_k,
    min_score=settings.retrieval_min_score, indexer=indexer,
    rewrite=settings.rag_rewrite_enabled, rerank=settings.rag_rerank_enabled,
)
store = RunStore(settings.db_path)
_agents = {
    "react": ReactAgent(settings, llm, registry, store=store),
    "planact": PlanActAgent(settings, llm, registry, store=store),
    "function_call": FunctionCallAgent(settings, llm, registry, store=store),
}
notifier = get_notifier(settings)
_task_registry = build_default_task_registry(settings)
_task_ctx = Services(settings, llm, notifier, store=store)
```

**这是 import 时执行的代码**。含义有三层：

- **好**：任何 `import app.main` 的路径（uvicorn、TestClient、`tests/test_api.py`）都拿到同一套对象，不需要 DI 容器或 `app.state`。
- **代价 1**：三个 Agent **共享同一个 `registry`、同一个 `store`、同一个 `llm`**。共享 `store` 是有意的（同一个 SQLite 连接，`check_same_thread=False` + WAL 让它能跨线程用）；共享 `registry` 意味着三个循环看到完全相同的工具集 —— 这正是 `team`/`collab` 命令要**另建 registry**（给不同 Agent 不同工具）的原因。
- **代价 2**：**import 就做 I/O**。`VectorStore(...)` 会建目录并连库，`RunStore(...)` 会建表。所以 `tests/` 里凡涉及 `app.main` 的测试都必须 monkeypatch 到 `tmp_path`，或者接受在仓库根建库。这是第 18 章讨论的第二个证据点。

L44–48 的注释明确写了共享连接的理由：应用与调度共用同一个 sqlite 连接，靠 `check_same_thread=False` + WAL。

## 4.2 lifespan（L55–60）

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = build_scheduler(_task_registry, _task_ctx)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
```

`shutdown(wait=False)` 是"别让退出卡住"的选择：正在跑的任务不会被等待完成。对个人项目这是对的取舍（否则一个 5 分钟的 RSS 抓取会让 Ctrl-C 无效）。`tests/test_api.py:149` 用 `with TestClient(main.app)` 真跑了一遍 lifespan，断言事件序列 `["start", "shutdown"]`。

## 4.3 FastAPI 实例与版本（L63）

```python
app = FastAPI(title="Warden", version="0.5.0", lifespan=lifespan)
```

**这里有一个真实的不一致**：`version="0.5.0"` 对应 CHANGELOG 的 M0–M6 里程碑编号，但 `app/__init__.py` 写的是 `__version__ = "0.1.0"`，而 `pyproject.toml` 写的是 `version = "0.2.0"`。**同一个项目三个版本号**。第 17 章会把它列入缺口清单。

`STATIC_DIR = Path(__file__).parent / "static"`（L65）。

## 4.4 路由逐条

| 路由 | 行 | 要点 |
|---|---|---|
| `GET /health` | L68–75 | `{"status":"ok","model":llm.model,"tools":registry.names(),"tasks":_task_registry.names()}` —— 一次调用即可看到装配结果的快照 |
| `GET /metrics` | L78–83 | `metrics_enabled` 为假 → `HTTPException(404, "metrics disabled")`；否则 `Response(metrics_text(), media_type="text/plain; version=0.0.4; charset=utf-8")` |
| `GET /` | L86–89 | `FileResponse(STATIC_DIR / "index.html")`，`include_in_schema=False` |
| `GET /api/v1/runs?limit=20` | L92–111 | 内嵌 `def iso(ts)`；`"error": (r.error or "")[:200]` 截断 |
| `POST /api/v1/chat` | L114–143 | 幂等回放 + trace_span |
| `GET /api/v1/tasks` | L146–151 | `TaskView` 列表 |
| `POST /api/v1/tasks/{name}/run` | L154–166 | `TaskNotFoundError` → 404 |

**`/metrics` 的 media_type 是一串完整字符串**（含 `version=0.0.4`）而不是 `"text/plain"`。这是 Prometheus 文本格式的约定，`tests/test_api.py:56` 只断言 `"text/plain" in content-type`，所以两种写法都能过 —— 但显式带版本是更正确的。

**`/api/v1/runs` 的 `iso()` 处理 `None`**：`finished_at` 在运行中为 `None`，不能直接 `fromtimestamp(None)`。`tests/test_api.py:68` 正是断言 `finished_at is None` 时响应里也是 `None`。

**`error` 截断 200 字符**：错误信息可能很长（含 traceback 摘要），列表接口不该把它全量吐出去。

## 4.5 `POST /api/v1/chat` 的幂等回放（L114–143）

```python
@app.post("/api/v1/chat")
def chat(req: ChatRequest) -> ChatResponse:
    with trace_span(req.request_id) as trace_id:
        if req.request_id:
            existing = store.get(req.request_id)
            if existing and existing.status == STATUS_OK and existing.output:
                return ChatResponse(answer=existing.output, agent=req.agent,
                                    model=llm.model, steps=[])
        history = [m.model_dump() for m in req.messages]
        result = agent.run(history, trace_id=trace_id)
        ...
```

**三个判断合起来定义了"可以回放"**：`existing` 非空（跑过）、`status == STATUS_OK`（成功结束）、`existing.output` 真值（有答案）。**注意 `steps=[]`** —— 回放路径不重建步骤，因为步骤是过程信息，回放的价值是答案与不重复计费。

**为什么不用 `status != STATUS_ERROR`**：`running` 状态的记录必须**不能**回放 —— 那是一次还在进行（或崩溃中途）的 run，回放它会返回 `None` 答案。用 `== STATUS_OK` 白名单而不是黑名单，是这里正确的方向。

`trace_span(req.request_id)` 把 HTTP 请求的 request_id 直接变成 trace_id/run id：

```python
@contextlib.contextmanager
def trace_span(trace_id=None):
    token = _trace_var.set(trace_id or new_trace_id())
    try:
        yield ...
    finally:
        _trace_var.reset(token)
```

传入 `None` 时自动生成 uuid4。**这条链路让日志、RunRecord、HTTP 请求三者共用同一个 id** —— 排查一次请求时，从响应里的 `request_id` 能一路追到 DB 行和日志行。

---

# 第 5 章 LLM 客户端：`_post` 重试状态机（`app/llm.py` 368 行）(验证)

这是全项目最复杂的一个函数。docstring（L1–9）先声明了不引入 openai SDK 的理由：

```
不引入 openai SDK：base_url 指向哪就是哪（OpenAI / Ollama / vLLM / LM Studio），
每行逻辑透明，便于切本地模型。
```

## 5.1 常量与错误类型（L46–51）

```python
TYPE_IDEMPOTENCY_CONFLICT = "infergate_idempotency_conflict"
TYPE_IDEMPOTENCY_IN_FLIGHT = "infergate_idempotency_in_flight"
_CAPABILITY_TIMEOUT_S = 10.0   # 能力探测限时：上下文窗口是"锦上添花"，绝不能让一次慢探测拖住对话
```

`_CAPABILITY_TIMEOUT_S` 单独存在（而不是直接用 `llm_timeout_s=120`）是刻意的：能力探测是一个"可选优化"，用 120 秒等它毫无道理。`LLMClient.capability` 里用 `timeout=min(float(self._timeout), _CAPABILITY_TIMEOUT_S)`（L334）—— 取两者的较小值，所以配置把 `llm_timeout_s` 调小到 5 秒时能力探测也会跟着变 5 秒。

## 5.2 数据类（L58–90）

```python
@dataclass
class Usage:                                    # L58
    prompt_tokens: int = 0
    completion_tokens: int = 0

@dataclass
class ToolCall:                                 # L66
    id: str
    name: str
    arguments: dict

@dataclass
class GatewayOutcome:                           # L75
    key: str = ""
    session_id: str = ""
    replayed: bool = False
    replay_origin: str = ""
    replay_age_ms: int = 0
    replay_upstream: str = ""
    upstream_name: str = ""
    attempts: int = 1
```

`GatewayOutcome` 的 docstring（L79–80）写明它的用途：**"不是 InferGate 时全是默认值；replayed=True 就是'这次没有真的打到上游、Warden 没有被重复计费'的证据。"** 它把"网络层的事实"变成"可读的对象状态"，供测试和运维断言 —— `tests/test_gateway.py:244` 就是断言 `gw.replayed is True` 与 `replay_age_ms == 1234`。

## 5.3 三个小工具函数（L93–130）

```python
def _gateway_error_type(resp) -> str:           # L93
    """读出 InferGate 错误体里的错误类型（非 409 / 不是 JSON / 别家网关 → 空串）。"""
    if getattr(resp, "status_code", None) != 409:     # L100
        return ""
    try:
        data = resp.json()
    except Exception:
        return ""                                     # L105
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict) and isinstance(err.get("type"), str):
        return err["type"]                            # L110  OpenAI 形状
    if isinstance(data.get("type"), str):
        return data["type"]                           # L112  顶层形状
    return ""
```

docstring L96–98 说明了为什么同时容忍两种形状，以及为什么吞掉所有解析异常：**"任何解析异常都吞掉：解析错误体失败不该比原始 HTTP 错误更严重。"** 这条原则很重要 —— 一个错误的 JSON 不该产生一个新的、更令人困惑的异常类型。`tests/test_gateway.py:223`（顶层形状）与 `:232`（409 体是 HTML，`json()` 直接抛）就是这两个分支的回归。

```python
def _header_int(headers, name: str) -> int:     # L116
    try:
        return int(float(str(headers.get(name, "") or "").strip()))   # L118
    except (TypeError, ValueError):
        return 0
```

**`int(float(...))` 而不是 `int(...)`**：HTTP 头里 `"1234.0"` 是常见写法（某些网关序列化 float 时会带 `.0`），直接 `int()` 会抛。先 `float` 再 `int` 两者都吃。

```python
def _retry_after_s(resp, default: float) -> float:   # L123
    headers = getattr(resp, "headers", None) or {}
    raw = str(headers.get("Retry-After", "") or "").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return max(0.0, float(default))              # L130
```

`max(0.0, ...)` 两侧都有，把负数夹到 0 —— 一个畸形的 `Retry-After: -5` 会让 `time.sleep(-5)` 抛 `ValueError`。注意 `Retry-After` 也支持 HTTP-date 格式（`Retry-After: Wed, 21 Oct 2015 07:28:00 GMT`）；这里只处理秒数，非数字会回落到 `default`，行为安全但功能不全（第 17 章的缺口项）。

## 5.4 `chat` 与 `chat_with_tools`（L153–200）

```python
def chat(self, messages, temperature=None) -> str:                     # L153
    payload = {"model": self._model, "messages": messages,
               "temperature": self._temperature if temperature is None else temperature}
    data = self._post(payload)
    try:
        content = data["choices"][0]["message"]["content"]              # L165
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected LLM response shape: {data}") from exc
    self._record_usage(data.get("usage") or {})                         # L168
    return content
```

`temperature is None` 与 `temperature == 0.0` 必须区分，所以用 `is None` 判断 —— 传 `0.0` 是"明确要贪婪解码"，不传是"用配置值"。这是 `temperature: float | None = None` 这个签名的意义。

三个异常合并处理（`KeyError`/`IndexError`/`TypeError`）覆盖了"响应形状不对"的所有方式：缺键、`choices` 是空数组、`data` 不是 dict。

```python
def chat_with_tools(self, messages, tools) -> tuple[str, list[ToolCall]]:   # L171
    payload = {..., "tools": tools}
    data = self._post(payload)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected LLM response shape: {data}") from exc
    self._record_usage(data.get("usage") or {})
    content = msg.get("content") or ""                                  # L190
    calls = []
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")   # L194
        except (json.JSONDecodeError, TypeError):
            args = {}                                                    # L196
        calls.append(ToolCall(id=tc.get("id") or "", name=tc["function"]["name"],
                              arguments=args))
    return content, calls
```

**L196 的 `args = {}` 是容错而非正确性**：模型偶尔会给出非法 JSON 的 arguments。选择"给空参数"而不是抛异常，让工具自己去校验并返回错误串（`CalculatorInput.expression` 是必填，空 dict 会在 `model_validate` 抛 → `_execute` 转成 `"error: ..."` 回传）。**错误由此继续走"数据回传"这条路，而不是变成异常穿透。** `tests/test_llm_client.py:24` 明确断言 `arguments="not json"` → `arguments={}`、无 id → `id=""`。

注意 `name=tc["function"]["name"]`（L198）是**下标而非 get** —— 名字缺失就是协议违规，这里不兜底。

## 5.5 `gateway_headers`（L203–225）(验证)

```python
def gateway_headers(self, payload: dict) -> dict[str, str]:
    if not self._settings.gateway_enabled:
        return {}                                            # L212
    headers: dict[str, str] = {}
    tenant = (self._settings.gateway_tenant or "").strip()    # L214
    if tenant:
        headers[HEADER_TENANT] = tenant
    messages = payload.get("messages") or []                  # L217
    session_id = resolve_session_id(messages, pinned=self._settings.gateway_session)
    if not session_id:
        return headers                                        # L220  ← 只带 tenant
    headers[HEADER_SESSION] = session_id
    headers[HEADER_IDEMPOTENCY_KEY] = derive_idempotency_key(
        session_id=session_id, messages=messages, payload=payload)
    return headers
```

docstring L208–209 是这条 `return` 的理由：

> 没有用户消息（纯 system 的内部调用）时不带幂等键：宁可不带，也不用随机值
> ——随机键等于放弃幂等，还会污染网关的键空间。

**这是一个"什么都不做才是对的"的决策。** 对纯 system 调用，没有稳定的会话锚点；用随机键会让网关为每次调用创建一个永不命中的键（既浪费网关存储，又让"键空间"这个调试工具失去意义）。**返回一个不带幂等键的请求，把幂等性诚实地放弃掉**，比假装有幂等性要好。

`tests/test_gateway.py:124` 断言纯 system 调用时三个头**全无**（只剩 `Authorization`）。

## 5.6 `_post`：三条网关语义（L227–293）(验证)

docstring（L230–237）把三条语义写全了：

```
* 409 infergate_idempotency_in_flight：同键的原始请求还在飞行。等
  Retry-After（缺失时用 gateway_retry_after_s）后**用同一个键**重发，
  上限 gateway_retry_attempts；到顶仍被拒就照常抛错。
* 409 infergate_idempotency_conflict：同键配了不同请求体，说明键派生有
  bug，重试不可能成功——大声记日志后立刻失败，不做无谓循环。
* X-InferGate-Idempotent-Replay: true：答案来自网关缓存，算正常成功，
  记日志 + 计数器 + last_gateway（"没有重复计费"的证据）。
```

实现逐段：

```python
url = f"{self._base_url}/chat/completions"                    # L239
headers = {"Authorization": f"Bearer {self._api_key}"}        # L240
gateway_headers = self.gateway_headers(payload)
headers.update(gateway_headers)
key = gateway_headers.get(HEADER_IDEMPOTENCY_KEY, "")         # L243
# 只有"带了幂等键"的请求才可能出现同键并发，也才值得为重试多等
max_attempts = max(1, int(self._settings.gateway_retry_attempts)) if key else 1   # L245
```

**L245 是整段最有信息量的一行。** 它把"有没有身份"直接翻译成"能不能重试"：

- 有幂等键 → 最多试 `gateway_retry_attempts`（默认 3）次
- 没有幂等键（纯 system 调用）→ `max_attempts = 1`，**结构上不可能重试**

为什么必须这样？因为"重试"在这里的语义是**同键重发**。没有键就没有"同键"可言，重发会变成一次全新的、可能被重复计费的上游调用。所以限制不是出于谨慎，而是出于正确性。

```python
        attempts = 1
        resp = None
        for attempt in range(1, max_attempts + 1):            # L248
            attempts = attempt
            try:
                resp = httpx.post(url, json=payload, headers=headers,
                                  timeout=self._timeout, **httpx_env_kwargs(url))   # L251
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM request failed: {exc}") from exc               # L259
            kind = _gateway_error_type(resp)                                      # L260
            if kind == TYPE_IDEMPOTENCY_CONFLICT:                                 # L261
                logger.error(
                    "InferGate 幂等键冲突（%s, key=%s, session=%s）：同一个键被用于不同的"
                    "请求体。这是 Idempotency-Key 派生的问题，重试不会有结果，直接失败。",
                    TYPE_IDEMPOTENCY_CONFLICT, key or "-",
                    gateway_headers.get(HEADER_SESSION, "-"))
                raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:500]}")   # L269
            if kind == TYPE_IDEMPOTENCY_IN_FLIGHT and attempt < max_attempts:          # L270
                delay = _retry_after_s(resp, self._settings.gateway_retry_after_s)
                logger.warning("InferGate 报告同键请求仍在飞行（%s, key=%s）：%.2fs 后"
                               "重试同一键（第 %d/%d 次尝试）", ...)
                if self._settings.metrics_enabled:
                    record_in_flight_retry(self._model)                                # L282
                time.sleep(delay)
                continue                                                               # L284
            break                                                                      # L285
        if resp is None:   # max_attempts >= 1，正常到不了这里
            raise LLMError("LLM request failed: no response")                          # L287
```

**conflict 与 in_flight 的处理差异是这段代码的智力核心**：

| | in_flight (L270) | conflict (L261) |
|---|---|---|
| 含义 | 同键的**同一个**请求还在跑 | 同键配了**不同**请求体 |
| 性质 | 暂时状态，等一等就好 | 键派生有 bug，是**永久**错误 |
| 处理 | 等 `Retry-After`，**同键**重发 | `logger.error` + 立刻 `LLMError` |
| 为什么 | 并发是正常的（重试风暴、多进程） | 重试必然再次冲突 → 纯浪费 |

`logger.error` 里带了 `key` 与 `session`，因为冲突意味着"键派生函数对两个不同输入给出了同一个键"（或反过来），**这是需要人工介入的确定性 bug**，日志必须自带复现所需的全部输入。`tests/test_gateway.py:205` 断言 conflict 只调 1 次且 `sleeps == []`。

`attempt < max_attempts` 这个额外条件（L270）是"最后一次不再 sleep"的优化：如果已经是最后一次尝试，等待毫无意义，直接 `break` 到下面的错误处理更快。

**`httpx.HTTPError` 不重试（L258–259）** —— 网络错误直接抛。这是有意的：网络层的重试由上层 `with_retry`（`app/harness/retry.py`）负责，`_post` 只负责**幂等键语义**的重试。**两层重试分工明确，不重叠。** 如果这里也重试网络错误，就会形成 `3 × 3 = 9` 次实际请求的组合爆炸。

```python
        self._record_gateway(resp, key=key,
                             session_id=gateway_headers.get(HEADER_SESSION, ""),
                             attempts=attempts)                                    # L288
        if resp.status_code != 200:
            raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:500]}")  # L292
        return resp.json()
```

**先 `_record_gateway` 再判状态码**（而不是反过来）是刻意的：即使最终会抛错，网关的记账信息也要先落到 `last_gateway` 上，供调用方/测试检查。`resp.text[:500]` 截断错误体 —— 错误响应可能是整个 HTML 页面。

## 5.7 `_record_gateway`（L295–323）

```python
def _record_gateway(self, resp, *, key, session_id, attempts) -> None:
    headers = getattr(resp, "headers", None) or {}                             # L301
    replayed = str(headers.get(HEADER_REPLAY, "") or "").strip().lower() == "true"   # L302
    self.last_gateway = GatewayOutcome(key=key, session_id=session_id, replayed=replayed,
        replay_origin=str(headers.get(HEADER_REPLAY_ORIGIN, "") or ""),
        replay_age_ms=_header_int(headers, HEADER_REPLAY_AGE),
        replay_upstream=str(headers.get(HEADER_REPLAY_UPSTREAM, "") or ""),
        upstream_name=str(headers.get(HEADER_UPSTREAM_NAME, "") or ""),
        attempts=attempts)
    if replayed:
        logger.info("InferGate 幂等回放：本轮由网关回放已存答案，未重复上游调用（key=%s, "
                    "origin=%s, age=%dms, upstream=%s）", ...)
        if self._settings.metrics_enabled:
            record_idempotent_replay(self._model)                              # L323
```

docstring L298–299 说了为什么全用 `getattr + get`：

> 全部用 getattr + get 取值：裸 OpenAI 或旧网关不返回这些头时，
> 这里只是记下一堆空值，绝不影响正常返回。

**`getattr(resp, "headers", None) or {}`** 这一层是给测试替身准备的 —— `tests/test_gateway.py:301` 的 `BareResp` 连 `headers` 属性都没有，直接测这条路径。

**L302 的规范化链条值得记**：`str(...)` → `.strip()` → `.lower()` → `== "true"`。四步各挡一类：值是 `None`/非字符串（`str`）、带空白（`strip`）、大写 `"TRUE"`（`lower`）。`tests/test_gateway.py:282` 用 `Replay: false` 测反向。

**replayed 时记 `record_idempotent_replay`** —— 这是一个独立的 Prometheus 计数器（`warden_gateway_idempotent_replays_total{model=...}`），Grafana 有专门面板。它的业务含义是"这一次没有花钱"。

## 5.8 能力发现（L326–354）

```python
def capability(self, model=None) -> ModelCapability | None:          # L326
    if not self._settings.gateway_capabilities_enabled:
        return None
    return self._capabilities.model(self._base_url, model or self._model,
        api_key=self._api_key, timeout=min(float(self._timeout), _CAPABILITY_TIMEOUT_S))

def context_window(self, model=None) -> int | None:                  # L337
    """模型的上下文窗口（token）；未知 → None，调用方自行回退。

    注意：Warden 目前没有任何按 token 估上下文的地方（历史裁剪是
    WARDEN_CONTEXT_MAX_MESSAGES，按**消息条数**），所以这个值现在是给运维/
    未来的量化入口，没有接进 Agent 循环——接进去等于新造一套裁剪策略。
    """
    cap = self.capability(model)
    if cap is None or cap.context_window <= 0:
        return None
    return cap.context_window
```

**L340–342 那段 docstring 是一份诚实的设计说明**：这个能力查询**已经实现了但没有被消费**。它明确解释了不接进去的理由（"接进去等于新造一套裁剪策略"）—— 因为现有裁剪是**按消息条数**（`context_max_messages=30`），改成按 token 需要一整套 token 估算逻辑（tokenizer、缓存、消息格式开销估算），这是一个独立的、有风险的功能，不该顺手塞进 M6。

这是一个**可接受的"死代码"**吗？在这个案例里是的：它在 `tests/test_gateway_live.py:129` 被真实网关验证过（`context_window()` 取到 128000），而且它是运维入口（`/health` 或调试脚本可以直接调用）。但它确实是"实现完整、未接入主流程"的模块，第 17 章会作为一种"未闭环"列出来。

## 5.9 `_record_usage`（L356–368）

```python
def _record_usage(self, usage: dict) -> None:
    self.last_usage = Usage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0))
    if self._settings.metrics_enabled:
        cost = compute_cost(self._settings, self.last_usage.prompt_tokens,
                            self.last_usage.completion_tokens)
        record_llm_usage(self._model, self.last_usage.prompt_tokens,
                         self.last_usage.completion_tokens, cost)
```

`usage.get(...) or 0` 处理两种缺失：键不存在（`None`）和值为 `None`。`int()` 再兜一层（某些后端返回 float）。

**`last_usage` 是"最后一次调用的用量"**，不是累计。累计在 Prometheus 里（`warden_tokens_total`）。这个区分很重要：如果一个 Agent 循环调了 4 次 LLM，`last_usage` 只反映第 4 次，而指标反映全部 4 次。测试必须知道这一点 —— `tests/test_gateway.py:244` 断言的是 `last_usage.prompt_tokens == 1`（那次特定的调用）。

---

# 第 6 章 Agent 骨架：模板方法（`app/agent/base.py` 300 行）(会话记录)

> 本章内容已在**第 1 讲**完整交付给用户，此处保留完整要点以便本讲义自洽。

## 6.1 JSON 解析的三级降级链（L49–85）

```python
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)          # L49

def _iter_json_objects(text: str) -> Iterator[dict]:                      # L52
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            obj, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(obj, dict):
            yield obj
        index = text.find("{", max(end, index + 1))                       # L64
```

正则四段的作用：

| 片段 | 作用 |
|---|---|
| `` ``` `` | 匹配围栏开头 |
| `(?:json)?` | 可选的语言标注（` ```json ` vs ` ``` `） |
| `\s*` | 吃掉围栏后/前的空白与换行 |
| `(.*?)` + `re.DOTALL` | 惰性捕获内容，`.` 匹配换行（JSON 必须能跨行） |

**L64 的 `max(end, index + 1)` 是一个防御死循环的细节**：正常情况下 `end` 一定大于 `index`（解析器至少吃掉一个字符）；但如果某个 `{` 让 `raw_decode` 返回 `end == index`（退化情形），`text.find("{", index)` 会再次找到同一个位置，`while` 永不退出。`max` 强制至少前进一格。

```python
def extract_json(text: str) -> dict:                                       # L67
    text = text.strip()                                                    # L74
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()                                          # L76
    try:
        return json.loads(text)                                            # L79
    except json.JSONDecodeError:
        pass
    for obj in _iter_json_objects(text):                                   # L83
        return obj
    raise ValueError(f"could not parse JSON from model output: {text[:300]!r}")   # L85
```

**三级降级**：① 整体是一个 JSON → 直接返回；② 整体不是，但**扫描出第一个完整的 JSON 对象** → 返回它；③ 都不行 → `ValueError`，错误信息带前 300 字符。

**为什么"取第一个"而不是"把所有对象拼起来再解析"**：CHANGELOG 记录了这个 bug 的真实后果 ——

> `extract_json()` 改为取第一个完整对象——旧实现把整段拼起来解析，解析失败后原始 JSON 文本被当成最终答案，**工具因此从不被执行**——本机 1.5B 模型实测正是这种输出，修复前 `tools` 完全走不到。

小模型常在一轮里连吐两个 JSON（先给 `action` 再给 `final_answer`）。拼接会得到 `{...}{...}`，`json.loads` 必然失败，然后上层（`_loop` 的 `parsed is None` 分支）把整段原文当答案返回 —— **工具永远没机会执行**。改取第一个对象后，那个 `action` 会被正常执行。`tests/test_react.py:123` 与 `:137` 就是这两个 case 的回归。

**`ValueError` 消息带 `text[:300]!r`**（用 `!r` 加引号与转义）—— 模型输出可能含控制字符，`!r` 让它在一行日志里可读。

## 6.2 数据结构（L20–46）

```python
@dataclass
class AgentStep:                                       # L20
    index: int
    thought: str | None = None
    action: str | None = None
    action_input: dict | None = None
    observation: Any = None

    def to_dict(self) -> dict:                         # L29（返回五键）
        return {"index": self.index, "thought": self.thought, "action": self.action,
                "action_input": self.action_input, "observation": self.observation}

@dataclass
class AgentRunResult:                                  # L40
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    agent: str = ""
    model: str = ""
    self_eval: dict | None = None
```

`observation: Any`（不是 `str`）与 `schemas.py:26` 的 `StepView.observation: Any` 是一致的两端。

## 6.3 `run` / `resume` 的模板方法与那条时序约束（L126–169）

```python
def run(self, history: list[dict], trace_id: str | None = None) -> AgentRunResult:   # L126
    # 整轮运行（含下面 _history_with_summary 里的摘要压缩调用）都归属同一个网关会话。
    # 会话 id 由**裁剪前**的完整历史派生。
    with gateway_session_scope(derive_session_id(history)):                          # L130
        return self._turn(history, trace_id)

def _turn(self, history, trace_id) -> AgentRunResult:                                # L133
    started = time.time()                                                            # L134
    history = self._history_with_summary(history)                                    # L137  ← 裁剪在这
    record = self._begin("chat", history, trace_id)                                   # L138  ← 落库在这
    try:
        result = self._run(history, record)
        self._maybe_self_eval(result)
        self._finish_ok(record, result)
        self._record_metrics("chat", "ok", started)
        return result
    except Exception as exc:
        self._finish_error(record, exc)
        self._record_metrics("chat", "error", started)
        raise

def resume(self, run_id: str) -> AgentRunResult:                                     # L150
    record = self._load_run(run_id)                                                  # L152
    history = record.input if isinstance(record.input, list) else []                  # L154  ← 已是裁剪结果
    with gateway_session_scope(derive_session_id(history)):                          # L155
        return self._resume_turn(record)
```

**L137 在 L138 之前**，这个顺序有两个后果：

1. **落库的 `input` 是裁剪后的历史。** L135–137 的注释明说："压缩结果随 record.input 持久化，resume 重建上下文时复用同一份，保证续跑与首跑看到一致的上下文。" 这是**正确的续跑设计**。
2. **但 `resume` 用它派生会话 id 就是错的**（L154–155）。`record.input` 已被裁剪；如果裁剪发生了（历史超过 30 条），`record.input[0]` 是那个 `{"role": "user", "content": "[Earlier conversation summary]\n..."}` 摘要块，而 `_first_user_content` 只认 `role == "user"` —— **它会取到摘要文本而不是用户的原话**，从而派生出与首跑不同的 session id。

**触发条件**：历史超过 `context_max_messages`（默认 30）**且** resume 同一个 run。**后果**：网关账本把同一场对话劈成两段，统计失真。**修法**：把派生结果（或原始 seed）存进 `record.meta`。这就是**缺口 ①**。

## 6.4 持久化辅助（L178–223）

```python
def _begin(self, kind, payload, trace_id) -> RunRecord | None:              # L178
    if self.store is None:
        return None
    record = RunRecord(id=trace_id or new_trace_id(), kind=kind, name=self.name, input=payload)
    self.store.start(record)
    return record

def _finish_ok(self, record, result) -> None:                              # L185
    if record is None:
        return
    record.status = STATUS_OK
    record.output = result.answer
    record.finished_at = time.time()
    self.store.update(record)

def _finish_error(self, record, exc) -> None:                              # L190
    if record is None:
        return
    record.status = STATUS_ERROR
    record.error = f"{type(exc).__name__}: {exc}"                          # L194
    record.finished_at = time.time()
    self.store.update(record)

def _record_metrics(self, kind, status, started) -> None:                  # L199
    if self.settings.metrics_enabled:
        record_run(kind, self.name, status, time.time() - started)

def _load_run(self, run_id) -> RunRecord:                                  # L204
    if self.store is None:
        raise ValueError("resume requires a RunStore")
    record = self.store.get(run_id)
    if record is None:
        raise ValueError(f"unknown run: {run_id}")
    if record.kind != "chat" or record.name != self.name:                   # L211
        raise ValueError(f"run {run_id} is not a {self.name} chat run")
    return record

def _persist_step(self, record, step: AgentStep, raw: str | None = None) -> None:   # L214
    if record is not None:
        d = step.to_dict()
        if raw is not None:
            d["raw"] = raw                                                  # L221
        record.steps.append(d)
        self.store.update(record)
```

**`_finish_ok` 只写 `output`，从不写 `result.self_eval`** —— 这就是**缺口 ②**：`_maybe_self_eval` 有守卫 `if ... or result.self_eval is not None: return`（靠"已经算过"来避免重复），但 resume 时 `result.self_eval` 是新建的 `None`，守卫失效 → **resume 会重算一次自评**（多一次 LLM 调用）。修法：把 `self_eval` 存进 `record.meta` 或 `record.output` 旁边。

**`_load_run` 的三道闸**：store 不存在 → 明确的 "resume requires a RunStore"；记录不存在 → "unknown run: ..."；**kind/name 不匹配 → 拒绝**。第三道闸很重要：用 PlanAct 的实例去 resume 一个 React 的 run 会读到形状完全不同的 `steps`，必须在入口处拦住。这也是 `FunctionCallAgent._resume` 能安全假设 `meta["messages"]` 存在的前提。

**`_persist_step` 的 `raw` 参数（L221）**是跨循环行为差异的源头：
- `react.py:174` 传 `raw`（**未 strip**）
- `react.py:189` / `:221` 回填 messages 时用 `raw.strip()`
- `function_call.py:109` / `:148` 显式传 `None`

于是 **React 的落库 raw 与内存 messages 的 content 存在首尾空白差异**，resume 重建上下文时会把带空白的版本塞回去 → **缺口 ③**（首跑与 resume 的上下文不完全相同）。而 FunctionCall 传 `None` 让它的 resume 走 `record.meta["messages"]`（native 上下文），两套信息互不重叠 —— 这是正确的那一半（见 7.3）。

## 6.5 `_execute`：两级错误分层（L225–237）

```python
def _execute(self, action: str, action_input: dict) -> Any:                  # L225
    try:
        tool = self.tools.get(action)
    except ToolNotFoundError:
        return self._truncate_observation(
            f"error: unknown tool '{action}'. Available: {self.tools.names()}")   # L230
    try:
        result = tool.run(action_input)
    except Exception as exc:
        return self._truncate_observation(f"error: {type(exc).__name__}: {exc}")  # L236
    return self._truncate_observation(result)
```

**两级分层的意义**：

- **第一级（工具不存在）**：错误信息里带上 `self.tools.names()` —— **列出可用工具**。这是给模型的纠错提示：它知道拼错了名字，并且知道正确的名字有哪些。`tests/test_react.py:34` 断言 `"unknown tool" in str(steps[0].observation)`。
- **第二级（工具执行失败）**：带异常类型名。`f"{type(exc).__name__}: {exc}"` 而不是只有 `str(exc)` —— 类型名（`ValidationError` vs `ValueError`）对模型判断"是参数错了还是表达式错了"很有帮助。
- **`except Exception` 而不是更窄**：这里是有意的宽捕获，因为"工具抛什么"是不可控的（第三方 MCP 工具、pydantic、用户写的函数）。**捕获后转成字符串而不是 `raise`，因为循环的连续性优先。**

注意 `_truncate_observation` 在三条 `return` 上都调用了 —— 包括两条错误路径。**这是对的**：工具错误信息也可能很长（pydantic 的 ValidationError 会列出整个 schema）。

## 6.6 `_truncate_observation`：类型保持（L239–250）

```python
def _truncate_observation(self, value: Any) -> Any:                          # L239
    limit = self.settings.max_observation_chars
    if limit <= 0 or not isinstance(value, str):                             # L246
        return value
    if len(value) <= limit:
        return value
    return value[:limit] + f"... [truncated, {len(value)} chars total]"       # L250
```

**L246 的 `not isinstance(value, str)` 是整个函数的要害**。它意味着：

- 计算器返回的 `12`（int）**原样通过**，不被转成字符串、不被截断
- dict 输出（有 `output_model` 的工具）原样通过
- **只有字符串会被截断**

这保证了 `tests/test_react.py:31` 的 `steps[0].observation == 4`（int 而非 `"4"`）成立，也就保证了 `schemas.py` 里 `observation: Any` 这条契约的完整性。**如果这里写成 `str(value)[:limit]`，整条"观测保持原类型"契约会在最底层断掉。**

`limit <= 0` 是"关闭截断"的哨兵（`config.py:35` 注释"0 = 不截断"）。

截断信息 `f"... [truncated, {len(value)} chars total]"` **带总长度**：让模型知道被截掉了多少（`tests/test_react.py:48` 断言 `"truncated" in obs`）。

## 6.7 `_history_with_summary`：上下文压缩（L252–278）

```python
def _history_with_summary(self, history: list[dict]) -> list[dict]:           # L252
    limit = self.settings.context_max_messages                               # L259
    if limit <= 0 or len(history) <= limit:                                  # L260
        return history
    overflow = history[:-limit]                                              # L262
    kept = history[-limit:]                                                  # L263
    try:
        summary = self.llm.chat([                                            # L265
            {"role": "system", "content": "Summarize the conversation below in 2-3 "
             "sentences, preserving the user's goals, key facts, and decisions."},
            *overflow,
        ])
    except Exception:
        return kept                                                          # L276
    return [{"role": "user", "content": f"[Earlier conversation summary]\n{summary}"}, *kept]   # L278
```

四个设计点：

1. **按消息条数不按 token**（`config.py:37` 注释）。token 估算需要 tokenizer + 每条消息的格式开销模型，收益不确定。条数是粗粒度但**确定、零依赖、可测**的。
2. **`overflow` 作为 user 消息直接拼接**（L265–268）：`*overflow` 展开的是 `{"role": "user"/"assistant", ...}` 的混合序列，附在一个 system 之后。**没有把它包装成"这是历史对话"** —— 依赖模型的指令遵从。
3. **摘要失败退化为纯窗口 `kept`**（L276）—— 极端情况下丢信息但**保住对话**。`tests/test_context.py:44` 断言这个降级。
4. **摘要块伪装成 `role: "user"`（L278）** —— 这是最微妙的一处。它必须放在数组首位（模拟"用户最早说的话"），且必须是 `user` 角色（`assistant` 放在最前面会让很多后端报错）。**副作用：`_first_user_content` 会把它当作用户消息**，这正是缺口 ① 的机制。

`tests/test_context.py:26` 断言 `len(out) == 3`（1 摘要 + 2 保留）、`out[0]["content"].startswith("[Earlier conversation summary]")`。

## 6.8 自评四态与 `replace` 而非 `format`（L280–300）

```python
def _maybe_self_eval(self, result: AgentRunResult) -> None:                  # L280
    if not self.settings.self_eval_enabled or result.self_eval is not None:
        return
    result.self_eval = self._self_eval(result.answer)

def _self_eval(self, answer: str) -> dict | None:                            # L289
    try:
        content = self._SELF_EVAL_PROMPT.replace("<<ANSWER>>", answer)        # L296
        raw = self.llm.chat([{"role": "user", "content": content}])           # L297
        return extract_json(raw)                                             # L298
    except Exception:
        return None                                                          # L300
```

**四态**：默认关闭（不调用）→ 开启且已有值（resume 场景下本应如此）→ 开启且成功（挂载 dict）→ 任何异常（静默 `None`）。

**L292–293 的注释解释了为什么必须用 `replace`**：`_SELF_EVAL_PROMPT` 本身含字面 JSON 花括号（`{"confidence": ...}`），而 `answer` 里也可能有 `{}`（比如用户问"这个字典怎么写"）。用 `.format()` 会把这两处都当占位符，抛 `KeyError` 或产生错误替换。**`replace` 只替换一个精确的哨兵 `<<ANSWER>>`，其他花括号原样保留。** `tests/test_self_eval.py:59` 断言 `"answer 42" in calls[1][-1]["content"]`（答案确实被插进去了）。

**`except Exception: return None`（L299–300）** —— 注释说"绝不影响主流程"。自评是一个附加价值，它的失败不该让一个已经成功的答案变成错误。这是"主线 4：一切可降级"的直接体现。

---

# 第 7 章 三条循环

## 7.1 `app/agent/react.py`（235 行）(验证)

### docstring 声明的输出契约（L1–10）

```
输出契约（JSON）：模型每轮只能输出一个 JSON 对象，二选一：
  {"thought": "...", "action": "tool_name", "action_input": {...}}
  {"thought": "...", "final_answer": "..."}

选 JSON 而非经典自由文本 "Action:" 解析的原因：
- 本地小模型（Ollama/Qwen）对 JSON 指令的遵从度高于自由格式
- 解析鲁棒（去代码围栏后 json.loads），无需脆弱的正则
```

**这两条理由是"为什么不用经典 ReAct 文本格式"的完整回答。** 经典格式（`Thought: ...\nAction: ...\nAction Input: ...`）需要正则 + 多行状态机解析，且对模型的换行/大小写/冒号变体极其敏感。JSON 有标准解析器兜底。**这是"用格式约束换解析可靠性"，代价是 prompt 更长、模型可能吐非法 JSON（所以要 `_chat_json` 的自纠）。**

### 提示词四块（L17–66）

- `_SYSTEM_TEMPLATE`（L17–37）：`{tools}` 在 L21（渲染后的工具清单）、`{fewshot}` 在 L36。**JSON 示例里的花括号写成 `{{...}}`** 因为要过 `.format()`。
- `_FEWSHOT`（L39–46）：一个完整的 `12 * 34 → 408` 示例，L40 标注 `Example (reference only; do not echo it verbatim)` —— **这条防的是模型照抄示例**。`tests/test_react.py:114` 断言 prompt 含 `"12 * 34"` 与 `'"action": "calculator"'`。
- `_RETRY_JSON_PROMPT`（L49–52）：`"Your previous reply was not valid JSON. Reply with EXACTLY ONE valid JSON object — no code fences, no extra text."`
- `_FORCE_FINAL_PROMPT`（L54–57）：`"You have reached the step limit. Based on the observations above, give the best final answer you can, as plain text."`
- `_REFLECT_PROMPT`（L59–66）：要求 `{"verdict": "ok" | "redo", "feedback": "..."}`。

### `_chat_json` 的自纠一次（L85–101）

```python
def _chat_json(self, messages) -> tuple[dict | None, str]:                   # L85
    raw = self._chat(messages)
    try:
        return extract_json(raw), raw
    except ValueError:
        pass
    messages.append({"role": "assistant", "content": raw.strip()})            # L95
    messages.append({"role": "user", "content": _RETRY_JSON_PROMPT})          # L96
    raw = self._chat(messages)                                              # L97
    try:
        return extract_json(raw), raw
    except ValueError:
        return None, raw                                                    # L101
```

**把模型自己的错输出塞回上下文再要求重试**（而不是只发一句"请输出 JSON"）—— 这让模型看到自己错在哪。`raw.strip()` 而不是 `raw`：避免在 assistant 消息里留首尾空白。

**恰好自纠一次，不循环。** 理由：如果两次都不行，第三次大概也不行，而每次重试都是一次真实调用。返回 `(None, raw)` 让上层决定怎么办（React 把它当答案、PlanAct 会抛）。

`tests/test_react.py:76`（自纠成功）与 `:90`（两次垃圾 → `answer == "garbage two"`）覆盖两条路径。

### `_reflect` 的观测清单（L103–126）

```python
observations = "\n".join(
    f"- {s.action or '(think)'}: {s.observation}"
    for s in steps
    if s.observation is not None                                          # L111
)
```

`if s.observation is not None` 过滤掉"还没执行的步骤"（`AgentStep` 的 observation 默认 `None`）。`s.action or '(think)'` 给纯推理步一个标签。

**解析失败默认放行（L122–123）**：`if parsed is None: return "ok", ""`。verdict 不在 `("ok", "redo")` 也归 `"ok"`（L126）—— **白名单式的"只有明确要求重做才重做"**。反过来（默认 redo）会让一个解析故障引发无限回炉。

### `_resume` 的上下文重建（L131–148）(验证)

```python
def _resume(self, record: RunRecord) -> AgentRunResult:                       # L131
    messages = self._messages(record.input or [])                            # L133
    steps: list[AgentStep] = []
    for d in record.steps:
        s = AgentStep(index=d["index"], thought=d.get("thought"), action=d.get("action"),
                      action_input=d.get("action_input"), observation=d.get("observation"))
        steps.append(s)
        if d.get("raw"):
            messages.append({"role": "assistant", "content": d["raw"]})        # L145
        if d.get("action"):
            messages.append({"role": "user", "content": f"Observation: {d.get('observation')}"})   # L147
    return self._loop(messages, steps, record, len(steps))                    # L148
```

**两个 `if` 是"按需重建"**：`raw` 存在才回填 assistant 消息（因为 `_persist_step` 的 raw 是可选的）；`action` 存在才回填 observation（纯推理步没有观测）。`d.get("raw")`（而非 `"raw" in d`）同时挡 `None` 和空串。

**L145 用 `d["raw"]`（未 strip），与首跑的 `raw.strip()`（L221）不一致** → 缺口 ③。

### `_loop` 的四条出口（L150–235）(验证)

```python
for index in range(start_index, self.settings.agent_max_steps):               # L157
    parsed, raw = self._chat_json(messages)
```

**出口 A：解析彻底失败（L160–168）**

```python
    if parsed is None:
        # 自纠后仍解析不出 JSON：把原始文本当最终回答，避免死循环
        answer = raw.strip()
        s = AgentStep(index=index, thought=None, observation=answer)
        steps.append(s)
        self._persist_step(record, s, raw)                                    # L165
        return AgentRunResult(answer=answer, steps=steps, agent=self.name, model=self.llm.model)
```

**把垃圾原文当答案返回** —— 注释写明理由是"避免死循环"。替代方案（继续重试 / 抛异常）都更糟：重试会烧 token，抛异常会让一次已完成的对话白费。

**出口 B：`final_answer`（L170–195）**

```python
    if "final_answer" in parsed:
        answer = str(parsed["final_answer"])
        s = AgentStep(index=index, thought=parsed.get("thought"), observation=answer)
        steps.append(s)
        self._persist_step(record, s, raw)                                    # L174  ← 传 raw 未 strip

        if self.settings.reflect_enabled:                                     # L177
            verdict, feedback = self._reflect(answer, steps)
            if verdict == "redo":
                steps.append(AgentStep(index=index, thought="reflection",
                                       action="_reflect", observation=feedback))   # L180–186
                self._persist_step(record, steps[-1], None)                   # L188
                messages.append({"role": "assistant", "content": raw.strip()})   # L189
                messages.append({"role": "user", "content": f"Reflection: {feedback}"})   # L190
                continue                                                      # L191
        return AgentRunResult(...)
```

**反思分支的四个动作**：记一个 `action="_reflect"` 的步（`_persist_step` 传 `None`，因为这不是模型输出）、把原答案回填、把反馈作为 user 消息、`continue` 回炉。

**注意 `index` 没有自增** —— `for` 循环的 `index` 由 `range` 递增，但 `continue` 后会**用掉下一个 index**。所以一次 redo 会消耗两步配额。`tests/test_reflect.py:31` 断言"恰好 1 个 `action == "_reflect"` 的步且 feedback 在 observation 里"。

**出口 C：有 `action`（L197–207 的判断 + L209–222 的执行）**

```python
    action = parsed.get("action")
    action_input = parsed.get("action_input") or {}
    if not action:
        # 既没 action 也没 final_answer：把原始文本当最终回答，避免死循环
        answer = raw.strip()
        ...
    observation = self._execute(action, action_input)                         # L209
    s = AgentStep(index=index, thought=parsed.get("thought"), action=action,
                  action_input=action_input, observation=observation)
    steps.append(s)
    self._persist_step(record, s, raw)                                        # L218
    messages.append({"role": "assistant", "content": raw.strip()})            # L221
    messages.append({"role": "user", "content": f"Observation: {observation}"})   # L222
```

**观测用 `f"Observation: {observation}"` 拼成 user 消息** —— 这是"prompt 级工具调用"的本质：没有原生的 tool 角色，用对话轮次模拟。

**出口 D：步数耗尽兜底（L224–235）**

```python
try:
    final = self._chat(messages + [{"role": "user", "content": _FORCE_FINAL_PROMPT}]).strip()
except Exception:
    last = steps[-1].observation if steps else "(none)"
    final = (f"reached max steps ({self.settings.agent_max_steps}) without a "
             f"final answer; last observation: {last}")
return AgentRunResult(answer=final, steps=steps, agent=self.name, model=self.llm.model)
```

**两层兜底**：先尝试让模型收尾；连这个调用都失败（网络挂了）才用模板文本。模板文本带 `agent_max_steps` 与 `last observation` —— 让用户知道发生了什么以及最后知道的信息。

## 7.2 `app/agent/planact.py`（204 行）(验证)

### docstring 里的设计取舍（L8–10）

```
设计取舍：M0 用"规划期即固定工具调用"的确定性执行（不逐步二次推理），
可解释、可控、无额外 LLM 调用；逐步二次推理留作后续扩展点。
断点续跑：plan 与已完成步骤都落库，resume 时跳过已执行步骤、直接接着 Act/Summarize。
```

**"规划期即固定工具调用"是三个形容词的取舍**：可解释（看 plan 就知道会做什么）、可控（工具参数在规划时就定了，不会中途跑偏）、省调用（不逐步推理）。

**代价**：计划质量完全取决于第一次规划。如果第 3 步的观察让第 4 步变得没意义（或需要不同的参数），PlanAct 不会自动调整 —— 只有**工具失败**才会触发重规划（L133–137）。这是"确定性优先于适应性"的选择。

### `_run`：规划并立即落库（L74–87）

```python
def _run(self, history, record) -> AgentRunResult:                            # L74
    request = history[-1]["content"]                                          # L75
    plan_messages = [
        {"role": "system", "content": _PLAN_TEMPLATE.format(tools=self.tools.render_prompt())},
        *history,
    ]
    plan_raw = self._chat(plan_messages)
    plan = extract_json(plan_raw).get("plan") or []                           # L83
    if record is not None:
        record.meta = {"plan": plan, "request": request}                      # L85
        self.store.update(record)
    return self._execute_plan(request, plan, record, 0)
```

**`record.meta` 在这里被写入两个键**：`plan`（resume 时不用重新规划）与 `request`（resume 时 `history` 可能已经不在手边，而汇总阶段需要原始请求）。这是 `RunRecord.meta` 这个字段存在的理由（`run_store.py:28` 注释"断点续跑需要的额外状态（如 PlanAct 的 plan）"）。

**`.get("plan") or []`**：模型可能给 `{"plan": null}` 或没有 `plan` 键 → 都退化成空计划（然后直接跳到 Summarize）。

**`history[-1]["content"]`** 假设最后一条是用户消息。如果最后一条是 assistant（不可能在这条路径上），会取错。这是一个未做防御的假设。

### `_resume`：从步骤数继续（L89–94）

```python
def _resume(self, record) -> AgentRunResult:                                  # L89
    meta = record.meta or {}
    plan = meta.get("plan") or []
    request = meta.get("request") or ""
    # 已完成的步骤数 = len(record.steps)，从那里接着执行
    return self._execute_plan(request, plan, record, len(record.steps))       # L94
```

**"已完成步骤数 = `len(record.steps)`"这个等式成立的前提是"每步都恰好落一条 step"**。检查 `_execute_plan`：

- 有工具的步：`steps.append` + `record.steps.append`（L122–130、L150–159）→ 1:1 ✓
- 无工具的推理步：`steps.append` + `record.steps.append`（L148、L150–159）→ 1:1 ✓
- 反思？PlanAct 没有反思。

所以等式成立。**但这里有一个未测的边界**：如果某一步在 `_execute` 里**卡住**然后进程被杀（不是抛异常，而是 `os._exit`），`record.steps` 里没有这条 step，resume 会**重新执行它**。`_execute` 的副作用是工具自己决定的 —— 计算器无副作用，`save_note` 会写库（产生新 uuid → 重复记忆）。**"幂等续跑"的保证只覆盖"已落库的步骤不重复"，不覆盖"正在执行的那一步"。** 这是该系统的一个真实边界（第 17 章）。

### `_execute_plan`：别名、失败检测、逐步落库（L96–175）(验证)

```python
done = record.steps if record is not None else []                             # L99  ← 别名，不是拷贝
steps = [AgentStep(index=d["index"], thought=d.get("step"), action=d.get("tool"),
                   action_input=d.get("args"), observation=d.get("result")) for d in done]
results = [{"step": d.get("step"), "result": d.get("result")} for d in done]  # L110
```

**L99 是别名**（`record.steps` 与局部 `done` 指向同一个 list）。这**恰好是需要的**：后面 L151 `record.steps.append(...)` 之后，`done` 也随之增长 —— 但这不影响正确性，因为 `steps` 与 `results` 已经在 L100–110 一次性建好了。**如果后面还有代码遍历 `done`，别名的增长会造成重复处理。** 目前没有，所以是安全的（但脆弱）。

注意 `steps` 与 `results` 是**两种形状**：`AgentStep`（给返回值用）与 `{"step","result"}`（给 Summarize 的 JSON 用）。

```python
    replans_left = self.settings.max_replans                                   # L113
    i = start_index
    while i < len(plan):                                                      # L115
        item = plan[i]
        step_desc = item.get("step", "")
        tool = item.get("tool")
        args = item.get("args") or {}
        if tool:
            observation = self._execute(tool, args)                           # L121
            steps.append(AgentStep(index=i, thought=step_desc, action=tool,
                                   action_input=args, observation=observation))
            results.append({"step": step_desc, "result": observation})
            # 失败检测（_execute 的错误以 "error: " 前缀字符串回传）+ 动态重规划
            if (isinstance(observation, str) and observation.startswith("error:")    # L135
                    and replans_left > 0):
                replans_left -= 1
                replanned = self._replan(request, plan, i, item, observation)
                if replanned:
                    # 用重规划结果替换失败步骤之后的剩余步骤
                    plan = plan[: i + 1] + replanned                          # L142
                    if record is not None:
                        record.meta = {**(record.meta or {}), "plan": plan}
                        self.store.update(record)
        else:
            # 无工具的纯推理步骤：不额外调用 LLM，交由汇总阶段处理
            steps.append(AgentStep(index=i, thought=step_desc))              # L148
            results.append({"step": step_desc, "result": "(reasoning step)"})
        if record is not None:
            record.steps.append({"index": i, "step": step_desc, "tool": tool,
                                 "args": args, "result": results[-1]["result"]})   # L151–158
            self.store.update(record)
        i += 1
```

**L135 的 `observation.startswith("error:")` 没有尾空格，而 `app/tools/base.py:76` 的 `_is_error` 用的是 `"error: "`（带尾空格）。** 这是一个真实的精度不一致：

| 位置 | 字面量 | 效果 |
|---|---|---|
| `app/tools/base.py:75`（docstring） | `"error: "` | 文档说的约定 |
| `app/tools/base.py:76`（代码） | `value.startswith("error: ")` | 工具层的判定（严格） |
| `app/agent/planact.py:132`（注释） | `"error: "` | 引用了带空格的约定 |
| `app/agent/planact.py:135`（代码） | `observation.startswith("error:")` | 重规划触发（宽松） |

**后果**：形如 `"error:unknown"`（无空格）的串会触发 PlanAct 重规划，但 `tools/base.py` 不把它当错误、仍会尝试 `output_model` 校验。由于 `base.py::_execute` 生成的错误**总是**带尾空格（`f"error: unknown tool ..."`、`f"error: {type(exc).__name__}: {exc}"`），这条不一致在实践中不会触发 —— **它是"约定没有单一来源"的代码气味，不是活跃 bug。** 但如果有第三方工具自己返回 `"error:xxx"`，两个模块会对它产生不同判断。

**L142 `plan = plan[:i+1] + replanned`** —— 保留已执行的（含失败的）步骤，用重规划结果替换剩余的。失败步骤本身留在计划里（所以 `steps` 里能看到它失败了）。`tests/test_planact.py:47` 断言 `steps[0].action == "nope"`、`steps[1].action == "calculator"`。

**L144–145 把新 plan 写回 `record.meta`** —— resume 时用的是**重规划后**的计划，不是初始计划。这是正确的（否则 resume 会重试那个已知失败的步骤）。

**L148 的推理步不调用 LLM** —— `results` 里标记 `"(reasoning step)"`。这个字符串会进 summarize 的 JSON，让模型知道这一步没有观测。

**每步一次 `store.update`**（L160）—— 一次 PlanAct 运行会有 N 次 UPDATE。对个人项目（N 通常 < 10）可接受；高频场景会成为瓶颈。

### Summarize（L163–175）与 `_replan`（L177–204）

```python
    summary_messages = [
        {"role": "system", "content": "You are Warden, summarizing an executed plan."},
        {"role": "user", "content": _SUMMARIZE_TEMPLATE.format(
            request=request, results=json.dumps(results, ensure_ascii=False))},   # L168–170
    ]
    answer = self._chat(summary_messages).strip()
```

`json.dumps(results, ensure_ascii=False)` 把结果序列化成 JSON 而不是自然语言 —— **确定性、无歧义、模型容易解析**。`ensure_ascii=False` 让中文保持可读（也影响 token 数：中文转义成 `\uXXXX` 会显著变长）。

```python
def _replan(self, request, plan, failed_index, failed_item, error) -> list:    # L177
    """工具失败后让模型重规划剩余步骤；失败则返回 []（保持原计划剩余步骤）。"""
    remaining = plan[failed_index + 1 :]                                      # L181
    messages = [ ... {"content": json.dumps({"request": request,
        "failed_step": failed_item.get("step", ""), "error": error,
        "remaining_steps": remaining}, ensure_ascii=False)} ... ]
    try:
        raw = self._chat(messages)
        return extract_json(raw).get("plan") or []                            # L202
    except Exception:
        return []                                                             # L204
```

**只把"失败步骤之后"的步骤发过去**（L181），配合 `_REPLAN_TEMPLATE` 的规则（L58–60）"不得重复失败或已完成的步骤"。返回 `[]` 时上游 `if replanned:` 为假 → 保持原计划剩余步骤。**"重规划失败 = 按原计划继续"** 是一个安全的默认（比"中止整个计划"好）。

`replans_left` 每触发一次减一（L138），到 0 后不再重规划（L136 的条件）。`tests/test_planact.py:67` 用 `max_replans=1` 断言 `len(llm.calls) == 3`（plan + 1 replan + summarize）。

## 7.3 `app/agent/function_call.py`（161 行）(验证)

### docstring 的定位（L1–7）

```
与 ReAct（JSON-in-prompt，模型输出 {"action","action_input"}）的区别：
这里把工具以 OpenAI `tools` 协议直接交给模型，模型原生返回 `tool_calls`
（含 tool_call_id 与结构化 arguments），执行后以 `role:"tool"` 回填上下文。
两条路线并存，展示"prompt 级"与"协议级"两种工具调用方式。
```

**两条路线并存是这个项目的教学价值之一**：同一套工具注册表，两种调用机制。协议级的优势是 arguments 由模型侧的 schema 约束（很多推理引擎会做 constrained decoding），劣势是需要后端支持 `tools` 字段。

### `_tools_spec`（L41–53）

```python
return [{"type": "function",
         "function": {"name": t.name, "description": t.description,
                      "parameters": t.json_schema()}}
        for t in self.tools.all()]
```

注意 **`parameters` 直接用 `t.json_schema()`**，而 ReAct 走的是 `render_prompt()`（把 schema 塞进文本）。**同一个 schema 的两种投递方式** —— 这是注册表只暴露 `json_schema()` 与 `render_prompt()` 两个方法的原因。`tests/test_function_call.py:51` 断言 `captured["tools"][0]["type"] == "function"` 且含 `"parameters"`。

### `_run` 把 messages 存进 meta（L72–78）

```python
def _run(self, history, record) -> AgentRunResult:                             # L72
    messages = self._messages(history)
    if record is not None:
        # 把 messages 存进 meta（同一引用随循环增长），供 resume 原样重建 native 上下文
        record.meta = {"messages": messages}                                   # L76
        self.store.update(record)
    return self._loop(messages, [], record, 0)
```

**注释里的"同一引用随循环增长"是关键**：`record.meta["messages"]` 指向的就是 `_loop` 内部不断 append 的那个 list。每次 `store.update` 会**序列化当时的内容**，所以 DB 里存的是"截至上一次 update 的上下文快照"。

**这是 M2 最优雅的一处设计**：resume 不需要"重建" native 上下文（不需要重新构造 `tool_calls` 消息、重新配 `tool_call_id`），**直接反序列化即可保证逐字节一致**。对比 React：它的 resume 要用 `record.steps` 里的 `raw` 和 `action` **重新拼**出消息序列，因此才会有缺口 ③（strip 差异）。**FunctionCall 用"存整个上下文"换掉了"重建上下文的正确性风险"。**

### `_resume`（L80–92）(验证)

```python
def _resume(self, record) -> AgentRunResult:                                   # L80
    messages = (record.meta or {}).get("messages") or self._messages(record.input or [])
    steps = [AgentStep(index=d["index"], thought=d.get("thought"), action=d.get("action"),
                       action_input=d.get("action_input"), observation=d.get("observation"))
             for d in record.steps]
    return self._loop(messages, steps, record, len(steps))
```

**`or self._messages(record.input or [])` 是一条兜底**：万一 meta 里没有 messages（旧版本落的库、或 meta 被清空），退化成"从 input 重建"。这会让 `_loop` 的首次请求缺少 tool 历史，但至少能跑。

**注意它不从 `record.steps` 回填任何消息**（对比 `react.py:144–147`）。这是**正确的**：native 上下文已经在 `meta["messages"]` 里完整包含了 assistant 与 tool 消息，再从 `steps` 拼一遍就会**重复注入**。

### `_loop`：重复注入为什么不会发生（L94–161）(验证)

**这个问题值得单独说清楚，因为它是"看起来会重复但设计上不会"的典型。** 我为此写了一个专门的实验脚本（`scripts/exp_fc_resume.py`）来实测。

```python
    for index in range(start_index, self.settings.agent_max_steps):            # L101
        content, calls = self._chat_with_tools(messages)

        if not calls:                                                          # L104
            answer = (content or "").strip()
            s = AgentStep(index=index, thought=answer, observation=answer)     # L107  thought = answer
            steps.append(s)
            self._persist_step(record, s, None)                                # L109  raw=None
            return AgentRunResult(answer=answer, steps=steps, ...)

        messages.append({"role": "assistant", "content": content or None,      # L115–131
            "tool_calls": [{"id": c.id, "type": "function",
                "function": {"name": c.name,
                             "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in calls]})
        for c in calls:                                                        # L132
            observation = self._execute(c.name, c.arguments)
            obs_text = (observation if isinstance(observation, str)
                        else json.dumps(observation, ensure_ascii=False, default=str))   # L134–138
            messages.append({"role": "tool", "tool_call_id": c.id, "content": obs_text})   # L139
            s = AgentStep(index=index, thought=None, action=c.name,
                          action_input=c.arguments, observation=observation)
            steps.append(s)
            self._persist_step(record, s, None)                                # L148  raw=None
```

**关键：L109 与 L148 都显式传 `raw=None`**，所以 FunctionCall 落库的步骤**没有 `raw` 键**。而 `_resume`（L80–92）**根本不看 `raw`/`action` 来拼消息** —— 它直接用 `meta["messages"]`。

于是"meta 里的 native 消息"与"record.steps 里的结构化步骤"被设计成**互不重叠的两套信息**：前者用于重建上下文，后者用于返回给调用方（`steps` 字段、CLI 打印、HTTP 响应）。**重复注入在上游就被避免了，不是靠去重逻辑。**

**实测验证**（`scripts/exp_fc_resume.py`，我为此写的脚本）：提示词"用计算器算 3 * 4，再用计算器算 5 * 6，最后只回答第二个结果。"

- crash 阶段退出码 70
- DB 里 `steps=2`、`meta['messages']=5 条` = `[system, user, assistant(tool_calls=calculator,calculator), tool(content='12'), tool(content='30')]`
- resume 答案 `'30'`
- **resume 首次请求也是 5 条消息、`role='tool'` 2 条、`role='user'` 且以 `Observation:` 开头 0 条** → **上下文没有重复注入**

`tests/test_resume.py` 只覆盖 React 与 PlanAct，**FunctionCall 的 resume 没有单元测试** —— 只有我这个手写实验。这是第 17 章的一个覆盖率缺口。

**`thought=answer, observation=answer`（L107）是 thought 与 answer 重复**：FunctionCall 的最终回答没有独立的"思考"字段（native 协议里 content 就是回答）。把两者设成同一个值是"让 CLI 打印不至于空着"的取舍。**代价：`AgentStep.thought` 在这里语义失真。**

**`obs_text` 的三段式（L134–138）**：字符串原样用；非字符串 `json.dumps(..., default=str)`。`default=str` 兜住不可序列化的对象（比如 datetime），避免"工具返回了一个 datetime 导致整个循环崩"。**注意存进 `AgentStep` 的 `observation` 是原值（L145），只有进 messages 的才是序列化后的** —— 保持了 `schemas.py` 的 `observation: Any` 契约。

**原生协议要求 assistant 消息带 `content: content or None`（L118）** —— 有些后端在有 tool_calls 时要求 content 显式为 `null` 而不是空串。

## 7.4 三条循环的横向对比

| | ReAct | PlanAct | FunctionCall |
|---|---|---|---|
| 工具投递 | prompt 文本（`render_prompt`） | prompt 文本 | 原生 `tools` 协议 |
| 模型输出 | JSON 对象（含 JSON 失败自纠） | 一次规划 JSON | `tool_calls` |
| 观测回填 | `user: "Observation: ..."` | 不回填（规划期已定） | `role: "tool"` + `tool_call_id` |
| LLM 调用次数 | 每步 1 次 + 自纠/反思 | 1 规划 + 1 汇总（+重规划） | 每步 1 次 |
| 落库的 raw | 有（未 strip） | 无（`steps` 形状不同） | 显式 `None` |
| resume 依据 | `record.steps` 重建消息 | `meta["plan"]` + 步骤数 | `meta["messages"]` 原样 |
| 动态适应 | 每步都可重新决策 | 仅工具失败时重规划 | 每步都可重新决策 |
| 副作用风险 | 每步可重试 | 未落库的那一步会重跑 | 每步可重试 |

---

# 第 8 章 三种多 Agent 协作

三个文件都不长（47 / 43 / 80 行），但覆盖了三种**结构上不同**的协作范式。

## 8.1 `app/agent/orchestrator.py`（47 行）—— 路由

```python
class Orchestrator:
    def __init__(self, specialists: dict[str, BaseAgent], llm: LLMClient) -> None:   # L21
        if not specialists:
            raise ValueError("Orchestrator needs at least one specialist")           # L23–24
        self.specialists = specialists
        self.llm = llm

    def _roster(self) -> str:                                                        # L28
        return "\n".join(f"- {name}: {a.description or a.name}"
                         for name, a in self.specialists.items())

    def run(self, history: list[dict]) -> AgentRunResult:                             # L33
        request = history[-1]["content"] if history else ""                          # L34
        route = extract_json(self.llm.chat([                                          # L35–42
            {"role": "system", "content": _ROUTER_SYSTEM.format(roster=self._roster())},
            *history,
        ]))
        name = route.get("specialist")                                               # L43
        task = route.get("task") or request                                          # L44
        agent = self.specialists.get(name) or next(iter(self.specialists.values()))   # L45–46
        return agent.run([{"role": "user", "content": task}])                         # L47
```

**三个值得注意的点**：

1. **路由 LLM 调用没有包 `with_retry`**（L35），而所有 Agent 内部的 `_chat` 都包了。**这是一处不一致**：路由调用的失败会直接冒泡，不重试。
2. **路由不产生 RunRecord**。只有被委派的专家 `agent.run()` 才落库。所以 `/api/v1/runs` 里看不到"路由决策"这一步 —— **多 Agent 的编排过程是不可见的**。
3. **原始 `history` 被丢弃**（L47）：专家只收到 `{"role": "user", "content": task}`，其中 `task` 是路由模型**改写过的**任务描述。**好处**：专家上下文干净、专注。**代价**：如果路由改写丢失了关键细节（比如用户提到的具体数字），专家无从得知。这是"路由即信息瓶颈"的固有取舍。
4. **兜底 `or next(iter(...))`（L46）**：模型给出未知专家名（或没给）时落到**第一个**成员。注释（L45 附近）说明"保证总能出结果"。`tests/test_orchestrator.py:50` 用 `"nope"` 测这条路径。**代价**：兜底是静默的 —— 用户不知道路由失败了。

## 8.2 `app/agent/pipeline.py`（43 行）—— 接力

```python
class CriticPipeline:
    def __init__(self, researcher: BaseAgent, critic: BaseAgent) -> None:      # L24
        self.researcher = researcher
        self.critic = critic

    def run(self, question: str) -> AgentRunResult:                            # L28
        research = self.researcher.run([{"role": "user", "content": question}])   # L29
        findings = research.answer
        # 用 f-string 拼接而非 .format()：findings 是模型/用户文本，可能含 {} 破坏 format
        critic_prompt = (f"Question: {question}\n\nResearch findings:\n{findings}\n\n"
                         f"{_CRITIC_INSTRUCTION}")                             # L33–35
        review = self.critic.run([{"role": "user", "content": critic_prompt}])    # L36
        return AgentRunResult(answer=review.answer,
                              steps=research.steps + review.steps,              # L40
                              agent="critic_pipeline",
                              model=self.researcher.llm.model)                  # L42
```

**L32 的注释是一处重要的教训**：`findings` 是模型/用户文本，可能含 `{}`。如果用 `.format()`，一段含 `{"a": 1}` 的研究结果会让 `.format()` 抛 `KeyError` 或注入错误内容。**这与 `_self_eval` 用 `replace` 而非 `format`（`base.py:296`）是同一个教训的第二次出现。** 两处都出过问题，两处都记了注释。

**L40 的 `research.steps + review.steps` 直接拼接，不重新编号。** 后果：`step.index` 会重复（research 的 0,1,2 后面跟 review 的 0,1）。`tests/test_pipeline.py:19` 只断言 `len(result.steps) == 2`，**没有断言 index 的连续性** —— 所以这个缺陷不会被测试发现。在 CLI 打印（`cli.py` 的 `step {s.index}: ...`）和 HTTP 响应（`StepView.index`）里，用户会看到 `step 0` 出现两次。**这是一处真实的可观测缺陷**（第 17 章）。

**`model=self.researcher.llm.model`（L42）** —— 取 researcher 的模型名。如果两个 Agent 用不同模型，这个值只反映前者。个人项目里通常同一个模型，所以可接受，但语义上不精确。

## 8.3 `app/agent/blackboard.py`（80 行）—— 共享状态

```python
class Blackboard:                                                             # L18
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()                                          # L23

    def write(self, key: str, value: str) -> None:                             # L25
        with self._lock:
            self._data[key] = value

    def read(self, key: str, default: str | None = None) -> str | None:        # L29
        with self._lock:
            return self._data.get(key, default)

    def keys(self) -> list[str]: ...                                          # L33
    def snapshot(self) -> dict[str, str]: ...                                  # L37

class BlackboardTeam:                                                          # L43
    def __init__(self, blackboard: Blackboard | None = None) -> None:          # L51
        self.blackboard = blackboard or Blackboard()
        self._members: list[tuple[BaseAgent, str]] = []

    def add(self, agent: BaseAgent, role: str) -> None:                        # L54
        self._members.append((agent, role))

    def run(self, question: str) -> AgentRunResult:                            # L58
        if not self._members:
            raise ValueError("BlackboardTeam needs at least one member")       # L59–60
        all_steps: list[AgentStep] = []
        result = None
        for i, (agent, role) in enumerate(self._members):                      # L63
            if i == 0:
                prompt = f"{question}\n\n{role}"                               # L65
            else:
                prompt = (f"Task: {question}\n\n" + f"{role}\n\n" +
                    "Earlier agents wrote their contributions to the shared blackboard. " +
                    "Read what you need with the blackboard_read tool, then continue.")   # L66–72
            result = agent.run([{"role": "user", "content": prompt}])           # L73
            all_steps += result.steps                                           # L74
        return AgentRunResult(answer=result.answer, steps=all_steps,
                              agent="blackboard_team",
                              model=self._members[0][0].llm.model)              # L75–80
```

**`Blackboard` 用 `threading.Lock` 而不是 `contextvars`/`asyncio.Lock`** —— 因为 Agent 循环是同步的，而 `BlackboardTeam.run` 是顺序执行的（L63 的 `for`），实际上**不存在并发写**。锁是预防性的（如果未来并行化，或者工具在别的线程里跑）。`tests/test_blackboard.py` 的测试也是顺序的。

**L66–72 的提示词明确告诉后续成员"用 blackboard_read 工具读取"** —— 这是**协作协议写进提示词**而非写进代码的例子。代码只提供工具，怎么用由提示词引导。

**docstring（L1–9）把黑板与 CriticPipeline 的差别说清楚了**：

> 对比 CriticPipeline 的"固定线性流水线"（前一个产出直接塞进后一个 prompt），黑板是"共享状态"——写方不必知道谁会读，读方不必依赖写方返回结构。

**三种范式的本质区别**：

| | Orchestrator | CriticPipeline | BlackboardTeam |
|---|---|---|---|
| 谁决定谁参与 | LLM 路由（运行时） | 代码写死（构造时） | 代码写死（构造时） |
| 通信方式 | 无（只有一个专家跑） | 前一个的 answer 进下一个 prompt | 共享键值存储 |
| 耦合度 | 无 | 强（要求 answer 是可直接用的文本） | 弱（读方自己挑 key） |
| 执行次数 | 1 个专家 | 全部固定成员 | 全部固定成员 |
| 落库的 RunRecord | 只有专家的 | 每个成员各一条 | 每个成员各一条 |

**注意最后一行**：`Orchestrator`、`CriticPipeline`、`BlackboardTeam` **本身都不落库** —— 它们不生成 RunRecord，只有它们委派的专家（`agent.run()`）会。所以一次 `team` 命令在 DB 里留下的是专家 run，而不是"团队运行"。**编排层没有自己的可观测性**，这是第 17 章的一项。

`tests/test_blackboard.py:41` 断言 `len(result.steps) == 4`、`steps[2].action == "blackboard_read"` —— 验证了"researcher 写、writer 读"的完整往返。

---

# 第 9 章 Harness：状态、重试、指标、trace、成本

## 9.1 `app/harness/run_store.py`（154 行）(验证)

docstring（L1–5）声明了设计取舍：

```
设计取舍：M2 用 stdlib sqlite3 + WAL，零外部服务即可跑通最小版本；
接口只暴露 start/update/get/list，后续换 PostgreSQL 只改这一处。
```

### 常量与数据类（L16–34）

```python
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"

@dataclass
class RunRecord:
    id: str
    kind: str   # "chat" | "task"
    name: str   # agent 名或 task 名
    status: str = STATUS_RUNNING
    input: Any = None
    meta: dict = field(default_factory=dict)   # 断点续跑需要的额外状态（如 PlanAct 的 plan）
    output: str | None = None
    error: str | None = None
    steps: list[dict] = field(default_factory=list)
    attempts: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
```

**`kind` 只有两个值**（`"chat"` / `"task"`），配合 `name` 形成二级区分：`(kind="chat", name="react")` vs `(kind="task", name="news_digest")`。`_load_run` 的三道闸（`base.py:211`）就是用这个二元组做匹配。

**`meta` 的用途是"每个循环自己的续跑状态"**：PlanAct 存 `plan`/`request`，FunctionCall 存 `messages`，React 不存（它靠 `steps` 里的 `raw` 重建）。**同一个字段承载三种完全不同的形状** —— 这是"用 dict 换灵活性"的取舍。代价：类型不安全，`_resume` 里必须用 `.get(...) or 默认值` 层层兜底。

**`started_at` 用 `field(default_factory=time.time)`** 而不是 `time.time()` —— 后者在类定义时求值一次，所有实例会共享同一个时间。

### 连接与 schema（L37–66）

```python
class RunStore:
    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)               # L40
        # check_same_thread=False：调度线程与 API 线程可共享同一连接
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)   # L42
        self._conn.row_factory = sqlite3.Row                               # L43
        self._conn.execute("PRAGMA journal_mode=WAL")                      # L44
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                input TEXT,
                meta TEXT,
                output TEXT,
                error TEXT,
                steps TEXT,
                attempts INTEGER DEFAULT 0,
                started_at REAL,
                finished_at REAL
            )
            """)
        self._conn.commit()
```

四个决定：

1. **`mkdir(parents=True, exist_ok=True)`（L40）** —— 让 `RunStore("data/warden.db")` 在 `data/` 不存在时也能工作。这是"装配即可用"的一部分（`main.py` 在 import 时就 new 它）。
2. **`check_same_thread=False`（L42）** —— 允许 APScheduler 的 `BackgroundScheduler` 线程与 FastAPI 的请求处理线程共用连接。**这是一个"用性能换便利"的决定**：SQLite 默认禁止跨线程使用连接正是为了防止未同步的访问；关掉它意味着把同步责任交给 WAL + 每次操作后 `commit`。
3. **`PRAGMA journal_mode=WAL`（L44）** —— Write-Ahead Logging 让读者不阻塞写者。对"后台任务在写、控制台在轮询读"的场景是必需的。
4. **`input`/`meta`/`steps` 存 TEXT（JSON 字符串）** —— 而不是规范化成多张表。**"一条 run 一个 blob"** 让写入是单行 INSERT/UPDATE（无事务协调），读取是一次 `json.loads`。代价：无法按 step 内容查询（SQLite 的 JSON1 扩展可以，但没用到）。

`tests/test_harness.py:47` 断言 `RunStore(path).close()` 调两次不抛 —— 验证 `CREATE TABLE IF NOT EXISTS` 的幂等性。

### `update` 的字段集（L76–90）

```python
def update(self, run: RunRecord) -> None:
    self._conn.execute(
        "UPDATE runs SET status=?, output=?, error=?, steps=?, meta=?, attempts=?, finished_at=? WHERE id=?",
        (run.status, run.output, run.error, self._dump(run.steps), self._dump(run.meta),
         run.attempts, run.finished_at, run.id))
    self._conn.commit()
```

**只更新 7 个字段**：`status`/`output`/`error`/`steps`/`meta`/`attempts`/`finished_at`。

**不更新的字段**：`id`（主键）、`kind`/`name`（身份，不该变）、`input`（首跑时定下，**这是关键** —— 见 9.1 末尾）、`started_at`（首跑时间）。

**`input` 不可变是缺口 ① 的机制来源**：`run()` 把**裁剪后**的历史存进 `input`（`base.py:138`），后续 `update` 永远不动它。所以 `resume` 拿到的一直是裁剪后的版本，无法恢复原始历史去重新派生会话 id。

### `list` 与 JSON 容错（L96–105、L110–154）

```python
def list(self, limit: int = 50, kind: str | None = None) -> list[RunRecord]:
    if kind:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE kind=? ORDER BY started_at DESC LIMIT ?", (kind, limit)).fetchall()
    else:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [self._row_to_record(r) for r in rows]

@staticmethod
def _row_to_record(row: sqlite3.Row) -> RunRecord:
    def _load(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None
    return RunRecord(..., input=_load(row["input"]), meta=_load(row["meta"]) or {},
                     steps=_load(row["steps"]) or [], ...)
```

**`_load` 的三层容错**：空串/`None` → `None`；JSON 坏了 → `None`（而不是抛）。**这让一个损坏的行不会让整个 `/api/v1/runs` 500。** 对"长期运行的个人系统"这是对的：宁可列表里某行显示不完整，也不要整个页面挂掉。

**`meta=_load(...) or {}` 与 `steps=_load(...) or []`** —— 把 `None` 归一成空容器，这样调用方可以无条件 `record.meta.get(...)`。**这是"在边界处消除 None"的正确做法。** 注意 `input=_load(...)` **没有**这个归一化（保持 `None`），因为 `input` 是 `Any`（可能真的存了 `None`），而 `base.py:154` 用 `isinstance(record.input, list)` 判断。

`list` 的 `ORDER BY started_at DESC` 让"最近的 run 在最前"—— 控制台的 `/api/v1/runs?limit=20` 直接消费这个顺序。`tests/test_harness.py:37` 断言 `list()` 返回 `["c","b","a"]`（插入顺序 a,b,c 的逆序）。

## 9.2 `app/harness/retry.py`（43 行）(会话记录)

```python
def with_retry(fn, *, attempts=3, backoff_s=1.0, retriable=None, on_attempt=None):   # L14
    last = None
    for i in range(attempts):
        if on_attempt is not None:
            on_attempt(i + 1)                                    # 从 1 开始
        try:
            return fn()
        except Exception as exc:
            last = exc
            if retriable is not None and not retriable(exc):
                raise                                            # 不可重试 → 立刻抛
            if i < attempts - 1:
                delay = backoff_s * (2 ** i)                     # 指数退避
                log.warning("retry %d/%d after %.2fs: %s", i + 1, attempts, delay, exc)
                time.sleep(delay)
    assert last is not None
    raise last
```

四个设计点：

1. **`on_attempt(i + 1)` 在每次尝试前调用，从 1 开始。** `scheduler/runner.py` 用 `on_attempt=attempts.append` 来数"实际尝试了几次"，然后写进 `record.attempts`。**这是"重试次数"的可观测化** —— `tests/test_scheduler.py:164` 断言 `retry_attempts=3` 时 `record.attempts == 3`。
2. **`retriable` 是一个可选谓词**，默认（`None`）表示"全部可重试"。这比"传入异常类型元组"更灵活（可以按错误码/消息判断）。目前**没有调用方使用它** —— 它是一个预留扩展点。
3. **`delay = backoff_s * (2 ** i)`** —— 1s、2s、4s。对 3 次尝试，总等待 3 秒。`tests/test_retry.py` 用 `backoff_s=0` 避免真睡眠；`tests/test_gateway.py` 用 monkeypatch `time.sleep` 精确断言退避秒数。
4. **末尾 `assert last is not None`** —— 静态分析用的断言（如果 `attempts=0`，循环体不执行、`last` 是 `None`，`raise last` 会抛 `TypeError`）。`attempts=0` 从未被调用方使用，但断言让"不可能发生"在测试里可被捕获。**注意 `attempts < 1` 时函数会抛 `TypeError` 而不是 `ValueError`** —— 一个小的健壮性缺口（第 17 章）。

**与 `_post` 的两层重试分工**（第 5.6 节已述）：`with_retry` 负责**网络层**重试（全是幂等的 GET/POST 重发），`_post` 负责**幂等键语义**的重试（同键重发）。两者不重叠，所以不会组合爆炸。

## 9.3 `app/harness/trace.py`（49 行）(会话记录)

```python
_trace_var: ContextVar[str] = ContextVar("warden_trace_id", default="")        # L10

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s"  # L12

def new_trace_id() -> str:
    return uuid.uuid4().hex                                                     # L15

@contextlib.contextmanager
def trace_span(trace_id: str | None = None):                                    # L24
    token = _trace_var.set(trace_id or new_trace_id())
    try:
        yield trace_id or _trace_var.get()
    finally:
        _trace_var.reset(token)                                                  # L31

class TraceFilter(logging.Filter):                                              # L34
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_var.get() or "-"                                # L36
        return True

def configure_logging(level: int = logging.INFO) -> None:                        # L42
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(TraceFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]                                                 # L47
    root.setLevel(level)
```

**`ContextVar` 而不是 `threading.local`**：`ContextVar` 在 `asyncio` 任务间自动隔离，且在 `asyncio.to_thread` 时按 context 复制。Warden 里 FastAPI（async 请求上下文）与 APScheduler（线程）是两个执行模型，`ContextVar` 让"作用域只在 with 块内有效"在两者下都成立。`gateway/identity.py` 的 `_session_var` 用的是同一机制、同一理由。

**`reset(token)` 而非 `set("")`** —— 保证可嵌套（外层 span 不会被内层清掉）。`tests/test_harness.py:53` 断言进出 `trace_span` 后 `get_trace_id()` 回到 `""`。

**`root.handlers[:] = [handler]`（L47）是整体替换而非 `addHandler`** —— 避免"多次调用 `configure_logging` 导致日志重复输出"（uvicorn 与 CLI 都会调它）。代价：**它会干掉别处已经装好的 handler**（比如测试里 `caplog` 的 handler）。这是"幂等优先"的取舍。

**`TraceFilter` 注入 `record.trace_id`** —— 让 format 里的 `%(trace_id)s` 有值。`or "-"` 保证没有 span 时也有可读输出。

## 9.4 `app/harness/metrics.py`（68 行）(会话记录)

docstring（L1–4）给出失败率定义：

```
失败率 = warden_runs_total{status="error"} / warden_runs_total（Grafana 面板与告警据此计算）
```

六个指标：

```python
RUNS_TOTAL = Counter("warden_runs_total", ..., ["kind", "name", "status"])          # L10
RUN_DURATION = Histogram("warden_run_duration_seconds", ..., ["kind", "name"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0))       # L20
TOKENS_TOTAL = Counter("warden_tokens_total", ..., ["model", "direction"])          # L28
COST_TOTAL = Counter("warden_cost_dollars_total", ..., ["model"])                   # L32
GATEWAY_REPLAY_TOTAL = Counter("warden_gateway_idempotent_replays_total",
    "InferGate 幂等回放次数（命中网关已存答案，未重复上游调用也未重复计费）。", ["model"])   # L36
GATEWAY_IN_FLIGHT_RETRY_TOTAL = Counter("warden_gateway_in_flight_retries_total",
    "因 409 infergate_idempotency_in_flight（同键原始请求仍在飞行）而等待重试的次数。", ["model"])   # L40
```

**bucket 边界的选择反映了 Agent 运行的耗时分布**：从 0.1s（纯计算/缓存命中）到 600s（本地大模型长生成）。10 个边界覆盖两个数量级。`p95` 面板直接用这个直方图。

**两个网关指标都带 docstring 式的 help 文本**，而且文本本身就是业务说明（"未重复上游调用也未重复计费"）。**这让 Prometheus/Grafana 的 tooltip 自带语义**，运维不必查代码。

```python
def record_run(kind: str, name: str, status: str, duration_s: float) -> None:       # L43
    RUNS_TOTAL.labels(kind=kind, name=name, status=status).inc()
    RUN_DURATION.labels(kind=kind, name=name).observe(duration_s)

def record_llm_usage(model, prompt_tokens, completion_tokens, cost) -> None:        # L49
    TOKENS_TOTAL.labels(model=model, direction="prompt").inc(prompt_tokens)
    TOKENS_TOTAL.labels(model=model, direction="completion").inc(completion_tokens)
    COST_TOTAL.labels(model=model).inc(cost)

def metrics_text() -> str:                                                           # L66
    out = generate_latest()
    return out.decode("utf-8") if isinstance(out, bytes) else out
```

**`metrics_text` 的 `isinstance` 判断**：不同 `prometheus_client` 版本返回 `bytes` 或 `str`。这是对依赖版本差异的防御。

**`record_run` 的 status 取值是 `ok`/`error`/`skipped`** —— 注意 `skipped` 也计入 `WARDEN_RUNS_TOTAL`，所以失败率公式的分母包含 skipped 的运行。这是正确的（skipped 也是一次"运行"），但会让"没有新条目"的日子分母变大、失败率被稀释。**告警里用 `clamp_min(..., 0.0001)` 防除零**（`deploy/prometheus/alerts.yml`）。

**所有 `record_*` 都由调用方的 `settings.metrics_enabled` 门控**（`base.py:200`、`llm.py:281/322`、`runner.py`）—— 指标模块本身不检查开关。这是"策略在调用方、机制在模块"的分层。

## 9.5 `app/harness/cost.py`（13 行）(会话记录)

```python
def compute_cost(settings, prompt_tokens: int, completion_tokens: int) -> float:    # L8
    return (prompt_tokens / 1e6 * settings.llm_input_price_per_1m
            + completion_tokens / 1e6 * settings.llm_output_price_per_1m)
```

**`1e6` 而不是 `1000000`** —— 与配置字段名 `_per_1m` 视觉对应。默认单价 0 → 本地模型成本恒为 0（`tests/test_cost.py:12` 断言 `compute_cost(Settings(), 1000, 1000) == 0.0`），而设 `2.0`/`6.0` 时 `1M + 1M tokens == 8.0`。

**零依赖、纯函数、无副作用** —— 13 行是全仓最小的模块。它单独存在（而不是内联在 `_record_usage` 里）的价值是**可单测**。

## 9.6 `app/harness/__init__.py`（33 行）

`__all__` 18 项：`STATUS_RUNNING`/`STATUS_OK`/`STATUS_ERROR`、`RunRecord`、`RunStore`、`TraceFilter`、`compute_cost`、`configure_logging`、`get_trace_id`、`metrics_text`、`new_trace_id`、`record_idempotent_replay`、`record_in_flight_retry`、`record_llm_usage`、`record_run`、`trace_span`、`with_retry`。

**注意它同时导出 `TraceFilter` 与 `configure_logging`** —— 但 `run_store.py`/`metrics.py`/`retry.py`/`cost.py` 的具体函数名只导出"给外部用"的那部分（`log`、`_Entry` 之类不导出）。

---

# 第 10 章 工具系统与记忆 RAG

## 10.1 `app/tools/base.py`（76 行）(会话记录)

docstring（L1–11）说明输入模型承担两件事：①运行时校验 `model_validate` LLM 给的 action_input；②`model_json_schema` 注入 prompt。

```python
@dataclass(frozen=True)
class Tool:                                                    # L23
    name: str
    description: str
    input_model: type[BaseModel]
    func: Callable[..., Any]
    output_model: type[BaseModel] | None = None
    timeout_s: float | None = None

    def json_schema(self) -> dict:                             # L32
        return self.input_model.model_json_schema()

    def run(self, raw_input: dict | None) -> Any:              # L36
        args = self.input_model.model_validate(raw_input or {})     # L38
        result = self._invoke(args)
        if self.output_model is not None and not _is_error(result):  # L40
            result = self._coerce_output(result)
        return result

    def _invoke(self, args) -> Any:                            # L44
        if self.timeout_s is None or self.timeout_s <= 0:
            return self.func(**args.model_dump())
        with ThreadPoolExecutor(max_workers=1) as pool:         # L51
            future = pool.submit(lambda: self.func(**args.model_dump()))
            try:
                return future.result(timeout=self.timeout_s)    # L54
            except FuturesTimeout:
                # Python 线程无法被强杀：超时后后台线程会自己跑完，这里只是
                # 不再等待，把'超时'作为观测回传给 Agent，避免循环被单个工具卡死。
                return f"error: tool '{self.name}' timed out after {self.timeout_s}s"   # L56

    def _coerce_output(self, result) -> Any:                   # L58
        model = self.output_model
        try:
            if isinstance(result, model):
                return result.model_dump()
            if isinstance(result, str):
                return model.model_validate_json(result).model_dump()
            return model.model_validate(result).model_dump()
        except Exception as exc:
            return (f"error: tool '{self.name}' output does not match schema "
                    f"{model.__name__}: {type(exc).__name__}: {exc}")    # L69-70
```

**`frozen=True`** 让 Tool 是不可变的 —— 工具在注册后不该被改（否则 prompt 里的 schema 与运行时的校验会不一致）。

**`_invoke` 的超时是"软超时"**，注释（L54–55）说得非常明确：**"Python 线程无法被强杀：超时后后台线程会自己跑完，这里只是不再等待"**。含义：

- Agent 循环不会被卡住 ✓
- 但那个线程还活着，可能在**继续占用资源**（对本地模型推理尤其明显）
- 且它可能**继续产生副作用**（如果工具写文件/发请求）
- `ThreadPoolExecutor` 在 `with` 块退出时会 `shutdown(wait=True)` —— **等等，那不就等了吗？**

**这里有一个真实的细节**：`with ThreadPoolExecutor(max_workers=1) as pool:` 的 `__exit__` 调用 `shutdown(wait=True)`，所以**函数返回时会阻塞等待那个超时的线程完成**！这意味着超时实际上**没有**让调用方尽快返回。

验证：`tests/test_tools.py:67` 用 `timeout_s=0.05` + 睡 0.3 的函数，测试**通过了** —— 说明它确实返回了错误串。但它返回的时间是 0.3s（线程跑完）而不是 0.05s。**测试只断言返回值，没有断言耗时**，所以这个缺陷不会被发现。

**这是一处真实可观测的性能缺陷**：设了 `timeout_s` 的工具在超时时，调用方实际等待的时间 = 工具真实耗时（可能远超 `timeout_s`）。**修法**是不要在 `with` 块里等 —— 比如用模块级 executor 且不 `shutdown`，或者用 `pool.shutdown(wait=False)`。这是第 17 章的一项，且有明确证据（代码结构 + 测试没有耗时断言）。

**`_coerce_output` 三种输入**：已经是模型实例（`model_dump` 回来）、JSON 字符串（`model_validate_json`）、dict（`model_validate`）。覆盖了工具作者可能返回的所有形状。失败时返回 `"error: ..."` 串（带 `model.__name__` 与异常类型）—— 与 `_execute` 的错误约定一致。

**`_coerce_output` 失败 → 返回错误串，而不是抛** —— 但注意它是**在 `run` 里被调用并赋给 `result`**（L40–42），所以调用方拿到的是字符串。**这让"输出校验失败"与"工具正常返回一个以 error: 开头的字符串"无法区分** —— 后者会被 `_is_error` 视为工具已上报错误而跳过校验。这是一个理论上的混淆点（第 17 章）。

```python
def _is_error(value: Any) -> bool:                             # L74
    """以 "error: " 开头的字符串视为工具已上报错误，不再做输出校验。"""
    return isinstance(value, str) and value.startswith("error: ")   # L76
```

**L76 的 `"error: "` 带尾空格** —— 与 `planact.py:135` 的 `"error:"` 不一致（见 7.2）。

## 10.2 `app/tools/registry.py`（50 行）(会话记录)

```python
class ToolNotFoundError(Exception):
    pass                                                        # L13-14

class ToolRegistry:
    def register(self, tool: Tool) -> None:                     # L21
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")    # L23
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:                           # L26
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"unknown tool: {name}") from None   # L30

    def names(self) -> list[str]: return list(self._tools)      # L32
    def all(self) -> list[Tool]: return list(self._tools.values())   # L35

    def render_prompt(self) -> str:                             # L42
        if not self._tools:
            return "(no tools available)"                       # L44
        return "\n".join(
            f"- {tool.name}: {tool.description}\n  parameters: "
            f"{json.dumps(tool.json_schema(), ensure_ascii=False)}"
            for tool in self._tools.values())
```

**`raise ... from None`（L30）** 抑制异常链 —— `ToolNotFoundError` 是给 Agent 循环用的**控制流信号**（`_execute` 捕获它转成错误串），不是 bug。带上 `KeyError` 的链会让日志里多一段无用的 `During handling of the above exception...`。

**`register` 抛 `ValueError` 而 `get` 抛 `ToolNotFoundError`** —— 前者是**编程错误**（重复注册，启动期就该发现），后者是**运行时输入问题**（模型编了个工具名）。两种错误用两种异常是正确的分层。

**`names()` 用 `list(self._tools)`（迭代 keys）** 而 `all()` 用 `list(self._tools.values())`。Python 3.7+ 的 dict 保序，所以"注册顺序 = 遍历顺序" —— `tests/test_memory.py:86` 断言 `names() == ["calculator", "get_current_time"]`（依赖这个顺序）。

**`render_prompt` 的空注册表分支返回 `"(no tools available)"`** —— 一个非空的占位文本，让 prompt 模板里的 `{tools}` 不会留空。空字符串会让模型困惑（"工具列表是空的还是没渲染？"）。

**docstring（L1–4）**：`"插件化关键：新增工具 = 写一个 Tool 并 register，核心循环零改动。"` —— 这是**主线 8** 的原始出处。

## 10.3 `app/tools/__init__.py`（43 行）(会话记录)

```python
def build_default_registry(retriever=None, top_k=4, min_score=0.0,
                           indexer=None, rewrite=False, rerank=False) -> ToolRegistry:   # L22
    registry = ToolRegistry()
    registry.register(calculator_tool)
    registry.register(datetime_tool)
    if retriever is not None:
        registry.register(make_search_notes_tool(retriever, top_k=top_k,
            min_score=min_score, rewrite=rewrite, rerank=rerank))
    if indexer is not None:
        registry.register(make_save_note_tool(indexer))
    return registry
```

**"两个总是有、两个可选"** 的结构：

| 工具 | 注册条件 | 依赖 |
|---|---|---|
| `calculator` | 总是 | 无 |
| `get_current_time` | 总是 | 无 |
| `search_notes` | `retriever is not None` | 嵌入 + 向量库 |
| `save_note` | `indexer is not None` | 嵌入 + 向量库（写） |

**`retriever` 与 `indexer` 可以独立开关** —— 允许"只读记忆"（比如 `eval_resume.py` 用无参 `build_default_registry()`，完全没有记忆）或"只写不读"的场景。

`tests/test_memory.py:69` 与 `:86` 正是断言这两个分支；`tests/test_save_note.py:35`/`:46` 同理。

**默认 `top_k=4`/`min_score=0.0`** 与 `config.py` 的 `retrieval_top_k=4`/`retrieval_min_score=0.0` 一致 —— 但**这里是重复的默认值**（`cli.py` 与 `main.py` 都显式传 `settings` 的值）。如果只改 config 而不改这里，`build_default_registry()` 的无参调用行为不变。这是一个小的 DRY 缺口。

## 10.4 内置工具逐个

### `calculator.py`（64 行）(会话记录)

docstring（L1–5）：

```
示例工具：安全的算术表达式求值。
关键：不用内置 eval（可执行任意代码）。改用 ast 解析 + 白名单节点，
只允许数字、四则运算、幂、取模、整除、括号，其余节点一律拒绝。
```

```python
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
           ast.FloorDiv: operator.floordiv}                         # L21–30
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}          # L32–36

def _eval(node: ast.AST) -> int | float:                              # L39
    if isinstance(node, ast.Expression): return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval(node.operand))
    raise ValueError(f"unsupported expression node: {type(node).__name__}")   # L48

def _safe_calculate(expression: str) -> int | float:                  # L51
    tree = ast.parse(expression, mode="eval")                         # L52
    result = _eval(tree)
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return result                                                 # L54
    raise ValueError("expression did not evaluate to a number")
```

**白名单而非黑名单** —— 只处理 `Expression`/`Constant`/`BinOp`/`UnaryOp` 四种节点，其余（`Call`、`Attribute`、`Name`、`Subscript`、`Lambda`、`Compare`…）一律 `ValueError`。这是**正确的安全设计方向**：`eval` 的危险来自"任何表达式"，所以必须穷举允许的而不是穷举禁止的。

**为什么 `mode="eval"`（L52）**：`ast.parse(expr, mode="eval")` 返回 `Expression` 节点且不接受语句（`import os` 会 `SyntaxError`）。这是第一道防线。

**`type(node.op) in _BINOPS` 而不是 `isinstance`** —— 精确匹配，防止未来的 AST 子类绕过。

**`not isinstance(result, bool)`（L54）** —— `True` 是 `int` 的子类，`isinstance(True, int)` 为真。如果不排除，`1 == 1` 之类的比较会返回 `True` 被当作数字 `1`。**由于 `Compare` 节点不在白名单里，这条实际上走不到** —— 但它是"防御性冗余"，成本为零。

`tests/test_registry.py:16` 断言 `pytest.raises(ValueError)` for `"__import__('os').system('ls')"` —— **这条测试是"安全属性"的回归测试，价值很高**。

**注意它返回 `int | float`（不是 str）** —— 这是 `observation: Any` 契约在实践中的第一个例子（`tests/test_react.py:31` 断言的 `observation == 4`）。

### `datetime_tool.py`（25 行）(会话记录)

```python
class NowInput(BaseModel):
    """无参数：LLM 调用时传空对象 {}"""                          # L12–13

def _now() -> str:                                             # L16
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")

datetime_tool = Tool(name="get_current_time",
                     description="Get the current local date and time.",
                     input_model=NowInput, func=_now)          # L20–25
```

**工具名 `get_current_time` 与变量名 `datetime_tool` 不一致** —— 这是一个小的可读性瑕疵（变量名暗示工具名，但实际不是）。`app/tools/builtin/__init__.py` 导出的是 `datetime_tool`，所以外部按变量名导入、按工具名调用。

**`datetime.now(UTC).astimezone()`** 的正确性：先取 UTC 再转本地时区。这比 `datetime.now()` 多了一步，但在"跨时区/夏令时"下更可靠（`now()` 直接用本地时钟）。`.isoformat(timespec="seconds")` 去掉微秒（`2026-01-15T14:30:00+08:00`）—— 对"现在几点"这个问题，微秒是噪声。`tests/test_registry.py:22` 断言输出是 str 且含 `"T"`。

### `search_notes.py`（42 行）(会话记录)

```python
def make_search_notes_tool(retriever, top_k=4, min_score=0.0,
                           rewrite=False, rerank=False) -> Tool:              # L18
    def _search(query: str):
        chunks = retriever.retrieve(query, k=top_k, min_score=min_score,
                                    rewrite=rewrite, rerank=rerank)
        if not chunks:
            return "没有在个人笔记里找到相关内容。"                              # L26
        lines = [f"[{c.doc_id}] (score={c.score:.2f})\n{c.text}" for c in chunks]
        return "\n\n---\n\n".join(lines)                                      # L31
```

**渲染格式 `[doc_id] (score=...)`** 是给模型的两条线索：来源标识（可以在答案里引用）与相似度（可以判断可靠性）。**`\n\n---\n\n` 分隔**让多个片段在文本里视觉分明（模型更容易区分"这是三段").

**Tool description（英文）要求模型引用 `[doc_id]`**：

> "Search your personal notes (RAG memory). Retrieve relevant note fragments by semantic similarity. Use it when the user asks about their own notes, past thoughts, or stored knowledge. When you use a fragment in your answer, cite its [doc_id]."

**"cite its [doc_id]"是提示词层面的可溯源设计** —— 代码里没有强制，靠模型遵从。

**空结果的字符串是中文**（`"没有在个人笔记里找到相关内容。"`），而工具描述是英文。**中英混用**：描述给模型看（英文更通用），结果是给人看的（中文可读）。`tests/test_cli.py:204` 断言这个字符串。

**为什么用工厂函数而不是模块级 Tool**：`retriever` 是运行时依赖（需要 settings/embedder/store），不能在 import 时构造。**工厂函数是这个问题的标准解法**，`make_save_note_tool`/`make_blackboard_*_tool` 同理。

### `save_note.py`（38 行）(会话记录)

docstring（L1–6）明确它和 `search_notes` 构成闭环：

```
save_note：把值得记住的内容写回长期记忆（RAG 记忆的"写"侧）。
与 search_notes（读）配成一对，形成长期记忆的读 + 写闭环：
Agent 在会话中得出重要结论/决策时主动 save_note，
之后（含下一次会话）可用 search_notes 检索到。
写入走 NotesIndexer.index_text（分块 → 嵌入 → 入库）。
```

```python
def _save(content: str, topic: str = "note") -> str:                    # L26
    # 每条记忆用独立 doc_id（含随机后缀），避免与已有记忆的 chunk id 冲突
    doc_id = f"note/{topic}/{uuid.uuid4().hex[:8]}"                     # L29
    n = indexer.index_text(content, doc_id)
    return f"已记住 {n} 段到长期记忆（doc_id={doc_id}）。之后可用 search_notes 检索到这段记忆。"   # L36
```

**`uuid4` 在这里是刻意的随机**（对比 `gateway/identity.py` 里"绝不用随机数"的规则）。区别在于：**幂等键要求"同一逻辑请求同一键"，而每条记忆在语义上就是新的、不可重复的**。用 `uuid4().hex[:8]`（8 位）而不是全 32 位是为了让 doc_id 可读。

**chunk id 的冲突分析**：`indexer.py` 用 `sha1(f"{doc_id}:{i}")` 生成 chunk id。如果两条记忆用同一个 `doc_id`，它们的 chunk 会互相覆盖（`INSERT OR REPLACE`）。`uuid4` 后缀正是为了避免这个 —— **这是一处"不随机就会丢数据"的真实需求**，不是随手加的。

`tests/test_save_note.py:51` 验证完整闭环：save → search 能召回。

### `blackboard.py`（53 行）(会话记录)

docstring（L1–7）的关键对比：

```
注意：黑板是进程内、易失的（与长期记忆 save_note 不同——save_note 入库可跨会话，
黑板只在一次多 Agent 会话内共享）。
```

```python
def make_blackboard_write_tool(blackboard) -> Tool:                    # L27
    def _write(key: str, value: str) -> str:
        blackboard.write(key, value)
        return f"written to blackboard[{key!r}]"                       # L35

def make_blackboard_read_tool(blackboard) -> Tool:                     # L42
    def _read(key: str):
        value = blackboard.read(key)
        return f"blackboard[{key!r}] is empty" if value is None else value   # L51
```

**`{key!r}` 用 repr** —— 让空格/引号在输出里可见（`blackboard['my key']` vs `blackboard[my key]`）。这是调试友好性。

**读缺失键返回字符串而不是错误** —— 因为"没写到"是一个**可能的正常状态**（第一个成员还没写），不是错误。用 `"error:"` 前缀会让 `_execute` 把它当失败、PlanAct 可能触发重规划。

**`blackboard: Any` 的类型标注**（L27）—— 而不是 `Blackboard`。这是为了避免 `app/tools/builtin/` → `app/agent/` 的**模块循环依赖**（`blackboard.py` 在 `app/agent/` 里）。用 `Any` + duck typing 解决。**这与 `retriever.py:75` 那句"延迟导入，避免 memory ↔ tools 的模块加载循环"是同一类问题的两种解法**（一个是延迟 import，一个是弱化类型）。

## 10.5 `app/memory/vector_store.py`（76 行）(会话记录)

docstring（L1–5）：

```
个人笔记量级（数千 chunk）用 SQLite + 暴力检索足够，零外部服务；
接口 add/search 留作换 Milvus/pgvector 只改这一处（与 RunStore 同一思路）
```

```python
@dataclass
class Chunk:                                              # L16
    id: str
    doc_id: str          # 来源笔记文件路径
    text: str
    score: float         # 余弦相似度（search 结果才有意义）

DDL: CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
    text TEXT NOT NULL, embedding TEXT NOT NULL)
```

**`score` 的注释"search 结果才有意义"** —— 因为 `add` 时不算分。这暗示了 `Chunk` 被复用于两个语义（存储实体 vs 检索结果）。**这是一个小的建模瑕疵**：`add(chunk_id, doc_id, text, embedding)` 根本不接受 `Chunk` 对象（L34），所以 `Chunk` 实际上只用于 search 返回。这个 dataclass 的形状是"检索结果"而不是"存储实体"。

```python
def add(self, chunk_id: str, doc_id: str, text: str, embedding: list[float]) -> None:   # L34
    self._conn.execute("INSERT OR REPLACE INTO chunks (id, doc_id, text, embedding) VALUES (?,?,?,?)",
                       (chunk_id, doc_id, text, json.dumps(embedding)))
    self._conn.commit()

def search(self, query_embedding: list[float], k: int = 4) -> list[Chunk]:              # L50
    rows = self._conn.execute("SELECT id, doc_id, text, embedding FROM chunks").fetchall()   # L52
    scored = [(_cosine(query_embedding, json.loads(r["embedding"])), r) for r in rows]   # L54
    scored.sort(key=lambda t: t[0], reverse=True)                                       # L55
    return [Chunk(id=r["id"], doc_id=r["doc_id"], text=r["text"], score=score)
            for score, r in scored[:k]]                                                 # L57
```

**暴力检索的全部代价在这三行（L52–57）**：

1. **全表读取**（无 `LIMIT`，无索引）—— O(N) 行 I/O
2. **每行 `json.loads`** 反序列化 embedding —— O(N×D) 解析开销
3. **Python 层的余弦计算 + 排序** —— O(N×D) 算术 + O(N log N) 排序

**对 1000 个 chunk（768 维）**：约 77 万个 float 的 JSON 解析 + 乘加。这在现代 CPU 上是**几十毫秒**级别（实测 p50 = 88ms，见附录 A）。

**`INSERT OR REPLACE`** 让同一 chunk_id 重写是幂等的 —— 这是 `indexer.py` 能做到"同一 doc_id 重写幂等"的基础。

```python
def _cosine(a: list[float], b: list[float]) -> float:                    # L70
    dot = sum(x * y for x, y in zip(a, b, strict=True))                  # L72
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0                                                        # L74
    return dot / (na * nb)
```

**`zip(..., strict=True)`（L72）是 Python 3.10+ 的特性**，在长度不等时抛 `ValueError` 而不是静默截断。**对向量点积这是关键的正确性保证**：如果查询向量 768 维而存储向量 512 维（换了嵌入模型），静默截断会给出一个**看似合理但错误**的相似度分数，而 `strict=True` 立刻报错。**这是一个"用崩溃换正确性"的正确选择。**

**`na == 0 or nb == 0 → 0.0`**：零向量与任何向量的余弦是未定义的；返回 0（而非 NaN）让排序稳定。**`math.sqrt` 而不是 `numpy.linalg.norm`** —— 零依赖。

## 10.6 `app/memory/embeddings.py`（40 行）(会话记录)

```python
def embed(self, texts: list[str]) -> list[list[float]]:                  # L22
    payload = {"model": self._settings.embedding_model, "input": texts}
    url = f"{self._settings.llm_base_url}/embeddings"
    resp = httpx.post(url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=self._settings.llm_timeout_s, **httpx_env_kwargs(url))    # L30
    resp.raise_for_status()
    try:
        data = resp.json()["data"]                                        # L34
        # 按 index 排序，保证输出顺序与输入一致（部分后端不保证顺序）
        data = sorted(data, key=lambda d: d["index"])                     # L37
        return [d["embedding"] for d in data]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise EmbeddingError(f"embedding failed: {exc}") from exc         # L39
```

**L36–37 的排序是必需的吗？** OpenAI 的 `/embeddings` 规范里 `data` 数组的 `index` 字段存在正是为了**显式表达顺序**，所以"部分后端不保证顺序"是真实情况（尤其并发/批处理实现）。**不排序的后果很隐蔽**：`indexer.py` 用 `zip(chunks, embeddings, strict=True)` 配对，如果顺序错乱，**每个 chunk 会存到另一个 chunk 的向量**，检索结果会完全错乱但不会报任何错。

**`sorted(data, key=lambda d: d["index"])`** 依赖 `index` 键存在。如果后端不给（`KeyError`），会走到 `except` 抛 `EmbeddingError` —— 一个明确的失败优于静默错乱。`tests/test_net.py:109` 用"乱序输入 index 1 先到"验证排序有效。

**异常合并成 `EmbeddingError`** —— 与 `LLMError` 平行。让调用方不必区分"HTTP 失败"与"响应形状不对"。

## 10.7 `app/memory/indexer.py`（81 行）(会话记录)

```python
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")                                 # L13

def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:   # L16
    chunk_size = chunk_size if chunk_size > 0 else 600                    # L22
    overlap = max(0, min(overlap, chunk_size - 1))                        # L24
    chunks: list[str] = []
    current = ""
    for para in _PARAGRAPH_SPLIT.split(text.strip()):
        p = para.strip()
        if not p:
            continue
        if not current:
            current = p
        elif len(current) + 2 + len(p) <= chunk_size:                     # L34
            current = f"{current}\n\n{p}"
        else:
            chunks.append(current)
            current = p
        if len(current) > chunk_size:                                     # L38
            current = _append_hard(chunks, current, chunk_size, overlap)
    if current:
        chunks.append(current)
    return chunks

def _append_hard(chunks: list[str], text: str, chunk_size: int, overlap: int) -> str:   # L43
    while len(text) > chunk_size:
        chunks.append(text[:chunk_size])
        text = text[chunk_size - overlap :] if overlap else text[chunk_size:]
    return text
```

**两级切分策略**：先按**空行**切段落（保留语义边界），再用**贪心合并**到 `chunk_size`；**单段超长时硬切**（L43–48，带 overlap）。

**`min(overlap, chunk_size - 1)`（L24）是必需的夹取**：如果 `overlap >= chunk_size`，硬切的 `text[chunk_size - overlap:]` 会得到**空串**（切片起点 >= 长度），导致 `while` 死循环。`chunk_size - 1` 保证至少前进 1 个字符。

**`len(current) + 2 + len(p)`（L34）的 `+2`** 是 `"\n\n"` 的长度 —— 判断的是**合并后**的长度，而不是两者长度之和。这是精确的。

**overlap 只在硬切时存在**（段落合并产生的 chunk 之间没有 overlap）—— 因为段落边界本身就是语义边界，重复内容无益。**硬切的 overlap（默认 100 字符）** 是为了让被切断的句子在两块里都出现，避免检索时"关键词正好在切口上"。

`index_text`：

```python
def index_text(self, text: str, doc_id: str) -> int:                      # L57
    chunks = chunk_text(text, chunk_size=self._settings.chunk_size,
                        overlap=self._settings.chunk_overlap)             # L62
    if not chunks:
        return 0
    embeddings = self._embedder.embed(chunks)                             # L66  一次请求
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):   # L69
        cid = hashlib.sha1(f"{doc_id}:{i}".encode()).hexdigest()          # L70
        self._store.add(cid, doc_id, chunk, emb)
    return len(chunks)
```

**docstring L60–61 说明了幂等性来源**：

```
doc_id 是来源标识：index_dir 用笔记文件路径，save_note 用 note/{topic}/{uuid}。
chunk id = sha1(f"{doc_id}:{i}")，同一 doc_id 重写幂等（INSERT OR REPLACE）。
```

**`sha1` 在这里完全够用** —— 不是安全用途，只是"确定性、定长、低碰撞"的标识符生成。用 sha256 也可以，但没必要。**关键性质是"同一 (doc_id, i) → 同一 cid"**，这样重新索引一个文件时旧 chunk 被 REPLACE 而不是堆积。

**`embeddings = self._embedder.embed(chunks)`（L66）是一次 HTTP 请求嵌入整篇的所有 chunk** —— 注释明说"单次 HTTP 请求"。这是**批处理优化**：20 个 chunk 一次请求 vs 20 次请求，对本地模型是数量级的差异。

**`zip(..., strict=True)`（L69）** 与 `_cosine` 里同一个理由：如果 embedder 返回的向量数少于 chunk 数（后端截断），`strict` 立刻报错而不是静默丢掉尾部 chunk。**这里的不一致会让"部分笔记没被索引"完全无声。**

`index_dir`：

```python
def index_dir(self, notes_dir: str) -> int:                               # L73
    root = Path(notes_dir)
    if not root.is_dir():
        return 0                                                          # L75
    added = 0
    for md in sorted(root.rglob("*.md")):                                 # L77
        added += self.index_text(md.read_text(encoding="utf-8"), str(md))
    return added
```

**`doc_id = str(md)`（即文件路径）** —— 所以搜索结果的 `[doc_id]` 是一个绝对/相对路径，用户可以据此打开原文件。**这是"引用来源"的具体化。**

**`sorted(root.rglob("*.md"))`** 让索引顺序确定（否则文件系统顺序不定，虽然 chunk id 与顺序无关，但日志/调试更可读）。

**`read_text(encoding="utf-8")` 显式指定** —— 全项目第三次出现这个纪律（另两处是 git log）。

## 10.8 `app/memory/retriever.py`（95 行）(会话记录)

docstring（L12–16 之前）说明两级管线"召回 + 精排"：① 查询改写（检索前 LLM 改写/扩展提升召回）；② LLM 重排（检索后精排提升精度）。

```python
def retrieve(self, query: str, k: int = 4, min_score: float = 0.0,
             rewrite: bool = False, rerank: bool = False) -> list[Chunk]:      # L32
    search_query = self._rewrite_query(query) if (rewrite and self.llm is not None) else query   # L41
    emb = self._embedder.embed([search_query])[0]                            # L42
    fetch_k = k * 2 if (rerank and self.llm is not None) else k              # L43
    chunks = self._store.search(emb, k=fetch_k)                              # L44
    # 阈值过滤：低相似度片段会稀释上下文，直接丢弃（上下文工程）
    chunks = [c for c in chunks if c.score >= min_score]                     # L46
    if rerank and self.llm is not None and len(chunks) > 1:                  # L48
        chunks = self._rerank(query, chunks, k)
    return chunks[:k]
```

**四个开关的组合逻辑**：

| 场景 | `search_query` | `fetch_k` | 后续 |
|---|---|---|---|
| 默认 | `query` 原样 | `k` | 直接 `[:k]` |
| 只 rewrite | LLM 改写后的 | `k` | 直接 `[:k]` |
| 只 rerank | `query` 原样 | `k * 2` | LLM 重排 → `[:k]` |
| 都开 | 改写后的 | `k * 2` | LLM 重排 → `[:k]` |

**`k * 2` 是重排的候选池**（L43）—— 给重排模型比目标更多的候选，让它有选择空间。倍数固定为 2 而非可配置，是简化。

**`self.llm is not None` 三处重复检查**（L41/43/48）—— 因为 `Retriever` 可以在无 LLM 的情况下构造（`build_default_registry(retriever=...)` 的纯检索用法）。**重复检查而不是在入口处提前 return**，是为了让"rewrite=True 但 llm=None"退化成纯向量检索而不是报错。这是降级设计。

**阈值过滤在重排之前（L46）** —— 顺序重要：先按相似度砍掉低分片段，再让 LLM 在剩下的里排序。如果反过来（先重排再过滤），阈值就应该作用在重排前的分数上，而重排后的顺序已经没有分数了。**当前顺序是对的**：`min_score` 是"余弦相似度阈值"，它必须作用在余弦相似度上。

**L45 的注释"上下文工程"** 说明过滤的动机：低相似度片段会**稀释上下文**（占用 token 却不提供信息，还可能误导模型）。默认 `min_score=0.0`（不过滤）是保守的 —— 因为余弦相似度的绝对值没有跨模型的可比性，一个安全的阈值需要按嵌入模型调。

```python
def _rewrite_query(self, query: str) -> str:                             # L52
    try:
        return self.llm.chat([{"role": "system", "content": _QUERY_REWRITE_PROMPT},
                              {"role": "user", "content": query}]).strip()
    except Exception:
        return query                                                      # L63  异常原样返回

def _rerank(self, query: str, chunks: list[Chunk], k: int) -> list[Chunk]:   # L65
    options = "\n\n".join(f"[{i}] {c.text}" for i, c in enumerate(chunks, start=1))   # L67
    ...
    # 延迟导入，避免 memory ↔ tools 的模块加载循环
    from ..agent.base import extract_json                                 # L76
    ...
    if not isinstance(data, dict):
        return chunks[:k]                                                 # L79
    order = data.get("ranked") or []                                      # L83
    ranked = []
    seen = set()
    for raw_idx in order:
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(chunks) and idx not in seen:                   # L88
            seen.add(idx)
            ranked.append(chunks[idx - 1])
    # 补齐模型未提及的候选（保持原相似度顺序），再截断到 k
    for c in chunks:
        if c not in ranked:                                               # L92
            ranked.append(c)
    return ranked[:k]
```

**L76 的延迟导入** 避免 `app/memory/retriever.py` → `app/agent/base.py` 的模块级导入。为什么会有循环？因为 `app/tools/builtin/search_notes.py` 导入 `Retriever`，而 `app/agent/base.py` 导入 `app/harness`…… 实际的循环路径是 `app.tools` → `app.memory` → `app.agent` → `app.tools`（`base.py` 里 `from .tools.registry import ToolNotFoundError` 之类）。**延迟导入把 import 时机推到函数调用时，此时所有模块都已加载完毕。**

**`1 <= idx <= len(chunks)`（L88）双重校验**：越界忽略 + 去重（`idx not in seen`）。**防御模型返回 `[3, 1, 3]` 或 `[99]` 这类畸形输出。**

**L91–94 的补齐是关键的正确性保证**：如果模型只排了 3 个候选中的 2 个（或一个都没排），剩下的按原相似度顺序补上。**没有这一步，`ranked[:k]` 可能返回少于 k 个结果甚至空列表** —— 一次 LLM 输出的格式问题会让检索"失效"。`注释"补齐模型未提及的候选（保持原相似度顺序）"` 明说了这个设计。

**`if c not in ranked`（L92）用 `Chunk` 的相等性** —— 因为 `Chunk` 是 `@dataclass`（默认 `eq=True`），按字段比较。如果两个 chunk 的所有字段都相同（不同 doc_id 但内容与分数一致的可能性很低），会被误判为重复。**实践中安全，但严格说应该比较 `id`。**

`tests/test_memory.py:130` 用 LLM 返回 `'{"ranked": [3, 1, 2]}'` 断言 `retrieve(k=2, rerank=True)` 得到 `["c", "a"]`。

## 10.9 `app/memory/__init__.py`（16 行）

导出 `Chunk`/`EmbeddingClient`/`EmbeddingError`/`NotesIndexer`/`Retriever`/`VectorStore`/`chunk_text`。**注意 `chunk_text` 被导出**（它是模块级函数而非类方法）—— 这暗示它被视为**可复用的公共 API**（测试直接导入它：`tests/test_memory.py:3`）。

---

# 第 11 章 网关协同：会话身份、幂等、能力发现

## 11.1 定位：`app/gateway/__init__.py`（44 行）(会话记录)

docstring（L1–8）是整个 M6 的设计声明：

```
Warden 与 InferGate 之间只差三个请求头和一个 GET：
这里集中生成它们，LLM 客户端（app/llm.py）是唯一使用者 —— 不新增第二条 HTTP 通路。
网关不认识这些头时（裸 OpenAI / 旧版 InferGate / Ollama）一切都照旧：
未知请求头会被忽略，能力探测失败返回 None，调用方退化为原有行为。
```

**"只差三个请求头和一个 GET"** 精确概括了 M6 的全部内容：

| 三条头发送 | 一个 GET 探测 |
|---|---|
| `X-InferGate-Session` | `GET {base_url}/v1/capabilities` |
| `X-InferGate-Tenant` | |
| `Idempotency-Key` | |

外加四条**响应头**解析（`X-InferGate-Idempotent-Replay` / `-Origin` / `-Age` / `-Upstream`、`X-InferGate-Upstream-Name`）。

**"LLM 客户端是唯一使用者 —— 不新增第二条 HTTP 通路"** 是一个重要的架构约束：M6 没有引入第二个 HTTP 客户端或第二套重试逻辑，而是**在既有的 `_post` 上加头、加语义**。这让"网关"成为 LLM 客户端的**一个模式**而不是**一个层**。

`__all__` 17 项（L27–44）。**`canonical_body` 不在其中** —— 要用必须 `from app.gateway.identity import canonical_body`。这是"导出策略（`derive_*`）而非内部工具"的副产品，但 `canonical_body` 恰是调试幂等问题时最需要单独调用的函数（第 17 章）。

## 11.2 `app/gateway/identity.py`（131 行）(验证)

> 本章内容已在**第 3 讲**完整交付。此处保留全部要点。

### 要解决的真实死结（docstring L3–5）

```
Warden 原本没有'会话'这个概念：唯一稳定身份是 trace_id（= run id），
而它是每次 run 现生成的 uuid4，天生'每次都不一样'，
拿它当会话 id 只会把一次对话拆成 N 个会话，
账本（GET /admin/sessions）也就统计不出'这次对话花了多少'。
```

**机制**：多轮对话在 Warden 里**不是一次 run** —— 每轮都是一次独立的 `agent.run()`，每次 `_begin()` 都会 `trace_id or new_trace_id()`（`base.py:181`）。所以第 1 轮是 `a1b2...`、第 2 轮是 `f9e8...`。

**网关侧**：`GET /admin/sessions/{id}` 按 id 聚合。拿 trace_id 当会话 id 的结果是 3 轮对话 = 3 条各含 1 次请求的"会话"。**"这次对话花了多少"在数据模型层面就无解 —— 不是查询写不出来，而是聚合键的粒度就是错的。**

### 为什么必须是内容哈希而不是存表

存表会引入：一张新表、一次写、一个"谁负责首轮创建 session"的时序问题、以及**进程崩溃时表里可能没有这条 session 而对话已经在进行**的窗口。

**内容哈希对崩溃天然免疫，因为它不存任何东西。** 这是 M2"崩溃后 resume 能接着跑"哲学在 M6 的延续。

### 常量（L30–42）(验证)

```python
# 与 InferGate internal/gateway/headers.go 中的拼写保持一致
HEADER_SESSION = "X-InferGate-Session"                # L31
HEADER_TENANT = "X-InferGate-Tenant"
HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"            # L33
HEADER_REPLAY = "X-InferGate-Idempotent-Replay"
HEADER_REPLAY_ORIGIN = "X-InferGate-Idempotent-Origin"
HEADER_REPLAY_AGE = "X-InferGate-Idempotent-Age"
HEADER_REPLAY_UPSTREAM = "X-InferGate-Idempotent-Upstream"
HEADER_UPSTREAM_NAME = "X-InferGate-Upstream-Name"
_ID_PREFIX = "warden-"                                # L41
_FIELD_SEP = "\x1f"                                   # L42
```

**L30 的注释标记了一个跨仓库耦合点。** 头名拼错不会报错、只会静默退化成"对面不认识这个头" —— 而 M6 的整个兼容性设计恰恰是"不认识就忽略"。**这意味着头名拼错时的失败模式是静默的**，所以必须有注释把耦合点钉住。

**`"Idempotency-Key"` 不带 `X-InferGate-` 前缀** —— 它是 OpenAI/Stripe SDK 的既有拼写。改成 `X-InferGate-Idempotency-Key` 会让任何走标准 SDK 的中介失效。

### `_FIELD_SEP = "\x1f"` 的歧义分析（L42）

**歧义是怎么产生的**：`_digest` 把多段字符串哈希在一起。不加分隔符时 `["ab", "c"]` 与 `["a", "bc"]` 产生相同的字节流 `abc` → **同一个哈希**。在幂等键里这会造成真实的键碰撞 → 网关把 A 请求的答案回放给 B 请求。

**为什么 `\x1f` 几乎不可能出现在正文里**：JSON 规范要求控制字符 `U+0000`–`U+001F` **必须**转义（写成 `\u001f`）。而 `_digest` 拿到的是**已解析成 Python str 的内容**（`_first_user_content` 返回 `content.strip()`），不是原始 JSON 文本。所以：

- 若用户真的想发一个 `\x1f`，它在 JSON 里是 `"\u001f"`、解析后是单个 U+001F，**确实会与分隔符冲突** —— 但这需要刻意构造
- 而 `\n`、`|`、`:` 等"看起来像分隔符"的字符在日常文本里到处都是

**结论**：用"极端情况下理论上的碰撞"换"日常情况下不可能碰撞"。正确但不绝对安全。

**顺带一个精度观察**：`derive_idempotency_key` 调用 `_digest([session_id, str(turn_index), canonical_body(payload)], 40)`（L94），其中 `session_id` 恒以 `"warden-"` 开头因而非空、`str(turn_index)` 恒非空 —— **第二个分隔符逻辑上冗余**。`_digest` 无条件给每段追加分隔符，是"不因调用点而改变行为"的一致选择，代价是几个字节。

### `_digest`（L45–51）(验证)

```python
def _digest(parts: Iterable[str], length: int) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(_FIELD_SEP.encode("utf-8"))
    return h.hexdigest()[:length]
```

1. **流式更新而非 `"".join(parts)`** —— 效果等价，但让"每段之后必然有分隔符"**在循环结构里成立**，包括最后一段。`join` 会让最后一段后面没有分隔符，使函数行为依赖参数个数。
2. **`encode("utf-8")` 显式指定** —— 默认就是 utf-8，但**显式写出来使函数不受 `-X utf8` / `PYTHONUTF8` 影响**，保证"同一份对话在不同机器/进程上派生出同一个 id"。这是全项目反复出现的纪律。
3. **`[:length]` 截断** —— docstring 理由："长度截断到 32/40 hex，避免把超长头塞给网关"。128 bit（32 hex）碰撞概率对个人项目绰绰有余。**这个选择让 id 变成定长**，从而可预测、可断言。
4. **没有 `try`** —— `parts` 里出现非字符串会抛 `AttributeError`。调用点全是字符串，所以这是"靠调用点保证"的内聚小函数。**与 `_post` 的防御姿态形成对比，而对比是对的**：`_post` 面对网络（不可信），`_digest` 面对自己的代码（可信）。

### `_first_user_content` 三层过滤（L54–64）(验证)

```python
def _first_user_content(messages: Iterable[dict]) -> str:
    for msg in messages or []:
        if not isinstance(msg, dict):        # L57-58
            continue
        if msg.get("role") != "user":        # L59-60
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():   # L61-63
            return content.strip()
    return ""
```

- **L57 非 dict**：`messages` 是 `Any`（调用来源包括 `payload.get("messages")` 的原始 JSON 反序列化结果）。畸形输入（`["hello"]`）在这里被吃掉而不是抛 `AttributeError`。
- **L59 非 user**：**实测验证过"在首条 user 消息前插一条 system，会话 id 不变"**。实际意义是"优化系统提示词不会把老会话拆成新会话"。
- **L62 三层过滤**：同时挡住"不是字符串"（多模态消息的 content 是 list）、空串、纯空白。"有内容"**必须用 strip 后的结果**。
- **`return content.strip()` 双重 strip**（判断一次、返回一次）是故意的：避免"判断用 strip、返回值没 strip"导致同一段文字因首尾空白不同而派生不同 id。**这是一个很容易写错的地方。**

### `derive_session_id`（L67–77）(验证)

```python
def derive_session_id(messages) -> str | None:
    opener = _first_user_content(messages)
    if not opener:
        return None
    return _ID_PREFIX + _digest([opener], 32)
```

**docstring L70–72 是整个文件最重要的一句警告**：

> 调用方若已把上下文窗口裁剪过（BaseAgent._history_with_summary 会丢掉最旧消息、甚至把开头换成摘要），应传**裁剪前**的完整历史，否则会话 id 会随窗口滑动而漂移。

**漂移为什么致命**：考虑一次超过 30 条的长对话。`_history_with_summary` 把最旧的 N 条换成一个 `role:"user"` 的摘要块放在数组首位。此时派生会话 id，`_first_user_content` 会取到**摘要文本** → 同一场对话在第 30 轮之后突然获得**新的会话 id** → 账本里这场对话被劈成两段，**断裂点取决于窗口大小这个跟语义无关的参数**。

**这就是 `BaseAgent.run()` 把派生放在 `_history_with_summary` 之前的原因，也正是缺口 ① 的落点**（`resume()` 用的是已裁剪的 `record.input`）。

**`return None` 而不是空串**：`gateway_headers` 用 `if not session_id:` 判断，两者都能接住；但 `None` 更诚实 —— 它表达"这个概念在这里不存在"，而空串是"存在但是空的"。`resolve_session_id` 返回 `str | None` 与之一致。

### `canonical_body`（L80–82）(验证)

```python
def canonical_body(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
```

| 参数 | 没有它会怎样 |
|---|---|
| `sort_keys=True` | 同一份 payload 因 dict 插入顺序不同而哈希不同。**实测验证：payload 键序打乱后键不变** |
| `ensure_ascii=False` | 中文被转义成 `\uXXXX`。虽然同样确定性，但让哈希**依赖 json 的转义策略实现**，且调试可读性归零 |
| `separators=(",", ":")` | 默认是 `", "` / `": "`（带空格）。虽然同样确定性，但**换 Python 版本或 json 实现就可能变** |

**为什么 `sort_keys=True` 能成立**：幂等键只需要"同一逻辑请求 → 同一键"，**不需要**"不同顺序 = 不同请求"。两个键序不同的 payload 语义上就是同一个请求，映射到同一个键是**正确的**，不是碰撞。

**副作用**：输出含原始中文字符 → 进 `_digest` → `.encode("utf-8")`。所以**"中文 payload 的幂等键"依赖 UTF-8 编码**。同一文件里 `_digest` 已显式写 `utf-8`，链条闭合。

### `derive_idempotency_key`（L85–94）(验证)

```python
def derive_idempotency_key(*, session_id: str, messages: list[dict], payload: dict) -> str:
    turn_index = len(messages or [])
    return _ID_PREFIX + _digest([session_id, str(turn_index), canonical_body(payload)], 40)
```

**docstring L88–89 坦白了近似性**：

> 轮次序号用历史消息条数近似：一轮对话里每次追加消息都会让它变大，所以新一轮必然是新键；而'同一轮的失败重试'重放的还是同一份 messages + payload，序号和哈希都不变 → 同键

**为什么"够用"**：这个序号的作用**不是计数**（不需要知道"这是第 3 轮"），而只是**区分**。"消息条数"满足区分性：任何新一轮都至少追加一条消息 → 条数必然增长。真正承担区分重任的是**第三项 `canonical_body(payload)`**（payload 含 messages 全文）。

**两项是冗余的双保险** —— 冗余正是幂等键需要的：**宁可不命中（多花一次上游调用），不可误命中（回放错误答案）。**

**一个真实的风险点**：`turn_index` 来自**实际发送的 messages**，而 `derive_session_id` 走的是**作用域/完整历史**。短对话（≤30 条）时两者一致；长对话时 `messages` 被压成 `[摘要块, *末30条]`，`len()` 可能是 31 而非真实的 45。**这依然安全**（同一份被压缩的输入重试时同样被压缩），但**"第 N 轮"这个概念在压缩前后不连续**。

**极端情况下的真实缺陷**：如果**同一逻辑轮**在压缩前失败一次、压缩后重试一次，两次 messages 不同 → 不同的键 → 网关不回放 → **上游被调用两次（重复计费）**。这需要"刚好跨过窗口边界 + 恰好失败"两个条件同时成立。

**根因与缺口 ① 相同**：`derive_idempotency_key` 拿"实际发送的 messages"而不是"逻辑历史"。**M6 的三处身份派生中，一处（`run()`）正确使用了裁剪前视图，两处（`resume()`、`derive_idempotency_key`）没有 —— 同一个根因，是 M6 目前唯一成体系的设计缺口。**

### ContextVar 作用域（L97–116）(验证)

```python
_session_var: ContextVar[str | None] = ContextVar("warden_gateway_session", default=None)   # L99-101

def current_session_id() -> str | None:                                          # L104
    return _session_var.get()

@contextlib.contextmanager
def gateway_session_scope(session_id: str | None):                               # L109
    token = _session_var.set(session_id or None)                                 # L112
    try:
        yield session_id
    finally:
        _session_var.reset(token)                                                # L116
```

L97–98 的注释解释了它存在的理由：

> 会话 id 的隐式作用域：BaseAgent 在整轮 run() 里按完整历史设置一次，于是这一轮里的每一次 LLM 调用（含摘要压缩、自评）都归属同一个会话。

**"含摘要压缩、自评"这几个字是这个机制的全部价值。** 回到第 6 章：`_history_with_summary` 会发一次**纯内部**的 LLM 调用，`_maybe_self_eval` 也会发一次。这两次调用**不由用户触发、用户也看不到，但它们真的花钱**。

- **没有作用域**：这两次调用会从自己的 messages 派生 session。摘要调用的 messages 是被裁剪掉的旧消息（role 全是 user/assistant），所以**会**派生出 session —— 派生自"这场对话最早的那句话"。**巧合之下答案是对的，但这是运气，不是设计。**
- **有作用域**：`resolve_session_id` 的第二优先级直接命中，**完全不看 messages** → "这一轮里所有 LLM 调用属于同一会话"成为**结构性保证**。

**`contextvars` 而非 `threading.local`**：`contextvars` 在 asyncio 任务间自动隔离、在 `asyncio.to_thread`/线程池里按 context 复制。Warden 的调度线程（APScheduler）与 FastAPI 请求处理是不同执行上下文，用 ContextVar 让"作用域只在 with 块内有效"在两种模型下都成立。**与 `harness/trace.py` 的 `_trace_var` 是同一套机制、同一个理由。**

**`reset(token)` 而非 `set(None)`（L116）**：这是可嵌套的关键。`run()` 里设了作用域，如果 `_history_with_summary` 内部（比如未来重构）也设一个，`set(None)` 会把外层作用域一起清掉，`reset(token)` 精确还原。

**`session_id or None`（L112）** 把空串规整成 `None` —— 所以 `gateway_session_scope("")` 等价于 `gateway_session_scope(None)`，都会让 `current_session_id()` 返回 `None`、落到第三优先级去派生。**这是配置留空时的正确行为。**

### `resolve_session_id` 三优先级决策表（L119–131）(验证)

```python
def resolve_session_id(messages, *, pinned: str = "") -> str | None:
    pinned = (pinned or "").strip()
    if pinned:
        return pinned                    # 优先级 1
    scoped = current_session_id()
    if scoped:
        return scoped                    # 优先级 2
    return derive_session_id(messages)   # 优先级 3（可能 None）
```

| 优先级 | 来源 | 什么时候用它 | 做什么 |
|---|---|---|---|
| 1 | `WARDEN_GATEWAY_SESSION` 配置 | 压测、手工对账、端到端验证 | 把流量**强制固定到一个会话** |
| 2 | `gateway_session_scope` 的 ContextVar | Agent run 期间（正常路径） | 让一轮内所有调用归属同一会话 |
| 3 | `derive_session_id(messages)` 现推 | 作用域外（脚本、评测、摘要） | 兜底 |

**优先级 1 高于 2 是有意的**：`tests/test_gateway_live.py` 的 `_client()` 显式设 `gateway_session=""`（空）来走派生规则；压测场景需要"所有请求都算同一会话" —— 必须能覆盖 Agent 自己设的作用域。

**`pinned.strip()` 与 `derive_session_id` 的 `return None` 形成闭环**：配置写纯空白会被 strip 成空串 → 落到优先级 2/3。避免"用一串空格当会话 id"把账本搞脏。

### 完整链路：从"没有身份"到"只能试一次"

```
纯 system 的内部调用
  → _first_user_content 返回 ""
  → derive_session_id 返回 None
  → resolve_session_id 返回 None
  → gateway_headers 只带 tenant 就 return（llm.py:220）
  → _post 的 key 为空 → max_attempts = 1（llm.py:245，结构上不可能重试）
```

**一条"没有身份"的请求，最终在状态机里被钳成"只能试一次"。这不是四个独立的判断，是一个判断被四层实现。**

### 实测验证（附录 A 原始数据）

| 断言 | 源码位置 | 实测证据 |
|---|---|---|
| 会话 id = `"warden-"` + 32 hex = 39 字符 | L77 | `warden-cbe30936f8017c0f036be13d65790b88`（39 字符）|
| 幂等键 = `"warden-"` + 40 hex = 47 字符 | L94 | 实测 `len == 47` |
| **live 调用的会话 id 可逐字节重算** | L74–77 | `warden-ca2dd12ee73ad7c7a765f49fc1947972`，从首条 user 消息 `'Reply with exactly: WARDEN-OK'` 重算命中 |
| **live 调用的幂等键可逐字节重算** | L93–94 | `warden-ed03199b6848caea4413c6c63c190b9cd706b797` 命中 |
| 追加消息不改会话 id | L59（只认 user） | 追加 2 条后续消息后 id 不变 |
| 前置 system 不改会话 id | L59 | 前置 system 后 id 不变 |
| 键序不敏感 | L82（`sort_keys`） | payload 键序打乱后键不变 |
| 换一轮 → 新键 | L93（`turn_index` 变） | 第 2 轮键不同 |
| 无 user 消息 → `None` | L75–76 | 空列表 / 纯 system → `None` |
| 分隔符确实在生效 | L50 | `canonical_body({'b':2,'a':1})` == `'{"a":1,"b":2}'` |

**"逐字节重算命中"是最强的证据**：同时证明①函数是纯的、②`_FIELD_SEP`/`sort_keys`/`ensure_ascii=False`/utf-8 这条编码链在真实调用路径上按预期执行、③`gateway_headers` 传给 `LLMClient` 的 payload 与外部重建的 payload 是同一份。**任何一环有隐藏的非确定性，两次重算不可能同时命中。**

## 11.3 `app/gateway/capabilities.py`（167 行）(会话记录)

docstring（L1–12）要点：

- 动机：**"上下文窗口只有网关知道（InferGate 配置里 `models.<name>.context_window`）。在这之前 Warden 只能靠 `WARDEN_CONTEXT_MAX_MESSAGES` 猜一个'保留多少条消息'，完全不知道模型真实能吃多少 token"**
- **"特意做成'探不到就返回 None'：裸 OpenAI、旧版网关、网络故障、500、非 JSON 响应全部退化为 None，绝不因为一次探测失败打断对话"**
- **"失败结果同样进缓存，否则对着一个不提供该端点的网关，每次 LLM 调用都要多花一次无用的 HTTP 请求"**

**"失败也缓存"是一个容易被忽略但价值很高的决定**：裸 OpenAI 端点**永远**不会提供 `/capabilities`，所以每次 `_post` 都探测一次意味着**每次对话都多一次注定 404 的 HTTP 请求**。缓存失败把这个成本从"每次"降到"每 TTL 一次"。`tests/test_gateway.py:382` 专门断言两次查询只发一次请求。

```python
CAPABILITIES_PATH = "capabilities"                                        # L27

def capabilities_url(base_url: str) -> str:                               # L30
    base = (base_url or "").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/{CAPABILITIES_PATH}"
```

**"不以 `/v1` 结尾则补 `/v1`"** 覆盖了三种输入形态（`tests/test_gateway.py:336` 逐一断言）：

| 输入 | 输出 |
|---|---|
| `http://gw:1/v1` | `http://gw:1/v1/capabilities` |
| `http://gw:1/` | `http://gw:1/v1/capabilities` |
| `http://gw.openai.com/v1/` | `http://gw.openai.com/v1/capabilities` |

**注意 `base_url` 在 Warden 里通常已经带 `/v1`**（`config.py:27` 默认 `http://localhost:11434/v1`），所以补 `/v1` 是给"用户配置成裸主机"的容错。

```python
@dataclass(frozen=True)
class ModelCapability:                                                    # L42
    model: str
    context_window: int = 0
    max_output_tokens: int = 0
    capabilities: tuple[str, ...] = ()
    available: bool = True

    @classmethod
    def from_json(cls, data: dict) -> ModelCapability:                    # L52
        """...context_window / max_output_tokens 在 InferGate 里带 omitempty，
        值为 0 时字段直接不出现，所以必须用 get 而不是下标"""
        caps = data.get("capabilities")
        return cls(
            model=str(data.get("model") or ""),
            context_window=int(data.get("context_window") or 0),           # L62
            max_output_tokens=int(data.get("max_output_tokens") or 0),
            capabilities=tuple(str(c) for c in caps) if isinstance(caps, list) else (),
            available=bool(data.get("available", True)))
```

**`omitempty` 是 Go 的 JSON tag 选项**，值为零值时字段被省略。这是**跨语言接口的一个真实陷阱**：Python 的 pydantic 默认总是输出字段，Go 的 `omitempty` 不输出。**`from_json` 的 docstring 专门提醒这一点**，实现上全部用 `.get(...) or 默认`。

**`available=bool(data.get("available", True))`** —— 注意默认是 `True`（不是 `False`）。语义是"字段缺失时假定可用"，因为**已经成功取到 JSON 了**，说明端点在响应。

**`capabilities=tuple(...)`** 用 tuple 而非 list，因为 dataclass 是 `frozen=True`（list 不可哈希，tuple 可以）。这让 `ModelCapability` 可哈希、可放进 set。

```python
@dataclass
class _Entry:                                                             # L69
    report: dict[str, Any] | None
    fetched_at: float

class CapabilityCache:
    def __init__(self, ttl_s: float = 60.0) -> None:                      # L77
        self._ttl_s = ttl_s
        self._entries: dict[str, _Entry] = {}

    def report(self, base_url, *, api_key="", timeout=10.0):              # L87
        url = capabilities_url(base_url)
        now = time.monotonic()
        cached = self._entries.get(url)
        if cached is not None and self._ttl_s > 0 and now - cached.fetched_at < self._ttl_s:
            return cached.report                                          # L91
        report = self._fetch(url, api_key=api_key, timeout=timeout)
        self._entries[url] = _Entry(report=report, fetched_at=now)        # L95  失败也缓存
        return report
```

**`time.monotonic()` 而不是 `time.time()`** —— 单调时钟不受系统时间调整（NTP 校时、手动改时间）影响。对 TTL 计算这是必需的：用 `time.time()` 时一次时钟回拨会让缓存**永久有效**（`now - fetched_at` 变负，永远小于 TTL）。

**`self._ttl_s > 0` 与 `ttl_s <= 0 表示不缓存`** 的哨兵约定（与 `config.py` 的 `0` 哨兵风格一致）。但注意**这里的 `0` 语义是"不缓存（每次探测）"，而 `config.py` 的 `context_max_messages=0` 是"关闭功能"** —— 同一项目里 `0` 有两种含义。`tests/test_gateway.py:392` 用 `ttl_s=0` 断言两次探测。

**缓存键是完整 URL**（`self._entries[url]`）—— 所以不同 `base_url` 的探测互不干扰。而全局单例 `get_capability_cache()` 让这个缓存**跨 LLMClient 实例共享**。

```python
    def _fetch(self, url, *, api_key, timeout) -> dict | None:            # L134
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout,
                             **httpx_env_kwargs(url))                     # L139
        except httpx.HTTPError as exc:  # 网络层错误：不是网关，退化为"不知道"
            logger.warning("能力探测失败（%s）：%s", url, exc)
            return None
        if resp.status_code != 200:
            # 裸 OpenAI / 旧网关最常见的情况，一条 info 足够，不要刷 warning
            logger.info("能力端点不可用（%s 返回 HTTP %d），按未知处理", url, resp.status_code)
            return None
        try:
            data = resp.json()
        except Exception as exc:  # 非 JSON（HTML 错误页、代理欢迎页等）
            logger.warning("能力端点返回的不是 JSON（%s）：%s", url, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("能力端点返回的形状不认识（%s）：%r", url, type(data).__name__)
            return None
        return data
```

**四类失败用了三种日志级别，这个分级是刻意的**：

| 失败 | 级别 | 理由 |
|---|---|---|
| 网络错误 | `warning` | 可能是配置错误（地址写错），值得注意 |
| 非 200 | **`info`** | **裸 OpenAI/旧网关最常见，是预期行为**，不应刷 warning 日志 |
| 非 JSON | `warning` | HTML 错误页/代理欢迎页 → 地址可能指向了错误的服务 |
| 形状不对 | `warning` | 端点存在但返回了意外结构 |

**注释"一条 info 足够，不要刷 warning"体现了日志级别的正确使用**：一个每分钟发生多次的预期事件不该用 warning（会淹没真实警告），也不该用 debug（完全看不见）。**这是"日志级别是给运维的信号，不是给开发者的调试输出"这一原则的实践。**

**`if api_key else {}`（L135）** —— 没配 key 时**不发 Authorization 头**（而不是发 `Bearer `）。有些网关对空 Bearer 会返回 401，而不发头则按匿名处理。

```python
_cache: CapabilityCache | None = None                                     # L156

def get_capability_cache(ttl_s: float | None = None) -> CapabilityCache:  # L159
    """进程内单例：能力报告每个进程只按 TTL 探测一次。
    传 ttl_s 只在第一次调用时生效（后续调用复用已建好的缓存）"""
    global _cache
    if _cache is None:
        _cache = CapabilityCache(ttl_s if ttl_s is not None else 60.0)
    return _cache
```

**"传 ttl_s 只在第一次调用时生效"是一个反直觉的 API**（docstring 明说了）。后果：`LLMClient.__init__` 里传 `settings.gateway_capabilities_ttl_s`（`llm.py:142–144`），但**如果这个 Client 不是第一个创建的，它的 TTL 设置会被忽略**。因为 `main.py` 在 import 时创建唯一的 `LLMClient`，实践上没问题；但 `tests/` 里创建多个 Client 时 TTL 由第一个决定 —— 这是测试隔离的一个隐患（`tests/test_gateway.py` 里凡是测 TTL 的都显式 `CapabilityCache(ttl_s=...)` 注入，见 `llm.py:134` 的 `capabilities` 参数）。

**`LLMClient.__init__` 的 `capabilities: CapabilityCache | None = None` 参数就是为了让测试能注入自己的缓存**（`llm.py:141` 注释"默认走全局单例，测试可注入自己的"）。**这是"单例 + 可注入"的标准折中。**

---

# 第 12 章 MCP 客户端

## 12.1 `app/mcp/client.py`（112 行）(会话记录)

docstring（L1–5）：

```
MCP（Model Context Protocol）最小客户端：stdio 传输 + JSON-RPC 2.0。
零第三方依赖：直接 subprocess 拉起 MCP server，按行收发 JSON-RPC。
规范参考：https://modelcontextprotocol.io/specification/2024-11-05/
```

**"最小客户端"是准确的自我描述**：只实现了 `initialize` / `notifications/initialized` / `tools/list` / `tools/call` 四个方法，没有 resources、prompts、sampling、roots、notifications 的订阅。**对 Warden 的需求（把外部工具挂进注册表）恰好够用。**

```python
@dataclass
class MCPTool:                                                            # L14
    name: str
    description: str
    input_schema: dict

class MCPError(Exception):                                                # L21
    """MCP 协议 / 进程 / 调用错误。"""

class MCPClient:
    def __init__(self, command: list[str]) -> None:                       # L26
        self._proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)   # L28–35
        self._id = 0
        self.initialize()                                                 # L37  构造即初始化
```

**`Popen` 六个参数各有理由**：

| 参数 | 作用 / 不设的后果 |
|---|---|
| `stdin/stdout=PIPE` | 通信通道 |
| **`stderr=DEVNULL`** | 不让 MCP server 的日志混进 `stdout` 的 JSON-RPC 流（**关键**：任何一行非 JSON 输出都会让 `json.loads` 失败）|
| `text=True` + `encoding="utf-8"` | 文本模式 + 显式编码（Windows 默认代码页会毁掉中文）|
| **`bufsize=1`** | 行缓冲 —— 让 `readline()` 能及时拿到完整的一行 |

**`stderr=DEVNULL` 是这里最重要的一个选择**：MCP server 通常会在 stderr 打日志（很多 SDK 默认如此）。如果 `stderr=PIPE` 而不读，pipe 缓冲区填满会让子进程**阻塞**（死锁）。如果 `stderr` 混到 stdout，JSON-RPC 解析会崩。`DEVNULL` 同时避免这两个问题，代价是**丢掉了 server 的日志**（第 17 章的一个可诊断性缺口）。

**"构造即初始化"（L37）** 让 `MCPClient(...)` 这个表达式就是一个"可用的客户端或抛异常"。好处：调用方不需要记住调 `initialize()`。代价：**构造函数有网络/进程副作用**（一个常见的反模式，但在这里换来了 API 简洁）。

```python
    def initialize(self) -> None:                                          # L39
        self._request("initialize", {                                          # L40
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "warden", "version": "0.4.0"}})              # L42
        # 初始化完成通知（notification：无 id，不期待响应）
        self._notify("notifications/initialized", {})                           # L48
```

**`clientInfo.version = "0.4.0"` 是硬编码字符串** —— 而 `app/__init__.py` 是 `0.1.0`、FastAPI 是 `0.5.0`、pyproject 是 `0.2.0`。**第四个版本号**，且它是唯一一个"发给外部服务"的版本号（第 17 章）。

**`initialize` 后必须发 `notifications/initialized`** —— 这是 MCP 规范的要求（两阶段握手）。实现上用 `_notify`（无 id、不读响应），符合 JSON-RPC 通知的语义。

```python
    def _request(self, method: str, params: dict) -> dict:                 # L51
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()                                           # L57
        while True:
            line = self._proc.stdout.readline()                            # L59
            if not line:
                raise MCPError(f"MCP server closed stdout while awaiting {method}")   # L61
            data = json.loads(line)
            # 跳过非本请求的响应/通知，直到等到匹配 id 的响应
            if data.get("id") == self._id:                                 # L64
                if "error" in data:
                    raise MCPError(f"{method} failed: {data['error']}")    # L66
                return data.get("result", {})                              # L67
```

**`while True` + `if data.get("id") == self._id` 的循环是必需的**：MCP server 可能主动发通知（server-initiated notifications，比如日志消息），或者因为并发而返回别的请求的响应。**这个循环让客户端只关心"我要的那条"**。

**`if not line` 检测 EOF**（`readline()` 在流关闭时返回空串）→ 明确的 `MCPError`。**没有这个检查，`json.loads("")` 会抛 `JSONDecodeError`，错误信息完全无法定位问题**（"期望值"这种消息看不出是进程死了）。**这是一个很好的错误信息设计例子。**

**注意 `_request` 不是线程安全的**（`self._id` 自增与读写无锁）。当前调用方都是单线程的（`cli.py` 的 `mcp-ls`、`build_mcp_registry`），所以安全。**这是一个未文档化的约束**（第 17 章）。

```python
    def _notify(self, method: str, params: dict) -> None:                  # L67
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        self._proc.stdin.flush()

    def list_tools(self) -> list[MCPTool]:                                 # L72
        result = self._request("tools/list", {})
        return [MCPTool(name=t["name"], description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {}))
                for t in result.get("tools", [])]

    def call_tool(self, name: str, arguments: dict) -> str:                # L83
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = _extract_text(result)
        if result.get("isError"):                                          # L87
            raise MCPError(f"tool {name} returned error: {text}")
        return text
```

**`_notify` 不含 `id` 字段** —— JSON-RPC 规范里"没有 id 的消息是通知，不期待响应"。所以 `_notify` 只写不读，不会与 `_request` 的 `while` 循环争抢 stdout。

**`call_tool` 把 `isError: true` 转成异常**（L87–88）—— 与"工具错误应作为数据回传"的主线（主线 3）**表面矛盾**。但看 `adapt_mcp_tool`：

```python
def _call(**kwargs):
    args = {k: v for k, v in kwargs.items() if v is not None}
    return client.call_tool(mcp_name, args)
```

`MCPError` 会被 `BaseAgent._execute` 的 `except Exception` 捕获 → 转成 `"error: MCPError: tool echo returned error: ..."` 回传。**所以异常在这里是"跨模块边界的内部机制"，最终仍被转成数据。** 这与 `Tool.run` 里 pydantic 校验失败抛异常、被 `_execute` 转成字符串是同一个模式。

```python
    def close(self) -> None:                                               # L90
        # stdin + stdout 都要关：只关 stdin 会让 stdout 的 TextIOWrapper 留到解释器
        # 退出时才被回收，测试/长驻进程里表现为 ResourceWarning: unclosed file。
        for pipe in (self._proc.stdin, self._proc.stdout):                 # L93
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        self._proc.terminate()                                             # L99
        try:
            self._proc.wait(timeout=5)                                     # L101
        except subprocess.TimeoutExpired:
            self._proc.kill()                                              # L103
```

**L91–92 的注释记录了一个真实的排查经历**：只关 stdin 会在测试里报 `ResourceWarning: unclosed file`。**这类警告在 pytest 里通常被忽略，但 `filterwarnings` 配置严格时会让测试失败**（`pyproject.toml` 里 `filterwarnings` 确实配置了忽略某些警告）。

**`terminate()` → `wait(5)` → `kill()` 的三段式**是标准的进程清理：先礼后兵。**`wait(timeout=5)` 是必需的** —— 没有它会留下僵尸进程；而无限 `wait()` 会让一个卡死的 server 挂住整个程序。**`except OSError` 包住 `pipe.close()`** 因为管道可能已因进程退出而关闭。

```python
def _extract_text(result: dict) -> str:                                    # L106
    """MCP tools/call 返回 content: [{type:"text", text}]，取所有 text 拼接。"""
    parts = [c.get("text", "") for c in result.get("content", [])
             if isinstance(c, dict) and c.get("type") == "text"]          # L110
    return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)   # L112
```

**兜底 `json.dumps(result)`（L112）**：如果 `content` 里没有任何 `type == "text"` 的块（比如 MCP server 返回了图片或结构化数据），**返回原始 JSON 而不是空串**。空串会让模型以为工具没返回任何东西；原始 JSON 至少包含了信息。**这是"降级也要保留信息"的例子。**

## 12.2 `app/mcp/tools.py`（57 行）(会话记录)

docstring（L1）："把 MCP 工具适配成 Warden 的 Tool（挂进 ToolRegistry，Agent 即可调用）。"

```python
_TYPE_MAP = {"string": str, "number": float, "integer": int,
             "boolean": bool, "array": list, "object": dict}                # L10–17

def build_input_model(name: str, input_schema: dict) -> type[BaseModel]:    # L20
    """类型映射是启发式的（string/number/integer/boolean/array/object），
    required 里的字段必填，其余 Optional 且默认 None（调用时过滤掉）"""
    properties = input_schema.get("properties", {}) or {}
    required = set(input_schema.get("required", []) or [])
    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        t = _TYPE_MAP.get(spec.get("type"), str)   # 未知类型回退 str            # L33
        if key in required:
            fields[key] = (t, Field(description=spec.get("description", "")))
        else:
            fields[key] = (t | None, Field(default=None,
                                           description=spec.get("description", "")))
    return create_model(f"MCP_{name}", **fields)                              # L38
```

**`create_model` 动态生成 pydantic 模型** —— 这是把"JSON Schema 描述的接口"变成"运行时可校验的 Python 类型"的标准做法。

**"类型映射是启发式的"是诚实的自我描述**，三个具体缺口：

1. **未知类型回退 `str`（L33）** —— 比如 `"type": "null"`、没有 `type` 字段、JSON Schema 的组合类型（`oneOf`/`anyOf`）都会变成 `str`。后果：本应是对象的参数会被要求传字符串（模型可能因此传 JSON 字符串，工具可能报错）。
2. **`t | None` 让可选参数可以是 None** —— 配合 `adapt_mcp_tool` 里的 `{k: v for k, v in kwargs.items() if v is not None}` 过滤，语义是"模型没给这个参数就不发"。**但这也意味着模型无法显式传 `null`**（会被过滤掉）—— 如果某个 MCP 工具的某个可选参数需要显式 null，这里表达不了。
3. **`array`/`object` 无 item 类型约束** —— `list` 与 `dict` 是裸类型，不校验元素。MCP 的 `inputSchema` 里 `items` 与 `properties` 被忽略。

**`f"MCP_{name}"`** 作为模型名 —— 出现在 pydantic 的校验错误信息里（`_coerce_output` 会带 `model.__name__`），所以能看出是哪个 MCP 工具。

```python
def adapt_mcp_tool(client, mcp_name, mcp_description,
                   input_schema, prefix="") -> Tool:                          # L41
    model = build_input_model(mcp_name, input_schema)
    def _call(**kwargs):
        args = {k: v for k, v in kwargs.items() if v is not None}             # L48  过滤 None
        return client.call_tool(mcp_name, args)
    name = (prefix + mcp_name) if prefix else mcp_name                        # L54
    return Tool(name=name, description=mcp_description or f"MCP tool {mcp_name}",
                input_model=model, func=_call)
```

**没有 `output_model`** —— MCP 的输出是自由文本（`_extract_text`），没有 schema 可校验。所以 MCP 工具的输出**不受 `_coerce_output` 保护**。

**没有 `timeout_s`** —— MCP 调用可能长时间阻塞（server 卡死），但这里没设超时。**`app/tools/base.py` 的超时机制存在却没有被 MCP 工具使用**，这是一个明显的可用缺口（第 17 章）。

`tests/test_mcp.py:68` 用内联的真 JSON-RPC stdio server 验证整条链路（`tool.run({"text":"hello"}) == "echo:hello"`）。

## 12.3 `app/mcp/registry.py`（24 行）(会话记录)

```python
def build_mcp_registry(servers: list[dict]) -> tuple[ToolRegistry, list[MCPClient]]:   # L10
    """servers: [{name, command: [...]}]。
    返回 (注册表, 客户端列表)：注册表可挂到 Agent 的 tools；客户端列表由调用方负责 close。"""
    registry = ToolRegistry()
    clients: list[MCPClient] = []
    for s in servers:
        name = s.get("name", "")
        client = MCPClient(s["command"])                                       # L18
        clients.append(client)
        prefix = f"{name}_" if name else ""                                    # L20
        for t in client.list_tools():
            registry.register(adapt_mcp_tool(client, t.name, t.description,
                                             t.input_schema, prefix))
    return registry, clients
```

**前缀机制解决命名冲突**：两个 MCP server 都提供 `echo` 工具时，有 `name` 的 server 的工具变成 `fs_echo`，没 `name` 的保持 `echo`。`tests/test_mcp_registry.py:47` 断言 `[t.name for t in registry.all()] == ["fs_echo", "echo"]`。

**"客户端列表由调用方负责 close"（docstring + 返回 tuple）** —— 因为注册表被 Agent 持有（生命周期长），而客户端的生命周期由调用方管（`cli.py` 的 `finally`、`_mcp_list` 的 `finally`）。**这是一个明确的资源所有权约定**，测试 `tests/test_mcp_registry.py:67` 专门验证"调用方 close 会生效"。

**`MCPClient(s["command"])` 用下标而不是 `.get`** —— `command` 缺失会 `KeyError`。对比 `name` 用 `.get`（可选）。**这正确地表达了"command 必需、name 可选"。**

**没有 try/except** —— `MCPClient(...)` 构造失败（命令不存在）会直接抛。**降级由调用方处理**：`cli.py` 的 `_team` 用 `try/except Exception` 包住并 warn 跳过（`tests/test_cli.py:299` 验证这条路径）。**"机制在库、策略在调用方"的分层。**

## 12.4 `app/mcp/__init__.py`（14 行）

docstring："MCP（Model Context Protocol）客户端 + 工具适配（M4）"。导出 `MCPClient`/`MCPError`/`MCPTool`/`adapt_mcp_tool`/`build_input_model`/`build_mcp_registry`。

---

# 第 13 章 定时任务与通知

## 13.1 `app/scheduler/base.py`（46 行）(会话记录)

```python
@dataclass
class TaskResult:                                                        # L15
    task: str
    status: str   # "ok" | "skipped" | "error"
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

@dataclass
class Services:                                                          # L24
    """任务运行所需的共享服务。store 为 None 时任务运行不落库。"""
    settings: Settings
    llm: LLMClient
    notifier: Notifier
    store: RunStore | None = None

class BaseTask(ABC):                                                     # L34
    """...schedule 是 APScheduler trigger 配置，如 {"trigger": "cron", "hour": 8}
    或 {"trigger": "interval", "minutes": 60}"""
    name: str = ""
    description: str = ""
    schedule: dict = {"trigger": "interval", "minutes": 60}              # L41

    @abstractmethod
    def run(self, ctx: Services) -> TaskResult: ...
```

**`Services` 是一个"依赖包"（context object）** —— 把任务需要的所有协作者打成一个对象，这样任务签名永远是 `run(ctx)`，新增依赖不改签名。**代价**：任务看不出自己实际用了哪些依赖（隐式）。`tests/test_tasks.py:65` 的 `_ctx()` 只传 `Services(Settings(), FakeLLM(), RecordingNotifier())`（store 默认 None）就能测。

**`store=None` 是一个受支持的状态**（docstring 明说"任务运行不落库"）。这让任务可以脱离 Harness 单测。`tests/test_scheduler.py:189` 专门验证 `store=None + metrics_enabled=False` 也能跑。

**`TaskResult.status` 三态** 与 `record_run` 的 status 一致（`ok`/`error`/`skipped`），这在 `runner.py` 里被直接透传给指标。

**`schedule` 是类属性默认值** —— 让"忘了设 schedule 的任务"有一个合理的默认（每小时一次），而不是崩。

## 13.2 `app/scheduler/registry.py`（31 行）(会话记录)

```python
class TaskNotFoundError(Exception): pass                                 # L8

class TaskRegistry:
    def register(self, task: BaseTask) -> None:                          # L12
        if task.name in self._tasks:
            raise ValueError(f"duplicate task name: {task.name}")
        self._tasks[task.name] = task

    def get(self, name: str) -> BaseTask:                                # L22
        try:
            return self._tasks[name]
        except KeyError:
            raise TaskNotFoundError(f"unknown task: {name}") from None
    def all(self): return list(self._tasks.values())                     # L26
    def names(self): return list(self._tasks)                            # L29
```

**与 `ToolRegistry` 完全同构**（注册时 `ValueError`、查找时专用异常、`names()`/`all()`）。docstring 明说"与工具注册表同构"。

**这是一个好的重复** —— 两个注册表的语义确实一样（名字 → 对象），共用抽象会引入不必要的耦合（工具与任务没有共同基类）。**"复制一个 30 行的模式"比"提取一个 15 行的基类"更清晰。**

## 13.3 `app/scheduler/runner.py`（97 行）(会话记录)

```python
def _build_trigger(schedule: dict):                                      # L23
    spec = dict(schedule)                                                # L24  拷贝，不改调用方的 dict
    kind = spec.pop("trigger", "interval")                               # L25
    if kind == "cron":
        return CronTrigger(**spec)
    if kind == "interval":
        return IntervalTrigger(**spec)
    raise ValueError(f"unsupported trigger type: {kind}")                # L30
```

**`dict(schedule)`（L24）是必需的**：`spec.pop("trigger")` 会修改字典，而 `schedule` 是**类属性**（`BaseTask.schedule`）—— 直接 pop 会**永久删除类上的 trigger 键**，第二次构造调度器时就会走默认分支。**这是一个真实的陷阱**，`tests/test_scheduler.py:89` 连续调用两次 `_build_trigger` 隐含验证了它。

`tests/test_scheduler.py:89` 断言 `str(_build_trigger({"trigger":"interval","minutes":5})) == "interval[0:05:00]"` —— **APScheduler 的 trigger `__str__` 是可断言的稳定表示**，这是一个很实用的测试技巧（不用启动 scheduler 就能验证 trigger 构造）。

```python
def build_scheduler(registry: TaskRegistry, ctx: Services) -> BackgroundScheduler:   # L33
    tz = datetime.now().astimezone().tzinfo                              # L34
    scheduler = BackgroundScheduler(timezone=tz)                         # L35
    for task in registry.all():
        scheduler.add_job(_run_task, trigger=_build_trigger(task.schedule), args=[task, ctx],
                          id=task.name, name=task.name,
                          coalesce=True, max_instances=1)                # L40–44
        log.info("scheduled %s: %s", task.name, task.schedule)
    return scheduler
```

**`datetime.now().astimezone().tzinfo`（L34）取本地时区** —— 模块 docstring 说"时间用本地时区（stdlib 取 tzinfo，不引入 tzlocal/pytz）"。

**为什么这个写法正确**：`datetime.now()` 返回 naive datetime（无时区），`.astimezone()` 会把它**按本地时区**附加 tzinfo，`.tzinfo` 再取出那个时区对象。**对比 `datetime.now().tzinfo`（返回 `None`）与 `dateutil.tz.tzlocal()`（第三方）。** 这个 idiom 是 stdlib-only 获取本地时区的标准做法。

**注意：得到的 tzinfo 是"当前那一刻的时区偏移"**（比如 `CST+08:00` 的固定偏移），**不是能感知夏令时的时区规则**。对 `{"hour": 8}` 这种任务，如果所在时区有夏令时切换，通知时间会在切换后偏移一小时。**这是"零依赖"的代价**（第 17 章）。

**`coalesce=True`** 让"错过的多次触发合并成一次" —— 如果机器休眠 3 天后唤醒，一个每小时的 interval 任务不会连跑 72 次。

**`max_instances=1`** 让同一个任务不能并发运行 —— 对 RSS 抓取 + LLM 摘要这种长任务必需（否则堆积）。`tests/test_scheduler.py:102` 断言 `jobs["stub"].max_instances == 1`。

```python
def _run_task(task: BaseTask, ctx: Services) -> TaskResult:              # L50
    tid = new_trace_id()                                                 # L51
    started = time.time()
    with trace_span(tid):                                                # L53
        record = None
        if ctx.store is not None:
            record = RunRecord(id=tid, kind="task", name=task.name)       # L56
            ctx.store.start(record)
        attempts: list[int] = []                                         # L58
        try:
            result = with_retry(lambda: task.run(ctx),
                                attempts=ctx.settings.retry_attempts,
                                backoff_s=ctx.settings.retry_backoff_s,
                                on_attempt=attempts.append)              # L60–63
        except Exception as exc:   # 重试仍失败：兜底，不让调度线程崩溃
            log.exception("task failed: %s", task.name)                  # L66
            result = TaskResult(task=task.name, status="error",
                                summary=str(exc), error=str(exc))        # L67
        if record is not None:
            record.status = result.status
            record.output = result.summary
            record.error = result.error
            record.attempts = len(attempts)                              # L72
            record.finished_at = time.time()
            ctx.store.update(record)
        _notify(task, ctx, result)
        if ctx.settings.metrics_enabled:
            record_run("task", task.name, result.status, time.time() - started)   # L79
        log.info("task done: %s -> %s", task.name, result.status)
    return result
```

**这个函数是 `BaseAgent._turn` 的"任务版镜像"**，结构完全平行：

| | `BaseAgent._turn` | `_run_task` |
|---|---|---|
| trace | `trace_span` 由外层 `run()` 提供 | 自己 `new_trace_id()` + `trace_span` |
| 落库 | `_begin` → `RunStore.start` | 内联 `RunRecord` + `store.start` |
| 重试 | 每个 `_chat` 单独包 `with_retry` | 整个 `task.run` 包 `with_retry` |
| 失败 | `_finish_error` + re-raise | **吞掉，构造 error TaskResult** |
| 指标 | `record_run("chat", name, status, ...)` | `record_run("task", name, status, ...)` |

**`except Exception` 吞掉异常（L65–67）是这里最重要的行为**，注释明说"不让调度线程崩溃"。**理由**：APScheduler 的任务异常会被记录但 keep 着 job（默认 `misfire_grace_time` 后继续），但一个未捕获异常会让 `_run_task` 的返回值为 `None` → 后面的 record 回填全崩 → **一条错误的任务变成一个没有记录的静默失败**。**显式构造 error TaskResult 保证"失败也被完整记录并通知"。** `tests/test_scheduler.py:148` 验证这条路径（`notifier.sent == [("stub 失败", "接口挂了")]`）。

**`record.attempts = len(attempts)`（L72）** —— `attempts` 由 `on_attempt=attempts.append` 填充，所以 `len(attempts)` 就是实际尝试次数。**注意它只在 `record is not None` 时被使用**，而 `attempts` 列表总是被填充（L58 无条件创建）。

**任务级重试 vs 调用级重试**：`_run_task` 把**整个任务**包在 `with_retry` 里，而任务内部的 LLM 调用**没有**自己的重试（`news_digest.run` 直接调 `ctx.llm.chat`）。所以一次 RSS 抓取失败会重跑**整个任务**（重新抓所有源、重新去重）。**这是"任务级幂等"的假设**：任务必须能被安全地重复执行。`news_digest` 满足（去重状态只在成功后保存），但一个"发邮件"的任务就不满足。**这个约束没有被文档化**（第 17 章）。

```python
def _notify(task: BaseTask, ctx: Services, result: TaskResult) -> None:   # L87
    """统一送达：ok 送报告正文，error 送失败原因，skipped 静默"""
    if result.status == "ok":
        ctx.notifier.send(f"{task.name} 报告", result.summary)
    elif result.status == "error":
        ctx.notifier.send(f"{task.name} 失败", result.summary)            # L92
```

**`skipped` 不发通知** —— `tests/test_scheduler.py:179` 断言 `notifier.sent == []`。**理由**：`skipped` 的语义是"没有新内容"（`news_digest` 的 `"没有新条目"`），每天都发一条"今天没有新资讯"是噪音。**这是一个很好的"沉默即成功"的默认。**

**标题用任务名而非描述**（`f"{task.name} 报告"`）—— 稳定的标题让 `FileNotifier` 的 slug 稳定（同名文件按时间戳区分）。

```python
def run_once(registry: TaskRegistry, ctx: Services, name: str) -> TaskResult:   # L95
    """手动触发单次运行（API / CLI 用）"""
    return _run_task(registry.get(name), ctx)
```

**复用 `_run_task` 而不是另写一份** —— 所以手动触发和定时触发**共享全部行为**：trace、落库、重试、通知、指标。`tests/test_scheduler.py:124` 验证 `run_once` 会落库并通知。**`registry.get(name)` 抛 `TaskNotFoundError`** 直接冒泡（不吞）—— 这是调用方的**输入错误**（未知任务名），不是任务运行失败，所以不该被转成 error TaskResult。`main.py` 与 `cli.py` 都捕获它并转成 404 / 退出码 1。

## 13.4 `app/notify/*`（4 文件，共 65 行）(会话记录)

```python
class Notifier(ABC):                                                     # base.py L8
    name: str = "base"
    @abstractmethod
    def send(self, title: str, content: str) -> None: ...

class ConsoleNotifier(Notifier):                                         # console.py
    name = "console"
    def send(self, title, content):
        print(f"\n===== {title} =====\n{content}\n")                    # L11

class FileNotifier(Notifier):                                            # file.py
    name = "file"
    def __init__(self, report_dir: str) -> None:
        self._dir = Path(report_dir)

    def send(self, title: str, content: str) -> None:                    # L14
        self._dir.mkdir(parents=True, exist_ok=True)                     # L15
        slug = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")[:60].lower()   # L16
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")                    # L17
        path = self._dir / f"{slug}_{ts}.md"                             # L18
        path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
        print(f"[file-notifier] wrote {path}")
```

**`FileNotifier` 的 slug 正则 `[^\w\-]+` 配 `flags=re.UNICODE`** —— `\w` 在 UNICODE 模式下包含中文，所以 `"Warden 周报: 09/10!"` 变成 `warden_周报_09_10`。**`[:60]` 截断**防止超长文件名（`tests/test_notify.py:41` 用 80 次重复的标题验证）。**`.lower()`** 让文件名跨平台一致（Windows 大小写不敏感、Linux 敏感）。

`tests/test_notify.py:36` 用 `re.fullmatch(r"warden_周报_09_10_\d{8}_\d{6}\.md", name)` 精确断言。**这条正则本身就是文档** —— 它记录了文件名格式的全部约定。

**时间戳只精确到秒（L17）** → **同一秒内发两次同名通知会互相覆盖**。`tests/test_notify.py:49` 明确测试并**记录了这个行为**（"documented deliberate tradeoff"）。修法是加毫秒或序号，但对个人项目（一天几次通知）没必要。

```python
def get_notifier(settings: Settings) -> Notifier:                        # __init__.py L13
    """按 settings.notify_kind 选择通知器（M1：console / file）"""
    if settings.notify_kind.lower() == "file":
        return FileNotifier(settings.report_dir)
    return ConsoleNotifier()                                            # L17
```

**".lower()" 让 `FILE`/`File` 都工作**，**未知值静默回落 console**（不抛）。`tests/test_notify.py:18` 用 `"carrier-pigeon"` 验证这个回落。**这是"配置错误不该让程序起不来"的选择** —— 但代价是拼错 `"fil"` 会静默变成 console 通知，用户可能纳闷"为什么报告没写成文件"（第 17 章的一个小的可用性缺口）。

## 13.5 `app/tasks/__init__.py`（25 行）(会话记录)

```python
def build_default_task_registry(settings: Settings) -> TaskRegistry:     # L19
    """构建 M1 默认任务注册表（3 个任务）。任务构造只吃配置，LLM/通知器经 Services 传入。"""
    reg = TaskRegistry()
    reg.register(NewsDigestTask(settings.rss_source_list, settings.data_dir))
    reg.register(RepoReportTask(settings.repo_path))
    reg.register(WeeklyReviewTask(settings.notes_dir, settings.repo_path))
    return reg
```

**"任务构造只吃配置，LLM/通知器经 Services 传入"** —— 这是依赖注入的边界：构造期只需要"静态配置"（路径、源列表），运行期才需要"活的协作者"（LLM、notifier）。**这让任务可以在测试里用 Fake LLM 构造**（`tests/test_tasks.py`）。

## 13.6 `app/tasks/news_digest.py`（132 行）(会话记录)

docstring（L1）："每日资讯/论文简报：抓 RSS → 去重（跨次运行） → LLM 摘要 → 通知"。

```python
_ATOM = {"a": "http://www.w3.org/2005/Atom"}                             # L14
_MAX_SEEN = 500   # 已见链接只保留最近 500 条，避免无限增长                # L15
```

**`_MAX_SEEN = 500` 是一个有意识的截断**：已见链接集合如果不限长，会无限增长（每天几十条，几年后 JSON 文件很大）。截断到最近 500 条的**后果**：一个非常老的链接如果再次出现在 feed 里（或者被挤出了 500 条窗口），会被当成"新条目"再发一次简报。**对"每日资讯"这个场景，这是可接受的**（RSS feed 本身通常只保留最近几十条）。**这是一个很好的"足够好"的取舍例子。**

```python
@dataclass
class FeedItem:                                                          # L18
    title: str
    link: str
    summary: str = ""
    published: str = ""

    def short(self, limit: int = 120) -> str:                            # L24
        body = " ".join((self.summary or self.title).split())            # L25
        return body[:limit]
```

**`short()` 的两步**：`.split()` 把任意空白（换行、制表、多空格）折叠 + `" ".join` 用单空格重连 → **HTML 摘要里的换行被压平**，然后截断 120 字符。**`(self.summary or self.title)`** 在没有摘要时回落到标题。这让 `_prompt` 里的每条条目都有一行可读的正文。

```python
def parse_feed(xml_text: str) -> list[FeedItem]:                         # L34
    """解析 RSS 2.0（<item>）与 Atom（<entry>）两种格式，stdlib 实现，不引 feedparser"""
    root = ET.fromstring(xml_text)                                       # L36
    items: list[FeedItem] = []
    for it in root.findall(".//item"):                                   # RSS
        items.append(FeedItem(_text(it.find("title")), _text(it.find("link")),
                              _text(it.find("description")), _text(it.find("pubDate"))))
    for en in root.findall(".//a:entry", _ATOM):                          # Atom
        link = ""
        for ln in en.findall("a:link", _ATOM):                           # L50
            if ln.get("rel") in (None, "", "alternate"):
                link = ln.get("href") or ""
                break
        if not link:                                                     # L56
            link = next((ln.get("href") or "" for ln in en.findall("a:link", _ATOM)), "")
        items.append(FeedItem(_text(en.find("a:title", _ATOM)),
                              link, _text(en.find("a:summary", _ATOM)),
                              _text(en.find("a:updated", _ATOM))))
    return [i for i in items if i.title and i.link]                       # L67
```

**RSS 与 Atom 的差异处理**：

| | RSS 2.0 | Atom |
|---|---|---|
| 条目 | `.//item` | `.//a:entry`（需命名空间）|
| 链接 | `<link>` 文本内容 | `<link href="...">` 属性 |
| 多个 link | 无 | 有（`rel="alternate"` / `"self"` / `"enclosure"`）|

**Atom 链接的两级选择（L50–57）**：优先 `rel` 为 `None`/`""`/`"alternate"` 的（这是"人类可读页面"的约定值），没有则取第一个任何 link 的 href。**这个优先级是正确的**：`rel="self"` 指向 feed 自身的 URL，`rel="enclosure"` 指向附件（可能是音频），只有 `alternate`（或缺省）指向文章页面。

**L67 的过滤（`if i.title and i.link`）** —— 两条都必需：**没有 link 的条目无法去重**（去重键是 link），没有 title 的条目无法展示。**在解析边界处过滤掉不完整数据，而不是在下游每个用法里判空。**

```python
class NewsDigestTask(BaseTask):                                          # L70
    name = "news_digest"
    description = "抓取 RSS 资讯/论文，去重后由 LLM 生成简报"
    schedule = {"trigger": "cron", "hour": 8, "minute": 0}               # L73

    def __init__(self, rss_sources: list[str], data_dir: str) -> None:   # L75
        self._sources = rss_sources
        self._seen_path = Path(data_dir) / "news_seen.json"              # L77
```

**`schedule` 是每天早上 8 点** —— 符合"资讯简报"的使用场景。

```python
    def _load_seen(self) -> set[str]:                                    # L79
        try:
            return set(json.loads(self._seen_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return set()                                                 # L83

    def _save_seen(self, seen: set[str]) -> None:                        # L85
        self._seen_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(list(seen)[-_MAX_SEEN:], ensure_ascii=False)   # L88
        self._seen_path.write_text(payload, encoding="utf-8")
```

**`_load_seen` 的容错覆盖两种失败**：文件不存在（`OSError`，首次运行）与 JSON 坏掉（手动编辑出错）。都返回空集 → **所有条目都算新** → 发一次完整简报。**这是正确的降级方向**（宁可重发也不要漏发）。

**`list(seen)[-_MAX_SEEN:]`（L88）** —— **注意这里有一个微妙的非确定性**：`set` 的迭代顺序在 Python 里依赖哈希值，对字符串**默认启用哈希随机化**（`PYTHONHASHSEED`）。所以同一个 `seen` 集合，两次运行可能保留**不同的 500 条**。**这不是 bug**（保留哪些旧链接无关紧要），但它意味着"截断后的 seen 集合"不可复现。**如果要严格复现，应该 `sorted(seen)[-_MAX_SEEN:]`。** 这是一个值得记录的小瑕疵（第 17 章）。

```python
    def _fetch_all(self) -> tuple[list[FeedItem], list[str]]:            # L91
        items: list[FeedItem] = []
        errors: list[str] = []
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:   # L93
            for src in self._sources:
                try:
                    resp = client.get(src)
                    resp.raise_for_status()
                    items.extend(parse_feed(resp.text))
                except Exception as exc:   # 单个源失败不拖垮整体
                    errors.append(f"{src}: {exc}")                       # L101
        return items, errors
```

**`errors` 与 `items` 并行返回** —— 让调用方知道"有些源失败了"但**不影响成功的源**。**`follow_redirects=True`** 是必需的（很多 feed URL 会 301 到 CDN）。**`timeout=15.0`** 对 RSS 抓取足够（对比 LLM 的 120s）。

**`except Exception` 的粒度是"每个源"**（在 for 循环内）—— 这是正确的：一个源坏了不该让其他源白抓。

```python
    def run(self, ctx: Services) -> TaskResult:                          # L104
        items, errors = self._fetch_all()
        seen = self._load_seen()
        fresh = [i for i in items if i.link not in seen]                 # L107
        if not fresh:
            if errors:
                return TaskResult(self.name, "error", "抓取失败", {"errors": errors})   # L110
            return TaskResult(self.name, "skipped", "没有新条目",
                              {"total": len(items), "new": 0})           # L112
        digest = ctx.llm.chat(self._prompt(fresh))                       # L114
        self._save_seen(seen | {i.link for i in fresh})                  # L115
        return TaskResult(self.name, "ok", digest,
                          {"total": len(items), "new": len(fresh), "errors": errors})
```

**四个决策点**：

1. **`if not fresh` 时区分"有错误"与"真没新条目"（L109–112）** —— **这是整个任务里最细致的判断**。如果所有源都失败了，`items` 是空的，`fresh` 也是空的 —— 如果不检查 `errors`，会返回 `skipped`（"没有新条目"），**把一个抓取故障伪装成"今天没有资讯"**。**这是一个真实会误导用户的失败模式，代码正确地防住了它。**
2. **`_save_seen` 在 LLM 调用之后（L115）** —— **顺序很重要**：如果先保存 seen 再调 LLM，而 LLM 调用失败（抛异常），这些条目就被标记为"已见"了 → 下次运行会跳过它们 → **资讯永久丢失**。把 `_save_seen` 放在最后，保证"只有成功生成了简报的条目才被标记为已见"。**这是"先落库还是先提交"的经典问题，代码选了正确的一边。**
3. **`seen | {i.link for i in fresh}`（L115）** —— 用集合并集而不是 `seen.update(...)`，保持 `_save_seen` 的参数是纯函数式的（不修改调用方的 `seen`）。
4. **`detail` 里带 `errors`**（L116）—— 即使成功也报告部分失败。**这让"简报缺了几个源"在运行记录里可查**（`/api/v1/runs` 的 detail 不展示，但 DB 里有）。

`tests/test_tasks.py:81` 验证"首跑 ok 且 `detail["new"]==2`，再跑 skipped 且 `detail["new"]==0`"。

```python
    def _prompt(self, items: list[FeedItem]) -> list[dict]:              # L122
        lines = [f"- [{i.title}]({i.link})" + (f" — {i.short()}" if i.summary else "")
                 for i in items[:30]]                                    # L124
        system = ("你是个人资讯助理。把下面的新条目汇总成一份简洁的中文简报："
                  "按主题归类，每条一句话点出核心信息，最后给出 3 条最值得关注的重点。"
                  "直接输出 Markdown。")                                  # L127–130
        return [{"role": "system", "content": system},
                {"role": "user", "content": "今日新条目：\n" + "\n".join(lines)}]
```

**`items[:30]` 的截断** 是上下文成本控制（每天 RSS 可能几百条）。**`if i.summary` 才加 `— short()`** 避免出现 `— ` 空尾。

**提示词的三条要求**（按主题归类 / 每条一句话 / 最后 3 条重点）—— **可验证的结构化输出要求**。"直接输出 Markdown"让结果可以直接进 `FileNotifier` 或控制台。

## 13.7 `app/tasks/repo_report.py`（109 行）(会话记录)

```python
_SCAN_SUFFIXES = (".py",)                                                # L13
_MARKERS = ("TODO", "FIXME", "HACK", "XXX")                              # L14
_SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__"}            # L15

class RepoReportTask(BaseTask):
    name = "repo_report"
    schedule = {"trigger": "interval", "hours": 24}                      # L22
```

**`_SKIP_DIRS` 的四个目录** 覆盖了"第三方代码与缓存" —— 扫 TODO 时必须跳过，否则 `.venv` 里的几千个 TODO 会淹没自己的代码。

```python
    def _scan_todos(self) -> list[str]:                                  # L26
        hits: list[str] = []
        for p in sorted(self._root.rglob("*")):                          # L27
            if not p.is_file() or p.suffix not in _SCAN_SUFFIXES:
                continue
            if _SKIP_DIRS & set(p.parts):                                # L31
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")    # L33
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if any(m in line for m in _MARKERS):
                    hits.append(f"{p}:{i}: {line.strip()}")              # L38
        return hits
```

**`_SKIP_DIRS & set(p.parts)`（L31）** 用集合交集检查**路径的任意一段**是否在黑名单里 —— 比 `if ".venv" in str(p)` 更精确（后者会误伤名为 `my.venv.notes.py` 的文件）。**这是一个用对数据结构解决问题的好例子。**

**`errors="ignore"`（L33）** —— 遇到非 UTF-8 文件（比如某些带 BOM 或 latin-1 的文件）时跳过坏字节而不是抛。**对"扫描整个代码库"这个场景是必需的**（总会有几个文件编码不对）。

**`f"{p}:{i}: {line.strip()}"`（L38）** —— 输出格式是 `路径:行号: 内容`，**这是 IDE/编辑器可直接点击跳转的格式**（grep 风格）。**这是一个小的可用性设计。**

```python
    def _declared_deps(self) -> list[str]:                               # L42
        """从 pyproject.toml 读声明依赖名（tomllib 为 3.11+ stdlib）"""
        try:
            data = tomllib.loads((self._root / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return []                                                    # L47
        return [self._dep_name(d) for d in data.get("project", {}).get("dependencies", [])]

    @staticmethod
    def _dep_name(dep: str) -> str:                                      # L50
        """从 "uvicorn[standard]>=0.30" 这类声明里只取包名 "uvicorn""""
        if "[" in dep:
            dep = dep.split("[", 1)[0]                                   # L53
        return re.split(r"[<>=!~;,\s]", dep, maxsplit=1)[0].strip()      # L54
```

**`_dep_name` 剥掉三层装饰**：

| 输入 | 输出 | 剥掉 |
|---|---|---|
| `uvicorn[standard]>=0.30` | `uvicorn` | extras + 版本 |
| `pydantic-settings>=2.3` | `pydantic-settings` | 版本 |
| `pytest>=8.0` | `pytest` | 版本 |
| `foo; python_version < "3.12"` | `foo` | 环境标记（`;`）|
| `foo!=1.0` | `foo` | 排除版本（`!=`）|

**`maxsplit=1`** 保证只切第一个分隔符，剩下全丢（正是想要的）。**`re.split` 的字符类 `[<>=!~;,\s]` 覆盖了 PEP 508 的所有版本运算符与分隔符。**

```python
    def _deps(self) -> list[str]:                                        # L58
        out = []
        for name in self._declared_deps():
            try:
                version = importlib_metadata.version(name)
                out.append(f"{name}=={version}")
            except importlib_metadata.PackageNotFoundError:
                out.append(f"{name}==(未安装)")                           # L64
        return out
```

**`importlib_metadata.version`（L61）读的是"实际安装的版本"**，与 `_declared_deps` 的"声明的约束"配对 —— 报告的价值在于**发现两者不一致**（声明 `>=0.30` 但实际装的是 `0.28`）。**`PackageNotFoundError` → `"(未安装)"`** 是一个明确的信号（声明了但没装）。

```python
    def _recent_commits(self, days: int = 7) -> list[str]:               # L67
        try:
            proc = subprocess.run(
                ["git", "log", "--oneline", f"--since={days} days ago"],
                cwd=self._root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15)          # L71–73
            # 见 weekly_review 同名方法：不写编码时 Windows 用本地代码页解码，
            # 中文提交信息会让整段输出在读取线程里丢掉。
            if proc.returncode != 0:
                return []
            return [ln for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception:   # 非 git 仓库 / git 不可用时静默降级
            return []                                                    # L82
```

**L74–75 的注释是全项目最值得记住的 Windows 教训之一**：

> 见 weekly_review 同名方法：不写编码时 Windows 用本地代码页解码，中文提交信息会让整段输出在读取线程里丢掉。

**"整段输出在读取线程里丢掉"** 意味着 `proc.stdout` 是**空的** —— 不是少了中文那几行，而是**整个 git log 都没有**。这个失败模式极其隐蔽（"git log 命令跑了但没输出"看起来像仓库没有提交）。**修法就是显式 `encoding="utf-8", errors="replace"`。**

**`errors="replace"`** 而不是 `"ignore"` —— 用 `?` 替代坏字节，保留位置信息。

**`except Exception` 覆盖 `FileNotFoundError`（git 没装）、`subprocess.TimeoutExpired`、`NotADirectoryError`** —— 全部降级为 `[]`。**"非 git 仓库"是一个正常状态**（用户可能把 Warden 放在非 git 目录）。

`tests/test_weekly_review.py:103` 用 `@pytest.mark.parametrize` 对 `weekly_review` 与 `repo_report` **同时**跑中文提交的回归测试 —— 因为两个文件有**同一段代码**（复制而来）。

**两处同名方法的重复**：`repo_report._recent_commits` 与 `weekly_review._recent_commits` 代码几乎相同（只差 `timeout` 与格式）。**这是刻意的重复还是疏漏？** 从"注释互相引用（'见 weekly_review 同名方法'）"来看，作者知道重复并接受了它。**代价**：一个 bug 要修两处（中文编码那个 bug 确实修了两处，CHANGELOG 记了）。**收益**：两个任务可以独立演化（比如 repo_report 想按作者过滤）。**这是一个边界情况，但"注释互相引用"表明是有意识的。**

```python
    def _prompt(self, todos, deps, commits) -> list[dict]:               # L96
        system = ("你是代码库维护助手。根据下面的扫描结果生成一份中文维护报告："
                  "按优先级列出需要处理的问题，指出可以升级的依赖，最后给出维护建议。"
                  "直接输出 Markdown。")
        user = ("## TODO/FIXME/HACK\n" +
                ("\n".join(f"- `{t}`" for t in todos[:50]) or "（无）") +   # L101
                "\n\n## 依赖\n" + ("\n".join(f"- `{d}`" for d in deps) or "（无）") +
                "\n\n## 近期提交\n" + ("\n".join(f"- `{c}`" for c in commits[:30]) or "（无）"))
```

**三段 Markdown 标题（`## TODO/FIXME/HACK` / `## 依赖` / `## 近期提交`）+ "（无）"占位** —— 让模型看到的输入结构恒定（即使某段为空），避免"这一节不存在"导致的输出结构漂移。**`或 "（无）"`** 处理空列表（空字符串会被当成假值）。

**三段的截断上限不同**（`todos[:50]` / deps 全部 / `commits[:30]`）—— 反映各段的预期规模（TODO 最多、依赖最少）。

## 13.8 `app/tasks/weekly_review.py`（74 行）(会话记录)

```python
class WeeklyReviewTask(BaseTask):
    name = "weekly_review"
    schedule = {"trigger": "cron", "day_of_week": "mon", "hour": 9, "minute": 0}   # L15

    def __init__(self, notes_dir: str, repo_path: str) -> None:          # L17
        self._notes_dir = Path(notes_dir) if notes_dir else None         # L18
        self._root = Path(repo_path)
```

**`notes_dir` 为空时 `None`**（而不是 `Path("")` → `Path(".")`）—— **这个区分很关键**：`Path("")` 是 `PosixPath('.')`，会让"没配笔记目录"变成"扫描当前目录的所有 markdown"。用 `None` 表达"未配置"。`tests/test_weekly_review.py:71` 用 `notes_dir=""` 与不存在的目录分别测试。

```python
    def _recent_notes(self, days: int = 7) -> list[str]:                 # L21
        if self._notes_dir is None or not self._notes_dir.is_dir():
            return []                                                    # L23
        cutoff = datetime.now() - timedelta(days=days)
        out = []
        for p in sorted(self._notes_dir.rglob("*.md")):                  # L26
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if mt >= cutoff:
                out.append(f"- {p.name}（改于 {mt:%Y-%m-%d %H:%M}）")     # L31
        return out
```

**按 mtime 而非内容判断"本周笔记"** —— 简单、零依赖、符合直觉。**`f"- {p.name}（改于 {mt:%Y-%m-%d %H:%M}）"` 用中文全角括号** —— 输出是给人看的。

**注意用 `p.name`（文件名）而非完整路径** —— 周报是给人读的，`2026-01-15.md` 比 `/home/u/notes/sub/2026-01-15.md` 更好。**代价**：同名文件在不同子目录下无法区分。

`tests/test_weekly_review.py:40–44` 的 `_write(..., age_days=30)` 用 **`os.utime` 回拨 mtime** 造出"30 天前的笔记" —— **这是一个测试技巧，避免等待或 mock 时钟**。

```python
    def _prompt(self, notes, commits) -> list[dict]:                     # L63
        system = ("你是个人复盘助手。根据下面的材料生成一份中文周报："
                  "总结本周做了什么、有哪些进展与收获、下周可以推进什么。直接输出 Markdown。")
        user = ("## 本周笔记\n" + ("\n".join(notes) or "（无）") +
                "\n\n## 本周提交\n" + ("\n".join(commits[:50]) or "（无）"))
```

**提交截断到 50** —— `tests/test_weekly_review.py:155` 用 60 条提交（c0..c59）断言 `"c49" in user and "c50" not in user`。**这是一个精确的边界测试**（验证的是"第 50 条包含、第 51 条不包含"）。

**system prompt 的三段结构与 `repo_report` 同构**（三条要求 + "直接输出 Markdown"）—— 两个任务的提示词模板一致，这是一个好的模式复用。

## 13.9 三个任务的横向对比

| | news_digest | repo_report | weekly_review |
|---|---|---|---|
| 触发 | `cron 8:00 每天` | `interval 24h` | `cron 周一 9:00` |
| 输入源 | RSS（网络） | 本地代码库 | 本地笔记 + git |
| 持久状态 | `data/news_seen.json` | 无 | 无 |
| 跳过条件 | 无新条目 | 无 | 无 |
| 失败降级 | 单源失败不整体失败 | git 不可用返回 `[]` | 目录缺失返回 `[]` |
| 输出截断 | 条目 30 | TODO 50 / 提交 30 | 提交 50 |
| LLM 调用 | 1 | 1 | 1 |

**只有 `news_digest` 有跨次运行的状态**，所以只有它需要 `skipped` 状态与 `_MAX_SEEN` 截断。**另外两个任务每天都产出报告**（即使内容相同）—— 这是"报告类任务"与"通知类任务"的本质差别。

**三个任务的 LLM 调用都是 1 次** —— 它们不涉及工具调用或多轮推理，只是"把数据喂给模型让它写文章"。**这是刻意的**：定时任务追求确定性，不需要 Agent 循环的自主性。

---

# 第 14 章 控制台与 CLI

## 14.1 `app/static/index.html`（120 行）(会话记录)

**零构建自包含控制台**：无框架、无 npm、无构建步骤，只有内联 CSS + 原生 fetch。这是"主线 3（够用优先）"最直接的体现 —— README 明确把它当作一个设计声明（"控制台是零构建静态 HTML"）。

```css
:root { --bg:#0f1117; --card:#171a21; --line:#262b36; --fg:#e6e8ee;
        --muted:#8a93a6; --accent:#4f8cff; --ok:#2ecc71; --err:#ff5c5c; --run:#f0a030; }
```

**九个 CSS 变量**（L8–9）—— 深色主题的完整调色板。**`--run: #f0a030`（橙色）** 是给 `status="running"` 用的（`statusPill` 的三分支之一）。这个颜色在代码里出现两次（CSS 变量与 JS 分支），是"样式与逻辑分离"的一个小代价。

**L43：header 里链到 `/metrics`** —— 控制台不渲染指标，只提供一个跳转到 Prometheus 文本端点的链接。**这是一个聪明的取舍**：Grafana 已经提供了完整的可视化，控制台重复实现图表毫无意义。

```javascript
async function json(url, opts) {                                         // L60–64
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function esc(s) {                                                        // L65–68
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function statusPill(s) {                                                 // L69–72
  const cls = s === 'ok' ? 'ok' : s === 'error' ? 'err' : 'run';
  ...
}
```

**三个基础设施函数，每个都解决一个真实问题**：

1. **`json(url, opts)`（L60–64）**：`if (!r.ok) throw new Error(await r.text())` —— **非 2xx 时把响应体当错误消息抛出**。这让 `加载失败：{"detail":"unknown task: nope"}` 这样的提示能显示后端的真实错误，而不是笼统的"请求失败"。
2. **`esc(s)`（L65–68）**：`replace(/[&<>"']/g, ...)` 转义**五个**危险字符（`&`、`<`、`>`、`"`、`'`）—— **这是防 XSS 的必需项**，因为 `loadRuns()` 会把 `record.error`（可能含用户/模型/第三方服务的文本）渲染进 innerHTML。`String(s ?? "")` 让 `null`/`undefined` 变成空串而不是 `"null"`。
3. **`statusPill(s)`（L69–72）**：三态映射（ok→绿、error→红、**其他→橙**）。**注意"其他"包含 `running` 但不限于它** —— 任何未知状态都显示橙色，这是"未知状态也可见"的健壮选择。

```javascript
async function loadTasks() {                                             // L80–102
  ... json(`/api/v1/tasks/${encodeURIComponent(b.dataset.task)}/run`, {method:'POST'}) ...
  catch (e) { alert('运行失败：' + e.message); }
  finally { b.disabled = false; b.textContent = '运行'; }
}
```

**按钮的三重保护**：

- **`b.disabled = true` + `b.textContent = '运行中…'`（点击时）** —— 防止重复点击（一次 RSS 抓取可能十几秒）
- **`encodeURIComponent(...)`（URL 构造）** —— 任务名含特殊字符时不会破坏 URL
- **`finally` 恢复按钮**（无论成功/失败）—— 失败后按钮不会永久禁用

**`alert(...)` 而不是 toast**：零依赖的直接选择。**代价**：阻塞式弹窗体验粗糙。

```javascript
setInterval(loadRuns, 5000);                                             // L117
```

**每 5 秒轮询 `/api/v1/runs?limit=20`** —— 这是整个控制台唯一的"实时性"机制。**代价**：每 5 秒一次 HTTP + SQLite 查询（即使开着多个标签页）。**对个人项目完全可接受**，但如果要做成生产级，这里应该是 SSE 或 WebSocket。

**三个 load 各自 `.catch`**（L114–116）—— `加载失败：` + 消息。**独立性很重要**：后端某个端点挂了不该让整页空白。

## 14.2 `app/cli.py`（379 行）(会话记录)

docstring（L1–13）列出九个用法：chat / tasks / run / index-notes / search / mcp-ls / team / research / collab。

### 共享的记忆装配（L39–47）

```python
def _build_memory(settings: Settings, llm=None):
    embedder = EmbeddingClient(settings)
    store = VectorStore(settings.memory_db_path)
    indexer = NotesIndexer(settings, embedder, store)
    return embedder, store, Retriever(embedder, store, llm=llm), indexer
```

**返回四元组**（embedder 也要返回，因为调用方要负责 close store）。**`llm=None` 是默认值** —— 意味着不改写/不重排（`retriever.py` 里三处 `self.llm is not None` 检查）。

**这不是 `main.py` 的复用** —— `main.py` 是模块级装配，CLI 是"每个子命令自己装配"。**两者的装配逻辑重复**（都是 embedder→store→indexer→retriever→registry），只是装配的位置不同。**这是"CLI 不 import app.main"的有意隔离**（CLI 不该在 import 时启动调度器、建 `data/warden.db`）。代价是重复。

### `_chat`（L50–80）

```python
def _chat(query: str, agent_name: str) -> int:
    settings = get_settings()
    store = RunStore(settings.db_path)
    mem_embedder, mem_store, retriever, indexer = _build_memory(settings, llm)   # (llm 先建)
    try:
        registry = build_default_registry(retriever=retriever, top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score, indexer=indexer,
            rewrite=settings.rag_rewrite_enabled, rerank=settings.rag_rerank_enabled)
        agent_cls = {"react": ReactAgent, "planact": PlanActAgent,
                     "function_call": FunctionCallAgent}[agent_name]              # L63–65
        agent = agent_cls(settings, llm, registry, store=store)
        result = agent.run([{"role": "user", "content": query}])                 # L67
        print(f"[{result.agent}|{result.model}] {result.answer}")                 # L69
        if result.self_eval: ...
        for s in result.steps:
            label = s.action or "(answer)"
            print(f"  step {s.index}: {s.thought or ''} -> {label} -> {s.observation}")   # L75
        return 0
    finally:
        # 跑挂也要把连接关掉，否则 CLI 退出前会漏 sqlite 句柄
        store.close(); mem_store.close()                                          # L79–80
```

**`agent_cls` 用字典分发**（L63–65）—— 与 `schemas.py:17` 的 `Literal` 三选一**重复了同一份信息**。如果新增一种循环，要改三处（schemas、main、cli）。**这是"插件化未打通"的具体表现**（对比 `ToolRegistry` 的"零改动"）。

**`finally` 里 close 两个 store（L79–80）** —— 注释明说理由。**`try/finally` 而不是 `with`**，因为要 return 值。**注意 `mem_store` 由 `_build_memory` 创建、`store` 由 `_chat` 创建** —— 两个不同的所有者，都在这里关。这是一个"所有权分散"的小代价。

**输出格式 `[{agent}|{model}] {answer}`** 与每步一行 `step {index}: {thought} -> {action} -> {observation}` —— `tests/test_cli.py` 对这些格式有精确断言（`"[react|script] 4"`、`"step 0: 算一下 -> calculator -> 4"`）。**格式的稳定性是被测试保证的。**

### `_run_task`（L90–105）

```python
def _run_task(name: str) -> int:
    settings = get_settings()
    notifier = get_notifier(settings)
    store = RunStore(settings.db_path)
    ctx = Services(settings, LLMClient(settings), notifier, store=store)
    try:
        result = run_once(_task_registry, ctx, name)     # 实际用 build_default_task_registry(settings)
        print(f"[{result.task}] {result.status}: {result.summary}")
        return 0 if result.status != "error" else 1       # L100
    except TaskNotFoundError as exc:
        print(f"错误：{exc}")
        return 1                                          # L103
    finally:
        # 「未知任务」这条早退路径原来会漏掉 store.close()，留下一个 sqlite 句柄
        store.close()                                     # L105
```

**L102 的注释记录了一个真实的 bug 修复**：

> 「未知任务」这条早退路径原来会漏掉 store.close()，留下一个 sqlite 句柄

**这是 `try/finally` 相对于"在每个 return 前 close"的价值证明** —— 早退路径（L101–103 的 `except`）是最容易漏的地方。**注释保留了这个教训，防止未来有人把 `finally` 改回"在 return 前 close"。**

**`return 0 if result.status != "error" else 1`（L100）** —— `ok` 与 `skipped` 都返回 0。**`skipped` 是成功**（"没有新条目"不是失败）。**`/api/v1/runs` 里的 status 会显示 skipped，但退出码是 0。**

### 其余子命令

| 命令 | 行 | 关键行为 |
|---|---|---|
| `_list_tasks` | L83–87 | `f"- {t.name}: {t.description}  (schedule={t.schedule})"` |
| `_index_notes` | L108–119 | 未设 `notes_dir` → `print("未设置 WARDEN_NOTES_DIR，无法索引笔记（在 .env 里配置后重试）。")` + 返回 1 |
| `_search_notes` | L122–138 | 空结果 → `print("没有在个人笔记里找到相关内容。")`（**与工具返回的字符串一致**）|
| `_mcp_list` | L141–154 | 未配置 → 返回 1；否则 finally 关所有 client |
| `_team` | L157–208 | researcher（RAG + MCP）与 writer（纯生成）两个 ReactAgent 定制 name/description |
| `_research` | L211–260 | researcher + critic（`ToolRegistry()` 空工具）→ CriticPipeline |
| `_collab` | L263–323 | Blackboard + researcher（写工具）+ writer（只读工具）|

**`_search_notes` 的空结果字符串与 `search_notes.py:26` 的工具返回完全一致**（`"没有在个人笔记里找到相关内容。"`）—— **同一个用户可见的消息在两处硬编码**。这是重复，但保证了"CLI 搜索"与"Agent 搜索"的体验一致。

**`_team` 里 MCP 加载失败只 warn（L165–167 附近）**：

```python
try:
    mcp_registry, mcp_clients = build_mcp_registry(settings.mcp_servers)
    ...
except Exception as exc:
    print(f"[warn] MCP 工具加载失败，跳过：{exc}")
```

**注意 `mcp_registry` 是"加到"默认注册表上的** —— 需要把 MCP 工具注册进既有 registry。**但 `ToolRegistry.register` 会在重名时抛 `ValueError`** —— 所以 MCP 工具名与内置工具冲突会导致这里抛异常，被 `except` 捕获并 warn 跳过**整个 MCP 注册表**。**这是一个真实的边界**（第 17 章）。

`tests/test_cli.py:299` 用 `RuntimeError("cannot spawn")` 验证 warn 路径。

### `main` 与 argparse（L326–379）

```python
def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="warden", description="Warden CLI")
    sub = parser.add_subparsers(dest="command", required=True)          # L330
    ...
    if __name__ == "__main__":
        raise SystemExit(main())
```

**`required=True`** 让无子命令时报错退出（`tests/test_cli.py:176` 断言 `SystemExit`）。

**`raise SystemExit(main())`（L379）** 让退出码从 `main()` 的返回值传播 —— **而不是 `sys.exit(main())`**。两者等价，但 `raise SystemExit` 更明确地表达"这是正常退出"。

**`argv: list[str] | None = None`** 让测试能传参（`cli.main(["chat", "..."])`），生产时 `argparse` 自动读 `sys.argv`。

**九个 subparser** 全部用 `add_subparsers`：`chat`（位置参数 `query` + `--agent` choices）、`tasks`、`run`（位置参数 `task`）、`index-notes`、`mcp-ls`、`team`/`research`/`collab`/`search`（各带 `query`）。

**`--agent` 用 `choices=["react", "planact", "function_call"]`** —— **第三处重复**（schemas、main、cli 的字典、这里）。`tests/test_cli.py:171` 用 `--agent bogus` 断言 `SystemExit`（argparse 的 choices 校验）。

---

# 第 15 章 脚本：冒烟与三个评测

`scripts/` 下 5 个文件、913 行，**全部是"跑真实模型"的端到端脚本，不在 CI 里跑**（CHANGELOG 明说评测"跑真实模型，不在 CI"）。

**五个脚本的共同模式**：

| 模式 | 实现 |
|---|---|
| 独立可运行 | `python -m scripts.xxx` 或 `python scripts/xxx.py` |
| sys.path 兜底 | `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` |
| 显式 utf-8 子进程 | `encoding="utf-8", errors="replace"` |
| 结果落 JSON | `--out data/eval/xxx.json` |
| 用环境变量配置 | `Settings()` 读 `WARDEN_*` |
| `ROOT` 常量 | `Path(__file__).resolve().parents[1]` |

## 15.1 `scripts/smoke.py`（128 行）(会话记录)

docstring（L1–8）：

```
端到端冒烟：用真实 LLM 跑通全部 Agent 模式，验证整条链路（非离线单测）。
覆盖：LLM 探活、react / planact / function_call 三种循环、research 接力、team 路由。
用法（需 LLM 在线，默认 http://localhost:11434/v1，模型见 .env）：python -m scripts.smoke。
退出码：0 = 全部通过；1 = 至少一项失败（或 LLM 不可达）。
```

```python
def _check(name: str, fn) -> bool:                                        # L31
    try:
        answer = fn()
    except Exception as exc:
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return False
    text = (answer or "").strip()
    if not text:
        print(f"[FAIL] {name}: empty answer")                             # L40
        return False
    print(f"[ ok ] {name}: {text[:80]}")
    return True
```

**三重判定**：抛异常 → FAIL；**空答案 → FAIL**；否则 PASS（只打印前 80 字符）。**"空答案"这一条很重要** —— 一个返回 `""` 的循环在"没有异常"的意义上是"成功"的，但实际是失败的。**这是一个"冒烟测试必须检查非空输出"的正确判断。**

`main()` 的顺序体现了依赖关系：

1. 打印 LLM 地址与模型（先让人看到配置）
2. **探活一次最小 chat**（`"Reply with the single word: ok"`）—— 失败即 `return 1`（**快速失败，不做后续浪费**）
3. 建 embedder/`VectorStore`/`Retriever` + `build_default_registry(retriever=...)`（**注意没传 llm，所以 smoke 不测改写/重排**）
4. 三种循环（共享 `store`）
5. research（`CriticPipeline`）
6. team（`Orchestrator`）
7. `store.close()` + `mem_store.close()`
8. `print("\n" + ("ALL PASS" if all(results) else "SOME FAILED"))` + 返回 0/1

**"探活优先"避免了"LLM 没起来但脚本跑了 6 个 case 全部失败"的噪音输出。**

**`Retriever(embedder, mem_store)` 不传 llm** —— 所以 smoke 测的是**纯向量检索**这条默认路径（与 `config.py` 的 `rag_rewrite_enabled=False`/`rag_rerank_enabled=False` 默认一致）。**这是一个"测默认配置"的合理选择**，但也意味着改写/重排路径**没有冒烟覆盖**（只有 `eval_retrieval.py --compare` 覆盖）。

## 15.2 `scripts/eval_resume.py`（304 行）(会话记录)

docstring（L1–18）是全项目写得最清楚的实验说明：

```
崩溃恢复评测：跨进程硬崩（os._exit）后 resume，统计恢复成功率。

崩溃点固定在「工具已经执行并落库、正要进入下一轮模型调用之前」：
此时上下文里已经出现 ``Observation: ...``（见 ``app/agent/react.py`` 的 ``_loop``），
说明至少有一个 ``AgentStep`` 已经写进 SQLite。

用 ``os._exit`` 直接杀进程 —— 不跑 finally、不落 ``_finish_error``，
留下的就是 ``running`` 记录，跟真实断电/被 kill 等价。

于是评测回答的是：半成品落库后进程硬崩，另起进程 resume 能不能接着跑完、
会不会重复执行已完成的步骤。
```

**"跟真实断电/被 kill 等价"是选择 `os._exit` 的全部理由** —— 它绕过 Python 的所有清理机制（`finally`、`atexit`、缓冲刷新）。**用 `sys.exit()` 或抛异常都做不到这一点**（它们会触发 `finally` → `_finish_error` → 状态变成 `error` 而不是 `running`）。

```python
TASKS: dict[str, tuple[str, str]] = {                                     # L41
    "a": ("用计算器算 3 * 4，只回答数字。", "12"),
    "b": ("先用计算器算 12 * 12，再把上一步的结果加上 100，最后只回答数字。", "244"),
    "c": ("先用 get_current_time 查一次当前时间，再用计算器算 9 * 9，最后只回答那个乘法结果。", "81"),
}

CRASH_POINTS: dict[str, list[int]] = {"a": [1], "b": [1, 2], "c": [1, 2]}   # L53
CRASH_EXIT = 70
MARKER = "EVAL_RESULT "
```

**三个任务的形态设计**（覆盖不同的步骤链）：

| 任务 | 形态 | 崩溃点 | 验证的 |
|---|---|---|---|
| a | 单步计算 | [1] | 崩在第 1 步之后 |
| b | 两步算术链 | [1, 2] | 崩在链条中间 |
| c | 工具 + 计算链（两个不同工具）| [1, 2] | 跨工具类型的链条 |

**每个任务是 (提示词, 期望答案) 二元组** —— 期望答案是**字面量**（`"12"`/`"244"`/`"81"`），用于程序化判定。**任务 b 的 244** 是一个刻意的设计：`12 * 12 = 144`，`144 + 100 = 244` —— 所以答案依赖**第一步的观测**，resume 如果丢了第一步的结果就答不对。**这让"恢复成功"不只是"跑完了"，而是"正确接着跑完了"。**

```python
class CrashLLM:                                                           # L56
    """上下文里已带 N 条 Observation 时，在下一次 chat 之前硬杀进程。
    其余调用与属性全部委托给真实客户端。"""

    def __getattr__(self, name):
        return getattr(self._inner, name)                                 # 委托

    @staticmethod
    def _observations(messages) -> int:                                   # L66
        return sum(1 for m in messages
                   if str(m.get("content", "")).startswith("Observation:"))   # L68

    def chat(self, messages, temperature=None):                           # L70
        self.calls += 1
        if self._observations(messages) >= self._crash_at:                 # L74
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH_EXIT)                                          # L78
        return self._inner.chat(messages, temperature)
```

**`__getattr__` 委托（L59–60）** 让 `CrashLLM` 无须实现全部接口（`model`/`last_usage`/`chat_with_tools`…）。**这是一个很实用的"部分 mock"技巧** —— 包装真实对象、只覆盖需要拦截的方法。

**`_observations` 的判定依据 `"Observation:"` 前缀（L68）** —— 因为 React 的 `_loop` 回填观测用的是 `f"Observation: {observation}"`（`react.py:222`）。**这个字符串是 CrashLLM 与 ReactAgent 之间的一份隐式契约** —— 如果 `react.py` 改了观测格式，这个评测会静默失效（永远不崩 → 所有 trial 被剔除为 `no_crash`）。**这是一个未文档化的耦合点**（第 17 章）。

**`flush` 之后 `os._exit`（L76–78）** —— **flush 是必需的**：否则已经 print 的内容（进度信息）会留在缓冲区里丢失。

```python
def build_agent(settings, llm, db_path):
    store = RunStore(db_path)
    return ReactAgent(settings, llm, build_default_registry(), store=store), store
```

**`build_default_registry()` 无参** → **只有 calculator + get_current_time，没有 `search_notes`/`save_note`**。这是**刻意的实验隔离**：评测不应该依赖笔记索引是否建好。

```python
def run_child(argv, timeout):
    return subprocess.run([sys.executable, __file__, *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout)                                                  # L129–138
```

**`encoding="utf-8", errors="replace"`** —— **同一个 Windows GBK 教训的第四次应用**（另三次：`repo_report`、`weekly_review`、`exp_fc_resume`）。**`[sys.executable, __file__]`** 保证子进程用同一个 Python 解释器跑同一个脚本。

```python
def parse_payload(stdout: str) -> dict | None:                            # L141
    for line in reversed(stdout.splitlines()):                            # L143
        if line.startswith(MARKER):
            try:
                return json.loads(line[len(MARKER):])
            except json.JSONDecodeError:
                return None
    return None
```

**从末行往前找 MARKER（L143）** —— 因为 `os._exit` 前可能有别的输出，而真正的 payload 是最后一行。**"从后往前"是对"进程被硬杀，输出可能不完整"的防御。**

```python
def duplicate_steps(steps: list[dict]) -> int:                            # L148
    seen = set()
    dup = 0
    for step in steps:
        if step.get("action") is None:                                    # L153  跳过推理步
            continue
        key = json.dumps([step.get("action"), step.get("action_input")],
                         sort_keys=True, ensure_ascii=False)              # L155
        if key in seen:
            dup += 1
        seen.add(key)
        ...
```

**去重键 = `(action, action_input)` 的规范化 JSON（L155）** —— `sort_keys=True` 让参数顺序无关。**"跳过推理步"（L153）** 因为纯推理步的 `action` 是 `None`，多个推理步会被误判为重复。

**`orchestrate` 的编排逻辑**：

```python
combos = [...]                     # tasks × CRASH_POINTS 轮转
for index in range(args.trials):
    task, crash_at = combos[index % len(combos)]
    run_id = f"eval-{uuid.uuid4().hex[:12]}"                              # 每次唯一
    # crash 子进程
    if code != CRASH_EXIT:
        entry["outcome"] = "no_crash"                                     # 剔除
        continue
    # 读 DB
    entry["steps_before_resume"] = len(record.steps)
    if entry["steps_before_resume"] < 1:
        entry["outcome"] = "no_partial_state"                             # 剔除
        continue
    # resume 子进程
    ok = (code == 0 and entry["status_after_resume"] == "ok"
          and expected in answer and entry["duplicate_steps"] == 0)       # 四重判定
```

**四个剔除/判定条件**：

| 条件 | 含义 |
|---|---|
| `code != CRASH_EXIT` → `no_crash` | 没崩（说明 crash_at 设置得比实际步数大）—— **不算实验，剔除** |
| `steps_before_resume < 1` → `no_partial_state` | 崩在任何步骤落库之前 —— **不是"半成品续跑"，剔除** |
| `status_after_resume != "ok"` | resume 没成功 |
| `expected not in answer` | 答案不对 |
| `duplicate_steps != 0` | 重复执行了步骤 |

**`expected in answer`（包含而非相等）** —— 因为模型的回答可能是 `"答案是 12"`。**这是一个宽容的匹配**（对"只回答数字"的提示词，模型通常给纯数字）。

**两个 `no_crash`/`no_partial_state` 的剔除是评测设计的关键**：它们保证分母只包含"真的发生了半成品崩溃"的试验。**报告里分开记录这三种计数（`crashed`/`no_crash`/`no_partial_state`）让剔除过程可见** —— 而不是悄悄丢掉。

**报告 JSON 的字段**：`model`/`endpoint`/`trials`/`crashed`/`skipped`/`no_crash`/`no_partial_state`/`resumed_ok`/`resume_success_rate`/`steps_before_resume`（去重排序后的集合）/`details`（每个 trial 的完整记录）。

**`steps_before_resume` 用 `sorted({...})`** —— 集合去重后排序，把"崩在第 1 步"与"崩在第 2 步"变成 `[1, 2]` 这样的可读摘要。

**CLI 的 `--reset`/`--keep-db` 用 `action="store_true"` + `dest="reset"`（L278–300）**：

```python
parser.add_argument("--reset", action="store_true", default=True)
parser.add_argument("--keep-db", dest="reset", action="store_false")
```

**这是 argparse 里实现"默认开、可用反向 flag 关"的标准 idiom** —— 两个 flag 写同一个 dest，后者用 `store_false`。**默认 `reset=True`** 意味着每次评测从干净的库开始（避免上一次的 run 记录污染）。**这个 idiom 值得一提，因为它不直观。**

## 15.3 `scripts/eval_retrieval.py`（374 行）(会话记录)

docstring（L1–12）说明它需要 `/embeddings`（`--compare` 还需要 `/chat/completions`）。

### 语料设计是评测的核心（L33–221）

```python
CORPUS: list[tuple[str, str, str]] = [...]   # 20 篇 (doc_id, 标题, 正文)，共 ~145 行
# 注释："20 篇互不相同的笔记。主题刻意相邻（Python / SQLite / 容器 / 可观测 / 检索），
#        词汇互相干扰，避免「关键词撞上就算命中」"
```

**20 篇的 doc_id 与标题**：

| | | | |
|---|---|---|---|
| `async-io` 异步编程 | `packaging` 打包发布 | `type-hints` 类型注解 | `unit-test` 单元测试 |
| `sqlite-wal` SQLite 并发写 | `sqlite-plan` 查询计划 | `image-layer` 镜像分层 | `container-net` 容器网络 |
| `health-probe` 健康检查 | `metrics` 指标类型 | `struct-log` 结构化日志 | `git-rebase` 变基 |
| `git-hook` 提交钩子 | `idempotency` 幂等重试 | `semantic-cache` 语义缓存 | `embedding-choice` 嵌入模型 |
| `chunking` 文本切块 | `token-cost` 调用计费 | `zettel` 卡片笔记 | `sleep-log` 作息记录 |

**"词汇互相干扰"的设计**：`sqlite-wal` 与 `sqlite-plan` 都含 "SQLite"；`git-rebase` 与 `git-hook` 都含 "git"；`metrics` 与 `struct-log` 都涉及"可观测"。**所以"含关键词就命中"的策略会失败** —— 这迫使评测衡量**语义**检索而非关键词匹配。

**两篇特殊的文档**：

- **`idempotency` 正文含 "键必须由确定性的输入派生，用随机数或当前时间做键等于每次都新建一次操作"** —— **这是 Warden 自己的设计原则写进了测试语料**。
- **`semantic-cache` 正文含 "只对文字长度足够的请求启用，短问句的向量区分度太差"** —— **这句话预言了本评测的发现**（见下面 `zettel` 的分析）。
- **`zettel` 正文只有 3 句共约 70 字** —— **语料里最短的文档**，而它成了最高频的 MISS（见附录 A）。

```python
QUERIES: list[tuple[str, str]] = [...]   # 40 条 (释义化查询, 正确 doc_id)，共 ~43 行
# 注释："查询是释义化的说法，尽量不出现标题里的原词"
```

**每篇 2 条查询**（20 × 2 = 40）。**"释义化、不含标题原词"是关键设计** —— 比如 `("怎么让一个线程在等网络返回的时候去处理别的请求", "async-io")`（不含"异步"二字）、`("多个进程同时往一个单文件数据库写会报什么错", "sqlite-wal")`。

**`zettel` 的两条查询是理解失败模式的钥匙**：

- `("怎样记笔记以后才方便复用", "zettel")`
- `("收集了很多材料但是记不住怎么办", "zettel")`

**这两条查询的正确答案是那篇 70 字的短笔记，但它们都 MISS 了**（实测投给了 `sqlite-wal`/`image-layer`）—— 见附录 A 的"相似度磁铁"分析。

```python
def build(settings, db_path):
    if db_path.exists():
        db_path.unlink()                                                  # L226  每次重建
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = VectorStore(str(db_path))
    indexer = NotesIndexer(settings, embedder, store)
    for doc_id, title, text in CORPUS:
        indexer.index_text(f"# {title}\n\n{text}", doc_id)                 # L233  标题并入正文
    llm = LLMClient(settings)
    return indexer, Retriever(embedder, store, llm=llm), store
```

**`f"# {title}\n\n{text}"`（L233）** —— **标题被并入被嵌入的文本**（作为 Markdown H1）。这让标题里的词也参与语义（比如 `"SQLite 并发写"` 这五个字）。**这是一个建模选择**：如果不并入，标题完全不参与检索；如果单独存为一个字段，需要多向量检索。**并入是最简单的方案。**

**`db_path.unlink()`（L226）** —— 每次运行重建索引（否则旧 chunk 会累积）。**注意它只删 `.db` 文件，不删 WAL 附属文件**（`-wal`/`-shm`）—— 如果之前的连接没干净关闭，这些文件会残留。**实践中因为 `store.close()` 在末尾调用，所以通常没问题**（一个小的健壮性瑕疵）。

```python
def evaluate(retriever, settings, *, k, rewrite, rerank, verbose=False) -> dict:   # L237
    hits = {1: 0, 3: 0, k: 0}
    rr = 0.0
    ranks: list[int] = []
    latencies: list[float] = []
    misses: list[dict] = []
    for query, want in QUERIES:
        started = time.perf_counter()                                      # L246
        chunks = retriever.retrieve(query, k=k,
            min_score=settings.retrieval_min_score, rewrite=rewrite, rerank=rerank)
        latencies.append((time.perf_counter() - started) * 1000)           # L249
        found = [c.doc_id for c in chunks]                                 # L251
        rank = found.index(want) + 1 if want in found else 0               # L252
        ranks.append(rank)
        if rank == 1: hits[1] += 1
        if 0 < rank <= 3: hits[3] += 1
        if 0 < rank <= k: hits[k] += 1
        rr += (1.0 / rank) if rank else 0.0
        if rank == 0:
            misses.append({"query": query, "want": want, "got": found})    # L262
            if verbose:
                print(f"  MISS  {query!r}\n        want={want} got={found}")
    n = len(QUERIES)
    return {"queries": n, "k": k,
            "hit@1": hits[1] / n, "hit@3": hits[3] / n, f"hit@{k}": hits[k] / n,
            "mrr": rr / n,
            "latency_p50_ms": latencies[n // 2],
            "latency_p95_ms": latencies[min(n - 1, int(n * 0.95))],
            "ranks": ranks, "misses": misses}                              # L237–284
```

**四个指标的定义**：

- **`rank = found.index(want) + 1`（L252）** —— 正确文档在返回列表里的**1-based 位置**；不在列表里则 `rank = 0`。
- **Hit@K** = `0 < rank <= K` 的查询占比。**注意 `rank == 0`（未召回）不计入任何 K** —— 这是 `hit_at` 函数的语义（L287–291）。
- **MRR** = 所有查询的 `1/rank` 平均（`rank == 0` 时贡献 0）。**MRR 对"排名靠前"更敏感**，所以它能区分"Hit@1=40% 但都排第 3"与"Hit@1=40% 且其余都排第 2"。
- **延迟 p50/p95** 用 `time.perf_counter()`（单调时钟，不受系统时间调整影响）。

**`latency_p50_ms = latencies[n // 2]`（L280）** —— **这是"排序后取中间"的近似还是"原始顺序取中间"？** 代码是**原始顺序的中间元素**（没有 `sorted`）。所以这实际上是"第 20 条查询的延迟"，**不是真正的中位数**！`p95` 同理（`latencies[int(n*0.95)]`）。

**这是一个真实的实现缺陷**：变量名与文档说是 p50/p95，但实际是"按查询顺序的第 n/2 与第 0.95n 个样本"。**因为查询是按语料顺序排列的（每篇两条），而延迟与文档内容相关**（`--compare` 时不同查询触发不同的 LLM 改写/重排），**这个"假 p50"会系统性偏移**。

**实测佐证**：附录 A 里纯向量的 p50=88ms、p95=107ms —— 两者很接近（因为纯向量检索的延迟几乎与查询无关，都是"全表余弦"）。而改写+重排的 p50=4383ms、p95=5684ms —— **这里两者差异明显，说明确实与查询相关，那么"未排序取中间"就有真实误差**。**修法：`sorted(latencies)[n//2]`。** 这是第 17 章的一项，且有实测数据支持。

```python
def hit_at(ranks: list[int], cutoff: int) -> float:                        # L287
    """...rank 0 = 未召回，不计入任何截断点"""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if 0 < r <= cutoff) / len(ranks)

def hit_curve(ranks: list[int], cutoffs: list[int]) -> dict[str, float]:   # L294
    """Hit@K 曲线：检索一次 k=max(cutoffs)，各截断点从同一份排序里数出来。
    这样各点之间可比（没有重新检索带来的噪声），也省掉 K 倍嵌入开销。"""
    return {str(c): hit_at(ranks, c) for c in cutoffs}
```

**`hit_curve` 的 docstring 解释了为什么要从同一份排序里数**：

> 这样各点之间可比（没有重新检索带来的噪声），也省掉 K 倍嵌入开销。

**两个理由都很实在**：

1. **可比性** —— 如果为每个 K 单独检索一次，不同 K 之间的差异里会混入**检索的随机性/实现差异**（比如 ANN 索引的近似性）。从同一份排序里截断则差异**纯粹来自截断**。
2. **成本** —— 一次 `k=8` 的检索 vs 四次（k=1,3,4,8）检索。对本地嵌入模型，这是 4 倍的嵌入开销。

**`hit_curve` 的单调性（"截断点越大，命中率只能持平或上升"）是被测试保证的**（`tests/test_eval_retrieval.py:24` 断言值单调不降）—— **这是一个数学性质，也是"实现正确"的判据**。如果哪天 `hit_at` 写错了（比如 `r == cutoff` 而不是 `r <= cutoff`），单调性测试会立刻失败。

`main()` 的流程：

```python
cutoffs = [int(x) for x in args.sweep.split(",") if x.strip()]
eval_k = max([args.k, *cutoffs]) if cutoffs else args.k                       # 按最大截断点检索一次
plain = evaluate(retriever, settings, k=eval_k, rewrite=False, rerank=False, verbose=...)
report["vector_only"] = plain
if cutoffs:
    curve = hit_curve(plain["ranks"], cutoffs)
    report["k_sweep"] = curve
if args.compare:
    enhanced = evaluate(retriever, settings, k=args.k, rewrite=True, rerank=True)
    report["rewrite_rerank"] = enhanced
    plain_at_k = hit_at(plain["ranks"], args.k)                              # 同口径比较
    print(f"差值：Hit@1 {enhanced['hit@1'] - plain['hit@1']:+.1%}，"
          f"Hit@{args.k} {enhanced[f'hit@{args.k}'] - plain_at_k:+.1%}，"
          f"MRR {enhanced['mrr'] - plain['mrr']:+.3f}")
```

**`plain_at_k = hit_at(plain["ranks"], args.k)`（对比时）是必需的** —— 因为 `plain` 是按 `eval_k`（可能是 8）检索的，而 `enhanced` 是按 `args.k`（可能是 4）。**直接比较 `plain[f"hit@{args.k}"]` 会 KeyError**（`plain` 的字典里只有 `hit@8`）。用 `hit_at` 从 `ranks` 里重算 `hit@4` 保证两边的口径一致。**这是一个容易被忽略的正确性细节。**

## 15.4 `scripts/eval_router.py`（132 行）(会话记录)

docstring（L1–13）说明了它存在的必要性：

```
Warden 的 LLMClient 与 EmbeddingClient 共用 settings.llm_base_url，
所以本地用 llama.cpp 跑「对话」和「嵌入」两个进程时，
需要一个入口把两者拼起来：
  python -m scripts.eval_router --chat http://127.0.0.1:11500 --embed http://127.0.0.1:11501
之后 WARDEN_LLM_BASE_URL=http://127.0.0.1:11434/v1 就能同时走 chat 与 embeddings。
用 Ollama 时不需要它——11434 一个端口本来就同时提供两者。
转发一律绕开系统代理（ProxyHandler({})）：目标全是本机，
而 Windows 上的系统代理会把 127.0.0.1 也劫持成 502 空 body（原因与解法见 app/net.py）。
```

**这段 docstring 精确说明了这个脚本的适用边界**："用 Ollama 时不需要它"。它是**为 llama.cpp 双进程场景写的适配器** —— 因为 llama.cpp 的 `llama-server` 一个进程要么是 chat 要么是 embeddings（`--embeddings` 标志），不能同时提供两者。

**"转发一律绕开系统代理"** 是 `app/net.py` 那个坑在脚本里的第二次处理 —— 但这里用的是 `urllib`（stdlib）而不是 `httpx`，所以不能复用 `httpx_env_kwargs`。**解法不同、原因相同。**

```python
_SKIP_HEADERS = frozenset({"host", "content-length", "connection",
                           "transfer-encoding", "accept-encoding"})        # L24–26
_TIMEOUT_S = 900.0                                                          # L27
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # L29
```

**`_SKIP_HEADERS` 里的五个头是"逐跳头"（hop-by-hop headers）**，按 HTTP 规范**不应被代理转发**：

| 头 | 不透传的理由 |
|---|---|
| `host` | 目标主机变了（从 router 变成上游），必须重写 |
| `content-length` | 体长度可能变化（如果做了任何处理）|
| `connection` | 连接管理是逐跳的 |
| `transfer-encoding` | 传输编码是逐跳的 |
| `accept-encoding` | **最微妙**：如果透传客户端的 `accept-encoding: gzip`，上游可能返回 gzip，而 router 只做字节转发不参与解压 —— 所以透传实际上是安全的。不透传则上游返回明文，router 也返回明文。**不透传更简单可控** |

**`_TIMEOUT_S = 900.0`（15 分钟）** —— 覆盖本地 CPU 推理一个大 prompt 的最坏情况。**注意它比 `config.py` 的 `llm_timeout_s=120.0` 大得多** —— 因为 router 是"转发层"，它不该比客户端更早超时（否则客户端看到的是 router 的超时而非上游的响应）。**但 900 > 120 意味着客户端的 120s 超时总是先触发** —— 所以 router 的这个值实际不会生效（对 httpx 客户端而言）。**这是"防御性地设一个很大值"的合理做法。**

```python
def make_handler(chat_url: str, embed_url: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"                                     # 必须显式设置
        server_version = "warden-eval-router"

        def _reply(self, status, payload, headers=None):                   # L44
            self.send_response(status)
            if headers is None:
                self.send_header("Content-Type", "application/json")
            else:
                for k, v in headers.items():
                    if k.lower() not in _SKIP_HEADERS:
                        self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)                                     # L57

        def _forward(self, base: str) -> None:                            # L59
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""             # L61
            req = urllib.request.Request(base + self.path, data=body or None,
                                         method=self.command)
            for k, v in self.headers.items():                             # L64
                if k.lower() not in _SKIP_HEADERS:
                    req.add_header(k, v)
            try:
                with _OPENER.open(req, timeout=_TIMEOUT_S) as resp:       # L68
                    self._reply(resp.status, resp.read(), dict(resp.headers))
            except urllib.error.HTTPError as exc:
                # 上游的 4xx/5xx 原样返回，别把客户端的判断吞掉。
                # HTTPError 自带一个要关闭的 body 流，不关会在解释器退出前
                # 触发 ResourceWarning（Implicitly cleaning up ...）
                try:
                    payload = exc.read()                                  # L73
                finally:
                    exc.close()                                           # L75
                self._reply(exc.code, payload)
            except Exception as exc:
                payload = f'{{"error": "router: {type(exc).__name__}: {exc}"}}'.encode()
                self._reply(502, payload)                                 # L80
```

**`protocol_version = "HTTP/1.1"`（L39）是必需的** —— `BaseHTTPRequestHandler` 默认是 `HTTP/1.0`，那意味着**每个请求一个连接**（`Connection: close`）。httpx 客户端的连接复用会失效，且 keep-alive 场景会异常。**这是 `http.server` 的一个经典陷阱。**

**`_reply` 里 `Content-Length` 是手工算的（L55）** —— 因为要支持 `headers=None`（本地生成的响应）与透传上游头两种路径。**HTTP/1.1 必须有 Content-Length 或 chunked**，否则客户端会一直等。

**`body = self.rfile.read(length) if length else b""`（L61）** —— **必须按 Content-Length 精确读取**，不能 `read()` 到底（那会阻塞到连接关闭）。**这是"实现 HTTP 转发"最容易写错的地方。**

**`except urllib.error.HTTPError` 的 `try/finally: exc.close()`（L72–75）** —— **CHANGELOG 记录了这个 ResourceWarning 的修复**：

> eval_router body 流（ResourceWarning）

`urllib.error.HTTPError` **本身是一个 response 对象**，即使你只读 `exc.code` 也要关闭它，否则它的 body 流会在解释器退出时才被 GC 回收，产生 `ResourceWarning: Implicitly cleaning up`。**这是一个非常隐蔽的资源泄漏**（异常路径上的资源常常被忽略）。

**"上游的 4xx/5xx 原样返回，别把客户端的判断吞掉"（L70）** —— **这是一个重要的代理设计原则**：代理不该把上游的错误"翻译"成自己的错误（比如统一的 502）。**原因**：调用方可能依赖具体的状态码（比如 `_post` 依赖 409 来触发幂等重试逻辑！）。**如果 router 把 409 转成 502，整个 M6 幂等机制在这个路由下就会失效。** 这条注释体现了一个真实的正确性考量。

**`_reply(exc.code, payload)` 不带 headers**（L76）—— 所以错误响应只有 `Content-Type: application/json`。上游错误响应里的 `Retry-After` **会丢失**！**这是一个真实的功能缺口**：通过 router 访问 InferGate 时，`_retry_after_s` 永远拿不到 `Retry-After`，会回落到 `gateway_retry_after_s`（默认 1.0s）。**对评测/本地使用够用，但值得记录**（第 17 章）。

```python
        def do_POST(self):                                                # L84
            if self.path.startswith("/v1/embeddings"):
                return self._forward(embed_url)
            if self.path.startswith("/v1/chat/completions"):
                return self._forward(chat_url)
            self.send_error(404, "unknown path")

        def do_GET(self):                                                 # L92
            if self.path == "/v1/models":
                return self._forward(chat_url)
            if self.path in ("/health", "/healthz"):
                return self._reply(200, b"ok")                            # L96
            self.send_error(404, "unknown path")
```

**路由规则四行**：

| 请求 | 转发到 |
|---|---|
| `POST /v1/embeddings*` | embed |
| `POST /v1/chat/completions*` | chat |
| `GET /v1/models` | chat |
| `GET /health` / `/healthz` | **本地回 `ok`**（不转发）|

**`startswith` 而非 `==`** —— 容忍尾部斜杠/查询串（`/v1/embeddings?x=1`）。

**`/health` 本地应答（L96）是刻意的** —— 让"路由器活着"与"上游活着"成为两个可分别探测的事实。`tests/test_eval_router.py:118` 断言 `/health` 返回 `(200, "ok")` **且两个上游都没收到请求**。

**`/v1/models` 转发到 chat 而不本地伪造** —— 因为模型列表需要上游的真实信息。**注意 llama-server 没有 `/health`，但有 `/v1/models`**（这是我在本会话实测发现的：探 `/health` 会得到 502，探 `/v1/models` 才能确认服务活着）。

```python
def main(argv=None):
    ...
    server = http.server.ThreadingHTTPServer((args.host, args.port),
                                             make_handler(args.chat, args.embed))   # L124
    print(f"router on http://{args.host}:{args.port}  chat={args.chat}  embed={args.embed}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
```

**`ThreadingHTTPServer` 而不是 `HTTPServer`** —— 单线程的 `HTTPServer` 会让一个慢请求阻塞其他所有请求（对"chat 慢、embed 快"的场景是致命的）。**`ThreadingHTTPServer` 为每个请求开一个线程。**

**`flush=True`（L128）** —— 让启动信息在重定向到文件时立刻可见（不 flush 会缓冲）。**在本会话里，三个后台服务作业的启动日志都需要这个才能及时看到。**

## 15.5 `scripts/exp_fc_resume.py`（137 行，本会话新增）(会话记录)

这是我为验证"FunctionCallAgent 的 resume 会不会重复注入上下文"而写的实验脚本。**它的价值在于覆盖了一个 `tests/` 里没有的路径**（`tests/test_resume.py` 只测 React 与 PlanAct）。

```python
CRASH_EXIT = 70
DB = "data/exp_fc_resume.db"
PROMPT = "用计算器算 3 * 4，再用计算器算 5 * 6，最后只回答第二个结果。"     # L36

class CrashLLM:                                                           # L39
    def chat_with_tools(self, messages, tools):
        self.calls += 1
        if self.calls >= self._crash_at:
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(CRASH_EXIT)                                          # L54

class SpyLLM:                                                             # L59
    def chat_with_tools(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages, default=str)))    # L64  深拷贝快照
        return self._inner.chat_with_tools(messages, tools)
```

**`SpyLLM` 的深拷贝（L64）** 是关键：`messages` 是**同一个 list 引用**（`FunctionCallAgent._run` 把它存进 `record.meta` 后不断 append），所以如果只存引用，快照会在后续循环里被改变。**`json.loads(json.dumps(...))` 是一个简单可靠的深拷贝**（代价：非 JSON 类型被 `default=str` 字符串化，但 messages 里全是 JSON 类型）。

**`--crash-at` 默认 2** —— 因为 `chat_with_tools` 的调用次数与 `chat` 不同（一次调用可能产生多个 tool_call，但只算一次 LLM 调用）。

`phase_resume` 的三段输出：

```python
# ① 打印 DB 里 meta["messages"] 的形状
for i, m in enumerate(meta.get("messages", [])):
    extra = f" tool_calls={[...]}" if m.get("tool_calls") else ""
    print(f"  meta[{i}] {m.get('role','?'):<9}{extra} content={str(m.get('content'))[:80]!r}")

# ② resume 并记录实际发给模型的消息
llm = SpyLLM(LLMClient(settings))
result = _agent(llm, store).resume(args.run_id)
print(f"resume 实际发给模型的第 1 次请求含 {len(llm.seen[0])} 条消息")

# ③ 重复检测
tool_msgs = [m for m in llm.seen[0] if m.get("role") == "tool"]
obs_msgs = [m for m in llm.seen[0] if m.get("role") == "user"
            and str(m.get("content", "")).startswith("Observation:")]      # L120–124
print(f"  其中 role='tool' {len(tool_msgs)} 条，role='user' 且以 Observation: 开头 {len(obs_msgs)} 条")
```

**`obs_msgs` 的检测（L120–124）是"重复注入"的判据**：如果 resume 走了 React 风格的"从 steps 重建消息"路径，就会出现 `role="user"` + `"Observation: ..."` 的消息（重复）。**实测 0 条** → 证明确实走的是 `meta["messages"]` 路径。

**这个脚本是一个"用实验证伪一个假设"的范例**：假设是"FunctionCall 的 resume 会重复注入"（因为 `_resume` 里也有从 `record.steps` 重建 `steps` 的逻辑），实验结论是**不会**（因为落库时 `raw=None` 且消息来自 meta）。**第 7.3 节的完整分析就建立在这个实验上。**

---

# 第 16 章 测试套件：34 文件逐项

> 本节的行号与用例清单来自本会话对 `tests/` 全部 34 个文件的只读清点（子代理任务，未修改任何文件）。
> **行数口径确认**：`Get-Content | Measure-Object -Line` 跳过空行因而系统性少算，read 工具的口径才是真实文件行数。
> **`tests/` 无 `conftest.py`** —— 全部 fixture 都在各自文件内定义。

## 16.1 逐文件清单

### `tests/test_api.py`（165 行）

**导入**：`datetime`(L3)、`TestClient`(L5)、`import app.main as main`(L7)、`Settings`(L8)、`STATUS_OK`(L9)。

**辅助**：`class FakeAgent` L12–35（计数 calls，`run()` 返回 `_Result(answer="4", steps=[_Step()], self_eval={"confidence":0.9})`）；`class FakeRecord` L38–47（`id="run-1"`、`status=STATUS_OK`、`output="缓存的回答"`）。

10 个用例：

| 行 | 用例 | 断言 |
|---|---|---|
| L50 | `test_health_reports_model_tools_and_tasks` | `status=="ok"`、`"calculator" in tools`、`"news_digest" in tasks` |
| L56 | `test_metrics_endpoint_serves_prometheus_text` | 200 + `"text/plain" in content-type` |
| L62 | `test_metrics_endpoint_404_when_disabled` | `Settings(metrics_enabled=False)` → 404 |
| L68 | `test_runs_endpoint_formats_timestamps` | `started_at == datetime.fromtimestamp(1_700_000_000.0).isoformat(timespec="seconds")`、`finished_at is None` |
| L78 | `test_chat_endpoint_maps_steps` | `monkeypatch.setitem(main._agents,"react",agent)`；`steps[0]["action"]=="calculator"`；`self_eval=={"confidence":0.9}`；`agent.calls==1` |
| L95 | `test_chat_endpoint_replays_cached_answer` | 同 `request_id` → `answer=="缓存的回答"`、`steps==[]`、**`agent.calls==0`** |
| L117 | `test_tasks_endpoint_lists_registry` | 三名任务且都有 `schedule` |
| L123 | `test_run_task_endpoint_returns_result` | `summary=="3 条新资讯"` |
| L138 | `test_run_task_endpoint_404_for_unknown_task` | 404 + detail 含 `"unknown task: nope"` |
| L149 | `test_lifespan_starts_and_stops_scheduler` | **`with TestClient(main.app)` 真跑 lifespan** → `events==["start","shutdown"]` |

**最有价值的三个**：幂等回放断言 `agent.calls==0`（证明**没调 LLM**）、lifespan 真跑、时间戳格式。

### `tests/test_blackboard.py`（98 行）

**辅助**：`class FakeLLM` L10–17。

| 行 | 用例 | 断言 |
|---|---|---|
| L20 | `test_blackboard_write_read_snapshot` | `read("missing") is None`、`read("missing","d")=="d"`、`snapshot()=={"a":"1"}` |
| L30 | `test_blackboard_tools_roundtrip` | 写读往返；缺失键含 `"empty"` |
| L41 | `test_blackboard_team_researcher_writes_writer_reads` | **`bb.read("findings")=="the answer is 42"`**、`len(result.steps)==4`、`steps[2].action=="blackboard_read"` |
| L91 | `test_blackboard_team_empty_raises` | try/except/**else-AssertionError** 模式；ValueError 含 `"at least one member"` |

### `tests/test_cli.py`（311 行）—— 最大的 CLI 测试

**辅助**：`FakeEmbedder` L21–28、`ScriptLLM` L31–49（**耗尽后返回 `'{"thought":"done","final_answer":"42"}'`**）、`_Step`/`_Result`/`_TaskResult`、四个团队 stub（L245–276）。

**`cli_env` fixture（L71–87）三招**：

```python
monkeypatch.chdir(tmp_path)                              # ① 不读仓库 .env
monkeypatch.setattr("app.config._settings", None)        # ② 打破 settings 单例
monkeypatch.setattr(cli, "EmbeddingClient", FakeEmbedder)  # ③ 拦截外部 I/O
```

18 个用例，关键几条：

| 行 | 断言 |
|---|---|
| L109 | ok→0 且 `"[news_digest] ok: 3 条新资讯"`；error→1 且 `"error: 炸了"` |
| L124 | `"[react\|script] 4"` |
| L129 | `"step 0: 算一下 -> calculator -> 4"` 与 `"[self-eval] confidence=0.9  工具确认过"` |
| L171 | `--agent bogus` → `SystemExit` |
| L217 | `"- mcp_read: mcp_read 工具"` 且 **`closed == ["client"]`** |
| L279/285/291 | `"[orchestrator -> researcher] 4"` / `"[critic] 4"` / `"[writer] 4"` |
| L299 | MCP 启动失败 → `"[warn] MCP 工具加载失败"` + `"cannot spawn"` |

### `tests/test_config.py`（92 行）

**autouse fixture `_clear_warden_env`（L16–20）删掉所有 `WARDEN_*`** —— 保证测试不受开发者 shell 影响。

| 行 | 断言 |
|---|---|
| L23 | 默认值全量：`llm_model=="qwen2.5:7b"`、`agent_max_steps==10`、`metrics_enabled is True`、`mcp_servers==[]`、`rss_source_list==[...]` |
| L40 | 环境变量前缀 + 类型强转 |
| L56 | `WARDEN_MCP_SERVERS` JSON 解析 |
| L66 | **空值回落默认**（`env_ignore_empty=True` 的验证）|
| L78 | **`WARDEN_AGENT_MAX_STEPS="很多步"` → ValidationError 且 `"agent_max_steps" in str(exc)`** |
| L85 | `frozen=True` 赋值抛 ValidationError |
| L91 | `get_settings() is get_settings()` |

### `tests/test_context.py`（77 行）

| 行 | 断言 |
|---|---|
| L18 | 未超限原样返回且**不消耗 LLM** |
| L26 | 超限后 `len(out)==3`、`out[0]["content"].startswith("[Earlier conversation summary]")` |
| L44 | 摘要失败退化为 `[history[1], history[2]]` |
| L62 | `run()` 集成：先压缩再进循环 |

### `tests/test_cost.py`（51 行）

`compute_cost(Settings(llm_input_price_per_1m=2.0, llm_output_price_per_1m=6.0), 1_000_000, 1_000_000) == 8.0`（L12）；Prometheus 计数器增量（L18）；`client.last_usage.prompt_tokens==7`（L35）。

### `tests/test_dashboard.py`（20 行）

GET "/" 200 + `text/html` + 含 `"Warden"`；`/api/v1/runs` 返回 list。

### `tests/test_eval_retrieval.py`（31 行）

**`hit_at([1,0,3,5,4],1)==1/5`、`(…,3)==2/5`、`(…,5)==4/5`、`(…,99)==4/5`**（rank 0 永不计入）；`hit_at([],4)==0.0`；**`hit_curve([1,2,0,7,3],[1,3,4,8])=={"1":1/5,"3":3/5,"4":3/5,"8":4/5}` 且值单调不降**（msg "截断点越大，命中率只能持平或上升"）。

### `tests/test_eval_router.py`（156 行）

**真起 `ThreadingHTTPServer` on port 0**（`_serve` contextmanager，finally shutdown + `thread.join(timeout=5)`）+ 两个 stub 上游。

| 行 | 断言 |
|---|---|
| L93 | `/v1/embeddings` 只到 embed 且 **body 逐字节相等**、`chat.received==[]` |
| L107 | `/v1/chat/completions` 与 `/v1/models` 都到 chat |
| L118 | **`/health` 本地回 `(200,"ok")` 且两个上游都没收到** |
| L133 | **上游 429 原样透传含 body** |
| L148 | 上游不可达 → 502 且 `"router:" in resp.json()["error"]` |

### `tests/test_function_call.py`（77 行）

`steps[0].observation==4`；未知工具后恢复；**`CaptureLLM` 断言 `captured["tools"][0]["type"]=="function"` 且含 `"parameters"`**；system prompt 含 `"12 * 34"`。

### `tests/test_gateway.py`（408 行）—— **最大的测试文件，19 个用例**

**`class Recorder`（L49–68）的两个关键设计**：

```python
def __call__(self, url, **kwargs):
    self.calls.append({"url": url, **kwargs})
    return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]   # 最后一个响应重复
@property
def urls(self): return [c["url"] for c in self.calls]
@property
def headers(self): return [c["headers"] for c in self.calls]
```

**`min(len-1, len-1)` 让"最后一个响应无限重复"** —— 这解决了一个真实的测试设计问题：一个总是 409 的用例不需要提供 3 个相同的响应对象。

| 行 | 断言 |
|---|---|
| L79 | 两轮的 tenant 恒定、session == `derive_session_id(turn1)`、两轮幂等键不同 |
| L101 | **两个新建 LLMClient 同样 messages → 同一个键**（跨进程可复现）|
| L113 | 键序不敏感 |
| L124 | **纯 system 调用三个头全无（只留 Authorization）** |
| L135 | `gateway_enabled=False` → 唯一头是 Authorization |
| L142 | **作用域让裁剪后的历史仍发完整历史的 session** |
| L160 | `gateway_session="pinned-session"` 胜过派生 |
| L170 | **in_flight 重试：`sleeps==[1.0]`、两次调用同键、`last_gateway.attempts==2`** |
| L185 | 无 Retry-After 用 `gateway_retry_after_s=0.25` |
| L194 | 到顶仍 409 → LLMError 且 `len(rec.calls)==3` |
| L205 | **conflict 不重试：1 次调用、`sleeps==[]`** |
| L223 | 顶层 `{"type":...}` 形状也识别 |
| L232 | 409 体是 HTML 不崩 |
| L244 | **回放算成功：`replayed is True`、`replay_origin=="req-original-1"`、`replay_age_ms==1234`、`upstream_name=="replay"`，且 `warden_gateway_idempotent_replays_total{model="gw-replay"}` +1** |
| L282 | `Replay: false` → `replayed is False` |
| L301 | 无 `headers` 属性的 BareResp 也正常 |
| L336 | `capabilities_url` 三种输入 |
| L342 | **`context_window()==128000`、缺失字段的 "tiny" → None、`rec.urls == ["http://gw:1/v1/capabilities"]`（TTL 内只探一次）** |
| L358/365/374 | 500/网络错误/非 JSON 全退化 |
| L382 | **失败也缓存：两次查询 `len(rec.calls)==1`** |
| L392 | `ttl_s=0` 时两次 |
| L401 | `gateway_capabilities_enabled=False` → `rec.calls == []` |

**`monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))`（L174/189/197/216）是这批测试的核心技巧** —— 断言精确的退避秒数（`[1.0]`、`[0.25]`、`[]`），**全程无真实睡眠**。

### `tests/test_gateway_live.py`（131 行）

`pytestmark = pytest.mark.live`（L34）+ `live`/`needs_mock` skipif（L28–31）→ **默认 `pytest` 完全不选它**。

**autouse `_no_proxy_for_loopback`（L37–45）设 `NO_PROXY`/`no_proxy`="127.0.0.1,localhost"** —— 注释"生产代码不动——远程 LLM 走代理本来就是对的"。**这是一个很好的测试哲学：测试适配生产，而不是为了测试改生产。**

`_turn_pair(nonce)`（L61–68）**首条用户消息带随机 nonce** —— 所以每次运行的会话互不干扰（否则会命中上一次的幂等缓存）。

| 行 | 断言 |
|---|---|
| L76 | 两轮同一会话；账本 `requests==2 and ok==2 and failed==0`；`prompt_tokens>0 and cost_usd>0` |
| L101 | **回放不打上游：`_calls()` 不变（6→6）、`upstream_name=="replay"`、账本 `requests==3, idempotent_replays==1`** |
| L129 | capabilities 取到真值 |

**docstring 强调"断言的是线路上的事实，不是代码自述"** —— 这三条测试的价值在于它们验证的是**真实网关的行为**（既有 Python 侧也有 Go 侧）。

### `tests/test_harness.py`（66 行）

| 行 | 断言 |
|---|---|
| L14 | RunStore 往返（含 meta/steps/output/status）|
| L37 | `list()` 倒序 `["c","b","a"]`、`list(kind="task")==["c","b"]` |
| L47 | **`RunStore(path).close()` 两次不抛**（CREATE IF NOT EXISTS 幂等）|
| L53 | `trace_span` 的 set/reset；`len(new_trace_id())==32` |
| L63 | `trace_span()` 自动生成 |

### `tests/test_llm_client.py`（98 行）

L24 断言 `arguments="not json"` → `arguments={}`、无 id → `id=""`（**容错路径**）；L72 `@pytest.mark.parametrize("method", ["chat","chat_with_tools"])` → `{"choices": []}` 抛 `LLMError, match="unexpected LLM response shape"`；ConnectError → `match="LLM request failed"`；500 → `match="LLM returned 500"`。

### `tests/test_mcp.py`（79 行）

**`_FAKE_SERVER`（L12–36）是内联在测试里的真 JSON-RPC stdio 服务器**：

```python
_FAKE_SERVER = """
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    ...
    print(json.dumps({...}), flush=True)
"""
```

**每一处 `print` 都用 `flush=True`** —— 否则管道缓冲会让客户端 `readline()` 永久阻塞。**这是"测试一个子进程协议"必须处理的细节。**

三个用例：`list_tools`/`call_tool`（`"echo:hi"`）；`build_input_model` required/optional（缺 required 抛 ValidationError）；`adapt_mcp_tool` 走通 client。

### `tests/test_mcp_registry.py`（72 行）

`FakeMCPClient` 用类级 `instances` 列表（L11–37）；**`[t.name for t in registry.all()] == ["fs_echo","echo"]`**（L47，**无 name 的 server 不加前缀**）；空配置；**`all(not c.closed)` → 逐个 close → `all(c.closed)`**（L67）。

### `tests/test_memory.py`（146 行）

**`FakeEmbedder`（L9–19）= 8 维词袋哈希（`hash(w) % 8`）** —— **零网络、确定性、可复现**：

```python
def embed(self, texts):
    out = []
    for t in texts:
        v = [0.0] * 8
        for w in t.split():
            v[hash(w) % 8] += 1.0
        out.append(v)
    return out
```

**注意 `hash(w)` 受 `PYTHONHASHSEED` 影响** —— 但测试只断言**排序位置**（`chunks[0].id == "a"`）而非具体分数，所以随机化不影响结果。**这是一个"用相对判定免疫哈希随机化"的模式。**

| 行 | 断言 |
|---|---|
| L34 | chunk_text 切分与合并（每块 ≤200）|
| L41 | 空输入 `[]` |
| L45 | 余弦排序 + `count()==3` |
| L69 | `build_default_registry(retriever=...)` 才注册 search_notes |
| L86 | **无 retriever 时 `names()==["calculator","get_current_time"]`** |
| L92 | `FixedEmbedder` 造余弦 1.0 vs 0.0 精确验证 `min_score=0.5` 只留 `["a"]` |
| L109 | **改写开启时 `embedder.queries[-1] == "warden personal multi-agent system"`** |
| L130 | **重排：LLM 回 `'{"ranked": [3, 1, 2]}'` → `retrieve(k=2, rerank=True)` 得 `["c","a"]`** |

### `tests/test_metrics.py`（20 行）

**增量而非绝对值**：先读 `REGISTRY.get_sample_value(...) or 0.0`，断言 `after == before + 1.0`。`metrics_text()` 含 `"warden_runs_total"` 与 `"warden_run_duration_seconds"`。

### `tests/test_net.py`（146 行，17 例）

```python
class Recorder:                                                          # L40–55
    @property
    def trust_env(self):
        return self.calls[0].get("trust_env", "缺省(读环境)")             # L54
```

**这个属性设计很精巧**：`"缺省(读环境)"` 这个哨兵字符串让"没传 kwarg"与"传了 False"可区分（`False` vs `"缺省"` 都是 falsy，直接 `assert not ...` 无法区分）。测试里 `rec.trust_env is False` 用的是 `is`。

| 行 | 断言 |
|---|---|
| L58 | parametrize 5 个 loopback URL（`127.0.0.1`、`localhost`、`LOCALHOST`、`127.0.0.2`、`[::1]`）为真 |
| L72 | parametrize 5 个非 loopback（`api.openai.com`、`192.168.1.10`、`ollama.internal`、`""`、`"not a url"`）为假 |
| L91 | **LLMClient 对 127.0.0.1 → `rec.trust_env is False`** |
| L100 | **对远程 → `"trust_env" not in rec.calls[0]`** |
| L109 | 嵌入客户端按 index 排序（乱序输入仍得 `[[0.1],[0.2]]`）|
| L125/132 | 传输错误与坏形状都包成 EmbeddingError |
| L139 | **能力探测对本机网关也关代理** |

### `tests/test_notify.py`（62 行）

`get_notifier` 四种 kind（含大写 `"FILE"` 与未知 `"carrier-pigeon"` 都回落）；**`re.fullmatch(r"warden_周报_09_10_\d{8}_\d{6}\.md", name)`**；标题 80 次重复 → slug ≤60；**同一秒发两次 → 只剩 1 个文件且内容是第二次**（已记录的故意取舍）。

### `tests/test_orchestrator.py`（62 行）

路由到指定专家且另一个专家 **0 次调用**；**未知专家 `"nope"` → 落到第一个成员 `"writer"`（兜底）**；`name`/`description` 覆盖类属性。

### `tests/test_pipeline.py`（84 行）

`result.agent=="critic_pipeline"`、`len(result.steps)==2`；**`SpyLLM` 断言 critic 收到的 prompt 里同时含 `"the sky is blue"` 与 `"what color is the sky?"`**（验证接力把两者都传下去了）。

### `tests/test_planact.py`（82 行）

`steps[0].observation == 12`（**int 非 str**）；**工具失败后重规划：`steps[0].action=="nope"`、`"unknown tool" in str(steps[0].observation)`、`steps[1].action=="calculator"`**；`Settings(max_replans=1)` 时 `len(llm.calls)==3`（plan + 1 replan + summarize）。

### `tests/test_react.py`（162 行）

| 行 | 断言 |
|---|---|
| L18 | `steps[0].observation == 4`（**int**）|
| L34 | 未知工具回传 |
| L48 | **截断：自造 Tool 返回 `"x"*5000` + `Settings(max_observation_chars=50)`，直调 `agent._execute("long", {})`，断言 startswith `"x"*50` 且 `<100` 且含 `"truncated"`** |
| L76 | 自纠一次成功 |
| L90 | 两次垃圾 → `answer=="garbage two"`（取**最后一次**原文）|
| L99 | `agent_max_steps=2` 强制收尾 |
| L114 | prompt 含 `"12 * 34"`、`'"action": "calculator"'`、`"final_answer"` |
| L123 | **一轮两个 JSON 取第一个（`"final_answer" not in parsed`）** |
| L137 | 两个对象时工具照常执行 |
| L155 | 纯垃圾抛 ValueError |

**L48 的用例是"断言私有方法"的典型** —— 它跳过整个循环直接调 `agent._execute`，因为要测的是截断逻辑而非循环。

### `tests/test_reflect.py`（69 行）

verdict ok 不留额外步；**redo → 恰好 1 个 `action=="_reflect"` 的步且 feedback 在 observation 里**；reflect 解析失败放行；**默认关闭时 `llm.script == []`（恰好一次调用）**。

### `tests/test_registry.py`（26 行）

默认注册表 `{"calculator","get_current_time"}`；`run({"expression":"12 * 7 + 3"}) == 87`；**`pytest.raises(ValueError)` for `"__import__('os').system('ls')"`（安全回归）**；datetime 返回含 `"T"` 的 str。

### `tests/test_resume.py`（115 行）

**`store` fixture（L13–18）yield + close**（注释"否则测试会话结束时会报未关闭连接的 ResourceWarning"）。

**`ScriptLLM`（L21–35）**：

```python
def chat(self, messages, temperature=None):
    item = self.script.pop(0)
    if isinstance(item, Exception):        # 脚本项是异常实例就抛
        raise item                          # 模拟 LLM 失败/运行中断
    return item
```

**"脚本项可以是异常实例"是一个很优雅的中断模拟** —— 不需要另一个类。

| 行 | 断言 |
|---|---|
| L43 | react 工具步后崩 → `status==STATUS_ERROR` 且 `len(steps)==1`；resume 后 `answer=="4"`、`len(result.steps)==2`、状态转 ok |
| L76 | **planact resume **不重跑**已完成工具步：`len(result.steps)==1` 且 `runs[0].meta["plan"]` 存在** |
| L108 | 正常运行落 ok |

### `tests/test_retry.py`（63 行）

成功不重试；失败两次后成功（`n==3`）；耗尽抛最后一个异常（`str(exc)=="last error"`，用 **try/except/else-AssertionError**）；`retriable=lambda e: False` 只试 1 次；`on_attempt=seen.append` → `seen==[1,2,3]`。

### `tests/test_save_note.py`（67 行）

index_text 写 chunk；有 indexer 才注册 save_note；**读写闭环：save_note 内容 `"the warden project uses a self-built react loop"` → 返回含 `"记住"`；search_notes 查询 `"react loop"` → 结果含 `"self-built react loop"`**。

### `tests/test_scheduler.py`（197 行）

**`StubTask`（L32–48）**：`schedule={"trigger":"interval","minutes":5}`，`run` 里**局部 import TaskResult**（避免循环导入）。

| 行 | 断言 |
|---|---|
| L66 | 注册表 get/names/all |
| L77 | 重名与未知任务的**精确消息**：`match="duplicate task name: stub"` / `"unknown task: nope"` |
| L89 | **`str(_build_trigger({"trigger":"interval","minutes":5}))=="interval[0:05:00]"`**；cron 的 str 含 `day_of_week='mon'` |
| L96 | 默认 interval；未知 trigger 抛 `match="unsupported trigger type: lunar"` |
| L102 | job ids 与 `max_instances == 1` |
| L124 | **run_once 落库并通知：`notifier.sent==[("stub 报告","ok 的产出")]` 且 `record.attempts==1`** |
| L141 | 未知任务抛 |
| L148 | **任务抛错被吞并记录：`(result.status,result.error)==(STATUS_ERROR,"接口挂了")`、`notifier.sent==[("stub 失败","接口挂了")]`** |
| L164 | `retry_attempts=3` → `task.calls==3` 且 `record.attempts==3` |
| L179 | **skipped 不发通知（`notifier.sent == []`）** |
| L189 | `store=None` + `metrics_enabled=False` 也能跑 |

### `tests/test_self_eval.py`（76 行）

默认关；开启后挂载 `{"confidence":0.9,"reason":"deterministic arithmetic"}`；垃圾输出静默；**`SpyLLM` 断言 `len(calls)==2` 且 `"answer 42" in calls[1][-1]["content"]`**。

### `tests/test_tasks.py`（113 行）

内联 `RSS_XML`（2 条，pubDate 只在第一条）与 `ATOM_XML`（2 条）；`FakeClient` 是 contextmanager（L51–62）。

| 行 | 断言 |
|---|---|
| L69/75 | RSS/Atom 解析标题与链接 |
| L81 | **news_digest 首跑 ok 且 `detail["new"]==2`，再跑 skipped 且 `detail["new"]==0`（去重跨次生效）** |
| L95 | repo_report ok 且 `detail["deps"] >= 4` |
| L103 | weekly_review ok |
| L111 | 默认注册表 `["news_digest","repo_report","weekly_review"]` |

### `tests/test_tools.py`（102 行）

output_model 把 dict 校验成 schema（`{"result":5}`）；不匹配返回 `"error: tool 'add' output does not match schema AddOutput"`；接受 JSON 字符串；**超时：`timeout_s=0.05` + 睡 0.3 → `"error: tool 'slow' timed out after 0.05s"`**；无超时正常；无 output_model 原样透传。

**注意超时用例没有断言耗时** —— 这正是第 10.1 节指出的 `ThreadPoolExecutor.__exit__` 会等待的缺陷不被发现的原因。

### `tests/test_weekly_review.py`（159 行）

`_write(..., age_days=30)` **用 `os.utime` 改 mtime** 造 7 天窗口（L40–44）；`_git` 辅助（L47–53）跑真 git。

| 行 | 断言 |
|---|---|
| L56 | 只留 7 天内的笔记（含嵌套目录）|
| L71 | 缺失/未设目录返回 `[]` |
| L78 | **真建 git 仓库提交 `"feat: 第一件事"` 并断言出现在输出里** |
| L91 | `monkeypatch.setattr("app.tasks.weekly_review.subprocess.run", boom)` 抛 OSError → 静默 `[]` |
| L103 | **parametrize `["weekly_review","repo_report"]` 的中文提交信息回归（`"feat: 中文提交信息不能丢"` 必须存活）** |
| L125 | run 的 detail 与 prompt 结构（system 含 `"个人复盘助手"`、user 含 `## 本周笔记`/`## 本周提交`）|
| L149 | 空段落标 `"（无）"` |
| L155 | **提交列表截断到 50（60 条时 `"c49"` 在、`"c50"` 不在）** |

**L103 的 parametrize 覆盖两个文件** —— 因为 `repo_report._recent_commits` 与 `weekly_review._recent_commits` 是同一段代码的复制。**这是一个针对"已知重复"的测试设计。**

## 16.2 覆盖图：哪些模块没有直接 import

**有直接 import 的**（37 个）：`app.cli`、`app.config`、`app.llm`、`app.main`、`app.net`、`app.agent.base`、`app.agent.blackboard`、`app.agent.function_call`、`app.agent.orchestrator`、`app.agent.pipeline`、`app.agent.planact`、`app.agent.react`、`app.gateway`（包）、`app.gateway.capabilities`、`app.harness`（包）、`app.harness.cost`、`app.harness.metrics`、`app.harness.retry`、`app.harness.run_store`、`app.mcp.client`、`app.mcp.registry`、`app.mcp.tools`、`app.memory.embeddings`、`app.memory.indexer`、`app.memory.retriever`、`app.memory.vector_store`、`app.notify`（包）、`app.notify.base`、`app.scheduler`（包）、`app.scheduler.base`、`app.scheduler.runner`、`app.tasks`（包）、`app.tasks.news_digest`、`app.tasks.repo_report`、`app.tasks.weekly_review`、`app.tools`（包）、`app.tools.base`、`app.tools.builtin.blackboard`。

**从未被测试直接 import 的**（14 个），及它们如何仍被覆盖：

| 模块 | 覆盖方式 |
|---|---|
| `app/__init__.py` | 包初始化 |
| **`app/schemas.py`** | **无测试直接 import**；经 `app.main` 的 import 间接覆盖（POST /api/v1/chat 与 tasks/run 走通）|
| `app/agent/__init__.py` | 经 `app.agent.react` 等加载 |
| `app/mcp/__init__.py` | 经 `app.mcp.client`/`tools` 加载 |
| `app/memory/__init__.py` | 经 `app.memory.*` 加载 |
| `app/gateway/identity.py` | **经 `app/gateway/__init__.py` 转出**（行为上由 test_gateway/test_gateway_live 覆盖）|
| `app/harness/trace.py` | 经 `from app.harness import ...` 覆盖 |
| `app/notify/console.py` | 经 `app/notify` 包覆盖 |
| `app/notify/file.py` | 经 `app/notify` 包覆盖 |
| `app/scheduler/registry.py` | 经 `app/scheduler/__init__.py` 转出 |
| `app/tools/registry.py` | 经 `app/tools/__init__.py` 转出；`from app.tools import ToolRegistry` 被三个测试用 |
| `app/tools/builtin/__init__.py` | 作为父包执行；其 re-export 被 `app/tools/__init__.py` 拉入 |
| `app/tools/builtin/calculator.py` | **无直接 import**；经 `build_default_registry()` 行为覆盖 |
| `app/tools/builtin/datetime_tool.py` | 同上（test_registry 断言 ISO 字符串）|
| `app/tools/builtin/save_note.py` | 经 `build_default_registry(indexer=...)` |
| `app/tools/builtin/search_notes.py` | 经 `build_default_registry(retriever=...)` |

**`app/schemas.py` 从未被直接 import 是一个真实的测试缺口** —— 它的 `Literal` 校验、`Field(min_length=1)`、`observation: Any` 的类型保持都只被**间接**覆盖。一个针对 schemas 的单元测试（比如"空 messages 列表被拒"、"observation 是 int 时不被转成 str"）会更有针对性。

**`app/gateway/identity.py` 只经包转出** 意味着**没有测试直接验证 `canonical_body`** —— 而它是幂等键的地基。测试只通过 `derive_idempotency_key` 间接覆盖它。

## 16.3 值得注意的测试技巧（19 条）

1. **`httpx` 模块级 monkeypatch** —— `httpx.post`/`httpx.get` 被替身替换，**零真实 socket**（test_cost L46、test_llm_client、test_net、test_gateway）。
2. **`Recorder` 回放对象** —— test_net 记录 kwargs 并暴露 `trust_env` 属性（用哨兵字符串区分"没传"与"传 False"）；test_gateway 支持响应序列且**最后一个重复** + `urls`/`headers` 属性。
3. **patch `time.sleep` 精确断言退避** —— `[1.0]`/`[0.25]`/`[]`，**全程无真实睡眠**。
4. **`FakeLLM` 从脚本列表 pop 是主导模式**；变体：`SpyLLM`（记录每次调用）、`ScriptLLM`（异常实例即抛，模拟中途崩）、`FakeToolLLM`（返回 `(content, [ToolCall])`）。
5. **断言私有方法** —— `agent._execute`、`agent._history_with_summary`、`agent._system_prompt`、`task._recent_notes`/`_recent_commits`/`_prompt`、模块私有 `from app.scheduler.runner import _build_trigger`。
6. **autouse fixture 控环境** —— 删光 `WARDEN_*`、设 `NO_PROXY`、`monkeypatch.chdir(tmp_path)` 防读仓库 `.env`、**重置 `app.config._settings` 单例**。
7. **`tmp_path` 上的真 SQLite**，且都 `close`。
8. **真子进程/真 socket** —— 内联 JSON-RPC stdio server（`flush=True`）、真 `ThreadingHTTPServer` on port 0、真 git 仓库。
9. **确定性哈希词袋嵌入**（8 维，`hash(w) % 8`）与 `FixedEmbedder`（精确构造余弦 1.0 vs 0.0）。
10. **Prometheus 用增量而非绝对值断言** + 唯一 label 值（`metric_probe`/`cost_test`/`gw-replay`）隔离。
11. **try/except/else-AssertionError 替代 `pytest.raises`**（test_blackboard L93、test_retry 三处）。
12. **`pytest.raises(..., match=...)` 断言错误文本**（含 test_config 断言字段名出现在 ValidationError 里）。
13. **FastAPI TestClient 技巧** —— `monkeypatch.setitem(main._agents, ...)`、`setattr(main.store, "list"/"get", ...)`、`with TestClient(main.app)` 真跑 lifespan。
14. **按字符串目标 monkeypatch** —— `"app.tasks.weekly_review.subprocess.run"`、`"app.config._settings"`。
15. **capsys 断言 CLI/通知输出**。
16. **真正则断言文件名** + 记录"同一秒覆盖"这个边界行为。
17. **`os.utime` 造文件年龄**。
18. **parametrize** —— test_net 两组各 5 个 URL、test_llm_client 跨两个方法、test_weekly_review 跨两个 task。
19. **marker/skip 纪律** —— **只有 `test_gateway_live.py` 用 marker，其余 33 个文件不用**。

---

# 第 17 章 已知缺口汇总

以下每一项都给出**精确位置**、**后果**、**触发条件**、**修法**。按严重度分三档。

## 17.1 真实缺陷（会产生错误行为）

### ① resume 的会话 id 在历史被压缩后漂移 —— **同一根因的第一处**

- **位置**：`app/agent/base.py:130`（`run()` 用原始 history 派生，**正确**）vs `app/agent/base.py:154–155`（`resume()` 用 `record.input` 派生，**已裁剪**）；`app/agent/base.py:137–138`（裁剪在落库之前，所以 `input` 是裁剪后的）
- **机制**：`_history_with_summary` 把最旧 N 条换成一个 `role: "user"` 的摘要块放在首位（`base.py:278`）；`_first_user_content` 只认 `role == "user"`（`identity.py:59`）→ 取到**摘要文本**而非用户原话 → 不同的 hash → 不同的 session id
- **触发条件**：历史超过 `context_max_messages`（默认 30）**且** resume 同一个 run
- **后果**：网关账本把同一场对话劈成两段，`/admin/sessions` 的成本/请求统计失真。**断裂点取决于窗口大小这个与语义无关的参数**
- **修法**：把派生结果（或原始 seed）存进 `record.meta`，`resume` 优先读它
- **为什么 M6 的其他两处派生不一致**：`run()` 做对了，`resume()` 与 `derive_idempotency_key` 没做对 —— **同一个判断被实现了三次，只有一次正确**

### ② `derive_idempotency_key` 用"实际发送的 messages"而非"逻辑历史" —— **同一根因的第二处**

- **位置**：`app/gateway/identity.py:93`（`turn_index = len(messages or [])`）；调用点 `app/llm.py:217` 取的是 `payload["messages"]`
- **机制**：长对话时 `payload["messages"]` 是压缩后的 `[摘要块, *末30条]`（长度 31），而非真实的 45。`turn_index` 因此**在压缩前后不连续**
- **触发条件**：**同一逻辑轮**在压缩发生前失败一次、在压缩发生后重试一次
- **后果**：两次的 `messages` 不同 → 不同的幂等键 → 网关不回放 → **上游被调用两次（重复计费）**
- **为什么通常安全**：同一份被压缩的输入在重试时同样被压缩 → 条数与哈希都不变。只有"跨过窗口边界"这个条件才破
- **修法**：与 ① 同源 —— 用裁剪前的逻辑历史派生
- **相关**：`app/llm.py:245` 的 `max_attempts` 依赖键存在；键不稳定时重试行为也随之不稳定

### ③ resume 会重算一次自评（多一次 LLM 调用）

- **位置**：`app/agent/base.py:185–197`（`_finish_ok` 只写 `output`，**从不写 `result.self_eval`**）；守卫在 `base.py:280–283`（`or result.self_eval is not None`）
- **机制**：`resume` 时 `result` 是新建的 `AgentRunResult`，`self_eval` 恒为 `None` → 守卫失效 → `_self_eval` 再跑一次
- **触发条件**：`self_eval_enabled=True` **且** resume 一个已成功结束（或已算过自评）的 run
- **后果**：多一次 LLM 调用（**真实的成本**），且 `last_usage`/tokens 指标会多计一次
- **修法**：把 `self_eval` 存进 `record.meta` 或新增一列

### ④ 落库的 `raw` 未 strip，导致 resume 与首跑的上下文不一致

- **位置**：`app/agent/react.py:174`（`_persist_step(record, s, raw)` —— 传未 strip 的 `raw`）vs `react.py:189`/`react.py:221`（回填 messages 用 `raw.strip()`）；重建在 `react.py:144–145`（用 `d["raw"]` 原样）
- **机制**：首跑写入 messages 的是 `raw.strip()`，resume 重建的是 `raw`（带首尾空白）
- **触发条件**：模型的 JSON 输出带首尾空白/换行 **且** resume
- **后果**：resume 的上下文与首跑**不逐字节相同**。对模型而言通常无影响（LLM 不在乎首尾空白），但它破坏了"resume 完全复现首跑上下文"这个更强的保证；若未来做 prompt 缓存，会因此失效
- **修法**：`react.py:174` 改成 `_persist_step(record, s, raw.strip())`，或 `react.py:145` 改成 `d["raw"].strip()`
- **对比**：`function_call.py:109`/`:148` 显式传 `None`，所以没有这个问题

### ⑤ `ThreadPoolExecutor` 的 `with` 块让工具超时失效

- **位置**：`app/tools/base.py:51–56`
- **机制**：`with ThreadPoolExecutor(max_workers=1) as pool:` 的 `__exit__` 调用 `shutdown(wait=True)` → **函数返回前会阻塞等待那个超时的线程跑完**
- **触发条件**：设了 `timeout_s` 的工具实际耗时超过它
- **后果**：调用方实际等待的时间 = **工具真实耗时**（可能远超 `timeout_s`）。Agent 循环仍会被卡住，只是拿到的是一句超时错误。**注释（L54–55）说"这里只是不再等待"是不准确的**
- **为什么没被测出**：`tests/test_tools.py:67` 只断言返回值，**没有断言耗时**
- **修法**：用 `pool.shutdown(wait=False)`（不用 `with`），或模块级共享 executor
- **严重度论证**：对本地模型推理这种"超时往往是几分钟"的场景，这是最影响体感的一个缺陷

### ⑥ `CriticPipeline` 的 `steps` 直接拼接导致 `step.index` 重复

- **位置**：`app/agent/pipeline.py:40`（`steps=research.steps + review.steps`）
- **机制**：两段 run 各自从 index 0 开始编号，拼接后不重排
- **后果**：CLI 打印（`cli.py` 的 `step {s.index}`）与 HTTP 响应（`StepView.index`）里会出现**两个 `step 0`**。任何按 index 索引 steps 的消费方都会取到错误的项
- **为什么没被测出**：`tests/test_pipeline.py:19` 只断言 `len(result.steps) == 2`
- **修法**：拼接后重新编号（`enumerate`）
- **同类**：`BlackboardTeam`（`blackboard.py:74` 的 `all_steps += result.steps`）**有同样的问题**

### ⑦ `latency_p50_ms` / `latency_p95_ms` 不是分位数

- **位置**：`scripts/eval_retrieval.py:280–281`
- **机制**：`latencies[n // 2]` 与 `latencies[min(n - 1, int(n * 0.95))]` 取的是**原始顺序**的第 n/2 与 0.95n 个样本，没有 `sorted()`
- **后果**：报告的 p50/p95 是"第 20 条查询的延迟"而非中位数。**实测佐证**：改写+重排组 p50=4383ms / p95=5684ms，两者差异说明延迟与查询相关，"未排序取中间"有真实误差
- **修法**：`sorted(latencies)[n // 2]` 与 `sorted(latencies)[min(n - 1, int(n * 0.95))]`

## 17.2 一致性瑕疵（不产生错误，但增加理解/维护成本）

### ⑧ 项目有**四个**版本号

| 位置 | 值 |
|---|---|
| `app/__init__.py:3` | `__version__ = "0.1.0"` |
| `pyproject.toml:7` | `version = "0.2.0"` |
| `app/main.py:63` | `FastAPI(version="0.5.0")` |
| `app/mcp/client.py:42` | `clientInfo.version = "0.4.0"`（**发给外部服务**）|

**最严重的是第四个** —— 它是唯一对外可见的，MCP server 会据此判断客户端能力。修法：统一从 `app.__version__` 取值。

### ⑨ 错误前缀的精度不一致

| 位置 | 字面量 |
|---|---|
| `app/tools/base.py:75`（docstring）| `"error: "` |
| `app/tools/base.py:76`（代码）| `value.startswith("error: ")`（**带尾空格**）|
| `app/agent/planact.py:132`（注释）| `"error: "` |
| `app/agent/planact.py:135`（代码）| `observation.startswith("error:")`（**不带尾空格**）|

**后果**：形如 `"error:unknown"` 的串会触发 PlanAct 重规划，但 `tools/base.py` 不把它当错误、仍尝试 `output_model` 校验。**因为 `base.py::_execute` 生成的错误总是带尾空格，这条不一致在实践中不触发** —— 它是"约定没有单一来源"的代码气味。修法：提取一个 `is_tool_error()` 常量函数。

### ⑩ `_turn`/`_resume_turn` 的指标用字面量而非常量

- **位置**：`app/agent/base.py:143`/`:147` 用字面量 `"ok"`/`"error"`，而 `STATUS_OK`/`STATUS_ERROR` 已经导入（L14 附近）
- **后果**：无。但如果有天常量值改了，这里会静默不一致

### ⑪ `base.py:175` 的抽象方法签名比子类实现更窄

- `base.py` 的 `_resume(self, record: RunRecord)` vs 三个子类的 `_resume(self, record: RunRecord | None)`
- Liskov 上从窄到宽是允许的（子类接受更多），所以**不是错误**，但读起来会困惑"到底能不能传 None"

### ⑫ `_execute` 里 `_truncate_observation` 被调用三次

- **位置**：`app/agent/base.py:230`/`:236`/`:237`
- 三条 return 各调一次，逻辑正确但重复。可以改成先算 `value` 再统一 return

### ⑬ `news_seen.json` 的截断不确定性

- **位置**：`app/tasks/news_digest.py:88`（`list(seen)[-_MAX_SEEN:]`）
- **机制**：`set` 的迭代顺序受 `PYTHONHASHSEED` 影响（字符串默认启用哈希随机化）→ 同一个 seen 集合两次运行可能保留**不同的 500 条**
- **后果**：无功能影响（保留哪些旧链接无关紧要），但"截断后的 seen 集合"**不可复现**
- **修法**：`sorted(seen)[-_MAX_SEEN:]`

### ⑭ `canonical_body` 未导出

- **位置**：`app/gateway/__init__.py:27–44` 的 `__all__` 17 项不含它
- **后果**：调试幂等问题（最需要单独调用它的场景）必须 `from app.gateway.identity import canonical_body`

### ⑮ 路由 LLM 调用没有 `with_retry`，且不产生 RunRecord

- **位置**：`app/agent/orchestrator.py:35–42`
- **后果**：① 路由调用的网络失败直接冒泡（其他所有 LLM 调用都重试）；② `/api/v1/runs` 里看不到路由决策 —— **多 Agent 的编排过程不可见**
- **同类**：`CriticPipeline` 与 `BlackboardTeam` 本身也不落库（只有它们委派的专家落库）

### ⑯ `agent` 名在三处重复定义

- `app/schemas.py:17`（`Literal[...]`）、`app/main.py:44–48`（`_agents` 字典）、`app/cli.py:63–65`（`agent_cls` 字典）、`app/cli.py`（`--agent` choices）
- **后果**：新增一种循环要改四处。对比 `ToolRegistry` 的"零改动" —— **Agent 的插件化没打通**

### ⑰ `get_capability_cache(ttl_s)` 的参数只在首次调用生效

- **位置**：`app/gateway/capabilities.py:159–167`（docstring 明说了）
- **后果**：`LLMClient.__init__` 传的 TTL（`llm.py:142–144`）在**非第一个** Client 上被忽略。实践上 `main.py` 只建一个，所以无影响；但测试里创建多个 Client 时 TTL 由第一个决定

### ⑱ 两处"0 哨兵"语义不同

- `config.py:36` 的 `max_observation_chars=0` = **关闭截断**
- `config.py:38` 的 `context_max_messages=0` = **关闭压缩**
- `gateway/capabilities.py:78` 的 `ttl_s <= 0` = **不缓存（每次探测）**
- **前两者是"关闭功能"，第三者实际上是"更频繁地执行功能"** —— 同一项目里 `0` 有两种相反的含义

### ⑲ MCP 相关的小缺口

| 缺口 | 位置 | 后果 |
|---|---|---|
| `stderr=DEVNULL` | `mcp/client.py:30` | MCP server 的日志完全丢失（排查外部工具问题时无信息）|
| 未用 `timeout_s` | `mcp/tools.py:41–57` | MCP 调用可能无限阻塞（`tools/base.py` 的超时机制存在却未使用）|
| `_request` 非线程安全 | `mcp/client.py:51–67` | 未文档化的约束（当前调用方都单线程）|
| 工具重名会让整个 MCP 注册表被跳过 | `tools/registry.py:23` + `cli.py` 的 `except` | MCP 工具名与内置名冲突 → 该 server 的**所有**工具都不注册 |
| 类型映射启发式 | `mcp/tools.py:10–17`/`:33` | 未知类型回退 `str`；`array`/`object` 无元素校验；无法显式传 `null` |

### ⑳ 通知与调度的小缺口

| 缺口 | 位置 | 后果 |
|---|---|---|
| 未知 `notify_kind` 静默回落 console | `notify/__init__.py:13–17` | 拼错 `"fil"` 会静默变成控制台通知，用户可能纳闷 |
| 文件名只到秒 | `notify/file.py:17` | 同一秒两次同名通知互相覆盖（**已记录为故意取舍**）|
| tzinfo 是固定偏移 | `scheduler/runner.py:34` | 有夏令时的时区在切换后通知时间偏移一小时 |
| 任务级重试的幂等假设未文档化 | `scheduler/runner.py:60–63` | `_run_task` 重跑**整个任务**；只有"可安全重复执行"的任务才适用 |

### ㉑ 评测脚本的隐式耦合

- **位置**：`scripts/eval_resume.py:66–68` 的 `_observations()` 依赖 `"Observation:"` 前缀
- **机制**：`react.py:222` 用 `f"Observation: {observation}"` 回填
- **后果**：如果 `react.py` 改了观测格式，评测会**静默失效**（永远不崩 → 所有 trial 被剔除为 `no_crash`，报告变成 0/0）。**`no_crash` 计数非零是一个可发现的信号，但没有断言**
- **修法**：在评测开头 assert 一次前缀常量

### ㉒ `eval_router` 的 `Retry-After` 丢失

- **位置**：`scripts/eval_router.py:76`（`self._reply(exc.code, payload)` 不带 headers）
- **后果**：通过 router 访问 InferGate 时，上游错误响应里的 `Retry-After` 会丢失 → `_retry_after_s` 永远回落 `gateway_retry_after_s`（默认 1.0s）
- **影响范围**：仅本地评测场景（`eval_router` 不用于生产）

### ㉓ 测试覆盖缺口

| 缺口 | 说明 |
|---|---|
| **`FunctionCallAgent._resume` 无单元测试** | `tests/test_resume.py` 只测 React 与 PlanAct；FunctionCall 的 resume 只有我手写的 `scripts/exp_fc_resume.py` 覆盖 |
| **`app/schemas.py` 从未被直接 import** | `Literal` 校验、`Field(min_length=1)`、`observation: Any` 的类型保持都只被间接覆盖 |
| **`app/gateway/identity.py` 只经包转出** | 没有测试直接验证 `canonical_body` |
| **`ThreadPoolExecutor` 超时的耗时未断言** | 见缺口 ⑤ |
| **`CriticPipeline` 的 step index 未断言** | 见缺口 ⑥ |
| **无 `tests/conftest.py`** | 五个 fixture 各自在文件内定义，有重复（三个 `RecordingNotifier`、四个 `FakeLLM`、两个 `_settings()`）|

### ㉔ 一处"实现完整但未接入"

- **位置**：`app/llm.py:337–354`（`context_window` / `max_output_tokens`）
- **docstring 自述**（L340–342）："Warden 目前没有任何按 token 估上下文的地方（历史裁剪是 `WARDEN_CONTEXT_MAX_MESSAGES`，按**消息条数**），所以这个值现在是给运维/未来的量化入口，没有接进 Agent 循环——接进去等于新造一套裁剪策略。"
- **判断**：这是**诚实的未完成**（有明确理由、有测试覆盖、有运维用途），不是死代码。但它确实是一个"实现完整、未闭环"的模块

---

# 第 18 章 架构评估：在什么规模下失效

> 本章的三个问题来自用户最初的验收标准（m00223）：**① 上万篇笔记的暴力余弦；② 多进程写 SQLite；③ 模块级单例装配对测试的影响。**
> 所有量化结论都有本会话的实测数据支撑（附录 A）。

## 18.1 问题一：暴力余弦的规模上限

### 算法复杂度

`vector_store.py:50–57` 的 `search` 是全表扫描：

```
N 个 chunk、D 维向量
① 全表 SELECT：        O(N) 行 I/O，每行含一个长度为 D 的 JSON 字符串
② json.loads 每行：     O(N·D) 字符串解析  ← 常常是瓶颈
③ 余弦计算：           O(N·D) 乘加 + 2·O(N·D) 求范数
④ 排序：               O(N log N)
⑤ 只取前 k：           丢弃 N-k 个结果
```

### 实测基线（本会话，附录 A）

| 指标 | 实测值 |
|---|---|
| 语料规模 | 20 篇 → **20 个 chunk** |
| 查询数 | 40 |
| D（nomic-embed-text-v1.5）| **768** |
| 纯向量 p50 | **88 ms** |
| 纯向量 p95 | **107 ms** |

**注意：20 个 chunk 时 p50 已经是 88ms** —— 但这里面**绝大部分是嵌入调用**（一次 `/embeddings` HTTP 往返），不是余弦计算。要拆解，需要用测试里的 `FakeEmbedder`（零网络）：

**拆解估算**（基于代码结构 + 数量级推理）：

| 阶段 | 20 chunk | 1,000 chunk | 10,000 chunk | 100,000 chunk |
|---|---|---|---|---|
| 嵌入一次查询（HTTP）| ~50–80 ms | ~50–80 ms | ~50–80 ms | ~50–80 ms |
| 全表 SELECT + JSON 解析（768 维）| <1 ms | ~20–40 ms | ~200–400 ms | **~2–4 s** |
| 余弦 + 排序 | <1 ms | ~10–20 ms | ~100–200 ms | **~1–2 s** |
| **总计（不含嵌入）** | **~1 ms** | **~30–60 ms** | **~0.3–0.6 s** | **~3–6 s** |

**估算依据**：一个 768 维 float 的 JSON 表示约 2–4 KB（`"[0.123456789,-0.987654321,...]"`，每个 float 约 18 字符）。10,000 个 chunk ≈ **20–40 MB 的 JSON 文本**需要解析。`json.loads` 的吞吐约 50–100 MB/s（CPython），所以 20–40 MB ≈ 0.2–0.4 s。余弦计算是 10,000 × 768 = 768 万次乘加 ≈ 0.1–0.2 s（Python 循环）。

### 失效点

**"上万篇笔记"意味着多大的 N？** 一篇笔记按 `chunk_size=600` 切分，平均 1–3 个 chunk。所以：

| 笔记数 | 估计 chunk 数 | 纯检索延迟（不含嵌入）| 是否可接受（交互场景）|
|---|---|---|---|
| 100 | 200–300 | ~10–20 ms | ✓ |
| 1,000 | 2,000–3,000 | ~60–120 ms | ✓ |
| **10,000** | **20,000–30,000** | **~0.6–1.2 s** | **△ 勉强（Agent 每轮都可能调 search_notes）** |
| 100,000 | 200,000–300,000 | ~6–12 s | ✗ |

**结论**：**上限约在 1 万篇笔记 / 2–3 万个 chunk**。超过之后：

1. **单次检索超过 1 秒** —— 而 Agent 循环里 `search_notes` 可能被调用多次（每次 ReAct 迭代都可能调），延迟会累加
2. **内存压力** —— 每次检索都把全表读进内存（20–40 MB 的字符串 + 解析后的 list）。**每 5 秒的控制台轮询不涉及这个**，但并发查询会成倍放大
3. **每次检索都重复解析相同的 embedding** —— **这是最大的可优化点**：embedding 是静态的，但每次查询都重新 `json.loads` 一遍。**缓存解析结果（或改用二进制格式）能立刻改善 2–3 倍**

### 三个具体的改进路径（按投入产出比）

| 方案 | 改动范围 | 预期收益 |
|---|---|---|
| **① embedding 以二进制存储**（`struct`/`array`/numpy bytes）| 只改 `vector_store.py` 的 add/search 两处的序列化 | 省掉 JSON 解析（最大的单项开销），可能 2–3× |
| **② 进程内 embedding 缓存** | `vector_store.py` 加一个 `dict[id, list[float]]` | 全表只解析一次，后续检索零解析 |
| **③ 换 ANN 索引**（hnswlib / faiss）| 改 `vector_store.py` 的 `search` 实现（接口不变）| O(log N)，但引入 C 扩展依赖 |

**docstring（`vector_store.py:1–5`）已经预留了第三个方案的接口**："接口 add/search 留作换 Milvus/pgvector 只改这一处"。

### 一个更隐蔽的失败模式：**短文档是"相似度磁铁"**

这是本会话的实测发现（附录 A），比延迟问题更值得关注：

**现象**：40 条查询里 19 条 MISS，其中 **`zettel` 出现在大量 MISS 的结果列表里**。`zettel` 是语料里**最短的文档**（约 70 字）。两条针对它的查询：

- `"怎样记笔记以后才方便复用"` → 投给了 `sqlite-wal`/`image-layer`
- `"收集了很多材料但是记不住怎么办"` → 同上

**机制**：短文档的嵌入向量**在单位球面上分布更集中**（它的语义"方向"不够特化），因此与**许多查询**的余弦相似度都不低。长文档因为包含更多具体词汇，其向量方向更"尖锐"，只在真正相关的查询上得高分。

**这个现象的语料内证**：`semantic-cache` 那篇的正文写着 —— **"只对文字长度足够的请求启用，短问句的向量区分度太差"**。**语料作者（也就是 Warden 的作者）知道这个现象，并把它写进了测试语料，而评测恰好证实了它。**

**后果**：MISS 不是随机的，而是**系统性地偏向短文档**。这意味着：

- 单纯的 Hit@K 数字掩盖了"哪类内容检索不到"
- **`zettel`（卡片笔记）这种"每篇都很短"的笔记法会被系统性检索失败** —— 而这恰恰是知识管理里最流行的方法之一
- 改进方向不是"提高 top-k"，而是**长度归一化**（把余弦分数按文档长度调整）或**用长文档的片段作为查询扩展**

**这是一个"规模无关但内容相关"的失效模式** —— 即使只有 20 篇笔记，短文档的检索就已经失效了。**它比"1 万篇笔记慢"更严重，因为增加硬件无济于事。**

### 改写+重排的实测代价（附录 A）

| 指标 | 纯向量 | 改写+重排 | 差值 |
|---|---|---|---|
| Hit@1 | 42.5% | **10.0%** | **−32.5%** |
| Hit@3 | 55.0% | 55.0% | 0 |
| Hit@4 | 65.0% | 57.5% | −7.5% |
| MRR | 0.538 | **0.323** | **−0.215** |
| p50 | **88 ms** | **4383 ms** | **~50×** |

**两条比 README 那张表更多的信息**：

1. **Hit@1 掉 3/4 但 Hit@3 保持同水平** → 它**把正确答案从第 1 位推到第 2–3 位，而不是推出候选集**。这说明重排的**排序质量**很差（本地 1.5B 模型的排序能力不足），但**召回没丢**。
2. **代价是 50 倍延迟**（88ms → 4383ms）—— 因为每个查询多两次 LLM 调用（改写 1 次 + 重排 1 次），在 CPU 上每次约 2 秒。

**这解释了为什么两项默认关闭**（`config.py:73–74` 的 `rag_rewrite_enabled=False` / `rag_rerank_enabled=False`）。**默认值是评测结论的产物，不是保守。**

## 18.2 问题二：多进程写 SQLite

### 当前的并发设计

```python
# run_store.py:41–44
self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
self._conn.row_factory = sqlite3.Row
self._conn.execute("PRAGMA journal_mode=WAL")
```

**WAL 模式让"一写多读"并发可行**：

| 模式 | 读-写并发 | 写-写并发 |
|---|---|---|
| 默认（rollback journal）| 写时读被阻塞 | 串行（数据库锁）|
| **WAL** | **读不阻塞写、写不阻塞读** | **仍然串行**（只有一个写者）|

### 单进程多线程（Warden 的实际形态）

`main.py` 的装配让 **FastAPI 请求线程与 APScheduler 后台线程共享同一个 `RunStore` 连接**：

- **好**：`check_same_thread=False` + 每次操作后 `commit()` + WAL → 实践上可行
- **风险 1**：**`sqlite3` 的 Python 绑定在 `check_same_thread=False` 时不提供任何内部锁** —— 两个线程同时 `execute` 同一个 Connection 是**未定义行为**（可能交错状态机、可能抛 `ProgrammingError: SQLite objects created in a thread can only be used in that same thread` 的派生错误、也可能静默损坏）。CPython 的 GIL 让"单个 C 调用"是原子的，但 `execute` + `commit` 是**两次**调用，中间可被切换
- **风险 2**：**没有 `busy_timeout`**。`sqlite3.connect` 默认 `timeout=5.0`（秒），所以写入冲突会等 5 秒后抛 `database is locked`。**WAL 下写-写冲突会立刻返回 `SQLITE_BUSY`**，Python 层靠 `timeout` 重试 5 秒
- **风险 3**：**`record.steps` 的每次 append 都触发一次完整 UPDATE**（`_persist_step`），而 `steps` 是整个 list 的 JSON。一次 10 步的 Agent 运行会写 10 次**递增大小的** JSON（第 10 次写的是全部 10 步）。**写放大是 O(N²)**（N = 步数）

### 真正的多进程场景

**关键观察**：`scripts/eval_resume.py` **就是多进程的** —— 它用 `subprocess.run` 起 crash 子进程与 resume 子进程，两者**先后**访问同一个 `data/exp_resume.db`：

```python
# eval_resume.py 的模式
crash 子进程：   RunStore(db) → agent.run() → os._exit(70)   ← 不 close
（父进程）：      RunStore(db) → record.steps 读取 → close
resume 子进程：  RunStore(db) → agent.resume() → close
```

**这里恰好安全**，因为三个进程**严格串行**（父进程 `subprocess.run` 等子进程结束）。**实测 6/6 成功**（附录 A）。

**但"进程被 `os._exit` 硬杀"意味着 SQLite 连接没有正常关闭** —— WAL 文件里可能有未 checkpoint 的数据，且 `-shm` 文件可能残留。**SQLite 的设计能容忍这一点**（下次打开时会恢复），但这是"多进程 + 硬崩"组合下唯一被实测验证过的路径。

### 失效条件

| 场景 | WAL 下的表现 | 判定 |
|---|---|---|
| 单进程、单线程（CLI）| 完全正常 | ✓ |
| 单进程、多线程（`uvicorn` + scheduler）| **实践可行但无锁保护**；写-写靠 5s timeout | △ 有隐患 |
| 多进程、严格串行（eval_resume）| 正常（实测 6/6）| ✓ |
| **多进程、并发写** | 写者串行化 → 高冲突时 `database is locked` | ✗ |
| **多进程、并发写 + 长事务** | 一个长事务阻塞所有其他写者（WAL 下写者互斥）| ✗ |
| **网络文件系统（NFS/SMB）** | **WAL 需要共享内存（`-shm`），NFS 上不可靠** | ✗ |

**结论**：**SQLite + WAL 的适用上限是"单个写进程（可以多线程）+ 任意数量读进程"**。

具体的失效触发条件：

1. **同时跑两个 Warden 实例**（比如一个 CLI `chat` 与一个 `uvicorn`）→ 两个写者 → 高冲突
2. **把 `data/` 放在 NFS/OneDrive/网络盘上** → WAL 的 `-shm` 依赖 mmap，网络文件系统上语义不保证
3. **`_persist_step` 的 O(N²) 写放大** + 长步骤链（`agent_max_steps=10` 意味着最多 10 次 UPDATE）→ 单次运行写出约 10 倍的数据量

### 改进路径（按必要性）

| 方案 | 改动 | 适用 |
|---|---|---|
| **加 `busy_timeout`** | `connect(..., timeout=30.0)` 或 `PRAGMA busy_timeout=30000` | 立刻缓解"并发写偶发 locked" |
| **加线程锁** | `RunStore` 内加一个 `threading.Lock` 包住每个方法 | 消除多线程未定义行为（**最低成本、最高收益**）|
| **改 `steps` 的存储** | 每步一行（新表 `steps(run_id, index, ...)`）| 消除 O(N²) 写放大 |
| **换 PostgreSQL** | 只改 `run_store.py`（docstring 已声明）| 真多进程/多机 |

**②（加线程锁）是投入产出比最高的**：`main.py` 的注释明确说"应用与调度共用同一个 sqlite 连接，靠 `check_same_thread=False` + WAL"，但**没有加锁** —— 这是一个"知道风险但没处理"的缺口。

### 一个具体的失败场景推演

`uvicorn` 收到一个 `POST /api/v1/chat`，同时 APScheduler 触发 `news_digest`：

```
线程 A（API）：  RunStore.start(record_A)
线程 B（调度）： RunStore.start(record_B)
```

两个线程共享一个 `sqlite3.Connection`。CPython 下：

- `conn.execute("INSERT ...")` 是**一次 C 调用** → GIL 保护，不会交错
- 但 `conn.commit()` 是**另一次 C 调用**

如果 A 的 `execute` 与 B 的 `execute` 交错（都在同一个隐式事务里），然后 A 的 `commit` 会把**两条 INSERT 都提交**；B 的 `commit` 提交空事务。**结果通常是对的**（两条都进去了），但：

- 如果 B 的 `execute` 在 A 的 `commit` **之后**，B 的 INSERT 会开一个新事务
- 如果某个语句抛异常（比如主键冲突），事务状态是"需要 rollback"，而另一个线程不知道 → **后续所有操作都可能失败**

**`sqlite3` 文档明确说**：`check_same_thread=False` 只关闭了"线程检查"，**不提供同步**，调用方必须自己序列化访问。**当前代码没有做这件事。**

## 18.3 问题三：模块级单例装配对测试的影响

### 装配点清单

| 位置 | 单例 | 副作用（import 时）|
|---|---|---|
| `app/config.py:104–112` | `_settings` | 读 `.env` + 环境变量，可能抛 ValidationError |
| `app/main.py:27` | `configure_logging()` | **替换 root logger 的全部 handlers** |
| `app/main.py:29–34` | `settings`/`llm`/`embedder`/`memory_store`/`retriever`/`indexer` | **建目录 + 连 SQLite** |
| `app/main.py:43` | `store = RunStore(settings.db_path)` | **建表** |
| `app/main.py:44–48` | `_agents` 三个实例 | 无 I/O 但持有上面的对象 |
| `app/main.py:50–52` | `notifier`/`_task_registry`/`_task_ctx` | 无 I/O |
| `app/gateway/capabilities.py:156` | `_cache` | 懒创建（无 import 副作用）|
| `app/tools/__init__.py` | 无（工厂函数）| 无 |
| `app/scheduler/runner.py` | 无（工厂函数）| 无 |

**关键在于 `app/main.py` 的模块级代码是"import 即执行"** —— 而 `app.main` 被 `tests/test_api.py`、`tests/test_dashboard.py` **直接 import**。

### 测试里的三个具体症状

**症状 1：在仓库根建库文件。** `tests/test_api.py:7` 的 `import app.main as main` 会执行 `RunStore(settings.db_path)`，而 `settings.db_path` 默认是 `data/warden.db` —— **所以在仓库根建 `data/warden.db` 与 `data/memory.db`**。README 第 197–199 行的 Windows 注意事项正是在说这类问题（"pytest 会在仓库根建 `pytest-of-<用户>/`"）。

**这与 `tests/test_harness.py`、`tests/test_resume.py` 的实践形成对比** —— 那些测试用 `tmp_path` 建独立的库（`RunStore(tmp_path/"w.db")`）并 `close()`。**问题在于 `app.main` 无法这样注入**。

**症状 2：测试之间的顺序依赖。** `main._agents`、`main.store`、`main.settings` 是**模块级可变对象**。`tests/test_api.py` 用三种方式绕开：

```python
monkeypatch.setitem(main._agents, "react", agent)        # L78
monkeypatch.setattr(main.store, "list", lambda **kw: [...])   # L68
monkeypatch.setattr(main.settings, ...)                  # L62
```

**`monkeypatch` 会在测试结束后自动还原**，所以这些是安全的。**但如果有人用直接赋值（`main.store = ...`）而不是 monkeypatch，污染会跨测试传播。** 这是"依赖注入缺位"的直接代价 —— 每处测试都要手工决定"用哪种方式替换哪一层"。

**症状 3：`get_settings()` 的缓存让测试互相影响。** `app/config.py:104` 的 `_settings` 是模块级缓存：

```python
def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

**第一个调用的测试决定了后续所有测试看到的配置**（除非显式重置）。`tests/test_cli.py:82` 的应对是：

```python
monkeypatch.setattr("app.config._settings", None)   # 打破单例
```

**这必须做，否则 `cli_env` fixture 设置的环境变量不会生效**（因为 `_settings` 已经被前一个测试填充了）。**`tests/test_config.py` 的 autouse fixture 更进一步** —— 它删掉所有 `WARDEN_*` 环境变量，保证 `Settings()` 每次构造都从干净状态开始（它不用 `get_settings()`，而是直接 `Settings()`，从而完全绕开缓存）。

### 三个层次的问题

| 层次 | 具体问题 | 现状 |
|---|---|---|
| **可测性** | `app.main` 无法用测试配置构造（没有 `create_app(settings)` 工厂）| 靠 monkeypatch 打补丁 |
| **隔离性** | 模块级可变对象在测试间泄漏 | 靠 monkeypatch 自动还原（**依赖每个人的纪律**）|
| **资源泄漏** | import 时建的 SQLite 连接**从不 close** | 测试进程退出时由解释器回收（可能报 ResourceWarning）|

### 如果重构，正确形态是什么

```python
# 现在是：
# app/main.py（模块级）
settings = get_settings()
llm = LLMClient(settings)
store = RunStore(settings.db_path)
_agents = {...}
app = FastAPI(...)

# 应该是：
def create_app(settings: Settings, *, llm=None, store=None) -> FastAPI:
    llm = llm or LLMClient(settings)
    store = store or RunStore(settings.db_path)
    agents = {...}
    app = FastAPI(lifespan=make_lifespan(...))
    app.state.agents = agents
    ...
    return app

app = create_app(get_settings())   # 生产仍是一行
```

**收益**：测试可以 `create_app(Settings(db_path=str(tmp_path/"t.db")), llm=FakeLLM())` → **零 monkeypatch、零仓库污染、零单例缓存问题**。

**代价**：`main.py` 从 40 行装配变成 ~60 行工厂 + 一行默认调用。**这是"为可测性付出的结构成本"，对个人项目是否值得是一个判断** —— 但当前状态确实让"测试 `app.main`"需要 3 种不同的 monkeypatch 技巧。

### 与既有取舍的一致性

值得注意的是：**`app/cli.py` 完全没有用模块级单例**（每个子命令自己装配、自己 close），`app/harness/retry.py`、`app/tools/__init__.py`、`app/scheduler/runner.py` 都是纯工厂函数。**所以"模块级单例"只在 `main.py` 与 `config.py` 两处。**

**`config.py` 的单例有正当理由**（配置只读、多处读取），但它**恰好是测试最需要打破的**。**`main.py` 的单例则没有任何技术必要性** —— 它只是"少写几行"。

## 18.4 综合：这套设计的规模边界

把三个问题合起来，回答"这套设计在什么规模下失效"：

| 维度 | 安全区间 | 失效点 | 是否有优雅的逃生通道 |
|---|---|---|---|
| **笔记检索（延迟）** | ≤ 1 万篇 / 2–3 万 chunk | 检索 > 1 s，Agent 多轮累加 | ✓ `vector_store.py` 只改两处 |
| **笔记检索（质量）** | **任何规模** | **短文档系统性成为相似度磁铁** | ✗ 需要算法改动（长度归一化）|
| **状态持久化（并发）** | 单写进程（可多线程）+ 任意读 | 两个写进程 / 网络盘 | ✓ `run_store.py` 只改一处（+加锁）|
| **状态持久化（写放大）** | 步数 ≤ 10（`agent_max_steps`）| 步数 50+ 时 O(N²) 写 | △ 需要改 schema |
| **测试隔离** | 单进程串行测试 | 需要真并发/多配置测试 | ✓ 提取 `create_app` 工厂 |
| **多 Agent 编排** | ≤ 3 个专家、单跳 | 长链路（接力 5+ 段）时 index 重复、无编排级可观测性 | △ 需要重编号 + 编排级 RunRecord |

**最诚实的总结是三句话**：

1. **延迟与并发问题都有明确的、低成本的逃生通道** —— 因为每个选型点都写了"换实现只改这一处"（`vector_store.py:4`、`run_store.py:4`、`gateway/__init__.py:7`）。**这是"接口留换点"这条主线的回报。**
2. **检索质量问题（短文档磁铁）没有任何逃生通道** —— 它不是性能问题而是**算法问题**，加机器无效。**这是最值得优先处理的一项**，而且它**在 20 篇笔记的规模上就已经暴露**。
3. **可测性问题是最纯的"结构债"** —— 它有确定的解法（`create_app` 工厂），没有技术难度，只是需要一次重构。**它的代价不在生产而在维护**：每加一个 API 端点测试，都要重新决定 monkeypatch 哪一层。

---

# 附录 A：本会话实测结果原始数据

## A.1 环境

| 项 | 值 |
|---|---|
| Python | `.venv\Scripts\python.exe` = **3.14.7** |
| 逻辑处理器 | **32** |
| 聊天模型 | `data/eval/models/qwen2.5-1.5b-instruct-q4_k_m.gguf`（**1,117,320,736 字节**）|
| 嵌入模型 | `data/eval/models/nomic-embed-text-v1.5.Q8_0.gguf`（**146,146,432 字节**）|
| 推理栈 | `data/eval/llama/llama-server.exe`（llama.cpp），纯 CPU |

**三个后台服务**（用 `pwsh run_in_background: true` 托管；**`Start-Process` 启动的进程会被 DSH 在命令返回时回收** —— 这是本会话踩到的关键坑）：

```
pwsh-104: llama-server -m ...qwen2.5-1.5b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 11500 -c 4096 -t 8
pwsh-105: llama-server -m ...nomic-embed-text-v1.5.Q8_0.gguf --port 11501 --embeddings -c 2048 -t 8
pwsh-106: python -m scripts.eval_router --port 11434 --chat http://127.0.0.1:11500 --embed http://127.0.0.1:11501
```

**环境变量模板**（每次跑 Warden 都要设）：

```powershell
$env:WARDEN_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:WARDEN_LLM_MODEL = "qwen2.5-1.5b-instruct"
$env:WARDEN_LLM_API_KEY = "local"
$env:WARDEN_EMBEDDING_MODEL = "nomic-embed-text-v1.5"
$env:PYTHONIOENCODING = "utf-8"
```

**工具探测的注意点**：llama-server **没有 `/health`**（探它会得到 502）；探活要用 `/v1/models`。router 有 `/health` → 返回 `ok`。

**启动日志中的两个自动调整**：

```
embeddings enabled with n_batch (2048) > n_ubatch (512) → setting n_batch = n_ubatch = 512 to avoid assertion failure
embeddings required but some input tokens were not marked as outputs -> overriding
```

## A.2 哈希派生可复现性（逐字节重算）

真实调用一次 `LLMClient.chat([{"role":"user","content":"Reply with exactly: WARDEN-OK"}])` 后：

```
client.last_gateway = GatewayOutcome(
    key='warden-ed03199b6848caea4413c6c63c190b9cd706b797',
    session_id='warden-ca2dd12ee73ad7c7a765f49fc1947972',
    replayed=False, replay_origin='', replay_age_ms=0,
    replay_upstream='', upstream_name='', attempts=1)
```

**从首条 user 消息独立重算的两个值**：

| 重算 | 结果 | 与 live 值比对 |
|---|---|---|
| `derive_session_id([{"role":"user","content":"Reply with exactly: WARDEN-OK"}])` | `warden-ca2dd12ee73ad7c7a765f49fc1947972` | **✓ 逐字节一致** |
| `derive_idempotency_key(...)` | `warden-ed03199b6848caea4413c6c63c190b9cd706b797` | **✓ 逐字节一致** |

**其他派生性质实测**：

| 输入变化 | 结果 |
|---|---|
| 单条 user | `warden-cbe30936f8017c0f036be13d65790b88`（**长度 39** = "warden-" + 32 hex）|
| 追加 2 条后续消息 | session id **不变** |
| **前面插一条 system** | session id **不变**（`_first_user_content` 只认 `role=="user"`）|
| 无 user 消息 / 空列表 | `None` |
| 幂等键长度 | **47** = "warden-" + 40 hex |
| **payload 键序打乱** | 幂等键**不变**（`canonical_body` 的 `sort_keys=True`）|
| 第 2 轮（消息不同） | 幂等键**不同** |
| `canonical_body({'b':2,'a':1})` | `'{"a":1,"b":2}'` |

## A.3 实验一：崩溃恢复评测（独立复现）

命令：

```powershell
python -m scripts.eval_resume --trials 6 --tasks a,b `
    --db data/exp_resume.db --out data/eval/exp-resume.json --timeout 300
```

输出（逐行）：

```
[1/6] a crash_at=1 eval-a8f18b5b2ac3 → resumed_ok status=ok answer='12' steps 1→2
[2/6] b crash_at=1 eval-b52c2e51ba12 → resumed_ok answer='244' steps 1→2
[3/6] b crash_at=2 eval-417c77814b07 → resumed_ok answer='244' steps 2→3
[4/6] a crash_at=1 eval-9cf2d65bce50 → resumed_ok answer='12' steps 1→2
[5/6] b crash_at=1 eval-1ec573c3d73f → resumed_ok answer='244' steps 1→2
[6/6] b crash_at=2 eval-309d81b6558e → resumed_ok answer='244' steps 2→3

崩溃（半成品已落库）6 次（另有 0 次未触发/无落库，已剔除），恢复成功 6 次 → 恢复成功率 100.0%
```

**每个 trial 的关键字段**：`crash_exit=70`、`status_before_resume="running"`、`status_after_resume="ok"`、`duplicate_steps=0`、`steps_before_resume ∈ {1, 2}`。

**历史产物**（`data/eval/resume.json`，30 trials）也一致：`model=qwen2.5-1.5b-instruct`、`endpoint=http://127.0.0.1:11500/v1`、`trials=30`、`crashed=30`、`resumed_ok=30`、`resume_success_rate=1.0`。

## A.4 实验二：FunctionCall 断点续跑上下文审计（本会话新增脚本）

命令：

```powershell
python -m scripts.exp_fc_resume --phase crash  --run-id <id> --crash-at 2
python -m scripts.exp_fc_resume --phase resume --run-id <id>
```

提示词：`用计算器算 3 * 4，再用计算器算 5 * 6，最后只回答第二个结果。`

**crash 阶段**：退出码 **70**。

**DB 里的状态**：`steps=2`、`meta['messages']` **5 条**：

```
[0] system      （_SYSTEM_TEMPLATE 渲染后）
[1] user        content='用计算器算 3 * 4，再用计算器算 5 * 6，最后只回答第二个结果。'
[2] assistant   tool_calls=['calculator', 'calculator']
[3] tool        content='12'
[4] tool        content='30'
```

**resume 阶段**：答案 `'30'`（正确）。

**首次请求的上下文形状**（关键验证）：

```
resume 实际发给模型的第 1 次请求含 5 条消息
  其中 role='tool' 2 条
  role='user' 且以 "Observation:" 开头 0 条     ← 没有重复注入
```

**为什么没有重复**（设计原因）：`function_call.py:109`/`:148` 调 `_persist_step(record, s, None)` 时**显式传 `raw=None`**，所以落库步骤里**没有 `raw` 键**；`_resume`（`:80–92`）走的是 `record.meta["messages"]`（native 上下文已完整含 assistant + tool 消息），从 `record.steps` 只重建 `AgentStep`、**不回填任何消息**。"meta 里的 native 消息"与"record.steps 里的结构化步骤"是**互不重叠的两套信息**，重复注入在上游就被避免了。

## A.5 实验三：检索评测

命令：

```powershell
python -m scripts.eval_retrieval --compare --sweep 1,3,4,8 --k 4 `
    --db data/exp_retrieval.db --out data/eval/exp-retrieval.json --verbose
```

端点 `11434/v1`、嵌入模型 `nomic-embed-text-v1.5`、语料 **20 篇**、已索引 **20 块**、查询 **40 条**（k=8）。

| 组 | n | Hit@1 | Hit@3 | Hit@4 | Hit@8 | MRR | p50 | p95 |
|---|---|---|---|---|---|---|---|---|
| **纯向量** | 40 | **42.5%** | 55.0% | 65.0% | 85.0% | **0.538** | **88 ms** | 107 ms |
| **改写+重排** | 40 | **10.0%** | 55.0% | 57.5% | — | **0.323** | **4383 ms** | 5684 ms |
| **差值** | | **−32.5%** | 0 | −7.5% | | **−0.215** | | |

**Hit@K 曲线**（同一份 k=8 排序，无重新检索噪声）：

```
Hit@K 曲线      : Hit@1=42.5% Hit@3=55.0% Hit@4=65.0% Hit@8=85.0%（同一份排序，k=8）
```

**两条比 README 那张表更多的解读**：

1. **改写+重排让 Hit@1 掉 3/4 但 Hit@3 保持同水平** → 它把正确答案**从第 1 位推到第 2–3 位，而不是推出候选集**。所以问题是**重排的排序质量**（本地 1.5B 模型排序能力不足），不是召回。
2. **代价是 50 倍延迟**（88ms → 4383ms）—— 每个查询多两次 LLM 调用。

**MISS 明细里的系统性现象**（`--verbose` 输出 19 条 MISS）：`zettel`、`type-hints`、`unit-test`、`semantic-cache` 高频出现。**`want=zettel` 的两条查询**：

```
MISS  '怎样记笔记以后才方便复用'
      want=zettel got=['sqlite-wal', 'image-layer', ...]

MISS  '收集了很多材料但是记不住怎么办'
      want=zettel got=['sqlite-wal', 'image-layer', ...]
```

→ **失败不是随机的：短文档在高维 embeddings 下系统性获得更高余弦相似度，`zettel`（约 70 字）这类短笔记是"相似度磁铁"。**

## A.6 沙箱环境的两个产物（非代码缺陷）

1. **`tests/test_cli.py` 在本沙箱下全部报 `PermissionError: [WinError 5] 拒绝访问`**。根因：DSH 沙箱把 `TEMP`/`TMP` 设为 `C:\Users\20106\AppData\Local\Temp\dsh-FzedQx`，pytest 的 `tmpdir.py:213 getbasetemp` → `pathlib.py:420 make_numbered_dir_with_cleanup` → `pathlib.py:175 find_prefixed` 里 `os.scandir(root)` 被 OS 拒绝（`pytest-of-20106` 目录不可扫描）。**`--basetemp=.pytest_tmp` 不能修复**（仍 18 errors）。其余测试正常（实测 `14 passed, 3 deselected`）。
2. **`Get-CimInstance Win32_OperatingSystem` 被沙箱拒绝**（拿不到 RAM 总量）；`$env:NUMBER_OF_PROCESSORS` = **32** 可用。

## A.7 已完成的实验状态汇总

| 实验 | 状态 | 结果 |
|---|---|---|
| 三种循环 + 两个协作模式实跑（`scripts/smoke.py`）| 历史产物 | ALL PASS（react 12、planact 3.0、function_call 59、research 与 team 非空）|
| 崩溃恢复（`eval_resume.py`）| **本会话独立复现** | **6/6 = 100%**（历史 30/30）|
| 检索评测（`eval_retrieval.py`）| **本会话完成** | 纯向量 Hit@1 42.5% / MRR 0.538；改写+重排 Hit@1 10.0% / MRR 0.323 |
| FunctionCall 续跑审计（`exp_fc_resume.py`）| **本会话完成** | 无重复注入（5 条消息、0 条 `Observation:`）|
| 网关实网回归（`test_gateway_live.py`）| 历史产物 | 3 passed（两轮同会话、回放不打上游 `6→6`、capabilities 128000）|
| **新增 Tool/Task/Agent 循环验证扩展点** | **未开始** | 见下 |

**唯一未完成的实验**：验证"`ToolRegistry.register` + 一个新 Tool 类"与"3 个任务 + 1 个新任务"的扩展点。**代码层面的结论已经明确**（`tools/registry.py:1–4` 声明了零改动、`tools/__init__.py:22` 是唯一的注册点、`scheduler/registry.py` 与 `tasks/__init__.py:19` 同构），**但缺少"真的加一个工具/任务跑通"的实测**。

---

# 附录 B：全仓文件清单与行数

> 行数为 `read` 工具口径（真实文件行数）。

## app/（51 个 .py，3455 行 + index.html 120 行）

| 文件 | 行数 | 职责 |
|---|---|---|
| `app/__init__.py` | 3 | `__version__ = "0.1.0"` |
| `app/config.py` | 112 | pydantic-settings 配置 + 单例 |
| `app/net.py` | 35 | 本机 URL 绕开系统代理 |
| `app/schemas.py` | 48 | API 请求/响应模型 |
| `app/main.py` | 166 | FastAPI 入口 + 模块级装配 |
| `app/llm.py` | 368 | LLM 客户端 + M6 网关语义 |
| `app/cli.py` | 379 | 九个子命令 |
| `app/agent/__init__.py` | 5 | 导出 5 个名字 |
| `app/agent/base.py` | 300 | 模板方法 + JSON 解析 |
| `app/agent/react.py` | 235 | ReAct 循环 |
| `app/agent/planact.py` | 204 | PlanAct 循环 |
| `app/agent/function_call.py` | 161 | 原生 function calling |
| `app/agent/orchestrator.py` | 47 | 路由 |
| `app/agent/pipeline.py` | 43 | 接力 |
| `app/agent/blackboard.py` | 80 | 共享黑板 |
| `app/harness/__init__.py` | 33 | 导出 18 个名字 |
| `app/harness/run_store.py` | 154 | SQLite 运行记录 |
| `app/harness/retry.py` | 43 | 指数退避 |
| `app/harness/trace.py` | 49 | trace_id + 日志 |
| `app/harness/metrics.py` | 68 | Prometheus 六指标 |
| `app/harness/cost.py` | 13 | 成本折算 |
| `app/gateway/__init__.py` | 44 | 导出 17 个名字 |
| `app/gateway/identity.py` | 131 | 会话 id / 幂等键派生 |
| `app/gateway/capabilities.py` | 167 | 能力探测 + TTL 缓存 |
| `app/mcp/__init__.py` | 14 | 导出 6 个名字 |
| `app/mcp/client.py` | 112 | JSON-RPC over stdio |
| `app/mcp/tools.py` | 57 | MCP → Tool 适配 |
| `app/mcp/registry.py` | 24 | MCP 注册表工厂 |
| `app/memory/__init__.py` | 16 | 导出 7 个名字 |
| `app/memory/embeddings.py` | 40 | `/embeddings` 客户端 |
| `app/memory/indexer.py` | 81 | 分块 + 嵌入 + 入库 |
| `app/memory/vector_store.py` | 76 | SQLite 暴力余弦 |
| `app/memory/retriever.py` | 95 | 召回 + 改写/重排 |
| `app/scheduler/__init__.py` | 13 | 导出 7 个名字 |
| `app/scheduler/base.py` | 46 | `TaskResult` / `Services` / `BaseTask` |
| `app/scheduler/registry.py` | 31 | 任务注册表 |
| `app/scheduler/runner.py` | 97 | APScheduler 装配 + 落库/重试/通知 |
| `app/notify/__init__.py` | 17 | `get_notifier` |
| `app/notify/base.py` | 12 | `Notifier` ABC |
| `app/notify/console.py` | 12 | 控制台通知 |
| `app/notify/file.py` | 24 | Markdown 文件通知 |
| `app/tasks/__init__.py` | 25 | 默认任务注册表 |
| `app/tasks/news_digest.py` | 132 | RSS 简报 |
| `app/tasks/repo_report.py` | 109 | 代码库报告 |
| `app/tasks/weekly_review.py` | 74 | 周报 |
| `app/tools/__init__.py` | 43 | `build_default_registry` |
| `app/tools/base.py` | 76 | `Tool` dataclass + 校验/超时 |
| `app/tools/registry.py` | 50 | 工具注册表 |
| `app/tools/builtin/__init__.py` | 4 | 导出两个工具 |
| `app/tools/builtin/calculator.py` | 64 | AST 白名单计算器 |
| `app/tools/builtin/datetime_tool.py` | 25 | 当前时间 |
| `app/tools/builtin/search_notes.py` | 42 | RAG 读 |
| `app/tools/builtin/save_note.py` | 38 | RAG 写 |
| `app/tools/builtin/blackboard.py` | 53 | 黑板读写工具 |
| `app/static/index.html` | 120 | 零构建控制台 |

## scripts/（5 个，913 行）

| 文件 | 行数 | 用途 |
|---|---|---|
| `scripts/eval_resume.py` | 304 | 跨进程硬崩 + resume 评测 |
| `scripts/eval_retrieval.py` | 374 | 检索 Hit@K / MRR 评测 |
| `scripts/eval_router.py` | 132 | 纯标准库 OpenAI 兼容路由器 |
| `scripts/smoke.py` | 131 | 端到端冒烟 |
| `scripts/exp_fc_resume.py` | 137 | **本会话新增**：FunctionCall 续跑审计 |

## tests/（34 个，2746 行）

| 文件 | 行 | 文件 | 行 |
|---|---|---|---|
| `test_api.py` | 165 | `test_mcp.py` | 79 |
| `test_blackboard.py` | 98 | `test_mcp_registry.py` | 72 |
| `test_cli.py` | 311 | `test_memory.py` | 146 |
| `test_config.py` | 92 | `test_metrics.py` | 20 |
| `test_context.py` | 77 | `test_net.py` | 146 |
| `test_cost.py` | 51 | `test_notify.py` | 62 |
| `test_dashboard.py` | 20 | `test_orchestrator.py` | 62 |
| `test_eval_retrieval.py` | 31 | `test_pipeline.py` | 84 |
| `test_eval_router.py` | 156 | `test_planact.py` | 82 |
| `test_function_call.py` | 77 | `test_react.py` | 162 |
| `test_gateway.py` | **408** | `test_reflect.py` | 69 |
| `test_gateway_live.py` | 131 | `test_registry.py` | 26 |
| `test_harness.py` | 66 | `test_resume.py` | 115 |
| `test_llm_client.py` | 98 | `test_retry.py` | 63 |
| `test_save_note.py` | 67 | `test_scheduler.py` | 197 |
| `test_self_eval.py` | 76 | `test_tasks.py` | 113 |
| `test_tools.py` | 102 | `test_weekly_review.py` | 159 |

## 顶层与部署

| 文件 | 行数 | 要点 |
|---|---|---|
| `pyproject.toml` | 63 | 7 个运行时依赖；ruff 行宽 100；pytest `pythonpath=["."]`（CI 修复）|
| `README.md` | 193 | 三条动机 + 架构图 + 配置表 + 评测结果 |
| `CHANGELOG.md` | 34 | 0.1.0 / 0.2.0 / 未发布 |
| `Dockerfile` | 28 | `python:3.14-slim`、非 root uid 10001、stdlib HEALTHCHECK |
| `deploy/docker-compose.yml` | 62 | app + prometheus + grafana（**grafana 钉 11.6.0**）|
| `deploy/prometheus/prometheus.yml` | 15 | scrape 15s、target `app:8000` |
| `deploy/prometheus/alerts.yml` | 16 | `WardenHighFailureRate`（5m 窗口 >20%，`clamp_min` 防除零）|
| `deploy/grafana/.../prometheus.yml` | 13 | datasource **uid 固定 `prometheus`** |
| `deploy/grafana/.../dashboards/warden.yml` | 13 | provider 自动载入 `/var/lib/grafana/dashboards` |
| `deploy/grafana/dashboards/warden.json` | 249 | 8 面板 |
| `.github/workflows/ci.yml` | 47 | lint（ruff 钉 0.16.10）+ test（3.11/3.14 matrix）+ docker build |
| `.env.example` | 86 | 全部配置项 |
| `uv.lock` | 1021 | 39 个依赖锁定 |

---

# 结语：这个项目的教学设计价值

走完 7114 行，Warden 最有价值的不是它的功能（个人 Agent 工作台），而是**它把每一个工程判断的理由都留在了代码里**。这份讲义里引用最多的不是代码行，而是 docstring 与注释：

| 位置 | 留下的判断 |
|---|---|
| `net.py:1–9` | 系统代理劫持 127.0.0.1 的实验现象与解法 |
| `base.py:292–293` | 为什么必须用 `replace` 而不是 `.format()` |
| `retriever.py:75` | 延迟导入避免模块循环 |
| `pipeline.py:32` | 用 f-string 而非 `.format()`（同一个教训的第二次） |
| `react.py:7–9` | 为什么用 JSON 而非经典 ReAct 文本格式 |
| `run_store.py:4` / `vector_store.py:4` | 换实现只改这一处 |
| `gateway/__init__.py:1–8` | "只差三个请求头和一个 GET" |
| `identity.py:3–5` | trace_id 为什么不能当会话 id |
| `capabilities.py` docstring | "探不到就返回 None，绝不打断对话" |
| `repo_report.py:74–75` / `weekly_review.py:42–43` | Windows GBK 让整段 git log 静默丢失 |
| `eval_resume.py:1–18` | 为什么 `os._exit` 等价于真实断电 |
| `tools/base.py:54–55` | "Python 线程无法被强杀" |
| `cli.py:102` | 早退路径漏 `store.close()` 的修复记录 |
| `llm.py:340–342` | 诚实说明"实现了但没接进去"及其理由 |

**这些注释构成了一个"决策日志"** —— 它们记录的不是"做了什么"，而是"为什么这样做"以及"不这样做会怎样"。这正是让一份代码可以被别人（或半年后的自己）真正理解的东西。

而这份讲义里第 17 章的 24 项缺口，**其中大部分（⑧⑨⑩⑪⑫⑭⑯⑱⑲⑳㉑㉒㉓）是我通过交叉引用多个文件发现的**，而不是任何单个文件里的错误 —— 这本身说明了一件事：**当一个项目的文档化水平足够高时，缺口会显现为"两处说法不一致"，而不是"某处看不出来"**。`"error: "` 与 `"error:"` 的差异、四处版本号、`steps` 的重编号，都是这一类。
