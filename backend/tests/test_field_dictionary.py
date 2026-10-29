"""字段字典、期次版本与 TBLX 映射资产回归。"""

from __future__ import annotations

from geoagent.datasets import default_registry
from geoagent.tools.field_dictionaries import label_for, load_dictionaries, prompt_summary
from geoagent.tools.labels import humanize_table


def test_field_dictionaries_cover_tz_qsx_czcsxm():
    dictionaries = load_dictionaries()
    assert dictionaries["version"]
    assert label_for("TZ", "ZZ") == "住宅"
    assert label_for("TZ", "GY") == "工业"
    assert label_for("QSX", "20250906") == "2025-09-06"
    assert label_for("CZCSXM", "203") == "村庄"
    # 未确认的数字编码不能编造含义
    assert label_for("TZ", "99") is None
    assert "TZ=图斑特征" in prompt_summary()


def test_humanize_table_maps_new_field_dictionaries():
    labels, rows = humanize_table(
        ["TZ", "QSX", "CZCSXM"],
        [{"TZ": "ZZ", "QSX": "20250906", "CZCSXM": "203"}],
    )
    assert labels == ["图斑特征", "前时相", "城镇村属性码"]
    assert rows[0] == {
        "图斑特征": "住宅",
        "前时相": "2025-09-06",
        "城镇村属性码": "村庄",
    }


def test_disabled_validation_period_is_hidden_and_versioned():
    registry = default_registry()
    assert registry.version
    assert "2026_3" not in {period.id for period in registry.periods}
    assert registry.get("2026_3") is None
    assert registry.default is not None and registry.default.version


def test_tblx_mapping_asset_is_dictionary_driven():
    import json
    from pathlib import Path

    asset = json.loads(
        (Path(__file__).resolve().parents[2] / "skills" / "land-report" / "assets" / "tblx_categories.json")
        .read_text(encoding="utf-8")
    )
    assert asset["source"].startswith("knowledge_base.dict_tblx")
    assert len(asset["entries"]) == 33
    assert all(entry.get("review_status") for entry in asset["entries"])
    assert sum(1 for entry in asset["entries"] if entry["review_status"] == "pending_business") == 6


def test_data_versions_match_dataset_version():
    import json
    from pathlib import Path

    from geoagent.datasets import default_registry

    asset = json.loads(
        (Path(__file__).resolve().parents[2] / "skills" / "qa-library" / "assets" / "data_versions.json")
        .read_text(encoding="utf-8")
    )
    assert asset["current_version"] == default_registry().version
