"""日志：一个出口（stdout）、一个格式、级别可用环境变量改。"""

import logging
import sys

import pytest

from app.lib import logs


@pytest.fixture(autouse=True)
def restore():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    uvicorn = logging.getLogger("uvicorn.access")
    saved_uvicorn = (list(uvicorn.handlers), uvicorn.propagate)
    yield
    root.handlers, root.level = saved
    uvicorn.handlers, uvicorn.propagate = saved_uvicorn


def test_writes_to_stdout_with_one_format(monkeypatch):
    monkeypatch.delenv("HOMEOS_LOG_LEVEL", raising=False)
    logs.setup()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout
    assert handler.formatter._fmt == logs.FORMAT


def test_level_comes_from_environment(monkeypatch):
    monkeypatch.setenv("HOMEOS_LOG_LEVEL", "debug")
    logs.setup()
    assert logging.getLogger().level == logging.DEBUG
    monkeypatch.setenv("HOMEOS_LOG_LEVEL", "nonsense")
    logs.setup()
    assert logging.getLogger().level == logging.INFO


def test_uvicorn_loggers_share_the_same_handler(monkeypatch):
    monkeypatch.delenv("HOMEOS_LOG_LEVEL", raising=False)
    logs.setup()
    root_handler = logging.getLogger().handlers[0]
    for name in logs.UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        assert logger.handlers == [root_handler], name
        assert logger.propagate is False, name


def test_noisy_libraries_are_quieted(monkeypatch):
    monkeypatch.delenv("HOMEOS_LOG_LEVEL", raising=False)
    logs.setup()
    for name in logs.NOISY:
        assert logging.getLogger(name).level == logging.WARNING, name
