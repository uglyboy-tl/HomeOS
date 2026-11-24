# 前端设计规范（`web/`）

只写**这个项目里相对共通、且不显然**的约定。样式变量看 `assets/tokens.css`，
设备与硬约束看根目录 `AGENTS.md`。新页面照这份做，不对劲的地方先改这里再改代码。

目标只有一个：**墨水屏上少刷几次**。整屏刷新约 300-500ms 且会闪黑，
所以下面每一条都在回答同一个问题——"这次能不能不画"。

---

## 1. 三条基础原则

### P1 首帧用浏览器缓存，第二帧用服务端真值

打开页面时先按上次的结果画（localStorage，同步、0 网络），再拉新数据覆盖。
**两边通常一致，所以大多数时候用户只看到一次刷新。**

两个现成例子：`assets/mijia.js`（有响应式状态，用 Alpine + `$persist`）、
`assets/home.js`（只是追加几张卡片，用原生 DOM + 手写 localStorage）。
**缓存原则一样，是否引入 Alpine 看页面有没有响应式状态**：没有就别引。

落地的写法（米家）：

```js
// 注册在 alpine:init 里，$persist 必须在 data 工厂求值，否则拦截器不生效
Alpine.data('mijiaPanel', () => ({
  cache: Alpine.$persist({ ts: 0, devices: null, values: null }).as('mijia/v1'),
  ...
```

```js
// x-init 里（不是工厂里！）读缓存，一次赋值成型——先建空对象再填就是两次重绘
if (!this.rooms.length && this.cache.devices && Date.now() - this.cache.ts < CACHE_MAX_AGE) {
  this.devicesPayload = this.cache.devices;
  this.values = this.cache.values;
  this.rooms = buildRooms(this.cache.devices, this.cache.values);
}
```

规则：

- 缓存键带版本（`mijia/v1`、`home/pages/v1`），数据结构一变就改版本号，旧缓存自动作废。
- 缓存有 TTL（都用 12 小时），过期当没有；读不到/坏了当没有。
- 只写属性、不整体替换被 `$persist` 包住的对象，否则 proxy 被换掉、之后不再落盘。
- **浏览器状态（停在哪个房间、展开/收起之类）单独用小 key**（`mijia/room`），
  不塞进带 TTL 的数据缓存：数据过期了，偏好还要在。
- 服务端也有缓存（米家：开关 3s、温湿度 300s、烟感 900s），前端缓存要跟它对齐思路，
  不要比服务端"更旧"。
- **请求粒度 = 页面上看得到的那一块**：米家只拉当前房间（`/api/mijia/state?room=客厅`），
  切房间才拉那一间；同一间房里的设备由后端并行读（一次往返）。后端扛得住并发，
  但客户端不要为了"提前热身"去拉看不见的房间。

### P2 只改变化的字段

数据回来时**不要重建列表、不要替换数组/对象**，就地改字段；字段没变就不赋值，
Alpine 连那个文本节点都不会碰。

```js
if (sw.on !== next) sw.on = next;        // 变了才写
if (sensor.text !== view.text) sensor.text = view.text;
```

配套的三条纪律：

- `x-for` 的 `:key` 必须用稳定 id（设备 id、房间名），换 key 等于整块重建。
- 结构真的变了（设备增删）才重建 `rooms`，重建时用已有 values 播种，避免出现一排默认值。
- **列表项与行内字段**不用 `x-show` 切换 `display`（会重排）。让元素一直在、文本为空就不占宽度：
  `<span class="sub" x-text="sensor.sub || ''">`。

改完可以量一下：`MutationObserver` 观察容器，**数据没变时刷新应该是 0 次变更**，
改一个开关应该是 2 次（class + 文字）。

### P3 失败即降级，不假装正常

拉数据失败时：**继续显示缓存值 + 停用写操作 + 明说原因**。
米家的做法是三条一起：顶部黑条「离线：显示上次状态，操作已停用」、
按钮上挂原生 `disabled`（`x-bind:disabled`）、点击处理里再兜一层。

