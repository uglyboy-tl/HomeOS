# 架构（v1，NAS 视角）

> **已过时**：本文描述的是重构初期的设想，实现已在多处偏离（无 `shell.html`/iframe、无 `rich.css`/`eink.css`、
> 无 `data-profile`、无页面后端 `api.py`/sqlite、`/api/home/*` 改名为 `/api/mijia/*`）。
> 以代码、`README.md`、`AGENTS.md`、`web/DESIGN.md` 为准，本文仅作历史参考。

替代 `HANDOFF.md` 的 §4/§5/§6。差别只有一条：**v1 不管客户端**。没有 rpi、没有 kiosk 脚本、
没有 systemd、没有本地小服务、不读任何硬件。出问题的入口只有一个：浏览器。

`HANDOFF.md` 其余部分（哪些坑踩过、哪些服务原样搬）仍然有效，见本文 §11。

---

## 1. 目标与非目标

**目标**

1. 一个 Docker 容器跑在 NAS 上，内部起 FastAPI web 服务，`:8080` 给家里所有设备打开。
2. 后端只有一套 API；前端按设备给不同皮肤，同一份 HTML、同一套路由。
3. 新增功能 = 往卷里加一个目录，不需要改后端、不需要重启、不需要重新构建镜像。
   opencode 在容器内就是干这个的。
4. 只读根文件系统：除两个卷之外全部只读，重启后状态一致。

**非目标（v1 明确不做）**

- 客户端侧任何东西：调亮度、开机自启、Chromium 参数、屏保/锁屏、硬件指标、重启关机。
- 用户系统、登录、多租户。家里内网，信任边界是「能连上 8080」。
- 多容器编排、消息队列、外部数据库。sqlite 够。
- HTTPS。等要出内网时一起做（§9）。

## 2. 三层

```
① 功能层   web/pages/<slug>/        唯一能写代码的地方：html + 可选 api.py + 可选 sqlite
② 能力层   app/services/            服务端替浏览器干的活：米家云、ASR/TTS、相册代理、opencode
③ 装配层   app/routes/ + web/assets 路由、皮肤、宫格。只由开发者维护，agent 不碰
```

判断标准：属于「页面自己的事」放 ①，需要密钥/外部服务/长连接放 ②，两者都不是就别做。

## 3. 页面结构：多文档（MPA），没有壳

**v1 没有 `shell.html`，没有 iframe，没有 shell.js。**

旧壳存在的唯一理由是「控制面板和 tab 要同时活着、切 tab 不重载」。控制面板已经整块删掉，
而 iframe 在 inkPalm 5 上的代价是硬的：EinkBro 里每个 iframe 都是一个完整文档 + 一份 JS 堆，
宫格挂 5 个就是 5 份，1GB RAM 的机器上切几次 tab 就开始被回收、白屏重排；而墨水屏本来就
要整屏刷新，省下的那点重绘换来的是内存和复杂度的双重债。

改成最朴素的多文档：

```
GET /                 → 宫格（服务端扫描功能目录生成，零 JS）
GET /p/<slug>         → 功能页（页面自己的 HTML，服务端注入皮肤 + 底部导航）
点导航                → 整页跳转，浏览器历史/后退免费得到
```

- 同一时刻只有一个文档，内存占用与页面数量无关。
- 导航条由服务端拼进 HTML，链接自带 `?p=` 参数，**换页不会丢皮肤**（旧设计要 shell.js 传 profile）。
- 页面之间不共享 JS 运行时：一页崩了只崩那一页，导航在导航条里、照样能走。
- 代价：换页丢内存态。墨水屏页面本来就不该有内存态，要留的东西写 sqlite（`api.py`）或
  `localStorage`，别指望文档活着。
- `grid.html` 是唯一「壳味」的文件，它只是宫格的骨架，不含逻辑。

## 4. 目录

根目录就是镜像构建上下文，`.dockerignore` 排掉 `docs/ tests/ .git/ scripts/`。

