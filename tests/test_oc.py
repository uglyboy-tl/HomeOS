"""`/oc/*` 反代：拼对目标地址，并注入上游 opencode 的 Basic 认证（不真的连上游）。

这个路由**故意不做访问认证**（内网自用），所以这里不测 401/503。
"""

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
    yield seen


def test_forwards_with_upstream_basic_auth(captured):
    r = TestClient(app).get("/oc/api/info?x=1")
    assert r.status_code == 200
    assert captured["url"].startswith("http://127.0.0.1:14100/api/info?x=1")
    expected = "Basic " + base64.b64encode(b"opencode:upstream-pw").decode()
    assert captured["headers"]["Authorization"] == expected


def test_method_is_forwarded(captured):
    TestClient(app).post("/oc/session/abc/message")
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/session/abc/message")


def test_mounts_every_method(captured):
    for method in ("put", "patch", "delete"):
        getattr(TestClient(app), method)("/oc/x")
    assert captured["method"] == "DELETE"

