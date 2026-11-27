"""pi 的 RPC 客户端：一条常驻子进程 + stdin/stdout 上的 JSONL。

只管"怎么跟 pi 说话"：起进程、按 id 对应答、把事件归一成 Event、收 stderr。"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass

from ... import config

log = logging.getLogger("homeos.pi")

STDERR_LINES = 40  # 环形缓冲：够看清一次崩溃，又不至于灌满日志
# 单行事件会带工具输出（读大文件时远超 64KiB 的默认上限），超了 readline 会抛异常
READ_LIMIT = 8 << 20
COMMAND_TIMEOUT = 30.0
ABORT_TIMEOUT = 20.0
CLOSE_GRACE = 5.0
TERM_GRACE = 2.0


class PiError(Exception):
    """pi 不可用：进程死了、协议不对、命令被拒。"""


class PiTimeout(PiError):
    """命令没在给定时间内回应。"""


class PiUnavailable(PiError):
    """起不来：找不到二进制，或扩展路径不对。"""


def available() -> bool:
    return shutil.which(config.PI_BIN) is not None


_version: str | None = None


async def version() -> str:
    """`pi --version`（缓存）：排障要看它，但不值得每次请求都跑一遍。"""
    global _version
    if _version is not None:
        return _version
    try:
        proc = await asyncio.create_subprocess_exec(
            config.PI_BIN,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(5.0):
            out, _ = await proc.communicate()
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        log.warning("pi --version 超时：%s", e)
        return ""
    except OSError as e:
        log.warning("pi --version 失败：%s", e)
        return ""
    _version = out.decode(errors="replace").strip()
    return _version


def extension_args() -> list[str]:
    return [arg for ext in config.PI_EXTENSIONS for arg in ("--extension", str(ext))]


def spawn_env() -> dict:
    """pi 会往家目录写运行时文件；agent 目录与 HOME 都收到我们自己的可写区。"""
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = str(config.PI_AGENT_DIR)
    env["HOME"] = str(config.PI_AGENT_DIR)
    return env


@dataclass(frozen=True)
class Event:
    """pi 的原始事件里，状态机只关心这几种。"""

    kind: str  # text / tool_start / tool_end / settled / error / closed
    text: str = ""
    tool: str = ""
    error: str = ""
    aborted: bool = False


def normalize(msg: dict) -> Event | None:
    kind = msg.get("type")
    if kind == "message_update":
        update = msg.get("assistantMessageEvent") or {}
        if update.get("type") == "text_delta" and update.get("delta"):
            return Event("text", text=str(update["delta"]))
        if update.get("type") == "toolcall_start":
            return Event("tool_start", tool=str(update.get("toolName") or ""))
        return None
    if kind == "tool_execution_start":
        return Event("tool_start", tool=str(msg.get("toolName") or ""))
    if kind == "tool_execution_end":
        return Event("tool_end", tool=str(msg.get("toolName") or ""))
    if kind == "agent_settled":
        return Event("settled", aborted=bool(msg.get("aborted")))
    if kind == "auto_retry_end" and not msg.get("success"):
        return Event("error", error=str(msg.get("finalError") or "模型请求失败"))
    if kind in ("agent_error", "error"):
        detail = msg.get("message") or msg.get("error") or "pi 报错"
        return Event("error", error=str(detail)[:300])
    return None  # 压缩、重试开始、队列变化：状态机不需要


@dataclass(frozen=True)
class LaunchSpec:
    """启动一条会话的全部差异项；扩展、cwd、env 由客户端统一加。"""

    tools: tuple[str, ...] = ()
    prompt: str = ""
    append: str = ""
    thinking: str = "off"
    session_id: str = ""

    def argv(self) -> list[str]:
        argv = [config.PI_BIN, "--mode", "rpc", *extension_args()]
        argv += ["--session-dir", str(config.PI_SESSION_DIR)]
        if self.session_id:
            argv += ["--session-id", self.session_id]
        argv += ["--tools", ",".join(self.tools)] if self.tools else ["--no-tools"]
        if self.prompt:
            argv += ["--system-prompt", self.prompt]
        if self.append:
            argv += ["--append-system-prompt", self.append]
        if self.thinking:
            argv += ["--thinking", self.thinking]
        return argv


class RpcClient:
    """一条 pi 子进程：发命令、读事件、收 stderr、优雅关闭。"""

    def __init__(self, spec: LaunchSpec) -> None:
        self._spec = spec
        self._proc: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task] = []
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future] = {}
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._seq = 0
        self.starts = 0

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self.alive and self._proc else None

    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    async def start(self) -> None:
        if self.alive:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self._spec.argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=READ_LIMIT,
            cwd=str(config.WEB_ROOT),
            env=spawn_env(),
        )
        self.starts += 1
        self._readers = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]

    async def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while line := await proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("pi 回了非 JSON 行：%r", line[:120])
                    continue
                if msg.get("type") == "response":
                    future = self._pending.pop(str(msg.get("id")), None)
                    if future and not future.done():
                        future.set_result(msg)
                elif msg.get("type") == "extension_ui_request":
                    # 无人值守：交互请求一律取消，否则 pi 会一直等
                    self._write(
                        {"type": "extension_ui_response", "id": msg.get("id"), "cancelled": True}
                    )
                elif msg.get("type") == "extension_error":
                    log.warning("扩展报错：%s", str(msg.get("error"))[:200])
                else:
                    event = normalize(msg)
                    if event is not None:
                        await self._events.put(event)
        except Exception as e:  # noqa: BLE001 — 读循环不能静默死掉，否则这一轮永远不结束
            log.error("读 pi 的输出失败：%s（%s）", e, self._spec.session_id or "新会话")
        finally:
            # 进程没了：等着回应的命令立刻失败，别让人干等到超时
            self._fail_pending("pi 退出了" + self._tail_suffix())
            await self._events.put(Event("closed"))

    def _tail_suffix(self) -> str:
        tail = " | ".join(list(self._stderr)[-3:])
        return f"；stderr：{tail}" if tail else ""

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(PiError(message))
        self._pending.clear()

    async def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while line := await proc.stderr.readline():
            text = line.decode(errors="replace").rstrip()
            if text:
                self._stderr.append(text)
                log.debug("pi stderr: %s", text)

    def _write(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise PiError("pi 不在运行")
        if proc.stdin.is_closing():
            raise PiError("pi 的输入已经关了")
        proc.stdin.write((json.dumps(payload) + "\n").encode())

    async def command(self, payload: dict, timeout: float = COMMAND_TIMEOUT) -> dict:
        await self.start()
        self._seq += 1
        req_id = str(self._seq)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        self._write({"id": req_id, **payload})
        try:
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError as e:
            raise PiTimeout(f"pi 没在 {timeout:g}s 内回应 {payload.get('type')}") from e
        finally:
            self._pending.pop(req_id, None)

    async def prompt(self, text: str) -> None:
        resp = await self.command({"type": "prompt", "message": text})
        if not resp.get("success", True):
            raise PiError(str(resp.get("error") or "pi 拒绝了这轮对话")[:300])

    async def abort(self) -> None:
        """停掉正在跑的一轮；pi 回到 idle 才回应，所以这里等它。"""
        if not self.alive:
            return
        with contextlib.suppress(PiError):
            await self.command({"type": "abort"}, timeout=ABORT_TIMEOUT)

    async def new_session(self) -> bool:
        """进程内开新会话；扩展可以取消。返回是否真的换了。"""
        resp = await self.command({"type": "new_session"})
        return not bool((resp.get("data") or {}).get("cancelled"))

    async def state(self) -> dict:
        return dict((await self.command({"type": "get_state"})).get("data") or {})

    async def stats(self) -> dict:
        return dict((await self.command({"type": "get_session_stats"})).get("data") or {})

    async def events(self):
        """按 pi 的输出顺序往外发；进程退出时补一帧 closed 后结束。"""
        while True:
            event = await self._events.get()
            yield event
            if event.kind == "closed":
                return

    def drain(self) -> None:
        """丢掉队列里残留的帧：上一轮被中断后它们已经没用了。"""
        while not self._events.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._events.get_nowait()

    def kill(self) -> None:
        for task in self._readers:
            task.cancel()

        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            proc.kill()
        # 读任务被取消后管道不会自己 EOF：显式关掉，否则进程重启会漏 fd
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()

    async def close(self) -> None:
        """优雅关：关 stdin 让 pi 自己收尾，超时才升级到 TERM / KILL。"""
        tasks = list(self._readers)
        proc = self._proc
        if proc is None or proc.returncode is not None:
            self.kill()
            await self._reap(tasks)
            return
        if proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.close()
        try:
            async with asyncio.timeout(CLOSE_GRACE):
                await proc.wait()
        except TimeoutError:
            log.info("pi 没在 %.0fs 内退出，改为 TERM", CLOSE_GRACE)
            proc.terminate()
            try:
                async with asyncio.timeout(TERM_GRACE):
                    await proc.wait()
            except TimeoutError:
                proc.kill()
                await proc.wait()
        self.kill()
        await self._reap(tasks)

    async def _reap(self, tasks: list) -> None:
        self._readers.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def ask(text: str) -> str:
    """一次性问答：跑完即退、不落会话、不给工具。程序侧不能借它绕过页面的护栏。"""
    argv = [config.PI_BIN, *extension_args(), "--no-tools", "--print", "--no-session", "--", text]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(config.WEB_ROOT),
        env=spawn_env(),
    )
    try:
        async with asyncio.timeout(config.ASK_TIMEOUT):
            out, err = await proc.communicate()
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise PiTimeout(f"pi 没在 {config.ASK_TIMEOUT:g}s 内跑完") from e
    if proc.returncode != 0:
        detail = err.decode(errors="replace").strip() or f"pi 退出码 {proc.returncode}"
        raise PiError(detail[-300:])
    return out.decode(errors="replace").strip()
