"""WS `/api/asr/stream`：二进制帧上行转 Azure，`{"partial"}` 推回去；`end` 收尾后回 `{"text"}`。"""

import logging

from fastapi import APIRouter, Depends, WebSocket

from ..lib import sockets
from ..services import asr

log = logging.getLogger("homeos.asr")

router = APIRouter(dependencies=[Depends(sockets.ready("azure_asr.endpoint", "azure_asr.api_key"))])


@router.websocket("/api/asr/stream")
async def api_stream(ws: WebSocket):
    async def partial(text: str) -> None:
        await sockets.send(ws, {"partial": text})

    sent = 0
    try:
        async with asr.Session(partial) as session:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") == "end":
                    await sockets.send(ws, {"text": await session.finish() or ""})
                    return
                pcm = message.get("bytes")
                if not pcm:
                    continue
                sent += len(pcm)
                if sent > asr.MAX_BYTES:
                    await sockets.send(ws, {"error": "说得太长了，分几次说吧"})
                    return
                await session.send(pcm)
    except asr.AsrError as e:  # 必须排在 GONE 前：AsrError 也是 RuntimeError
        log.warning("语音识别失败：%s", e)
        await sockets.send(ws, {"error": str(e)})
    except sockets.GONE:
        pass
    finally:
        await sockets.close(ws)
