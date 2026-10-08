from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pathlib import PurePath

from ..agents.graph import build_geo_graph
from ..core.context import ConversationContext, EventSink
from ..core.events import Event
from ..core.llm import LLMConfigurationError
from ..memory.memory import NoopMemory
from ..memory.session import ConversationSession
from ..memory.store import ConversationStore
from .schemas import ConversationCreate, ModelSwitch, UserMessage

router = APIRouter(prefix="/api")


def _store(request: Request) -> ConversationStore:
    return request.app.state.store


@router.get("/files/reports/{filename}")
async def download_report(filename: str, request: Request) -> FileResponse:
    """下载生成的 Word 或 PDF 快报。"""
    safe = PurePath(filename).name
    suffix = PurePath(safe).suffix.lower()
    if safe != filename or suffix not in {".docx", ".pdf"}:
        raise HTTPException(status_code=400, detail="非法文件名")
    reports_dir = request.app.state.settings.reports_dir
    path = (reports_dir / safe).resolve()
    if not path.is_relative_to(reports_dir.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    media_type = (
        "application/pdf"
        if suffix == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path, media_type=media_type, filename=safe)


def _conversation_or_404(store: ConversationStore, conversation_id: str) -> dict[str, Any]:
    conv = store.get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


def _new_context(
    app: Any,
    conversation: dict[str, Any],
    event_sink: Optional[EventSink] = None,
) -> ConversationContext:
    """创建一次对话轮次的运行时上下文，并从持久化历史恢复消息窗口。

    同一会话在断线重连 / 切换会话后重新建立连接时，内存中的旧 session 已不存在，
    这里把 JSONL 中已持久化的历史消息载入新的 ConversationSession，保证多轮续聊。
    """
    store = app.state.store
    session = ConversationSession(store=store, conversation_id=conversation["id"])
    session.restore()
    return ConversationContext(
        conversation_id=conversation["id"],
        session=session,
        model=conversation["model"],
        llm=app.state.llm,
        store=store,
        memory=NoopMemory(),
        event_sink=event_sink,
        skills=app.state.skills,
        pg=app.state.pg,
        embeddings=app.state.embeddings,
        knowledge=app.state.knowledge,
        reports_dir=app.state.settings.reports_dir,
        transcripts_dir=app.state.settings.data_dir / "transcripts",
    )


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


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, request: Request
) -> dict[str, Any]:
    store = _store(request)
    _conversation_or_404(store, conversation_id)
    store.delete(conversation_id)
    return {"ok": True}


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

    ctx = _new_context(request.app, conv)
    try:
        flow = build_geo_graph(router_model=request.app.state.settings.router_model or None)
        _, payload = await flow.run(ctx, payload=body.content)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=502, detail=f"模型不可用: {exc}") from exc
    return {"reply": payload.content, "model": payload.model or conv["model"]}


@router.post("/conversations/{conversation_id}/rollback-last-user-turn")
async def rollback_last_user_turn(
    conversation_id: str,
    request: Request,
) -> dict[str, Any]:
    """撤回最后一条用户消息及其回答（前端修改文字后以新内容重新发起）。"""
    store = _store(request)
    _conversation_or_404(store, conversation_id)
    old = store.rollback_last_user_turn(conversation_id)
    if old is None:
        raise HTTPException(status_code=409, detail="conversation has no user message")
    return {"ok": True, "removed": old.get("content", "")}


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

    ctx = _new_context(
        app,
        conv,
        event_sink=lambda event: _ws_send(websocket, event),
    )
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict) or data.get("type") != "user":
                continue
            content = str(data.get("content", "")).strip()
            if not content:
                continue
            workflow = app.state.template_workflows.get(str(data.get("workflow_id", "")))
            question = next(
                (item for item in workflow["questions"]
                 if item["id"] == data.get("question_id")),
                None,
            ) if workflow else None
            ctx.answer_origin = (
                "template_free"
                if data.get("source") == "template_free"
                and question is not None
                and question["question"].strip() == content
                else ""
            )
            # 每轮开始时刷新会话的模型设置（可能已通过 REST 切换过）。
            ctx.model = store.get(conversation_id)["model"]
            await ctx.emit(Event("turn_start", {
                "conversation_id": conversation_id,
                "answer_origin": ctx.answer_origin,
            }))
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
