"""SQLAgent 准确率评估（golden 用例，真实库 + 真实 LLM，仅手动运行）。

用法：
    # 只验证参考 SQL 能否在真实库上跑出期望值（不调用 LLM）
    backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py --refs-only

    # 完整评估：跑 LLM 问答并与参考值比对（需要 OPENAI_API_KEY）
    backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py
    backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py --case 2

覆盖：6 个示例问题的“问题 → 参考 SQL → 期望数值”回归。
口径约定（与 SQLAgent 知识卡一致）：
- 地类名称/编码、耕地、建设用地 无修饰 -> 原土地类型（DLBM/DLMC，三调）
- 图斑类型/变化后 -> TBLX
- 面积 MJ 单位平方米
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from geoagent.agents.sql import SQLAgent  # noqa: E402
from geoagent.config import Settings  # noqa: E402
from geoagent.core.context import ConversationContext  # noqa: E402
from geoagent.core.events import Event  # noqa: E402
from geoagent.core.llm import LLMService  # noqa: E402
from geoagent.memory.memory import NoopMemory  # noqa: E402
from geoagent.memory.session import ConversationSession  # noqa: E402
from geoagent.memory.store import MemoryConversationStore  # noqa: E402
from geoagent.skills import SkillLoader  # noqa: E402
from geoagent.tools.pg import PgGateway  # noqa: E402

TABLE = 'data."2026_1_change_landuse"'

CASES: list[dict[str, Any]] = [
    {
        "id": "1",
        "question": "列出数据中所有的地类名称和对应的地类编码",
        "reference_sql": None,
        "expected_contains": ["水田", "0101", "工业用地", "0601"],
        "requires_sql": False,
    },
    {
        "id": "2",
        "question": "计算每种类型的总面积",
        "reference_sql": (
            f'SELECT t."TBLX", count(*) AS polygon_count, sum(t."MJ") AS area_m2 '
            f"FROM {TABLE} t GROUP BY t.\"TBLX\" ORDER BY area_m2 DESC"
        ),
        "expected": {"total_polygons": 164798, "top_area_m2": 8415206.70},
        "tolerance": 0.005,
        "requires_sql": False,
    },
    {
        "id": "3",
        "question": "筛选出所有\"耕地\"地类的图斑，统计其图斑数量和总面积",
        "reference_sql": (
            f'SELECT count(*) AS polygon_count, sum("MJ") AS area_m2 FROM {TABLE} '
            'WHERE "DLBM" LIKE \'01%\''
        ),
        "expected_numeric_field": "area_m2",
        "tolerance": 0.005,
        "requires_sql": True,
    },
    {
        "id": "3b",
        "question": "统计变化后为耕地的图斑数量和总面积",
        "reference_sql": (
            f'SELECT count(*) AS polygon_count, sum("MJ") AS area_m2 FROM {TABLE} '
            'WHERE "TBLX" = \'01\''
        ),
        "expected_numeric_field": "area_m2",
        "tolerance": 0.005,
        "requires_sql": True,
    },
    {
        "id": "4",
        "question": "找出面积最大的单个图斑，说明其图斑类型和所在行政区",
        "reference_sql": (
            f'SELECT "OBJECTID", "XMC", "DLMC", "TBLX", "MJ" FROM {TABLE} '
            'ORDER BY "MJ" DESC, "OBJECTID" LIMIT 1'
        ),
        "expected": {"max_mj": 200.0},
        "tolerance": 0.001,
        "expected_contains": ["亩"],
        "requires_sql": True,
    },
    {
        "id": "5",
        "question": "按面积从大到小排序所有建设用地图斑，显示前10条记录",
        "reference_sql": (
            f'SELECT "OBJECTID", "XMC", "DLMC", "TBLX", "MJ" FROM {TABLE} '
            'WHERE "DLBM" ~ \'^(05|06|07|08|09|100[1-5]|100[7-9]|1109|1201)\' '
            'ORDER BY "MJ" DESC, "OBJECTID" LIMIT 10'
        ),
        "expected": {"max_mj": 200.0, "row_count": 10},
        "tolerance": 0.001,
        "requires_sql": True,
    },
    {
        "id": "6",
        "question": "计算建设用地占总面积的百分比",
        "reference_sql": (
            f'SELECT sum("MJ") AS jsyd_area_m2, '
            f'(SELECT sum("MJ") FROM {TABLE}) AS total_area_m2 FROM {TABLE} '
            'WHERE "DLBM" ~ \'^(05|06|07|08|09|100[1-5]|100[7-9]|1109|1201)\''
        ),
        "expected_percent": True,
        "tolerance": 0.005,
        "requires_sql": True,
    },
]


def _extract_numbers(text: str) -> list[float]:
    cleaned = text.replace(",", "")
    return [float(m) for m in re.findall(r"\d+(?:\.\d+)?", cleaned)]


def _number_matches(
    answer: str,
    expected: float,
    tolerance: float,
    *,
    unit_from_m2: bool = False,
) -> bool:
    """匹配正文数字；``unit_from_m2`` 时同时接受期望值换算成亩后的数字。

    库内面积是平方米，问答展示统一为亩（1 ㎡ = 0.0015 亩）。golden 参考值来自
    原始 SQL（平方米），因此面积类断言必须同时接受亩，否则会把正确回答判成失败。
    """
    candidates = [expected]
    if unit_from_m2:
        candidates.append(expected * 0.0015)
    for num in _extract_numbers(answer):
        for candidate in candidates:
            if abs(num - candidate) / max(abs(candidate), 1e-9) <= tolerance:
                return True
    return False


class EventCollector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def sink(self, event: Event) -> None:
        self.events.append(event.to_dict())

    def tool_names(self) -> list[str]:
        return [e.get("name", "") for e in self.events if e.get("type") == "tool_call"]


async def compute_reference(gw: PgGateway, case: dict[str, Any]) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    sql = case.get("reference_sql")
    if sql:
        result = await gw.run_sql(sql, conversation_id="eval")
        rows = result["rows"]
        ref["rows"] = rows
        if case.get("expected_percent"):
            first = rows[0]
            ref["percent"] = float(first["jsyd_area_m2"]) / float(first["total_area_m2"]) * 100
        if "expected" in case:
            if "top_area_m2" in case["expected"]:
                ref["top_area_m2"] = float(rows[0]["area_m2"])
            if "max_mj" in case["expected"]:
                ref["max_mj"] = float(rows[0]["MJ"])
            if "row_count" in case["expected"]:
                ref["row_count"] = len(rows)
            if "total_polygons" in case["expected"]:
                total = await gw.run_sql(
                    f"SELECT count(*) AS n FROM {TABLE}", conversation_id="eval"
                )
                ref["total_polygons"] = total["rows"][0]["n"]
        if "expected_numeric_field" in case:
            ref["numeric_value"] = float(rows[0][case["expected_numeric_field"]])
    return ref


async def evaluate_case(
    gw: PgGateway,
    settings: Settings,
    case: dict[str, Any],
    model: str,
    refs_only: bool,
) -> dict[str, Any]:
    ref = await compute_reference(gw, case)
    report: dict[str, Any] = {"id": case["id"], "question": case["question"], "reference": ref}
    if refs_only:
        report["status"] = "OK (reference computed)"
        return report

    tmp = Path(tempfile.mkdtemp(prefix="geoagent_eval_"))
    store = MemoryConversationStore()
    conv = await store.create(title="eval", model=model)
    session = ConversationSession(store=store, conversation_id=conv["id"])
    collector = EventCollector()
    skills = SkillLoader(settings.skills_dir)
    skills.scan()
    ctx = ConversationContext(
        conversation_id=conv["id"],
        session=session,
        model=model,
        llm=LLMService(settings),
        store=store,
        memory=NoopMemory(),
        event_sink=collector.sink,
        skills=skills,
        pg=gw,
        transcripts_dir=tmp / "transcripts",
    )
    agent = SQLAgent(model=model)
    _, final = await agent.exec(ctx, case["question"])
    answer = final.content or ""
    # 标准问题路径的正文只给结论摘要，完整字典/明细在 artifact 表格里；
    # 文本包含性断言需要把 artifact 也纳入，否则会误判“字典没返回”。
    artifact_text = "\n".join(
        json.dumps(event.get("data"), ensure_ascii=False)
        for event in collector.events
        if event.get("type") == "artifact"
    )
    answer_for_contains = answer + ("\n" + artifact_text if artifact_text else "")
    tool_names = collector.tool_names()
    checks: list[str] = []
    ok = True
    if case.get("requires_sql"):
        if "run_sql" in tool_names:
            checks.append("调用了 run_sql")
        else:
            ok = False
            checks.append("未调用 run_sql")
    else:
        checks.append("无需查询数据库")

    for text in case.get("expected_contains", []):
        if text in answer_for_contains:
            checks.append(f"包含「{text}」")
        else:
            ok = False
            checks.append(f"缺少「{text}」")

    tolerance = case.get("tolerance", 0.005)
    if "percent" in ref:
        hit = _number_matches(answer, ref["percent"], tolerance)
        checks.append(f"占比 ≈{ref['percent']:.2f}% -> {'命中' if hit else '未命中'}")
        ok = ok and hit
    if "numeric_value" in ref:
        hit = _number_matches(
            answer, ref["numeric_value"], tolerance, unit_from_m2=True
        )
        checks.append(f"数值 ≈{ref['numeric_value']:.2f} -> {'命中' if hit else '未命中'}")
        ok = ok and hit
    for key, value in case.get("expected", {}).items():
        if key in ref:
            hit = _number_matches(
                answer,
                float(ref[key]),
                tolerance,
                unit_from_m2=key in {"max_mj", "top_area_m2"},
            )
            checks.append(f"{key}={ref[key]} -> {'命中' if hit else '未命中'}")
            ok = ok and hit

    report["status"] = "PASS" if ok else "FAIL"
    report["checks"] = checks
    report["answer"] = answer[:600]
    report["tool_names"] = tool_names
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="SQLAgent golden 评估")
    parser.add_argument("--refs-only", action="store_true", help="只验证参考 SQL，不调用 LLM")
    parser.add_argument("--case", default=None, help="只跑指定用例 id（如 3b）")
    parser.add_argument("--model", default=None, help="模型 id，默认取 GEOAGENT_DEFAULT_MODEL")
    args = parser.parse_args()

    settings = Settings()
    if not settings.pg_dsn:
        print("未配置 GEOAGENT_PG_DSN，请先在 backend/.env 填写。")
        return 2
    model = args.model or settings.default_model
    gw = PgGateway(
        dsn=settings.pg_dsn,
        whitelist=settings.pg_whitelist,
        max_rows=500,
        timeout_s=30,
    )
    try:
        cases = [c for c in CASES if args.case is None or str(c["id"]) == str(args.case)]
        if not cases:
            print(f"没有 id={args.case} 的用例")
            return 2
        failed = 0
        for case in cases:
            report = await evaluate_case(gw, settings, case, model, args.refs_only)
            print(f"\n== [{report['id']}] {report['question']} -> {report['status']}")
            for line in report.get("checks", []):
                print("  -", line)
            if report.get("reference"):
                print("  参考值:", report["reference"])
            if report.get("answer"):
                print("  模型回答:", report["answer"])
            if report.get("tool_names"):
                print("  工具调用:", report["tool_names"])
            if report["status"] == "FAIL":
                failed += 1
        print(f"\n结果: {len(cases) - failed}/{len(cases)} 通过")
        return 1 if failed else 0
    finally:
        await gw.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
