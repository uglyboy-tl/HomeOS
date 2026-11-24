"""助手：页面模式 → opencode agent 的薄门面。

只做四件事：分模式建会话、把话发出去、把本轮状态（含增量文本）攒起来、把 opencode 的
错误翻成人话。历史永远从 opencode 读，这里不存消息、不落盘。

两个数据来源，各管一段：
- SSE 事件：正文增量（唯一能逐字上屏的来源，消息要等步骤结束才落库）；
- 消息列表：权威结果（结束时校对一次，SSE 掉线时也能还原）。

前端怎么拿由它决定：页面走 /api/agent/stream（限流 4 次/秒）或轮询，测试台走 /ask 同步接口。
"""

import asyncio
import json
import logging
import time

import httpx

from . import opencode

log = logging.getLogger("homeos.agent")

# 页面上的三个模式 → opencode 的 agent 定义（opencode/agents/*.md）
MODES = {"chat": "chat", "design": "pages", "device": "device"}

RESYNC_EVERY = 1.0  # 秒：对账频率上限，别让每个 token 都去问一次 opencode
EMPTY_GRACE = 5.0  # 秒：done 了但正文还没落库时，先当"还在工作"等一会儿
STREAM_TICK = 0.25  # 秒：SSE 推送上限（4 次/秒，够"逐字"又不至于刷爆墨水屏）


class UnknownMode(Exception):
    """页面传了不认识的模式。"""


class AgentError(Exception):
    """opencode 那层不可用（没起、没授权、上游报错）。"""


def _blank(agent: str | None = None) -> dict:
    """一个模式的本轮状态。所有键都在这里，别在别处拼半个字典。"""
    return {
        "session_id": None,
        "agent": agent,  # 覆盖 MODES 里的默认 agent（测试用）
        "user_msg_id": "",  # 本轮那句话的 id：判断"我的消息落库没有"
        "reply_msg_id": "",  # 正在生成的 assistant：增量文本按它归并
        "question": "",
        "text": "",
        "state": "idle",  # idle / thinking / done / error
        "error": "",
        "dirty": False,  # 有事件来过，还没对账
        "synced_at": 0.0,
        "empty_since": 0.0,  # done 但正文还没落库的起点，见 turn()
    }


_state: dict[str, dict] = {mode: _blank() for mode in MODES}


# ---------- opencode HTTP ----------


async def _call(method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, opencode.url(path), json=body, headers=opencode.headers()
            )
    except httpx.TimeoutException as e:
        raise AgentError(f"opencode 没在 {timeout:g}s 内回话") from e
    except httpx.HTTPError as e:
        raise AgentError(f"opencode 连不上：{e}") from e
    if resp.status_code >= 400:
        raise AgentError(_error_text(resp.text) or f"opencode 返回 {resp.status_code}")
    try:
        return resp.json() if resp.content else {}
    except ValueError as e:  # 反代页面、错误页都可能混进 200 里
        raise AgentError(f"opencode 回了不是 JSON：{e}") from e


def _error_text(raw: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:120]
    return str(data.get("message") or data.get("_tag") or "")[:120]


def human_error(message: str) -> str:
    """把 opencode / provider 的错误翻成一句人话。"""
    low = (message or "").lower()
    if not low:
        return "助手出错了，稍后再试"
    if "requires an api key" in low or "provider.auth" in low or "unauthorized" in low:
        return "模型没授权，检查 .env 里的 key"
    if "no-route" in low or "no such model" in low or "model not found" in low:
        return "这个模型不存在或没配好，检查模型名"
    if "usage limit" in low or "rate limit" in low or "too many requests" in low:
        return "模型额度用完了或太频繁，过会儿再试"
    if "context" in low and ("long" in low or "length" in low or "exceed" in low):
        return "这次聊太长了，点「新对话」重开"
    return "助手出错了：" + message[:80]


# ---------- 消息列表 → 本轮结果 ----------


