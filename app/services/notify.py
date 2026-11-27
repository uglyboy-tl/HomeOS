"""通知：把一条消息推到 Gotify。要出声用 /api/speech，不在这里分流。"""

import logging

import httpx

from .. import config
from ..lib import health

log = logging.getLogger("homeos.notify")

PRIORITY = {"info": 2, "warn": 6, "error": 8}


class NotifyError(RuntimeError):
    """推不出去：Gotify 没配好、挂了、或拒绝了这条消息。"""


def push(text: str, title: str = "", level: str = "info") -> dict:
    """推一条通知；标题缺省取正文前 20 字。返回 Gotify 的消息 id。"""
    heading = (title or "").strip() or text.strip()[:20]
    # 放头里：httpx 会把带 query 的 URL 写进日志
    headers = {"X-Gotify-Key": config.GOTIFY_TOKEN}
    try:
        resp = httpx.post(
            f"{config.GOTIFY_URL.rstrip('/')}/message",
            headers=headers,
            json={"title": heading, "message": text, "priority": PRIORITY[level]},
            timeout=config.GOTIFY_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NotifyError(f"Gotify 推送失败：{e}") from e
    return {"id": (resp.json() or {}).get("id")}


def check_config() -> None:
    config.require("gotify.url", "gotify.token")


def probe() -> None:
    """Gotify 有公开的 /version，比推一条测试消息轻。"""
    resp = httpx.get(f"{config.GOTIFY_URL.rstrip('/')}/version", timeout=config.GOTIFY_TIMEOUT)
    resp.raise_for_status()


health.register("notify", check=check_config, probe=probe)
