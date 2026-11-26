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
    """页面不认证就发 cookie，所以里面只能是派生值，否则等于把 /oc 的钥匙公开。"""
    cookie = TestClient(app).get("/mijia").headers["set-cookie"]
    assert "a" * 32 not in cookie
    wrong = TestClient(app)
    wrong.headers["X-HomeOS-Token"] = cookie.split("homeos=")[1].split(";")[0]
    assert wrong.get("/oc/api/info").status_code == 401


def test_api_needs_auth(tok):
    c = TestClient(app)
    assert c.get("/api/pages").status_code == 401
    assert c.get("/api/mijia/devices").status_code == 401
    assert c.get("/api/ready").status_code == 200  # 健康检查保持开放


def test_cookie_makes_frontend_work(tok):
    c = TestClient(app)
    c.get("/mijia")  # 打开一次页面 = 拿到 cookie
    assert c.get("/api/pages").status_code == 200
    assert c.get("/api/mijia/devices").status_code == 200


def test_header_works_for_other_devices(tok):
    c = TestClient(app)
    c.headers["X-HomeOS-Token"] = "a" * 32
    assert c.get("/api/pages").status_code == 200


def test_wrong_header_falls_back_to_cookie(tok):
    c = TestClient(app)
    c.get("/mijia")
    assert c.get("/api/pages", headers={"X-HomeOS-Token": "nope"}).status_code == 200


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
    assert c.get("/api/pages").status_code == 200
    assert "set-cookie" not in c.get("/mijia").headers


def test_closed_without_token_in_prod(monkeypatch):
    """生产未配令牌 = 接口全关：否则 /api/agent/say 的 device 模式就是匿名 shell。"""
    monkeypatch.setattr(config, "TOKEN", "")
    monkeypatch.setattr(config, "DEV", False)
    c = TestClient(app)
    assert c.get("/api/pages").status_code == 401
    assert c.get("/api/agent/turn").status_code == 401
    assert c.get("/api/ready").status_code == 200  # 健康检查不受影响


def test_every_api_router_is_guarded(tok):
    c = TestClient(app)
    for path in ("/api/pages", "/api/mijia/devices", "/api/agent/info"):
        assert c.get(path).status_code == 401, path


def test_invalid_token_degrades_instead_of_crashing(tmp_path):
    """写错令牌不能让面板全黑：降级成未配置（/oc 关掉），日志里说清楚。"""
    (tmp_path / "homeos.yaml").write_text('server: {token: "short; bad"}\n')
    env = {**os.environ, "HOMEOS_CONFIG_DIR": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, "-c", "import app.config as c; print(repr(c.TOKEN))"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "''"
    assert "server.token" in r.stderr
