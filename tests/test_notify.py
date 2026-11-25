"""通知分发：level → priority、标题缺省、通道互不拖累。"""

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


@pytest.fixture
def spoken(monkeypatch):
    said = []

    def fake_say(text):
        said.append(text)
        return {"text": text}

    monkeypatch.setattr(notify.mijia, "say", fake_say)
    return said


def test_gotify_payload_and_priority(gotify, spoken):
    out = notify.send("备份链异常", "⚠️ 备份链异常", "error")
    assert out == {"gotify": {"ok": True, "id": 12}}
    assert gotify["url"] == "http://gotify/message"
    # token 走头：httpx 会把带 query 的 URL 写进日志，凭据不该出现在那里
    assert gotify["headers"] == {"X-Gotify-Key": "gtfy-token"}
    assert "token=" not in gotify["url"]
    assert gotify["body"] == {"title": "⚠️ 备份链异常", "message": "备份链异常", "priority": 8}


@pytest.mark.parametrize(("level", "priority"), [("info", 2), ("warn", 6), ("error", 8)])
def test_level_map(gotify, spoken, level, priority):
    notify.send("x", level=level)
    assert gotify["body"]["priority"] == priority


def test_title_defaults_to_head_of_text(gotify, spoken):
    notify.send("备份链异常 2 项：pg-back 超过 26h 没有新包")
    assert gotify["body"]["title"] == "备份链异常 2 项：pg-back 超过"


def test_speak_uses_mijia(gotify, spoken):
    out = notify.send("该睡觉了", channels=("speak",))
    assert out["speak"]["ok"] is True
    assert spoken == ["该睡觉了"]


def test_one_channel_failing_does_not_stop_the_other(gotify, spoken, monkeypatch):
    def boom(text):
        raise RuntimeError("米家没登录")

    monkeypatch.setattr(notify.mijia, "say", boom)
    out = notify.send("x", channels=("gotify", "speak"))
    assert out["gotify"]["ok"] is True
    assert out["speak"] == {"ok": False, "error": "米家没登录"}


def test_missing_token_is_reported_not_raised(monkeypatch, spoken):
    monkeypatch.setattr(config, "GOTIFY_TOKEN", "")
    out = notify.send("x")
    assert out["gotify"]["ok"] is False
    assert "HOMEOS_GOTIFY_TOKEN" in out["gotify"]["error"]


def test_unknown_channel_comes_back_as_that_channel_failing(gotify, spoken):
    """HTTP 层用 Literal 挡住了；这里只确保直接调 service 时不会把其他通道一起拖坏。"""
    out = notify.send("x", channels=("gotify", "sms"))
    assert out["gotify"]["ok"] is True
    assert out["sms"]["ok"] is False
