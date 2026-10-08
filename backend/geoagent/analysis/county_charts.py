"""读取模拟图斑，按区县统计并绑定图斑覆盖范围；不是行政区边界。"""

from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from shapely import make_valid, union_all
from shapely.geometry import mapping

from ..tools.labels import TBLX_LABELS

MAX_VECTOR_ZIP = 100 * 1024 * 1024


def read_vector_zip(path: Path) -> Any:
    """GDAL只读ZIP，无需解压；只接受一套Shapefile及明确的坐标系。"""
    import pyogrio

    path = path.resolve(strict=True)
    if path.suffix.lower() != ".zip" or path.stat().st_size > MAX_VECTOR_ZIP:
        raise ValueError("请选择不超过100MB的Shapefile ZIP。")
    with ZipFile(path) as archive:
        members = archive.infolist()
        if sum(item.file_size for item in members) > 300 * 1024 * 1024:
            raise ValueError("ZIP解压后超过300MB。")
        if any(".." in Path(item.filename).parts or item.filename.startswith(("/", "\\")) for item in members):
            raise ValueError("ZIP包含不安全路径。")
        shapes = [item.filename for item in members if item.filename.lower().endswith(".shp")]
        if len(shapes) != 1:
            raise ValueError("ZIP应只含一套Shapefile。")
    frame = pyogrio.read_dataframe(f"/vsizip/{path.as_posix()}/{shapes[0]}", encoding="UTF-8")
    if frame.crs is None:
        raise ValueError("缺少坐标系，不能猜测位置。")
    result = frame.to_crs(4326)
    result.attrs["source_crs"] = frame.crs.to_string()
    return result


def _boundary_index(boundaries: Any) -> tuple[dict[str, Any], list[str]]:
    required = {"XZQDM", "XZQMC", "geometry"}
    if not required <= set(boundaries.columns):
        raise ValueError("行政区边界缺少XZQDM、XZQMC或geometry字段。")
    if boundaries.empty or len(boundaries) > 10000:
        raise ValueError("行政区边界应包含1至10000条区县记录。")
    indexed, warnings = {}, []
    for _, row in boundaries.iterrows():
        code, name = str(row["XZQDM"]).strip(), str(row["XZQMC"]).strip()
        if not code.isdigit() or len(code) != 6 or not name or name.lower() == "nan":
            raise ValueError("行政区边界代码或名称缺失。")
        if code in indexed:
            raise ValueError("行政区边界代码重复，请先按区县合并。")
        geometry = row.geometry
        if geometry is None or geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"{name}行政区边界缺少有效多边形。")
        if not geometry.is_valid:
            geometry = make_valid(geometry)
            if geometry.geom_type == "GeometryCollection":
                geometry = union_all([part for part in geometry.geoms if part.geom_type in {"Polygon", "MultiPolygon"}])
            if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError(f"{name}边界无法修复为多边形。")
            warnings.append(f"{name}原始行政区几何无效，已修复用于显示与定位；请核对原件。")
        indexed[code] = {"name": name, "geometry": geometry}
    return indexed, warnings


