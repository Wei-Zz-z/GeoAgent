from __future__ import annotations

from typing import Optional

from ..core.agent import Agent
from ..tools.geo import get_geo_tools
from ..tools.registry import Tool

GEO_SYSTEM_PROMPT = (
    "你是 GeoAgent 的地理空间分析智能体，负责使用工具完成空间分析与数据可视化。\n"
    "规则：\n"
    "1. 需要空间计算或加载数据时，先调用工具，再基于工具结果回答。\n"
    "2. 工具结果会同步展示在用户的会话窗口中，不要在回答里重复输出完整 GeoJSON。\n"
    "3. 坐标一律使用 WGS84 经纬度（[经度, 纬度]）。\n"
    "4. 工具执行失败时，向用户解释原因并给出可操作的修正建议。\n"
    "5. 可用工具：list_datasets（列出数据集）、load_dataset（加载数据集）、"
    "buffer_point（点缓冲区）、polygon_area（多边形面积）、"
    "distance_between_points（两点距离）。"
)


class GeoAgent(Agent):
    def __init__(
        self,
        model: Optional[str] = None,
        tools: Optional[list[Tool]] = None,
    ) -> None:
        super().__init__(
            name="geo_analysis",
            system_prompt=GEO_SYSTEM_PROMPT,
            tools=tools if tools is not None else get_geo_tools(),
            model=model,
            max_turns=8,
        )
