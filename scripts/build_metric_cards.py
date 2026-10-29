"""生成智能问数指标卡（metric_cards.json）。

指标卡是条目语义画像的落盘形式：匹配时优先读卡，卡缺失时才从问句/SQL 自动推导。
修改 ``terms.json`` 或问题库后重新运行本脚本即可刷新。

用法：
    backend/.venv/Scripts/python.exe scripts/build_metric_cards.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from geoagent.qa_semantics import load_terms, profile_entry, resolve_asset_path  # noqa: E402
from geoagent.report.question_library import load_question_library  # noqa: E402


def build() -> dict:
    assets = ROOT / "skills" / "qa-library" / "assets"
    library = load_question_library(assets / "question_library.json")
    terms = load_terms(str(resolve_asset_path("terms.json")))
    cards: dict[str, dict] = {}
    for entry in library["entries"]:
        profile = profile_entry(entry, terms)
        cards[str(entry["id"])] = {
            "question": entry.get("question", ""),
            "intent": entry.get("intent", ""),
            "risk": profile.risk,
            **profile.to_dict(),
        }
    return {
        "name": "geoagent-metric-cards",
        "version": "1.0",
        "description": "标准问题指标卡：主体/过滤/聚合/维度/前后时项/风险。由 scripts/build_metric_cards.py 生成，可手工修订。",
        "source": "question_library.json + terms.json",
        "cards": cards,
    }


def main() -> int:
    output = ROOT / "skills" / "qa-library" / "assets" / "metric_cards.json"
    data = build()
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {output}（{len(data['cards'])} 张指标卡）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
