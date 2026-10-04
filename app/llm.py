"""OpenAI 兼容的 LLM 客户端。只依赖 httpx，直接 POST /chat/completions。

不引入 openai SDK：base_url 指向哪就是哪（OpenAI / Ollama / vLLM / LM Studio），
每行逻辑透明，便于切本地模型。
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import Settings
from .harness import compute_cost, record_llm_usage


class LLMError(Exception):
    """LLM 调用失败（网络错误 / 4xx / 5xx）。"""


@dataclass
class Usage:
    """一次 LLM 调用的 token 用量。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.llm_base_url.rstrip("/")
        self._api_key = settings.llm_api_key
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._timeout = settings.llm_timeout_s
        self.last_usage: Usage | None = None

    @property
    def model(self) -> str:
        return self._model

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        """发送 messages，返回助手文本回复。

        messages 元素形如 {"role": "system"|"user"|"assistant", "content": "..."}。
        """
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature if temperature is None else temperature,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:  # 网络层错误（连接失败/超时）
            raise LLMError(f"LLM request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {data}") from exc
        self._record_usage(data.get("usage") or {})
        return content

    def _record_usage(self, usage: dict) -> None:
        """把本次调用的 token 用量记入 last_usage，并按单价折算成本记入指标（M5）。"""
        self.last_usage = Usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
        if self._settings.metrics_enabled:
            cost = compute_cost(self._settings, self.last_usage.prompt_tokens,
                                self.last_usage.completion_tokens)
            record_llm_usage(self._model, self.last_usage.prompt_tokens,
                             self.last_usage.completion_tokens, cost)
