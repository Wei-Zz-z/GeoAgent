"""用明确的米制几何核对指标，不依赖正式居住用地、模型或数据库。"""
import json
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon, box, mapping

from geoagent.analysis.residential_coverage import (
    ResidentialCoverage, calculate_residential_coverage, monitoring_indicators,
)
from geoagent.tools.facility_access import analyze_elder_accessibility
from test_facility_access import make_data


def write_land(directory: Path, geometries: list, *, crs: bool = True) -> None:
    content = {'type': 'FeatureCollection', 'source': '测试居住用地',
               'features': [{'type': 'Feature', 'properties': {}, 'geometry': mapping(g)} for g in geometries]}
    if crs:
        content['crs'] = {'type': 'name', 'properties': {'name': 'EPSG:32651'}}
    (directory / 'residential_land.geojson').write_text(json.dumps(content), encoding='utf-8')


def test_union_both_land_and_multiple_facilities_with_same_boundary() -> None:
    result = calculate_residential_coverage(
        [box(-100, 0, 30, 100), box(20, 0, 50, 100)],
        [box(-20, -20, 60, 120), box(40, -20, 120, 120)], box(0, 0, 100, 100),
    )
    # 两组都有重叠及界外部分，分母10000、分子5000，不重复计数。
    assert result == {'residential_covered_area_m2': 5000.0,
                      'residential_total_area_m2': 10000.0, 'residential_coverage_percent': 50.0}


def test_disjoint_coverage_is_legitimate_zero_with_known_denominator() -> None:
    result = calculate_residential_coverage([box(200, 200, 300, 300)],
                                            [box(0, 0, 100, 100)], box(0, 0, 100, 100))
    assert result['residential_coverage_percent'] == 0
    assert result['residential_total_area_m2'] == 10000


@pytest.mark.parametrize('coverages,expected', [
    ([Point()], 0.0), ([Polygon()], 0.0),
    ([Point(), box(0, 0, 50, 100), Polygon()], 50.0),
])
def test_explicit_empty_coverage_preserves_valid_denominator(coverages, expected) -> None:
    result = calculate_residential_coverage(coverages, [box(0, 0, 100, 100)], box(0, 0, 100, 100))
    assert result['residential_total_area_m2'] == 10000.0
    assert result['residential_coverage_percent'] == expected
    assert result['residential_covered_area_m2'] == expected * 100


@pytest.mark.parametrize('coverages,land,boundary', [
    ([], [box(0, 0, 10, 10)], box(0, 0, 10, 10)),
    ([box(0, 0, 10, 10)], [], box(0, 0, 10, 10)),
    ([box(0, 0, 10, 10)], [box(20, 20, 30, 30)], box(0, 0, 10, 10)),
    ([box(0, 0, 10, 10)], [box(0, 0, 10, 10)], None),
])
def test_unknown_or_zero_denominator_is_null(coverages, land, boundary) -> None:
    assert all(v is None for v in calculate_residential_coverage(coverages, land, boundary).values())


def test_optional_missing_land_does_not_return_zero(tmp_path: Path) -> None:
    overlay = ResidentialCoverage(tmp_path, box(0, 0, 10, 10), 'EPSG:32651')
    assert overlay.metadata['available'] is False
    assert overlay.metadata['status'] == 'missing_data'
    assert all(value is None for value in overlay.summarize(box(0, 0, 10, 10)).values())


def test_no_crs_is_rejected_instead_of_guessing(tmp_path: Path) -> None:
    write_land(tmp_path, [box(0, 0, 100, 100)], crs=False)
    overlay = ResidentialCoverage(tmp_path, box(0, 0, 100, 100), 'EPSG:32651')
    assert overlay.metadata['status'] == 'invalid_data'
    assert overlay.metadata['available'] is False


def test_invalid_crs_and_non_metre_target_are_unavailable(tmp_path: Path) -> None:
    write_land(tmp_path, [box(0, 0, 100, 100)])
    assert ResidentialCoverage(tmp_path, box(0, 0, 100, 100), 'EPSG:4326').metadata['status'] == 'invalid_data'
    content = json.loads((tmp_path / 'residential_land.geojson').read_text(encoding='utf-8'))
    content['crs']['properties']['name'] = 'UNRECOGNIZED:CRS'
    (tmp_path / 'residential_land.geojson').write_text(json.dumps(content), encoding='utf-8')
    assert ResidentialCoverage(tmp_path, box(0, 0, 100, 100), 'EPSG:32651').metadata['status'] == 'invalid_data'


