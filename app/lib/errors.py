"""HTTP 错误形状 `{"error": {"code", "message"}}`，与 agent 快照里的 error 同构。

成功返回资源本体（不包 ok）；状态码说类别，`code` 给程序判断，`message` 给人看。"""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config
from .validation import describe

log = logging.getLogger("homeos.errors")


class ApiError(Exception):
    """路由层与领域层共用的错误：带状态码与稳定的 code。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def envelope(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


def _validation_message(errors: list[dict]) -> str:
    """第一条说清楚，其余只给个数。"""
    if not errors:
        return "请求格式不对"
    first = describe(errors[0], skip=1)  # 位置里的第一段是 body/query
    rest = len(errors) - 1
    return f"{first}（另有 {rest} 处）" if rest else first


def install(app: FastAPI) -> None:
    async def api_error(_request, exc: ApiError):  # noqa: ANN001 — FastAPI 回调签名
        return envelope(exc.status, exc.code, str(exc))

    async def not_configured(_request, exc):  # noqa: ANN001 — FastAPI 回调签名
        return envelope(503, "not_configured", str(exc))

    async def invalid_request(_request, exc: RequestValidationError):  # noqa: ANN001
        return envelope(422, "bad_input", _validation_message(exc.errors()))

    async def http_error(_request, exc: StarletteHTTPException):  # noqa: ANN001
        code = {401: "unauthorized", 404: "not_found"}.get(
            exc.status_code, "bad_input" if exc.status_code < 500 else "internal"
        )
        return envelope(exc.status_code, code, str(exc.detail), exc.headers)

    async def unexpected(request, exc):  # noqa: ANN001 — 兜底：栈由 uvicorn 记，这里只留一条
        log.warning("未处理的异常：%s %s", request.method, request.url.path)
        return envelope(500, "internal", "服务内部错误")

    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(config.NotConfigured, not_configured)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(Exception, unexpected)
