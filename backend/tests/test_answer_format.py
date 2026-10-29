"""回答排版回归：去掉 Markdown 标题、类型编码、期次表名与内部实现说明。"""

from __future__ import annotations

from types import SimpleNamespace

from geoagent.datasets import DatasetPeriod
from geoagent.prematch import _compose
from geoagent.tools.stat import _summary_lines


def test_compose_is_plain_readable_text():
    entry = {
        "id": "total_summary",
        "question": "全库共有多少个变化图斑、总面积是多少",
        "answer_hint": "全库图斑总数与总面积",
        "unit": "亩",
    }
    period = DatasetPeriod(
        id="2026_2",
        label="2026年第二期",
        table='data."2026_2_change_landuse"',
        year=2026,
        round=2,
    )
    executed = SimpleNamespace(
        body="全库合计：共 27,722 个图斑、总面积 1,838,583.37 亩。",
        unit="亩",
    )
    text = _compose(
        entry,
        period,
        executed,
        caliber={"scope": "全库", "risk": "low"},
    )
    assert not text.lstrip().startswith("#")
    assert "##" not in text
    assert "data.\"" not in text
    assert "本回答由标准问题库条目" not in text
    assert "数据期次：2026年第二期" in text
    assert "统计范围" not in text  # 默认全库不啰嗦
    assert "风险等级" not in text  # 低风险不展示


def test_type_summary_has_no_field_codes():
    lines = _summary_lines(
        [{"name": "动土", "code": "DT", "n": 67738, "area_m2": 8415206.70}],
        67738,
        8415206.70,
    )
    text = "\n".join(lines)
    assert "- 动土：" in text
    assert "DT" not in text
    assert "TBLX" not in text
