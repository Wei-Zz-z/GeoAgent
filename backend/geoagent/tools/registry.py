from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError

from .result import Artifact, ToolResult

TOOL_REGISTRY: dict[str, "Tool"] = {}


@dataclass
class Tool:
    """可被 LLM 调用的工具，带 JSON Schema 描述。

    相比 poipoi-agent 的改进：可选 Pydantic 模型自动生成 schema 并校验参数，
    执行过程支持异步。
    """

    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]
    params_model: Optional[type[BaseModel]] = None
    is_async: bool = False
    takes_ctx: bool = False

    @classmethod
    def build(
        cls,
        name: str,
        description: str,
        fn: Callable[..., Any],
        params: Optional[type[BaseModel] | dict[str, Any]] = None,
    ) -> "Tool":
        is_async = inspect.iscoroutinefunction(fn)
        takes_ctx = "ctx" in inspect.signature(fn).parameters
        params_model: Optional[type[BaseModel]] = None
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        if isinstance(params, type) and issubclass(params, BaseModel):
            params_model = params
            schema = params.model_json_schema()
        elif isinstance(params, dict):
            schema = params
        return cls(
            name=name,
            description=description,
            fn=fn,
            parameters=schema,
            params_model=params_model,
            is_async=is_async,
            takes_ctx=takes_ctx,
        )

    def to_llm_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def run(self, ctx: Any = None, **kwargs: Any) -> Any:
        if self.params_model is not None:
            try:
                validated = self.params_model.model_validate(kwargs)
            except ValidationError as exc:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    content=f"参数校验失败: {exc.errors()[:3]}",
                    is_error=True,
                )
            kwargs = validated.model_dump()

        if self.is_async:
            if self.takes_ctx:
                return await self.fn(ctx=ctx, **kwargs)
            return await self.fn(**kwargs)
        if self.takes_ctx:
            return self.fn(ctx=ctx, **kwargs)
        return self.fn(**kwargs)


def register_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[type[BaseModel] | dict[str, Any]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册工具的装饰器，将函数注册到全局注册表。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__
        tool_desc = description or (inspect.getdoc(fn) or "").strip()
        TOOL_REGISTRY[tool_name] = Tool.build(
            name=tool_name,
            description=tool_desc,
            fn=fn,
            params=params,
        )
        return fn

    return decorator


def get_tools(*names: str) -> list[Tool]:
    """按名称获取工具；未指定名称时返回全部已注册工具。"""
    if not names:
        return list(TOOL_REGISTRY.values())
    return [TOOL_REGISTRY[n] for n in names if n in TOOL_REGISTRY]


def all_tools() -> list[Tool]:
    return list(TOOL_REGISTRY.values())


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def normalize_result(raw: Any, tool_call_id: str, name: str) -> ToolResult:
    """将任意工具返回值归一化为 ToolResult。"""
    if isinstance(raw, ToolResult):
        raw.tool_call_id = raw.tool_call_id or tool_call_id
        raw.name = raw.name or name
        return raw
    if isinstance(raw, dict) and ("content" in raw or "artifacts" in raw):
        artifacts = []
        for item in raw.get("artifacts", []):
            artifacts.append(item if isinstance(item, Artifact) else Artifact(**item))
        return ToolResult(
            tool_call_id=tool_call_id,
            name=name,
            content=str(raw.get("content", "")),
            is_error=bool(raw.get("is_error", False)),
            artifacts=artifacts,
        )
    return ToolResult(
        tool_call_id=tool_call_id,
        name=name,
        content=_stringify(raw),
    )
