"""会话存储：PostgreSQL 持久化（多用户、按登录用户归属）。

- ``conversations``：会话元数据（标题 / 模型 / 归属 / 时间）；
- ``conversation_messages``：每条消息一行，消息体按 jsonb 原样保存（与旧 JSONL 行等价），
  顺序按自增 id，会话删除时级联清理；
- 方法全部是 async：数据库 IO 不放在事件循环的阻塞路径上。Agent 循环仍是同步的内存
  操作，新增消息由 ``ConversationSession`` 暂存，每轮结束由调用方 ``flush()`` 落库；
- ``MemoryConversationStore`` 是同一接口的内存实现，仅用于单元测试与未配库的本地联调
  （``GEOAGENT_CHAT_STORE=memory``），进程重启即丢失。

本地 JSONL 存储已停用：``data/conversations/`` 下的历史文件不再读取，也不做迁移。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import asyncpg

logger = logging.getLogger("geoagent.chat")

# schema 名会直接拼进 SQL，只允许安全标识符
SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _json_default(obj: Any) -> Any:
    """jsonb 序列化的兜底：Decimal/时间转基础类型，避免工具结果写库崩溃。"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def summarize_title(content: str, limit: int = 20) -> str:
    """根据首条用户消息生成会话标题（去空白、超长截断加省略号）。"""
    text = " ".join(str(content).split())
    if not text:
        return "未命名会话"
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _is_placeholder_title(title: str) -> bool:
    """判断标题是否还是默认占位（新建会话尚未起标题）。"""
    text = (title or "").strip()
    if not text:
        return True
    if text in ("新会话", "新对话"):
        return True
    if text.startswith(("新会话 ", "新对话 ", "会话 ")):
        return True
    return False


