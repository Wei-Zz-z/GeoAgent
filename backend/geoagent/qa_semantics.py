"""智能问数语义匹配：查询理解 → 候选召回 → 槽位重排 → 风险分级决策。

与 ``report/question_library.py`` 的字面匹配相比，本模块补的是"结构化语义"
（主体 / 前后时项 / 聚合 / 维度 / 限定 / 转换方向），因此没见过的口语问法也能
落到正确标准问题，而不是只能靠别名查表。

设计边界（对应《智能问数语义匹配与准确性优化方案》）：

- 词表、阈值、指标卡都放在 ``skills/qa-library/assets/``，业务可编辑；
- 字面分只作为特征之一，不再用 ``raw/16`` 直接当置信度；
- 输出 ``decision``（execute / candidates / clarify / fallback），预匹配按决策执行，
  不再"低分就静默转自由 SQL"；
- 高风险口径（永农/红线/疑似违法）提高自动执行门槛，并在口径卡中标注"疑似"。
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .report.question_library import _candidate as _lexical_candidate

# 具体主体：权重高于图斑、地类等泛主体；也是"条目多出主体"惩罚的依据。
SPECIFIC_SUBJECTS = {
    "farmland", "forest", "grassland", "garden", "construction", "water", "ecology",
    "yongnong", "redline", "fragment", "adjustable",
}
GENERIC_SUBJECTS = {"patch", "land_class", "patch_type", "total", "unit"}

CN_NUMBERS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 指标卡生成逻辑里按工具名固定的语义（工具本身的口径比问句文本更可靠）。
TOOL_PROFILES: dict[str, dict[str, Any]] = {
    "summarize_by_type": {
        "subjects": {"patch_type"}, "aggs": {"count", "area"}, "dims": {"type"},
    },
    "farmland_flow_summary": {
        "subjects": {"farmland"}, "aggs": {"flow", "source", "target"}, "side": "flow",
    },
    "construction_change_summary": {
        "subjects": {"construction"}, "aggs": {"count", "area", "source", "structure"},
        "dims": {"county"}, "side": "after",
    },
    "fragment_stats": {
        "subjects": {"patch"}, "filters": {"fragment"}, "aggs": {"count", "ratio"},
    },
    "top_conversions": {
        "subjects": {"transition"}, "aggs": {"top", "area"}, "dims": {"type"},
    },
    "convert_area": {"subjects": {"unit"}, "aggs": {"list"}},
    "warning_yongnong_polygons": {
        "subjects": {"yongnong", "construction"}, "filters": {"yongnong"},
        "aggs": {"count", "area", "map"}, "dims": {"county"}, "risk": "high",
    },
    "warning_redline_polygons": {
        "subjects": {"redline", "construction"}, "filters": {"redline"},
        "aggs": {"count", "area", "map"}, "dims": {"county"}, "risk": "high",
    },
}

# 条目级修正：文本推导不出来的语义（方向、活跃度、合计核对等）。
ENTRY_OVERRIDES: dict[str, dict[str, Any]] = {
    # 同工具条目必须按条目区分：总览不是"按类型"，按类型才带 type 维度。
    "total_summary": {
        "replace_subjects": {"patch", "total"},
        "set_aggs": {"count", "area"},
        "set_dims": set(),
        "side": "",
    },
    "by_type_summary": {
        "replace_subjects": {"patch", "patch_type"},
        "set_aggs": {"count", "area"},
        "set_dims": {"type"},
        "side": "after",
    },
    "all_type_transition": {
        "replace_subjects": {"land_class", "transition"},
        "set_aggs": {"flow", "area", "count"},
        "set_dims": {"land_class"},
        "side": "flow",
    },
    "farmland_inflow_source": {"side": "flow"},
    "farmland_outflow_detail": {"side": "flow"},
    "forest_outflow_detail": {"side": "flow"},
    "construction_source_detail": {"aggs": {"source"}, "dims": {"land_class"}},
    "construction_structure": {"aggs": {"structure"}},
    "conversion_activity": {"aggs": {"activity"}, "dims": {"type"}},
    "total_vs_net_reconcile": {"aggs": {"reconcile"}, "side": "flow"},
}

DEFAULT_ASSET_DIR = (
    Path(__file__).resolve().parents[2] / "skills" / "qa-library" / "assets"
)

# 向量召回 → RRF 融合：
# - 只把 embedding 相似度 >= 0.45 的条目纳入向量排名，避免低相似条目也拿到“第一名”奖励；
# - RRF 只作为有界加分（默认最多 +0.08），不替换原有槽位分，防止向量把错误口径顶上来；
# - 词法排名继续来自原别名/关键词/标准问句匹配。
RRF_K = 60
VECTOR_MIN_SIMILARITY = 0.45
VECTOR_WEIGHT = 0.08

# 置信度校准：离线用脚本拟合 logistic，把匹配分映射成 p(correct)。
CALIBRATION_FILENAME = "match_calibration.json"
CALIBRATION_MIN_P = 0.60
UNSUPPORTED_AGGS = {"average", "median"}
CALIBRATION_FEATURES = (
    "score",
    "lexical",
    "subject",
    "filter",
    "side",
    "agg",
    "dim",
    "vector",
    "rrf",
    "risk_high",
    "risk_medium",
    "is_top1",
    "margin",
    "adversarial",
    "vague",
)


# ---------------------------------------------------------------- 资产定位与加载


def resolve_asset_path(filename: str, skills_dir: Optional[str | Path] = None) -> Path:
    """定位语义资产：优先运行时技能目录，其次仓库默认目录。"""
    if skills_dir:
        candidate = Path(skills_dir) / "qa-library" / "assets" / filename
        if candidate.is_file():
            return candidate
    return DEFAULT_ASSET_DIR / filename


@lru_cache(maxsize=4)
def load_terms(path: str) -> dict[str, Any]:
    """加载语义词表（按路径缓存）；文件缺失或损坏时抛出异常，由调用方降级。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("subjects"), dict):
        raise ValueError("terms.json 缺少 subjects")
    return data


