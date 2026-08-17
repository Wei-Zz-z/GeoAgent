from __future__ import annotations

from typing import Optional

from ..core.agent import Agent

CHAT_SYSTEM_PROMPT = (
    "你是一个友好的地理空间分析助手。对于需要地图、数据或空间计算的问题，"
    "系统会路由到专门的分析智能体；你负责一般性问答、解释概念和引导用户。"
)


class ChatAgent(Agent):
    def __init__(self, model: Optional[str] = None) -> None:
        super().__init__(name="chat", system_prompt=CHAT_SYSTEM_PROMPT, tools=[], model=model)
