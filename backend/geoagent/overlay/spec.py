"""套合输入规范：图层角色、必填字段、表名与期次命名规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 套合表固定落在 data schema，与现有两期套合表一致。
SCHEMA = "data"

# 暂存表所在 schema（套合过程中的中间表，任务结束统一清理）。
STAGING_SCHEMA = "overlay_tmp"

# 图层角色定义：中文名、是否必填、必填属性字段、说明。
# 字段名与现有两期套合表一致；缺少必填字段直接拒绝，缺少可选字段只给警告并置空。
LAYERS: dict[str, dict[str, Any]] = {
    "hsx": {
        "label": "后时相（变化监测图斑）",
        "required": True,
        "fields": ("XZQDM", "JCBH", "TBLX", "QSX", "HSX"),
        "hint": "母斑：提供切分边界与母斑属性（图斑类型、前后时相）",
    },
    "qsx": {
        "label": "前时相（变更地类图斑）",
        "required": True,
        "fields": ("XZQDM", "DLBM", "DLMC"),
        "hint": "碎片：套合后一行对应一块碎片，地类属性取自这里",
    },
    "yjn": {
        "label": "永久基本农田",
        "required": False,
        "fields": (),
        "hint": "可选：相交即标记“是否涉及永农”为是",
    },
    "hx": {
        "label": "生态保护红线",
        "required": False,
        "fields": (),
        "hint": "可选：相交即标记“是否涉及红线”为是",
    },
}

# 后时相缺失时置空并给出警告的属性（不阻断套合）。
HSX_OPTIONAL_FIELDS = ("XMC", "TZ", "DDTC", "BZ", "JSYDXZ")
# 前时相缺失时置空并给出警告的属性。
QSX_OPTIONAL_FIELDS = ("TBXHMC", "ZZSXDM", "ZZSXMC", "CZCSXM", "TBXHDM")

# 目标表文本字段宽度（与现有两期套合表 DDL 完全一致，超出即拒绝，避免静默截断）。
TEXT_WIDTHS: dict[str, int] = {
    "XZQDM": 6,
    "XMC": 30,
    "TBLX": 5,
    "TZ": 10,
    "QSX": 8,
    "HSX": 8,
    "DDTC": 50,
    "BZ": 100,
    "JSYDXZ": 50,
    "JCBH": 50,
    "DLBM": 5,
    "DLMC": 60,
    "TBXHMC": 100,
    "ZZSXDM": 30,
    "ZZSXMC": 100,
    "CZCSXM": 4,
    "TBXHDM": 30,
    "SFYN": 10,
    "SFHX": 50,
}

# 源数据坐标系：CGCS2000 3 度带 40 带（中央经线 120°，含带号前缀）。
DEFAULT_SOURCE_EPSG = 4528
# 入库坐标系：CGCS2000 地理坐标，与现有两期一致。
TARGET_EPSG = 4490

_TABLE_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff]{1,60}$")

_CN_NUMBERS = ("", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")


def validate_table_name(raw: str) -> str:
    """校验用户填写的表名；表名只用于双引号包裹的标识符，禁止任何拼接风险。"""
    name = str(raw or "").strip()
    if not name:
        raise ValueError("请填写套合表名。")
    if any(ch in name for ch in "\"'`;.()[]\\/%"):
        raise ValueError("表名不能包含引号、点号、分号等符号。")
    if not _TABLE_RE.match(name):
        raise ValueError("表名只能包含中英文、数字与下划线，且不超过 60 个字符。")
    return name


def quote_table(name: str) -> str:
    """返回可直接写进 SQL 的 schema."表名" 全限定名。"""
    safe = validate_table_name(name)
    return f'{SCHEMA}."{safe}"'


def validate_period(year: int, round_no: int) -> tuple[int, int]:
    """校验年份与期次。"""
    try:
        year_value = int(year)
        round_value = int(round_no)
    except (TypeError, ValueError):
        raise ValueError("年份与期次必须是整数。") from None
    if not 1900 <= year_value <= 2100:
        raise ValueError("年份应在 1900 至 2100 之间。")
    if not 1 <= round_value <= 12:
        raise ValueError("期次应在第 1 期至第 12 期之间。")
    return year_value, round_value


def suggest_table_name(year: int, round_no: int) -> str:
    """按现有命名习惯给出默认表名，例如 2026_3_change_landuse。"""
    year_value, round_value = validate_period(year, round_no)
    return f"{year_value}_{round_value}_change_landuse"


def dataset_id(year: int, round_no: int) -> str:
    """期次标识，例如 2026_3。"""
    year_value, round_value = validate_period(year, round_no)
    return f"{year_value}_{round_value}"


def period_label(year: int, round_no: int) -> str:
    """期次展示名，例如 2026年第三期。"""
    year_value, round_value = validate_period(year, round_no)
    return f"{year_value}年第{_CN_NUMBERS[round_value]}期"


def period_aliases(year: int, round_no: int) -> list[str]:
    """期次别名：覆盖用户可能说出的说法（问答链路据此识别期次）。"""
    year_value, round_value = validate_period(year, round_no)
    cn = _CN_NUMBERS[round_value]
    return [
        f"{year_value}年第{cn}期",
        f"{year_value}年第{round_value}期",
        f"{year_value}年{round_value}期",
        f"{year_value}_{round_value}",
        f"{year_value}-{round_value}",
        f"第{cn}期",
        f"第{round_value}期",
    ]


def layer_label(role: str) -> str:
    """图层角色的中文名。"""
    spec = LAYERS.get(role)
    return str(spec["label"]) if spec else role


@dataclass
class OverlayOptions:
    """一次套合的目标配置：表名 + 期次 + 是否登记。"""

    table_name: str
    year: int
    round: int
    label: str = ""
    dataset_id_value: str = ""
    note: str = ""
    overwrite: bool = False
    register: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def table_ref(self) -> str:
        return quote_table(self.table_name)

    @property
    def period_id(self) -> str:
        return self.dataset_id_value or dataset_id(self.year, self.round)

    @property
    def period_label(self) -> str:
        return self.label or period_label(self.year, self.round)

    def to_dataset_entry(self) -> dict[str, Any]:
        """转成 datasets.json 里的一条期次登记。"""
        return {
            "id": self.period_id,
            "label": self.period_label,
            "table": self.table_ref,
            "year": int(self.year),
            "round": int(self.round),
            "aliases": period_aliases(self.year, self.round),
            "note": self.note or f"{self.period_label}地类变化图斑表（套合生成）",
            "enabled": True,
        }


def make_options(
    table_name: str,
    year: int,
    round_no: int,
    *,
    label: str = "",
    dataset_id_value: str = "",
    note: str = "",
    overwrite: bool = False,
    register: bool = True,
) -> OverlayOptions:
    """构造并校验套合配置。"""
    safe_name = validate_table_name(table_name)
    year_value, round_value = validate_period(year, round_no)
    return OverlayOptions(
        table_name=safe_name,
        year=year_value,
        round=round_value,
        label=str(label or "").strip(),
        dataset_id_value=str(dataset_id_value or "").strip(),
        note=str(note or "").strip(),
        overwrite=bool(overwrite),
        register=bool(register),
    )


def describe_layers() -> list[dict[str, Any]]:
    """给前端渲染的图层清单。"""
    return [
        {
            "key": role,
            "label": str(spec["label"]),
            "required": bool(spec["required"]),
            "fields": list(spec["fields"]),
            "hint": str(spec["hint"]),
        }
        for role, spec in LAYERS.items()
    ]