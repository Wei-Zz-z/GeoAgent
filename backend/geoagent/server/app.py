from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings
from ..core.llm import LLMService
from ..memory.store import ConversationStore
from ..skills import SkillLoader
from .routes import router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="GeoAgent", version="0.1.0")
    app.state.settings = settings
    app.state.llm = LLMService(settings)
    app.state.store = ConversationStore(settings.data_dir)
    app.state.skills = SkillLoader(settings.skills_dir)
    app.state.skills.scan()

    # 开发用 CORS：Vue 开发服务器运行在不同端口。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "GeoAgent", "docs": "/docs"}

    return app


app = create_app()
