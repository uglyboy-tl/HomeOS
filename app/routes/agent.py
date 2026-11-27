"""助手接口：程序用 ask，页面用 prompt/abort/new-session/turn/stream/state。

接口只做一件事：上一轮没结束时 `/prompt` 回 409，先 `/abort` 再 `/prompt` 是调用方的事。"""

import json
import time

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from ..lib import health
from ..lib.errors import ApiError, envelope
from ..services import agent
from ..services.agent import modes

MODE_PATTERN = modes.ID_RE.pattern

router = APIRouter(tags=["agent"])

MAX_INPUT = 12_000  # 字符：给程序用的 ask 会装整段内容（网页、邮件、报错）
PING_SECONDS = 15  # SSE 心跳：跨过反向代理与设备的空闲超时

_ERRORS = {
    agent.UnknownMode: (400, "unknown_mode"),
    agent.Busy: (409, "busy"),
    agent.CoolingDown: (503, "cooldown"),
    agent.PiUnavailable: (503, "pi_unavailable"),
    agent.PiTimeout: (504, "pi_timeout"),
    agent.PiError: (502, "pi_crashed"),
}


def install_error_handlers(app) -> None:
    for exc_type, (status, code) in _ERRORS.items():

        async def handler(_request, exc, status=status, code=code):  # noqa: ANN001 — 回调签名
            message = str(exc) or code
            if code == "unknown_mode":
                message = f"未知模式：{exc}（见 config/agents.yaml）"
            return envelope(status, code, message)

        app.add_exception_handler(exc_type, handler)


class PromptReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="chat", pattern=MODE_PATTERN)
    text: str = Field(min_length=1)


class ModeReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="chat", pattern=MODE_PATTERN)


class AskReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1)


@router.post("/api/agent/ask", dependencies=health.deps("agent"))
async def api_ask(req: AskReq) -> dict:
    """给程序用：chat 模式的一次问答，跑完即退，不碰页面状态。"""
    text = req.input.strip()
    if not text:
        raise ApiError(400, "bad_input", "input 是空的")
    truncated = len(text) > MAX_INPUT
    started = time.perf_counter()
    reply = await agent.runtime.ask(text[:MAX_INPUT])
    return {
        "reply": reply,
        "truncated": truncated,
        "ms": round((time.perf_counter() - started) * 1000),
    }


@router.post("/api/agent/prompt", dependencies=health.deps("agent"))
async def api_prompt(req: PromptReq) -> dict:
    """起一轮；上一轮没结束就 409。"""
    text = req.text.strip()
    if not text:
        raise ApiError(400, "bad_input", "内容是空的")
    return await agent.runtime.prompt(req.mode, text)


@router.post("/api/agent/abort")
async def api_abort(req: ModeReq) -> dict:
    """中止当前一轮；已经产出的话留在快照里。"""
    return await agent.runtime.abort(req.mode)


@router.post("/api/agent/new-session", dependencies=health.deps("agent"))
async def api_new_session(req: ModeReq) -> dict:
    """换一条空会话（不重启进程，也不删 pi 的会话文件）。"""
    return await agent.runtime.new_session(req.mode)


@router.get("/api/agent/turn")
async def api_turn(mode: str = "chat") -> dict:
    """当前快照；页面进来时先看它，判断要不要跟流。"""
    return agent.runtime.snapshot(mode)


@router.get("/api/agent/stream")
async def api_stream(mode: str = "chat") -> EventSourceResponse:
    """SSE：有变化就推一帧完整快照（服务端 4 帧/秒封顶）。"""
    agent.runtime.snapshot(mode)  # 未知模式在开流之前就 400

    async def frames():
        async for snapshot in agent.runtime.stream(mode):
            yield {"data": json.dumps(snapshot, ensure_ascii=False)}

    return EventSourceResponse(frames(), ping=PING_SECONDS, headers={"Cache-Control": "no-store"})
