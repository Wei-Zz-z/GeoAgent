from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..agents.graph import build_geo_graph
from ..core.context import ConversationContext
from ..core.events import Event
from ..core.llm import LLMConfigurationError
from ..memory.memory import NoopMemory
from ..memory.session import ConversationSession
from ..memory.store import ConversationStore
from .schemas import ConversationCreate, ModelSwitch, UserMessage

router = APIRouter(prefix="/api")


def _store(request: Request) -> ConversationStore:
    return request.app.state.store


def _conversation_or_404(store: ConversationStore, conversation_id: str) -> dict[str, Any]:
    conv = store.get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    return {"models": request.app.state.settings.list_profiles()}


@router.get("/conversations")
async def list_conversations(request: Request) -> dict[str, Any]:
    return {"conversations": _store(request).list()}


@router.post("/conversations")
async def create_conversation(
    body: ConversationCreate,
    request: Request,
) -> dict[str, Any]:
    store = _store(request)
    model = body.model or request.app.state.settings.default_model
    try:
        request.app.state.settings.profile(model)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return store.create(title=body.title, model=model)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    conv = _conversation_or_404(store, conversation_id)
    return {**conv, "messages": store.messages(conversation_id)}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    _conversation_or_404(store, conversation_id)
    return {"messages": store.messages(conversation_id)}


@router.put("/conversations/{conversation_id}/model")
async def switch_model(
    conversation_id: str,
    body: ModelSwitch,
    request: Request,
) -> dict[str, Any]:
    store = _store(request)
    _conversation_or_404(store, conversation_id)
    try:
        request.app.state.settings.profile(body.model)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conv = store.set_model(conversation_id, body.model)
    assert conv is not None
    return conv


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: UserMessage,
    request: Request,
) -> dict[str, Any]:
    store = _store(request)
    conv = _conversation_or_404(store, conversation_id)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")

    session = ConversationSession(store=store, conversation_id=conversation_id)
    ctx = ConversationContext(
        conversation_id=conversation_id,
        session=session,
        model=conv["model"],
        llm=request.app.state.llm,
        store=store,
        memory=NoopMemory(),
        event_sink=None,
        skills=request.app.state.skills,
    )
    try:
        flow = build_geo_graph(router_model=request.app.state.settings.router_model or None)
        _, payload = await flow.run(ctx, payload=body.content)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=502, detail=f"模型不可用: {exc}") from exc
    return {"reply": payload.content, "model": payload.model or conv["model"]}


async def _ws_send(websocket: WebSocket, event: Event) -> None:
    await websocket.send_json(event.to_dict())


@router.websocket("/conversations/{conversation_id}/ws")
async def chat_ws(websocket: WebSocket, conversation_id: str) -> None:
    await websocket.accept()
    app = websocket.scope["app"]
    store = app.state.store
    conv = store.get(conversation_id)
    if conv is None:
        await websocket.send_json({"type": "error", "message": "conversation not found"})
        await websocket.close(code=4404)
        return

    session = ConversationSession(store=store, conversation_id=conversation_id)
    ctx = ConversationContext(
        conversation_id=conversation_id,
        session=session,
        model=conv["model"],
        llm=app.state.llm,
        store=store,
        memory=NoopMemory(),
        event_sink=lambda event: _ws_send(websocket, event),
        skills=app.state.skills,
    )
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict) or data.get("type") != "user":
                continue
            content = str(data.get("content", "")).strip()
            if not content:
                continue
            # 每轮开始时刷新会话的模型设置（可能已通过 REST 切换过）。
            ctx.model = store.get(conversation_id)["model"]
            await ctx.emit(Event("turn_start", {"conversation_id": conversation_id}))
            try:
                flow = build_geo_graph(router_model=app.state.settings.router_model or None)
                await flow.run(ctx, payload=content)
            except LLMConfigurationError as exc:
                await ctx.emit(Event("error", {"message": f"模型不可用: {exc}"}))
            except Exception as exc:
                await ctx.emit(
                    Event(
                        "error",
                        {"message": f"{type(exc).__name__}: {exc}"},
                    )
                )
            await ctx.emit(Event("turn_end", {"conversation_id": conversation_id}))
    except WebSocketDisconnect:
        pass
