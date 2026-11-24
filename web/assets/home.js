/* 首页：固定页写死在 HTML 里，功能页（web/pages/<slug>/）从 /api/pages 追加。

   按 web/DESIGN.md 的 P1：先把上次的清单从 localStorage 画上，再拉接口覆盖。
   卡片按 slug 复用，清单没变就不动 DOM；只有增删/改序才重建那部分。 */

const CACHE_KEY = 'home/pages/v1';
const CACHE_MAX_AGE = 12 * 3600 * 1000;

(() => {
  const grid = document.getElementById('hw-grid');
  if (!grid) return;

  /* ---- 缓存的清单 ---- */

  function readCache() {
    try {
      const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (!cached || !Array.isArray(cached.pages)) return null;
      if (Date.now() - cached.ts > CACHE_MAX_AGE) return null;
      return cached.pages;
    } catch {
      return null; // 缓存坏了就当没有
    }
  }

  function writeCache(pages) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ ts: Date.now(), pages }));
    } catch {
      /* 配额满了不影响使用 */
    }
  }

  /* ---- 渲染：按 slug 复用卡片 ---- */

  const icon = (name) => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('width', '24'); // 属性只是兜底，实际大小由 CSS 给
    svg.setAttribute('height', '24');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `/assets/icons.svg#${name}`);
    svg.appendChild(use);
    return svg;
  };

  /* 一张卡片 = <li><a class=card>…</a></li>：宫格是列表，链接是列表项的内容 */
  function cardOf(item) {
    const li = document.createElement('li');
    li.dataset.slug = item.slug;
    li.dataset.signature = `${item.slug}|${item.title}|${item.icon}`;
    const card = document.createElement('a');
    card.className = 'card';
    card.href = `/p/${item.slug}`;
    const title = document.createElement('span');
    title.className = 'card-title';
    title.textContent = item.title; // 标题是 agent 写的，用文本节点，别当 HTML 塞
    card.append(icon(item.icon), title);
    li.appendChild(card);
    return li;
  }

  function render(pages) {
    // 签名带 title/icon：标题或图标改了也要重画（agent 改 meta.json 是常事）
    const next = pages.map((p) => `${p.slug}|${p.title}|${p.icon}`);
    const current = [...grid.querySelectorAll('li[data-slug]')].map((el) => el.dataset.signature);

    // 一个字都不动
    if (next.join(',') === current.join(',')) return;

    // 重建动态部分（固定页是 HTML 里写死的，没有 data-slug，不会被碰到）
    for (const el of grid.querySelectorAll('li[data-slug]')) el.remove();
    for (const item of pages) grid.appendChild(cardOf(item));
  }

  /* ---- 拉数据 ---- */

  async function load() {
    try {
      const resp = await fetch('/api/pages');
      const body = await resp.json();
      if (body && body.ok) {
        render(body.data || []);
        writeCache(body.data || []);
      }
    } catch {
      /* 拉不到就继续显示缓存（或只有固定页） */
    }
  }

  const cached = readCache();
  if (cached) render(cached);
  load();
})();
