"""通知：level → priority、标题缺省、失败转成领域错误。"""

import pytest

from app import config
from app.services import notify


@pytest.fixture
def gotify(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 12}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return Resp()

    monkeypatch.setattr(config, "GOTIFY_URL", "http://gotify")
    monkeypatch.setattr(config, "GOTIFY_TOKEN", "gtfy-token")
    monkeypatch.setattr(notify.httpx, "post", fake_post)
    return seen


def test_payload_headers_and_title(gotify):
    out = notify.push("备份链异常", "⚠️ 备份链异常", "error")
    assert out == {"id": 12}
    assert gotify["url"] == "http://gotify/message"
    # token 走头：httpx 会把带 query 的 URL 写进日志，凭据不该出现在那里
    assert gotify["headers"] == {"X-Gotify-Key": "gtfy-token"}
    assert "token=" not in gotify["url"]
    assert gotify["body"]["title"] == "⚠️ 备份链异常"
    assert gotify["body"]["priority"] == 8


@pytest.mark.parametrize(("level", "priority"), [("info", 2), ("warn", 6), ("error", 8)])
def test_level_map(gotify, level, priority):
    notify.push("x", level=level)
    assert gotify["body"]["priority"] == priority


def test_title_defaults_to_head_of_text(gotify):
    notify.push("备份链异常 2 项：pg-back 超过 26h 没有新包")
    assert gotify["body"]["title"] == "备份链异常 2 项：pg-back 超过 26h 没有新包"[:20]


def test_transport_failure_is_a_domain_error(gotify, monkeypatch):
    def boom(*_args, **_kwargs):
        raise notify.httpx.ConnectError("连不上")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with pytest.raises(notify.NotifyError):
        notify.push("x")
