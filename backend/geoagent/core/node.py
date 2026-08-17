from __future__ import annotations

import asyncio
from typing import Any, Optional


class Node:
    """图节点基类，借鉴并改进了 poipoi-agent 的 core/node.py。

    - 异步执行
    - 每会话独立的 context，替代全局 shared dict
    - 沿用同样的构图 DSL：`node - "action" >> next_node`
    - 支持可选重试与退避等待
    """

    def __init__(
        self,
        name: Optional[str] = None,
        max_retries: int = 1,
        wait: float = 0.0,
    ) -> None:
        self.name = name or self.__class__.__name__
        self.max_retries = max_retries
        self.wait = wait
        self._edges: dict[str, Node] = {}
        self._pending_action = "default"

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        raise NotImplementedError

    async def _exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        for attempt in range(self.max_retries):
            try:
                return await self.exec(ctx, payload)
            except Exception:
                if attempt == self.max_retries - 1:
                    raise
                if self.wait > 0:
                    await asyncio.sleep(self.wait)
        raise RuntimeError(f"Node '{self.name}' exhausted retries")

    def __rshift__(self, other: "Node") -> "Node":
        self._edges[self._pending_action] = other
        self._pending_action = "default"
        return other

    def __sub__(self, action: str) -> "Node":
        if not isinstance(action, str):
            raise TypeError("Action must be a string")
        self._pending_action = action or "default"
        return self

    def edges(self) -> dict[str, "Node"]:
        return dict(self._edges)


class Flow:
    """异步图执行器。按节点返回的 action 依次跳转。

    支持循环：Agent 内部的消息级循环属于循环，图级循环在超过 max_steps 后终止。
    """

    def __init__(self, start: Node, max_steps: int = 100) -> None:
        self.start = start
        self.max_steps = max_steps
        self.visited: list[str] = []

    async def run(self, ctx: Any, payload: Any = None) -> tuple[str, Any]:
        curr: Optional[Node] = self.start
        last_action = "default"
        steps = 0
        self.visited = []
        while curr is not None:
            steps += 1
            if steps > self.max_steps:
                raise RuntimeError(
                    f"Flow exceeded max_steps={self.max_steps} "
                    f"(possible unbounded cycle: {self.visited[-10:]})"
                )
            self.visited.append(curr.name)
            last_action, payload = await curr._exec(ctx, payload)
            curr = curr._edges.get(last_action)
        return last_action, payload
