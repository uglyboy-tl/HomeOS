"""米家服务层：缓存分层、读回确认、落盘、播报、设备表缓存。

路由层已经有用例；这里只测服务自己的判断，云端用 tests/fakes/mijia.py 替身。
"""

from __future__ import annotations

import json
import os

import pytest

from app import config
from app.services import mijia
from tests.fakes.mijia import FakeApi, install

TABLE = """
rooms:
  客厅:
    吸顶灯: {did: "1001", prop: "on"}
    温湿度: {did: "1002", kind: th, props: {temperature: temperature, humidity: humidity}}
    烟感: {did: "1003", kind: smoke, props: {status: status, battery: battery}}
tts: {did: "9999"}
"""

PROPS = {
    "1001": {"on": False},
    "1002": {"temperature": "24.44", "humidity": "33.6"},
    "1003": {"status": 2, "battery": 87},
}


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "mijia.yaml"
    path.write_text(TABLE, encoding="utf-8")
    monkeypatch.setattr(config, "MIJIA_CONFIG", path)
    monkeypatch.setattr(mijia, "_table_cache", None)
    return path


@pytest.fixture
def cloud(table, monkeypatch):
    api = FakeApi(props=PROPS, devices=[{"did": "1001"}])
    install(monkeypatch, mijia, api)
    return api


def test_client_loads_once_and_reuses_the_device_list(cloud):
    first = mijia._client()
    second = mijia._client()
    assert first is second is cloud
    assert cloud.device_list_calls == 1, "设备列表只该拉一次，新增设备靠重启"


def test_client_reports_login_failure(table, monkeypatch):
    api = FakeApi()
    install(monkeypatch, mijia, api, login_error=RuntimeError("auth.json 过期"))
    with pytest.raises(mijia.MijiaError, match="没登录"):
        mijia._client()


def test_client_reports_missing_library(table, monkeypatch):
    monkeypatch.setattr(mijia, "mijiaAPI", None)
    monkeypatch.setattr(mijia, "_IMPORT_ERROR", ImportError("no module"))
    with pytest.raises(mijia.MijiaError, match="库不可用"):
        mijia._client()


def test_values_returns_every_device(cloud):
    got = mijia.values()
    assert set(got["values"]) == {"客厅/吸顶灯", "客厅/温湿度", "客厅/烟感"}
    assert got["values"]["客厅/温湿度"] == {"temperature": 24.4, "humidity": 34}
    assert got["values"]["客厅/烟感"] == {"status": 2, "fault": True, "battery": 87}
    assert got["values"]["客厅/吸顶灯"] == {"on": False}
    assert isinstance(got["ts"], int)


def test_values_only_reads_requested_ids(cloud):
    got = mijia.values(["客厅/吸顶灯"])
    assert set(got["values"]) == {"客厅/吸顶灯"}
    assert {did for did, _ in cloud.gets} == {"1001"}


def test_values_unknown_id_names_it(cloud):
    with pytest.raises(mijia.UnknownDevice, match="客厅/台灯"):
        mijia.values(["客厅/吸顶灯", "客厅/台灯"])


def test_one_broken_device_does_not_break_the_rest(cloud, monkeypatch):
    original = mijia._sensor_values

    def half_broken(sensor):
        if sensor["kind"] == "smoke":
            raise RuntimeError("BLE 超时")
        return original(sensor)

    monkeypatch.setattr(mijia, "_sensor_values", half_broken)
    got = mijia.values()
    assert got["values"]["客厅/烟感"]["error"] == "BLE 超时"
    assert got["values"]["客厅/吸顶灯"] == {"on": False}


def test_switch_cache_expires_after_its_ttl(cloud, clock):
    mijia.values(["客厅/吸顶灯"])
    assert len(cloud.gets) == 1

    clock.advance(mijia.SWITCH_TTL - 0.5)
    mijia.values(["客厅/吸顶灯"])
    assert len(cloud.gets) == 1, "TTL 内不该再读云"

    clock.advance(1.0)
    mijia.values(["客厅/吸顶灯"])
    assert len(cloud.gets) == 2, "过期后必须重新读"


def test_sensor_cache_lives_longer_than_switch(cloud, clock):
    mijia.values(["客厅/温湿度", "客厅/吸顶灯"])
    clock.advance(mijia.SWITCH_TTL + 1)
    mijia.values(["客厅/温湿度", "客厅/吸顶灯"])
    temperature_reads = [read for read in cloud.gets if read == ("1002", "temperature")]
    assert temperature_reads == [("1002", "temperature")], "温湿度 300s 内只用缓存"
    switch_reads = [read for read in cloud.gets if read == ("1001", "on")]
    assert len(switch_reads) == 2, "开关 3s 后必须重读"


def test_stale_value_is_returned_when_cloud_fails(cloud, clock):
    mijia.values(["客厅/温湿度"])
    clock.advance(mijia.SENSOR_TTL + 1)
    cloud.get_error = RuntimeError("云端 500")
    got = mijia.values(["客厅/温湿度"])["values"]["客厅/温湿度"]
    assert got["temperature"] == 24.4
    assert got["stale"] is True


