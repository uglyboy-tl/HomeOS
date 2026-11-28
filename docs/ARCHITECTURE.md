# 架构

## 1. 定位

家庭局域网里的**能力网关**，web 页面只是它的第一个消费者。只做三件别处做不了或做起来贵的事：

1. **设备与执行**：米家设备表、写后读回确认、多房间共享缓存（云接口的坑别人要重写一遍）。
2. **智能**：pi agent、实时语音识别、音箱播报（涉及密钥、长连接、按量计费）。
3. **聚合**：一次请求回答"家里现在什么样"（跨上游组装，单个上游给不出）。

判断准则：**能被一行 `curl` 直连上游完成的事，不进网关。** Jellyfin、Immich、Miniflux、
Gotify、AdGuard、Beszel 都有自己的 HTTP API，客户端直接调。

## 2. 接口分四类

| 类型 | 接口 | 承诺给谁 | 单 worker |
|---|---|---|---|
| 通用能力 | `/api/mijia/*`、`/api/mpd/*`、`/api/notify`、`/api/speech`、`/api/metrics`、`/api/agent/ask` | 局域网内其他应用，**只加不改** | 不要求（无状态） |
| 会话型 | `/api/agent/{prompt,abort,new-session,turn,stream}`、`/api/asr/stream` | 只给自家页面 | **要求** |
| 描述型 | `/api/info`、`/api/info/{agent,agent/state,mijia,pages}` | 任何客户端；先看它再决定发不发执行命令 | 不要求 |
| 页面私有 | `/`、`/mijia`、`/agent`、`/p/<slug>`、`/assets/*`、`/pages/<slug>/assets/*` | 只有自家前端 | 不要求 |
| 健康检查 | `/api/ready` | 容器 healthcheck | 不要求 |

- **通用能力都是无状态的**（缓存是加速不是状态）。将来若拆进程，切分线就在这里。
- **会话型要求 uvicorn 单 worker**：`services/agent/runtime.py` 持有每个模式的状态与常驻 pi 进程，
  SSE 订阅同一份内存快照，多 worker 会各自失联。别为了"网关"加 worker。
- **描述型不挂健康依赖**：上游挂了也要能回答"谁挂了"。
- 新增能力先在 `web/pages/` 用起来，出现第二个客户端才升格成通用能力。

## 3. 契约表

`$TOKEN` = `config/server.token`；所有 `/api/*`（除 `/api/ready`）认 cookie 或请求头，
下表的「认证」列只标例外。