不做的：只弹一个一闪而过的提示然后继续能点（会拿旧数据乱写设备）。

---

## 2. Alpine 的用法（只用这些）

| 用什么 | 说明 |
|---|---|
| `x-data` / `x-init` | 一个组件，结构在 HTML，逻辑在 `assets/<页>.js` |
| `x-for` + `:key` | 列表一律走它，`:key` 必须稳定 |
| `x-text` / `:class` / `:hidden` / `:aria-*` / `@click` | 够用，不需要更多 |
| `$persist` | 浏览器缓存，见 P1；**小偏好另开一个 `.as()` 键** |

- **不引其它 Alpine 插件**，不用 `x-transition`（零动画是硬约束）；`x-show` 只用于
  “整个提示条出现 / 消失” 这种本来就要改变布局的地方（见 P2）。
- **vendor 是本地副本**，不走 CDN：`assets/vendor/alpine.min.js`（3.14.9，gzip 16KB）、
  `assets/vendor/alpine-persist.min.js`（同版本）。升级 = 替换同名文件 + 改这份文档的版本号。
- **脚本顺序不能动**：

```html
<script defer src="/assets/vendor/alpine-persist.min.js"></script>
<script defer src="/assets/<页>.js"></script>            <!-- 注册 alpine:init 监听 -->
<script defer src="/assets/vendor/alpine.min.js"></script>
```

`alpine.js` 一执行就立刻 `start()`（defer 脚本跑在 `readyState=interactive`），
排在它后面的注册都会晚一步，组件名解析不到（控制台会报 `xxx is not defined`）。

---

## 3. 文件与页面结构

```
web/
  home.html  mijia.html  agent.html      固定页（人能改，路由写死在 app/routes/pages.py 的 FIXED）
  pages/<slug>/index.html + meta.json    功能页（agent 写，一个目录一页）
  assets/
    tokens.css                           唯一皮肤：变量、排版、宫格、返回按钮
    <页>.css  <页>.js                     页面自己的样式与逻辑，与同名 html 对齐
    vendor/                              第三方本地副本（alpine）
    icons.svg  page.template.html        图标 sprite、功能页起手模板
```

- 页面 = 一份完整 HTML + 可选同名 CSS/JS，**能单独打开**；服务端不往页面里塞内容。
- 页面之间不互相链，左上角「返回」自己写（`<a class="back" href="/">←</a>`）。
- 样式只用 `tokens.css` 的变量和 rem；`px` 宽度会在另一种尺寸的屏上溢出。
- 首页只有固定页的卡片是手写的，功能页那张卡片由 `home.js` 追加（带 `data-slug`），
  固定页卡片不参与 diff。

---

## 4. 性能预算

| 项 | 预算 |
|---|---|
| 单页自己的 JS（gzip） | ≤ 10KB（米家约 5KB） |
| vendor（gzip） | 16.9KB，只有米家页引，浏览器会缓存 |
| 轮询 | ≥ 60 秒，且只在页面可见时（`document.hidden`） |
| 一屏 | 不滚动，行高固定 |

代价参考：Alpine 初始化在桌面 12-19ms、折算到 inkPalm 约 70-190ms，45 个节点的 `x-for`
渲染 1-4ms（设备上 10-40ms）。这部分藏在整屏刷新里，感知不到；**真正要盯的是重绘次数**。

---

## 5. 新页面检查清单

- [ ] `<head>` 里三件事：viewport、`color-scheme: light`、`tokens.css`
- [ ] 需要数据时：缓存键带版本、TTL、首帧一次赋值；有响应式状态才引 Alpine
- [ ] 列表 `:key` 用稳定 id；数据更新只改字段；列表项与行内字段不用 `x-show`
- [ ] 用户偏好（当前房间等）单独存一个 key，不进数据缓存
- [ ] 失败时：显示旧值 + 停用操作 + 一行原因
- [ ] 轮询 ≥ 60s 且只在可见时
- [ ] 自己写返回按钮，不链其它页面
- [ ] 用浏览器量一次：数据没变时刷新应产生 0 次 DOM 变更

