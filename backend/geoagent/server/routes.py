from __future__ import annotations

from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pathlib import PurePath

from ..agents.graph import DEFAULT_AGENT_ID, agent_catalog, run_turn
from ..auth.deps import current_user, owned_conversation, scope_user
from ..auth.session import UserIdentity
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
    """下载生成的 Word 或 PDF 快报。

    两种凭据都接受：网页登录态（Cookie），或 MCP 返回的带签名一次性链接
    （`?e=<过期时间>&s=<签名>`）——这样外部前端的浏览器直接点链接就能下载。
    """
    safe = PurePath(filename).name
    suffix = PurePath(safe).suffix.lower()
    if safe != filename or suffix not in {".docx", ".pdf"}:
        raise HTTPException(status_code=400, detail="非法文件名")
    if getattr(request.state, "user", None) is None:
        signer = getattr(request.app.state, "download_signer", None)
        if signer is None or not signer.verify(
            safe,
            request.query_params.get("e", ""),
            request.query_params.get("s", ""),
        ):
            raise HTTPException(status_code=401, detail="未登录或下载链接已失效，请重新获取。")
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


async def _conversation_or_404(
    store: ConversationStore,
    conversation_id: str,
    user: UserIdentity,
) -> dict[str, Any]:
    """取会话；不存在或不属于当前用户都按 404 处理（不暴露他人会话是否存在）。"""
    conv = await owned_conversation(store, conversation_id, user)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


async def _new_context(
    app: Any,
    conversation: dict[str, Any],
    event_sink: Optional[EventSink] = None,
) -> ConversationContext:
    """创建一次对话轮次的运行时上下文，并从持久化历史恢复消息窗口。

    同一会话在断线重连 / 切换会话后重新建立连接时，内存中的旧 session 已不存在，
    这里把会话库中已持久化的历史消息载入新的 ConversationSession，保证多轮续聊。
    """
    store = app.state.store
    session = ConversationSession(store=store, conversation_id=conversation["id"])
    await session.restore()
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
        datasets=app.state.datasets,
        settings=app.state.settings,
        embeddings=app.state.embeddings,
        knowledge=app.state.knowledge,
        reports_dir=app.state.settings.reports_dir,
        transcripts_dir=app.state.settings.data_dir / "transcripts",
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/agents")
async def list_agents() -> dict[str, Any]:
    """可选智能体目录（前端会话旁的"智能体"选择框）。"""
    return {"agents": agent_catalog(), "default_id": DEFAULT_AGENT_ID}


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    return {"models": request.app.state.settings.list_profiles()}


@router.get("/datasets")
async def list_datasets(request: Request) -> dict[str, Any]:
    """已登记的期次数据集（供前端展示/选择期次）。"""
    return request.app.state.datasets.to_dict()


@router.get("/conversations")
async def list_conversations(
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    """只返回当前登录用户的会话（改造前遗留的无主会话仍可见）。"""
    return {"conversations": await _store(request).list(owner_id=user.id)}


@router.post("/conversations")
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    model = body.model or request.app.state.settings.default_model
    try:
        request.app.state.settings.profile(model)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await store.create(
        title=body.title,
        model=model,
        owner_id=user.id,
        owner_name=user.display_name,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    conv = await _conversation_or_404(store, conversation_id, user)
    return {**conv, "messages": await store.messages(conversation_id)}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    await _conversation_or_404(store, conversation_id, user)
    return {"messages": await store.messages(conversation_id)}


@router.put("/conversations/{conversation_id}/model")
async def switch_model(
    conversation_id: str,
    body: ModelSwitch,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    await _conversation_or_404(store, conversation_id, user)
    try:
        request.app.state.settings.profile(body.model)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conv = await store.set_model(conversation_id, body.model)
    assert conv is not None
    return conv


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    await _conversation_or_404(store, conversation_id, user)
    await store.delete(conversation_id)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: UserMessage,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    store = _store(request)
    conv = await _conversation_or_404(store, conversation_id, user)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")

    ctx = await _new_context(request.app, conv)
    try:
        payload = await run_turn(
            ctx,
            body.content,
            agent_id=body.agent or "",
            router_model=request.app.state.settings.router_model or None,
        )
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=502, detail=f"模型不可用: {exc}") from exc
    finally:
        # 本轮消息落库（失败时也保留已产生的部分，与旧行为一致）
        await ctx.session.flush()
    return {"reply": payload.content, "model": payload.model or conv["model"]}


@router.post("/conversations/{conversation_id}/rollback-last-user-turn")
async def rollback_last_user_turn(
    conversation_id: str,
    request: Request,
    user: UserIdentity = Depends(current_user),
) -> dict[str, Any]:
    """撤回最后一条用户消息及其回答（前端修改文字后以新内容重新发起）。"""
    store = _store(request)
    await _conversation_or_404(store, conversation_id, user)
    old = await store.rollback_last_user_turn(conversation_id)
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
    # 鉴权失败时先给出明确的错误事件再关闭，前端据此提示重新从省域平台进入
    user = scope_user(websocket)
    if user is None:
        await websocket.send_json(
            {"type": "error", "message": "未登录或登录已过期，请从省域平台重新进入。"}
        )
        await websocket.close(code=4401)
        return
    conv = await owned_conversation(store, conversation_id, user)
    if conv is None:
        await websocket.send_json({"type": "error", "message": "conversation not found"})
        await websocket.close(code=4404)
        return

    ctx = await _new_context(
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
            # 前端可显式指定期次（如界面上选了"2026年第二期"）；未指定则按问题文本识别。
            ctx.dataset_id = str(data.get("dataset_id", ""))
            # 会话旁的“智能体”选择框：显式指定时不走 LLM 路由
            agent_id = str(data.get("agent", "")).strip()
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
            refreshed = await store.get(conversation_id)
            ctx.model = (refreshed or conv)["model"]
            await ctx.emit(Event("turn_start", {
                "conversation_id": conversation_id,
                "answer_origin": ctx.answer_origin,
            }))
            try:
                await run_turn(
                    ctx,
                    content,
                    agent_id=agent_id,
                    router_model=app.state.settings.router_model or None,
                )
            except LLMConfigurationError as exc:
                await ctx.emit(Event("error", {"message": f"模型不可用: {exc}"}))
            except Exception as exc:
                await ctx.emit(
                    Event(
                        "error",
                        {"message": f"{type(exc).__name__}: {exc}"},
                    )
                )
            finally:
                # 本轮消息落库，保证前端在 turn_end 后能拉到完整历史
                await ctx.session.flush()
            await ctx.emit(Event("turn_end", {"conversation_id": conversation_id}))
    except WebSocketDisconnect:
        pass
