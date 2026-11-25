# 架构（NAS 视角）

## 1. 定位

家庭局域网里的**能力网关**，web 页面只是它的第一个消费者。

它只做三件别处做不了或做起来贵的事，其余一律不包：

1. **设备与执行**：米家设备表 + 写后读回确认 + 多房间共享缓存（云接口的坑别人要重写一遍）。
2. **智能**：opencode agent、实时语音识别、音箱播报（涉及密钥、长连接、按小时计费）。
3. **聚合**：一次请求回答"家里现在什么样"（跨多个上游组装，单个上游给不出）。

判断准则：**能被一行 `curl` 直连上游完成的事，不进网关。** 局域网里 Jellyfin、
Immich、Miniflux、Gotify、AdGuard、Beszel 都有自己的 HTTP API，客户端直接调。

## 2. 接口分三类，边界写死在这里

| 类型 | 接口 | 承诺给谁 | 认证 |
|---|---|---|---|
| 通用能力 | `/api/mijia/*`、`/api/mpd/*`、`/api/agent/ask` | 局域网内其他应用，**只加不改** | cookie 或 `X-HomeOS-Token` |
| 会话型 | `/api/agent/{turn,stream,say,new,bind,model,info,trace}`、`/api/asr/stream` | 只给自家助手页；内存态、常驻 SSE，注定单进程 | 同上 |
| 页面私有 | `/`、`/mijia`、`/agent`、`/p/<slug>`、`/assets/*` | 只有自家前端 | 无（这几个是发文件 + 种 cookie） |
| 健康检查 | `/api/ready` | 容器 healthcheck | 无 |

- 通用能力都是**无状态**的（缓存是加速，不是状态），将来若拆进程，切分线就在这里。
- 会话型接口要求 **uvicorn 单 worker**：`services/agent.py` 持有每模式的对账状态与一条
  SSE 订阅，多 worker 会各自失联。别为了"网关"去加 worker。
- 新增能力先在 `web/pages/` 里用起来，出现第二个客户端才谈通用化。

## 3. 页面结构：MPA，没有壳

每页都是完整、独立的 HTML：能单独打开、能拷走、坏了只坏自己。导航是普通链接，浏览器历史免费得到。

代价：换页丢内存态。要留的东西写 `localStorage`。

## 4. 目录

```
app/                     镜像内 Python（只读）
  main.py                装配：路由表 + lifespan
  config.py              唯一读环境变量的地方
  security.py            认证：页面 cookie + 请求头令牌
  scan.py                扫 web/pages/*/meta.json，给首页与 /api/pages 用
  routes/
    pages.py             /  /mijia  /agent  /p/<slug>  /assets/*  /pages/<slug>/assets/*
    mijia.py             米家：state / switch / say / devices（sync def）
    mpd.py               音乐：state / control（action 分派）/ sync
    notify.py            通知：默认推 Gotify，可选音箱播报
    agent.py             /api/agent/*：对话、模式、模型
    asr.py               WS /api/asr/stream → Azure
    oc.py                /oc/* → opencode（需 X-HomeOS-Token）
    health.py            /api/ready  /api/pages
  services/
    mijia.py             设备表（mtime 缓存）+ 三级缓存 + 写后读回确认 + 播报
    agent.py             对话对账、SSE 订阅、播报副作用
    asr.py               Azure 实时 WS：帧格式、共用 X-RequestId
    opencode.py          上游地址与 Basic 头
  _proxy.py              /oc 的反代（流式/非流式同一份）
  runtime.py             关服务时让 SSE 生成器退出

web/                     前端，纯静态零构建
  home.html              首页：固定页写死，功能页由 home.js 从 /api/pages 追加
  mijia.html             米家页
  agent.html             助手页（聊天/设计/高级）
  assets/                tokens.css（唯一皮肤）、icons.svg、各页 js/css、recorder-worklet.js
  pages/<slug>/          ★卷：功能页（agent 唯一能写的地方）
    index.html  meta.json  assets/

opencode/                opencode 配置根（XDG_CONFIG_HOME=<仓库根>）
  AGENTS.md  agents/{chat,pages,device}.md  opencode.jsonc  cli.json
config/mijia.yaml        米家设备表 + tts
docker/  Dockerfile entrypoint.sh
compose.yaml             NAS 部署（read_only + 卷 + Traefik 标签）
```

