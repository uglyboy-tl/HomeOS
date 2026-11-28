/* 助手页：模式由后端 config/agents.yaml 决定（/api/info/agent 取），页面不写死。
   只显示最近一轮；模式与各模式最后一轮存 localStorage，进来先画缓存再对账。 */
(() => {
  const tabs = document.querySelector('.agent-tabs');
  const log = document.querySelector('.agent-log');
  const form = document.querySelector('.agent-say');
  const say = form && form.querySelector('textarea');
  const fresh = document.querySelector('.agent-new');
  const mic = document.querySelector('.agent-mic');
  const send = document.querySelector('.agent-send');
  // 图标固定在 HTML 里，切换只看发送键上的 is-stop（CSS 做），这里不碰
  if (!tabs || !log || !form || !say || !fresh || !mic || !send) return;

  let MODES = []; // [{id,label,hint}]，来自 /api/info/agent
  const WORKING = '正在工作…';
  const POLL_MS = 800;
  const POLL_MAX = 75; // 轮询兜底最多盯 1 分钟
  const MODE_KEY = 'agent/mode';
  const MODES_KEY = 'agent/modes'; // 标签列表缓存：进页面就能画出来，不等 /api/info/agent
  const CACHE_KEY = (m) => 'agent/turn/' + m;
  // 后端错误码 → 「接下来怎么办」：这些码的原因从提示里看不出来
  const ERROR_HINT = {
    cooldown: '刚才连续崩了几次，约一分钟后再试',
    pi_timeout: '这一轮太久了，换短一点的内容再发',
    pi_unavailable: '助手引擎没起来，检查 config/pi-*.json 与 agents.yaml',
    pi_crashed: '助手进程挂了，已自动重启，再发一次',
  };

  let mode = '';
  let busy = false;
  let epoch = 0; // 切模式/新对话 +1，让旧的轮询与 SSE 作废
  let voiceTurn = false; // 这一句是语音说的
  let pendingSpeak = false; // 这一轮结束后要念出来
  let spokenFor = -1; // 已经念过的 epoch：同一轮只念一次
  let liveClose = null; // 当前那条 SSE 的收尾函数：作废时必须主动关，否则连接一直挂着

  /* 本地缓存：模式 + 每个模式最后那一轮 */

  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null; // 隐私模式禁了存储就什么都不画
    }
  }

  function write(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* 存不下不影响使用 */
    }
  }

  function parse(raw) {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function readModes() {
    const list = parse(read(MODES_KEY));
    return Array.isArray(list) && list.length ? list : null;
  }

  const readTurn = (m) => (m ? parse(read(CACHE_KEY(m))) : null);

  // SSE 那帧本来就是 JSON 字符串，原样存省一次序列化
  const cache = (turn) =>
    write(CACHE_KEY(mode), typeof turn === 'string' ? turn : JSON.stringify(turn));

  /* 渲染：一轮 = 用户那句 + 正文 + 状态 */

  const DOM = { user: null, reply: null, note: null };

  function paint() {
    log.textContent = '';
    DOM.user = node('p', 'agent-say-line');
    DOM.reply = node('p', 'agent-reply');
    DOM.note = node('p', 'agent-note');
    // 「正在工作…」这种过渡状态不该被读屏播报（对局外人只是噪音）
    DOM.note.setAttribute('aria-live', 'off');
    log.append(DOM.user, DOM.reply, DOM.note);
  }

  function node(tag, cls) {
    const el = document.createElement(tag);
    el.className = cls;
    return el;
  }

  function noteOf(turn) {
    if (turn.state === 'error') {
      const message =
        typeof turn.error === 'string' ? turn.error : turn.error && turn.error.message;
      return message || '助手出错了';
    }
    // 已经有正文就不再挂「正在工作」：否则字都滚出来了，那行还赖在下面
    if (turn.state === 'thinking' && !turn.text) return WORKING;
    return '';
  }

  function setText(el, value) {
    if (el.textContent !== value) el.textContent = value;
    el.hidden = !value;
  }

  /* 说进来的念出去：只有语音发起的这一轮才调播报，失败静默（音箱没配/离线不该在页面上报错） */
  function speak(turn) {
    if (!pendingSpeak || spokenFor === epoch) return;
    const text = (turn.text || '').trim();
    if (!text) return;
    pendingSpeak = false;
    spokenFor = epoch;
    fetch('/api/speech', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).catch(() => {});
  }

  /* 内容没变就整块跳过：墨水屏上任何一次无意义重绘都是可见的一闪 */
  function show(turn) {
    if (turn.state === 'done') speak(turn);
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

  /* 请求 */

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
    if (!resp.ok) {
      const failure = data.error || {};
      const error = new Error(failure.message || data.detail || 'HTTP ' + resp.status);
      error.code = failure.code || '';
      throw error;
    }
    return data;
  }

  /* 忙的时候发送键变成停止键：同一个位置两个作用，不额外占地方 */
  function setBusy(on) {
    busy = on;
    say.disabled = on;
    mic.disabled = on;
    fresh.disabled = on;
    send.setAttribute('aria-label', on ? '停止' : '发送');
    send.classList.toggle('is-stop', on); // 两个图标由 CSS 按这个类切换
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
        cache(ev.data); // 原样存，省一次序列化
        if (finished(turn)) finish();
      };
      es.onerror = () => {
        es.close();
        if (settled) return;
        settled = true;
        // 轮询也会失败；失败也要 resolve，不然这一轮永远结束不了
        poll(token).then(resolve, (err) => {
          fail('读不到对话：' + err.message);
          resolve();
        });
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
    // 提示文案也是缓存来的（agent/modes）：同样的字已经在屏幕上就别重建
    const text = hintOf(mode);
    const only = log.childElementCount === 1 ? log.firstElementChild : null;
    if (only && only.classList.contains('agent-empty') && only.textContent === text) return;
    reset();
    const p = node('p', 'agent-empty');
    p.textContent = text;
    log.appendChild(p);
  }

  function hintOf(id) {
    const found = MODES.find((m) => m.id === id);
    return found ? found.hint : '';
  }

  /* 标签由配置画出来，增删模式只改 config/agents.yaml */
  function renderTabs() {
    tabs.textContent = '';
    for (const m of MODES) {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.mode = m.id;
      b.textContent = m.label;
      tabs.append(b);
    }
  }

  /* 选中态：ARIA 要的是 aria-current="true"，不是空属性值（toggleAttribute 设的是 ""，
     CSS 的 [aria-current="true"] 就匹配不上，选中项不高亮） */
  function markTab() {
    for (const b of tabs.querySelectorAll('button[data-mode]')) {
      if (b.dataset.mode === mode) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    }
  }

  /* 模式表就位就画（缓存和服务器回来各调一次，内容一样时把重复渲染省掉） */
  function applyModes(list, saved) {
    if (JSON.stringify(list) === JSON.stringify(MODES)) return;
    MODES = list;
    mode = MODES.some((m) => m.id === saved) ? saved : MODES[0].id;
    renderTabs();
    markTab();
  }

  /* 进页面 / 切模式：先画缓存（或缓存里已有的提示文案），再对服务器；还在工作就继续跟。
     prefetched 是 boot 里跟 info 并发拿到的同一模式的快照，有就不重复请求。 */
  async function load(prefetched) {
    const token = ++epoch;
    const cached = readTurn(mode);
    const hasCache = Boolean(cached && (cached.question || cached.text));
    if (hasCache) {
      show(cached); // boot 里可能已经画过，没变化时 show 自己会跳
      // 让缓存帧先真的画出来，别和网络帧挤在同一瞬间（墨水屏上就是一次"卡"）
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
      if (!prefetched) await new Promise((r) => setTimeout(r, 250));
      if (token !== epoch) return;
    } else if (!prefetched) {
      hint(); // 没这一轮的内容，但模式表的缓存里有提示文案，先把它画出来
    }
    try {
      const turn = await (prefetched || api('/api/agent/turn?mode=' + encodeURIComponent(mode)));
      if (token !== epoch) return;
      // 会话换了（服务重启/新建）：屏幕上那段缓存不是这个会话的，擦掉回到初始态
      if (cached && (cached.session || '') !== (turn.session || '')) {
        cache(turn);
        return hint();
      }
      cache(turn);
      // 服务器没有这一轮：本地缓存留着不动，比擦成提示再画一遍省一次刷新
      if (!turn.question && turn.state === 'idle') return hasCache ? undefined : hint();
      show(turn);
      if (turn.state !== 'thinking') return;
      setBusy(true);
      await follow(token);
    } catch (err) {
      if (token === epoch) fail('读不到对话：' + err.message);
    } finally {
      if (token === epoch) setBusy(false);
    }
  }

  /* 输入框跟着内容长高：CSS 封顶 14rem，超了自己滚 */
  function grow() {
    say.style.height = 'auto';
    say.style.height = say.scrollHeight + 'px';
  }

  /* 交互 */

  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn || btn.dataset.mode === mode) return;
    mode = btn.dataset.mode;
    write(MODE_KEY, mode);
    markTab();
    invalidate(); // 旧轮询/旧 SSE 作废
    setBusy(false);
    reset();
    load();
  });

  /* 语音输入：点一次开始、再点一次结束；边说边出字，结束把最终文本填进输入框，不自动发送。 */

  const MIC_ERROR = '拿不到麦克风（要 https 或 localhost，并允许麦克风权限）';

  let rec = null; // { ws, ctx, stream, node, buf, bytes, endTimer }
  let baseText = ''; // 开始录音时输入框里的内容，识别结果接在它后面

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function downsample(input, from) {
    if (from === 16000) return input;
    const ratio = from / 16000;
    const out = new Float32Array(Math.floor(input.length / ratio));
    for (let i = 0; i < out.length; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(input.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = start; j < end; j += 1) sum += input[j];
      out[i] = sum / Math.max(1, end - start);
    }
    return out;
  }

  function toPcm16(samples) {
    const out = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      const v = Math.max(-1, Math.min(1, samples[i]));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return out;
  }

  async function startVoice() {
    baseText = say.value;
    const ws = new WebSocket(
      (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/asr/stream',
    );
    ws.binaryType = 'arraybuffer';
    rec = { ws, ctx: null, stream: null, node: null, buf: [], bytes: 0 };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === 'error') return stopVoice(msg.message || '识别失败');
      if (msg.type === 'partial') {
        setVoiceText(msg.text || '');
      }
      if (msg.type === 'final') {
        setVoiceText(msg.text || '');
        stopVoice(msg.text ? '' : '没识别到内容，再说一次');
      }
    };
    ws.onclose = (e) => {
      // 正常结束是我们自己 close 的（code 1000/1005），其它都算异常，说清楚
      if (rec) stopVoice(e.code === 1000 || e.code === 1005 ? '' : '识别连接断了：' + e.code);
    };

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      // Android WebView 上 echoCancellation/noiseSuppression 有时直接 OverconstrainedError，
      // 退回最宽松的约束再试一次；还不行就把原因抛给调用方
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (second) {
        throw new Error(MIC_ERROR + '（' + (second.name || '') + '）');
      }
    }

    // 等异步的这段时间里用户可能又点了一次（rec 被清空），这里及时收摊，别把麦克风留着
    if (!rec) {
      for (const t of stream.getTracks()) t.stop();
      return;
    }

    // 不指定 16000：Android 8.1 上 16k 采集会给静音流，让系统用默认率、我们自己重采样
    const ctx = new AudioContext();
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
    // 等音轨真的活了再建 source：Android 上过早连接会拿到一路静音
    const track = stream.getAudioTracks()[0];
    if (track && track.readyState !== 'live') {
      await Promise.race([
        new Promise((r) => {
          track.onunmute = r;
        }),
        sleep(500),
      ]);
    }
    await sleep(150);
    const src = ctx.createMediaStreamSource(stream);
    const sink = ctx.createGain();
    sink.gain.value = 0; // 别把麦克风回放到喇叭
    src.connect(sink);
    sink.connect(ctx.destination);

    rec.ctx = ctx;
    rec.stream = stream;
    const rate = ctx.sampleRate;

    const onSamples = (samples) => {
      if (!rec) return; // 停止时队列里可能还剩一帧
      const pcm = toPcm16(downsample(samples, rate));
      rec.buf.push(new Uint8Array(pcm.buffer));
      rec.bytes += pcm.byteLength;
      if (rec.bytes >= 3200) flushVoice(); // 100ms 一帧
    };

    if (!rec) {
      for (const t of stream.getTracks()) t.stop();
      ctx.close().catch(() => {});
      return;
    }

    let worklet = null;
    if (ctx.audioWorklet) {
      try {
        await ctx.audioWorklet.addModule('/assets/recorder-worklet.js?v=17');
        worklet = new AudioWorkletNode(ctx, 'homeos-recorder');
      } catch {
        worklet = null; // 有些 WebView 拿不到 worklet，直接走下面的兜底
      }
    }
    if (worklet) {
      worklet.port.onmessage = (e) => onSamples(e.data);
      worklet.port.postMessage({ on: true });
      src.connect(worklet);
      worklet.connect(sink);
      rec.node = worklet;
    } else {
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (e) => onSamples(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(proc);
      proc.connect(sink);
      rec.node = proc;
    }
  }

  function flushVoice() {
    if (!rec || rec.ws.readyState !== WebSocket.OPEN) return;
    const chunk = new Uint8Array(rec.bytes);
    let at = 0;
    for (const part of rec.buf) {
      chunk.set(part, at);
      at += part.length;
    }
    rec.buf = [];
    rec.bytes = 0;
    rec.ws.send(chunk.buffer);
  }

  function setVoiceText(text) {
    say.value = baseText ? baseText + text : text;
    if (text) voiceTurn = true; // 识别出了内容：这一句算语音提问
    grow();
  }

  function stopVoice(error) {
    const cur = rec;
    rec = null;
    clearTimeout(cur?.endTimer);
    mic.setAttribute('aria-pressed', 'false');
    if (!cur) return;
    if (cur.node && cur.node.port) cur.node.port.postMessage({ on: false });
    if (cur.stream) for (const track of cur.stream.getTracks()) track.stop();
    if (cur.ctx) cur.ctx.close().catch(() => {});
    if (cur.ws.readyState === WebSocket.OPEN) cur.ws.close();
    if (error) fail(error);
    else say.focus();
  }

  mic.addEventListener('click', async () => {
    if (rec) {
      // 让后端收尾；万一它不回，6 秒后自己停，别把按钮永远按着
      if (rec.ws.readyState === WebSocket.OPEN) {
        rec.ws.send('end');
        rec.endTimer = setTimeout(() => stopVoice('没听到结果，再说一次'), 6000);
      } else {
        stopVoice('');
      }
      return;
    }
    mic.setAttribute('aria-pressed', 'true');
    try {
      await startVoice();
    } catch (err) {
      stopVoice(err.message || MIC_ERROR);
    }
  });

  say.addEventListener('input', () => {
    voiceTurn = false; // 手动改过就不再算语音提问
    grow();
  });

  fresh.addEventListener('click', async () => {
    invalidate();
    setBusy(false);
    try {
      cache(await api('/api/agent/new-session', { mode }));
      say.value = '';
      grow();
      hint();
    } catch (err) {
      hint();
      fail('开新对话失败：' + err.message);
    }
  });

  /* 停止：先断掉跟随，再让后端 abort；后端回的快照就是屏幕该显示的样子 */
  async function stopTurn() {
    invalidate();
    const token = epoch;
    try {
      const turn = await api('/api/agent/abort', { mode });
      if (token !== epoch) return;
      show(turn);
      cache(turn);
    } catch (err) {
      if (token === epoch) fail('停不下来：' + err.message);
    } finally {
      if (token === epoch) setBusy(false);
    }
  }

  async function startTurn(text, retried) {
    setBusy(true);
    // 只在第一轮记下“这是语音提的问”：busy 重试时不能再把它抹成 false
    if (!retried) pendingSpeak = voiceTurn;
    voiceTurn = false;
    const token = ++epoch;
    say.value = '';
    grow();
    paint();
    DOM.user.textContent = text;
    DOM.note.textContent = WORKING;
    try {
      const started = await api('/api/agent/prompt', { mode, text });
      if (token !== epoch) return;
      show(started);
      cache(started);
      await follow(token);
    } catch (err) {
      // 上一轮还在跑：先停再发（这是页面自己的组合，后端只负责拒绝）
      if (err.code === 'busy' && !retried) {
        setBusy(false);
        await api('/api/agent/abort', { mode }).catch(() => {});
        return startTurn(text, true);
      }
      if (token === epoch) fail(ERROR_HINT[err.code] || err.message);
    } finally {
      if (token === epoch) {
        setBusy(false);
        say.focus();
      }
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (busy) {
      stopTurn();
      return;
    }
    const text = say.value.trim();
    if (!text) return;
    startTurn(text);
  });

  async function boot() {
    // 标签与内容都先同步画（不等网络），服务器回来再对账；两个请求并发
    const saved = read(MODE_KEY);
    const said = readModes();
    if (said) applyModes(said, saved);
    const cached = saved ? readTurn(saved) : null;
    if (cached && (cached.question || cached.text)) show(cached);
    const infoReq = api('/api/info/agent');
    // 预取的那一份可能永远没人 await（模式被改掉、或 info 先失败），自己兜住 rejection
    const turnReq = saved
      ? api('/api/agent/turn?mode=' + encodeURIComponent(saved)).catch(() => null)
      : null;
    let info;
    try {
      info = await infoReq;
    } catch (err) {
      fail('读不到模式：' + err.message);
      return;
    }
    const fresh = info.modes || [];
    if (!fresh.length) {
      fail('后端没配任何模式（config/agents.yaml）');
      return;
    }
    write(MODES_KEY, JSON.stringify(fresh));
    applyModes(fresh, saved);
    write(MODE_KEY, mode); // 下次进页面就有模式与标签可读，不必等 info
    // 引擎起不来的话，等用户发第一句才报太晚了：先把原因画出来
    if (info.engine && info.engine.available === false) {
      const why = (info.engine.problems || [])[0] || info.engine.last_error;
      fail(why ? '助手引擎没起来：' + why : '助手引擎没起来，检查 config/pi-*.json');
      return;
    }
    load(mode === saved ? turnReq : null);
  }

  boot();
})();
