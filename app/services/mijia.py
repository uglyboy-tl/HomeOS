"""米家能力层：读状态、写开关。

设备表在 config/mijia.yaml（见 config/README.md），这里只有缓存与设备 I/O。
调用方都在线程池里（routes/mijia.py 的 sync def），别把这里改成 async。

锁的边界：`_cache_lock` 只罩缓存字典与内存映射，不罩网络 I/O；同一台设备的云端
调用用它的 `_device_lock` 串行（mijiaDevice 共用一条 requests 会话，不能交错）。
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial

from .. import config, mijia_config

try:
    from mijiaAPI import mijiaAPI, mijiaDevice
except Exception as e:  # noqa: BLE001 — 没装、装坏了：只有米家页不可用
    mijiaAPI = mijiaDevice = None
    _IMPORT_ERROR: Exception | None = e
else:
    _IMPORT_ERROR = None

# 缓存时长（秒）。温湿度、烟感变化慢 + 烟感是 BLE 设备，少读省电
SWITCH_TTL = 3
SENSOR_TTL = 300
SMOKE_TTL = 900
# 烟感 status：规格里 1=Busy 2=Fault，但实测 mcn02 常态就是 1（待机），按 2 才算故障
SMOKE_FAULT = 2

CACHE_FILE = config.DATA_DIR / "mijia" / "state.json"
CACHE_VERSION = 2  # 缓存值的形状变了就加一，旧文件自动作废

_cache_lock = threading.Lock()
_client_lock = threading.Lock()
_device_locks: dict[str, threading.RLock] = {}
_disk: dict = {}
_dirty = False  # 缓存有新增才落盘，别每个请求都重写文件
_api = None
_devices: dict = {}


class MijiaError(RuntimeError):
    """米家这一层不可用：没登录、装不上 mijiaAPI、设备表写错、云接口一直失败。"""


class UnknownSwitch(LookupError):
    """请求里给的开关 id 不在设备表里。"""


# ---------------------------------------------------------------- 缓存


def _load_disk() -> None:
    # 没缓存、文件坏了、或版本对不上：当空缓存跑
    with contextlib.suppress(OSError, ValueError, TypeError):
        doc = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("version") == CACHE_VERSION:
            entries = doc.get("entries")
            if isinstance(entries, dict):
                with _cache_lock:   # 读路径拿的是这把锁，写也得是它
                    _disk.update(entries)


def _save_disk() -> None:
    global _dirty
    if not _dirty:
        return
    with _cache_lock:   # 拷贝快照：另一个线程可能正在往 _disk 插新键
        if not _dirty:
            return
        entries = dict(_disk)
        _dirty = False
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        payload = {"version": CACHE_VERSION, "entries": entries}
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_FILE)  # 原子替换，断电不会写出半截文件
    except OSError:
        with _cache_lock:
            _dirty = True   # 没写成，下回再试


def _cached(key: str, ttl: float, fetch: Callable[[], dict]) -> dict:
    """带落盘缓存的读：新鲜就用缓存，过期才调云接口；调失败退回旧值并标记 stale。"""
    with _cache_lock:
        entry = _disk.get(key)
        ok = isinstance(entry, dict) and isinstance(entry.get("v"), dict)
        if not (ok and isinstance(entry.get("ts"), (int, float))):
            entry = None
        fresh = entry is not None and time.time() - entry["ts"] < ttl
    if fresh:
        return entry["v"]
    try:
        value = fetch()
    except Exception:
        if entry:
            return {**entry["v"], "stale": True}
        raise
    global _dirty
    with _cache_lock:
        _disk[key] = {"ts": time.time(), "v": value}
        _dirty = True
    return value


# ---------------------------------------------------------------- 设备


def _table() -> dict:
    try:
        return mijia_config.load()
    except mijia_config.ConfigError as e:
        raise MijiaError(f"设备表有问题：{e}") from e


def _client():
    """懒加载 mijiaAPI：没登录/没装也只影响米家页，不影响别的页面。"""
    global _api
    if _api is not None:
        return _api
    with _client_lock:
        if _api is None:
            if mijiaAPI is None:
                raise MijiaError(f"米家库不可用：{_IMPORT_ERROR}") from _IMPORT_ERROR
            try:
                api = mijiaAPI()
                # 每次构造都会拉一遍设备列表（~0.8s），缓存住；新增设备要重启
                devices = api.get_devices_list()
                api.get_devices_list = lambda *a, **kw: devices
            except Exception as e:  # noqa: BLE001 — 多半是没登录（auth.json 不在）
                raise MijiaError(f"米家没登录：{e}") from e
            _load_disk()
            _api = api
        return _api


def warm() -> None:
    """预热：把登录态与设备列表准备好（约 0.8s）。失败就算了，第一个请求会再试。"""
    _client()


def _device_lock(did: str) -> threading.RLock:
    with _cache_lock:
        return _device_locks.setdefault(did, threading.RLock())


def _dev(did: str):
    with _cache_lock:
        dev = _devices.get(did)
    if dev is not None:
        return dev
    with _device_lock(did):
        with _cache_lock:
            dev = _devices.get(did)
        if dev is None:
            dev = mijiaDevice(_client(), did=did, sleep_time=0)
            with _cache_lock:
                _devices[did] = dev
        return dev


def _read(did: str, prop: str):
    with _device_lock(did):
        return _dev(did).get(prop)


# ---------------------------------------------------------------- 数据


def _sensor_values(sensor: dict) -> dict:
    """传感器的原始数值。显示成「24.4° 34%」是前端的事，这里只给数。"""
    did, props = sensor["did"], sensor["props"]
    with _device_lock(did):
        dev = _dev(did)
        if sensor["kind"] == "th":
            return {
                "temperature": round(float(dev.get(props["temperature"])), 1),
                "humidity": round(float(dev.get(props["humidity"]))),
            }
        status = dev.get(props["status"])
        return {
            "status": status,
            "fault": status == SMOKE_FAULT,
            "battery": dev.get(props["battery"]),
        }


def _cached_sensor(sensor: dict) -> dict:
    ttl = SENSOR_TTL if sensor["kind"] == "th" else SMOKE_TTL
    return _cached(f"sensor:{sensor['id']}", ttl, partial(_sensor_values, sensor))


def _cached_switch(sw: dict) -> dict:
    return _cached(f"switch:{sw['id']}", SWITCH_TTL, partial(_switch_values, sw))


def _switch_values(sw: dict) -> dict:
    return {"on": bool(_read(sw["did"], sw["prop"]))}


def devices() -> dict:
    """设备清单（纯数据，不碰云）：房间顺序 + 每台设备的 id/名字/房间/类型。

    任何前端（网页、ESP32、别的脚本）都用这一份，怎么排版由它们自己决定。
    """
    table = _table()
    rooms = [{"id": room["id"], "name": room["label"]} for room in table["rooms"]]
    items = []
    for room in table["rooms"]:
        for sw in room["switches"]:
            items.append(
                {"id": sw["id"], "name": sw["label"], "room": room["id"], "kind": "switch"}
            )
        for sensor in room["sensors"]:
            items.append(
                {
                    "id": sensor["id"],
                    "name": sensor["label"],
                    "room": room["id"],
                    "kind": sensor["kind"],
                }
            )
    return {"rooms": rooms, "devices": items}


class UnknownRoom(LookupError):
    """请求里的房间不在设备表里。"""


def read_state(room: str | None = None) -> dict:
    """读状态：按设备 id 给原始值，不含任何显示格式。

    `room` 给定时只读那一间。**同一间房里的设备并行读**：一次云调用约 150ms，
    串行等五台就是 750ms，并行只要一个往返。
    每台设备自己一条缓存键（开关 3s、温湿度 300s、烟感 900s），一台失败只标自己；
    同设备的并发用它的 `_device_lock` 串行（mijiaDevice 共用一条会话，不能交错）。
    """
    _client()  # 没登录就在这一行失败，别等到每个设备各失败一次
    rooms = _table()["rooms"]
    if room is not None:
        rooms = [r for r in rooms if r["id"] == room]
        if not rooms:
            raise UnknownRoom(f"未知房间：{room}")

    jobs = []
    for one in rooms:
        jobs += [(sw, _cached_switch) for sw in one["switches"]]
        jobs += [(sensor, _cached_sensor) for sensor in one["sensors"]]

    values = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
            futures = {pool.submit(_read_one, fn, item): item for item, fn in jobs}
            for future in as_completed(futures):
                values[futures[future]["id"]] = future.result()
    _save_disk()
    return {"ts": int(time.time()), "values": values}


def _read_one(fn: Callable[[dict], dict], item: dict) -> dict:
    """读一台设备，失败只影响它自己。"""
    try:
        return fn(item)
    except Exception as e:  # noqa: BLE001 — 云接口的失败种类不可枚举
        return {"error": str(e)[:80]}


def set_switch(sid: str, want: bool) -> dict:
    """开/关一个设备，并且**读回确认**——只看写接口的 ack 不算成功。"""
    switch = _table()["switches"].get(sid)
    if switch is None:
        raise UnknownSwitch(f"未知开关：{sid}")
    _client()
    did, prop = switch["did"], switch["prop"]
    with _device_lock(did):
        dev = _dev(did)
        dev.set(prop, want)
        # 云接口读状态有约 1s 延迟，第一次读回常常还是旧值，所以间隔重读几次
        actual = want
        for _ in range(4):
            time.sleep(0.4)
            actual = bool(dev.get(prop))
            if actual == want:
                break
    with _cache_lock:
        _disk.pop(f"switch:{sid}", None)  # 只作废这一台的缓存，别的设备照常命中
    return {"id": sid, "on": actual}