def test_no_cache_and_cloud_failure_reports_error(cloud):
    cloud.get_error = RuntimeError("云端 500")
    got = mijia.values(["客厅/吸顶灯"])["values"]["客厅/吸顶灯"]
    assert "云端 500" in got["error"]


def test_write_reads_back_until_it_sticks(cloud):
    cloud.reads_before_apply = 1  # 第一次读回还是旧值
    assert mijia.set_power("客厅/吸顶灯", True) == {"id": "客厅/吸顶灯", "on": True}
    assert cloud.sets == [("1001", "on", True)]


def test_write_that_never_sticks_reports_reality(cloud):
    cloud.reads_before_apply = 99
    assert mijia.set_power("客厅/吸顶灯", True) == {"id": "客厅/吸顶灯", "on": False}
    assert len(cloud.gets) == 4, "最多读回 4 次就放弃，不无限重试"


def test_write_only_drops_its_own_cache(cloud):
    mijia.values(["客厅/吸顶灯", "客厅/温湿度"])
    assert "switch:客厅/吸顶灯" in mijia._disk
    mijia.set_power("客厅/吸顶灯", True)
    assert "switch:客厅/吸顶灯" not in mijia._disk, "写过的那台要作废"
    assert "sensor:客厅/温湿度" in mijia._disk, "别的设备照常命中缓存"


def test_unknown_switch_is_rejected_before_touching_cloud(cloud):
    with pytest.raises(mijia.UnknownDevice, match="客厅/台灯"):
        mijia.set_power("客厅/台灯", True)
    assert cloud.sets == []


def test_speak_uses_the_table_speaker(cloud):
    assert mijia.speak("该睡觉了") == {"text": "该睡觉了", "did": "9999"}
    assert cloud.actions == [("9999", mijia.SAY_ACTION, {"in": ["该睡觉了"]})]


def test_speak_can_target_another_device(cloud):
    assert mijia.speak("客厅播报", device="1001")["did"] == "1001"


def test_speak_without_table_speaker_is_not_configured(cloud, tmp_path, monkeypatch):
    no_tts = tmp_path / "mijia.yaml"
    no_tts.write_text('rooms:\n  客厅:\n    灯: {did: "1", prop: "on"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "MIJIA_CONFIG", no_tts)
    monkeypatch.setattr(mijia, "_table_cache", None)
    with pytest.raises(config.NotConfigured, match="tts"):
        mijia.speak("嗨")


def test_disk_cache_round_trips(cloud):
    mijia.values()
    assert mijia.CACHE_FILE.exists()
    payload = json.loads(mijia.CACHE_FILE.read_text(encoding="utf-8"))
    assert payload["version"] == mijia.CACHE_VERSION
    assert set(payload["entries"]) == {
        "switch:客厅/吸顶灯",
        "sensor:客厅/温湿度",
        "sensor:客厅/烟感",
    }

    mijia._disk.clear()
    mijia._dirty = False
    mijia._load_disk()
    assert set(mijia._disk) == set(payload["entries"])


def test_corrupt_disk_cache_is_ignored(cloud):
    mijia.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mijia.CACHE_FILE.write_text("{坏掉的 json", encoding="utf-8")
    mijia._load_disk()  # 不该抛
    assert mijia._disk == {}


def test_disk_cache_with_old_version_is_ignored(cloud):
    mijia.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mijia.CACHE_FILE.write_text(
        json.dumps({"version": mijia.CACHE_VERSION - 1, "entries": {"switch:x": {}}}),
        encoding="utf-8",
    )
    mijia._load_disk()
    assert mijia._disk == {}


def test_save_failure_keeps_the_dirty_flag(cloud, tmp_path, monkeypatch):
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "state.json" / "nested.json")
    (tmp_path / "state.json").write_text("我是一个文件，不是目录", encoding="utf-8")
    mijia.values()
    assert mijia._dirty is True, "没写成要留着标记，下回再试"


def test_table_change_is_picked_up_without_restart(cloud, table):
    assert "客厅/吸顶灯" in mijia.devices()["devices"][0]["id"]
    table.write_text(TABLE.replace("吸顶灯:", "台灯:"), encoding="utf-8")
    stamp = table.stat().st_mtime_ns + 1_000_000
    os.utime(table, ns=(stamp, stamp))
    ids = {item["id"] for item in mijia.devices()["devices"]}
    assert "客厅/台灯" in ids


def test_devices_describes_rooms_and_kinds(cloud):
    got = mijia.devices()
    assert got["rooms"] == [{"id": "客厅", "name": "客厅"}]
    kinds = {item["id"]: item["kind"] for item in got["devices"]}
    assert kinds == {"客厅/吸顶灯": "switch", "客厅/温湿度": "th", "客厅/烟感": "smoke"}
