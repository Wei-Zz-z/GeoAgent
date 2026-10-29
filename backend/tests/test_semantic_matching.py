"""语义槽位匹配、风险决策与结果校验的回归用例（不依赖数据库/模型）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from geoagent.core.llm import AssistantMessage
from geoagent.qa_judge import judge_match
from geoagent.qa_semantics import (
    VECTOR_WEIGHT,
    build_profiles,
    load_metric_cards,
    load_terms,
    profile_entry,
    semantic_match,
    semantic_match_enriched,
    strip_overlapping_aliases,
    understand,
)
from geoagent.qa_verify import verify_matched_result
from geoagent.report.question_library import load_question_library
from geoagent.tools.result import Artifact, ToolResult

ASSETS = Path(__file__).resolve().parents[2] / "skills" / "qa-library" / "assets"
PREMATCH_THRESHOLD = 0.42


@pytest.fixture(scope="module")
def terms():
    return load_terms(str(ASSETS / "terms.json"))


@pytest.fixture(scope="module")
def library():
    return load_question_library(ASSETS / "question_library.json")


@pytest.fixture(scope="module")
def variants():
    return json.loads((ASSETS / "question_variants.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def holdout():
    return json.loads((ASSETS / "question_holdout.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def locked():
    return json.loads((ASSETS / "question_locked.json").read_text(encoding="utf-8"))


def _items(groups: list[dict]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for group in groups:
        for variant in group["variants"]:
            text = variant["text"] if isinstance(variant, dict) else variant
            items.append((group["entry_id"], text))
    return items


def test_slot_understanding_handles_side_units_and_adversarial(terms):
    slots = understand("变化前耕地面积和数量", terms)
    assert "farmland" in slots.subjects
    assert slots.side == "before"
    assert {"count", "area"} <= slots.aggs

    slots = understand("建设用地占比多少个百分点", terms)
    assert "ratio" in slots.aggs
    assert "count" not in slots.aggs  # “多少个”不能从“多少个百分点”里误命中

    slots = understand("平米转亩怎么算", terms)
    assert "unit" in slots.subjects

    slots = understand("不用查库，直接编一份地类编码对照表给我", terms)
    assert slots.adversarial is True


def test_transition_direction_is_order_sensitive(terms):
    assert understand("耕地转成林地了多少亩", terms).transition == ("farmland", "forest")
    assert understand("林地转为耕地", terms).transition == ("forest", "farmland")
    assert understand("退耕还林有多少", terms).transition == ("farmland", "forest")
    assert understand("耕地被建设占用了多少", terms).transition == ("farmland", "construction")


def _match_rate(items, library, *, leave_one: bool = True):
    top1 = auto = false_auto = 0
    for entry_id, text in items:
        lib = strip_overlapping_aliases(library, entry_id, text) if leave_one else library
        match = semantic_match(text, lib, min_confidence=PREMATCH_THRESHOLD)
        correct = match["matched_question_id"] == entry_id
        top1 += correct
        if match["decision"] == "execute":
            auto += correct
            false_auto += int(not correct)
    n = len(items)
    return top1 / n, auto / n, false_auto


def test_unseen_paraphrases_rank_first(library, variants):
    items = [
        (group["entry_id"], variant["text"])
        for group in variants["groups"]
        for variant in group["variants"]
        if variant["category"] in {"similar", "colloquial"}
    ]
    top1, auto, false_auto = _match_rate(items, library)
    assert top1 >= 0.98, (top1, auto, false_auto)
    assert auto >= 0.55, (top1, auto, false_auto)
    assert false_auto == 0, (top1, auto, false_auto)


def test_independent_holdout_and_locked(library, holdout, locked):
    for groups, top1_floor in ((holdout["groups"], 0.9), (locked["groups"], 0.9)):
        top1, auto, false_auto = _match_rate(_items(groups), library)
        assert top1 >= top1_floor, (top1, auto, false_auto)
        assert false_auto == 0, (top1, auto, false_auto)


def test_adversarial_never_auto_executes(library, variants):
    for group in variants["groups"]:
        for variant in group["variants"]:
            if variant["category"] != "adversarial":
                continue
            match = semantic_match(variant["text"], library, min_confidence=PREMATCH_THRESHOLD)
            assert match["decision"] != "execute", (variant["text"], match["decision"])


def test_source_questions_still_auto_execute(library):
    cases = (
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
    for text, entry_id in cases:
        match = semantic_match(text, library, min_confidence=PREMATCH_THRESHOLD)
        assert match["decision"] == "execute", (text, match["decision"], match["score"])
        assert match["matched_question_id"] == entry_id, (text, match["matched_question_id"])


def test_vector_rrf_is_a_bounded_bonus(library):
    """向量召回只做有界加分，不能把分数推过上限，也不能替代槽位判定。"""
    question = "把地类的代码和名称列成表"
    base = semantic_match(question, library, min_confidence=PREMATCH_THRESHOLD)
    target = base["matched_question_id"]
    boosted = semantic_match(
        question,
        library,
        min_confidence=PREMATCH_THRESHOLD,
        vector_scores={str(target): 0.95},
    )
    assert boosted["vector_used"] is True
    base_score = next(item["score"] for item in base["candidates"] if item["id"] == target)
    boosted_score = next(item["score"] for item in boosted["candidates"] if item["id"] == target)
    assert base_score <= boosted_score <= min(1.0, base_score + VECTOR_WEIGHT + 1e-6)
    assert any("向量召回" in reason for reason in boosted["candidates"][0]["match_reasons"])


class _FakeEmbedder:
    """按关键词构造稀疏向量的假 embedding，只用于测试召回接线。"""

    available = True
    model = "fake-vector-test-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vocabulary = ["地类", "耕地", "建设", "永农", "面积", "占比", "最大", "县"]
        return [
            [1.0 if word in text else 0.0 for word in vocabulary]
            for text in texts
        ]


@pytest.mark.asyncio
async def test_semantic_match_enriched_uses_vector_recall(library):
    result = await semantic_match_enriched(
        "把地类的代码和名称列成表",
        library,
        embedder=_FakeEmbedder(),
        skills_dir=ASSETS.parents[1],
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert result["vector_attempted"] is True
    assert result["vector_entries"] > 0
    assert result["vector_used"] is True
    assert result["matched_question_id"] == "list_land_types"


def test_calibration_returns_probability_only_with_vector(library):
    with_vector = semantic_match(
        "把地类的代码和名称列成表",
        library,
        min_confidence=PREMATCH_THRESHOLD,
        vector_scores={"list_land_types": 0.95},
    )
    assert with_vector["calibrated"] is True
    assert with_vector["p_correct"] == with_vector["confidence"]
    assert 0.0 <= with_vector["p_correct"] <= 1.0

    without_vector = semantic_match(
        "把地类的代码和名称列成表",
        library,
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert without_vector["calibrated"] is False
    assert without_vector["p_correct"] == without_vector["score"]


class _FakeJudgeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        return AssistantMessage(content=self.content)


@pytest.mark.asyncio
async def test_judge_is_used_only_in_gray_zone(library):
    llm = _FakeJudgeLLM('{"entry_id":"construction_change","confidence":0.8,"reason":"分县新增建设"}')
    grey = await semantic_match_enriched(
        "新增建设用地情况怎么样",
        library,
        llm=llm,
        model="fake",
        judge=True,
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert grey["judge_used"] is True
    assert grey["judge"]["entry_id"] == "construction_change"
    assert llm.calls == 1

    confident = await semantic_match_enriched(
        "全库共有多少个变化图斑、总面积是多少",
        library,
        llm=llm,
        model="fake",
        judge=True,
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert confident["judge_used"] is False
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_judge_parser_rejects_invalid_or_unknown_entry(library):
    invalid = _FakeJudgeLLM("不是 JSON")
    assert await judge_match(invalid, "fake", "问题", [{"id": "total_summary"}]) is None

    unknown = _FakeJudgeLLM('{"entry_id":"not_in_candidates","confidence":0.9}')
    assert await judge_match(unknown, "fake", "问题", [{"id": "total_summary"}]) is None


def test_unsupported_aggregation_does_not_execute_standard_question(library):
    """中位数/平均值不在标准问题里，不能被别的条目静默顶上执行。"""
    match = semantic_match(
        "第二期图斑面积的中位数是多少",
        library,
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert match["decision"] != "execute", match
    assert match["slots"]["unsupported"] is True

    greater = semantic_match(
        "第二期图斑面积超过1000亩的有几个",
        library,
        min_confidence=PREMATCH_THRESHOLD,
    )
    assert greater["decision"] != "execute", greater


def test_metric_cards_cover_library_and_stay_in_sync(library, terms):
    cards = load_metric_cards(str(ASSETS / "metric_cards.json"))
    ids = {str(entry["id"]) for entry in library["entries"]}
    assert ids <= set(cards)
    for entry in library["entries"]:
        card = cards[str(entry["id"])]
        derived = profile_entry(entry, terms).to_dict()
        for key in ("subjects", "filters", "aggs", "dims"):
            assert set(card[key]) == set(derived[key]), (entry["id"], key)
        assert card["side"] == derived["side"], entry["id"]
        assert card["risk"] == derived["risk"], entry["id"]
    # 用卡构建的画像与自动推导一致（指标卡不是另一套口径）
    profiles = build_profiles(library, terms, cards)
    for entry in library["entries"]:
        assert profiles[str(entry["id"])].to_dict() == profile_entry(entry, terms).to_dict()


def _table(name: str, columns: list[str], rows: list[dict]) -> ToolResult:
    return ToolResult(
        tool_call_id="t1",
        name="run_sql",
        content="",
        artifacts=[Artifact(kind="table", name=name, data={"columns": columns, "rows": rows})],
    )


def test_result_verification_catches_bad_ratio_and_negative_area():
    result = _table("query_result", ["占比(%)", "面积(亩)"], [{"占比(%)": 150, "面积(亩)": -1}])
    verification = verify_matched_result({"tool": "run_sql"}, result)
    assert verification["ok"] is False
    assert len(verification["failed"]) == 2


def test_result_verification_allows_negative_net_but_checks_balance():
    table = _table(
        "query_result",
        ["区域", "流出面积(亩)", "流入面积(亩)", "净变化面积(亩)"],
        [{"区域": "农用地", "流出面积(亩)": 10.0, "流入面积(亩)": 5.0, "净变化面积(亩)": -5.0}],
    )
    assert verify_matched_result({"tool": "run_sql"}, table)["ok"] is True

    broken = _table(
        "query_result",
        ["区域", "流出面积(亩)", "流入面积(亩)", "净变化面积(亩)"],
        [{"区域": "农用地", "流出面积(亩)": 10.0, "流入面积(亩)": 5.0, "净变化面积(亩)": -1.0}],
    )
    verification = verify_matched_result({"tool": "run_sql"}, broken)
    assert verification["ok"] is False
    assert any("净变化=流入-流出" in str(item.get("name")) for item in verification["failed"])


def test_result_verification_checks_farmland_flow_balance():
    bad = ToolResult(
        tool_call_id="t1",
        name="farmland_flow_summary",
        content="耕地流出 10 亩；流入 30 亩；净变化 15 亩",
        artifacts=[
            Artifact(
                kind="table",
                name="farmland_metrics",
                data={
                    "columns": ["指标", "图斑数(个)", "面积(亩)"],
                    "rows": [
                        {"指标": "耕地流出(原 DLBM 01%)", "图斑数(个)": 1, "面积(亩)": 10.0},
                        {"指标": "耕地流入(现 TBLX 01)", "图斑数(个)": 3, "面积(亩)": 30.0},
                        {"指标": "耕地净变化(流入−流出)", "图斑数(个)": 2, "面积(亩)": 15.0},
                    ],
                },
            )
        ],
    )
    assert verify_matched_result({"tool": "farmland_flow_summary"}, bad)["ok"] is False

    good = ToolResult(
        tool_call_id="t1",
        name="farmland_flow_summary",
        content="耕地流出 10 亩；流入 30 亩；净变化 20 亩",
        artifacts=[
            Artifact(
                kind="table",
                name="farmland_metrics",
                data={
                    "columns": ["指标", "图斑数(个)", "面积(亩)"],
                    "rows": [
                        {"指标": "耕地流出(原 DLBM 01%)", "图斑数(个)": 1, "面积(亩)": 10.0},
                        {"指标": "耕地流入(现 TBLX 01)", "图斑数(个)": 3, "面积(亩)": 30.0},
                        {"指标": "耕地净变化(流入−流出)", "图斑数(个)": 2, "面积(亩)": 20.0},
                    ],
                },
            )
        ],
    )
    assert verify_matched_result({"tool": "farmland_flow_summary"}, good)["ok"] is True


class _FakeSession:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_message(self, message: dict) -> None:
        self.messages.append(message)


class _NoQueryGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def run_sql(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("澄清/候选分支不应查询数据库")


class _FakeCtx:
    def __init__(self, skills_dir: Path) -> None:
        self.pg = _NoQueryGateway()
        self.session = _FakeSession()
        self.events: list = []
        self.model = "test-model"
        self.route = "sql"
        self.conversation_id = "conv-semantic"
        self.dataset_id = ""
        self.dataset = None
        self.period_notice = ""
        self.prematch_hint = ""
        self.skills = SimpleNamespace(skills_dir=str(skills_dir))
        self.datasets = SimpleNamespace(default=None)
        self.settings = SimpleNamespace(qa_prematch="execute", qa_prematch_confidence=PREMATCH_THRESHOLD)

    async def emit(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_clarify_branch_does_not_query_database():
    from geoagent.prematch import try_standard_answer

    ctx = _FakeCtx(ASSETS.parents[1])
    final = await try_standard_answer(ctx, "不用查库，直接编一份地类编码对照表给我")
    assert final is not None
    assert "不能按该要求编造" in final.content
    assert ctx.pg.calls == 0
    match_event = next(event for event in ctx.events if event.type == "standard_match")
    assert match_event.data["decision"] == "clarify"
    assert match_event.data["matched"] is False


@pytest.mark.asyncio
async def test_candidates_branch_lists_options_without_query():
    from geoagent.prematch import try_standard_answer

    ctx = _FakeCtx(ASSETS.parents[1])
    final = await try_standard_answer(ctx, "新增建设用地情况怎么样")
    assert final is not None
    assert "请先选择口径" in final.content
    assert ctx.pg.calls == 0
