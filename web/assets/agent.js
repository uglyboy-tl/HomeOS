/* 助手页：三个模式各走一个 agent（chat / pages / device），全部通过后端 /api/agent/*。

   显示规则（按 opencode 实际状态收敛过）：
   - 发送后只有一句「正在工作…」：chat 没思考链，pages/device 会长时间思考或调工具，
     页面不跟着猜"在思考还是在干活"。
   - 正文有多少显示多少：优先 SSE（后端限流 4 次/秒），不支持就轮询；文本没变不碰 DOM。
   - 只有最近一轮：新的一轮直接替掉旧的。

   持久化（照 kiosk 的做法）：模式与每个模式最后那一轮存 localStorage，进来先画缓存，
   再向服务器对账——墨水屏上第一帧就有内容，且内容没变时不会二次重绘。 */
(() => {
  const tabs = document.querySelector('.agent-tabs');
  const log = document.querySelector('.agent-log');
  const form = document.querySelector('.agent-say');
  const say = form && form.querySelector('textarea');
  const send = form && form.querySelector('.send');
  const fresh = document.querySelector('.agent-new');
  if (!tabs || !log || !form || !say || !send) return;

  const HINT = {
    chat: '聊天模式：问什么答什么，不改页面。',
    design: '设计模式：改屏幕的样子，加一页、换个颜色。',
    device: '高级模式：动局域网里的别的设备，查服务状态。',
  };
  const WORKING = '正在工作…';
  const POLL_MS = 800;
  const POLL_MAX = 75; // 轮询兜底最多盯 1 分钟
  const MODE_KEY = 'agent/mode';
  const CACHE_KEY = (m) => 'agent/turn/' + m;

  let mode = 'chat';
  let busy = false;
  let epoch = 0; // 切模式/新对话 +1，让旧的轮询与 SSE 作废
  let liveClose = null; // 当前那条 SSE 的收尾函数：作废时必须主动关，否则连接一直挂着

  /* ---- 本地缓存：模式 + 每个模式最后那一轮 ---- */

  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function write(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* 存不下不影响使用 */
    }
  }

  function cache(turn) {
    write(CACHE_KEY(mode), JSON.stringify(turn));
  }

  function readTurn(m) {
    const raw = read(CACHE_KEY(m));
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  /* ---- 渲染：一轮 = 用户那句 + 正文 + 状态 ---- */

  const DOM = { user: null, reply: null, note: null };

  function paint() {
    log.textContent = '';
    DOM.user = node('p', 'agent-say-line');
    DOM.reply = node('p', 'agent-reply');
    DOM.note = node('p', 'agent-note');
    log.append(DOM.user, DOM.reply, DOM.note);
  }

  function node(tag, cls) {
    const el = document.createElement(tag);
    el.className = cls;
    return el;
  }

  function noteOf(turn) {
    if (turn.state === 'error') return turn.error || '助手出错了';
    if (turn.state === 'thinking') return WORKING;
    return '';
  }

  function setText(el, value) {
    if (el.textContent !== value) el.textContent = value;
    el.hidden = !value;
  }

  /* 内容没变就整块跳过：墨水屏上任何一次无意义重绘都是可见的一闪 */
  function show(turn) {
    if (!DOM.user) paint();
    const note = noteOf(turn);
    if (
      DOM.user.textContent === (turn.question || '') &&
      DOM.reply.textContent === (turn.text || '') &&
      DOM.note.textContent === note &&
      DOM.note.classList.contains(turn.state === 'error' ? 'agent-error' : 'agent-note')
    ) {
      return;
    }
    setText(DOM.user, turn.question || '');
    setText(DOM.reply, turn.text || '');
    DOM.note.className = turn.state === 'error' ? 'agent-error' : 'agent-note';
    setText(DOM.note, note);
    log.scrollTop = log.scrollHeight;
  }

  /* ---- 请求 ---- */

  async function api(path, body) {
    const init =
      body === undefined
        ? {}
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          };
    const resp = await fetch(path, init);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || 'HTTP ' + resp.status);
    return data;
  }

  function setBusy(on) {
    busy = on;
    send.disabled = on;
  }

  /* 跟着这一轮走到结束：优先 SSE，EventSource 不可用或断线就退回轮询。
     两条路都只调 show()，所以"文本没变不重绘"只在一处保证。
     注意：连接后第一帧可能是"这一轮还没开始"的 idle，不能当成结束。 */
  // done 但正文还没到（后端会短暂发这种帧）也不算结束，否则回复永远不显示
  const finished = (turn) =>
    turn.state === 'error' || (turn.state === 'done' && Boolean(turn.text));

  function invalidate() {
    epoch += 1;
    if (liveClose) liveClose(); // 旧的 SSE 不会自己结束：后端没变化就不推帧
  }

  async function follow(token) {
    if (!window.EventSource) return poll(token);
    await new Promise((resolve) => {
      const es = new EventSource('/api/agent/stream?mode=' + encodeURIComponent(mode));
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        es.close();
        if (liveClose === finish) liveClose = null;
        resolve();
      };
      liveClose = finish;
      es.onmessage = (ev) => {
        if (token !== epoch) return finish();
        let turn;
        try {
          turn = JSON.parse(ev.data);
        } catch {
          return; // 半截数据丢掉，下一帧会补
        }
        show(turn);
        write(CACHE_KEY(mode), ev.data); // 原样存，省一次序列化
        if (finished(turn)) finish();
      };
      es.onerror = () => {
        es.close();
        if (settled) return;
        settled = true;
        poll(token).then(resolve);
      };
    });
  }

  async function poll(token) {
    for (let i = 0; i < POLL_MAX; i += 1) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      if (token !== epoch) return;
      const turn = await api('/api/agent/turn?mode=' + encodeURIComponent(mode));
      if (token !== epoch) return;
      show(turn);
      cache(turn);
      if (finished(turn)) return;
    }
    // 拿不到推送不代表后端停了：别把"还在跑"报成失败
    if (token === epoch) fail('还在跑，稍后刷新看结果');
  }

  function fail(message) {
    if (!DOM.note) paint();
    DOM.note.className = 'agent-error';
    DOM.note.textContent = message;
    DOM.note.hidden = false;
  }

  function reset() {
    log.textContent = '';
    DOM.user = DOM.reply = DOM.note = null;
  }

  function hint() {
    reset();
    const p = node('p', 'agent-empty');
    p.textContent = HINT[mode] || '';
    log.appendChild(p);
  }

  /* 进页面 / 切模式：先画缓存，再对服务器；还在工作就继续跟 */
  async function load() {
    const token = ++epoch;
    const cached = readTurn(mode);
    if (cached && (cached.question || cached.text)) show(cached);
    else hint();
    // 让缓存帧先真的画出来，别和网络帧挤在同一瞬间（墨水屏上就是一次"卡"）
    await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
    if (cached) await new Promise((r) => setTimeout(r, 250));
    if (token !== epoch) return;
    try {
      const turn = await api('/api/agent/turn?mode=' + encodeURIComponent(mode));
      if (token !== epoch) return;
      if (!turn.question && turn.state === 'idle') return hint();
      show(turn);
      cache(turn);
      if (turn.state !== 'thinking') return;
      setBusy(true);
      await follow(token);
    } catch (err) {
      if (token === epoch) fail('读不到对话：' + err.message);
    } finally {
      if (token === epoch) setBusy(false);
    }
  }

  /* ---- 交互 ---- */

  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn || btn.dataset.mode === mode) return;
    mode = btn.dataset.mode;
    write(MODE_KEY, mode);
    for (const b of tabs.querySelectorAll('button[data-mode]')) {
      b.toggleAttribute('aria-current', b === btn);
    }
    invalidate(); // 旧轮询/旧 SSE 作废
    setBusy(false);
    reset();
    load();
  });

  if (fresh) {
    fresh.addEventListener('click', async () => {
      invalidate();
      setBusy(false);
      try {
        cache(await api('/api/agent/new', { mode }));
        hint();
      } catch (err) {
        hint();
        fail('开新对话失败：' + err.message);
      }
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = say.value.trim();
    if (!text || busy) return;
    setBusy(true);
    const token = ++epoch;
    say.value = '';
    paint();
    DOM.user.textContent = text;
    DOM.note.textContent = WORKING;
    try {
      show(await api('/api/agent/say', { mode, text }));
      await follow(token);
    } catch (err) {
      if (token === epoch) fail(err.message);
    } finally {
      if (token === epoch) {
        setBusy(false);
        say.focus();
      }
    }
  });

  const saved = read(MODE_KEY);
  mode = HINT[saved] ? saved : 'chat';
  for (const b of tabs.querySelectorAll('button[data-mode]')) {
    b.toggleAttribute('aria-current', b.dataset.mode === mode);
  }
  load();
})();
