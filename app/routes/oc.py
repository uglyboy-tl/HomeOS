"""/oc/* → opencode 反代：注入上游 Basic 认证，并要求 `X-HomeOS-Token`（未配置即 403）。

上游口令由 entrypoint 每次启动随机生成并 export，没人需要手填，也不能从外部覆盖。
"""

import httpx
from fastapi import APIRouter, Depends, Request

from ..lib.proxy import forward
from ..lib.security import require_token
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
