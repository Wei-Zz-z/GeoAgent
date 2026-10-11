
"""期次数据集注册表测试（纯函数，不依赖数据库）。"""

from __future__ import annotations

from types import SimpleNamespace

from geoagent.datasets import (
    DatasetPeriod,
    DatasetRegistry,
    bind_entry_sql,
    bind_table,
    context_table,
    default_registry,
    find_period,
    load_registry,
)

TABLE = 'data."2026_1_change_landuse"'


def test_default_registry_reads_repo_asset():
    registry = default_registry()
    period = registry.default
    assert period is not None
    assert period.id == "2026_1"
    assert period.table == TABLE
    assert TABLE in registry.tables


def test_missing_asset_falls_back_to_empty_registry(tmp_path):
    registry = load_registry(tmp_path / "nope.json")
    assert registry.periods == ()
    assert registry.default is None


def test_find_period_prefers_alias_then_reports_unknown():
    registry = DatasetRegistry(
        periods=(
            DatasetPeriod(
                id="2026_1",
                label="2026年第一期",
                table=TABLE,
                aliases=("第一期", "2026_1"),
                year=2026,
                round=1,
            ),
        ),
        default_id="2026_1",
    )
    hit = find_period(registry, "帮我查2026年第一期耕地流出")
    assert hit.dataset is not None and hit.dataset.id == "2026_1"
    assert hit.mentioned is False

    unknown = find_period(registry, "第二期耕地流出是多少")
    assert unknown.dataset is None
    assert unknown.mentioned is True
    assert "第二期" in unknown.raw

    plain = find_period(registry, "耕地流出是多少")
    assert plain.dataset is not None and plain.dataset.id == "2026_1"
    assert plain.mentioned is False


def test_bind_table_handles_placeholder_and_legacy_name():
    target = 'data."2026_2_change_landuse"'
    assert (
        bind_table("SELECT 1 FROM {table}", target)
        == f"SELECT 1 FROM {target}"
    )
    assert (
        bind_table(f"SELECT 1 FROM {TABLE} t JOIN knowledge_base.dict_tblx d ON true", target, [TABLE])
        == f"SELECT 1 FROM {target} t JOIN knowledge_base.dict_tblx d ON true"
    )
    # 未登记的其它表不能被误替换
    other = 'SELECT 1 FROM data."other_table"'
    assert bind_table(other, target, [TABLE]) == other


def test_bind_entry_sql_uses_registry_default():
    registry = DatasetRegistry(
        periods=(
            DatasetPeriod(id="2026_1", label="2026年第一期", table=TABLE),
            DatasetPeriod(id="2026_9", label="2026年第九期", table='data."2026_9_change_landuse"'),
        ),
        default_id="2026_9",
    )
    assert bind_entry_sql("SELECT 1 FROM {table}", registry) == 'SELECT 1 FROM data."2026_9_change_landuse"'
    # 历史遗留的具体表名只要已登记期次，就会被改绑到当期表
    assert bind_entry_sql("SELECT 1 FROM " + TABLE, registry) == 'SELECT 1 FROM data."2026_9_change_landuse"'
    # 未登记的表名保持原样，避免误伤别的业务表
    assert bind_entry_sql('SELECT 1 FROM data."other_table"', registry) == 'SELECT 1 FROM data."other_table"'


def test_context_table_prefers_current_period():
    ctx = SimpleNamespace(
        dataset=DatasetPeriod(id="2026_2", label="2026年第二期", table='data."2026_2_change_landuse"'),
        datasets=DatasetRegistry(periods=(DatasetPeriod(id="2026_1", label="旧", table=TABLE),), default_id="2026_1"),
    )
    assert context_table(ctx) == 'data."2026_2_change_landuse"'
    bare = SimpleNamespace()
    assert context_table(bare) == TABLE
