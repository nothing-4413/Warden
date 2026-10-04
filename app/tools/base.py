"""工具定义：一个 Tool 用 pydantic 模型描述输入（可选输出），用函数实现执行。

输入模型（input_model）承担两件事：
1. 运行时校验 —— model_validate 反序列化 LLM 给的 action_input
2. 生成 JSON Schema —— model_json_schema 注入 prompt 供模型参考

输出模型（output_model）可选：声明后，run() 会把 func 的返回值强制校验成该模型，
校验失败时返回错误串（供 Agent 自纠），而不是让脏数据悄悄流回上下文。

timeout_s 可选：大于 0 时给工具执行加墙钟超时，超时返回错误串，避免单个工具卡死整个循环。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Type

from pydantic import BaseModel


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: Type[BaseModel]
    func: Callable[..., Any]
    output_model: Type[BaseModel] | None = None
    timeout_s: float | None = None

    def json_schema(self) -> dict[str, Any]:
        """输入参数的 JSON Schema（用于注入 prompt 与 API 描述）。"""
        return self.input_model.model_json_schema()

    def run(self, raw_input: dict[str, Any]) -> Any:
        """校验输入 → 执行（可带超时）→ 校验输出（可选）。"""
        validated = self.input_model.model_validate(raw_input or {})
        result = self._invoke(validated)
        if self.output_model is not None and not _is_error(result):
            result = self._coerce_output(result)
        return result

    def _invoke(self, validated: BaseModel) -> Any:
        """执行 func。timeout_s>0 时在后台线程跑，超时返回错误串。"""
        if self.timeout_s is None or self.timeout_s <= 0:
            return self.func(**validated.model_dump())

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.func, **validated.model_dump())
        try:
            return future.result(timeout=self.timeout_s)
        except TimeoutError:
            # Python 线程无法被强杀：超时后后台线程会自己跑完，这里只是
            # 不再等待，把"超时"作为观测回传给 Agent，避免循环被单个工具卡死。
            return f"error: tool '{self.name}' timed out after {self.timeout_s}s"

    def _coerce_output(self, result: Any) -> Any:
        """把 func 返回值强制校验成 output_model；失败返回错误串。"""
        model = self.output_model
        try:
            if isinstance(result, model):
                return result.model_dump()
            if isinstance(result, str):
                return model.model_validate_json(result).model_dump()
            return model.model_validate(result).model_dump()
        except Exception as exc:  # 任何校验失败都转成错误串，供 Agent 自纠
            return (
                f"error: tool '{self.name}' output does not match schema "
                f"{model.__name__}: {type(exc).__name__}: {exc}"
            )


def _is_error(value: Any) -> bool:
    """约定：以 "error: " 开头的字符串视为工具已上报的错误，不再做输出校验。"""
    return isinstance(value, str) and value.startswith("error: ")