## 5. 皮肤：只有一种（墨水屏）

纯黑白、零圆角、零阴影、零动画，`web/assets/tokens.css` 是唯一样式基线，`!important` 是故意的
（页面由 agent 写，不可能全部自觉）。没有 `?p=`、没有 `data-profile`、没有服务端注入。

```css
html { font-size: calc(min(100vw / var(--vw), 100vh / var(--vh))); }  /* 1rem = 虚拟画布宽的 1/--vw */
```

页面只写 `rem`、只用 `tokens.css` 的变量；写死 `px` 宽度会在别的屏上溢出。
性能预算与刷新原则见 `web/DESIGN.md` 与 `.agents/skills/modern-web-guidance/SKILL.md`。

## 6. 契约（外部能看到的全部）

| 方法 | 路径 | 说明 | 可见性 |
|---|---|---|---|
| GET | `/` | 首页宫格 | 页面 |
| GET | `/mijia` `/agent` | 固定页（`pages.FIXED` 里登记才可达） | 页面 |
| GET | `/p/<slug>` | 功能页 | 页面 |
| GET | `/assets/*` `/pages/<slug>/assets/*` | 静态资源 | 页面 |
| GET | `/api/pages` | 功能页清单（slug/title/icon/order） | 需认证 |
| GET | `/api/ready` | 健康检查 | 页面 |
| GET | `/api/mijia/devices` | 房间顺序 + 每台设备 id/名字/房间/类型 | 通用（需认证） |
| GET | `/api/mijia/state?room=` | 全部或一间房的状态（开关 3s / 温湿度 300s / 烟感 900s）；**中文要 URL 编码** | 通用（需认证） |
| POST | `/api/mijia/switch` | `{id,on}`，返回读回确认后的真实值；未知开关 400、云不可用 503 | 通用（需认证） |
| POST | `/api/mijia/say` | `{text}`，让音箱念（先记再播，多客户端只响一遍） | 通用（需认证） |
| POST | `/api/agent/ask` | `{input,prompt?,timeout?,speak?}` → 一段解读 + 耗时/模型/token；独立会话、跑完即删 | 通用（需认证） |
| GET | `/api/mpd/state` | 播放状态 + 当前曲目 + 列表长度；`?device=mpd` | 通用（需认证） |
| POST | `/api/mpd/control` | `{action,device?,playlist?,volume?,random?,repeat?,crossfade?,on?}`，返回执行后的状态 | 通用（需认证） |
| POST | `/api/mpd/sync` | `{rebuild?}`：update 索引，rebuild 再按一级目录重建同名列表（实测 614 首 0.8s，硬上限 60s，同时只允许一次否则 409） | 通用（需认证） |
| POST | `/api/notify` | `{text,title?,level?,channels?}`：逐通道发送，返回每个通道的结果 | 通用（需认证） |
| GET | `/api/agent/turn` | 取本轮状态与正文 | 会话 |
| GET | `/api/agent/stream` | SSE 推进度（服务端限流 4 次/秒） | 会话 |
| POST | `/api/agent/new` `/bind` `/model` | 新对话 / 绑定会话 / 选模型 | 会话 |
| GET | `/api/agent/info` `/trace` | 模型与会话信息 / 本轮步骤 | 会话 |
| POST | `/api/agent/say` | 手动播报 | 会话 |
| WS | `/api/asr/stream` | 音频帧上行，`{partial}`/`{text}`/`{error}` 下行 | 会话 |
| ANY | `/oc/*` | opencode 反代（注入上游 Basic），**必须带 `X-HomeOS-Token`** | 通用（需令牌） |

