/* 助手页：三个模式各走一个 agent（chat / pages / device），全部走后端 /api/agent/*。
   只显示最近一轮；模式与各模式最后一轮存 localStorage，进来先画缓存再对账。 */
(() => {
  const tabs = document.querySelector('.agent-tabs');
  const log = document.querySelector('.agent-log');
  const form = document.querySelector('.agent-say');
  const say = form && form.querySelector('textarea');
  const fresh = document.querySelector('.agent-new');
  const mic = document.querySelector('.agent-mic');
  const send = document.querySelector('.agent-send');
  if (!tabs || !log || !form || !say || !fresh || !mic || !send) return;

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

  /* 本地缓存：模式 + 每个模式最后那一轮 */

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
    if (turn.state === 'error') return turn.error || '助手出错了';
    // 已经有正文就不再挂「正在工作」：否则字都滚出来了，那行还赖在下面
    if (turn.state === 'thinking' && !turn.text) return WORKING;
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
    if (!resp.ok) throw new Error(data.detail || 'HTTP ' + resp.status);
    return data;
  }

  function setBusy(on) {
    busy = on;
    say.disabled = on;
    mic.disabled = on;
    send.disabled = on;
    fresh.disabled = on;
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
      // 服务器说什么就存什么：idle 也要覆盖缓存，否则下次进来又先把旧内容画一遍再擦掉
      cache(turn);
      if (!turn.question && turn.state === 'idle') return hint();
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

  /* 选中态：ARIA 要的是 aria-current="true"，不是空属性值（toggleAttribute 设的是 ""，
     CSS 的 [aria-current="true"] 就匹配不上，选中项不高亮） */
  function markTab() {
    for (const b of tabs.querySelectorAll('button[data-mode]')) {
      if (b.dataset.mode === mode) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
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
      if (msg.error) return stopVoice(msg.error);
      if (msg.partial) {
        setVoiceText(msg.partial);
      }
      if (msg.text !== undefined) {
        setVoiceText(msg.text);
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

  say.addEventListener('input', grow);

  fresh.addEventListener('click', async () => {
    invalidate();
    setBusy(false);
    try {
      cache(await api('/api/agent/new', { mode }));
      say.value = '';
      grow();
      hint();
    } catch (err) {
      hint();
      fail('开新对话失败：' + err.message);
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = say.value.trim();
    if (!text || busy) return;
    setBusy(true);
    const token = ++epoch;
    say.value = '';
    grow();
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
  markTab();
  load();
})();
