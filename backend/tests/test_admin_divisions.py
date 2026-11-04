"""地址关联不修改设施空间位置，歧义及缺失保留为未匹配。"""
from geoagent.analysis.admin_divisions import match_addresses


def test_unique_full_address_match_and_parent_constraint() -> None:
    data = {'streets': [
        {'areaCode': 'a', 'code': 's', 'name': '测试街道'},
        {'areaCode': 'b', 'code': 'other', 'name': '测试街道'},
    ], 'villages': [{'streetCode': 's', 'code': 'v', 'name': '新安社区'}]}
    facility = {'id': '1', 'district_id': 'a', 'address': '测试街道新安社区1号'}
    result = match_addresses([facility], data)['1']
    assert result['street_code'] == 's' and result['village_code'] == 'v'
    assert '待核实' in result['status']
    assert facility == {'id': '1', 'district_id': 'a', 'address': '测试街道新安社区1号'}


def test_ambiguous_missing_and_institution_name_do_not_guess() -> None:
    data = {'streets': [
        {'areaCode': 'a', 'code': 's', 'name': '测试街道'},
        {'areaCode': 'a', 'code': 't', 'name': '另一街道'},
    ], 'villages': []}
    for address in ['', '测试街道另一街道交界处', '测试路1号']:
        result = match_addresses([{'id': '1', 'district_id': 'a', 'name': '测试街道养老院', 'address': address}], data)
        assert result['1']['street_code'] == ''
