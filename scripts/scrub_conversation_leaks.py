"""清洗历史会话里被写进正文的工具调用标记（数据库版）。

背景：部分 OpenAI 兼容端点偶发未把工具调用解析成结构化 ``tool_calls``，标记被当作
正文落库（例如 ``<function_calls><invoke name="run_sql">...``）。这类文本会一直留在
会话历史里，并可能被模型模仿。会话已改为存 PostgreSQL，本脚本扫描
``<schema>.conversation_messages`` 里 role='assistant' 的消息，剥掉标记、保留可读正文；
写入前把受影响行备份成 JSONL。

用法（在后端目录下执行）：
    uv run python ../scripts/scrub_conversation_leaks.py          # 预览
    uv run python ../scripts/scrub_conversation_leaks.py --apply  # 实际写入
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 允许从仓库任意目录运行：把 backend 加入导入路径。
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import asyncpg  # noqa: E402

from geoagent.config import Settings, load_dotenv  # noqa: E402
from geoagent.core.toolcall_guard import has_leaked_call, scrub_leaked_calls  # noqa: E402
from geoagent.memory.store import _init_connection  # noqa: E402

# 整条消息只是一个发不出去的标记时，替换成与运行时一致的说明文本。
FALLBACK_NOTICE = "（本轮模型未按工具调用格式返回结果，已中止。请重试或换一种问法。）"


def scrub_payload(payload: dict) -> tuple[dict, bool]:
    """处理一条消息；返回 (新消息, 是否发生修改)。"""
    if not isinstance(payload, dict) or payload.get("role") != "assistant":
        return payload, False
    content = payload.get("content")
    if not isinstance(content, str) or not has_leaked_call(content):
        return payload, False
    cleaned = scrub_leaked_calls(content)
    updated = dict(payload)
    updated["content"] = cleaned if cleaned.strip() else FALLBACK_NOTICE
    return updated, True


async def run(apply: bool) -> int:
    load_dotenv()
    settings = Settings()
    dsn = (settings.chat_dsn or "").strip()
    schema = settings.chat_schema
    if not dsn:
        print("未配置会话库连接串（GEOAGENT_CHAT_DSN / GEOAGENT_PG_DSN），无法清洗。")
        return 1

    conn = await asyncpg.connect(dsn=dsn)
    try:
        await _init_connection(conn)
        rows = await conn.fetch(
            f"SELECT id, conversation_id, payload FROM {schema}.conversation_messages "
            "WHERE role = 'assistant' ORDER BY id"
        )
        targets = []
        for row in rows:
            updated, modified = scrub_payload(row["payload"])
            if modified:
                targets.append((row, updated))

        if targets and apply:
            backup = settings.data_dir / f"scrub_backup_{datetime.now():%Y%m%d%H%M%S}.jsonl"
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("w", encoding="utf-8") as handle:
                for row, _ in targets:
                    handle.write(
                        json.dumps(
                            {
                                "id": row["id"],
                                "conversation_id": row["conversation_id"],
                                "payload": row["payload"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            print(f"已备份 {len(targets)} 条原始消息：{backup}")
            for row, updated in targets:
                await conn.execute(
                    f"UPDATE {schema}.conversation_messages SET payload = $2 WHERE id = $1",
                    row["id"],
                    updated,
                )

        print(f"扫描 {len(rows)} 条助手消息，命中 {len(targets)} 条。")
        for row, _ in targets[:20]:
            print(f"  id={row['id']} conversation={row['conversation_id']}")
        if len(targets) > 20:
            print(f"  ...另有 {len(targets) - 20} 条")
        print("已写入。" if apply else "预览模式，未写入；加 --apply 执行清洗。")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="清洗会话历史里的文本化工具调用标记（数据库）")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
