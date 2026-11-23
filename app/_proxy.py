"""把请求转给本机另一个服务，并把响应逐块回传（SSE 要的就是不缓冲）。"""

import logging

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .runtime import stop_event

log = logging.getLogger("homeos.proxy")


async def forward(
    request: Request,
    url: str,
    *,
    label: str,
    timeout: httpx.Timeout,
    headers: dict | None = None,
):
    body = await request.body()
    head = {"Accept-Encoding": "identity", **(headers or {})}  # 逐块转发，上游别压缩
    if body:
        head["Content-Type"] = request.headers.get("content-type", "application/json")

    client = httpx.AsyncClient(timeout=timeout)
    req = client.build_request(request.method, url, content=body or None, headers=head)
    try:
        resp = await client.send(req, stream=True)
    except httpx.TimeoutException:
        await client.aclose()
        return JSONResponse({"ok": False, "error": f"{label}太慢了（超时）"}, 504)
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"ok": False, "error": f"{label}没启动"}, 502)
    except BaseException:
        # 其他任何异常（如非法 URL 的 ValueError）也不能把连接漏在那
        await client.aclose()
        raise

    async def cleanup() -> None:
        # 客户端在响应头之前就断开时，StreamingResponse 根本不会被迭代，
        # 所以关连接不能只写在 gen() 的 finally 里；background 总会跑。
        await resp.aclose()
        await client.aclose()

    async def gen():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
                if stop_event.is_set() or await request.is_disconnected():
                    break
        except (httpx.RemoteProtocolError, httpx.ReadError) as e:
            # opencode 有时不把分块发完就关连接，浏览器会自己重连，这里安静结束。
            log.info("%s 流中断: %s", label, e)

    return StreamingResponse(
        gen(),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        background=BackgroundTask(cleanup),
        headers={
            "Cache-Control": "no-store",
            **(
                {"content-encoding": resp.headers["content-encoding"]}
                if "content-encoding" in resp.headers
                else {}
            ),
        },
    )
