# 配置目录（挂载进容器的 `/app/config`）

`homeos.yaml` 放外部服务的接入信息，`mijia.yaml` 放米家设备表。仓库里这两份是**空白模板**，
真值放在各机器自己的配置目录：

| 场景 | 目录 | 进 git |
|---|---|---|
| 本机开发（`scripts/dev.sh`） | `.devdata/config/`（首次从模板复制） | 不进 |
| 容器（compose 挂载） | 宿主机 `./config/` | 不进（部署目录） |

挂载目录为空时，启动从镜像的 `/app/defaults/` 复制模板过去（已有文件不覆），再写入令牌与
opencode 口令。本机改完保存即生效（uvicorn 监听该目录）；生产重启容器。

## `homeos.yaml` — 外部服务

```yaml
gotify:
  url: "http://gotify"
  token: ""
beszel:
  url: "http://beszel:8090"
  user: ""
  password: ""
mpd:
  host: "192.168.0.100"
  port: 6600
azure_asr:
  endpoint: "eastasia.stt.speech.microsoft.com"
  api_key: ""
  lang: "zh-CN"
opencode:
  serve_password: ""
  go_api_key: ""
  exa_key: ""
server:
  token: ""
```

| 键 | 说明 |
|---|---|
| `gotify.url` / `token` | Gotify 的地址与 Apps 里的应用 token；缺任一项 `/api/notify` 整个接口 503 |
| `beszel.url` / `user` / `password` | 读本机指标用的只读账号；缺任一项 `/api/metrics` 返回 503 |
| `mpd.host` / `port` | 音乐服务地址（无密码）；`port` 留空 = 6600 |
| `azure_asr.endpoint` / `api_key` / `lang` | 微软 Azure 实时识别；`endpoint` 是不带 `wss://` 的区域域名，`lang` 必须是完整 locale（裸 `zh` 会 400） |
| `opencode.serve_password` | web ↔ opencode serve 的口令，容器启动时自动生成、每次重换 |
| `opencode.go_api_key` / `exa_key` | 模型供应商与网页搜索的 key；缺 `go_api_key` 时 `/api/agent/*` 全 503 |
| `server.token` | 对外令牌（页面 cookie 与 `X-HomeOS-Token` 都由它派生）；空则启动时生成、之后固定 |

三条规则：

- **只有 `mpd.port`、`azure_asr.endpoint`、`azure_asr.lang` 有内置默认值**，删掉或留空会回落到
  `app/config.py` 的 `DEFAULTS`；服务地址、账号、密钥都必须写在文件里。
- **token 类凭据可用环境变量覆盖**（`GOTIFY_TOKEN`、`AZURE_SPEECH_KEY`、`OPENCODE_GO_API_KEY`），
  密码类（`beszel.password`、`opencode.serve_password`）只能写在文件里。
- `server.token` 与 `opencode.serve_password` 由 `docker/prepare.py` 启动时行级改写（不动注释）。

## `mijia.yaml` — 米家设备表

改设备不用改代码：房间、顺序、did、属性名全在这个文件里。改完下一次请求就生效
（按 mtime 重新读），不用重启服务。

两级结构，`房间 → 设备`，两级都是页面上直接显示的中文名字：

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

### 字段

| 字段 | 说明 |
|---|---|
| 房间名 / 设备名 | 既是显示名也是 id（接口里用 `客厅/吸顶灯`）。改名等于换了台设备 |
| `did` | 设备 did，不是设备名 |
| `prop` | 开关要写的属性名：`on`、`on-2`、`on-3` |
| `kind` | `th`（温度 + 湿度，显示成 `24.5° 34%`）或 `smoke`（状态 + 电量） |
| `props` | `th` 要 `temperature`/`humidity`；`smoke` 要 `status`/`battery` |
| 顶层 `tts.did` | 默认播报音箱。不写 = `/api/mijia/say` 必须显式带 `did` |

### 播报怎么念话

`POST /api/mijia/say {"text":"开会了"}` —— 用顶层 `tts.did`；
`{"text":"开会了","did":"380623598"}` —— 指定另一台音箱（纯数字，非数字回 400）。
文本当动作参数发给小米云端，语音合成在云端做，本地不生成音频。换默认设备改 `tts.did`。

三个踩过的坑（写设备表时别再试错）：

- 不能写 `text-content` 属性（spec 里它是只读，云会回「Property不可写」）；
- 动作参数必须放在 `in` 里，放 `value` 会被回「Action参数个数不匹配」；
- siid/aiid 不写进设备表：代码按 miot 动作名 `play-text` 查（l7a 是 siid5、x08c 是 siid3，
  写死反而换设备就废）。

只回「指令已下发」：云端没有播放回执，`code=0` 只代表接受。

### 注意

- **所有字符串都加引号**。YAML 1.1 里 `on` 是布尔值、纯数字会被读成整数，两种都会让属性名对不上。
- 名字里不能有 `/`（它拼 id 用）。
- 新增设备后要重启服务：`get_devices_list` 只在第一次调用时取一遍（每次约 0.8 秒，缓存在进程里）。
- 缓存策略（开关 3s、温湿度 300s、烟感 900s）是程序策略，在 `app/services/mijia.py`，不放这里。
- 单条设备读失败只影响自己那一行（页面显示「读不到」并禁用该行），不影响整页。
- 这个文件含真实 did（设备指纹，不是凭证），**不要随仓库外发**。
