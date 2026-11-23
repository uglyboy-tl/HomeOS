"""运行期配置：所有环境变量只在这里读一次，别的模块从这里取值。

镜像里除两个卷（web/pages、/data）之外全只读，所以路径都是常量，没有写回逻辑。
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
# 测试会把这两个指到 tmp_path
WEB_ROOT = Path(os.environ.get("HOMEOS_WEB_ROOT") or APP_DIR.parent / "web").resolve()
PAGES_DIR = WEB_ROOT / "pages"
# 运行时状态目录：容器里是 /data（卷）；本机没有 /data 时退回仓库里的 .devdata
_DATA_ENV = os.environ.get("HOMEOS_DATA_DIR")
if _DATA_ENV:
    DATA_DIR = Path(_DATA_ENV)
elif os.access("/data", os.W_OK):
    DATA_DIR = Path("/data")
else:
    DATA_DIR = APP_DIR.parent / ".devdata"

# 米家设备表（房间 → 设备）；容器里由 compose 把 ./config 只读挂进来
MIJIA_CONFIG = Path(
    os.environ.get("HOMEOS_MIJIA_CONFIG") or APP_DIR.parent / "config" / "mijia.yaml"
).resolve()

# opencode（127.0.0.1，只给 /oc 反代用；密码由 entrypoint 自动生成，两边进程从环境变量读）
OC_HOST = "127.0.0.1"
OC_PORT = int(os.environ.get("HOMEOS_OC_PORT", "14100"))
# web → opencode 的服务密码：由 entrypoint 生成并 export
OC_SERVER_PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")

# 启动时后台预热米家（登录 + 设备列表约 0.8s，别让第一个请求付这笔钱）
WARM_MIJIA = os.environ.get("HOMEOS_WARM_MIJIA") == "1"

# 本地开发：非 DEV 才挂 /_docs、才信任"没有反向代理"
DEV = os.environ.get("HOMEOS_DEV") == "1"
