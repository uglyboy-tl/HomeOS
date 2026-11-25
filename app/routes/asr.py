"""语音识别：WS `/api/asr/stream`。

浏览器一边说话一边推二进制（16kHz 单声道 16bit 裸 PCM），这里转给 Azure，
把中间结果按 `{"partial": "..."}` 推回去；说完推一条文本 `end`，再回 `{"text": "..."}` 并关连接。
出错回 `{"error": "..."}`。
"""

import contextlib
import logging

from fastapi import APIRouter, WebSocket

from ..services import asr

router = APIRouter()
log = logging.getLogger("homeos.asr")


@router.websocket("/api/asr/stream")
async def api_stream(ws: WebSocket):
    await ws.accept()

    async def partial(text: str) -> None:
        await ws.send_json({"partial": text})

    sent = 0
    try:
        async with asr.Session(partial) as session:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") == "end":
                    await ws.send_json({"text": await session.finish() or ""})
                    return
                pcm = message.get("bytes")
                if not pcm:
                    continue
                sent += len(pcm)
                if sent > asr.MAX_BYTES:
                    await ws.send_json({"error": "说得太长了，分几次说吧"})
                    return
                await session.send(pcm)
    except asr.AsrError as e:
        log.warning("语音识别失败：%s", e)
        with contextlib.suppress(RuntimeError):
            await ws.send_json({"error": str(e)})
    finally:
        with contextlib.suppress(RuntimeError):
            await ws.close()
