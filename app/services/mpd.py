"""MPD（行协议，6600）：读状态、控制播放、同步曲库。
连上先收 `OK MPD <ver>`，之后一行一条命令，回 `key: value` 行，以 `OK` 或 `ACK` 结束。
全部 sync def：局域网往返毫秒级，但别占事件循环。"""

import logging
import socket
import threading
import time
from contextlib import suppress

from .. import config

log = logging.getLogger("homeos.mpd")

DEVICES = ("mpd",)  # 现在只有这一路输出；将来 DLNA/AirPlay 就是这里加一项


class MpdError(Exception):
    """连不上 MPD，或它拒绝了命令。"""


class MpdNotFound(MpdError):
    """要的播放列表 / 目录不存在。"""


class MpdValueError(MpdError):
    """参数不合法（换行会变成第二条命令，直接拒）。"""


class MpdBusyError(MpdError):
    """同一时刻只允许一次 sync（交错 playlistclear/add 会把列表写坏）。"""


def _quote(arg: str) -> str:
    r"""带空白或引号类字符的参数整段加引号；换行直接拒（它会变成第二条命令）。

    MPD 的分词器把 `'` 和 `"` 都当引号开头（未加引号的 `Dan Gibson's` 会被回
    `Invalid unquoted character`），`\` 则会被当转义吃掉，所以四个都要走加引号分支。
    """
    if any(c in arg for c in "\r\n"):
        raise MpdValueError("参数里不能有换行")
    if arg and not any(c in arg for c in " \"'\\\t"):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Conn:
    def __init__(self) -> None:
        try:
            self.sock = socket.create_connection(
                (config.MPD_HOST, config.MPD_PORT), config.MPD_CONNECT_TIMEOUT
            )
        except OSError as e:
            raise MpdError(f"连不上 MPD（{config.MPD_HOST}:{config.MPD_PORT}）：{e}") from e
        self.sock.settimeout(config.MPD_TIMEOUT)
        try:
            self.file = self.sock.makefile("rwb")
            greeting = self.file.readline().decode("utf-8", "replace").strip()
        except OSError as e:
            self.close()
            raise MpdError(f"连上了但握手失败：{e}") from e
        if not greeting.startswith("OK MPD"):
            self.close()
            raise MpdError(f"{config.MPD_HOST} 不是 MPD：{greeting[:40]}")

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        file = getattr(self, "file", None)
        for closer in (file.close if file else None, self.sock.close):
            if closer:
                with suppress(OSError):
                    closer()

    def cmd(self, *args: object) -> list[tuple[str, str]]:
        """发一条命令，回 [(key, value), ...]；重复的键会在列表里出现多次。"""
        line = " ".join(_quote(str(a)) for a in args)
        try:
            self.file.write((line + "\n").encode())
            self.file.flush()
            rows: list[tuple[str, str]] = []
            while True:
                raw = self.file.readline()
                if not raw:
                    raise MpdError("MPD 断开了连接")
                text = raw.decode("utf-8", "replace").rstrip("\n")
                if text == "OK":
                    return rows
                if text.startswith("ACK"):
                    if "No such" in text or "not found" in text:
                        raise MpdNotFound(text)
                    raise MpdError(text)
                key, _, value = text.partition(": ")
                rows.append((key, value))
        except OSError as e:
            raise MpdError(f"和 MPD 通信失败：{e}") from e


def _status(c: _Conn) -> dict:
    return dict(c.cmd("status"))


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def state() -> dict:
    with _Conn() as c:
        return _read_state(c)


def _read_state(c: _Conn) -> dict:
    st = _status(c)
    song = dict(c.cmd("currentsong"))
    volume = _int(st.get("volume"), -1)  # 没有音量控制的输出会回 -1，对外报 null
    return {
        "state": st.get("state", "stop"),
        "volume": volume if volume >= 0 else None,
        "repeat": st.get("repeat") == "1",
        "random": st.get("random") == "1",
        "crossfade": _int(st.get("xfade")),
        "updating": "updating_db" in st,
        "song": {
            "file": song.get("file"),
            "title": song.get("Title") or song.get("Name"),
            "artist": song.get("Artist"),
            # 播放位置在 status 里；currentsong 的 elapsed 恒为 0
            "elapsed": _float(st.get("elapsed")),
            "duration": _float(st.get("duration") or song.get("duration")),
        }
        if song.get("file")
        else None,
        "playlist": {"length": _int(st.get("playlistlength"))},
    }


