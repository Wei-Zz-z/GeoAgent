"""历史乡镇边界正面积相交试算，不改写为现行区划或社区覆盖。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely import make_valid, transform as transform_coordinates
from shapely.geometry import shape
from shapely.ops import transform, unary_union


@lru_cache(maxsize=2)
def _load(path: str, modified_ns: int, metric_crs: str) -> tuple[dict[str, Any], list]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    project = Transformer.from_crs(data['crs']['properties']['name'], metric_crs, always_xy=True).transform
    groups: dict[tuple[str, str], list] = {}
    repaired = 0
    for feature in data['features']:
        props = feature['properties']
        geom = transform_coordinates(shape(feature['geometry']), project, interleaved=False)
        if not geom.is_valid:
            geom = make_valid(geom)
            repaired += 1
        if geom.area > 0:
            groups.setdefault((props['County'], props['Town']), []).append(geom)
    boundaries = [(county, name, unary_union(parts)) for (county, name), parts in groups.items()]
    return {'available': True, 'boundary_count': len(boundaries), 'source': data.get('source'),
            'version_status': data.get('version_status'), 'repaired_count': repaired,
            'notice': '历史边界试算：仅面积大于0的相交乡镇街道计数，接触边线不计；原县区名称可能已变更。相交不代表全部覆盖，不是社区或人口覆盖率。'}, boundaries


def township_boundaries(directory: Path, metric_crs: str) -> tuple[dict[str, Any], list]:
    path = directory / 'historical_townships.geojson'
    if not path.is_file():
        return {'available': False, 'notice': '历史乡镇街道边界未配置，暂不能统计。'}, []
    return _load(str(path), path.stat().st_mtime_ns, metric_crs)


def township_intersections(coverage: Any, boundaries: list, available: bool) -> dict[str, Any]:
    rows = []
    for county, name, boundary in boundaries:
        if not coverage.intersects(boundary):
            continue
        area = coverage.intersection(boundary).area
        if area > 0:
            rows.append({'county': county, 'name': name, 'intersection_m2': round(area, 2),
                         'boundary_area_m2': round(boundary.area, 2),
                         'intersection_percent': round(area / boundary.area * 100, 4)})
    rows.sort(key=lambda row: (-row['intersection_m2'], row['county'], row['name']))
    return {'historical_township_count': len(rows) if available else None, 'historical_townships': rows}


def township_uncovered(coverage: Any, boundaries: list, available: bool) -> tuple[Any, dict]:
    """仅在与本档覆盖面有正面积相交的街道内计算剩余范围。"""
    selected = [boundary for _, _, boundary in boundaries
                if coverage.intersects(boundary) and coverage.intersection(boundary).area > 0]
    scope = unary_union(selected)
    remaining = scope.difference(coverage)
    return remaining, {
        'uncovered_scope': 'intersecting_townships',
        'uncovered_township_count': len(selected) if available else None,
        'model_uncovered_m2': round(remaining.area, 2) if available else None,
    }