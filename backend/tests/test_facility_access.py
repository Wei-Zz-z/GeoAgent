"""可达性测试不依赖外部路网、模型或数据库。"""
import math

import pytest
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from geoagent.analysis.facility_access import FacilityAccess
from geoagent.agents.elder_care import ElderCareAgent
from geoagent.tools.facility_access import FacilityAccessParams, analyze_elder_accessibility, search_elder_facilities


def make_data(lines: list[tuple[list[tuple[float, float]], str]], origin: tuple[float, float] = (500500, 3300000), mode: str = 'walking', oneway: str = 'B') -> FacilityAccess:
    unproject = Transformer.from_crs(32651, 4326, always_xy=True).transform
    roads = [{'geometry': {'type': 'LineString', 'coordinates': [unproject(*p) for p in points]},
              'properties': {'layer': level, 'oneway': oneway}} for points, level in lines]
    return FacilityAccess({'metadata': {'metric_crs': 'EPSG:32651', 'region': '测试区', 'road_year': '2025', 'travel_mode': mode},
        'facilities': [{'id': 'test', 'name': '测试养老院', 'address': '测试路', 'region': '测试区', 'coordinates': unproject(*origin)}],
        'roads': roads})


def test_mid_edge_and_partial_edge_not_overshot() -> None:
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')])
    result = data.analyze('test', [5, 10, 15], 1.2)
    assert [r['road_length_m'] for r in result['rows']] == pytest.approx([200, 400, 600], abs=.01)
    assert len(data.graph) == 2  # 临时接入点不污染下一次分析
    # 转回计算所用的米制坐标；经纬度中的直线边不等于投影后的直线边。
    area = [transform(data.project, shape(f['geometry'])) for f in result['geojson']['features'] if f['properties']['kind'] == 'area']
    assert area[0].buffer(.001).covers(area[1]) and area[1].buffer(.001).covers(area[2])


def test_overlapping_intervals_do_not_double_count() -> None:
    data = make_data([([(500000, 3300000), (500282, 3300000)], '0')], (500141, 3300010))
    result = data.analyze('test', [5, 10, 15], 4.2)
    assert [r['road_length_m'] for r in result['rows']] == pytest.approx([282, 282, 282], abs=.01)


def test_polygon_fills_road_loop_and_overlays_facility_points() -> None:
    from shapely.geometry import Point
    ring = [(500000, 3300000), (500200, 3300000), (500200, 3300200),
            (500000, 3300200), (500000, 3300000)]
    data = make_data([(ring, '0')], (500100, 3300000))
    for fid, xy in [('inside', (500100, 3300100)), ('outside', (502000, 3302000))]:
        data.facilities.append({'id': fid, 'coordinates': data.unproject(*xy)})
    result = data.analyze('test', [5, 10, 15], 4.2)
    row = result['rows'][-1]
    assert all(r['coverage_area_mu'] >= r['display_area_mu'] for r in result['rows'])
    assert row['other_facility_ids'] == ['inside']
    assert row['other_facility_count'] == 1
    assert row['coverage_area_mu'] > row['display_area_mu']
    feature = next(f for f in result['geojson']['features'] if f['properties'] == {'kind': 'area', 'minutes': 15})
    polygon = transform(data.project, shape(feature['geometry']))
    assert polygon.is_valid and polygon.covers(Point(500100, 3300100))
    assert result['geojson']['coverage_method'] == 'component_concave_hull'


@pytest.mark.parametrize('oneway,expected', [('F', 800), ('T', 200), ('B', 1000)])
def test_driving_respects_direction_from_mid_segment(oneway: str, expected: float) -> None:
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')], (500200, 3300000), 'driving', oneway)
    result = data.analyze('test', [5], 30)
    assert result['rows'][0]['road_length_m'] == pytest.approx(expected, abs=.01)
    assert result['geojson']['travel_mode'] == 'driving'
    assert '转向禁令' in result['notice']


def test_driving_partial_edge_is_only_forward() -> None:
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')], (500200, 3300000), 'driving', 'F')
    assert data.analyze('test', [1], 6)['rows'][0]['road_length_m'] == pytest.approx(100, abs=.01)
    with pytest.raises(ValueError):
        data.analyze('test', [5], 61)


