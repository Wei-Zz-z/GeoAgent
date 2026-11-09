"""入口验证中间件：只负责网页登录态（Cookie 会话）。

- 保护 /api（REST 与 WebSocket 共用同一份 Cookie 登录态）；
- 白名单：健康检查、登录接口，以及**自己负责鉴权**的接口——
  `/mcp` 由 MCP 层用令牌校验，快报下载由路由校验「登录态或签名链接」；
- 未开启入口验证（``GEOAGENT_AUTH_MODE=dev``）时注入开发身份，行为与改造前一致；
- WebSocket 不在这里拒绝：前端需要收到明确的错误事件，因此只解析登录态放进 scope，
  由 WS 路由判断并关闭连接。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from .downloads import DOWNLOAD_PATH_PREFIX
from .session import UserIdentity, dev_identity

# 无需登录即可访问的接口：健康检查与登录相关接口本身
PUBLIC_PATHS = ("/api/health",)
PUBLIC_PATH_PREFIXES = ("/api/auth/",)
# 自己校验凭据的接口（中间件只放行，不代它判断）
SELF_AUTHENTICATED_PREFIXES = (DOWNLOAD_PATH_PREFIX,)


class AuthMiddleware:
    """纯 ASGI 中间件，同时覆盖 http 与 websocket scope。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        user = self._resolve_user(scope, scope["app"].state)
        # Starlette 的 Request.state / WebSocket.state 读取 scope["state"]
        scope["state"] = {**scope.get("state", {}), "user": user}

        if scope["type"] == "http" and user is None and not self._is_public(scope):
            await self._unauthorized(send)
            return
        await self.app(scope, receive, send)

    def _resolve_user(self, scope: dict[str, Any], state: Any) -> Optional[UserIdentity]:
        settings = getattr(state, "settings", None)
        if settings is None or not getattr(settings, "auth_enabled", False):
            return dev_identity()
        signer = getattr(state, "session_signer", None)
        if signer is None:
            return None
        token = _cookie_value(scope, getattr(settings, "auth_cookie_name", "geoagent_session"))
        return signer.verify(token) if token else None

    @staticmethod
    def _is_public(scope: dict[str, Any]) -> bool:
        # 浏览器跨域预检不带凭据，放行交给 CORS 中间件处理
        if scope.get("method") == "OPTIONS":
            return True
        path = scope.get("path", "")
        if not path.startswith("/api"):
            # /mcp 等非 /api 路径由各自的接口自行鉴权
            return True
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES):
            return True
        return path.startswith(SELF_AUTHENTICATED_PREFIXES)

    @staticmethod
    async def _unauthorized(
        send: Any, detail: str = "未登录或登录已过期，请从省域平台重新进入。"
    ) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def _cookie_value(scope: dict[str, Any], name: str) -> str:
    """从原始 scope 头里取 Cookie（http 请求与 websocket 握手都适用）。"""
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers") or ()
    for key, value in headers:
        if key != b"cookie":
            continue
        for item in value.decode("latin-1").split(";"):
            key_part, _, raw = item.strip().partition("=")
            if key_part == name:
                return raw.strip()
    return ""