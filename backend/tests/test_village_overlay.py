import gzip
import json

from shapely.geometry import box, mapping

from geoagent.analysis.village_overlay import boundary_kind, village_boundaries, village_intersections, village_display_features


def test_mixed_granularity_does_not_become_community():
    assert boundary_kind('小河街道', '330105003000') == '街道乡镇级或汇总单元'
    assert boundary_kind('半山村社区', '330105103203') == '名称含社区'
    assert boundary_kind('总管堂村', '330105104211') == '名称为村'
    assert boundary_kind('田园集团', '330105103003') == '其他或粒度待核实'


def test_intersection_excludes_boundary_touch_and_preserves_names(tmp_path):
    features = []
    for code, name, kind, geom in [('1', '同名社区', '名称含社区', box(0, 0, 10, 10)),
                                    ('2', '同名社区', '名称含社区', box(10, 0, 20, 10)),
                                    ('3', '接边村', '名称为村', box(0, 10, 20, 20))]:
        features.append(dict(geometry=mapping(geom), properties=dict(code=code, name=name, kind=kind)))
    with gzip.open(tmp_path / 'historical_villages.geojson.gz', 'wt', encoding='utf-8') as stream:
        json.dump(dict(features=features, source_crs='EPSG:32651'), stream)
    meta, units, tree = village_boundaries(tmp_path, 'EPSG:32651')
    result = village_intersections(box(5, 0, 15, 10), units, tree, meta['available'])
    assert result['historical_village_unit_count'] == 2
    assert result['historical_community_named_count'] == 2
    assert [r['intersection_m2'] for r in result['historical_village_units']] == [50, 50]
    assert all(r['intersection_percent'] == 50 for r in result['historical_village_units'])
    features = village_display_features([result, result], units, tree, lambda x, y: (x, y))
    assert len(features) == 2
    assert {f['properties']['code'] for f in features} == {'1', '2'}
    assert all(f['properties']['kind'] == 'village_boundary' for f in features)


def test_missing_is_not_zero(tmp_path):
    meta, units, tree = village_boundaries(tmp_path, 'EPSG:32651')
    result = village_intersections(box(0, 0, 1, 1), units, tree, meta['available'])
    assert result['historical_village_unit_count'] is None
    assert result['historical_community_named_count'] is None
