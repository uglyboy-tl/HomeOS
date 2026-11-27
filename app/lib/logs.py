"""日志走 stdout 交给 docker 收集，不落盘；级别用 `HOMEOS_LOG_LEVEL`（默认 INFO）。

uvicorn 的 logger 也换成同一套 handler，`docker logs` 里只有一种格式。"""

import logging
import os
import sys

FORMAT = "%(levelname)s %(name)s: %(message)s"
# httpx 的 INFO 会把含 query 的完整 URL 写进日志（凭据不能出现在那里）；
# mpd.base 每次连接/断开都打一条 INFO，我们是“每请求一次连接”，纯噪音
NOISY = {"httpx": logging.WARNING, "mpd.base": logging.WARNING}
# uvicorn 自己配了 handler；不换的话两种格式会混在同一个流里
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def setup() -> None:
    level = (os.environ.get("HOMEOS_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))

    for name, value in NOISY.items():
        logging.getLogger(name).setLevel(value)
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
