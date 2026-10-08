"""标准问题库（标准问题 + SQL/工具示例）加载与检索。

数据源是业务可编辑的 JSON 资产（改动时只编辑 JSON、不碰代码）：
    skills/qa-library/assets/question_library.json

本模块只负责：定位资产 → 加载 → 关键词检索 → 暴露 ``search_question_library``
工具，为智能问数系统的报表查询提供准确依据（命中标准问题 → 返回其参考 SQL
与推荐执行方式）。设计参照 ``tools/categories.py``：把"经常调整的业务数据"
放在技能资产里，代码不做口径内嵌。

检索采用轻量中文关键词/别名子串打分，不引入分词依赖；命中按得分降序返回。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..core.embedding import cosine_similarity
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


@dataclass(frozen=True)
class QuestionEntry:
    """标准问题库中的一条：标准问法 + 意图 + 推荐执行方式 + 参考 SQL。"""

    id: str
    question: str
    intent: str
    strategy: str
    tool: str = ""
    sql: str = ""
    unit: str = ""
    answer_hint: str = ""
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


def default_asset_path() -> Path:
    """仓库默认资产路径（<项目根>/skills/qa-library/assets/question_library.json）。"""
    return (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "qa-library"
        / "assets"
        / "question_library.json"
    )


def resolve_asset_path(skills_dir: Optional[str | Path] = None) -> Path:
    """定位问题库资产：优先用运行时技能目录，其次仓库默认路径。"""
    if skills_dir:
        if Path(skills_dir).is_file():
            return Path(skills_dir)
        candidate = (
            Path(skills_dir) / "qa-library" / "assets" / "question_library.json"
        )
        if candidate.is_file():
            return candidate
    default = default_asset_path()
    if not default.is_file():
        raise FileNotFoundError(
            f"缺少标准问题库资产: {default}（应随 qa-library 提供）"
        )
    return default


@lru_cache(maxsize=8)
def load_entries(asset_path: Optional[Path] = None) -> tuple[QuestionEntry, ...]:
    """加载问题库并解析为 QuestionEntry 元组（按路径缓存）。"""
    path = resolve_asset_path(asset_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[QuestionEntry] = []
    for item in data.get("entries", []):
        entries.append(
            QuestionEntry(
                id=str(item.get("id", "")),
                question=str(item.get("question", "")),
                intent=str(item.get("intent", "")),
                strategy=str(item.get("strategy", "sql")),
                tool=str(item.get("tool", "")),
                sql=str(item.get("sql", "")),
                unit=str(item.get("unit", "")),
                answer_hint=str(item.get("answer_hint", "")),
                aliases=tuple(str(a) for a in item.get("aliases", [])),
                keywords=tuple(str(k) for k in item.get("keywords", [])),
            )
        )
    return tuple(entries)


def _score(query: str, entry: QuestionEntry) -> float:
    """子串打分：别名命中权重最高，其次关键词，最后标准问法整体重合。"""
    q = query.lower()
    score = 0.0
    for alias in entry.aliases:
        a = alias.lower()
        if a and a in q:
            score += 3.0 * len(a)
    for kw in entry.keywords:
        k = kw.lower()
        if k and k in q:
            score += 1.0 * len(k)
    qn = entry.question.lower()
    if qn and (qn in q or q in qn):
        score += 5.0
    return score


def search(
    query: str,
    top_k: int = 3,
    skills_dir: Optional[str | Path] = None,
) -> list[QuestionEntry]:
    """按关键词/别名检索标准问题库，返回得分降序的前 top_k 条。"""
    entries = load_entries(resolve_asset_path(skills_dir))
    scored = [(e, _score(query, e)) for e in entries]
    scored = [(e, s) for e, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [e for e, _ in scored[:top_k]]


# ---------------------------------------------------------------- 语义检索（混合）

# 混合检索超参：
# - 关键词分与语义相似度的权重（保留关键词命中不丢，同时引入语义召回）；
# - 关键词分的量纲归一上限（现有 _score 是子串长度累加，量纲不定）；
# - 语义命中的余弦阈值（低于该值且无关键词命中时丢弃，避免误召回无关条目）。
_KEYWORD_WEIGHT = 0.4
_SEMANTIC_WEIGHT = 0.6
_KEYWORD_SCORE_CAP = 30.0
_SEMANTIC_THRESHOLD = 0.45

_entry_vectors_cache: dict[tuple[str, str], list[list[float]]] = {}


def _entry_text(entry: QuestionEntry) -> str:
    """构造用于向量化的代表性文本：标准问法 + 意图 + 别名。"""
    parts = [entry.question, entry.intent]
    parts.extend(a for a in entry.aliases if a)
    return " ".join(parts)


async def _entry_vectors(embedder: Any, path: Path) -> list[list[float]]:
    """返回条目的向量（懒加载；按 (路径, 模型) 缓存，避免每次检索重复调用 embedding）。"""
    key = (str(path), str(getattr(embedder, "model", "")))
    cached = _entry_vectors_cache.get(key)
    if cached is not None:
        return cached
    entries = load_entries(path)
    vectors = await embedder.embed([_entry_text(e) for e in entries])
    _entry_vectors_cache[key] = vectors
    return vectors


async def search_semantic(
    query: str,
    top_k: int = 3,
    skills_dir: Optional[str | Path] = None,
    embedder: Any = None,
) -> list[QuestionEntry]:
    """混合语义检索：关键词打分 + embedding 余弦相似度加权。

    - 关键词命中（``_score > 0``）的条目始终保留，不破坏既有准确率；
    - 未命中关键词但语义相似度 >= ``_SEMANTIC_THRESHOLD`` 的条目也会召回（新增覆盖面）；
    - embedding 调用失败时整体降级为关键词检索，保证可用性。
    """
    path = resolve_asset_path(skills_dir)
    entries = load_entries(path)
    if not entries or embedder is None:
        return search(query, top_k=top_k, skills_dir=skills_dir)
    try:
        vectors = await _entry_vectors(embedder, path)
        qvec = (await embedder.embed([query]))[0]
    except Exception:
        # embedding 不可用/失败 → 回退关键词检索。
        return search(query, top_k=top_k, skills_dir=skills_dir)
    scored: list[tuple[QuestionEntry, float]] = []
    for entry, vec in zip(entries, vectors):
        kw = _score(query, entry)
        sim = max(0.0, cosine_similarity(qvec, vec))
        kw_norm = min(1.0, kw / _KEYWORD_SCORE_CAP)
        combined = _KEYWORD_WEIGHT * kw_norm + _SEMANTIC_WEIGHT * sim
        if kw > 0 or sim >= _SEMANTIC_THRESHOLD:
            scored.append((entry, combined))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [e for e, _ in scored[:top_k]]


def _format_match(entries: list[QuestionEntry]) -> str:
    """把命中的问题库条目渲染为给 LLM 看的文本。"""
    lines = [f"在标准问题库中命中 {len(entries)} 条："]
    for i, e in enumerate(entries, 1):
        if e.strategy == "tool" and e.tool:
            action = f"调用工具 {e.tool}()"
        else:
            action = "用 run_sql 执行下方参考 SQL（可按需调整参数/维度）"
        lines.append(f"{i}. [{e.id}] {e.intent}，标准问法：「{e.question}」")
        lines.append(f"   - 推荐执行：{action}")
        if e.sql:
            lines.append(f"   - 参考 SQL：{e.sql}")
        if e.unit:
            lines.append(f"   - 单位：{e.unit}")
        if e.answer_hint:
            lines.append(f"   - 回答要点：{e.answer_hint}")
    return "\n".join(lines)


class SearchQuestionParams(BaseModel):
    """search_question_library 参数。"""

    query: str = Field(
        description="用户的问题或关键词（中文）。用于在标准问题库中检索最接近的标准问题"
    )
    top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="返回的匹配条目数（默认 3）",
    )


@register_tool(
    "search_question_library",
    (
        "在标准问题库中检索与用户问题最接近的标准问题，返回其参考 SQL、推荐工具、"
        "口径单位与回答要点，作为报表查询的准确依据。当用户问题疑似属于常见统计"
        "口径（总量/按类型/耕地/建设用地/流向/占比/分县等）时优先调用，命中后按其"
        "推荐方式执行（调用确定性工具或 run_sql 参考 SQL）。"
    ),
    SearchQuestionParams,
)
async def search_question_library(
    ctx: Any, query: str, top_k: int = 3
) -> ToolResult:
    skills = getattr(ctx, "skills", None)
    skills_dir = getattr(skills, "skills_dir", None)
    embedder = getattr(ctx, "embeddings", None)
    knowledge = getattr(ctx, "knowledge", None)
    try:
        if knowledge is not None and hasattr(knowledge, "search_questions"):
            matches = await knowledge.search_questions(
                query, top_k=top_k, skills_dir=skills_dir
            )
        elif embedder is not None and getattr(embedder, "available", False):
            matches = await search_semantic(
                query, top_k=top_k, skills_dir=skills_dir, embedder=embedder
            )
        else:
            matches = search(query, top_k=top_k, skills_dir=skills_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return ToolResult(
            tool_call_id="",
            name="search_question_library",
            content=f"标准问题库加载失败: {exc}",
            is_error=True,
        )
    if not matches:
        return ToolResult(
            tool_call_id="",
            name="search_question_library",
            content=(
                "未在标准问题库中命中与「" + query + "」匹配的标准问题。"
                "请改用 run_sql / describe_table 自行查询（遵守 SQL 纪律与口径默认值）。"
            ),
            artifacts=[
                Artifact(
                    kind="table",
                    name="question_library",
                    data={"columns": ["检索", "结果"], "rows": [{"检索": query, "结果": "未命中"}]},
                )
            ],
        )
    rows = [
        {
            "编号": e.id,
            "意图": e.intent,
            "标准问法": e.question,
            "策略": "工具" if e.strategy == "tool" else "SQL",
            "工具": e.tool,
            "参考SQL": e.sql,
            "单位": e.unit,
        }
        for e in matches
    ]
    return ToolResult(
        tool_call_id="",
        name="search_question_library",
        content=_format_match(matches),
        artifacts=[
            Artifact(
                kind="table",
                name="question_library",
                data={
                    "columns": ["编号", "意图", "标准问法", "策略", "工具", "参考SQL", "单位"],
                    "rows": rows,
                },
            )
        ],
    )


def get_qa_tools() -> list[Any]:
    """返回标准问题库相关工具（当前仅 search_question_library）。"""
    return get_tools("search_question_library")