def _ordered(messages: list[dict]) -> list[dict]:
    """opencode 的 /message 是最新在前；这里统一成时间正序。"""
    return sorted(
        messages, key=lambda m: ((m.get("time") or {}).get("created") or 0, m.get("id") or "")
    )


def _text(message: dict) -> str:
    """用户那句存在消息顶层的 text，assistant 的正文在 content parts 里。"""
    if message.get("text"):
        return message["text"]
    return "".join(
        p.get("text", "") for p in (message.get("content") or []) if p.get("type") == "text"
    )


def summarize(messages: list[dict], after: str = "") -> dict | None:
    """还原「最近一轮」。给了 after 就只认那条用户消息；它还没落库时返回 None。"""
    ordered = _ordered(messages or [])
    last_user = None
    for i, m in enumerate(ordered):
        if m.get("type") == "user" and (not after or m.get("id") == after):
            last_user = i
    if last_user is None:
        return None if after else {"question": "", "text": "", "done": False, "error": ""}

    text, done, error = "", False, ""
    for m in ordered[last_user + 1 :]:
        if m.get("type") != "assistant":
            continue
        text = _text(m)
        done = bool((m.get("time") or {}).get("completed"))
        err = m.get("error") or {}
        finish = m.get("finish") or m.get("rawFinish") or ""
        if err:
            error = human_error(str(err.get("message") or err.get("type") or ""))
        elif finish and finish not in ("stop", "tool-calls"):
            error = human_error("")  # 没有 error 对象但收尾异常：别当成"还在跑"
    return {"question": _text(ordered[last_user]), "text": text, "done": done, "error": error}


# ---------- 会话与本轮状态 ----------


def check(mode: str) -> dict:
    try:
        return _state[mode]
    except KeyError:
        raise UnknownMode(mode) from None


async def _create_session(mode: str) -> str:
    st = _state[mode]
    data = await _call("POST", "/api/session", {"agent": st["agent"] or MODES[mode]})
    sid = (data.get("data") or {}).get("id")
    if not sid:
        raise AgentError("建会话失败")
    st["session_id"] = sid
    return sid


async def ensure_session(mode: str) -> str:
    """会话没了（容器重启、被删）就重建；不按时间自动淘汰——页面上点「新对话」才换。"""
    st = check(mode)
    if st["session_id"]:
        try:
            await _call("GET", f"/api/session/{st['session_id']}")
            return st["session_id"]
        except AgentError:
            st["session_id"] = None
    return await _create_session(mode)


def _snapshot(mode: str) -> dict:
    st = _state[mode]
    return {k: st[k] for k in ("state", "question", "text", "error")} | {"mode": mode}


async def say(mode: str, text: str) -> dict:
    sid = await ensure_session(mode)
    st = _state[mode]
    st.update(question=text, text="", state="thinking", error="", dirty=True, reply_msg_id="")
    try:
        sent = await _call("POST", f"/api/session/{sid}/prompt", {"text": text})
        st["user_msg_id"] = (sent.get("data") or {}).get("id") or ""
    except AgentError as e:
        st.update(state="error", error=human_error(str(e)))
        raise
    # 先回"我记下了"：消息可能还没落库，这时候读消息会读成上一轮
    return _snapshot(mode)


async def new_conversation(mode: str) -> dict:
    """点「新对话」：丢会话、清本轮状态。旧对话仍在 opencode 里，只是不再用它。"""
    st = check(mode)  # 原地清空：正在 await 的旧帧拿的还是同一个字典，别换成孤儿
    st.clear()
    st.update(_blank())
    await _create_session(mode)
    return _snapshot(mode)


