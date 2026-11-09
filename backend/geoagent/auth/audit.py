"""登录审计日志（JSONL）：记录谁在何时以何种方式进入系统。

只记录登录结果与用户标识，不记录 ticket 等凭证本身。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class AuthAudit:
    """追加写入 JSONL 的登录审计；写失败只忽略，不阻断登录。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def record(
        self,
        event: str,
        *,
        user_id: str = "",
        account: str = "",
        name: str = "",
        organization: str = "",
        detail: str = "",
    ) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user_id": user_id,
            "account": account,
            "name": name,
            "organization": organization,
            "detail": detail,
        }
        try:
            await asyncio.to_thread(self._append, entry)
        except OSError:
            return

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
