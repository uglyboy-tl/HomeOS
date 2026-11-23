"""健康检查与功能清单。"""

from fastapi import APIRouter

from .. import scan

router = APIRouter()


@router.get("/api/ready")
def ready():
    return {"ok": True}


@router.get("/api/pages")
def pages():
    """首页用它列功能页（服务端不再拼 HTML，列表由页面自己拉）。"""
    return {"ok": True, "data": scan.features()}
