"""米家设备接口：值、开关。目录在 /api/info/mijia，播报在 /api/speech。

全部 sync def：mijiaAPI 一次读回约 1 秒，async def 会堵住事件循环。"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..lib import health
from ..lib.errors import ApiError
from ..services import mijia

CLOUD = health.deps("mijia.cloud")
router = APIRouter(tags=["mijia"], dependencies=health.deps("mijia.table"))
log = logging.getLogger("homeos.mijia")


class PowerReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    on: bool


def _call(fn, *args):
    """领域错误 → HTTP；顺手把这次调用的结果回写进可用性缓存。"""
    try:
        out = fn(*args)
    except mijia.TableError as e:  # 配置写坏了，跟云端无关
        raise ApiError(503, "not_configured", str(e)) from e
    except mijia.UnknownDevice as e:
        log.warning("请求无效：%s", e)
        raise ApiError(400, "unknown_device", str(e)) from e
    except mijia.MijiaError as e:
        raise health.failing("mijia.cloud", e) from e
    except Exception as e:  # noqa: BLE001 — 云接口的失败种类不可枚举
        log.exception("米家调用失败")
        health.note("mijia.cloud", False, "米家内部错误")
        raise ApiError(500, "internal", "米家内部错误") from e
    health.note("mijia.cloud", True)
    return out


@router.get("/api/mijia/values", dependencies=CLOUD)
def api_values(ids: str | None = None) -> dict:
    """按设备 id 给值；`ids` 逗号分隔（中文与斜杠要 URL 编码），不传就全读。"""
    wanted = [one.strip() for one in ids.split(",") if one.strip()] if ids else None
    return _call(mijia.values, wanted)


@router.put("/api/mijia/power", dependencies=CLOUD)
def api_power(req: PowerReq) -> dict:
    """开/关一台设备；返回云上读回的真实值（设备 id 里有斜杠，所以放 body 不放路径）。"""
    out = _call(mijia.set_power, req.id, req.on)
    log.info("power %s → %s", req.id, out["on"])
    return out
