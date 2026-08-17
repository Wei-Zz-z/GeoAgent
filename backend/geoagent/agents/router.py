from __future__ import annotations

from typing import Any, Optional

from ..core.events import Event
from ..core.node import Node

ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "route",
        "description": "Route the user request to the right agent",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["geo", "chat"],
                    "description": (
                        "geo: spatial analysis / data / map / coordinates / buffer / "
                        "distance / area / layers; chat: anything else"
                    ),
                }
            },
            "required": ["target"],
        },
    },
}

ROUTER_SYSTEM_PROMPT = (
    "你是 GeoAgent 的意图路由器。判断用户请求是否需要地理空间分析工具"
    "（加载数据集、缓冲区、距离、面积、图层、坐标、地图可视化、空间分析等），"
    "需要则调用 route 工具并设置 target=geo，否则设置 target=chat。"
)

# 当路由模型不可用时的兜底方案。
ROUTE_GEO_KEYWORDS = (
    "缓冲区",
    "buffer",
    "距离",
    "面积",
    "图层",
    "数据",
    "坐标",
    "加载",
    "叠加",
    "裁剪",
    "相交",
    "地图",
    "poi",
    "兴趣点",
    "geojson",
    "shp",
    "矢量",
    "栅格",
    "分析",
)


class RouterNode(Node):
    """将用户请求路由到地理分析智能体或通用对话智能体。"""

    def __init__(self, model: Optional[str] = None) -> None:
        super().__init__(name="router")
        self.model = model

    async def exec(self, ctx: Any, payload: Any) -> tuple[str, Any]:
        user_text = str(payload)
        model = self.model or ctx.model
        target = "chat"
        reason = ""
        try:
            message = await ctx.llm.chat(
                model=model,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                tools=[ROUTE_TOOL],
            )
            if message.tool_calls:
                candidate = message.tool_calls[0].arguments.get("target")
                if candidate in ("geo", "chat"):
                    target = candidate
            reason = message.content or ""
        except Exception:
            # 兜底：路由模型不可用时改用关键字启发式路由。
            lowered = user_text.lower()
            target = "geo" if any(k in lowered for k in ROUTE_GEO_KEYWORDS) else "chat"
            reason = "heuristic fallback (router model unavailable)"

        await ctx.emit(Event("route", {"target": target, "reason": reason}))
        return target, payload
