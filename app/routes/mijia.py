"""米家：设备表、状态、开关（/api/mijia/*）。

全部是 sync def（走线程池）：mijiaAPI 一次读回约 1 秒，写成 async def 会堵住事件循环。
`/api/mijia/devices` 只读设备表、不碰云，所以是毫秒级。
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import mijia

router = APIRouter()
log = logging.getLogger("homeos.mijia")


class SwitchReq(BaseModel):
    id: str
    on: bool


class SayReq(BaseModel):
    text: str


def _call(fn, *args) -> dict:
    """统一的错误映射：未知开关 400，米家不可用 503，其它 500（细节只进日志，不回给客户端）。"""
    try:
        return {"ok": True, **fn(*args)}
    except (mijia.UnknownSwitch, mijia.UnknownRoom) as e:
        raise HTTPException(400, str(e)) from e
    except mijia.MijiaError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:  # noqa: BLE001 — 云接口的失败种类不可枚举
        log.exception("米家调用失败")
        raise HTTPException(500, "米家内部错误") from e


@router.get("/api/mijia/state")
def api_state(room: str | None = None):
    """全部设备，或只要一间房（`?room=客厅`）。前端只显示一间，就只问一间。"""
    return _call(mijia.read_state, room)


@router.post("/api/mijia/switch")
def api_switch(req: SwitchReq):
    return _call(mijia.set_switch, req.id, req.on)


@router.post("/api/mijia/say")
def api_say(req: SayReq):
    """让音箱念一句话（文本在小米云端合成）。"""
    if not req.text.strip():
        raise HTTPException(400, "播报文本是空的")
    return _call(mijia.say, req.text)


@router.get("/api/mijia/devices")
def api_devices():
    """设备清单：房间顺序 + 每台设备的 id/名字/房间/类型。纯数据，任何前端都用得上。"""
    return _call(mijia.devices)
