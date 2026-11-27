"""MPD 接口：状态、传输、队列、播放器参数、列表、曲库。"""

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..lib import health
from ..lib.errors import ApiError
from ..services import mpd

router = APIRouter(
    tags=["mpd"],
    dependencies=health.deps("mpd"),
    responses={
        404: {"description": "列表或曲目不存在"},
        409: {"description": "状态冲突或正在同步"},
    },
)
log = logging.getLogger("homeos.mpd")


class PlaybackReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["playing", "paused", "stopped"]


class SettingsReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume: int | None = Field(default=None, ge=0, le=100)
    random: bool | None = None
    repeat: bool | None = None
    crossfade: int | None = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(value is None for value in (self.volume, self.random, self.repeat, self.crossfade)):
            raise ValueError("至少要改一个参数")
        return self


class PlaylistReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


def _call(fn, *args, **kwargs) -> dict:
    """领域错误 → HTTP：找不到 404，正在同步 409，MPD 不可用 503。"""
    try:
        out = fn(*args, **kwargs)
    except mpd.MpdNotFound as e:
        raise ApiError(404, "not_found", str(e)) from e
    except mpd.MpdConflict as e:
        log.info("MPD 当前状态不允许：%s", e)
        raise ApiError(409, "conflict", str(e)) from e
    except mpd.MpdBusyError as e:
        raise ApiError(409, "busy", str(e)) from e
    except mpd.MpdError as e:
        log.warning("MPD 不可用：%s", e)
        raise health.failing("mpd", e) from e
    health.note("mpd", True)
    return out


@router.get("/api/mpd/state")
def api_state() -> dict:
    """播放状态、当前曲目、播放器参数。"""
    return _call(mpd.state)


@router.put("/api/mpd/playback")
def api_playback(req: PlaybackReq) -> dict:
    """要播放/暂停/停止，不是"执行哪个动作"。"""
    return _call(mpd.playback, req.state)


@router.post("/api/mpd/queue/next")
def api_next() -> dict:
    return _call(mpd.queue_next)


@router.post("/api/mpd/queue/prev")
def api_prev() -> dict:
    return _call(mpd.queue_prev)


@router.patch("/api/mpd/settings")
def api_settings(req: SettingsReq) -> dict:
    """改播放器参数；一次改多个也走命令列表，不会改一半。"""
    return _call(
        mpd.settings,
        volume=req.volume,
        random=req.random,
        repeat=req.repeat,
        crossfade=req.crossfade,
    )


@router.post("/api/mpd/playlist")
def api_playlist(req: PlaylistReq) -> dict:
    """换一个列表并开始播（清空队列 → 载入 → 播放 是一步）。"""
    name = req.name.strip()
    if not name:
        raise ApiError(400, "bad_input", "name 是空的")
    log.info("playlist %s", name)
    return _call(mpd.playlist, name)


@router.post("/api/mpd/library/update")
def api_library_update() -> dict:
    """让 MPD 重新扫曲库并等索引建完。"""
    return _call(mpd.library_update)


@router.post("/api/mpd/library/rebuild")
def api_library_rebuild() -> dict:
    """按一级目录重建同名播放列表（会打断正在播的那份）。"""
    out = _call(mpd.library_rebuild)
    log.info(
        "library rebuild dirs=%s tracks=%s skipped=%s",
        out.get("dirs"),
        out.get("tracks"),
        len(out.get("skipped") or []),
    )
    return out
