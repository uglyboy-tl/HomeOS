"""opencode 的地址与认证：口令只有一份，拼 URL / Basic 头都从这里出。"""

import base64

from .. import config


def url(path: str) -> str:
    return f"http://{config.OC_HOST}:{config.OC_PORT}{path}"


def headers() -> dict:
    cred = base64.b64encode(f"opencode:{config.OC_SERVER_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}
