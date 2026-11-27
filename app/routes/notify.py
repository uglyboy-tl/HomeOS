"""通知接口：一条消息推 Gotify。"""

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..lib import health
from ..lib.errors import ApiError
from ..services import notify

router = APIRouter(tags=["notify"], dependencies=health.deps("notify"))
log = logging.getLogger("homeos.notify")


class NotifyReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    title: str = ""
    level: Literal["info", "warn", "error"] = "info"


@router.post("/api/notify")
def api_notify(req: NotifyReq) -> dict:
    """推一条通知；返回 Gotify 的消息 id。"""
    text = req.text.strip()
    if not text:
        raise ApiError(400, "bad_input", "text 是空的")
    try:
        out = notify.push(text, req.title, req.level)
    except notify.NotifyError as e:
        log.warning("通知推送失败：%s", e)
        raise health.failing("notify", e) from e
    health.note("notify", True)
    log.info("notify level=%s chars=%d id=%s", req.level, len(text), out["id"])
    return out
