"""地理演示工具（纯 Python 实现，暂不依赖 PyQGIS）。

这些工具用于演示工具/artifact 协议——结果如何在会话窗口中渲染。
后续可用运行在 worker 进程中的 PyQGIS 工具替换/扩充它们。
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


DEMO_DATASETS: dict[str, dict[str, Any]] = {
    "beijing_pois": {
        "name": "北京兴趣点示例",
        "description": "北京市区 6 个示例兴趣点（学校、医院、公园、商场等）",
        "features": [
            {"name": "北京大学", "category": "教育", "lon": 116.312, "lat": 39.992},
            {"name": "协和医院", "category": "医疗", "lon": 116.417, "lat": 39.914},
            {"name": "朝阳公园", "category": "公园", "lon": 116.479, "lat": 39.933},
            {"name": "国贸商城", "category": "商业", "lon": 116.459, "lat": 39.909},
            {"name": "故宫博物院", "category": "文化", "lon": 116.397, "lat": 39.917},
            {"name": "北京南站", "category": "交通", "lon": 116.378, "lat": 39.865},
        ],
    },
    "beijing_subway": {
        "name": "北京地铁站点示例",
        "description": "北京 6 个示例地铁站点",
        "features": [
            {"name": "天安门东", "line": "1号线", "lon": 116.407, "lat": 39.908},
            {"name": "西单", "line": "1号线", "lon": 116.374, "lat": 39.907},
            {"name": "国贸", "line": "1号线", "lon": 116.459, "lat": 39.909},
            {"name": "中关村", "line": "4号线", "lon": 116.317, "lat": 39.982},
            {"name": "北京南站", "line": "4号线", "lon": 116.378, "lat": 39.865},
            {"name": "朝阳门", "line": "2号线", "lon": 116.434, "lat": 39.923},
        ],
    },
}


class ListDatasetsParams(BaseModel):
    pass


class LoadDatasetParams(BaseModel):
    dataset_id: str = Field(description="Dataset id from list_datasets")


class BufferPointParams(BaseModel):
    lon: float = Field(description="Center longitude (WGS84)")
    lat: float = Field(description="Center latitude (WGS84)")
    radius_km: float = Field(gt=0, description="Buffer radius in kilometers")


class PolygonAreaParams(BaseModel):
    coordinates: list[list[float]] = Field(
        min_length=3,
        description="Ring coordinates as [[lon, lat], ...] (WGS84, planar approx.)",
    )


class DistanceParams(BaseModel):
    from_coord: list[float] = Field(
        min_length=2, max_length=2, description="Start point [lon, lat]"
    )
    to_coord: list[float] = Field(
        min_length=2, max_length=2, description="End point [lon, lat]"
    )


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [f["lon"], f["lat"]],
                },
                "properties": {k: v for k, v in f.items() if k not in ("lon", "lat")},
            }
            for f in features
        ],
    }


def _circle_geojson(lon: float, lat: float, radius_km: float) -> dict[str, Any]:
    n = 64
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    points = [
        [lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n)]
        for i in range(n)
    ]
    points.append(points[0])
    area_km2 = math.pi * radius_km * radius_km
    return {
        "type": "Feature",
        "properties": {"radius_km": radius_km, "area_km2": round(area_km2, 3)},
        "geometry": {"type": "Polygon", "coordinates": [points]},
    }


@register_tool("list_datasets", "List available demo geospatial datasets", ListDatasetsParams)
def list_datasets() -> ToolResult:
    rows = [
        {
            "id": ds_id,
            "name": ds["name"],
            "description": ds["description"],
            "features": len(ds["features"]),
        }
        for ds_id, ds in DEMO_DATASETS.items()
    ]
    content = "可用数据集:\n" + "\n".join(f"- {r['id']}: {r['name']} ({r['features']} 个要素)" for r in rows)
    return ToolResult(
        tool_call_id="",
        name="list_datasets",
        content=content,
        artifacts=[
            Artifact(
                kind="table",
                name="datasets",
                data={"columns": ["id", "name", "description", "features"], "rows": rows},
            )
        ],
    )


@register_tool("load_dataset", "Load a demo dataset as GeoJSON features", LoadDatasetParams)
def load_dataset(dataset_id: str) -> ToolResult:
    ds = DEMO_DATASETS.get(dataset_id)
    if ds is None:
        return ToolResult(
            tool_call_id="",
            name="load_dataset",
            content=f"数据集不存在: {dataset_id}",
            is_error=True,
        )
    geojson = _feature_collection(ds["features"])
    return ToolResult(
        tool_call_id="",
        name="load_dataset",
        content=f"已加载数据集 {ds['name']}，共 {len(ds['features'])} 个要素。",
        artifacts=[Artifact(kind="geojson", name=dataset_id, data=geojson)],
    )


@register_tool("buffer_point", "Create a circular buffer around a point", BufferPointParams)
def buffer_point(lon: float, lat: float, radius_km: float) -> ToolResult:
    geojson = _circle_geojson(lon, lat, radius_km)
    area_km2 = geojson["properties"]["area_km2"]
    return ToolResult(
        tool_call_id="",
        name="buffer_point",
        content=(
            f"已生成以 ({lon:.4f}, {lat:.4f}) 为中心、半径 {radius_km} km 的缓冲区，"
            f"面积约 {area_km2} km²。"
        ),
        artifacts=[Artifact(kind="geojson", name="buffer", data=geojson)],
    )


def _shoelace_area_km2(coordinates: list[list[float]]) -> float:
    ring = coordinates + [coordinates[0]]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2.0
    return area * (111.0 * 111.0)  # 小范围平面近似


@register_tool("polygon_area", "Compute the area of a polygon ring (km²)", PolygonAreaParams)
def polygon_area(coordinates: list[list[float]]) -> ToolResult:
    if len(coordinates) < 3:
        return ToolResult(
            tool_call_id="",
            name="polygon_area",
            content="多边形至少需要 3 个顶点",
            is_error=True,
        )
    area_km2 = round(_shoelace_area_km2(coordinates), 3)
    return ToolResult(
        tool_call_id="",
        name="polygon_area",
        content=f"多边形面积约 {area_km2} km²（平面近似，适用于小范围）。",
        artifacts=[
            Artifact(
                kind="geojson",
                name="polygon",
                data={
                    "type": "Feature",
                    "properties": {"area_km2": area_km2},
                    "geometry": {"type": "Polygon", "coordinates": [coordinates]},
                },
            )
        ],
    )


def _haversine_km(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


@register_tool(
    "distance_between_points",
    "Compute the great-circle distance between two points (km)",
    DistanceParams,
)
def distance_between_points(from_coord: list[float], to_coord: list[float]) -> ToolResult:
    distance_km = round(_haversine_km(from_coord, to_coord), 3)
    return ToolResult(
        tool_call_id="",
        name="distance_between_points",
        content=f"两点间大圆距离约 {distance_km} km。",
        artifacts=[
            Artifact(
                kind="geojson",
                name="distance_line",
                data={
                    "type": "Feature",
                    "properties": {"distance_km": distance_km},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [from_coord, to_coord],
                    },
                },
            )
        ],
    )


def get_geo_tools() -> list[Any]:
    names = (
        "list_datasets",
        "load_dataset",
        "buffer_point",
        "polygon_area",
        "distance_between_points",
    )
    return get_tools(*names)
