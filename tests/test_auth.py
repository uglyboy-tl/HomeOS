"""认证分档：页面发 cookie 免输入、其他程序要头、未配置时放行。"""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app import config
from app.main import app


@pytest.fixture
def tok(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "a" * 32)


def test_page_sets_cookie_only_over_https(tok):
    c = TestClient(app)
    plain = c.get("/mijia").headers["set-cookie"]
    assert "homeos=" in plain and "HttpOnly" in plain and "SameSite=Strict" in plain
    assert "Secure" not in plain  # http 下加了浏览器会丢

    proxied = c.get("/mijia", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]
    assert "Secure" in proxied


def test_cookie_never_carries_the_token(tok):
    """页面不认证就发 cookie，里面只能是派生值，否则等于把接口令牌公开。"""
    cookie = TestClient(app).get("/mijia").headers["set-cookie"]
    assert "a" * 32 not in cookie
    derived = cookie.split("homeos=")[1].split(";")[0]
    wrong = TestClient(app)
    wrong.headers["X-HomeOS-Token"] = derived
    assert wrong.get("/api/info/pages").status_code == 401


def test_api_needs_auth(tok):
    c = TestClient(app)
    assert c.get("/api/info/pages").status_code == 401
    assert c.get("/api/info/mijia").status_code == 401
    assert c.get("/api/ready").status_code == 200  # 健康检查保持开放


def test_cookie_makes_frontend_work(tok):
    c = TestClient(app)
    c.get("/mijia")  # 打开一次页面 = 拿到 cookie
    assert c.get("/api/info/pages").status_code == 200
    assert c.get("/api/info/mijia").status_code == 200


def test_header_works_for_other_devices(tok):
    c = TestClient(app)
    c.headers["X-HomeOS-Token"] = "a" * 32
    assert c.get("/api/info/pages").status_code == 200


def test_wrong_header_falls_back_to_cookie(tok):
    c = TestClient(app)
    c.get("/mijia")
    assert c.get("/api/info/pages", headers={"X-HomeOS-Token": "nope"}).status_code == 200


def test_static_assets_stay_open(tok):
    c = TestClient(app)
    assert c.get("/assets/tokens.css").status_code == 200


def test_ws_is_rejected_without_auth(tok):
    with (
        pytest.raises(WebSocketDenialResponse) as err,
        TestClient(app).websocket_connect("/api/asr/stream"),
    ):
        pass
    assert err.value.status_code == 401


def test_open_without_token_in_dev(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "")
    monkeypatch.setattr(config, "DEV", True)
    c = TestClient(app)
    assert c.get("/api/info/pages").status_code == 200
    assert "set-cookie" not in c.get("/mijia").headers


def test_closed_without_token_in_prod(monkeypatch):
    """生产未配令牌 = 接口全关：否则 /api/agent/say 的 device 模式就是匿名 shell。"""
    monkeypatch.setattr(config, "TOKEN", "")
    monkeypatch.setattr(config, "DEV", False)
    c = TestClient(app)
    assert c.get("/api/info/pages").status_code == 401
    assert c.get("/api/info/agent").status_code == 401
    assert c.get("/api/ready").status_code == 200  # 健康检查不受影响


def test_every_api_router_is_guarded(tok):
    c = TestClient(app)
    for path in ("/api/info/pages", "/api/info/mijia", "/api/info/agent"):
        assert c.get(path).status_code == 401, path


def test_invalid_token_degrades_instead_of_crashing(tmp_path, repo_root):
    """写错令牌不能让面板全黑：降级成未配置，日志里说清楚。"""
    (tmp_path / "server.token").write_text("short; bad\n", encoding="utf-8")
    env = {**os.environ, "HOMEOS_CONFIG_DIR": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(repr(c.TOKEN))"],
        env=env,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "''"
    assert "server.token" in r.stderr


def test_bearer_token_works_and_legacy_header_still_works(tok):
    c = TestClient(app)
    bearer = {"Authorization": "Bearer " + "a" * 32}
    assert c.get("/api/info/pages", headers=bearer).status_code == 200
    assert c.get("/api/info/pages", headers={"X-HomeOS-Token": "a" * 32}).status_code == 200
    assert c.get("/api/info/pages", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_cookie_has_a_lifetime(tok):
    cookie = TestClient(app).get("/mijia").headers["set-cookie"]
    assert "Max-Age=2592000" in cookie


def test_safe_headers_cover_pages_assets_and_api(tok):
    """安全头由 ASGI 中间件统一加：页面、资源、API 都要有（SSE 也不能被缓冲）。"""
    c = TestClient(app)
    c.get("/mijia")  # 拿 cookie
    for path in ("/mijia", "/assets/tokens.css", "/p/demo", "/api/ready", "/api/info/pages"):
        head = c.get(path).headers
        assert head["x-content-type-options"] == "nosniff", path
        assert head["x-frame-options"] == "DENY", path
        assert head["referrer-policy"] == "no-referrer", path

    with c.stream("GET", "/api/agent/stream?mode=chat") as response:
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-type"].startswith("text/event-stream")


def test_401_keeps_www_authenticate_and_uses_the_envelope(tok):
    r = TestClient(app).get("/api/info/pages")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert "www-authenticate" in r.headers
