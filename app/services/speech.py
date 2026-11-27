"""播报能力：把机器文本变成念得出来的句子，再交给具体设备。

现在只有米家音箱；加本地 TTS 或别的牌子只在这个文件里加分支，路径与调用方不用动。"""

import logging
import re

from . import mijia

log = logging.getLogger("homeos.speech")

SAY_MAX = 200  # 一次播报的文本上限（实测 280 字也接受，留余量）

_MARKDOWN = re.compile(
    r"```[\s\S]*?```|`([^`]*)`|\*\*?([^*]*)\*\*?|^#+\s*|\[([^\]]*)\]\([^)]*\)", re.M
)


def clean(text: str) -> str:
    """去掉代码块、行内标记、链接地址：音箱会把符号一个一个念出来。"""
    plain = _MARKDOWN.sub(lambda m: m.group(1) or m.group(3) or m.group(2) or " ", text or "")
    return re.sub(r"\s+", " ", plain).strip()


def say(text: str, device: str = "") -> dict:
    """念一句；`device` 为空就用配置里的默认喇叭。返回真正念出去的文本与设备。"""
    said = clean(text)[:SAY_MAX]
    if not said:
        raise ValueError("没有可念的内容")
    return mijia.speak(said, device)
