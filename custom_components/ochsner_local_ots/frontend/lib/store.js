// One shared data store per hass.connection: the one-shot plant payload
// (ochsner_local_ots/plants) plus the live subscription feed
// (ochsner_local_ots/subscribe). Shared by every card instance and the panel.

const STORES = new WeakMap();
const STOP_DELAY = 10000; // keep the subscription briefly when cards re-mount
const REFETCH_MIN_INTERVAL = 5000;
const RETRY_DELAY = 30000;

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
    this._refetchedFor = new Map();
    this._onReady = () => {
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
    // Re-fetch once per distinct mismatch (no loop if the backend stays inconsistent).
    if (sig && this._refetchedFor.get(ev.plant_id) !== sig) {
      this._refetchedFor.set(ev.plant_id, sig);
      this.refetch(false);
    }
    this._emit();
  }

  /**
   * Circuit/DHW presence in the feed differs from the loaded payload → the
   * plants payload must be re-fetched. Returns a signature of the feed's
   * structure when it mismatches, else null.
   */
  _structureMismatch(ev) {
    if (!this.plants) return null;
    const plant = this.plant(ev.plant_id);
    const caps = ev.capabilities || {};
    const vals = ev.values || {};
    const present = [];
    for (let n = 1; n <= 4; n++) {
      const av = vals[`hc${n}.availability`];
      if (av === undefined || av === null) continue;
      if ((Number(av) & 1) === 1 && !caps.bms) present.push(n);
    }
    const sig = JSON.stringify([!!caps.dhw, !!caps.bms, present]);
    if (!plant) return sig;
    if ("dhw" in caps && !!caps.dhw !== !!plant.dhw) return sig;
    if ("bms" in caps && plant.capabilities && !!caps.bms !== !!plant.capabilities.bms) return sig;
    const have = new Set((plant.circuits || []).map((c) => Number(c.index)));
    for (let n = 1; n <= 4; n++) {
      const av = vals[`hc${n}.availability`];
      if (av === undefined || av === null) continue;
      if (present.includes(n) !== have.has(n)) return sig;
    }
    return null;
  }
}
