# 前端设计规范（`web/`）

写给未来写页面的人（和 agent）。这里只讲**为什么这样画**，不讲某个页面怎么实现；
具体写法看 `assets/page.template.html`，能用的数值看 `assets/tokens.css`。

## 0. 先认清这块屏

| 事实 | 后果 |
|---|---|
| 反射式、无背光、1-bit 或 16 级灰且灰阶不等距 | 只有黑与白可信，文字必须纯黑压纯白 |
| 一次绘制 = 一次可见的刷新，整屏刷新 200–800ms 且有闪 | 绘制次数与绘制面积都是预算，不是免费操作 |
| 脏区代价 = 变化的**外接矩形**，与变了几个像素无关 | 会变的东西要聚在一起，别散落在屏幕两端 |
| 面板波形决定刷新，页面无法请求局部刷新 | 只能靠"少改 DOM"去间接缩小脏区 |
| 读者常常在一米外，且包含一个 6 岁小孩 | 字大、目标大、信息少，宁少勿小 |
| 浏览器不会替你关动画（EinkBro 只关自己的转场） | 动画/过渡必须页面自己清零 |

## 1. 七条原则

**一、每一次绘制都是打扰。**
能不动就不动：文本没变不写 DOM，列表没变不重建，状态没变不重画。
判断标准不是"改得多不多"，而是"屏幕上有多少矩形区域变了"。

**二、先占位，再填字。**
布局与内容分两步：先用固定高度把位置留出来，再往里写值。
高度不跳 = 只重绘那一块；高度跳一次 = 整页跟着重排。
填充方向也固定：**白底填黑字**，不要黑底挖白字（后者是残影与全刷的主因）。

**三、没有颜色，状态只能走三条通道。**
文字（优先，一句话说清"失败，30 秒后重试"）→ 形状（边框、实心块、双线）→ 位置（排序、分组）。
反色只给**小块、高价值**的地方（选中的标签、确认按钮、状态徽章）；
整行或整屏反白换来的辨识度，不值得那张残影。

**四、不做动画，也不假装自己在动。**
没有过渡、没有骨架 shimmer、没有无限旋转的 loading。
"正在工作"用静态图形 + 三个字；超过一次刷新的等待，用文字说明还要多久或能做什么。

**五、一屏一页。**
不滚动、不分页器、不超过 6 个可点目标；翻页优于滚动。
塞不下的第一反应是**减内容**，不是缩字号，也不是加滚动条。

**六、写什么就是什么。**
没读到就说没读到，别显示成"关"；写成功要读回确认，没确认就不假装成功；
未知（还没拉过）与失败（拉过但错了）必须用不同表现。

**七、代码要能被下一个人改。**
零构建、无 CDN、页面是一份能单独拷走打开的 HTML；
一页一个 HTML + 可选同名 CSS/JS；不放共享运行时（除非真有响应式列表，见 §4）。

## 2. 排版与尺寸

只用 `tokens.css` 的变量，**不写裸数值**。四档字号够用：

| 用途 | 变量 | 备注 |
|---|---|---|
| 页面标题 | `--fs-title` | 一页一个 |
| 返回、标签、行主文本、强调 | `--fs-lg` | 与顶层标签同号 |
| 正文、状态、提示 | `--fs-body` | 不小于 1.8rem，这是下限不是上限 |
| 次要标签 | `--fs-sm` | 只给标签，不给要读的句子 |

- 线宽三档：`--bw-thin` 分隔、`--bw` 边框、`--bw-heavy` 聚焦；**不用灰线**，实线比灰线稳。
- 可点目标：`--tap`（卡片）≈ 2cm、`--tap-row`（列表行）、`--tap-sm`（图标按钮）≥ 4.6rem —— 6 岁的手指按 2cm 设计。
- 会变的数字加 `font-variant-numeric: tabular-nums`：9 → 10 宽度不变，邻居不重排。
- 行宽不要超过约 30 个汉字（`ch` 级别限制）；一行太长在无滚动的屏上很难找到下一行。

## 3. 图形与文案

判断顺序：**一个形状 + 一次点击能说清的事，就别写字。**

- 图标用 `/assets/icons.svg` 的 symbol（`<use>`）；内联路径等于每页复制一份 `d`。
- 描边、直角端点，不填色、不圆角：低分辨率下圆角与细线糊成一团。
- 装饰性图形 `aria-hidden="true"`，不承载语义。
- 能写的字只有四类：对话正文与输入、数字与单位、专名、一行以内的错因。
- 禁用态与缺失态优先"干脆不显示"；必须在就写清原因，不要用透明度表达不可用（屏上没有"淡"这回事）。

