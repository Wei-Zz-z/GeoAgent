"""套合数据管理：列出 data schema 下的套合表，并删除不再需要的期次。

删除是破坏性操作，所以这里的护栏是层层叠加的：

1. 只操作 ``data`` schema，且表名必须来自实际存在的表（不接受任意拼接）；
2. 必须把表名原样输入确认（前端二次确认），确认不一致直接拒绝；
3. 默认先调用 ``pg_dump`` 把整表备份到 ``<data_dir>/overlay_backups``，
   备份失败即中止，不会出现"备份没成功但表已经删了"；
4. 删除成功后同步摘掉 ``datasets.json`` 里的期次登记，并刷新运行时的
   期次表与只读白名单（前端"数据表"下拉框随即更新）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from ..datasets import DatasetRegistry
from .db import OverlayDatabase
from .registry import load_asset, remove_dataset

# 备份文件后缀：pg_dump 自定义格式，恢复用 pg_restore。
BACKUP_SUFFIX = ".dump"


def table_name_of(spec: str) -> str:
    """把 ``data."x"`` / ``data.x`` / ``x`` 统一成表名。"""
    text = str(spec or "").strip().replace('"', "")
    if not text:
        return ""
    return text.split(".")[-1].strip()


def _quoted(name: str) -> str:
    """安全地引用表名（只用于我们自己校验过的名字）。"""
    return '"' + str(name).replace('"', '""') + '"'


def _pg_dump_path() -> Optional[Path]:
    """定位 pg_dump：环境变量 → PATH → psql 同目录 → 常见安装目录。"""
    configured = os.getenv("GEOAGENT_PG_DUMP", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured)
    found = shutil.which("pg_dump") or shutil.which("pg_dump.exe")
    if found:
        return Path(found)
    psql = shutil.which("psql") or shutil.which("psql.exe")
    if psql:
        candidate = Path(psql).with_name("pg_dump.exe")
        if candidate.is_file():
            return candidate
    candidates: list[Path] = []
    for base in (os.getenv("ProgramFiles", ""), os.getenv("ProgramFiles(x86)", "")):
        if base:
            candidates.extend(sorted(Path(base).glob("PostgreSQL/*/bin/pg_dump.exe")))
    candidates.append(Path(r"D:\PostgreSQL\bin\pg_dump.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


async def backup_table(db: OverlayDatabase, table_name: str, directory: Path) -> Path:
    """用 pg_dump 把整表导出到备份目录；失败即抛错（调用方据此中止删除）。"""
    dump = _pg_dump_path()
    if dump is None:
        raise ValueError(
            "找不到 pg_dump，无法备份。可取消勾选“删除前备份”后直接删除（不可恢复），"
            "或设置 GEOAGENT_PG_DUMP 指向 pg_dump 可执行文件。"
        )
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = directory / f"{table_name}_{stamp}{BACKUP_SUFFIX}"
    command = [
        str(dump),
        "--dbname", db.dsn,
        "--table", f"data.{_quoted(table_name)}",
        "--format", "custom",
        "--no-owner",
        "--no-privileges",
        "--file", str(path),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
    except OSError as exc:
        raise ValueError(f"备份失败：无法执行 pg_dump（{exc}）。") from exc
    if process.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        detail = stderr.decode("utf-8", errors="replace").strip()[:300]
        raise ValueError(f"备份失败，已中止删除：{detail or f'pg_dump 退出码 {process.returncode}'}")
    return path


async def list_overlay_tables(db: OverlayDatabase, registry: DatasetRegistry) -> list[dict[str, Any]]:
    """列出 data schema 下的套合表：行数、面积、几何覆盖与期次登记情况。"""
    rows = await db.fetch(
        "SELECT c.relname AS table_name, obj_description(c.oid, 'pg_class') AS comment "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'data' AND c.relkind = 'r' ORDER BY c.relname"
    )
    registered = {table_name_of(period.table): period for period in registry.periods}
    items: list[dict[str, Any]] = []
    for row in rows:
        name = str(row["table_name"])
        columns = await db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'data' AND table_name = $1",
            name,
        )
        available = {str(column["column_name"]) for column in columns}
        parts = ["count(*) AS rows"]
        if "geom" in available:
            parts.append("count(*) FILTER (WHERE geom IS NOT NULL) AS with_geom")
        if "MJ" in available:
            parts.append('sum("MJ")::float8 AS area_m2')
        if "CREATE_AT" in available:
            parts.append('min("CREATE_AT") AS created_at')
        stats = await db.fetchrow(f"SELECT {', '.join(parts)} FROM data.{_quoted(name)}")
        period = registered.get(name)
        area_m2 = float((stats or {}).get("area_m2") or 0)
        items.append(
            {
                "table_name": name,
                "table_ref": f'data.{_quoted(name)}',
                "comment": (row["comment"] or ""),
                "rows": int((stats or {}).get("rows") or 0),
                "with_geom": int((stats or {}).get("with_geom") or 0),
                "area_m2": round(area_m2, 2),
                "area_mu": round(area_m2 * 0.0015, 2),
                "created_at": str((stats or {}).get("created_at") or ""),
                "registered": period is not None,
                "dataset_id": period.id if period else "",
                "label": period.label if period else "",
                "year": period.year if period else None,
                "round": period.round if period else None,
                "is_default": bool(period is not None and registry.default is not None
                                   and registry.default.id == period.id),
            }
        )
    return items


async def delete_overlay_dataset(
    db: OverlayDatabase,
    *,
    dataset_id: str = "",
    table_name: str = "",
    confirm: str = "",
    backup: bool = True,
    asset_path: Optional[Path] = None,
    backups_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """删除一期套合数据：备份（可选）→ 删表 → 摘掉期次登记。"""
    asset = load_asset(asset_path)
    entries: Sequence[dict[str, Any]] = list(asset.get("datasets") or [])
    entry: Optional[dict[str, Any]] = None
    wanted_id = str(dataset_id or "").strip()
    if wanted_id:
        entry = next((item for item in entries if str(item.get("id", "")).strip() == wanted_id), None)
        if entry is None:
            raise ValueError(f"没有登记过这个期次：{wanted_id}")
    target = table_name_of((entry or {}).get("table") or table_name)
    if not target:
        raise ValueError("请指定要删除的期次或表名。")
    if str(confirm or "").strip().lower() != target.lower():
        raise ValueError(f"确认失败：请把表名 {target} 原样输入后再删除。")

    exists = await db.table_exists("data", target)
    backup_path: Optional[Path] = None
    if exists and backup:
        backup_path = await backup_table(db, target, Path(backups_dir or Path.cwd() / "overlay_backups"))
    if exists:
        await db.execute(f"DROP TABLE IF EXISTS data.{_quoted(target)} CASCADE;")

    unregistered = False
    if entry is not None:
        unregistered = remove_dataset(str(entry.get("id", "")), path=asset_path)
    elif not exists:
        raise ValueError(f"表 data.{_quoted(target)} 不存在，也没有对应的期次登记。")

    return {
        "table_name": target,
        "table_ref": f"data.{_quoted(target)}",
        "dropped": bool(exists),
        "unregistered": bool(unregistered),
        "dataset_id": str((entry or {}).get("id", "")),
        "label": str((entry or {}).get("label", "")),
        "backup": str(backup_path) if backup_path else None,
    }