def test_driving_unknown_oneway_rejected() -> None:
    with pytest.raises(ValueError, match='单行方向'):
        make_data([([(500000, 3300000), (501000, 3300000)], '0')], mode='driving', oneway='?')


def test_speed_changes_network_result() -> None:
    data = make_data([([(500000, 3300000), (502000, 3300000)], '0')])
    slow = data.analyze('test', [5], 1.2)['rows'][0]['road_length_m']
    fast = data.analyze('test', [5], 2.4)['rows'][0]['road_length_m']
    assert fast == pytest.approx(2 * slow, abs=.01)


def test_connector_cost_and_distant_facility() -> None:
    lines = [([(500000, 3300000), (501000, 3300000)], '0')]
    data = make_data(lines, (500500, 3300050))
    result = data.analyze('test', [5], 1.2)
    assert result['access_distance_m'] == pytest.approx(50, abs=.01)
    assert result['rows'][0]['road_length_m'] == pytest.approx(100, abs=.01)
    with pytest.raises(ValueError, match='超过100米'):
        make_data(lines, (500500, 3300200)).analyze('test', [5], 4.2)


def test_no_cross_layer_or_unconnected_crossing_shortcut() -> None:
    lines = [([(500000, 3300000), (500500, 3300000), (501000, 3300000)], '0'),
             ([(500500, 3299500), (500500, 3300000), (500500, 3300500)], '1')]
    data = make_data(lines, (500100, 3300000))
    assert data.analyze('test', [30], 6)['rows'][0]['road_length_m'] == pytest.approx(1000, abs=.01)


def test_shared_vertex_connects_and_disconnected_road_does_not() -> None:
    data = make_data([([(500000, 3300000), (500500, 3300000)], '0'),
                      ([(500500, 3300000), (500500, 3300500)], '0'),
                      ([(500600, 3300000), (501100, 3300000)], '0')], (500100, 3300000))
    assert data.analyze('test', [30], 6)['rows'][0]['road_length_m'] == pytest.approx(1000, abs=.01)


@pytest.mark.parametrize('thresholds,speed', [([], 4.2), ([0], 4.2), ([31], 4.2), ([math.nan], 4.2), ([5], 0), ([5], math.inf)])
def test_invalid_parameters(thresholds: list[float], speed: float) -> None:
    with pytest.raises(ValueError):
        FacilityAccessParams(facility_id='test', thresholds=thresholds, walking_speed_kmh=speed)


def test_unknown_facility_and_no_candidates() -> None:
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')])
    assert data.search('北京') == []
    with pytest.raises(ValueError, match='编号不存在'):
        data.analyze('guessed', [5], 4.2)


def test_facility_directory_only_exposes_display_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from geoagent.server.routes import list_elder_facilities
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')])
    monkeypatch.setattr('geoagent.analysis.facility_access.get_facility_access', lambda: data)
    result = list_elder_facilities()
    assert result['region'] == '测试区'
    assert len(result['facilities']) == 1
    assert set(result['facilities'][0]) == {'id', 'name', 'region', 'address', 'district_id', 'available', 'status'}
    assert result['districts'] == [{'id': '测试区', 'name': '测试区'}]


@pytest.mark.asyncio
async def test_registered_tool_and_artifact_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')])
    monkeypatch.setattr('geoagent.tools.facility_access.get_facility_access', lambda: data)
    search = await search_elder_facilities('测试养老院')
    assert 'test' in search.content
    result = await analyze_elder_accessibility('test')
    assert [a.kind for a in result.artifacts] == ['geojson', 'table']
    assert result.artifacts[1].data['columns'][1] == '单次步行距离预算（米）'
    assert [r[1] for r in result.artifacts[1].data['rows']] == [350, 700, 1050, 1400, 1750, 2100]
    assert len(result.artifacts[0].data['statistics']) == 6
    assert [row['walking_budget_m'] for row in result.artifacts[0].data['statistics']] == [350, 700, 1050, 1400, 1750, 2100]
    assert result.artifacts[0].data['thresholds'] == [5, 10, 15, 20, 25, 30]
    assert '坐标系未核实' in result.content
    names = {t.name for t in ElderCareAgent().tools}
    assert 'search_elder_facilities' in names and 'analyze_elder_accessibility' in names
    assert 'e2sca_analysis' not in names
