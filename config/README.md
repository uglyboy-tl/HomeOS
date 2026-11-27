# 配置目录（挂载进容器的 `/app/config`）

仓库里这些都是**空白模板**，真值在各自的配置目录：

| 场景 | 目录 |
|---|---|
| 本机开发 | `.devdata/config/`（`scripts/dev.sh` 首次从模板复制） |
| 容器 | 宿主机 `./config/`（compose 挂载） |

挂载目录为空时，`docker/prepare.py` 从镜像的 `/app/defaults/` 复制模板过去（已有文件不覆）。

| 文件 | 改完什么时候生效 |
|---|---|
| `mijia.yaml` | 下一次请求（按 mtime 重读） |
| `agents.yaml` `agents.md` | 刷新页面看到新标签；**已起着的会话仍用旧 prompt**，点「新对话」才换 |
| `pi-models.json` `pi-settings.json` | 同上，pi 启动子进程时读；软链到 `/data/pi/`，不用重启容器 |
| `homeos.yaml` | 重启容器（服务接入信息只在启动时读一次） |
| `server.token` | 重启容器 |

`cc-safety-net-policy.json` 是**源文件**：构建期被拷进镜像的只读层
（`/app/web/.cc-safety-net/policy.json`），改它要重建镜像——运行时不读 `config/` 里的这份。

## `homeos.yaml` — 外部服务

```yaml
gotify:
  url: "http://gotify"
  token: ""
beszel:
  url: "http://beszel:8090"
  user: "" # 只读账号
  password: ""
mpd:
  host: "192.168.0.100"
  port: 6600
azure_asr:
  endpoint: "eastasia.stt.speech.microsoft.com"
  api_key: ""
  lang: "zh-CN"
```

| 键 | 说明 |
|---|---|
| `gotify.url` / `token` | 播报通知用；缺任一项该功能不可用 |
| `beszel.url` / `user` / `password` | 本机指标用；缺任一项该功能不可用 |
| `mpd.host` / `port` | 无密码；`port` 留空 = 6600 |
| `azure_asr.endpoint` / `api_key` / `lang` | `endpoint` 不带 `wss://`；`lang` 必须是完整 locale（裸 `zh` 会 400） |

- **只有 `mpd.port`、`azure_asr.endpoint`、`azure_asr.lang` 有内置默认值**，删掉或留空回落到
  `app/config.py` 的 `DEFAULTS`；服务地址、账号、密码都必须写在文件里。
- **token 类凭据可用环境变量覆盖**（`GOTIFY_TOKEN`、`AZURE_SPEECH_KEY`），密码类不行。

## `server.token`

对外令牌（0600 的纯文本），容器首次启动由 `prepare.py` 生成，也可手写。留空 = 生产全关。
换令牌后所有客户端要重新取一次。

## `pi-models.json` / `pi-settings.json` — pi 自己的配置

- `pi-models.json`：provider（`baseUrl`/`api`）、模型能力（`reasoning`、`contextWindow`）、
  `compat.thinkingFormat`，以及 `apiKey`——可以写死，也可以写 `${OPENCODE_GO_API_KEY}` 从环境变量取。
- `pi-settings.json`：`defaultProvider`/`defaultModel`、`defaultProjectTrust`、遥测与界面开关。

`--provider`/`--model` 不再走命令行，全部以这两个文件为准。

## `agents.yaml` / `agents.md` — 页面上的模式

`agents.yaml` 一条一个模式：`id`、`label`、`hint`、`thinking`、`tools` 白名单、`prompt`。
`agents.md` 是所有模式共用的工作区说明。加模式只改这两个文件，代码不认具体 id。

`tools` 里的工具名就是 pi 的内置工具（`read write edit grep find ls`，`bash` 刻意不给改页面模式）。

## `mijia.yaml` — 米家设备表

改设备不用改代码：房间、顺序、did、属性名全在这。两级结构，两级都是页面上直接显示的中文名字：

```yaml
rooms:
  客厅:
    吸顶灯: { did: "1095838227", prop: "on-3" }     # 没有 kind = 开关
    温湿度:                                          # 有 kind = 传感器
      kind: th
      did: "1095838227"
      props: { temperature: "temperature-6", humidity: "relative-humidity-6" }
# 播报用的音箱：不在房间里，它是「念出来」这个出口
tts: { did: "380188346" }
```

| 字段 | 说明 |
|---|---|
| 房间名 / 设备名 | 既是显示名也是 id。改名等于换了台设备 |
| `did` | 设备 did，不是设备名 |
| `prop` | 开关要写的属性名：`on`、`on-2`、`on-3` |
| `kind` | `th`（温度 + 湿度）或 `smoke`（状态 + 电量） |
| `props` | `th` 要 `temperature`/`humidity`；`smoke` 要 `status`/`battery` |
| 顶层 `tts.did` | 默认播报音箱；不写就必须每次显式指定 `did` |

写设备表时的四个坑：

- **所有字符串加引号**：YAML 1.1 里 `on` 是布尔、纯数字会读成整数，属性名就对不上。
- 名字里不能有 `/`（它拼 id 用）。
- 新增设备要重启服务：`get_devices_list` 只调一次（约 0.8s），缓存在进程里。
- 这个文件含真实 did（设备指纹，不是凭证），**不要随仓库外发**。
