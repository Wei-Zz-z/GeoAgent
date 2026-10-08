"""土地变化监测快报相关模块。

子模块按需导入，避免仅使用 DOCX 模板解析器时被迫加载数据库驱动。
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_briefing_docx", "compute_briefing_stats"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .briefing import build_briefing_docx, compute_briefing_stats

        return {
            "build_briefing_docx": build_briefing_docx,
            "compute_briefing_stats": compute_briefing_stats,
        }[name]
    raise AttributeError(name)
