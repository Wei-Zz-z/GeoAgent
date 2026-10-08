from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings
from ..core.embedding import EmbeddingService
from ..core.llm import LLMService
from ..knowledge.kb import KnowledgeBase
from ..memory.store import ConversationStore
from ..skills import SkillLoader
from ..tools.pg import JsonlAuditSink, PgGateway
from .routes import router
from .template_routes import router as template_router
from .county_routes import router as county_router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        pg: Optional[PgGateway] = getattr(app.state, "pg", None)
        if pg is not None:
            await pg.close()
        await app.state.knowledge.close()

    app = FastAPI(title="GeoAgent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.llm = LLMService(settings)
    app.state.embeddings = EmbeddingService(settings)
    app.state.knowledge = KnowledgeBase(settings, app.state.embeddings)
    app.state.store = ConversationStore(settings.data_dir)
    app.state.skills = SkillLoader(settings.skills_dir)
    app.state.skills.scan()
    app.state.pg = PgGateway(
        dsn=settings.pg_dsn,
        whitelist=settings.pg_whitelist,
        max_rows=settings.pg_max_rows,
        timeout_s=settings.pg_timeout_s,
        audit_sink=JsonlAuditSink(settings.pg_audit_path),
    )
    # 模板和逐项查询结果只存在当前进程；后端重启后需要重新上传。
    app.state.template_workflows = {}

    # 开发用 CORS：Vue 开发服务器运行在不同端口。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(template_router)
    app.include_router(county_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "GeoAgent", "docs": "/docs"}

    return app


app = create_app()
