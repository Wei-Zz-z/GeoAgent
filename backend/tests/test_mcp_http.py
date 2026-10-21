"""MCP 对外接口用例：令牌鉴权、签名下载链接、无状态问答与调用审计（不依赖真实网络/数据库）。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from geoagent.auth.downloads import FileDownloadSigner
from geoagent.server.app import create_app
from geoagent.stateless import run_stateless_question

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def _client(tmp_path, monkeypatch, tokens: str = "secret-token:门户A") -> TestClient:
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEOAGENT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("GEOAGENT_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("GEOAGENT_MCP_TOKENS", tokens)
    monkeypatch.setenv("GEOAGENT_AUTH_MODE", "ticket")
    return TestClient(create_app())


def test_mcp_requires_bearer_token(tmp_path, monkeypatch) -> None:
    """未带/带错令牌的 401 由 MCP 层给出（带 WWW-Authenticate），不是 web 登录态兜的。"""
    client = _client(tmp_path, monkeypatch)

    anonymous = client.post("/mcp/", json=INITIALIZE, headers=MCP_HEADERS)
    assert anonymous.status_code == 401
    assert "Bearer" in anonymous.headers.get("www-authenticate", "")
    assert "invalid_token" in anonymous.text

    wrong = client.post(
        "/mcp/", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": "Bearer nope"}
    )
    assert wrong.status_code == 401


def test_mcp_fails_closed_without_configured_tokens(tmp_path, monkeypatch) -> None:
    """没配 GEOAGENT_MCP_TOKENS 时 MCP 一律拒绝：对外开放必须先发令牌。"""
    client = _client(tmp_path, monkeypatch, tokens="")
    resp = client.post(
        "/mcp/", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": "Bearer anything"}
    )
    assert resp.status_code == 401


def test_mcp_accepts_valid_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    # MCP 的会话管理器随应用生命周期启动，因此这里用 with 触发 lifespan
    with client:
        resp = client.post(
            "/mcp/",
            json=INITIALIZE,
            headers={**MCP_HEADERS, "authorization": "Bearer secret-token"},
        )
    assert resp.status_code == 200


def test_mcp_token_does_not_open_other_api_paths(tmp_path, monkeypatch) -> None:
    """服务令牌只放行 /mcp，不能拿来读写会话接口。"""
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/api/conversations", headers={"authorization": "Bearer secret-token"})
    assert resp.status_code == 401


def test_signed_download_link_grants_access_without_cookie(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    settings = client.app.state.settings
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    sample = settings.reports_dir / "测试快报.docx"
    sample.write_bytes(b"PK\x03\x04docx")

    # 没有签名参数：仍然要求登录态
    assert client.get(f"/api/files/reports/{sample.name}").status_code == 401

    url = client.app.state.download_signer.url(sample.name)
    assert client.get(url).status_code == 200
    assert client.get(url).content == b"PK\x03\x04docx"

    # 换文件名 / 过期签名：都不放行
    assert client.get(url.replace("测试快报", "别的快报")).status_code == 401

    signer = FileDownloadSigner(secret="test-secret", ttl_s=3600)
    _, signature = signer.issue(sample.name, now=time.time() - 7200)
    expired = f"/api/files/reports/{sample.name}?e={int(time.time()) - 3600}&s={signature}"
    assert client.get(expired).status_code == 401


async def test_stateless_question_returns_answer_and_records_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    answer = "耕地转为建设用地 12.34 亩。"

    class FakePayload:
        content = answer

    async def fake_run_turn(ctx, payload, **_kwargs):
        ctx.session.add_user(payload)
        ctx.session.add_message({"role": "assistant", "content": answer})
        return FakePayload()

    monkeypatch.setattr("geoagent.stateless.run_turn", fake_run_turn)
    result = await run_stateless_question(
        app, question="耕地转为建设用地的面积是多少？", caller="门户A", user_id="u-1"
    )

    assert result.is_error is False
    assert result.answer == answer

    store = app.state.store
    records = await store.mcp_calls()
    assert len(records) == 1
    assert records[0]["question"].startswith("耕地转为建设用地")
    assert records[0]["answer"] == answer
    assert records[0]["caller"] == "门户A"
    assert records[0]["user_id"] == "u-1"

    # 无状态：不产生任何会话记录
    assert await store.list() == []


async def test_stateless_question_reports_model_failure_as_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path / "data"))
    app = create_app()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("上游模型 401")

    monkeypatch.setattr("geoagent.stateless.run_turn", boom)
    result = await run_stateless_question(app, question="测试问题", caller="门户A")

    assert result.is_error is True
    assert "上游模型 401" in result.answer
    records = await app.state.store.mcp_calls()
    assert records[0]["ok"] is False