@lru_cache(maxsize=4)
def load_metric_cards(path: str) -> dict[str, dict[str, Any]]:
    """加载指标卡（按路径缓存）；文件缺失时返回空表，运行时回退自动推导。"""
    source = Path(path)
    if not source.is_file():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    cards = data.get("cards")
    return cards if isinstance(cards, dict) else {}


@lru_cache(maxsize=4)
def load_calibration(path: str) -> Optional[dict[str, Any]]:
    """加载置信度校准参数；缺失或结构不合法时返回 None（回退原始匹配分）。"""
    source = Path(path)
    if not source.is_file():
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    features = data.get("features")
    weights = data.get("weights")
    if not isinstance(features, list) or not isinstance(weights, list):
        return None
    if len(features) != len(weights):
        return None
    return data


def clear_semantic_cache() -> None:
    """清空词表/指标卡/校准缓存（测试或热更新资产后调用）。"""
    load_terms.cache_clear()
    load_metric_cards.cache_clear()
    load_calibration.cache_clear()


# ---------------------------------------------------------------- 查询理解


def _norm(text: str) -> str:
    """归一化：小写、去空白，只去掉“如/例如/→”这类示例括号，保留口径说明。"""
    value = str(text or "").lower()
    value = re.sub(r"[（(](?:如|例如|比如)[^）)]*[）)]", "", value)
    value = re.sub(r"[（(][^）)]*→[^）)]*[）)]", "", value)
    return re.sub(r"[\s\u3000]+", "", value)


def _hits(text: str, words: list[str]) -> list[str]:
    return [word for word in words if word and word in text]


@dataclass
class QuerySlots:
    """一轮问题被理解出的结构化槽位。"""

    text: str = ""
    subjects: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    aggs: set[str] = field(default_factory=set)
    dims: set[str] = field(default_factory=set)
    side: str = ""
    transition: Optional[tuple[str, str]] = None
    top_n: Optional[int] = None
    threshold: Optional[float] = None
    risk: str = "low"
    adversarial: bool = False
    vague: bool = False
    unsupported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjects": sorted(self.subjects),
            "filters": sorted(self.filters),
            "aggregations": sorted(self.aggs),
            "dimensions": sorted(self.dims),
            "side": self.side,
            "transition": list(self.transition) if self.transition else None,
            "top_n": self.top_n,
            "threshold": self.threshold,
            "risk": self.risk,
            "adversarial": self.adversarial,
            "vague": self.vague,
            "unsupported": self.unsupported,
        }


def _detect_transition(text: str, terms: dict[str, Any]) -> Optional[tuple[str, str]]:
    """方向敏感地识别“A 转/变/流向 B”，顺序不能反。"""
    transitions = terms.get("transitions") or {}
    for phrase, pair in (transitions.get("phrases") or {}).items():
        if phrase and phrase in text and pair and len(pair) == 2:
            return (str(pair[0]), str(pair[1]))
    # 常见口语：耕地被建设占用 / 耕地上盖房子
    if "耕地" in text and ("建设" in text or "厂房" in text or "房子" in text) and any(
        word in text for word in ("占用", "被占", "盖房", "建厂", "建房")
    ):
        return ("farmland", "construction")
    patterns: list[tuple[int, str, str, str, str]] = []
    for item in transitions.get("ordered") or []:
        source = str(item.get("source") or "")
        target = str(item.get("target") or "")
        for pattern in item.get("patterns") or []:
            if len(pattern) != 2:
                continue
            patterns.append(
                (len(str(pattern[0])) + len(str(pattern[1])), source, target,
                 str(pattern[0]), str(pattern[1]))
            )
    # 长模式优先，避免“建设”抢走“建设用地”
    for _, source, target, source_word, target_word in sorted(
        patterns, key=lambda row: row[0], reverse=True
    ):
        regex = re.compile(
            rf"{re.escape(source_word)}[^，。；]{{0,8}}?"
            r"(?:转|变|流向|转化|占用)(?:为|成)?"
            rf"[^，。；]{{0,4}}?{re.escape(target_word)}"
        )
        if regex.search(text):
            return (source, target)
    return None


