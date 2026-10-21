"""套合流程编排：解析输入 → 落暂存表 → 切分套合 → 叠加标记 → 建表入库 → 登记期次。

口径（与现有两期套合表一致，已用抽稀数据逐条核对）：

- 一行 = 一块前时相碎片按后时相图斑边界切分后的结果；
- 母斑属性（图斑类型、前后时相、备注等）取自后时相整斑；
- 地类属性（地类编码/名称、图斑细化、种植属性、城镇村属性）取自前时相碎片；
- ``MJ`` = 碎片几何面积（平方米，展示层再换算为亩）；
- ``SFYN`` / ``SFHX`` = 与永久基本农田 / 生态保护红线**相交即算**；
- 几何统一转成 CGCS2000 地理坐标（EPSG:4490）的 MultiPolygon。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from .db import OverlayDatabase
from .ddl import create_table_sql, drop_table_sql, insert_sql, post_create_sql
from .layers import LayerData, check_text_widths, frame_records, read_layer
from .registry import register_dataset
from .spec import (
    LAYERS,
    STAGING_SCHEMA,
    TARGET_EPSG,
    OverlayOptions,
    layer_label,
)

# 进度回调：(阶段名, 百分比, 说明)
ProgressFn = Callable[[str, int, str], Awaitable[None]]

# 暂存表列（顺序即 COPY 列顺序），与目标表字段一一对应。
STAGING_COLUMNS: dict[str, tuple[str, ...]] = {
    "hsx": ("jcbh", "xzqdm", "xmc", "tblx", "tz", "qsx", "hsx", "ddtc", "bz", "jsydxz"),
    "qsx": ("jcbh", "xzqdm", "dlbm", "dlmc", "tbxhmc", "zzsxdm", "zzsxmc", "czcsxm", "tbxhdm"),
    "yjn": ("xzqdm",),
    "hx": ("xzqdm",),
}

# 需要做列宽校验的字段（与上层字段名对应）。
WIDTH_CHECK_FIELDS = (
    "XZQDM", "XMC", "TBLX", "TZ", "QSX", "HSX", "DDTC", "BZ", "JSYDXZ", "JCBH",
    "DLBM", "DLMC", "TBXHMC", "ZZSXDM", "ZZSXMC", "CZCSXM", "TBXHDM",
)


class OverlayError(Exception):
    """套合流程错误（面向用户的提示信息）。"""


@dataclass
class OverlayInputs:
    """一次套合的输入文件：按图层角色分组（同一图层可含 .shp/.dbf/.prj 多个文件）。"""

    hsx: list[Path] = field(default_factory=list)
    qsx: list[Path] = field(default_factory=list)
    yjn: list[Path] = field(default_factory=list)
    hx: list[Path] = field(default_factory=list)

    def role_files(self, role: str) -> list[Path]:
        return list(getattr(self, role, []) or [])

    def to_dict(self) -> dict[str, int]:
        return {role: len(self.role_files(role)) for role in LAYERS}


async def _noop(stage: str, percent: int, detail: str = "") -> None:
    return None


def _staging_table(job: str, role: str) -> str:
    """暂存表名：加字母前缀，避免以数字开头的标识符需要额外加引号。"""
    return f"o{job}_{role}"


def _staging_ref(job: str, role: str) -> str:
    return f"{STAGING_SCHEMA}.{_staging_table(job, role)}"


def _create_staging_sql(ref: str, columns: Sequence[str]) -> str:
    cols = ", ".join(f"{name} text" for name in columns)
    return (
        f"CREATE SCHEMA IF NOT EXISTS {STAGING_SCHEMA};\n"
        f"CREATE TABLE {ref} ({cols}, geom_wkb bytea);"
    )


def _fill_geometry_sql(ref: str, epsg: int) -> str:
    """把 WKB 转成几何：修复无效环、只保留面、丢掉空几何。"""
    return (
        f"ALTER TABLE {ref} ADD COLUMN geom geometry(Geometry,{int(epsg)});\n"
        f"UPDATE {ref} SET geom = ST_CollectionExtract("
        f"ST_MakeValid(ST_GeomFromWKB(geom_wkb, {int(epsg)})), 3);\n"
        f"DELETE FROM {ref} WHERE geom IS NULL OR ST_IsEmpty(geom);\n"
        f"ALTER TABLE {ref} DROP COLUMN geom_wkb;\n"
        f"CREATE INDEX ON {ref} USING gist (geom);"
    )


def _overlay_sql(frag_ref: str, hsx_ref: str, qsx_ref: str) -> str:
    """按后时相边界切分前时相碎片，并挂接母斑属性。"""
    return f"""
