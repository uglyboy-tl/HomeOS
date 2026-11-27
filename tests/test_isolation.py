"""守卫自检：这些断言保证 conftest 的隔离措施没被悄悄改坏。

守卫失效不会让功能坏，只会让"测试全绿"变得不可信，所以单独测。
"""

import socket
from pathlib import Path

import pytest

from app import config
from app.lib import health


def test_network_is_blocked():
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError, match="不许连"):
            sock.connect(("8.8.8.8", 53))
    finally:
        sock.close()


def test_loopback_is_allowed():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.socket()
        client.connect(server.getsockname())
        client.close()
    finally:
        server.close()


def test_cwd_is_a_temp_dir(tmp_path):
    assert tmp_path.resolve() == Path.cwd().resolve()


def test_pi_points_at_the_fake(fake_pi):
    assert str(fake_pi) == config.PI_BIN
    assert fake_pi.exists()
    assert Path(config.PI_BIN).resolve().parent == Path.cwd().resolve()


def test_config_dir_is_temporary(agent_config, repo_root):
    assert agent_config == config.CONFIG_DIR
    assert (repo_root / "config").resolve() != config.CONFIG_DIR.resolve()


def test_health_status_is_reset_between_tests():
    assert {item["code"] for item in health.status().values()} == {"unknown"}


def test_mijia_does_not_touch_real_devices():
    from app.services import mijia

    with pytest.raises(AssertionError, match="不连米家云"):
        mijia.mijiaAPI()
    with pytest.raises(mijia.MijiaError, match="不连米家云"):
        mijia._client()
