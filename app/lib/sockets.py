"""WS 路由的公共约定：不可用回 `{"type": "error"}` 帧。

WS 握手之后才能发帧，所以 `ready()` 比 HTTP 依赖多一步 accept；`Rejected` 用来中止 handler。"""

import contextlib

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from . import health
from .errors import ApiError

# 客户端提前走了：uvicorn 的 ClientDisconnected 是 OSError，向已关闭的连接发送抛 RuntimeError
GONE = (WebSocketDisconnect, OSError, RuntimeError)


class Rejected(Exception):
    """依赖里已经把错误帧发完并关了连接，用它中止后续 handler。"""


async def _rejected_response(_request, _exc):  # noqa: ANN001 — FastAPI 回调签名
    return None  # WS 请求没有 HTTP 响应，什么都不用做


async def send(ws: WebSocket, payload: dict) -> None:
    """推一帧；对端已经走了就不算错（后台回调也会调到这里）。"""
    with contextlib.suppress(*GONE):
        await ws.send_json(payload)


async def close(ws: WebSocket) -> None:
    with contextlib.suppress(*GONE):
        await ws.close()


def ready(name: str):
    """WS 路由依赖：先 accept（否则连错误帧都发不出去），再查健康登记簿。"""

    async def check(ws: WebSocket) -> None:
        await ws.accept()
        try:
            health.check(name)
            health.guard(name)
        except ApiError as e:
            await send(ws, {"type": "error", "code": e.code, "message": str(e)})
            await close(ws)
            raise Rejected from e

    return check


def install(app) -> None:
    app.add_exception_handler(Rejected, _rejected_response)
