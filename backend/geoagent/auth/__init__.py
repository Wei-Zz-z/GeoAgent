"""入口验证与用户体系：省域平台 ticket 换取本系统登录态。

对外导出登录身份（UserIdentity）、ticket 校验器（TicketValidator）、
会话凭据签发器（SessionSigner）与 /api 保护中间件（AuthMiddleware）。
"""

from .deps import current_user, owned_conversation, request_user, scope_user
from .middleware import AuthMiddleware
from .session import DEV_USER_ID, SessionSigner, TicketReplayCache, UserIdentity, dev_identity
from .ticket import TicketError, TicketValidator

__all__ = [
    "AuthMiddleware",
    "DEV_USER_ID",
    "SessionSigner",
    "TicketError",
    "TicketReplayCache",
    "TicketValidator",
    "UserIdentity",
    "current_user",
    "dev_identity",
    "owned_conversation",
    "request_user",
    "scope_user",
]
