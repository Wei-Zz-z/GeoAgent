from __future__ import annotations

from typing import Any, Optional

from ..tools.executor import ToolExecutor
from ..tools.registry import Tool
from .events import Event
from .llm import AssistantMessage
from .node import Node


class Agent(Node):
    """自包含的智能体：系统提示词 + 工具集 + 模型配置 + LLM 工具循环。

    Agent *本身* 就是一个 Node，因此不同功能的 Agent 可以与自定义节点
    （例如路由器）通过 `- "action" >>` 语法组合成图。
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: Optional[list[Tool]] = None,
        model: Optional[str] = None,
        max_turns: int = 8,
        temperature: Optional[float] = None,
    ) -> None:
        super().__init__(name=name)
        self.system_prompt = system_prompt
        self.tools = list(tools or [])
        self.model = model
        self.max_turns = max_turns
        self.temperature = temperature
        self.executor = ToolExecutor(self.tools)

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        user_text = str(payload)
        ctx.session.add_user(user_text)

        model = self.model or ctx.model
        tool_schemas = [t.to_llm_format() for t in self.tools] or None
        final: Optional[AssistantMessage] = None

        for _ in range(self.max_turns):
            memory_context = ""
            if ctx.memory is not None:
                memory_context = await ctx.memory.build_context(user_text)
            messages = ctx.session.build_llm_messages(
                system_prompt=self.system_prompt,
                memory_context=memory_context,
            )

            if ctx.event_sink is not None:
                async def on_token(delta: str) -> None:
                    await ctx.emit(Event("token", {"delta": delta}))

                final = await ctx.llm.stream_chat(
                    model=model,
                    messages=messages,
                    tools=tool_schemas,
                    temperature=self.temperature,
                    on_token=on_token,
                )
            else:
                final = await ctx.llm.chat(
                    model=model,
                    messages=messages,
                    tools=tool_schemas,
                    temperature=self.temperature,
                )

            ctx.session.add_message(final.to_dict())
            if not final.tool_calls:
                break

            for tc in final.tool_calls:
                await ctx.emit(
                    Event(
                        "tool_call",
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                    )
                )
                result = await self.executor.execute(tc, ctx=ctx)
                ctx.session.add_tool(result.to_message())
                await ctx.emit(
                    Event(
                        "tool_result",
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "is_error": result.is_error,
                            "content": result.content[:2000],
                        },
                    )
                )
                for artifact in result.artifacts:
                    await ctx.emit(Event("artifact", artifact.to_dict()))

        if final is None:
            raise RuntimeError(f"Agent '{self.name}' produced no response")

        await ctx.emit(
            Event("message", {"role": "assistant", "content": final.content, "model": model})
        )
        return "default", final
