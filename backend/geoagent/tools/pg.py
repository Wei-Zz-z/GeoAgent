"""PostGIS 受控 SQL 查询工具层（只读）。

LLM 不直接持有数据库连接；所有查询经由本层执行，并强制满足 AGENTS.md 的护栏：

- 只读账号（仅 SELECT 权限，部署时由数据库角色保证）
- 强制 LIMIT（自动包裹一层 LIMIT，避免超大结果集）
- 查询超时（asyncio 超时取消）
- 单语句（禁止分号与 WITH / EXPLAIN / DML）
- 表/视图白名单（解析出的所有表引用必须命中白名单）
- 查询审计日志（JSONL，逐条记录 SQL 与执行结果）

连接串一律从环境变量 ``GEOAGENT_PG_DSN`` 读取，禁止硬编码账号密码。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

import asyncpg
from pydantic import BaseModel, Field

from .labels import humanize_table
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


# ---------------------------------------------------------------- 异常


class PgError(Exception):
    """PG 工具层错误基类。"""


class PgNotConfiguredError(PgError):
    """数据库未配置（缺少 GEOAGENT_PG_DSN）。"""


class PgGuardError(PgError):
    """SQL 未通过受控护栏检查。"""


class PgTimeoutError(PgError):
    """查询超时。"""


class PgQueryError(PgError):
    """数据库执行错误（连接失败 / SQL 错误等）。"""


def _json_safe(value: Any) -> Any:
    """把数据库返回的非 JSON 类型转换为可序列化值（供 artifact / 持久化使用）。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ---------------------------------------------------------------- SQL 护栏（纯函数）


@dataclass(frozen=True)
class _Token:
    """SQL 词法单元。kind: ws/comment/string/dollar/ident/qident/number/punct。"""

    kind: str
    value: str


_DOLLAR_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def tokenize_sql(sql: str) -> list[_Token]:
    """轻量 SQL 词法切分：识别注释、字符串、美元引用、双引号标识符。"""
    tokens: list[_Token] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            j = i
            while j < n and sql[j].isspace():
                j += 1
            tokens.append(_Token("ws", sql[i:j]))
            i = j
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            if j == -1:
                j = n
            tokens.append(_Token("comment", sql[i:j]))
            i = j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            tokens.append(_Token("comment", sql[i:j]))
            i = j
            continue
        if ch == "'":
            j = i + 1
            buf = ["'"]
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        buf.append("''")
                        j += 2
                        continue
                    buf.append("'")
                    j += 1
                    break
                buf.append(sql[j])
                j += 1
            tokens.append(_Token("string", "".join(buf)))
            i = j
            continue
        if ch == '"':
            j = i + 1
            buf = ['"']
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        buf.append('""')
                        j += 2
                        continue
                    buf.append('"')
                    j += 1
                    break
                buf.append(sql[j])
                j += 1
            tokens.append(_Token("qident", "".join(buf)))
            i = j
            continue
        m = _DOLLAR_RE.match(sql, i)
        if m:
            tag = m.group(0)
            end = sql.find(tag, i + len(tag))
            end = n if end == -1 else end + len(tag)
            tokens.append(_Token("dollar", sql[i:end]))
            i = end
            continue
        if ch.isalpha() or ch == "_" or ord(ch) > 127:
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_" or ord(sql[j]) > 127):
                j += 1
            tokens.append(_Token("ident", sql[i:j]))
            i = j
            continue
        if ch.isdigit():
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "._-+"):
                j += 1
            tokens.append(_Token("number", sql[i:j]))
            i = j
            continue
        tokens.append(_Token("punct", ch))
        i += 1
    return tokens


def normalize_ident(token_value: str) -> str:
    """规范化标识符：双引号内的内容原样保留，未加引号的转小写。"""
    v = token_value.strip()
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1].replace('""', '"')
    return v.lower()


