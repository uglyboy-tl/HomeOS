"""容器启动前的配置准备：缺文件补默认、生成令牌、轮换 opencode 口令。"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("prepare", ROOT / "docker" / "prepare.py")
prepare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare)


@pytest.fixture
def defaults(tmp_path):
    d = tmp_path / "defaults"
    d.mkdir()
    (d / "homeos.yaml").write_text(
        'server:\n  token: ""\n'
        'opencode:\n  serve_password: ""\n  go_api_key: "go-key"\n  exa_key: "exa-key"\n',
        encoding="utf-8",
    )
    (d / "mijia.yaml").write_text("rooms: {}\n", encoding="utf-8")
    return d


def _run(tmp_path, defaults):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    sys.argv = ["prepare.py", str(cfg), str(defaults)]
    prepare.main()
    return cfg


def test_replace_value_keeps_comments_and_other_keys():
    text = '# 顶部注释\nserver:\n  token: "old"\n\ngotify:\n  token: "g"\n'
    out = prepare._replace_value(text, "server", "token", "new")
    assert '"new"' in out and '"old"' not in out
    assert "# 顶部注释" in out and 'token: "g"' in out


def test_replace_value_keeps_trailing_comment():
    """行尾注释不能被替换吃掉（模板里每个字段都带一句说明）。"""
    out = prepare._replace_value('k:\n  token: "" # 说明\n', "k", "token", "abc")
    assert out == 'k:\n  token: "abc" # 说明\n'


def test_replace_value_appends_missing_section():
    out = prepare._replace_value('gotify:\n  url: ""\n', "server", "token", "abc")
    assert out.endswith('server:\n  token: "abc"\n')


def test_replace_value_inserts_missing_key_into_existing_section():
    out = prepare._replace_value("opencode:\n  other: 1\n", "opencode", "password", "pw")
    assert '  password: "pw"\n  other: 1' in out


def test_copies_defaults_and_keeps_token_across_runs(tmp_path, defaults):
    cfg = _run(tmp_path, defaults)
    first = (cfg / "homeos.yaml").read_text(encoding="utf-8")
    assert (cfg / "mijia.yaml").exists()
    assert 'token: ""' not in first

    second = (_run(tmp_path, defaults) / "homeos.yaml").read_text(encoding="utf-8")

    def token(text: str) -> str:
        return text.split('token: "')[1].split('"')[0]

    assert token(first) == token(second)  # 令牌首次生成后固定
    assert first.split('serve_password: "')[1] != second.split('serve_password: "')[1]


def test_prints_password_then_opencode_keys(tmp_path, defaults, capsys):
    cfg = _run(tmp_path, defaults)
    assert cfg.exists()  # 口令与两个 key 是 prepare.py 交给 entrypoint 的唯一输出
    password, go_key, exa_key = capsys.readouterr().out.split()
    assert len(password) == 32 and go_key == "go-key" and exa_key == "exa-key"


def test_secrets_file_is_owner_only(tmp_path, defaults):
    """里面有令牌与口令，权限必须是 0600（容器里按 umask 默认会是 0644）。"""
    cfg = _run(tmp_path, defaults)
    assert (cfg / "homeos.yaml").stat().st_mode & 0o777 == 0o600
