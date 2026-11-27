"""配置：挂载目录为空时从 defaults 复制、留空取默认值、token 可被环境变量覆盖。"""

import pytest

from app import config

# `_apply()` 会改这些模块属性，用完必须还原（否则串到下一个用例）
TOUCHED = (
    "GOTIFY_URL",
    "GOTIFY_TOKEN",
    "BESZEL_URL",
    "BESZEL_USER",
    "BESZEL_PASSWORD",
    "MPD_HOST",
    "MPD_PORT",
    "ASR_HOST",
    "ASR_KEY",
    "ASR_LANG",
    "TOKEN",
    "CONFIG_DIR",
    "MIJIA_CONFIG",
    "SERVICES_CONFIG",
)


@pytest.fixture(autouse=True)
def restore():
    saved = {name: getattr(config, name) for name in TOUCHED}
    yield
    for name, value in saved.items():
        setattr(config, name, value)
    config.PROBLEMS.clear()


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "homeos.yaml").write_text('gotify: {token: ""}\nmpd: {host: "10.0.0.9"}\n')
    (defaults / "mijia.yaml").write_text("rooms: {}\n")
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "SERVICES_CONFIG", cfg / "homeos.yaml")
    monkeypatch.setattr(config, "MIJIA_CONFIG", cfg / "mijia.yaml")
    return cfg


def test_reads_whatever_is_mounted(cfgdir):
    """补默认文件是 docker/prepare.py 的事；config 只负责读，缺文件就退回内置默认值。"""
    config._apply()
    assert config.MPD_HOST == ""  # 没有 homeos.yaml 时不炸
    assert config.MPD_PORT == 6600
    (cfgdir / "homeos.yaml").write_text('mpd: {host: "10.0.0.9"}\n', encoding="utf-8")
    config._apply()
    assert config.MPD_HOST == "10.0.0.9"


def test_blank_values_fall_back_to_code_defaults(cfgdir):
    """只有产品内置默认值（Azure 端点/locale、MPD 端口）会兜底；服务地址留空就是空。"""
    (cfgdir / "homeos.yaml").write_text(
        'gotify: {url: ""}\nmpd: {host: "", port: ""}\n'
        'beszel: {url: ""}\nazure_asr: {endpoint: "", lang: ""}\n'
    )
    config._apply()
    assert config.GOTIFY_URL == "" and config.BESZEL_URL == ""
    assert config.MPD_HOST == ""
    assert config.MPD_PORT == 6600
    assert (config.ASR_HOST, config.ASR_LANG) == ("eastasia.stt.speech.microsoft.com", "zh-CN")


def test_missing_mount_falls_back_to_defaults_dir(tmp_path, monkeypatch):
    """挂载目录没建/不可写时，直接读镜像里那份，服务照样能起。"""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "nope")
    monkeypatch.setattr(config, "SERVICES_CONFIG", tmp_path / "nope" / "homeos.yaml")
    monkeypatch.setattr(config, "MIJIA_CONFIG", tmp_path / "nope" / "mijia.yaml")
    config._apply()
    assert config.MPD_PORT == 6600


def test_env_overrides_yaml_key(cfgdir, monkeypatch):
    (cfgdir / "homeos.yaml").write_text(
        'azure_asr: {endpoint: "eastasia.example", api_key: "from-yaml"}\n'
    )
    monkeypatch.setenv("AZURE_SPEECH_KEY", "from-env")
    config._apply()
    assert (config.ASR_HOST, config.ASR_KEY) == ("eastasia.example", "from-env")
    monkeypatch.delenv("AZURE_SPEECH_KEY")
    config._apply()
    assert config.ASR_KEY == "from-yaml"


def test_broken_yaml_falls_back_to_defaults(cfgdir):
    (cfgdir / "homeos.yaml").write_text("gotify: [oops\n")
    config._apply()
    assert config.GOTIFY_TOKEN == "" and config.GOTIFY_URL == ""
    assert config.MPD_PORT == 6600
    assert config.ASR_HOST == "eastasia.stt.speech.microsoft.com"


def test_unknown_key_is_reported_but_other_fields_work(cfgdir):
    """拼错的键要报出来，但不能连累同一段里写对的值。"""
    (cfgdir / "homeos.yaml").write_text('mpd: {host: "10.0.0.9", prot: 6600}\n', encoding="utf-8")
    config._apply()
    assert config.MPD_HOST == "10.0.0.9"
    assert any("prot" in problem for problem in config.PROBLEMS)


def test_bad_port_falls_back_without_losing_host(cfgdir):
    (cfgdir / "homeos.yaml").write_text('mpd: {host: "10.0.0.9", port: "abc"}\n', encoding="utf-8")
    config._apply()
    assert (config.MPD_HOST, config.MPD_PORT) == ("10.0.0.9", 6600)
    assert any("port" in problem and "整数" in problem for problem in config.PROBLEMS)


def test_unknown_section_is_reported(cfgdir):
    (cfgdir / "homeos.yaml").write_text('gotfy: {url: "x"}\nmpd: {host: "1.2.3.4"}\n')
    config._apply()
    assert config.MPD_HOST == "1.2.3.4"
    assert any("gotfy" in problem for problem in config.PROBLEMS)


def test_loose_token_permissions_are_reported(cfgdir, monkeypatch):
    """令牌是 0600 之外还能被同机其他用户读到：不阻断，但要提醒。"""
    token = cfgdir / "server.token"
    token.write_text("a" * 32, encoding="utf-8")
    token.chmod(0o644)
    monkeypatch.setattr(config, "TOKEN_FILE", token)
    config._apply()
    assert config.TOKEN == "a" * 32
    assert any("权限" in problem for problem in config.PROBLEMS)
