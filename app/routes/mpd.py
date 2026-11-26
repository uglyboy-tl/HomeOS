"""MPD 接口：读状态、一条控制入口（action 分派）、曲库同步。"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import config
from ..services import mpd

router = APIRouter(dependencies=[Depends(config.ready("mpd.host"))])
log = logging.getLogger("homeos.mpd")


class ControlReq(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 拼错字段要报错，别静默什么都不做

    action: Literal["play", "pause", "stop", "volume", "next", "prev"]
    device: str | None = None
    playlist: str | None = None
    volume: int | None = None
    random: bool | None = None
    repeat: bool | None = None
    crossfade: int | None = None
    on: bool | None = None


ALLOWED = {
    "play": {"playlist", "volume", "random", "repeat", "crossfade"},
    "pause": {"on"},
    "stop": set(),
    "volume": {"volume"},
    "next": set(),
    "prev": set(),
}


class SyncReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rebuild: bool = False


def _device(name: str | None) -> str:
    """现在只有 mpd 这一路输出；给别的值直接拒绝，别让拼错变成"放到别处去了"。"""
    if name is None:
        return mpd.DEVICES[0]
    if name not in mpd.DEVICES:
        raise HTTPException(400, f"暂不支持该设备：{name}，可选 {'/'.join(mpd.DEVICES)}")
    return name


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except mpd.MpdValueError as e:
        raise HTTPException(400, str(e)) from e
    except mpd.MpdBusyError as e:
        raise HTTPException(409, str(e)) from e
    except mpd.MpdNotFound as e:
        log.warning("MPD 找不到：%s", e)
        raise HTTPException(404, str(e)) from e
    except mpd.MpdError as e:
        log.warning("MPD 不可用：%s", e)
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001 — 上游的失败种类不可枚举
        log.exception("MPD 调用失败")
        raise HTTPException(500, "MPD 内部错误") from e


@router.get("/api/mpd/state")
def api_state(device: str | None = None):
    return {"ok": True, "device": _device(device), "state": _call(mpd.state)}


@router.post("/api/mpd/control")
def api_control(req: ControlReq):
    """一条入口管所有播放动作；返回动作之后的状态，调用方一次就能确认。"""
    device = _device(req.device)
    if req.action == "volume" and req.volume is None:
        raise HTTPException(400, "action=volume 需要 volume")
    if req.volume is not None and not 0 <= req.volume <= 100:
        raise HTTPException(400, "volume 要在 0-100 之间")
    if req.crossfade is not None and not 0 <= req.crossfade <= 10:
        raise HTTPException(400, "crossfade 要在 0-10 之间")
    fields = req.model_dump(exclude_none=True, exclude={"action", "device"})
    if any(isinstance(v, str) and not v.strip() for v in fields.values()):
        raise HTTPException(400, "空字符串参数")
    stray = set(fields) - ALLOWED[req.action]
    if stray:
        raise HTTPException(400, f"action={req.action} 不接受 {', '.join(sorted(stray))}")
    state = _call(mpd.control, req.action, **fields)
    log.info("control %s %s", req.action, " ".join(f"{k}={v}" for k, v in fields.items()))
    return {"ok": True, "device": device, "state": state}


@router.post("/api/mpd/sync")
def api_sync(req: SyncReq):
    """update 曲库索引；rebuild 再按一级目录重建同名播放列表（慢，最多 60s）。"""
    out = _call(mpd.sync, req.rebuild)
    log.info(
        "sync rebuild=%s dirs=%s tracks=%s skipped=%s",
        req.rebuild,
        out["dirs"],
        out["tracks"],
        len(out["skipped"]),
    )
    return {"ok": True, **out}
