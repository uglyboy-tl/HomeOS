"""MPD 服务层：状态整形、播放控制、播放器参数、列表载入、曲库同步（用一个假 MPD 服务器跑）。"""

import socket
import threading

import pytest

from app import config
from app.services import mpd

SONG = "file: 深度睡眠/a b.mp3\nTitle: Midnight Blue\nArtist: Dan Gibson\nOK\n"


def tokens(line: str) -> list[str]:
    """按 MPD 的分词规则拆一行：双引号包起来的部分算一个参数，`\\` 是转义。"""
    out: list[str] = []
    buf = ""
    quoted = False
    escaped = False
    for char in line:
        if escaped:
            buf += char
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == " " and not quoted:
            out.append(buf)
            buf = ""
        else:
            buf += char
    if buf or quoted:
        out.append(buf)
    return out


class FakeMpd:
    """极简 MPD：按命令名回脚本化应答，并记录收到的命令行与参数。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.seen: list[str] = []
        self.calls: list[list[str]] = []
        self.files = {"深度睡眠": ["a b.mp3", 'quote"d.flac']}
        self.playlists = ["深度睡眠"]
        self.song = True
        self.no_mixer = False
        self.updating = 0  # status 里还回几次 updating_db
        self.fail_add: set[str] = set()  # 这些 uri 的 playlistadd 回 ACK
        self.fail_clear: set[str] = set()  # 这些列表的 playlistclear 回 ACK
        self.not_playing = False  # next/previous 回 "Not playing"
        self.in_list = False
        self.pending: list[str] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        file = conn.makefile("rwb")
        file.write(b"OK MPD 0.24.0\n")
        file.flush()
        while line := file.readline():
            cmdline = line.decode().rstrip("\n")
            self.seen.append(cmdline)
            reply = self._dispatch(cmdline)
            if reply:
                file.write(reply.encode())
                file.flush()
        conn.close()

    def _dispatch(self, cmdline: str) -> str:
        if cmdline == "command_list_ok_begin":
            self.in_list, self.pending = True, []
            return ""
        if cmdline == "command_list_end":
            self.in_list = False
            body = ""
            for one in self.pending:
                reply = self._reply(one)
                body += reply[:-3] + "list_OK\n" if reply.endswith("OK\n") else reply
            self.pending = []
            return body + "OK\n"
        if self.in_list:
            self.pending.append(cmdline)
            return ""
        return self._reply(cmdline)

    def _reply(self, cmdline: str) -> str:
        parts = tokens(cmdline)
        self.calls.append(parts)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
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
            return "".join(f"playlist: {one}\n" for one in self.playlists) + "OK\n"
        if cmd == "listfiles":
            if arg:
                return "".join(f"file: {name}\n" for name in self.files.get(arg, [])) + "OK\n"
            return "".join(f"directory: {name}\n" for name in self.files) + "OK\n"
        if cmd in ("load", "playlistclear") and arg in self.fail_clear:
            return f"ACK [50@0] {{{cmd}}} No such playlist\n"
        if cmd in ("next", "previous") and self.not_playing:
            return f"ACK [55@0] {{{cmd}}} Not playing\n"
        if cmd == "playlistadd":
            uri = parts[2] if len(parts) > 2 else ""
            if uri in self.fail_add:
                return "ACK [50@0] {playlistadd} No such directory\n"
            return "OK\n"
        return "OK\n"

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]

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


def test_volume_without_mixer_is_none(fake):
    fake.no_mixer = True
    assert mpd.state()["volume"] is None


def test_playback_maps_states_to_commands(fake):
    fake.calls.clear()
    mpd.playback("playing")
    mpd.playback("paused")
    mpd.playback("stopped")
    sent = [call for call in fake.calls if call[0] not in ("status", "currentsong")]
    assert sent == [["play"], ["pause", "1"], ["stop"]]


def test_settings_only_touches_what_is_given(fake):
    fake.calls.clear()
    mpd.settings(volume=26)
    sent = [call for call in fake.calls if call[0] not in ("status", "currentsong")]
    assert sent == [["setvol", "26"]]


def test_settings_applies_several_atomically(fake):
    fake.calls.clear()
    mpd.settings(volume=30, random=True, repeat=True, crossfade=2)
    sent = [call for call in fake.calls if call[0] not in ("status", "currentsong")]
    assert sent == [["setvol", "30"], ["random", "1"], ["repeat", "1"], ["crossfade", "2"]]
    assert "command_list_ok_begin" in fake.seen  # 改一半就断的事不会发生


def test_queue_navigation(fake):
    fake.calls.clear()
    mpd.queue_next()
    mpd.queue_prev()
    sent = [call[0] for call in fake.calls if call[0] not in ("status", "currentsong")]
    assert sent == ["next", "previous"]


def test_playlist_clears_then_loads_then_plays(fake):
    """`load` 是追加：不清空的话每晚都往队列里多叠一份整张列表。"""
    fake.calls.clear()
    mpd.playlist("深度睡眠")
    sent = [call for call in fake.calls if call[0] not in ("status", "currentsong")]
    assert sent == [["clear"], ["load", "深度睡眠"], ["play"]]
    assert "command_list_ok_begin" in fake.seen  # 三步要么都成，要么都不成


def test_library_update_waits_for_index(fake):
    fake.updating = 2
    fake.calls.clear()
    assert mpd.library_update() == {"updated": True}
    assert fake.commands().count("update") == 1
    assert fake.commands().count("status") >= 3


def test_library_rebuild_counts_tracks(fake):
    fake.files = {"深度睡眠": ["ok.mp3", "ok2.mp3"], "白噪音": ["rain.mp3"]}
    out = mpd.library_rebuild()
    assert out["dirs"] == 2 and out["tracks"] == 3 and out["skipped"] == []


def test_library_rebuild_skips_bad_file_instead_of_failing(fake):
    fake.files = {"深度睡眠": ["ok.mp3", "坏.wav"]}
    fake.playlists = []
    fake.fail_add = {"深度睡眠/坏.wav"}
    out = mpd.library_rebuild()
    assert out["tracks"] == 1
    assert [item["item"] for item in out["skipped"]] == ["深度睡眠/坏.wav"]


def test_library_rebuild_skips_bad_directory_instead_of_failing(fake):
    fake.playlists = ["深度睡眠"]
    fake.fail_clear = {"深度睡眠"}
    out = mpd.library_rebuild()
    assert [item["item"] for item in out["skipped"]] == ["深度睡眠"]


def test_names_with_spaces_and_quotes_survive_the_wire(fake):
    """转义由 python-mpd2 负责：带空格、单引号、双引号、反斜杠的名字要原样到达。"""
    names = ["Dan Gibson's A.mp3", 'quote"d.flac', "a\\b.mp3"]
    fake.files = {"深度睡眠": names}
    fake.playlists = []
    mpd.library_rebuild()
    added = [call[2] for call in fake.calls if call[0] == "playlistadd"]
    assert added == [f"深度睡眠/{name}" for name in names]


def test_missing_playlist_is_not_found(fake):
    fake.fail_clear = {"深度睡眠"}
    with pytest.raises(mpd.MpdNotFound):
        mpd.playlist("深度睡眠")


def test_next_while_stopped_is_a_conflict(fake):
    fake.not_playing = True
    with pytest.raises(mpd.MpdConflict):
        mpd.queue_next()


def test_unreachable_mpd_is_a_domain_error(monkeypatch):
    monkeypatch.setattr(config, "MPD_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "MPD_PORT", 1)
    with pytest.raises(mpd.MpdError):
        mpd.state()
