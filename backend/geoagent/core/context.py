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
    skills: Any = None  # 类型：skills.SkillLoader（技能加载器）
    pg: Any = None  # 类型：tools.pg.PgGateway（受控数据库网关，未配置时为 None）
    datasets: Any = None  # 类型：datasets.DatasetRegistry（期次数据集注册表）
    dataset: Any = None  # 类型：datasets.DatasetPeriod（本轮解析出的期次）
    dataset_id: str = ""  # 前端显式指定的期次；为空则按问题文本识别，再退默认期次
    agent_id: str = ""  # 前端"智能体"选择框指定的智能体；为空则走 RouterNode 兜底
    settings: Any = None  # 类型：config.Settings（问答链路开关等）
    period_notice: str = ""  # 期次提示（如问题提到的是未登记期次）
    prematch_hint: str = ""  # 标准问题命中提示（qa_prematch=hint 时注入提示词）
    query_state: dict[str, Any] = field(default_factory=dict)  # 多轮追问的轻量状态
    followup: dict[str, Any] = field(default_factory=dict)  # 本轮追问补全信息
    followup_notice: str = ""  # 追问无法解析时的定向提示
    current_question: str = ""  # 用户本轮原话（用于数字溯源/审计）
    answer_metadata: dict[str, Any] = field(default_factory=dict)  # 最终回答附加元数据
    embeddings: Any = None  # 类型：core.embedding.EmbeddingService
    knowledge: Any = None  # 类型：knowledge.kb.KnowledgeBase
    reports_dir: Any = None  # 类型：Path（快报等生成文件的输出目录，默认在仓库外）
    subagent_depth: int = 0
    subagents: list[dict[str, Any]] = field(default_factory=list)
    compact_requested: bool = False
    transcripts_dir: Any = None  # 类型：Path（压缩归档/大结果转存目录）

    async def emit(self, event: Event) -> None:
        if self.event_sink is not None:
            await self.event_sink(event)
