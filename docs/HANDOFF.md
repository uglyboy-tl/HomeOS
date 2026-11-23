# 交接：kiosk 重构（从零搭建新框架）

> **已过时**：本文描述的是重构初期的设想，实现已在多处偏离（无 `shell.html`/iframe、无 `rich.css`/`eink.css`、
> 无 `data-profile`、无页面后端 `api.py`/sqlite、`/api/home/*` 改名为 `/api/mijia/*`）。
> 以代码、`README.md`、`AGENTS.md`、`web/DESIGN.md` 为准，本文仅作历史参考。

写于 2026-11-23。下一轮在**新建的项目目录**里重新搭建，旧仓库只作为参考实现和 git 备份。

---

## 1. 起点状态（已完成，不用重做）

旧仓库 `/home/uglyboy/Code/kiosk`，分支 `main`，**无 remote**。

- 工作区**已干净**，所有改动已在 5 个提交里落地（都只是原来的前端/art 工作，与重构无关）：
  `4ac747b` modern-web-guidance 技能 · `9977312` 控制中心改原生 dialog · `96ad4f9` webkit 滚动条清理 · `148a4d7` 文档判据 · `36f1132` art 动画规则
- 因此旧实现可随时 `git show` / checkout 查阅，重构期间**不要**在旧仓库里继续改动。

要被吸收的第二个项目：`/home/uglyboy/Documents/WorkSpace/eink-panel`
（`app.py` 米家后端 + `static/home.html`、`static/smart-home.html` 墨水屏前端 + `README.md` 里 14 条墨水屏设计规范）。
迁移完成后再删它；删除前先备份米家登录态 `~/.config/mijia-api/auth.json`。

## 2. 重构目标（用户原话要点）

- rpi + 7 寸屏这套"太重"，改为：**服务跑在 NAS（或 rpi）上提供网页，墨水屏设备直接开网页**，重活交服务端。
- 把 eink-panel 的米家控制整合进来，`eink-panel` 从 WorkSpace 清除。
- 前端按设备给不同页面：**由请求方发起不同请求，不靠代码判定设备**。
- 前端要能对不同分辨率自适应（主要针对墨水屏）。
- 后续做成 Docker 镜像：**只有 agent 能开发的部分挂卷**，保证服务稳定性。
- 现在：彻底重构，"先忘记有什么，重新思考要做什么"，**在新项目里从零搭建**。

## 3. 已拍板的决定（用户已确认，不要再问）

| 项 | 决定 |
|---|---|
| 目标客户端 | inkPalm 5（EinkBro/Chromium）、**Kindle + 第三方浏览器**、手机/平板也算。**Kindle 内置浏览器不作为目标**（所以可以用现代 CSS：flex/grid/rem/min()） |
| 两种屏的页面策略 | **墨水屏优先，普通屏复用**：一份 HTML，靠皮肤覆盖 |
| Docker 形态 | **单容器多进程**（s6-overlay 或 supervisor），不是 compose 多服务 |
| rpi + 7 寸屏 | **只当客户端**（Chromium 全屏），服务与 agent 全在 NAS |
| 删掉的功能 | 硬件指标（`/stats.json`、`services/proc.py` 的 `StatsRunner`）、重启/关机与二次确认页、`/api/reset`、**整个控制中心**（`api/control.py` + `dim_loop` + 亮度/音量/WiFi 状态）、屏保/锁屏页不进 v1 |
| 保留 | `/api/ready`（容器健康检查）、米家、语音 ASR/TTS、Immich 相册、opencode 反代 |

删除的连带影响（已和用户对齐）：服务搬到 NAS 后，所有"读/写本机硬件"的能力都失效，所以控制中心整块拆掉；亮度交给客户端（rpi 一个 systemd timer 写 sysfs，或不自动调暗）；客户端因此**不需要任何本地小服务**；容器不需要 `/sys`、不需要 pipewire，可彻底 `read_only`。

## 4. 设计（本文件是唯一载体，尚未写进任何 spec 文件）

### 4.1 系统只有三层

```
① 页面空间   agent 唯一能写的地方：一份 HTML + 可选同名 Python（+ sqlite）
② 能力层     服务端替客户端做的重活：米家云、ASR/TTS、相册代理、opencode 编排
③ 编排层     agent 读写 ①，浏览器只读 ①
```
不在这三层里的东西，v1 一律不进。

### 4.2 目录（`image/` 是镜像构建上下文，除两个卷外全只读）

