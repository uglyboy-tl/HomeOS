"""助手接口：/api/agent/*。

页面用 /turn（轮询兜底）、/stream（SSE 逐字）、/say、/new；测试台用 /ask、/info、/trace、
/bind、/model 直接验证 opencode 的 agent 与模型配置。
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..services import agent

router = APIRouter()


def install_error_handlers(app) -> None:
    """把领域异常统一映射成 HTTP：模式不认识 400，opencode 不可用 503。

    放在这里而不是每个 endpoint 里 try/except：九条路由不该抄同一段映射。
    """

    @app.exception_handler(agent.UnknownMode)
    async def _unknown_mode(_request, exc):
        return JSONResponse({"detail": f"未知模式：{exc}"}, status_code=400)

    @app.exception_handler(agent.AgentError)
    async def _agent_error(_request, exc):
        return JSONResponse({"detail": agent.human_error(str(exc))}, status_code=503)


class SayReq(BaseModel):
    mode: str
    text: str


class ModeReq(BaseModel):
    mode: str


class AskReq(BaseModel):
    mode: str = "chat"
    text: str
    timeout: float = 90.0


class BindReq(BaseModel):
    mode: str
    agent: str


class ModelReq(BaseModel):
    mode: str
    providerID: str
    id: str
    variant: str = ""


@router.post("/api/agent/say")
async def api_say(req: SayReq):
    return await agent.say(req.mode, _text(req.text))


@router.get("/api/agent/turn")
async def api_turn(mode: str = "chat"):
    return await agent.turn(mode)


@router.post("/api/agent/new")
async def api_new(req: ModeReq):
    return await agent.new_conversation(req.mode)


@router.get("/api/agent/stream")
async def api_stream(request: Request, mode: str = "chat"):
    """SSE：边生成边推（服务端限流 4 次/秒）。前端不支持 EventSource 时退回轮询。"""
    agent.check(mode)  # 未知模式在流开始前就 400，别让异常掉进生成器里

    async def events():
        async for payload in agent.stream(mode):
            if await request.is_disconnected():
                return
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ---------- 测试台 ----------


@router.get("/api/agent/info")
async def api_info(mode: str = "chat"):
    return await agent.info(mode)


@router.get("/api/agent/trace")
async def api_trace(mode: str = "chat", limit: int = 20):
    return {"mode": mode, "messages": await agent.trace(mode, limit)}


@router.post("/api/agent/ask")
async def api_ask(req: AskReq):
    return await agent.ask(req.mode, _text(req.text), min(max(req.timeout, 1.0), 300.0))


@router.post("/api/agent/bind")
async def api_bind(req: BindReq):
    return await agent.bind_agent(req.mode, req.agent)


@router.post("/api/agent/model")
async def api_model(req: ModelReq):
    return await agent.set_model(req.mode, req.providerID, req.id, req.variant)


def _text(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise HTTPException(400, "内容是空的")
    return text
