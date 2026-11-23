"""/oc/* → opencode 反代：把浏览器请求转给容器内的 opencode，并注入它的 Basic 认证。

**故意不做访问认证**：这套东西只在内网跑，加口令只是多一层要维护的东西，挡不住真正
有权限的人（页面里拿得到），所以按"内网可信"处理。代价写在这里，将来要暴露到公网，
必须先在反代层补认证/限源，而不是指望这个路由。

上游口令（`OPENCODE_SERVER_PASSWORD`）由 entrypoint 自动生成并 export，没人需要手填。
"""

import base64

import httpx
from fastapi import APIRouter, Request

from .. import config
from .._proxy import forward

router = APIRouter()


@router.api_route("/oc/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def oc_proxy(request: Request, path: str):
    url = f"http://{config.OC_HOST}:{config.OC_PORT}/{path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    cred = base64.b64encode(f"opencode:{config.OC_SERVER_PASSWORD}".encode()).decode()
    return await forward(
        request,
        url,
        label="opencode",
        timeout=httpx.Timeout(None, connect=10.0),
        headers={"Authorization": f"Basic {cred}"},
    )