```
app/                      镜像内 Python（只读）
  main.py                 create_app()：路由表 + lifespan
  config.py               唯一读环境变量的地方
  render.py               皮肤/导航注入 + 功能目录扫描（三次字符串替换，无模板引擎）
  routes/
    pages.py              GET /  /s/<name>  /p/<slug>  /assets/*  /pages/<slug>/assets/*
    feature.py            POST /pageapi/<slug>/<action> → pageworker 反代（步骤 2）
    home.py               米家（步骤 5）
    voice.py              /api/asr  /api/say  /tts/c/<hash>.mp3（步骤 6）
    album.py              Immich 代理（暂缓）
    oc.py                 /oc/* → opencode（步骤 4）
    health.py             /api/ready  /api/pages
  services/               home.py speech.py asr.py tts.py opencode.py
  pageworker.py           功能页后端进程（127.0.0.1:14101，步骤 2）

web/                      前端
  grid.html               首页（宫格）骨架：<!--homeos:head--> 与 <!--cards--> 两个标记
  system/                 ★固定页（human 维护，不在卷里，agent 改不到）
    pages.json            固定页清单：name/title/icon/order，`href` 或同名 .html
    mijia.html            米家控制页（先占位，内容待定）
  assets/                 tokens.css icons.svg（实现里只有这两样共享资源）
  pages/                  ★卷 1：功能目录（agent 唯一可写）

opencode/                 opencode 的东西（XDG_CONFIG_HOME=<repo>，配置根就是它）
  AGENTS.md  agents/{chat,pages,device}.md  opencode.jsonc  cli.json

docker/  Dockerfile entrypoint.sh
compose.yaml              NAS 部署（read_only + 两个卷）
.env.example              data/env 的模板
docker/entrypoint.sh      唯一启动入口：容器无参数=前台；本机带子命令=后台托管
tests/  docs/
```

**两类页面，一条边界**

| | 位置 | 谁能改 | 路由 |
|---|---|---|---|
| 固定页 | `web/system/<name>.html` + `system/pages.json` | 只有人（不在卷里） | `/`（home）、`/s/<name>` |
| 功能页 | `web/pages/<slug>/` | agent（卷） | `/p/<slug>` |

v1 的固定页只有两个：`home`（首页宫格，路由就是 `/`，`grid:false` 所以自己不进宫格）
与 `mijia`（米家控制，先占位）。清单里没登记的 `web/system/*.html` 访问不到；
条目缺文件或缺 name 只丢一条，不影响别的页。宫格与底部导航都由「清单 + 目录扫描」拼出来，
没有第二处要同步。

**一个功能 = 一个目录**（相对 HANDOFF 的 `pN.html` 平铺，这是唯一的结构改动）：

```
web/pages/<slug>/
  meta.json   {"title":"天气","icon":"cloud","order":3}
  index.html  页面本体
  api.py      可选：init(db) / handle(action, payload, db)      ← 步骤 2
  data.db     可选：sqlite，由 api.py 自己建                      ← 步骤 2
  assets/     可选：本页图片、第三方脚本
```

- 删一个功能 = `rm -rf` 那个目录，宫格里自动消失（旧设计的 `tabs.json` 双写清单取消，
  改由服务端扫描目录 + 读 `meta.json`，`GET /api/pages` 返回）。少一处能写坏的地方。
- `data.db` 跟着功能目录走：备份、回滚、复制功能都只是一次 `cp -r`。
- 同页串行、并发上限 2、`init(db)` 只跑一次、改 `api.py` 下一个请求即生效（按 mtime 热加载）。
- `meta.json` 缺 `icon` 用 `square` 兜底、缺 `title` 用 slug、缺 `order` 排最后；
  `icon` 不匹配 `[a-z0-9-]+` 一律当 `square`（它会被拼进 `<use href>`）。

## 5. 皮肤：靠 `?p=` 参数

| URL | profile | 效果 |
|---|---|---|
| `/`、`/p/weather` | rich | 96×54 虚拟画布，圆角、阴影、彩色 |
| `/?p=eink`、`/p/weather?p=eink` | eink | 36×72 虚拟画布，纯黑白、零圆角、零阴影、零动画 |

- 只认参数，不嗅探 UA、不读 cookie。默认 rich，`?p=eink` 切墨水屏。
- 服务端在 `<html>` 上写 `data-profile`、注入对应皮肤 `<link>`（不是 JS 改，避免首屏闪一下）。
- 导航条由服务端拼，每条链接都带 `?p=`，所以在页面之间跳转不会掉回 rich。
  **页面自己写的内链必须带 `?p=`**（`page.js` 给一个 `url()` 小工具），这条写进 `AGENTS.md`。
- 两份 CSS 只覆盖变量，页面**只写 rem、只用四色、只用四档字号**：

```css
:root   { --vw:96; --vh:54; --bg:#faf7f2; --fg:#1f1b16; --accent:#e8a33d; --r:1.8rem; }
html[data-profile='eink'] { --vw:36; --vh:72; --bg:#fff; --fg:#000; --accent:#000; --r:0; }
html { font-size: calc(min(100vw / var(--vw), 100vh / var(--vh))); }  /* 1rem = 画布宽的 1/--vw */
```

1rem 的实际大小：桌面 1280 宽约 13.3px、1080 宽约 11.3px、inkPalm（720 宽，eink）20px、
Kindle（600 宽，eink）16.7px。**不要乘 10**，那是早期文档里的笔误，乘了 1rem 就变成 130px。

