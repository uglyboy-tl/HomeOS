"""pi 的 RPC 传输：启动参数、命令应答、事件归一、stderr、崩溃与关闭。"""

import asyncio

import pytest

from app import config
from app.services.agent import rpc


def _run(coro):
    return asyncio.run(coro)


def test_launch_spec_without_session_has_no_session_id(monkeypatch):
    monkeypatch.setattr(config, "PI_EXTENSIONS", [])
    argv = rpc.LaunchSpec(tools=(), prompt="p", append="a", thinking="off").argv()
    assert argv[:3] == [config.PI_BIN, "--mode", "rpc"]
    assert "--no-session" not in argv  # 常驻会话不落盘是靠 --session-dir 决定的
    assert argv[argv.index("--session-dir") + 1] == str(config.PI_SESSION_DIR)
    assert "--session-id" not in argv
    assert "--no-tools" in argv
    assert argv[argv.index("--system-prompt") + 1] == "p"
    assert argv[argv.index("--append-system-prompt") + 1] == "a"
    assert argv[argv.index("--thinking") + 1] == "off"


def test_launch_spec_with_session_and_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PI_EXTENSIONS", [])
    argv = rpc.LaunchSpec(tools=("read", "write"), thinking="low", session_id="chat-1").argv()
    assert argv[argv.index("--session-id") + 1] == "chat-1"
    assert argv[argv.index("--tools") + 1] == "read,write"
    assert "--no-tools" not in argv


def test_launch_spec_passes_every_extension(monkeypatch, tmp_path):
    first, second = tmp_path / "a.js", tmp_path / "b.js"
    monkeypatch.setattr(config, "PI_EXTENSIONS", [first, second])
    argv = rpc.LaunchSpec(tools=(), thinking="").argv()
    passed = [argv[i + 1] for i, item in enumerate(argv) if item == "--extension"]
    assert passed == [str(first), str(second)]
    assert "--thinking" not in argv


