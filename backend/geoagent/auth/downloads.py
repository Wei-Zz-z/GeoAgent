"""快报等文件的签名下载链接。

MCP 调用方没有浏览器 Cookie，不能直接访问 ``GET /api/files/reports/{文件名}``
（该接口默认受登录态保护）。这里给出一条带过期时间的签名链接：链接本身即可下载，
不需要把主令牌交给对方，过期后自动失效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any, Optional

# 与 server/routes.py 的下载路由保持一致
DOWNLOAD_PATH_PREFIX = "/api/files/reports/"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class FileDownloadSigner:
    """签发/校验下载链接签名（HMAC-SHA256，密钥与会话凭据同源）。"""

    def __init__(self, secret: str, ttl_s: int = 3600) -> None:
        self._secret = (secret or "").encode("utf-8")
        if not self._secret:
            raise ValueError("下载签名密钥不能为空")
        self.ttl_s = max(60, int(ttl_s))

    def issue(self, filename: str, *, now: Optional[float] = None) -> tuple[int, str]:
        """返回 (过期时间戳, 签名)。"""
        expires = int((time.time() if now is None else now) + self.ttl_s)
        return expires, self._sign(filename, expires)

    def verify(
        self,
        filename: str,
        expires: Any,
        signature: str,
        *,
        now: Optional[float] = None,
    ) -> bool:
        """校验签名与有效期；文件名被改、签名不符或已过期都返回 False。"""
        exp = _to_int(expires)
        if exp is None or exp <= (time.time() if now is None else now):
            return False
        if not signature or not filename:
            return False
        return hmac.compare_digest(self._sign(filename, exp), str(signature))

    def url(self, filename: str, *, base_url: str = "", now: Optional[float] = None) -> str:
        """拼出可下载的链接；base_url 为空时返回相对路径，由调用方补自己的域名。"""
        expires, signature = self.issue(filename, now=now)
        path = f"{DOWNLOAD_PATH_PREFIX}{filename}?e={expires}&s={signature}"
        return f"{base_url.rstrip('/')}{path}" if base_url else path

    def expires_at(self, *, now: Optional[float] = None) -> str:
        moment = (time.time() if now is None else now) + self.ttl_s
        return datetime.fromtimestamp(moment, tz=timezone.utc).isoformat()

    def _sign(self, filename: str, expires: int) -> str:
        message = f"{filename}:{expires}".encode("utf-8")
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        return _b64(digest)