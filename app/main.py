"""应用装配：只有路由表和 lifespan，没有业务逻辑。"""

import asyncio
import contextlib
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from . import config, runtime
from .routes import agent, asr, health, mijia, oc, pages
from .security import require_auth
from .services import agent as agent_service
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
    # 常驻订阅 opencode 的事件流：本轮文本靠它攒，前端只按自己的节奏来取
    stop = asyncio.Event()
    watcher = asyncio.create_task(agent_service.run_subscriber(stop))
    try:
        yield
    finally:
        stop.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
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
    agent.install_error_handlers(app)
    if not config.TOKEN:
        logging.getLogger("homeos").warning(
            "未配置 HOMEOS_TOKEN：/oc 关闭，%s",
            "接口放行（DEV）" if config.DEV else "接口需令牌（生产）",
        )
    # 所有 /api/* 统一挂认证（新增路由自动受保护），只有 /api/ready 单独挂在外面
    api = APIRouter(dependencies=[Depends(require_auth)])
    for r in (health.api_router, mijia.router, asr.router, agent.router):
        api.include_router(r)
    app.include_router(health.router)
    app.include_router(api)
    app.include_router(oc.router)
    app.include_router(pages.router)
    return app


app = create_app()
