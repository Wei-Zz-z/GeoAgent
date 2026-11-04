"""设施步行可达范围：米制路网、边上接入、部分路段截取与可追溯试算。"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .regional_facility_access import RegionalFacilityAccess

import networkx as nx
from shapely import concave_hull
from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import substring, transform, unary_union
from shapely.strtree import STRtree
from .community_coverage import reference_points, summarize_communities
from .district_overlay import district_boundary, intersection_statistics
from .township_overlay import township_boundaries, township_intersections
from .population_overlay import PopulationOverlay
from .village_overlay import village_boundaries, village_intersections, village_display_features


def data_directory() -> Path:
    """独立业务数据目录，不复用会话数据目录。"""
    return Path(os.environ.get('GEOAGENT_ELDER_DATA_DIR') or
                Path(__file__).resolve().parents[2] / 'data' / 'elder_access')


class FacilityAccess:
    """加载后只读；每次分析单独复制图用于插入临时接入点。"""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self.metadata = bundle['metadata']
        self.facilities = bundle['facilities']
        self.mode = self.metadata.get('travel_mode', 'walking')
        self.project = Transformer.from_crs(4326, self.metadata['metric_crs'], always_xy=True).transform
        self.unproject = Transformer.from_crs(self.metadata['metric_crs'], 4326, always_xy=True).transform
        self.graph = nx.DiGraph() if self.mode == 'driving' else nx.Graph()
        # 同层共享原始顶点才连接；不把平面相交的高架与地面道路直接打通。
        for road in bundle['roads']:
            geom = transform(self.project, shape(road['geometry']))
            lines = list(geom.geoms) if geom.geom_type == 'MultiLineString' else [geom]
            level = str(road['properties'].get('layer', '0'))
            for line in lines:
                coords = list(line.coords)
                for a, b in zip(coords, coords[1:]):
                    u, v = (round(a[0], 3), round(a[1], 3), level), (round(b[0], 3), round(b[1], 3), level)
                    length = math.dist(u[:2], v[:2])
                    if length > 0:
                        if self.mode == 'driving':
                            direction = road['properties'].get('oneway')
                            if direction not in {'B', 'F', 'T'}:
                                raise ValueError('驾车道路缺少已识别的单行方向，不能按双向猜测。')
                            if direction in {'B', 'F'}:
                                self.graph.add_edge(u, v, length=length)
                            if direction in {'B', 'T'}:
                                self.graph.add_edge(v, u, length=length)
                        else:
                            self.graph.add_edge(u, v, length=length)
        if not self.graph.number_of_edges():
            raise ValueError('试点区域没有可用于步行分析的道路。')
        self.edges = list(dict.fromkeys(tuple(sorted((u, v))) for u, v in self.graph.edges))
        self.segments = [LineString([u[:2], v[:2]]) for u, v in self.edges]
        self.tree = STRtree(self.segments)
        nx.freeze(self.graph)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """名称、地址和试点区名称检索，精确名称优先。"""
        query = query.strip()
        rows = [f for f in self.facilities if not query or any(query in str(f.get(k, ''))
                for k in ('id', 'name', 'address', 'region'))]
        return sorted(rows, key=lambda f: (f['name'] != query, f['name'], f['id']))[:limit]

    def analyze(self, facility_id: str, thresholds: list[float], speed_kmh: float, study_area: str = 'facility_district') -> dict[str, Any]:
        """路段长度/速度计算；接入距离计入耗时，不使用直线圆形替代。"""
        if study_area not in {'facility_district', 'hangzhou'}:
            raise ValueError('研究区仅支持设施所属区县或杭州全域。')
        if not thresholds or len(thresholds) > 6 or any(not math.isfinite(t) or t <= 0 or t > 30 for t in thresholds):
            raise ValueError('时间阈值须为1至6个大于0且不超过30分钟的数值。')
        low, high = (5, 60) if self.mode == 'driving' else (1, 6)
        if not math.isfinite(speed_kmh) or not low <= speed_kmh <= high:
            raise ValueError(f'当前模式速度须在{low}至{high}公里/小时之间。')
        facility = next((f for f in self.facilities if f['id'] == facility_id), None)
        if facility is None:
            raise ValueError('设施编号不存在，请先检索并使用返回的设施编号。')
        origin = transform(self.project, Point(facility['coordinates']))
        index = int(self.tree.nearest(origin))
        segment = self.segments[index]
        u, v = self.edges[index]
        offset = segment.project(origin)
        snapped = segment.interpolate(offset)
        access_m = origin.distance(snapped)
        if access_m > 100:
            raise ValueError(f'设施距最近可用道路约{access_m:.0f}米，超过100米接入上限。请核实坐标和入口，暂不生成范围。')
        graph = self.graph.copy()
        start = 'facility_snap'
        forward = self.graph.has_edge(u, v)
        reverse = self.graph.has_edge(v, u)
        if reverse or offset < .001:
            graph.add_edge(start, u, length=offset)
        if forward or segment.length-offset < .001:
            graph.add_edge(start, v, length=segment.length-offset)
        metres_min = speed_kmh * 1000 / 60
        distances = nx.single_source_dijkstra_path_length(
            graph, start, cutoff=max(thresholds)*metres_min-access_m, weight='length'
        ) if max(thresholds)*metres_min >= access_m else {}
        features: list[dict[str, Any]] = []
        rows: list[dict[str, float]] = []
        larger_coverage = None
        community_reference, community_points = reference_points(data_directory(), self.project)
        district_reference, boundary = district_boundary(data_directory(), facility, self.metadata['metric_crs'], study_area)
        township_reference, townships = township_boundaries(data_directory(), self.metadata['metric_crs'])
        village_reference, village_units, village_tree = village_boundaries(data_directory(), self.metadata['metric_crs'])
        population = PopulationOverlay(data_directory(), boundary, self.metadata['metric_crs'])
        facility_points = [(f['id'], transform(self.project, Point(f['coordinates'])))
                           for f in self.facilities if f.get('coordinates') and f['id'] != facility_id]
        for minutes in sorted(set(thresholds), reverse=True):
            budget = minutes * metres_min - access_m
            parts = []
            if budget >= 0:
                for i, ((a, b), edge) in enumerate(zip(self.edges, self.segments)):
                    length = edge.length
                    intervals = []
                    left = budget - distances.get(a, math.inf)
                    right = budget - distances.get(b, math.inf)
                    if left > 0 and self.graph.has_edge(a, b):
                        intervals.append((0.0, min(length, left)))
                    if right > 0 and self.graph.has_edge(b, a):
                        intervals.append((max(0.0, length-right), length))
                    # 接入点可能位于长路段中间，两端都不可达时也要保留中间可达部分。
                    if i == index and budget > 0:
                        intervals.append((max(0.0, offset-budget) if reverse else offset,
                                          min(length, offset+budget) if forward else offset))
                    # 先在同一路段的一维里程上合并，避免浮点插值产生重叠线段重复计长。
                    merged: list[list[float]] = []
                    for lo, hi in sorted(intervals):
                        if merged and lo <= merged[-1][1]:
                            merged[-1][1] = max(merged[-1][1], hi)
                        elif hi > lo:
                            merged.append([lo, hi])
                    parts.extend(substring(edge, lo, hi) for lo, hi in merged)
            if not parts:
                rows.append({'minutes': minutes, 'road_length_m': 0.0, 'display_area_mu': 0.0,
                             'coverage_area_mu': 0.0, 'display_area_m2': 0.0, 'coverage_area_m2': 0.0,
                             'other_facility_count': 0,
                             'community_proxy_count': 0 if community_reference['available'] else None,
                             'community_proxy_names': []})
                rows[-1].update(intersection_statistics(Point(), boundary))
                rows[-1].update(township_intersections(Point(), townships, township_reference['available']))
                rows[-1].update(village_intersections(Point(), village_units, village_tree, village_reference['available']))
                rows[-1].update(population.summarize(Point()))
                if boundary is not None:
                    features.append({'type': 'Feature', 'properties': {'kind': 'uncovered', 'minutes': minutes},
                                     'geometry': mapping(transform(self.unproject, boundary))})
                continue
            reachable = unary_union(parts)
            # 20米道路走廊仅用于显示，不能解释为整个面内任意位置均可达。
            corridor = reachable.buffer(20, cap_style=2)
            # 分别包络走廊连通片，避免把相离的路网片区跨空白连接成一个大面。
            components = list(corridor.geoms) if corridor.geom_type == 'MultiPolygon' else [corridor]
            coverage = unary_union([corridor] + [concave_hull(part, ratio=.25, allow_holes=False) for part in components])
            # 时间越短的概略面约束在较长时间面内，避免包络变化造成时间档反向扩张。
            if larger_coverage is not None:
                coverage = coverage.intersection(larger_coverage)
            larger_coverage = coverage
            covered_ids = [fid for fid, point in facility_points if coverage.covers(point)]
            communities = summarize_communities(coverage, community_points)
            for kind, geometry in [('area', coverage), ('corridor', corridor), ('road', reachable)]:
                features.append({'type': 'Feature', 'properties': {'kind': kind, 'minutes': minutes},
                                 'geometry': mapping(transform(self.unproject, geometry))})
            rows.append({'minutes': minutes, 'road_length_m': round(reachable.length, 2),
                         'display_area_mu': round(corridor.area * .0015, 2),
                         'coverage_area_mu': round(coverage.area * .0015, 2),
                         'display_area_m2': round(corridor.area, 2), 'coverage_area_m2': round(coverage.area, 2),
                         'other_facility_count': len(covered_ids), 'other_facility_ids': covered_ids,
                         'community_proxy_count': len(communities) if community_reference['available'] else None,
                         'community_proxy_names': communities})
            rows[-1].update(intersection_statistics(coverage, boundary))
            rows[-1].update(township_intersections(coverage, townships, township_reference['available']))
            rows[-1].update(village_intersections(coverage, village_units, village_tree, village_reference['available']))
            rows[-1].update(population.summarize(coverage))
            if boundary is not None:
                remaining = boundary.difference(coverage)
                if not remaining.is_empty:
                    features.append({'type': 'Feature', 'properties': {'kind': 'uncovered', 'minutes': minutes},
                                     'geometry': mapping(transform(self.unproject, remaining))})
        features.extend(village_display_features(rows, village_units, village_tree, self.unproject))
        if boundary is not None:
            features.append({'type': 'Feature', 'properties': {'kind': 'study_area'},
                             'geometry': mapping(transform(self.unproject, boundary))})
        features.append({'type': 'Feature', 'properties': {'kind': 'facility', 'name': facility['name']},
                         'geometry': mapping(Point(facility['coordinates']))})
        if access_m > .01:
            features.append({'type': 'Feature', 'properties': {'kind': 'connector'},
                             'geometry': mapping(transform(self.unproject, LineString([origin, snapped])))})
        if not any(r['road_length_m'] > 0 for r in rows):
            raise ValueError('所选时间内无法接入可用道路，请核实设施入口或增加时间。')
        notice = ('试算：机构坐标系未核实，暂按WGS84解释；设施至道路的连接为直线假设，已计入耗时。'
                  '速度为分析假设；道路通行权限及跨层连接尚未全面核验。彩色面是20米道路走廊按连通片生成的凹包概略覆盖面（ratio=0.25），'
                  '会填补道路间空隙，未剔除水体、围墙和不可进入用地，不代表面内所有位置可达。'
                  '面内其他机构数为本次路网包内已收录设施点的几何叠加，排除起点；不代表这些机构的路网可达性、人口覆盖或服务供需。')
        if self.mode == 'driving':
            notice += '驾车简化试算：按单行字段通行；未计实时拥堵、信号灯、转向禁令、停车及上下车时间，未逐路应用限速。'
        ordered_rows = sorted(rows, key=lambda row: row['minutes'])
        if len(ordered_rows) > 1 and ordered_rows[-1]['road_length_m'] == ordered_rows[-2]['road_length_m']:
            notice += '较长时间档的可达路段未增加，可能受当前连通分量或路网缺失限制；不代表已覆盖全部周边。'
        return {'facility': facility, 'metadata': self.metadata, 'speed_kmh': speed_kmh,
                'travel_mode': self.mode,
                'community_reference': community_reference,
                'district_reference': district_reference,
                'township_reference': township_reference,
                'village_reference': village_reference,
                'population_reference': population.metadata,
                'access_distance_m': round(access_m, 2), 'direction': '从设施出发', 'notice': notice,
                'rows': sorted(rows, key=lambda r: r['minutes']),
                'geojson': {'type': 'FeatureCollection', 'features': features,
                            'analysis_type': 'elder_accessibility', 'notice': notice, 'travel_mode': self.mode,
                            'coverage_method': 'component_concave_hull', 'coverage_ratio': .25,
                            'community_reference': community_reference,
                            'district_reference': district_reference,
                            'township_reference': township_reference,
                            'village_reference': village_reference,
                            'population_reference': population.metadata,
                            'thresholds': sorted(set(thresholds))}}


@lru_cache(maxsize=2)
def _load(path: str, modified_ns: int) -> FacilityAccess:
    return FacilityAccess(json.loads(Path(path).read_text(encoding='utf-8')))


def get_facility_access(travel_mode: str = 'walking') -> FacilityAccess | RegionalFacilityAccess:
    if travel_mode not in {'walking', 'driving'}:
        raise ValueError('仅支持步行或驾车模式。')
    if (data_directory() / 'catalog.json').is_file():
        from .regional_facility_access import RegionalFacilityAccess
        return RegionalFacilityAccess(data_directory(), travel_mode)
    path = data_directory() / ('pilot-driving.json' if travel_mode == 'driving' else 'pilot.json')
    if not path.is_file():
        raise ValueError(f'{travel_mode}模式试点数据尚未配置，请先准备对应路网。')
    return _load(str(path), path.stat().st_mtime_ns)
