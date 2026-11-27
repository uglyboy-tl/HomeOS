"""启动前的配置准备：只补「改了会生效」的文件，令牌单独一个文件。"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("prepare", ROOT / "docker" / "prepare.py")
prepare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare)


@pytest.fixture
def defaults(tmp_path):
    d = tmp_path / "defaults"
    d.mkdir()
    (d / "homeos.yaml").write_text('gotify: {url: ""}\n', encoding="utf-8")
    (d / "mijia.yaml").write_text("rooms: {}\n", encoding="utf-8")
    (d / "agents.yaml").write_text("chat: {}\n", encoding="utf-8")
    (d / "pi-models.json").write_text("{}\n", encoding="utf-8")
    # 镜像里烤死的策略文件：不该被复制到挂载目录
    (d / "cc-safety-net-policy.json").write_text("{}\n", encoding="utf-8")
    return d


def _run(tmp_path, defaults, token=False):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    prepare.main((["--token"] if token else []) + [str(cfg), str(defaults)])
    return cfg


def test_copies_only_editable_files(tmp_path, defaults):
    cfg = _run(tmp_path, defaults)
    assert (cfg / "homeos.yaml").exists() and (cfg / "mijia.yaml").exists()
    assert (cfg / "pi-models.json").exists()
    assert not (cfg / "cc-safety-net-policy.json").exists()  # 改它要重建镜像


def test_token_only_when_asked(tmp_path, defaults):
    """本机开发不要令牌（DEV=1 直接放行）；容器里 entrypoint 传 --token。"""
    assert not (_run(tmp_path, defaults) / "server.token").exists()
    assert (_run(tmp_path, defaults, token=True) / "server.token").exists()


def test_token_is_generated_once_and_owner_only(tmp_path, defaults):
    cfg = _run(tmp_path, defaults, token=True)
    token = (cfg / "server.token").read_text(encoding="utf-8").strip()
    assert len(token) >= 32
    assert (cfg / "server.token").stat().st_mode & 0o777 == 0o600
    # 第二次启动沿用同一个令牌，否则所有客户端与页面 cookie 一起失效
    again = _run(tmp_path, defaults, token=True)
    assert (again / "server.token").read_text().strip() == token


def test_existing_files_are_not_overwritten(tmp_path, defaults):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "homeos.yaml").write_text("gotify: {url: 'http://mine'}\n", encoding="utf-8")
    prepare.main([str(cfg), str(defaults)])
    assert "mine" in (cfg / "homeos.yaml").read_text(encoding="utf-8")


def test_prepare_is_silent(tmp_path, defaults, capsys):
    _run(tmp_path, defaults)
    assert capsys.readouterr().out.strip() == ""