720×1440（inkPalm 5）、600×800（Kindle）、1080×2400（手机）、1920×1080（桌面）等比缩放。
兜不住的：写死 px 宽度、彩色位图、canvas 里的彩色。这三样写进 `AGENTS.md` 当硬约束。

## 6. 墨水屏性能预算

实质内容已搬进 `web/DESIGN.md`（第 4 节）与 `.agents/skills/modern-web-guidance/SKILL.md`，
这里不再重复，避免两份口径（早期版本写过“不引 Alpine”“eink.css”，与本项目现状相反）。

## 7. 容器与进程

单容器三进程，`docker/entrypoint.sh` 前台起、任一退出就整体退出（不引 s6）。同一个脚本在本机
带子命令时切到后台托管，所以只有一份启动逻辑：

| 进程 | 监听 | 说明 |
|---|---|---|
| `uvicorn app.main:app` | `0.0.0.0:8080` | 对外唯一入口 |
| `python -m app.pageworker` | `127.0.0.1:14101` | 页面后端，崩了只影响单页 |
| `opencode serve` | `127.0.0.1:14100` | agent，只在容器内可达 |

- 根文件系统 `read_only: true`，`/tmp` 走 tmpfs（512m，agent 的唯一可写暂存区）。
- 配置根：`XDG_CONFIG_HOME=/app`（配置在 `/app/opencode`），`XDG_CACHE_HOME=/data/cache`。
- 卷 1 `./pages:/app/web/pages`（功能）；卷 2 `./data:/data`（运行时状态，可丢）。
- `HOME=/data`（米家 token 落 `/data/.config/mijia-api/auth.json`）、
  `XDG_DATA_HOME=/data/opencode`（**session 与 snapshot 在这里，撤销靠它**）。
- 健康检查 `GET /api/ready`，`restart: unless-stopped`。
- `// ponytail:` 单容器意味着任一进程死掉会整体重启、opencode 长会话被打断。
  真需要进程级自愈再换 s6-overlay，现在不值得。

**构建与运行**

```bash
# 本机开发（同一个脚本，带子命令时后台托管）
./docker/entrypoint.sh up|down|restart [web|pages|agent|all]|status|log

# NAS
mkdir -p data && cp .env.example data/env && $EDITOR data/env   # 填 OPENCODE_SERVER_PASSWORD
docker compose up -d --build      # 镜像 homeos:dev，容器 homeos
docker compose logs -f homeos
curl -fsS localhost:8080/api/ready
```

镜像里 opencode 只装 arm64（`opencode-linux-arm64.tar.gz`，钉死 `OPENCODE_VERSION`），
amd64 构建会直接失败而不是悄悄装错。**这份 Dockerfile 还没在真机上构建过**（本机没有 docker），
第一次在 NAS 上跑要盯三件事：opencode 能不能在只读配置根下起来、`opencode --version` 是否成功、
`/api/ready` 是否 200。

## 8. 契约（浏览器能看到的全部）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 宫格，`?p=eink` 切墨水屏 |
| GET | `/p/<slug>` | 功能页，注入皮肤与导航 |
| GET | `/s/<name>` | 固定页（只认 `system/pages.json` 里登记的） |
| GET | `/api/pages` | 扫描功能目录得到的清单（slug/title/icon/order） |
| GET | `/api/ready` | 容器健康检查 |
| GET | `/api/home/state` | 米家全量（内存 3s / 温湿度 300s / 烟感 900s） |
| POST | `/api/home/switch` | `{id,on}`，返回读回确认后的真实值 |
| POST | `/api/asr` | 16k 单声道 WAV → `{text}` |
| POST | `/api/say` | 切句合成，返回 `/tts/c/<hash>.mp3` |
| GET | `/tts/c/<hash>.mp3` | 已合成音频，`max-age=86400` |
| GET | `/api/album/*` | Immich 代理（服务端注入 key） |
| GET/POST | `/pageapi/<slug>/<action>` | 页面自己的后端，5s 超时、并发 2 |
| ANY | `/oc/*` | opencode 反代（注入 Basic 认证，SSE 流式） |

页面作者（= agent）可见的另外三样：`meta.json`、`api.py` 的 `init(db)/handle(...)` 契约、
`pagedb` / `pagedel` 两个命令行工具。除此之外不该碰 `app/`。

## 9. 扩展契约：opencode 怎么新增功能

```
新建 web/pages/<slug>/  +  meta.json  +  index.html  [+ api.py]
   → 下一次 GET / 出现新格子
   → 点开就是新页面，不需要重启、不需要重建镜像、不需要改后端
```

保证这一条成立的四件事：

