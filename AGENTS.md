# HomeOS

家庭局域网的能力网关：一台 NAS 上的 FastAPI 容器，只包"别处做不了或做起来贵"的三样——
设备与执行（米家）、智能（agent/ASR/播报）、聚合。web 页面是它的第一个消费者，
目标屏只有墨水屏。自用项目：优先"跑得稳、改得动"，不为扩展性设计。

```
app/           镜像内 Python（只读）
  routes/      HTTP 映射：解析入参、调一个 service、翻错误码，到此为止
  services/    业务实现（米家、mpd、agent、asr、speech、notify、metrics）
  lib/         横切：security 认证 / errors 错误信封 / health 探活 / sockets WS 约定
  config.py    所有常量与配置读取，别处只读它
web/           纯静态零构建（frontend 见下）
web/pages/     ★功能页：agent 唯一可写的地方（挂载决定边界）
docker/        镜像层（见 docker/README.md）；compose.yaml 是部署形态
scripts/       dev.sh 本机托管 / sync.sh 只推 app/ web/ 到 NAS
config/        真值配置（homeos.yaml 服务与令牌、mijia.yaml 设备表、agents.yaml 模式）
docs/          ARCHITECTURE 接口契约与架构决定
```

三条分界线：

1. **固定页**（`web/*.html`）只有人能改，路由写死在 `app/routes/pages.py` 的 `FIXED`。
2. **功能页**（`web/pages/<slug>/`）是 agent 唯一能写的地方；写边界靠挂载，密钥靠 cc-safety-net 的 deny_paths。
3. agent 的工作目录是 `web/`；模式（prompt、工具白名单、思考档位）全在 `config/agents.yaml`。

## 接口设计原则

先分类，再设计；契约表在 `docs/ARCHITECTURE.md` §3，改了要同步。

- **按"承诺给谁"分类**：通用能力（无状态，局域网其他程序可直接调，只加不改）、
  会话型（内存态 + 常驻 SSE，只给自家页面，因此 uvicorn 单 worker）、页面私有（只发文件）、
  健康检查（`/api/ready`，不认证）。新增能力先在功能页里用起来，出现第二个客户端才升格为通用。
- **描述与执行分开**：`/api/info/*` 只报事实、不挂健康依赖（上游挂了也要能回答）；
  执行类挂 `health.deps(...)`，每请求先看缓存状态，冷却期内直接 503 而不是等超时。
- **路由只做四件事**：校验入参 → 调一个 service 方法 → 把领域异常翻成错误码 → 返回原始数据。
  格式化、排序、拼文案、算展示用的"正常/故障"都不在路由里。
- **成功返回资源本体，不包 `ok`**；失败一律 `{"error":{"code","message"}}`。
  `code` 是稳定枚举（见 `tests/test_contract.py` 的 `ERROR_CODES`），`message` 给人看，状态码说类别。
- **语义优先于便捷**：一个端点一件事，不用 `?action=` 做复合动作；设备 id 带斜杠就放 body 不放路径；
  写操作返回**读回后的真值**而不是 ack。
- **默认 sync def**：`mijiaAPI` 一次读回约 1s，`async def` 会堵住整个事件循环。
  async 只给真长连接（SSE、WS、子进程读循环）。
- **无状态优先**：缓存是加速不是状态；持久态只存在会话型接口与 `/data` 里。
- **一行 `curl` 直连上游能完成的事不进网关**（Jellyfin/Immich/Miniflux 都有自己的 API）。

## 认证两档

威胁模型见 `docs/ARCHITECTURE.md` §8：这层挡误用与扫描，主边界在网络层。

- 自家前端：页面响应种 `homeos` cookie（`HttpOnly; SameSite=Strict`，https 才 `Secure`），
  里面是令牌的派生值——页面响应不认证就发，写原文等于公开钥匙。
- 其他程序：`X-HomeOS-Token: <config/server.token>`（或 `Authorization: Bearer`）。
- 未配令牌时只有 `HOMEOS_DEV=1` 放行，生产一律 401；容器首次启动会自动生成一个。

## 代码原则

- **去冗余**：同一件事只在一个地方说；重复判断、只包装一行的函数、复述代码的注释都算冗余。
- **合理抽象**：重复两次以上且语义一致才抽，抽完要减少阅读量而不是把逻辑藏远。
- **语意性**：命名、结构、数据流自己说清做了什么。
- **注释要有预算**：模块 docstring ≤3 行，函数/类 ≤1 行，行内 `#` ≤1 行且贴着那一行。
  只留三种——非显然的约束（外部协议、并发、平台差异）、反直觉的取舍、不这样写就会错的地方。
  写完自问：删掉它会不会有人写错？不会就不是注释。
