"""MPD：播放状态与控制、曲库索引。

行协议交给 python-mpd2（转义/ACK/命令列表）；这里只做连接、异常翻译、状态整形。"""

import contextlib
import logging
import threading
import time

import mpd as client_lib

from .. import config
from ..lib import health

log = logging.getLogger("homeos.mpd")

NO_EXIST = 50  # MPD 的 ACK 错误码：找不到列表/曲目/目录

_SYNC_LOCK = threading.Lock()


class MpdError(Exception):
    """MPD 不可用或命令失败。"""


class MpdNotFound(MpdError):
    """列表、曲目或目录不存在。"""


class MpdBusyError(MpdError):
    """同一时刻只允许一次曲库同步。"""


class MpdConflict(MpdError):
    """当前状态不允许这个操作（比如停着的时候切下一首）。"""


@contextlib.contextmanager
def _conn():
    client = client_lib.MPDClient()
    # python-mpd2 的 timeout 同时管连接与读；connect() 的 timeout 参数已弃用，只能分两段设
    client.timeout = config.MPD_CONNECT_TIMEOUT
    try:
        client.connect(config.MPD_HOST, config.MPD_PORT)
    except (client_lib.MPDError, OSError) as e:
        raise MpdError(f"连不上 MPD（{config.MPD_HOST}:{config.MPD_PORT}）：{e}") from e
    client.timeout = config.MPD_TIMEOUT
    try:
        yield client
    except client_lib.CommandError as e:
        raise _translate(e) from e
    except (client_lib.MPDError, OSError) as e:
        raise MpdError(f"和 MPD 通信失败：{e}") from e
    finally:
        with contextlib.suppress(client_lib.MPDError, OSError):
            client.disconnect()


def _translate(error: client_lib.CommandError) -> MpdError:
    message = error.msg or str(error)
    errno = getattr(error.errno, "value", error.errno)
    if errno == NO_EXIST:
        return MpdNotFound(message)
    # 停着的时候切歌、没有上一首：这是调用时机的问题，不是上游坏了
    if message.startswith(("Not playing", "No next", "No previous")):
        return MpdConflict(message)
    return MpdError(message)


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def state() -> dict:
    with _conn() as client:
        return _read_state(client)


def playback(want: str) -> dict:
    """`playing` / `paused` / `stopped`：声明要什么，不是要执行哪个动作。"""
    with _conn() as client:
        if want == "playing":
            client.play()
        elif want == "paused":
            client.pause(1)
        else:
            client.stop()
        return _read_state(client)


def settings(
    *,
    volume: int | None = None,
    random: bool | None = None,
    repeat: bool | None = None,
    crossfade: int | None = None,
) -> dict:
    """改播放器参数；一次改多个也走命令列表，不会改一半。"""
    commands = []
    if volume is not None:
        commands.append(("setvol", volume))
    if random is not None:
        commands.append(("random", 1 if random else 0))
    if repeat is not None:
        commands.append(("repeat", 1 if repeat else 0))
    if crossfade is not None:
        commands.append(("crossfade", crossfade))
    with _conn() as client:
        if commands:
            client.command_list_ok_begin()
            for name, value in commands:
                getattr(client, name)(value)
            client.command_list_end()
        return _read_state(client)


def queue_next() -> dict:
    with _conn() as client:
        client.next()
        return _read_state(client)


def queue_prev() -> dict:
    with _conn() as client:
        client.previous()
        return _read_state(client)


def playlist(name: str) -> dict:
    """清空队列 → 载入列表 → 开始播。三步在一个命令列表里，失败不会留下空队列。"""
    with _conn() as client:
        client.command_list_ok_begin()
        client.clear()
        client.load(name)
        client.play()
        client.command_list_end()
        return _read_state(client)


def library_update() -> dict:
    """让 MPD 重新扫描曲库，等索引建完。"""
    with _lock():
        deadline = time.monotonic() + config.MPD_SYNC_TIMEOUT
        with _conn() as client:
            client.update()
            _wait_update(client, deadline)
    return {"updated": True}


def library_rebuild() -> dict:
    """按一级目录重建同名播放列表（逐项容错，会打断正在播的那份）。"""
    with _lock():
        deadline = time.monotonic() + config.MPD_SYNC_TIMEOUT
        with _conn() as client:
            client.update()
            _wait_update(client, deadline)
            existing = {row["playlist"] for row in client.listplaylists() if "playlist" in row}
            dirs = [row["directory"] for row in client.listfiles() if "directory" in row]
            tracks, skipped = 0, []
            for directory in dirs:
                if time.monotonic() > deadline:
                    raise MpdError(f"重建列表超过 {config.MPD_SYNC_TIMEOUT:g}s（已建 {tracks} 首）")
                name = directory.rstrip("/")
                try:
                    if name in existing:
                        client.playlistclear(name)
                    files = [row["file"] for row in client.listfiles(directory) if "file" in row]
                    for filename in files:
                        try:
                            client.playlistadd(name, f"{directory}/{filename}")
                            tracks += 1
                        except client_lib.CommandError as e:  # 单曲坏了只跳过
                            skipped.append({"item": f"{directory}/{filename}", "reason": _it(e)})
                except client_lib.CommandError as e:
                    skipped.append({"item": directory, "reason": _it(e)})
    return {"updated": True, "dirs": len(dirs), "tracks": tracks, "skipped": skipped}


def _it(error: client_lib.CommandError) -> str:
    return str(error.msg or error)[:80]


@contextlib.contextmanager
def _lock():
    if not _SYNC_LOCK.acquire(blocking=False):
        raise MpdBusyError("已有一次曲库同步在跑")
    try:
        yield
    finally:
        _SYNC_LOCK.release()


def _wait_update(client: client_lib.MPDClient, deadline: float) -> None:
    while "updating_db" in client.status():
        if time.monotonic() > deadline:
            raise MpdError(f"索引超过 {config.MPD_SYNC_TIMEOUT:g}s 还没完")
        time.sleep(0.5)


def _read_state(client: client_lib.MPDClient) -> dict:
    status = client.status()
    song = client.currentsong()
    volume = _int(status.get("volume"), -1)  # 没有音量控制的输出回 -1，对外报 null
    return {
        "state": status.get("state", "stop"),
        "volume": volume if volume >= 0 else None,
        "repeat": status.get("repeat") == "1",
        "random": status.get("random") == "1",
        "crossfade": _int(status.get("xfade")),
        "updating": "updating_db" in status,
        "song": {
            "file": song.get("file"),
            # python-mpd2 把 currentsong 的键全转小写
            "title": song.get("title") or song.get("name"),
            "artist": song.get("artist"),
            # 播放位置在 status 里；currentsong 的 elapsed 恒为 0
            "elapsed": _float(status.get("elapsed")),
            "duration": _float(status.get("duration") or song.get("duration")),
        }
        if song.get("file")
        else None,
        "playlist": {"length": _int(status.get("playlistlength"))},
    }


def check_config() -> None:
    """静态检查：主机配了没。"""
    config.require("mpd.host")


def probe() -> None:
    """运行期探测：真的去问一句状态。"""
    state()


health.register("mpd", check=check_config, probe=probe)
