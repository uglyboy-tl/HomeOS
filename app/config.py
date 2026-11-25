"""运行期配置：所有环境变量只在这里读一次，别的模块从这里取值。

镜像里除两个卷（web/pages、/data）之外全只读，所以路径都是常量，没有写回逻辑。
"""

import logging
import os
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
# 测试会把这两个指到 tmp_path
WEB_ROOT = Path(os.environ.get("HOMEOS_WEB_ROOT") or APP_DIR.parent / "web").resolve()
PAGES_DIR = WEB_ROOT / "pages"
# 运行时状态目录：容器内约定 /data（宿主机挂哪里由 compose 的 volumes 定），
# 本机没有可写 /data 时落仓库的 .devdata。docker/entrypoint.sh 用同一条判据。
DATA_DIR = Path("/data") if os.access("/data", os.W_OK) else APP_DIR.parent / ".devdata"

# 米家设备表（房间 → 设备）；容器里由 compose 把 ./config 只读挂进来
MIJIA_CONFIG = Path(
    os.environ.get("HOMEOS_MIJIA_CONFIG") or APP_DIR.parent / "config" / "mijia.yaml"
).resolve()

# opencode（127.0.0.1，只给 /oc 反代用；密码由 entrypoint 自动生成，两边进程从环境变量读）
OC_HOST = "127.0.0.1"
OC_PORT = int(os.environ.get("HOMEOS_OC_PORT", "14100"))
# web → opencode 的服务密码：由 entrypoint 生成并 export
OC_SERVER_PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")

# 对外接口的共享令牌（`X-HomeOS-Token`）。空 = 关掉 /oc，其余接口放行（本机开发）。
# 只收 [A-Za-z0-9_-]：它要拼进 `Set-Cookie`，含分号/空格会让浏览器直接丢掉那个 cookie。
# 写错不拦服务启动（面板不能被一个手误全黑）：降级成未配置，只把 /oc 关掉。
TOKEN = os.environ.get("HOMEOS_TOKEN", "").strip()
if TOKEN and not re.fullmatch(r"[A-Za-z0-9_-]{16,}", TOKEN):
    logging.getLogger("homeos").error(
        "HOMEOS_TOKEN 只接受 16 位以上的 [A-Za-z0-9_-]（openssl rand -hex 24），已忽略本次配置"
    )
    TOKEN = ""

# 启动时后台预热米家（登录 + 设备列表约 0.8s，别让第一个请求付这笔钱）
WARM_MIJIA = os.environ.get("HOMEOS_WARM_MIJIA") == "1"

# 语音识别：Azure 实时 WS。HOST 是区域端点，不带协议与路径
ASR_HOST = (
    os.environ.get("HOMEOS_ASR_HOST", "")
    .removeprefix("https://")
    .removeprefix("wss://")
    .rstrip("/")
)
ASR_KEY = os.environ.get("HOMEOS_ASR_KEY", "")
ASR_LANG = os.environ.get("HOMEOS_ASR_LANG", "zh-CN")  # Azure 只认完整 locale，裸 zh 会 400
ASR_TIMEOUT = float(os.environ.get("HOMEOS_ASR_TIMEOUT", "10"))

# 本地开发：非 DEV 才挂 /_docs、才信任"没有反向代理"
DEV = os.environ.get("HOMEOS_DEV") == "1"
