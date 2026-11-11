from shapely.geometry import box
from geoagent.analysis.district_overlay import district_boundary, intersection_statistics


def test_intersection_clips_cross_border_area_and_uses_full_district_denominator():
    result = intersection_statistics(box(5, 5, 15, 15), box(0, 0, 10, 10))
    assert result['district_intersection_mu'] == round(25 * .0015, 2)
    assert result['district_coverage_percent'] == 25
    assert intersection_statistics(box(20, 20, 21, 21), box(0, 0, 10, 10))['district_coverage_percent'] == 0


def test_missing_boundary_does_not_fabricate_zero(tmp_path):
    meta, boundary = district_boundary(tmp_path, {'region': '某区'}, 'EPSG:32651')
    assert not meta['available'] and boundary is None
    assert intersection_statistics(box(0, 0, 1, 1), boundary)['district_intersection_mu'] is None
