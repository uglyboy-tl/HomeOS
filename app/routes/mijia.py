"""米家接口。全部 sync def：mijiaAPI 一次读回约 1 秒，async def 会堵住事件循环。"""

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
    did: str = ""  # 空 = 用 mijia.yaml 顶层的 tts.did


def _call(fn, *args) -> dict:
    """统一的错误映射：未知开关 400，米家不可用 503，其它 500（细节只进日志，不回给客户端）。"""
    try:
        return {"ok": True, **fn(*args)}
    except (mijia.UnknownSwitch, mijia.UnknownRoom) as e:
        log.warning("请求无效：%s", e)
        raise HTTPException(400, str(e)) from e
    except mijia.MijiaError as e:
        log.warning("米家不可用：%s", e)
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
    out = _call(mijia.set_switch, req.id, req.on)
    log.info("switch %s → %s", req.id, out["on"])
    return out


@router.post("/api/mijia/say")
def api_say(req: SayReq):
    """让音箱念一句话（文本在小米云端合成）。did 指别台音箱时可不配 tts。"""
    if not req.text.strip():
        raise HTTPException(400, "播报文本是空的")
    if req.did and not req.did.isdigit():
        raise HTTPException(400, "did 是纯数字串")
    out = _call(mijia.say, req.text, req.did)
    log.info("say %d 字 did=%s", len(out["text"]), out["did"])
    return out


@router.get("/api/mijia/devices")
def api_devices():
    """设备清单：房间顺序 + 每台设备的 id/名字/房间/类型。纯数据，任何前端都用得上。"""
    return _call(mijia.devices)
