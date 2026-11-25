# homeos

家庭局域网的能力网关，跑在 NAS 的一个容器里：**后端是主体**（米家设备控制、家庭 agent、
语音识别与播报），**web 只是它的第一个消费者**——墨水屏（inkPalm 5、Kindle 第三方浏览器）
打开就是家里的一面面板，孩子说一句/点一下，容器里的 agent 就改一页。

它只包"别处做不了或做起来贵"的能力：设备与执行（米家的坑）、智能（agent/ASR/播报）、
聚合（一次请求拿到家里状态）。Jellyfin、Immich、Miniflux 这些已有成熟 API 的服务不包一层——
局域网里的其他程序直接调它们就好。

- **定位、接口契约与取舍：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**
- 前端设计规范（缓存优先、Alpine 用法、刷新原则）：[`web/DESIGN.md`](web/DESIGN.md)
- 米家设备表怎么写：[`config/README.md`](config/README.md)
- 代码原则与已知的坑：[`AGENTS.md`](AGENTS.md)

## 现在能用的

| 页面 | 说明 |
|---|---|
| `/` | 首页宫格：固定页写死，功能页从 `/api/pages` 追加 |
| `/mijia` | 米家：房间标签 + 开关 + 温湿度/烟感；缓存首帧 + 只拉当前房间的设备 |
| `/agent` | 助手：聊天/设计/高级三个模式 + 对话区 + 语音输入；后端是 `/api/agent/*` |
| `/p/<slug>` | agent 写的功能页（`web/pages/<slug>/`），一个目录一页 |

接口分三类（详见 `docs/ARCHITECTURE.md` §2、§6）：**通用能力**（`/api/mijia/*`，局域网内其他应用
可以直接调，只加不改）、**会话型**（`/api/agent/*`、`/api/asr/stream`，只给自家页面）、
**页面私有**（静态页面与 `/api/pages`）。`/oc/*` 是 opencode 反代，**必须带 `X-HomeOS-Token`**。

只有一种皮肤（墨水屏）：纯黑白、零动画、rem 缩放，`web/assets/tokens.css` 是唯一的样式基线。

## 开发

```bash
./docker/entrypoint.sh up            # web(8080) + opencode(14100)
./docker/entrypoint.sh restart web   # web|agent|all
./docker/entrypoint.sh status|log|down

uv run pytest -q                     # 74 条，不联网
uv run ruff check . && uv run ruff format .
bunx @biomejs/biome@2.6.8 check --write .   # 前端（web/ 下的固定页与 assets）lint + 格式化
```

改 `web/pages/*` 或 `config/mijia.yaml` 不用重启（前者刷新即见，后者按 mtime 重读）。

前端格式化只覆盖人能改的那部分（`web/*.html`、`web/assets/*.{html,js,css}`，见 `biome.jsonc`）；
`web/pages/**` 是 agent 写的地盘，不参与格式化。biome 只在**本机手动跑**：CI 不装 bun、也不检查前端格式，
NAS 上同样没有 bun。
规则里 `a11y`、`complexity`、`style` 三组是关掉的，原因写在 `biome.jsonc` 注释里。

容器里也想改 `app/*.py` 立即生效（不重建容器）：给 compose 加上 `HOMEOS_DEV: "1"`，
entrypoint 会带 `--reload --reload-dir /app/app`。代价是每次存盘 app 进程重启，
助手页正在生成的那一轮会断（前端会自动重建会话），米家与 opencode 的启动动作会重跑一遍。
没有 `watchfiles` 时 uvicorn 退化成 0.25s 轮询，本机这个文件量无所谓。

## 部署（NAS）

NAS 上统一放在 `Docker/` 目录下（群晖 `/volume1/Docker/HomeOS`、威联通 `/share/Docker/HomeOS`），
下面命令里的路径都按这个来。调试阶段直接整份传过去、在 NAS 上本地构建：

```bash
# 本机 → NAS（不传虚拟环境、.git、缓存；data/ 与 NAS 本地状态不覆盖）
rsync -av \
  --exclude .venv --exclude .git --exclude .devdata --exclude .playwright-cli \
  --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
  ./ nas:Docker/HomeOS/

# NAS 上
cd Docker/HomeOS
mkdir -p data ~/.config/mijia-api && cp .env.example .env   # 没有必填项，只有模型/搜索 key 要填
uv run python -m mijiaAPI login             # 首次：扫码，登录态落在 ~/.config/mijia-api（没装 uv 就用 pip3 install --user mijiaAPI 再 python3 -m mijiaAPI login）
docker compose up -d --build
```

