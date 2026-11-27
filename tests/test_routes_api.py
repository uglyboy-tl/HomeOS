"""新接口的路由层：错误映射、参数校验、状态码（service 层已在 test_mpd/test_notify 覆盖）。"""

import pytest

from app import config
from app.services import mpd, notify


@pytest.fixture
def broken(monkeypatch):
    def boom(*_a, **_k):
        raise mpd.MpdError("连不上 MPD")

    monkeypatch.setattr(mpd, "state", boom)
    monkeypatch.setattr(mpd, "playback", boom)


def test_mpd_state_ok(client, monkeypatch):
    monkeypatch.setattr(mpd, "state", lambda: {"state": "play", "volume": 20})
    r = client.get("/api/mpd/state")
    assert r.status_code == 200
    assert r.json() == {"state": "play", "volume": 20}


def test_mpd_state_upstream_down_is_503(client, broken):
    assert client.get("/api/mpd/state").status_code == 503


def test_mpd_playback_applies_state(client, monkeypatch):
    seen = {}

    def playback(want):
        seen["want"] = want
        return {"state": "paused"}

    monkeypatch.setattr(mpd, "playback", playback)
    r = client.put("/api/mpd/playback", json={"state": "paused"})
    assert r.status_code == 200 and r.json() == {"state": "paused"}
    assert seen["want"] == "paused"


def test_mpd_playback_rejects_unknown_state(client):
    assert client.put("/api/mpd/playback", json={"state": "rewind"}).status_code == 422


def test_mpd_settings_validates(client):
    """参数形状与范围都归 Pydantic：统一 422 + 同一套错误体。"""
    for body in ({}, {"volume": 101}, {"crossfade": 20}, {"volumn": 20}):
        r = client.patch("/api/mpd/settings", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"]["code"] == "bad_input"
    assert (
        "至少要改一个参数" in client.patch("/api/mpd/settings", json={}).json()["error"]["message"]
    )


def test_mpd_settings_pass_through(client, monkeypatch):
    seen = {}

    def settings(**kwargs):
        seen.update(kwargs)
        return {"volume": 26}

    monkeypatch.setattr(mpd, "settings", settings)
    r = client.patch("/api/mpd/settings", json={"volume": 26})
    assert r.status_code == 200 and r.json() == {"volume": 26}
    assert seen == {"volume": 26, "random": None, "repeat": None, "crossfade": None}


def test_mpd_empty_playlist_is_400(client):
    """空串会被当成"没传"，等于换列表失败却静默播上一份。"""
    assert client.post("/api/mpd/playlist", json={"name": "  "}).status_code == 400


def test_mpd_playlist_typo_field_is_rejected(client):
    assert client.post("/api/mpd/playlist", json={"name": "x", "songs": 3}).status_code == 422


def test_mpd_library_rebuild_busy_is_409(client, monkeypatch):
    def busy(*_a, **_k):
        raise mpd.MpdBusyError("已有一次同步在跑")

    monkeypatch.setattr(mpd, "library_rebuild", busy)
    r = client.post("/api/mpd/library/rebuild")
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"


def test_mpd_library_update_ok(client, monkeypatch):
    monkeypatch.setattr(mpd, "library_update", lambda: {"updated": True})
    assert client.post("/api/mpd/library/update").json() == {"updated": True}


def test_mpd_missing_playlist_is_404(client, monkeypatch):
    def not_found(*_a, **_k):
        raise mpd.MpdNotFound("ACK No such playlist")

    monkeypatch.setattr(mpd, "playlist", not_found)
    r = client.post("/api/mpd/playlist", json={"name": "不存在"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_mpd_next_while_stopped_is_409(client, monkeypatch):
    def conflict(*_a, **_k):
        raise mpd.MpdConflict("Not playing")

    monkeypatch.setattr(mpd, "queue_next", conflict)
    r = client.post("/api/mpd/queue/next")
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_notify_pushes_and_returns_id(client, monkeypatch):
    seen = {}

    def push(text, title="", level="info"):
        seen.update(text=text, title=title, level=level)
        return {"id": 1}

    monkeypatch.setattr(notify, "push", push)
    r = client.post("/api/notify", json={"text": "hello", "level": "warn"})
    assert r.status_code == 200 and r.json() == {"id": 1}
    assert seen == {"text": "hello", "title": "", "level": "warn"}


def test_notify_failure_is_503(client, monkeypatch):
    def push(*_a, **_k):
        raise notify.NotifyError("Gotify 挂了")

    monkeypatch.setattr(notify, "push", push)
    r = client.post("/api/notify", json={"text": "hello"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"


def test_notify_validates_input(client):
    assert client.post("/api/notify", json={"text": "  "}).status_code == 400
    assert client.post("/api/notify", json={"text": "x", "level": "fatal"}).status_code == 422
    assert client.post("/api/notify", json={"text": "x", "channels": ["sms"]}).status_code == 422


def test_ask_rejects_unknown_field(client):
    r = client.post("/api/agent/ask", json={"input": "x", "agent": "chat"})
    assert r.status_code == 422


def test_mpd_route_needs_host(client, monkeypatch):
    monkeypatch.setattr(config, "MPD_HOST", "")
    r = client.get("/api/mpd/state")
    assert r.status_code == 503 and "mpd.host" in r.json()["error"]["message"]


def test_validation_errors_use_the_same_envelope(client):
    r = client.put("/api/mijia/power", json={"id": "x"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "bad_input"
    assert "on" in body["message"]


def test_agent_rejects_empty_text_and_bad_mode(client, monkeypatch):
    assert client.post("/api/agent/prompt", json={"text": ""}).status_code == 422
    r = client.post("/api/agent/prompt", json={"mode": "BAD", "text": "你好"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_input"


def test_mpd_settings_rejects_empty_body_and_bad_values(client):
    c = client
    assert c.patch("/api/mpd/settings", json={}).status_code == 422
    assert c.patch("/api/mpd/settings", json={"volume": 101}).status_code == 422
    assert c.patch("/api/mpd/settings", json={"volume": -1}).status_code == 422
    assert c.patch("/api/mpd/settings", json={"crossfade": -1}).status_code == 422
    assert c.patch("/api/mpd/settings", json={"volume": "响"}).status_code == 422


def test_mijia_devices_reports_a_broken_table(client, monkeypatch):
    from app.services import mijia

    def broken():
        raise mijia.TableError("顶层要有 rooms")

    monkeypatch.setattr(mijia, "devices", broken)
    response = client.get("/api/info/mijia")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"


def test_pages_scan_skips_broken_meta(tmp_path, monkeypatch):
    from app import scan

    root = tmp_path / "pages"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "index.html").write_text("<h1>x</h1>")
    (root / "ok" / "meta.json").write_text('{"title": "好的"}')
    (root / "bad").mkdir()
    (root / "bad" / "index.html").write_text("<h1>x</h1>")
    (root / "bad" / "meta.json").write_text("{不是 json")
    (root / "list").mkdir()
    (root / "list" / "index.html").write_text("<h1>x</h1>")
    (root / "list" / "meta.json").write_text("[]")
    monkeypatch.setattr(config, "PAGES_DIR", root)
    assert [item["slug"] for item in scan.features()] == ["ok"]
