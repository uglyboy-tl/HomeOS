"""进程级的退出信号：SSE 这类长连接在应用关闭时要能自己退出，不能挂在 socket 上等。"""

import asyncio

stop_event = asyncio.Event()