CREATE TABLE {frag_ref} AS
WITH cut AS (
    SELECT
        q.xzqdm, h.xmc, h.tblx, h.tz, h.qsx, h.hsx, h.ddtc, h.bz, h.jsydxz,
        coalesce(h.jcbh, q.jcbh) AS jcbh,
        q.dlbm, q.dlmc, q.tbxhmc, q.zzsxdm, q.zzsxmc, q.czcsxm, q.tbxhdm,
        (ST_Dump(ST_CollectionExtract(
            ST_MakeValid(ST_Intersection(h.geom, q.geom)), 3))).geom AS geom
    FROM {hsx_ref} h
    JOIN {qsx_ref} q
      ON h.xzqdm = q.xzqdm
     AND h.geom && q.geom
     AND ST_Intersects(h.geom, q.geom)
)
SELECT xzqdm, xmc, tblx, tz, qsx, hsx, ddtc, bz, jsydxz, jcbh,
       dlbm, dlmc, tbxhmc, zzsxdm, zzsxmc, czcsxm, tbxhdm,
       '否'::varchar(10) AS sfyn,
       '否'::varchar(50) AS sfhx,
       round(ST_Area(geom)::numeric, 2) AS mj,
       geom
FROM cut
WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom) AND ST_Area(geom) > 0;
"""


def _flag_sql(frag_ref: str, layer_ref: str, column: str) -> str:
    """与永农/红线相交的碎片打标记（相交即算）。"""
    return (
        f"UPDATE {frag_ref} f SET {column} = '是' "
        f"WHERE EXISTS (SELECT 1 FROM {layer_ref} t "
        f"WHERE t.geom && f.geom AND ST_Intersects(t.geom, f.geom));"
    )


def _frag_stats_sql(frag_ref: str) -> str:
    return (
        "SELECT count(*) AS rows, count(DISTINCT xzqdm) AS counties, "
        "coalesce(sum(mj), 0) AS area_m2, "
        "count(*) FILTER (WHERE sfyn = '是') AS sfyn_yes, "
        "count(*) FILTER (WHERE sfhx = '是') AS sfhx_yes "
        f"FROM {frag_ref}"
    )


def _target_stats_sql(table_ref: str) -> str:
    return (
        'SELECT count(*) AS rows, count(DISTINCT "XZQDM") AS counties, '
        'coalesce(sum("MJ"), 0) AS area_m2 FROM ' + table_ref
    )


async def run_overlay(
    inputs: OverlayInputs,
    options: OverlayOptions,
    db: OverlayDatabase,
    *,
    progress: Optional[ProgressFn] = None,
    asset_path: Optional[Path] = None,
) -> dict[str, Any]:
    """执行一次套合；失败时回滚目标表，暂存表始终清理。"""
    emit = progress or _noop
    started = time.perf_counter()
    job = uuid.uuid4().hex[:10]
    warnings: list[str] = list(options.warnings)
    target_ref = options.table_ref
    target_created = False
    staged: list[str] = []
    frag_ref = _staging_ref(job, "frag")

    async def report(stage: str, percent: int, detail: str = "") -> None:
        await emit(stage, percent, detail)

    try:
        await db.connect()
        await report("检查目标表", 3, target_ref)
        if await _target_exists(db, options):
            if not options.overwrite:
                raise OverlayError(
                    f"表 {target_ref} 已存在。请换一个表名，或勾选“覆盖已存在的表”。"
                )

        # 1) 读取输入图层
        await report("读取输入图层", 6, "核对字段、坐标系与编码")
        layers: dict[str, LayerData] = {}
        missing = [
            layer_label(role)
            for role, spec in LAYERS.items()
            if spec["required"] and not inputs.role_files(role)
        ]
        if missing:
            raise OverlayError("缺少必需图层：" + "、".join(missing))
        for role in LAYERS:
            files = inputs.role_files(role)
            if not files:
                continue
            await report("读取输入图层", 6, f"读取{layer_label(role)}")
            data = await asyncio.to_thread(read_layer, files, role)
            width_problems = check_text_widths(data.frame, WIDTH_CHECK_FIELDS)
            if width_problems:
                raise OverlayError(
                    f"{layer_label(role)} 属性超长，无法写入套合表：" + "；".join(width_problems)
                )
            warnings.extend(data.info.warnings)
            layers[role] = data

        # 2) 落暂存表
        await report("写入暂存表", 22, "导入 PostGIS 并建空间索引")
        for role, data in layers.items():
            ref = _staging_ref(job, role)
            columns = STAGING_COLUMNS[role]
            await db.execute(_create_staging_sql(ref, columns))
            staged.append(ref)
            records = await asyncio.to_thread(
                frame_records, data.frame, [c.upper() for c in columns]
            )
            await db.copy_records(
                STAGING_SCHEMA, _staging_table(job, role), list(columns) + ["geom_wkb"], records
            )
            await db.execute(_fill_geometry_sql(ref, data.info.epsg))
            data.info.rows = int(await db.fetchval(f"SELECT count(*) FROM {ref}"))
            await report("写入暂存表", 22 + 6, f"{layer_label(role)} {data.info.rows} 个要素")

        # 3) 切分套合
        await report("切分套合", 45, "按后时相边界切分前时相碎片")
        await db.execute(_overlay_sql(frag_ref, _staging_ref(job, "hsx"), _staging_ref(job, "qsx")))
        staged.append(frag_ref)
        frag_rows = int(await db.fetchval(f"SELECT count(*) FROM {frag_ref}") or 0)
        if frag_rows == 0:
            raise OverlayError("套合结果为空：请检查前后时相是否属于同一期、坐标系是否一致。")

        # 4) 叠加标记（标记完成后再统计，否则拿到的仍是标记前的 0）
        await report("叠加标记", 62, "标记永农/红线压盖并重算面积")
        for role, column, label in (("yjn", "sfyn", "永久基本农田"), ("hx", "sfhx", "生态保护红线")):
            if role in layers:
                await db.execute(_flag_sql(frag_ref, _staging_ref(job, role), column))
            else:
                warnings.append(f"未提供{label}图层，套合表中相关字段统一记为“否”。")
        partial = await db.fetchrow(_frag_stats_sql(frag_ref))

        # 5) 建表入库
        await report("建表入库", 76, f"创建 {target_ref}")
        if options.overwrite:
            await db.execute(drop_table_sql(options.table_name))
        await db.execute(create_table_sql(options.table_name))
        target_created = True
        for statement in post_create_sql(options.table_name):
            await db.execute(statement)
        await report("建表入库", 88, "写入套合结果并转换坐标系")
        await db.execute(
            insert_sql(
                options.table_name,
                frag_ref,
                layers["qsx"].info.epsg,
                TARGET_EPSG,
            )
        )

        # 6) 校验
        await report("校验结果", 95, "核对行数与面积")
        stats = await db.fetchrow(_target_stats_sql(target_ref))
        rows = int(stats["rows"] or 0)
        area_m2 = float(stats["area_m2"] or 0)
        frag_area = float(partial["area_m2"] or 0)
        if rows != int(partial["rows"] or 0):
            raise OverlayError(
                f"入库行数（{rows}）与套合结果（{int(partial['rows'])}）不一致，已回滚。"
            )
        if abs(area_m2 - frag_area) > 1.0:
            warnings.append(
                f"入库面积合计（{area_m2:.2f} 平方米）与套合结果（{frag_area:.2f}）存在差异。"
            )

        entry = options.to_dataset_entry()
        if options.register:
            await report("登记期次", 98, f"{entry['label']} → {entry['table']}")
            register_dataset(entry, path=asset_path, overwrite=options.overwrite)

        result = {
            "job_id": job,
            "table": target_ref,
            "table_name": options.table_name,
            "dataset_id": entry["id"],
            "label": entry["label"],
            "year": options.year,
            "round": options.round,
            "rows": rows,
            "counties": int(stats["counties"] or 0),
            "area_m2": round(area_m2, 2),
            "area_mu": round(area_m2 * 0.0015, 2),
            "sfyn_yes": int(partial["sfyn_yes"] or 0),
            "sfhx_yes": int(partial["sfhx_yes"] or 0),
            "layers": {role: data.info.to_dict() for role, data in layers.items()},
            "warnings": warnings,
            "registered": bool(options.register),
            "dataset": entry if options.register else None,
            "elapsed_s": round(time.perf_counter() - started, 2),
        }
        await report("完成", 100, f"{rows} 行已入库")
        return result
    except Exception:
        if target_created:
            try:
                await db.execute(drop_table_sql(options.table_name))
            except Exception:
                warnings.append("回滚目标表失败，请手工检查 " + target_ref)
        raise
    finally:
        await db.drop_tables(staged)


async def _target_exists(db: OverlayDatabase, options: OverlayOptions) -> bool:
    """目标表是否已存在（schema 固定为 data）。"""
    return await db.table_exists("data", options.table_name)