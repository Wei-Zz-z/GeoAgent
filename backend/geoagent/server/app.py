from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..auth.audit import AuthAudit
from ..auth.downloads import FileDownloadSigner
from ..auth.middleware import AuthMiddleware
from ..auth.session import SessionSigner, TicketReplayCache, load_session_secret
from ..auth.ticket import TicketValidator
from ..config import Settings
from ..core.embedding import EmbeddingService
from ..core.llm import LLMService
from ..knowledge.kb import KnowledgeBase
from ..memory.store import create_conversation_store
from ..skills import SkillLoader
from ..tools.pg import JsonlAuditSink, PgGateway
from .auth_routes import router as auth_router
from .routes import router
from .template_routes import router as template_router
from .county_routes import router as county_router
from .overlay_routes import router as overlay_router


logger = logging.getLogger("geoagent.server")


def _mcp_session_manager() -> Any:
    """取 MCP Streamable HTTP 的会话管理器。

    挂载在 /mcp 的子应用不会自动执行自己的 lifespan，必须由宿主应用启动它，
    否则 MCP 请求会因任务组未初始化而失败。
    """
    try:
        from ..mcp_http import mcp as mcp_server

        return mcp_server.session_manager
    except Exception as exc:  # pragma: no cover - 仅在依赖缺失等异常情况下触发
        logger.warning("MCP 会话管理器不可用：%s", exc)
        return None


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 会话库：连接并确保表结构（内存实现为空操作）。建库失败不阻塞启动，
        # 由各接口在真正读写时抛出明确错误。
        try:
            await app.state.store.connect()
        except Exception as exc:  # pragma: no cover - 依赖真实数据库
            logger.warning("会话库初始化失败，会话将无法持久化：%s", exc)
        manager = _mcp_session_manager()
        try:
            if manager is None:
                yield
            else:
                async with manager.run():
                    yield
        finally:
            pg: Optional[PgGateway] = getattr(app.state, "pg", None)
            if pg is not None:
                await pg.close()
            await app.state.knowledge.close()
            await app.state.store.close()

    # 没加载 .env 时的典型症状：数据库与模型 key 都拿不到。这里显式提示，避免静默跑成
    # 「会话不落库 + 模型 401」——这正是 `uvicorn ...`（不带 --env-file）最容易踩的坑。
    default_profile = settings.model_registry.get(settings.default_model)
    if not settings.pg_dsn and (default_profile is None or not default_profile.api_key):
        logger.warning(
            "配置看起来是空的（既没有 GEOAGENT_PG_DSN，也没有模型 key）："
            "后端可能没有加载 .env；请用 uv run --env-file .env uvicorn "
            "geoagent.server.app:app 启动，或把这些变量写进服务/容器的环境变量。"
        )

    app = FastAPI(title="GeoAgent", version="0.1.0", lifespan=lifespan,
                  docs_url=None,redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.llm = LLMService(settings)
    app.state.embeddings = EmbeddingService(settings)
    app.state.knowledge = KnowledgeBase(settings, app.state.embeddings)
    app.state.datasets = settings.datasets
    app.state.store = create_conversation_store(settings)
    app.state.skills = SkillLoader(settings.skills_dir)
    app.state.skills.scan()
    app.state.pg = PgGateway(
        dsn=settings.pg_dsn,
        whitelist=settings.pg_whitelist,
        max_rows=settings.pg_max_rows,
        timeout_s=settings.pg_timeout_s,
        audit_sink=JsonlAuditSink(settings.pg_audit_path),
    )
    # 入口验证与用户体系：ticket 换登录态 + 按登录用户隔离会话。
    # dev 模式（GEOAGENT_AUTH_MODE=dev）下中间件注入开发身份，行为与改造前一致。
    session_secret = load_session_secret(settings.auth_secret, settings.data_dir)
    app.state.session_signer = SessionSigner(
        secret=session_secret,
        ttl_s=settings.auth_session_ttl_s,
    )
    # 快报等文件的签名下载链接（MCP 调用方没有 Cookie，靠签名参数取文件）
    app.state.download_signer = FileDownloadSigner(
        secret=session_secret,
        ttl_s=settings.mcp_download_ttl_s,
    )
    app.state.ticket_validator = TicketValidator(
        url=settings.auth_ticket_validate_url,
        timeout_s=settings.auth_ticket_timeout_s,
    )
    app.state.ticket_replay = TicketReplayCache(ttl_s=120.0)
    app.state.auth_audit = AuthAudit(settings.auth_audit_path)

    # 模板和逐项查询结果只存在当前进程；后端重启后需要重新上传。
    app.state.template_workflows = {}
    # 套合任务：进度与结果只存在当前进程，前端按任务号轮询。
    app.state.overlay_jobs = {}
    app.state.overlay_tasks = set()

    # 入口验证中间件：后添加的 CORS 位于最外层，401 响应也带跨域头、预检不被拦。
    app.add_middleware(AuthMiddleware)

    # 开发用 CORS：Vue 开发服务器运行在不同端口。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(template_router)
    app.include_router(county_router)
    app.include_router(overlay_router)

    # MCP（Streamable HTTP）对外接口：延迟导入避免与 create_app 形成循环依赖。
    try:
        from ..mcp_http import streamable_http_app

        app.mount("/mcp", streamable_http_app())
    except Exception as exc:  # pragma: no cover - 仅在依赖缺失等异常情况下触发
        logger.warning("MCP HTTP 接口未挂载：%s", exc)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "GeoAgent", "docs": "/docs"}

    return app


app = create_app()
