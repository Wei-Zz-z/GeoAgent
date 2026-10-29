"""期次数据集注册表：一张套合表 = 一期数据。

问答链路里"统计口径"和"数据期次"必须分开管理：

- 口径（"DLBM" LIKE '01%'、"TBLX" 属于建设编码集、扩展码怎么算……）属于标准问题条目，
  写在 ``skills/qa-library/assets/question_library.json``，跨期次不变；
- 物理表名属于某一期套合数据，写在本模块加载的
  ``skills/qa-library/assets/datasets.json``，换一期只改这份资产。

因此问题库与确定性工具里的 SQL 一律使用 ``{table}`` 占位符（也兼容历史遗留的某期
具体表名），执行前由 :func:`bind_table` 绑定当期表名，避免把"2026 年第一期"固化进
代码与业务资产。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# 问题库 SQL 中的期次表占位符。
PLACEHOLDER = "{table}"

# 完全没有登记期次时的兜底表：保持与历史行为一致，避免资产缺失时整条链路不可用。
FALLBACK_TABLE = 'data."2026_1_change_landuse"'


@dataclass(frozen=True)
class DatasetPeriod:
    """一期套合数据：期次标识 + 物理表名 + 用户可能用到的别名。"""

    id: str
    label: str
    table: str
    aliases: tuple[str, ...] = ()
    year: Optional[int] = None
    round: Optional[int] = None
    note: str = ""
    enabled: bool = True
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "table": self.table,
            "aliases": list(self.aliases),
            "year": self.year,
            "round": self.round,
            "note": self.note,
            "enabled": self.enabled,
            "version": self.version,
        }


@dataclass(frozen=True)
class PeriodLookup:
    """一次期次识别的结果。

    - ``dataset``：命中的已登记期次（问题未提期次时为默认期次）；
    - ``mentioned``：问题里出现了期次字样，但没有命中任何已登记期次（例如问"第二期"
      而第二期还没登记表）。此时必须显式提示用户，不能悄悄用默认期次回答；
    - ``raw``：命中的原文片段，用于告诉用户"我按哪一期理解"。
    """

    dataset: Optional[DatasetPeriod]
    mentioned: bool = False
    raw: str = ""


@dataclass(frozen=True)
class DatasetRegistry:
    """期次注册表：按 id / 按问题文本解析期次。"""

    periods: tuple[DatasetPeriod, ...] = ()
    default_id: str = ""
    version: str = ""

    @property
    def default(self) -> Optional[DatasetPeriod]:
        if self.default_id:
            found = self.get(self.default_id)
            if found is not None:
                return found
        return self.periods[0] if self.periods else None

    def get(self, dataset_id: str) -> Optional[DatasetPeriod]:
        key = str(dataset_id or "").strip()
        if not key:
            return None
        for period in self.periods:
            if period.id == key:
                return period
        # 也允许用展示名/别名直接指定期次。
        normalized = _normalize(key)
        if not normalized:
            return None
        for period in self.periods:
            if normalized in _period_keys(period):
                return period
        return None

    @property
    def tables(self) -> list[str]:
        """全部已登记期次的物理表名（用于拼接数据库白名单）。"""
        return [p.table for p in self.periods if p.table]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default_id": self.default_id,
            "datasets": [p.to_dict() for p in self.periods],
        }


def default_asset_path() -> Path:
    """仓库默认资产路径（<项目根>/skills/qa-library/assets/datasets.json）。"""
    return (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "qa-library"
        / "assets"
        / "datasets.json"
    )


def resolve_asset_path(skills_dir: Optional[str | Path] = None) -> Path:
    """定位期次资产：优先用运行时技能目录，其次仓库默认路径。"""
    if skills_dir:
        candidate = Path(skills_dir) / "qa-library" / "assets" / "datasets.json"
        if candidate.is_file():
            return candidate
    return default_asset_path()


@lru_cache(maxsize=8)
def load_registry(asset_path: Optional[Path] = None) -> DatasetRegistry:
    """加载期次注册表；资产不存在时返回空注册表（调用方回退 FALLBACK_TABLE）。"""
    path = Path(asset_path) if asset_path else default_asset_path()
    if not path.is_file():
        return DatasetRegistry(periods=(), default_id="")
    data = json.loads(path.read_text(encoding="utf-8"))
    periods: list[DatasetPeriod] = []
    version = str(data.get("version", ""))
    for item in data.get("datasets", []):
        periods.append(
            DatasetPeriod(
                id=str(item.get("id", "")),
                label=str(item.get("label", "")),
                table=str(item.get("table", "")),
                aliases=tuple(str(a) for a in item.get("aliases", [])),
                year=item.get("year"),
                round=item.get("round"),
                note=str(item.get("note", "")),
                enabled=bool(item.get("enabled", True)),
                version=str(item.get("version") or version),
            )
        )
    return DatasetRegistry(
        periods=tuple(p for p in periods if p.id and p.table and p.enabled),
        default_id=str(data.get("default_id", "")),
        version=version,
    )


def default_registry() -> DatasetRegistry:
    """仓库默认期次注册表（未配置 GEOAGENT_SKILLS_DIR 时使用）。"""
    return load_registry(default_asset_path())


def describe_periods(registry: DatasetRegistry) -> str:
    """把已登记期次渲染成一行中文，供提示词与提示信息复用。"""
    if not registry.periods:
        return "未登记任何期次数据"
    default = registry.default
    parts = []
    for period in registry.periods:
        mark = "，当前默认" if default is not None and period.id == default.id else ""
        parts.append(f"{period.label}（{period.table}{mark}）")
    return "；".join(parts)


def _normalize(text: str) -> str:
    """去掉空白与标点、统一小写，仅保留中英文与数字（用于别名比对）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text or "").lower())


