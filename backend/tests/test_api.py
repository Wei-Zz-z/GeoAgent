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
    assert {"qwen3.7-flash", "qwen3.7-plus"} <= ids
    assert len(ids) == 2
    assert all("available" in m for m in models)


def test_conversation_crud_and_model_switch(client):
    created = client.post(
        "/api/conversations",
        json={"title": "测试会话", "model": "qwen3.7-flash"},
    )
    assert created.status_code == 200
    conv = created.json()
    cid = conv["id"]
    assert conv["model"] == "qwen3.7-flash"

    listed = client.get("/api/conversations")
    assert any(c["id"] == cid for c in listed.json()["conversations"])

    switched = client.put(f"/api/conversations/{cid}/model", json={"model": "qwen3.7-plus"})
    assert switched.status_code == 200
    assert switched.json()["model"] == "qwen3.7-plus"

    bad = client.put(f"/api/conversations/{cid}/model", json={"model": "does-not-exist"})
    assert bad.status_code == 400


def test_send_message_requires_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    created = client.post(
        "/api/conversations",
        json={"model": "qwen3.7-flash"},
    )
    cid = created.json()["id"]
    resp = client.post(f"/api/conversations/{cid}/messages", json={"content": "你好"})
    assert resp.status_code == 502
    assert "OPENAI_API_KEY" in resp.json()["detail"]


def test_send_message_unknown_conversation(client):
    resp = client.post("/api/unknown/messages", json={"content": "hi"})
    assert resp.status_code == 404
