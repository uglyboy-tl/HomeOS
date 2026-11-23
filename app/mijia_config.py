"""米家设备表：数据在 config/mijia.yaml，这里只读文件、校验、拼 id。

结构就是两级：房间名 → 设备名。有 `kind` 的是传感器，没有的是开关。
校验完的东西才交给 services/mijia.py，那边不再做防御。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config

# 每种传感器必须给齐的属性
KIND_PROPS = {"th": ("temperature", "humidity"), "smoke": ("status", "battery")}
SEP = "/"  # 房间/设备 拼 id 用，所以名字里不能有它

_cache: tuple[float, dict] | None = None


class ConfigError(ValueError):
    """设备表写错了。消息里带确切位置，照着改 YAML 就行。"""


def _str(obj: dict, key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} 的 {key} 必须是非空字符串")
    return value.strip()


def _name(raw, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{where} 必须是非空名字")
    name = raw.strip()
    if SEP in name:
        raise ConfigError(f"{where} 不能含 {SEP!r}")
    return name


def _device(name: str, raw, where: str) -> dict:
    """一台设备：有 kind 是传感器，没有是开关。"""
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必须是对象")
    did = _str(raw, "did", where)
    if "kind" not in raw:
        return {
            "id": name,
            "label": name,
            "did": did,
            "prop": _str(raw, "prop", where),
        }
    kind = _str(raw, "kind", where)
    if kind not in KIND_PROPS:
        raise ConfigError(f"{where} 的 kind 只能是 {'/'.join(KIND_PROPS)}")
    props = raw.get("props")
    if not isinstance(props, dict):
        raise ConfigError(f"{where} 缺 props")
    return {
        "id": name,
        "label": name,
        "kind": kind,
        "did": did,
        "props": {key: _str(props, key, f"{where}.props") for key in KIND_PROPS[kind]},
    }


def _parse(doc) -> dict:
    rooms_raw = doc.get("rooms") if isinstance(doc, dict) else None
    if not isinstance(rooms_raw, dict) or not rooms_raw:
        raise ConfigError("顶层要有 rooms，且至少一个房间")

    rooms: list[dict] = []
    switches: dict[str, dict] = {}
    sensors: dict[str, dict] = {}
    for room_label, devices in rooms_raw.items():
        room_name = _name(room_label, "房间名")
        if not isinstance(devices, dict) or not devices:
            raise ConfigError(f"房间 {room_name} 下面要至少一台设备")
        room = {"id": room_name, "label": room_name, "switches": [], "sensors": []}
        for device_label, raw in devices.items():
            device_name = _name(device_label, f"{room_name} 下的设备名")
            where = f"{room_name}.{device_name}"
            item = _device(device_name, raw, where)
            full_id = f"{room_name}{SEP}{device_name}"
            item["id"] = full_id
            bucket = switches if "kind" not in item else sensors
            bucket[full_id] = item
            room["switches" if "kind" not in item else "sensors"].append(item)
        rooms.append(room)
    return {"rooms": rooms, "switches": switches, "sensors": sensors}


def load() -> dict:
    """读设备表。按 mtime 缓存，改完下一次请求就是新的。

    返回 {"rooms": [{id,label,switches:[...],sensors:[...]}],
          "switches": {id: 开关}, "sensors": {id: 传感器}}。
    """
    global _cache
    path = Path(config.MIJIA_CONFIG)
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        raise ConfigError(f"读不到设备表 {path}：{e}") from e
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"设备表 {path} 解析失败：{e}") from e
    data = _parse(doc)
    _cache = (mtime, data)
    return data