### 页面与自述

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/mijia`、`/agent` | 固定页（`routes/pages.py` 的 `FIXED` 登记才可达） |
| GET | `/p/<slug>`、`/pages/<slug>/assets/*` | 功能页与其资源 |
| GET | `/assets/*` | 共享静态资源 |
| GET | `/api/ready` | 健康检查，**不认证** |
| GET | `/api/info` | 能力登记簿（7 项：`mijia.table/mijia.cloud/agent/asr/mpd/notify/metrics`）+ `problems` |
| GET | `/api/info/agent` | 引擎（可用性/问题/上次错误）、模型、模式列表、默认模式 |
| GET | `/api/info/agent/state?mode=` | 排障：进程、会话、用量、stderr 尾部 |
| GET | `/api/info/mijia` | 设备目录：房间顺序 + 每台的 id/name/room/kind（只读本地表，不碰云） |
| GET | `/api/info/pages` | 功能页清单（slug/title/icon/order） |

### 米家（sync def）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/mijia/values?ids=` | 按 id 给原始值，逗号分隔（中文要 URL 编码），不传就全读；单台失败只标它自己 |
| PUT | `/api/mijia/power` | `{id,on}` → **读回确认后的真值** `{id,on}`；未知设备 400 |

缓存分层：开关 3s（内存）、温湿度 300s、烟感 900s（后两者落盘 `/data/mijia/state.json`），
读失败退回旧值并标 `stale`。

### MPD（sync def）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/mpd/state` | 播放状态、当前曲目、音量/随机/循环/crossfade、列表长度 |
| PUT | `/api/mpd/playback` | `{state: playing\|paused\|stopped}`，要状态不要动作 |
| POST | `/api/mpd/queue/next`、`/queue/prev` | 队列前后一首 |
| PATCH | `/api/mpd/settings` | `{volume?,random?,repeat?,crossfade?}`，走命令列表，不会改一半 |
| POST | `/api/mpd/playlist` | `{name}`：清空队列 → 载入 → 播放是一步 |
| POST | `/api/mpd/library/update` | 重扫曲库并等索引建完 |
| POST | `/api/mpd/library/rebuild` | 按一级目录重建同名列表（会打断正在播的那份；同时只允许一次，否则 409） |

### 其他通用能力

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/notify` | `{text,title?,level?:info\|warn\|error}` → `{id}`；只推 Gotify，要出声另外调 `/api/speech` |
| POST | `/api/speech` | `{text,device?}` → `{text,did}`；去 markdown + 截断 200 字，`device` 空则用 `mijia.yaml` 的 `tts.did` |
| GET | `/api/metrics` | NAS 指标（cpu/负载/内存/温度/磁盘/网络/uptime），来自 Beszel 最新 1 分钟采样 |
| POST | `/api/agent/ask` | `{input}` → `{reply,truncated,ms}`；固定 chat、一次性 `--no-session`、无工具，超 12000 字截断 |

### 助手与语音（会话型）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/prompt` | `{mode,text}` 起一轮；上一轮没结束 → 409 `busy` |
| POST | `/api/agent/abort` | `{mode}` 中止当前轮，已产出的正文留在快照里 |
| POST | `/api/agent/new-session` | `{mode}` 换一条空会话（`new_session`，不重启进程） |
| GET | `/api/agent/turn?mode=` | 当前快照：`{v,mode,state,question,text,error,session,progress,ts}` |
| GET | `/api/agent/stream?mode=` | SSE 推完整快照，服务端 4 帧/秒，`ping` 15s |
| WS | `/api/asr/stream` | 音频帧上行；下行 `{type: partial\|final\|error}` |

- **只加不改**：上表被其他客户端调用后，不改路径、不改语义、不改返回形状。
- 会话上下文由 pi 落到 `/data/sessions`（命名卷，`--session-id <mode>-xxxx`），重启能续；
  快照里的 `session` 是页面判断"缓存还属于当前会话吗"的依据。
- `ask` 是一次性调用，不碰页面状态，也不共用页面会话。

### 错误

成功返回资源本体（不包 `ok`）；失败一律 `{"error":{"code","message"}}`，状态码说类别、
`code` 给程序判断、`message` 给人看。稳定 `code` 共 14 个（`tests/test_contract.py` 有枚举快照）：

```
bad_input  busy  conflict  cooldown  internal  not_configured  not_found
pi_crashed  pi_timeout  pi_unavailable  unauthorized  unavailable
unknown_device  unknown_mode
```

### 调用注意

- **query 里的中文必须 URL 编码**：HTTP 只允许 ASCII 的 request-target，uvicorn（h11）在进应用前
  就回 400。curl 用 `-G --data-urlencode`，页面用 `encodeURIComponent`，写接口走 body 不受影响。
- `ask` 的 `input` 当**不可信内容**：chat 模式带 websearch/webfetch，喂进去的日志里若有指令，
  可能被据此外发请求。

```bash
curl -sS -H "X-HomeOS-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"<日志/数据>"}' https://homeos.uglyboy.cn/api/agent/ask
```

## 4. 页面结构：MPA，没有壳

每页都是完整、独立的 HTML：能单独打开、能拷走、坏了只坏自己。导航是普通链接，浏览器历史免费得到。
代价是换页丢内存态——要留的东西写 `localStorage`。设计规则见 [`../web/DESIGN.md`](../web/DESIGN.md)。

## 5. 目录

```
app/
  main.py                 装配：路由表 + lifespan
  config.py               唯一读环境变量的地方；缺配置记进 PROBLEMS
  scan.py                 扫 web/pages/*/meta.json
  routes/                 HTTP 映射：校验、调 service、翻错误码
    pages.py info.py mijia.py mpd.py notify.py speech.py metrics.py ready.py
    agent.py              prompt/abort/new-session/turn/stream/ask
    asr.py                WS 转发
  services/
    mijia.py              设备表（mtime 缓存）+ 三级缓存 + 读回确认 + speak
    mpd.py                python-mpd2；settings/playlist 走命令列表保证原子
    notify.py metrics.py speech.py asr.py
    agent/{modes,rpc,runtime}.py   模式表 / pi 的 RPC 客户端 / 会话与快照
  lib/
    security.py           认证：页面 cookie / 请求头令牌
    errors.py             错误信封与 code
    health.py             能力登记簿：check（静态）+ guard（运行期缓存，COOLDOWN 30s）
    validation.py sockets.py shutdown.py logs.py headers.py
