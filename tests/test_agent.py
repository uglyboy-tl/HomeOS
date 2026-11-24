"""助手门面：只测纯逻辑（错误翻译、消息还原、模式映射），不连 opencode。"""

from app.services import agent


def test_blank_holds_every_key_turn_reads():
    """_blank() 是唯一的状态工厂：少了键，turn() 就会在边界上炸。"""
    blank = agent._blank()
    for key in ("state", "text", "error", "dirty", "synced_at", "empty_since"):
        assert key in blank


async def _no_resync(mode):
    return None


def test_turn_survives_empty_done(monkeypatch):
    """opencode 先落库一条空正文、正文随后才补：这一帧不能崩，也不能当成结束。"""
    import asyncio

    monkeypatch.setattr(agent, "resync", _no_resync)
    agent._state["chat"] = agent._blank() | {"state": "done", "text": ""}
    first = asyncio.run(agent.turn("chat"))
    assert first["state"] == "thinking"  # 空 done 先当还在工作
    second = asyncio.run(agent.turn("chat"))  # 再读一次不该 KeyError
    assert second["state"] == "thinking"


def test_turn_gives_up_on_empty_done(monkeypatch):
    """宽限期也过了还是没正文：转成 error，否则前端永远等不到收尾。"""
    import asyncio
    import time

    monkeypatch.setattr(agent, "resync", _no_resync)
    agent._state["chat"] = agent._blank() | {
        "state": "done",
        "text": "",
        "empty_since": time.monotonic() - agent.EMPTY_GRACE - 1,
    }
    snap = asyncio.run(agent.turn("chat"))
    assert snap["state"] == "error"
    assert "回复" in snap["error"]


def test_modes_map_to_opencode_agents():
    assert agent.MODES == {"chat": "chat", "design": "pages", "device": "device"}


def test_human_error_translates_common_cases():
    assert "key" in agent.human_error("OpenCode Go requires an API key")
    assert "额度" in agent.human_error("usage limit reached")
    assert "太长" in agent.human_error("context length exceeded")
    assert agent.human_error("") == "助手出错了，稍后再试"
    assert agent.human_error("boom") == "助手出错了：boom"


def _msg(mid, kind, created, text="", done=None, error=None):
    m = {"id": mid, "type": kind, "time": {"created": created}}
    if done is not None:
        m["time"]["completed"] = done
    if kind == "user":
        m["text"] = text
    else:
        m["content"] = [{"type": "text", "text": text}]
    if error:
        m["error"] = error
    return m


def test_summarize_takes_newest_turn_regardless_of_order():
    # opencode 的 /message 是最新在前；这里故意倒序喂进去
    messages = [
        _msg("m3", "assistant", 30, "第二个回答", done=31),
        _msg("m2", "user", 20, "第二个问题"),
        _msg("m1", "assistant", 10, "第一个回答", done=11),
        _msg("m0", "user", 5, "第一个问题"),
    ]
    got = agent.summarize(messages)
    assert got["question"] == "第二个问题"
    assert got["text"] == "第二个回答"
    assert got["done"] is True
    assert got["error"] == ""


def test_summarize_marks_unfinished_turn():
    messages = [
        _msg("m1", "assistant", 10, "说到一半"),
        _msg("m0", "user", 5, "问题"),
    ]
    got = agent.summarize(messages)
    assert got["done"] is False
    assert got["text"] == "说到一半"


def test_summarize_translates_error():
    messages = [
        _msg(
            "m1",
            "assistant",
            10,
            "",
            error={"type": "provider.auth", "message": "requires an API key"},
        ),
        _msg("m0", "user", 5, "问题"),
    ]
    got = agent.summarize(messages)
    assert "key" in got["error"]


def test_summarize_empty():
    assert agent.summarize([]) == {"question": "", "text": "", "done": False, "error": ""}


def test_compact_is_chronological_and_carries_diagnostics():
    messages = [
        _msg("m2", "assistant", 20, "答", done=21),
        _msg("m1", "user", 10, "问"),
    ]
    messages[0]["model"] = {"providerID": "opencode-go", "id": "x", "variant": "none"}
    messages[0]["tokens"] = {"input": 1, "output": 2}
    rows = agent.compact(messages)
    assert [r["type"] for r in rows] == ["user", "assistant"]
    assert rows[0]["text"] == "问"
    assert rows[1]["done"] is True
    assert rows[1]["model"] == "opencode-go/x#none"
    assert rows[1]["tokens"] == {"input": 1, "output": 2}


def test_compact_translates_error_and_keeps_raw_type():
    messages = [
        _msg(
            "m1",
            "assistant",
            10,
            "",
            done=11,
            error={"type": "provider.auth", "message": "requires an API key"},
        ),
        _msg("m0", "user", 5, "问"),
    ]
    row = agent.compact(messages)[-1]
    assert row["error_raw"] == "provider.auth"
    assert "key" in row["error"]


def test_human_error_maps_no_route():
    assert "模型" in agent.human_error("provider.no-route: no such model")


def test_summarize_ignores_messages_before_given_user():
    messages = [
        _msg("m3", "assistant", 30, "新答", done=31),
        _msg("m2", "user", 20, "新问"),
        _msg("m1", "assistant", 10, "旧答", done=11),
        _msg("m0", "user", 5, "旧问"),
    ]
    got = agent.summarize(messages, after="m2")
    assert got["question"] == "新问"
    assert got["text"] == "新答"


def test_summarize_returns_none_while_own_message_not_stored():
    messages = [_msg("m1", "assistant", 10, "旧答", done=11), _msg("m0", "user", 5, "旧问")]
    assert agent.summarize(messages, after="m9") is None


def test_summarize_treats_odd_finish_as_error():
    m = _msg("m1", "assistant", 10, "", done=11)
    m["finish"] = "error"
    got = agent.summarize([m, _msg("m0", "user", 5, "问")])
    assert got["error"]
