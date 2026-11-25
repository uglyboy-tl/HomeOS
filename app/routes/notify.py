"""通知接口：一条消息 + 走哪些通道（推 Gotify / 音箱播报）。"""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..services import notify

router = APIRouter()
log = logging.getLogger("homeos.notify")


class NotifyReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    title: str = ""
    level: Literal["info", "warn", "error"] = "info"
    channels: list[Literal["gotify", "speak"]] = Field(default_factory=lambda: ["gotify"])


@router.post("/api/notify")
def api_notify(req: NotifyReq):
    """默认只推 Gotify；要出声显式加 "speak"。部分通道失败仍 200，全挂才 503。"""
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text 是空的")
    if not req.channels:
        raise HTTPException(400, "channels 不能为空")
    results = notify.send(text, req.title, req.level, tuple(dict.fromkeys(req.channels)))
    ok = any(r["ok"] for r in results.values())
    log.info(
        "notify level=%s channels=%s chars=%d ok=%s",
        req.level,
        ",".join(req.channels),
        len(text),
        ok,
    )
    if not ok:
        return JSONResponse({"ok": False, "results": results}, status_code=503)
    return {"ok": True, "results": results}
