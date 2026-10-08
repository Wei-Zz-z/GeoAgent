"""三份已知模板共用的自动查询、回填与文件输出入口。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .brother_trial import fill_brother_report, is_brother_template
from .briefing import compute_briefing_stats
from .client_trial import fill_client_trial_report, is_client_trial_template, query_client_trial
from .pdf import PdfConversionError, convert_docx_to_pdf
from .question_library import load_question_library
from .workflow import REQUIRED_QUESTION_IDS, fill_question_library_report, is_question_library_template


def template_kind(parsed: dict[str, Any]) -> str | None:
    if not {"blocks", "slots", "chart_anchors"}.issubset(parsed):
        return None
    if is_client_trial_template(parsed):
        return "client"
    if is_question_library_template(parsed):
        return "library"
    if is_brother_template(parsed):
        return "brother"
    return None


async def generate_supported_report(
    source: Path,
    parsed: dict[str, Any],
    gateway: Any,
    skills_dir: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    """仅对已核验的原件执行固定只读统计；PDF失败不抹掉已生成Word。"""
    kind = template_kind(parsed)
    if kind is None:
        raise ValueError("该模板未完成固定口径与回填位置核验，不能自动生成。")
    started_at = datetime.now().astimezone()
    if kind == "client":
        stats = await query_client_trial(gateway, skills_dir)
    elif kind == "brother":
        stats = await compute_briefing_stats(gateway, skills_dir)
    else:
        library = await asyncio.to_thread(
            load_question_library,
            skills_dir / "qa-library" / "assets" / "question_library.json",
        )
        entries = {entry["id"]: entry for entry in library["entries"]}
        stats = {}
        for entry_id in REQUIRED_QUESTION_IDS:
            sql = str(entries[entry_id].get("sql", "")).strip()
            if not sql:
                raise ValueError(f"标准问题 {entry_id} 缺少参考 SQL。")
            stats[entry_id] = await gateway.run_sql(sql, conversation_id="template-library")
            if not stats[entry_id].get("rows"):
                raise ValueError(f"标准问题 {entry_id} 未返回数据，不回填。")
    await asyncio.to_thread(reports_dir.mkdir, parents=True, exist_ok=True)
    word_path = reports_dir / f"模板快报_{kind}_核验版_{uuid4().hex[:8]}.docx"
    if kind == "client":
        summary = await asyncio.to_thread(
            fill_client_trial_report, source, parsed, stats, word_path,
            query_started_at=started_at,
        )
    elif kind == "brother":
        summary = await asyncio.to_thread(
            fill_brother_report, source, parsed, stats, word_path,
            query_started_at=started_at,
        )
    else:
        await asyncio.to_thread(
            fill_question_library_report, source, parsed, stats, word_path,
            query_started_at=started_at,
        )
        summary = {"filled_scalars": 6, "filled_tables": 2, "charts": 2, "pending_items": []}
    pdf_path = word_path.with_suffix(".pdf")
    pdf_error = None
    try:
        await asyncio.to_thread(convert_docx_to_pdf, word_path, pdf_path)
    except PdfConversionError as exc:
        pdf_error = str(exc)
    return {
        "template_kind": kind,
        "word_path": str(word_path.resolve()),
        "pdf_path": None if pdf_error else str(pdf_path.resolve()),
        "pdf_error": pdf_error,
        "query_started_at": started_at.isoformat(),
        "summary": summary,
        "review_only": True,
    }