```
image/
  app/
    main.py            装配：路由表 + lifespan
    config.py          唯一读环境变量的地方（DATA_DIR / WEB_ROOT / PAGES_DIR / key）
    routes/            pages.py static.py oc.py home.py voice.py album.py health.py
    services/          home.py(米家) speech.py asr.py tts.py opencode.py proc.py
    pageworker.py      页面后端进程（127.0.0.1:14101）
  web/
    shell.html         唯一壳：tab 栏 + iframe（原生 JS，**不引 Alpine/Bulma**）
    eink.html          墨水屏入口：无 iframe，宫格直出
    assets/            tokens.css rich.css eink.css shell.js page.js icons.svg
    pages/             ← 卷 1（agent 可写）
  opencode/            XDG_CONFIG_HOME：AGENTS.md agents/ skills/ opencode.jsonc
  s6/                  web / pages / agent 三个长驻单元
client/                ← 不进镜像：rpi 的 labwc 配置 + Chromium 启动脚本
compose.yaml Dockerfile docs/ scripts/
```

卷只有两个：`/app/web/pages`（agent 可写代码）和 `/data`（sqlite、tts 缓存、opencode session+snapshot、米家缓存与 token）。

### 4.3 皮肤契约（一份 HTML 喂两种屏）

入口决定 profile，**不由设备判断**：`/` = rich，`/e/` = eink。服务端在 `<head>` 注入对应皮肤并在 `<html>` 上写 `data-profile`，`<body>` 末尾注入导航条。

```css
:root {
  --vw: 128; --vh: 72;        /* rich = 1280×720 虚拟画布 */
  --bg:#fff; --fg:#000; --line:#000; --accent:#…;
  --r:18px; --shadow:3px 4px 0 var(--line);
  --fs-title:2.4rem; --gap-md:1.2rem;
}
html[data-profile='eink'] {
  --vw:36; --vh:64; --r:0; --shadow:none; --accent:#000;   /* 纯黑白 */
}
html { font-size: calc(min(100vw / var(--vw), 100vh / var(--vh)) * 10); }
```

- 页面**只用 rem**、只用 `--bg/--fg/--line/--accent` 四色与四档字号 → 360×640 / 600×800 / 1280×720 自动等比。
- `eink.css` 靠覆盖变量就够；`!important` 只留一组兜底：`animation/transition/border-radius/box-shadow` 清零、`background-image:none`、`-webkit-tap-highlight-color:transparent`。
- **兜不住的边界**（写进文档，别假装能兜）：页面写死 px 宽度、彩色位图、canvas 里的彩色绘制。
- 墨水屏禁区：不轮询快于 60s、不用 SSE 逐字更新、不做时钟、无 hover。

### 4.4 能力契约（页面与客户端看到的全部）

```
GET  /api/home/state          米家全量（内存 3s / 温湿度 300s / 烟感 900s）
POST /api/home/switch         {id, on} → 读回确认后的真实值
POST /api/asr                 16k 单声道 WAV → {text}
POST /api/say · /tts/c/<hash> 切句合成 + 缓存
GET  /api/album/*             Immich 代理（注入 key）
POST /pageapi/<page>/<action> 页面自己的后端
ANY  /oc/*                    opencode 反代
GET  /api/ready               容器健康检查
```

页面作者可见的另外三样：`pages/tabs.json`（清单）、`.py` 后端契约 `init(db)` / `handle(action, payload, db)`、`pagedb` / `pagedel` 两个小工具。

### 4.5 保留 / 重写 / 删除（建议，待用户确认后执行）

| 原样搬（只改 import 与路径） | 理由 |
|---|---|
| `pageworker` 的模块热加载、同页串行、并发 2 上限、seed 机制 | pyc 校验只到"秒+大小"、sqlite 共享连接不能交错，都是踩出来的 |
| `speech.py` 切句、`asr.py` 三家链路自动选 | 有测试、有实测耗时表 |
| `opencode` 反代 + SSE 逐块转发 + 权限 AutoDeny | 长连接退出、权限卡死重来一遍不值 |
| `pagedb` / `pagedel` 的约束 | 页面数据安全边界 |

重写：web 壳与全部 assets、静态路由（profile 注入）、目录、配置、容器、**米家后端（新写）**。

### 4.6 实施顺序（每步可单独验证，合计约 8 小时）

1. 骨架：目录 + `config.py` + 静态路由 + profile 注入 + 空宫格（验证：`/` 与 `/e/` 都出图标）
2. 页面后端：pageworker + `tabs.json` + `pagedb/pagedel`（验证：一页带 sqlite 的页跑通）
3. agent 装配 + 撤销（验证：说一句能改页面）
4. 米家（验证：一页开关控制真实设备）
5. 语音（验证：说一句有回复有声）
6. 容器 + 客户端（验证：墨水屏打开 NAS 地址）

## 5. 必须带过去的坑（都是本次分析中确认过的）

