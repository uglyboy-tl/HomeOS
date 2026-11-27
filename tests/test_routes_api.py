"""新接口的路由层：错误映射、参数校验、状态码（service 层已在 test_mpd/test_notify 覆盖）。"""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import mpd, notify


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def broken(monkeypatch):
    def boom(*_a, **_k):
        raise mpd.MpdError("连不上 MPD")

    monkeypatch.setattr(mpd, "state", boom)
    monkeypatch.setattr(mpd, "control", boom)


def test_mpd_state_ok(client, monkeypatch):
    monkeypatch.setattr(mpd, "state", lambda: {"state": "play", "volume": 20})
    r = client.get("/api/mpd/state")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "device": "mpd", "state": {"state": "play", "volume": 20}}


def test_mpd_state_upstream_down_is_503(client, broken):
    assert client.get("/api/mpd/state").status_code == 503


def test_mpd_unknown_device_is_400(client):
    assert client.get("/api/mpd/state?device=kef").status_code == 400
    r = client.post("/api/mpd/control", json={"action": "stop", "device": "kef"})
    assert r.status_code == 400 and "暂不支持" in r.text


def test_mpd_empty_playlist_is_400(client):
    """空串会被当成"没传"，等于换列表失败却静默播上一份。"""
    assert (
        client.post("/api/mpd/control", json={"action": "play", "playlist": ""}).status_code == 400
    )


def test_mpd_volume_needs_value_and_range(client):
    assert client.post("/api/mpd/control", json={"action": "volume"}).status_code == 400
    assert (
        client.post("/api/mpd/control", json={"action": "volume", "volume": 101}).status_code == 400
    )
    assert (
        client.post("/api/mpd/control", json={"action": "play", "crossfade": 20}).status_code == 400
    )


def test_mpd_field_not_used_by_action_is_400(client):
    """action=next 带 volume 会静默丢弃，跟 extra="forbid" 的初衷相反。"""
    r = client.post("/api/mpd/control", json={"action": "next", "volume": 20})
    assert r.status_code == 400 and "volume" in r.text


def test_mpd_typo_field_is_rejected(client):
    assert (
        client.post("/api/mpd/control", json={"action": "volume", "volumn": 20}).status_code == 422
    )


def test_mpd_concurrent_sync_is_409(client, monkeypatch):
    def busy(*_a, **_k):
        raise mpd.MpdBusyError("已有一次同步在跑")

    monkeypatch.setattr(mpd, "sync", busy)
    assert client.post("/api/mpd/sync", json={}).status_code == 409


def test_mpd_missing_playlist_is_404(client, monkeypatch):
    def not_found(*_a, **_k):
        raise mpd.MpdNotFound("ACK No such playlist")

    monkeypatch.setattr(mpd, "control", not_found)
    r = client.post("/api/mpd/control", json={"action": "play", "playlist": "不存在"})
    assert r.status_code == 404


def test_mpd_newline_in_playlist_is_400(client):
    r = client.post("/api/mpd/control", json={"action": "play", "playlist": "x\nsetvol 100"})
    assert r.status_code == 400


def test_notify_ok_and_result_shape(client, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"gotify": {"ok": True, "id": 1}})
    r = client.post("/api/notify", json={"text": "hello", "level": "warn"})
    assert r.status_code == 200 and r.json() == {
        "ok": True,
        "results": {"gotify": {"ok": True, "id": 1}},
    }


def test_notify_all_channels_failed_is_503(client, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: {"gotify": {"ok": False, "error": "x"}})
    r = client.post("/api/notify", json={"text": "hello"})
    assert r.status_code == 503 and r.json()["ok"] is False


def test_notify_validates_input(client):
    assert client.post("/api/notify", json={"text": "  "}).status_code == 400
    assert client.post("/api/notify", json={"text": "x", "channels": []}).status_code == 400
    assert client.post("/api/notify", json={"text": "x", "level": "fatal"}).status_code == 422
    assert client.post("/api/notify", json={"text": "x", "channels": ["sms"]}).status_code == 422


def test_mpd_route_needs_host(client, monkeypatch):
    monkeypatch.setattr(config, "MPD_HOST", "")
    r = client.get("/api/mpd/state")
    assert r.status_code == 503 and "mpd.host" in r.json()["detail"]
