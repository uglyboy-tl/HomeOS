# HomeOS

家里所有屏共用的网页服务：一台 NAS 上的 FastAPI，容器里同时跑 opencode，孩子点一下/说一句，
agent 改一页。目标客户端只有墨水屏（inkPalm 5、Kindle 第三方浏览器），区别只是屏幕大小。

自用项目：优先"跑得稳、改得动"，不为扩展性做设计。**只有一种皮肤（墨水屏）**，不追求多设备适配。

## 结构

```
app/                     镜像内 Python（只读）
  main.py                装配：路由表 + lifespan
  config.py              唯一读环境变量的地方（端口、路径、DEV）
  scan.py                扫 web/pages/*/meta.json，给首页与 /api/pages 用
  routes/
    pages.py             /  /mijia  /agent  /p/<slug>  /assets/*  /pages/<slug>/assets/*
    mijia.py             米家：/api/mijia/state  /api/mijia/switch（sync def）
    oc.py                /oc/* → opencode（注入上游 Basic，SSE 逐块转发；路由本身不认证）
    health.py            /api/ready  /api/pages
  services/
    mijia.py             设备表 + 三级缓存 + 写后读回确认
    opencode.py          读密码、拼 URL、Basic 头
  _proxy.py              /oc 用的反代（流式/非流式同一份）
  runtime.py             关服务时让 SSE 生成器退出

web/                     前端，纯静态零构建
  home.html              首页：固定页写死在 HTML 里，功能页由 home.js 从 /api/pages 追加
  mijia.html             米家：房间标签 + 开关行 + 环境条（assets/mijia.{css,js}）
  agent.html             助手：聊天/设计/高级三个模式 + 对话区 + 输入条（仅前端，未接后端）
  assets/                tokens.css（唯一皮肤）、icons.svg、home.js、mijia.{css,js}、agent.{css,js}、
                         page.template.html（功能页起手模板）
  pages/                 ★卷 1：功能页目录（agent 唯一能写的地方）
    <slug>/index.html    页面本体（完整独立 HTML）
    <slug>/meta.json     {"title","icon","order"}
    <slug>/assets/       这一页自己的图/脚本

opencode/                opencode 配置根（XDG_CONFIG_HOME=<仓库根>）
  AGENTS.md agents/{chat,pages,device}.md opencode.jsonc cli.json

docker/  Dockerfile entrypoint.sh
compose.yaml             NAS 部署（read_only + 两个卷）
tests/  docs/
```

三条分界线：

1. **固定页**（`web/*.html`）只有人能改，路由写死在 `app/routes/pages.py` 的 `FIXED` 里。
2. **功能页**（`web/pages/<slug>/`）是 agent 唯一能写的地方，由 `opencode/agents/pages.md` 的
   permissions 强制；首页从 `/api/pages` 把它们列出来，不需要登记。
3. opencode 的工作目录是 `web/`，所以权限里的路径写成 `pages/**`，不是 `web/pages/**`。

## 代码原则

个人项目：只在本机和 NAS 上跑。不考虑多用户、并发、扩展性、向后兼容。

- **去冗余**：同一件事只在一个地方说。重复判断、只包装一行的函数、复述代码的注释都算冗余。
- **合理抽象**：重复两次以上且语义一致才抽；抽出来要减少阅读量，不是把逻辑藏远。
- **代码语意性**：命名、结构、数据流自己说清做了什么。
- **减少注释**：只写「为什么」（不显然的约束、踩过的坑、反直觉的取舍），不复述代码。

每次 review（含提交前自查）按这四条反过来查：新增的每行是不是必须的、能不能用已有机制代替、
注释是不是在说代码已经说清的事。

## 命令

```bash
./docker/entrypoint.sh up                    # web(8080) + opencode(14100)，后台托管
./docker/entrypoint.sh restart [web|agent|all]
./docker/entrypoint.sh status|log|down

uv run pytest -q                             # 不联网，约 1 秒
uv run ruff check . && uv run ruff format .

mkdir -p ~/.config/mijia-api && cp .env.example .env   # 填模型 key；服务密码自动生成
docker compose up -d --build                          # 镜像只支持 arm64
```

`web/pages/*` 改完不用重启：刷新即见。

## 跑起来的东西

