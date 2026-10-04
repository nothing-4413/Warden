"""EmbeddingClient：OpenAI 兼容 /embeddings（Ollama 或 OpenAI 均可）。

注意：嵌入用独立模型（如 nomic-embed-text / bge-m3），与对话模型 qwen2.5 分开。
"""
from __future__ import annotations

import httpx

from ..config import Settings


class EmbeddingError(Exception):
    """嵌入请求失败（网络 / 模型不存在 / 响应格式错误）。"""


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        """把一批文本嵌入成向量，返回与输入等长的向量列表。"""
        payload = {"model": self.settings.embedding_model, "input": texts}
        try:
            resp = httpx.post(
                f"{self.settings.llm_base_url}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                timeout=self.settings.llm_timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            # 按 index 排序，保证输出顺序与输入一致（部分后端不保证顺序）
            data = sorted(data, key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"embedding failed: {exc}") from exc
