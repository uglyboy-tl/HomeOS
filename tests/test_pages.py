"""静态页面路由与功能页扫描：页面就是文件，原样发出去。"""

import json

import pytest
from fastapi.testclient import TestClient

from app import config, scan
from app.main import app


@pytest.fixture
def web(tmp_path, monkeypatch):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "home.html").write_text("<!doctype html><title>home</title>")
    (root / "mijia.html").write_text("<!doctype html><title>mijia</title>")
    (root / "agent.html").write_text("<!doctype html><title>agent</title>")
    monkeypatch.setattr(config, "WEB_ROOT", root)
    monkeypatch.setattr(config, "PAGES_DIR", root / "pages")
    return root


def make_page(web, slug, meta=None, body="<h1>x</h1>"):
    d = web / "pages" / slug
    d.mkdir()
    (d / "index.html").write_text(f"<!doctype html><body>{body}</body>")
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return d


def test_fixed_pages_are_plain_files(web):
    c = TestClient(app)
    assert "<title>home</title>" in c.get("/").text
    assert "<title>mijia</title>" in c.get("/mijia").text
    assert "<title>agent</title>" in c.get("/agent").text


def test_page_is_served_verbatim(web):
    make_page(web, "x", {"title": "x", "icon": "star"}, body="<h1>agent 写的</h1>")
    r = TestClient(app).get("/p/x")
    assert r.status_code == 200
    assert r.text == "<!doctype html><body><h1>agent 写的</h1></body>"  # 一个字都不改


def test_unknown_pages_are_404(web):
    c = TestClient(app)
    assert c.get("/p/ghost").status_code == 404
    assert c.get("/p/BAD NAME").status_code == 404
    assert c.get("/nope").status_code == 404


def test_traversal_is_blocked(web):
    (web / "secret.txt").write_text("s")
    c = TestClient(app)
    assert c.get("/assets/../secret.txt").status_code == 404
    assert c.get("/assets/%2e%2e/secret.txt").status_code == 404


def test_page_assets(web):
    d = make_page(web, "x", {"title": "x"})
    (d / "assets").mkdir()
    (d / "assets" / "a.png").write_bytes(b"\x89PNG")
    assert TestClient(app).get("/pages/x/assets/a.png").status_code == 200
    assert TestClient(app).get("/pages/x/assets/../../home.html").status_code == 404


def test_features_sorted_and_skips_broken(web):
    make_page(web, "b", {"title": "乙", "icon": "star", "order": 2})
    make_page(web, "a", {"title": "甲", "icon": "cloud", "order": 1})
    make_page(web, "zzz", {"title": "无 order"})
    make_page(web, "bad", None)  # 没 meta.json
    (web / "pages" / "nohtml").mkdir()  # 没 index.html
    (web / "pages" / "nohtml" / "meta.json").write_text('{"title":"x"}')
    got = [it["slug"] for it in scan.features()]
    assert got == ["a", "b", "zzz"]
    assert scan.features()[2]["order"] == 9999


def test_bad_icon_falls_back(web):
    make_page(web, "x", {"title": "x", "icon": '"/><script>'})
    assert scan.features()[0]["icon"] == "square"


def test_api_pages_lists_features(web):
    make_page(web, "x", {"title": "示例", "icon": "star"})
    body = TestClient(app).get("/api/info/pages").json()
    assert body[0]["slug"] == "x"


def test_symlink_outside_web_is_blocked(web, tmp_path):
    """web/ 里放一个指向外面的软链：resolve() 之后不在 WEB_ROOT 下，必须 404。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    (web / "assets" / "link.txt").symlink_to(secret)
    assert TestClient(app).get("/assets/link.txt").status_code == 404


def test_page_sets_a_hardened_cookie(web, monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "a" * 32)
    cookie = TestClient(app, base_url="http://testserver").get("/").headers["set-cookie"]
    assert cookie.startswith("homeos=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Max-Age=2592000" in cookie
    assert "Secure" not in cookie  # http 下不发 Secure，否则设备收不到


def test_cookie_is_secure_over_https(web, monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "a" * 32)
    cookie = TestClient(app, base_url="https://testserver").get("/").headers["set-cookie"]
    assert "Secure" in cookie


def test_ready_does_not_hand_out_cookies(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "a" * 32)
    assert "set-cookie" not in TestClient(app).get("/api/ready").headers


def test_cache_headers(web, monkeypatch):
    (web / "assets" / "a.js").write_text("//x")
    c = TestClient(app)
    page = c.get("/").headers["cache-control"]
    assert "no-store" in page

    monkeypatch.setattr(config, "DEV", True)
    assert "no-store" in c.get("/assets/a.js").headers["cache-control"]

    monkeypatch.setattr(config, "DEV", False)
    assert "max-age" in c.get("/assets/a.js").headers["cache-control"].lower()


def test_absolute_and_dotdot_paths_are_404(web):
    c = TestClient(app)
    for path in (
        "/p/../home.html",
        "/p/%2e%2e",
        "/assets//etc/passwd",
        "/pages/x/assets//etc/passwd",
    ):
        assert c.get(path).status_code == 404, path