## 4. 页面结构与代码

```
web/
  home.html  mijia.html  agent.html     固定页（人写，路由在 app/routes/pages.py 的 FIXED）
  pages/<slug>/index.html + meta.json   功能页（agent 写，一目录一页，起手照 page.template.html）
  assets/tokens.css                     唯一皮肤；其余 <页>.css / <页>.js 与同名 html 对齐
  assets/icons.svg  page.template.html  assets/vendor/（第三方本地副本，只有 Alpine）
```

- 语义优先：跳转用 `<a href>`，动作用 `<button type="button">`，选中用 `aria-current="true"`（容器 `role="group"` + `aria-label`），真禁用用原生 `disabled`。写一半的 `role` 比不写更糟。
- 监听挂容器、用 `e.target.closest()` 分发：页面会重绘，逐个绑必然漏。
- 视口：`min-height: 100dvh`、底部留 `max(var(--gap), env(safe-area-inset-bottom, 0px))`、不写 `user-scalable=no`。
- 静态资源改动时给 HTML 里的引用加 `?v=N`（EinkBro 对子资源缓存极顽固）。

**什么时候可以用 Alpine**：只有当页面需要"响应式列表 + 局部状态"时才引（固定页、本地副本）。纯追加内容用原生 DOM。

引 Alpine 时三条硬约束：

1. 脚本顺序不能变——`persist` → 本页 JS → `alpine`；`alpine` 一执行就 `start()`，之后再注册 `alpine:init` 不生效。
2. `$persist` 必须 `.using()` 一个带 try/catch 与大小上限的 storage：插件内部对 `JSON.parse` 与 `setItem` 都没有保护，缓存写坏或超配额会直接中断组件初始化。
3. `init()` 里建的定时器与监听，`destroy()` 里必须全部收回；`[x-cloak]` 的 CSS 已在 `tokens.css`，直接用即可。

## 5. 数据与刷新

**三拍**：进页面先用缓存把上次的内容画出来 → 再向服务器对账（变了才重绘）→ 还在工作的继续跟随到结束。
缓存的是**屏幕上那一屏**（已格式化好的视图），不是接口原文；对账只改变化的字段。

| 场景 | 规矩 |
|---|---|
| 轮询 | ≥ 60s，`document.hidden` 时不跑；回来时先补一次 |
| 推送 | 服务端限流 ≤ 4 帧/秒——再快面板只会永远停在过渡中 |
| 写操作 | `PUT/POST` 后**读回确认**再改界面；返不回预期值就如实说失败 |
| 未拉过 vs 拉失败 | 前者显示"…"（可点、点了重拉），后者显示"读不到"（不可点） |
| 整页失败 | 旧值照旧显示 + 一行离线原因 + 一个能点的重试入口；不要静默禁用一整屏 |
| 恢复 | 失败后 10 秒自动重试一次，别等下一轮 120 秒 |

## 6. 验收

1. 有缓存时，导航后 50–200ms 内页面内容就完整可见（不是空白等网络）。
2. 数据没变时 `MutationObserver` 记 0 次变更；改一个状态记 1–2 次。
3. 断网 / 后端 500：旧值仍在、有原因、有重试入口，且这状态进了缓存。
4. 一屏不滚动；可点目标 ≥ `--tap` / `--tap-sm`；没有为塞内容而调小字号。
5. 页面上没有任何动画、过渡、hover 效果；屏幕上的数字宽度不随值变化跳动。

## 7. 参考：该按哪套规范做

有分歧时按这个顺序定：**物理事实（§0）> 可访问性（WCAG/ARIA）> 墨水屏专项指南 > 通用 Web 规范 > 个人品味**。
找不到直接答案时按 §1 的七条原则推，推完把结论回写进本文档（一句 + 出处）。

### 通用 Web（Google 系为主）

