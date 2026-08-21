"""短期会话消息窗口：消息容器 + 工具调用历史合法性维护。

上下文压缩（大结果转存、旧消息归档、已读结果占位、LLM 摘要）统一由
`memory/compactor.py` 的 ContextCompactor 在每次调用模型前执行；
本模块只负责存放消息、归一化、构建 LLM 消息，以及清理孤儿 tool 消息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConversationSession:
    """有界的短期会话消息窗口（消息本身的压缩由 ContextCompactor 负责）。

    - 保持 OpenAI 工具调用历史的合法性（清理孤儿 tool 消息）
    - 绑定 ConversationStore 时支持写穿透持久化
    """

    store: Any = None
    conversation_id: Optional[str] = None
    _messages: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, message: dict[str, Any]) -> None:
        normalized = dict(message)
        self._messages.append(normalized)
        if self.store is not None and self.conversation_id:
            self.store.add_message(self.conversation_id, normalized)

    def add_user(self, content: str) -> None:
        self.add_message({"role": "user", "content": content})

    def add_tool(self, tool_message: dict[str, Any]) -> None:
        # 工具结果不在此截断：大结果由 ContextCompactor 按预算转存/占位处理。
        self.add_message(tool_message)

    def history(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def raw_messages(self) -> list[dict[str, Any]]:
        """返回内部消息列表本身，供压缩管线原地改写。"""
        return self._messages

    def clear(self) -> None:
        self._messages = []

    def build_llm_messages(
        self,
        system_prompt: Optional[str] = None,
        memory_context: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if memory_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "以下是与你当前问题相关的长期记忆，请按需参考，"
                        "若与用户当前要求冲突，以当前要求为准：\n"
                        f"{memory_context}"
                    ),
                }
            )
        messages.extend(
            {k: v for k, v in m.items() if k not in ("ts", "artifacts")}
            for m in self._messages
        )
        return messages

    def drop_orphan_tool_messages(self) -> None:
        """清理孤儿 tool 消息，保证 OpenAI 工具调用历史合法：

        没有对应 assistant tool_call 的 tool 消息会被删除，
        没有对应 tool 结果的 tool_call 也会被删除。
        """
        changed = True
        while changed:
            changed = False
            valid_ids = {
                tc.get("id")
                for m in self._messages
                if m.get("role") == "assistant"
                for tc in m.get("tool_calls", [])
                if tc.get("id")
            }
            kept = []
            for m in self._messages:
                if m.get("role") == "tool" and m.get("tool_call_id") not in valid_ids:
                    changed = True
                    continue
                kept.append(m)
            self._messages = kept

            pending_ids = {
                m.get("tool_call_id")
                for m in self._messages
                if m.get("role") == "tool" and m.get("tool_call_id")
            }
            for m in self._messages:
                if m.get("role") != "assistant" or not m.get("tool_calls"):
                    continue
                keep_calls = [tc for tc in m["tool_calls"] if tc.get("id") in pending_ids]
                if len(keep_calls) != len(m["tool_calls"]):
                    changed = True
                m["tool_calls"] = keep_calls
