/* 米家控制页的数据层（结构在 mijia.html，Alpine 渲染）。
   缓存的是 rooms——屏幕上那一屏的视图模型，不是接口原文：首帧直接用上次的数组上屏，
   不重新格式化；数据回来只改变化的字段，清单没变不重建 rooms。 */

const CACHE_KEY = 'mijia/v2';
const ROOM_KEY = 'mijia/room'; // 上次停在哪间房：与数据缓存分开存，不过期
const LOST = { text: '读不到', cls: null, sub: null, fault: false };
// 还没拉到值：先别喊“读不到”，那是拉失败才该说的话
const PENDING = { text: '…', cls: null, sub: null, fault: false };

/* ---- 纯函数：接口数据 → 视图模型 ---- */

function sensorView(kind, value) {
  // 没给值 = 还没拉过（冷启动）；拿到了但带 error = 真失败。两者不能都说“读不到”
  if (value === undefined || value === null) return PENDING;
  if (value.error) return LOST;
  if (kind === 'th') {
    const temp = Number(value.temperature);
    const hum = Number(value.humidity);
    if (!Number.isFinite(temp) || !Number.isFinite(hum)) return LOST;
    return { text: `${temp.toFixed(1)}° ${Math.round(hum)}%`, cls: null, sub: null, fault: false };
  }
  if (typeof value.fault !== 'boolean') return LOST;
  return {
    text: value.fault ? '故障' : '正常',
    cls: 'word',
    sub: value.battery == null ? null : `${value.battery}%`,
    fault: value.fault,
  };
}

function buildRooms(payload, prev) {
  const names = new Map((payload.rooms || []).map((r) => [r.id, r.name || r.id]));
  const byRoom = new Map([...names.keys()].map((id) => [id, []]));
  for (const d of payload.devices || []) {
    if (!byRoom.has(d.room)) byRoom.set(d.room, []);
    byRoom.get(d.room).push(d);
  }
  // 设备表变了重建时，先沿用上一次显示的值：否则会退成一排「关」和「读不到」
  const old = new Map();
  for (const r of prev || []) {
    for (const sw of r.switches) old.set(sw.id, sw);
    for (const s of r.sensors) old.set(s.id, s);
  }
  return [...byRoom].map(([id, list]) => ({
    id,
    label: names.get(id) || id, // 后端给了显示名就用它，id 只是稳定键
    switches: list
      .filter((d) => d.kind === 'switch')
      .map((d) => {
        const was = old.get(d.id);
        return {
          id: d.id,
          label: d.name,
          on: was ? was.on === true : false,
          bad: was ? was.bad === true : false, // 这台读不到：显示「读不到」并禁用
          pending: !was, // 还没拉过：显示「…」也别先假装它是关的
          busy: false,
        };
      }),
    sensors: list
      .filter((d) => d.kind !== 'switch')
      .map((d) => {
        const was = old.get(d.id);
        const view = was && was.kind === d.kind ? was : sensorView(d.kind, null);
        return {
          id: d.id,
          label: d.name,
          kind: d.kind,
          text: view.text,
          cls: view.cls || null,
          sub: view.sub || null,
          fault: view.fault === true,
        };
      }),
  }));
}

/* ---- 组件 ---- */

// $persist 必须在 data 工厂里求值；注册要赶在 alpine 执行之前（见 mijia.html 的注释）

/* $persist 内部对 JSON.parse 与 setItem 都没有 try/catch：缓存被写坏或超配额会直接抛错，
   组件初始化就断了。套一层 storage：读不到当没有，写不进去当没写，超上限就丢弃。 */
