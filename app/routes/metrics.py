"""硬件指标接口：NAS 本机读数（数据来自 Beszel，见 services/metrics.py）。"""

import logging

from fastapi import APIRouter

from ..lib import health
from ..services import metrics

router = APIRouter(tags=["metrics"], dependencies=health.deps("metrics"))
log = logging.getLogger("homeos.metrics")


@router.get("/api/metrics")
def api_metrics() -> dict:
    try:
        out = metrics.snapshot()
    except metrics.BeszelError as e:
        log.warning("metrics 失败：%s", e)
        raise health.failing("metrics", e) from e
    health.note("metrics", True)
    return out