def _play(c: _Conn, fields: dict) -> None:
    if fields.get("playlist"):
        c.cmd("clear")  # load 是追加，不清空的话每晚多叠一份
        c.cmd("load", fields["playlist"])
    if fields.get("random") is not None:
        c.cmd("random", "1" if fields["random"] else "0")
    if fields.get("repeat") is not None:
        c.cmd("repeat", "1" if fields["repeat"] else "0")
    if fields.get("crossfade") is not None:
        c.cmd("crossfade", fields["crossfade"])
    if fields.get("volume") is not None:
        c.cmd("setvol", fields["volume"])
    c.cmd("play")


ACTIONS = {
    "play": _play,
    "pause": lambda c, f: c.cmd("pause", "1" if f.get("on", True) else "0"),
    "stop": lambda c, f: c.cmd("stop"),
    "volume": lambda c, f: c.cmd("setvol", f["volume"]),
    "next": lambda c, f: c.cmd("next"),
    "prev": lambda c, f: c.cmd("previous"),
}


def control(action: str, **fields: object) -> dict:
    """执行一个动作，返回动作之后的状态（写接口必须读回）。"""
    if action not in ACTIONS:
        raise MpdValueError(f"未知动作：{action}")
    for value in fields.values():
        if isinstance(value, str):
            _quote(value)  # 只做校验：坏参数在连 MPD 之前就拒
    with _Conn() as c:
        ACTIONS[action](c, fields)
        return _read_state(c)


def _wait_update(c: _Conn, deadline: float) -> None:
    while "updating_db" in _status(c):
        if time.monotonic() > deadline:
            raise MpdError(f"索引超过 {config.MPD_SYNC_TIMEOUT:g}s 还没完")
        time.sleep(0.5)


def _dirs_and_files(c: _Conn, parent: str = "") -> tuple[list[str], list[str]]:
    """列一层目录与文件；MPD 的返回是扁平的 key/value，目录在前。"""
    rows = c.cmd("listfiles", parent) if parent else c.cmd("listfiles")
    dirs = [v for k, v in rows if k == "directory"]
    files = [v for k, v in rows if k == "file"]
    return dirs, files


_SYNC_LOCK = threading.Lock()


def sync(rebuild: bool = False) -> dict:
    """update 索引；`rebuild` 时按一级目录重建同名播放列表（逐项容错，会打断正在播的那份）。"""
    if not _SYNC_LOCK.acquire(blocking=False):
        raise MpdBusyError("已有一次同步在跑")
    try:
        return _sync(rebuild)
    finally:
        _SYNC_LOCK.release()


def _sync(rebuild: bool) -> dict:
    deadline = time.monotonic() + config.MPD_SYNC_TIMEOUT
    with _Conn() as c:
        c.cmd("update")
        _wait_update(c, deadline)
        if not rebuild:
            return {"updated": True, "dirs": 0, "tracks": 0, "skipped": []}

        existing = {v for k, v in c.cmd("listplaylists") if k == "playlist"}
        dirs, _ = _dirs_and_files(c)
        tracks, skipped = 0, []
        for directory in dirs:
            if time.monotonic() > deadline:
                raise MpdError(f"重建列表超过 {config.MPD_SYNC_TIMEOUT:g}s（已建 {tracks} 首）")
            name = directory.rstrip("/")
            try:
                if name in existing:
                    c.cmd("playlistclear", name)
                _, files = _dirs_and_files(c, directory)
                for filename in files:
                    try:
                        c.cmd("playlistadd", name, f"{directory}/{filename}")
                        tracks += 1
                    except MpdError as e:  # 单曲坏了只跳过，别中断整轮
                        skipped.append({"item": f"{directory}/{filename}", "reason": str(e)[:80]})
            except MpdError as e:
                skipped.append({"item": directory, "reason": str(e)[:80]})
        return {"updated": True, "dirs": len(dirs), "tracks": tracks, "skipped": skipped}
