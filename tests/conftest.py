"""测试默认按本机开发跑：不配令牌、DEV=1，于是接口免认证；要验生产行为自己覆盖。

没有这个 fixture 的话，`config.DEV` 在测试里是 False，而令牌为空 → 所有 `/api/*` 401，
每个用例都得先造一遍 cookie。
"""

import pytest

from app import config


@pytest.fixture(autouse=True)
def _dev_defaults(monkeypatch):
    monkeypatch.setattr(config, "DEV", True)
    monkeypatch.setattr(config, "TOKEN", "")
