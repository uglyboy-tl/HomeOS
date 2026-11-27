"""契约：路由清单、认证边界、能力登记、错误信封。

这里断言的是"对外承诺"，不是实现细节。路由或错误码一变，这些用例会先红，
逼着同步前端、文档、NAS 上的 cron。
"""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

# 有意的接口清单：改这里之前先想清楚谁在用（页面 / cron / 其他程序）
API_ROUTES = {
    ("POST", "/api/agent/abort"),
    ("POST", "/api/agent/ask"),
    ("POST", "/api/agent/new-session"),
    ("POST", "/api/agent/prompt"),
    ("GET", "/api/agent/stream"),
    ("GET", "/api/agent/turn"),
    ("GET", "/api/info"),
    ("GET", "/api/info/agent"),
    ("GET", "/api/info/agent/state"),
    ("GET", "/api/info/mijia"),
    ("GET", "/api/info/pages"),
    ("GET", "/api/metrics"),
    ("PUT", "/api/mijia/power"),
    ("GET", "/api/mijia/values"),
    ("POST", "/api/mpd/library/rebuild"),
    ("POST", "/api/mpd/library/update"),
    ("PUT", "/api/mpd/playback"),
    ("POST", "/api/mpd/playlist"),
    ("POST", "/api/mpd/queue/next"),
    ("POST", "/api/mpd/queue/prev"),
    ("PATCH", "/api/mpd/settings"),
    ("GET", "/api/mpd/state"),
    ("POST", "/api/notify"),
    ("GET", "/api/ready"),
    ("POST", "/api/speech"),
}

PAGES = {
    ("GET", "/"),
    ("GET", "/{name}"),
    ("GET", "/p/{slug}"),
    ("GET", "/assets/{name:path}"),
    ("GET", "/pages/{slug}/assets/{name:path}"),
}

ERROR_CODES = {
    "bad_input",
    "busy",
    "conflict",
    "cooldown",
    "internal",
    "not_configured",
    "not_found",
    "pi_crashed",
    "pi_timeout",
    "pi_unavailable",
    "unauthorized",
    "unknown_device",
    "unknown_mode",
    "unavailable",
}


def _api_routes() -> set[tuple[str, str]]:
    found = set()
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/"):
            for method in ops:
                if method.upper() not in {"HEAD", "OPTIONS"}:
                    found.add((method.upper(), path))
    return found


def _page_routes() -> set[tuple[str, str]]:
    from app.routes import pages

    return {
        (method, route.path)
        for route in pages.router.routes
        for method in getattr(route, "methods", ())
        if method != "HEAD"
    }


def test_api_route_inventory_is_intentional():
    """增删改接口必须是有意识的：页面、cron、其他程序都写死了这些路径。"""
    assert _api_routes() == API_ROUTES, "接口清单变了：同步 web/、docs/ARCHITECTURE.md、NAS crontab"


def test_page_route_inventory_is_stable():
    assert _page_routes() == PAGES


@pytest.mark.parametrize(("method", "path"), sorted(API_ROUTES - {("GET", "/api/ready")}))
def test_api_requires_token(method, path, monkeypatch):
    """除 /api/ready 外，任何接口没有令牌都必须是 401（不是 422、不是 500）。"""
    monkeypatch.setattr(config, "TOKEN", "a" * 32)
    monkeypatch.setattr(config, "DEV", False)
    body = {} if method != "GET" else None
    response = TestClient(app).request(method, path, json=body)
    assert response.status_code == 401, f"{method} {path} 漏了认证"


def test_ready_stays_open():
    assert TestClient(app).get("/api/ready").status_code == 200


EXPECTED_CAPABILITIES = {
    "agent",
    "asr",
    "metrics",
    "mijia.cloud",
    "mijia.table",
    "mpd",
    "notify",
}


def test_capabilities_cover_every_registered_capability():
    """页面与 cron 用 /api/info 预判：登记的能力必须一个不少地报出来。"""
    body = TestClient(app).get("/api/info").json()
    assert isinstance(body["problems"], list)
    assert set(body["capabilities"]) == EXPECTED_CAPABILITIES


def test_every_capability_reports_a_known_shape():
    for name, status in TestClient(app).get("/api/info").json()["capabilities"].items():
        assert set(status) == {"ok", "code", "message", "checked_at"}, name
        assert status["code"] in {"ok", "unknown", "not_configured", "unavailable"}, name


def _error(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert body["error"]["code"] in ERROR_CODES, body
    return body["error"]


def test_unauthorized_envelope_keeps_www_authenticate(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "a" * 32)
    monkeypatch.setattr(config, "DEV", False)
    response = TestClient(app).get("/api/info")
    assert response.status_code == 401
    assert _error(response)["code"] == "unauthorized"
    assert response.headers.get("WWW-Authenticate")


def test_not_found_on_api():
    response = TestClient(app).get("/api/没有这个")
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


def test_validation_error_is_bad_input():
    response = TestClient(app).put("/api/mijia/power", json={"device": "客厅/吸顶灯"})
    assert response.status_code == 422
    assert _error(response)["code"] == "bad_input"


def test_method_not_allowed_is_bad_input():
    response = TestClient(app).delete("/api/mpd/state")
    assert response.status_code == 405
    assert _error(response)["code"] == "bad_input"


def test_not_configured_is_503(monkeypatch):
    monkeypatch.setattr(config, "GOTIFY_URL", "")
    monkeypatch.setattr(config, "GOTIFY_TOKEN", "")
    response = TestClient(app).post("/api/notify", json={"text": "hi"})
    assert response.status_code == 503
    assert _error(response)["code"] == "not_configured"


def test_unhandled_error_is_opaque_500(monkeypatch):
    from app.services import notify as notify_service

    def boom(*_args, **_kwargs):
        raise RuntimeError("内部细节不该出现在响应里")

    monkeypatch.setattr(notify_service, "push", boom)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/notify", json={"text": "hi"})
    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "internal"
    assert "内部细节" not in error["message"]


def test_stream_rejects_unknown_mode_before_opening():
    response = TestClient(app).get("/api/agent/stream", params={"mode": "没有这个"})
    assert response.status_code == 400
    assert _error(response)["code"] == "unknown_mode"


def test_security_headers_on_every_response():
    for path in ("/", "/api/ready", "/api/没有这个"):
        headers = TestClient(app).get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff", path
        assert headers["X-Frame-Options"] == "DENY", path
        assert headers["Referrer-Policy"] == "no-referrer", path
