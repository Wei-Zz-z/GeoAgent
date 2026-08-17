from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from openai import AsyncOpenAI

from ..config import ModelProfile, Settings


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
        try:
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_parts.append(delta.content)
                    if on_token is not None:
                        await on_token(delta.content)
                for i, tc in enumerate(delta.tool_calls or []):
                    slot = tool_slots.setdefault(
                        i, {"id": "", "function": {"name": "", "arguments": ""}}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["function"]["arguments"] += tc.function.arguments
        except Exception:
            if content_parts or tool_slots:
                raise
            # 部分提供商不支持流式（例如带工具时），回退到非流式调用。
            return await self.chat(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

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
