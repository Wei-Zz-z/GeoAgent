from __future__ import annotations

from typing import Any, Optional

from ..core.context import ConversationContext
from ..core.node import Flow
from .chat import ChatAgent
from .geo import GeoAgent
from .router import RouterNode


def build_geo_graph(
    router_model: Optional[str] = None,
    geo_model: Optional[str] = None,
    chat_model: Optional[str] = None,
) -> Flow:
    """构建默认图：Router -> {GeoAgent, ChatAgent}。

    Agent 本身就是 Node，因此后续可通过相同的 `- "action" >>` 语法扩展
    规划器/总结器/质检等智能体或自定义节点。
    """
    router = RouterNode(model=router_model)
    geo = GeoAgent(model=geo_model)
    chat = ChatAgent(model=chat_model)

    router - "geo" >> geo
    router - "chat" >> chat
    return Flow(router)


async def run_conversation_turn(ctx: ConversationContext, user_text: str) -> Any:
    """便捷函数：用默认图执行一轮对话。"""
    flow = build_geo_graph()
    _, payload = await flow.run(ctx, payload=user_text)
    return payload
