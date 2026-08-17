from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geoagent.server.app import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path))
    app = create_app()
    return TestClient(app)


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_models_include_switchable_models(client):
    resp = client.get("/api/models")
    assert resp.status_code == 200
    models = resp.json()["models"]
    ids = {m["id"] for m in models}
    assert {"qwen-flash", "qwen-plus", "gpt-4o-mini", "glm-4.7-flash", "deepseek-chat"} <= ids
    assert all("available" in m for m in models)


def test_conversation_crud_and_model_switch(client):
    created = client.post(
        "/api/conversations",
        json={"title": "测试会话", "model": "gpt-4o-mini"},
    )
    assert created.status_code == 200
    conv = created.json()
    cid = conv["id"]
    assert conv["model"] == "gpt-4o-mini"

    listed = client.get("/api/conversations")
    assert any(c["id"] == cid for c in listed.json()["conversations"])

    switched = client.put(f"/api/conversations/{cid}/model", json={"model": "glm-4.7-flash"})
    assert switched.status_code == 200
    assert switched.json()["model"] == "glm-4.7-flash"

    bad = client.put(f"/api/conversations/{cid}/model", json={"model": "does-not-exist"})
    assert bad.status_code == 400


def test_send_message_requires_api_key(client, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    created = client.post(
        "/api/conversations",
        json={"model": "glm-4.7-flash"},
    )
    cid = created.json()["id"]
    resp = client.post(f"/api/conversations/{cid}/messages", json={"content": "你好"})
    assert resp.status_code == 502
    assert "ZHIPU_API_KEY" in resp.json()["detail"]


def test_send_message_unknown_conversation(client):
    resp = client.post("/api/unknown/messages", json={"content": "hi"})
    assert resp.status_code == 404
