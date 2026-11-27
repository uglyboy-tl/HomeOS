"""应用装配：路由、异常处理、安全头、lifespan。没有业务逻辑。"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from . import config
from .lib import errors, headers, health, logs, shutdown, sockets
from .lib.security import require_auth
from .routes import agent, asr, info, metrics, mijia, mpd, notify, pages, ready, speech
from .services import agent as agent_service

log = logging.getLogger("homeos")

TAGS = [
    {"name": "info", "description": "描述与诊断：只报事实，不执行能力"},
    {"name": "mijia", "description": "设备值与开关"},
    {"name": "speech", "description": "让音箱念一句"},
    {"name": "mpd", "description": "播放控制与曲库"},
    {"name": "notify", "description": "推一条 Gotify 通知"},
    {"name": "metrics", "description": "NAS 本机指标"},
    {"name": "agent", "description": "助手的一次性问答与会话"},
    {"name": "health", "description": "存活检查"},
]

# 认证边界：API 一组挂 require_auth；页面自己种 cookie，健康检查保持开放
API_ROUTERS = (
    info.router,
    mijia.router,
    speech.router,
    mpd.router,
    notify.router,
    metrics.router,
    asr.router,
    agent.router,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    shutdown.stop_event.clear()
    await agent_service.runtime.startup()  # 只做静态校验与 pi 探测，毫秒级
    # 米家登录要 1 秒、Beszel/Gotify 要网络：探测放后台，别拦服务启动
    probes = asyncio.create_task(health.startup())
    try:
        yield
    finally:
        probes.cancel()  # 先让 SSE 生成器退出，再关 pi 进程
        with contextlib.suppress(asyncio.CancelledError):
            await probes
        shutdown.stop_event.set()
        await agent_service.runtime.aclose()


def _install_handlers(app: FastAPI) -> None:
    errors.install(app)  # 统一 {"error": {code, message}}
    sockets.install(app)
    agent.install_error_handlers(app)  # 未知模式 / 忙 / 崩溃等 agent 自己的错误码


def _install_routers(app: FastAPI) -> None:
    api = APIRouter(dependencies=[Depends(require_auth)])
    for router in API_ROUTERS:
        api.include_router(router)
    app.include_router(ready.router)
    app.include_router(api)
    # 兜底的静态路由必须在最后：它带 /{name} 这种宽匹配
    app.include_router(pages.router)


def create_app() -> FastAPI:
    logs.setup()
    app = FastAPI(
        title="homeos",
        docs_url="/_docs" if config.DEV else None,
        openapi_url="/_openapi.json" if config.DEV else None,
        redoc_url=None,
        lifespan=lifespan,
        openapi_tags=TAGS,
    )
    app.add_middleware(headers.SecurityHeaders)
    _install_handlers(app)
    _install_routers(app)
    if not config.TOKEN:
        log.warning(
            "未配置 server.token：%s",
            "接口放行（DEV）" if config.DEV else "接口需令牌（生产）",
        )
    return app


app = create_app()
