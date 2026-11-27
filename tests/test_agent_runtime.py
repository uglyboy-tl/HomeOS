"""会话状态机：一轮的生命周期、快照、持久化、空闲回收、崩溃退避、配置热更新。"""

import asyncio
import json
import time
from importlib import import_module

import pytest

from app import config
from app.lib import health
from app.services import agent

runtime_module = import_module("app.services.agent.runtime")


def _run(coro):
    return asyncio.run(coro)


async def _start() -> agent.Runtime:
    runtime = agent.Runtime()
    await runtime.startup()
    return runtime


async def _wait(runtime, mode="chat", seconds=5.0):
    deadline = time.monotonic() + seconds
    while runtime.snapshot(mode)["state"] == "thinking" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return runtime.snapshot(mode)


def test_prompt_finishes_with_text(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "你好")

    async def run():
        runtime = await _start()
        started = await runtime.prompt("chat", "在吗")
        snapshot = await _wait(runtime)
        await runtime.aclose()
        return started, snapshot

    started, snapshot = _run(run())
    assert started["state"] == "thinking"
    assert started["question"] == "在吗"
    assert snapshot["state"] == "done"
    assert snapshot["text"] == "你好"
    assert snapshot["session"]


def test_busy_prompt_is_rejected(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "慢")
    monkeypatch.setenv("FAKE_PI_SLEEP", "1")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "第一次")
        try:
            with pytest.raises(agent.Busy):
                await runtime.prompt("chat", "第二次")
        finally:
            await runtime.abort("chat")
            await runtime.aclose()

    _run(run())


def test_abort_keeps_what_was_already_said(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "说到一半")
    monkeypatch.setenv("FAKE_PI_SLEEP", "1")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "长一点")
        await _wait_text(runtime)
        aborted = await runtime.abort("chat")
        await runtime.aclose()
        return aborted

    snapshot = _run(run())
    assert snapshot["state"] == "done"
    assert snapshot["text"] == "说到一半"


