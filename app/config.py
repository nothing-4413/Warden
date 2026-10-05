"""Warden 配置：从环境变量读取（可选加载 .env），全部有默认值。

设计取舍：不引入 python-dotenv，用一个约 10 行的极简 loader；
所有配置集中在此文件，新增开关只改这一处。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _load_dotenv(path: str = ".env") -> None:
    """极简 .env 加载：只处理 KEY=VALUE 行，不覆盖已存在的环境变量。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


# 在 Settings 定义之前加载，这样字段默认值能读到 .env 里的值
_load_dotenv()


@dataclass(frozen=True)
class Settings:
    # LLM（OpenAI 兼容接口；指向 Ollama 的 /v1 即本地模型）
    llm_base_url: str = os.getenv("WARDEN_LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = os.getenv("WARDEN_LLM_API_KEY", "ollama")
    llm_model: str = os.getenv("WARDEN_LLM_MODEL", "qwen2.5:7b")
    llm_temperature: float = float(os.getenv("WARDEN_LLM_TEMPERATURE", "0"))
    llm_timeout_s: float = float(os.getenv("WARDEN_LLM_TIMEOUT_S", "120"))

    # Agent 循环
    agent_max_steps: int = int(os.getenv("WARDEN_AGENT_MAX_STEPS", "10"))
    # 工具观测截断上限（上下文工程：防止长工具输出撑爆上下文窗口，0 = 不截断）
    max_observation_chars: int = int(os.getenv("WARDEN_MAX_OBSERVATION_CHARS", "2000"))
    # 多轮上下文窗口上限：历史超过此条数时，把最旧消息压成一段摘要（0 = 不压缩，保留全部历史）
    context_max_messages: int = int(os.getenv("WARDEN_CONTEXT_MAX_MESSAGES", "30"))
    # 自反思（Reflexion-lite）：给出最终答案前让模型自我校验，必要时回补（true 开启，多一次 LLM 调用）
    reflect_enabled: bool = os.getenv("WARDEN_REFLECT_ENABLED", "false").lower() in ("1", "true", "yes")
    # PlanAct 动态重规划：某步工具执行失败时重规划剩余步骤的上限次数（0 = 关闭重规划）
    max_replans: int = int(os.getenv("WARDEN_MAX_REPLANS", "2"))
    # 置信度自评（self-eval）：给出最终答案后让模型评估自己答案的可信度（true 开启，多一次 LLM 调用）
    self_eval_enabled: bool = os.getenv("WARDEN_SELF_EVAL_ENABLED", "false").lower() in ("1", "true", "yes")

    # API
    api_host: str = os.getenv("WARDEN_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("WARDEN_API_PORT", "8000"))

    # 调度与任务（M1）
    notify_kind: str = os.getenv("WARDEN_NOTIFY_KIND", "console")  # console | file
    report_dir: str = os.getenv("WARDEN_REPORT_DIR", "reports")
    data_dir: str = os.getenv("WARDEN_DATA_DIR", "data")
    rss_sources: str = os.getenv(
        "WARDEN_RSS_SOURCES",
        "https://news.ycombinator.com/rss,https://export.arxiv.org/rss/cs.AI",
    )
    notes_dir: str = os.getenv("WARDEN_NOTES_DIR", "")
    repo_path: str = os.getenv("WARDEN_REPO_PATH", ".")

    # Harness（M2）
    db_path: str = os.getenv("WARDEN_DB_PATH", "data/warden.db")
    retry_attempts: int = int(os.getenv("WARDEN_RETRY_ATTEMPTS", "3"))
    retry_backoff_s: float = float(os.getenv("WARDEN_RETRY_BACKOFF_S", "1.0"))
    metrics_enabled: bool = os.getenv("WARDEN_METRICS_ENABLED", "true").lower() in ("1", "true", "yes")

    # 记忆 RAG（M3）
    embedding_model: str = os.getenv("WARDEN_EMBEDDING_MODEL", "nomic-embed-text")
    memory_db_path: str = os.getenv("WARDEN_MEMORY_DB_PATH", "data/memory.db")
    chunk_size: int = int(os.getenv("WARDEN_CHUNK_SIZE", "600"))
    chunk_overlap: int = int(os.getenv("WARDEN_CHUNK_OVERLAP", "100"))
    retrieval_top_k: int = int(os.getenv("WARDEN_RETRIEVAL_TOP_K", "4"))
    # 检索相似度阈值：低于该分数的片段不注入上下文（0 = 不过滤）
    retrieval_min_score: float = float(os.getenv("WARDEN_RETRIEVAL_MIN_SCORE", "0"))
    # 检索增强：检索前用 LLM 改写查询提升召回、检索后用 LLM 重排 top-k 提升精度（各多一次 LLM 调用）
    rag_rewrite_enabled: bool = os.getenv("WARDEN_RAG_REWRITE_ENABLED", "false").lower() in ("1", "true", "yes")
    rag_rerank_enabled: bool = os.getenv("WARDEN_RAG_RERANK_ENABLED", "false").lower() in ("1", "true", "yes")

    # MCP（M4）：JSON 数组 [{name, command:[...]}]
    mcp_servers_json: str = os.getenv("WARDEN_MCP_SERVERS", "")

    # 成本统计（M5）：单价 USD / 1M tokens（默认 0 = 本地模型免费）
    llm_input_price_per_mtok: float = float(os.getenv("WARDEN_LLM_INPUT_PRICE_PER_1M", "0"))
    llm_output_price_per_mtok: float = float(os.getenv("WARDEN_LLM_OUTPUT_PRICE_PER_1M", "0"))

    # 网关集成 InferGate（M6）：会话/租户归属 + 幂等重放 + 能力发现
    # 是否发送 X-InferGate-Session / X-InferGate-Tenant / Idempotency-Key。
    # 未知请求头会被普通 OpenAI 端点忽略，所以默认开启是安全的；关掉即退化为普通请求。
    gateway_enabled: bool = os.getenv("WARDEN_GATEWAY_ENABLED", "true").lower() in ("1", "true", "yes")
    # 租户：InferGate 按租户隔离会话账本与幂等缓存；缺省 anonymous（匿名）
    gateway_tenant: str = os.getenv("WARDEN_GATEWAY_TENANT", "warden")
    # 显式钉死会话 id（留空 = 由本轮完整历史的首条用户消息确定性派生，跨轮稳定、跨进程可复现）
    gateway_session: str = os.getenv("WARDEN_GATEWAY_SESSION", "")
    # 409 infergate_idempotency_in_flight（同键的原始请求仍在飞行）的重试次数上限
    gateway_retry_attempts: int = int(os.getenv("WARDEN_GATEWAY_RETRY_ATTEMPTS", "3"))
    # 上面那种重试的等待秒数：优先用响应里的 Retry-After，缺失时用这个缺省值
    gateway_retry_after_s: float = float(os.getenv("WARDEN_GATEWAY_RETRY_AFTER_S", "1"))
    # 能力发现（GET {base_url}/capabilities）：开关与 TTL 秒数；探不到就返回 None，不影响对话
    gateway_capabilities_enabled: bool = os.getenv(
        "WARDEN_GATEWAY_CAPABILITIES_ENABLED", "true").lower() in ("1", "true", "yes")
    gateway_capabilities_ttl_s: float = float(os.getenv("WARDEN_GATEWAY_CAPABILITIES_TTL_S", "60"))

    @property
    def rss_source_list(self) -> list[str]:
        return [s.strip() for s in self.rss_sources.split(",") if s.strip()]

    @property
    def mcp_servers(self) -> list[dict]:
        """解析 WARDEN_MCP_SERVERS JSON 为 [{name, command:[...]}]，空则 []。"""
        if not self.mcp_servers_json.strip():
            return []
        return json.loads(self.mcp_servers_json)


_settings: Settings | None = None


def get_settings() -> Settings:
    """进程内单例（M0 配置只读，够用；M2 引入持久化后再扩展）。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
