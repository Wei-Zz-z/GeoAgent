"""智能问数匹配效果离线评估（不连数据库、不调用模型）。

用法：
    backend/.venv/Scripts/python.exe scripts/eval_question_matching.py
    backend/.venv/Scripts/python.exe scripts/eval_question_matching.py --json outputs/qa-match.json
    backend/.venv/Scripts/python.exe scripts/eval_question_matching.py --check

指标：
- Top-1 / Top-3：正确条目是否排在第一位 / 前三位；
- 自动执行率：决策为 execute 且条目正确 / 样本数；
- 误执行（false-auto）：决策为 execute 但条目错误（比覆盖率更重要）；
- 留一别名：删除目标条目中与问法互为子串的别名，模拟“没见过这条问法”。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from geoagent.qa_semantics import (  # noqa: E402
    VECTOR_MIN_SIMILARITY,
    VECTOR_WEIGHT,
    semantic_match,
    strip_overlapping_aliases,
)
from geoagent.report.question_library import (  # noqa: E402
    load_question_library,
    match_question,
)

ASSETS = ROOT / "skills" / "qa-library" / "assets"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def variant_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """question_variants.json → 扁平问法列表。"""
    items: list[dict[str, Any]] = []
    for group in data.get("groups", []):
        for variant in group.get("variants", []):
            items.append(
                {
                    "entry_id": str(group.get("entry_id") or ""),
                    "text": str(variant.get("text") or ""),
                    "category": str(variant.get("category") or ""),
                    "expect": str(variant.get("expect") or ""),
                }
            )
    return items


def group_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """question_holdout/locked 的 groups 结构 → 扁平问法列表。"""
    items: list[dict[str, Any]] = []
    for group in data.get("groups", []):
        for variant in group.get("variants", []):
            if isinstance(variant, str):
                text, split = variant, ""
            else:
                text, split = str(variant.get("text") or ""), str(variant.get("split") or "")
            items.append(
                {
                    "entry_id": str(group.get("entry_id") or ""),
                    "text": text,
                    "category": "holdout",
                    "expect": "",
                    "split": split,
                }
            )
    return items


def _ece(probabilities: list[float], labels: list[int], bins: int = 10) -> float:
    if not probabilities:
        return 0.0
    total = len(probabilities)
    value = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        selected = [
            (probability, label)
            for probability, label in zip(probabilities, labels)
            if (lower <= probability < upper) or (index == bins - 1 and probability == 1.0)
        ]
        if not selected:
            continue
        confidence = sum(item[0] for item in selected) / len(selected)
        accuracy = sum(item[1] for item in selected) / len(selected)
        value += len(selected) / total * abs(accuracy - confidence)
    return round(value, 4)


def _brier(probabilities: list[float], labels: list[int]) -> float:
    if not probabilities:
        return 0.0
    return round(
        sum((probability - label) ** 2 for probability, label in zip(probabilities, labels))
        / len(probabilities),
        4,
    )


def evaluate(
    items: list[dict[str, Any]],
    library: dict[str, Any],
    *,
    matcher: str,
    threshold: float,
    leave_one: bool = True,
    vector_scores_by_text: dict[str, dict[str, float]] | None = None,
    vector_weight: float = VECTOR_WEIGHT,
) -> dict[str, Any]:
    total = len(items)
    top1 = top3 = auto = false_auto = 0
    vector_used = 0
    probabilities: list[float] = []
    labels: list[int] = []
    decisions: dict[str, int] = {}
    latencies: list[float] = []
    errors: list[dict[str, Any]] = []
    for item in items:
        target = item["entry_id"]
        text = item["text"]
        lib = strip_overlapping_aliases(library, target, text) if leave_one else library
        started = time.perf_counter()
        if matcher == "semantic":
            vector_scores = (vector_scores_by_text or {}).get(text) or None
            match = semantic_match(
                text,
                lib,
                min_confidence=threshold,
                vector_scores=vector_scores,
                vector_weight=vector_weight,
            )
            matched_id = match.get("matched_question_id")
            candidate_ids = [str(candidate.get("id")) for candidate in match.get("candidates") or []]
            decision = str(match.get("decision") or "fallback")
            is_auto = decision == "execute"
            score = match.get("score")
            vector_used += int(bool(match.get("vector_used")))
        else:
            match = match_question(text, lib, min_confidence=threshold)
            matched_id = match.get("matched_question_id")
            candidate_ids = [str(candidate.get("id")) for candidate in match.get("candidates") or []]
            is_auto = match.get("status") == "candidate_found"
            decision = "execute" if is_auto else "fallback"
            score = match.get("confidence")
        latencies.append((time.perf_counter() - started) * 1000)
        correct = matched_id == target
        top1 += correct
        top3 += target in candidate_ids
        decisions[decision] = decisions.get(decision, 0) + 1
        if is_auto:
            auto += correct
            false_auto += int(not correct)
        if matcher == "semantic":
            probability = match.get("p_correct")
            if probability is None:
                probability = score
            probabilities.append(float(probability or 0.0))
            labels.append(int(correct))
        if not correct:
            errors.append(
                {
                    "entry_id": target,
                    "text": text,
                    "matched_id": matched_id,
                    "score": score,
                    "decision": decision,
                    "category": item.get("category"),
                }
            )

    def pct(value: int) -> float:
        return round(value / total, 4) if total else 0.0

    return {
        "n": total,
        "top1": pct(top1),
        "top3": pct(top3),
        "auto": pct(auto),
        "auto_correct": auto,
        "false_auto": false_auto,
        "vector_used": vector_used,
        "ece": _ece(probabilities, labels) if matcher == "semantic" else 0.0,
        "brier": _brier(probabilities, labels) if matcher == "semantic" else 0.0,
        "decisions": decisions,
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else 0,
            "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else 0,
        },
        "errors": errors,
    }


def fmt(metrics: dict[str, Any]) -> str:
    return (
        f"| {metrics['n']} | {metrics['top1']:.1%} | {metrics['top3']:.1%} | "
        f"{metrics['auto']:.1%} | {metrics['false_auto']} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="智能问数匹配效果评估")
    parser.add_argument("--threshold", type=float, default=0.42, help="语义匹配自动执行基础阈值")
    parser.add_argument(
        "--baseline-threshold",
        type=float,
        default=0.5,
        help="旧字面匹配的对比阈值（保持改造前的 0.5，便于和方案基线对齐）",
    )
    parser.add_argument("--json", type=Path, default=None, help="把完整指标写入 JSON")
    parser.add_argument("--check", action="store_true", help="按验收门槛返回非零退出码")
    parser.add_argument(
        "--vector",
        action="store_true",
        help="启用 embedding 向量召回 + RRF（需要 OPENAI_API_KEY；失败自动回退规则匹配）",
    )
    parser.add_argument("--vector-weight", type=float, default=VECTOR_WEIGHT, help="RRF 加分上限")
    parser.add_argument(
        "--vector-min-similarity",
        type=float,
        default=VECTOR_MIN_SIMILARITY,
        help="纳入向量排名的最低余弦相似度",
    )
    parser.add_argument("--baseline", action="store_true", default=True, help="同时跑旧字面匹配基线")
    args = parser.parse_args()

    library = load_question_library(ASSETS / "question_library.json")
    variants = variant_items(load_json(ASSETS / "question_variants.json"))
    holdout = group_items(load_json(ASSETS / "question_holdout.json"))
    locked = group_items(load_json(ASSETS / "question_locked.json"))

    vector_map: dict[str, dict[str, float]] | None = None
    if args.vector:
        from geoagent.config import Settings, load_dotenv
        from geoagent.core.embedding import EmbeddingService
        from geoagent.tools.qa_library import vector_scores_many

        load_dotenv()
        embedder = EmbeddingService(Settings())
        if not embedder.available:
            print("未配置 OPENAI_API_KEY，--vector 已跳过，按纯规则匹配评估。")
        else:
            texts = sorted(
                {item["text"] for item in variants} | {item["text"] for item in holdout}
                | {item["text"] for item in locked}
            )
            rows = asyncio.run(
                vector_scores_many(
                    texts,
                    skills_dir=ROOT / "skills",
                    embedder=embedder,
                    min_similarity=args.vector_min_similarity,
                )
            )
            vector_map = dict(zip(texts, rows))
            matched = sum(1 for row in rows if row)
            print(f"向量召回：{len(texts)} 条问题，其中 {matched} 条命中相似度≥{args.vector_min_similarity} 的条目")

    vector_kwargs = (
        {"vector_scores_by_text": vector_map, "vector_weight": args.vector_weight}
        if vector_map
        else {}
    )

    sc_items = [item for item in variants if item["category"] in {"similar", "colloquial"}]
    dev_items = [item for item in holdout if item.get("split") != "locked"]
    locked_holdout = [item for item in holdout if item.get("split") == "locked"]

    result: dict[str, Any] = {"threshold": args.threshold, "datasets": {}}
    mode_label = "语义 + 向量 RRF" if vector_map else "语义槽位匹配（纯规则）"
    print(
        f"\n# 智能问数匹配效果（threshold={args.threshold}，{mode_label}）\n"
    )
    print(f"## {mode_label}\n")
    print("| 数据集 | N | Top-1 | Top-3 | 自动执行 | 误执行 |")
    print("| --- | --- | --- | --- | --- | --- |")
    sets = {
        "95 条问法（含别名，不代表未见问法）": (variants, False),
        "57 条 similar/colloquial（留一别名）": (sc_items, True),
        "38 条独立留出（留一别名）": (holdout, True),
        "19 条留出 dev（留一别名）": (dev_items, True),
        "19 条留出 locked（留一别名）": (locked_holdout, True),
        "19 条锁定集（留一别名）": (locked, True),
    }
    for label, (items, leave_one) in sets.items():
        metrics = evaluate(
            items,
            library,
            matcher="semantic",
            threshold=args.threshold,
            leave_one=leave_one,
            **vector_kwargs,
        )
        result["datasets"][label] = metrics
        print(f"| {label} {fmt(metrics)}")

    print(f"\n## 旧字面匹配基线（threshold={args.baseline_threshold}，仅对比）\n")
    print("| 数据集 | N | Top-1 | Top-3 | 自动执行 | 误执行 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for label in ("95 条问法（含别名，不代表未见问法）", "57 条 similar/colloquial（留一别名）"):
        items = variants if label.startswith("95") else sc_items
        leave_one = not label.startswith("95")
        metrics = evaluate(
            items,
            library,
            matcher="lexical",
            threshold=args.baseline_threshold,
            leave_one=leave_one,
        )
        result["datasets"]["baseline:" + label] = metrics
        print(f"| {label} {fmt(metrics)}")

    print("\n## 95 条问法分类型（语义匹配）\n")
    print("| 类型 | N | Top-1 | 自动执行 | 误执行 |")
    print("| --- | --- | --- | --- | --- |")
    by_category: dict[str, Any] = {}
    for category in ("similar", "colloquial", "ambiguous", "adversarial"):
        subset = [item for item in variants if item["category"] == category]
        metrics = evaluate(
            subset,
            library,
            matcher="semantic",
            threshold=args.threshold,
            leave_one=False,
            **vector_kwargs,
        )
        by_category[category] = metrics
        print(f"| {category} | {metrics['n']} | {metrics['top1']:.1%} | {metrics['auto']:.1%} | {metrics['false_auto']} |")
    result["by_category"] = by_category

    print("\n## 置信度校准（top-1 p(correct)）\n")
    print("| 数据集 | ECE | Brier |")
    print("| --- | --- | --- |")
    for label in (
        "57 条 similar/colloquial（留一别名）",
        "38 条独立留出（留一别名）",
        "19 条锁定集（留一别名）",
        "95 条问法（含别名，不代表未见问法）",
    ):
        metrics = result["datasets"][label]
        print(f"| {label} | {metrics.get('ece', 0):.4f} | {metrics.get('brier', 0):.4f} |")

    unseen = result["datasets"]["57 条 similar/colloquial（留一别名）"]
    print(
        f"\n匹配耗时：p50={unseen['latency_ms']['p50']}ms，p95={unseen['latency_ms']['p95']}ms（42 条标准问题）"
    )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"完整指标已写入 {args.json}")

    if args.check:
        failures: list[str] = []
        if unseen["auto"] < 0.55:
            failures.append(f"留一别名自动执行率 {unseen['auto']:.1%} < 55%")
        if unseen["false_auto"] > 0:
            failures.append(f"留一别名误执行 {unseen['false_auto']} > 0")
        holdout_metrics = result["datasets"]["38 条独立留出（留一别名）"]
        if holdout_metrics["top1"] < 0.9:
            failures.append(f"独立留出 Top-1 {holdout_metrics['top1']:.1%} < 90%")
        if failures:
            print("\n验收未通过：\n- " + "\n- ".join(failures))
            return 1
        print("\n验收通过：未见问法覆盖率与误执行均达标。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
