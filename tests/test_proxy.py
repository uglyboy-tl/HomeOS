"""`/oc` 用的流式反代：上游超时 504、连不上 502、正常时逐块回传且连接被关掉。

不连真服务：把 `httpx.AsyncClient` 换成一个假的。
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import _proxy
from app._proxy import forward


class FakeResp:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_raw(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        closed.append("resp")


class FakeClient:
    mode = "ok"
    chunks: list[bytes] = [b"data: 1\n\n", b"data: 2\n\n"]

    def __init__(self, *args, **kwargs):
        pass

    def build_request(self, *args, **kwargs):
        sent_headers.clear()
        sent_headers.update(kwargs.get("headers") or {})
        return object()

    async def send(self, req, stream=False):
        if FakeClient.mode == "timeout":
            raise httpx.TimeoutException("太慢")
        if FakeClient.mode == "boom":
            raise httpx.ConnectError("连不上")
        return FakeResp(FakeClient.chunks)

    async def aclose(self):
        closed.append("client")


closed: list[str] = []
sent_headers: dict[str, str] = {}


@pytest.fixture
def client(monkeypatch):
    closed.clear()
    monkeypatch.setattr(_proxy.httpx, "AsyncClient", FakeClient)
    app = FastAPI()

    @app.get("/t")
    async def t(request: Request):
        return await forward(request, "http://upstream/x", label="测试", timeout=httpx.Timeout(1))

    return TestClient(app)


def test_timeout_maps_to_504(client):
    FakeClient.mode = "timeout"
    r = client.get("/t")
    assert r.status_code == 504
    assert "超时" in r.json()["error"]
    assert "client" in closed  # 失败也要关掉 client


def test_upstream_down_maps_to_502(client):
    FakeClient.mode = "boom"
    r = client.get("/t")
    assert r.status_code == 502
    assert "没启动" in r.json()["error"]
    assert "client" in closed


def test_only_whitelisted_headers_go_upstream(client):
    """客户端带来的任何头（含 /oc 的令牌、Cookie）都不转发。"""
    FakeClient.mode = "ok"
    client.get("/t", headers={"X-HomeOS-Token": "s3cret", "Cookie": "a=b"})
    assert sent_headers == {"Accept-Encoding": "identity"}


def test_streams_chunks_and_closes_everything(client):
    FakeClient.mode = "ok"
    r = client.get("/t")
    assert r.status_code == 200
    assert r.text == "data: 1\n\ndata: 2\n\n"
    assert closed == ["resp", "client"]  # background 里两样都关了


def test_stop_event_ends_stream(monkeypatch):
    """应用关闭时 SSE 生成器要自己退出，不能挂着等上游。"""
    closed.clear()
    monkeypatch.setattr(_proxy.httpx, "AsyncClient", FakeClient)

    class SlowResp(FakeResp):
        async def aiter_raw(self):
            while True:
                await asyncio.sleep(0)
                yield b"x"

    FakeClient.mode = "ok"
    monkeypatch.setattr(FakeClient, "send", lambda self, req, stream=False: _res(SlowResp([b"x"])))
    from app.runtime import stop_event

    stop_event.set()
    try:
        app = FastAPI()

        @app.get("/t")
        async def t(request: Request):
            return await forward(
                request, "http://upstream/x", label="测试", timeout=httpx.Timeout(1)
            )

        r = TestClient(app).get("/t")
        assert r.status_code == 200
        assert "resp" in closed and "client" in closed
    finally:
        stop_event.clear()


async def _res(resp):
    return resp