1. **麦克风**：`getUserMedia` 只在 secure context 可用。rpi 访问 `127.0.0.1` 算 localhost，改成访问 `http://nas:8088` 就没了。两条路：NAS 上加 Caddy + 自签/内网证书（推荐，一次覆盖所有客户端），或客户端 Chromium 加 `--unsafely-treat-insecure-origin-as-secure=http://nas:8088`。
2. **opencode 状态必须落 `/data`**：`XDG_CONFIG_HOME` 指只读配置根，`XDG_DATA_HOME=/data`；session 与 **snapshot（撤销依赖它）**在卷里，否则一重启撤销就失效。
3. **米家 token**：mijiaAPI 按 `HOME` 找 `~/.config/mijia-api/auth.json`，镜像里 `HOME=/data` 就落到 `/data/.config/mijia-api/auth.json`。重新登录必须用户自己扫码，不要代跑。
4. **插件缓存**：`cc-safety-net@latest` 在运行时下载，缓存目录必须在 `/data`，否则每次重启重装（需实测）。
5. **opencode 是否往 cwd 落文件**：cwd 计划设 `/app/web`（只读，只有 `pages/` 是卷）。若实测会写 cwd，就得把 cwd 改到 `pages` 并同时改权限路径口径（现在权限写 `pages/**`）。
6. **撤销是两段**：opencode 快照恢复 + 撤销前调 `/api/pages/reload` 让 pageworker 放开 sqlite 连接。
7. **页面可见性别只靠 `visibilitychange`**：shell 用 `hidden` 切页，子文档 `document.hidden` 未必跟着变，以 shell 发的 `kiosk:visible` 为准，且处理函数要幂等。
8. **米家后端要 sync def**（走线程池）：mijiaAPI 一次读回约 1s，别放进 async 堵事件循环；`set` 后要间隔重读 4 次 ×0.4s 才算确认。
9. **墨水屏 14 条规范**在 `~/Documents/WorkSpace/eink-panel/README.md`（纯黑白、零动画、零圆角、按钮 ≥88px、一屏不滚动、反白表示"开"、点后即时翻转本地状态等），是新 `eink.css` 与页面的直接依据。
10. 旧页面里 `pages/*` 不进 git（本地有 `.gitignore` 写 `*`），设备上没有这个文件——迁移页面时注意。

## 6. 未决问题（上一轮提问被用户跳过，需要在新会话里重新确认）

1. **"从零"到哪一层**：只重写壳与架构、服务层保留（建议）／连 pageworker+语音+opencode 一起重写（耗时翻倍且重踩坑）／只重写前端。
2. **v1 范围**：米家 / 语音 / 相册 / 页面撤销，四项都做还是先做最小闭环（建议至少含米家 + 撤销）。
3. 亮度自动调暗（白天 100%、22:00-07:00 20%）是否保留（保留 = 客户端一个 timer）。

## 7. 用户与仓库约定（新项目里要重新立 AGENTS.md）

- 全程中文；正文不用 Unicode 连字符。
- **不自动 `git commit` / `git push`**（本次是用户显式要求才提交的）。
- 简洁优先：不写未请求的抽象，简化处留 `// ponytail:` 注释说明天花板。
- 改动超过 3 步、涉及删除/覆盖时先给方案再动手。
- 本机没有真 Node，一律 `bun` / `bunx`；浏览器自动化用 `playwright-cli`，只用系统已装的 Edge/Chrome，**永不执行 `playwright install`**。
- 多个独立工具调用合并成一条 bash，或写一个 codemode 脚本批量跑。

## 8. 建议下一轮调用的技能（Skill 工具）

| 场景 | 技能 |
|---|---|
| 写/改新壳与页面、查原生控件与 CSS 做法（皮肤、布局、对话框、大命中区） | `modern-web-guidance` |
| 起自己的无头浏览器截图看 `/` 和 `/e/`、读 console 报错、模拟 360×640 与 600×800 视口 | `debug-page`（配合 `playwright-cli`） |
| 需要核对 Docker/s6-overlay、opencode XDG 目录、FastAPI 现代写法的**当前**官方文档 | 先用 codemode 里的 `mcp__context7__*`；多来源交叉验证再考虑 `research` |
| 写新项目的 AGENTS.md / agent 定义 / SKILL.md，或规则被忽略、过度自检 | `prompt-optimize` |

## 9. 参考文件（旧实现，按需阅读而不是全读）

- `/home/uglyboy/Code/kiosk/host/kiosk/` — 现有 FastAPI 装配、`pageworker.py`、`static/routes.py`、`settings.py`
- `/home/uglyboy/Code/kiosk/host/opencode/` — 三个 agent 定义、skills、`opencode.jsonc`（权限写法）
- `/home/uglyboy/Code/kiosk/web/DESIGN.md`、`BACKEND.md`、`GAMES.md`、`ART.md` — 页面与后端契约的现有版本
- `/home/uglyboy/Code/kiosk/host/systemd/user/*.service` — 现在的进程参数与沙箱设置（容器化时对照）
- `/home/uglyboy/Documents/WorkSpace/eink-panel/app.py` — 米家设备表与三层缓存的直接来源
- `/home/uglyboy/Documents/WorkSpace/eink-panel/static/smart-home.html` — 墨水屏面板的参考实现（即时反馈、指纹比对防重绘）
