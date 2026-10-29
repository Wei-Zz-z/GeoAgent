from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from ..memory.compactor import ContextCompactor
from ..tools.builtin import get_builtin_tools
from ..tools.registry import Tool
from .events import Event
from .llm import AssistantMessage, ToolCall
from .node import Node
from .toolcall_guard import parse_leaked_calls, scrub_internal_links, scrub_leaked_calls


class Agent(Node):
    """自包含的智能体：系统提示词 + 工具集 + 模型配置 + LLM 工具循环。

    Agent *本身* 就是一个 Node，因此不同功能的 Agent 可以与自定义节点
    （例如路由器）通过 `- "action" >>` 语法组合成图。

    内置工具（task / list_skills / load_skill / compact）的用途由各自的工具 description
    承担，技能目录由 SkillLoader.catalog_prompt() 在调用前追加，不在提示词里复述。
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
        self.system_prompt = system_prompt.strip()
        base_tools = list(tools or [])
        base_names = {t.name for t in base_tools}
        self.tools = base_tools + [
            t for t in get_builtin_tools() if t.name not in base_names
        ]
        self.model = model
        self.max_turns = max_turns
        self.temperature = temperature
        # 局部导入避免 tools -> core -> tools 的循环依赖。
        from ..tools.executor import ToolExecutor

        self.executor = ToolExecutor(self.tools)

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        user_text = str(payload)
        ctx.session.add_user(user_text)
        ctx.answer_metadata = {}
        ctx.turn_tool_messages = []

        model = self.model or ctx.model
        tool_schemas = [t.to_llm_format() for t in self.tools] or None
        final: Optional[AssistantMessage] = None
        reactive_done = False
        compactor = ContextCompactor(
            transcripts_dir=getattr(ctx, "transcripts_dir", None)
        )
        empty_retries = 0
        leak_retries = 0

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

            # 兼容端点偶发：模型把工具调用写成了正文里的 XML 标记。先尽量还原成真正的
            # 工具调用继续执行；还原不了就剥掉标记，绝不让标记外发或落库。
            leaked = parse_leaked_calls(final.content or "")
            if leaked:
                final.content = scrub_leaked_calls(final.content or "")
                usable = [call for call in leaked if call.name in self.executor.tool_map]
                if usable:
                    final.tool_calls = list(final.tool_calls) + [
                        ToolCall(
                            id=f"leak_{uuid4().hex[:8]}",
                            name=call.name,
                            arguments=call.arguments,
                        )
                        for call in usable
                    ]
                elif leak_retries < 1 and not final.content.strip():
                    # 整条回复只是一个发不出去的标记：纠正一次，且不落库。
                    leak_retries += 1
                    ctx.session.add_user(
                        "你上一条回复把工具调用写成了正文标记，没有真正发出调用。"
                        "请改用工具调用重新发起，或基于已有结果直接用中文回答。"
                    )
                    continue
                elif not final.content.strip():
                    final.content = "（本轮模型未按工具调用格式返回结果，已中止。请重试或换一种问法。）"

            # 出口兜底：正文不列内部下载链接（文件走卡片预览/下载）
            final.content = scrub_internal_links(final.content or "")
            # 子类可做最终回答校验/改写（例如 SQLAgent 的数字溯源）。
            final = await self.finalize_answer(ctx, final)
            final_dict = final.to_dict()
            final_dict["route"] = getattr(ctx, "route", "") or ""
            if getattr(ctx, "answer_origin", ""):
                final_dict["answer_origin"] = ctx.answer_origin
            metadata = getattr(ctx, "answer_metadata", None)
            if metadata:
                final_dict.update(metadata)
            subagents = getattr(ctx, "subagents", None)
            if subagents:
                final_dict["subagents"] = list(subagents)
            if not final.tool_calls and not (final.content or "").strip() and empty_retries < 1:
                # 空回复重试一次：不落库，提示模型基于已有工具结果给出回答。
                empty_retries += 1
                ctx.session.add_user(
                    "你还没有给出最终回答。请基于已有的工具结果，直接输出完整的中文回答。"
                )
                continue
            ctx.session.add_message(final_dict)
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
                tool_message = result.to_message()
                ctx.session.add_tool(tool_message)
                ctx.turn_tool_messages.append(tool_message)
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

        if final is None:
            raise RuntimeError(f"Agent '{self.name}' produced no response")

        await ctx.emit(
            Event(
                "message",
                {
                    "role": "assistant",
                    "content": final.content,
                    "model": model,
                    "route": getattr(ctx, "route", "") or "",
                    "answer_origin": getattr(ctx, "answer_origin", "") or "",
                    "subagents": list(getattr(ctx, "subagents", []) or []),
                    **dict(getattr(ctx, "answer_metadata", {}) or {}),
                },
            )
        )
        return "default", final

    async def finalize_answer(self, ctx: Any, final: AssistantMessage) -> AssistantMessage:
        """最终回答出口钩子；默认原样返回，SQLAgent 用它做数字溯源。"""
        return final
