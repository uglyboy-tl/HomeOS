"""应用装配：只有路由表和 lifespan，没有业务逻辑。"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse

from . import config
from .lib import shutdown, sockets
from .lib.security import require_auth
from .routes import asr, health, metrics, mijia, mpd, notify, pages
from .services import mijia as mijia_service

log = logging.getLogger("homeos")


def _warm_mijia() -> None:
    try:
        mijia_service.warm()
    except Exception:  # noqa: BLE001 — 预热失败不影响服务，请求时还会再试
        log.info("米家预热失败（未登录？），忽略")


async def _not_configured(_request, exc):  # noqa: ANN001 — FastAPI 回调签名
    return JSONResponse({"detail": str(exc)}, status_code=503)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    shutdown.stop_event.clear()
    if config.WARM_MIJIA:
        threading.Thread(target=_warm_mijia, daemon=True).start()
    try:
        yield
    finally:
        shutdown.stop_event.set()  # 先让 SSE 生成器退出，再关服务


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx 的 INFO 会把完整 URL（含 query）写进 docker 日志，凭据不能出现在那里
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = FastAPI(
        title="homeos",
        docs_url="/_docs" if config.DEV else None,
        openapi_url="/_openapi.json" if config.DEV else None,
        lifespan=lifespan,
    )
    app.add_exception_handler(config.NotConfigured, _not_configured)
    sockets.install(app)

    if not config.TOKEN:
        log.warning(
            "未配置 server.token：%s",
            "接口放行（DEV）" if config.DEV else "接口需令牌（生产）",
        )
    # 顺序有意义：API 在前，兜底的静态路由在后
    api = APIRouter(dependencies=[Depends(require_auth)])
    for r in (
        pages.api_router,
        mijia.router,
        mpd.router,
        notify.router,
        metrics.router,
        asr.router,
    ):
        api.include_router(r)
    app.include_router(health.router)
    app.include_router(api)
    app.include_router(pages.router)
    return app


app = create_app()
