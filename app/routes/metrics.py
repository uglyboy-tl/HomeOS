"""硬件指标接口：NAS 本机读数（数据来自 Beszel，见 services/metrics.py）。"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import config
from ..services import metrics

router = APIRouter(
    dependencies=[Depends(config.ready("beszel.url", "beszel.user", "beszel.password"))]
)
log = logging.getLogger("homeos.metrics")


@router.get("/api/metrics")
def api_metrics():
    try:
        return {"ok": True, "metrics": metrics.snapshot()}
    except metrics.BeszelError as e:
        log.warning("metrics 失败：%s", e)
        raise HTTPException(503, str(e)) from e
