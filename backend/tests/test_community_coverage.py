from shapely.geometry import Point, box

from geoagent.analysis.community_coverage import reference_points, summarize_communities


def test_boundary_included_duplicates_deduplicated_and_outside_ignored():
    points = [({'community_code': code, 'community_name': name}, Point(x, y))
              for code, name, x, y in [('a', '同名社区', 1, 1), ('a', '同名社区', 0, 0),
                                      ('b', '同名社区', 2, 2), ('c', '面外社区', 5, 5)]]
    result = summarize_communities(box(0, 0, 3, 3), points)
    assert [r['code'] for r in result] == ['a', 'b']


def test_missing_reference_is_explicit_not_zero(tmp_path):
    metadata, points = reference_points(tmp_path, lambda x, y: (x, y))
    assert metadata['available'] is False and points == []