"只加不改"：上表被其他客户端调用后，不改路径、不改语义、不改返回形状。
（`/api/agent/ask` 在引入本次功能时形状从 `{mode,text}` 换成了 `{input,prompt}`，旧的会 422；从此之后按上面这条守。）

给其他程序用的一次调用（内部固定 `chat` agent，独立会话、不碰页面状态、默认不播报）：

```bash
curl -sS -H "X-HomeOS-Token: $HOMEOS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"<日志/数据>","prompt":"两句话说清失败原因","speak":true}' \
  https://homeos.uglyboy.cn/api/agent/ask
# → {"ok":true,"text":"…","model":"…","elapsed_ms":3210,"tokens":{…},"cost":0.0006,"truncated":false}
```

- `input` 必填，超 12000 字符截断并回 `truncated: true`；`prompt` 为空用内置指令。
- 同步返回，上限 300s；超时 504，上游不可用 503。
- 不给 `agent` 参数：`device`（能跑 shell）只属 `/oc`，这里只产出文字。
- `input` 一律当**不可信内容**：`chat` agent 有 websearch/webfetch，喂进来的日志里若带指令，可能被据此外发请求。

音乐控制：`action` 是固定词表（`play|pause|stop|volume|next|prev`），不是 MPD 命令透传。

```bash
# 每晚播「深度睡眠」：一次调用把列表、随机、循环、音量都定下来
curl -sS -H "X-HomeOS-Token: $HOMEOS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"play","playlist":"深度睡眠","volume":30,"random":true,"repeat":true,"crossfade":2}' \
  https://homeos.uglyboy.cn/api/mpd/control

curl -d '{"action":"volume","volume":26}' -H "X-HomeOS-Token: $HOMEOS_TOKEN" \
  -H 'Content-Type: application/json' https://homeos.uglyboy.cn/api/mpd/control
curl -d '{"action":"stop"}' -H "X-HomeOS-Token: $HOMEOS_TOKEN" \
  -H 'Content-Type: application/json' https://homeos.uglyboy.cn/api/mpd/control
```

- 写接口都回执行后的 `state`（与米家 `switch` 同一规矩）。
- `device` 现在只认 `mpd`（省略即它）；给别的值 400——将来 DLNA/AirPlay 就在这里加一项。
- 连不上 MPD 503，列表/目录不存在 404，`volume` 越界或字段拼错 400。

通知：一条正文，默认只推 Gotify；要出声必须显式加 `speak`（半夜的备份告警不该喊醒人）。
```bash
curl -sS -H "X-HomeOS-Token: $HOMEOS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"备份链异常 2 项：…","title":"⚠️ 备份链异常","level":"error"}' \
  https://homeos.uglyboy.cn/api/notify
# → {"ok":true,"results":{"gotify":{"ok":true,"id":6401}}}
```

- `level` 三档映射 Gotify priority：info 2 / warn 6 / error 8；`title` 缺省取正文前 20 字。
- `channels` 可选 `gotify` / `speak`，默认 `["gotify"]`。逐通道回报；部分成功仍 200，全挂 503。
- Gotify 走容器内服务名 `http://gotify`（同在 traefik 网络），不绕公网。

**调用注意**：query 里的中文必须 URL 编码。HTTP 规范只允许 ASCII 的 request-target，uvicorn 的解析器（h11）
在进到应用前就回 `400 Invalid HTTP request`，所以这事在服务端改不了。curl 用 `-G --data-urlencode`：

```bash
curl -sG https://homeos.uglyboy.cn/api/mijia/state \
  -H "X-HomeOS-Token: $HOMEOS_TOKEN" --data-urlencode "room=客厅"
```

页面里用 `encodeURIComponent`，天然没问题；POST 接口的房间名走 body，也不受影响。

## 7. 扩展契约：agent 怎么新增功能页

