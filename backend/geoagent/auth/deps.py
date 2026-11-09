"""鉴权依赖：路由与 WebSocket 里取当前登录用户、按用户取会话。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from .session import UserIdentity, dev_identity


def current_user(request: Request) -> UserIdentity:
    """FastAPI 依赖：当前登录用户（中间件已解析并挂到 request.state.user）。"""
    user = getattr(request.state, "user", None)
    if not isinstance(user, UserIdentity):
        raise HTTPException(
            status_code=401,
            detail="未登录或登录已过期，请从省域平台重新进入。",
        )
    return user


def scope_user(connection: Any) -> Optional[UserIdentity]:
    """取 HTTP/WebSocket 连接上的登录用户；未登录返回 None。"""
    user = getattr(getattr(connection, "state", None), "user", None)
    return user if isinstance(user, UserIdentity) else None


def request_user(request: Request) -> UserIdentity:
    """取当前请求的登录用户。

    中间件会给所有 /api 请求注入身份；未开启入口验证（dev 模式）时回退开发身份，
    保证内部调用与离线脚本仍然可用。
    """
    user = scope_user(request)
    if user is not None:
        return user
    settings = getattr(getattr(request.app, "state", None), "settings", None)
    if settings is None or not getattr(settings, "auth_enabled", False):
        return dev_identity()
    raise HTTPException(
        status_code=401,
        detail="未登录或登录已过期，请从省域平台重新进入。",
    )


async def owned_conversation(
    store: Any, conversation_id: str, user: UserIdentity
) -> Optional[dict[str, Any]]:
    """按登录用户取会话：不存在或不属于当前用户都返回 None。

    两种情况统一返回 None，是为了不暴露“别人存在这个会话”。
    """
    if not conversation_id:
        return None
    return await store.get(conversation_id, owner_id=user.id)