def normalize_qualified(spec: str) -> tuple[str, str]:
    """把 ``schema.table`` 规范化为 (schema, table)，兼容带双引号的形式。"""
    parts = [p.strip() for p in spec.split(".")]
    if len(parts) == 1:
        return "", normalize_ident(parts[0])
    return normalize_ident(parts[0]), normalize_ident(parts[1])


def quote_ident(part: str) -> str:
    """按需给标识符加双引号（非小写简单标识符才加）。"""
    if re.fullmatch(r"[a-z_][a-z0-9_]*", part):
        return part
    return '"' + part.replace('"', '""') + '"'


_FROM_JOIN_KEYWORDS = {
    "FROM",
    "JOIN",
}
_FROM_END_KEYWORDS = {
    "WHERE",
    "GROUP",
    "HAVING",
    "ORDER",
    "LIMIT",
    "OFFSET",
    "FETCH",
    "UNION",
    "EXCEPT",
    "INTERSECT",
}
_FORBIDDEN_FIRST_KEYWORDS = {
    "WITH",
    "EXPLAIN",
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "SET",
    "RESET",
    "SHOW",
    "DO",
    "CALL",
    "LOCK",
    "NOTIFY",
    "LISTEN",
    "UNLISTEN",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "PREPARE",
    "EXECUTE",
    "DEALLOCATE",
    "DECLARE",
    "CLOSE",
    "MOVE",
    "IMPORT",
    "VALUES",
    "TABLE",
}


def extract_table_refs(tokens: list[_Token]) -> list[tuple[str, str]]:
    """提取 FROM/JOIN 引用的表名，返回规范化后的 (schema, table) 列表。"""
    meaningful = [t for t in tokens if t.kind not in ("ws", "comment")]
    refs: list[tuple[str, str]] = []
    expect_table = False
    in_from = False
    paren_in_from: list[bool] = []
    prev_punct: Optional[str] = None
    i = 0
    while i < len(meaningful):
        t = meaningful[i]
        if t.kind == "ident":
            upper = t.value.upper()
            if upper in _FROM_JOIN_KEYWORDS:
                in_from = True
                expect_table = True
                prev_punct = None
                i += 1
                continue
            if upper in _FROM_END_KEYWORDS:
                in_from = False
                expect_table = False
                prev_punct = None
                i += 1
                continue
        if t.kind in ("ident", "qident") and expect_table or (
            t.kind in ("ident", "qident") and in_from and prev_punct == ","
        ):
            schema_part = normalize_ident(t.value)
            next_i = i + 1
            if (
                next_i < len(meaningful)
                and meaningful[next_i].kind == "punct"
                and meaningful[next_i].value == "."
            ):
                next_i += 1
                if next_i < len(meaningful) and meaningful[next_i].kind in ("ident", "qident"):
                    table_part = normalize_ident(meaningful[next_i].value)
                    refs.append((schema_part, table_part))
                    expect_table = False
                    i = next_i + 1
                    prev_punct = None
                    continue
            refs.append(("", schema_part))
            expect_table = False
            prev_punct = None
            i = next_i
            continue
        if t.kind == "punct":
            if t.value == "(":
                # 保存外层状态，防止内层 FROM 污染括号后的字段或函数。
                paren_in_from.append(in_from)
                in_from = False
                expect_table = False
            elif t.value == ")":
                in_from = paren_in_from.pop() if paren_in_from else False
                expect_table = False
            prev_punct = t.value
        i += 1
    return refs


def _strip_trailing_semicolons(sql: str) -> str:
    s = sql.rstrip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _ref_allowed(ref: tuple[str, str], norm_whitelist: set[tuple[str, str]]) -> bool:
    if ref in norm_whitelist:
        return True
    # 未写 schema 时，允许命中白名单中任意同名表（依赖数据库 search_path）。
    if not ref[0] and any(table == ref[1] for _, table in norm_whitelist):
        return True
    return False


