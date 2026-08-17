from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """一次对话轮次中发出的流式事件。

    事件类型：token、route、tool_call、tool_result、artifact、message、
    error、turn_start、turn_end。
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
