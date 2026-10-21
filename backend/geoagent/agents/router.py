from __future__ import annotations

from typing import Any, Optional

from ..config import elder_care_enabled
from ..core.events import Event
from ..core.node import Node


def route_tool_spec() -> dict[str, Any]:
    """路由工具定义；养老场景关闭时把 elder_care 从枚举里摘掉。

    这样即使问题里出现"养老"字样，路由模型也没有可选项，不会把请求
    引到未部署的场景上。
    """
    scenes = ["sql"]
    description = (
        "sql: 数据库查询与土地变化统计（计算/统计/汇总/面积/占比/"
        "每种类型/耕地/建设用地/地类/图斑/流向、快报/简报生成）；"
    )
    if elder_care_enabled():
        scenes.append("elder_care")
        description += "elder_care: 养老机构可达性/覆盖/床位/供需分析；"
    scenes.append("chat")
    description += "chat: 其它"
    return {
        "type": "function",
        "function": {
            "name": "route",
            "description": "判断用户请求应交给哪个智能体",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": scenes,
                        "description": description,
                    }
                },
                "required": ["target"],
            },
        },
    }

# 各目标的判定标准写在 ROUTE_TOOL 的 target 枚举描述里（单一来源），此处只给角色与兜底策略。
ROUTER_SYSTEM_PROMPT = (
    "你是 GeoAgent 的意图路由器：按 route 工具 target 参数的说明判断用户请求应交给哪个"
    "智能体，不确定时选 chat。"
)

# 当路由模型不可用时的兜底方案。
ROUTE_ELDER_CARE_KEYWORDS = (
    "养老",
    "老年",
    "机构",
    "护理",
    "可达",
    "覆盖",
    "床位",
    "供需",
    "nursing",
    "elder",
    "care",
    "accessibility",
    "facility",
)

ROUTE_SQL_KEYWORDS = (
    "查询",
    "统计",
    "汇总",
    "计算",
    "面积",
    "占比",
    "净变化",
    "流出",
    "流入",
    "sql",
    "数据库",
    "数据表",
    "表结构",
    "表字段",
    "地类",
    "图斑",
    "耕地",
    "建设用地",
    "变化图斑",
    "地类变化",
    "流向",
    "tblx",
    "dict",
    "select",
    "count",
    "sum",
    "快报",
    "简报",
    "监测报告",
    "生成报告",
    "总结报告",
    "group by",
    "join",
    "where",
)


class RouterNode(Node):
    """将用户请求路由到 SQL / 养老 / 通用对话智能体。"""

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
                tools=[route_tool_spec()],
            )
            candidate = (
                message.tool_calls[0].arguments.get("target") if message.tool_calls else None
            )
            allowed = ("sql", "elder_care", "chat") if elder_care_enabled() else ("sql", "chat")
            if candidate in allowed:
                target = candidate
            reason = message.content or ""
        except Exception:
            # 兜底：路由模型不可用时改用关键字启发式路由。
            lowered = user_text.lower()
            if elder_care_enabled() and any(k in lowered for k in ROUTE_ELDER_CARE_KEYWORDS):
                target = "elder_care"
            elif any(k in lowered for k in ROUTE_SQL_KEYWORDS):
                target = "sql"
            else:
                target = "chat"
            reason = "heuristic fallback (router model unavailable)"

        await ctx.emit(Event("route", {"target": target, "reason": reason}))
        # 记录本次路由结果，供 Agent 持久化到最终助手消息。
        ctx.route = target
        return target, payload
