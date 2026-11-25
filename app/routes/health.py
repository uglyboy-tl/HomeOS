"""健康检查与功能清单。`/api/ready` 是唯一不认证的接口（容器 healthcheck 用）。"""

from fastapi import APIRouter

from .. import scan

router = APIRouter()
api_router = APIRouter()


@router.get("/api/ready")
def ready():
    return {"ok": True}


@api_router.get("/api/pages")
def pages():
    """首页用它列功能页（服务端不再拼 HTML，列表由页面自己拉）。"""
    return {"ok": True, "data": scan.features()}