```
新建 web/pages/<slug>/  +  meta.json  +  index.html
   → 刷新首页即出现新格子
   → 点开就是新页面，不需要重启、不需要重建镜像、不需要改后端
```

保证这条成立的三件事：

1. **权限**：opencode 的写权限只有 `pages/**`（工作目录是 `web/`），`app/` 对它不可写。
   它要"新能力"（新外部服务、新密钥）时必须停下来说，由人改 `app/` + `config.py`。
2. **隔离**：页面是独立文档，坏掉的 JS 只坏自己。
3. **无注册表**：首页是扫描出来的，没有第二份清单要同步；写坏一个 `meta.json` 最多少一个格子。

## 8. 进程、卷与安全

| 进程 | 监听 | 说明 |
|---|---|---|
| `uvicorn app.main:app` | `0.0.0.0:8080` | 唯一入口，**单 worker**（见 §2） |
| `opencode serve` | `127.0.0.1:14100` | agent，只能经 `/oc` 到达 |

- 根文件系统 `read_only: true`；`/tmp` tmpfs 512m（agent 的暂存区）；
  `/data` tmpfs 1g **mode=1777**（嵌套挂载米家登录态时 Docker 会把 tmpfs 根建成 root:0755）。
- 卷：`./web/pages`（功能，备份它就备份全部用户内容）、
  `~/.config/mijia-api`（米家登录态，库写死路径）。
- 部署不发布宿主端口，走 Traefik：`https://homeos.uglyboy.cn` —— HTTPS 才有 secure context，
  墨水屏上麦克风才能用。
- `HOMEOS_DEV=1` 只在本机（`/_docs`、静态资源 `no-store`、uvicorn reload）。

**安全边界（2026-11 实测）**

- 家里有公网 IPv6（联通动态），Traefik 听 `[::]:443`，泛域名证书。`movie`/`photo` 等子域有
  AAAA 记录，等于公开了家庭 IPv6；拿 `Host: homeos.uglyboy.cn` 对着它请求就能打到本服务。
  所以"内网自用"不是安全边界，**网络层要挡**（Traefik `ipAllowList` 只放内网段）。

**认证分两档**（`app/security.py`）：

- 自家前端：页面响应种 `homeos` cookie（`HttpOnly; SameSite=Strict`，https 才加 `Secure`）。
  cookie 值是 `HMAC(令牌, 固定盐)` 的派生值——页面响应本身不认证，放原文等于把 `/oc` 的钥匙公开。
- 其他设备/程序：`X-HomeOS-Token: $HOMEOS_TOKEN`。`/api/*`（除 `/api/ready`）认这两者之一。
- `/oc` 只认令牌原文，不认 cookie；未配令牌 → 403 关闭。
- 未配置 `HOMEOS_TOKEN`：页面与 `/api/*` 放行（本机开发），`/oc` 关闭；令牌格式非法时同样降级并记 error。
- 这一层挡的是误用与扫描，**不是**定向攻击（任何能 GET 页面的人都拿到合法 cookie）。主边界是网络层。
- **家宽上任何"内网自用"服务都不能加公网 A/AAAA 记录**，除非先补认证。

## 9. 已拍板 / 不做

| 项 | 决定 |
|---|---|
| 接口版本 | 不引 `/v1`；靠"只加不改"与 §6 的契约表 |
| 认证 | 页面对应 cookie（零感知）、其他设备对应共享令牌；不做用户体系。主边界是 Traefik `ipAllowList` |
| 数据层 | 不引 sqlite/页面后端；要存东西用 `localStorage` |
| 皮肤 | 只有墨水屏一种，没有 `?p=`、没有 `data-profile` |
| 前端构建 | 不引打包、CDN、外部字体、框架；固定页例外（`web/assets/vendor/` 本地副本） |
| 进程模型 | 单容器两进程；若要拆，"通用能力"是无状态那一半 |
| 鉴权体系 | 不做多用户/权限矩阵（单用户家庭，成本远超收益） |
