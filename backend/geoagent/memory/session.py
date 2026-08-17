from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


def _default_summarize(old_messages: list[dict[str, Any]]) -> str:
    lines = []
    for msg in old_messages:
        role = str(msg.get("role", "unknown"))
        content = str(msg.get("content", "")).strip().replace("\n", " ")
        if len(content) > 140:
            content = content[:140] + "..."
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


@dataclass
class ConversationSession:
    """有界的短期会话消息窗口，借鉴并改进了 poipoi-agent。

    - 保持 OpenAI 工具调用历史的合法性（清理孤儿 tool 消息）
    - 截断过长的工具/助手内容
    - 基于规则的滚动摘要（后续可注入 LLM 摘要器）
    - 绑定 ConversationStore 时支持写穿透持久化
    """

    max_messages: int = 30
    recent_window: int = 10
    tool_max_chars: int = 1200
    assistant_max_chars: int = 2000
    rolling_summary_max_chars: int = 4000
    store: Any = None
    conversation_id: Optional[str] = None
    summarizer: Optional[Callable[[list[dict[str, Any]]], str]] = None
    _messages: list[dict[str, Any]] = field(default_factory=list)
    _rolling_summary: str = ""

    def add_message(self, message: dict[str, Any]) -> None:
        normalized = self._normalize_message(message)
        self._messages.append(normalized)
        if self.store is not None and self.conversation_id:
            self.store.add_message(self.conversation_id, normalized)
        self._trim()

    def add_user(self, content: str) -> None:
        self.add_message({"role": "user", "content": content})

    def add_tool(self, tool_message: dict[str, Any]) -> None:
        content = str(tool_message.get("content", ""))
        if len(content) > self.tool_max_chars:
            head, tail = content[:900], content[-200:]
            content = (
                f"{head}\n...\n{tail}\n\n"
                f"[Tool output truncated: {len(tool_message.get('content', ''))} chars]"
            )
            tool_message = {**tool_message, "content": content}
        self.add_message(tool_message)

    def history(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages = []
        self._rolling_summary = ""

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
        if self._rolling_summary:
            messages.append(
                {
                    "role": "system",
                    "content": "以下是会话较早阶段的滚动摘要，请结合当前上下文使用：\n"
                    + self._rolling_summary,
                }
            )
        messages.extend(
            {k: v for k, v in m.items() if k not in ("ts", "artifacts")}
            for m in self._messages
        )
        return messages

    def _trim(self) -> None:
        if self.max_messages <= 0 or len(self._messages) <= self.max_messages:
            return
        self._rolling_summarize_old_messages()
        self._role_priority_prune()
        self._drop_orphan_tool_messages()
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]
            self._drop_orphan_tool_messages()

    def _normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(message)
        if (
            normalized.get("role") == "assistant"
            and len(str(normalized.get("content", ""))) > self.assistant_max_chars
        ):
            content = str(normalized["content"])
            normalized["content"] = (
                content[: self.assistant_max_chars]
                + f"\n\n[Assistant output truncated from {len(content)} chars]"
            )
        return normalized

    def _rolling_summarize_old_messages(self) -> None:
        keep_recent = max(1, min(self.recent_window, self.max_messages))
        if len(self._messages) <= keep_recent:
            return
        old_messages = self._messages[:-keep_recent]
        if not old_messages:
            return
        summarizer = self.summarizer or _default_summarize
        block = summarizer(old_messages)
        if block:
            if self._rolling_summary:
                self._rolling_summary = self._rolling_summary + "\n" + block
            else:
                self._rolling_summary = block
        if len(self._rolling_summary) > self.rolling_summary_max_chars:
            self._rolling_summary = self._rolling_summary[-self.rolling_summary_max_chars :]
        self._messages = self._messages[-keep_recent:]

    def _role_priority_prune(self) -> None:
        if len(self._messages) <= self.max_messages:
            return
        priority = {"system": 0, "user": 0, "tool": 1, "assistant": 2}
        indexed = list(enumerate(self._messages))
        indexed.sort(key=lambda item: (priority.get(str(item[1].get("role", "")), 3), -item[0]))
        keep = sorted(idx for idx, _ in indexed[: self.max_messages])
        self._messages = [self._messages[idx] for idx in keep]

    def _drop_orphan_tool_messages(self) -> None:
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
