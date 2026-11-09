"""套合专用的 PostgreSQL/PostGIS 写入句柄。

问答链路走的是只读受控网关（``tools/pg.py``）；套合需要建表、写数据、建索引，
因此单独开一个写入句柄，并且只在管理端（上传页面 / 命令行）使用。
连接串同样来自环境变量 ``GEOAGENT_PG_DSN``，不硬编码账号密码。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import asyncpg


class OverlayDbError(Exception):
    """套合数据库错误基类。"""


class OverlayDatabase:
    """按需创建连接池的写入句柄；一个套合任务用完即关。"""

    def __init__(self, dsn: str) -> None:
        self.dsn = str(dsn or "").strip()
        self._pool: Any = None

    @property
    def configured(self) -> bool:
        return bool(self.dsn)

    async def connect(self) -> None:
        if not self.configured:
            raise OverlayDbError(
                "数据库未配置：请在后端 .env 设置 GEOAGENT_PG_DSN（套合需要写入权限）。"
            )
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=1, max_size=2, command_timeout=None
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, sql: str, *args: Any) -> str:
        await self.connect()
        async with self._pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        await self.connect()
        async with self._pool.acquire() as conn:
            return list(await conn.fetch(sql, *args))

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        await self.connect()
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        await self.connect()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def copy_records(
        self,
        schema: str,
        table: str,
        columns: Sequence[str],
        records: Iterable[Sequence[Any]],
    ) -> None:
        """批量写入（COPY）。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.copy_records_to_table(
                    table, records=records, columns=list(columns), schema_name=schema
                )

    async def table_exists(self, schema: str, table: str) -> bool:
        return bool(
            await self.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = $1 AND table_name = $2)",
                schema,
                table,
            )
        )

    async def drop_tables(self, refs: Iterable[str]) -> None:
        """尽力清理中间表；清理失败不影响主流程结果。"""
        for ref in refs:
            try:
                await self.execute(f"DROP TABLE IF EXISTS {ref} CASCADE;")
            except Exception:
                continue