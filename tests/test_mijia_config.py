"""米家设备表：房间 → 设备，读 YAML、校验、拼 id。写错要报出确切位置。"""

import textwrap

import pytest

from app import config, mijia_config

GOOD = """
rooms:
  客厅:
    吸顶灯: { did: "1", prop: "on-3" }
    温湿度:
      kind: th
      did: "1"
      props: { temperature: "temperature-6", humidity: "relative-humidity-6" }
  次卧:
    烟感:
      kind: smoke
      did: "blt.1"
      props: { status: "status", battery: "battery-level" }
"""


@pytest.fixture
def table(tmp_path, monkeypatch):
    path = tmp_path / "mijia.yaml"
    path.write_text(textwrap.dedent(GOOD), encoding="utf-8")
    monkeypatch.setattr(config, "MIJIA_CONFIG", path)
    mijia_config._cache = None
    return path


def test_two_levels_and_order(table):
    data = mijia_config.load()
    assert [r["label"] for r in data["rooms"]] == ["客厅", "次卧"]
    room = data["rooms"][0]
    assert [s["label"] for s in room["switches"]] == ["吸顶灯"]
    assert [s["label"] for s in room["sensors"]] == ["温湿度"]
    assert set(data["switches"]) == {"客厅/吸顶灯"}
    assert set(data["sensors"]) == {"客厅/温湿度", "次卧/烟感"}


def test_switch_needs_did_and_prop(table):
    assert mijia_config.load()["switches"]["客厅/吸顶灯"] == {
        "id": "客厅/吸顶灯",
        "label": "吸顶灯",
        "did": "1",
        "prop": "on-3",
    }


def test_reload_when_file_changes(table):
    assert [r["label"] for r in mijia_config.load()["rooms"]] == ["客厅", "次卧"]
    table.write_text(
        'rooms:\n  书房:\n    灯: { did: "9", prop: "on" }\n',
        encoding="utf-8",
    )
    import os
    import time

    later = time.time() + 2
    os.utime(table, (later, later))  # mtime 只到秒
    assert [r["label"] for r in mijia_config.load()["rooms"]] == ["书房"]


def test_missing_prop_reports_position(table):
    table.write_text('rooms:\n  客厅:\n    灯: { did: "1" }\n', encoding="utf-8")
    with pytest.raises(mijia_config.ConfigError) as e:
        mijia_config.load()
    assert "客厅.灯" in str(e.value) and "prop" in str(e.value)


def test_bad_kind_and_missing_props(table):
    table.write_text(
        'rooms:\n  客厅:\n    灯:\n      kind: lux\n      did: "1"\n      props: {}\n',
        encoding="utf-8",
    )
    with pytest.raises(mijia_config.ConfigError) as e:
        mijia_config.load()
    assert "kind" in str(e.value)


def test_slash_in_name_rejected(table):
    table.write_text('rooms:\n  客/厅:\n    灯: { did: "1", prop: "on" }\n', encoding="utf-8")
    with pytest.raises(mijia_config.ConfigError):
        mijia_config.load()


def test_empty_rooms_rejected(table):
    table.write_text("rooms: {}\n", encoding="utf-8")
    with pytest.raises(mijia_config.ConfigError):
        mijia_config.load()


def test_missing_file_is_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIJIA_CONFIG", tmp_path / "nope.yaml")
    mijia_config._cache = None
    with pytest.raises(mijia_config.ConfigError) as e:
        mijia_config.load()
    assert "读不到设备表" in str(e.value)


def test_repo_config_is_valid():
    """仓库里那份真的设备表必须能过校验（改了 YAML 立刻能发现写错）。"""
    mijia_config._cache = None
    data = mijia_config.load()
    assert data["rooms"], "至少要有一个房间"
    assert all(s["prop"] for s in data["switches"].values())