def validate_select_sql(sql: str, whitelist: Sequence[str]) -> tuple[str, list[tuple[str, str]]]:
    """校验并返回可执行的只读 SQL；未通过护栏检查时抛出 PgGuardError。"""
    cleaned = _strip_trailing_semicolons(sql)
    tokens = [t for t in tokenize_sql(cleaned) if t.kind not in ("ws", "comment")]
    if not tokens:
        raise PgGuardError("SQL 为空")
    first = tokens[0]
    if first.kind != "ident":
        raise PgGuardError("仅允许只读 SELECT 语句")
    if first.value.upper() != "SELECT":
        if first.value.upper() in _FORBIDDEN_FIRST_KEYWORDS:
            raise PgGuardError(
                f"仅允许只读 SELECT 语句，已拒绝语句类型: {first.value.upper()}"
            )
        raise PgGuardError("仅允许只读 SELECT 语句")
    if any(t.kind == "punct" and t.value == ";" for t in tokens):
        raise PgGuardError("仅允许单条语句，禁止使用分号")
    refs = extract_table_refs(tokens)
    norm_whitelist = {normalize_qualified(w) for w in whitelist}
    for ref in refs:
        if not _ref_allowed(ref, norm_whitelist):
            shown = ".".join(p for p in ref if p)
            raise PgGuardError(f"表不在白名单内，已拒绝: {shown}")
    return cleaned, refs


def enforce_limit(sql: str, limit: int) -> str:
    """强制 LIMIT：统一在外层包裹 LIMIT，防止超大结果集。"""
    cleaned = _strip_trailing_semicolons(sql).strip()
    return f"SELECT * FROM ( {cleaned} ) AS _pg_guard LIMIT {int(limit)}"


# ---------------------------------------------------------------- 审计


class JsonlAuditSink:
    """把查询审计日志逐条追加写入 JSONL 文件。"""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path

    async def write(self, entry: dict[str, Any]) -> None:
        if self.path is None:
            return
        await asyncio.to_thread(self._append, entry)

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 网关


_DEFAULT_DESCRIPTIONS = {
    "data.2026_1_change_landuse": (
        "2026 年第一期地类变化图斑（原土地类型 DLBM/DLMC、变化后图斑类型 TBLX、面积 MJ 平方米）"
    ),
}