const STORE_LIMIT = 200_000; // 字符：远超我们存的那几个房间
const guardedStorage = {
  getItem(key) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return null;
      JSON.parse(raw); // 只验证：坏数据当没有，否则 persist 里那句裸的 JSON.parse 会直接抛错
      return raw;
    } catch {
      return null;
    }
  },
  setItem(key, value) {
    try {
      if (!value || value.length <= STORE_LIMIT) localStorage.setItem(key, value);
    } catch {
      /* 配额满或隐私模式：这一次不存，页面照用 */
    }
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.data('mijiaPanel', () => {
    return {
      // 浏览器缓存：{ts, sig, stale, rooms}；rooms 就是屏幕上那一屏，不是接口原文
      cache: Alpine.$persist({ ts: 0, sig: '', stale: false, rooms: null })
        .as(CACHE_KEY)
        .using(guardedStorage),
      // 上次停在哪间房：单独的 key，不受数据缓存 TTL 影响
      lastRoom: Alpine.$persist('').as(ROOM_KEY).using(guardedStorage),
      sig: '', // 设备清单签名：接口或缓存里那一份
      rooms: [], // 视图模型：缓存里直接拿来上屏
      cur: hashRoom() || '',
      stale: false, // 整次拉取失败 → 显示缓存值但停用操作
      toast: '',
      _inflight: false, // refresh 去重
      _timer: null, // toast 的收尾定时器
      _poll: null, // 轮询定时器
      _retry: null, // 离线后的重连定时器
      _onHash: null,
      _onVisible: null,

      get current() {
        return (
          this.rooms.find((r) => r.id === this.cur) ||
          this.rooms[0] || { switches: [], sensors: [] }
        );
      },

      async start() {
        // $persist 的恢复值在 data 初始化后才就位，所以缓存必须在这里读（工厂里读的是默认值）
        if (!this.rooms.length && Array.isArray(this.cache.rooms) && this.cache.rooms.length) {
          // 不看时间：首帧先给上次的，随后 refresh 一定会拿真值或标成离线
          this.rooms = this.cache.rooms;
          this.sig = this.cache.sig || '';
          this.stale = this.cache.stale === true;
        }
        // 没带 hash 时回到上次停留的房间；它不存在了（设备表改过）就回第一间
        if (!this.rooms.some((r) => r.id === this.cur)) {
          const remembered = this.lastRoom;
          if (this.rooms.some((r) => r.id === remembered)) this.cur = remembered;
          else this.cur = this.rooms.length ? this.rooms[0].id : '';
        }
        this._onHash = () => this.pick(hashRoom(), true);
        addEventListener('hashchange', this._onHash);
        this._poll = setInterval(() => {
          if (!document.hidden) this.refresh();
        }, 120000);
        this._onVisible = () => {
          if (!document.hidden) this.refresh();
        };
        document.addEventListener('visibilitychange', this._onVisible);
        await this.refresh(true);
      },

      // 组件移除时把定时器与监听收回去；将来把 SSE/WS 搬进来也放这里关
      destroy() {
        clearInterval(this._poll);
        clearTimeout(this._timer);
        clearTimeout(this._retry);
        removeEventListener('hashchange', this._onHash);
        document.removeEventListener('visibilitychange', this._onVisible);
      },

      showToast(msg) {
        this.toast = msg;
        clearTimeout(this._timer);
        this._timer = setTimeout(() => {
          this.toast = '';
        }, 6000);
      },

      async request(url, init) {
        let resp;
        try {
          resp = await fetch(url, init);
        } catch {
          // 墨水屏 Wi-Fi 会中途睡下去（写请求要等 1-2s 读回），缓一下重发一次；
          // 开关是幂等的，重发安全
          await new Promise((r) => setTimeout(r, 300));
          try {
            resp = await fetch(url, init);
          } catch {
            throw new Error('网络断了');
          }
        }
        const body = await resp.json().catch(() => ({ error: { message: '返回不是 JSON' } }));
        const failure = body.error || {};
        if (!resp.ok) throw new Error(failure.message || '请求失败');
        return body;
      },

      /* 只改变化的字段：该字段没变就不赋值，Alpine 也就不会重绘那个节点。
         响应里没有的设备跳过：只拉了当前这间房，别把别的房间改成未知值 */
      applyValues(partial) {
        for (const room of this.rooms) {
          for (const sw of room.switches) {
            const value = partial[sw.id];
            if (!value) continue;
            const next = Boolean(!value.error && value.on === true);
            const bad = Boolean(value.error);
            if (sw.on !== next) sw.on = next;
            if (sw.bad !== bad) sw.bad = bad;
            if (sw.pending) sw.pending = false;
          }
          for (const sensor of room.sensors) {
            const value = partial[sensor.id];
            if (!value) continue;
            const view = sensorView(sensor.kind, value);
            if (sensor.text !== view.text) sensor.text = view.text;
            if (sensor.cls !== view.cls) sensor.cls = view.cls;
            if (sensor.sub !== view.sub) sensor.sub = view.sub;
            if (sensor.fault !== view.fault) sensor.fault = view.fault;
          }
        }
      },

      /* 只拉当前这间房：设备 id 由视图模型给出，后端只读这些，不多碰云。 */
      async fetchRoom(id) {
        const room = this.rooms.find((r) => r.id === id);
        if (!room) return;
        const ids = [...room.switches, ...room.sensors].map((d) => d.id);
        if (!ids.length) return;
        const query = ids.map(encodeURIComponent).join(',');
        const payload = await this.request(`/api/mijia/values?ids=${query}`);
        this.applyValues(payload.values || {});
        this.saveCache();
      },

      async loadDevices() {
        const payload = await this.request('/api/info/mijia');
        const sig = JSON.stringify(payload);
        // 清单没变就别重建 rooms（重建一次 = 一次整块重绘）
        if (sig === this.sig) return;
        this.sig = sig;
        this.rooms = buildRooms(payload, this.rooms);
        if (!this.rooms.some((r) => r.id === this.cur)) {
          this.cur = this.rooms.length ? this.rooms[0].id : '';
        }
      },

      saveCache() {
        // $persist 只在「改属性」时写盘；整体替换对象会把 proxy 换掉，之后就不存了
        this.cache.ts = Date.now();
        this.cache.sig = this.sig;
        this.cache.stale = this.stale;
        // 存成普通对象：把 Alpine 的响应式代理折回纯数据；busy 是过程态，不进缓存
        this.cache.rooms = this.rooms.map((r) => ({
          ...r,
          switches: r.switches.map((s) => ({ ...s, busy: false })),
          sensors: r.sensors.map((s) => ({ ...s })),
        }));
      },

      async refresh(withDevices) {
        if (this._inflight) return; // 轮询与回到前台撞车时只跑一次
        this._inflight = true;
        try {
          if (!this.rooms.length) {
            // 冷启动：先拿清单（本地文件，毫秒级），再拉当前房间
            await this.loadDevices();
            await this.fetchRoom(this.cur);
          } else {
            await Promise.all([withDevices ? this.loadDevices() : null, this.fetchRoom(this.cur)]);
          }
          this.stale = false;
          clearTimeout(this._retry); // 回来了就不用再重试
          this.saveCache(); // 离线态也是显示内容，要跟着存
        } catch (e) {
          this.stale = true;
          this.saveCache();
          this.showToast('读取失败：' + e.message);
          this._armRetry();
        } finally {
          this._inflight = false;
        }
      },

      /* 离线后 10 秒自己再试一次：离线条一直挂到下一次 120 秒轮询太久 */
      _armRetry() {
        clearTimeout(this._retry);
        this._retry = setTimeout(() => {
          if (!document.hidden) this.refresh(true);
        }, 10000);
      },

      pick(id, fromHash) {
        if (!id || !this.rooms.some((r) => r.id === id)) return;
        if (id === this.cur) {
          if (!fromHash) this.refresh(true); // 再点当前房间 = 手动重拉（含设备清单）
          return;
        }
        this.cur = id;
        this.lastRoom = id;
        if (!fromHash) location.hash = id;
        // 先用缓存值画出这间房（rows 里已经有上次的 on/text），再拉一次最新的
        this.fetchRoom(id).catch((e) => this.showToast('读取失败：' + e.message));
      },

      async toggle(sw) {
        if (sw.busy || sw.bad) return; // 这一台就读不到：不写，不假装
        // 离线/没读到状态时，点一下就是「重连」，不然一整排按钮看着像是坏了
        if (this.stale) {
          this.showToast('正在重连…');
          await this.refresh(true);
          return;
        }
        if (sw.pending) {
          this.showToast('还在读状态，稍等一下');
          await this.refresh(true);
          return;
        }
        const want = sw.on !== true;
        sw.busy = true; // 期间这一行锁住并显示「…」
        try {
          const body = await this.request('/api/mijia/power', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: sw.id, on: want }),
          });
          sw.on = body.on; // 服务端读回成功才变色：失败时灯其实没动
          if (body.on !== want) this.showToast('设备没按预期变化');
          this.saveCache();
        } catch (e) {
          this.showToast('失败：' + e.message); // 状态保持原样，不假装成功
        } finally {
          sw.busy = false;
        }
      },
    };
  });
});

function hashRoom() {
  const raw = (location.hash || '').replace('#', '');
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}