web/                     纯静态零构建（DESIGN.md 是唯一设计依据）
  home.html mijia.html agent.html
  assets/                tokens.css、icons.svg、各页 js/css、page.template.html、recorder-worklet.js
  pages/<slug>/          功能页（agent 唯一可写）：index.html + meta.json + assets/
config/                  homeos.yaml mijia.yaml agents.yaml agents.md pi-models.json pi-settings.json
docker/                  Dockerfile entrypoint.sh prepare.py（见 docker/README.md）
scripts/                 dev.sh 本机托管 / sync.sh 推 app 与 web 到 NAS
compose.yaml             部署形态：read_only + 卷 + Traefik 标签
tests/                   322 个用例，覆盖率门槛 95%
```

## 6. 皮肤：只有一种

纯黑白、零动画、rem 缩放，`web/assets/tokens.css` 是唯一样式基线（`!important` 是有意的：
页面由 agent 写，不可能全部自觉）。没有 `?p=`、没有 `data-profile`、没有服务端注入。
原则与验收判据在 [`../web/DESIGN.md`](../web/DESIGN.md)。

## 7. 扩展契约：agent 怎么新增功能页

```
新建 web/pages/<slug>/  +  meta.json  +  index.html
   → 刷新首页即出现新格子 → 点开就是新页面
   → 不需要重启、不需要重建镜像、不需要改后端
```

保证这条成立的三件事：

1. **权限**：写只到 `pages/**`（靠挂载），密钥文件读不到（cc-safety-net 的 deny_paths）。
   它要"新能力"（新外部服务、新密钥）时必须停下来说，由人改 `app/` + `config.py`。
2. **隔离**：页面是独立文档，坏掉的 JS 只坏自己。
3. **无注册表**：首页是扫描出来的，没有第二份清单要同步；写坏一个 `meta.json` 最多少一个格子。

## 8. 进程、卷与安全

| 进程 | 监听 | 说明 |
|---|---|---|
| `uvicorn app.main:app` | `0.0.0.0:8080` | 唯一入口，单 worker（见 §2）；部署不发布宿主端口，由 Traefik 转发 `homeos.uglyboy.cn` |

- 根文件系统 `read_only: true`；`/tmp` tmpfs 512m（agent 的暂存区）；`/data` tmpfs 128m
  **mode=1777**（嵌套挂载米家登录态时 Docker 会把 tmpfs 根建成 root:0755）。
- 可写点只有挂载：`./pages` → `/app/web/pages`（**备份它就备份了全部用户内容**）、
  `./config`、`~/.config/mijia-api`（米家登录态，库写死路径）、命名卷 `homeos-sessions` → `/data/sessions`。

**安全边界（2026-11 实测）**

- 家里有公网 IPv6，Traefik 听 `[::]:443` 且是泛域名证书。`movie`/`photo` 等子域有 AAAA 记录，
  等于公开了家庭地址；拿 `Host: homeos.uglyboy.cn` 对着它请求就能打到本服务。
  **"内网自用"不是安全边界**，网络层要挡（Traefik `ipAllowList` 只放内网段）。
- 认证两档（`app/lib/security.py`）：自家前端用页面响应种的 `homeos` cookie
  （`HttpOnly; SameSite=Strict`，https 才 `Secure`，值是 `HMAC(令牌, 固定盐)` 的派生值——
  页面响应本身不认证，放原文等于把令牌公开）；其他程序用请求头令牌。
- 未配置令牌时只有 `HOMEOS_DEV=1` 放行，生产 401。
- 这层挡误用与扫描，**不是**定向攻击：任何能 GET 页面的人都拿得到合法 cookie。
- **家宽上任何"内网自用"服务都不能加公网 A/AAAA 记录**，除非先补认证。

## 9. 已拍板 / 不做

| 项 | 决定 |
|---|---|
| 接口版本 | 不引 `/v1`，靠"只加不改"与 §3 契约表 |
| 认证 | 页面 cookie（零感知）+ 共享令牌；不做用户体系，主边界是 Traefik `ipAllowList` |
| 数据层 | 不引 sqlite/页面后端；要存东西用 `localStorage` |
| 皮肤 | 只有墨水屏一种，没有多皮肤开关 |
| 前端构建 | 不引打包、CDN、外部字体、框架；固定页例外（`web/assets/vendor/` 本地副本） |
| 进程模型 | 单容器单 worker；若要拆，"通用能力"是无状态的那一半 |
| 鉴权体系 | 不做多用户/权限矩阵（单用户家庭，成本远超收益） |
