"""所有响应都带的安全头；纯 ASGI 实现——`BaseHTTPMiddleware` 会缓冲响应体，SSE 会坏。"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_HEADERS = (
    (b"x-content-type-options", b"nosniff"),  # 页面是 agent 写的 HTML，不能被嗅探改类型
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *SAFE_HEADERS]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
