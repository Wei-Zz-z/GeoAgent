"""现有养老智能体的浙江试点工具。"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated, Literal, Any

from pydantic import BaseModel, Field

from ..analysis.facility_access import get_facility_access
from .registry import Tool, get_tools, register_tool
from .result import Artifact, ToolResult


def area_summary_mu(value: Any) -> Any:
    """给模型的面积统一为亩；原始地图计算数据仍保留平方米。"""
    if isinstance(value, list):
        return [area_summary_mu(item) for item in value]
    if isinstance(value, dict):
        return {key[:-3] + '_mu' if key.endswith('_m2') else key:
                (round(item * .0015, 2) if item is not None else None) if key.endswith('_m2')
                else area_summary_mu(item)
                for key, item in value.items()
                if not (key.endswith('_mu') and key[:-3] + '_m2' in value)}
    return value


class SearchFacilitiesParams(BaseModel):
    query: str = Field(default='', max_length=100, description='机构名称、地址或区县名；留空列出试点设施。')
    limit: int = Field(default=10, ge=1, le=30, description='最多返回的设施数。')


class FacilityAccessParams(BaseModel):
    study_area: Literal['facility_district', 'hangzhou'] = Field(default='facility_district', description='研究区：设施所属区县，或杭州13区县合并范围；影响统计分母，不改变出发设施和路网。')
    travel_mode: Literal['walking', 'driving'] = Field(default='walking', description='walking步行；driving简化驾车试算。')
    driving_speed_kmh: float = Field(default=30, ge=5, le=60, allow_inf_nan=False, description='驾车假设速度，默认30公里/小时，不含拥堵和路口等待。')
    facility_id: str = Field(description='必须使用检索工具返回的设施编号，不猜测编号。')
    thresholds: list[Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)]] = Field(
        default_factory=lambda: [5, 10, 15, 20, 25, 30], min_length=1, max_length=6, description='步行分钟数，默认5、10、15、20、25、30六档，最多30分钟。')
    walking_speed_kmh: float = Field(default=4.2, ge=1, le=6, allow_inf_nan=False,
                                    description='步行速度，公里/小时；默认4.2仅为试算假设，不代表老年人统一标准。')


@register_tool('search_elder_facilities', '检索已准备的浙江试点养老机构，返回可分析的设施编号、名称和地址。未命中不能改用其他城市数据。', SearchFacilitiesParams)
async def search_elder_facilities(query: str = '', limit: int = 10) -> ToolResult:
    def run() -> ToolResult:
        data = get_facility_access()
        rows = data.search(query, limit)
        return ToolResult('', 'search_elder_facilities',
            json.dumps({'试点区域': data.metadata['region'], '设施总数': len(data.facilities),
                        '区县': getattr(data, 'districts', []), '结果': rows, '说明': '同名或多个候选需用户选择。坐标系待核实；available=false的机构不可分析，须说明status原因。'}, ensure_ascii=False),
            artifacts=[Artifact(kind='table', name='养老设施检索', data={
                'columns': ['设施编号', '机构名称', '地址', '坐标状态'],
                'rows': [[f['id'], f['name'], f['address'], f.get('status', '待核实')] for f in rows]})])
    return await asyncio.to_thread(run)


@register_tool('analyze_elder_accessibility', '计算选定养老机构沿试点步行路网出发的可达道路和5至30分钟六档展示范围，返回地图、统计和限制说明。无法接入路网时明确失败，不画直线圆代替。', FacilityAccessParams)
async def analyze_elder_accessibility(facility_id: str, thresholds: list[float] | None = None,
                                      walking_speed_kmh: float = 4.2, travel_mode: str = 'walking',
                                      driving_speed_kmh: float = 30, study_area: str = 'facility_district') -> ToolResult:
    def run() -> ToolResult:
        data = get_facility_access() if travel_mode == 'walking' else get_facility_access(travel_mode)
        speed = driving_speed_kmh if travel_mode == 'driving' else walking_speed_kmh
        label = '驾车' if travel_mode == 'driving' else '步行'
        times = [5, 10, 15, 20, 25, 30] if thresholds is None else thresholds
        result = data.analyze(facility_id, times, speed) if study_area == 'facility_district' else data.analyze(facility_id, times, speed, study_area)
        summary = area_summary_mu({k: v for k, v in result.items() if k != 'geojson'})
        summary['area_unit'] = '亩'
        result['geojson']['statistics'] = [dict(row, travel_budget_m=round(row['minutes'] * speed * 1000 / 60, 1),
                                              walking_budget_m=round(row['minutes'] * speed * 1000 / 60, 1) if travel_mode == 'walking' else None) for row in result['rows']]
        result['geojson']['speed_kmh'] = speed
        response = ToolResult('', 'analyze_elder_accessibility', json.dumps(summary, ensure_ascii=False), artifacts=[
            Artifact(kind='geojson', name=f"{result['facility']['name']} · {label}可达性试算", data=result['geojson']),
            Artifact(kind='table', name='可达道路统计（试算）', data={
                'columns': [f'{label}时间（分钟）', f'单次{label}距离预算（米）', '可达道路累计长度（米）', '概略覆盖面积（亩）', '面内其他已收录机构（个）'],
                'rows': [[r['minutes'], round(r['minutes'] * speed * 1000 / 60, 1), r['road_length_m'], round(r['coverage_area_m2'] * .0015, 2) if r.get('coverage_area_m2') is not None else r.get('coverage_area_mu'), r.get('other_facility_count')] for r in result['rows']]})])
        if result['population_reference']['available']:
            response.artifacts.append(Artifact(kind='table', name='2020年总人口覆盖估算（非老年人口）', data={
                'columns': ['分钟', '概略面内估算人口', '研究区模型未覆盖人口', '研究区人口占比（%）'],
                'rows': [[r['minutes'], r['covered_population_2020'], r['model_uncovered_population_2020'],
                          r['population_coverage_percent']] for r in result['rows']]}))
        return response
    return await asyncio.to_thread(run)


def get_facility_access_tools() -> list[Tool]:
    return get_tools('search_elder_facilities', 'analyze_elder_accessibility')
