"""LLM 成本统计（M5）：token 用量 × 模型单价 → 美元成本。"""

from __future__ import annotations

from ..config import Settings


def compute_cost(settings: Settings, prompt_tokens: int, completion_tokens: int) -> float:
    """按单价折算本次调用的成本（USD）。单价单位：USD / 1M tokens（默认 0 = 本地模型免费）。"""
    return (
        prompt_tokens / 1_000_000.0 * settings.llm_input_price_per_1m
        + completion_tokens / 1_000_000.0 * settings.llm_output_price_per_1m
    )
