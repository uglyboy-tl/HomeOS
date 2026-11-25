"""/oc/* → opencode 反代：注入上游 Basic 认证，并要求 `X-HomeOS-Token`（未配置即 403）。

上游口令（`OPENCODE_SERVER_PASSWORD`）由 entrypoint 自动生成并 export，没人需要手填。
"""

import httpx
from fastapi import APIRouter, Depends, Request

from .._proxy import forward
from ..security import require_token
from ..services import opencode

router = APIRouter()


@router.api_route(
    "/oc/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    dependencies=[Depends(require_token)],
)
async def oc_proxy(request: Request, path: str):
    url = opencode.url(f"/{path.lstrip('/')}")
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return await forward(
        request,
        url,
        label="opencode",
        timeout=httpx.Timeout(None, connect=10.0),
        headers=opencode.headers(),
    )