def _period_keys(period: DatasetPeriod) -> set[str]:
    keys = {_normalize(period.id), _normalize(period.label)}
    keys.update(_normalize(alias) for alias in period.aliases)
    keys.discard("")
    return keys


_CN_NUMBERS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}
_PERIOD_PATTERN = re.compile(
    r"(?:(?P<year>\d{4})\s*年?\s*)?第?\s*(?P<round>[0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*期"
)
_DIGIT_PERIOD_PATTERN = re.compile(r"(?P<year>\d{4})\s*[_\-]\s*(?P<round>\d{1,2})(?!\d)")


def _parse_round(text: str) -> Optional[int]:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return _CN_NUMBERS.get(text)


def find_period(registry: DatasetRegistry, text: str) -> PeriodLookup:
    """从用户问题里识别期次。

    优先级：命中已登记别名 → 命中"XXXX年第N期 / XXXX-N"样式（未登记则标记
    ``mentioned``，由调用方提示用户）→ 默认期次。
    """
    normalized = _normalize(text)
    if normalized:
        candidates: list[tuple[int, DatasetPeriod]] = []
        for period in registry.periods:
            for key in _period_keys(period):
                if key and key in normalized:
                    candidates.append((len(key), period))
        if candidates:
            # 先比长别名，避免"第一期"抢走"2026年第一期"。
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            best = candidates[0][1]
            return PeriodLookup(dataset=best, raw=best.label)

    raw_text = str(text or "")
    for pattern in (_PERIOD_PATTERN, _DIGIT_PERIOD_PATTERN):
        match = pattern.search(raw_text)
        if not match:
            continue
        year_text = match.groupdict().get("year")
        year_value = int(year_text) if year_text else None
        round_value = _parse_round(match.group("round"))
        if round_value is not None:
            for period in registry.periods:
                if period.round == round_value and (year_value is None or period.year == year_value):
                    return PeriodLookup(dataset=period, raw=match.group(0))
        return PeriodLookup(dataset=None, mentioned=True, raw=match.group(0))

    return PeriodLookup(dataset=registry.default)


def table_variants(spec: str) -> list[str]:
    """一条表名的常见书写形式：原样 / 去引号 / 仅表名（带引号）/ 仅表名。"""
    spec = str(spec or "").strip()
    if not spec:
        return []
    unquoted = spec.replace('"', "")
    variants = [spec, unquoted]
    bare = unquoted.split(".")[-1]
    if bare and bare != unquoted:
        variants.extend([f'"{bare}"', bare])
    unique: list[str] = []
    for item in variants:
        if item and item not in unique:
            unique.append(item)
    # 长串优先替换，避免先替换短串导致长串匹配不上。
    unique.sort(key=len, reverse=True)
    return unique


def bind_table(sql: str, table: str, known_tables: Any = ()) -> str:
    """把 SQL 里的期次表绑定为目标表。

    - ``{table}`` 占位符直接替换；
    - 兼容历史遗留的某期具体表名：只替换"已登记期次"的表名，不会误伤别的表。
    """
    target = str(table or "").strip()
    if not sql or not target:
        return sql
    bound = sql.replace(PLACEHOLDER, target)
    for known in known_tables or ():
        if not known or str(known) == target:
            continue
        for variant in table_variants(str(known)):
            if variant in bound:
                bound = bound.replace(variant, target)
    return bound


def bind_entry_sql(
    sql: str,
    registry: Optional[DatasetRegistry] = None,
    dataset: Optional[DatasetPeriod] = None,
) -> str:
    """按注册表/指定期次绑定标准问题 SQL（问答、模板、快报三条链路共用）。"""
    active = registry if isinstance(registry, DatasetRegistry) else default_registry()
    period = dataset if isinstance(dataset, DatasetPeriod) else active.default
    table = period.table if period is not None else FALLBACK_TABLE
    return bind_table(sql, table, active.tables)


def context_table(ctx: Any) -> str:
    """取当前轮次的期次表名；没有上下文或没有登记期次时回退 FALLBACK_TABLE。

    确定性统计工具只关心"当前这一期的表"，期次由 Agent 在每轮开始时解析并写入 ctx。
    """
    dataset = getattr(ctx, "dataset", None)
    if dataset is not None:
        table = getattr(dataset, "table", None)
        if not table and isinstance(dataset, dict):
            table = dataset.get("table")
        if table:
            return str(table)
    registry = getattr(ctx, "datasets", None)
    period = registry.default if isinstance(registry, DatasetRegistry) else None
    if period is None:
        period = default_registry().default
    return period.table if period is not None else FALLBACK_TABLE