1. **权限**：opencode 的 `edit` 只有 `pages/**` 与 `/tmp/**`；`shell` 只放行
   `pagedb` / `pagedel` / `preview` / `playwright-cli`；`app/` 对它不可写。它想加「能力」（新外部服务、
   新密钥）时必须停下来告诉人，由人改 `app/` + `config.py`。
2. **隔离**：页面是独立文档，坏掉的 JS 只坏自己；页面后端是独立进程，死循环不拖垮 web。
3. **可撤销**：整个 `web/pages` 卷可被 opencode 快照恢复；恢复前先调 pageworker 的 `/_reload`
   放掉 sqlite 连接，否则旧连接会把内容写回去。
4. **无注册表**：宫格是扫描出来的，没有第二个地方要同步，写坏一个 `meta.json` 最多少一个格子。

## 10. 状态、备份与安全

- 卷 1（`pages/`）= **全部用户内容**：页面代码 + 每页 sqlite。备份、迁移、回滚都是拷贝这一个目录。
- 卷 2（`/data`）= 可丢的运行时状态（tts 缓存、opencode session/snapshot、米家 token）。
  丢了要重扫码登录米家，仅此而已。
- 代码在镜像里，靠 git 管；不要经 opencode 改 `app/` 与 `web/assets/`。
- v1 只在内网监听，不做登录。暴露公网之前必须补：Caddy + 内网证书（顺带解决麦克风需要
  secure context）+ Basic Auth，并关掉 opencode 的 `device` 模式（那个模式能跑任意 shell）。
- `/oc/*` 是全仓最危险的接口：等于把「能改文件、能跑命令」的 agent 暴露给能访问 8080 的人。

## 11. 沿用 HANDOFF 的部分

原样搬（只改 import 与路径）：`pageworker` 的热加载/串行/并发 2/seed 机制、`speech.py` 切句、
`asr.py` 三家链路自动降级、`oc` 反代的 SSE 与权限 AutoDeny、`pagedb/pagedel` 的约束。

必须带过去的坑（编号同 HANDOFF §5）：麦克风要 secure context（2）、opencode 状态落 `/data`（3）、
米家 token 落 `HOME=/data`（4）、插件缓存目录（5）、cwd 只读的探测（6）、撤销分两段（7）、
米家要 sync def（8）、墨水屏 14 条规范（9）。

## 12. 实施顺序（每步可单独验证）

| # | 内容 | 验证 | 估时 |
|---|---|---|---|
| 1 | 骨架：`config.py` + `render.py` + 路由 + 宫格 + 三个 CSS + 一份示例页面 | `/` 与 `/?p=eink` 都出宫格，两份皮肤都对 | ✅ 已完成 |
| 2 | pageworker + `/api/pages` + `api.py` 热加载 + `pagedb/pagedel` | 加一个目录多一个格子；带 sqlite 的页跑通 | 2h |
| 3 | 一个真实功能页（画板或天气）走完整流程 | 手机与墨水屏都能点 | 1h |
| 4 | opencode 装配 + `/oc` 反代 | 对话里「加一页天气」，刷新后就出现 | 2h |
| 5 | 米家（新写，sync def 走线程池） | 一页开关控制真实设备 | 1.5h |
| 6 | 语音 ASR/TTS（原样搬） | 说一句，有回复有声 | 1h |
| 7 | Dockerfile + compose + 只读根 + 首启自检 | NAS 上 `docker compose up -d`，三台设备能开 | 1.5h |

**已定的范围：先只做第 1 步**（骨架），拿三台设备各开一次看皮肤与缩放对不对，再决定往下走。
顺序有依赖：4 需要 2/3 先跑通，7 放在最后一次性收口。

## 13. 已拍板 / 待定

| 项 | 决定 |
|---|---|
| 功能页形态 | 目录式 `web/pages/<slug>/`（不是 `pN.html` 平铺） |
| 页面结构 | MPA，无壳、无 iframe（§3） |
| 皮肤选择 | `?p=eink` 参数；不嗅探 UA、不写 cookie |
| v1 范围 | 先只做骨架（第 1 步），其余按 §12 顺序推进 |
| 相册（Immich） | v1 不做 |
| 语音（ASR/TTS） | v1 不做 |
| 固定页 | 只有 `home` 与 `mijia` 两个位置；固定页在 `web/system/`，不进卷、agent 改不到 |
| 米家 | 只留页面位置（`/s/mijia` 空壳）；后端口径未定；opencode 的 mijia 技能已删 |
| opencode 配置 | 放仓库 `opencode/`（3 个 agent + mijia 技能）；不含 playwright/preview 技能，agent 不能自查截图 |
| 镜像 | 单 Dockerfile，arm64，opencode 版本钉死；未在真机构建过（本机无 docker） |
| 亮度自动调暗 | 不做（屏幕自己的事，容器碰不到） |