async def resync(mode: str) -> None:
    """按消息列表核对本轮：结束时给权威结果，SSE 掉线时也能还原。"""
    st = _state[mode]
    if not st["session_id"]:
        return
    st["synced_at"] = time.monotonic()  # 先记：失败也按同一频率上限重试，别每帧都打
    try:
        data = await _call("GET", f"/api/session/{st['session_id']}/message")
    except AgentError:
        return
    st["dirty"] = False
    summary = summarize(data.get("data") or [], st["user_msg_id"])
    if summary is None:
        return  # 自己那句还没落库：保持现状，下次再问
    if not summary["question"]:
        st.update(text="", state="idle", error="")
        return
    st["question"] = summary["question"]
    if summary["text"]:
        st["text"] = summary["text"]  # 落库了：以消息为准（增量可能有缺字）
    st["error"] = summary["error"]
    st["state"] = "error" if summary["error"] else "done" if summary["done"] else "thinking"
    if st["state"] == "thinking":
        st["empty_since"] = 0.0


async def turn(mode: str) -> dict:
    """取本轮状态；对账有频率上限，别让每个 token 都去问一次 opencode。"""
    st = check(mode)
    due = time.monotonic() - st["synced_at"] > RESYNC_EVERY
    if due and (st["dirty"] or st["state"] == "thinking"):
        await resync(mode)

    now = time.monotonic()
    # 「已完成但还没正文」：消息先落库、正文随后才补齐（实测会出一帧空 done）。
    # 宽限期内当还在工作；过期仍没有就转成错误——前端只在"有正文的 done"或 error 上收尾，
    # 一直挂在 done+空正文会让发送按钮永久卡住。
    if st["state"] == "done" and not st["text"] and not st["error"]:
        st["empty_since"] = st["empty_since"] or now
        if now - st["empty_since"] < EMPTY_GRACE:
            st["state"] = "thinking"
        else:
            st.update(state="error", error="这次没有回复，请再说一次")
    else:
        st["empty_since"] = 0.0
    return _snapshot(mode)


# ---------- opencode SSE：增量文本的唯一来源 ----------


def _handle(event: dict) -> None:
    kind = event.get("type") or ""
    data = event.get("data") or {}
    sid = data.get("sessionID")
    mode = next((m for m, st in _state.items() if sid and st["session_id"] == sid), None)
    if mode is None:
        return
    st = _state[mode]
    if kind == "session.text.delta":
        mid = data.get("assistantMessageID") or ""
        if mid and mid != st["reply_msg_id"]:
            st["reply_msg_id"] = mid
            st["text"] = ""  # 换了一条新消息：从头攒
        st["text"] += data.get("delta") or ""
        st.update(state="thinking", dirty=True)
    elif kind in ("session.step.failed", "session.execution.failed"):
        err = data.get("error") or {}
        st.update(state="error", error=human_error(str(err.get("message") or "")), dirty=True)
    elif kind == "session.step.ended":
        st["dirty"] = True  # 结束与否以消息列表为准（这里读不到正文落库没有）


async def _consume_events(stop: asyncio.Event) -> None:
    async with (
        httpx.AsyncClient(timeout=None, headers=opencode.headers()) as client,
        client.stream("GET", opencode.url("/api/event")) as resp,
    ):
        resp.raise_for_status()
        log.info("opencode 事件流已连接")
        async for line in resp.aiter_lines():
            if stop.is_set():
                return
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                _handle(json.loads(payload))
            except Exception as e:  # noqa: BLE001 — 帧坏了只丢这一帧，不能让整条流断掉
                log.warning("事件帧处理失败，跳过：%s", e)


async def run_subscriber(stop: asyncio.Event) -> None:
    """常驻任务：断线退避重连，重连后把各模式对一次账。"""
    backoff = 0.5
    while not stop.is_set():
        try:
            await _consume_events(stop)
            backoff = 0.5
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 事件流断法太多，一律重连
            log.warning("事件流断开：%s", e)
        for mode in MODES:
            await resync(mode)
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except TimeoutError:
            backoff = min(backoff * 2, 8)


# ---------- 给前端：SSE 与同步提问 ----------