- **[web.dev](https://web.dev/)** — Google 官方的 Web 质量指南（性能、可访问性、HTML/CSS 实践）。拿它定“什么时候该动 DOM”“静态资源怎么不拖慢首屏”，**不拿它的 Lighthouse 分数当目标**（我们只有一种屏、一种网络）。
- **[GoogleChrome/modern-web-guidance](https://github.com/GoogleChrome/modern-web-guidance)** — Google 出的前端最佳实践仓库，已装成本仓的 `.agents/skills/modern-web-guidance`。**写任何页面前先查一遍**：弹框/菜单/表单校验/表格/滚动/无障碍都有原生做法，手写 JS/CSS 往往更差。
  - 例外：它的动效与“流畅过渡”条目在墨水屏上不适用，本文 §1 第四条优先。
- **[MDN Web Docs](https://developer.mozilla.org/zh-CN/docs/Web)** — 行为与 API 的唯一真相：Page Visibility（后台节流）、Storage 配额（5MiB 与 `QuotaExceededError`）、`EventSource`/`WebSocket`。
- **[WCAG 2.2 快速索引](https://www.w3.org/WAI/WCAG22/quickref/)** 与 **[ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/)** — 语义角色、`aria-current` / `aria-pressed` 的正确用法、对比度、触摸目标（§2.5.8 是 24px 下限，44px 是 AAA，别把 44 当标准）。近年新增的 `:focus-visible`、`prefers-reduced-motion`、`dialog`/`popover` 都在这里。
- **Material 3 / Apple HIG** — 只借**间距与层级**两件事；动画、色彩体系、动态过渡一律不借（理由见下方的 e-paper 设计系统研究：它们“恰好放大 e-paper 的短板”）。

### 排版

- **[Practical Typography](https://practicaltypography.com/)**（免费在线书）— 行宽、行高、字距、强调手段的判断标准。对应到我们：行宽上限、正文行高 1.35–1.5、强调用字重而不是斜体。
- **[Refactoring UI](https://www.refactoringui.com/)**（付费）— 视觉层级靠尺寸/字重/留白而不是颜色。单色屏上这条恰好是唯一可用的手段。

### 墨水屏专项

- **[Designing for e-ink](https://epaper-components.dev/guides/designing-for-eink/)** — 对比度是物理预算（反射屏 12:1–16:1 且随环境光衰减）、16–18px 是正文下限不是上限、48px 触摸下限、分页优于滚动、状态只能走文字/形状/位置。
- **[Partial refresh](https://epaper-components.dev/guides/partial-refresh/)** — 脏区代价 = 变化的**外接矩形**；更新快于约 4Hz 只会永远停在半过渡；每 10–50 次局部刷新该补一次全刷；`innerHTML` 重建是病态情况（脏区按整块算）。本文 §1 前两条与 §5 的 4 帧/秒上限都出自这里。
- **CHI 2026《A Dedicated E-Paper Design System for Mobile Phones》**（[论文](https://dl.acm.org/doi/full/10.1145/3772318.3791459)，成果即 [Mudita Mindful Design](https://mudita.com/developers/)）— 目前唯一成体系的移动 e-paper 设计系统：等高列表项、分隔线位置固定以限残影、禁用态能省就省、确认反馈用反色。
- **[EinkBro 源码](https://github.com/plateaukao/einkbro)** — 看浏览器层面做了什么：只关 Activity 转场、只做图片抖色优化，**没有**页面级动画抑制，也没有 `prefers-reduced-motion` 注入。结论：别指望浏览器替你关动画。
- **E Ink 官方没有公开的 UI 设计指南**，Onyx/Kobo/Kindle 也没有。别再去找，这四份就是全部可信材料。

### 触摸与儿童

- **[NN/g: Children's UX: Physical Development](https://www.nngroup.com/articles/children-ux-physical-development/)** — 4–6 岁建议 **2cm×2cm** 触摸目标（成人 1cm 的 4 倍）。`--tap` 按这个定。
- 想再放宽时看 WCAG 2.5.5（44×44 AA/AAA 之争）与 Fitts 定律，不要凭手感。

### 我们用的技术

- **[Alpine.js 官方文档](https://alpinejs.dev/)** — `x-for` 必须单根 `<template>` + 稳定 `:key`；`x-show` 只切 `display`、`x-if` 真删 DOM（会跑 `destroy()`）；`[x-cloak]` 的 CSS 要自己写（已在 `tokens.css`）。
- **persist 插件[源码](https://github.com/alpinejs/alpine/blob/main/packages/persist/src/index.js)** — 恢复发生在 data 被接管时（工厂里读到的是默认值）；`storageGet`/`storageSet` **没有** try/catch，所以必须 `.using()` 自己的 storage。
