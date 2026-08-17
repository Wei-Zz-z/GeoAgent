from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Artifact:
    """附加在工具结果上的结构化输出，用于会话窗口内的可视化渲染。

    kind: "geojson" | "table" | "text" | "image" | ...
    """

    kind: str
    data: Any
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "data": self.data, "name": self.name}


@dataclass
class ToolResult:
    """归一化的工具执行结果。"""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    artifacts: list[Artifact] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }
        if self.artifacts:
            message["artifacts"] = [a.to_dict() for a in self.artifacts]
        return message
