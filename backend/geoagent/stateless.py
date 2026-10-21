"""无状态问答执行：调用方自带历史，我们不维护会话，只落一条 MCP 调用记录。

用于外部前端通过 MCP 调用我们的问答能力：
- 不写会话表（`ConversationSession(store=None)`），因此没有并发与多实例约束；
- 多轮上下文由调用方在 `history` 里回传；
- 只返回最终答案，不返回模型的思考过程与工具轨迹；
- 每次调用往 `mcp_calls` 表记一条「提问 + 最终回复」审计。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .agents.graph import run_turn
from .core.context import ConversationContext
from .core.llm import LLMConfigurationError
from .memory.memory import NoopMemory
from .memory.session import ConversationSession

logger = logging.getLogger("geoagent.mcp")


@dataclass
class StatelessAnswer:
    """一次无状态问答的结果。"""

    answer: str = ""
    route: str = ""
    dataset_id: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False


class EventCollector:
    """把本轮事件收集起来：只取路由与结构化产物，过程话术不外发。"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)

    def route(self) -> str:
        for event in reversed(self.events):
            if getattr(event, "type", "") == "route":
                return str((getattr(event, "data", None) or {}).get("target", ""))
        return ""

    def artifacts(self) -> list[dict[str, Any]]:
        items = []
        for event in self.events:
            if getattr(event, "type", "") != "artifact":
                continue
            data = getattr(event, "data", None) or {}
            items.append(
                {
                    "kind": data.get("kind", ""),
                    "name": data.get("name", ""),
                    "data": data.get("data"),
                }
            )
        return items

    def last_message(self) -> str:
        for event in reversed(self.events):
            if getattr(event, "type", "") == "message":
                return str((getattr(event, "data", None) or {}).get("content", ""))
        return ""


def build_stateless_context(
    app: Any,
    *,
    history: Optional[list[dict[str, Any]]] = None,
    dataset_id: str = "",
    agent_id: str = "",
    event_sink: Any = None,
) -> ConversationContext:
    """构造一个不落库的会话上下文（与 web 会话共用同一套问答链路）。"""
    settings = app.state.settings
    session = ConversationSession(store=None, conversation_id=None)
    session.load(history or [])
    ctx = ConversationContext(
        conversation_id="",
        session=session,
        model=settings.default_model,
        llm=app.state.llm,
        store=None,
        memory=NoopMemory(),
        event_sink=event_sink,
        skills=app.state.skills,
        pg=app.state.pg,
        datasets=app.state.datasets,
        settings=settings,
        embeddings=app.state.embeddings,
        knowledge=app.state.knowledge,
        reports_dir=settings.reports_dir,
        transcripts_dir=settings.data_dir / "transcripts",
    )
    ctx.dataset_id = str(dataset_id or "")
    ctx.agent_id = str(agent_id or "")
    return ctx


async def run_stateless_question(
    app: Any,
    *,
    question: str,
    history: Optional[list[dict[str, Any]]] = None,
    dataset_id: str = "",
    agent_id: str = "",
    caller: str = "",
    user_id: str = "",
) -> StatelessAnswer:
    """执行一次无状态问答，并记录一条 MCP 调用审计。"""
    text = str(question or "").strip()
    if not text:
        return StatelessAnswer(answer="问题不能为空。", is_error=True)

    collector = EventCollector()
    ctx = build_stateless_context(
        app,
        history=history,
        dataset_id=dataset_id,
        agent_id=agent_id,
        event_sink=collector,
    )
    started = time.perf_counter()
    is_error = False
    try:
        result = await run_turn(
            ctx,
            text,
            agent_id=str(agent_id or ""),
            router_model=app.state.settings.router_model or None,
        )
        answer = str(getattr(result, "content", "") or "")
    except LLMConfigurationError as exc:
        answer, is_error = f"模型不可用：{exc}", True
    except Exception as exc:  # 外部调用方需要拿到可读原因，而不是 500
        logger.exception("MCP 无状态问答失败")
        answer, is_error = f"{type(exc).__name__}: {exc}", True

    answer = answer.strip() or collector.last_message().strip()
    if not answer and not is_error:
        answer, is_error = "本轮没有生成有效回答，请重试或换一种问法。", True

    duration_ms = int((time.perf_counter() - started) * 1000)
    await _record_call(
        app,
        caller=caller,
        user_id=user_id,
        dataset_id=dataset_id,
        agent_id=agent_id,
        question=text,
        answer=answer,
        route=collector.route(),
        ok=not is_error,
        error="" if not is_error else answer,
        duration_ms=duration_ms,
    )
    return StatelessAnswer(
        answer=answer,
        route=collector.route(),
        dataset_id=str(dataset_id or ""),
        artifacts=collector.artifacts() if not is_error else [],
        is_error=is_error,
    )


async def _record_call(app: Any, **kwargs: Any) -> None:
    """记录 MCP 调用（提问 + 最终回复）。审计写失败不影响回答。"""
    store = getattr(app.state, "store", None)
    if store is None:
        return
    try:
        await store.record_mcp_call(**kwargs)
    except Exception:
        logger.warning("MCP 调用审计写入失败", exc_info=True)