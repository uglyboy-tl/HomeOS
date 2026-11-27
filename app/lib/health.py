"""能力可用性登记簿：静态配置检查 + 运行期可用性缓存。

路由依赖只读缓存；真实调用后用 `note()` 回写。探测方式与阈值见 `docs/ARCHITECTURE.md`。"""

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends

from .. import config
from .errors import ApiError

log = logging.getLogger("homeos.health")

COOLDOWN = 30.0  # 默认：不可用后挡多久再放一个请求进去试探（可按能力覆盖，agent 是 0）
PROBE_TIMEOUT = 10.0


@dataclass
class Status:
    ok: bool
    code: str  # ok / not_configured / unavailable
    message: str
    checked_at: float


class Capability:
    def __init__(
        self,
        name: str,
        check: Callable[[], None],
        probe: Callable[[], None] | None,
        cooldown: float,
    ) -> None:
        self.name = name
        self._check = check
        self._probe = probe
        self._cooldown = cooldown
        # unknown 放行：宁可让第一个请求去发现，也不要启动就一律 503
        self._status = Status(ok=True, code="unknown", message="", checked_at=0.0)
        self._retry_at = 0.0

    def check(self) -> None:
        """静态检查；任何失败都算"没配好"，消息原样给调用方看。"""
        try:
            self._check()
        except config.NotConfigured as e:
            raise ApiError(503, "not_configured", str(e)) from e
        except Exception as e:  # noqa: BLE001 — 静态检查不可枚举，统一归到配置问题
            raise ApiError(503, "not_configured", f"{self.name}：{e}") from e

    def guard(self) -> None:
        """运行期检查：读缓存，冷却期内直接挡掉。"""
        status = self._status
        if status.ok:
            return
        if time.monotonic() < self._retry_at:
            raise ApiError(503, "unavailable", status.message or f"{self.name} 不可用")

    def note(self, ok: bool, message: str = "") -> None:
        """真实调用的结果回写；失败时开冷却。"""
        self._status = Status(
            ok=ok,
            code="ok" if ok else "unavailable",
            message=message[:200],
            checked_at=time.time(),
        )
        self._retry_at = 0.0 if ok else time.monotonic() + self._cooldown
        if not ok:
            log.warning("%s 不可用：%s", self.name, message[:200])

    async def probe_once(self) -> None:
        try:
            self._check()
        except Exception as e:  # noqa: BLE001 — 配置有问题时探测没有意义
            self.note(False, str(e))
            return
        if self._probe is None:
            self.note(True)
            return
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                if inspect.iscoroutinefunction(self._probe):
                    await self._probe()  # 探测本身是异步的（比如复用 rpc.version 的缓存）
                else:
                    await asyncio.to_thread(self._probe)
        except Exception as e:  # noqa: BLE001 — 探测就是来看失败的
            self.note(False, str(e))
        else:
            self.note(True)


_registry: dict[str, Capability] = {}


def register(
    name: str,
    *,
    check: Callable[[], None],
    probe: Callable[[], None] | None = None,
    cooldown: float = COOLDOWN,
) -> None:
    """`cooldown=0` 表示只报告不可用、不挡请求（状态会自己恢复）。"""
    _registry[name] = Capability(name, check, probe, cooldown)


def check(name: str) -> None:
    """静态检查（同步）；不过就抛 ApiError(503, not_configured)。"""
    _registry[name].check()


def guard(name: str) -> None:
    """运行期检查（同步）；冷却期内抛 ApiError(503, unavailable)。"""
    _registry[name].guard()


def note(name: str, ok: bool, message: str = "") -> None:
    _registry[name].note(ok, message)


def failing(name: str, error: Exception) -> ApiError:
    """真实调用失败的统一出口：回写缓存并给出 503。"""
    note(name, False, str(error))
    return ApiError(503, "unavailable", str(error))


def reset() -> None:
    """把状态清回 unknown（测试用：不让上一个用例的失败影响下一个）。"""
    for capability in _registry.values():
        capability._status = Status(ok=True, code="unknown", message="", checked_at=0.0)
        capability._retry_at = 0.0


def get(name: str) -> dict:
    """单个能力的现状：静态检查现场跑（便宜），再叠上运行期的缓存结果。"""
    capability = _registry[name]
    current = capability._status
    entry = {
        "ok": current.ok,
        "code": current.code,
        "message": current.message,
        "checked_at": current.checked_at,
    }
    try:
        capability.check()
    except ApiError as e:
        entry.update(ok=False, code=e.code, message=str(e))
    return entry


def status() -> dict[str, dict]:
    return {name: get(name) for name in _registry}


async def startup() -> None:
    """启动时把每个能力探一遍；失败只影响该能力，不拦启动。"""
    await asyncio.gather(*(capability.probe_once() for capability in _registry.values()))


def deps(*names: str) -> list:
    """一次声明某个能力的两条依赖（静态 + 运行期）：`dependencies=health.deps("mpd")`。"""

    def static_check(name: str):
        async def dependency() -> None:
            check(name)

        return dependency

    def runtime_check(name: str):
        async def dependency() -> None:
            guard(name)

        return dependency

    return [Depends(static_check(name)) for name in names] + [
        Depends(runtime_check(name)) for name in names
    ]
