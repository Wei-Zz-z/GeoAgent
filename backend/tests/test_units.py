"""面积单位换算与期次白名单测试（纯函数 + 配置，不依赖数据库）。"""

from __future__ import annotations

from geoagent.config import DEFAULT_PG_WHITELIST, Settings
from geoagent.tools.labels import humanize_table
from geoagent.tools.units import (
    MU_PER_M2,
    area_header,
    convert_mu_headers,
    is_area_column,
    m2_to_mu,
    normalize_unit,
)

TABLE_1 = 'data."2026_1_change_landuse"'
TABLE_2 = 'data."2026_2_change_landuse"'


def test_m2_to_mu_uses_0_0015_factor():
    assert MU_PER_M2 == 0.0015
    assert m2_to_mu(1000) == 1.5
    assert m2_to_mu(666.6667) == 1.0
    assert m2_to_mu(0) == 0.0
    assert m2_to_mu(None) is None
    assert m2_to_mu("bad") == "bad"


def test_is_area_column_matches_area_names_only():
    assert is_area_column("MJ")
    assert is_area_column("area")
    assert is_area_column("outflow_m2")
    assert is_area_column("farmland_net_m2")
    assert not is_area_column("n")
    assert not is_area_column("XMC")
    # 已是亩的列不再二次换算
    assert not is_area_column("area_mu")


def test_area_header_keeps_chinese_names():
    assert area_header("MJ") == "面积(亩)"
    assert area_header("total_area_m2") == "总面积(亩)"
    assert area_header("farmland_inflow_m2") == "耕地流入面积(亩)"
    assert area_header("custom_m2") == "custom(亩)"


def test_humanize_table_converts_area_to_mu():
    columns = ["XMC", "MJ", "n"]
    rows = [{"XMC": "庆元县", "MJ": 666.6667, "n": 3}]
    labels, out_rows = humanize_table(columns, rows)
    assert labels == ["县级行政区名称", "面积(亩)", "n"]
    assert out_rows[0]["面积(亩)"] == 1.0
    assert out_rows[0]["n"] == 3


def test_humanize_table_keeps_duplicate_area_columns_distinct():
    labels, _ = humanize_table(["area", "area_m2"], [{"area": 1, "area_m2": 2}])
    assert labels[0] == "面积(亩)"
    assert labels[1] != labels[0]


def test_convert_mu_headers_renames_stat_tool_tables():
    columns = ["图斑类型", "图斑数(个)", "面积(平方米)"]
    rows = [{"图斑类型": "动土", "图斑数(个)": 5, "面积(平方米)": 1000.0}]
    new_columns, new_rows = convert_mu_headers(columns, rows)
    assert new_columns == ["图斑类型", "图斑数(个)", "面积(亩)"]
    assert new_rows[0]["面积(亩)"] == 1.5
    assert new_rows[0]["图斑数(个)"] == 5


def test_normalize_unit_maps_square_meters_to_mu():
    assert normalize_unit("平方米") == "亩"
    assert normalize_unit("㎡") == "亩"
    assert normalize_unit("亩") == "亩"
    assert normalize_unit("") == ""


def test_default_whitelist_contains_both_period_tables():
    assert TABLE_1 in DEFAULT_PG_WHITELIST
    assert TABLE_2 in DEFAULT_PG_WHITELIST


def test_settings_merges_registered_period_tables(monkeypatch):
    """显式配置白名单时，也要能查询 datasets.json 里登记的期次表。"""
    monkeypatch.setenv("GEOAGENT_PG_WHITELIST", TABLE_1 + ",knowledge_base.dict_tblx")
    monkeypatch.delenv("GEOAGENT_SKILLS_DIR", raising=False)
    settings = Settings()
    assert TABLE_1 in settings.pg_whitelist
    assert TABLE_2 in settings.pg_whitelist
    assert "knowledge_base.dict_tblx" in settings.pg_whitelist
    assert len(settings.pg_whitelist) == len(set(settings.pg_whitelist))


def test_datasets_asset_registers_both_periods():
    from geoagent.datasets import default_registry, find_period

    registry = default_registry()
    ids = [period.id for period in registry.periods]
    assert ids == ["2026_1", "2026_2"]
    assert registry.default is not None and registry.default.id == "2026_1"

    second = find_period(registry, "2026年第二期耕地有多少")
    assert second.dataset is not None and second.dataset.id == "2026_2"
    assert second.dataset.table == TABLE_2

    first = find_period(registry, "帮我看下第一期耕地流出")
    assert first.dataset is not None and first.dataset.id == "2026_1"

    default = find_period(registry, "耕地流出是多少")
    assert default.dataset is not None and default.dataset.id == "2026_1"
