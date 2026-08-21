from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .events import Event

EventSink = Callable[[Event], Awaitable[None]]


@dataclass
class ConversationContext:
    """一次对话的运行时状态。

    替代 poipoi-agent 的全局 shared dict，保证多用户/多会话之间不共享可变状态。
    """

    conversation_id: str
    session: Any  # 类型：memory.session.ConversationSession
    model: str
    llm: Any  # 类型：core.llm.LLMService
    store: Any  # 类型：memory.store.ConversationStore
    memory: Optional[Any] = None  # 类型：memory.memory.MemoryProvider（长期记忆，后续实现）
    event_sink: Optional[EventSink] = None
    todos: list[dict[str, str]] = field(default_factory=list)
    skills: Any = None  # 类型：skills.SkillLoader（技能加载器）
    subagent_depth: int = 0
    subagents: list[dict[str, Any]] = field(default_factory=list)
    compact_requested: bool = False
    transcripts_dir: Any = None  # 类型：Path（压缩归档/大结果转存目录）

    async def emit(self, event: Event) -> None:
        if self.event_sink is not None:
            await self.event_sink(event)