def understand(question: str, terms: Optional[dict[str, Any]] = None) -> QuerySlots:
    """把自然语言问题解析成槽位（规则优先，不调用模型/网络）。"""
    terms = terms or load_terms(str(resolve_asset_path("terms.json")))
    text = _norm(question)
    slots = QuerySlots(text=text)

    for subject_id, meta in (terms.get("subjects") or {}).items():
        if _hits(text, list(meta.get("synonyms") or [])):
            slots.subjects.add(str(subject_id))
    # “亩/公顷”只在换算语境下算主体，避免把普通面积问法误判成单位换算
    unit_words = {word for word in ("亩", "公顷", "平方米", "平米") if word in text}
    if ("换算" in text or "单位" in text or ("转" in text and len(unit_words) >= 2)) and unit_words:
        slots.subjects.add("unit")
        slots.aggs.add("list")
    # “不同类型的变化图斑”没有出现“图斑类型”四个字，但确实是按类型汇总
    if "类型" in text and ("图斑" in text or "变化" in text) and "地类" not in text:
        slots.subjects.add("patch_type")

    for agg_id, meta in (terms.get("aggregations") or {}).items():
        if _hits(text, list(meta.get("synonyms") or [])):
            slots.aggs.add(str(agg_id))
    for dim_id, meta in (terms.get("dimensions") or {}).items():
        if _hits(text, list(meta.get("synonyms") or [])):
            slots.dims.add(str(dim_id))
    for filter_id, meta in (terms.get("filters") or {}).items():
        if _hits(text, list(meta.get("synonyms") or [])):
            slots.filters.add(str(filter_id))
    if re.search(r"小于\s*\d+", text):
        slots.filters.add("fragment")

    transition = _detect_transition(text, terms)
    if transition:
        slots.transition = transition
        slots.subjects.add("transition")
        slots.subjects.add("land_class")
        slots.side = "transition"
    elif slots.aggs & {"flow"}:
        slots.side = "flow"
    else:
        cues = terms.get("side_cues") or {}
        if _hits(text, list(cues.get("after") or [])):
            slots.side = "after"
        elif _hits(text, list(cues.get("before") or [])):
            slots.side = "before"

    number_match = re.search(r"(?:前|top)\s*(\d{1,3})(?:条|个|种|名|位)?", text)
    if number_match:
        slots.top_n = int(number_match.group(1))
    else:
        cn_match = re.search(r"前([一二两三四五六七八九十]{1,2})", text)
        if cn_match:
            slots.top_n = CN_NUMBERS.get(cn_match.group(1))
    threshold_match = re.search(r"小于\s*(\d+(?:\.\d+)?)", text)
    if threshold_match:
        slots.threshold = float(threshold_match.group(1))
    # 标准问题库只有“小于 X”的细碎图斑口径；大于/超过类阈值交自由 SQL。
    if re.search(r"(超过|大于|高于|不少于|至少|以上)\s*\d", text):
        slots.unsupported = True

    slots.adversarial = bool(_hits(text, list(terms.get("negation") or [])))
    if re.search(r"按[^，。；]{0,20}(?:等于|=|算成|当成)", text):
        slots.adversarial = True
    slots.unsupported = slots.unsupported or any(
        word in text
        for word in ("方差", "标准差", "四分位", "分位数", "中位数", "平均值", "均值", "众数", "相关系数")
    )
    slots.vague = bool(_hits(text, list(terms.get("vague") or []))) and not (
        slots.aggs & {"ratio", "flow", "max", "top", "list"}
    )
    slots.risk = _risk_of(slots, terms)
    return slots


def _risk_of(slots: QuerySlots, terms: dict[str, Any]) -> str:
    rules = terms.get("risk") or {}
    high_subjects = set(rules.get("high_subjects") or [])
    if slots.subjects & high_subjects or _hits(slots.text, list(rules.get("high_words") or [])):
        return "high"
    medium_aggs = set(rules.get("medium_aggs") or [])
    medium_dims = set(rules.get("medium_dims") or [])
    if slots.aggs & medium_aggs or slots.transition or slots.dims & medium_dims:
        return "medium"
    return "low"


# ---------------------------------------------------------------- 条目画像


@dataclass
class EntryProfile:
    """标准问题条目的语义画像（指标卡）。"""

    subjects: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    aggs: set[str] = field(default_factory=set)
    dims: set[str] = field(default_factory=set)
    side: str = ""
    transition: Optional[tuple[str, str]] = None
    risk: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjects": sorted(self.subjects),
            "filters": sorted(self.filters),
            "aggs": sorted(self.aggs),
            "dims": sorted(self.dims),
            "side": self.side,
            "transition": list(self.transition) if self.transition else None,
            "risk": self.risk,
        }


