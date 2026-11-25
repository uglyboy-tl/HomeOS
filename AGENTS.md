# HomeOS

家庭局域网的能力网关：一台 NAS 上的 FastAPI 容器（里同跑 opencode），只包"别处做不了或做起来贵"
的三样——设备与执行（米家）、智能（agent/ASR/播报）、聚合。web 页面是它的第一个消费者，
目标屏只有墨水屏（inkPalm 5、Kindle 浏览器）。

自用项目：优先"跑得稳、改得动"，不为扩展性做设计。只有一种皮肤（墨水屏），不追求多设备适配。

## 结构

```
app/                     镜像内 Python（只读）
  main.py                装配：路由表 + lifespan
  config.py              唯一读环境变量的地方（端口、路径、DEV、令牌）
  security.py            认证：页面 cookie / 请求头令牌
  scan.py                扫 web/pages/*/meta.json，给首页与 /api/pages 用
  routes/                pages.py  mijia.py  asr.py  agent.py  oc.py  health.py
  services/              mijia.py  asr.py  agent.py  opencode.py
  _proxy.py              /oc 的反代（流式/非流式同一份）
  runtime.py             关服务时让 SSE 生成器退出

web/                     前端，纯静态零构建
  home.html  mijia.html  agent.html        三个固定页
  assets/                tokens.css（唯一皮肤）、icons.svg、各页 js/css、
                         page.template.html（功能页起手模板）、recorder-worklet.js
  pages/<slug>/          ★卷：功能页（agent 唯一能写的地方）index.html + meta.json + assets/

opencode/                opencode 配置根（XDG_CONFIG_HOME=<仓库根>）
docker/  Dockerfile entrypoint.sh
compose.yaml             NAS 部署（read_only + 卷 + Traefik 标签）
tests/  docs/  config/mijia.yaml
```

三条分界线：

1. **固定页**（`web/*.html`）只有人能改，路由写死在 `app/routes/pages.py` 的 `FIXED` 里。
2. **功能页**（`web/pages/<slug>/`）是 agent 唯一能写的地方，由 `opencode/agents/pages.md` 的
   permissions 强制；首页从 `/api/pages` 列出它们，不需要登记。
3. opencode 的工作目录是 `web/`，所以权限里的路径写 `pages/**`，不是 `web/pages/**`。

**接口分三类，边界不动**（详见 `docs/ARCHITECTURE.md` §2、§6，改了要同步那张契约表）：

- 通用能力（`/api/mijia/*`）：无状态，其他应用可直接调，只加不改。
- 会话型（`/api/agent/*`、`/api/asr/stream`）：内存态 + 常驻 SSE，只给自家页面，因此 uvicorn 单 worker。
- 页面私有（静态页面）：只发文件。`/api/ready` 供健康检查，不认证。

新增能力先在 `web/pages/` 里用起来，出现第二个客户端才谈通用化。能被一行 `curl` 直连上游
完成的事不进网关（Jellyfin/Immich/Miniflux 都有自己的 API）。

**认证两档**（`app/security.py`，威胁模型见 ARCHITECTURE §8）：

- 自家前端：页面响应种 `homeos` cookie（`HttpOnly; SameSite=Strict`，https 才加 `Secure`），
  fetch 自动带上。cookie 里是令牌的派生值（页面响应不认证就发，原文等于公开钥匙）。
- 其他设备/程序：`X-HomeOS-Token: $HOMEOS_TOKEN`。`/api/*`（除 `/api/ready`）认这两者之一。
- `/oc` 只认令牌原文，不认 cookie；未配置令牌时页面与 `/api/*` 放行、`/oc` 关闭（403）。

## 代码原则

个人项目：只在本机和 NAS 上跑。不考虑多用户、并发、扩展性、向后兼容。

- **去冗余**：同一件事只在一个地方说。重复判断、只包装一行的函数、复述代码的注释都算冗余。
- **合理抽象**：重复两次以上且语义一致才抽；抽出来要减少阅读量，不是把逻辑藏远。
- **代码语意性**：命名、结构、数据流自己说清做了什么。
- **注释要有预算**：模块 docstring ≤3 行，函数/类 ≤1 行，行内 `#` ≤1 行且贴着那一行。
  超了搬 `docs/`，踩过的坑搬本文件。写完自问：删掉它，会不会有人写错？不会就不是注释。

注释只留三种：非显然的约束（外部协议、并发、平台差异）、反直觉的取舍、不这样写就会错的地方。
复述代码、叙述设计意图、抄文档结论、`# ---- 名称 ----` 分隔横幅都不算。

## 命令

```bash
./docker/entrypoint.sh up|down|restart [web|agent|all]|status|log   # 本机后台托管
uv run pytest -q                             # 不联网，约 1 秒
uv run ruff check . && uv run ruff format .
bunx @biomejs/biome@2.6.8 check --write .    # 前端 lint + 格式化（规则见 biome.jsonc）
```