class PgGateway:
    """受控 PostgreSQL/PostGIS 只读访问网关。

    连接池懒加载（首次查询时创建）；测试可注入 ``pool`` 替换真实连接池。
    """

    def __init__(
        self,
        dsn: str,
        whitelist: Sequence[str],
        max_rows: int = 200,
        timeout_s: float = 10.0,
        audit_sink: Any = None,
        pool: Any = None,
    ) -> None:
        self.dsn = dsn
        self.whitelist = list(whitelist)
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self.audit_sink = audit_sink
        self._pool = pool  # 测试注入用

    @property
    def configured(self) -> bool:
        return bool(self.dsn or self._pool is not None)

    async def _get_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=5,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def list_tables(self) -> list[dict[str, Any]]:
        """返回白名单表目录（schema / 表名 / 展示名 / 描述）。"""
        result: list[dict[str, Any]] = []
        for spec in self.whitelist:
            schema, table = normalize_qualified(spec)
            key = f"{schema}.{table}" if schema else table
            result.append(
                {
                    "schema": schema,
                    "table": table,
                    "name": f"{schema}.{table}" if schema else table,
                    "quoted": (
                        f"{quote_ident(schema)}.{quote_ident(table)}"
                        if schema
                        else quote_ident(table)
                    ),
                    "description": _DEFAULT_DESCRIPTIONS.get(key, ""),
                }
            )
        return result

    async def describe_table(
        self,
        table: str,
        conversation_id: str = "",
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        """查看白名单表的列结构（information_schema，参数化查询）。"""
        norm = normalize_qualified(table)
        norm_whitelist = {normalize_qualified(w) for w in self.whitelist}
        if not _ref_allowed(norm, norm_whitelist):
            shown = ".".join(p for p in norm if p)
            raise PgGuardError(f"表不在白名单内，已拒绝: {shown}")
        pool = await self._get_pool()
        schema, tname = norm
        if schema:
            sql = (
                "SELECT column_name, data_type, udt_name, is_nullable, "
                "character_maximum_length, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position"
            )
            args: tuple[Any, ...] = (schema, tname)
        else:
            sql = (
                "SELECT column_name, data_type, udt_name, is_nullable, "
                "character_maximum_length, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = $1 ORDER BY ordinal_position"
            )
            args = (tname,)
        async with pool.acquire() as conn:
            rows = await asyncio.wait_for(conn.fetch(sql, *args), self.timeout_s)
        return {"table": table, "columns": [dict(r) for r in rows]}

    async def schema_metadata(self) -> dict[str, Any]:
        """返回白名单表的完整元数据（表注释 + 列注释 + 主键 + 类型）。

        只读、参数化；供向量知识库构建脚本调用（不经 LLM 直接调用，不走护栏 SQL）。
        每次按 whitelist 逐表查询 information_schema 与 pg_catalog。
        """
        pool = await self._get_pool()
        tables: list[dict[str, Any]] = []
        async with pool.acquire() as conn:
            for spec in self.whitelist:
                schema, tname = normalize_qualified(spec)
                cols = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT column_name, data_type, udt_name, is_nullable, "
                        "character_maximum_length, numeric_precision, numeric_scale "
                        "FROM information_schema.columns "
                        "WHERE table_schema = $1 AND table_name = $2 "
                        "ORDER BY ordinal_position",
                        schema,
                        tname,
                    ),
                    self.timeout_s,
                )
                comments = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT a.attname AS column_name, d.description AS comment "
                        "FROM pg_catalog.pg_class c "
                        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_catalog.pg_attribute a "
                        "ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
                        "LEFT JOIN pg_catalog.pg_description d "
                        "ON d.objoid = c.oid AND d.objsubid = a.attnum "
                        "WHERE n.nspname = $1 AND c.relname = $2",
                        schema,
                        tname,
                    ),
                    self.timeout_s,
                )
                comment_by_col = {
                    row["column_name"]: (row["comment"] or "") for row in comments
                }
                trow = await asyncio.wait_for(
                    conn.fetchrow(
                        "SELECT obj_description(c.oid, 'pg_class') AS comment "
                        "FROM pg_catalog.pg_class c "
                        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = $1 AND c.relname = $2",
                        schema,
                        tname,
                    ),
                    self.timeout_s,
                )
                table_comment = (trow["comment"] or "") if trow else ""
                pks = await asyncio.wait_for(
                    conn.fetch(
                        "SELECT kcu.column_name "
                        "FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        "ON tc.constraint_name = kcu.constraint_name "
                        "AND tc.table_schema = kcu.table_schema "
                        "WHERE tc.constraint_type = 'PRIMARY KEY' "
                        "AND tc.table_schema = $1 AND tc.table_name = $2",
                        schema,
                        tname,
                    ),
                    self.timeout_s,
                )
                pk_cols = {row["column_name"] for row in pks}
                key = f"{schema}.{tname}" if schema else tname
                columns: list[dict[str, Any]] = []
                for col in cols:
                    cn = col["column_name"]
                    columns.append(
                        {
                            "column_name": cn,
                            "data_type": col["data_type"],
                            "udt_name": col["udt_name"],
                            "is_nullable": col["is_nullable"],
                            "character_maximum_length": col["character_maximum_length"],
                            "comment": comment_by_col.get(cn, ""),
                            "is_primary_key": cn in pk_cols,
                        }
                    )
                tables.append(
                    {
                        "schema": schema,
                        "table": tname,
                        "description": table_comment
                        or _DEFAULT_DESCRIPTIONS.get(key, ""),
                        "columns": columns,
                    }
                )
        return {"tables": tables}

    async def run_sql(
        self,
        sql: str,
        conversation_id: str = "",
        tool_call_id: str = "",
    ) -> dict[str, Any]:
        """执行受控只读查询，返回 {columns, rows, row_count, truncated}。"""
        if not self.configured:
            raise PgNotConfiguredError(
                "数据库未配置：请在后端 .env 设置 GEOAGENT_PG_DSN 后重启服务"
            )
        entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "conversation_id": conversation_id,
            "tool_call_id": tool_call_id,
            "sql": sql,
        }
        started = time.perf_counter()
        try:
            cleaned, refs = validate_select_sql(sql, self.whitelist)
            guarded = enforce_limit(cleaned, self.max_rows)
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await asyncio.wait_for(conn.fetch(guarded), self.timeout_s)
            data = [{k: _json_safe(v) for k, v in row.items()} for row in rows]
            entry.update(
                {
                    "sql_used": guarded,
                    "tables": [".".join(p for p in ref if p) for ref in refs],
                    "row_count": len(data),
                    "truncated": len(data) >= self.max_rows,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error": None,
                }
            )
            await self._audit(entry)
            return {
                "columns": list(data[0]) if data else [],
                "rows": data,
                "row_count": len(data),
                "truncated": len(data) >= self.max_rows,
            }
        except asyncio.TimeoutError:
            entry.update({"error": f"timeout after {self.timeout_s:g}s", "duration_ms": None})
            await self._audit(entry)
            raise PgTimeoutError(f"查询超时（{self.timeout_s:g} 秒）") from None
        except PgGuardError as exc:
            entry.update({"error": str(exc)})
            await self._audit(entry)
            raise
        except asyncpg.exceptions.UndefinedTableError as exc:
            entry.update({"error": f"UndefinedTableError: {exc}"})
            await self._audit(entry)
            raise PgQueryError(
                "表不存在，请检查：1) 表名/schema 是否正确；2) 以数字开头的表名必须"
                '加双引号，例如 data."2026_1_change_landuse"；3) 连接的数据库是否'
                "包含该表。"
            ) from exc
        except Exception as exc:
            entry.update({"error": f"{type(exc).__name__}: {exc}"})
            await self._audit(entry)
            raise PgQueryError(str(exc)) from exc

    async def _audit(self, entry: dict[str, Any]) -> None:
        if self.audit_sink is None:
            return
        try:
            await self.audit_sink.write(entry)
        except Exception:
            # 审计失败不影响查询结果。
            pass


