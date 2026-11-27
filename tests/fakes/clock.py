"""可控时钟：接管 time.monotonic / time.time / time.perf_counter，其余属性转发真模块。

用法：`clock` fixture 会把它装进所有用时间的 app 模块（health、runtime、mijia、mpd、asr、路由），
测试里 `clock.advance(400)` 就能跳过 TTL，不用真 sleep。
"""

import time as _real


class Clock:
    def __init__(self, monotonic: float = 10_000.0, wall: float = 1_760_000_000.0):
        self._mono = monotonic
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def perf_counter(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def sleep(self, seconds: float) -> None:
        """替掉 time.sleep：不真睡，只把时钟往前推。

        注意：被测代码里"写后重读 4 次 × 0.4s"会顺手吃掉 1.6s TTL，写 TTL 断言时记得算上。
        """
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds

    def __getattr__(self, name):
        return getattr(_real, name)
