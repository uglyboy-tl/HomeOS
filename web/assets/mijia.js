/* 米家控制页的数据层（结构在 mijia.html，Alpine 渲染）。
   墨水屏上每多画一次都是可见的一闪，所以：首帧用 localStorage 上次结果，只写变化的字段，
   清单没变不重建 rooms。 */

const CACHE_KEY = 'mijia/v1';
const ROOM_KEY = 'mijia/room'; // 上次停在哪间房：与数据缓存分开存，不过期
const CACHE_MAX_AGE = 12 * 3600 * 1000; // 超过半天不信缓存
const LOST = { text: '读不到', cls: null, sub: null, fault: false };

/* ---- 纯函数：接口数据 → 视图模型 ---- */

function sensorView(kind, value) {
  // 缺值/失败/只有半个字段都算“读不到”：不能把没拿到的数当 0 或“正常”
  if (!value || value.error) return LOST;
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

function buildRooms(payload, values) {
  const names = new Map((payload.rooms || []).map((r) => [r.id, r.name || r.id]));
  const byRoom = new Map([...names.keys()].map((id) => [id, []]));
  for (const d of payload.devices || []) {
    if (!byRoom.has(d.room)) byRoom.set(d.room, []);
    byRoom.get(d.room).push(d);
  }
  const value = (id) => (values && values[id]) || {};
  return [...byRoom].map(([id, list]) => ({
    id,
    label: names.get(id) || id, // 后端给了显示名就用它，id 只是稳定键
    switches: list
      .filter((d) => d.kind === 'switch')
      .map((d) => ({
        id: d.id,
        label: d.name,
        on: value(d.id).on === true,
        bad: Boolean(value(d.id).error), // 这台读不到：显示「读不到」并禁用
        busy: false,
      })),
    sensors: list
      .filter((d) => d.kind !== 'switch')
      .map((d) => ({
        id: d.id,
        label: d.name,
        kind: d.kind,
        ...sensorView(d.kind, values && values[d.id]),
      })),
  }));
}

/* ---- 组件 ---- */

// $persist 必须在 data 工厂里求值；注册要赶在 alpine 执行之前（见 mijia.html 的注释）
document.addEventListener('alpine:init', () => {
  Alpine.data('mijiaPanel', () => {
    return {
      // 浏览器缓存：{ts, devices, values}，由 $persist 自动读写 localStorage
      cache: Alpine.$persist({ ts: 0, devices: null, values: null }).as(CACHE_KEY),
      // 上次停在哪个房间：单独的 key，不受数据缓存 TTL 影响
      lastRoom: Alpine.$persist('').as(ROOM_KEY),
      devicesPayload: null,
      values: {}, // 服务端原始值，rooms 由它派生
      rooms: [], // 在 start() 里用缓存/接口填一次成型
      cur: hashRoom() || '',
      stale: false, // 整次拉取失败 → 显示缓存值但停用操作
      toast: '',
      _inflight: false, // refresh 去重
      _timer: null,

      get current() {
        return (
          this.rooms.find((r) => r.id === this.cur) ||
          this.rooms[0] || { switches: [], sensors: [] }
        );
      },

      async start() {
        // $persist 的恢复值在 data 初始化后才就位，所以缓存必须在这里读（工厂里读的是默认值）
        if (!this.rooms.length && this.cache.devices && this.cache.values) {
          if (Date.now() - this.cache.ts < CACHE_MAX_AGE) {
            // 一次赋值：首帧就是缓存里的真值，不会先出一排「关」
            this.devicesPayload = this.cache.devices;
            this.values = this.cache.values;
            this.rooms = buildRooms(this.cache.devices, this.cache.values);
          }
        }
        // 没带 hash 时回到上次停留的房间；它不存在了（设备表改过）就回第一间
        if (!this.rooms.some((r) => r.id === this.cur)) {
          const remembered = this.lastRoom;
          if (this.rooms.some((r) => r.id === remembered)) this.cur = remembered;
          else this.cur = this.rooms.length ? this.rooms[0].id : '';
        }
        addEventListener('hashchange', () => this.pick(hashRoom(), true));
        setInterval(() => {
          if (!document.hidden) this.refresh();
        }, 120000);
        document.addEventListener('visibilitychange', () => {
          if (!document.hidden) this.refresh();
        });
        await this.refresh(true);
      },

      showToast(msg) {
        this.toast = msg;
        clearTimeout(this._timer);
        this._timer = setTimeout(() => {
          this.toast = '';
        }, 6000);
      },

      async get(url, init) {
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
        const body = await resp.json().catch(() => ({ ok: false, detail: '返回不是 JSON' }));
        if (!resp.ok || !body.ok) throw new Error(body.detail || body.error || '请求失败');
        return body;
      },

      /* 只改变化的字段：该字段没变就不赋值，Alpine 也就不会重绘那个节点 */
      /* 状态是「一间房」的：合并进 values，再从 values 派生当前视图（只改变化字段） */
      applyValues(partial) {
        Object.assign(this.values, partial);
        for (const room of this.rooms) {
          for (const sw of room.switches) {
            const value = this.values[sw.id] || {};
            const next = Boolean(!value.error && value.on === true);
            const bad = Boolean(value.error);
            if (sw.on !== next) sw.on = next;
            if (sw.bad !== bad) sw.bad = bad;
          }
          for (const sensor of room.sensors) {
            const view = sensorView(sensor.kind, this.values[sensor.id]);
            if (sensor.text !== view.text) sensor.text = view.text;
            if (sensor.cls !== view.cls) sensor.cls = view.cls;
            if (sensor.sub !== view.sub) sensor.sub = view.sub;
            if (sensor.fault !== view.fault) sensor.fault = view.fault;
          }
        }
      },

      /* 只拉当前这间房。房间里的设备由后端并行读（一次往返），前端不用再拆。 */
      async fetchRoom(id) {
        if (!id) return;
        const state = await this.get(`/api/mijia/state?room=${encodeURIComponent(id)}`);
        this.applyValues(state.values || {});
        this.saveCache();
      },

      async loadDevices() {
        const payload = await this.get('/api/mijia/devices');
        // 清单没变就别重建 rooms（重建一次 = 一次整块重绘）
        if (JSON.stringify(payload) === JSON.stringify(this.devicesPayload)) return;
        this.devicesPayload = payload;
        this.rooms = buildRooms(payload, this.values);
        if (!this.rooms.some((r) => r.id === this.cur)) {
          this.cur = this.rooms.length ? this.rooms[0].id : '';
        }
      },

      saveCache() {
        // $persist 只在「改属性」时写盘；整体替换对象会把 proxy 换掉，之后就不存了
        this.cache.ts = Date.now();
        this.cache.devices = this.devicesPayload;
        this.cache.values = { ...this.values };
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
        } catch (e) {
          this.stale = true;
          this.showToast('读取失败：' + e.message);
        } finally {
          this._inflight = false;
        }
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
        if (this.stale || sw.busy || sw.bad) return; // 离线或读不到时不让点，避免拿旧值乱写
        const want = sw.on !== true;
        sw.busy = true; // 期间这一行锁住并显示「…」
        try {
          const body = await this.get('/api/mijia/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: sw.id, on: want }),
          });
          sw.on = body.on; // 服务端读回成功才变色：失败时灯其实没动
          if (body.on !== want) this.showToast('设备没按预期变化');
          this.values[sw.id] = { on: body.on };
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