# ---------------------------------------------------------------- 工具


class ListTablesParams(BaseModel):
    pass


class DescribeTableParams(BaseModel):
    table: str = Field(
        description='表名（带 schema），例如 data."2026_1_change_landuse"'
    )


class RunSqlParams(BaseModel):
    sql: str = Field(
        description="只读 SELECT 单语句（禁止 WITH/EXPLAIN/分号/写操作；自动强制 LIMIT）"
    )


def _get_gateway(ctx: Any) -> Optional[PgGateway]:
    return getattr(ctx, "pg", None)


def _no_gateway_result(name: str) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        name=name,
        content="数据库未接入：请在后端 .env 配置 GEOAGENT_PG_DSN 后重启服务。",
        is_error=True,
    )


@register_tool(
    "list_tables",
    "列出当前只读白名单内的数据库表（含表名与说明）",
    ListTablesParams,
)
async def list_tables(ctx: Any) -> ToolResult:
    gateway = _get_gateway(ctx)
    if gateway is None:
        return _no_gateway_result("list_tables")
    try:
        tables = await gateway.list_tables()
    except PgError as exc:
        return ToolResult(
            tool_call_id="", name="list_tables", content=str(exc), is_error=True
        )
    lines = ["可用数据库表:"]
    for t in tables:
        desc = f"（{t['description']}）" if t["description"] else ""
        lines.append(f"- {t['quoted']}{desc}")
    return ToolResult(
        tool_call_id="",
        name="list_tables",
        content="\n".join(lines),
        artifacts=[
            Artifact(
                kind="table",
                name="tables",
                data={
                    "columns": ["schema", "table", "name", "description"],
                    "rows": [
                        {k: t[k] for k in ("schema", "table", "name", "description")}
                        for t in tables
                    ],
                },
            )
        ],
    )