| 端口 | 作用 |
|---|---|
| 8080 | 唯一对外入口：页面、`/api/*`、`/oc` 反代 |
| 14100 | opencode serve，只听 127.0.0.1（外部只能经 `/oc`） |

容器：`read_only: true`，`/tmp` 走 tmpfs；卷只有 `./web/pages`（功能）与 `./data`（运行时状态）。
`docker/entrypoint.sh` 一个脚本两种模式：无参数 = 容器前台，带子命令 = 本机后台托管。

## 不要做

- **不加数据层**：没有 sqlite、没有页面后端、没有 `pagedb`。要存东西先用 `localStorage`。
- **不引第二种皮肤**：没有 rich/eink 之分，没有 `?p=`、没有 `data-profile`、没有服务端注入。
  页面就是完整 HTML，能单独打开、能拷走。
- 不引构建步骤、CDN、外部字体、前端框架。要图标用 `assets/icons.svg` 的 symbol 或内联 SVG。
  例外：**固定页**（`web/*.html`，人维护）可以用 `web/assets/vendor/` 里的本地副本（目前只有 Alpine +
  persist，见 `web/DESIGN.md`）；**功能页**（`web/pages/<slug>/`）不得引任何框架。
- 不写死密钥；需要密钥的东西走环境变量或 `.env`（不进 git）。
  web ↔ opencode 的服务密码不需要人管：entrypoint 每次启动生成到 `$HOMEOS_DATA_DIR/auth/opencode`。
  容器里 `/data` 是 tmpfs，所以这个密码、opencode 会话、米家缓存都**不跨重启**（绑定的只有
  `pages/`、`config/`、宿主机 `~/.config/mijia-api`）。
- 不做登录鉴权（v1 只在内网，暴露公网前必须先补 Caddy + Basic Auth，并关掉 device 模式）。
- 不自动 `git commit` / `git push`。
- 一次性操作当场用命令行做，不往 `entrypoint.sh` 里加分支。
- 不以 `HOMEOS_*` 之外的名义读环境变量；新变量一律先进 `app/config.py`。

## 已知的坑（已按本环境筛过）

从 kiosk 搬过来的经验里，**有一半不适用了**（没有客户端、没有 iframe、没有页面后端、没有 PWA）。
下面按"适用 / 未验证 / 不适用"分好，别再把不适用当规矩。

**适用**

- **米家必须 sync def**：`mijiaAPI` 一次读回约 1s，写成 `async def` 会堵住事件循环，整站跟着卡。
- **写后要读回确认**：`set()` 只回 ack；云接口有约 1s 延迟，所以间隔 0.4s 重读最多 4 次，
  等于目标值才算成功。前端拿服务端读回的值才变色（期间那行显示「…」），失败时灯其实没动，
  不假装成功——墨水屏上宁可等 1s 也别翻两次。
- **缓存分层**：开关 3s 内存、温湿度 300s、烟感 900s，后两者落盘到
  `$HOMEOS_DATA_DIR/mijia/state.json`；读失败退回旧值并标 `stale`。落盘缓存的作用是
  **同一次运行内**多个房间/页面共享一次云读取（容器里 `/data` 是 tmpfs，重启即清）；
  去掉它每个房间都要各自等云。
- **`get_devices_list` 只在首次调一次**（每次约 0.8s，缓存成常量）。新增设备要重启服务。
- **墨水屏开销**：轮询 ≥ 60s 且只在可见时；不要 SSE 逐字、不要时钟、不要 hover；
  例外只有一个：助手页的模型输出用 SSE 推，但**服务端限流 4 次/秒**、页面只在文本变化时重绘
  （逐 token 刷会糊屏，4 次/秒等于手写输入法的观感）；
  内容没变就不重画（`mijia.js` 比指纹）。
- **事件委托**：监听挂在容器上，别在渲染时逐个绑——静态 DOM 不会重绑，绑了就点不动（踩过）。
- **opencode 的消息落库滞后于事件**：正文要等步骤结束才进 `/message`，所以
  逐字文本只能来自 SSE 的 `session.text.delta`；`done` 可能先到一帧（正文还没有），
  前端把它当结束就会永远不显示回复（踩过）。
- **`tokens.css` 的 `!important` 是有意的**：页面是 agent 写的，不可能全部自觉。别为某页放开。

