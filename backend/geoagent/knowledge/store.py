"""向量知识库存储层：统一的向量记录模型 + 文件 / pgvector 两种后端。

设计要点：

- 检索面（``KnowledgeBase``）只面对 ``VectorStore`` 接口，不关心后端实现；
- 默认文件后端（JSONL）零额外依赖、零数据库权限、可离线测试，适合小规模
  （数百条）知识库的线性扫描检索；
- pgvector 后端把向量持久化在 PostgreSQL 库内（``GEOAGENT_VECTOR_STORE=pgvector``
  时启用），需要建扩展 ``vector`` 与建表的权限（部署说明见仓库文档）；
- 两者都按 ``kind`` 分区（question / schema），检索时按 kind 过滤；
- ``VectorRecord`` 的 ``payload`` 必须是 JSON 可序列化的元数据，命中后原样返回给
  调用方（问题库条目 / schema 元数据）。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import asyncpg

from ..core.embedding import cosine_similarity


@dataclass
class VectorRecord:
    """知识库中的一条：id + 分区 + 原文 + 元数据 + 向量。"""

    id: str
    kind: str
    text: str
    payload: dict[str, Any]
    vector: list[float] = field(default_factory=list)


class VectorStoreError(RuntimeError):
    """向量存储后端初始化 / 写入失败。"""


class VectorStore(ABC):
    """向量存储统一接口（后端无关）。"""

    @abstractmethod
    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        """写入 / 覆盖一批记录（按 (kind, id) 幂等）。"""

    @abstractmethod
    async def search(
        self,
        vector: list[float],
        top_k: int,
        kind: Optional[str] = None,
    ) -> list[tuple[VectorRecord, float]]:
        """按余弦相似度检索，返回 (记录, 相似度) 降序列表。"""

    @abstractmethod
    async def count(self, kind: Optional[str] = None) -> int:
        """统计记录数（可按 kind 过滤）。"""

    @abstractmethod
    async def clear(self, kind: Optional[str] = None) -> None:
        """清空记录（可按 kind 过滤）。"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接等资源。"""


