"""MPD 服务层：行协议、参数转义、动作分派、曲库同步（用一个假 MPD 服务器跑）。"""

import socket
import threading

import pytest

from app import config
from app.services import mpd

SONG = "file: 深度睡眠/a b.mp3\nTitle: Midnight Blue\nArtist: Dan Gibson\nOK\n"


class FakeMpd:
    """极简 MPD：按命令名回脚本化应答，并记录收到的命令行。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.seen: list[str] = []
        self.files = {"深度睡眠": ["a b.mp3", 'quote"d.flac']}
        self.playlists = ["深度睡眠"]
        self.song = True
        self.no_mixer = False
        self.updating = 0  # status 里还回几次 updating_db
        self.fail_add: set[str] = set()  # 这些 uri 的 playlistadd 回 ACK
        self.fail_clear: set[str] = set()  # 这些列表的 playlistclear 回 ACK
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        f = conn.makefile("rwb")
        f.write(b"OK MPD 0.24.0\n")
        f.flush()
        while line := f.readline():
            cmdline = line.decode().rstrip("\n")
            self.seen.append(cmdline)
            f.write(self._reply(cmdline).encode())
            f.flush()
        conn.close()

    def _reply(self, cmdline: str) -> str:
        cmd, _, arg = cmdline.partition(" ")
        if cmd == "status":
            if self.no_mixer:
                return "repeat: 0\nrandom: 0\nplaylistlength: 0\nstate: stop\nvolume: -1\nOK\n"
            rows = [
                "volume: 30",
                "repeat: 1",
                "random: 1",
                "playlistlength: 2",
                "state: play",
                "xfade: 2",
                "elapsed: 12.5",
                "duration: 240.0",
            ]
            if self.updating:
                rows.append("updating_db: 1")
                self.updating -= 1
            return "\n".join(rows) + "\nOK\n"
        if cmd == "currentsong":
            return SONG if self.song else "OK\n"
        if cmd == "listplaylists":
            return "".join(f"playlist: {p}\n" for p in self.playlists) + "OK\n"
        if cmd == "listfiles":
            if arg:
                return "".join(f"file: {name}\n" for name in self.files.get(arg, [])) + "OK\n"
            return "".join(f"directory: {name}\n" for name in self.files) + "OK\n"
        if cmd == "load" and arg in self.fail_clear:
            return "ACK [50@0] {load} No such playlist\n"
        if cmd == "playlistclear":
            if arg in self.fail_clear:
                return "ACK [50@0] {playlistclear} No such playlist\n"
            return "OK\n"
        if cmd == "playlistadd":
            uri = arg.partition(" ")[2]
            if uri in self.fail_add:
                return "ACK [50@0] {playlistadd} No such directory\n"
            return "OK\n"
        return "OK\n"

    def close(self):
        self.sock.close()


@pytest.fixture
def fake(monkeypatch):
    srv = FakeMpd()
    monkeypatch.setattr(config, "MPD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "MPD_PORT", srv.port)
    monkeypatch.setattr(config, "MPD_TIMEOUT", 5.0)
    yield srv
    srv.close()


def test_state_flattens_status_and_currentsong(fake):
    st = mpd.state()
    assert st["state"] == "play" and st["volume"] == 30
    assert st["repeat"] is True and st["random"] is True
    assert st["crossfade"] == 2  # MPD 的键叫 xfade
    assert st["song"]["title"] == "Midnight Blue"
    # 播放位置取自 status；currentsong 里没有这两个字段
    assert st["song"]["elapsed"] == 12.5 and st["song"]["duration"] == 240.0
    assert st["playlist"]["length"] == 2


def test_state_without_song(fake):
    fake.song = False
    assert mpd.state()["song"] is None


def test_play_sets_what_is_given(fake):
    mpd.control("play", playlist="深度睡眠", volume=30, random=True, crossfade=2)
    for line in ("load 深度睡眠", "random 1", "crossfade 2", "setvol 30", "play"):
        assert line in fake.seen


def test_play_clears_queue_before_load(fake):
    """`load` 是追加：不清空的话每晚都往队列里多叠一份整张列表。"""
    fake.seen.clear()
    mpd.control("play", playlist="深度睡眠")
    assert fake.seen.index("clear") < fake.seen.index("load 深度睡眠")


def test_volume_only_touches_volume(fake):
    mpd.control("volume", volume=26)
    assert "setvol 26" in fake.seen
    assert not [s for s in fake.seen if s.startswith(("random", "repeat", "crossfade", "load"))]


def test_terminal_actions(fake):
    for action in ("stop", "next", "prev", "pause"):
        mpd.control(action)
    mpd.control("pause", on=False)
    assert {"stop", "next", "previous", "pause 1", "pause 0"} <= set(fake.seen)


def test_apostrophe_and_backslash_are_quoted(fake):
    """MPD 把 `'` 当引号开头（未加引号会回 Invalid unquoted character），`\` 会被吃掉。"""
    fake.files = {"深度睡眠": ["Dan Gibson's A.mp3", "a\\b.mp3"]}
    fake.playlists = []
    mpd.sync(rebuild=True)
    assert 'playlistadd 深度睡眠 "深度睡眠/Dan Gibson\'s A.mp3"' in fake.seen
    assert [s for s in fake.seen if "a\\\\b.mp3" in s], "反斜杠要加引号并转义"


def test_names_with_space_and_quote_are_escaped(fake):
    fake.playlists = []
    mpd.sync(rebuild=True)
    assert "playlistadd 深度睡眠 深度睡眠/a b.mp3" not in fake.seen  # 走的是引号形式
    assert 'playlistadd 深度睡眠 "深度睡眠/a b.mp3"' in fake.seen
    assert [s for s in fake.seen if 'quote\\"d.flac' in s], "双引号要转义"


def test_sync_waits_for_index(fake):
    fake.updating = 2
    assert mpd.sync()["updated"] is True
    assert fake.seen[0] == "update"
    assert sum(1 for s in fake.seen if s == "status") >= 3


def test_sync_rebuild_counts_tracks(fake):
    fake.files = {"深度睡眠": ["ok.mp3", "ok2.mp3"], "白噪音": ["rain.mp3"]}
    out = mpd.sync(rebuild=True)
    assert out["dirs"] == 2 and out["tracks"] == 3 and out["skipped"] == []


def test_sync_skips_bad_file_instead_of_failing(fake):
    fake.files = {"深度睡眠": ["ok.mp3", "坏.wav"]}
    fake.playlists = []
    fake.fail_add = {"深度睡眠/坏.wav"}
    out = mpd.sync(rebuild=True)
    assert out["tracks"] == 1
    assert [s["item"] for s in out["skipped"]] == ["深度睡眠/坏.wav"]


def test_sync_skips_bad_directory_instead_of_failing(fake):
    fake.playlists = ["深度睡眠"]
    fake.fail_clear = {"深度睡眠"}
    out = mpd.sync(rebuild=True)
    assert [s["item"] for s in out["skipped"]] == ["深度睡眠"]


def test_unreachable_mpd_is_a_domain_error(monkeypatch):
    monkeypatch.setattr(config, "MPD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "MPD_PORT", 1)
    with pytest.raises(mpd.MpdError):
        mpd.state()


def test_ack_no_such_becomes_not_found(fake):
    fake.fail_clear = {"深度睡眠"}
    with pytest.raises(mpd.MpdNotFound):
        mpd.control("play", playlist="深度睡眠")


def test_volume_without_mixer_is_none(fake):
    fake.no_mixer = True
    assert mpd.state()["volume"] is None
