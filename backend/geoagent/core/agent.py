from __future__ import annotations

from typing import Any, Optional

from ..memory.compactor import ContextCompactor
from ..tools.builtin import get_builtin_tools
from ..tools.executor import ToolExecutor
from ..tools.registry import Tool
from .events import Event
from .llm import AssistantMessage
from .node import Node


# 所有 Agent 统一追加的规划引导（英文，见 AGENTS.md 语言约定）。
BUILTIN_TOOL_GUIDANCE = (
    "\n\nWhen the user's request involves multiple steps, first call todo_write to "
    "create a task list, then update item statuses (pending / in_progress / "
    "completed) as you make progress. "
    "Delegate large, self-contained subtasks to a subagent with the task tool to keep "
    "this conversation focused. "
    "Use list_skills to see available skills, and load_skill to read their full "
    "instructions when the task requires specialized knowledge."
)

# 连续若干轮工具调用未更新任务清单时，注入的提醒。
REMINDER_MESSAGE = (
    "Reminder: you have not updated your task list for a few rounds. "
    "If the current task is multi-step, call todo_write to reflect your progress."
)

# 连续多少轮工具调用未使用 todo_write 后触发提醒。
TODO_REMINDER_ROUNDS = 3


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
        self.system_prompt = f"{system_prompt}\n{BUILTIN_TOOL_GUIDANCE}".strip()
        base_tools = list(tools or [])
        base_names = {t.name for t in base_tools}
        self.tools = base_tools + [
            t for t in get_builtin_tools() if t.name not in base_names
        ]
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
        rounds_since_todo = 0
        pending_reminder = False
        reactive_done = False
        compactor = ContextCompactor(
            transcripts_dir=getattr(ctx, "transcripts_dir", None)
        )

        for _ in range(self.max_turns):
            # 每次调用模型前先执行四步压缩管线（参考 learn-claude-code s08）。
            await compactor.prepare(
                ctx.session.raw_messages(),
                user_text,
                ctx.llm,
                model,
            )
            memory_context = ""
            if ctx.memory is not None:
                memory_context = await ctx.memory.build_context(user_text)
            system_prompt = self.system_prompt
            skills = getattr(ctx, "skills", None)
            if skills is not None:
                catalog = skills.catalog_prompt()
                if catalog:
                    system_prompt = f"{system_prompt}\n\n{catalog}"
            messages = ctx.session.build_llm_messages(
                system_prompt=system_prompt,
                memory_context=memory_context,
            )
            if pending_reminder:
                messages.append({"role": "system", "content": REMINDER_MESSAGE})
                pending_reminder = False

            try:
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
            except Exception as exc:
                lowered = str(exc).lower()
                too_long = (
                    "prompt_too_long" in lowered
                    or "context length" in lowered
                    or "too many tokens" in lowered
                )
                if not reactive_done and too_long:
                    # 补救一次：摘要旧历史后重试。
                    reactive_done = True
                    await compactor.reactive_compact(
                        ctx.session.raw_messages(),
                        user_text,
                        ctx.llm,
                        model,
                    )
                    continue
                raise

            final_dict = final.to_dict()
            if ctx.todos:
                final_dict["todos"] = list(ctx.todos)
            subagents = getattr(ctx, "subagents", None)
            if subagents:
                final_dict["subagents"] = list(subagents)
            ctx.session.add_message(final_dict)
            if not final.tool_calls:
                break

            used_todo = any(tc.name == "todo_write" for tc in final.tool_calls)
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
            if getattr(ctx, "compact_requested", False):
                await compactor.compact_history(
                    ctx.session.raw_messages(),
                    user_text,
                    ctx.llm,
                    model,
                    force=True,
                )
                ctx.compact_requested = False
            if used_todo:
                rounds_since_todo = 0
            else:
                rounds_since_todo += 1
                if rounds_since_todo >= TODO_REMINDER_ROUNDS:
                    pending_reminder = True
                    rounds_since_todo = 0

        if final is None:
            raise RuntimeError(f"Agent '{self.name}' produced no response")

        await ctx.emit(
            Event(
                "message",
                {
                    "role": "assistant",
                    "content": final.content,
                    "model": model,
                    "todos": list(ctx.todos),
                    "subagents": list(getattr(ctx, "subagents", []) or []),
                },
            )
        )
        return "default", final
