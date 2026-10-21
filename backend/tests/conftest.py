"""测试公共夹具。

会话存储默认使用内存实现：单元测试不依赖真实 PostgreSQL（见 AGENTS.md 测试规范）。
需要验证真实库时，在环境里显式设置 GEOAGENT_CHAT_STORE=postgres 与 GEOAGENT_CHAT_DSN。
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _memory_conversation_store():
    """整个测试会话强制使用内存会话存储。"""
    key = "GEOAGENT_CHAT_STORE"
    previous = os.environ.get(key)
    os.environ[key] = "memory"
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous
