"""米家：设备表解析 + 接口（数据形状、失败怎么报）。不连真云、不点真灯。"""

import json
import os
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import mijia, speech

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
    def say(text, device=""):
        return {"text": text.strip(), "did": device}

    monkeypatch.setattr(mijia, "devices", lambda: DEVICES)
    monkeypatch.setattr(mijia, "values", lambda ids=None: VALUES)
    monkeypatch.setattr(mijia, "set_power", lambda sid, on: {"id": sid, "on": on})
    monkeypatch.setattr(speech, "say", say)


def test_devices_is_pure_data(fake):
    """设备清单只有名字与类型，没有状态、没有排版。"""
    body = TestClient(app).get("/api/info/mijia").json()
    assert [r["name"] for r in body["rooms"]] == ["客厅", "次卧"]
    assert body["devices"][0] == {
        "id": "客厅/吸顶灯",
        "name": "吸顶灯",
        "room": "客厅",
        "kind": "switch",
    }
    assert "on" not in body["devices"][0]


def test_values_are_raw_data(fake):
    """值按设备 id 给原始数值；「24.4° 34%」这种格式是前端的事。"""
    values = TestClient(app).get("/api/mijia/values").json()["values"]
    assert values["客厅/吸顶灯"] == {"on": True}
    assert values["客厅/温湿度"] == {"temperature": 24.4, "humidity": 34}
    assert values["次卧/烟感"] == {"status": 1, "fault": False, "battery": 100}
    assert "text" not in values["客厅/温湿度"]


def test_values_accepts_ids_filter(fake, monkeypatch):
    seen = {}

    def read(ids=None):
        seen["ids"] = ids
        return VALUES

    monkeypatch.setattr(mijia, "values", read)
    r = TestClient(app).get("/api/mijia/values", params={"ids": "客厅/吸顶灯,次卧/烟感"})
    assert r.status_code == 200 and seen["ids"] == ["客厅/吸顶灯", "次卧/烟感"]


def test_unknown_device_is_400(monkeypatch):
    def read(ids=None):
        raise mijia.UnknownDevice("未知设备：ghost")

    monkeypatch.setattr(mijia, "values", read)
    r = TestClient(app).get("/api/mijia/values", params={"ids": "ghost"})
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "unknown_device", "message": "未知设备：ghost"}


def test_power_returns_confirmed_value(fake):
    r = TestClient(app).put("/api/mijia/power", json={"id": "客厅/吸顶灯", "on": False})
    assert r.status_code == 200
    assert r.json() == {"id": "客厅/吸顶灯", "on": False}


def test_power_rejects_bad_body(fake):
    assert TestClient(app).put("/api/mijia/power", json={"id": "x"}).status_code == 422


def test_speech_passes_text_through(fake):
    r = TestClient(app).post("/api/speech", json={"text": " 开会了 "})
    assert r.status_code == 200
    assert r.json() == {"text": "开会了", "did": ""}


def test_speech_can_target_another_speaker(fake):
    r = TestClient(app).post("/api/speech", json={"text": "开会了", "device": "380623598"})
    assert r.status_code == 200
    assert r.json()["did"] == "380623598"


def test_speech_rejects_empty_text(fake):
    r = TestClient(app).post("/api/speech", json={"text": "   "})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_input"


def test_speech_rejects_bad_body(fake):
    assert TestClient(app).post("/api/speech", json={}).status_code == 422


def test_speech_without_speaker_is_503(monkeypatch):
    def boom(text, device=""):
        raise mijia.MijiaError("没配播报啣叭")

    monkeypatch.setattr(speech, "say", boom)
    r = TestClient(app).post("/api/speech", json={"text": "喂"})
    assert r.status_code == 503 and "播报" in r.json()["error"]["message"]


def test_unknown_device_on_power_is_400(fake, monkeypatch):
    def boom(sid, on):
        raise mijia.UnknownDevice(f"未知设备：{sid}")

    monkeypatch.setattr(mijia, "set_power", boom)
    r = TestClient(app).put("/api/mijia/power", json={"id": "ghost", "on": True})
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "未知设备：ghost"  # 不带 KeyError 的引号


def test_not_logged_in_is_503(monkeypatch):
    def boom(ids=None):
        raise mijia.MijiaError("米家没登录：auth.json 不在")

    monkeypatch.setattr(mijia, "values", boom)
    r = TestClient(app).get("/api/mijia/values")
    assert r.status_code == 503 and "没登录" in r.json()["error"]["message"]


def test_internal_error_does_not_leak_details(monkeypatch):
    def boom(ids=None):
        raise RuntimeError("token=secret did=1095838227")

    monkeypatch.setattr(mijia, "values", boom)
    r = TestClient(app).get("/api/mijia/values")
    assert r.status_code == 500
    assert r.json()["error"]["message"] == "米家内部错误"
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
        "rooms": [],
        "switches": {
            "客厅/好灯": {"id": "客厅/好灯", "label": "好灯", "did": "1", "prop": "on"},
            "客厅/坏灯": {"id": "客厅/坏灯", "label": "坏灯", "did": "2", "prop": "on"},
        },
        "sensors": {},
    }
    monkeypatch.setattr(mijia, "_load_table", lambda: table)
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

    values = mijia.values()["values"]
    assert values["客厅/好灯"] == {"on": True}
    assert "error" in values["客厅/坏灯"]
    assert calls == {"ok": 1, "bad": 1}

    # 再读一次：好灯命中缓存不再调云，坏灯继续重试
    mijia.values()
    assert calls == {"ok": 1, "bad": 2}


def test_values_filters_ids_and_skips_other_devices(tmp_path, monkeypatch):
    """只读指定设备；写在同一表里的其它设备不会被碰。"""
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")
    mijia._disk.clear()
    table = {
        "rooms": [],
        "switches": {
            "客厅/灯": {"id": "客厅/灯", "label": "灯", "did": "1", "prop": "on"},
            "次卧/灯": {"id": "次卧/灯", "label": "灯", "did": "2", "prop": "on"},
        },
        "sensors": {
            "客厅/温湿度": {
                "id": "客厅/温湿度",
                "label": "温湿度",
                "kind": "th",
                "did": "1",
                "props": {"temperature": "t", "humidity": "h"},
            }
        },
    }
    monkeypatch.setattr(mijia, "_load_table", lambda: table)
    monkeypatch.setattr(mijia, "_client", lambda: object())
    monkeypatch.setattr(mijia, "_read", lambda did, prop: True)
    monkeypatch.setattr(
        mijia, "_sensor_values", lambda sensor: {"temperature": 24.0, "humidity": 30}
    )

    values = mijia.values(["客厅/灯", "客厅/温湿度"])["values"]
    assert set(values) == {"客厅/灯", "客厅/温湿度"}
    assert values["客厅/灯"] == {"on": True}

    with pytest.raises(mijia.UnknownDevice):
        mijia.values(["没有这台"])


def _one_switch_table():
    return {
        "rooms": [],
        "switches": {
            "客厅/灯": {"id": "客厅/灯", "label": "灯", "did": "1", "prop": "on"},
        },
        "sensors": {},
    }


def test_set_power_confirms_by_readback(monkeypatch):
    """写完要读回；读回等于目标值才算成功，并且只作废这一台的缓存。"""
    monkeypatch.setattr(mijia, "_load_table", _one_switch_table)
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

    assert mijia.set_power("客厅/灯", True) == {"id": "客厅/灯", "on": True}
    assert "switch:客厅/灯" not in mijia._disk  # 这一台被作废
    assert "sensor:客厅/温湿度" in mijia._disk  # 别的设备不受影响


def test_set_power_reports_actual_value_when_readback_disagrees(monkeypatch):
    """设备没按预期变化时不谎报成功。"""
    monkeypatch.setattr(mijia, "_load_table", _one_switch_table)
    monkeypatch.setattr(mijia, "_client", lambda: object())
    monkeypatch.setattr(mijia.time, "sleep", lambda _: None)

    class Stubborn:
        def set(self, prop, want):
            pass  # ack 了，但状态一直不变

        def get(self, prop):
            return False

    monkeypatch.setattr(mijia, "_dev", lambda did: Stubborn())
    assert mijia.set_power("客厅/灯", True)["on"] is False


def test_set_power_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr(mijia, "_load_table", _one_switch_table)
    with pytest.raises(mijia.UnknownDevice):
        mijia.set_power("没有这台", True)


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


# ---------------------------------------------------------------- 设备表

GOOD = """
rooms:
  客厅:
    吸顶灯: { did: "1", prop: "on-3" }
    温湿度:
      kind: th
      did: "1"
      props: { temperature: "temperature-6", humidity: "relative-humidity-6" }
  次卧:
    烟感:
      kind: smoke
      did: "blt.1"
      props: { status: "status", battery: "battery-level" }
"""


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "mijia.yaml"
    path.write_text(textwrap.dedent(GOOD), encoding="utf-8")
    monkeypatch.setattr(config, "MIJIA_CONFIG", path)
    mijia._table_cache = None
    return path


def test_two_levels_and_order(table):
    data = mijia._load_table()
    assert [r["label"] for r in data["rooms"]] == ["客厅", "次卧"]
    room = data["rooms"][0]
    assert [s["label"] for s in room["switches"]] == ["吸顶灯"]
    assert [s["label"] for s in room["sensors"]] == ["温湿度"]
    assert set(data["switches"]) == {"客厅/吸顶灯"}
    assert set(data["sensors"]) == {"客厅/温湿度", "次卧/烟感"}


def test_switch_needs_did_and_prop(table):
    assert mijia._load_table()["switches"]["客厅/吸顶灯"] == {
        "id": "客厅/吸顶灯",
        "label": "吸顶灯",
        "did": "1",
        "prop": "on-3",
    }


def test_reload_when_file_changes(table):
    assert [r["label"] for r in mijia._load_table()["rooms"]] == ["客厅", "次卧"]
    table.write_text('rooms:\n  书房:\n    灯: { did: "9", prop: "on" }\n', encoding="utf-8")
    later = time.time() + 2
    os.utime(table, (later, later))  # mtime 只到秒
    assert [r["label"] for r in mijia._load_table()["rooms"]] == ["书房"]


def test_missing_prop_reports_position(table):
    table.write_text('rooms:\n  客厅:\n    灯: { did: "1" }\n', encoding="utf-8")
    with pytest.raises(mijia.TableError) as e:
        mijia._load_table()
    assert "客厅.灯" in str(e.value) and "prop" in str(e.value)


def test_bad_kind_and_missing_props(table):
    table.write_text(
        'rooms:\n  客厅:\n    灯:\n      kind: lux\n      did: "1"\n      props: {}\n',
        encoding="utf-8",
    )
    with pytest.raises(mijia.TableError) as e:
        mijia._load_table()
    assert "kind" in str(e.value)


def test_slash_in_name_rejected(table):
    table.write_text('rooms:\n  客/厅:\n    灯: { did: "1", prop: "on" }\n', encoding="utf-8")
    with pytest.raises(mijia.TableError):
        mijia._load_table()


def test_empty_rooms_rejected(table):
    table.write_text("rooms: {}\n", encoding="utf-8")
    with pytest.raises(mijia.TableError):
        mijia._load_table()


def test_missing_file_is_table_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIJIA_CONFIG", tmp_path / "nope.yaml")
    mijia._table_cache = None
    with pytest.raises(mijia.TableError) as e:
        mijia._load_table()
    assert "读不到设备表" in str(e.value)


def test_repo_config_is_valid(repo_root, monkeypatch):
    """仓库里那份真的设备表必须能过校验（改了 YAML 立刻能发现写错）。"""
    monkeypatch.setattr(config, "MIJIA_CONFIG", repo_root / "config" / "mijia.yaml")
    mijia._table_cache = None
    data = mijia._load_table()
    assert data["rooms"], "至少要有一个房间"
    assert all(s["prop"] for s in data["switches"].values())


def test_tts_is_top_level_not_a_room_device(table):
    assert mijia._load_table()["tts"] is None  # 没写就是没有播报能力
    table.write_text(table.read_text("utf-8") + '\ntts: { did: "380" }\n', encoding="utf-8")
    mijia._table_cache = None
    data = mijia._load_table()
    assert data["tts"] == {"did": "380"}
    names = [one["id"] for room in data["rooms"] for one in room["switches"] + room["sensors"]]
    assert "380" not in names  # 不混进房间设备里


def test_tts_needs_did(table):
    table.write_text(table.read_text("utf-8") + "\ntts: {}\n", encoding="utf-8")
    mijia._table_cache = None
    with pytest.raises(mijia.TableError, match="tts 的 did"):
        mijia._load_table()
