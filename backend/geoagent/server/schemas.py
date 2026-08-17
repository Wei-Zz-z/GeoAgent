from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ConversationCreate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class ModelSwitch(BaseModel):
    model: str


class UserMessage(BaseModel):
    content: str


class MessageOut(BaseModel):
    role: str
    content: str
    ts: str
    model: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
