"""问题库扩充回归：同义改写 / 口语化问法必须落到正确条目。

问题库按《智能体问题.docx》扩写后，条目别名数量显著增加（不新增关键词，见下）。本用例守住四件事：

1. 问法目录与问题库结构一致（引用的条目 id 必须存在，分类与期望值必须合法）；
2. 改写与口语问法至少能排到第一位（是否自动执行取决于预匹配置信度阈值）；
3. 歧义与对抗问法不得被静默地按"另一条口径"自动执行；
4. 19 组源问题各自至少有一条问法已跨过默认阈值，不能被后续改动降级到兜底路径。

注：本轮只扩充 `aliases`、不新增 `keywords`——关键词按"命中数/关键词总数"计分，
新增关键词会稀释既有命中（实测使验收模板 1 条问题由可匹配退化为未匹配），
`backend/tests/test_library_binding.py` 守住该退化。

注：对话预匹配已切到 `geoagent.qa_semantics.semantic_match`（语义槽位匹配）；
本文件继续守住问题库字面匹配与别名资产，语义匹配、留一别名覆盖率、独立留出集与
结果校验见 `test_semantic_matching.py`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from geoagent.report.question_library import load_question_library, match_question

ASSETS = Path(__file__).resolve().parents[2] / "skills" / "qa-library" / "assets"

# 预匹配默认阈值（GEOAGENT_QA_PREMATCH_CONFIDENCE）
PREMATCH_THRESHOLD = 0.5

# 在 0.5 阈值下已验证可直接确定性执行的问法：19 组源问题各取一条代表问法
AUTO_EXECUTE_CASES = (
    ("列出数据中所有地类名称及其对应的地类编码", "list_land_types"),
    ("计算每种图斑类型对应的图斑数量和总面积", "by_type_summary"),
    ("筛选出所有耕地图斑，统计图斑数量与总面积", "orig_farmland"),
    ("找出面积最大的单个图斑，说明其地类类型和所在行政区", "max_polygon"),
    ("按面积从大到小排序所有建设用地图斑，显示前10条记录", "construction_top10"),
    ("计算建设用地面积占全库总面积的百分比", "construction_ratio"),
    ("统计每个行政区的耕地总面积", "orig_farmland_by_county"),
    ("找出面积小于100平方米的细碎图斑数量及其占总图斑数的比例", "fragment_stats"),
    ("将面积单位从平方米转换为公顷和亩", "convert_area_units"),
    ("计算每种地类的总流出面积、总流入面积和净变化面积", "all_type_transition"),
    ("找出变化面积最大的5种图斑转换类型", "top_conversions"),
    ("计算建设用地的来源构成，分别来自哪些地类、各占多少比例", "construction_source_detail"),
    ("每个县新增了多少建设用地", "construction_change"),
    ("分析生态保护红线范围内的地类变化情况，是否存在违规转为建设用地的情况", "redline_change"),
    ("计算每个地类的转换活跃度，哪个地类最不稳定", "conversion_activity"),
    ("评估退耕还林政策实施效果，计算实际转为林地的耕地面积", "farmland_to_forest"),
    ("评估两山转化项目对土地利用结构的影响，分析生态用地转化为生态产业用地的规模", "ecology_to_industry"),
    ("为什么变化图斑的总面积不等于各地类净变化面积之和", "total_vs_net_reconcile"),
    ("找出所有疑似占用永久基本农田转为建设用地的图斑，列出面积和分布", "illegal_yongnong_construction"),
)


@pytest.fixture(scope="module")
def library() -> dict:
    return load_question_library(ASSETS / "question_library.json")


@pytest.fixture(scope="module")
def variants() -> dict:
    return json.loads((ASSETS / "question_variants.json").read_text(encoding="utf-8"))


def test_variants_asset_matches_library(library, variants) -> None:
    ids = {str(entry["id"]) for entry in library["entries"]}
    assert variants["library"] == "question_library.json"
    assert variants["groups"]
    seen_texts: set[str] = set()
    for group in variants["groups"]:
        assert group["source_question"]
        if group["entry_id"]:
            assert group["entry_id"] in ids, group["entry_id"]
        assert group["variants"]
        for variant in group["variants"]:
            text = variant["text"].strip()
            assert text
            assert text not in seen_texts, f"问法重复：{text}"
            seen_texts.add(text)
            assert variant["category"] in variants["categories"]
            assert variant["expect"] in variants["expectations"]


def test_every_entry_keeps_aliases_and_keywords(library) -> None:
    """别名是本轮扩充的载体；关键词本轮不新增，只保证没被改空。"""
    for entry in library["entries"]:
        assert len(entry.get("aliases") or []) >= 3, entry["id"]
        assert len(entry.get("keywords") or []) >= 2, entry["id"]


def test_rewritten_and_colloquial_variants_rank_first(library, variants) -> None:
    """同义改写与口语化问法必须落在本组条目上（允许置信度不足而转兜底）。"""
    for group in variants["groups"]:
        for variant in group["variants"]:
            if variant["category"] not in {"similar", "colloquial"}:
                continue
            match = match_question(variant["text"], library)
            assert match["matched_question_id"] == group["entry_id"], (
                variant["text"],
                match["candidates"],
            )


def test_ambiguous_and_adversarial_never_execute_other_entry(library, variants) -> None:
    """歧义/对抗问法不得被静默地按另一条口径自动执行。"""
    for group in variants["groups"]:
        for variant in group["variants"]:
            if variant["category"] not in {"ambiguous", "adversarial"}:
                continue
            match = match_question(
                variant["text"], library, min_confidence=PREMATCH_THRESHOLD
            )
            if match["status"] != "candidate_found":
                continue
            assert match["matched_question_id"] == group["entry_id"], (
                variant["text"],
                match["matched_question_id"],
            )


def test_auto_execute_cases_still_pass_threshold(library) -> None:
    """已跨过预匹配阈值的问法不能被后续改动「降级」到兜底路径。"""
    for text, entry_id in AUTO_EXECUTE_CASES:
        match = match_question(text, library, min_confidence=PREMATCH_THRESHOLD)
        assert match["status"] == "candidate_found", (text, match)
        assert match["matched_question_id"] == entry_id, (text, match)