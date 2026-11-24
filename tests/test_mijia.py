"""米家接口：只验证「数据形状 + 失败怎么报」。不连真云、不点真灯。

设备表的解析与校验在 test_mijia_config.py。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import mijia

DEVICES = {
    "rooms": [{"id": "客厅", "name": "客厅"}, {"id": "次卧", "name": "次卧"}],
    "devices": [
        {"id": "客厅/吸顶灯", "name": "吸顶灯", "room": "客厅", "kind": "switch"},
        {"id": "客厅/温湿度", "name": "温湿度", "room": "客厅", "kind": "th"},
        {"id": "次卧/烟感", "name": "烟感", "room": "次卧", "kind": "smoke"},
    ],
}

VALUES = {
    "ts": 1,
    "values": {
        "客厅/吸顶灯": {"on": True},
        "客厅/温湿度": {"temperature": 24.4, "humidity": 34},
        "次卧/烟感": {"status": 1, "fault": False, "battery": 100},
    },
}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(mijia, "devices", lambda: DEVICES)
    monkeypatch.setattr(mijia, "read_state", lambda room=None: VALUES)
    monkeypatch.setattr(mijia, "set_switch", lambda sid, on: {"id": sid, "on": on})


def test_devices_is_pure_data(fake):
    """设备清单只有名字与类型，没有状态、没有排版。"""
    body = TestClient(app).get("/api/mijia/devices").json()
    assert body["ok"] is True
    assert [r["name"] for r in body["rooms"]] == ["客厅", "次卧"]
    assert body["devices"][0] == {
        "id": "客厅/吸顶灯",
        "name": "吸顶灯",
        "room": "客厅",
        "kind": "switch",
    }
    assert "on" not in body["devices"][0]


def test_state_is_raw_values(fake):
    """状态按设备 id 给原始数值；「24.4° 34%」这种格式是前端的事。"""
    values = TestClient(app).get("/api/mijia/state").json()["values"]
    assert values["客厅/吸顶灯"] == {"on": True}
    assert values["客厅/温湿度"] == {"temperature": 24.4, "humidity": 34}
    assert values["次卧/烟感"] == {"status": 1, "fault": False, "battery": 100}
    assert "text" not in values["客厅/温湿度"]


def test_switch_returns_confirmed_value(fake):
    r = TestClient(app).post("/api/mijia/switch", json={"id": "客厅/吸顶灯", "on": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": "客厅/吸顶灯", "on": False}


def test_switch_rejects_bad_body(fake):
    assert TestClient(app).post("/api/mijia/switch", json={"id": "x"}).status_code == 422


def test_unknown_switch_is_400(fake, monkeypatch):
    def boom(sid, on):
        raise mijia.UnknownSwitch(f"未知开关：{sid}")

    monkeypatch.setattr(mijia, "set_switch", boom)
    r = TestClient(app).post("/api/mijia/switch", json={"id": "ghost", "on": True})
    assert r.status_code == 400
    assert r.json()["detail"] == "未知开关：ghost"  # 不带 KeyError 的引号


def test_not_logged_in_is_503(monkeypatch):
    def boom(room=None):
        raise mijia.MijiaError("米家没登录：auth.json 不在")

    monkeypatch.setattr(mijia, "read_state", boom)
    r = TestClient(app).get("/api/mijia/state")
    assert r.status_code == 503 and "没登录" in r.json()["detail"]


def test_internal_error_does_not_leak_details(monkeypatch):
    def boom(room=None):
        raise RuntimeError("token=secret did=1095838227")

    monkeypatch.setattr(mijia, "read_state", boom)
    r = TestClient(app).get("/api/mijia/state")
    assert r.status_code == 500
    assert r.json()["detail"] == "米家内部错误"
    assert "secret" not in r.text and "1095838227" not in r.text


def test_cache_file_roundtrip_and_version(tmp_path, monkeypatch):
    """缓存文件带版本：形状一变旧文件自动作废，不会把旧结构读回来。"""
    path = tmp_path / "mijia" / "state.json"
    monkeypatch.setattr(mijia, "CACHE_FILE", path)

    mijia._disk.clear()
    mijia._disk["sensor:客厅/温湿度"] = {"ts": 1, "v": {"temperature": 24.4, "humidity": 34}}
    monkeypatch.setattr(mijia, "_dirty", True)  # 落盘只在缓存有新增时发生
    mijia._save_disk()
    saved = json.loads(path.read_text())
    assert saved["version"] == mijia.CACHE_VERSION
    assert saved["entries"]["sensor:客厅/温湿度"]["v"]["humidity"] == 34

    mijia._disk.clear()
    mijia._load_disk()
    assert "sensor:客厅/温湿度" in mijia._disk

    # 版本对不上就当没有
    saved["version"] = mijia.CACHE_VERSION - 1
    path.write_text(json.dumps(saved))
    mijia._disk.clear()
    mijia._load_disk()
    assert mijia._disk == {}


def test_load_disk_ignores_garbage(tmp_path, monkeypatch):
    """缓存文件坏了（顶层是数组 / 完全不是 JSON）不该让米家页一直 500。"""
    path = tmp_path / "mijia" / "state.json"
    path.parent.mkdir()
    monkeypatch.setattr(mijia, "CACHE_FILE", path)
    for garbage in ("[1, 2]", "not json"):
        path.write_text(garbage)
        mijia._disk.clear()
        mijia._load_disk()
        assert mijia._disk == {}


def test_state_cached_per_device(tmp_path, monkeypatch):
    """每台设备一条缓存：一台读失败不影响另一台命中，也不再有整页一次性缓存。"""
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()
    calls = {"ok": 0, "bad": 0}

    table = {
        "rooms": [
            {
                "id": "客厅",
                "label": "客厅",
                "switches": [
                    {"id": "客厅/好灯", "label": "好灯", "did": "1", "prop": "on"},
                    {"id": "客厅/坏灯", "label": "坏灯", "did": "2", "prop": "on"},
                ],
                "sensors": [],
            }
        ],
        "switches": {},
        "sensors": {},
    }
    monkeypatch.setattr("app.mijia_config.load", lambda: table)
    monkeypatch.setattr(mijia, "_client", lambda: object())

    def read_ok(did, prop):
        calls["ok"] += 1
        return True

    def read_bad(did, prop):
        calls["bad"] += 1
        raise RuntimeError("云接口超时")

    monkeypatch.setattr(
        mijia, "_read", lambda did, prop: read_bad(did, prop) if did == "2" else read_ok(did, prop)
    )

    values = mijia.read_state()["values"]
    assert values["客厅/好灯"] == {"on": True}
    assert "error" in values["客厅/坏灯"]
    assert calls == {"ok": 1, "bad": 1}

    # 再读一次：好灯命中缓存不再调云，坏灯继续重试
    mijia.read_state()
    assert calls == {"ok": 1, "bad": 2}


def test_state_accepts_room_filter(monkeypatch):
    seen = {}

    def read(room=None):
        seen["room"] = room
        return VALUES

    monkeypatch.setattr(mijia, "read_state", read)
    r = TestClient(app).get("/api/mijia/state", params={"room": "客厅"})
    assert r.status_code == 200 and seen["room"] == "客厅"


def test_unknown_room_is_400(monkeypatch):
    def read(room=None):
        raise mijia.UnknownRoom(f"未知房间：{room}")

    monkeypatch.setattr(mijia, "read_state", read)
    r = TestClient(app).get("/api/mijia/state", params={"room": "nope"})
    assert r.status_code == 400 and "未知房间" in r.json()["detail"]


def test_read_state_filters_room_and_reads_in_parallel(tmp_path, monkeypatch):
    """只读指定房间；同一间房的设备并行读（顺序不影响结果）。"""
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()
    table = {
        "rooms": [
            {
                "id": "客厅",
                "label": "客厅",
                "switches": [
                    {"id": "客厅/灯", "label": "灯", "did": "1", "prop": "on"},
                ],
                "sensors": [
                    {
                        "id": "客厅/温湿度",
                        "label": "温湿度",
                        "kind": "th",
                        "did": "1",
                        "props": {"temperature": "t", "humidity": "h"},
                    }
                ],
            },
            {
                "id": "次卧",
                "label": "次卧",
                "switches": [{"id": "次卧/灯", "label": "灯", "did": "2", "prop": "on"}],
                "sensors": [],
            },
        ],
        "switches": {},
        "sensors": {},
    }
    monkeypatch.setattr("app.mijia_config.load", lambda: table)
    monkeypatch.setattr(mijia, "_client", lambda: object())
    monkeypatch.setattr(mijia, "_read", lambda did, prop: True)
    monkeypatch.setattr(
        mijia, "_sensor_values", lambda sensor: {"temperature": 24.0, "humidity": 30}
    )

    values = mijia.read_state("客厅")["values"]
    assert set(values) == {"客厅/灯", "客厅/温湿度"}
    assert values["客厅/灯"] == {"on": True}

    with pytest.raises(mijia.UnknownRoom):
        mijia.read_state("没有这间")


def _one_switch_table():
    return {
        "rooms": [],
        "switches": {
            "客厅/灯": {"id": "客厅/灯", "label": "灯", "did": "1", "prop": "on"},
        },
        "sensors": {},
    }


def test_set_switch_confirms_by_readback(monkeypatch):
    """写完要读回；读回等于目标值才算成功，并且只作废这一台的缓存。"""
    monkeypatch.setattr("app.mijia_config.load", _one_switch_table)
    monkeypatch.setattr(mijia, "_client", lambda: object())
    monkeypatch.setattr(mijia.time, "sleep", lambda _: None)

    class Dev:
        value = False

        def set(self, prop, want):
            self.value = want

        def get(self, prop):
            return self.value

    dev = Dev()
    monkeypatch.setattr(mijia, "_dev", lambda did: dev)
    mijia._disk.clear()
    mijia._disk["switch:客厅/灯"] = {"ts": 1, "v": {"on": False}}
    mijia._disk["sensor:客厅/温湿度"] = {"ts": 1, "v": {"temperature": 24.0, "humidity": 30}}

    assert mijia.set_switch("客厅/灯", True) == {"id": "客厅/灯", "on": True}
    assert "switch:客厅/灯" not in mijia._disk  # 这一台被作废
    assert "sensor:客厅/温湿度" in mijia._disk  # 别的设备不受影响


def test_set_switch_reports_actual_value_when_readback_disagrees(monkeypatch):
    """设备没按预期变化时不谎报成功。"""
    monkeypatch.setattr("app.mijia_config.load", _one_switch_table)
    monkeypatch.setattr(mijia, "_client", lambda: object())
    monkeypatch.setattr(mijia.time, "sleep", lambda _: None)

    class Stubborn:
        def set(self, prop, want):
            pass  # ack 了，但状态一直不变

        def get(self, prop):
            return False

    monkeypatch.setattr(mijia, "_dev", lambda did: Stubborn())
    assert mijia.set_switch("客厅/灯", True)["on"] is False


def test_set_switch_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr("app.mijia_config.load", _one_switch_table)
    with pytest.raises(mijia.UnknownSwitch):
        mijia.set_switch("没有这台", True)


def test_cached_returns_stale_value_when_refetch_fails(tmp_path, monkeypatch):
    """有旧值、这次读失败 → 返回旧值并标 stale（不是死代码，别再被当成不可达分支删掉）。"""
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()
    mijia._disk["switch:x"] = {"ts": 0, "v": {"on": True}}

    def boom():
        raise RuntimeError("云接口超时")

    out = mijia._cached("switch:x", 0.01, boom)
    assert out == {"on": True, "stale": True}


def test_save_disk_is_safe_under_concurrency(tmp_path, monkeypatch):
    """落盘时另一个线程正在插键：不能抛「dictionary changed size during iteration」。"""
    import threading

    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()
    mijia._dirty = True
    stop = threading.Event()
    errors = []

    def churn():
        n = 0
        while not stop.is_set():
            mijia._disk[f"k{n}"] = {"ts": 1, "v": {"on": n % 2 == 0}}
            n += 1

    worker = threading.Thread(target=churn)
    worker.start()
    try:
        for _ in range(20):
            mijia._save_disk()
    except Exception as e:  # noqa: BLE001 — 就是想抓住任何异常
        errors.append(e)
    finally:
        stop.set()
        worker.join()

    assert errors == []
    assert mijia.CACHE_FILE.is_file()


def test_read_errors_are_not_cached(tmp_path, monkeypatch):
    """读失败不能被当成数据缓存：否则一次瞬时超时会把「读不到」锁死一整个 TTL。"""
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()

    def boom():
        raise RuntimeError("云接口超时")

    with pytest.raises(RuntimeError):
        mijia._cached("sensor:x", 900, boom)
    assert "sensor:x" not in mijia._disk  # 无缓存时：抛异常，什么都不写

    mijia._disk["sensor:x"] = {"ts": 0, "v": {"temperature": 24.0, "humidity": 30}}
    out = mijia._cached("sensor:x", 900, boom)  # 有旧值时：返回旧值 + stale
    assert out["stale"] is True
    assert mijia._disk["sensor:x"]["ts"] == 0  # 落盘时间戳没被刷新（不算成功读取）
