// One shared data store per hass.connection: the one-shot plant payload
// (ochsner_local_ots/plants) plus the live subscription feed
// (ochsner_local_ots/subscribe). Shared by every card instance and the panel.

const STORES = new WeakMap();
const STOP_DELAY = 10000; // keep the subscription briefly when cards re-mount
const REFETCH_MIN_INTERVAL = 5000;
const RETRY_DELAY = 30000;
// Structure change seen in the feed: re-fetch after these delays while the
// mismatch persists (the backend reloads the entry after a section change,
// so the first answer may still be the old structure). Then give up until
// the feed's structure changes again.
const STRUCTURE_REFETCH = [1000, 10000, 60000];
// Time programs are read on demand (not polled): again after this long.
const SCHEDULE_MAX_AGE = 10 * 60 * 1000;
// availability bits per circuit: present, heat, cool, room sensor, humidity
const HC_BITS = [1, 2, 4, 32, 64];

export function getStore(hass) {
  const conn = hass && hass.connection;
  if (!conn) return null;
  let s = STORES.get(conn);
  if (!s) {
    s = new PlantStore(conn);
    STORES.set(conn, s);
  }
  return s;
}

export class PlantStore {
  constructor(conn, { timers } = {}) {
    this.conn = conn;
    this.timers = timers || globalThis;
    this.plants = null; // array once loaded
    this.settings = {};
    this.live = new Map(); // plant_id → last event
    this.error = null; // { code, message }
    this.loading = false;
    this.language = undefined;
    this.rev = 0; // bumps on every change
    this._listeners = new Set();
    this._unsub = null;
    this._subscribing = null;
    this._stopTimer = null;
    this._retryTimer = null;
    this._lastFetch = 0;
    this._fetchPromise = null;
    this._refetchQueued = null;
    this._mismatch = new Map(); // plant_id → { sig, tries, timer }
    this.schedules = new Map(); // plant_id → { at, circuits: Map(n → program) | null, error, loading }
    this._onReady = () => {
      for (const s of this.schedules.values()) s.at = 0; // re-read after a reconnect
      this.refetch(true);
    };
  }

  setLanguage(lang) {
    if (!lang || lang === this.language) return;
    const had = this.language !== undefined;
    this.language = lang;
    if (had && this._listeners.size) this.refetch(true);
  }

  addListener(fn) {
    this._listeners.add(fn);
    if (this._stopTimer) {
      this.timers.clearTimeout(this._stopTimer);
      this._stopTimer = null;
    }
    if (this._listeners.size === 1 && !this._started) this._start();
    return () => {
      this._listeners.delete(fn);
      if (!this._listeners.size) {
        if (this._stopTimer) this.timers.clearTimeout(this._stopTimer);
        this._stopTimer = this.timers.setTimeout(() => {
          this._stopTimer = null;
          if (!this._listeners.size) this._stop();
        }, STOP_DELAY);
      }
    };
  }

  plant(plantId) {
    return (this.plants || []).find((p) => p.plant_id === plantId) || null;
  }

  liveFor(plantId) {
    return this.live.get(plantId) || null;
  }

  /** The plant's time programs: { circuits: Map(n → program) | null, error, loading } or null before the first read. */
  schedulesFor(plantId) {
    return this.schedules.get(plantId) || null;
  }

  /** Read the plant's time programs unless a recent read (or one in flight) exists. */
  ensureSchedules(plantId) {
    const s = this.schedules.get(plantId);
    const age = s ? Date.now() - s.at : Infinity;
    if (s && (s.loading || age < (s.error ? RETRY_DELAY : SCHEDULE_MAX_AGE))) return;
    this.fetchSchedules(plantId);
  }

  fetchSchedules(plantId, force = false) {
    const prev = this.schedules.get(plantId);
    if (prev && prev.loading) return prev.loading;
    const entry = { at: prev ? prev.at : 0, circuits: prev ? prev.circuits : null, error: null, loading: null };
    entry.loading = this.conn
      .sendMessagePromise({ type: "ochsner_local_ots/schedules", plant_id: plantId, force })
      .then((res) => {
        entry.circuits = new Map(((res && res.circuits) || []).map((c) => [Number(c.index), c]));
      })
      .catch((err) => {
        entry.error = { code: (err && err.code) || "error", message: (err && err.message) || String(err) };
      })
      .finally(() => {
        entry.at = Date.now();
        entry.loading = null;
        this._emit();
      });
    this.schedules.set(plantId, entry);
    return entry.loading;
  }

  /** Store whole days ({day: [[minute, value], …]}) of a circuit's program; resolves with the program read back. */
  async saveSchedule(plantId, circuit, days) {
    const res = await this.conn.sendMessagePromise({ type: "ochsner_local_ots/schedule/set", plant_id: plantId, circuit, days });
    let entry = this.schedules.get(plantId);
    if (!entry) {
      entry = { at: Date.now(), circuits: null, error: null, loading: null };
      this.schedules.set(plantId, entry);
    }
    if (!entry.circuits) entry.circuits = new Map();
    entry.circuits.set(Number(res.index), res);
    this._emit();
    return res;
  }

  _emit() {
    this.rev++;
    for (const fn of [...this._listeners]) {
      try {
        fn(this);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("ochsner-heatpump listener failed", err);
      }
    }
  }

  _start() {
    this._started = true;
    if (this.conn.addEventListener) this.conn.addEventListener("ready", this._onReady);
    this.refetch(true);
    this._subscribe();
  }