async def _wait_text(runtime, mode="chat", seconds=5.0):
    """等到正文出现再 abort：固定 sleep 在 CI 满载时不可靠。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if runtime.snapshot(mode)["text"]:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("正文一直没出现")


def test_new_session_clears_text_and_changes_id(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "旧对话")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        before = await _wait(runtime)
        after = await runtime.new_session("chat")
        await runtime.aclose()
        return before, after

    before, after = _run(run())
    assert after["text"] == ""
    assert after["state"] == "idle"
    assert after["session"] != before["session"]


def test_snapshot_survives_restart(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "记住我")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "记一下")
        done = await _wait(runtime)
        await runtime.aclose()
        restarted = await _start()
        restored = restarted.snapshot("chat")
        await restarted.aclose()
        return done, restored

    done, restored = _run(run())
    assert restored["text"] == done["text"]
    assert restored["session"] == done["session"]
    assert restored["state"] == "done"


def test_thinking_snapshot_is_marked_interrupted(fake_pi):
    config.PI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (config.PI_SESSION_DIR / "chat.json").write_text(
        json.dumps({"v": 2, "state": "thinking", "text": "半句话", "session": "s1"}),
        encoding="utf-8",
    )

    async def run():
        runtime = await _start()
        snapshot = runtime.snapshot("chat")
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["state"] == "error"
    assert snapshot["error"]["code"] == "interrupted"
    assert snapshot["text"] == "半句话"


def test_legacy_session_id_is_adopted(fake_pi):
    config.PI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (config.PI_SESSION_DIR / "chat.id").write_text("chat-abc123", encoding="utf-8")

    async def run():
        runtime = await _start()
        snapshot = runtime.snapshot("chat")
        await runtime.aclose()
        return snapshot

    assert _run(run())["session"] == "chat-abc123"


def test_crash_reports_code_and_stderr_then_cools_down(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "会崩")
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")
    monkeypatch.setenv("FAKE_PI_STDERR", "上游炸了")

    async def run():
        runtime = await _start()
        for _ in range(3):
            await runtime.prompt("chat", "来")
            await _wait(runtime)
        report = await runtime.state("chat")
        with pytest.raises(agent.CoolingDown):
            await runtime.prompt("chat", "还来")
        await runtime.aclose()
        return report

    report = _run(run())
    assert report["snapshot"]["error"]["code"] == "pi_crashed"
    assert "上游炸了" in report["stderr_tail"]
    assert report["process"]["crashes"] == 3


def test_idle_eviction_closes_the_process(fake_pi, monkeypatch):
    monkeypatch.setattr(runtime_module, "EVICT_IDLE", 0.0)
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        conversation = runtime._conversation("chat")
        await conversation.evict()
        report = await runtime.state("chat")
        await runtime.aclose()
        return report

    report = _run(run())
    assert report["process"]["alive"] is False
    assert report["snapshot"]["text"] == "好"


def test_state_report_carries_usage(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        report = await runtime.state("chat")
        await runtime.aclose()
        return report

    report = _run(run())
    assert report["session"]["messages"] == 2
    assert report["stats"]["tokens"] == {"input": 11, "output": 22}
    assert report["engine"]["available"] is True


def test_stream_yields_snapshots(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "慢慢说")
    monkeypatch.setenv("FAKE_PI_SLEEP", "1")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        frames = []
        async for snapshot in runtime.stream("chat"):
            frames.append(snapshot["state"])
            if len(frames) == 2:
                break
        await runtime.abort("chat")
        await runtime.aclose()
        return frames

    assert _run(run())[0] == "thinking"


def test_config_change_restarts_process_but_keeps_context(fake_pi, monkeypatch, agent_config):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        first = await runtime.state("chat")
        (agent_config / "agents.yaml").write_text(
            "- id: chat\n  label: 聊天\n  hint: 新提示\n  prompt: 换个说法\n",
            encoding="utf-8",
        )
        (agent_config / "agents.yaml").touch()
        await runtime.prompt("chat", "再来")
        await _wait(runtime)
        second = await runtime.state("chat")
        await runtime.aclose()
        return first, second

    first, second = _run(run())
    assert second["config"]["fingerprint"] != first["config"]["fingerprint"]
    assert second["process"]["pid"] != first["process"]["pid"], "配置变了必须换一个进程"
    assert second["session"]["id"] == first["session"]["id"], "重启后要按会话 id 接上上下文"


def test_broken_config_only_marks_agent_unavailable(fake_pi, agent_config):
    (agent_config / "agents.yaml").write_text("- id: Bad\n  prompt: x\n", encoding="utf-8")

    async def run():
        runtime = await _start()
        info = runtime.info()
        await runtime.aclose()
        return info

    info = _run(run())
    assert info["engine"]["available"] is False
    assert any("id 非法" in problem for problem in info["engine"]["problems"])


def test_missing_pi_binary_is_a_problem(monkeypatch, agent_config):
    monkeypatch.setattr(config, "PI_BIN", "/nonexistent/pi")
    monkeypatch.setattr(config, "PI_EXTENSIONS", [])

    async def run():
        runtime = await _start()
        problems = runtime.info()["engine"]["problems"]
        await runtime.aclose()
        return problems

    assert any("找不到 pi" in problem for problem in _run(run()))


def test_new_session_is_not_undone_by_legacy_migration(fake_pi):
    """新开过会话、且没起进程时重启：不能又把旧 <mode>.id 接回来。"""
    config.PI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (config.PI_SESSION_DIR / "chat.id").write_text("chat-old", encoding="utf-8")

    async def run():
        runtime = await _start()
        await runtime.new_session("chat")
        await runtime.aclose()
        restarted = await _start()
        snapshot = restarted.snapshot("chat")
        await restarted.aclose()
        return snapshot

    assert _run(run())["session"] == ""


def test_garbage_state_file_is_tolerated(fake_pi):
    config.PI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (config.PI_SESSION_DIR / "chat.json").write_text(
        json.dumps({"v": 2, "state": "done", "progress": "oops", "ts": "yesterday"}),
        encoding="utf-8",
    )

    async def run():
        runtime = await _start()
        snapshot = runtime.snapshot("chat")
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["state"] == "done"
    assert snapshot["progress"] == {"step": 0, "tool": ""}


def test_crash_marks_agent_unavailable_in_health(fake_pi, monkeypatch):
    """可用性只有一处真相：真实轮次失败后，health 立刻说不可用。"""
    monkeypatch.setenv("FAKE_PI_TEXT", "会崩")
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "来")
        await _wait(runtime)
        await runtime.aclose()

    _run(run())
    assert health.get("agent")["ok"] is False


def test_ask_reports_a_friendly_message(fake_pi, monkeypatch):
    """一次性问答不把 pi 的英文 stderr 原样甩给调用方。"""
    from app.services.agent import rpc

    async def boom(_text):
        raise rpc.PiError("No API key found for provider opencode-go")

    monkeypatch.setattr(rpc, "ask", boom)
    with pytest.raises(rpc.PiError) as caught:
        _run(agent.Runtime().ask("在吗"))
    assert "没授权" in str(caught.value)


def test_friendly_messages_translate_upstream_errors():
    assert "授权" in runtime_module._friendly("No API key found for provider")
    assert "额度" in runtime_module._friendly("429 rate limit reached")
    assert "超时" in runtime_module._friendly("request timed out")
    assert runtime_module._friendly("奇怪的东西") == "奇怪的东西"
    assert runtime_module._friendly("") == "pi 报错了"


def test_pi_timeout_ends_the_turn_with_a_clear_code(fake_pi, monkeypatch):
    async def run():
        runtime = await _start()
        conversation = runtime._conversation("chat")
        client = runtime_module.rpc.RpcClient(runtime_module.rpc.LaunchSpec(thinking="off"))
        await client.start()

        async def timed_out(_text):
            raise runtime_module.rpc.PiTimeout("pi 没在 30s 内回应 prompt")

        monkeypatch.setattr(client, "prompt", timed_out)
        conversation._client = client
        await conversation._run("在吗")
        snapshot = runtime.snapshot("chat")
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["state"] == "error"
    assert snapshot["error"]["code"] == "pi_timeout"


def test_no_reply_is_reported_as_such(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        snapshot = await _wait(runtime)
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["error"]["code"] == "no_reply"


def test_provider_error_from_pi_is_not_called_a_crash(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_ERROR", "No API key found")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        snapshot = await _wait(runtime)
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["error"]["code"] == "provider_error"
    assert "授权" in snapshot["error"]["message"]


def test_abort_without_text_returns_to_idle(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "")
    monkeypatch.setenv("FAKE_PI_SLEEP", "3")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await runtime.abort("chat")
        snapshot = await _wait(runtime)
        await runtime.aclose()
        return snapshot

    snapshot = _run(run())
    assert snapshot["state"] == "idle"
    assert snapshot["error"] is None


def test_missing_pi_binary_is_pi_unavailable(fake_pi, monkeypatch):
    monkeypatch.setattr(config, "PI_BIN", "/nonexistent/pi")

    async def run():
        runtime = await _start()
        with pytest.raises(runtime_module.rpc.PiUnavailable, match="起 pi 失败"):
            await runtime.prompt("chat", "在吗")
        await runtime.aclose()

    _run(run())


def test_startup_does_not_start_a_process(fake_pi):
    async def run():
        runtime = await _start()
        report = await runtime.state("chat")
        await runtime.aclose()
        return report

    report = _run(run())
    assert report["process"]["alive"] is False, "启动只是探测，别为每个模式拉起 pi"


def test_evict_keeps_process_while_someone_is_watching(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")
    monkeypatch.setattr(runtime_module, "EVICT_IDLE", 0.0)

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        conversation = runtime._conversation("chat")
        conversation._viewers = 1
        await conversation.evict()
        alive_while_watched = (await runtime.state("chat"))["process"]["alive"]
        conversation._viewers = 0
        await conversation.evict()
        alive_after = (await runtime.state("chat"))["process"]["alive"]
        await runtime.aclose()
        return alive_while_watched, alive_after

    alive_while_watched, alive_after = _run(run())
    assert alive_while_watched is True
    assert alive_after is False


def test_evict_respects_recent_use(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        conversation = runtime._conversation("chat")
        await conversation.evict()
        report = await runtime.state("chat")
        await runtime.aclose()
        return report["process"]["alive"]

    assert _run(run()) is True, "刚用过（EVICT_IDLE 内）不该回收"


def test_conversation_close_aborts_a_running_turn(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "慢慢说")
    monkeypatch.setenv("FAKE_PI_SLEEP", "3")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        conversation = runtime._conversation("chat")
        await conversation.close()
        return conversation.busy, conversation._client

    busy, client = _run(run())
    assert busy is False
    assert client is None


def test_new_session_without_a_process_just_clears_the_id(fake_pi):
    async def run():
        runtime = await _start()
        conversation = runtime._conversation("chat")
        conversation._session_id = "旧的"
        await conversation.new_session()
        await runtime.aclose()
        return conversation._session_id, conversation._cleared

    session_id, cleared = _run(run())
    assert session_id == ""
    assert cleared is True


def test_stale_client_is_closed_on_next_turn(fake_pi, monkeypatch):
    """配置热更新后旧进程要关掉，但会话 id 留着接上下文。"""
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        conversation = runtime._conversation("chat")
        old = conversation._client
        conversation._stale = True
        await runtime.prompt("chat", "再来")
        await _wait(runtime)
        report = await runtime.state("chat")
        was_closed = old.alive is False  # 先看：不能在 aclose 兜底之后才关
        await runtime.aclose()
        return was_closed, report

    was_closed, report = _run(run())
    assert was_closed is True, "旧进程必须在换新客户端时就关掉，别等 aclose 兜底"
    assert report["snapshot"]["text"] == "好"


def test_snapshot_file_failure_does_not_break_the_turn(fake_pi, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")
    broken = tmp_path / "会话目录是个文件"
    broken.write_text("我不是目录", encoding="utf-8")
    monkeypatch.setattr(config, "PI_SESSION_DIR", broken)

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        snapshot = await _wait(runtime)
        await runtime.aclose()
        return snapshot

    assert _run(run())["text"] == "好"


def test_sweeper_evicts_idle_conversations(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "好")
    monkeypatch.setattr(runtime_module, "EVICT_IDLE", 0.0)
    monkeypatch.setattr(runtime_module, "SWEEP_EVERY", 0.05)

    async def run():
        runtime = await _start()
        await runtime.prompt("chat", "在吗")
        await _wait(runtime)
        conversation = runtime._conversation("chat")
        for _ in range(100):
            if conversation._client is None:
                break
            await asyncio.sleep(0.02)
        alive = conversation._client is not None and conversation._client.alive
        await runtime.aclose()
        return alive

    assert _run(run()) is False


def test_crash_limit_reports_cooldown(fake_pi, monkeypatch, clock):
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")
    monkeypatch.setenv("FAKE_PI_TEXT", "每次都崩")

    async def run():
        runtime = await _start()
        for _ in range(runtime_module.CRASH_LIMIT):
            await runtime.prompt("chat", "在吗")
            await _wait(runtime)
        with pytest.raises(runtime_module.CoolingDown):
            await runtime.prompt("chat", "再试")
        clock.advance(runtime_module.CRASH_COOLDOWN + 1)
        await runtime.prompt("chat", "冷却过了")
        await _wait(runtime)
        await runtime.aclose()

    _run(run())
