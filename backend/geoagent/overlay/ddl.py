"""套合目标表的建表 / 索引 / 注释 SQL，结构与现有两期套合表完全一致。"""

from __future__ import annotations

import hashlib
import re

from .spec import SCHEMA, quote_table

# 列定义（名称, 类型, 中文注释）：照搬 2026_1 / 2026_2 两期表，geom 直接落在 4490。
COLUMN_DEFS: list[tuple[str, str, str]] = [
    ("OBJECTID", "bigint NOT NULL", "主键"),
    ("XZQDM", "character varying(6) NOT NULL", "县级行政区代码"),
    ("XMC", "character varying(30)", "县级行政区名称"),
    ("TBLX", "character varying(5)", "图斑类型"),
    ("TZ", "character varying(10)", "图斑特征"),
    ("QSX", "character varying(8)", "前时相"),
    ("HSX", "character varying(8)", "后时相"),
    ("DDTC", "character varying(50)", "单独图层名称"),
    ("BZ", "character varying(100)", "备注"),
    ("JSYDXZ", "character varying(50)", "建设用地性质"),
    ("JCBH", "character varying(50)", "图斑编号"),
    ("DLBM", "character varying(5)", "地类编码"),
    ("DLMC", "character varying(60)", "地类名称"),
    ("TBXHMC", "character varying(100)", "图斑细化名称"),
    ("ZZSXDM", "character varying(30)", "种植属性代码"),
    ("ZZSXMC", "character varying(100)", "种植属性名称"),
    ("CZCSXM", "character varying(4)", "城镇村属性码"),
    ("TBXHDM", "character varying(30)", "图斑细化代码"),
    ("SFYN", "character varying(10)", "是否涉及永农"),
    ("SFHX", "character varying(50)", "是否涉及红线"),
    ("MJ", "numeric(15,2)", "面积"),
    ("CREATE_AT", "timestamp without time zone DEFAULT now()", "记录创建时间"),
    ("UPDATE_AT", "timestamp without time zone DEFAULT now()", "记录最后修改时间"),
    ("geom", "public.geometry(MultiPolygon,4490)", "图斑几何（CGCS2000 地理坐标 EPSG:4490）"),
]

# 需要从套合结果写入的业务列（OBJECTID 由自增序列生成，时间列取默认值）。
INSERT_COLUMNS: tuple[str, ...] = (
    "XZQDM", "XMC", "TBLX", "TZ", "QSX", "HSX", "DDTC", "BZ", "JSYDXZ", "JCBH",
    "DLBM", "DLMC", "TBXHMC", "ZZSXDM", "ZZSXMC", "CZCSXM", "TBXHDM",
    "SFYN", "SFHX", "MJ",
)

_INDEX_SUFFIXES = ("tblx", "hsx", "qsx", "dlbm", "xzqdm_jcbh")


def _slug(table_name: str) -> str:
    """给索引/约束起名用的短标识（含内容哈希，避免不同表名撞名）。"""
    base = re.sub(r"[^a-z0-9_]", "", str(table_name).lower()) or "tbl"
    if not (base[0].isalpha() or base[0] == "_"):
        base = "t" + base
    digest = hashlib.md5(str(table_name).encode("utf-8")).hexdigest()[:6]
    return f"{base[:28]}_{digest}"


def create_table_sql(table_name: str) -> str:
    """建表语句：直接以 4490 的 MultiPolygon 建列，避免入库后再改列类型。"""
    table_ref = quote_table(table_name)
    columns = ",\n    ".join(f'"{name}" {type_sql}' for name, type_sql, _ in COLUMN_DEFS)
    sequence = f'{SCHEMA}."{table_name}_OBJECTID_seq"'
    return (
        f"CREATE TABLE {table_ref} (\n    {columns}\n);\n"
        f"ALTER TABLE {table_ref} ALTER COLUMN \"OBJECTID\" ADD GENERATED ALWAYS AS IDENTITY "
        f"(SEQUENCE NAME {sequence} START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1);"
    )


def drop_table_sql(table_name: str) -> str:
    """删除目标表（含自增序列）。"""
    return f"DROP TABLE IF EXISTS {quote_table(table_name)} CASCADE;"


def post_create_sql(table_name: str, table_ref: str | None = None) -> list[str]:
    """建表后的收尾：主键、索引、注释、更新时间触发器。"""
    ref = table_ref or quote_table(table_name)
    slug = _slug(table_name)
    statements = [
        f'ALTER TABLE {ref} ADD CONSTRAINT "{table_name}_pkey" PRIMARY KEY ("OBJECTID");',
        f'CREATE INDEX "idx_{slug}_geom" ON {ref} USING gist (geom);',
    ]
    for suffix in _INDEX_SUFFIXES:
        columns = '"XZQDM", "JCBH"' if suffix == "xzqdm_jcbh" else f'"{suffix.upper()}"'
        statements.append(
            f'CREATE INDEX "idx_{slug}_{suffix}" ON {ref} USING btree ({columns});'
        )
    statements.append(
        f"CREATE TRIGGER trg_update_time BEFORE UPDATE ON {ref} "
        "FOR EACH ROW EXECUTE FUNCTION public.fn_update_timestamp();"
    )
    statements.append(
        f"COMMENT ON TABLE {ref} IS '{table_name} 地类变化图斑表（套合生成）';"
    )
    for name, _, comment in COLUMN_DEFS:
        column_ref = "geom" if name == "geom" else f'"{name}"'
        statements.append(f"COMMENT ON COLUMN {ref}.{column_ref} IS '{comment}';")
    return statements


def insert_sql(table_name: str, frag_ref: str, source_epsg: int, target_epsg: int) -> str:
    """把套合结果写进目标表：面积已重算，几何统一转成目标坐标系。"""
    table_ref = quote_table(table_name)
    columns = ", ".join(f'"{name}"' for name in INSERT_COLUMNS)
    select = ", ".join(f"f.{name.lower()}" for name in INSERT_COLUMNS)
    return (
        f"INSERT INTO {table_ref} ({columns}, geom)\n"
        f"SELECT {select}, ST_Multi(ST_Transform(f.geom, {int(target_epsg)}))\n"
        f"FROM {frag_ref} f;"
    )