def aggregate_counties(frame: Any, boundaries: Any = None) -> dict[str, Any]:
    required = {"XZQDM", "XMC", "TBLX", "MJ", "MU", "geometry"}
    if not required <= set(frame.columns):
        raise ValueError("缺少字段：" + ",".join(sorted(required - set(frame.columns))))
    if frame.empty:
        raise ValueError("矢量文件为空。")
    if len(frame) > 200000:
        raise ValueError("当前版本最多支持20万个图斑。")
    boundary_index, warnings = _boundary_index(boundaries) if boundaries is not None else ({}, [])
    counties: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code, name = str(row["XZQDM"]).strip(), str(row["XMC"]).strip()
        if not code.isdigit() or len(code) != 6 or not name or name.lower() == "nan":
            raise ValueError("区县代码或名称缺失，不能定位。")
        area, mu = float(row["MJ"]), float(row["MU"])
        if not math.isfinite(area) or not math.isfinite(mu) or area < 0 or mu < 0:
            raise ValueError("面积缺失或无效，不能静默忽略。")
        if abs(area * 0.0015 - mu) > 0.001:
            raise ValueError("MJ与MU单位换算不一致，请核对字段。")
        county = counties.setdefault(code, {"name": name, "areas": defaultdict(float), "count": 0,
                                             "geometries": [], "missing_geometry": 0})
        if county["name"] != name:
            raise ValueError("同一区县编号对应不同名称。")
        kind = str(row["TBLX"]).strip()
        label = TBLX_LABELS.get(kind, f"未识别地类（{kind}）")
        county["areas"][label] += area * 0.0015
        county["count"] += 1
        if boundaries is not None:
            continue
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            county["missing_geometry"] += 1
            continue
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("存在非多边形几何。")
        county["geometries"].append(make_valid(geometry) if not geometry.is_valid else geometry)
    features = []
    for code, county in sorted(counties.items()):
        if county["missing_geometry"]:
            warnings.append(f"{county['name']}有{county['missing_geometry']}条缺少几何，面积仍计入；定位覆盖不完整。")
        if boundaries is not None:
            boundary = boundary_index.get(code)
            if boundary is None:
                raise ValueError(f"{county['name']}（{code}）未匹配到行政区边界，不能猜测。")
            if boundary["name"] != county["name"]:
                raise ValueError(f"区划代码{code}在两份数据中的名称不一致，请核对版本。")
            footprint = boundary["geometry"]
        else:
            if not county["geometries"]:
                raise ValueError(f"{county['name']}无可用多边形，不能定位。")
            footprint = union_all(county["geometries"])
        anchor = footprint.representative_point()
        # 简化只影响底图显示，不影响面积汇总或图表中心。
        display = footprint.simplify(0.0005, preserve_topology=True)
        if not display.covers(anchor):
            display = footprint
        features.append({"type": "Feature", "geometry": mapping(display), "properties": {
            "XZQDM": code, "name": county["name"], "count": county["count"],
            "area_mu": sum(county["areas"].values()), "values": dict(county["areas"]),
            "anchor": [anchor.x, anchor.y], "has_statistics": True,
        }})
    if boundaries is not None:
        for code, boundary in sorted(boundary_index.items()):
            if code in counties:
                continue
            warnings.append(f"{boundary['name']}没有对应变化图斑统计，不视为零。")
            features.append({"type": "Feature", "geometry": mapping(boundary["geometry"].simplify(0.0005, preserve_topology=True)),
                             "properties": {"XZQDM": code, "name": boundary["name"], "count": None,
                                            "area_mu": None, "values": {}, "has_statistics": False}})
    return {"type": "FeatureCollection", "features": features, "metadata": {
        "mock": True, "unit": "亩", "feature_count": len(frame), "county_count": len(counties),
        "area_mu": sum(sum(county["areas"].values()) for county in counties.values()),
        "boundary_count": len(boundary_index), "uses_administrative_boundaries": boundaries is not None,
        "boundary_source_crs": boundaries.attrs.get("source_crs", str(boundaries.crs)) if boundaries is not None else None,
        "geometry_source": "用户提供的区县行政区边界，按XZQDM关联统计" if boundaries is not None else "按区县合并变化图斑覆盖范围，不是行政区边界",
        "anchor_method": "原行政区边界的内部代表点，不是行政中心" if boundaries is not None else "未简化图斑覆盖范围的内部代表点，不是区县行政中心",
        "area_method": "SUM(MJ)×0.0015；MU仅用于逐行单位校验，不重复累加",
        "display_simplification_degrees": 0.0005, "warnings": warnings,
    }}


def build_county_chart_map(path: Path, boundary_path: Path | None = None) -> dict[str, Any]:
    return {"kind": "map_chart", "name": "区县地类面积统计（模拟数据）",
            "data": aggregate_counties(read_vector_zip(path), read_vector_zip(boundary_path) if boundary_path else None)}
