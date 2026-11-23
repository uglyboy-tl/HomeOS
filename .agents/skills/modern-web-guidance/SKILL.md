---
name: modern-web-guidance
description: 写或改页面之前先查一遍（浏览器原生做法比自己手写 JS/CSS 稳）。这些场景有原生 API：弹窗/确认框/抽屉/菜单浮层/popover、表单与校验、表格、勾选与开关、日期时间、滚动与动画、长列表卡顿、无障碍与焦点。不用于后端（FastAPI/sqlite）、脚本、构建工具。
---

# 现代 Web 写法

来源：<https://github.com/GoogleChrome/modern-web-guidance>（Chrome/Edge/Firefox/MDN 共同维护）。

CLI 走 bunx，不要用 npx（这机器上没有 node）。**每条命令都带 `timeout 20` 前缀**：这是 shell 白名单的形式，也避免断网时把会话卡死。

## 1. 先搜（英文 2-5 个词）

一次只查一个能力，不要复合查询；去掉框架名和项目名词。

```bash
timeout 20 bunx modern-web-guidance@latest search "light dismiss dialog"
```

返回 `[{id, description}]`。看 `description` 判断：

- 有匹配 → 直接 `retrieve`，不要再 `list`
- 空或不相干 → 换个具体名词（dialog / modal / popover / table / form）再搜一次
- 还是没覆盖 → 就自己写，别硬套不相干的指南

## 2. 再取全文

```bash
timeout 20 bunx modern-web-guidance@latest retrieve "dialog"
```

超过 9.5KB 会落到 `/tmp/mwg-guide-*.md`，用 read 工具读那个文件（不要 cat/head）。
同时匹配到一个组件的指南和它所属的领域指南（`html` `css` `forms` `accessibility` `color` `performance` `security`）时，两个都取。

**不要给 bunx 接管道**（`| tail`、`| head`）：白名单按整条命令匹配，管道会变成另一条被拒的命令。输出自己就截断了。

## 3. 对照检查

改完检查一遍：指南里标了 MANDATORY 的 CSS/属性是否都在；标 DON'T 的手写替代（自己写 focus trap、`overflow:hidden` 锁滚动这类）是否混进来了。不要为了用上指南去加没要的功能。

## 本项目的前提（覆盖指南的默认假设）

- **谁在用这个技能**：开发这个仓库的人或本机的 coding agent，不是容器里的 pages agent
  （镜像里没有 bun，它也没有 shell 权限）。写 `web/assets/*`、`web/*.html`、
  `web/assets/page.template.html` 时用。
- **目标设备只有墨水屏**：默案 inkPalm 5（720×1440，EinkBro）+ Kindle 第三方浏览器，
  区别只是屏大小。没有彩色屏、不需要两套皮肤。
- **允许 Newly Available**，不 polyfill、不写多浏览器降级分支（除非 MANDATORY）。
- 无构建、无框架、本地资源：SSR / bundler / `@types/react` / 包体积那几节直接跳过。

### 本项目的硬约束（与指南冲突时以这里为准）

- **只用 rem 与 `tokens.css` 的变量**，不写 px 宽度。指南说“单设备用 px 就行”不适用：
  我们同时要跑 600×800 和 720×1440。
- **事件一律用委派**（挂在容器上），不在渲染时逐个 `addEventListener`——页面会重绘。
- **零动画、零过渡、零阴影、零圆角**，`tokens.css` 里那组 `!important` 不要为某页放开。
- **轮询 ≥ 60s 且只在可见时**；不用 SSE 逐字、不做时钟、不做 hover。
- 命中区：按钮/标签 ≥ 5.8rem 高，纯图标按钮 ≥ 4.6rem 见方（约 92px）。

### 查到了也直接跳过的

- `text-wrap: balance` / `pretty`：中文不吃西文断行优化。
- `prefers-reduced-motion` / `prefers-contrast` / `forced-colors` 分支：设备没有偏好入口，
  而且我们本来就零动画。`color-scheme: light` 已经声明，不需要深色适配。
- 为屏幕阅读器做的复杂处理（焦点陷阱、live region 细节）：客户端没有辅助技术接入。
  但**成本为零的语义照旧**：`<button type>`、`aria-label`、`role="tab"` + `aria-selected`、
  `aria-hidden` 装饰图标。
- 字体加载策略（`font-display`、子集化）：不引外部字体，用系统字体。

### 我们采纳的几条（已落在代码里，新页面照抄）

- `min-height: 100dvh` 而不是 `100vh`：地址栏收起时 `vh` 会多出一截滚动。
- `padding-bottom: max(var(--gap), env(safe-area-inset-bottom, 0px))`：避开手势条。
- `body { overflow-wrap: break-word }`、`img, video { max-inline-size: 100% }`：320px 宽也不溢出。
- `:focus-visible` 描边：鼠标/键盘环境下代替 hover 反馈。
- 表单：`type="submit"` + `preventDefault()`，`enterkeyhint="send"`。

## 存量现代化（可选）

整体扫旧写法时才跑，单点修改不要用（只覆盖约 20% 的主题）：

```bash
timeout 20 bunx modern-web-guidance@latest legacy-scan pages --json
```

结果按每条 finding 的 `keepWhen`/`dropWhen` 排掉误报（fallback、vendor 代码）。

## 离线

首次运行要联网拉包（~17MB，进 `~/.cache/.bun/install/cache`）。断网时加 `--offline`：

```bash
timeout 20 bunx --offline modern-web-guidance@latest search "dialog"
```
