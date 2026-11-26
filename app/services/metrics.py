"""NAS 本机指标：从 Beszel 取最新一份 1 分钟采样，压成页面要的几个数。

只读账号登录一次，会话 token 留在进程内；401 说明过期，重登一次。
"""

import logging

import httpx

from .. import config

log = logging.getLogger("homeos.metrics")

_TOKEN = {"value": ""}
_LATEST = {"perPage": 1, "sort": "-created", "filter": "type='1m'"}


class BeszelError(RuntimeError):
    pass


def _login(c: httpx.Client) -> str:
    r = c.post(
        "/api/collections/users/auth-with-password",
        json={"identity": config.BESZEL_USER, "password": config.BESZEL_PASSWORD},
    )
    if r.status_code != 200:
        raise BeszelError(f"Beszel 登录失败（{r.status_code}）")
    return r.json()["token"]


def _get(c: httpx.Client, path: str, params: dict | None = None) -> dict:
    if not _TOKEN["value"]:
        _TOKEN["value"] = _login(c)
    r = c.get(path, params=params, headers={"Authorization": f"Bearer {_TOKEN['value']}"})
    if r.status_code == 401:
        _TOKEN["value"] = _login(c)
        r = c.get(path, params=params, headers={"Authorization": f"Bearer {_TOKEN['value']}"})
    if r.status_code != 200:
        raise BeszelError(f"Beszel 返回 {r.status_code}")
    return r.json()


def snapshot() -> dict:
    try:
        with httpx.Client(base_url=config.BESZEL_URL, timeout=config.BESZEL_TIMEOUT) as c:
            latest = _get(c, "/api/collections/system_stats/records", _LATEST)["items"]
            systems = _get(c, "/api/collections/systems/records")["items"]
    except httpx.HTTPError as e:
        raise BeszelError(f"Beszel 连不上：{type(e).__name__}") from e
    if not latest:
        raise BeszelError("Beszel 没有采样数据")
    st = latest[0]["stats"]
    ni = st.get("ni") or {}
    iface = next(iter(ni), "")
    rx, tx = (ni[iface][1], ni[iface][0]) if iface else (0, 0)  # 每项是 [发出速率, 接收速率, 累计…]
    return {
        "cpu": st.get("cpu"),
        "cores": len(st.get("cpus") or []),
        "load": st.get("la") or [],
        "mem": {"pct": st.get("mp"), "used": st.get("mu"), "total": st.get("m")},
        "temp": (st.get("t") or {}).get("cpu_thermal"),
        "disk": {"pct": st.get("dp"), "used": st.get("du"), "total": st.get("d")},
        "net": {"iface": iface, "rx": rx, "tx": tx},
        "uptime": (systems[0].get("info") or {}).get("u") if systems else None,
    }
