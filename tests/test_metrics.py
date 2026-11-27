"""硬件指标：字段映射、token 过期重登、Beszel 不可用时的错误。"""

import httpx
import pytest

from app import config
from app.services import metrics

# Beszel 的 `system_stats.stats` 字段长这样（缩写就是上游的名字，别“翻译”）
SAMPLE = {
    "cpu": 14.42,
    "cpus": [10, 20, 30, 40],
    "la": [0.84, 0.8, 0.74],
    "m": 7.65,
    "mu": 4.49,
    "mp": 58.77,
    "d": 97.87,
    "du": 22.42,
    "dp": 24.15,
    "t": {"cpu_thermal": 45.28},
    "ni": {"eth0": [20640, 5955, 130196064581, 123370396546]},
}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeBeszel:
    """按脚本回包；脚本项是 (status, payload)，或要抛的异常。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def _next(self, call):
        self.calls.append(call)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(*item)

    def post(self, path, json=None):
        return self._next(("POST", path))

    def get(self, path, params=None, headers=None):
        return self._next(("GET", path, headers))


@pytest.fixture
def beszel(monkeypatch):
    monkeypatch.setattr(config, "BESZEL_USER", "homeos@local")
    monkeypatch.setattr(config, "BESZEL_PASSWORD", "pw")
    metrics._TOKEN["value"] = ""
    made = {}

    def install(*script):
        made["client"] = FakeBeszel(script)
        monkeypatch.setattr(metrics.httpx, "Client", lambda **_: made["client"])
        return made["client"]

    return install


def test_maps_latest_sample(beszel):
    c = beszel(
        (200, {"token": "t1"}),
        (200, {"items": [{"stats": SAMPLE}]}),
        (200, {"items": [{"info": {"u": 2675579}}]}),
    )
    got = metrics.snapshot()
    assert got == {
        "cpu": 14.42,
        "cores": 4,
        "load": [0.84, 0.8, 0.74],
        "mem": {"pct": 58.77, "used": 4.49, "total": 7.65},
        "temp": 45.28,
        "disk": {"pct": 24.15, "used": 22.42, "total": 97.87},
        "net": {"iface": "eth0", "rx": 5955, "tx": 20640},
        "uptime": 2675579,
    }
    assert c.calls[1][2]["Authorization"] == "Bearer t1"


def test_relogins_once_when_token_expired(beszel):
    c = beszel(
        (200, {"token": "t1"}),
        (401, {}),
        (200, {"token": "t2"}),
        (200, {"items": [{"stats": SAMPLE}]}),
        (200, {"items": [{"info": {"u": 1}}]}),
    )
    assert metrics.snapshot()["cpu"] == 14.42
    assert c.calls[-1][2]["Authorization"] == "Bearer t2"


def test_unreachable_beszel_is_a_beszel_error(beszel):
    beszel(httpx.ConnectError("boom"))
    with pytest.raises(metrics.BeszelError, match="连不上"):
        metrics.snapshot()


def test_route_needs_beszel_credentials(monkeypatch):
    """缺配置在路由依赖上就被挡掉，不用等到连 Beszel。"""
    monkeypatch.setattr(config, "BESZEL_USER", "")
    with pytest.raises(config.NotConfigured, match="beszel.user"):
        config.require("beszel.url", "beszel.user", "beszel.password")


def test_route_reports_503_when_beszel_down(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def boom():
        raise metrics.BeszelError("Beszel 连不上：ConnectError")

    monkeypatch.setattr(metrics, "snapshot", boom)
    r = TestClient(create_app()).get("/api/metrics")
    assert r.status_code == 503
    assert "连不上" in r.json()["error"]["message"]
