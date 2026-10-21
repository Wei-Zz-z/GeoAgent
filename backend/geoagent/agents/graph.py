from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..config import elder_care_enabled
from ..core.context import ConversationContext
from ..core.events import Event
from ..core.llm import AssistantMessage
from ..core.node import Flow, Node
from .chat import ChatAgent
from .router import RouterNode
from .sql import SQLAgent


@dataclass(frozen=True)
class AgentSpec:
    """可选智能体目录项：前端"智能体"选择框按此渲染。"""

    id: str
    label: str
    description: str


AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec("sql", "土地变化统计问答", "地类变化统计、面积流向、快报生成与预警图斑上图"),
    AgentSpec("elder_care", "养老可达性评估", "养老机构步行可达性、供需匹配分析"),
    AgentSpec("chat", "通用对话", "概念解释、一般性问答与操作引导"),
)

DEFAULT_AGENT_ID = "sql"

_AGENT_IDS = {spec.id for spec in AGENT_SPECS}

# 场景未启用时的统一提示（当前只有养老可达性评估会整体关闭）。
SCENE_DISABLED_MESSAGE = (
    "本部署未启用「养老可达性评估」场景。当前可用：土地变化统计问答、通用对话。"
    "如需启用，请在后端环境变量里设置 GEOAGENT_ELDER_CARE=1 后重启服务。"
)


class DisabledSceneNode(Node):
    """场景关闭时的占位节点：给出明确提示，不报错也不静默改换场景。"""

    def __init__(self) -> None:
        super().__init__(name="disabled_scene")

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        model = getattr(ctx, "model", "") or ""
        await ctx.emit(
            Event(
                "message",
                {
                    "role": "assistant",
                    "content": SCENE_DISABLED_MESSAGE,
                    "model": model,
                    "route": getattr(ctx, "route", "") or "",
                    "answer_origin": getattr(ctx, "answer_origin", "") or "",
                    "subagents": [],
                },
            )
        )
        ctx.session.add_message({"role": "assistant", "content": SCENE_DISABLED_MESSAGE})
        return "default", AssistantMessage(content=SCENE_DISABLED_MESSAGE, model=model)


def agent_catalog() -> list[dict[str, str]]:
    """可选智能体清单（供 GET /api/agents 与前端选择框使用）。"""
    return [
        {"id": spec.id, "label": spec.label, "description": spec.description}
        for spec in AGENT_SPECS
        if spec.id != "elder_care" or elder_care_enabled()
    ]


def is_known_agent(agent_id: str) -> bool:
    return str(agent_id or "").strip() in _AGENT_IDS


def build_agent(agent_id: str, model: Optional[str] = None) -> Node:
    """按会话选择的智能体 id 构建单个 Agent 节点（不做 LLM 路由）。"""
    target = str(agent_id or "").strip()
    if target == "elder_care":
        if not elder_care_enabled():
            return DisabledSceneNode()
        # 延迟导入：场景关闭时不再加载 analysis 下的 geopandas/networkx 重依赖
        from .elder_care import ElderCareAgent

        return ElderCareAgent(model=model)
    if target == "chat":
        return ChatAgent(model=model)
    return SQLAgent(model=model)


def build_geo_graph(
    router_model: Optional[str] = None,
    chat_model: Optional[str] = None,
    elder_care_model: Optional[str] = None,
    sql_model: Optional[str] = None,
) -> Flow:
    """兜底图：Router -> {SQLAgent, ElderCareAgent, ChatAgent}。

    会话已选择智能体时走 :func:`run_turn` 直连，不经过这里；保留本图是为了兼容
    未带 ``agent`` 字段的老客户端与冒烟脚本。
    """
    router = RouterNode(model=router_model)
    sql = SQLAgent(model=sql_model)
    chat = ChatAgent(model=chat_model)
    elder: Node
    if elder_care_enabled():
        from .elder_care import ElderCareAgent

        elder = ElderCareAgent(model=elder_care_model)
    else:
        elder = DisabledSceneNode()

    router - "sql" >> sql
    router - "elder_care" >> elder
    router - "chat" >> chat
    return Flow(router)


async def run_turn(
    ctx: ConversationContext,
    payload: str,
    *,
    agent_id: str = "",
    router_model: Optional[str] = None,
) -> Any:
    """执行一轮对话。

    指定 ``agent_id``（前端"智能体"选择框，或 REST 的 ``agent`` 字段）时直接交给
    该 Agent 执行；只有未指定/未知时才退回 RouterNode 图，保证行为向后兼容。
    """
    target = str(agent_id or "").strip()
    if is_known_agent(target):
        ctx.agent_id = target
        ctx.route = target
        await ctx.emit(Event("route", {"target": target, "reason": "会话选择的智能体"}))
        node = build_agent(target)
        _, result = await node.exec(ctx, payload)
        return result

    flow = build_geo_graph(router_model=router_model or None)
    _, result = await flow.run(ctx, payload=payload)
    return result


async def run_conversation_turn(ctx: ConversationContext, user_text: str) -> Any:
    """便捷函数：用默认图执行一轮对话（未指定智能体时的兜底路径）。"""
    return await run_turn(ctx, user_text)