def profile_entry(entry: dict[str, Any], terms: dict[str, Any]) -> EntryProfile:
    """从标准问句、intent、keywords、SQL 与工具名推导条目语义画像。"""
    text = " ".join(
        [
            str(entry.get("question", "")),
            str(entry.get("intent", "")),
            " ".join(str(item) for item in entry.get("keywords") or []),
        ]
    )
    slots = understand(text, terms)
    profile = EntryProfile(
        subjects=set(slots.subjects),
        filters=set(slots.filters),
        aggs=set(slots.aggs),
        dims=set(slots.dims),
        side=slots.side,
        transition=slots.transition,
        risk=slots.risk,
    )
    sql = str(entry.get("sql") or "")

    def sql_has(*needles: str) -> bool:
        return any(needle in sql for needle in needles)

    if sql_has('"DLBM" LIKE \'01%', "DLBM LIKE '01%"):
        profile.subjects.add("farmland")
        profile.side = profile.side or "before"
    if sql_has('"DLBM" LIKE \'03%'):
        profile.subjects.add("forest")
    if sql_has('"DLBM" LIKE \'04%'):
        profile.subjects.add("grassland")
    if sql_has('"DLBM" LIKE \'02%'):
        profile.subjects.add("garden")
    if sql_has('"DLBM" LIKE \'11%'):
        profile.subjects.add("water")
    if sql_has('"DLBM" ~') and "05|06" in sql:
        profile.subjects.add("construction")
        profile.side = profile.side or "before"
    if sql_has('"TBLX" IN (\'01\',\'1\')', '"TBLX" IN (\'01\', \'1\')', '"TBLX" IN (\'01\',\'1\')'):
        profile.subjects.add("farmland")
        profile.side = "after"
    if sql_has("20','YH"):
        profile.subjects.add("construction")
        if profile.side != "before":
            profile.side = "after"
    if sql_has('"SFYN"=\'是\''):
        profile.filters.add("yongnong")
    if sql_has('"SFHX"=\'是\''):
        profile.filters.add("redline")
    if sql_has('GROUP BY "XMC"'):
        profile.dims.add("county")
    if sql_has('sum("MJ")'):
        profile.aggs.add("area")
    if sql_has("count(*)"):
        profile.aggs.add("count")
    if sql_has("* 100", "100.0"):
        profile.aggs.add("ratio")
    if sql_has('ORDER BY "MJ" DESC') and "LIMIT" in sql:
        profile.aggs.add("top")
    if sql_has('"MJ" <'):
        profile.filters.add("fragment")
    if sql_has("ZZSXMC"):
        profile.subjects.add("planting")
        if "即可恢复" in sql:
            profile.filters.add("adjustable")
    if sql_has('"TZ"'):
        profile.subjects.add("feature")
    if sql_has("QSX"):
        profile.subjects.add("timephase")
    if sql_has("CZCSXM"):
        profile.subjects.add("czcs")
    if sql_has("JSYDXZ"):
        profile.aggs.add("structure")
    if sql_has("dict_tblx"):
        profile.subjects.add("patch_type")
        profile.aggs.add("list")
    if sql_has("dict_land_classification_summary"):
        profile.subjects.add("land_class")
        profile.aggs.add("list")

    tool = str(entry.get("tool") or "")
    if tool in TOOL_PROFILES:
        fixed = TOOL_PROFILES[tool]
        profile.subjects = set(fixed.get("subjects", profile.subjects))
        profile.aggs |= set(fixed.get("aggs") or [])
        profile.dims |= set(fixed.get("dims") or [])
        profile.filters |= set(fixed.get("filters") or [])
        profile.side = str(fixed.get("side") or profile.side)
        profile.risk = str(fixed.get("risk") or profile.risk)

    override = ENTRY_OVERRIDES.get(str(entry.get("id") or ""))
    if override:
        if "replace_subjects" in override:
            profile.subjects = set(override.get("replace_subjects") or [])
        if "set_aggs" in override:
            profile.aggs = set(override.get("set_aggs") or [])
        if "set_dims" in override:
            profile.dims = set(override.get("set_dims") or [])
        if "set_filters" in override:
            profile.filters = set(override.get("set_filters") or [])
        profile.subjects |= set(override.get("subjects") or [])
        profile.aggs |= set(override.get("aggs") or [])
        profile.dims |= set(override.get("dims") or [])
        profile.filters |= set(override.get("filters") or [])
        if override.get("side"):
            profile.side = str(override["side"])

    # 风险按最终画像重算，避免工具/条目修正后风险丢失
    if profile.filters & {"yongnong", "redline"} or any(
        word in text for word in ("违法", "违规")
    ):
        profile.risk = "high"
    elif profile.risk == "low" and (
        profile.aggs & {"ratio", "flow", "reconcile", "activity"}
        or profile.transition
        or profile.dims & {"county_rank"}
    ):
        profile.risk = "medium"
    return profile


def _profile_from_card(card: dict[str, Any], entry: dict[str, Any], terms: dict[str, Any]) -> EntryProfile:
    """指标卡转画像；缺字段时用自动推导补齐。"""
    derived = profile_entry(entry, terms)
    transition = card.get("transition")
    return EntryProfile(
        subjects=set(card.get("subjects") or derived.subjects),
        filters=set(card.get("filters") or derived.filters),
        aggs=set(card.get("aggs") or derived.aggs),
        dims=set(card.get("dims") or derived.dims),
        side=str(card.get("side") or derived.side),
        transition=tuple(transition) if transition else derived.transition,
        risk=str(card.get("risk") or derived.risk),
    )


def build_profiles(
    library: dict[str, Any],
    terms: dict[str, Any],
    cards: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, EntryProfile]:
    """为问题库中的每条标准问题建立画像（有指标卡时优先用卡）。"""
    cards = cards or {}
    profiles: dict[str, EntryProfile] = {}
    for entry in library.get("entries", []):
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        card = cards.get(entry_id)
        # 指标卡与问句文本对不上，说明问题库改过但卡没刷新：回退自动推导，避免吃旧口径。
        if isinstance(card, dict) and str(card.get("question") or "") == str(entry.get("question") or ""):
            profiles[entry_id] = _profile_from_card(card, entry, terms)
        else:
            profiles[entry_id] = profile_entry(entry, terms)
    return profiles


