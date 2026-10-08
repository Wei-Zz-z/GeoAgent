"""受控 SQL 工具层测试（全部使用假连接池，不依赖真实数据库）。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from geoagent.core.llm import ToolCall
from geoagent.tools import ToolExecutor
from geoagent.config import DEFAULT_PG_WHITELIST
from geoagent.memory.store import ConversationStore
from geoagent.tools.pg import (
    PgGateway,
    PgGuardError,
    PgNotConfiguredError,
    PgTimeoutError,
    enforce_limit,
    extract_table_refs,
    get_sql_tools,
    normalize_qualified,
    tokenize_sql,
    validate_select_sql,
)
from geoagent.tools.labels import COLUMN_LABELS, TBLX_LABELS, humanize_table

WHITELIST = ['data."2026_1_change_landuse"', "knowledge_base.dict_tblx"]


@pytest.mark.parametrize("function", ["LEFT", "RIGHT"])
def test_guard_string_functions_before_coalesce_are_not_tables(function):
    sql = (f'SELECT {function}("XZQDM"::text,4) AS code, '
           'COALESCE(SUM("MJ"),0) AS area FROM data."2026_1_change_landuse" '
           f'GROUP BY {function}("XZQDM"::text,4)')
    _, refs = validate_select_sql(sql, WHITELIST)
    assert refs == [("data", "2026_1_change_landuse")]


@pytest.mark.parametrize("modifier", ["LEFT", "RIGHT", "FULL", "INNER", "CROSS", "NATURAL"])
def test_guard_join_modifiers_still_reject_unlisted_table(modifier):
    sql = f'SELECT 1 FROM data."2026_1_change_landuse" {modifier} JOIN private.secret ON true'
    with pytest.raises(PgGuardError, match="private.secret"):
        validate_select_sql(sql, WHITELIST)


class FakeConn:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.queries: list[str] = []
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        self.args.append(args)
        if self.error is not None:
            raise self.error
        return self.rows


class _Acquire:
    """模拟 asyncpg pool.acquire() 返回的异步上下文管理器。"""

    def __init__(self, conn: FakeConn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.closed = False

    def acquire(self):
        return _Acquire(self.conn)

    async def close(self):
        self.closed = True


class ListSink:
    def __init__(self):
        self.entries: list[dict] = []

    async def write(self, entry: dict):
        self.entries.append(entry)


class FakeCtx:
    def __init__(self, pg=None, conversation_id="conv-1"):
        self.pg = pg
        self.conversation_id = conversation_id


# ---------------------------------------------------------------- 词法与护栏


def test_column_labels_cover_core_fields():
    assert COLUMN_LABELS["OBJECTID"] == "主键"
    assert COLUMN_LABELS["XMC"] == "县级行政区名称"
    assert COLUMN_LABELS["TBLX"] == "图斑类型"
    assert COLUMN_LABELS["QSX"] == "前时相"
    assert COLUMN_LABELS["HSX"] == "后时相"
    assert COLUMN_LABELS["JCBH"] == "图斑编号"
    assert COLUMN_LABELS["DLMC"] == "原地类名称"
    assert COLUMN_LABELS["MJ"] == "面积(平方米)"
    assert COLUMN_LABELS["SFYN"] == "是否涉及永久基本农田"
    assert COLUMN_LABELS["SFHX"] == "是否涉及生态保护红线"


def test_tblx_labels_cover_codes_and_zero_padded_forms():
    assert TBLX_LABELS["DT"] == "动土"
    assert TBLX_LABELS["20"] == "建/构筑物"
    assert TBLX_LABELS["01"] == "耕地"
    assert TBLX_LABELS["1"] == "耕地"
    assert TBLX_LABELS["03"] == "林地"
    assert TBLX_LABELS["QT"] == "其他"


def test_humanize_table_translates_headers_and_tblx_values():
    columns = ["OBJECTID", "XMC", "TBLX", "MJ", "extra_col"]
    rows = [
        {
            "OBJECTID": 1,
            "XMC": "庆元县",
            "TBLX": "20",
            "MJ": 80.62,
            "extra_col": "keep-me",
        },
        {
            "OBJECTID": 2,
            "XMC": "庆元县",
            "TBLX": "DT",
            "MJ": 69.49,
            "extra_col": None,
        },
    ]
    labels, out_rows = humanize_table(columns, rows)
    assert labels == ["主键", "县级行政区名称", "图斑类型", "面积(平方米)", "extra_col"]
    assert out_rows[0]["图斑类型"] == "建/构筑物"
    assert out_rows[1]["图斑类型"] == "动土"
    assert out_rows[0]["主键"] == 1
    assert out_rows[0]["extra_col"] == "keep-me"


def test_config_default_whitelist_includes_main_and_dict_tables():
    assert DEFAULT_PG_WHITELIST[0] == 'data."2026_1_change_landuse"'
    assert "knowledge_base.dict_tblx" in DEFAULT_PG_WHITELIST
    assert "knowledge_base.dict_land_classification_summary" in DEFAULT_PG_WHITELIST
    assert len(DEFAULT_PG_WHITELIST) == 3


def test_normalize_qualified_handles_quoted_identifiers():
    assert normalize_qualified('data."2026_1_change_landuse"') == ("data", "2026_1_change_landuse")
    assert normalize_qualified('data."2026年第一期地类变化图斑表"') == (
        "data",
        "2026年第一期地类变化图斑表",
    )
    assert normalize_qualified("knowledge_base.dict_tblx") == ("knowledge_base", "dict_tblx")


def test_tokenizer_keeps_semicolons_inside_strings_and_comments():
    tokens = tokenize_sql("SELECT 'a;b' /* c;d */ FROM t -- e;f\n")
    punct = [t.value for t in tokens if t.kind == "punct"]
    assert punct == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE data.t SET a=1",
        "DELETE FROM data.t",
        "INSERT INTO data.t VALUES (1)",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SELECT * FROM data.t",
        "DROP TABLE data.t",
        "VALUES (1)",
        "SELECT 1; SELECT 2",
        "SELECT * FROM data.t; DROP TABLE data.t",
    ],
)
def test_guard_rejects_unsafe_sql(sql):
    with pytest.raises(PgGuardError):
        validate_select_sql(sql, WHITELIST)


def test_guard_allows_trailing_semicolon():
    cleaned, refs = validate_select_sql(
        "SELECT * FROM knowledge_base.dict_tblx;", WHITELIST
    )
    assert not cleaned.endswith(";")
    assert refs == [("knowledge_base", "dict_tblx")]


def test_guard_rejects_non_whitelisted_table():
    with pytest.raises(PgGuardError, match="白名单"):
        validate_select_sql("SELECT * FROM public.secret_table", WHITELIST)


def test_guard_rejects_join_with_non_whitelisted_table():
    with pytest.raises(PgGuardError, match="白名单"):
        validate_select_sql(
            "SELECT * FROM knowledge_base.dict_tblx JOIN public.other ON 1=1",
            WHITELIST,
        )


def test_guard_allows_whitelisted_tables():
    sql = (
        'SELECT * FROM data."2026_1_change_landuse" t '
        "JOIN knowledge_base.dict_tblx d ON t.dict_code = d.code"
    )
    cleaned, refs = validate_select_sql(sql, WHITELIST)
    assert ("data", "2026_1_change_landuse") in refs
    assert ("knowledge_base", "dict_tblx") in refs
    assert cleaned == sql


def test_guard_allows_schema_less_ref_matching_whitelist():
    _, refs = validate_select_sql("SELECT * FROM dict_tblx", WHITELIST)
    assert refs == [("", "dict_tblx")]


def test_guard_does_not_treat_aliases_or_subquery_labels_as_tables():
    sql = (
        "SELECT x.* FROM (SELECT * FROM knowledge_base.dict_tblx) x "
        'JOIN data."2026_1_change_landuse" AS d ON 1=1'
    )
    _, refs = validate_select_sql(sql, WHITELIST)
    assert ("", "x") not in refs
    assert ("", "d") not in refs
    assert len(refs) == 2


def test_guard_allows_dollar_quoted_string_with_semicolons():
    sql = "SELECT $$a;b$$ AS v FROM knowledge_base.dict_tblx"
    validate_select_sql(sql, WHITELIST)


def test_guard_restores_outer_select_after_scalar_subquery():
    sql = 'SELECT (SELECT COUNT(*) FROM knowledge_base.dict_tblx), ROUND(1.5, 1)'
    _, refs = validate_select_sql(sql, WHITELIST)
    assert refs == [("knowledge_base", "dict_tblx")]


def test_guard_restores_outer_from_and_checks_comma_join():
    sql = 'SELECT * FROM (SELECT * FROM knowledge_base.dict_tblx WHERE 1=1) x, public.secret_table'
    with pytest.raises(PgGuardError, match="secret_table"):
        validate_select_sql(sql, WHITELIST)


def test_guard_checks_nested_subquery_tables():
    sql = 'SELECT (SELECT (SELECT COUNT(*) FROM public.secret_table)), ROUND(1.5, 1)'
    with pytest.raises(PgGuardError, match="secret_table"):
        validate_select_sql(sql, WHITELIST)


def test_guard_rejects_empty_sql():
    with pytest.raises(PgGuardError):
        validate_select_sql("   -- 只有注释\n", WHITELIST)


def test_extract_table_refs_handles_comma_joins():
    refs = extract_table_refs(
        [t for t in tokenize_sql("FROM a, b, c WHERE 1=1") if t.kind not in ("ws", "comment")]
    )
    assert refs == [("", "a"), ("", "b"), ("", "c")]


def test_enforce_limit_wraps_query():
    wrapped = enforce_limit("SELECT 1", 10)
    assert wrapped == "SELECT * FROM ( SELECT 1 ) AS _pg_guard LIMIT 10"


# ---------------------------------------------------------------- 网关（假连接池）


def _gateway(pool: FakePool | None = None, sink: ListSink | None = None) -> PgGateway:
    return PgGateway(
        dsn="postgresql://user:pass@192.168.3.209:5432/land_change",
        whitelist=WHITELIST,
        max_rows=200,
        timeout_s=10,
        audit_sink=sink,
        pool=pool,
    )


@pytest.mark.asyncio
async def test_run_sql_enforces_limit_and_returns_rows():
    conn = FakeConn(rows=[{"dict_code": "0101", "name": "水田"}, {"dict_code": "0102", "name": "旱地"}])
    pool = FakePool(conn)
    gw = _gateway(pool)
    result = await gw.run_sql("SELECT dict_code, name FROM knowledge_base.dict_tblx")
    assert result["columns"] == ["dict_code", "name"]
    assert result["row_count"] == 2
    assert "LIMIT 200" in conn.queries[0]
    assert conn.queries[0].startswith("SELECT * FROM ( ")


@pytest.mark.asyncio
async def test_run_sql_normalizes_json_safe_values():
    conn = FakeConn(rows=[{"area_m2": Decimal("123.45"), "ts": datetime(2026, 8, 31, 10, 30)}])
    gw = _gateway(FakePool(conn))
    result = await gw.run_sql("SELECT 1")
    assert result["rows"][0] == {"area_m2": 123.45, "ts": "2026-08-31T10:30:00"}


def test_store_persists_decimal_artifact(tmp_path):
    store = ConversationStore(tmp_path)
    conv = store.create(title="t", model="m")
    store.add_message(
        conv["id"],
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "ok",
            "artifacts": [
                {
                    "kind": "table",
                    "name": "q",
                    "data": {
                        "columns": ["area"],
                        "rows": [{"area": Decimal("123.45")}],
                    },
                }
            ],
        },
    )
    messages = store.messages(conv["id"])
    row = messages[-1]["artifacts"][0]["data"]["rows"][0]
    assert row["area"] == 123.45


@pytest.mark.asyncio
async def test_run_sql_audits_success_and_failure():
    sink = ListSink()
    conn = FakeConn(rows=[{"a": 1}])
    gw = _gateway(FakePool(conn), sink)
    await gw.run_sql("SELECT a FROM knowledge_base.dict_tblx", conversation_id="c1")
    assert sink.entries and sink.entries[-1]["conversation_id"] == "c1"
    assert sink.entries[-1]["error"] is None

    with pytest.raises(PgGuardError):
        await gw.run_sql("SELECT * FROM public.bad", conversation_id="c2")
    assert sink.entries[-1]["error"] and "白名单" in sink.entries[-1]["error"]


@pytest.mark.asyncio
async def test_run_sql_timeout_raises_and_audits():
    sink = ListSink()
    conn = FakeConn(error=asyncio.TimeoutError())
    gw = _gateway(FakePool(conn), sink)
    with pytest.raises(PgTimeoutError, match="超时"):
        await gw.run_sql("SELECT a FROM knowledge_base.dict_tblx")
    assert sink.entries[-1]["error"] and "timeout" in sink.entries[-1]["error"]


@pytest.mark.asyncio
async def test_gateway_not_configured():
    gw = PgGateway(dsn="", whitelist=WHITELIST)
    with pytest.raises(PgNotConfiguredError, match="GEOAGENT_PG_DSN"):
        await gw.run_sql("SELECT 1")


@pytest.mark.asyncio
async def test_describe_table_checks_whitelist_and_queries_schema():
    conn = FakeConn(rows=[{"column_name": "id", "data_type": "integer"}])
    gw = _gateway(FakePool(conn))
    with pytest.raises(PgGuardError):
        await gw.describe_table("public.secret_table")

    info = await gw.describe_table('data."2026_1_change_landuse"')
    assert "information_schema.columns" in conn.queries[0]
    assert conn.args[0] == ("data", "2026_1_change_landuse")
    assert info["columns"][0]["column_name"] == "id"


@pytest.mark.asyncio
async def test_list_tables_returns_whitelist_catalog():
    gw = _gateway()
    tables = await gw.list_tables()
    assert len(tables) == 2
    assert tables[0]["quoted"] == 'data."2026_1_change_landuse"'
    assert tables[1]["quoted"] == "knowledge_base.dict_tblx"


# ---------------------------------------------------------------- 工具


@pytest.mark.asyncio
async def test_sql_tools_report_missing_gateway():
    executor = ToolExecutor(get_sql_tools())
    for name, args in (
        ("list_tables", {}),
        ("describe_table", {"table": "knowledge_base.dict_tblx"}),
        ("run_sql", {"sql": "SELECT 1"}),
    ):
        result = await executor.execute(
            ToolCall(id="c", name=name, arguments=args),
            ctx=FakeCtx(pg=None),
        )
        assert result.is_error
        assert "GEOAGENT_PG_DSN" in result.content


@pytest.mark.asyncio
async def test_sql_tools_run_with_fake_gateway():
    conn = FakeConn(
        rows=[
            {"dict_code": "0101", "name": "水田"},
            {"dict_code": "0102", "name": "旱地"},
        ]
    )
    ctx = FakeCtx(pg=_gateway(FakePool(conn)))
    executor = ToolExecutor(get_sql_tools())

    listed = await executor.execute(ToolCall(id="c1", name="list_tables", arguments={}), ctx=ctx)
    assert not listed.is_error
    assert listed.artifacts[0].kind == "table"
    assert len(listed.artifacts[0].data["rows"]) == 2

    conn.rows = [
        {
            "column_name": "dict_code",
            "data_type": "character varying",
            "udt_name": "varchar",
            "is_nullable": "NO",
            "character_maximum_length": 10,
        }
    ]
    described = await executor.execute(
        ToolCall(
            id="c2",
            name="describe_table",
            arguments={"table": "knowledge_base.dict_tblx"},
        ),
        ctx=ctx,
    )
    assert not described.is_error

    conn.rows = [
        {"dict_code": "0101", "name": "水田"},
        {"dict_code": "0102", "name": "旱地"},
    ]
    queried = await executor.execute(
        ToolCall(
            id="c3",
            name="run_sql",
            arguments={"sql": "SELECT * FROM knowledge_base.dict_tblx"},
        ),
        ctx=ctx,
    )
    assert not queried.is_error
    assert queried.artifacts[0].kind == "table"
    assert queried.artifacts[0].data["columns"] == ["dict_code", "name"]


@pytest.mark.asyncio
async def test_run_sql_tool_humanizes_result_table():
    conn = FakeConn(
        rows=[
            {"OBJECTID": 1, "XMC": "义乌市", "TBLX": "DT", "DLMC": "乔木林地", "MJ": 200.0},
            {"OBJECTID": 2, "XMC": "萧山区", "TBLX": "20", "DLMC": "公园与绿地", "MJ": 199.99},
        ]
    )
    ctx = FakeCtx(pg=_gateway(FakePool(conn)))
    executor = ToolExecutor(get_sql_tools())
    queried = await executor.execute(
        ToolCall(
            id="c9",
            name="run_sql",
            arguments={
                "sql": 'SELECT "OBJECTID", "XMC", "TBLX", "DLMC", "MJ" '
                'FROM data."2026_1_change_landuse"'
            },
        ),
        ctx=ctx,
    )
    assert not queried.is_error
    table = queried.artifacts[0].data
    assert table["columns"] == ["主键", "县级行政区名称", "图斑类型", "原地类名称", "面积(平方米)"]
    assert table["rows"][0]["图斑类型"] == "动土"
    assert table["rows"][1]["图斑类型"] == "建/构筑物"
    assert "图斑类型" in queried.content
