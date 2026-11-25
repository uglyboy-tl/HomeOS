"""`/oc/*` 反代：拼对目标地址、注入上游 Basic 认证、校验 `X-HomeOS-Token`（不真的连上游）。"""

import base64

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.routes import oc


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    async def fake_forward(request, url, *, label, timeout, headers=None):
        seen.update(url=url, headers=headers or {}, method=request.method)
        return {"ok": True}

    monkeypatch.setattr(oc, "forward", fake_forward)
    monkeypatch.setattr(config, "OC_SERVER_PASSWORD", "upstream-pw")
    monkeypatch.setattr(config, "TOKEN", "s3cret")
    yield seen


@pytest.fixture
def client(captured):
    c = TestClient(app)
    c.headers["X-HomeOS-Token"] = "s3cret"
    return c


def test_forwards_with_upstream_basic_auth(captured, client):
    r = client.get("/oc/api/info?x=1")
    assert r.status_code == 200
    assert captured["url"].startswith("http://127.0.0.1:14100/api/info?x=1")
    expected = "Basic " + base64.b64encode(b"opencode:upstream-pw").decode()
    assert captured["headers"]["Authorization"] == expected


def test_method_is_forwarded(captured, client):
    client.post("/oc/session/abc/message")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/session/abc/message")


def test_mounts_every_method(captured, client):
    for method in ("put", "patch", "delete"):
        getattr(client, method)("/oc/x")
    assert captured["method"] == "DELETE"


def test_rejects_without_token(captured):
    r = TestClient(app).get("/oc/api/info")
    assert r.status_code == 401
    assert "X-HomeOS-Token" in r.text
    assert "url" not in captured


def test_rejects_wrong_token(captured):
    r = TestClient(app).get("/oc/api/info", headers={"X-HomeOS-Token": "nope"})
    assert r.status_code == 401


def test_closed_when_token_unset(captured, monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "")
    r = TestClient(app).get("/oc/api/info", headers={"X-HomeOS-Token": "s3cret"})
    assert r.status_code == 403
    assert "HOMEOS_TOKEN" in r.text


def test_non_ascii_token_is_rejected(captured):
    """请求头按 latin-1 解码，非 ASCII 曾让 compare_digest 抛 TypeError（500）。"""
    # httpx 的 str 头只收 ASCII，要触发 latin-1 解码得直接给字节
    r = TestClient(app).get("/oc/api/info", headers={"X-HomeOS-Token": b"caf\xe9"})
    assert r.status_code == 401
