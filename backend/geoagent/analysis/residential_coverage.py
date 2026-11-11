"""监测指标按居住用地统计；现有单设施概略面只提供辅助试算。"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely import make_valid
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union
from shapely.errors import GEOSException


SOURCE_TITLE = '城镇国土空间监测指标研究项目成果报告'
SOURCE_SECTION = '5.4.3 指标计算方法确定／宜居生活'
ELDER_FORMULA = '15分钟步行范围覆盖的居住用地面积／同一研究区居住用地总面积×100%'


def _empty_statistics() -> dict[str, Any]:
    return {'residential_covered_area_m2': None, 'residential_total_area_m2': None,
            'residential_coverage_percent': None}


def calculate_residential_coverage(
    coverages: Sequence[BaseGeometry], residential_land: Sequence[BaseGeometry],
    study_boundary: BaseGeometry | None,
) -> dict[str, Any]:
    """输入为同一米制坐标系；分子与分母都先并集去重并裁剪研究区。"""
    if study_boundary is None or study_boundary.is_empty or not residential_land or not coverages:
        return _empty_statistics()
    geometries = [study_boundary, *residential_land, *coverages]
    if any(not geom.is_valid or (not geom.is_empty and (
           not all(math.isfinite(v) for v in geom.bounds)
           or geom.geom_type not in {'Polygon', 'MultiPolygon'}))
           for geom in geometries):
        return _empty_statistics()
    land = unary_union(residential_land).intersection(study_boundary)
    total = land.area
    if total <= 0:
        return _empty_statistics()
    covered = land.intersection(unary_union(coverages)).area
    return {'residential_covered_area_m2': round(covered, 2),
            'residential_total_area_m2': round(total, 2),
            'residential_coverage_percent': round(covered / total * 100, 4)}


@lru_cache(maxsize=4)
def _read_land(path: str, modified_ns: int, metric_crs: str) -> tuple[dict[str, Any], BaseGeometry]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    source_crs = data.get('crs', {}).get('properties', {}).get('name')
    if not source_crs:
        raise ValueError('居住用地未声明坐标系，不能猜测。')
    target_crs = CRS.from_user_input(metric_crs)
    if not target_crs.is_projected or not target_crs.axis_info or any(
        axis.unit_conversion_factor != 1 for axis in target_crs.axis_info
    ):
        raise ValueError('居住用地面积计算需要米制投影。')
    project = Transformer.from_crs(source_crs, target_crs, always_xy=True).transform
    if data.get('type') != 'FeatureCollection' or not data.get('features'):
        raise ValueError('居住用地没有面要素。')
    polygons = []
    for feature in data['features']:
        geometry = shape(feature['geometry'])
        if geometry.is_empty or geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
            raise ValueError('居住用地须全部为已筛选的非空面要素。')
        projected = make_valid(transform(project, geometry))
        if projected.geom_type not in {'Polygon', 'MultiPolygon'} or not all(
            math.isfinite(value) for value in projected.bounds
        ):
            raise ValueError('居住用地投影后无有效面。')
        polygons.append(projected)
    return {'source': data.get('source') or '已配置的居住用地面',
            'source_crs': str(source_crs), 'feature_count': len(polygons)}, unary_union(polygons)


class ResidentialCoverage:
    def __init__(self, directory: Path, boundary: BaseGeometry | None, metric_crs: str) -> None:
        self.land: BaseGeometry | None = None
        self.boundary = boundary
        self.metadata: dict[str, Any] = {
            'available': False, 'status': 'missing_data', 'source': None,
            'reason': '缺少居住用地面数据，未计算居住用地覆盖率。', 'total_area_m2': None,
        }
        path = directory / 'residential_land.geojson'
        if not path.is_file():
            return
        if boundary is None or boundary.is_empty or not boundary.is_valid or boundary.area <= 0:
            self.metadata['reason'] = '研究区边界不可用，未计算居住用地覆盖率。'
            return
        try:
            source, land = _read_land(str(path), path.stat().st_mtime_ns, metric_crs)
            self.metadata.update(source)
            self.land = land.intersection(boundary)
            if self.land.is_empty or self.land.area <= 0:
                self.land = None
                self.metadata.update(status='zero_denominator', reason='研究区内居住用地面积为零，覆盖率未计算。')
                return
            self.metadata.update(available=True, status='available', total_area_m2=round(self.land.area, 2),
                                 reason='已按研究区裁剪并合并居住用地；当前仅作单设施概略覆盖试算。')
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, ProjError, GEOSException) as exc:
            self.land = None
            self.metadata.update(status='invalid_data', reason=f'居住用地数据或坐标系不完整（{type(exc).__name__}），未计算覆盖率。')

    def summarize(self, coverage: BaseGeometry) -> dict[str, Any]:
        if self.land is None:
            return _empty_statistics()
        return calculate_residential_coverage([coverage], [self.land], self.boundary)


def monitoring_indicators(rows: list[dict[str, Any]], travel_mode: str,
                          reference: dict[str, Any]) -> list[dict[str, Any]]:
    """原文区域指标与单设施辅助值分别记录，不能用人口或行政区比例填补。"""
    common = {'source': SOURCE_TITLE, 'source_section': SOURCE_SECTION, 'unit': '%'}
    scope_reason = '社区养老设施类型、完整集合及覆盖范围精度尚未核验；本轮仅分析单设施。'
    formal = {**common, 'id': 'community_elder_15min_coverage',
              'name': '社区养老设施步行15分钟覆盖率', 'value': None,
              'status': 'scope_incomplete' if reference.get('available') else 'missing_data',
              'reason': scope_reason if reference.get('available') else reference.get('reason', '居住用地不可用。') + scope_reason,
              'formula': ELDER_FORMULA, 'scope': 'study_area_all_community_facilities',
              'required_data': ['研究区居住用地面', '完整且分类核实的社区养老设施', '全部适用设施的15分钟步行范围']}
    row = next((row for row in rows if row['minutes'] == 15), None)
    value = row.get('residential_coverage_percent') if row and travel_mode == 'walking' else None
    eligible = row is not None and travel_mode == 'walking'
    single = {**common, 'id': 'single_facility_residential_15min',
              'name': '本设施15分钟居住用地覆盖率试算', 'value': value,
              'status': 'trial' if value is not None else 'not_applicable' if not eligible
                        else reference.get('status', 'missing_data') if not reference.get('available') else 'not_calculated',
              'reason': '基于单设施概略面，不能视为全区社区养老设施监测指标。' if value is not None
                        else '本轮不是含15分钟档的步行分析，未计算该步行指标。' if not eligible
                        else reference.get('reason', '居住用地不可用。') if not reference.get('available')
                        else '本次结果未保存15分钟居住用地叠加数值，请重新分析。',
              'formula': '本设施15分钟步行概略面覆盖的居住用地面积／同一研究区居住用地总面积×100%',
              'scope': 'selected_facility', 'required_data': ['研究区居住用地面', '本设施15分钟步行概略面']}
    return [formal, single]