def profile_from_dict(data: dict[str, Any]) -> EntryProfile:
    """把指标卡/事件里的字典还原成画像（预匹配组装口径卡时用）。"""
    transition = data.get("transition")
    return EntryProfile(
        subjects=set(data.get("subjects") or []),
        filters=set(data.get("filters") or []),
        aggs=set(data.get("aggs") or []),
        dims=set(data.get("dims") or []),
        side=str(data.get("side") or ""),
        transition=tuple(transition) if transition else None,
        risk=str(data.get("risk") or "low"),
    )


# ---------------------------------------------------------------- 打分


def _idf(profiles: dict[str, EntryProfile]) -> dict[str, float]:
    """按条目出现频次计算主体权重：永农/红线等稀有主体权重更高。"""
    total = max(len(profiles), 1)
    counts: dict[str, int] = {}
    for profile in profiles.values():
        for subject in profile.subjects | profile.filters:
            counts[subject] = counts.get(subject, 0) + 1
    weights = {
        item: math.log((total + 1) / (count + 0.5))
        for item, count in counts.items()
    }
    for generic in GENERIC_SUBJECTS:
        if generic in weights:
            weights[generic] *= 0.15
    weights["patch"] = 0.05
    return weights


def _subject_score(query: QuerySlots, profile: EntryProfile, idf: dict[str, float]) -> float:
    asked = query.subjects
    if not asked:
        return 0.5
    hit = asked & profile.subjects
    numerator = sum(idf.get(item, 1.0) for item in hit)
    denominator = sum(idf.get(item, 1.0) for item in asked)
    score = numerator / denominator if denominator else 0.0
    if asked & {"land_class"} and not (asked & SPECIFIC_SUBJECTS):
        extra = (profile.subjects & SPECIFIC_SUBJECTS) - asked
        if extra:
            score *= 0.55
    if (asked & SPECIFIC_SUBJECTS) and not (profile.subjects & SPECIFIC_SUBJECTS) and not query.transition:
        score *= 0.4
    extras = (profile.subjects & SPECIFIC_SUBJECTS) - asked
    if extras:
        score *= max(0.35, 1.0 - 0.18 * len(extras))
    return max(0.0, min(1.0, score))


def _filter_score(query: QuerySlots, profile: EntryProfile) -> float:
    if not query.filters:
        return 0.6
    hit = query.filters & profile.filters
    return len(hit) / len(query.filters) if hit else 0.0


def _side_score(query: QuerySlots, profile: EntryProfile) -> float:
    if query.transition:
        if profile.transition == query.transition:
            return 1.0
        if profile.transition:
            return 0.0
        return 0.35
    if query.side == "flow":
        return 1.0 if profile.side == "flow" or profile.aggs & {"flow"} else 0.4
    if query.side and profile.side and query.side in {"before", "after"} and profile.side in {"before", "after"}:
        return 1.0 if query.side == profile.side else 0.0
    return 0.6


def _agg_score(query: QuerySlots, profile: EntryProfile) -> float:
    asked = set(query.aggs)
    if not asked:
        return 0.5
    if "max" in asked and "top" in profile.aggs:
        asked = (asked - {"max"}) | {"top"}
    hit = asked & profile.aggs
    score = len(hit) / len(asked)
    penalties = {
        "ratio": ("ratio", 0.3),
        "list": ("list", 0.3),
        "flow": ("flow", 0.3),
        "activity": ("activity", 0.3),
        "source": ("source", 0.25),
        "target": ("target", 0.25),
    }
    for ask, (need, amount) in penalties.items():
        if ask in asked and need not in profile.aggs:
            score -= amount
    if not (asked & {"structure", "source"}) and profile.aggs & {"structure", "source"}:
        score -= 0.12
    if "ratio" in asked and profile.aggs & {"source", "structure"}:
        score -= 0.15
    return max(0.0, min(1.0, score))


def _dim_score(query: QuerySlots, profile: EntryProfile) -> float:
    asked = query.dims
    if not asked:
        return 0.6
    hit = asked & profile.dims
    score = len(hit) / len(asked)
    if "county" in asked and "county" not in profile.dims:
        score = max(0.0, score - 0.7)
    if "type" in asked and "type" not in profile.dims:
        score = max(0.0, score - 0.3)
    return score


def _score_entry(
    query: QuerySlots,
    profile: EntryProfile,
    lexical: float,
    idf: dict[str, float],
) -> tuple[float, dict[str, float]]:
    components = {
        "subject": _subject_score(query, profile, idf),
        "filter": _filter_score(query, profile),
        "side": _side_score(query, profile),
        "agg": _agg_score(query, profile),
        "dim": _dim_score(query, profile),
        "lexical": lexical,
    }
    semantic = (
        0.33 * components["subject"]
        + 0.19 * components["filter"]
        + 0.14 * components["side"]
        + 0.13 * components["agg"]
        + 0.15 * components["dim"]
        + 0.06 * components["lexical"]
    )
    score = 0.50 * components["lexical"] + 0.50 * semantic
    if components["filter"] == 0 and query.filters:
        score *= 0.15
    if components["subject"] == 0:
        score *= 0.2
    if (
        query.side in {"before", "after"}
        and profile.side in {"before", "after"}
        and query.side != profile.side
    ):
        score *= 0.3
    if "county" in query.dims and "county" not in profile.dims:
        score *= 0.9
    if "county" not in query.dims and "county" in profile.dims:
        score *= 0.93
    return score, components


