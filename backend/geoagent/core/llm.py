from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from openai import AsyncOpenAI

from ..config import ModelProfile, Settings
from .toolcall_guard import StreamLeakFilter


class LLMConfigurationError(RuntimeError):
    """模型配置缺失或错误时抛出的异常。"""


@dataclass
class ToolCall:
    """从助手消息中解析出的归一化工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]

    @classmethod
    def from_openai(cls, item: Any) -> "ToolCall":
        function = getattr(item, "function", None)
        name = getattr(function, "name", "") or ""
        raw_arguments = getattr(function, "arguments", "") or "{}"
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {}
        else:
            arguments = dict(raw_arguments or {})
        if not isinstance(arguments, dict):
            arguments = {}
        return cls(id=getattr(item, "id", "") or "", name=name, arguments=arguments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class AssistantMessage:
    """归一化的 LLM 响应，兼容 OpenAI 风格的 tool_calls。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    reasoning_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
            "model": self.model,
        }
        if self.tool_calls:
            data["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        return data


class LLMService:
    """OpenAI 兼容的 LLM 门面，支持模型切换。

    每个 (provider, base_url) 复用同一个 AsyncOpenAI 客户端；任何 OpenAI 兼容端点
    （OpenAI / 智谱 / DeepSeek / Ollama / 阿里千问 ...）都可注册为 config.py 中的
    ModelProfile。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}

    def profile(self, model: str) -> ModelProfile:
        return self.settings.profile(model)

    def _client(self, profile: ModelProfile) -> AsyncOpenAI:
        base_url = os.getenv("OPENAI_BASE_URL") or profile.base_url
        key = (profile.provider, base_url or "")
        if key not in self._clients:
            kwargs: dict[str, Any] = {"api_key": profile.api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._clients[key] = AsyncOpenAI(**kwargs)
        return self._clients[key]

    def _kwargs(
        self,
        profile: ModelProfile,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": profile.id,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        temp = temperature if temperature is not None else profile.temperature
        if temp is not None:
            kwargs["temperature"] = temp
        tokens = max_tokens if max_tokens is not None else profile.max_tokens
        if tokens is not None:
            kwargs["max_tokens"] = tokens
        return kwargs

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AssistantMessage:
        profile = self.profile(model)
        if not profile.api_key:
            raise LLMConfigurationError(
                f"Model '{model}' is not available: set env var {profile.api_key_env}"
            )
        client = self._client(profile)
        kwargs = self._kwargs(profile, messages, tools, temperature, max_tokens, stream=False)
        response = await client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        tool_calls = [ToolCall.from_openai(tc) for tc in (message.tool_calls or [])]
        return AssistantMessage(
            content=message.content or "",
            tool_calls=tool_calls,
            model=model,
            reasoning_content=getattr(message, "reasoning_content", None) or "",
        )

    async def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> AssistantMessage:
        """流式调用补全接口，边接收边累积内容与 tool_calls。"""
        profile = self.profile(model)
        if not profile.api_key:
            raise LLMConfigurationError(
                f"Model '{model}' is not available: set env var {profile.api_key_env}"
            )
        client = self._client(profile)
        kwargs = self._kwargs(profile, messages, tools, temperature, max_tokens, stream=True)

        content_parts: list[str] = []
        tool_slots: dict[int, dict[str, Any]] = {}
        # 前端逐字渲染的最小转发粒度：无论上游一次推多大一段，都切成小段转发，
        # 避免"卡片先出、正文结尾才一次性冒出来"的观感。
        token_chunk_size = 12
        pending_text = ""
        # 模型偶尔把工具调用写成正文里的 XML 标记（兼容端点未解析成结构化调用）；
        # 转发前先缓冲疑似标记，避免脏文本已经显示在前端。
        leak_filter = StreamLeakFilter()

        async def _forward(text: str) -> None:
            """先过泄漏过滤器，再按小段转发给前端。"""
            safe = leak_filter.feed(text)
            if safe:
                await _emit_tokens(safe)

        async def _emit_tokens(delta: str) -> None:
            """把上游文本分片切小后逐段转发给 on_token。"""
            nonlocal pending_text
            pending_text += delta
            while len(pending_text) >= token_chunk_size:
                piece, pending_text = (
                    pending_text[:token_chunk_size],
                    pending_text[token_chunk_size:],
                )
                if on_token is not None:
                    await on_token(piece)

        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_parts.append(delta.content)
                    await _forward(delta.content)
                for tc in delta.tool_calls or []:
                    # 用流内 index（缺失时退回 id）作为槽位，避免多个工具调用挤进同一槽。
                    index = getattr(tc, "index", None)
                    key = index if index is not None else (getattr(tc, "id", None) or 0)
                    slot = tool_slots.setdefault(
                        key, {"id": "", "function": {"name": "", "arguments": ""}}
                    )
                    if tc.id and not slot["id"]:
                        slot["id"] = tc.id
                    if tc.function:
                        name = tc.function.name or ""
                        # 部分兼容端点每个分片都会重复完整 name；只取首次、忽略重复，
                        # 避免把 "run_sql" 拼成 "run_sqlrun_sql"。
                        if name and not slot["function"]["name"]:
                            slot["function"]["name"] = name
                        if tc.function.arguments:
                            slot["function"]["arguments"] += tc.function.arguments
            if on_token is not None:
                await _forward(leak_filter.flush())
                if pending_text:
                    await on_token(pending_text)
        except Exception:
            if content_parts or tool_slots:
                # 已推送过部分内容时不再整体回退，避免与已流出的片段重复；
                # 先把缓冲里的尾段发出去再抛错，让前端保留已见到的正文。
                if on_token is not None:
                    await _forward(leak_filter.flush())
                    if pending_text:
                        await on_token(pending_text)
                raise
            # 部分提供商不支持流式（例如带工具时），回退到非流式调用；
            # 正文仍然切成小段转发，保证前端体验与真实流式一致。
            message = await self.chat(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if on_token is not None and message.content:
                for i in range(0, len(message.content), token_chunk_size):
                    await on_token(message.content[i : i + token_chunk_size])
            return message

        tool_calls = []
        for slot in tool_slots.values():
            raw_args = slot["function"]["arguments"]
            try:
                arguments = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(id=slot["id"], name=slot["function"]["name"], arguments=arguments)
            )
        return AssistantMessage(
            content="".join(content_parts),
            tool_calls=tool_calls,
            model=model,
        )
