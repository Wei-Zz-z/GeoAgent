"""TBLX（图斑类型）→ 三大类 映射加载器。

映射是经常调整的业务数据，数据源放在技能资产中（改动时只编辑 JSON，不碰代码）：
    skills/land-report/assets/tblx_categories.json
本模块只负责定位资产、加载并提供查询帮助函数；SQLAgent 知识卡中的摘要应与
该 JSON 保持同步。

三大类沿用三调口径：农用地 / 建设用地 / 未利用地。JSON 的 code/name 以
knowledge_base.dict_tblx 为正式清单；`review_status=pending_business` 表示分类边界
仍需业务确认（不是“未实现”），确认后只改 JSON，不改代码。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

CATEGORY_LABELS: dict[str, str] = {
    "agricultural": "农用地",
    "construction": "建设用地",
    "unused": "未利用地",
    "separate": "单列类型（不计入三大类）",
}


def default_asset_path() -> Path:
    """仓库默认技能资产路径（<项目根>/skills/land-report/assets/...）。"""
    return (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "land-report"
        / "assets"
        / "tblx_categories.json"
    )


def resolve_asset_path(skills_dir: Optional[str | Path] = None) -> Path:
    """定位映射资产：优先用运行时技能目录，其次仓库默认路径。"""
    if skills_dir:
        candidate = Path(skills_dir) / "land-report" / "assets" / "tblx_categories.json"
        if candidate.is_file():
            return candidate
    default = default_asset_path()
    if not default.is_file():
        raise FileNotFoundError(
            f"缺少 TBLX 三大类映射资产: {default}（应随 land-report 技能提供）"
        )
    return default


@lru_cache(maxsize=8)
def load_entries(asset_path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """加载映射：code -> {name, category, uncertain}；01-04 同时收录 1-4 写法。"""
    path = resolve_asset_path(asset_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, Any]] = {}
    for item in data["entries"]:
        code = item["code"]
        info = {
            "name": item["name"],
            "category": item["category"],
            "uncertain": bool(item.get("uncertain", False)),
            "review_status": str(item.get("review_status") or
                                 ("pending_business" if item.get("uncertain") else "confirmed")),
        }
        entries[code] = info
        # 数据中 1-4 号地类存为前导零，同时收录不带前导零的写法。
        if len(code) == 2 and code[0] == "0" and code[1].isdigit():
            entries[code[1:]] = info
    return entries


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def tblx_category(code: str, skills_dir: Optional[str | Path] = None) -> Optional[str]:
    """返回 TBLX 编码对应的三大类 key；未收录返回 None。"""
    info = load_entries(resolve_asset_path(skills_dir)).get(code)
    return info["category"] if info else None


def category_codes(category: str, skills_dir: Optional[str | Path] = None) -> list[str]:
    """返回某三大类包含的全部 TBLX 编码（含前导零别名）。"""
    entries = load_entries(resolve_asset_path(skills_dir))
    return sorted(code for code, info in entries.items() if info["category"] == category)


# “建设用地（含疑似）”口径：正式建设类 + 动土/推堆土/瓦砾/推平。
# 与 SQLAgent 提示词第 3 节、问题库条目里的“建设模糊集”一致；separate 类中的
# “其他(QT)”不属于疑似建设，不计入。
SUSPECT_CONSTRUCTION_CODES: tuple[str, ...] = ("DT", "TD", "WL", "TP")


def construction_codes(include_suspect: bool = True) -> list[str]:
    """返回建设用地代码集；include_suspect=True 时含“疑似建设”类（19 类）。"""
    codes = list(category_codes("construction"))
    if include_suspect:
        for code in SUSPECT_CONSTRUCTION_CODES:
            if code not in codes:
                codes.append(code)
    return sorted(codes)


def display_groups(
    skills_dir: Optional[str | Path] = None,
) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """按 JSON 原始顺序返回三大类分组，供报告/对话展示（不含前导零别名）。"""
    path = resolve_asset_path(skills_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    groups: dict[str, list[tuple[str, str, bool]]] = {}
    for item in data["entries"]:
        groups.setdefault(item["category"], []).append(
            (item["code"], item["name"], bool(item.get("uncertain", False)))
        )
    order = ["agricultural", "construction", "unused", "separate"]
    return [
        (category_label(key), groups[key])
        for key in order
        if key in groups
    ]
