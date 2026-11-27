"""会话状态机：每个模式一条 pi 会话，管一轮的生命周期、快照、持久化、回收与退避。

对外只说"某个模式现在是什么样"；起进程、读 yaml 都不在这里。"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator

from ... import config
from ...lib import health, shutdown
from . import modes, rpc

log = logging.getLogger("homeos.agent")

EVICT_IDLE = 600.0  # 空闲这么久就回收进程；会话已落盘，下次按 id 接上
SWEEP_EVERY = 60.0
STREAM_TICK = 0.25  # SSE 采样间隔：4 帧/秒，够"逐字"又不至于刷爆墨水屏
CRASH_COOLDOWN = 60.0  # 连续崩溃后的冷却
CRASH_WINDOW = 300.0
CRASH_LIMIT = 3


class UnknownMode(Exception):
    """请求的模式不在 config/agents.yaml 里。"""


class Busy(Exception):
    """上一轮还没结束。"""

    def __init__(self) -> None:
        super().__init__("上一轮还没结束")


class CoolingDown(Exception):
    """连续崩溃后的冷却期。"""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"连续出错，{seconds:.0f} 秒后再试")
        self.seconds = seconds


_json_cache: tuple[tuple, list[str]] = ((), [])


def _json_problems() -> list[str]:
    """两个 pi 的 json 只按 mtime 重新解析：config_problems 挂在每个 /prompt 的依赖上。"""
    global _json_cache
    paths = [config.CONFIG_DIR / name for name in ("pi-models.json", "pi-settings.json")]
    stamps = tuple(modes.stamp(path) for path in paths)
    if stamps == _json_cache[0]:
        return list(_json_cache[1])
    problems = []
    for path in paths:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"{path.name} 不可用：{e}")
    _json_cache = (stamps, problems)
    return list(problems)


def reset_cache() -> None:
    """清缓存（测试用）。"""
    global _json_cache
    _json_cache = ((), [])


def config_problems() -> list[str]:
    """静态：不碰网络、不跑子进程就能查出来的问题。"""
    problems = list(modes.catalog().problems) + _json_problems()
    if not rpc.available():
        problems.append(f"找不到 pi（{config.PI_BIN}）")
    for ext in config.PI_EXTENSIONS:
        if not ext.exists():
            problems.append(f"扩展不存在：{ext}")
    return problems


def check_config() -> None:
    problems = config_problems()
    if problems:
        raise config.NotConfigured("；".join(problems))


async def probe() -> None:
    """运行期：pi 能跑，且会话目录写得进去。"""
    if not await rpc.version():
        raise RuntimeError("pi --version 没有输出")
    config.PI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    marker = config.PI_SESSION_DIR / ".probe"
    marker.write_text("ok", encoding="utf-8")
    marker.unlink()


def _blank(mode: str) -> dict:
    return {
        "v": 2,
        "mode": mode,
        "state": "idle",
        "question": "",
        "error": None,
        "progress": {"step": 0, "tool": ""},
        "ts": 0.0,
    }


async def _query_or_empty(command, timeout: float = 2.0) -> dict:
    """/state 用：问不到就给空，不要因为这个排障接口自己挂住。"""
    try:
        async with asyncio.timeout(timeout):
            return await command()
    except (rpc.PiError, TimeoutError):
        return {}


def _friendly(error: str) -> str:
    """上游报错翻成一句人话：墨水瓶上不该出现一段英文栈。"""
    text = (error or "").strip()
    low = text.lower()
    if "no api key" in low or "no models available" in low or "unauthorized" in low:
        return "模型没授权，检查 config/pi-models.json 里的 apiKey"
    if "rate limit" in low or "429" in low:
        return "模型额度用完了，过一会儿再试"
    if "timed out" in low or "timeout" in low:
        return "这一轮超时了，再说一次"
    return text[:120] or "pi 报错了"


class Conversation:
    """一个模式的会话：一条 pi 进程 + 一轮的状态 + 落盘的那份快照。"""

    def __init__(self, mode: modes.Mode, fingerprint: str, shared: str) -> None:
        self.mode = mode
        self._fingerprint = fingerprint
        self._shared = shared
        self._state = _blank(mode.id)
        self._text = ""
        self._session_id = ""
        self._error = ""  # 本轮 pi 报的错，等 settled 再一起定性
        self._client: rpc.RpcClient | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._viewers = 0
        self._last_used = time.monotonic()
        self._started_at = 0.0
        self._stale = False
        self._cleared = False  # 用户主动新开过会话：重启后不要又把旧 <mode>.id 接回来
        self._crashes: deque[float] = deque(maxlen=CRASH_LIMIT)
        self._cooldown_until = 0.0
        self._load()

    @property
    def _path(self):
        return config.PI_SESSION_DIR / f"{self.mode.id}.json"

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> dict:
        return {**self._state, "text": self._text, "session": self._session_id}

    def apply(self, mode: modes.Mode, fingerprint: str, shared: str) -> None:
        """配置变了：记住新内容，下次用到时重建进程（同一会话 id，上下文不丢）。"""
        changed = mode != self.mode or fingerprint != self._fingerprint
        self.mode, self._fingerprint, self._shared = mode, fingerprint, shared
        if changed:
            self._stale = True
            log.info("模式 %s 的配置变了，下次使用会重启它的进程", mode.id)

    async def prompt(self, text: str) -> dict:
        async with self._lock:
            if self.busy:
                raise Busy
            self._check_cooldown()
            await self._client_or_start()
            self._text = ""
            self._error = ""
            self._state.update(
                question=text,
                state="thinking",
                error=None,
                progress={"step": 0, "tool": ""},
                ts=time.time(),
            )
            self._save()
            self._task = asyncio.create_task(self._run(text))
            return self.snapshot()

    async def abort(self) -> dict:
        async with self._lock:
            await self._stop_turn()
            if self._state["state"] == "thinking":
                self._state.update(
                    state="done" if self._text else "idle", error=None, ts=time.time()
                )
            self._last_used = time.monotonic()
            self._save()
            return self.snapshot()

    async def new_session(self) -> dict:
        async with self._lock:
            await self._stop_turn()
            client = self._client
            if client is not None and client.alive:
                if await client.new_session():
                    state = await client.state()
                    self._session_id = str(state.get("sessionId") or "")
                    self._cleared = False
                else:
                    log.info("模式 %s 的新会话被扩展取消了", self.mode.id)
            else:
                # 没进程可换：把旧 id 丢掉，下次起进程时由 pi 建新的（并记住这是有意的）
                self._session_id = ""
                self._cleared = True
            self._text = ""
            self._error = ""
            self._state.update(_blank(self.mode.id))
            self._state["ts"] = time.time()
            self._save()
            return self.snapshot()

    async def stream(self) -> AsyncIterator[dict]:
        """有变化才推一帧；关服务时靠 shutdown.stop_event 退出，不留死循环。"""
        self._viewers += 1
        try:
            last = ""
            while not shutdown.stop_event.is_set():
                snap = self.snapshot()
                key = json.dumps(snap, ensure_ascii=False, sort_keys=True)
                if key != last:
                    last = key
                    yield snap
                await asyncio.sleep(STREAM_TICK)
        finally:
            self._viewers -= 1

    async def report(self) -> dict:
        client = self._client
        alive = bool(client and client.alive)
        session: dict = {}
        stats: dict = {}
        if client is not None and alive:
            session, stats = await asyncio.gather(
                _query_or_empty(client.state),
                _query_or_empty(client.stats),
            )
        now = time.monotonic()
        return {
            "process": {
                "alive": alive,
                "pid": client.pid if client else None,
                "uptime_s": round(now - self._started_at, 1) if alive else 0.0,
                "starts": client.starts if client else 0,
                "crashes": len(self._crashes),
                "cooldown_s": max(0.0, round(self._cooldown_until - now, 1)),
            },
            "session": {
                "id": self._session_id,
                "file": str(session.get("sessionFile") or ""),
                "messages": int(session.get("messageCount") or 0),
            },
            "stats": {
                "tokens": stats.get("tokens") or {},
                "cost": stats.get("cost") or 0.0,
                "context": stats.get("contextUsage") or {},
            },
            "snapshot": self.snapshot(),
            "stderr_tail": client.stderr_tail() if client else [],
            "config": {
                "fingerprint": self._fingerprint,
                "loaded_at": os.path.getmtime(self._path) if self._path.exists() else 0.0,
                "session_dir": str(config.PI_SESSION_DIR),
            },
        }

    async def evict(self) -> None:
        """空闲就关进程；有人看着、或在跑、或刚用过，都不动。"""
        if self.busy or self._viewers or self._lock.locked():
            return
        if time.monotonic() - self._last_used < EVICT_IDLE:
            return
        client, self._client = self._client, None
        if client is not None and client.alive:
            await client.close()
            log.info("模式 %s 空闲回收；下次用会按会话 id 接上", self.mode.id)

    async def close(self) -> None:
        if self._task is not None and not self._task.done() and self._client is not None:
            with contextlib.suppress(rpc.PiError):
                await self._client.abort()  # 先让它停手，关 stdin 才不会等满优雅期
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def _run(self, text: str) -> None:
        client = self._client
        try:
            assert client is not None
            client.drain()  # 上一轮中断后可能还剩帧，不能算进这一轮
            await client.prompt(text)
            async for event in client.events():
                if event.kind == "text":
                    self._text += event.text
                elif event.kind == "tool_start":
                    step = self._state["progress"]["step"] + 1
                    self._state["progress"] = {"step": step, "tool": event.tool}
                elif event.kind == "tool_end":
                    self._state["progress"] = {"step": self._state["progress"]["step"], "tool": ""}
                elif event.kind == "retry":
                    self._error = ""  # 重新开始了：上一次的错不算数
                elif event.kind == "error":
                    self._error = event.error
                elif event.kind == "settled":
                    self._settle(aborted=event.aborted)
                    break
                elif event.kind == "closed":
                    self._on_crash(client)
                    break
        except asyncio.CancelledError:
            raise  # 状态由 abort() 决定
        except rpc.PiTimeout as e:
            self._fail("pi_timeout", _friendly(str(e)))
        except rpc.PiError as e:
            self._fail("pi_crashed", _friendly(str(e)))
        finally:
            self._task = None
            self._last_used = time.monotonic()
            self._save()

    def _settle(self, aborted: bool) -> None:
        if self._error:
            self._fail("provider_error", _friendly(self._error))
        elif aborted:
            self._state.update(state="done" if self._text else "idle", error=None)
        elif not self._text:
            self._fail("no_reply", "这次没有回复，请再说一次")
        else:
            self._state.update(state="done", error=None)
        self._state["ts"] = time.time()
        if self._state["state"] == "done":
            self._crashes.clear()
            self._cooldown_until = 0.0
            health.note("agent", True)

    def _fail(self, code: str, message: str) -> None:
        log.warning("模式 %s 出错：%s %s", self.mode.id, code, message[:200])
        if code in {"pi_crashed", "pi_timeout"}:
            health.note("agent", False, message)
        progress = {"step": self._state["progress"]["step"], "tool": ""}
        self._state.update(
            state="error", error={"code": code, "message": message}, progress=progress
        )
        self._state["ts"] = time.time()

    def _on_crash(self, client: rpc.RpcClient) -> None:
        tail = client.stderr_tail()[-5:]
        log.error(
            "模式 %s 的 pi 退出了；stderr 末尾：%s", self.mode.id, " | ".join(tail) or "（空）"
        )
        self._record_crash()
        self._fail("pi_crashed", "pi 进程退出了，再说一次试试")

    def _check_cooldown(self) -> None:
        left = self._cooldown_until - time.monotonic()
        if left > 0:
            raise CoolingDown(left)

    def _record_crash(self) -> None:
        now = time.monotonic()
        self._crashes.append(now)
        if len(self._crashes) == CRASH_LIMIT and now - self._crashes[0] <= CRASH_WINDOW:
            self._cooldown_until = now + CRASH_COOLDOWN
            log.error(
                "模式 %s 在 %.0fs 内崩了 %d 次，冷却 %.0fs",
                self.mode.id,
                CRASH_WINDOW,
                CRASH_LIMIT,
                CRASH_COOLDOWN,
            )

    async def _stop_turn(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._client is not None:
            await self._client.abort()
            self._client.drain()

    async def _client_or_start(self) -> rpc.RpcClient:
        if self._stale:
            old, self._client = self._client, None
            self._stale = False
            if old is not None:
                await old.close()
        client = self._client
        if client is not None and client.alive:
            return client
        spec = rpc.LaunchSpec(
            tools=self.mode.tools,
            prompt=self.mode.prompt,
            append=self._shared,
            thinking=self.mode.thinking,
            session_id=self._session_id,
        )
        client = rpc.RpcClient(spec)
        try:
            await client.start()
            state = await client.state()
        except (OSError, rpc.PiError) as e:
            await client.close()
            raise rpc.PiUnavailable(f"起 pi 失败：{_friendly(str(e))}") from e
        session_id = str(state.get("sessionId") or "")
        if session_id and session_id != self._session_id:
            self._session_id = session_id
            self._cleared = False
            self._save()
        self._client = client
        self._started_at = time.monotonic()
        return client

    def _load(self) -> None:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        if isinstance(doc, dict) and doc.get("v") == 2:
            self._restore(doc)
        if self._state["state"] == "thinking":
            # 上次的进程已经不在了，那一轮只能算被打断
            self._state.update(
                state="error", error={"code": "interrupted", "message": "上次的回复被打断了"}
            )
            self._save()
        if not self._session_id and not self._cleared:
            self._adopt_legacy_session()

    def _restore(self, doc: dict) -> None:
        """派生数据可能是人手改的或旧版写的：逐字段认，类型不对就用默认值，不让它变成 TypeError。"""
        if doc.get("state") in {"idle", "thinking", "done", "error"}:
            self._state["state"] = doc["state"]
        if isinstance(doc.get("question"), str):
            self._state["question"] = doc["question"]
        error = doc.get("error")
        if error is None or (isinstance(error, dict) and isinstance(error.get("code"), str)):
            self._state["error"] = error
        progress = doc.get("progress")
        if isinstance(progress, dict) and isinstance(progress.get("step"), int):
            tool = str(progress.get("tool") or "")
            self._state["progress"] = {"step": progress["step"], "tool": tool}
        if isinstance(doc.get("ts"), (int, float)):
            self._state["ts"] = float(doc["ts"])
        if isinstance(doc.get("text"), str):
            self._text = doc["text"]
        if isinstance(doc.get("session"), str):
            self._session_id = doc["session"]
        if isinstance(doc.get("cleared"), bool):
            self._cleared = doc["cleared"]

    def _adopt_legacy_session(self) -> None:
        """旧版把 id 写在 <mode>.id 里；沿用一次，让升级前的那段对话不断。"""
        legacy = config.PI_SESSION_DIR / f"{self.mode.id}.id"
        try:
            value = legacy.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if value:
            self._session_id = value
            self._save()
            log.info("模式 %s 沿用旧会话 id %s", self.mode.id, value)

    def _save(self) -> None:
        payload = {
            **self._state,
            "text": self._text,
            "session": self._session_id,
            "cleared": self._cleared,
        }
        tmp = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:
            log.warning("存 %s 失败：%s", self._path, e)


class Runtime:
    """所有模式的集合：按模式取会话、巡检回收、关停。"""

    def __init__(self) -> None:
        self._version = ""
        self._conversations: dict[str, Conversation] = {}
        self._sweeper: asyncio.Task | None = None

    async def startup(self) -> None:
        self._version = await rpc.version() if rpc.available() else ""
        if self._sweeper is None:
            self._sweeper = asyncio.create_task(self._sweep())

    async def aclose(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None
        conversations = list(self._conversations.values())
        self._conversations.clear()
        await asyncio.gather(*(c.close() for c in conversations), return_exceptions=True)

    def info(self) -> dict:
        catalog = modes.catalog()
        settings = modes.settings()
        return {
            "engine": self._engine(),
            "model": {
                "provider": settings["provider"],
                "id": settings["model"],
                "thinking": settings["thinking"],
            },
            "modes": [
                {
                    "id": mode.id,
                    "label": mode.label,
                    "hint": mode.hint,
                    "tools": list(mode.tools),
                    "thinking": mode.thinking,
                }
                for mode in catalog.modes.values()
            ],
            "default_mode": next(iter(catalog.modes), ""),
        }

    def snapshot(self, mode: str) -> dict:
        return self._conversation(mode).snapshot()

    async def prompt(self, mode: str, text: str) -> dict:
        return await self._conversation(mode).prompt(text)

    async def abort(self, mode: str) -> dict:
        return await self._conversation(mode).abort()

    async def new_session(self, mode: str) -> dict:
        return await self._conversation(mode).new_session()

    async def state(self, mode: str) -> dict:
        conversation = self._conversation(mode)
        report = await conversation.report()
        return {"mode": mode, "engine": self._engine(), **report}

    def stream(self, mode: str) -> AsyncIterator[dict]:
        return self._conversation(mode).stream()

    async def ask(self, text: str) -> str:
        try:
            return await rpc.ask(text)
        except rpc.PiError as e:
            raise rpc.PiError(_friendly(str(e))) from e

    def _engine(self) -> dict:
        """`available` 只说配置能不能用；`last_error` 是上一次运行失败的原因（可重试）。"""
        problems = config_problems()
        status = health.get("agent")
        return {
            "name": "pi",
            "available": not problems,
            "version": self._version,
            "problems": problems,
            "last_error": "" if status["ok"] else status["message"],
        }

    def _conversation(self, mode: str) -> Conversation:
        catalog = modes.catalog()
        if mode not in catalog.modes:
            raise UnknownMode(mode)
        conversation = self._conversations.get(mode)
        if conversation is None:
            conversation = Conversation(catalog.modes[mode], catalog.fingerprint, catalog.shared)
            self._conversations[mode] = conversation
        else:
            conversation.apply(catalog.modes[mode], catalog.fingerprint, catalog.shared)
        return conversation

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_EVERY)
            for conversation in list(self._conversations.values()):
                with contextlib.suppress(Exception):
                    await conversation.evict()


runtime = Runtime()

# 单次崩溃不代表上游长期不可用：只报告状态，不挡下一次请求（连续崩溃有 CRASH_COOLDOWN）
health.register("agent", check=check_config, probe=probe, cooldown=0)
