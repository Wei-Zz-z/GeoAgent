"""三份固定模板的自动生成边界与师兄版位置绑定。"""

from pathlib import Path

import pytest

from geoagent.report.brother_trial import brother_answers, is_brother_template
from geoagent.report import supported_templates


def _stats() -> dict:
    return {
        "total_n": 100, "total_area": 2000,
        "orig_crop": {"n": 40, "area": 800},
        "cur_crop": {"n": 30, "area": 600},
        "crop_net_n": -10, "crop_net_area": -200,
        "crop2const": {"n": 8, "area": 160},
        "restore2crop": {"n": 4, "area": 80},
        "cur_const": {"n": 60, "area": 1200},
        "crop2const_pct": 13.333,
        "top_net_counties": [{"xmc": "甲区", "outflow": 300, "inflow": 100, "net": -200}],
        "top_const_counties": [{"xmc": "乙区", "n": 10, "area": 200}],
    }


def test_brother_bindings_keep_area_unit_and_slot_order() -> None:
    ids = [
        "b6:s0", "b6:s1", *(f"b8:s{i}" for i in range(6)),
        *(f"b9:s{i}" for i in range(4)), "b10:table", "b12:table",
        *(f"b14:s{i}" for i in range(5)), "b16:table",
    ]
    parsed = {"slots": [{"id": slot_id} for slot_id in ids]}
    scalar, tables = brother_answers(parsed, _stats())
    assert scalar["b6:s0"] == "100"
    assert scalar["b6:s1"] == "3.00"
    assert scalar["b8:s5"] == "-0.30"
    assert scalar["b14:s4"] == "13.33"
    assert tables["b12:table"] == [["甲区", "0.45", "0.15", "-0.30"]]
    assert tables["b16:table"] == [["乙区", "10", "0.30"]]
    with pytest.raises(ValueError, match="待填位置发生变化"):
        brother_answers({"slots": []}, _stats())


def test_brother_template_requires_exact_file() -> None:
    assert not is_brother_template({"sha256": "other", "blocks": [], "slots": []})


@pytest.mark.asyncio
async def test_supported_generation_rejects_unknown_before_query(tmp_path: Path) -> None:
    class Gateway:
        async def run_sql(self, *_args, **_kwargs):
            raise AssertionError("未知模板不可查库")

    with pytest.raises(ValueError, match="不能自动生成"):
        await supported_templates.generate_supported_report(
            tmp_path / "other.docx", {"sha256": "other"}, Gateway(),
            tmp_path / "skills", tmp_path / "reports",
        )
