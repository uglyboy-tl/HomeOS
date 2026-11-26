"""页面路由：静态 HTML 原样发出（无模板无注入）+ `/api/pages` 功能页清单。"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, scan
from ..lib.security import auth_cookie

router = APIRouter()
# 两个 router 是认证边界：静态页不能要令牌（页面响应自己种 cookie），`/api/pages` 必须挂认证组
api_router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
# 开发时也别让浏览器缓存静态资源：改完 CSS/JS 要立刻看到
ASSET_CACHE = {"Cache-Control": "no-store" if config.DEV else "public, max-age=300"}

# 固定页：加一页 = 这里加一行 + 在 web/ 放一个 html，再在首页写一个链接
FIXED = {"home": "home.html", "mijia": "mijia.html", "agent": "agent.html"}


def _file(rel: str, headers: dict | None = None):
    """web/ 下的文件。`..` 直接拒：它可能绕回 web/ 里另一个子树（/assets/../home.html），
    不只是防跑出 web/。"""
    if ".." in rel:
        raise HTTPException(status_code=404)
    full = (config.WEB_ROOT / rel).resolve()
    if config.WEB_ROOT not in full.parents or not full.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(full, headers=headers or ASSET_CACHE)


def _page(rel: str, request: Request):
    head = dict(NO_STORE)
    if config.TOKEN:
        head["Set-Cookie"] = auth_cookie(request)
    return _file(rel, head)


@router.get("/")
def home(request: Request):
    return _page(FIXED["home"], request)


@router.get("/{name}")
def fixed_page(name: str, request: Request):
    if name == "home" or name not in FIXED:
        raise HTTPException(status_code=404)
    return _page(FIXED[name], request)


@router.get("/p/{slug}")
def page(slug: str, request: Request):
    if not scan.SLUG.match(slug):
        raise HTTPException(status_code=404)
    return _page(f"pages/{slug}/index.html", request)


@router.get("/pages/{slug}/assets/{name:path}")
def page_asset(slug: str, name: str):
    if not scan.SLUG.match(slug):
        raise HTTPException(status_code=404)
    return _file(f"pages/{slug}/assets/{name}")


@router.get("/assets/{name:path}")
def asset(name: str):
    return _file(f"assets/{name}")


@api_router.get("/api/pages")
def api_pages():
    """首页用它列功能页（服务端不拼 HTML，列表由页面自己拉）。"""
    return {"ok": True, "data": scan.features()}
