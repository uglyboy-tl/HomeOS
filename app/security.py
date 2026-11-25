"""认证：页面靠 cookie 免输入，其他设备/程序带 `X-HomeOS-Token`。

页面响应是未认证的，所以 cookie 里放令牌的派生值而不是原文——否则任何 GET 一次页面的人
都能拿它去调 `/oc`（等于 shell）。挡的是误用与扫描，主边界仍在网络层。
"""

import hashlib
import hmac
import logging

from fastapi import Header, HTTPException
from starlette.requests import HTTPConnection

from . import config

log = logging.getLogger("homeos.security")

COOKIE = "homeos"
_SALT = b"homeos-cookie-v1"


def auth_cookie(request: HTTPConnection) -> str:
    flags = "Path=/; HttpOnly; SameSite=Strict"
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    return f"{COOKIE}={cookie_value()}; {flags}{'; Secure' if https else ''}"


def cookie_value() -> str:
    return hmac.new(config.TOKEN.encode(), _SALT, hashlib.sha256).hexdigest()


def _same(given: str, want: str) -> bool:
    # 请求头按 latin-1 解码，非 ASCII 会让 compare_digest 抛 TypeError
    return hmac.compare_digest(given.encode(), want.encode())


def require_auth(request: HTTPConnection, x_homeos_token: str = Header(default="")) -> None:
    """cookie 或请求头任一有效即可。令牌未配时只有 DEV 放行，生产一律 401。"""
    if not config.TOKEN:
        if config.DEV:
            return
        raise HTTPException(401, "服务端未配置 HOMEOS_TOKEN，接口已关闭")
    cookie = request.cookies.get(COOKIE, "")
    if _same(x_homeos_token, config.TOKEN) or _same(cookie, cookie_value()):
        return
    log.warning(
        "拒绝：未认证，来源 %s %s",
        request.client.host if request.client else "?",
        request.url.path,
    )
    raise HTTPException(
        401,
        "需要认证：带 X-HomeOS-Token 或先打开一次页面",
        {"WWW-Authenticate": "HomeOS-Token"},
    )


def require_token(request: HTTPConnection, x_homeos_token: str = Header(default="")) -> None:
    """`/oc` 专用：只认令牌原文（不接受页面 cookie），未配置即关闭。"""
    if not config.TOKEN:
        raise HTTPException(403, "未配置 HOMEOS_TOKEN，/oc 已关闭")
    if not _same(x_homeos_token, config.TOKEN):
        log.warning("拒绝 /oc：令牌不对，来源 %s", request.client.host if request.client else "?")
        raise HTTPException(
            401, "X-HomeOS-Token 不正确", headers={"WWW-Authenticate": "HomeOS-Token"}
        )
