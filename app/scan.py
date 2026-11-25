"""扫描 web/pages/：一个目录一个功能页，坏一个只是少一格。"""

import json
import logging
import re

from . import config

log = logging.getLogger("homeos.scan")

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
ICON = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def features() -> list[dict]:
    out: list[dict] = []
    if not config.PAGES_DIR.is_dir():
        return out
    for d in sorted(config.PAGES_DIR.iterdir()):
        if not d.is_dir() or not SLUG.match(d.name):
            continue
        if not (d / "index.html").is_file():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("跳过 %s：meta.json 读不出来（%s）", d.name, e)
            continue
        if not isinstance(meta, dict):
            log.warning("跳过 %s：meta.json 不是对象", d.name)
            continue
        icon = str(meta.get("icon") or "square")
        order = meta.get("order")
        out.append(
            {
                "slug": d.name,
                "title": str(meta.get("title") or d.name),
                "icon": icon if ICON.match(icon) else "square",
                "order": order if isinstance(order, (int, float)) else 9999,
            }
        )
    out.sort(key=lambda it: (it["order"], it["slug"]))
    return out
