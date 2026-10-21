"""把 GeoAgent 的能力以 MCP（Streamable HTTP）暴露给外部前端。

对外只有三个工具：

- ``ask``：无状态问答。调用方自带历史，我们只返回最终答案（不返回模型的思考过程
  与工具轨迹）；默认也不回传表格/图斑等大产物，需要时用 ``include_data`` 打开。
- ``generate_briefing``：按默认模板生成土地变化监测快报，返回带有效期的签名下载链接。
- ``list_datasets``：只读列出已登记期次，供上面两个工具的 ``dataset_id`` 使用。

与 ``mcp_server.py``（stdio、面向本机 MCP 客户端）的区别：这里面向网络上其他团队的
前端/服务，因此不接收本机文件路径、不依赖进程内的模板工作流状态，也不维护用户会话
（每次调用只往 ``mcp_calls`` 表记一条「提问 + 最终回复」）。
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context

from .agents.graph import DEFAULT_AGENT_ID, agent_catalog
from .config import Settings, _parse_mcp_tokens, load_dotenv
from .stateless import build_stateless_context, run_stateless_question
from .tools.registry import all_tools

logger = logging.getLogger("geoagent.mcp")


@dataclass(frozen=True)
class McpContext:
    """MCP 服务进程内的共享资源（与 create_app 装配一致）。"""

    settings: Settings
    app: Any


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[McpContext]:
    load_dotenv()
    # 延迟导入：避免 mcp_http 与 server.app 形成循环导入
    # （否则先导入本模块时 /mcp 会静默挂载失败）
    from .server.app import create_app

    settings = Settings()
    app = create_app(settings)
    try:
        yield McpContext(settings=settings, app=app)
    finally:
        gateway = getattr(app.state, "pg", None)
        if gateway is not None:
            await gateway.close()
        await app.state.knowledge.close()
        await app.state.store.close()


def _configured_tokens() -> dict[str, str]:
    """当前配置的 MCP 令牌（每次读取，便于本地改 .env 后重启即可生效）。"""
    return _parse_mcp_tokens(os.getenv("GEOAGENT_MCP_TOKENS", ""))


def _match_token(tokens: dict[str, str], token: str) -> str:
    """比对令牌并返回调用方名称（定时比较避免时序侧信道）。"""
    if not token:
        return ""
    for known, label in tokens.items():
        if hmac.compare_digest(str(known), token):
            return str(label)
    return ""


class GeoAgentTokenVerifier:
    """MCP 侧的令牌校验：用预共享令牌识别调用方（本系统没有 OAuth 授权服务器）。

    未配置任何令牌时一律拒绝（fail closed）——要对外开放 MCP 就必须先配 GEOAGENT_MCP_TOKENS。
    """

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        label = _match_token(_configured_tokens(), str(token or ""))
        if not label:
            return None
        return AccessToken(token=str(token), client_id=label, scopes=[], expires_at=None)


def _auth_settings() -> AuthSettings:
    """MCP 鉴权元数据；issuer 只是对外声明的标识（本项目用预共享令牌，不走 OAuth 流程）。"""
    base = os.getenv("GEOAGENT_PUBLIC_BASE_URL", "").strip().rstrip("/") or "http://127.0.0.1:8000"
    return AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp/",
        validate_token_resource=False,
    )


mcp = MCPServer(
    "GeoAgent 土地变化问答",
    lifespan=lifespan,
    token_verifier=GeoAgentTokenVerifier(),
    auth=_auth_settings(),
)


def streamable_http_app() -> Any:
    """返回可挂到 FastAPI 上的 MCP Streamable HTTP 应用（挂载路径见 server/app.py）。"""
    from mcp.server.transport_security import TransportSecuritySettings

    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def _caller_info(ctx: Any) -> tuple[str, str]:
    """取调用方标识（MCP 已鉴权的令牌标签）与透传的最终用户标识。"""
    caller = ""
    access = get_access_token()
    if access is not None:
        caller = str(getattr(access, "client_id", "") or "")
    try:
        request = getattr(ctx.request_context, "request", None)
    except Exception:
        return caller, ""
    state = getattr(request, "state", None)
    if not caller:
        caller = str(getattr(state, "mcp_caller", "") or "")
    headers = getattr(request, "headers", None)
    user_id = ""
    if headers is not None:
        try:
            user_id = str(headers.get("x-geoagent-user") or "")
        except Exception:
            user_id = ""
    return caller, user_id


def _briefing_tool() -> Any:
    for tool in all_tools():
        if tool.name == "generate_briefing":
            return tool
    raise RuntimeError("未找到 generate_briefing 工具")


@mcp.tool()
async def ask(
    ctx: Context[McpContext],
    question: str,
    history: Optional[list[dict[str, Any]]] = None,
    dataset_id: str = "",
    agent_id: str = "",
    include_data: bool = True,
) -> dict[str, Any]:
    """土地变化统计问答：把问题交给 GeoAgent，返回最终文字结论。

    history 为调用方维护的多轮上下文（形如 [{"role":"user","content":"..."}, ...]），
    只取最近若干条；不传即单轮问答。dataset_id 用 list_datasets 的返回值；
    留空时按问题文本识别期次，识别不到用默认期次。include_data=True 时额外返回
    表格/图表/图斑等结构化产物（体积可能较大）。本工具不返回模型的思考过程。
    """
    context: McpContext = ctx.request_context.lifespan_context
    caller, user_id = _caller_info(ctx)
    result = await run_stateless_question(
        context.app,
        question=question,
        history=history,
        dataset_id=dataset_id,
        agent_id=agent_id,
        caller=caller,
        user_id=user_id,
    )
    payload: dict[str, Any] = {
        "answer": result.answer,
        "route": result.route,
        "dataset_id": result.dataset_id,
        "is_error": result.is_error,
    }
    if include_data and result.artifacts:
        payload["artifacts"] = result.artifacts
    return payload


@mcp.tool()
async def generate_briefing(
    ctx: Context[McpContext],
    dataset_id: str = "",
) -> dict[str, Any]:
    """按默认模板生成土地变化监测快报（Word），返回可直接下载的签名链接。

    dataset_id 用 list_datasets 的返回值，留空用默认期次。链接带有效期，
    到期后需要重新调用本工具生成；文件保留在服务端报告目录中。
    """
    context: McpContext = ctx.request_context.lifespan_context
    app = context.app
    settings = context.settings
    datasets = app.state.datasets

    period = None
    wanted = str(dataset_id or "").strip()
    if wanted:
        period = datasets.get(wanted)
        if period is None:
            raise ValueError(f"未登记的数据期次：{wanted}")
    else:
        period = datasets.default
    if period is None:
        raise ValueError("没有可用的数据期次，请先在套合入库中登记一期数据。")

    scoped = build_stateless_context(app)
    scoped.dataset = period
    scoped.dataset_id = period.id
    result = await _briefing_tool().run(ctx=scoped)
    if getattr(result, "is_error", False):
        raise ValueError(str(getattr(result, "content", "") or "快报生成失败"))

    signer = app.state.download_signer
    files = []
    for artifact in getattr(result, "artifacts", []) or []:
        data = getattr(artifact, "data", None) or {}
        filename = str(data.get("filename") or "")
        if not filename:
            continue
        files.append(
            {
                "filename": filename,
                "kind": "pdf" if filename.lower().endswith(".pdf") else "docx",
                "url": signer.url(filename, base_url=settings.public_base_url),
            }
        )
    if not files:
        raise ValueError("快报文件未生成，请检查后端报告目录权限。")

    return {
        "period": {"id": period.id, "label": period.label},
        "summary": str(getattr(result, "content", "") or ""),
        "files": files,
        "expires_at": signer.expires_at(),
        "notice": "疑似违法占地为预警口径，是否违法需叠加执法与用地管理审批数据。",
    }


@mcp.tool()
async def list_datasets(ctx: Context[McpContext]) -> dict[str, Any]:
    """列出已登记的数据期次（一张套合表 = 一期），供 ask / generate_briefing 选择 dataset_id。"""
    context: McpContext = ctx.request_context.lifespan_context
    datasets = context.app.state.datasets
    return {
        "default_id": datasets.default_id,
        "datasets": [period.to_dict() for period in datasets.periods],
        "agents": agent_catalog(),
        "default_agent_id": DEFAULT_AGENT_ID,
    }