def _labels(terms: dict[str, Any], section: str, ids: set[str]) -> list[str]:
    mapping = terms.get(section) or {}
    return [str((mapping.get(item) or {}).get("label") or item) for item in sorted(ids)]


def _reasons(
    query: QuerySlots,
    profile: EntryProfile,
    components: dict[str, float],
    terms: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    subjects = _labels(terms, "subjects", query.subjects & profile.subjects)
    if subjects:
        reasons.append("主体：" + "、".join(subjects))
    filters = _labels(terms, "filters", query.filters & profile.filters)
    if filters:
        reasons.append("限定：" + "、".join(filters))
    if query.transition and profile.transition == query.transition:
        reasons.append("方向：" + "→".join(query.transition))
    elif query.side and query.side == profile.side:
        reasons.append("时项：" + {"before": "变化前", "after": "变化后", "flow": "流入流出"}.get(query.side, query.side))
    aggs = _labels(terms, "aggregations", query.aggs & profile.aggs)
    if aggs:
        reasons.append("聚合：" + "、".join(aggs))
    dims = _labels(terms, "dimensions", query.dims & profile.dims)
    if dims:
        reasons.append("维度：" + "、".join(dims))
    reasons.append(f"字面分：{components['lexical']:.2f}")
    return reasons


def _best_risk(profile: EntryProfile) -> str:
    return profile.risk or "low"


def calibration_feature_row(
    candidate: dict[str, Any],
    query: QuerySlots,
    margin: float,
    top_id: str,
) -> dict[str, float]:
    """把一条候选压成校准特征（训练脚本与运行时共用）。"""
    components = (candidate.get("semantic") or {}).get("components") or {}
    return {
        "score": float(candidate.get("score") or 0.0),
        "lexical": float(components.get("lexical") or 0.0),
        "subject": float(components.get("subject") or 0.0),
        "filter": float(components.get("filter") or 0.0),
        "side": float(components.get("side") or 0.0),
        "agg": float(components.get("agg") or 0.0),
        "dim": float(components.get("dim") or 0.0),
        "vector": float(components.get("vector") or 0.0),
        "rrf": float(components.get("rrf") or 0.0),
        "risk_high": 1.0 if candidate.get("risk") == "high" else 0.0,
        "risk_medium": 1.0 if candidate.get("risk") == "medium" else 0.0,
        "is_top1": 1.0 if str(candidate.get("id")) == str(top_id) else 0.0,
        "margin": float(margin or 0.0),
        "adversarial": 1.0 if query.adversarial else 0.0,
        "vague": 1.0 if query.vague else 0.0,
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def apply_calibration(
    scored: list[dict[str, Any]],
    query: QuerySlots,
    margin: float,
    top_id: str,
    calibration: dict[str, Any],
) -> bool:
    """给每条候选计算 p(correct)；返回是否成功应用校准。"""
    features = calibration.get("features") or []
    weights = calibration.get("weights") or []
    means = calibration.get("means") or {}
    scales = calibration.get("scales") or {}
    intercept = float(calibration.get("intercept") or 0.0)
    if not features or len(features) != len(weights):
        return False
    if calibration.get("requires_vector"):
        has_vector = any(float((item.get("semantic") or {}).get("components", {}).get("vector") or 0) > 0 for item in scored)
        if not has_vector:
            return False
    for item in scored:
        row = calibration_feature_row(item, query, margin, top_id)
        z = intercept
        for name, weight in zip(features, weights):
            mean = float(means.get(name, 0.0))
            scale = float(scales.get(name, 1.0)) or 1.0
            z += float(weight) * ((row.get(name, 0.0) - mean) / scale)
        probability = max(0.0, min(1.0, _sigmoid(z)))
        item["p_correct"] = round(probability, 4)
        item["confidence"] = item["p_correct"]
        item["calibrated"] = True
    return True


def semantic_match(
    question: str,
    library: dict[str, Any],
    *,
    top_k: int = 3,
    min_confidence: float = 0.42,
    skills_dir: Optional[str | Path] = None,
    vector_scores: Optional[dict[str, float]] = None,
    vector_min_similarity: float = VECTOR_MIN_SIMILARITY,
    vector_weight: float = VECTOR_WEIGHT,
) -> dict[str, Any]:
    """语义匹配入口：返回同 ``match_question`` 兼容的结构，并带决策/槽位/风险。

    ``vector_scores`` 是“问题 ↔ 标准问题”的 embedding 余弦相似度（可选）。
    传入后与词法排名做 RRF，作为有界加分；不传则完全走规则匹配（离线可用）。
    """
    terms_path = resolve_asset_path("terms.json", skills_dir)
    terms = load_terms(str(terms_path))
    cards_path = resolve_asset_path("metric_cards.json", skills_dir)
    cards = load_metric_cards(str(cards_path))
    profiles = build_profiles(library, terms, cards)
    idf = _idf(profiles)
    query = understand(question, terms)

    scored: list[dict[str, Any]] = []
    for entry in library.get("entries", []):
        entry_id = str(entry.get("id") or "")
        profile = profiles.get(entry_id)
        if profile is None:
            continue
        lexical = float((_lexical_candidate(question, entry) or {}).get("confidence", 0.0))
        score, components = _score_entry(query, profile, lexical, idf)
        compatible = not (
            (query.filters and not (query.filters & profile.filters))
            or (query.transition and profile.transition and query.transition != profile.transition)
            or ((query.aggs & UNSUPPORTED_AGGS) and not (profile.aggs & query.aggs))
            or query.unsupported
        )
        scored.append(
            {
                "id": entry_id,
                "question": entry.get("question"),
                "intent": entry.get("intent"),
                "strategy": entry.get("strategy"),
                "tool": entry.get("tool") or None,
                "unit": entry.get("unit"),
                "score": round(score, 4),
                "confidence": round(score, 4),
                "match_reasons": _reasons(query, profile, components, terms),
                "compatible": compatible,
                "risk": _best_risk(profile),
                "side": profile.side,
                "transition": list(profile.transition) if profile.transition else None,
                "semantic": {
                    "components": {key: round(value, 4) for key, value in components.items()},
                    "profile": profile.to_dict(),
                },
            }
        )
    vector_top: list[str] = []
    vector_applied = False
    if vector_scores:
        # 词法排名与向量排名做 RRF；向量只作为有界加分，不替代槽位分。
        lexical_order = sorted(scored, key=lambda item: (-item["score"], str(item["id"])))
        lexical_rank = {item["id"]: index for index, item in enumerate(lexical_order)}
        vector_pairs = sorted(
            (
                (str(entry_id), float(similarity))
                for entry_id, similarity in vector_scores.items()
                if float(similarity) >= vector_min_similarity
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )
        vector_top = [entry_id for entry_id, _ in vector_pairs[:3]]
        vector_rank = {entry_id: index for index, (entry_id, _) in enumerate(vector_pairs)}
        max_rrf = 2.0 / (RRF_K + 1)
        for item in scored:
            entry_id = str(item["id"])
            if entry_id not in vector_rank:
                continue
            rrf = (
                1.0 / (RRF_K + lexical_rank[entry_id] + 1)
                + 1.0 / (RRF_K + vector_rank[entry_id] + 1)
            )
            normalized = min(1.0, rrf / max_rrf)
            similarity = float(vector_scores.get(entry_id, 0.0))
            item["score"] = round(min(1.0, item["score"] + vector_weight * normalized), 4)
            item["confidence"] = item["score"]
            item["semantic"]["components"]["vector"] = round(similarity, 4)
            item["semantic"]["components"]["rrf"] = round(normalized, 4)
            item["match_reasons"].append(
                f"向量召回：相似度 {similarity:.2f}，RRF {normalized:.2f}"
            )
            vector_applied = True
    scored.sort(key=lambda item: (-item["score"], str(item["id"])))
    top_id = str(scored[0]["id"]) if scored else ""
    top_score = scored[0]["score"] if scored else 0.0
    second_score = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = round(top_score - second_score, 4) if scored else 0.0

    calibration = load_calibration(str(resolve_asset_path(CALIBRATION_FILENAME, skills_dir)))
    calibrated = False
    if calibration and scored:
        calibrated = apply_calibration(scored, query, margin, top_id, calibration)
    if not calibrated:
        for item in scored:
            item["p_correct"] = item["score"]
            item["calibrated"] = False
            item["confidence"] = item["score"]

    candidates = scored[:top_k]
    best = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None

    decision_cfg = terms.get("decision") or {}
    thresholds = decision_cfg.get("score_thresholds") or {}
    candidate_margin = float(decision_cfg.get("candidate_margin", 0.05))
    candidate_floor = float(decision_cfg.get("candidate_floor", 0.36))
    effective_min = max(float(min_confidence), float(decision_cfg.get("minimum_confidence", 0.42)))
    risk = best["risk"] if best else "low"
    execute_threshold = max(effective_min, float(thresholds.get(risk, 0.42)))

    decision = "fallback"
    notice = ""
    if best is not None and best["compatible"]:
        if query.adversarial:
            decision = "clarify"
            notice = "问题里包含与库内口径冲突的要求，需先确认，不能按该要求直接编造或改变口径。"
        elif query.vague and margin < candidate_margin:
            decision = "candidates"
            notice = "问题比较笼统，最接近的标准问题不止一个，请先选择口径。"
        elif best["score"] >= execute_threshold and (
            not calibrated or float(best.get("p_correct") or 0.0) >= CALIBRATION_MIN_P
        ):
            decision = "execute"
        elif best["score"] >= execute_threshold:
            decision = "clarify"
            notice = "匹配分达到阈值，但校准后的 p(correct) 不足，先确认口径再查库。"
        elif best["score"] >= candidate_floor and margin < candidate_margin:
            decision = "candidates"
            notice = "没有足够把握直接执行，以下是最接近的标准问题。"
        elif best["score"] >= candidate_floor:
            decision = "clarify"
            notice = "最接近的标准问题匹配分不足，先确认口径再查库。"
        else:
            decision = "fallback"
            notice = "未找到可靠匹配的标准问题，转自由 SQL。"
    elif best is not None:
        notice = "最接近的条目与问题限定条件冲突，未自动执行。"

    status = "unmatched"
    if best is not None:
        if best["compatible"] and best["score"] >= effective_min:
            status = "candidate_found"
        elif best["score"] >= max(0.3, effective_min * 0.7):
            status = "needs_review"

    return {
        "status": status,
        "decision": decision,
        "matched_question_id": best["id"] if best else None,
        "confidence": best["confidence"] if best else None,
        "p_correct": best.get("p_correct") if best else None,
        "calibrated": calibrated,
        "score": best["score"] if best else None,
        "margin": margin,
        "risk": risk,
        "execute_threshold": round(execute_threshold, 4),
        "p_threshold": CALIBRATION_MIN_P if calibrated else None,
        "match_reasons": best["match_reasons"] if best else [],
        "slots": query.to_dict(),
        "slot_labels": {
            "subjects": _labels(terms, "subjects", query.subjects),
            "filters": _labels(terms, "filters", query.filters),
            "aggregations": _labels(terms, "aggregations", query.aggs),
            "dimensions": _labels(terms, "dimensions", query.dims),
        },
        "candidates": candidates,
        "notice": notice,
        "terms_version": terms.get("version"),
        "vector_used": vector_applied,
        "vector_top": vector_top,
    }


# ---------------------------------------------------------------- 口径卡


async def semantic_match_enriched(
    question: str,
    library: dict[str, Any],
    *,
    embedder: Any = None,
    skills_dir: Optional[str | Path] = None,
    top_k: int = 3,
    min_confidence: float = 0.42,
    vector_min_similarity: float = VECTOR_MIN_SIMILARITY,
    vector_weight: float = VECTOR_WEIGHT,
    llm: Any = None,
    model: str = "",
    judge: bool = False,
) -> dict[str, Any]:
    """带向量召回（以及可选 LLM judge）的语义匹配（预匹配主链路使用）。

    - 有可用 embedder 时，先算“问题 ↔ 标准问题”的余弦相似度，再与词法排名做 RRF；
    - embedding 不可用/超时/报错时，静默回退到纯规则匹配，不让问答链路失败；
    - ``judge=True`` 时只在 candidates/clarify 灰度区调一次 LLM 复核，execute 不调。
    """
    vector_map: dict[str, float] = {}
    attempted = embedder is not None and bool(getattr(embedder, "available", False))
    if attempted:
        try:
            from .tools.qa_library import vector_scores as _vector_scores

            vector_map = await _vector_scores(
                question,
                skills_dir=skills_dir,
                embedder=embedder,
                min_similarity=vector_min_similarity,
            )
        except Exception:
            vector_map = {}
    result = semantic_match(
        question,
        library,
        top_k=top_k,
        min_confidence=min_confidence,
        skills_dir=skills_dir,
        vector_scores=vector_map or None,
        vector_min_similarity=vector_min_similarity,
        vector_weight=vector_weight,
    )
    result["vector_attempted"] = attempted
    result["vector_entries"] = len(vector_map)

    result["judge_used"] = False
    result["judge"] = None
    if (
        judge
        and llm is not None
        and result.get("decision") in {"candidates", "clarify"}
        and not (result.get("slots") or {}).get("adversarial")
    ):
        from .qa_judge import judge_match

        verdict = await judge_match(
            llm,
            model or "",
            question,
            result.get("candidates") or [],
            result.get("slot_labels") or {},
        )
        if verdict:
            result["judge_used"] = True
            result["judge"] = verdict
            chosen = str(verdict.get("entry_id") or "none")
            top_id = str(result.get("matched_question_id") or "")
            if chosen == "none":
                result["decision"] = "clarify"
                result["notice"] = "LLM 复核认为候选都不够匹配，先确认口径。"
            elif chosen != top_id:
                result["decision"] = "candidates"
                result["notice"] = "LLM 复核更倾向于另一个候选，请先确认口径。"
    return result


def caliber_card(
    entry: dict[str, Any],
    profile: EntryProfile,
    *,
    period: Any = None,
    arguments: Optional[dict[str, Any]] = None,
    terms: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """生成回答前的口径卡：指标、口径、期次、范围、单位、风险。"""
    terms = terms or load_terms(str(resolve_asset_path("terms.json")))
    arguments = arguments or {}
    return {
        "metric_id": entry.get("id"),
        "metric": entry.get("question"),
        "caliber": entry.get("answer_hint") or entry.get("intent") or "",
        "subjects": _labels(terms, "subjects", profile.subjects),
        "filters": _labels(terms, "filters", profile.filters),
        "dimensions": _labels(terms, "dimensions", profile.dims),
        "side": profile.side,
        "risk": profile.risk,
        "period_id": getattr(period, "id", "") or "",
        "period": getattr(period, "label", "") or "",
        "table": getattr(period, "table", "") or "",
        "data_version": getattr(period, "version", "") or "",
        "unit": str(entry.get("unit") or "亩"),
        "scope": str(arguments.get("county") or "全库"),
    }


# ---------------------------------------------------------------- 评测辅助


def strip_overlapping_aliases(
    library: dict[str, Any],
    entry_id: str,
    text: str,
) -> dict[str, Any]:
    """复制问题库并删除目标条目中与给定问法互为子串的别名（留一别名评测）。"""
    clone = copy.deepcopy(library)
    normalized = "".join(
        char for char in str(text).lower()
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
    for entry in clone.get("entries", []):
        if str(entry.get("id")) != str(entry_id):
            continue
        kept: list[str] = []
        for alias in entry.get("aliases") or []:
            alias_text = "".join(
                char for char in str(alias).lower()
                if char.isalnum() or "\u4e00" <= char <= "\u9fff"
            )
            if alias_text and (alias_text in normalized or normalized in alias_text):
                continue
            kept.append(alias)
        entry["aliases"] = kept
    return clone