- **Python**：ruff line-length 100、py312、`E F I UP B BLE SIM`；宽泛捕获写 `# noqa: BLE001` 并说明原因。

## 前端

只一条：**动 `web/` 之前先读 [`web/DESIGN.md`](web/DESIGN.md)**，原则、数值来源、验收判据都在那里。
这里只留三条不进那篇的硬约束：不引构建步骤与 CDN、不引前端框架（固定页可用 `assets/vendor/` 的
Alpine 本地副本，功能页不得引）、页面只写 `rem` 且只用 `tokens.css` 的变量。

## 工作流

- 改动先在本地 `scripts/dev.sh` + `pytest` 验：页面、`/api/*`、米家、MPD、notify、ASR 本机都能跑。
- `sync.sh --go` 推 `app/`、`web/` 到 NAS 即生效（app 热重载、web 刷新）；只有镜像层靠重建验证。
- NAS 上的 `config/`、`.env` 是真值，脚本不推它们。

```bash
./scripts/dev.sh up|down|restart|status|log   # 本机后台托管（agent 由请求拉起，没有常驻进程）
./scripts/sync.sh [--go] [--restart]                   # 默认演练；docker/ config/ 不推
./scripts/release.sh [patch|minor|major|X.Y.Z] [--go]  # 发版：默认演练；--go 才改版本、提交、打 tag、推
uv run pytest -q                                       # 不联网，约 16 秒
uv run ruff check . && uv run ruff format .
bunx @biomejs/biome@2.6.8 check --write .              # 前端 lint + 格式化（本机手动跑）
```

## 不要做

- 不加数据层：没有 sqlite、没有页面后端；要存东西用 `localStorage`。
- 不引第二种皮肤：没有 `?p=`、没有 `data-profile`、没有服务端注入；页面就是完整 HTML，能单独拷走。
- 不做登录体系（没有用户、没有会话管理）。
- 不写死密钥：token/key 进 `config/homeos.yaml`，`app/` 里不出现密钥字面量。
- 环境变量只当"可轮换 token"的覆盖（`GOTIFY_TOKEN`、`AZURE_SPEECH_KEY`、`OPENCODE_GO_API_KEY`、`EXA_API_KEY`）；
  密码与运行参数走 `app/config.py`。路径类例外：`HOMEOS_PI_BIN/AGENT_DIR/EXTENSIONS/SESSION_DIR/CONFIG_DIR/LOG_LEVEL/DEV`。
- 一次性操作当场用命令行做，不往 `docker/entrypoint.sh` 里加分支（本机的放 `scripts/dev.sh`）。
- 不自动 `git commit` / `git push`。

## 已知的坑

- **米家写后必须读回**：`set()` 只回 ack，云接口约 1s 延迟；间隔 0.4s 重读最多 4 次，等于目标值才算成功。
- **米家缓存分层**：开关 3s 内存、温湿度 300s、烟感 900s，后两者落盘 `/data/mijia/state.json`；
  读失败退回旧值并标 `stale`。落盘是为了同一次运行内多房间共享一次云读取。
- **`get_devices_list` 只调一次**（约 0.8s，缓存成常量）：新增设备要重启服务。
- **播报是助手一轮的副产品**：一轮结束且有正文就丢给 `speech.say`（去 markdown 与截断都在那里），
  同一段只念一次，失败只记日志。
- **语音只在桌面/常规浏览器可用**：inkPalm 的 EinkBro 交付音频会丢块，设备上只能用系统输入法。
  一轮音频必须共用同一个 `X-RequestId`，每帧换 id 云端只回 `turn.start`。
- **pi 的 RPC 是行列协议**：按 `\n` 切 JSON，`response` 按键 id 对，其余是事件；
  `--extension` 路径按 cwd 解析，必须传绝对路径。
- **query 里放中文要 URL 编码**：非 ASCII 在 uvicorn 解析层就被拒成 400，到不了我们的代码。
  curl 用 `-G --data-urlencode`，页面用 `encodeURIComponent`，写接口走 body 不受影响。
- **米家重新登录必须人扫码**；登录态与对话会话都在 `/data` 的卷里，重启不丢。
