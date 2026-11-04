"""历史村级边界相交统计；保留混合粒度，不推断现行社区覆盖率。"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree


def boundary_kind(name: str, code: str) -> str:
    if code.endswith('000'):
        return '街道乡镇级或汇总单元'
    if '社区' in name:
        return '名称含社区'
    if name.endswith('村'):
        return '名称为村'
    return '其他或粒度待核实'


@lru_cache(maxsize=2)
def _load(path: str, modified_ns: int, metric_crs: str) -> tuple[dict, list, STRtree]:
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        data = json.load(stream)
    project = Transformer.from_crs(data['source_crs'], metric_crs, always_xy=True).transform
    units, geometries = [], []
    repaired = 0
    for feature in data['features']:
        geom = transform(project, shape(feature['geometry']))
        if not geom.is_valid:
            geom = make_valid(geom)
            repaired += 1
        if geom.area <= 0:
            continue
        units.append(feature['properties'])
        geometries.append(geom)
    meta = {key: value for key, value in data.items() if key not in {'features', 'type'}}
    meta.update(available=bool(units), boundary_count=len(units), repaired_count=repaired)
    return meta, units, STRtree(geometries)


def village_boundaries(directory: Path, metric_crs: str) -> tuple[dict, list, STRtree]:
    path = directory / 'historical_villages.geojson.gz'
    if not path.is_file():
        return {'available': False, 'notice': '村级边界未配置，暂不能统计。'}, [], STRtree([])
    return _load(str(path), path.stat().st_mtime_ns, metric_crs)


def village_intersections(coverage: Any, units: list, tree: STRtree, available: bool) -> dict:
    rows = []
    for index in tree.query(coverage, predicate='intersects'):
        boundary = tree.geometries[index]
        area = coverage.intersection(boundary).area
        if area > 0:
            rows.append(dict(units[index], intersection_m2=round(area, 2),
                             boundary_area_m2=round(boundary.area, 2),
                             intersection_percent=round(area / boundary.area * 100, 4)))
    rows.sort(key=lambda row: (-row['intersection_m2'], row['code']))
    return {'historical_village_unit_count': len(rows) if available else None,
            'historical_community_named_count': sum(row['kind'] == '名称含社区' for row in rows) if available else None,
            'historical_village_units': rows}


def village_display_features(rows: list[dict], units: list, tree: STRtree, unproject: Any) -> list[dict]:
    """仅输出本次相交边界；五米简化只用于显示，不参与面积计算。"""
    codes = {item['code'] for row in rows for item in row['historical_village_units']}
    return [{'type': 'Feature', 'properties': dict(unit, kind='village_boundary'),
             'geometry': mapping(transform(unproject, tree.geometries[index].simplify(5, preserve_topology=True)))}
            for index, unit in enumerate(units) if unit['code'] in codes]