@register_tool(
    "describe_table",
    "查看白名单表的列结构（列名/类型/是否可空）",
    DescribeTableParams,
)
async def describe_table(ctx: Any, table: str) -> ToolResult:
    gateway = _get_gateway(ctx)
    if gateway is None:
        return _no_gateway_result("describe_table")
    try:
        info = await gateway.describe_table(
            table,
            conversation_id=getattr(ctx, "conversation_id", ""),
        )
    except PgError as exc:
        return ToolResult(
            tool_call_id="", name="describe_table", content=str(exc), is_error=True
        )
    columns = info["columns"]
    rows = [
        {
            "column": c["column_name"],
            "type": c["data_type"],
            "udt": c["udt_name"],
            "nullable": c["is_nullable"],
            "max_length": c["character_maximum_length"],
        }
        for c in columns
    ]
    lines = [f"表 {info['table']} 共 {len(columns)} 列:"]
    lines += [f"- {r['column']}: {r['type']} (nullable={r['nullable']})" for r in rows]
    return ToolResult(
        tool_call_id="",
        name="describe_table",
        content="\n".join(lines),
        artifacts=[
            Artifact(
                kind="table",
                name="table_columns",
                data={"columns": ["column", "type", "udt", "nullable", "max_length"], "rows": rows},
            )
        ],
    )


def _row_summary(columns: list[str], rows: list[dict[str, Any]]) -> str:
    if not columns:
        return "columns: (none)"
    parts = [f"columns: {', '.join(columns)}"]
    # 小结果全量展示，避免模型误把"前几行"当成全部；大结果只给前 10 行并提示剩余。
    max_rows = len(rows) if len(rows) <= 100 else 10
    for row in rows[:max_rows]:
        parts.append(" | ".join(str(row.get(c, ""))[:40] for c in columns))
    if len(rows) > max_rows:
        parts.append(f"...（结果共 {len(rows)} 行，仅显示前 {max_rows} 行，请用聚合查询获取全量）")
    return "\n".join(parts)


@register_tool(
    "run_sql",
    "在受控只读库上执行 SELECT 查询并返回表格（自动强制 LIMIT）",
    RunSqlParams,
)
async def run_sql(ctx: Any, sql: str) -> ToolResult:
    gateway = _get_gateway(ctx)
    if gateway is None:
        return _no_gateway_result("run_sql")
    try:
        result = await gateway.run_sql(
            sql,
            conversation_id=getattr(ctx, "conversation_id", ""),
        )
    except PgError as exc:
        return ToolResult(
            tool_call_id="",
            name="run_sql",
            content=f"SQL 查询失败: {exc}",
            is_error=True,
        )
    # 展示与摘要统一使用中文表头，并把 TBLX 编码翻译为中文名称。
    columns, rows = humanize_table(result["columns"], result["rows"])
    note = "（结果已截断至查询上限）" if result["truncated"] else ""
    content = f"查询返回 {result['row_count']} 行{note}。\n" + _row_summary(columns, rows)
    return ToolResult(
        tool_call_id="",
        name="run_sql",
        content=content,
        artifacts=[
            Artifact(
                kind="table",
                name="query_result",
                data={"columns": columns, "rows": rows},
            )
        ],
    )


def get_sql_tools() -> list[Any]:
    return get_tools("list_tables", "describe_table", "run_sql")
