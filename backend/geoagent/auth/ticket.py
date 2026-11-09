"""省域平台 ticket 校验：拿一次性 ticket 换用户信息。

参考《省域平台2.0浙政钉用户接入文档2023》：

- 场景跳转过来的 URL 携带 sykjzlptv2_ticket（可能在查询串，也可能在 # 之后的片段里）；
- GET {validate_url}?ticket=xxx 换取用户信息，ticket 只能使用一次、超时时间 2 分钟；
- 成功返回 {"result": {...}, "success": true, "message": "操作成功"}，
  失败返回 success=false 与提示语（ticket无效或超时 / ticket格式不正确）。

该接口本身没有 appKey/签名，安全性依赖 ticket 的一次性与短时效，因此这里只用于
“票据换用户信息”，持续鉴权使用本系统自己的会话凭据。
"""

from __future__ import annotations

from typing import Optional

import httpx

from .session import UserIdentity

DEFAULT_VALIDATE_URL = "https://kjzl.zrzyt.zj.gov.cn/bev2/api/Ticket/ValidateTicket"


class TicketError(Exception):
    """ticket 换取用户信息失败；status 直接作为回给前端的 HTTP 状态码。"""

    def __init__(self, message: str, *, status: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TicketValidator:
    """调用省域平台 ValidateTicket 接口校验 ticket。"""

    def __init__(
        self,
        url: str = DEFAULT_VALIDATE_URL,
        timeout_s: float = 5.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.url = url or DEFAULT_VALIDATE_URL
        self.timeout_s = timeout_s
        # 测试注入用：正式运行走 httpx 默认传输
        self._transport = transport

    async def validate(self, ticket: str) -> UserIdentity:
        value = (ticket or "").strip()
        if not value:
            raise TicketError("缺少 ticket", status=400)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, transport=self._transport
            ) as client:
                response = await client.get(self.url, params={"ticket": value})
        except httpx.HTTPError as exc:
            raise TicketError(f"无法连接省域平台校验 ticket：{exc}", status=502) from exc
        if response.status_code != 200:
            raise TicketError(f"省域平台返回异常状态 {response.status_code}", status=502)
        try:
            data = response.json()
        except ValueError as exc:
            raise TicketError("省域平台返回内容无法解析", status=502) from exc
        if not isinstance(data, dict):
            raise TicketError("省域平台返回内容无法解析", status=502)
        if not data.get("success"):
            message = str(data.get("message") or "").strip() or "ticket无效或超时"
            raise TicketError(message)
        result = data.get("result")
        if not isinstance(result, dict) or not result:
            raise TicketError("省域平台未返回用户信息")
        user = UserIdentity.from_platform(result)
        if not user.id:
            raise TicketError("省域平台未返回用户标识")
        return user
