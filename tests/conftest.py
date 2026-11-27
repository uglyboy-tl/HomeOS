"""测试共同环境：本机开发默认值、假 pi、临时配置目录。

外部服务默认给一套"已配置"的假值，否则路由上的 `config.require()` 会先把请求挡成 503；
要测"没配置"的用例自己把它们置空。
"""

import contextlib
import shutil
import socket
import warnings
from pathlib import Path

import pytest

# 这条警告在 import starlette.testclient 时就发，早于 pytest 装 ini 里的 filterwarnings
warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=UserWarning,
)

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from tests.fakes.clock import Clock  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", ""}

CONFIGURED = {
    "GOTIFY_URL": "http://gotify",
    "GOTIFY_TOKEN": "gtfy-test",
    "BESZEL_URL": "http://beszel",
    "BESZEL_USER": "user@test",
    "BESZEL_PASSWORD": "pw",
    "ASR_HOST": "asr.example",
    "ASR_KEY": "asr-key",
    "MPD_HOST": "127.0.0.1",
}

AGENTS_YAML = """
- id: chat
  label: 聊天
  hint: 聊天模式
  thinking: "off"
  tools: []
  prompt: 你只说话。
- id: design
  label: 改页面
  hint: 改页面模式
  thinking: low
  tools: [read, write]
  prompt: 你改页面。
"""


@pytest.fixture(autouse=True)
def _dev_defaults(monkeypatch):
    monkeypatch.setattr(config, "DEV", True)
    monkeypatch.setattr(config, "TOKEN", "")
    for name, value in CONFIGURED.items():
        monkeypatch.setattr(config, name, value)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    """进程内共享的状态（健康缓存、会话、米家缓存）每个用例前清干净。"""
    from app.lib import health
    from app.services import mijia
    from app.services.agent import modes
    from app.services.agent.runtime import reset_cache

    health.reset()
    reset_cache()
    modes.reset_cache()
    monkeypatch.setattr(mijia, "_api", None)
    monkeypatch.setattr(mijia, "_dirty", False)
    monkeypatch.setattr(mijia, "_disk", {})
    monkeypatch.setattr(mijia, "_devices", {})
    monkeypatch.setattr(mijia, "_table_cache", None)
    monkeypatch.setattr(mijia, "CACHE_FILE", tmp_path / "mijia" / "state.json")


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    """不出网、不写仓库、不起真 pi：越界就断言失败，要例外自己 patch。"""
    monkeypatch.chdir(tmp_path)
    real_connect = socket.socket.connect

    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] not in LOCAL_HOSTS:
            raise AssertionError(f"测试里不许连 {address}（要用例自己 patch 掉这个出口）")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def clock(monkeypatch) -> Clock:
    """把 app 里所有用时间的模块换成可控时钟。"""
    import importlib

    from app.lib import health
    from app.routes import agent as agent_routes
    from app.services import asr, mijia, mpd

    ticking = Clock()
    modules = [health, mijia, mpd, asr, agent_routes]
    modules.append(importlib.import_module("app.services.agent.runtime"))
    for module in modules:
        monkeypatch.setattr(module, "time", ticking)
    return ticking


@pytest.fixture(autouse=True)
def _session_dir(tmp_path_factory, monkeypatch):
    """会话落盘目录指到临时目录：不让测试写真正的 /data/sessions。"""
    monkeypatch.setattr(config, "PI_SESSION_DIR", tmp_path_factory.mktemp("sessions"))


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch, tmp_path):
    """默认不外呼：真米家账号在开发机上时，跑测试会让音箱突然说话。要测就自己换掉这两个构造器。"""
    from app.services import mijia

    def denied(*_args, **_kwargs):
        raise AssertionError("测试里不连米家云（需要时 patch mijia.mijiaAPI / mijiaDevice）")

    monkeypatch.setattr(mijia, "mijiaAPI", denied)
    monkeypatch.setattr(mijia, "mijiaDevice", denied)
    auth = tmp_path / "miauth" / "auth.json"
    auth.parent.mkdir()
    auth.write_text("{}", encoding="utf-8")  # 默认"已登录"，测"没登录"自己删掉它
    monkeypatch.setattr(mijia, "AUTH_FILE", auth)


@pytest.fixture(autouse=True)
def _no_leaked_pi(monkeypatch):
    """漏掉 close() 的 RpcClient 会留下子进程和 asyncio 警告：当断言失败处理。"""
    from app.services.agent import rpc

    live: set = set()
    real_init, real_close = rpc.RpcClient.__init__, rpc.RpcClient.close

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        live.add(self)

    async def close(self):
        try:
            await real_close(self)
        finally:
            live.discard(self)

    monkeypatch.setattr(rpc.RpcClient, "__init__", init)
    monkeypatch.setattr(rpc.RpcClient, "close", close)
    yield
    leaked = [client for client in live if client.alive]
    for client in leaked:
        with contextlib.suppress(Exception):  # 清理失败不该盖住真正的断言
            client.kill()
    assert not leaked, f"这些 pi 客户端没关：{[client.pid for client in leaked]}"


@pytest.fixture
def client() -> TestClient:
    """不跑 lifespan 的裸客户端：只测 HTTP 语义时用它（要生命周期就自己 with）。"""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _agent_env(tmp_path, monkeypatch):
    """默认就有一套临时 config/ 与假 pi：任何用例都不会碰到真配置、起真 pi。"""
    from app.lib import shutdown
    from app.services.agent import modes

    root = tmp_path / "agent-config"
    root.mkdir()
    (root / "agents.yaml").write_text(AGENTS_YAML, encoding="utf-8")
    (root / "agents.md").write_text("公共前缀。", encoding="utf-8")
    (root / "pi-settings.json").write_text(
        '{"defaultProvider": "fake", "defaultModel": "fake-1", "defaultThinkingLevel": "off"}',
        encoding="utf-8",
    )
    (root / "pi-models.json").write_text('{"providers": {}}', encoding="utf-8")

    fake = tmp_path / "fake_pi"
    shutil.copy(Path(__file__).parent / "fakes" / "fake_pi.py", fake)
    fake.chmod(0o755)

    # 这几份在 import 时就绑定到仓库 config/ 了：不指过来，用例会依赖开发者本地内容
    mijia = tmp_path / "mijia.yaml"
    mijia.write_text('rooms:\n  客厅:\n    灯: {did: "1001", prop: "on"}\n', encoding="utf-8")
    services = tmp_path / "homeos.yaml"
    services.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(config, "CONFIG_DIR", root)
    monkeypatch.setattr(config, "MIJIA_CONFIG", mijia)
    monkeypatch.setattr(config, "SERVICES_CONFIG", services)
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "没有这个令牌文件")
    monkeypatch.setattr(config, "PI_BIN", str(fake))
    monkeypatch.setattr(config, "PI_EXTENSIONS", [])
    monkeypatch.setattr(config, "PI_AGENT_DIR", tmp_path / "agent")
    (tmp_path / "agent").mkdir()
    shutdown.stop_event.clear()
    modes.reset_cache()
    yield root
    modes.reset_cache()


@pytest.fixture
def repo_root() -> Path:
    """仓库根：某些用例要读真实的 config/ 文件。"""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def agent_config(_agent_env):
    return _agent_env


@pytest.fixture
def fake_pi(_agent_env) -> Path:
    return Path(config.PI_BIN)
