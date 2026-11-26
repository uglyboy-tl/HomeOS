"""健康检查：`/api/ready` 是唯一不认证的接口（容器 healthcheck 用）。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/ready")
def ready():
    return {"ok": True}
