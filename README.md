# homeos

家里的屏的网页服务：一台 NAS 上跑一个容器，墨水屏（inkPalm 5、Kindle 第三方浏览器）打开就是家里的面板；
家庭 agent 在容器里改页面，加一个功能就是加一个目录。

- **架构与取舍：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**（部分章节已落后于代码，见文件开头）
- 前端设计规范（缓存优先、Alpine 用法、刷新原则）：[`web/DESIGN.md`](web/DESIGN.md)
- 米家设备表怎么写：[`config/README.md`](config/README.md)
- 代码原则与已知的坑：[`AGENTS.md`](AGENTS.md)

## 现在能用的

| 页面 | 说明 |
|---|---|
| `/` | 首页宫格：固定页写死，功能页从 `/api/pages` 追加 |
| `/mijia` | 米家：房间标签 + 开关 + 温湿度/烟感；缓存首帧 + 只拉当前房间的设备 |
| `/agent` | 助手：聊天/设计/高级三个模式 + 对话区 + 输入条（只有前端，未接后端） |
| `/p/<slug>` | agent 写的功能页（`web/pages/<slug>/`），一个目录一页 |

接口：`/api/mijia/devices`、`/api/mijia/state?room=客厅`、`/api/mijia/switch`、`/api/pages`、`/api/ready`、
`/oc/*`（opencode 反代，**不做访问认证**，见下）。

只有一种皮肤（墨水屏）：纯黑白、零动画、rem 缩放，`web/assets/tokens.css` 是唯一的样式基线。

## 开发

```bash
./docker/entrypoint.sh up            # web(8080) + opencode(14100)
./docker/entrypoint.sh restart web   # web|agent|all
./docker/entrypoint.sh status|log|down

uv run pytest -q                     # 45 条，不联网
uv run ruff check . && uv run ruff format .
```

改 `web/pages/*` 或 `config/mijia.yaml` 不用重启（前者刷新即见，后者按 mtime 重读）。

## 部署（NAS）

```bash
mkdir -p data && cp .env.example data/env    # 没有必填项，只有模型/搜索 key 要填
docker compose up -d --build
```

opencode 的服务密码（web ↔ opencode 之间）由 entrypoint 首次启动自动生成，
落在 `data/auth/opencode`，不需要任何人手填。

镜像只支持 arm64，opencode 版本与 sha256 钉在 `docker/Dockerfile`；`config/` 只读挂进容器，
改设备表不用重建镜像。本机没有 docker，这条还没在真机验证过。

## `/oc` 不做认证（有意的取舍）

`/oc` 把请求转给容器里的 agent，而 agent 有 shell 权限。这里**不设口令**：
这套东西只在内网跑，页面就在同一个局域网里，加口令既挡不住想进的人（口令必然落在
页面或同一个网络里），又多一份要维护的东西。代价是：**能连上 8080 的人就能让 agent
跑命令**。要暴露到公网之前，先在反代层补认证/限源，而不是在这个路由上。

## 没做的

语音（ASR/TTS）、相册（Immich）、页面后端与数据库。
