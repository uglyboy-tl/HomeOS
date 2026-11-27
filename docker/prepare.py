"""启动前的配置准备：空挂载目录补默认配置，容器模式另外生成对外令牌。

entrypoint.sh 与 scripts/dev.sh 共用，只用标准库。
只补「人改了会生效」的文件——镜像里烤死的策略文件不在此列。
"""

import secrets
import shutil
import sys
from pathlib import Path

# 都进挂载目录，改完（重启或「新对话」后）真生效：服务、设备表、模式表、pi 的模型与设置
FILES = (
    "homeos.yaml",
    "mijia.yaml",
    "agents.yaml",
    "agents.md",
    "pi-models.json",
    "pi-settings.json",
)


def main(argv: list[str]) -> None:
    dirs = [Path(a) for a in argv if not a.startswith("--")]
    unknown = [a for a in argv if a.startswith("--") and a != "--token"]
    if len(dirs) != 2 or unknown:
        sys.exit("用法：prepare.py [--token] <config_dir> <defaults_dir>")
    config_dir, defaults = dirs
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if not (config_dir / name).exists() and (defaults / name).exists():
            shutil.copy(defaults / name, config_dir / name)

    if "--token" not in argv:
        return
    token = config_dir / "server.token"
    if not token.exists() or not token.read_text(encoding="utf-8").strip():
        token.write_text(secrets.token_hex(24), encoding="utf-8")
    token.chmod(0o600)  # 它是钥匙，同目录其他文件是 0644


if __name__ == "__main__":
    main(sys.argv[1:])
