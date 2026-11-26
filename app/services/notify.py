"""通知：一条消息按通道分发（gotify 推送 / 音箱播报）。

通道各自成败，一个失败不影响另一个；调用方拿到逐通道结果自己判断。
"""

import logging

import httpx

from .. import config
from . import mijia

log = logging.getLogger("homeos.notify")

PRIORITY = {"info": 2, "warn": 6, "error": 8}


def _gotify(text: str, title: str, level: str) -> dict:
    resp = httpx.post(
        f"{config.GOTIFY_URL.rstrip('/')}/message",
        headers={"X-Gotify-Key": config.GOTIFY_TOKEN},  # 放头里：httpx 会把带 query 的 URL 写进日志
        json={"title": title, "message": text, "priority": PRIORITY[level]},
        timeout=config.GOTIFY_TIMEOUT,
    )
    resp.raise_for_status()
    return {"id": (resp.json() or {}).get("id")}


def _speak(text: str, title: str, level: str) -> dict:
    return {"text": mijia.say(text)["text"]}


HANDLERS = {"gotify": _gotify, "speak": _speak}


def send(
    text: str, title: str = "", level: str = "info", channels: tuple[str, ...] = ("gotify",)
) -> dict:
    """逐通道发送，返回 {通道: {ok, ...}}。标题缺省取正文前 20 字。"""
    heading = (title or "").strip() or text.strip()[:20]
    results: dict[str, dict] = {}
    for name in channels:
        try:
            results[name] = {"ok": True, **HANDLERS[name](text, heading, level)}
        except Exception as e:  # noqa: BLE001 — 每个通道的失败种类不可枚举，且不该互相拖累
            log.warning("通知通道 %s 失败：%s", name, e)
            results[name] = {"ok": False, "error": str(e)[:120]}
    return results