**未验证（第一次上 NAS 要盯）**

- **opencode 在只读镜像根下能起来**：它会往配置根写 `service.json`，所以 entrypoint 把
  `opencode/` 整份复制到 `/data/config/` 再用。本机模拟过（`HOMEOS_MODE=container`）API 200，
  但没在真镜像里跑过。
- **米家登录态**：`$HOME/.config/mijia-api/auth.json`，容器里 `HOME=/data` → 要放到
  `data/.config/mijia-api/auth.json`。重新登录必须人扫码。
- **opencode 会不会往 cwd 写文件**：cwd 是 `/app/web`（只读，只有 `pages/` 是卷）。
- **arm64 依赖**：`mijiaAPI` 的 pillow / pycryptodome 在 arm64 上有轮子，没实际装过。
- **镜像里没有中文字体**：服务端不渲染文字，所以现在无所谓；将来要是做服务端出图
  （ESP32 那条路）得先把字库打进去。

**不适用（kiosk 有、这里没有）**

| kiosk 的坑 | 为什么不适用 |
|---|---|
| 麦克风要 secure context | 语音整块砍了 |
| iframe 里 `document.hidden` 不准 | 没有 iframe，页面都是顶层文档 |
| pyc 校验只到"秒+大小"（别用 importlib） | 没有页面后端，不动态 compile 页面代码 |
| 撤销是两段（快照 + `/api/pages/reload`） | 没有 sqlite、没有页面撤销；opencode 自己的 revert 直接改文件 |
| 插件缓存要落 /data（cc-safety-net） | 不用插件 |
| `tabs.json` 与页面双写 | 首页从 `/api/pages` 拉，没有第二份清单 |
| DSI 面板 / labwc / systemd / 亮度 | 客户端只剩浏览器，容器碰不到硬件 |
| pages 目录不进 git | 这里的 `web/pages/demo` 是仓库内容 |

**其它环境差异**

- opencode 只装 arm64（`opencode-linux-arm64.tar.gz`），版本钉在 `Dockerfile`；amd64 构建直接失败。
- `opencode upgrade` 不做，密钥不进代码/docs。
- `/oc` 把请求转给有 shell 权限的 agent，**故意不做认证**（内网自用，2026-11 决定）：
  加口令挡不住同网段能看页面源码的人，只增加维护面。代价是“能连 8080 就能让 agent 跑命令”，
  所以 **`/oc` 绝不能暴露到公网**；真要出内网，先在反代层补认证/限源。
- `HOMEOS_DEV=1` 只在本机（决定 `/_docs` 与静态资源是否 `no-store`），容器里不设。
- `.dockerignore` 排掉 `web/pages/*`（那是卷），镜像里只建空目录。

## Python / 前端规范

- 写或改前端（`web/assets/*`、`web/*.html`、`page.template.html`）前先看
  `.agents/skills/modern-web-guidance/SKILL.md`：它说怎么用 bunx 查原生做法，也写了
  **本项目覆盖通用指南的那几条**（只用 rem、事件委派、零动画、命中区大小；
  中文不吃 `text-wrap`、不要 `prefers-*` 分支）。冲突时以那里的硬约束为准。
  那个技能只给本机（pi）用：镜像里没 bun，容器里的 pages agent 读不到也跑不了。
- 已落地的几件事，新页面照拄：`min-height: 100dvh`（不用 `100vh` 当主值，
  但 `font-size` 的虚拟画布公式故意用 `vh`，它要稳不要跟地址标跳动）、
  `padding-bottom: env(safe-area-inset-bottom)`、
  `overflow-wrap: break-word` + `img { max-inline-size: 100% }`、`:focus-visible` 描边、
  表单 `preventDefault()`。
- ruff：line-length 100、py312、`E F I UP B BLE SIM`。需要宽泛捕获时写 `# noqa: BLE001` 并说明原因。
- 后端只读 `app/config.py` 里的常量；路由优先 sync def，异步只在真需要长连接时用。
- 前端是原生 ES，无打包；`assets/*.js` 是 IIFE，不引模块系统。
- 页面只写 `rem`，只用 `tokens.css` 里的变量；写死 `px` 宽度会在别的屏上溢出。
