"""页面路由：把静态 HTML 原样发出去。

没有模板、没有注入：每个页面都是独立、完整的 HTML，自己能打开、能拷走。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config, scan

router = APIRouter()

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


@router.get("/")
def home():
    return _file(FIXED["home"], NO_STORE)


@router.get("/{name}")
def fixed_page(name: str):
    if name == "home" or name not in FIXED:
        raise HTTPException(status_code=404)
    return _file(FIXED[name], NO_STORE)


@router.get("/p/{slug}")
def page(slug: str):
    if not scan.SLUG.match(slug):
        raise HTTPException(status_code=404)
    return _file(f"pages/{slug}/index.html", NO_STORE)


@router.get("/pages/{slug}/assets/{name:path}")
def page_asset(slug: str, name: str):
    if not scan.SLUG.match(slug):
        raise HTTPException(status_code=404)
    return _file(f"pages/{slug}/assets/{name}")


@router.get("/assets/{name:path}")
def asset(name: str):
    return _file(f"assets/{name}")
