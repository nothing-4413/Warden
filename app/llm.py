"""OpenAI 兼容的 LLM 客户端。只依赖 httpx，直接 POST /chat/completions。

不引入 openai SDK：base_url 指向哪就是哪（OpenAI / Ollama / vLLM / LM Studio），
每行逻辑透明，便于切本地模型。
"""
from __future__ import annotations

import httpx

from .config import Settings


class LLMError(Exception):
    """LLM 调用失败（网络错误 / 4xx / 5xx）。"""


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.llm_base_url.rstrip("/")
        self._api_key = settings.llm_api_key
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._timeout = settings.llm_timeout_s

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
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {data}") from exc
