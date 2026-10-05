"""InferGate 能力发现（M6）：GET {base_url}/v1/capabilities 的懒加载 + TTL 缓存。

上下文窗口只有网关知道（InferGate 配置里 models.<name>.context_window）。
在这之前 Warden 只能靠 WARDEN_CONTEXT_MAX_MESSAGES 猜一个"保留多少条消息"，
完全不知道模型真实能吃多少 token。

本模块是取这个值的唯一入口，并且刻意做成"探不到就返回 None"：
裸 OpenAI、旧版网关、网络故障、500、非 JSON 响应全部退化为 None，
调用方按老行为走，绝不因为一次探测失败打断对话。
失败结果同样进缓存，否则对着一个不提供该端点的网关，每次 LLM 调用都要多花一次
无用的 HTTP 请求。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CAPABILITIES_PATH = "capabilities"


def capabilities_url(base_url: str) -> str:
    """由 OpenAI 兼容 base_url 推出能力端点。

    base_url 通常已经以 /v1 结尾（http://host:18939/v1）→ /v1/capabilities；
    少见地不带 /v1 时补上，因为该端点固定在网关的 /v1 命名空间下。
    """
    base = (base_url or "").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/{CAPABILITIES_PATH}"


@dataclass(frozen=True)
class ModelCapability:
    """一个模型的声明式能力（对应 InferGate 的 ModelCapability）。"""

    model: str
    context_window: int = 0
    max_output_tokens: int = 0
    capabilities: tuple[str, ...] = ()
    available: bool = True

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ModelCapability":
        """从一条 models[] 记录构造。

        context_window / max_output_tokens 在 InferGate 里带 omitempty，
        值为 0 时字段直接不出现，所以必须用 get 而不是下标。
        """
        caps = data.get("capabilities")
        return cls(
            model=str(data.get("model") or ""),
            context_window=int(data.get("context_window") or 0),
            max_output_tokens=int(data.get("max_output_tokens") or 0),
            capabilities=tuple(str(c) for c in caps) if isinstance(caps, list) else (),
            available=bool(data.get("available", True)),
        )


@dataclass
class _Entry:
    """一次探测的结果（含失败），连带取回的时刻。"""

    report: dict[str, Any] | None
    fetched_at: float


class CapabilityCache:
    """进程内的能力报告缓存：按 URL 分开，TTL 内不重复请求。

    ttl_s <= 0 表示不缓存（每次都探测，测试与排查时用）。
    """

    def __init__(self, ttl_s: float = 60.0) -> None:
        self._ttl_s = float(ttl_s)
        self._entries: dict[str, _Entry] = {}

    def report(self, base_url: str, *, api_key: str = "",
               timeout: float = 10.0) -> dict[str, Any] | None:
        """取能力报告；探测失败/非 200/非 JSON 一律返回 None（并缓存该失败）。"""
        url = capabilities_url(base_url)
        now = time.monotonic()
        cached = self._entries.get(url)
        if cached is not None and self._ttl_s > 0 and now - cached.fetched_at < self._ttl_s:
            return cached.report
        report = self._fetch(url, api_key=api_key, timeout=timeout)
        self._entries[url] = _Entry(report=report, fetched_at=now)
        return report

    def model(self, base_url: str, model: str, **kwargs: Any) -> ModelCapability | None:
        """取某个模型的能力；报告取不到或列表里没有该模型 → None。"""
        report = self.report(base_url, **kwargs)
        if not report:
            return None
        models = report.get("models")
        if not isinstance(models, list):
            return None
        for item in models:
            if isinstance(item, dict) and str(item.get("model") or "") == model:
                return ModelCapability.from_json(item)
        return None

    def context_window(self, base_url: str, model: str, **kwargs: Any) -> int | None:
        """模型的上下文窗口（token）；未知或为 0 → None。"""
        cap = self.model(base_url, model, **kwargs)
        if cap is None or cap.context_window <= 0:
            return None
        return cap.context_window

    def max_output_tokens(self, base_url: str, model: str, **kwargs: Any) -> int | None:
        """模型单次最多能输出多少 token；未知或为 0 → None。"""
        cap = self.model(base_url, model, **kwargs)
        if cap is None or cap.max_output_tokens <= 0:
            return None
        return cap.max_output_tokens

    def invalidate(self, base_url: str | None = None) -> None:
        """丢掉缓存：给 base_url 则只丢那一个，否则清空。"""
        if base_url is None:
            self._entries.clear()
            return
        self._entries.pop(capabilities_url(base_url), None)

    def _fetch(self, url: str, *, api_key: str, timeout: float) -> dict[str, Any] | None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
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


_cache: CapabilityCache | None = None


def get_capability_cache(ttl_s: float | None = None) -> CapabilityCache:
    """进程内单例：能力报告每个进程只按 TTL 探测一次。

    传 ttl_s 只在第一次调用时生效（后续调用复用已建好的缓存）。
    """
    global _cache
    if _cache is None:
        _cache = CapabilityCache(ttl_s if ttl_s is not None else 60.0)
    return _cache
