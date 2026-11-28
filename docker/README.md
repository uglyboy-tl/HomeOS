# docker/

当前状态：**一个容器**（python:3.12-alpine），uvicorn 监听 8080，agent 的 pi 由 web 按请求拉起子进程。
镜像同时出 amd64 与 arm64，约 218MB。

## 镜像里有什么

| 层 | 内容 |
|---|---|
| `pibin` | 用 bun 把 `@earendil-works/pi-coding-agent`（当前 1.7.0）交叉编成 musl 单文件。官方只发 glibc 包，alpine 是 musl，所以必须自编 |
| `piext` | `cc-safety-net` 的进程内扩展（只拷 `dist/`，入口 619B + chunks） |
| 运行时 | Python 依赖装进 `/app/.venv`（uv），`libstdc++` 必须有（bun 编出来的是 C++，缺它报 symbol not found）；最终镜像里没有 bun |

`prepare.py` 在启动前补配置：挂载目录缺哪份就从 `/app/defaults/` 复制哪份，容器模式再生成
`config/server.token`（0600，之后不再变）。它只补"人改了会生效"的文件，镜像里烤死的策略文件不在此列。

## 运行时布局

| 路径 | 是什么 |
|---|---|
| `/app` | 代码与模板，**只读**（`read_only: true`） |
| `/data` | tmpfs **128m**（1777），重启即清：pi 的 `$HOME`、米家缓存（实测 <1MB） |
| `/data/sessions` | 命名卷 `homeos-sessions`：对话会话，跨 `down/up` 与镜像升级都在 |
| `/tmp` | tmpfs 512m，唯一可写的临时区 |
| `./config` → `/app/config` | 真值配置（挂载，容器启动会检查可写，缺挂载直接退出） |
| `./pages` → `/app/web/pages` | 功能页（agent 唯一能写的地方） |
| `~/.config/mijia-api` → `/data/.config/mijia-api` | 米家登录态（rw，库要刷新 token；路径 `mijiaAPI` 写死） |

`entrypoint.sh` 依次做：`HOME=/data`、`PI_CODING_AGENT_DIR=/data/pi`、把 `config/pi-*.json` 软链进
`/data/pi`（改完点「新对话」即生效）、跑 `prepare.py`、`exec uvicorn`。
信任 Traefik 的转发头（只信任内网段，不写 `*`），所以日志里是真实客户端 IP、scheme 是 https。

环境变量（全部有默认值，正常部署不用碰）：

| 变量 | 作用 |
|---|---|
| `HOMEOS_DEV=1` | 打开 `--reload --reload-dir /app/app`；本机调试形态用 `compose.dev.yaml` 打开 |
| `HOMEOS_PI_BIN` / `_AGENT_DIR` / `_EXTENSIONS` / `_SESSION_DIR` | 覆盖 pi 的路径（本机开发指向本机那份） |
| `HOMEOS_CONFIG_DIR` / `HOMEOS_LOG_LEVEL` | 覆盖配置目录与日志级别 |

## 两种部署形态

- **生产**：只有 `compose.yaml`。镜像从 ghcr 拉，宿主只挂 `config/` 与 `pages/`。
- **调试**：加一份自写的 `compose.dev.yaml`（不进仓库，NAS 上常驻），把 `app/ web/ docker/` 挂进来、
  设 `HOMEOS_DEV=1`，改代码不用重建镜像。`scripts/sync.sh` 会自动识别 NAS 上有没有这个文件。

`sync.sh` 推 `app/`（可 `--delete`）与 `web/`（`--delete` 但排除 `pages/`，否则会把 NAS 上的功能页删成示例）；
`config/`、`.env`、`docker/` 一律不推。

## 构建

- CI：`.github/workflows/image.yml` 在 main 与 `v*` tag 上先 ruff + pytest（覆盖率门槛 95%），
  再用原生 arm 与 amd64 runner 各建一份、按 digest 合并推 `ghcr.io/<owner>/<repo>`。
  tag 有 `latest`、`sha-xxxxxxx`、`v*`。PR 只跑测试，不构建。
- 本地：`docker compose build`（x86 机器要出 arm64 先 `docker run --privileged --rm tonistiigi/binfmt --install arm64`）。
- GHCR 引用必须全小写；owner 或仓库名带大写会 `invalid reference format`。

## 改镜像时的坑

- **镜像层是累加的**：`COPY --from` 拷进来的二进制后面 `rm` 只盖层、不减体积；
  "装完就卸"的东西必须在同一个 `RUN` 里装与卸，否则镜像反而变大。
- **pi 版本升级**：改 `ARG PI_VERSION` 即可（当前 1.7.0）。自编的二进制拿不到上游的额外资产：
  `dist/theme`、`dist/export-html`、图片缩放用的 photon wasm 都不在 npm 包里，所以导出 HTML 与图片缩放
  不可用（我们用不到）。版本号靠构建前写进 `package.json` 注入，否则 `pi --version` 会报 `0.0.0`。
  升级后要验：chat 一轮、工具调用、`--extension` 仍是绝对路径、`bundle` 的 `--no-compile-autoload-*` 参数没变。
- 想在容器里改 `app/*.py` 立即生效：只开 `HOMEOS_DEV=1` 就够，但每次存盘 app 进程会重启，
  助手页正在生成的那一轮会断（重发一句即可）。
