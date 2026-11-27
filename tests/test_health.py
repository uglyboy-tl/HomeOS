"""能力可用性：静态检查挡住、运行期缓存挡住、调用后回写、info 永不被挡。"""

from app import config
from app.lib import health
from app.services import mijia, mpd


def test_static_check_blocks_whole_router(client, monkeypatch):
    monkeypatch.setattr(config, "MPD_HOST", "")
    r = client.get("/api/mpd/state")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "not_configured"
    assert "mpd.host" in r.json()["error"]["message"]


def test_runtime_cache_blocks_without_probing(client, monkeypatch):
    monkeypatch.setattr(mpd, "state", lambda: {"state": "play"})
    health.note("mpd", False, "连不上 MPD")
    r = client.get("/api/mpd/state")
    assert r.status_code == 503
    assert r.json()["error"] == {"code": "unavailable", "message": "连不上 MPD"}


def test_cooldown_expires_and_next_call_recovers(client, monkeypatch):
    # 冷却按能力在注册时定下（agent 是 0），这里把 mpd 的冷却也设成 0 来测半开
    monkeypatch.setattr(health._registry["mpd"], "_cooldown", 0.0)
    monkeypatch.setattr(mpd, "state", lambda: {"state": "play"})
    health.note("mpd", False, "临时故障")
    r = client.get("/api/mpd/state")
    assert r.status_code == 200
    assert health.status()["mpd"]["ok"] is True  # 真实调用成功后就恢复


def test_successful_call_marks_capability_ok(client, monkeypatch):
    monkeypatch.setattr(mijia, "values", lambda ids=None: {"values": {}})
    assert client.get("/api/mijia/values").status_code == 200
    assert health.status()["mijia.cloud"]["code"] == "ok"


def test_failed_call_marks_capability_down(client, monkeypatch):
    def boom(ids=None):
        raise mijia.MijiaError("米家没登录")

    monkeypatch.setattr(mijia, "values", boom)
    assert client.get("/api/mijia/values").status_code == 503
    status = health.status()["mijia.cloud"]
    assert status["ok"] is False and status["message"] == "米家没登录"


def test_info_is_never_gated(client, monkeypatch):
    """agent 坏了、米家没登录，描述类接口照样 200 并如实说明。"""
    monkeypatch.setattr(config, "PI_BIN", "/nonexistent/pi")
    monkeypatch.setattr(config, "PI_EXTENSIONS", [])
    info = client.get("/api/info").json()
    assert info["capabilities"]["agent"]["ok"] is False
    assert info["capabilities"]["agent"]["code"] == "not_configured"
    assert client.get("/api/info/agent").status_code == 200
    assert client.get("/api/info/mijia").status_code == 200
    assert client.get("/api/info/pages").status_code == 200


def test_mijia_catalog_works_while_cloud_is_down(client, monkeypatch):
    monkeypatch.setattr(mijia, "devices", lambda: {"rooms": [], "devices": []})
    health.note("mijia.cloud", False, "米家没登录")
    assert client.get("/api/info/mijia").status_code == 200
    assert client.get("/api/mijia/values").status_code == 503


def test_info_reports_config_problems(client, monkeypatch):
    monkeypatch.setattr(config, "PROBLEMS", ["mpd：port 应该是整数"])
    assert client.get("/api/info").json()["problems"] == ["mpd：port 应该是整数"]
