"""WS `/api/asr/stream`：二进制帧上行转 Azure，回 `partial`/`final`/`error` 三类帧。"""

import logging

from fastapi import APIRouter, Depends, WebSocket

from ..lib import sockets
from ..services import asr

log = logging.getLogger("homeos.asr")

router = APIRouter(tags=["asr"], dependencies=[Depends(sockets.ready("asr"))])


@router.websocket("/api/asr/stream")
async def api_stream(ws: WebSocket):
    async def partial(text: str) -> None:
        await sockets.send(ws, {"type": "partial", "text": text})

    sent = 0
    try:
        async with asr.Session(partial) as session:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") == "end":
                    text = await session.finish() or ""
                    await sockets.send(ws, {"type": "final", "text": text})
                    return
                pcm = message.get("bytes")
                if not pcm:
                    continue
                sent += len(pcm)
                if sent > asr.MAX_BYTES:
                    too_long = {"type": "error", "code": "too_long", "message": "说得太长了"}
                    await sockets.send(ws, too_long)
                    return
                await session.send(pcm)
    except asr.AsrError as e:  # 必须排在 GONE 前：AsrError 也是 RuntimeError
        log.warning("语音识别失败：%s", e)
        await sockets.send(ws, {"type": "error", "code": "asr_failed", "message": str(e)})
    except sockets.GONE:
        pass
    finally:
        await sockets.close(ws)
