"""向量知识库数据源：把问题库与数据库 schema 元数据转成可向量化的记录。

数据来源三类（对应 ``kind="question"`` / ``kind="schema"``）：

1. 标准问题库（``qa_library`` 的 19 条）——问法 + 意图 + 别名 + 参考 SQL；
2. 数据库 schema 元数据（``PgGateway.schema_metadata()``）——表注释 / 列注释 / 类型 / 主键；
3. 字典表内容（dict_tblx 的 TBLX 编码→名称、dict_land_classification_summary 的
   地类编码→名称→一级类→三大类）——把"数据知识"也纳入语义检索，帮助 schema linking。

构建脚本（``scripts/build_vector_store.py``）负责：加载这些源 → 用 ``EmbeddingService``
向量化 → 写入 ``VectorStore``。本模块只负责"把源转成记录"的纯函数，便于离线测试。
"""

from __future__ import annotations

from typing import Any, Sequence

from .store import VectorRecord


def build_question_records(entries: Sequence[Any]) -> list[VectorRecord]:
    """把标准问题库条目转成 question 记录（text = 问法 + 意图 + 别名）。"""
    records: list[VectorRecord] = []
    for entry in entries:
        aliases = [a for a in getattr(entry, "aliases", ()) if a]
        text = " ".join(
            [getattr(entry, "question", ""), getattr(entry, "intent", ""), *aliases]
        )
        payload = {
            "id": getattr(entry, "id", ""),
            "question": getattr(entry, "question", ""),
            "intent": getattr(entry, "intent", ""),
            "strategy": getattr(entry, "strategy", "sql"),
            "tool": getattr(entry, "tool", ""),
            "sql": getattr(entry, "sql", ""),
            "unit": getattr(entry, "unit", ""),
            "answer_hint": getattr(entry, "answer_hint", ""),
        }
        records.append(
            VectorRecord(id=payload["id"], kind="question", text=text, payload=payload)
        )
    return records


def build_schema_records(meta: dict[str, Any]) -> list[VectorRecord]:
    """把 schema 元数据转成 schema 记录（表级 + 列级各一条）。"""
    records: list[VectorRecord] = []
    for table in meta.get("tables", []):
        schema = str(table.get("schema", ""))
        tname = str(table.get("table", ""))
        fq = f"{schema}.{tname}" if schema else tname
        description = str(table.get("description", "") or "")
        records.append(
            VectorRecord(
                id=f"table:{fq}",
                kind="schema",
                text=" ".join([f"表 {fq}", description]).strip(),
                payload={
                    "scope": "table",
                    "schema": schema,
                    "table": tname,
                    "name": fq,
                    "description": description,
                },
            )
        )
        for column in table.get("columns", []):
            col = str(column.get("column_name", ""))
            if not col:
                continue
            data_type = str(column.get("data_type", "") or "")
            comment = str(column.get("comment", "") or "")
            pk = "主键" if column.get("is_primary_key") else ""
            payload = {
                "scope": "column",
                "schema": schema,
                "table": tname,
                "column": col,
                "data_type": data_type,
                "comment": comment,
                "is_primary_key": bool(column.get("is_primary_key")),
            }
            records.append(
                VectorRecord(
                    id=f"column:{fq}.{col}",
                    kind="schema",
                    text=" ".join([f"字段 {fq}.{col}", col, data_type, comment, pk]).strip(),
                    payload=payload,
                )
            )
    return records


async def collect_schema_meta(gateway: Any) -> dict[str, Any]:
    """从受控网关收集白名单表的 schema 元数据（失败时返回空，不中断构建）。"""
    try:
        return await gateway.schema_metadata()
    except Exception:
        return {"tables": []}


async def collect_dict_records(gateway: Any) -> list[VectorRecord]:
    """把两张字典表的内容转成 schema 记录（TBLX 名称 + 地类编码名称大类）。

    尽力而为：字典表读取失败不影响问题库与 schema 元数据的构建。
    """
    records: list[VectorRecord] = []
    try:
        result = await gateway.run_sql(
            'SELECT "TBLX", "TBLX_CODE" FROM knowledge_base.dict_tblx ORDER BY "TBLX"',
            conversation_id="kb-build",
        )
        for row in result.get("rows", []):
            code = str(row.get("TBLX", ""))
            name = str(row.get("TBLX_CODE", "") or "")
            if not code:
                continue
            records.append(
                VectorRecord(
                    id=f"dict:tblx:{code}",
                    kind="schema",
                    text=f"图斑类型编码 {code} 的名称为 {name}",
                    payload={
                        "scope": "dict",
                        "table": "dict_tblx",
                        "code": code,
                        "name": name,
                    },
                )
            )
    except Exception:
        pass

    try:
        result = await gateway.run_sql(
            'SELECT "DLBM", "DLMC", "YJLBM", "YJLMC", "CATEGORY_NAME" '
            'FROM knowledge_base.dict_land_classification_summary ORDER BY "DLBM"',
            conversation_id="kb-build",
        )
        for row in result.get("rows", []):
            dlbm = str(row.get("DLBM", ""))
            if not dlbm:
                continue
            dlmc = str(row.get("DLMC", "") or "")
            yjlmc = str(row.get("YJLMC", "") or "")
            category = str(row.get("CATEGORY_NAME", "") or "")
            text = f"地类编码 {dlbm} 名称为 {dlmc}，一级类 {yjlmc}，三大类 {category}".rstrip()
            records.append(
                VectorRecord(
                    id=f"dict:landcls:{dlbm}",
                    kind="schema",
                    text=text,
                    payload={
                        "scope": "dict",
                        "table": "dict_land_classification_summary",
                        "dlbm": dlbm,
                        "dlmc": dlmc,
                        "yjlmc": yjlmc,
                        "category": category,
                    },
                )
            )
    except Exception:
        pass
    return records
