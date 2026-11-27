"""描述类接口：只说事实、不执行能力。上游挂了也要能回答，供页面与外部程序预判。"""

from fastapi import APIRouter

from .. import config, scan
from ..lib import health
from ..lib.errors import ApiError
from ..services import agent, mijia

router = APIRouter(tags=["info"])


@router.get("/api/info")
def api_info() -> dict:
    """能力清单 + 每个能力的缓存状态 + 配置问题：外部程序先看它，再决定要不要发执行命令。"""
    return {"capabilities": health.status(), "problems": list(config.PROBLEMS)}


@router.get("/api/info/agent")
def api_agent_info() -> dict:
    """引擎、模型、模式表、problems。"""
    return agent.runtime.info()


@router.get("/api/info/agent/state")
async def api_agent_state(mode: str = "chat") -> dict:
    """排障：进程、会话、用量、stderr 尾部。"""
    return await agent.runtime.state(mode)


@router.get("/api/info/mijia")
def api_mijia_devices() -> dict:
    """设备目录：房间顺序 + 每台设备的 id/名字/类型。只读本地表，不碰云。"""
    try:
        return mijia.devices()
    except mijia.TableError as e:
        raise ApiError(503, "not_configured", str(e)) from e


@router.get("/api/info/pages")
def api_pages() -> list:
    """功能页清单。"""
    return scan.features()
