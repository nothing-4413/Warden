import time

from pydantic import BaseModel

from app.tools.base import Tool


class AddInput(BaseModel):
    a: int
    b: int


class AddOutput(BaseModel):
    result: int


class NoInput(BaseModel):
    pass


def _add(a: int, b: int) -> dict:
    return {"result": a + b}


def _bad_add(a: int, b: int) -> dict:
    return {"wrong_key": a + b}


def test_output_model_validates_dict_to_schema():
    tool = Tool(
        name="add",
        description="add two ints",
        input_model=AddInput,
        func=_add,
        output_model=AddOutput,
    )
    assert tool.run({"a": 2, "b": 3}) == {"result": 5}


def test_output_model_returns_error_on_mismatch():
    tool = Tool(
        name="add",
        description="add two ints",
        input_model=AddInput,
        func=_bad_add,
        output_model=AddOutput,
    )
    out = tool.run({"a": 2, "b": 3})
    assert isinstance(out, str)
    assert out.startswith("error: tool 'add' output does not match schema AddOutput")


def test_output_model_accepts_json_string():
    def as_json(a: int, b: int) -> str:
        return f'{{"result": {a + b}}}'

    tool = Tool(
        name="add",
        description="add two ints",
        input_model=AddInput,
        func=as_json,
        output_model=AddOutput,
    )
    assert tool.run({"a": 4, "b": 5}) == {"result": 9}


def test_timeout_returns_error_for_slow_tool():
    def slow() -> str:
        time.sleep(0.3)
        return "done"

    tool = Tool(
        name="slow",
        description="sleeps forever",
        input_model=NoInput,
        func=slow,
        timeout_s=0.05,
    )
    out = tool.run({})
    assert isinstance(out, str)
    assert out.startswith("error: tool 'slow' timed out after 0.05s")


def test_no_timeout_by_default_runs_normally():
    tool = Tool(
        name="fast",
        description="returns ok",
        input_model=NoInput,
        func=lambda: "ok",
    )
    assert tool.run({}) == "ok"


def test_default_output_model_is_none_passthrough():
    # 未声明 output_model 时，返回值原样透传（向后兼容）
    tool = Tool(
        name="raw",
        description="returns raw dict",
        input_model=NoInput,
        func=lambda: {"arbitrary": True},
    )
    assert tool.run({}) == {"arbitrary": True}
