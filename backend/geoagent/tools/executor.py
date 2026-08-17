from __future__ import annotations

from typing import Any, Optional

from ..core.llm import ToolCall
from .registry import Tool, all_tools, normalize_result
from .result import ToolResult


class ToolExecutor:
    """异步执行归一化的 LLM 工具调用，相比 poipoi-agent 的改进：

    - 调用前先做 Pydantic 参数校验
    - 结构化错误以 ToolResult（is_error=True）返回
    - artifacts 与文本内容一起透传
    """

    def __init__(self, tools: Optional[list[Tool]] = None) -> None:
        tool_list = tools if tools is not None else all_tools()
        self.tool_map = {t.name: t for t in tool_list}

    async def execute(self, tool_call: ToolCall, ctx: Any = None) -> ToolResult:
        tool = self.tool_map.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Tool '{tool_call.name}' not found",
                is_error=True,
            )
        try:
            raw = await tool.run(ctx=ctx, **tool_call.arguments)
        except Exception as exc:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"Error: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        return normalize_result(raw, tool_call.id, tool_call.name)
