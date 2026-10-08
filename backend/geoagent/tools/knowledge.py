"""向量知识库检索工具：search_schema_knowledge（schema 元数据语义检索）。

问题库检索复用既有 ``search_question_library``（其内部已接入 ``ctx.knowledge`` 的
持久化向量）；本模块补上"schema 元数据语义检索"，用于智能问数系统在不确定字段名 /
口径 / 编码含义时做 schema linking，替代"凭空猜列名"。

未构建向量知识库或 embedding 未配置时，本工具返回引导性提示，回退到
list_tables / describe_table / run_sql 查字典表。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


class SearchSchemaParams(BaseModel):
    """search_schema_knowledge 参数。"""

    query: str = Field(
        description=(
            "要查找的字段名 / 口径 / 编码含义的中文描述或关键词，例如"
            "“面积字段叫什么”“地类编码是哪一列”“图斑类型 01 是什么”"
        )
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回的匹配条目数（默认 5）",
    )


def _format_hits(hits: list[dict[str, Any]]) -> str:
    lines = [f"向量知识库命中 {len(hits)} 条 schema 知识："]
    for i, h in enumerate(hits, 1):
        scope = h.get("scope", "")
        if scope == "table":
            desc = h.get("description") or ""
            lines.append(
                f"{i}. 表 {h.get('name')}"
                + (f"（{desc}）" if desc else "")
            )
        elif scope == "column":
            extra = "，主键" if h.get("is_primary_key") else ""
            comment = h.get("comment") or ""
            lines.append(
                f"{i}. 字段 {h.get('table')}.{h.get('column')}，类型 {h.get('data_type')}"
                + (f"（{comment}）" if comment else "")
                + extra
            )
        elif scope == "dict":
            lines.append(
                f"{i}. 字典：{h.get('code')} → {h.get('name') or h.get('dlmc') or h.get('category')}"
            )
        else:
            lines.append(f"{i}. {h}")
    return "\n".join(lines)


@register_tool(
    "search_schema_knowledge",
    (
        "在向量知识库中检索表/字段注释、字段类型、编码含义（TBLX/地类编码）等 "
        "schema 元数据。当不确定字段名、口径或编码含义时优先调用，命中后按返回的"
        "字段名/口径写 SQL；未命中再用 list_tables / describe_table / run_sql 查字典表。"
    ),
    SearchSchemaParams,
)
async def search_schema_knowledge(
    ctx: Any, query: str, top_k: int = 5
) -> ToolResult:
    kb = getattr(ctx, "knowledge", None)
    if kb is None or not hasattr(kb, "search_schema"):
        return ToolResult(
            tool_call_id="",
            name="search_schema_knowledge",
            content="向量知识库未接入：请改用 list_tables / describe_table / run_sql 查字典表。",
            is_error=True,
        )
    hits = await kb.search_schema(query, top_k=top_k)
    if not hits:
        return ToolResult(
            tool_call_id="",
            name="search_schema_knowledge",
            content=(
                f"未在向量知识库中命中与「{query}」相关的字段/口径。"
                "请改用 list_tables / describe_table 查看表结构，或 run_sql 查字典表。"
            ),
            artifacts=[
                Artifact(
                    kind="table",
                    name="schema_knowledge",
                    data={"columns": ["检索", "结果"], "rows": [{"检索": query, "结果": "未命中"}]},
                )
            ],
        )
    rows = [
        {
            "作用域": h.get("scope", ""),
            "表": h.get("table", "") or h.get("name", ""),
            "字段": h.get("column", ""),
            "编码": h.get("code", ""),
            "名称/注释": (
                h.get("name")
                or h.get("comment")
                or h.get("dlmc")
                or h.get("category")
                or h.get("description")
                or ""
            ),
            "类型": h.get("data_type", ""),
            "相似度": h.get("_similarity", ""),
        }
        for h in hits
    ]
    return ToolResult(
        tool_call_id="",
        name="search_schema_knowledge",
        content=_format_hits(hits),
        artifacts=[
            Artifact(
                kind="table",
                name="schema_knowledge",
                data={
                    "columns": ["作用域", "表", "字段", "编码", "名称/注释", "类型", "相似度"],
                    "rows": rows,
                },
            )
        ],
    )


def get_knowledge_tools() -> list[Any]:
    return get_tools("search_schema_knowledge")
