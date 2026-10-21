from ..config import elder_care_enabled
from .executor import ToolExecutor
from .registry import Tool, all_tools, get_tools, register_tool
from .result import Artifact, ToolResult

# 导入各工具模块，使其中的工具在包导入时完成注册。
from . import builtin as _builtin  # noqa: E402,F401
from . import chart as _chart  # noqa: E402,F401
from . import pg as _pg  # noqa: E402,F401
from . import report as _report  # noqa: E402,F401
from . import stat as _stat  # noqa: E402,F401

if elder_care_enabled():
    # 养老场景关闭时不导入：accessibility 会把 geopandas/pandas/networkx
    # 整套分析栈拉进常驻内存，即使这个场景从未被调用过。
    from . import accessibility as _accessibility  # noqa: E402,F401

__all__ = [
    "ToolExecutor",
    "Tool",
    "all_tools",
    "get_tools",
    "register_tool",
    "Artifact",
    "ToolResult",
]
