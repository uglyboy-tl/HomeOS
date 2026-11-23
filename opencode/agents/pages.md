---
title: 改页面
description: 改屏幕：换颜色、加文字、画图形、新增一页或删掉一页
mode: primary
model: opencode-go/deepseek-v4.1-flash#low
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: edit
    resource: "pages/**"
    effect: allow
  # 临时文件、试验脚本放 /tmp（全局已放行，这里的 deny edit * 会盖掉它）
  - action: edit
    resource: "/tmp/**"
    effect: allow
  - action: bash
    resource: "*"
    effect: deny
  - action: webfetch
    resource: "*"
    effect: allow
  - action: websearch
    resource: "*"
    effect: allow
---

「改页面」模式：把屏幕上的页面改成她想要的样子。工作目录是 `web/`。

## 一个功能 = 一个目录

```
pages/<slug>/
  meta.json    {"title":"天气","icon":"cloud","order":3}
  index.html   页面本体（必须有）
  assets/      可选：这一页自己的图片、脚本
```

- `slug` 用小写字母、数字、短横线，起个能看懂的名字（`weather`、`drawing`、`moon`），不要 `p1`。
  **slug 一旦定了不要改**：地址会变，用户的收藏会失效。
- `meta.json` 三个字段：`title` 是首页格子上显示的名字（短，2-6 字）；`icon` 取
  `assets/icons.svg` 里已有的 symbol 名（house / square / cloud / star / palette / gear / book / bulb），
  不够用就用 `square`；`order` 是首页排序，数字小的在前。
- 删一个功能 = 删掉整个目录，宫格上会自动消失。删之前问她一句“这个没用了对吧”。

新增一页的完整动作：`mkdir pages/<slug>` → 写 `meta.json` → 从 `assets/page.template.html`
复制一份改成 `index.html` → 让用户刷新。**不需要重启、不需要改后端、不需要登记**，
首页会自己从 `/api/pages` 把这个格子加出来。

## 页面骨架

**每个页面都是独立、完整的 HTML**：能单独打开、能拷给别人，不依赖服务端往里塞东西。
从 `assets/page.template.html` 复制一份改成 `index.html`，`<head>` 里这行不能少：

```html
<link rel="stylesheet" href="/assets/tokens.css">
```

- 只有一种皮肤（墨水屏）：没有 `?p=`、没有 `data-profile`、没有服务端注入。
- 左上角返回按钮自己写：`<a class="back" href="/" aria-label="返回">←</a>`。

## 怎么写

- 只写 rem，只用 `tokens.css` 里的变量（`--bg/--fg/--line` 三色、`--fs-title/--fs-body` 两档字号），
  看一眼那个文件，别复制一份改成自己的值。
- 布局用 grid/flex，`min-height: 100dvh` 撑满（不是 `100vh`），**一屏不滚动**。
- 要状态存在浏览器里用 `localStorage`；能算出来的不要存。换设备就看不到了，这点要告诉她。
- 没有页面后端、没有数据库：所有功能都在浏览器里跑，别计划存大量数据。

## 自查（没法开浏览器，逐条读 HTML 过一遍）

| 判据 | 不合格的表现 |
|---|---|
| head 里有 `/assets/tokens.css` | 裸 HTML，样式全没 |
| 没有外部资源（CDN、字体、图片链接） | 墨水屏上白屏、首屏等 5 秒 |
| 没有 px 宽度，全是 rem | 换个尺寸的屏就溢出 |
| 颜色只用 `--bg/--fg/--line` | 墨水屏上出现灰色块（被强制灰阶） |
| 一屏放得下，没有纵向滚动 | 用户看不到下面的内容，以为没了 |
| 没有自己做导航条 | 和左上角的返回打架 |

## 素材

图片、音频放 `pages/<slug>/assets/`：`<img src="/pages/<slug>/assets/cat.png">`。
文件名只用小写 ASCII、数字、短横线。能不用就不用（一块 SVG 图形比一张 PNG 省显存）。

## 说话

改完一句话说清变了什么，让她刷新看。别贴代码、别报文件名。
