"""概略覆盖面与设施所属区县相交；不估算人口覆盖或服务盲区。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform, unary_union


@lru_cache(maxsize=2)
def _read(path: str, modified_ns: int) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def district_boundary(directory: Path, facility: dict[str, Any], metric_crs: str, study_area: str = 'facility_district') -> tuple[dict[str, Any], Any]:
    path = directory / 'district_boundaries.geojson'
    if not path.is_file():
        return {'available': False, 'notice': '区县边界未配置，未计算行政区叠加面积。'}, None
    data = _read(str(path), path.stat().st_mtime_ns)
    candidates = [f for f in data['features'] if
                  (facility.get('district_id') and str(f['properties']['gb']) == '156' + facility['district_id']) or
                  (not facility.get('district_id') and f['properties']['name'] == facility.get('region'))]
    if study_area == 'hangzhou':
        candidates = data['features']
    if not candidates or (study_area != 'hangzhou' and len(candidates) != 1):
        return {'available': False, 'notice': '未唯一匹配设施所属区县边界，未计算行政区叠加面积。'}, None
    feature = candidates[0]
    project = Transformer.from_crs(data['crs']['properties']['name'], metric_crs, always_xy=True).transform
    originals = [shape(f['geometry']) for f in candidates]
    repaired = any(not geom.is_valid for geom in originals)
    geom = transform(project, unary_union([make_valid(geom) for geom in originals]))
    if not geom.is_valid:
        repaired = True
        geom = make_valid(geom)
    if geom.is_empty or geom.area <= 0:
        return {'available': False, 'notice': '区县边界无有效面积，未计算行政区叠加面积。'}, None
    return {'available': True, 'name': '杭州市（现有13区县边界合并）' if study_area == 'hangzhou' else feature['properties']['name'], 'source': data.get('source'),
            'area_mu': round(geom.area * .0015, 2), 'area_m2': round(geom.area, 2), 'boundary_repaired': repaired,
            'notice': '按所选研究区统计；面积比例分母为该边界文件全域面积，不代表社区或人口覆盖率。'}, geom


def intersection_statistics(coverage: Any, boundary: Any) -> dict[str, Any]:
    if boundary is None:
        return {'district_intersection_mu': None, 'district_intersection_m2': None, 'district_coverage_percent': None,
                'model_uncovered_m2': None}
    area = coverage.intersection(boundary).area
    return {'district_intersection_mu': round(area * .0015, 2),
            'district_intersection_m2': round(area, 2),
            'model_uncovered_m2': round(boundary.difference(coverage).area, 2),
            'district_coverage_percent': round(area / boundary.area * 100, 4)}
