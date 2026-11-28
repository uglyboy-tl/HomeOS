# homeos

家庭局域网的能力网关，跑在 NAS 的一个容器里。**后端是主体**（米家设备控制、家庭 agent、语音识别与播报、
MPD、通知），**web 只是它的第一个消费者**——墨水屏（inkPalm 5、Kindle 浏览器）打开就是家里的一面面板。
Jellyfin、Immich、Miniflux 这类已有成熟 API 的服务不包一层。

代码原则与"怎么做新东西"看 [`AGENTS.md`](AGENTS.md)；这里只记已经做完的样子。

## 现在能用的

| 页面 | 状态 |
|---|---|
| `/` | 首页：两张固定卡片写死在 HTML 里，功能页从 `/api/info/pages` 追加；能力异常时才出现一行状态带 |
| `/mijia` | 米家：房间标签 + 开关 + 温湿度/烟感；缓存首帧、只拉当前房间、写后读回才变色、离线时点行即重连 |
| `/agent` | 助手：模式来自 `config/agents.yaml`（默认聊天/改页面），对话走 SSE 跟随一轮，支持语音输入、停止、新对话 |
| `/p/<slug>` | 功能页：`web/pages/<slug>/` 一个目录一页，agent 写的就是这里 |

| 能力 | 接口 | 状态 |
|---|---|---|
| 设备 | `GET /api/info/mijia`、`GET /api/mijia/values`、`PUT /api/mijia/power` | 缓存分层（开关 3s / 温湿度 300s / 烟感 900s），写后 0.4s×4 次读回确认 |
| 智能 | `POST /api/agent/{ask,prompt,abort,new-session}`、`GET /api/agent/{turn,stream}` | pi 子进程按模式常驻，SSE 限流 4 帧/秒；`ask` 是一次性调用 |
| 播报 | `POST /api/speech` | 去 markdown + 截断 200 字，设备由 `mijia.yaml` 的 `tts.did` 决定 |
| 语音 | `WS /api/asr/stream` | Azure 流式识别，`partial` / `final` / `error` 三类帧 |
| 音乐 | `GET /api/mpd/state`、`PUT /api/mpd/playback`、`POST /api/mpd/{queue/next,queue/prev,playlist,library/update,library/rebuild}`、`PATCH /api/mpd/settings` | python-mpd2；crontab 的定时播放/音量已全部切到这些接口 |
| 通知 | `POST /api/notify` | 只推 Gotify；要出声另外调 `/api/speech` |
| 监控 | `GET /api/metrics` | Beszel 只读账号 |
| 自述 | `GET /api/info`、`/api/info/{agent,agent/state,mijia,pages}` | 能力登记簿（7 项）+ 配置问题；外部程序先看它再决定发不发执行命令 |
| 健康 | `GET /api/ready` | 容器 healthcheck，不认证 |

错误一律 `{"error":{"code","message"}}`，14 个稳定 `code`；完整契约表在
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §6（`tests/test_contract.py` 有路由快照，改接口会红）。

## 本机开发

```bash
./scripts/dev.sh up            # web 跑在 127.0.0.1:8080，agent 由 pi 子进程按需拉起
./scripts/dev.sh restart       # 停掉再起，改过导出环境变量或依赖时用
./scripts/dev.sh status|log|down

uv run pytest -q                                          # 322 个用例，约 16 秒，不联网
uv run pytest -q --cov=app --cov-fail-under=95            # CI 门槛 95%，当前 96%
uv run ruff check . && uv run ruff format .
bunx @biomejs/biome@2.6.8 check --write .                 # 前端 lint + 格式化（本机手动跑，CI 不装 bun）
```

改 `web/pages/*` 或 `config/mijia.yaml` 不用重启（前者刷新即见，后者按 mtime 重读）。
前端格式化只覆盖人能改的那部分（`web/*.html`、`web/assets/*`，见 `biome.jsonc`）；`web/pages/**` 归 agent，不参与。

## 部署（NAS）

栈目录 `/srv/Dev/Work/Docker/homeos`（树莓派，arm64）。镜像由 CI 出双架构，`compose.yaml` 里
`read_only: true` + tmpfs + 三个挂载点；**镜像内部细节、构建方式、调试形态见 [`docker/README.md`](docker/README.md)**。

```bash
# NAS 上：cp .env.example .env 填 key；docker compose pull && up -d
# 本机改完代码：./scripts/sync.sh --go [--restart]   推 app/ 与 web/，config/ 与 .env 不动
```

米家要两样，缺一样米家页就 503（其它页不受影响）：

| 要什么 | 放哪 |
|---|---|
| 设备表 `mijia.yaml` | 宿主机 `./config/`（挂进 `/app/config`；空目录会从镜像模板生成），填法见 [`config/README.md`](config/README.md) |
| 登录态（`auth.json` + 设备 json） | 宿主机 `$XDG_CONFIG_HOME/mijia-api/`（默认 `~/.config/mijia-api/`），挂到 `/data/.config/mijia-api` |

登录态不拷贝：哪台机器跑容器就在哪台扫码一次，扫码后 `docker compose restart` 让容器重建客户端。

## 认证

| 谁 | 怎么过 |
|---|---|
| 自家前端 | 页面响应种 `homeos` cookie（`HttpOnly; SameSite=Strict`，值是令牌的 HMAC 派生值），fetch 自动带上 |
| 其他设备/程序 | 请求头 `X-HomeOS-Token: <config/server.token>` 或 `Authorization: Bearer` |
| `/api/ready` | 不认证 |

令牌由容器首次启动写进 `config/server.token`（0600，之后不变）。未配令牌时只有 `HOMEOS_DEV=1` 放行。
这层挡误用与扫描，不是定向攻击——主边界在网络层（威胁模型见 ARCHITECTURE §8）。

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 定位、接口分类与契约、目录、皮肤、进程与卷、拍板过的不做
- [`web/DESIGN.md`](web/DESIGN.md) — 前端设计规范（墨水屏原则、排版与尺寸、数据与刷新、验收）
- [`docker/README.md`](docker/README.md) — 镜像、运行时布局、两种部署形态、构建
- [`config/README.md`](config/README.md) — `homeos.yaml` / `mijia.yaml` / `agents.yaml` 字段表
- [`AGENTS.md`](AGENTS.md) — 代码原则、接口设计原则、已知的坑

## 没做的

- 相册（Immich 自己有 API）、页面后端与数据库（页面状态只在 `localStorage`）
- 多用户与登录体系：没有用户概念，只有 cookie 与程序令牌