---

## 6. 踩过的坑

- **`$persist` 的时机**：工厂里读到的是默认值（拦截器还没应用），必须在 `x-init` 里读；
  整体替换被持久化的对象会让 proxy 失效，只改属性。
- **`x-text` 遇 `null`**：会渲染成字符串 `"null"`，可空字段写成 `x-text="v || ''"`。
- **`x-show`**：在列表项/行内字段上用会重排，用「空文本不占位」代替；
  提示条这类整块出现的东西可以用。
- **`:key` 写成数组下标**：设备增删时整块重建，墨水屏上就是一次可见的整区重画。
- **静态资源缓存**：容器里 `max-age=300`，改完前端要么等 5 分钟，要么强刷。
- **首屏探针**：验缓存是否生效，别用"看快不快"，直接量——导航后 50-200ms 读 `document.querySelectorAll`，
  应该是完整内容；再看 `MutationObserver` 计数，数据没变应为 0。

## 7. 语义：能用原生就用原生

按 `modern-web-guidance` 的 html 指南修过的几条，新页面照抄：

- **切视图用 `<button>` + `aria-current`，别用半套 `role="tab"`**。完整的 tab 模式要求
  `aria-controls`、roving `tabindex`、方向键，我们都没有；写一半比不写更糟。
  容器用 `role="group"` + `aria-label`，当前项 `aria-current="true"`（其余不写这个属性）。
- **可点元素角色要对**：同页内动作用 `<button>`，跳转用 `<a href>`。
- **列表用 `<ul>` / `<li>`**：首页宫格就是「一组页面的链接」，所以是 `ul` + `li` + `a`。
- **禁用用原生 `disabled`**，不要 `aria-disabled` + `pointer-events` 组合；外观我们自己用 CSS 保住
  （墨水屏上是黑白，别让 UA 灰掉）。
- **装饰性 SVG** 一律 `aria-hidden="true"`，图标按钮自己给 `aria-label`；`<use>` 引的 sprite
  必须同源，`viewBox` 放在 `<symbol>` 上，外层 `<svg>` 给尺寸（CSS 也能给，属性只是兜底）。
- 可点元素统一 `touch-action: manipulation`，去掉双击缩放的 300ms 等待。
- 不写 `maximum-scale` / `user-scalable=no`，不关页面缩放。

## 8. 助手页（/agent）与 /api/agent/*

页面只做三件事：画一轮、发一句、跟着看到结束；消息顺序、错误措辞、会话生命周期都在后端。

- **显示只有四种态**：提示语 / 「正在工作…」/ 正文 / 错误。chat 没有思考链，pages/device 会长时间
  思考或调工具，页面不区分"思考"和"干活"。
- **只留最近一轮**：新一轮直接替换旧的（不用清屏再画）。
- **持久化**：`agent/mode` 记模式、`agent/turn/<mode>` 记每个模式最后一轮；进来先画缓存，
  再对服务器，内容一致时**零重绘**。
- **逐字**：SSE `/api/agent/stream`（服务端限流 4 次/秒）+ 轮询兜底；`done` 但没正文的帧不算结束。
- **软键盘**：字号用的视口高度在加载时钉成 `--vh-px`（`agent.html` 里的内联脚本，公式仍只在 `tokens.css`），
  键盘弹出只压高度不改字号；`.agent` 锁 `100dvh` + `overflow:hidden`，长对话由 `.agent-log` 自己滚。
- **测试台接口**（验证 opencode 配置用）：

  ```
  GET  /api/agent/info?mode=     绑定的 agent 与模型
  GET  /api/agent/trace?mode=    消息轨迹（时间正序，带 model/tokens/cost/finish/error_raw）
  POST /api/agent/ask            {mode,text,timeout} 同步一轮，返回耗时/token/模型
  POST /api/agent/bind           {mode,agent} 临时换 agent（重建会话）
  POST /api/agent/model          {mode,providerID,id,variant} 换模型
  ```
