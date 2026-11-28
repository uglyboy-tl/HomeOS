/* 首页：固定页写死在 HTML 里，功能页从 /api/info/pages 追加。先画缓存清单再对账，
   卡片按 slug 复用，不需要动就不碰 DOM。 */

const CACHE_KEY = 'home/pages/v1';

/* 状态带只报页面上真会用到的能力：没配通知/监控/语音是常态，报了只是噪音 */
const WATCHED = { 'mijia.table': '设备表', 'mijia.cloud': '米家', agent: '助手', mpd: '音乐' };

(() => {
  const grid = document.getElementById('hw-grid');
  if (!grid) return;

  /* ---- 缓存的清单 ---- */

  function readCache() {
    try {
      const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      return cached && Array.isArray(cached.pages) ? cached.pages : null;
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
      const resp = await fetch('/api/info/pages');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const list = await resp.json();
      render(list);
      writeCache(list);
    } catch {
      /* 拉不到就继续显示缓存（或只有固定页） */
    }
  }

  /* ---- 能力异常带（/api/info）：好用的时候 DOM 里什么都不放 ---- */

  const alert = document.getElementById('hw-alert');

  /* 后端消息动辄一句长话，只取第一个分句；短名已经出现在句子里就不再重复加前缀 */
  function brief(name, message) {
    const clause = String(message || '')
      .split(/[。；;：:，,]/)[0]
      .trim()
      .slice(0, 24);
    if (!clause) return name + '不可用';
    return clause.includes(name) ? clause : name + '：' + clause;
  }

  function trouble(info) {
    const caps = info.capabilities || {};
    const parts = [];
    for (const [key, name] of Object.entries(WATCHED)) {
      const cap = caps[key];
      if (cap && cap.ok === false) parts.push(brief(name, cap.message));
    }
    const problems = Array.isArray(info.problems) ? info.problems.length : 0;
    if (problems) parts.push('配置有 ' + problems + ' 处问题');
    return parts.join(' · ');
  }

  async function loadStatus() {
    if (!alert) return;
    let info;
    try {
      const resp = await fetch('/api/info');
      if (!resp.ok) return; // 拉不到不动它：别把网络问题说成能力问题
      info = await resp.json();
    } catch {
      return;
    }
    const text = trouble(info);
    // 文本没变就不碰 DOM：墨水屏上任何一次无意义写入都是一次可见的闪
    if (alert.textContent === text && alert.hidden === !text) return;
    alert.textContent = text;
    alert.hidden = !text;
  }

  const cached = readCache();
  if (cached) render(cached);
  load();
  loadStatus();
  // 墨水屏常年挂在墙上：从屏保/后台回来时再对一次，不设定时器
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) loadStatus();
  });
})();
