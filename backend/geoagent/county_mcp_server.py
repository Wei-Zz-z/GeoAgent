"""区县地图统计MCP：独立于快报服务，stdio供外部智能体调用。"""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server import MCPServer

from .analysis.county_charts import build_county_chart_map
from .config import Settings

mcp = MCPServer("GeoAgent 区县地图统计")


@mcp.tool()
async def create_county_chart_map(vector_zip_path: str, boundary_zip_path: str | None = None) -> dict[str, Any]:
    """读取模拟图斑ZIP；可另传区县行政区边界ZIP，以XZQDM关联并定位。保存完整地图JSON，返回路径及统计。只读源数据、不查旧库，几何需地图客户端渲染。"""
    if boundary_zip_path:
        artifact = await asyncio.to_thread(build_county_chart_map, Path(vector_zip_path), Path(boundary_zip_path))
    else:
        artifact = await asyncio.to_thread(build_county_chart_map, Path(vector_zip_path))
    directory = Settings().reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"county_map_{uuid4().hex}.json"
    await asyncio.to_thread(target.write_text, json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    data = artifact["data"]
    return {"kind": "map_chart_file", "artifact_path": str(target.resolve()),
            "metadata": data["metadata"],
            "counties": [feature["properties"] for feature in data["features"]],
            "notice": "完整几何保存在本机JSON文件中；需地图客户端渲染。底图来源：" + data["metadata"]["geometry_source"]}


if __name__ == "__main__":
    mcp.run()
