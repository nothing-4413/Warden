"""Warden 配置：pydantic-settings 从环境变量 / .env 读取，字段带类型与默认值。

设计取舍：所有开关集中在此文件，新增配置只改这一处；类型写错时在启动那一刻就按字段名报错，
不必等到第一次用到才抛 ValueError。真实环境变量优先于 .env（.env 不覆盖已存在的变量）。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """字段对应的环境变量 = WARDEN_ + 字段名大写（如 llm_model ← WARDEN_LLM_MODEL）。"""

    model_config = SettingsConfigDict(
        env_prefix="WARDEN_",
        env_file=".env",
        env_file_encoding="utf-8",
        # .env 里写成 `KEY=`（留空）的项按未设置处理，回落默认值
        env_ignore_empty=True,
        # 无关的 WARDEN_* 变量（历史遗留 / 其它工具）不影响启动
        extra="ignore",
        frozen=True,
    )

    # LLM（OpenAI 兼容接口；指向 Ollama 的 /v1 即本地模型）
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    llm_temperature: float = 0.0
    llm_timeout_s: float = 120.0

    # Agent 循环
    agent_max_steps: int = 10
    # 工具观测截断上限（上下文工程：防止长工具输出撑爆上下文窗口，0 = 不截断）
    max_observation_chars: int = 2000
    # 多轮上下文窗口上限：历史超过此条数时，把最旧消息压成一段摘要（0 = 不压缩，保留全部历史）
    context_max_messages: int = 30
    # 自反思（Reflexion-lite）：给出最终答案前让模型自我校验，必要时回补（true 开启，多一次 LLM 调用）
    reflect_enabled: bool = False
    # PlanAct 动态重规划：某步工具执行失败时重规划剩余步骤的上限次数（0 = 关闭重规划）
    max_replans: int = 2
    # 置信度自评（self-eval）：给出最终答案后让模型评估自己答案的可信度（true 开启，多一次 LLM 调用）
    self_eval_enabled: bool = False

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # 调度与任务（M1）
    notify_kind: str = "console"  # console | file
    report_dir: str = "reports"
    data_dir: str = "data"
    rss_sources: str = "https://news.ycombinator.com/rss,https://export.arxiv.org/rss/cs.AI"
    notes_dir: str = ""
    repo_path: str = "."

    # Harness（M2）
    db_path: str = "data/warden.db"
    retry_attempts: int = 3
    retry_backoff_s: float = 1.0
    metrics_enabled: bool = True

    # 记忆 RAG（M3）
    embedding_model: str = "nomic-embed-text"
    memory_db_path: str = "data/memory.db"
    chunk_size: int = 600
    chunk_overlap: int = 100
    retrieval_top_k: int = 4
    # 检索相似度阈值：低于该分数的片段不注入上下文（0 = 不过滤）
    retrieval_min_score: float = 0.0
    # 检索增强：检索前用 LLM 改写查询提升召回、检索后用 LLM 重排 top-k 提升精度（各多一次 LLM 调用）
    rag_rewrite_enabled: bool = False
    rag_rerank_enabled: bool = False

    # MCP（M4）：[{"name": ..., "command": [...]}]，环境变量里写 JSON
    mcp_servers: list[dict] = []

    # 成本统计（M5）：单价 USD / 1M tokens（默认 0 = 本地模型免费）
    llm_input_price_per_1m: float = 0.0
    llm_output_price_per_1m: float = 0.0

    # 网关集成 InferGate（M6）：会话/租户归属 + 幂等重放 + 能力发现
    # 是否发送 X-InferGate-Session / X-InferGate-Tenant / Idempotency-Key。
    # 未知请求头会被普通 OpenAI 端点忽略，所以默认开启是安全的；关掉即退化为普通请求。
    gateway_enabled: bool = True
    # 租户：InferGate 按租户隔离会话账本与幂等缓存；缺省 anonymous（匿名）
    gateway_tenant: str = "warden"
    # 显式钉死会话 id（留空 = 由本轮完整历史的首条用户消息确定性派生，跨轮稳定、跨进程可复现）
    gateway_session: str = ""
    # 409 infergate_idempotency_in_flight（同键的原始请求仍在飞行）的重试次数上限
    gateway_retry_attempts: int = 3
    # 上面那种重试的等待秒数：优先用响应里的 Retry-After，缺失时用这个缺省值
    gateway_retry_after_s: float = 1.0
    # 能力发现（GET {base_url}/capabilities）：开关与 TTL 秒数；探不到就返回 None，不影响对话
    gateway_capabilities_enabled: bool = True
    gateway_capabilities_ttl_s: float = 60.0

    @property
    def rss_source_list(self) -> list[str]:
        return [s.strip() for s in self.rss_sources.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    """进程内单例（配置只读，读一次即可）。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
