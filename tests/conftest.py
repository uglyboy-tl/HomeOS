"""测试默认按本机开发跑：不配令牌、DEV=1，于是接口免认证；要验生产行为自己覆盖。

外部服务默认给一套"已配置"的假值，否则路由上的 `config.require()` 会先把请求挡成 503；
要测"没配置"的用例自己把它们置空。
"""

import pytest

from app import config

CONFIGURED = {
    "GOTIFY_URL": "http://gotify",
    "GOTIFY_TOKEN": "gtfy-test",
    "BESZEL_URL": "http://beszel",
    "BESZEL_USER": "user@test",
    "BESZEL_PASSWORD": "pw",
    "ASR_HOST": "asr.example",
    "ASR_KEY": "asr-key",
    "MPD_HOST": "127.0.0.1",
    "OPENCODE_GO_KEY": "go-test",
}


@pytest.fixture(autouse=True)
def _dev_defaults(monkeypatch):
    monkeypatch.setattr(config, "DEV", True)
    monkeypatch.setattr(config, "TOKEN", "")
    monkeypatch.setattr(config, "WARM_MIJIA", False)  # 不让 lifespan 去真登米家
    for name, value in CONFIGURED.items():
        monkeypatch.setattr(config, name, value)
