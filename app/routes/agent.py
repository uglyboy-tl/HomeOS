"""助手接口 /api/agent/*：页面用 turn/stream/say/new，ask 给程序，info/trace/bind/model 调试。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from .. import config
from ..services import agent

router = APIRouter(dependencies=[Depends(config.ready("opencode.go_api_key"))])


def install_error_handlers(app) -> None:
    """领域异常 → HTTP：未知模式 400，opencode 不可用 503。"""

    @app.exception_handler(agent.UnknownMode)
    async def _unknown_mode(_request, exc):
        return JSONResponse({"detail": f"未知模式：{exc}"}, status_code=400)

    @app.exception_handler(agent.AgentError)
    async def _agent_error(_request, exc):
        return JSONResponse({"detail": agent.human_error(str(exc))}, status_code=503)

    @app.exception_handler(agent.AskTimeout)
    async def _ask_timeout(_request, exc):
        return JSONResponse({"detail": f"这一轮没跑完：{exc}"}, status_code=504)


class SayReq(BaseModel):
    mode: str
    text: str


class ModeReq(BaseModel):
    mode: str


class AskReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str
    prompt: str = ""
    timeout: float = 60.0
    speak: bool = False


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
    if not req.text.strip():
        raise HTTPException(400, "内容是空的")
    return await agent.say(req.mode, req.text)


@router.get("/api/agent/turn")
async def api_turn(mode: str = "chat"):
    return await agent.turn(mode)


@router.post("/api/agent/new")
async def api_new(req: ModeReq):
    return await agent.new_conversation(req.mode)


@router.post("/api/agent/ask")
async def api_ask(req: AskReq):
    """给程序用：交一段现成内容，拿回一段解读。独立会话、跑完即删、默认不播报。"""
    if not req.input.strip():
        raise HTTPException(400, "input 是空的")
    if len(req.prompt) > agent.MAX_PROMPT:
        raise HTTPException(400, f"prompt 太长（{len(req.prompt)} 字，上限 {agent.MAX_PROMPT}）")
    return await agent.ask(req.input, req.prompt, min(max(req.timeout, 1.0), 300.0), req.speak)


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


@router.get("/api/agent/info")
async def api_info(mode: str = "chat"):
    return await agent.info(mode)


@router.get("/api/agent/trace")
async def api_trace(mode: str = "chat", limit: int = 20):
    return {"mode": mode, "messages": await agent.trace(mode, limit)}


@router.post("/api/agent/bind")
async def api_bind(req: BindReq):
    return await agent.bind_agent(req.mode, req.agent)


@router.post("/api/agent/model")
async def api_model(req: ModelReq):
    return await agent.set_model(req.mode, req.providerID, req.id, req.variant)
