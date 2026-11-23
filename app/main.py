"""应用装配：只有路由表和 lifespan，没有业务逻辑。"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config, runtime
from .routes import health, mijia, oc, pages
from .services import mijia as mijia_service


def _warm_mijia() -> None:
    try:
        mijia_service.warm()
    except Exception:  # noqa: BLE001 — 预热失败不影响服务，请求时还会再试
        logging.getLogger("homeos").info("米家预热失败（未登录？），忽略")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime.stop_event.clear()
    if config.WARM_MIJIA:
        threading.Thread(target=_warm_mijia, daemon=True).start()
    try:
        yield
    finally:
        runtime.stop_event.set()  # 先让 SSE 生成器退出，再关服务


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = FastAPI(
        title="homeos",
        docs_url="/_docs" if config.DEV else None,
        openapi_url="/_openapi.json" if config.DEV else None,
        lifespan=lifespan,
    )
    # 顺序有意义：API 在前，兜底的静态路由在后
    app.include_router(health.router)
    app.include_router(mijia.router)
    app.include_router(oc.router)
    app.include_router(pages.router)
    return app


app = create_app()
