"""容器启动前的配置准备：空挂载目录补默认配置，生成令牌、轮换 opencode 密码。

由 docker/entrypoint.sh 调用（只有容器模式跑）。按固定顺序打印三行给 shell 抓取：
opencode 密码、模型 key、搜索 key —— 后两个 opencode 只认环境变量。
"""

import pathlib
import re
import secrets
import shutil
import sys

import yaml


def _replace_value(text: str, section: str, key: str, value: str) -> str:
    """行级替换：只改目标键那一行，保住文件里的注释与排版。"""
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if line.strip() == f"{section}:"), None)
    if head is None:
        return f'{text.rstrip()}\n{section}:\n  {key}: "{value}"\n'
    end = next(
        (i for i in range(head + 1, len(lines)) if lines[i] and not lines[i][0].isspace()),
        len(lines),
    )
    for i in range(head + 1, end):
        if re.match(rf"\s*{key}\s*:", lines[i]):
            # 只换值，保留行尾注释
            lines[i] = re.sub(
                r'(:\s*)("[^"]*"|\S*)(\s+#.*)?$',
                lambda m: f'{m.group(1)}"{value}"{m.group(3) or ""}',
                lines[i],
            )
            break
    else:
        lines.insert(head + 1, f'  {key}: "{value}"')
    return "\n".join(lines) + "\n"


def main() -> None:
    config_dir, defaults = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("homeos.yaml", "mijia.yaml"):
        if not (config_dir / name).exists() and (defaults / name).exists():
            shutil.copy(defaults / name, config_dir / name)

    path = config_dir / "homeos.yaml"
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text) or {}
    if not str((doc.get("server") or {}).get("token") or "").strip():
        text = _replace_value(text, "server", "token", secrets.token_hex(24))
    password = secrets.token_hex(16)  # 每次启动都换，不接受外部干预
    path.write_text(_replace_value(text, "opencode", "serve_password", password), encoding="utf-8")
    path.chmod(0o600)  # 里面有令牌与口令
    opencode = doc.get("opencode") or {}
    for value in (password, opencode.get("go_api_key") or "", opencode.get("exa_key") or ""):
        print(value)


if __name__ == "__main__":
    main()
