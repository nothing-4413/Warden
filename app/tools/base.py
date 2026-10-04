"""工具定义：一个 Tool 用 pydantic 模型描述输入，用函数实现执行。

输入模型同时承担两件事：
1. 运行时校验 —— model_validate 反序列化 LLM 给的 action_input
2. 生成 JSON Schema —— model_json_schema 注入 prompt 供模型参考
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: Type[BaseModel]
    func: Callable[..., Any]

    def json_schema(self) -> dict[str, Any]:
        """输入参数的 JSON Schema（用于注入 prompt 与 API 描述）。"""
        return self.input_model.model_json_schema()

    def run(self, raw_input: dict[str, Any]) -> Any:
        """校验并执行。input_model 决定需要哪些字段。"""
        validated = self.input_model.model_validate(raw_input or {})
        return self.func(**validated.model_dump())
