"""agent 的 HTTP 语义：状态码、错误码、SSE 首帧、忙时拒接。"""

import json
import time
from importlib import import_module

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import agent


@pytest.fixture
def agent_client(fake_pi, monkeypatch):
    """每个用例一个干净的 Runtime：别人的常驻会话不会串进来。"""
    fresh = agent.Runtime()
    monkeypatch.setattr(agent, "runtime", fresh)
    with TestClient(app) as test_agent_client:
        yield test_agent_client


def _wait_done(client, mode="chat", seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/agent/turn?mode={mode}").json()
        if snapshot["state"] != "thinking":
            return snapshot
        time.sleep(0.02)
    raise AssertionError("这一轮没有在预期时间内结束")


def _wait_text(client, mode="chat", seconds=5.0):
    """等到正文出现再 abort：别用固定 sleep 抢时序，CI 满载时会掉。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if client.get(f"/api/agent/turn?mode={mode}").json()["text"]:
            return
        time.sleep(0.02)
    raise AssertionError("正文一直没出现")


def test_info_describes_engine_and_modes(agent_client):
    body = agent_client.get("/api/info/agent").json()
    assert body["engine"]["available"] is True
    assert body["engine"]["version"] == "fake-pi 1.0"
    assert body["engine"]["problems"] == []
    assert [mode["id"] for mode in body["modes"]] == ["chat", "design"]
    assert body["default_mode"] == "chat"
    assert body["model"]["provider"] == "fake"
    assert "bin" not in body


def test_prompt_then_turn(agent_client, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "你好")
    started = agent_client.post("/api/agent/prompt", json={"mode": "chat", "text": "在吗"})
    assert started.status_code == 200
    assert started.json()["state"] == "thinking"
    done = _wait_done(agent_client)
    assert done["state"] == "done"
    assert done["text"] == "你好"
    assert done["session"]


def test_second_prompt_is_409_busy(agent_client, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "慢")
    monkeypatch.setenv("FAKE_PI_SLEEP", "1")
    assert agent_client.post("/api/agent/prompt", json={"text": "第一次"}).status_code == 200
    second = agent_client.post("/api/agent/prompt", json={"text": "第二次"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "busy"
    assert agent_client.post("/api/agent/abort", json={"mode": "chat"}).status_code == 200


def test_abort_keeps_partial_text(agent_client, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "说到一半")
    monkeypatch.setenv("FAKE_PI_SLEEP", "1")
    agent_client.post("/api/agent/prompt", json={"text": "长一点"})
    _wait_text(agent_client)
    body = agent_client.post("/api/agent/abort", json={"mode": "chat"}).json()
    assert body["state"] == "done"
    assert body["text"] == "说到一半"


def test_new_session_starts_empty(agent_client, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "旧对话")
    agent_client.post("/api/agent/prompt", json={"text": "在吗"})
    before = _wait_done(agent_client)
    after = agent_client.post("/api/agent/new-session", json={"mode": "chat"}).json()
    assert after["text"] == ""
    assert after["state"] == "idle"
    assert after["session"] != before["session"]


def test_unknown_mode_is_400(agent_client):
    response = agent_client.post("/api/agent/prompt", json={"mode": "nope", "text": "在吗"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_mode"
    assert agent_client.get("/api/agent/turn?mode=nope").status_code == 400


def test_empty_text_is_400(agent_client):
    response = agent_client.post("/api/agent/prompt", json={"text": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_input"


def test_state_reports_process_and_session(agent_client, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")
    monkeypatch.setenv("FAKE_PI_STDERR", "一行告警")
    agent_client.post("/api/agent/prompt", json={"text": "在吗"})
    _wait_done(agent_client)
    body = agent_client.get("/api/info/agent/state?mode=chat").json()
    assert body["process"]["alive"] is True
    assert body["session"]["messages"] == 2
    assert body["stats"]["tokens"] == {"input": 11, "output": 22}
    assert "一行告警" in body["stderr_tail"]
    assert body["snapshot"]["state"] == "done"


def test_ask_runs_once_and_rejects_extra_fields(agent_client):
    body = agent_client.post("/api/agent/ask", json={"input": "在吗"}).json()
    assert body["reply"] == "答复:在吗"
    assert body["truncated"] is False
    assert isinstance(body["ms"], int) and body["ms"] >= 0
    assert agent_client.post("/api/agent/ask", json={"input": "  "}).status_code == 400
    extra = {"input": "x", "mode": "design"}
    assert agent_client.post("/api/agent/ask", json=extra).status_code == 422


def test_stream_sends_a_snapshot_frame(agent_client):
    with agent_client.stream("GET", "/api/agent/stream?mode=chat") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                snapshot = json.loads(line[len("data: ") :])
                assert snapshot["mode"] == "chat"
                assert snapshot["v"] == 2
                return
    raise AssertionError("没有收到快照帧")


def test_missing_pi_is_503(fake_pi, monkeypatch):
    monkeypatch.setattr(config, "PI_BIN", "/nonexistent/pi")
    fresh = agent.Runtime()
    monkeypatch.setattr(agent, "runtime", fresh)
    with TestClient(app) as test_agent_client:
        info = test_agent_client.get("/api/info/agent").json()
        assert info["engine"]["available"] is False
        response = test_agent_client.post("/api/agent/prompt", json={"text": "在吗"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "not_configured"


def test_one_crash_does_not_block_the_next_prompt(agent_client, monkeypatch):
    """崩一次只报告状态，不该把下一次请求挡 30 秒（连续崩溃才有冷却）。"""
    monkeypatch.setenv("FAKE_PI_TEXT", "会崩")
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")
    assert agent_client.post("/api/agent/prompt", json={"text": "第一次"}).status_code == 200
    _wait_done(agent_client)
    monkeypatch.delenv("FAKE_PI_CRASH_AFTER")
    monkeypatch.setenv("FAKE_PI_TEXT", "好了")
    again = agent_client.post("/api/agent/prompt", json={"text": "再来"})
    assert again.status_code == 200


def test_unhandled_error_uses_the_same_envelope(fake_pi, monkeypatch):
    """未处理异常也走 {"error": {...}}，细节只在日志里。"""
    from fastapi.testclient import TestClient

    from app.services import agent as agent_service

    runtime_module = import_module("app.services.agent.runtime")

    fresh = agent_service.Runtime()
    monkeypatch.setattr(agent_service, "runtime", fresh)

    def boom(*_a, **_k):
        raise RuntimeError("token=secret")

    monkeypatch.setattr(runtime_module, "config_problems", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/api/info/agent")
    assert r.status_code == 500
    assert r.json()["error"] == {"code": "internal", "message": "服务内部错误"}
    assert "secret" not in r.text


def test_http_errors_use_the_same_envelope(agent_client):
    r = agent_client.get("/api/agent/turn?mode=nope")
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_mode"


def test_engine_reports_config_problems_and_last_error_separately(agent_client, monkeypatch):
    """available 只说配置；单次崩溃只体现在 last_error，请求仍可重试。"""
    monkeypatch.setenv("FAKE_PI_TEXT", "会崩")
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")
    agent_client.post("/api/agent/prompt", json={"text": "来"})
    _wait_done(agent_client)
    engine = agent_client.get("/api/info/agent").json()["engine"]
    assert engine["available"] is True  # 配置没坏
    assert engine["problems"] == []
    assert engine["last_error"]  # 但上一次运行失败了
