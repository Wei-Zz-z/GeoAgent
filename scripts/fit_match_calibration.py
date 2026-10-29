"""拟合智能问数置信度校准（match score → p(correct)）。

训练样本来自“已有别名 + 95 条问法中的 similar/colloquial + 留出集 dev 半区”，
**不使用 question_locked.json**；每条问法取正例 + 前 5 个困难负例，特征与运行时
`qa_semantics.calibration_feature_row` 完全一致，避免训练/推理两套特征。

用法：
    backend/.venv/Scripts/python.exe scripts/fit_match_calibration.py
    backend/.venv/Scripts/python.exe scripts/fit_match_calibration.py --no-vector
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from geoagent.config import Settings, load_dotenv  # noqa: E402
from geoagent.core.embedding import EmbeddingService  # noqa: E402
from geoagent.qa_semantics import (  # noqa: E402
    CALIBRATION_FEATURES,
    calibration_feature_row,
    semantic_match,
    strip_overlapping_aliases,
    understand,
)
from geoagent.report.question_library import load_question_library  # noqa: E402
from geoagent.tools.qa_library import vector_scores_many  # noqa: E402

ASSETS = ROOT / "skills" / "qa-library" / "assets"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def training_queries(library: dict) -> list[tuple[str, str]]:
    """(问题, 目标条目 id)：别名 + similar/colloquial + dev 留出。"""
    items: list[tuple[str, str]] = []
    for entry in library.get("entries", []):
        entry_id = str(entry.get("id"))
        for alias in entry.get("aliases") or []:
            text = str(alias).strip()
            if len(text) >= 3:
                items.append((text, entry_id))
    variants = load_json(ASSETS / "question_variants.json")
    for group in variants.get("groups", []):
        for variant in group.get("variants", []):
            if variant.get("category") in {"similar", "colloquial"}:
                items.append((str(variant.get("text")), str(group.get("entry_id"))))
    holdout = load_json(ASSETS / "question_holdout.json")
    for group in holdout.get("groups", []):
        for variant in group.get("variants", []):
            if isinstance(variant, dict) and variant.get("split") != "locked":
                items.append((str(variant.get("text")), str(group.get("entry_id"))))
    # 去重（同一问题只保留一次）
    unique: dict[str, tuple[str, str]] = {}
    for text, entry_id in items:
        key = "".join(ch for ch in text.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if key and key not in unique:
            unique[key] = (text, entry_id)
    return list(unique.values())


def build_rows(library: dict, terms: dict, vector_map: dict[str, dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
    """构造 top-1 级样本：正常问法 + 留一别名“压力版”，标签是 top-1 是否正确。

    置信度要回答的是“本轮 top-1 条目有多大概率正确”，因此训练目标必须是 top-1
    正确性，而不是候选级正负例；否则 top-1 的 p 会被大量负例压低。
    """
    rows: list[list[float]] = []
    labels: list[int] = []
    entries = library.get("entries", [])
    for text, target in training_queries(library):
        for lib in (
            library,
            strip_overlapping_aliases(library, target, text),
        ):
            result = semantic_match(
                text,
                lib,
                top_k=len(entries),
                min_confidence=0.0,
                vector_scores=vector_map.get(text) or None,
            )
            candidates = result.get("candidates") or []
            if not candidates:
                continue
            top = candidates[0]
            query = understand(text, terms)
            row = calibration_feature_row(top, query, float(result.get("margin") or 0.0), str(top["id"]))
            rows.append([float(row.get(name, 0.0)) for name in CALIBRATION_FEATURES])
            labels.append(1 if str(top["id"]) == target else 0)
    return np.asarray(rows, dtype=float), np.asarray(labels, dtype=float)


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, epochs: int = 4000, lr: float = 0.3):
    """标准化 + 逻辑回归（梯度下降，足够当前样本量）。"""
    means = X.mean(axis=0)
    scales = X.std(axis=0)
    scales[scales == 0] = 1.0
    Z = (X - means) / scales
    Z = np.hstack([np.ones((Z.shape[0], 1)), Z])
    weights = np.zeros(Z.shape[1])
    for _ in range(epochs):
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(Z @ weights, -30, 30)))
        gradient = Z.T @ (probabilities - y) / len(y)
        gradient[1:] += l2 * weights[1:]
        weights -= lr * gradient
    return weights, means, scales


def ece(probabilities: np.ndarray, labels: np.ndarray, bins: int = 10) -> float:
    total = len(probabilities)
    value = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (probabilities >= lower) & (probabilities < upper if index < bins - 1 else probabilities <= upper)
        count = int(mask.sum())
        if count == 0:
            continue
        confidence = float(probabilities[mask].mean())
        accuracy = float(labels[mask].mean())
        value += count / total * abs(accuracy - confidence)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="拟合智能问数置信度校准")
    parser.add_argument("--no-vector", action="store_true", help="不调用 embedding，只拟合纯规则特征")
    args = parser.parse_args()
    load_dotenv()
    settings = Settings()
    library = load_question_library(ASSETS / "question_library.json")
    terms = json.loads((ASSETS / "terms.json").read_text(encoding="utf-8"))

    vector_map: dict[str, dict[str, float]] = {}
    requires_vector = False
    if not args.no_vector and EmbeddingService(settings).available:
        texts = sorted({text for text, _ in training_queries(library)})
        rows = asyncio.run(
            vector_scores_many(texts, skills_dir=ROOT / "skills", embedder=EmbeddingService(settings), min_similarity=0.45)
        )
        vector_map = dict(zip(texts, rows))
        requires_vector = any(bool(row) for row in rows)

    X, y = build_rows(library, terms, vector_map)
    if len(y) < 100 or y.sum() == 0:
        print("训练样本不足，放弃生成校准文件。")
        return 2
    weights, means, scales = fit_logistic(X, y)
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(
        np.hstack([np.ones((X.shape[0], 1)), (X - means) / scales]) @ weights, -30, 30
    )))
    train_ece = ece(probabilities, y)
    payload = {
        "name": "geoagent-match-calibration",
        "version": "1.0",
        "features": list(CALIBRATION_FEATURES),
        "weights": [round(float(value), 8) for value in weights[1:]],
        "intercept": round(float(weights[0]), 8),
        "means": {name: round(float(value), 8) for name, value in zip(CALIBRATION_FEATURES, means)},
        "scales": {name: round(float(value), 8) for name, value in zip(CALIBRATION_FEATURES, scales)},
        "requires_vector": requires_vector,
        "samples": int(len(y)),
        "positive_rate": round(float(y.mean()), 4),
        "train_ece": round(float(train_ece), 4),
        "trained_on": "aliases + similar/colloquial + holdout dev; locked 未参与",
    }
    output = ASSETS / "match_calibration.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {output}：samples={len(y)} positive_rate={y.mean():.3f} train_ece={train_ece:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
