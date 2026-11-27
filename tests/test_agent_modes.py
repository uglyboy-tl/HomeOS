"""模式表：解析、校验、缓存与仓库里那份真配置的约束。"""

import json

from app.services.agent import modes


def _write(root, text, name="agents.yaml"):
    (root / name).write_text(text, encoding="utf-8")


def test_catalog_parses_in_order(agent_config):
    catalog = modes.catalog()
    assert list(catalog.modes) == ["chat", "design"]
    design = catalog.modes["design"]
    assert design.tools == ("read", "write")
    assert design.thinking == "low"
    assert catalog.problems == ()
    assert len(catalog.fingerprint) == 12


def test_catalog_is_cached_until_files_change(agent_config):
    first = modes.catalog()
    assert modes.catalog() is first
    _write(agent_config, "- id: chat\n  prompt: 改过了\n")
    second = modes.catalog()
    assert second is not first
    assert second.fingerprint != first.fingerprint


def test_bad_id_is_dropped_with_problem(agent_config):
    _write(agent_config, "- id: Bad\n  prompt: x\n")
    catalog = modes.catalog()
    assert catalog.modes == {}
    assert any("id 非法" in problem for problem in catalog.problems)


def test_duplicate_id(agent_config):
    _write(agent_config, "- id: chat\n  prompt: 一\n- id: chat\n  prompt: 二\n")
    catalog = modes.catalog()
    assert list(catalog.modes) == ["chat"]
    assert any("重复" in problem for problem in catalog.problems)


def test_bad_thinking(agent_config):
    _write(agent_config, "- id: chat\n  prompt: x\n  thinking: turbo\n")
    assert any("thinking" in problem for problem in modes.catalog().problems)


def test_unknown_tool(agent_config):
    _write(agent_config, "- id: chat\n  prompt: x\n  tools: [nuke]\n")
    catalog = modes.catalog()
    assert catalog.modes == {}
    assert any("未知工具" in problem for problem in catalog.problems)


def test_missing_prompt(agent_config):
    _write(agent_config, "- id: chat\n  label: 聊天\n")
    assert any("没有 prompt" in problem for problem in modes.catalog().problems)


def test_missing_files(agent_config):
    (agent_config / "agents.yaml").unlink()
    catalog = modes.catalog()
    assert catalog.modes == {}
    assert any("agents.yaml" in problem for problem in catalog.problems)


def test_hint_falls_back_to_label(agent_config):
    _write(agent_config, "- id: chat\n  label: 聊天\n  prompt: x\n")
    assert modes.catalog().modes["chat"].hint == "聊天"


def test_settings_reads_pi_defaults(agent_config):
    assert modes.settings() == {"provider": "fake", "model": "fake-1", "thinking": "off"}


def test_each_mode_carries_its_own_prompt(agent_config):
    catalog = modes.catalog()
    assert catalog.modes["chat"].prompt == "你只说话。"
    assert catalog.modes["design"].prompt == "你改页面。"


def test_repo_settings_trust_the_working_directory(repo_root):
    """信任写在 pi 的 agent 设置里：cwd 要么是只读镜像、要么是用户自己挂的 web。"""
    settings = json.loads((repo_root / "config/pi-settings.json").read_text(encoding="utf-8"))
    assert settings["defaultProjectTrust"] == "always"


def test_repo_model_turns_thinking_off_properly(repo_root):
    """关思考靠请求里 thinking.type=disabled，这依赖 compat.thinkingFormat。"""
    text = (repo_root / "config/pi-models.json").read_text(encoding="utf-8")
    model = json.loads(text)["providers"]["opencode-go"]["models"][0]
    assert model["reasoning"] is True
    assert model["compat"]["thinkingFormat"] == "deepseek"


def test_repo_agents_only_declare_tools(repo_root):
    """文件边界不靠 agents.yaml：写边界是容器挂载，读边界是 cc-safety-net 的 deny_paths。"""
    import yaml

    doc = yaml.safe_load((repo_root / "config/agents.yaml").read_text(encoding="utf-8"))
    for raw in doc:
        assert set(raw) <= {"id", "label", "hint", "thinking", "tools", "prompt"}
        assert "bash" not in (raw.get("tools") or [])


def test_empty_file_is_reported(agent_config):
    _write(agent_config, "   \n")
    catalog = modes.catalog()
    assert catalog.modes == {}
    assert any("空的" in problem for problem in catalog.problems)
