"""会话切换竞态回归：切换/新建会话时不得带出旧会话的消息。

前端 store 的竞态用 Node 直接跑（stub 掉 fetch / WebSocket），用例在
``frontend/scripts/check-chat-store.mjs``；本机没有 Node 时跳过，不阻塞其它测试。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "frontend" / "scripts" / "check-chat-store.mjs"
REGISTER = Path(__file__).resolve().parents[2] / "frontend" / "scripts" / "esm-register.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node，跳过前端 store 回归")
def test_chat_store_switching_keeps_conversations_isolated() -> None:
    assert SCRIPT.is_file() and REGISTER.is_file(), "缺少前端回归脚本"
    completed = subprocess.run(
        ["node", "--import", "./scripts/esm-register.mjs", "./scripts/check-chat-store.mjs"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "会话切换回归失败：\n" + (completed.stdout or "") + (completed.stderr or "")
    )
    assert "全部通过" in completed.stdout