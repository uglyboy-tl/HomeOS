"""`ask` 给程序用：交一段内容拿一段解读，独立会话、跑完即删、默认不播报。"""

import asyncio

import pytest

from app.services import agent


@pytest.fixture
def fake_opencode(monkeypatch):
    calls = []
    store = {"messages": []}

    async def fake_call(method, path, body=None, timeout=30.0, timeout_error=agent.AgentError):
        calls.append((method, path, body))
        if path == "/api/session":
            return {"data": {"id": "ses_new"}}
        if path.endswith("/prompt"):
            store["messages"] = [
                {"type": "user", "text": body["text"], "time": {"created": 1}},
                {
                    "type": "assistant",
                    "text": "备份失败是磁盘满了",
                    "model": {"providerID": "p", "id": "m", "variant": ""},
                    "tokens": {"input": 812, "output": 12},
                    "time": {"created": 2, "completed": 3},
                },
            ]
            return {}
        if path.endswith("/wait"):
            if timeout < 5:  # 测试里传 1s 就当作跑不完
                raise timeout_error("太慢")
            return {}
        if path.endswith("/message"):
            return {"data": store["messages"]}
        if method == "DELETE":
            return {}
        raise AssertionError(f"{method} {path}")

    monkeypatch.setattr(agent, "_call", fake_call)
    return calls


@pytest.fixture
def spoken(monkeypatch):
    said = []

    async def fake_speak(text):
        said.append(text)

    monkeypatch.setattr(agent, "_speak", fake_speak)
    return said


def sent_prompt(calls):
    return next(b["text"] for m, p, b in calls if p.endswith("/prompt"))


def test_ask_returns_reading_and_meta(fake_opencode, spoken):
    out = asyncio.run(agent.ask("磁盘 100% 已满"))
    assert out["ok"] and out["text"] == "备份失败是磁盘满了"
    assert out["tokens"] == {"input": 812, "output": 12}
    assert out["truncated"] is False and out["elapsed_ms"] >= 0


def test_ask_uses_its_own_session_and_deletes_it(fake_opencode, spoken):
    asyncio.run(agent.ask("一"))
    assert fake_opencode[0] == ("POST", "/api/session", {"agent": "chat"})
    assert ("DELETE", "/api/session/ses_new", None) in fake_opencode


def test_ask_does_not_touch_page_state(fake_opencode, spoken):
    before = dict(agent._state["chat"])
    asyncio.run(agent.ask("顺手做点事"))
    assert agent._state["chat"] == before


def test_ask_speaks_only_when_asked(fake_opencode, spoken):
    asyncio.run(agent.ask("一"))
    assert spoken == []
    asyncio.run(agent.ask("二", speak=True))
    assert spoken == ["备份失败是磁盘满了"]


def test_default_prompt_and_fencing(fake_opencode, spoken):
    asyncio.run(agent.ask("原始数据"))
    text = sent_prompt(fake_opencode)
    assert text.startswith(agent.DEFAULT_PROMPT)
    assert "<<<内容开始>>>\n原始数据\n<<<内容结束>>>" in text


def test_given_prompt_goes_before_the_data(fake_opencode, spoken):
    asyncio.run(agent.ask("原始数据", "两句话说清失败原因"))
    assert sent_prompt(fake_opencode).startswith("两句话说清失败原因\n\n<<<内容开始>>>")


def test_long_input_is_truncated_and_flagged(fake_opencode, spoken):
    out = asyncio.run(agent.ask("x" * (agent.MAX_INPUT + 10)))
    assert out["truncated"] is True
    assert len(sent_prompt(fake_opencode)) < agent.MAX_INPUT + 200


def test_timeout_raises_ask_timeout_and_still_cleans_up(fake_opencode, spoken):
    with pytest.raises(agent.AskTimeout):
        asyncio.run(agent.ask("慢活", timeout=1.0))
    assert ("DELETE", "/api/session/ses_new", None) in fake_opencode


def test_no_reply_is_an_error(fake_opencode, spoken, monkeypatch):
    async def empty_call(method, path, body=None, timeout=30.0, timeout_error=agent.AgentError):
        if path == "/api/session":
            return {"data": {"id": "ses_x"}}
        if path.endswith("/message"):
            return {"data": []}
        return {}

    monkeypatch.setattr(agent, "_call", empty_call)
    with pytest.raises(agent.AgentError):
        asyncio.run(agent.ask("空"))


def test_error_only_reply_propagates_reason(fake_opencode, spoken, monkeypatch):
    """最后一行 assistant 只有 error 没有正文时，把那句错误原样带出去。"""

    async def call(method, path, body=None, timeout=30.0, timeout_error=agent.AgentError):
        if path == "/api/session":
            return {"data": {"id": "ses_e"}}
        if path.endswith("/message"):
            return {
                "data": [
                    {"type": "user", "text": "q", "time": {"created": 1}},
                    {
                        "type": "assistant",
                        "content": [],
                        "error": {"message": "model not found"},
                        "time": {"created": 2, "completed": 3},
                    },
                ]
            }
        return {}

    monkeypatch.setattr(agent, "_call", call)
    with pytest.raises(agent.AgentError) as err:
        asyncio.run(agent.ask("q"))
    assert "模型不存在" in str(err.value)