def _vector_to_pg_str(vector: list[float]) -> str:
    """把浮点向量转成 pgvector 可接受的字符串字面量 '[0.1,0.2,...]'。"""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class FileVectorStore(VectorStore):
    """文件后端：JSONL 落盘 + 进程内线性余弦检索。

    适用于数百条规模的知识库；数据文件默认在 ``GEOAGENT_DATA_DIR/knowledge/vectors.jsonl``。
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._records: dict[tuple[str, str], VectorRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = VectorRecord(
                id=str(item.get("id", "")),
                kind=str(item.get("kind", "")),
                text=str(item.get("text", "")),
                payload=dict(item.get("payload", {}) or {}),
                vector=[float(x) for x in (item.get("vector") or [])],
            )
            if record.id:
                self._records[(record.kind, record.id)] = record

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in self._records.values():
                f.write(
                    json.dumps(
                        {
                            "id": record.id,
                            "kind": record.kind,
                            "text": record.text,
                            "payload": record.payload,
                            "vector": record.vector,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        tmp.replace(self.path)

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        for record in records:
            self._records[(record.kind, record.id)] = record
        self._save()

    async def search(
        self,
        vector: list[float],
        top_k: int,
        kind: Optional[str] = None,
    ) -> list[tuple[VectorRecord, float]]:
        scored = [
            (record, cosine_similarity(vector, record.vector))
            for record in self._records.values()
            if kind is None or record.kind == kind
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    async def count(self, kind: Optional[str] = None) -> int:
        if kind is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.kind == kind)

    async def clear(self, kind: Optional[str] = None) -> None:
        if kind is None:
            self._records.clear()
        else:
            self._records = {
                key: r for key, r in self._records.items() if r.kind != kind
            }
        self._save()

    async def close(self) -> None:
        return None


class PgVectorStore(VectorStore):
    """pgvector 后端：向量持久化在 PostgreSQL 库内。

    使用独立连接池（与受控只读网关分离，构建时可能需要写权限账号）；
    表结构：``geoagent_kb.vectors(id, kind, text, payload jsonb, embedding vector)``。
    """

    TABLE = "geoagent_kb.vectors"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=1, max_size=3
            )
        return self._pool

    async def _ensure(self) -> None:
        """确保扩展、schema 与表存在（权限不足时抛 VectorStoreError）。"""
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute("CREATE SCHEMA IF NOT EXISTS geoagent_kb")
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                    "id text PRIMARY KEY,"
                    "kind text NOT NULL,"
                    "text text NOT NULL,"
                    "payload jsonb NOT NULL,"
                    "embedding vector"
                    ")"
                )
        except asyncpg.PostgresError as exc:
            raise VectorStoreError(
                f"pgvector 后端初始化失败（可能缺少建扩展/建表权限）: {exc}"
            ) from exc

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        await self._ensure()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for record in records:
                    await conn.execute(
                        f"INSERT INTO {self.TABLE} "
                        "(id, kind, text, payload, embedding) "
                        "VALUES ($1, $2, $3, $4::jsonb, $5::vector) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "kind = EXCLUDED.kind, text = EXCLUDED.text, "
                        "payload = EXCLUDED.payload, embedding = EXCLUDED.embedding",
                        record.id,
                        record.kind,
                        record.text,
                        json.dumps(record.payload, ensure_ascii=False),
                        _vector_to_pg_str(record.vector),
                    )

    async def search(
        self,
        vector: list[float],
        top_k: int,
        kind: Optional[str] = None,
    ) -> list[tuple[VectorRecord, float]]:
        await self._ensure()
        pool = await self._get_pool()
        vec_str = _vector_to_pg_str(vector)
        async with pool.acquire() as conn:
            if kind is not None:
                rows = await conn.fetch(
                    f"SELECT id, kind, text, payload, "
                    f"1 - (embedding <=> $1::vector) AS similarity "
                    f"FROM {self.TABLE} WHERE kind = $2 "
                    f"ORDER BY embedding <=> $1::vector LIMIT $3",
                    vec_str,
                    kind,
                    top_k,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT id, kind, text, payload, "
                    f"1 - (embedding <=> $1::vector) AS similarity "
                    f"FROM {self.TABLE} "
                    f"ORDER BY embedding <=> $1::vector LIMIT $2",
                    vec_str,
                    top_k,
                )
        return [
            (
                VectorRecord(
                    id=row["id"],
                    kind=row["kind"],
                    text=row["text"],
                    payload=json.loads(row["payload"]) if row["payload"] else {},
                ),
                float(row["similarity"]),
            )
            for row in rows
        ]

    async def count(self, kind: Optional[str] = None) -> int:
        await self._ensure()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if kind is not None:
                row = await conn.fetchrow(
                    f"SELECT count(*) AS n FROM {self.TABLE} WHERE kind = $1", kind
                )
            else:
                row = await conn.fetchrow(f"SELECT count(*) AS n FROM {self.TABLE}")
        return int(row["n"]) if row else 0

    async def clear(self, kind: Optional[str] = None) -> None:
        await self._ensure()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if kind is not None:
                await conn.execute(f"DELETE FROM {self.TABLE} WHERE kind = $1", kind)
            else:
                await conn.execute(f"DELETE FROM {self.TABLE}")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def create_store(settings: Any, mode: Optional[str] = None) -> VectorStore:
    """按配置选择后端。mode: file / pgvector / auto（默认）。

    - file：文件后端（最稳，零权限）；
    - pgvector：库内持久化，需要 ``GEOAGENT_PGVECTOR_DSN``（默认沿用 GEOAGENT_PG_DSN）；
    - auto：文件已构建过就用文件，否则有 pg DSN 就试 pgvector，最后回退文件。
    """
    m = (mode or getattr(settings, "vector_store", "auto") or "auto").strip()
    file_path = Path(getattr(settings, "knowledge_dir")) / "vectors.jsonl"
    if m == "file":
        return FileVectorStore(file_path)
    if m == "pgvector":
        dsn = getattr(settings, "pgvector_dsn", "")
        if not dsn:
            raise VectorStoreError(
                "pgvector 后端需要配置 GEOAGENT_PGVECTOR_DSN（或 GEOAGENT_PG_DSN）"
            )
        return PgVectorStore(dsn)
    # auto
    if file_path.is_file():
        return FileVectorStore(file_path)
    if getattr(settings, "pgvector_dsn", ""):
        return PgVectorStore(getattr(settings, "pgvector_dsn"))
    return FileVectorStore(file_path)
