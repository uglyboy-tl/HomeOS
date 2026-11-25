"""语音识别：帧格式、事件解析、会话与真 WS 服务端的往返（不连 Azure）。"""

import asyncio
import json

import pytest
import websockets
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import asr


def test_audio_frame_layout():
    frame = asr.audio_frame(b"\x01\x02\x03", "rid123")
    size = int.from_bytes(frame[:2], "big")
    head = frame[2 : 2 + size].decode()
    assert "Path:audio" in head
    assert "X-RequestId:rid123" in head  # 一轮共用同一个 id，换 id 云端会当成新的一轮
    assert "Content-Type:audio/x-wav" in head
    assert frame[2 + size :] == b"\x01\x02\x03"


def test_config_frame_is_json_after_blank_line():
    raw = asr._text_frame("speech.config", {"context": {}})
    head, blank, body = raw.partition("\r\n\r\n")
    assert "Path:speech.config" in head and blank == "\r\n\r\n"
    assert json.loads(body) == {"context": {}}


def test_parse_server_frame():
    raw = (
        "X-RequestId:x\r\nPath:speech.hypothesis\r\nContent-Type:application/json\r\n\r\n"
        '{"Text": "你好"}'
    )
    assert asr.parse_server_frame(raw) == ("speech.hypothesis", {"Text": "你好"})
    assert asr.parse_server_frame("Path:turn.end") == ("turn.end", None)
    assert asr.parse_server_frame("Path:speech.phrase\r\n\r\nnot json") == ("speech.phrase", None)


def test_not_configured(monkeypatch):
    monkeypatch.setattr(config, "ASR_KEY", "")
    with pytest.raises(asr.NotConfigured):
        asr.Session(lambda text: asyncio.sleep(0))


def test_session_round_trip(monkeypatch):
    """对着一个假的 Azure WS 跑一遍：config → 音频帧 → hypothesis → phrase → turn.end。"""
    seen = {"config": False, "audio": 0, "ids": set()}

    async def handler(ws):
        async for message in ws:
            if isinstance(message, str):
                seen["config"] = seen["config"] or "speech.config" in message
                continue
            size = int.from_bytes(message[:2], "big")
            head = message[2 : 2 + size].decode()
            seen["ids"].add(next(p for p in head.split("\r\n") if p.startswith("X-RequestId:")))
            seen["audio"] += len(message) - 2 - size
            if len(message) - 2 - size == 0:  # 空帧 = 说完了
                await ws.send(asr._text_frame("speech.hypothesis", {"Text": "把灯"}))
                await ws.send(
                    asr._text_frame(
                        "speech.phrase",
                        {"RecognitionStatus": "Success", "DisplayText": "把灯关掉。"},
                    )
                )
                await ws.send("Path:turn.end\r\n\r\n")

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(config, "ASR_KEY", "k")
            monkeypatch.setattr(config, "ASR_HOST", "127.0.0.1")
            monkeypatch.setattr(
                asr, "_stream_url", lambda: f"ws://127.0.0.1:{port}/stt?language=zh-CN"
            )
            partials = []

            async def on_partial(text):
                partials.append(text)

            async with asr.Session(on_partial) as session:
                await session.send(b"\x00\x01" * 1600)
                text = await session.finish()
            return partials, text

    partials, text = asyncio.run(main())
    assert seen["config"] is True
    assert seen["audio"] == 3200 + 0
    assert len(seen["ids"]) == 1  # 整轮共用同一个 id（每帧换 id 云端会把每帧当成新一轮）
    assert partials == ["把灯"]
    assert text == "把灯关掉。"


class _FakeSession:
    def __init__(self, on_partial, rate=asr.SAMPLE_RATE):
        self.on_partial = on_partial

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def send(self, pcm):
        await self.on_partial("把灯")

    async def finish(self, timeout=5.0):
        return "把灯关掉。"


def test_ws_route_pushes_partials_then_text(monkeypatch):
    monkeypatch.setattr(asr, "Session", _FakeSession)
    with TestClient(app).websocket_connect("/api/asr/stream") as ws:
        ws.send_bytes(b"\x00\x01" * 100)
        assert ws.receive_json() == {"partial": "把灯"}
        ws.send_text("end")
        assert ws.receive_json() == {"text": "把灯关掉。"}


def test_ws_route_reports_missing_config(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise asr.NotConfigured("没配置")

    monkeypatch.setattr(asr, "Session", Boom)
    with TestClient(app).websocket_connect("/api/asr/stream") as ws:
        assert ws.receive_json() == {"error": "没配置"}
