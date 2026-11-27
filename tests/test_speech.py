"""播报能力层：把机器文本变成念得出来的句子，再交给设备。"""

import pytest

from app.services import mijia, speech


@pytest.fixture
def speaker(monkeypatch):
    seen = {}

    def speak(text, device=""):
        seen.update(text=text, device=device)
        return {"text": text, "did": device or "tts-1"}

    monkeypatch.setattr(mijia, "speak", speak)
    return seen


def test_inline_markdown_is_stripped(speaker):
    out = speech.say("**加粗** 和 `代码` 和 [链接](http://example.com)")
    assert out["text"] == "加粗 和 代码 和 链接"


def test_fenced_code_block_is_dropped(speaker):
    out = speech.say("看这个：\n```python\nprint(1)\n```\n完了")
    assert "print" not in out["text"]
    assert out["text"].startswith("看这个：") and out["text"].endswith("完了")


def test_long_text_is_truncated(speaker):
    out = speech.say("字" * 500)
    assert len(out["text"]) == speech.SAY_MAX


def test_device_is_passed_through(speaker):
    speech.say("该睡觉了", "380623598")
    assert speaker == {"text": "该睡觉了", "device": "380623598"}


def test_empty_text_is_rejected(speaker):
    with pytest.raises(ValueError):
        speech.say("   ")


def test_broken_table_is_not_configured(monkeypatch):
    """设备表写错是配置问题，不能落进 400 bad_input（TableError 也是 ValueError）。"""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import speech as speech_service

    def broken(text, device=""):
        raise mijia.TableError("config/mijia.yaml 第 3 行写错了")

    monkeypatch.setattr(speech_service, "say", broken)
    r = TestClient(app).post("/api/speech", json={"text": "喂"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"
