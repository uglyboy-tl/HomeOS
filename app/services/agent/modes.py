"""模式表：config/agents.yaml（prompt/工具/思考档位）与 agents.md（公共前缀）。

只读文件、只做校验与缓存；问题收集成 problems 交给 /info，不拦启动。"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ... import config

log = logging.getLogger("homeos.agents")

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# pi 的内置工具名。不给 bash 是刻意的：页面模式不需要任意命令
TOOLS = frozenset({"read", "write", "edit", "grep", "find", "ls", "bash"})
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    hint: str
    prompt: str
    thinking: str
    tools: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    modes: dict[str, Mode] = field(default_factory=dict)
    shared: str = ""
    fingerprint: str = ""
    problems: tuple[str, ...] = ()


def stamp(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (0, 0)


def _read(path: Path, problems: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        problems.append(f"读不了 {path.name}：{e}")
        return ""


def _mode(raw: dict, seen: set[str], problems: list[str]) -> Mode | None:
    """一条 yaml 记录 → Mode；这一条有问题就丢掉它，不牵连别的模式。"""
    mode_id = str(raw.get("id") or "").strip()
    if not ID_RE.match(mode_id):
        problems.append(f"模式 id 非法（{mode_id!r}）：小写字母开头，只含 a-z 0-9 -，最长 32 位")
        return None
    if mode_id in seen:
        problems.append(f"模式 id 重复：{mode_id}")
        return None
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        problems.append(f"模式 {mode_id} 没有 prompt")
        return None
    thinking = str(raw.get("thinking") or "off").strip()
    if thinking not in THINKING:
        problems.append(f"模式 {mode_id} 的 thinking 非法：{thinking}（取 {'/'.join(THINKING)}）")
        return None
    tools = tuple(str(tool).strip() for tool in (raw.get("tools") or []) if str(tool).strip())
    unknown = [tool for tool in tools if tool not in TOOLS]
    if unknown:
        problems.append(f"模式 {mode_id} 用了未知工具：{'、'.join(unknown)}")
        return None
    label = str(raw.get("label") or mode_id).strip()
    return Mode(
        id=mode_id,
        label=label,
        hint=str(raw.get("hint") or "").strip() or label,
        prompt=prompt,
        thinking=thinking,
        tools=tools,
    )


def _parse(text: str, problems: list[str]) -> dict[str, Mode]:
    try:
        doc = yaml.safe_load(text) or []
    except yaml.YAMLError as e:
        problems.append(f"agents.yaml 解析失败：{e}")
        return {}
    if not isinstance(doc, list):
        problems.append("agents.yaml 顶层应该是列表（每个模式一条）")
        return {}
    found: dict[str, Mode] = {}
    for raw in doc:
        if not isinstance(raw, dict):
            problems.append("agents.yaml 里有不是映射的条目")
            continue
        mode = _mode(raw, set(found), problems)
        if mode is not None:
            found[mode.id] = mode
    if not found:
        problems.append("agents.yaml 里没有一个可用的模式")
    return found


_cached: tuple[tuple, Catalog] | None = None


def catalog() -> Catalog:
    """当前模式表；文件没变就用缓存（每个请求都会来问一次）。"""
    global _cached
    yml = config.CONFIG_DIR / "agents.yaml"
    md = config.CONFIG_DIR / "agents.md"
    key = (stamp(yml), stamp(md))
    if _cached is not None and _cached[0] == key:
        return _cached[1]

    problems: list[str] = []
    raw = _read(yml, problems)
    shared = _read(md, problems)
    if raw and not raw.strip():
        problems.append("agents.yaml 是空的：至少要有一个模式")
    modes = _parse(raw, problems) if raw.strip() else {}
    fingerprint = hashlib.sha1(f"{raw}\x00{shared}".encode()).hexdigest()[:12]
    result = Catalog(modes=modes, shared=shared, fingerprint=fingerprint, problems=tuple(problems))
    for problem in problems:
        log.error("agent 配置：%s", problem)
    _cached = (key, result)
    return result


_settings: tuple[tuple, dict] | None = None


def settings() -> dict:
    """pi 的启动默认值，只用于展示（/info），不改变 pi 的行为。"""
    global _settings
    path = config.CONFIG_DIR / "pi-settings.json"
    key = (stamp(path),)
    if _settings is not None and _settings[0] == key:
        return _settings[1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("读 %s 失败：%s", path, e)
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    value = {
        "provider": str(doc.get("defaultProvider") or ""),
        "model": str(doc.get("defaultModel") or ""),
        "thinking": str(doc.get("defaultThinkingLevel") or ""),
    }
    _settings = (key, value)
    return value


def reset_cache() -> None:
    """清缓存（测试用）。"""
    global _cached, _settings
    _cached = None
    _settings = None
