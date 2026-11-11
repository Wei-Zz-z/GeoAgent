"""验证区县路网分发和缺失数据拒绝，不依赖真实资料。"""
import json

import pytest

from geoagent.analysis.regional_facility_access import RegionalFacilityAccess


@pytest.fixture
def catalog(tmp_path):
    data = {'metadata': {'region': '杭州市'}, 'districts': [{'id': '330122', 'name': '桐庐县'}, {'id': '330127', 'name': '淳安县'}],
            'facilities': [{'id': 'ready', 'name': '测试机构', 'region': '桐庐县', 'district_id': '330122', 'address': '测试路', 'available': True},
                           {'id': 'missing', 'name': '缺失机构', 'region': '淳安县', 'district_id': '330127', 'available': False, 'status': '坐标缺失'}]}
    (tmp_path / 'catalog.json').write_text(json.dumps(data), encoding='utf-8')
    return tmp_path


def test_search_and_missing_data(catalog):
    data = RegionalFacilityAccess(catalog, 'walking')
    assert [f['id'] for f in data.search('桐庐县')] == ['ready']
    with pytest.raises(ValueError, match='坐标缺失'):
        data.analyze('missing', [5], 4.2)
    with pytest.raises(ValueError, match='尚未准备'):
        data.analyze('ready', [5], 4.2)
    with pytest.raises(ValueError, match='编号不存在'):
        data.analyze('unknown', [5], 4.2)


def test_catalog_api_keeps_empty_district_and_unavailable_facility(catalog, monkeypatch):
    from geoagent.server.routes import list_elder_facilities
    monkeypatch.setenv('GEOAGENT_ELDER_DATA_DIR', str(catalog))
    result = list_elder_facilities()
    assert len(result['districts']) == 2
    assert result['region'] == '杭州市'
    unavailable = next(f for f in result['facilities'] if f['id'] == 'missing')
    assert not unavailable['available']
    assert unavailable['status'] == '坐标缺失'
    assert unavailable['district_id'] == '330127'


@pytest.mark.parametrize('mode,filename,speed', [('walking', 'pilot.json', 4.2), ('driving', 'pilot-driving.json', 30)])
def test_dispatches_to_selected_county_and_mode(catalog, monkeypatch, mode, filename, speed):
    path = catalog / '330122' / mode / filename
    path.parent.mkdir(parents=True)
    path.write_text('{}', encoding='utf-8')
    class Analysis:
        def analyze(self, facility_id, thresholds, speed_kmh):
            return (facility_id, thresholds, speed_kmh)
    def load(selected, modified):
        assert selected == str(path.resolve())
        assert modified == path.stat().st_mtime_ns
        return Analysis()
    monkeypatch.setattr('geoagent.analysis.regional_facility_access._load', load)
    assert RegionalFacilityAccess(catalog, mode).analyze('ready', [5], speed) == ('ready', [5], speed)
