"""运行期配置：服务与密钥读 config/*.yaml，运行模式与路径读环境变量。

解析结果写成模块属性；写错的地方记进 `PROBLEMS`，不拦启动，从 `/api/info` 能看到。"""

import logging
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .lib.validation import describe

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
MIJIA_CONFIG = CONFIG_DIR / "mijia.yaml"
SERVICES_CONFIG = CONFIG_DIR / "homeos.yaml"
TOKEN_FILE = CONFIG_DIR / "server.token"

# 本地开发：非 DEV 才挂 /_docs、才信任"没有反向代理"
DEV = os.environ.get("HOMEOS_DEV") == "1"

# 各服务的超时是程序策略，不当配置项
GOTIFY_TIMEOUT = 10.0
BESZEL_TIMEOUT = 5.0
MPD_CONNECT_TIMEOUT = 3.0
MPD_TIMEOUT = 10.0
MPD_SYNC_TIMEOUT = 60.0  # update 与重建列表共用一个预算
ASR_TIMEOUT = 10.0
ASK_TIMEOUT = 90.0  # 一次性 ask 的预算，流式由调用方自己控

# agent 内核：二进制、agent 目录、扩展可以用环境变量指到本机那份（路径属于部署差异）
PI_BIN = os.environ.get("HOMEOS_PI_BIN") or "pi"
PI_AGENT_DIR = Path(os.environ.get("HOMEOS_PI_AGENT_DIR") or (DATA_DIR / "pi"))
# 会话落盘目录：属于运行时数据区（容器里 /data 是命名卷），重启能续上上下文
PI_SESSION_DIR = Path(os.environ.get("HOMEOS_PI_SESSION_DIR") or (DATA_DIR / "sessions"))
# 扩展：秘密与危险命令拦截（cc-safety-net）；路径边界靠容器挂载与它的 deny_paths
PI_EXTENSIONS = [
    Path(p).expanduser().resolve()
    for p in (os.environ.get("HOMEOS_PI_EXTENSIONS") or "").split(":")
    if p.strip()
]

# 本次解析发现的问题（拼写、类型、令牌格式/权限）；只记不改，供 /api/info 与日志使用
PROBLEMS: list[str] = []


class NotConfigured(RuntimeError):
    """外部服务缺配置。由 main.py 统一转 503，业务模块不用管。"""


class _Section(BaseModel):
    """yaml 的一段：留空（不写或写 ""）等于用默认值，写了不认识的键要报出来。"""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_blanks(cls, data):
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if value not in (None, "")}


class Gotify(_Section):
    url: str = ""
    token: str = ""


class Beszel(_Section):
    url: str = ""
    user: str = ""
    password: str = ""


class Mpd(_Section):
    host: str = ""
    port: int = Field(default=6600, ge=1, le=65535)


class AzureAsr(_Section):
    endpoint: str = "eastasia.stt.speech.microsoft.com"  # 只认域名，不能带协议与路径
    api_key: str = ""
    lang: str = "zh-CN"  # Azure 只认完整 locale，裸 zh 会 400


class Services(_Section):
    gotify: Gotify = Field(default_factory=Gotify)
    beszel: Beszel = Field(default_factory=Beszel)
    mpd: Mpd = Field(default_factory=Mpd)
    azure_asr: AzureAsr = Field(default_factory=AzureAsr)


def _read_yaml() -> object:
    try:
        return yaml.safe_load(SERVICES_CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as e:
        PROBLEMS.append(f"读 {SERVICES_CONFIG.name} 失败：{e}")
        return {}


def _load(name: str, model: type[BaseModel], raw: object) -> BaseModel:
    """一段一段校验：坏掉的键丢掉再试一次，好的字段照用（比如 port 写错不该让 host 也失效）。"""
    data = raw if isinstance(raw, dict) else {}
    for _ in range(4):
        try:
            return model.model_validate(data)
        except ValidationError as e:
            PROBLEMS.append(f"{name}：{describe(e.errors()[0])}")
            bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
            if not bad:
                break
            data = {key: value for key, value in data.items() if key not in bad}
    return model()


def _services() -> Services:
    raw = _read_yaml()
    if not isinstance(raw, dict):
        PROBLEMS.append(f"{SERVICES_CONFIG.name} 顶层应该是映射（段名: 内容）")
        return Services()
    known = Services.model_fields
    sections = {}
    for name, value in raw.items():
        if name not in known:
            PROBLEMS.append(f"{SERVICES_CONFIG.name} 里有不认识的段：{name}")
            continue
        sections[name] = _load(name, known[name].annotation, value)
    return Services.model_validate(sections)


def _env(name: str, default: str) -> str:
    """可轮换的令牌类才走环境变量；密码与运行参数不进环境。"""
    return (os.environ.get(name) or "").strip() or default


def _read_token() -> str:
    """对外令牌：docker/prepare.py 首次启动时生成的文件（0600），人也可以手改。"""
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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
}


def _value(field: str):
    """查模块属性而不是 yaml：环境变量覆盖与测试里的 monkeypatch 都体现在属性上。"""
    return globals().get(REQUIRED[field], "")


def require(*fields: str) -> None:
    """缺哪项就报哪项，消息直接指向 yaml 的位置。"""
    missing = [f for f in fields if not str(_value(f) or "").strip()]
    if missing:
        raise NotConfigured(f"没配 {'、'.join(missing)}：填 config/homeos.yaml 里对应项")


def _host(endpoint: str) -> str:
    return endpoint.removeprefix("https://").removeprefix("wss://").rstrip("/")


def _apply() -> None:
    global GOTIFY_URL, GOTIFY_TOKEN, BESZEL_URL, BESZEL_USER, BESZEL_PASSWORD, MPD_HOST, MPD_PORT
    global ASR_HOST, ASR_KEY, ASR_LANG, TOKEN
    PROBLEMS.clear()
    services = _services()

    GOTIFY_URL = services.gotify.url
    GOTIFY_TOKEN = _env("GOTIFY_TOKEN", services.gotify.token)
    BESZEL_URL = services.beszel.url
    BESZEL_USER = services.beszel.user
    BESZEL_PASSWORD = services.beszel.password
    MPD_HOST = services.mpd.host
    MPD_PORT = services.mpd.port
    ASR_HOST = _host(services.azure_asr.endpoint)
    ASR_LANG = services.azure_asr.lang
    ASR_KEY = _env("AZURE_SPEECH_KEY", services.azure_asr.api_key)

    TOKEN = _read_token()
    # 空 = 生产全关（DEV 放行）；只收 [A-Za-z0-9_-]，它要拼进 Set-Cookie
    if TOKEN and not re.fullmatch(r"[A-Za-z0-9_-]{16,}", TOKEN):
        PROBLEMS.append("server.token 只接受 16 位以上 [A-Za-z0-9_-]，已忽略")
        TOKEN = ""
    if TOKEN:
        mode = TOKEN_FILE.stat().st_mode & 0o777
        if mode & 0o077:
            PROBLEMS.append(f"server.token 权限是 {mode:o}，建议 600（同机其他用户可读）")

    for problem in PROBLEMS:
        log.error("配置：%s", problem)


_apply()
