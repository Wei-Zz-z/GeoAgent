"""子 Agent：以全新会话上下文运行嵌套 Agent，只返回最终文本。

设计参考 learn-claude-code s06：
- 子 Agent 使用全新的消息窗口（不持久化、不进入父上下文）；
- 父与子共享同一进程 / 工具注册表，但子 Agent 不携带 task 工具（防无界委派）；
- 父子均受最大委派深度限制（防御性，当前结构天然只有一层）。
"""

from __future__ import annotations

from uuid import uuid4
from typing import Any, Optional

from ..memory.session import ConversationSession
from ..tools.registry import all_tools
from .agent import Agent
from .context import ConversationContext
from .events import Event

MAX_SUBAGENT_DEPTH = 2
MAX_SUBAGENT_TURNS = 12

SUBAGENT_SYSTEM_PROMPT = (
    "You are a subagent. Complete the assigned subtask precisely and report the "
    "result as a concise summary. Use tools when needed. "
    "Do not ask the parent agent for help."
)


class SubagentError(RuntimeError):
    """子 Agent 委派失败（如超过深度限制）。"""


def build_subagent_tools() -> list[Any]:
    """子 Agent 可用的工具：全部已注册工具，但不含 task（禁止无界递归委派）。"""
    return [t for t in all_tools() if t.name != "task"]


async def run_subagent(
    parent_ctx: ConversationContext,
    prompt: str,
    model: Optional[str] = None,
) -> str:
    """以全新上下文运行一个子 Agent，返回其最终文本。"""
    depth = int(getattr(parent_ctx, "subagent_depth", 0) or 0)
    if depth >= MAX_SUBAGENT_DEPTH:
        raise SubagentError(f"subagent depth limit ({MAX_SUBAGENT_DEPTH}) reached")

    sub_id = uuid4().hex[:8]
    subagents = getattr(parent_ctx, "subagents", None)
    if subagents is not None:
        subagents.append(
            {"id": sub_id, "prompt": prompt, "status": "running", "content": ""}
        )
    await parent_ctx.emit(
        Event("subagent_start", {"id": sub_id, "prompt": prompt})
    )

    try:
        session = ConversationSession(store=None, conversation_id=None)
        sub_ctx = ConversationContext(
            conversation_id=parent_ctx.conversation_id,
            session=session,
            model=model or parent_ctx.model,
            llm=parent_ctx.llm,
            store=None,
            memory=None,
            event_sink=None,
            skills=getattr(parent_ctx, "skills", None),
            subagent_depth=depth + 1,
        )
        sub_agent = Agent(
            name="subagent",
            system_prompt=SUBAGENT_SYSTEM_PROMPT,
            tools=build_subagent_tools(),
            model=model or parent_ctx.model,
            max_turns=MAX_SUBAGENT_TURNS,
        )
        _, final = await sub_agent.exec(sub_ctx, prompt)
        text = final.content or "(no summary)"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if subagents is not None:
            for item in subagents:
                if item["id"] == sub_id:
                    item["status"] = "error"
                    item["content"] = message
        await parent_ctx.emit(
            Event("subagent_end", {"id": sub_id, "is_error": True, "content": message})
        )
        raise

    if subagents is not None:
        for item in subagents:
            if item["id"] == sub_id:
                item["status"] = "done"
                item["content"] = text
    await parent_ctx.emit(
        Event("subagent_end", {"id": sub_id, "is_error": False, "content": text})
    )
    return text
