"""登录身份、会话凭据签发/校验与 ticket 复用缓存。

会话凭据（Cookie 值）是自包含的签名串：HMAC-SHA256 签名 + 载荷内过期时间，
服务端不维护会话表——这样后端重启不会把在线用户挤下线，也不需要额外的清理任务。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


def _text(value: Any) -> str:
    """平台字段可能为 null，或带首尾空格（如示例里的 Id），统一洗成字符串。"""
    return "" if value is None else str(value).strip()


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class UserIdentity:
    """一次登录对应的用户信息，字段取自省域平台 ValidateTicket 的 result。"""

    id: str
    name: str = ""
    account: str = ""
    user_name: str = ""
    organization: str = ""
    organization_line: str = ""
    xzqh: str = ""
    xzqh_code: str = ""
    nodes_auth: str = ""
    role: Optional[int] = None
    last_login_time: str = ""
    is_dev: bool = False

    @classmethod
    def from_platform(cls, result: dict[str, Any]) -> "UserIdentity":
        """按接入文档的字段名解析用户信息（EmployeeCode 已脱敏，不作唯一标识）。"""
        return cls(
            id=_text(result.get("Id")),
            name=_text(result.get("Name")),
            account=_text(result.get("Account")),
            user_name=_text(result.get("UserName")),
            organization=_text(result.get("OrganizationName")),
            organization_line=_text(result.get("OrganizationLine")),
            xzqh=_text(result.get("Xzqh")),
            xzqh_code=_text(result.get("XzqhCode")),
            nodes_auth=_text(result.get("NodesAuth")),
            role=_to_int(result.get("Role")),
            last_login_time=_text(result.get("LastLoginTime")),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserIdentity":
        """从会话凭据载荷还原身份（字段为签名时的内部名）。"""
        role = data.get("role")
        return cls(
            id=_text(data.get("id")),
            name=_text(data.get("name")),
            account=_text(data.get("account")),
            user_name=_text(data.get("user_name")),
            organization=_text(data.get("organization")),
            organization_line=_text(data.get("organization_line")),
            xzqh=_text(data.get("xzqh")),
            xzqh_code=_text(data.get("xzqh_code")),
            nodes_auth=_text(data.get("nodes_auth")),
            role=role if isinstance(role, int) else _to_int(role),
            last_login_time=_text(data.get("last_login_time")),
            is_dev=bool(data.get("is_dev")),
        )

    @property
    def display_name(self) -> str:
        """界面上展示的名字：优先姓名，其次账号。"""
        return self.name or self.account or self.user_name or self.id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# dev 模式（未开启入口验证）下的占位身份，保持改造前的开发联调体验。
DEV_USER_ID = "dev"


def dev_identity() -> UserIdentity:
    return UserIdentity(
        id=DEV_USER_ID,
        name="开发模式",
        account="dev",
        organization="本地联调",
        is_dev=True,
    )


def service_identity(label: str) -> UserIdentity:
    """机器调用方的服务身份（MCP 令牌 / 签名下载链接），不参与 web 会话隔离。"""
    name = str(label or "service").strip() or "service"
    return UserIdentity(id=f"mcp:{name}", name=name, account=name, organization="外部调用方")


class SessionSigner:
    """签发/校验登录态 Cookie 值，形如 base64url(载荷).签名。"""

    def __init__(self, secret: str, ttl_s: int) -> None:
        self._secret = (secret or "").encode("utf-8")
        if not self._secret:
            raise ValueError("会话签名密钥不能为空")
        self.ttl_s = max(60, int(ttl_s))

    def issue(self, user: UserIdentity, *, now: Optional[float] = None) -> str:
        issued = time.time() if now is None else now
        payload = {
            "u": user.to_dict(),
            "iat": int(issued),
            "exp": int(issued) + self.ttl_s,
        }
        body = _b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str, *, now: Optional[float] = None) -> Optional[UserIdentity]:
        """校验凭据；签名不符、格式损坏或已过期都返回 None。"""
        if not token or "." not in token:
            return None
        body, _, signature = token.rpartition(".")
        if not hmac.compare_digest(self._sign(body), signature):
            return None
        try:
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        moment = time.time() if now is None else now
        expires = _to_int(payload.get("exp"))
        if expires is None or expires <= moment:
            return None
        data = payload.get("u")
        if not isinstance(data, dict) or not _text(data.get("id")):
            return None
        return UserIdentity.from_dict(data)

    def _sign(self, body: str) -> str:
        digest = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        return _b64encode(digest)


def load_session_secret(configured: str, data_dir: Path) -> str:
    """会话签名密钥：优先显式配置，否则在数据目录里持久化生成（重启后登录态不失效）。"""
    value = (configured or "").strip()
    if value:
        return value
    path = Path(data_dir) / "auth_secret.key"
    try:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        generated = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        # 数据目录不可写时不阻塞启动：密钥只在本次进程内有效
        return secrets.token_urlsafe(48)


class TicketReplayCache:
    """同一 ticket 在有效期内重复提交时复用已签发的凭据。

    ticket 只能用一次且 2 分钟过期：用户刷新页面会重放 URL 上的 ticket，
    若每次都去平台校验就会拿到 ticket无效或超时，看起来像登录失败。
    """

    def __init__(self, ttl_s: float = 120.0) -> None:
        self.ttl_s = float(ttl_s)
        self._items: dict[str, tuple[float, str]] = {}

    def get(self, ticket: str, *, now: Optional[float] = None) -> str:
        self._purge(now=now)
        item = self._items.get(ticket)
        return item[1] if item else ""

    def put(self, ticket: str, token: str, *, now: Optional[float] = None) -> None:
        self._purge(now=now)
        self._items[ticket] = (time.time() if now is None else now, token)

    def _purge(self, *, now: Optional[float] = None) -> None:
        deadline = (time.time() if now is None else now) - self.ttl_s
        for key in [key for key, (ts, _) in self._items.items() if ts < deadline]:
            self._items.pop(key, None)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