改前端（`web/`）同步后刷新即可；改 `app/` 或 `docker/` 要 `docker compose up -d --build`。
镜像只支持 arm64：NAS 是 x86 的话先 `docker run --privileged --rm tonistiigi/binfmt --install arm64`，
之后构建走模拟，会慢很多。

米家要两样东西，缺一样米家页就是 503（其它页不受影响）：

| 要什么 | 放哪 | 怎么进容器 |
|---|---|---|
| 设备表 `mijia.yaml` | 仓库里的 `config/` | compose 已挂 `./config:/app/config:ro`；镜像里也带一份当默认 |
| 登录态（`auth.json` + 设备 json） | 宿主机 `$XDG_CONFIG_HOME/mijia-api/`（没设时是 `~/.config/mijia-api/`） | compose 把这个目录挂到 `/data/.config/mijia-api`（rw，库要刷新 token） |

登录态不拷贝：哪台机器跑容器就在哪台登录一次，容器里读的就那一份。在 NAS 上重新扫码后
`docker compose restart` 让容器重新构造米家客户端。

挂载源是 `${XDG_CONFIG_HOME:-$HOME/.config}/mijia-api`，XDG 改了目录也能跟上。但 `mijiaAPI`
自身写死 `~/.config`，不认 XDG：如果你把 `XDG_CONFIG_HOME` 改到了别处，要么在那儿登录一次让
库把 `mijia-api/` 建在那里，要么把已有目录移过去——否则挂到的是空目录，米家页 503。

opencode 的服务密码（web ↔ opencode 之间）由 entrypoint 首次启动自动生成，
落在 `data/auth/opencode`，不需要任何人手填。

镜像只支持 arm64，opencode 版本与 sha256 钉在 `docker/Dockerfile`。镜像里带一份仓库里的
`config/mijia.yaml`，不挂卷也能跑；`config/` 只读挂进容器后以机器上那份为准，改设备表不用重建镜像。
本机没有 docker，这套还没在真机验证过。

### 用 GitHub 构建的镜像

`.github/workflows/image.yml` 在 main / `v*` tag 上跑：先 ruff + pytest，再用 QEMU 交叉构建
arm64 镜像并推到 `ghcr.io/<owner>/<repo>`。tag：`latest`（默认分支）、`sha-xxxxxxx`、`v*` 对应版本号。
**PR 只跑测试，不构建也不推包**（arm64 要跑 QEMU，一次十几分钟）。
GHCR 的引用必须全小写，owner 或仓库名带大写会 `invalid reference format`。

用镜像时把 `compose.yaml` 里 `build:` 那两行和 `image: homeos:dev` 一起换掉：

```yaml
services:
  homeos:
    image: ghcr.io/<owner>/<repo>:latest   # 替掉 image: homeos:dev，并删掉 build/image 那三行
```

```bash
docker login ghcr.io                  # 包是 private 时必需
docker compose pull && docker compose up -d
```

`data/`（运行时状态、密钥）还是要自己准备。

前提：仓库已推到 GitHub（现在还没有 remote）。

## 认证：自家页面免输入，其他设备要令牌

| 谁 | 怎么过 | 体验 |
|---|---|---|
| 自家前端（任何浏览器打开页面） | 页面响应种 `HttpOnly; SameSite=Strict` 的 `homeos` cookie（值是令牌的 HMAC 派生值，不是原文），之后 fetch 自动带上 | 零感知，页面代码里没有令牌 |
| 其他设备/程序 | 请求头 `X-HomeOS-Token: $HOMEOS_TOKEN` | 一行 header |
| `/api/ready` | 不认证 | 容器健康检查用 |
| `/oc` | 只认请求头（等于 shell 权限，cookie 不行）；未配令牌 → 403 关闭 | — |

`HOMEOS_TOKEN` 没配时：页面与 `/api/*` 照常可用（本机开发），`/oc` 关闭。生成：
`openssl rand -hex 24`。

这层挡的是误用与扫描，**不是**定向攻击——任何能 GET 页面的人都拿得到合法 cookie（只是调不了 `/oc`）。
主边界在网络层：
家里有公网 IPv6，Traefik 听 `[::]:443` 且证书是泛域名，用 `movie.uglyboy.cn` 的 AAAA 就能拿到
家庭地址，再拿 `Host: homeos.uglyboy.cn` 打进来（2026-11 实测从公网侧返回 200）。所以 Traefik
上要补 `ipAllowList`（只放内网段）。

## 没做的

语音（ASR/TTS）、相册（Immich）、页面后端与数据库。
