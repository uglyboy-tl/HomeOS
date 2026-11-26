"""运行期配置：服务与密钥读 config/*.yaml，只有运行模式与路径读环境变量。

镜像里除挂载点（web/pages、/data、/app/config）之外全只读，所以路径都是常量。
"""

import logging
import os
import re
from pathlib import Path

import yaml

log = logging.getLogger("homeos")

APP_DIR = Path(__file__).resolve().parent
# 测试直接把这两个指到 tmp_path
WEB_ROOT = (APP_DIR.parent / "web").resolve()
PAGES_DIR = WEB_ROOT / "pages"
# 运行时状态目录：容器内约定 /data（宿主机挂哪里由 compose 的 volumes 定），
# 本机没有可写 /data 时落仓库的 .devdata。docker/entrypoint.sh 用同一条判据。
DATA_DIR = Path("/data") if os.access("/data", os.W_OK) else APP_DIR.parent / ".devdata"

# 配置目录：容器里是挂载的 /app/config，本机是仓库 config/
CONFIG_DIR = Path(os.environ.get("HOMEOS_CONFIG_DIR") or APP_DIR.parent / "config").resolve()
# 镜像里另存的一份默认配置：挂载目录为空时从这复制，令牌不在里面
DEFAULTS_DIR = APP_DIR.parent / "defaults"
MIJIA_CONFIG = CONFIG_DIR / "mijia.yaml"
SERVICES_CONFIG = CONFIG_DIR / "homeos.yaml"

# opencode（127.0.0.1，只给 /oc 反代用）。口令由 entrypoint 写进 homeos.yaml 的
# opencode.serve_password，容器启动前生成；本机开发不设，服务不开鉴权。
OC_HOST = "127.0.0.1"
OC_PORT = 14100

# 启动时后台预热米家（登录 + 设备列表约 0.8s，别让第一个请求付这笔钱）；测试里关掉
WARM_MIJIA = True

# 本地开发：非 DEV 才挂 /_docs、才信任"没有反向代理"
DEV = os.environ.get("HOMEOS_DEV") == "1"

# 各服务的超时是程序策略，不当配置项
GOTIFY_TIMEOUT = 10.0
BESZEL_TIMEOUT = 5.0
MPD_CONNECT_TIMEOUT = 3.0
MPD_TIMEOUT = 10.0
MPD_SYNC_TIMEOUT = 60.0  # update 与重建列表共用一个预算
ASR_TIMEOUT = 10.0

# 产品内置默认值：只收「yaml 里留空也有意义」的项（服务地址、账号、密钥都不在此）。
DEFAULTS = {
    "mpd": {"port": 6600},
    "azure_asr": {
        "endpoint": "eastasia.stt.speech.microsoft.com",
        "lang": "zh-CN",  # Azure 只认完整 locale，裸 zh 会 400
    },
}


def _copy_default(path: Path) -> None:
    """挂载目录里没这份配置就从镜像的默认那份复制一份过去。"""
    if path.exists():
        return
    src = DEFAULTS_DIR / path.name
    try:
        path.write_bytes(src.read_bytes())
        log.info("已从 %s 生成 %s", src, path)
    except OSError as e:
        log.error("生成 %s 失败，直接用默认那份：%s", path, e)


def _ensure_files() -> None:
    for name in ("mijia.yaml", "homeos.yaml"):
        _copy_default(CONFIG_DIR / name)


# 各服务“必须有值”的项：yaml 键 → 模块属性（属性里已合并环境变量覆盖）
REQUIRED = {
    "gotify.url": "GOTIFY_URL",
    "gotify.token": "GOTIFY_TOKEN",
    "beszel.url": "BESZEL_URL",
    "beszel.user": "BESZEL_USER",
    "beszel.password": "BESZEL_PASSWORD",
    "azure_asr.endpoint": "ASR_HOST",
    "azure_asr.api_key": "ASR_KEY",
    "mpd.host": "MPD_HOST",
    "opencode.go_api_key": "OPENCODE_GO_KEY",
}


class NotConfigured(RuntimeError):
    """外部服务缺配置。由 main.py 统一转 503，业务模块不用管。"""


def _value(field: str):
    """查模块属性而不是 yaml：环境变量覆盖与测试里的 monkeypatch 都体现在属性上。"""
    return globals().get(REQUIRED[field], "")


def require(*fields: str) -> None:
    """缺哪项就报哪项，消息直接指向 yaml 的位置。"""
    missing = [f for f in fields if not str(_value(f) or "").strip()]
    if missing:
        raise NotConfigured(f"没配 {'、'.join(missing)}：填 config/homeos.yaml 里对应项")


def ready(*fields: str):
    """路由依赖：`APIRouter(dependencies=[Depends(config.ready("mpd.host"))])`。"""

    def check() -> None:
        require(*fields)

    return check


def _env(name: str, default):
    return (os.environ.get(name) or "").strip() or default


def _section(doc: dict, name: str) -> dict:
    """yaml 里的留空（不写或写 ""）等于取默认值；默认值没列的键原样放行。"""
    got = doc.get(name) or {}
    base = DEFAULTS.get(name, {})
    return {k: got.get(k) or base.get(k) or "" for k in set(base) | set(got)}


def _apply() -> None:
    global GOTIFY_URL, GOTIFY_TOKEN, BESZEL_URL, BESZEL_USER, BESZEL_PASSWORD, MPD_HOST, MPD_PORT
    global ASR_HOST, ASR_KEY, ASR_LANG, TOKEN, OC_SERVER_PASSWORD, OPENCODE_GO_KEY
    _ensure_files()
    try:
        doc = yaml.safe_load(SERVICES_CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    except (OSError, yaml.YAMLError) as e:
        log.error("读 %s 失败，用默认值：%s", SERVICES_CONFIG, e)
        doc = {}
    gotify = _section(doc, "gotify")
    beszel = _section(doc, "beszel")
    mpd = _section(doc, "mpd")
    asr = _section(doc, "azure_asr")
    opencode = doc.get("opencode") or {}

    GOTIFY_URL = gotify.get("url", "")
    GOTIFY_TOKEN = _env("GOTIFY_TOKEN", gotify.get("token", ""))
    BESZEL_URL = beszel.get("url", "")
    BESZEL_USER = beszel.get("user", "")
    BESZEL_PASSWORD = beszel.get("password", "")
    MPD_HOST = mpd.get("host", "")
    MPD_PORT = int(mpd["port"])
    # Azure 只认完整 locale（裸 zh 会 400），endpoint 不能带协议与路径
    ASR_HOST = asr.get("endpoint", "").removeprefix("https://").removeprefix("wss://").rstrip("/")
    ASR_LANG = asr["lang"]
    ASR_KEY = _env("AZURE_SPEECH_KEY", asr.get("api_key", ""))
    # 口令只能由 entrypoint 写进文件；模型的 key 可用环境变量覆盖
    OC_SERVER_PASSWORD = opencode.get("serve_password") or ""
    OPENCODE_GO_KEY = _env("OPENCODE_GO_API_KEY", opencode.get("go_api_key"))

    # 空 = /oc 关闭、其余接口放行；只收 [A-Za-z0-9_-]，它要拼进 Set-Cookie
    TOKEN = str((doc.get("server") or {}).get("token") or "").strip()
    if TOKEN and not re.fullmatch(r"[A-Za-z0-9_-]{16,}", TOKEN):
        log.error("server.token 只接受 16 位以上 [A-Za-z0-9_-]，已忽略")
        TOKEN = ""


_apply()
