"""从本地JSON机构记录、行政区GeoJSON及道路SHP准备养老可达性试点。

机构JSON字段：source_row/name/code/address/coordinates_raw；不修改源文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform

WALK_CLASSES = {'residential', 'living_street', 'service', 'unclassified', 'tertiary',
                'tertiary_link', 'secondary', 'secondary_link', 'primary', 'primary_link',
                'footway', 'pedestrian', 'path', 'steps', 'track', 'track_grade1',
                'track_grade2', 'track_grade3', 'track_grade4', 'track_grade5'}
DRIVE_CLASSES = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link',
                 'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified',
                 'residential', 'living_street', 'service'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--facilities', type=Path, required=True)
    parser.add_argument('--boundary', type=Path, required=True)
    parser.add_argument('--roads', type=Path, required=True)
    parser.add_argument('--county-code', required=True)
    parser.add_argument('--road-year', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--travel-mode', choices=['walking', 'driving'], default='walking')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('输出目录已存在，请指定新目录，避免覆盖已准备数据。')
    boundaries = json.loads(args.boundary.read_text(encoding='utf-8-sig'))
    selected = [b for b in boundaries['features'] if str(b['properties'].get('gb', '')).endswith(args.county_code)]
    if len(selected) != 1:
        raise SystemExit('区划代码没有唯一匹配，请核对边界文件。')
    feature = selected[0]
    # 天地图边界明确标注CGCS2000；设施坐标没有来源说明，保留原值并标注假设。
    boundary_crs = boundaries.get('crs', {}).get('properties', {}).get('name')
    if not boundary_crs:
        raise SystemExit('行政区数据未注明坐标系。')
    boundary = transform(Transformer.from_crs(boundary_crs, 4326, always_xy=True).transform,
                         make_valid(shape(feature['geometry'])))
    metric_crs = 'EPSG:32651'
    project = Transformer.from_crs(4326, metric_crs, always_xy=True).transform
    unproject = Transformer.from_crs(metric_crs, 4326, always_xy=True).transform
    buffer_m = 31000 if args.travel_mode == 'driving' else 4000
    buffer = transform(project, boundary).buffer(buffer_m)
    bbox = transform(unproject, buffer).bounds
    records = json.loads(args.facilities.read_text(encoding='utf-8'))
    accepted, excluded = [], []
    seen = set()
    for record in records:
        try:
            lon, lat = [float(v) for v in str(record.get('coordinates_raw')).replace('，', ',').split(',')]
            if not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lon == 0 and lat == 0):
                raise ValueError()
        except (ValueError, TypeError):
            excluded.append({'source_row': record['source_row'], 'reason': '缺失或无效坐标'})
            continue
        if not boundary.covers(Point(lon, lat)):
            continue
        key = (record['name'], lon, lat)
        if key in seen:
            excluded.append({'source_row': record['source_row'], 'reason': '同名同坐标重复'})
            continue
        seen.add(key)
        digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:12]
        accepted.append({'id': f'elder-{digest}', 'name': record['name'], 'address': record.get('address') or '',
                         'coordinates': [lon, lat], 'region': feature['properties']['name'],
                         'source_row': record['source_row'], 'coordinate_status': 'unknown_assumed_wgs84'})
    roads = gpd.read_file(args.roads, bbox=bbox)
    if roads.crs is None or roads.crs.to_epsg() != 4326:
        raise SystemExit('此准备流程要求WGS84道路数据，请先核实并转换源道路坐标系。')
    class_counts = Counter(roads['fclass'])
    roads = roads[roads['fclass'].isin(DRIVE_CLASSES if args.travel_mode == 'driving' else WALK_CLASSES)].copy()
    if args.travel_mode == 'driving' and not roads['oneway'].isin(['B', 'F', 'T']).all():
        raise SystemExit('驾车道路包含未知单行字段，需核实后再准备。')
    road_features = []
    for record in roads.to_dict('records'):
        geom = record['geometry']
        if geom is None or geom.is_empty or geom.geom_type not in ('LineString', 'MultiLineString'):
            continue
        # 驾车保留原始线方向，避免裁切重组线段影响单行方向。
        metric = transform(project, geom)
        if args.travel_mode == 'walking':
            metric = metric.intersection(buffer)
        if metric.is_empty or metric.geom_type not in ('LineString', 'MultiLineString'):
            continue
        road_features.append({'type': 'Feature', 'geometry': mapping(transform(unproject, metric)),
                              'properties': {'layer': str(record.get('layer') or '0'), 'fclass': record['fclass'],
                                             'oneway': record.get('oneway')}})
    metadata = {'region': feature['properties']['name'], 'county_code': args.county_code,
                'metric_crs': metric_crs, 'road_year': args.road_year, 'road_source': '用户提供的OSM道路整理数据',
                'facility_source': '用户提供的机构养老服务信息表', 'facility_crs': 'unknown_assumed_wgs84',
                'boundary_crs': boundary_crs, 'boundary_buffer_m': buffer_m, 'travel_mode': args.travel_mode,
                'walking_policy': '按道路类型筛选、双向步行；同层共享顶点连接；不依据车辆单行限制步行。通行权限和跨层接续仍待核实。',
                'road_features': len(road_features), 'facility_count': len(accepted), 'is_verified': False}
    if args.travel_mode == 'driving':
        metadata.pop('walking_policy')
        metadata['driving_policy'] = '机动车道路筛选；B双向、F顺原线方向、T逆原线方向；同层共享顶点连接。未核验转向禁令、通行权限及跨层连接，未计拥堵、信号灯及逐路限速。'
    args.output.mkdir(parents=True)
    filename = 'pilot-driving.json' if args.travel_mode == 'driving' else 'pilot.json'
    (args.output / filename).write_text(json.dumps({'metadata': metadata, 'facilities': accepted,
        'boundary': mapping(boundary), 'roads': road_features}, ensure_ascii=False), encoding='utf-8')
    (args.output / 'quality.json').write_text(json.dumps({'metadata': metadata, 'excluded': excluded,
        'source_road_class_counts': class_counts}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == '__main__':
    main()
