"""入口验证接口：用省域平台 ticket 换本系统登录态。

- POST /api/auth/ticket  前端把 URL 上的 sykjzlptv2_ticket 交上来换登录态；
- GET  /api/auth/me      当前登录用户（前端启动时调用；dev 模式返回开发身份）；
- POST /api/auth/logout  退出登录（清 Cookie）。

ticket 只能使用一次且 2 分钟过期，因此这里立刻换成自包含的签名 Cookie（HttpOnly），
之后所有 /api 请求（含 WebSocket 握手）都靠它鉴权。凭据无服务端会话表，
退出登录是清 Cookie——如需强制失效，改 GEOAGENT_AUTH_SECRET 让旧凭据整体作废。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth.deps import scope_user
from ..auth.ticket import TicketError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TicketLoginBody(BaseModel):
    """前端从跳转 URL 上取到的 ticket。"""

    ticket: str = ""


def _set_session_cookie(response: Response, settings: Any, token: str, ttl_s: int) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=ttl_s,
        httponly=True,
        samesite="lax",
        secure=bool(settings.auth_cookie_secure),
        path="/",
    )


@router.post("/ticket")
async def login_with_ticket(
    body: TicketLoginBody,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """用一次性 ticket 换登录态；同一 ticket 在有效期内重复提交会复用已有登录态。"""
    state = request.app.state
    settings = state.settings
    signer = state.session_signer
    replay = state.ticket_replay
    ticket = (body.ticket or "").strip()
    if not ticket:
        raise HTTPException(status_code=400, detail="缺少 ticket")

    token = replay.get(ticket)
    user = signer.verify(token) if token else None
    if user is None:
        try:
            user = await state.ticket_validator.validate(ticket)
        except TicketError as exc:
            await state.auth_audit.record("login_failed", detail=exc.message)
            raise HTTPException(status_code=exc.status, detail=exc.message) from exc
        token = signer.issue(user)
        replay.put(ticket, token)

    _set_session_cookie(response, settings, token, signer.ttl_s)
    await state.auth_audit.record(
        "login",
        user_id=user.id,
        account=user.account,
        name=user.name,
        organization=user.organization,
        detail="ticket",
    )
    return {"user": user.to_dict(), "auth_mode": settings.auth_mode}


@router.get("/me")
async def me(request: Request, response: Response) -> dict[str, Any]:
    """当前登录用户；顺带把 Cookie 续到完整有效期（活跃用户不掉线）。"""
    state = request.app.state
    settings = state.settings
    user = scope_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="未登录或登录已过期，请从省域平台重新进入。",
        )
    if not user.is_dev:
        _set_session_cookie(
            response, settings, state.session_signer.issue(user), state.session_signer.ttl_s
        )
    return {"user": user.to_dict(), "auth_mode": settings.auth_mode}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    """退出登录：清掉 Cookie（凭据本身无状态，客户端删除即视为退出）。"""
    user = scope_user(request)
    if user is not None and not user.is_dev:
        await request.app.state.auth_audit.record(
            "logout", user_id=user.id, account=user.account, name=user.name
        )
    response.delete_cookie(request.app.state.settings.auth_cookie_name, path="/")
    return {"ok": True}
