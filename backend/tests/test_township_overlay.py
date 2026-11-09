from shapely.geometry import box
from geoagent.analysis.township_overlay import township_boundaries, township_intersections
from geoagent.analysis.district_overlay import intersection_statistics


def test_positive_area_only_and_same_name_different_counties():
    boundaries = [('原甲区', '同名街道', box(0, 0, 10, 10)),
                  ('原乙区', '同名街道', box(10, 0, 20, 10)),
                  ('原甲区', '接边街道', box(0, 10, 20, 20))]
    result = township_intersections(box(5, 0, 15, 10), boundaries, True)
    assert result['historical_township_count'] == 2
    assert [r['intersection_m2'] for r in result['historical_townships']] == [50, 50]
    assert all(r['intersection_percent'] == 50 for r in result['historical_townships'])


def test_missing_boundary_is_not_reported_as_zero(tmp_path):
    meta, boundaries = township_boundaries(tmp_path, 'EPSG:32651')
    assert not meta['available']
    assert township_intersections(box(0, 0, 1, 1), boundaries, False)['historical_township_count'] is None


def test_model_erase_partitions_study_area_including_cross_border_coverage():
    result = intersection_statistics(box(5, 5, 15, 15), box(0, 0, 10, 10))
    assert result['model_uncovered_m2'] == 75
    assert result['district_intersection_m2'] + result['model_uncovered_m2'] == 100


def test_uncovered_only_uses_positive_intersections():
    from geoagent.analysis.township_overlay import township_uncovered
    boundaries = [('甲区', '相交街道', box(0, 0, 10, 10)),
                  ('甲区', '接边街道', box(10, 0, 20, 10)),
                  ('甲区', '远处街道', box(30, 0, 40, 10))]
    remaining, stats = township_uncovered(box(5, 0, 10, 10), boundaries, True)
    assert remaining.equals(box(0, 0, 5, 10))
    assert stats['model_uncovered_m2'] == 50
    assert stats['uncovered_township_count'] == 1
    empty, missing = township_uncovered(box(5, 0, 10, 10), [], False)
    assert empty.is_empty
    assert missing['model_uncovered_m2'] is None