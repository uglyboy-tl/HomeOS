"""Azure 实时识别（会话模式 WS）；只支持这一家：换供应商等于换协议。

一轮音频必须共用同一个 `X-RequestId`（每帧换 id，云端只回 turn.start）；帧格式见两个构造函数。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from .. import config
from ..lib import health

# 语音识别按音频时长计费，这里挡一下异常的长时间流（10 分钟）
MAX_SECONDS = 600
SAMPLE_RATE = 16000
MAX_BYTES = MAX_SECONDS * SAMPLE_RATE * 2  # 16bit 单声道


class AsrError(RuntimeError):
    """识别不可用或失败（没配置、上游报错、协议不符预期）。"""


def _text_frame(path: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    head = (
        f"X-RequestId:{uuid.uuid4().hex}\r\n"
        f"Path:{path}\r\n"
        f"X-Timestamp:{int(time.time() * 1000)}\r\n"
        "Content-Type:application/json; charset=utf-8"
    )
    return f"{head}\r\n\r\n{body}"


def audio_frame(pcm: bytes, request_id: str) -> bytes:
    """一帧音频：头部长度(2字节大端) + 头部 + 裸 PCM。"""
    head = (
        f"X-RequestId:{request_id}\r\n"
        f"Path:audio\r\n"
        f"X-Timestamp:{int(time.time() * 1000)}\r\n"
        "Content-Type:audio/x-wav"
    ).encode()
    return len(head).to_bytes(2, "big") + head + pcm


def _config_message(rate: int) -> dict:
    return {
        "context": {
            "system": {
                "name": "homeos",
                "version": "0.1",
                "build": "1",
                "lang": "Python",
                "os": "Linux",
            },
            "audio": {
                "source": {"type": "Stream"},
                "format": {
                    "type": "AudioStream",
                    "compression": "PCM",
                    "samplerate": rate,
                    "bitspersample": 16,
                    "channelcount": 1,
                },
            },
        }
    }


def parse_server_frame(raw: str) -> tuple[str, dict | None]:
    """服务端文本帧 → (path, body)。body 是 JSON 对象，解析不了就是 None。"""
    head, _, body = raw.partition("\r\n\r\n")
    path = next(
        (
            line.split(":", 1)[1].strip()
            for line in head.split("\r\n")
            if line.lower().startswith("path:")
        ),
        "",
    )
    if not body:
        return path, None
    try:
        return path, json.loads(body)
    except ValueError:
        return path, None


def _stream_url() -> str:
    return (
        f"wss://{config.ASR_HOST}/speech/recognition/conversation/cognitiveservices/v1"
        f"?format=simple&language={config.ASR_LANG}"
    )


class Session:
    """一轮识别：`send()` 推 PCM，`finish()` 收尾回最终文本；中间结果走 `on_partial` 回调。"""

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None]],
        rate: int = SAMPLE_RATE,
    ) -> None:
        self.on_partial = on_partial
        self.rate = rate
        self.request_id = uuid.uuid4().hex
        self._final: str | None = None
        self._error: str | None = None
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._done = asyncio.Event()

    async def __aenter__(self) -> Session:
        try:
            self._ws = await websockets.connect(
                _stream_url(),
                additional_headers={
                    "Ocp-Apim-Subscription-Key": config.ASR_KEY,
                    "X-ConnectionId": uuid.uuid4().hex,
                },
                max_size=None,
                open_timeout=config.ASR_TIMEOUT,
            )
        except (websockets.WebSocketException, OSError) as e:
            raise AsrError(f"连不上识别服务：{e}") from e
        await self._ws.send(_text_frame("speech.config", _config_message(self.rate)))
        self._reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _read(self) -> None:
        try:
            async for raw in self._ws:  # type: ignore[union-attr]
                if isinstance(raw, bytes):  # 服务端的音频段（我们不用合成，忽略）
                    continue
                path, body = parse_server_frame(raw)
                if path == "speech.hypothesis" and body:
                    text = (body.get("Text") or "").strip()
                    if text:
                        await self.on_partial(text)
                elif path == "speech.phrase" and body:
                    if body.get("RecognitionStatus") == "Success":
                        self._final = (body.get("DisplayText") or "").strip() or None
                elif path == "turn.end":
                    return
        except websockets.WebSocketException as e:
            self._error = f"识别连接断了：{e}"
        finally:
            self._done.set()  # 正常结束、异常结束都要放行 finish()

    async def send(self, pcm: bytes) -> None:
        try:
            await self._ws.send(audio_frame(pcm, self.request_id))  # type: ignore[union-attr]
        except websockets.WebSocketException as e:
            raise AsrError(f"识别连接断了：{e}") from e

    async def finish(self, timeout: float = 5.0) -> str | None:
        """收尾：发一个空音频帧表示说完了，等 turn.end（云端靠静音判断断句）。"""
        await self.send(b"")
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except TimeoutError:
            self._error = "识别服务没回结果"
        if self._error:
            raise AsrError(self._error)
        return self._final


def check_config() -> None:
    config.require("azure_asr.endpoint", "azure_asr.api_key")


# 只有建 WS 才探得到真实可用性：失败在帧里报，所以没有 probe
health.register("asr", check=check_config)
