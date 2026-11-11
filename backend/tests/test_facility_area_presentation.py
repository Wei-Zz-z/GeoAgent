from geoagent.tools.facility_access import area_summary_mu


def test_area_conversion_is_recursive_and_does_not_change_counts_or_source():
    source = {'rows': [{'coverage_area_m2': 10000, 'coverage_area_mu': 999,
                        'road_length_m': 1000, 'other_facility_count': 3,
                        'villages': [{'intersection_m2': 100}]}],
              'model_uncovered_m2': None}
    result = area_summary_mu(source)
    assert result['rows'][0]['coverage_area_mu'] == 15
    assert result['rows'][0]['road_length_m'] == 1000
    assert result['rows'][0]['other_facility_count'] == 3
    assert result['rows'][0]['villages'][0]['intersection_mu'] == .15
    assert result['model_uncovered_mu'] is None
    assert source['rows'][0]['coverage_area_m2'] == 10000