  _stop() {
    this._started = false;
    for (const m of this._mismatch.values()) if (m.timer) this.timers.clearTimeout(m.timer);
    this._mismatch.clear();
    if (this.conn.removeEventListener) this.conn.removeEventListener("ready", this._onReady);
    if (this._retryTimer) this.timers.clearTimeout(this._retryTimer);
    this._retryTimer = null;
    const un = this._unsub;
    this._unsub = null;
    if (typeof un === "function") {
      Promise.resolve()
        .then(() => un())
        .catch(() => {});
    } else if (this._subscribing) {
      this._subscribing.then((u) => typeof u === "function" && !this._started && u()).catch(() => {});
    }
    this._subscribing = null;
  }

  async _subscribe() {
    if (this._unsub || this._subscribing) return;
    try {
      // home-assistant-js-websocket re-subscribes automatically after reconnects.
      this._subscribing = this.conn.subscribeMessage((ev) => this._onEvent(ev), { type: "ochsner_local_ots/subscribe" });
      const un = await this._subscribing;
      this._subscribing = null;
      if (!this._started) {
        if (typeof un === "function") un();
        return;
      }
      this._unsub = un;
    } catch (err) {
      this._subscribing = null;
      this._setError(err);
      this._scheduleRetry();
    }
  }

  _scheduleRetry() {
    if (this._retryTimer || !this._started) return;
    this._retryTimer = this.timers.setTimeout(() => {
      this._retryTimer = null;
      if (!this._started) return;
      this.refetch(true);
      this._subscribe();
    }, RETRY_DELAY);
  }

  _setError(err) {
    this.error = { code: (err && err.code) || "error", message: (err && err.message) || String(err) };
    this._emit();
  }

  /** (Re-)load the plants payload; throttled unless forced. */
  refetch(force = false) {
    const now = Date.now();
    if (this._fetchPromise) {
      if (force) this._refetchQueued = true;
      return this._fetchPromise;
    }
    if (!force && now - this._lastFetch < REFETCH_MIN_INTERVAL) {
      if (!this._refetchQueued) {
        this._refetchQueued = true;
        this.timers.setTimeout(() => {
          this._refetchQueued = false;
          this.refetch(true);
        }, REFETCH_MIN_INTERVAL - (now - this._lastFetch));
      }
      return Promise.resolve();
    }
    this._lastFetch = now;
    this.loading = true;
    const msg = { type: "ochsner_local_ots/plants" };
    if (this.language) msg.language = this.language;
    this._fetchPromise = this.conn
      .sendMessagePromise(msg)
      .then((res) => {
        this.plants = (res && res.plants) || [];
        this.settings = (res && res.settings) || {};
        this.error = null;
      })
      .catch((err) => {
        this.error = { code: (err && err.code) || "error", message: (err && err.message) || String(err) };
        this._scheduleRetry();
      })
      .finally(() => {
        this.loading = false;
        this._fetchPromise = null;
        this._emit();
        if (this._refetchQueued === true) {
          this._refetchQueued = false;
          this.refetch(true);
        }
      });
    return this._fetchPromise;
  }

  _onEvent(ev) {
    if (!ev || !ev.plant_id) return;
    this.live.set(ev.plant_id, ev);
    const sig = this._structureMismatch(ev);
    if (sig) this._structureChanged(ev.plant_id, sig);
    else this._clearMismatch(ev.plant_id);
    this._emit();
  }

  _clearMismatch(plantId) {
    const m = this._mismatch.get(plantId);
    if (m && m.timer) this.timers.clearTimeout(m.timer);
    this._mismatch.delete(plantId);
  }

  /** Debounced re-fetch while the feed's structure differs from the payload. */
  _structureChanged(plantId, sig) {
    let m = this._mismatch.get(plantId);
    if (!m || m.sig !== sig) {
      if (m && m.timer) this.timers.clearTimeout(m.timer);
      m = { sig, tries: 0, timer: null };
      this._mismatch.set(plantId, m);
    }
    if (m.timer || m.tries >= STRUCTURE_REFETCH.length) return;
    m.timer = this.timers.setTimeout(() => {
      m.timer = null;
      m.tries++;
      if (this._started) this.refetch(true);
    }, STRUCTURE_REFETCH[m.tries]);
  }

  /**
   * What decides the layout: present circuits with their capability bits,
   * DHW, BMS, pool and buffer. Returns the feed's structure signature when
   * it differs from the loaded payload, else null.
   */
  _structureMismatch(ev) {
    if (!this.plants) return null;
    const plant = this.plant(ev.plant_id);
    const caps = ev.capabilities || {};
    const vals = ev.values || {};
    const pcaps = (plant && plant.capabilities) || {};
    const have = new Map(((plant && plant.circuits) || []).map((c) => [Number(c.index), c]));
    const flags = (c) => (c ? [true, !!c.heat, !!c.cool, !!c.room_sensor, !!c.humidity] : [false, false, false, false, false]);
    const feedCircuits = [], payloadCircuits = [];
    for (let n = 1; n <= 4; n++) {
      const av = vals[`hc${n}.availability`];
      const p = flags(have.get(n));
      payloadCircuits.push(p);
      if (av === undefined || av === null || caps.bms) feedCircuits.push(caps.bms ? flags(null) : p); // unknown → as loaded
      else feedCircuits.push(HC_BITS.map((bit, i) => (i === 0 || (Number(av) & 1) === 1) && (Number(av) & bit) === bit));
    }
    const pick = (src, key, fallback) => (key in src ? !!src[key] : fallback);
    const feed = JSON.stringify([
      pick(caps, "bms", !!pcaps.bms), pick(caps, "dhw", !!pcaps.dhw),
      pick(caps, "pool", !!pcaps.pool), pick(caps, "buffer", !!pcaps.buffer), feedCircuits,
    ]);
    if (!plant) return feed;
    const loaded = JSON.stringify([!!pcaps.bms, !!pcaps.dhw, !!pcaps.pool, !!pcaps.buffer, payloadCircuits]);
    return feed === loaded ? null : feed;
  }
}