def test_land_clipped_before_measuring_and_cache_refreshes(tmp_path: Path) -> None:
    write_land(tmp_path, [box(-10, -10, 110, 110)])
    overlay = ResidentialCoverage(tmp_path, box(0, 0, 100, 100), 'EPSG:32651')
    assert overlay.metadata['total_area_m2'] == 10000
    assert overlay.summarize(box(0, 0, 50, 100))['residential_coverage_percent'] == 50
    write_land(tmp_path, [box(0, 0, 25, 100)])
    refreshed = ResidentialCoverage(tmp_path, box(0, 0, 100, 100), 'EPSG:32651')
    assert refreshed.metadata['total_area_m2'] == 2500
    assert refreshed.summarize(box(0, 0, 50, 100))['residential_coverage_percent'] == 100


def test_zero_land_in_study_area_remains_unknown(tmp_path: Path) -> None:
    write_land(tmp_path, [box(100, 100, 200, 200)])
    overlay = ResidentialCoverage(tmp_path, box(0, 0, 10, 10), 'EPSG:32651')
    assert overlay.metadata['status'] == 'zero_denominator'
    assert overlay.metadata['available'] is False
    assert overlay.summarize(box(0, 0, 10, 10))['residential_coverage_percent'] is None


@pytest.mark.parametrize('mode,minutes,value,status', [
    ('walking', 15, 45.0, 'trial'), ('driving', 15, None, 'not_applicable'),
    ('walking', 10, None, 'not_applicable'),
])
def test_region_indicator_never_inherits_single_facility_or_other_mode(mode, minutes, value, status) -> None:
    rows = [{'minutes': minutes, 'residential_coverage_percent': 45.0}]
    result = monitoring_indicators(rows, mode, {'available': True})
    formal, single = result[:2]
    assert formal['value'] is None and formal['status'] == 'scope_incomplete'
    assert formal['scope'] == 'study_area_all_community_facilities'
    assert single['value'] == value and single['status'] == status
    assert len(result) == 2


def prepare_facility_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_land(tmp_path, [box(500000, 3299990, 501000, 3300010)])
    boundary = {'type': 'FeatureCollection', 'source': '测试边界',
                'crs': {'type': 'name', 'properties': {'name': 'EPSG:32651'}},
                'features': [{'type': 'Feature', 'properties': {'name': '测试区'},
                              'geometry': mapping(box(499000, 3299000, 502000, 3301000))}]}
    (tmp_path / 'district_boundaries.geojson').write_text(json.dumps(boundary), encoding='utf-8')
    monkeypatch.setattr('geoagent.analysis.facility_access.data_directory', lambda: tmp_path)
    data = make_data([([(500000, 3300000), (501000, 3300000)], '0')])
    return data


def test_analysis_preserves_numerator_denominator_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = prepare_facility_result(tmp_path, monkeypatch)
    result = data.analyze('test', [15], 4.2)
    assert result['rows'][0]['residential_total_area_m2'] == 20000
    assert result['rows'][0]['residential_coverage_percent'] == pytest.approx(100, abs=.01)
    assert result['residential_reference'] == result['geojson']['residential_reference']
    assert result['monitoring_indicators'] == result['geojson']['monitoring_indicators']
    assert result['monitoring_indicators'][0]['value'] is None


def test_short_time_cannot_reach_road_but_has_known_residential_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = prepare_facility_result(tmp_path, monkeypatch)
    data.facilities[0]['coordinates'] = data.unproject(500500, 3300050)
    result = data.analyze('test', [1, 15], 1.0)
    short, long = result['rows']
    assert result['access_distance_m'] == pytest.approx(50, abs=.01)
    assert short['road_length_m'] == 0
    assert short['residential_total_area_m2'] == 20000.0
    assert short['residential_covered_area_m2'] == 0.0
    assert short['residential_coverage_percent'] == 0.0
    assert long['road_length_m'] > 0
    assert long['residential_covered_area_m2'] > 0


@pytest.mark.asyncio
async def test_tool_model_map_and_table_share_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = prepare_facility_result(tmp_path, monkeypatch)
    monkeypatch.setattr('geoagent.tools.facility_access.get_facility_access', lambda: data)
    result = await analyze_elder_accessibility('test', thresholds=[15])
    summary = json.loads(result.content)
    geojson = result.artifacts[0].data
    assert summary['monitoring_indicators'] == geojson['monitoring_indicators']
    assert summary['rows'][0]['residential_total_area_mu'] == 30
    assert geojson['statistics'][0]['residential_total_area_m2'] == 20000
    table = next(a.data for a in result.artifacts if a.name == '养老相关监测指标与数据状态')
    assert table['rows'][0][1] is None
    assert table['rows'][1][1] == summary['monitoring_indicators'][1]['value']
