"""页面路由：静态 HTML 原样发出（无模板无注入）。功能页清单在 /api/info/pages。"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, scan
from ..lib.security import auth_cookie

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}

# 固定页：加一页 = 这里加一行 + 在 web/ 放一个 html，再在首页写一个链接
FIXED = {"home": "home.html", "mijia": "mijia.html", "agent": "agent.html"}


def _asset_headers() -> dict:
    """开发时也别让浏览器缓存静态资源：改完 CSS/JS 要立刻看到（安全头由 lib/headers 统一加）。"""
    return {"Cache-Control": "no-store" if config.DEV else "public, max-age=300"}


def _file(rel: str, headers: dict | None = None):
    """web/ 下的文件；`..` 直接拒，它可能绕回 web/ 里另一个子树。"""
    if ".." in rel:
        raise HTTPException(404, "没有这个文件")
    full = (config.WEB_ROOT / rel).resolve()
    if config.WEB_ROOT not in full.parents or not full.is_file():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(full, headers=headers or _asset_headers())


def _page(rel: str, request: Request):
    head = dict(NO_STORE)
    if config.TOKEN:
        head["Set-Cookie"] = auth_cookie(request)
    return _file(rel, head)


def _slug(slug: str) -> str:
    """功能页 slug 的合法形状；不合法就是地址不存在，不是参数错误。"""
    if not scan.SLUG.match(slug):
        raise HTTPException(404, "没有这个文件")
    return slug


@router.get("/")
def home(request: Request):
    return _page(FIXED["home"], request)


@router.get("/{name}")
def fixed_page(name: str, request: Request):
    if name == "home" or name not in FIXED:
        raise HTTPException(404, "没有这个文件")
    return _page(FIXED[name], request)


@router.get("/p/{slug}")
def page(slug: str, request: Request):
    return _page(f"pages/{_slug(slug)}/index.html", request)


@router.get("/pages/{slug}/assets/{name:path}")
def page_asset(slug: str, name: str):
    return _file(f"pages/{_slug(slug)}/assets/{name}")


@router.get("/assets/{name:path}")
def asset(name: str):
    return _file(f"assets/{name}")