def _row_to_conversation(row: Any) -> dict[str, Any]:
    """把会话行转成原先 JSONL 时代的字典结构（前端依赖这些字段名与时间格式）。"""
    return {
        "id": row["id"],
        "title": row["title"],
        "model": row["model"],
        "owner_id": row["owner_id"],
        "owner_name": row["owner_name"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def _init_connection(conn: Any) -> None:
    """连接初始化：jsonb 直接收发 Python 对象，免去手工序列化。"""
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda value: json.dumps(value, ensure_ascii=False, default=_json_default),
        decoder=json.loads,
        schema="pg_catalog",
    )


class ConversationStore:
    """PostgreSQL 会话存储：会话元数据 + 消息表。"""

    def __init__(
        self,
        dsn: str,
        schema: str = "geoagent",
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        self.dsn = (dsn or "").strip()
        if not self.dsn:
            raise ValueError("会话库连接串为空：请配置 GEOAGENT_CHAT_DSN 或 GEOAGENT_PG_DSN")
        if not SCHEMA_RE.match(schema or ""):
            raise ValueError(f"非法的会话库 schema 名：{schema!r}")
        self.schema = schema
        self._min_size = max(1, int(min_size))
        self._max_size = max(self._min_size, int(max_size))
        self._pool: Optional[asyncpg.Pool] = None
        # 待落库缓冲：Agent 循环是同步的，消息先暂存，每轮结束 flush
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._pending_title: dict[str, str] = {}

    @property
    def connected(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        """建立连接池并确保表结构存在（幂等；各方法内部按需调用）。"""
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            init=_init_connection,
        )
        try:
            await self._ensure_schema()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    async def _ensure_schema(self) -> None:
        """建表（幂等）。表结构与语义：一张会话表 + 一张消息表，消息按会话级联删除。"""
        schema = self.schema
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.conversations (
                    id          text PRIMARY KEY,
                    title       text NOT NULL,
                    model       text NOT NULL DEFAULT '',
                    owner_id    text NOT NULL DEFAULT '',
                    owner_name  text NOT NULL DEFAULT '',
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    updated_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS conversations_owner_created_idx
                    ON {schema}.conversations (owner_id, created_at DESC)
                """
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.conversation_messages (
                    id              bigserial PRIMARY KEY,
                    conversation_id text NOT NULL
                                    REFERENCES {schema}.conversations(id) ON DELETE CASCADE,
                    role            text NOT NULL DEFAULT '',
                    payload         jsonb NOT NULL,
                    created_at      timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS conversation_messages_conversation_idx
                    ON {schema}.conversation_messages (conversation_id, id)
                """
            )
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.mcp_calls (
                    id          bigserial PRIMARY KEY,
                    caller      text NOT NULL DEFAULT '',
                    user_id     text NOT NULL DEFAULT '',
                    dataset_id  text NOT NULL DEFAULT '',
                    agent_id    text NOT NULL DEFAULT '',
                    question    text NOT NULL,
                    answer      text NOT NULL DEFAULT '',
                    route       text NOT NULL DEFAULT '',
                    ok          boolean NOT NULL DEFAULT true,
                    error       text NOT NULL DEFAULT '',
                    duration_ms integer NOT NULL DEFAULT 0,
                    created_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS mcp_calls_created_idx
                    ON {schema}.mcp_calls (created_at DESC)
                """
            )
            await conn.execute(
                f"COMMENT ON TABLE {schema}.mcp_calls IS 'MCP 外部调用审计：只保留提问与最终回复'"
            )
            await conn.execute(
                f"COMMENT ON TABLE {schema}.conversations IS 'GeoAgent 会话元数据（owner_id 为省域平台用户 Id）'"
            )
            await conn.execute(
                f"COMMENT ON TABLE {schema}.conversation_messages IS 'GeoAgent 会话消息（payload 与旧 JSONL 行等价，按 id 排序）'"
            )

    async def _ensure_connected(self) -> None:
        if self._pool is None:
            await self.connect()

    # ---- 会话元数据 ----

    async def create(
        self,
        title: Optional[str] = None,
        model: str = "",
        owner_id: str = "",
        owner_name: str = "",
    ) -> dict[str, Any]:
        await self._ensure_connected()
        conversation_id = uuid4().hex[:12]
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                f"""
                INSERT INTO {self.schema}.conversations (id, title, model, owner_id, owner_name)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                conversation_id,
                title or f"会话 {conversation_id[:6]}",
                model,
                owner_id,
                owner_name,
            )
        return _row_to_conversation(row)

    async def get(
        self, conversation_id: str, owner_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """取会话；指定 owner_id 时，不属于该用户的会话按不存在处理。"""
        await self._ensure_connected()
        sql = f"SELECT * FROM {self.schema}.conversations WHERE id = $1"
        params: list[Any] = [conversation_id]
        if owner_id:
            # 改造前遗留的无主会话继续对登录用户可见，避免历史会话消失
            sql += " AND (owner_id = '' OR owner_id = $2)"
            params.append(owner_id)
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(sql, *params)
        return _row_to_conversation(row) if row else None

    async def list(self, owner_id: Optional[str] = None) -> list[dict[str, Any]]:
        """按创建时间倒序返回会话；指定 owner_id 时只返回本人会话。"""
        await self._ensure_connected()
        sql = f"SELECT * FROM {self.schema}.conversations"
        params: list[Any] = []
        if owner_id:
            sql += " WHERE owner_id = '' OR owner_id = $1"
            params.append(owner_id)
        sql += " ORDER BY created_at DESC, id DESC"
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(sql, *params)
        return [_row_to_conversation(row) for row in rows]

    async def set_model(self, conversation_id: str, model: str) -> Optional[dict[str, Any]]:
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            row = await conn.fetchrow(
                f"""
                UPDATE {self.schema}.conversations
                   SET model = $2, updated_at = now()
                 WHERE id = $1
                RETURNING *
                """,
                conversation_id,
                model,
            )
        return _row_to_conversation(row) if row else None

    async def _set_title_if_placeholder(
        self, conn: Any, conversation_id: str, content: str
    ) -> None:
        row = await conn.fetchrow(
            f"SELECT title FROM {self.schema}.conversations WHERE id = $1", conversation_id
        )
        if row is None or not _is_placeholder_title(row["title"]):
            return
        await conn.execute(
            f"""
            UPDATE {self.schema}.conversations
               SET title = $2, updated_at = now()
             WHERE id = $1
            """,
            conversation_id,
            summarize_title(content),
        )

    async def update_title_if_placeholder(
        self, conversation_id: str, content: str
    ) -> Optional[dict[str, Any]]:
        """若标题仍是占位，则用首条用户消息生成标题。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await self._set_title_if_placeholder(conn, conversation_id, content)
        return await self.get(conversation_id)

    async def delete(self, conversation_id: str) -> None:
        """删除会话（消息由外键级联删除）。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                f"DELETE FROM {self.schema}.conversations WHERE id = $1", conversation_id
            )

    # ---- 消息 ----

    def stage_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        """暂存一条待落库消息（同步，供 Agent 循环调用）。

        入队时做一层浅拷贝并补时间戳：后续上下文压缩会原地改写内存里的消息，
        落库内容应以消息产生时的样子为准。
        """
        entry = dict(message)
        entry.setdefault("ts", _utc_now_iso())
        self._pending.setdefault(conversation_id, []).append(entry)

    def stage_title(self, conversation_id: str, content: str) -> None:
        """暂存标题候选（仅第一条用户消息生效，落库时再判断占位）。"""
        if content and conversation_id not in self._pending_title:
            self._pending_title[conversation_id] = content

    async def flush(self, conversation_id: str) -> None:
        """把暂存的消息与标题写库；会话已删除时静默丢弃。"""
        messages = self._pending.pop(conversation_id, [])
        title = self._pending_title.pop(conversation_id, "")
        if not messages and not title:
            return
        await self._ensure_connected()
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                async with conn.transaction():
                    if messages:
                        await conn.executemany(
                            f"""
                            INSERT INTO {self.schema}.conversation_messages
                                (conversation_id, role, payload)
                            VALUES ($1, $2, $3)
                            """,
                            [
                                (conversation_id, str(item.get("role") or ""), item)
                                for item in messages
                            ],
                        )
                        await conn.execute(
                            f"UPDATE {self.schema}.conversations SET updated_at = now() WHERE id = $1",
                            conversation_id,
                        )
                    if title:
                        await self._set_title_if_placeholder(conn, conversation_id, title)
        except asyncpg.ForeignKeyViolationError:
            logger.warning("会话 %s 已不存在，本轮消息未落库", conversation_id)
            return

    async def add_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        """立即写入一条消息（供不经过 ConversationSession 的调用方使用）。"""
        self.stage_message(conversation_id, message)
        await self.flush(conversation_id)

    async def add_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
    ) -> None:
        """批量写入消息（可选带标题候选）。"""
        for item in messages or ():
            self.stage_message(conversation_id, item)
        if title:
            self.stage_title(conversation_id, title)
        await self.flush(conversation_id)

    async def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """按写入顺序返回会话的全部消息。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                f"""
                SELECT payload FROM {self.schema}.conversation_messages
                 WHERE conversation_id = $1
                 ORDER BY id
                """,
                conversation_id,
            )
        return [dict(row["payload"]) for row in rows]

    async def rollback_last_user_turn(self, conversation_id: str) -> Optional[dict[str, Any]]:
        """撤回最后一条用户消息及其后的全部回答；返回被撤回的用户消息。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    SELECT id, payload FROM {self.schema}.conversation_messages
                     WHERE conversation_id = $1 AND role = 'user'
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    conversation_id,
                )
                if row is None:
                    return None
                await conn.execute(
                    f"""
                    DELETE FROM {self.schema}.conversation_messages
                     WHERE conversation_id = $1 AND id >= $2
                    """,
                    conversation_id,
                    row["id"],
                )
                await conn.execute(
                    f"UPDATE {self.schema}.conversations SET updated_at = now() WHERE id = $1",
                    conversation_id,
                )
        return dict(row["payload"])


    async def record_mcp_call(
        self,
        *,
        question: str,
        answer: str = "",
        caller: str = "",
        user_id: str = "",
        dataset_id: str = "",
        agent_id: str = "",
        route: str = "",
        ok: bool = True,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """记录一次 MCP 调用（只保留提问与最终回复，供审计与追溯）。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.mcp_calls
                    (caller, user_id, dataset_id, agent_id, question, answer, route, ok, error, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                caller,
                user_id,
                dataset_id,
                agent_id,
                question,
                answer,
                route,
                ok,
                error,
                int(duration_ms),
            )


    async def mcp_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        """按时间倒序读取 MCP 调用记录（审计/自检查看）。"""
        await self._ensure_connected()
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            rows = await conn.fetch(
                f"""
                SELECT caller, user_id, dataset_id, agent_id, question, answer,
                       route, ok, error, duration_ms, created_at
                  FROM {self.schema}.mcp_calls
                 ORDER BY id DESC
                 LIMIT $1
                """,
                int(limit),
            )
        return [
            {
                "caller": row["caller"],
                "user_id": row["user_id"],
                "dataset_id": row["dataset_id"],
                "agent_id": row["agent_id"],
                "question": row["question"],
                "answer": row["answer"],
                "route": row["route"],
                "ok": row["ok"],
                "error": row["error"],
                "duration_ms": row["duration_ms"],
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]


class MemoryConversationStore:
    """内存会话存储：与 ConversationStore 同接口，供单元测试与未配库时使用。"""

    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._mcp_calls: list[dict[str, Any]] = []
        self._pending: dict[str, list[dict[str, Any]]] = {}
        self._pending_title: dict[str, str] = {}

    @property
    def connected(self) -> bool:
        return True

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create(
        self,
        title: Optional[str] = None,
        model: str = "",
        owner_id: str = "",
        owner_name: str = "",
    ) -> dict[str, Any]:
        conversation_id = uuid4().hex[:12]
        now = _utc_now_iso()
        conv = {
            "id": conversation_id,
            "title": title or f"会话 {conversation_id[:6]}",
            "model": model,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "created_at": now,
            "updated_at": now,
        }
        self._conversations[conversation_id] = conv
        return dict(conv)

    async def get(
        self, conversation_id: str, owner_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        if owner_id and conv.get("owner_id") and conv["owner_id"] != owner_id:
            return None
        return dict(conv)

    async def list(self, owner_id: Optional[str] = None) -> list[dict[str, Any]]:
        items = [
            dict(c)
            for c in self._conversations.values()
            if not owner_id or not c.get("owner_id") or c["owner_id"] == owner_id
        ]
        return sorted(items, key=lambda c: c["created_at"], reverse=True)

    async def set_model(self, conversation_id: str, model: str) -> Optional[dict[str, Any]]:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        conv["model"] = model
        conv["updated_at"] = _utc_now_iso()
        return dict(conv)

    async def update_title_if_placeholder(
        self, conversation_id: str, content: str
    ) -> Optional[dict[str, Any]]:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        if _is_placeholder_title(conv.get("title", "")):
            conv["title"] = summarize_title(content)
            conv["updated_at"] = _utc_now_iso()
        return dict(conv)

    async def delete(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)
        self._messages.pop(conversation_id, None)

    def stage_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        entry = dict(message)
        entry.setdefault("ts", _utc_now_iso())
        self._pending.setdefault(conversation_id, []).append(entry)

    def stage_title(self, conversation_id: str, content: str) -> None:
        if content and conversation_id not in self._pending_title:
            self._pending_title[conversation_id] = content

    async def flush(self, conversation_id: str) -> None:
        messages = self._pending.pop(conversation_id, [])
        title = self._pending_title.pop(conversation_id, "")
        if messages:
            if conversation_id not in self._conversations:
                logger.warning("会话 %s 已不存在，本轮消息未落库", conversation_id)
            else:
                self._messages.setdefault(conversation_id, []).extend(messages)
                self._conversations[conversation_id]["updated_at"] = _utc_now_iso()
        if title:
            await self.update_title_if_placeholder(conversation_id, title)

    async def add_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        self.stage_message(conversation_id, message)
        await self.flush(conversation_id)

    async def add_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
        title: str = "",
    ) -> None:
        for item in messages or ():
            self.stage_message(conversation_id, item)
        if title:
            self.stage_title(conversation_id, title)
        await self.flush(conversation_id)

    async def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._messages.get(conversation_id, [])]

    async def record_mcp_call(
        self,
        *,
        question: str,
        answer: str = "",
        caller: str = "",
        user_id: str = "",
        dataset_id: str = "",
        agent_id: str = "",
        route: str = "",
        ok: bool = True,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """记录一次 MCP 调用（内存实现，仅测试与本地联调用）。"""
        self._mcp_calls.append(
            {
                "caller": caller,
                "user_id": user_id,
                "dataset_id": dataset_id,
                "agent_id": agent_id,
                "question": question,
                "answer": answer,
                "route": route,
                "ok": ok,
                "error": error,
                "duration_ms": int(duration_ms),
                "created_at": _utc_now_iso(),
            }
        )

    async def mcp_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        """按时间倒序读取 MCP 调用记录（内存实现）。"""
        return [dict(item) for item in reversed(self._mcp_calls[-int(limit):])]

    async def rollback_last_user_turn(self, conversation_id: str) -> Optional[dict[str, Any]]:
        items = self._messages.get(conversation_id, [])
        index = -1
        for position in range(len(items) - 1, -1, -1):
            if items[position].get("role") == "user":
                index = position
                break
        if index < 0:
            return None
        removed = items[index]
        del items[index:]
        conv = self._conversations.get(conversation_id)
        if conv is not None:
            conv["updated_at"] = _utc_now_iso()
        return dict(removed)


def create_conversation_store(settings: Any) -> Any:
    """按配置创建会话存储。

    默认写 PostgreSQL（``GEOAGENT_CHAT_DSN``，未配置时沿用业务库 ``GEOAGENT_PG_DSN``）；
    显式 ``GEOAGENT_CHAT_STORE=memory`` 或未配置任何 DSN 时退回内存实现（重启丢数据）。
    """
    mode = str(getattr(settings, "chat_store", "postgres") or "postgres").strip().lower()
    if mode in ("memory", "mem", "none"):
        return MemoryConversationStore()
    dsn = str(getattr(settings, "chat_dsn", "") or "").strip()
    if not dsn:
        logger.warning(
            "未配置 GEOAGENT_CHAT_DSN / GEOAGENT_PG_DSN：会话改用内存存储，进程重启后会话丢失。"
        )
        return MemoryConversationStore()
    return ConversationStore(
        dsn=dsn,
        schema=str(getattr(settings, "chat_schema", "geoagent") or "geoagent"),
    )