def test_normalize_keeps_only_what_the_state_machine_needs():
    def norm(msg):
        return rpc.normalize(msg)

    delta = {
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": "好"},
    }
    assert norm(delta) == rpc.Event("text", text="好")
    assert (
        norm({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta"}})
        is None
    )
    tool = {"type": "tool_execution_start", "toolName": "read"}
    assert norm(tool) == rpc.Event("tool_start", tool="read")
    assert norm({"type": "agent_settled", "aborted": True}) == rpc.Event("settled", aborted=True)
    assert norm({"type": "compaction_start", "reason": "threshold"}) is None
    failed_retry = {"type": "auto_retry_end", "success": False, "finalError": "boom"}
    assert norm(failed_retry) == rpc.Event("error", error="boom")
    assert norm({"type": "auto_retry_end", "success": True}) is None


def test_version(fake_pi, monkeypatch):
    monkeypatch.setattr(rpc, "_version", None)
    assert _run(rpc.version()) == "fake-pi 1.0"


def test_start_reads_session_state(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.start()
        try:
            return await client.state()
        finally:
            await client.close()

    state = _run(run())
    assert state["sessionId"].startswith("fake-session")
    assert state["sessionFile"].endswith(f"{state['sessionId']}.jsonl")


def test_prompt_streams_text_then_settles(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "你好")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.prompt("在吗")
        kinds, text = [], ""
        async for event in client.events():
            kinds.append(event.kind)
            if event.kind == "text":
                text += event.text
            if event.kind == "settled":
                break
        await client.close()
        return kinds, text

    kinds, text = _run(run())
    assert text == "你好"
    assert kinds[-1] == "settled"


def test_tool_events_are_reported(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TOOL", "read")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=("read",), thinking="off"))
        await client.prompt("读一下")
        tools = []
        async for event in client.events():
            if event.kind.startswith("tool"):
                tools.append((event.kind, event.tool))
            if event.kind == "settled":
                break
        await client.close()
        return tools

    assert _run(run()) == [("tool_start", "read"), ("tool_end", "read")]


def test_stderr_is_kept_for_diagnosis(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_STDERR", "第一行告警\n第二行告警")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.prompt("在吗")
        async for event in client.events():
            if event.kind == "settled":
                break
        deadline = asyncio.get_running_loop().time() + 2.0
        while len(client.stderr_tail()) < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)  # stderr 读任务是独立协程，等它把两行读完
        tail = client.stderr_tail()
        await client.close()
        return tail

    tail = _run(run())
    assert tail == ["第一行告警", "第二行告警"]


def test_crash_ends_the_stream_with_closed(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_TEXT", "四个字")
    monkeypatch.setenv("FAKE_PI_CRASH_AFTER", "1")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.prompt("在吗")
        kinds = []
        async for event in client.events():
            kinds.append(event.kind)
            if event.kind == "closed":
                break
        await client.close()
        return kinds

    assert _run(run())[-1] == "closed"


def test_new_session_changes_id(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        before = (await client.state())["sessionId"]
        assert await client.new_session() is True
        after = (await client.state())["sessionId"]
        await client.close()
        return before, after

    before, after = _run(run())
    assert before != after


def test_abort_does_not_raise(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.start()
        await client.abort()
        await client.close()

    _run(run())


def test_ask_returns_plain_text(fake_pi):
    assert _run(rpc.ask("在吗")) == "答复:在吗"


def test_ask_failure_is_pi_error(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_EXIT", "3")
    with pytest.raises(rpc.PiError) as caught:
        _run(rpc.ask("在吗"))
    assert "假 pi 按 FAKE_PI_EXIT 失败" in str(caught.value)


def test_close_stops_the_process(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.start()
        pid = client.pid
        await client.close()
        return pid, client.alive

    pid, alive = _run(run())
    assert pid is not None
    assert alive is False


def test_huge_single_line_is_read(fake_pi, monkeypatch):
    """pi 的单行事件会带工具输出：超过 64KiB 也不能把读循环搞死。"""
    monkeypatch.setenv("FAKE_PI_BIG", "1")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.prompt("读个大文件")
        size = 0
        async for event in client.events():
            if event.kind == "text":
                size += len(event.text)
            if event.kind == "settled":
                break
        await client.close()
        return size

    assert _run(run()) == 200_000


def test_pending_command_fails_when_process_dies(fake_pi, monkeypatch):
    """等命令时进程没了：立刻报错，不要干等 30 秒超时。"""
    monkeypatch.setenv("FAKE_PI_DIE_ON_STATE", "1")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(tools=(), thinking="off"))
        await client.start()
        start = asyncio.get_running_loop().time()
        with pytest.raises(rpc.PiError):
            await client.state()
        elapsed = asyncio.get_running_loop().time() - start
        await client.close()
        return elapsed

    assert _run(run()) < 5.0


def test_version_is_cached(fake_pi, monkeypatch):
    monkeypatch.setattr(rpc, "_version", None)
    assert _run(rpc.version()) == "fake-pi 1.0"
    assert _run(rpc.version()) == "fake-pi 1.0"


def test_version_failure_is_empty_not_fatal(fake_pi, monkeypatch):
    monkeypatch.setattr(rpc, "_version", None)
    monkeypatch.setattr(config, "PI_BIN", "/nonexistent/pi")
    assert _run(rpc.version()) == ""


def test_non_json_and_unknown_events_are_skipped(fake_pi, monkeypatch):
    """pi 的 stdout 里可能夹非 JSON 行与状态机不关心的帧：不能因此读不下去。"""
    monkeypatch.setenv("FAKE_PI_NOISE", "1")
    monkeypatch.setenv("FAKE_PI_TEXT", "好")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.prompt("在吗")
        text = ""
        async for event in client.events():
            if event.kind == "text":
                text += event.text
            if event.kind == "settled":
                break
        await client.close()
        return text

    assert _run(run()) == "好"


def test_extension_ui_request_is_cancelled(fake_pi, monkeypatch, tmp_path):
    """无人值守：交互确认一律取消，否则 pi 会一直等一个不会来的答复。"""
    marker = tmp_path / "ui.json"
    monkeypatch.setenv("FAKE_PI_UI_REQUEST", "1")
    monkeypatch.setenv("FAKE_PI_UI_FILE", str(marker))

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        await client.state()
        for _ in range(50):
            if marker.exists():
                break
            await asyncio.sleep(0.02)
        await client.close()

    _run(run())
    assert '"cancelled": true' in marker.read_text(encoding="utf-8")


def test_close_escalates_to_term(fake_pi, monkeypatch):
    """pi 不理 stdin 关闭：5 秒后升级 TERM，别让进程挂在那儿。"""
    monkeypatch.setenv("FAKE_PI_IGNORE_CLOSE", "1")
    monkeypatch.setattr(rpc, "CLOSE_GRACE", 0.2)
    monkeypatch.setattr(rpc, "TERM_GRACE", 0.2)

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        await client.close()
        return client.alive

    assert _run(run()) is False


def test_close_escalates_to_kill(fake_pi, monkeypatch):
    """连 TERM 都不理：最后必须 KILL，不许留下僵尸 pi。"""
    monkeypatch.setenv("FAKE_PI_IGNORE_CLOSE", "1")
    monkeypatch.setenv("FAKE_PI_IGNORE_TERM", "1")
    monkeypatch.setattr(rpc, "CLOSE_GRACE", 0.2)
    monkeypatch.setattr(rpc, "TERM_GRACE", 0.2)

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        await client.close()
        return client.alive

    assert _run(run()) is False


def test_command_timeout_is_pi_timeout(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_DELAY", "1")

    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        try:
            with pytest.raises(rpc.PiTimeout):
                await client.command({"type": "get_state"}, timeout=0.05)
        finally:
            await client.close()

    _run(run())


def test_write_on_dead_process_is_pi_error(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        client._proc.kill()
        await client._proc.wait()
        with pytest.raises(rpc.PiError):
            client._write({"type": "get_state"})
        await client.close()

    _run(run())


def test_ask_timeout_kills_the_process(fake_pi, monkeypatch):
    monkeypatch.setenv("FAKE_PI_PRINT_HANG", "1")
    monkeypatch.setattr(config, "ASK_TIMEOUT", 0.2)
    with pytest.raises(rpc.PiTimeout):
        _run(rpc.ask("在吗"))


def test_events_end_after_closed(fake_pi):
    async def run():
        client = rpc.RpcClient(rpc.LaunchSpec(thinking="off"))
        await client.start()
        client._proc.kill()
        await client._proc.wait()
        kinds = [event.kind async for event in client.events()]
        await client.close()
        return kinds

    assert _run(run())[-1] == "closed"