async def stream(mode: str):
    """按变化推本轮状态；没变化就不发（避免无意义的整屏刷新）。"""
    check(mode)
    last = ""
    while True:
        payload = json.dumps(await turn(mode), ensure_ascii=False)
        if payload != last:
            last = payload
            yield payload
        await asyncio.sleep(STREAM_TICK)


async def ask(mode: str, text: str, timeout: float = 90.0) -> dict:
    """同步问一句：等它跑完再返回，带耗时/token/模型，脚本里直接下判断。"""
    started = time.monotonic()
    await say(mode, text)
    sid = _state[mode]["session_id"]
    try:
        await _call("POST", f"/api/experimental/session/{sid}/wait", timeout=timeout)
    except AgentError as e:
        return _snapshot(mode) | {"error": human_error(str(e)), "state": "error"}
    await resync(mode)
    rows = await trace(mode, limit=4)
    last = next((r for r in reversed(rows) if r["type"] == "assistant"), None)
    snapshot = _snapshot(mode)
    if last is None:
        # 等完了却一条 assistant 都没有：多半模型 id/授权不对，opencode 把这一轮直接毙了
        snapshot.update(state="error", error="没有拿到回复，检查模型配置")
    return snapshot | {
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "model": (last or {}).get("model"),
        "tokens": (last or {}).get("tokens"),
        "cost": (last or {}).get("cost"),
    }


# ---------- 测试台：验证 opencode 配置用 ----------


def compact(messages: list[dict], limit: int = 20) -> list[dict]:
    """一行一条的时间正序轨迹：核对 agent / 模型 / 工具 / 错误 / token 都靠它。"""
    rows = []
    for m in _ordered(messages or [])[-limit:]:
        row = {"type": m.get("type"), "at": (m.get("time") or {}).get("created")}
        row["text"] = _text(m)
        if m.get("type") == "assistant":
            row["done"] = bool((m.get("time") or {}).get("completed"))
            for key in ("finish", "tokens", "cost"):
                if m.get(key) is not None:
                    row[key] = m[key]
            model = m.get("model") or {}
            if model:
                row["model"] = f"{model.get('providerID')}/{model.get('id')}#{model.get('variant')}"
            err = m.get("error") or {}
            if err:
                row["error"] = human_error(str(err.get("message") or err.get("type") or ""))
                row["error_raw"] = err.get("type")
        rows.append(row)
    return rows


async def trace(mode: str, limit: int = 20) -> list[dict]:
    sid = await ensure_session(mode)
    data = await _call("GET", f"/api/session/{sid}/message")
    return compact(data.get("data") or [], limit)


async def info(mode: str) -> dict:
    """看这个模式绑着谁、用哪个模型。"""
    st = check(mode)
    out = {"mode": mode, "agent": st["agent"] or MODES[mode], "session": st["session_id"]}
    if not st["session_id"]:
        return out | {"model": None}
    try:
        body = (await _call("GET", f"/api/session/{st['session_id']}")).get("data") or {}
    except AgentError:
        return out | {"session": None, "model": None}
    return out | {"agent": body.get("agent") or out["agent"], "model": body.get("model")}


async def bind_agent(mode: str, agent: str) -> dict:
    """把某模式临时换成别的 agent（重建会话），用于验证不同 agent 的配置。"""
    st = check(mode)  # 同 new_conversation：原地清，别让在飞的 turn() 写进孤儿字典
    st.clear()
    st.update(_blank(agent))
    await _create_session(mode)
    return await info(mode)


async def set_model(mode: str, provider: str, model_id: str, variant: str = "") -> dict:
    """给当前会话换模型；opencode 只在会话级别记这个。"""
    sid = await ensure_session(mode)
    spec = {"providerID": provider, "id": model_id} | ({"variant": variant} if variant else {})
    await _call("POST", f"/api/session/{sid}/model", {"model": spec})
    return await info(mode)
