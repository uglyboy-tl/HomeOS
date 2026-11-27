"""播报接口：把一段文本念出来，用哪台设备由能力层决定。"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..lib import health
from ..lib.errors import ApiError
from ..services import mijia, speech

router = APIRouter(tags=["speech"], dependencies=health.deps("mijia.cloud", "mijia.table"))
log = logging.getLogger("homeos.speech")


class SayReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    device: str = ""  # 空 = 用 mijia.yaml 顶层的 tts.did


@router.post("/api/speech")
def api_say(req: SayReq) -> dict:
    """念一句；返回真正念出去的文本与被用的设备。"""
    if not req.text.strip():
        raise ApiError(400, "bad_input", "文本是空的")
    try:
        out = speech.say(req.text, req.device)
    except mijia.TableError as e:  # 它也是 ValueError，必须排在前面
        raise ApiError(503, "not_configured", str(e)) from e
    except mijia.MijiaError as e:
        log.warning("播报失败：%s", e)
        raise health.failing("mijia.cloud", e) from e
    except ValueError as e:
        raise ApiError(400, "bad_input", str(e)) from e
    health.note("mijia.cloud", True)
    log.info("speech %d 字 did=%s", len(out["text"]), out["did"])
    return out
