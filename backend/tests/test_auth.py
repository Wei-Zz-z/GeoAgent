"""入口验证与用户体系用例：不联网、不依赖真实省域平台。

ticket 校验用 httpx.MockTransport 顶替，覆盖成功换票、失败透传、平台不可达、
同一 ticket 重复提交、会话不跨用户可见、WebSocket 鉴权与 dev 模式兼容。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from geoagent.auth.session import SessionSigner, TicketReplayCache, UserIdentity
from geoagent.auth.ticket import TicketValidator
from geoagent.server.app import create_app

# 接入文档里的返回示例（Id 尾部带空格，用于验证字段清洗）
PLATFORM_RESULT: dict[str, Any] = {
    "Id": "d34bb611-1350-471a ",
    "UserName": "schkxjsyjy-cj",
    "Name": "程*",
    "Account": "78634808",
    "OrganizationName": "数字城市所",
    "Xzqh": "省级",
    "XzqhCode": None,
    "OrganizationLine": "浙政钉-->省政府机构-->省自然资源厅-->数字城市所",
    "Role": 65535,
    "LastLoginTime": "2022-05-12 11:36:45",
    "Job": None,
    "EmployeeCode": "GE_93df1e63b12d40***1c327d7**62637e",
    "OrganizationCode": None,
    "NodesAuth": "省级",
}


def _result(user_id: str, name: str = "程*") -> dict[str, Any]:
    return {**PLATFORM_RESULT, "Id": user_id, "Name": name}


def _handler(users: dict[str, dict[str, Any]], calls: list[str]):
    """模拟省域平台 ValidateTicket：按 ticket 返回对应用户，未知 ticket 报无效。"""

    def handle(request: httpx.Request) -> httpx.Response:
        ticket = request.url.params.get("ticket", "")
        calls.append(ticket)
        result = users.get(ticket)
        if result is None:
            return httpx.Response(
                200,
                json={"result": "", "success": False, "message": "ticket无效或超时"},
            )
        return httpx.Response(
            200,
            json={"result": result, "success": True, "message": "操作成功"},
        )

    return handle


def _client(tmp_path, monkeypatch, handler, mode: str = "ticket") -> TestClient:
    """开启入口验证的应用；平台接口用 MockTransport 顶替。"""
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEOAGENT_AUTH_MODE", mode)
    monkeypatch.setenv("GEOAGENT_AUTH_SECRET", "unit-test-secret")
    app = create_app()
    app.state.ticket_validator = TicketValidator(
        url="https://example.test/bev2/api/Ticket/ValidateTicket",
        timeout_s=1.0,
        transport=httpx.MockTransport(handler),
    )
    return TestClient(app)


def _ws_headers(client: TestClient) -> dict[str, str]:
    """把已登录的会话 Cookie 显式带上，避免依赖测试客户端是否自动透传 Cookie。"""
    name = client.app.state.settings.auth_cookie_name
    token = client.cookies.get(name)
    return {"cookie": f"{name}={token}"} if token else {}


def test_ticket_login_returns_user_and_protects_api(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    client = _client(tmp_path, monkeypatch, _handler({"t-1": PLATFORM_RESULT}, calls))

    # 未登录：/api 受保护，健康检查与登录接口除外
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    resp = client.post("/api/auth/ticket", json={"ticket": "t-1"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["auth_mode"] == "ticket"
    user = payload["user"]
    assert user["id"] == "d34bb611-1350-471a"  # 文档示例里的尾部空格被清掉
    assert user["name"] == "程*"
    assert user["organization"] == "数字城市所"
    assert user["nodes_auth"] == "省级"
    assert user["role"] == 65535
    assert client.cookies.get("geoagent_session")

    assert client.get("/api/conversations").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["account"] == "78634808"


def test_invalid_and_missing_ticket(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, _handler({}, []))

    # 平台判定 ticket 无效：原样透传平台提示
    invalid = client.post("/api/auth/ticket", json={"ticket": "bad"})
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "ticket无效或超时"

    # 前端没取到 ticket
    assert client.post("/api/auth/ticket", json={}).status_code == 400


def test_platform_unreachable_returns_502(tmp_path, monkeypatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("platform down")

    client = _client(tmp_path, monkeypatch, handle)
    resp = client.post("/api/auth/ticket", json={"ticket": "t-1"})
    assert resp.status_code == 502
    assert "无法连接省域平台" in resp.json()["detail"]


def test_same_ticket_replay_reuses_session(tmp_path, monkeypatch) -> None:
    """刷新页面会重放 URL 上的 ticket：只向平台校验一次，且仍然登录成功。"""
    calls: list[str] = []
    client = _client(tmp_path, monkeypatch, _handler({"t-1": _result("u-1")}, calls))

    first = client.post("/api/auth/ticket", json={"ticket": "t-1"})
    second = client.post("/api/auth/ticket", json={"ticket": "t-1"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["user"] == second.json()["user"]
    assert calls == ["t-1"]


def test_conversations_are_isolated_per_user(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    handler = _handler(
        {"t-a": _result("u-a", "甲"), "t-b": _result("u-b", "乙")},
        calls,
    )
    owner = _client(tmp_path, monkeypatch, handler)
    owner.post("/api/auth/ticket", json={"ticket": "t-a"})
    created = owner.post("/api/conversations", json={"title": "甲的会话"}).json()
    conversation_id = created["id"]
    assert created["owner_id"] == "u-a"

    other = TestClient(owner.app)  # 另一个浏览器：另一个用户
    other.post("/api/auth/ticket", json={"ticket": "t-b"})
    assert other.get("/api/conversations").json()["conversations"] == []
    assert other.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert other.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert other.delete(f"/api/conversations/{conversation_id}").status_code == 404

    # 本人依然可见
    listed = owner.get("/api/conversations").json()["conversations"]
    assert [c["id"] for c in listed] == [conversation_id]


def test_websocket_requires_session_and_ownership(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    handler = _handler(
        {"t-a": _result("u-a", "甲"), "t-b": _result("u-b", "乙")},
        calls,
    )
    owner = _client(tmp_path, monkeypatch, handler)
    owner.post("/api/auth/ticket", json={"ticket": "t-a"})
    conversation_id = owner.post("/api/conversations", json={"title": "甲的会话"}).json()["id"]
    url = f"/api/conversations/{conversation_id}/ws"

    # 没有登录态：先收到明确的错误事件，再断开
    anon = TestClient(owner.app)
    with anon.websocket_connect(url) as ws:
        event = ws.receive_json()
        assert event["type"] == "error"
        assert "未登录" in event["message"]
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()

    # 已登录但不是自己的会话：按会话不存在处理
    other = TestClient(owner.app)
    other.post("/api/auth/ticket", json={"ticket": "t-b"})
    with other.websocket_connect(url, headers=_ws_headers(other)) as ws:
        assert ws.receive_json() == {"type": "error", "message": "conversation not found"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_template_endpoint_rejects_other_users_conversation(tmp_path, monkeypatch) -> None:
    """模板接口带 conversation_id 时必须按归属校验，不能写进别人的会话。"""
    calls: list[str] = []
    handler = _handler(
        {"t-a": _result("u-a", "甲"), "t-b": _result("u-b", "乙")},
        calls,
    )
    owner = _client(tmp_path, monkeypatch, handler)
    owner.post("/api/auth/ticket", json={"ticket": "t-a"})
    conversation_id = owner.post("/api/conversations", json={"title": "甲的会话"}).json()["id"]

    other = TestClient(owner.app)
    other.post("/api/auth/ticket", json={"ticket": "t-b"})
    other.app.state.template_workflows["wf"] = {
        "questions": [{"id": "Q1", "question": "统计铁路图斑数量"}]
    }
    resp = other.post(
        "/api/report-templates/wf/questions/Q1/capture-free",
        json={"conversation_id": conversation_id},
    )
    assert resp.status_code == 404


def test_dev_mode_keeps_previous_behaviour(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, _handler({}, []), mode="dev")

    assert client.get("/api/conversations").status_code == 200  # 不需要登录
    me = client.get("/api/auth/me").json()
    assert me["auth_mode"] == "dev"
    assert me["user"]["is_dev"] is True

    created = client.post("/api/conversations", json={"title": "dev 会话"}).json()
    assert created["owner_id"] == "dev"


def test_session_signer_rejects_tampered_and_expired_tokens() -> None:
    signer = SessionSigner(secret="unit-test-secret", ttl_s=60)
    user = UserIdentity.from_platform(PLATFORM_RESULT)
    token = signer.issue(user, now=1000)

    assert signer.verify(token, now=1010) == user
    assert signer.verify(token + "x", now=1010) is None
    assert signer.verify(token, now=1061) is None
    assert signer.verify("", now=1010) is None


def test_replay_cache_forgets_expired_tickets() -> None:
    cache = TicketReplayCache(ttl_s=10)
    cache.put("t-1", "token-1", now=100)
    assert cache.get("t-1", now=105) == "token-1"
    assert cache.get("t-1", now=111) == ""  # 超出有效窗口后不再复用
