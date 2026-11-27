"""认证：页面靠 cookie 免输入，其他设备/程序带 `X-HomeOS-Token` 或 `Authorization: Bearer`。

cookie 里是令牌的 HMAC 派生值——页面响应未认证就发，放原文等于公开接口令牌。"""

import hashlib
import hmac
import logging

from fastapi import Header, HTTPException
from starlette.requests import HTTPConnection

from .. import config

log = logging.getLogger("homeos.security")

COOKIE = "homeos"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 天：设备冷启动后不用重新打开一次页面
_SALT = b"homeos-cookie-v1"


def auth_cookie(request: HTTPConnection) -> str:
    """页面响应里种的 cookie。Path=/、无 Domain，与 `__Host-` 的前提一致（除了前缀）。"""
    flags = f"Path=/; HttpOnly; SameSite=Strict; Max-Age={COOKIE_MAX_AGE}"
    # 容器里靠 uvicorn --proxy-headers 把 scheme 改对；老镜像没这个参数时看代理头兜底
    secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    )
    return f"{COOKIE}={cookie_value()}; {flags}{'; Secure' if secure else ''}"


def cookie_value() -> str:
    return hmac.new(config.TOKEN.encode(), _SALT, hashlib.sha256).hexdigest()


def _same(given: str, want: str) -> bool:
    # 请求头按 latin-1 解码，非 ASCII 会让 compare_digest 抛 TypeError
    return hmac.compare_digest(given.encode(), want.encode())


def require_auth(
    request: HTTPConnection,
    authorization: str = Header(default=""),
    x_homeos_token: str = Header(default=""),
) -> None:
    """cookie 或令牌任一有效即可。令牌认 `Authorization: Bearer` 与 `X-HomeOS-Token`。"""
    if not config.TOKEN:
        if config.DEV:
            return
        raise HTTPException(401, "服务端未配置 server.token，接口已关闭")
    given = x_homeos_token or authorization.removeprefix("Bearer ").strip()
    cookie = request.cookies.get(COOKIE, "")
    if _same(given, config.TOKEN) or _same(cookie, cookie_value()):
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