部署：栈目录里 `cp .env.example .env` 填 key，`docker compose pull && up -d`（镜像来自 ghcr；
要自建才加 `--build`，且只支持 arm64）。米家先 `mkdir -p ~/.config/mijia-api` 再扫码登录。

`web/pages/*` 改完不用重启：刷新即见。

## 跑起来的东西

| 端口 | 作用 |
|---|---|
| 8080 | 容器内监听：页面、`/api/*`、`/oc`。本机 dev 开在 127.0.0.1；部署不发布到宿主，由 Traefik 转发 `homeos.uglyboy.cn` |
| 14100 | opencode serve，只听 127.0.0.1（外部只能经 `/oc`） |

容器 `read_only: true`；`/tmp` tmpfs 512m、`/data` tmpfs 1g（mode=1777，有嵌套绑定挂载时必须显式），
所以运行时状态不跨重启。绑定只有三处：`./web/pages`（功能）、`./config:ro`（设备表）、
`~/.config/mijia-api`（米家登录态，库写死路径）。

## 不要做

- 不加数据层：没有 sqlite、没有页面后端。要存东西用 `localStorage`。
- 不引第二种皮肤：没有 `?p=`、没有 `data-profile`、没有服务端注入。页面就是完整 HTML，能单独拷走。
- 不引构建步骤、CDN、外部字体、前端框架。图标用 `assets/icons.svg` 的 symbol 或内联 SVG。
  例外：固定页可用 `web/assets/vendor/` 的本地副本（Alpine + persist）；功能页不得引任何框架。
- 不写死密钥：走环境变量或 `.env`（不进 git）。web ↔ opencode 的服务密码由 entrypoint 生成。
- 不做登录体系（没有用户、没有会话管理）：页面 cookie、程序令牌，主边界是网络层。
- 不自动 `git commit` / `git push`。
- 不以 `HOMEOS_*` 之外的名义读环境变量；新变量一律先进 `app/config.py`。一次性操作当场用命令行做，
  不往 `entrypoint.sh` 里加分支。

## 已知的坑

- **米家必须 sync def**：`mijiaAPI` 一次读回约 1s，`async def` 会堵住事件循环，整站跟着卡。
- **写后要读回确认**：`set()` 只回 ack，云接口约 1s 延迟，所以间隔 0.4s 重读最多 4 次，等于目标值
  才算成功；前端拿服务端读回的值才变色，失败不假装成功（墨水屏上宁可等 1s 也别翻两次）。
- **缓存分层**：开关 3s 内存、温湿度 300s、烟感 900s，后两者落盘 `/data/mijia/state.json`；
  读失败退回旧值并标 `stale`。落盘是为了同一次运行内多房间共享一次云读取。
- **播报是助手这一轮的对账副产品**：`resync()` 确认"完成且有正文"就把正文压成一句丢给 `mijia.say`
  （截断在 `say()` 里做），同一段只念一次；失败只记日志。
- **`get_devices_list` 只调一次**（约 0.8s，缓存成常量）。新增设备要重启服务。
- **语音只在桌面/常规浏览器可用**：inkPalm 的 EinkBro 交付的音频断续丢块（同一段重放给 Azure 也识别不出），
  设备上只能用系统输入法。一轮音频必须共用同一个 `X-RequestId`，每帧换 id 云端只回 `turn.start`。
- **墨水屏开销**：轮询 ≥ 60s 且只在可见时；不要时钟、不要 hover；内容没变不重画。
  唯一例外是助手页的 SSE：服务端限流 4 次/秒，页面只在文本变化时重绘。
- **`tokens.css` 的 `!important` 是有意的**：页面是 agent 写的，不可能全部自觉。别为某页放开。
- **opencode 的消息落库滞后于事件**：正文要等步骤结束才进 `/message`，逐字文本只能来自 SSE 的
  `session.text.delta`；`done` 可能先到一帧（正文还没有），把它当结束就永远不显示回复。
- **事件委托**：监听挂容器上，别在渲染时逐个绑——静态 DOM 不会重绑，绑了就点不动。
- 米家重新登录必须人扫码；`/data` 是 tmpfs，重启后 opencode 会话与米家缓存都会重建。

## 前端 / Python

- 改前端（`web/assets/*`、`web/*.html`、`page.template.html`）前先看 `web/DESIGN.md` §1-§6
  与 `.agents/skills/modern-web-guidance/SKILL.md`（后者写了本项目覆盖通用指南的几条硬约束；
  只在 pi 里可用，容器里的 pages agent 读不到）。
- 页面只写 `rem`，只用 `tokens.css` 的变量；写死 `px` 宽度会在别的屏上溢出。
  起手用 `page.template.html`。
- 前端是原生 ES、无打包，`assets/*.js` 是 IIFE，不引模块系统。
- ruff：line-length 100、py312、`E F I UP B BLE SIM`。需要宽泛捕获时写 `# noqa: BLE001` 并说明原因。
- 后端只读 `app/config.py` 的常量；路由优先 sync def，异步只用在真需要长连接时。
