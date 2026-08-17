from __future__ import annotations

from typing import Any


class MemoryProvider:
    """长期记忆接口（占位）。

    poipoi-agent 的长期记忆逻辑（记忆类型、关键字检索、滚动摘要）
    将在后续里程碑中移植并改进到这里。
    """

    async def build_context(self, query: str, k: int = 4) -> str:
        return ""


class NoopMemory(MemoryProvider):
    """空实现，长期记忆正式落地前使用。"""

    async def build_context(self, query: str, k: int = 4) -> str:
        return ""
