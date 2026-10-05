"""示例工具：安全的算术表达式求值。

关键：不用内置 eval（可执行任意代码）。改用 ast 解析 + 白名单节点，
只允许数字、四则运算、幂、取模、整除、括号，其余节点一律拒绝。
"""

from __future__ import annotations

import ast
import operator

from pydantic import BaseModel, Field

from ..base import Tool


class CalculatorInput(BaseModel):
    expression: str = Field(description="要计算的算术表达式，例如 '12 * 7 + 3'")


# 允许的二元运算：AST 节点类型 -> 对应的 operator 函数
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

# 允许的一元运算
_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        return _UNARYOPS[type(node.op)](_eval(node.operand))
    raise ValueError(f"unsupported expression node: {type(node).__name__}")


def _safe_calculate(expression: str) -> int | float:
    tree = ast.parse(expression, mode="eval")
    result = _eval(tree)
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return result
    raise ValueError("expression did not evaluate to a number")


calculator_tool = Tool(
    name="calculator",
    description="Evaluate an arithmetic expression safely (supports + - * / ** % // and parentheses).",
    input_model=CalculatorInput,
    func=_safe_calculate,
)
